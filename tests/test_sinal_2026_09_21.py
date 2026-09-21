"""Sinal / entrada como composição da condição de pagamento (21/09/2026).

    encargo_efetivo = (1 − percentual_sinal) × encargo_condicao_saldo

O sinal é pago à vista sem encargo; a condição da cotação passa a ser a do SALDO. Sinal 0 é
exatamente o comportamento tradicional. Os oracles são independentes do motor (Decimal).
"""
import json
from decimal import Decimal

import pytest
from sqlmodel import select

from app import comercial_service as com
from app import config_service as cfg
from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, ZERO
from app.models import Cliente, CondicaoPagamento, Cotacao, CotacaoItem, Fornecedor
from app.payment_terms import (
    EncargoResolvido, OK, REVIEW_REQUIRED, SinalInvalido, encargo_com_sinal,
    percentual_sinal_do_formulario, resolver_encargo, rotulo_condicao, validar_percentual_sinal,
)
from app.pricing_engine import preco_b2b, preco_de_tabela, preco_por_desconto
from conftest import RequestFalsa, _novo_usuario
from decimais import MARGEM_DO_CENTAVO, aprox
from tests.crisis.conftest import add_item, chamar, nova_cotacao, produto_nacional, salvar_cabecalho

X = Decimal

CONDICOES = {"À VISTA": X("0"), "30": X("0.016"), "30/60": X("0.032"), "30/60/90": X("0.048"),
             "30/60/90/120": X("0.064"), "30/60/90/120/150": X("0.080")}
SINAIS = (X("0"), X("0.10"), X("0.30"), X("0.50"), X("0.75"), X("1"))


def oracle_encargo(sinal: Decimal, encargo_saldo: Decimal) -> Decimal:
    return (X("1") - sinal) * encargo_saldo


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def admin():
    u = _novo_usuario("ADMIN")
    u.can_approve_quotes = True
    return u


@pytest.fixture
def owner():
    u = _novo_usuario("OWNER")
    u.can_approve_quotes = True
    return u


def _encargo(codigo):
    return EncargoResolvido(pct=CONDICOES[codigo], confirmado=True, label=f"{codigo} dias",
                            origem="tabela")


# ===========================================================================
# 1. Fórmula pura — sinal × condição (0/10/30/50/75/100 × 6 condições canônicas)
# ===========================================================================
@pytest.mark.parametrize("sinal", SINAIS)
@pytest.mark.parametrize("codigo", sorted(CONDICOES))
def test_encargo_efetivo_e_proporcional_ao_saldo(sinal, codigo):
    r = encargo_com_sinal(_encargo(codigo), sinal)
    assert r.pct == oracle_encargo(sinal, CONDICOES[codigo])
    assert r.status == OK and not r.bloqueado
    if sinal == 0:
        assert r.label == f"{codigo} dias"
    elif sinal == 1:
        assert r.pct == 0 and r.label == "100% à vista (sinal)"
    else:
        assert r.label.startswith(f"{int(sinal * 100)}% de sinal + {int(100 - sinal * 100)}% em ")


def test_exemplos_do_enunciado():
    assert encargo_com_sinal(_encargo("30/60/90"), X("0")).pct == X("0.048")
    assert encargo_com_sinal(_encargo("30/60/90"), X("0.30")).pct == X("0.0336")
    assert encargo_com_sinal(_encargo("30/60"), X("0.50")).pct == X("0.0160")
    assert encargo_com_sinal(_encargo("30/60/90"), X("0.50")).pct == X("0.0240")
    assert encargo_com_sinal(_encargo("30/60/90/120/150"), X("1")).pct == 0


def test_sinal_zero_devolve_o_proprio_encargo_sem_tocar():
    e = _encargo("30/60/90")
    assert encargo_com_sinal(e, 0) is e
    assert encargo_com_sinal(e, None) is e
    assert encargo_com_sinal(e, "") is e


def test_maior_sinal_nunca_aumenta_o_encargo():
    for codigo in CONDICOES:
        anterior = None
        for s in (X("0"), X("0.05"), X("0.10"), X("0.30"), X("0.5"), X("0.75"), X("0.99"), X("1")):
            atual = encargo_com_sinal(_encargo(codigo), s).pct
            assert anterior is None or atual <= anterior, (codigo, s)
            anterior = atual


def test_saldo_bloqueado_continua_bloqueado_salvo_sinal_de_100():
    cartao = resolver_encargo([], "CARTAO")
    assert cartao.bloqueado
    for s in (X("0.10"), X("0.30"), X("0.99")):
        r = encargo_com_sinal(cartao, s)
        assert r.bloqueado and r.status == REVIEW_REQUIRED and r.pct == 0
        assert r.motivo == cartao.motivo
    cem = encargo_com_sinal(cartao, X("1"))
    assert not cem.bloqueado and cem.pct == 0 and cem.confirmado


def test_sinal_nao_da_desconto_adicional():
    """O único efeito do sinal é o encargo: nenhum outro campo do encargo muda e nada é
    subtraído do preço — o oráculo é a própria fórmula."""
    e = _encargo("30/60/90")
    r = encargo_com_sinal(e, X("0.30"))
    assert r.pct == e.pct * X("0.7") and r.confirmado == e.confirmado and r.origem == "sinal"


# ===========================================================================
# 2. Validação — 0 ≤ sinal ≤ 100; recusa negativo, > 100, NaN, texto
# ===========================================================================
@pytest.mark.parametrize("ruim", [-1, -0.01, 101, 100.01, "abc", "nan", "inf", "-inf", "1e999", True])
def test_percentual_invalido_e_recusado(ruim):
    with pytest.raises(SinalInvalido):
        validar_percentual_sinal(ruim)
    with pytest.raises(SinalInvalido):
        percentual_sinal_do_formulario(ruim if not isinstance(ruim, bool) else "x")


@pytest.mark.parametrize("entrada, esperado", [
    (None, 0), ("", 0), (0, 0), ("0", 0), (0.3, X("0.3")), ("30", X("0.30")), ("30%", X("0.30")),
    ("33,5", X("0.335")), (1, 1), ("100", 1), (100, 1), (X("0.5"), X("0.5")),
])
def test_percentual_valido_normaliza_para_fracao(entrada, esperado):
    assert validar_percentual_sinal(entrada) == esperado


@pytest.mark.parametrize("campo, esperado", [
    ("", 0), ("0", 0), ("1", X("0.01")), ("30", X("0.30")), ("30,5", X("0.305")), ("100", 1),
])
def test_formulario_e_sempre_percentual(campo, esperado):
    assert percentual_sinal_do_formulario(campo) == esperado


def test_rotulo_comercial():
    assert rotulo_condicao(0, "30/60/90 dias") == "30/60/90 dias"
    assert rotulo_condicao(X("0.30"), "30/60/90 dias") == "30% de sinal + 70% em 30/60/90 dias"
    assert rotulo_condicao(X("0.335"), "30 dias") == "33,5% de sinal + 66,5% em 30 dias"
    assert rotulo_condicao(1, "30 dias") == "100% à vista (sinal)"


# ===========================================================================
# 3. Cotação — sinal é material: B2B, tabela, preço (desconto preservado), comissão,
#    margem, totais, fingerprint, aprovação
# ===========================================================================
def _item(session, it_id):
    session.expire_all()
    return session.get(CotacaoItem, it_id)


def _b2b_oracle(session, cot_id, it):
    """B2B recomputado pelo motor puro a partir das regras do cenário atual."""
    from app.models import Produto
    cot = session.get(Cotacao, cot_id)
    produto = session.get(Produto, it.produto_id)
    regras, ctx = ps.regras_da_cotacao(session, cot, produto, comissao_formacao_pct=it.comissao_formacao_pct,
                                       politica=it.politica_comercial)
    margem = ps.margem_padrao(session, produto)
    return preco_b2b(D(it.custo_unitario), margem.margem_pct, regras).preco_negociado, ctx, margem


def test_sinal_30_em_30_60_90_reprecifica_e_preserva_desconto(session, fornecedores, admin):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.20"}}, ator=admin)
    session.commit()
    it = _item(session, it.id)
    assert it.encargo_pct == aprox(0.048) and it.percentual_sinal == 0 and it.encargo_saldo_pct == aprox(0.048)
    b2b0, tab0, preco0, com0 = it.preco_recomendado, it.preco_tabela, it.preco_negociado, it.comissao_valor
    fp0 = wf.fingerprint(session.get(Cotacao, cot.id), ws.itens_de(session, cot.id))

    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    c = session.get(Cotacao, cot.id)
    it = _item(session, it.id)
    assert c.percentual_sinal == aprox(0.30) and c.condicao_pagamento == "30/60/90"
    # encargo efetivo = 0,7 × 4,8% = 3,36% — pinado no item com o que o formou
    assert it.encargo_pct == aprox(0.0336) and it.percentual_sinal == aprox(0.30)
    assert it.encargo_saldo_pct == aprox(0.048)
    # B2B e tabela caem (encargo menor → primeiro centavo válido menor), tabela = 2 × B2B
    assert it.preco_recomendado < b2b0 and it.preco_tabela < tab0
    assert D(it.preco_tabela) == preco_de_tabela(D(it.preco_recomendado), 2)
    b2b_oracle, _ctx, margem = _b2b_oracle(session, cot.id, it)
    assert D(it.preco_recomendado) == b2b_oracle
    # desconto preservado como alavanca; preço reformado a partir da tabela nova
    assert it.desconto_editado_pct == aprox(0.20) and it.modo_edicao == "desconto"
    assert it.preco_negociado == aprox(float(preco_por_desconto(D(it.preco_tabela), X("0.20"))))
    assert it.preco_negociado < preco0
    assert it.comissao_faixa_pct == aprox(0.08) and it.comissao_valor != com0
    # preço com 20% sobre a tabela (= 1,6 × B2B) fica acima do B2B: margem ≥ alvo
    assert it.preco_negociado > it.preco_recomendado
    assert D(it.margem_liquida) >= D(margem.margem_pct) - MARGEM_DO_CENTAVO
    fp1 = wf.fingerprint(c, ws.itens_de(session, cot.id))
    assert fp1 != fp0


def test_sinal_100_zera_o_encargo_e_saldo_e_irrelevante(session, fornecedores, admin):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90/120/150")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="100")
    it = _item(session, it.id)
    assert it.encargo_pct == 0 and it.percentual_sinal == 1 and it.status_pagamento == OK
    # o mesmo B2B de "À vista" sem sinal
    cot2 = nova_cotacao(session, condicao_pagamento="À VISTA")
    from app.models import Produto
    it2 = add_item(session, cot2, session.get(Produto, it.produto_id))
    assert _item(session, it2.id).preco_recomendado == aprox(it.preco_recomendado)
    # saldo bloqueado (CARTAO) com sinal 100% não bloqueia
    salvar_cabecalho(session, cot, condicao_pagamento="CARTAO", possui_sinal="sim", percentual_sinal="100")
    it = _item(session, it.id)
    assert it.status_pagamento == OK and it.encargo_pct == 0 and it.preco_recomendado > 0
    assert not [b for b in ws.avaliar(session, session.get(Cotacao, cot.id)).blockers
                if "PAGAMENTO" in (b.codigo or "").upper() or "ENCARGO" in (b.codigo or "").upper()]
    # e com 30% continua bloqueado — a parte financiada precisa de condição cadastrada
    salvar_cabecalho(session, cot, condicao_pagamento="CARTAO", possui_sinal="sim", percentual_sinal="30")
    it = _item(session, it.id)
    assert it.status_pagamento == REVIEW_REQUIRED


def test_sinal_zero_e_exatamente_o_tradicional(session, fornecedores, admin):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    it = _item(session, it.id)
    b2b0, preco0 = it.preco_recomendado, it.preco_negociado
    c = session.get(Cotacao, cot.id)
    fp0 = wf.fingerprint(c, ws.itens_de(session, cot.id))
    # 30% e volta a 0: mesmos números, mesmo fingerprint
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    assert _item(session, it.id).preco_recomendado < b2b0
    salvar_cabecalho(session, cot, possui_sinal="", percentual_sinal="30")   # checkbox desmarcada
    c = session.get(Cotacao, cot.id)
    it = _item(session, it.id)
    assert c.percentual_sinal == 0 and it.preco_recomendado == aprox(b2b0) and it.preco_negociado == aprox(preco0)
    assert wf.fingerprint(c, ws.itens_de(session, cot.id)) == fp0
    # fingerprint de cotação sem sinal não carrega a chave (cotação anterior mantém o hash)
    itens = ws.itens_de(session, cot.id)
    from types import SimpleNamespace
    sem_campo = SimpleNamespace(**{k: getattr(c, k) for k in wf.CAMPOS_MATERIAIS_COTACAO},
                                freight_manual_confirmado=False)
    assert not hasattr(sem_campo, "percentual_sinal")
    assert wf.fingerprint(sem_campo, itens) == fp0


@pytest.mark.parametrize("sinal", ["10", "30", "50", "75"])
def test_b2b_com_sinal_bate_com_o_motor_puro_e_e_monotono(session, fornecedores, sinal):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90/120")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    b2b0 = _item(session, it.id).preco_recomendado
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal=sinal)
    it = _item(session, it.id)
    b2b_oracle, ctx, _m = _b2b_oracle(session, cot.id, it)
    assert D(it.preco_recomendado) == b2b_oracle <= D(b2b0)
    assert D(it.encargo_pct) == aprox(oracle_encargo(X(sinal) / 100, X("0.064")))
    assert ctx["condicao_pagamento_texto"] == f"{sinal}% de sinal + {100 - int(sinal)}% em 30/60/90/120 dias"


def test_mudar_sinal_invalida_aprovacao_e_reaprova_e_emite(session, fornecedores, admin, owner):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.55"}}, ator=admin)
    session.commit()
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    c = session.get(Cotacao, cot.id)
    pront = ws.avaliar(session, c)
    assert pront.precisa_aprovacao and not pront.pode_emitir
    pedido = ws.solicitar_aprovacao(session, c, ator=admin, justificativa="cliente estratégico")
    session.commit()
    ws.decidir(session, c, pedido.id, ator=owner, aprovar=True, comentario="ok")
    session.commit()
    assert ws.avaliar(session, c).pode_emitir
    # sinal 30 → 50: material — a aprovação cai
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="50")
    c = session.get(Cotacao, cot.id)
    assert ws.avaliar(session, c).aprovacao_valida is False
    assert _item(session, it.id).encargo_pct == aprox(0.024)
    pedido2 = ws.solicitar_aprovacao(session, c, ator=admin, justificativa="reaprovar com 50%")
    session.commit()
    ws.decidir(session, c, pedido2.id, ator=owner, aprovar=True, comentario="ok")
    session.commit()
    snap = ws.emitir(session, c, ator=owner)
    session.commit()
    fiscal = json.loads(snap.fiscal_json)
    assert fiscal["percentual_sinal"] == aprox(0.50) and fiscal["condicao_saldo"] == "30/60/90"
    assert fiscal["condicao_pagamento_texto"] == "50% de sinal + 50% em 30/60/90 dias"
    assert fiscal["encargo_efetivo_pct"] == aprox(0.024)
    linha = json.loads(snap.itens_json)[0]
    assert linha["percentual_sinal"] == aprox(0.50) and linha["encargo_saldo_pct"] == aprox(0.048)
    assert linha["encargo_pct"] == aprox(0.024) and snap.aprovacao_id == pedido2.id


def test_pdf_mostra_condicao_legivel_e_nao_o_encargo(session, fornecedores, admin, owner):
    from app.pdf_bridge import montar_documento
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0), 3)
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    c = session.get(Cotacao, cot.id)
    cliente = session.get(Cliente, c.cliente_id)
    texto_condicao = cfg.condicao_textual(session, c)
    assert texto_condicao == "30% de sinal + 70% em 30/60/90 dias"
    header, items, totals = montar_documento(c, cliente, ws.itens_de(session, c.id), rascunho=True,
                                             condicao_label=texto_condicao)
    assert header["condicao_pagamento"] == texto_condicao
    texto = json.dumps([header, items, totals], ensure_ascii=False).lower()
    for palavra in ("encargo", "3,36", "0.0336", "tabela", "b2b", "comiss", "margem", "custo"):
        assert palavra not in texto, palavra
    # PDF final: o texto vem do snapshot, mesmo que a rota passe outro rótulo
    snap = ws.emitir(session, c, ator=owner)
    session.commit()
    header2, _i, _t = montar_documento(c, cliente, ws.itens_de(session, c.id), snapshot=snap,
                                       condicao_label="30/60/90 dias")
    assert header2["condicao_pagamento"] == texto_condicao


def test_revisao_e_duplicata_herdam_o_sinal(session, fornecedores, admin, owner):
    from app.routers.cotacoes import duplicar
    cot = nova_cotacao(session, condicao_pagamento="30/60")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="50")
    c = session.get(Cotacao, cot.id)
    ws.emitir(session, c, ator=owner)
    session.commit()
    rev = ws.criar_revisao(session, c, ator=owner)
    session.commit()
    assert rev.percentual_sinal == aprox(0.50) and rev.condicao_pagamento == "30/60"
    r = chamar(duplicar, RequestFalsa(admin), cotacao_id=cot.id, session=session)
    session.commit()
    nova_id = int(r.headers["location"].rsplit("/", 1)[-1])
    nova = session.get(Cotacao, nova_id)
    assert nova.percentual_sinal == aprox(0.50)
    it_nova = ws.itens_de(session, nova_id)[0]
    assert it_nova.encargo_pct == aprox(0.016) and it_nova.percentual_sinal == aprox(0.50)
    assert it_nova.preco_recomendado == aprox(_item(session, it.id).preco_recomendado)


def test_sinal_invalido_na_rota_e_recusado_sem_alterar_nada(session, fornecedores, admin):
    from app.routers.cotacoes import atualizar_cabecalho
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    antes = _item(session, it.id).preco_negociado
    for ruim in ("-5", "101", "abc", "nan", "inf"):
        r = chamar(atualizar_cabecalho, RequestFalsa(admin), cotacao_id=cot.id, session=session,
                   condicao_pagamento="30/60", possui_sinal="sim", percentual_sinal=ruim,
                   estado_destino="", contribuinte_icms="sim", freight_type="CIF")
        assert r.status_code == 400, ruim
        session.expire_all()
        c = session.get(Cotacao, cot.id)
        assert c.percentual_sinal == aprox(0.30) and c.condicao_pagamento == "30/60/90"
        assert _item(session, it.id).preco_negociado == aprox(antes)


def test_memoria_e_contexto_registram_o_sinal(session, fornecedores):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    it = _item(session, it.id)
    mem = json.loads(it.memoria_json)
    assert mem["cenario"]["percentual_sinal"] == aprox(0.30)
    assert mem["cenario"]["condicao_pagamento_texto"] == "30% de sinal + 70% em 30/60/90 dias"
    assert mem["fiscal"]["encargo_pct"] == aprox(0.0336) and mem["fiscal"]["encargo_saldo_pct"] == aprox(0.048)


def test_vendedora_ve_sinal_e_condicao_mas_nao_encargo(session, fornecedores, admin):
    from app.routers.cotacoes import _item_para_json
    from app.routers.negociacao import negociacao_atual
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, possui_sinal="sim", percentual_sinal="30")
    corpo = json.loads(bytes(negociacao_atual(RequestFalsa(vend), cot.id, session).body))
    assert encontrar_confidenciais(corpo) == []
    texto = json.dumps(corpo).lower()
    assert "encargo" not in texto and "0.0336" not in texto
    item_json = _item_para_json(_item(session, it.id), pode_ver_economia=False)
    assert "encargo_pct" not in item_json and "encargo_saldo_pct" not in item_json
    assert "percentual_sinal" not in item_json or item_json["percentual_sinal"] == aprox(0.30)


def test_condicao_opaca_legada_fica_desativada_e_o_passo_e_idempotente(session, owner):
    from app import dados_2026_09_21 as dados
    # o seed novo não a cria; simula banco antigo com a linha ativa
    legada = CondicaoPagamento(codigo=dados.CONDICAO_SINAL_LEGADA, label="30% de sinal + 30/60/90",
                               encargo_pct=None, encargo_confirmado=False, ordem=70)
    session.add(legada)
    session.commit()
    assert dados.aplicar_sinal(session, owner)["desativadas"] == 1
    session.commit()
    assert dados.aplicar_sinal(session, owner)["desativadas"] == 0
    session.refresh(legada)
    assert legada.ativo is False and "composição" in (legada.notas or "")
    assert all(c.codigo != dados.CONDICAO_SINAL_LEGADA for c in cfg.condicoes_pagamento(session))
    # e resolver por ela bloqueia (não é mais uma condição aceitável)
    assert resolver_encargo(session.exec(select(CondicaoPagamento)).all(), dados.CONDICAO_SINAL_LEGADA).bloqueado


def test_encargo_saldo_pct_e_nulo_quando_o_saldo_esta_bloqueado(session, fornecedores):
    cot = nova_cotacao(session, condicao_pagamento="CARTAO")
    _regras, ctx = ps.regras_da_cotacao(session, session.get(Cotacao, cot.id))
    assert ctx["bloqueado"] and ctx["encargo_saldo_pct"] is None and ctx["percentual_sinal"] == 0

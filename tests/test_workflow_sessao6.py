"""Sessão 6 — workflow comercial: negociação, aprovação, emissão e revisão.

A pergunta que organiza esta suíte:

    o que o servidor deixa acontecer, e o que ele recusa mesmo que o navegador insista?

Quase todo teste aqui é sobre uma recusa. É o formato certo: um workflow comercial não vale
pelo que permite — vale pelo que impede quando alguém está com pressa.
"""
import inspect
import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import workflow as wf
from app import workflow_service as ws
from app.dinheiro import D, dinheiro
from app.models import (
    AprovacaoCotacao, Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto,
    SnapshotEmissao, StatusCotacao, Usuario,
)
from conftest import RequestFalsa, _novo_usuario

HOJE = date.today()


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def chamar(funcao, request, **kwargs):
    args = {}
    for nome, p in inspect.signature(funcao).parameters.items():
        if nome == "request":
            args[nome] = request
            continue
        if nome in kwargs:
            args[nome] = kwargs[nome]
            continue
        padrao = p.default
        v = getattr(padrao, "default", padrao)
        args[nome] = None if (v is inspect.Parameter.empty
                              or repr(v) == "PydanticUndefined") else v
    return funcao(**args)


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


def aprovador(**kw):
    u = _novo_usuario("OWNER")
    for k, v in kw.items():
        setattr(u, k, v)
    return u


def vendedor(papel="VENDEDOR_INTERNO"):
    return _novo_usuario(papel)


_SEQ = iter(range(1, 9999))


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def cliente(session):
    c = session.exec(select(Cliente)).first()
    if c is None:
        c = Cliente(nome="Cliente WF", estado="São Paulo")
        session.add(c)
        session.commit()
        session.refresh(c)
    return c


def novo_produto(session, fornecedor, custo=100.0, familia="Flat Sheet"):
    sku = f"WF-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia=familia,
                cost_method=CostMethod.national_supplier.value, margem_padrao_pct=0.14)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def nova_cotacao(session, cliente, **kw):
    dados = dict(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                 estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                 condicao_pagamento="30", numero=f"WF-{next(_SEQ):04d}",
                 status=StatusCotacao.rascunho.value, freight_type="FOB")
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def add_item(session, cot, produto, quantidade=5.0, ator=None):
    from app.routers.cotacoes import adicionar_item
    chamar(adicionar_item, RequestFalsa(ator or _novo_usuario("ADMIN")),
           cotacao_id=cot.id, produto_id=produto.id, quantidade=quantidade,
           modo="margem", valor=0.14, session=session)
    session.commit()
    return ws.itens_de(session, cot.id)[-1]


def recomendado_de(item):
    """O preço recomendado **deste cenário** — não o `preco_base` do catálogo.

    A distinção é o que separa "venda interestadual normal" de "desconto": o preço-base foi
    formado com outro destino fiscal e outra condição de pagamento.
    """
    return dinheiro(item.preco_recomendado)


def negociar(session, cot, item, preco, ator=None):
    """Altera o preço negociado pelo caminho real — o servidor recalcula tudo."""
    from app.routers.cotacoes import montar_regras, _aplicar_resultado, _calcular
    produto = session.get(Produto, item.produto_id)
    regras, _r, ctx = montar_regras(cot, session, produto)
    res = _calcular("preco", item.custo_unitario, item.quantidade, float(D(preco)),
                    regras, item.preco_base)
    item.modo_edicao = "preco"
    item.valor_editado = float(D(preco))
    _aplicar_resultado(item, res, regras, ctx)
    session.add(item)
    session.flush()
    ws.invalidar_aprovacoes_obsoletas(session, cot, motivo="preço negociado")
    session.commit()
    session.refresh(item)
    return item


# ===========================================================================
# P0 §53 — desconto com margem boa ainda exige aprovação
# ===========================================================================
def test_p0_preco_abaixo_do_recomendado_exige_aprovacao(session, daune, cliente):
    """A autonomia de desconto do vendedor é zero — mesmo com a margem intacta.

    A aprovação não existe para verificar se sobrou lucro; existe para que abrir mão de
    receita seja decisão de quem tem alçada.
    """
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    recomendado = recomendado_de(item)

    negociar(session, cot, item, recomendado - D("1.00"))
    prontidao = ws.avaliar(session, cot)

    assert prontidao.precisa_aprovacao is True
    motivos = {e.motivo for e in prontidao.excecoes}
    assert wf.PRECO_ABAIXO in motivos
    assert prontidao.pode_emitir is False


def test_preco_acima_do_recomendado_nao_exige_aprovacao(session, daune, cliente):
    """Não criar burocracia onde não há exceção."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) + D("50.00"))

    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao is False
    assert prontidao.pode_emitir is True


def test_margem_abaixo_do_alvo_exige_aprovacao(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("30.00"))

    motivos = {e.motivo for e in ws.avaliar(session, cot).excecoes}
    assert wf.PRECO_ABAIXO in motivos and wf.MARGEM_ABAIXO in motivos


def test_p0_desconto_num_item_nao_se_esconde_atras_de_outro(session, daune, cliente):
    """§15: item A com desconto e item B com acréscimo — A continua sendo exceção."""
    a = novo_produto(session, daune, custo=100.0)
    b = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    ia, ib = add_item(session, cot, a), add_item(session, cot, b)

    negociar(session, cot, ia, recomendado_de(ia) - D("20.00"))
    negociar(session, cot, ib, recomendado_de(ib) + D("80.00"))

    resumo = wf.resumo_comercial(ws.itens_de(session, cot.id))
    assert resumo["total_negociado"] > resumo["total_recomendado"]   # o total disfarça
    excecoes = ws.avaliar(session, cot).excecoes
    assert any(e.motivo == wf.PRECO_ABAIXO and e.escopo == ia.nome_produto
               for e in excecoes)


# ===========================================================================
# P0 §54/§55 — negociação recalcula tudo
# ===========================================================================
def test_p0_negociacao_recalcula_todos_os_componentes(session, daune, cliente):
    """Nenhum componente pode continuar preso ao preço recomendado."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p, quantidade=4.0)
    antes = dict(preco=D(item.preco_negociado), fat=D(item.faturamento),
                 imp=D(item.impostos), com=D(item.comissao_valor),
                 lucro=D(item.lucro), margem=D(item.margem_liquida),
                 markup=D(item.markup_implicito))

    novo_preco = dinheiro(D(item.preco_negociado) - D("25.00"))
    negociar(session, cot, item, novo_preco)

    assert D(item.preco_negociado) == novo_preco
    assert D(item.faturamento) == dinheiro(novo_preco * D("4"))
    for campo, anterior in (("impostos", antes["imp"]), ("comissao_valor", antes["com"]),
                            ("lucro", antes["lucro"]), ("margem_liquida", antes["margem"]),
                            ("markup_implicito", antes["markup"])):
        assert D(getattr(item, campo)) != anterior, f"{campo} não foi recalculado"
    # e a identidade da linha continua fechando sobre a receita NOVA
    assert D(item.faturamento) == (D(item.custo_total) + D(item.impostos)
                                   + D(item.comissao_valor) + D(item.lucro))


def test_p0_comissao_muda_quando_a_negociacao_cruza_a_faixa(session, daune, cliente):
    """A faixa de comissão é função do markup — e o markup é função do preço cobrado."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)

    faixas = set()
    for preco in ("250.00", "300.00", "400.00", "600.00"):
        negociar(session, cot, item, preco)
        faixas.add(D(item.comissao_pct))
    assert len(faixas) > 1, "a comissão não acompanhou o markup"


# ===========================================================================
# P0 §56 — aprovação invalidada por alteração material
# ===========================================================================
def test_p0_alteracao_material_invalida_a_aprovacao(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))

    ator_v = vendedor()
    pedido = ws.solicitar_aprovacao(session, cot, ator=ator_v,
                                    justificativa="cliente fechou volume maior")
    session.commit()
    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True)
    session.commit()
    assert ws.avaliar(session, cot).aprovacao_valida is True
    assert ws.avaliar(session, cot).pode_emitir is True

    # o vendedor mexe no preço depois de aprovado
    negociar(session, cot, item, recomendado_de(item) - D("40.00"))
    session.refresh(pedido)

    assert pedido.status == ws.INVALIDADA
    assert pedido.invalidacao_motivo
    prontidao = ws.avaliar(session, cot)
    assert prontidao.aprovacao_valida is False
    assert prontidao.pode_emitir is False


def test_alteracao_de_quantidade_invalida_aprovacao(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p, quantidade=3.0)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True)
    session.commit()

    item.quantidade = 9.0
    session.add(item)
    session.flush()
    ws.invalidar_aprovacoes_obsoletas(session, cot, motivo="quantidade alterada")
    session.commit()
    session.refresh(pedido)
    assert pedido.status == ws.INVALIDADA


def test_alteracao_nao_material_nao_invalida(session, daune, cliente):
    """§24: nota interna descritiva não muda economia nem contexto — não invalida."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True)
    session.commit()

    cot.observacoes = "cliente pediu entrega em duas remessas"
    session.add(cot)
    session.commit()
    ws.invalidar_aprovacoes_obsoletas(session, cot, motivo="teste")
    session.commit()
    session.refresh(pedido)
    assert pedido.status == ws.APROVADA
    assert ws.avaliar(session, cot).aprovacao_valida is True


# ===========================================================================
# P0 §57 — race condition
# ===========================================================================
def test_p0_aprovador_com_tela_velha_nao_aprova(session, daune, cliente):
    """O aprovador viu F1; o vendedor mudou para F2. Aprovar F1 não pode passar."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    session.commit()
    f1 = pedido.fingerprint

    negociar(session, cot, item, recomendado_de(item) - D("60.00"))
    f2 = wf.fingerprint(cot, ws.itens_de(session, cot.id))
    assert f1 != f2

    with pytest.raises(ws.OperacaoInvalida):
        ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True,
                   fingerprint_visto=f1)
    session.commit()
    assert ws.avaliar(session, cot).aprovacao_valida is False


def test_decidir_duas_vezes_e_idempotente(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True)
    session.commit()
    de_novo = ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=False)
    session.commit()
    assert de_novo.id == pedido.id and de_novo.status == ws.APROVADA


def test_rejeicao_devolve_a_cotacao_para_edicao(session, daune, cliente):
    """§25: rejeitar não apaga nem encerra — devolve para o vendedor ajustar."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    session.commit()
    assert cot.status == StatusCotacao.aguardando_aprovacao.value

    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=False,
               comentario="desconto alto demais")
    session.commit()

    assert pedido.status == ws.REJEITADA and pedido.comentario
    assert cot.status == StatusCotacao.rascunho.value
    assert session.get(AprovacaoCotacao, pedido.id) is not None    # não foi apagado
    # e o vendedor consegue mexer de novo
    negociar(session, cot, item, recomendado_de(item) - D("2.00"))


# ===========================================================================
# P0 §58 — emitido é imutável
# ===========================================================================
def _emitir(session, cot):
    return ws.emitir(session, cot, ator=aprovador())


def test_p0_emitida_e_imutavel(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    snapshot = _emitir(session, cot)
    session.commit()

    assert cot.status == StatusCotacao.emitida.value
    assert snapshot.fingerprint == cot.fingerprint
    congelado = (item.preco_negociado, item.quantidade, item.faturamento)

    from app.routers.cotacoes import adicionar_item, atualizar_cabecalho, remover_item
    req = RequestFalsa(aprovador())
    for funcao, kw in ((adicionar_item, dict(produto_id=p.id, quantidade=1.0,
                                             modo="margem", valor=0.14)),
                       (remover_item, dict(item_id=item.id)),
                       (atualizar_cabecalho, dict(condicao_pagamento="30/60"))):
        with pytest.raises(HTTPException) as erro:
            chamar(funcao, req, cotacao_id=cot.id, session=session, **kw)
        assert erro.value.status_code == 409

    session.refresh(item)
    assert (item.preco_negociado, item.quantidade, item.faturamento) == congelado


def test_emitir_duas_vezes_nao_cria_dois_snapshots(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    s1 = _emitir(session, cot)
    session.commit()
    s2 = _emitir(session, cot)
    session.commit()
    assert s1.id == s2.id
    todos = session.exec(select(SnapshotEmissao)
                         .where(SnapshotEmissao.cotacao_id == cot.id)).all()
    assert len(todos) == 1


def test_snapshot_congela_o_documento(session, daune, cliente):
    """§40: reconstruir o emitido não pode depender de lookup vivo."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    snap = _emitir(session, cot)
    session.commit()

    itens = json.loads(snap.itens_json)
    assert itens[0]["nome"] == item.nome_produto
    assert D(itens[0]["preco_negociado"]) == D(item.preco_negociado)
    assert itens[0]["custo_referencia_id"] == item.custo_referencia_id
    assert json.loads(snap.fiscal_json)["estado_destino"] == cot.estado_destino
    assert json.loads(snap.cliente_json)["nome"] == cliente.nome
    assert snap.emitido_por and snap.emitido_em


def test_snapshot_pina_a_aprovacao_usada(session, daune, cliente):
    """§41: não basta 'aprovado = sim' — qual decisão, exatamente."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    ws.decidir(session, cot, pedido.id, ator=aprovador(), aprovar=True)
    session.commit()

    snap = _emitir(session, cot)
    session.commit()
    assert snap.aprovacao_id == pedido.id
    assert snap.aprovacao_fingerprint == pedido.fingerprint == snap.fingerprint


# ===========================================================================
# P0 §59 — revisão
# ===========================================================================
def test_p0_revisao_preserva_a_emitida(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    _emitir(session, cot)
    session.commit()
    congelado = (cot.status, cot.fingerprint, item.preco_negociado)

    r2 = ws.criar_revisao(session, cot, ator=aprovador())
    session.commit()

    assert r2.revisao == 2 and r2.status == StatusCotacao.rascunho.value
    assert r2.cotacao_origem_id == cot.id
    assert r2.numero == cot.numero                       # numeração preservada
    assert len(ws.itens_de(session, r2.id)) == 1         # itens copiados
    session.refresh(cot)
    session.refresh(item)
    assert (cot.status, cot.fingerprint, item.preco_negociado) == congelado
    # a revisão nova não herda aprovação
    assert ws.aprovacoes_de(session, r2.id) == []
    assert len(ws.revisoes_de(session, r2)) == 2


def test_revisao_so_a_partir_de_emitida(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    with pytest.raises(ws.OperacaoInvalida):
        ws.criar_revisao(session, cot, ator=aprovador())


# ===========================================================================
# P0 §60/§61 — blockers × ESTIMADO
# ===========================================================================
def test_p0_a_cotar_nao_e_aprovavel(session, daune, cliente):
    """Aprovação é decisão comercial. Ela não cria o número que falta."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    item.status_custo_item = "A_COTAR"
    session.add(item)
    session.commit()

    prontidao = ws.avaliar(session, cot)
    assert prontidao.pode_emitir is False
    assert any(b.codigo == "CUSTO_A_COTAR" for b in prontidao.blockers)

    # nem mesmo o OWNER emite
    with pytest.raises(ws.OperacaoInvalida):
        ws.emitir(session, cot, ator=aprovador())


def test_review_required_fiscal_bloqueia(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    item.status_fiscal = "REVIEW_REQUIRED"
    item.motivo_fiscal = "origem fiscal indeterminada"
    session.add(item)
    session.commit()
    assert ws.avaliar(session, cot).pode_emitir is False


def test_frete_cif_irresolvido_bloqueia_e_fob_nao(session, daune, cliente):
    """§45: aprovação não transforma frete desconhecido em zero. FOB não bloqueia."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente, freight_type="CIF")
    add_item(session, cot, p)
    session.commit()
    frete_ruim = {"cif": True, "status": "FRETE_A_COTAR",
                  "motivo": "cidade fora da cobertura"}
    assert ws.avaliar(session, cot, frete=frete_ruim).pode_emitir is False

    cot.freight_type = "FOB"
    session.add(cot)
    session.commit()
    assert ws.avaliar(session, cot, frete=frete_ruim).pode_emitir is True


def test_p0_estimado_emite_mas_nao_compromete(session, daune, cliente):
    """ESTIMADO forma proposta; compromisso firme, não — o número veio de proxy."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    item.status_custo_item = "ESTIMADO"
    item.confirmation_pending = True
    session.add(item)
    session.commit()

    prontidao = ws.avaliar(session, cot)
    assert prontidao.pode_emitir is True
    assert prontidao.precisa_aprovacao is False       # §43: não vira burocracia

    _emitir(session, cot)
    session.commit()
    assert cot.status == StatusCotacao.emitida.value

    compromisso = ws.validar_compromisso_firme(session, cot)
    assert compromisso.pode is False
    assert any("ESTIMADO" in m for m in compromisso.impedimentos)


def test_compromisso_firme_exige_emissao(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()
    c = ws.validar_compromisso_firme(session, cot)
    assert c.pode is False
    assert any("não foi emitida" in m for m in c.impedimentos)


def test_revalidar_bloqueia_compromisso_mas_nao_a_proposta(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    item.status_custo_item = "REVALIDAR"
    session.add(item)
    session.commit()
    assert ws.avaliar(session, cot).pode_emitir is True
    _emitir(session, cot)
    session.commit()
    assert any("REVALIDAR" in m
               for m in ws.validar_compromisso_firme(session, cot).impedimentos)


# ===========================================================================
# P0 §62 — premissa desatualizada
# ===========================================================================
def test_p0_premissa_desatualizada_nao_emite_em_silencio(session, daune, cliente):
    from app import admin_service as adm
    from app import custo_service as cs

    p = novo_produto(session, daune, custo=100.0)
    prop = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="CONFIRMADO",
                                 fonte="V1")
    adm.aplicar_custo_sku(session, p.id, prop, ator=aprovador(), cnet_brl="100.00",
                          status="CONFIRMADO", fonte="V1")
    session.commit()

    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()
    assert ws.avaliar(session, cot).pode_emitir is True

    # entra V2 — o rascunho NÃO muda sozinho
    prop2 = adm.preview_custo_sku(session, p.id, cnet_brl="140.00", status="CONFIRMADO",
                                  fonte="V2 — tabela nova")
    adm.aplicar_custo_sku(session, p.id, prop2, ator=aprovador(), cnet_brl="140.00",
                          status="CONFIRMADO", fonte="V2 — tabela nova")
    session.commit()

    item = ws.itens_de(session, cot.id)[0]
    assert D(item.custo_unitario) == D("100.00")        # continua V1
    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao is True
    assert wf.PREMISSA_VELHA in {e.motivo for e in prontidao.excecoes}
    assert prontidao.pode_emitir is False               # não sai em silêncio

    # escolha B: manter a premissa antiga, com alçada e motivo
    ws.manter_premissas_antigas(session, cot, ator=aprovador(),
                                motivo="proposta já negociada com o cliente")
    session.commit()
    assert ws.avaliar(session, cot).pode_emitir is True


def test_manter_premissa_antiga_exige_alcada_e_motivo(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()
    with pytest.raises(HTTPException) as erro:
        ws.manter_premissas_antigas(session, cot, ator=vendedor(), motivo="porque sim")
    assert erro.value.status_code == 403
    with pytest.raises(ws.OperacaoInvalida):
        ws.manter_premissas_antigas(session, cot, ator=aprovador(), motivo="")


# ===========================================================================
# P0 §63/§64 — alçada
# ===========================================================================
@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_vendedor_nao_aprova(session, daune, cliente, papel):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(papel), justificativa="x")
    session.commit()

    with pytest.raises(HTTPException) as erro:
        ws.decidir(session, cot, pedido.id, ator=vendedor(papel), aprovar=True)
    assert erro.value.status_code == 403
    session.refresh(pedido)
    assert pedido.status == ws.PENDENTE


def test_p0_admin_sem_alcada_nao_aprova(session, daune, cliente):
    """§46: administrar premissa não é autorizar desconto. São permissões diferentes."""
    so_economia = Usuario(id=77, email="premissas@anara.test", nome="P", senha_hash="h",
                          papel="ADMIN", can_manage_economics=True,
                          can_approve_quotes=False)
    com_alcada = Usuario(id=78, email="alcada@anara.test", nome="A", senha_hash="h",
                         papel="ADMIN", can_manage_economics=False,
                         can_approve_quotes=True)
    assert so_economia.gerencia_economia is True and so_economia.aprova_cotacoes is False
    assert com_alcada.aprova_cotacoes is True

    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    session.commit()

    with pytest.raises(HTTPException) as erro:
        ws.decidir(session, cot, pedido.id, ator=so_economia, aprovar=True)
    assert erro.value.status_code == 403

    decidido = ws.decidir(session, cot, pedido.id, ator=com_alcada, aprovar=True)
    session.commit()
    assert decidido.status == ws.APROVADA


def test_papel_forjado_no_formulario_nao_aprova(session, daune, cliente):
    import app.routers.workflow as rw

    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="x")
    session.commit()

    with pytest.raises(HTTPException) as erro:
        chamar(rw.aprovar, RequestFalsa(vendedor()), cotacao_id=cot.id,
               pedido_id=pedido.id, comentario="", fingerprint="", session=session,
               can_approve_quotes=True, papel="OWNER", approval_required=False)
    assert erro.value.status_code == 403


def test_pedido_sem_excecao_e_recusado(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()
    with pytest.raises(ws.OperacaoInvalida):
        ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="quero")


def test_pedido_de_excecao_exige_justificativa(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    with pytest.raises(ws.OperacaoInvalida):
        ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="  ")


# ===========================================================================
# Transições
# ===========================================================================
@pytest.mark.parametrize("de,para", [
    (wf.DRAFT, wf.SENT),
    (wf.PENDING_APPROVAL, wf.ISSUED),
    (wf.ISSUED, wf.DRAFT),
    (wf.SENT, wf.DRAFT),
    (wf.CANCELLED, wf.DRAFT),
    (wf.CANCELLED, wf.ISSUED),
])
def test_transicoes_invalidas_sao_recusadas(de, para):
    assert not wf.pode_transicionar(de, para)
    with pytest.raises(wf.TransicaoInvalida):
        wf.exigir_transicao(de, para)


def test_estado_legado_nao_entra_no_workflow(session, daune, cliente):
    """As 18 cotações históricas não são reescritas nem movidas por aqui."""
    with pytest.raises(wf.TransicaoInvalida):
        wf.exigir_transicao("perdida", wf.ISSUED)


def test_enviar_e_cancelar(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    _emitir(session, cot)
    session.commit()

    ws.marcar_enviada(session, cot, ator=aprovador())
    session.commit()
    assert cot.status == StatusCotacao.enviada.value and cot.sent_em and cot.sent_por
    ws.marcar_enviada(session, cot, ator=aprovador())     # idempotente
    session.commit()

    ws.cancelar(session, cot, ator=aprovador(), motivo="cliente desistiu")
    session.commit()
    assert cot.status == StatusCotacao.cancelada.value
    assert cot.cancelamento_motivo == "cliente desistiu"
    # cancelar não apaga o snapshot
    assert session.exec(select(SnapshotEmissao)
                        .where(SnapshotEmissao.cotacao_id == cot.id)).all()


def test_cancelar_exige_motivo(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()
    with pytest.raises(ws.OperacaoInvalida):
        ws.cancelar(session, cot, ator=aprovador(), motivo="")


# ===========================================================================
# Fingerprint
# ===========================================================================
def test_fingerprint_e_estavel_e_sensivel(session, daune, cliente):
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    itens = ws.itens_de(session, cot.id)

    f1 = wf.fingerprint(cot, itens)
    assert wf.fingerprint(cot, itens) == f1            # estável
    # reformatar um número não muda o hash
    item.quantidade = float(D(item.quantidade))
    assert wf.fingerprint(cot, ws.itens_de(session, cot.id)) == f1
    # mudar o destino fiscal muda
    cot.estado_destino = "Minas Gerais"
    assert wf.fingerprint(cot, itens) != f1


# ===========================================================================
# §65 — confidencialidade não regride
# ===========================================================================
@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_situacao_nao_vaza_economia_para_vendedor(session, daune, cliente, papel):
    import app.routers.workflow as rw

    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))

    dados = corpo(chamar(rw.situacao, RequestFalsa(vendedor(papel)), cotacao_id=cot.id,
                         session=session))
    assert dados["precisa_aprovacao"] is True          # ele precisa saber disso
    for e in dados["excecoes"]:
        assert set(e) == {"motivo", "escopo"}          # e só isso
    texto = json.dumps(dados)
    for proibido in ("margem_alvo", "margem_real", "preco_recomendado", "custo", "lucro"):
        assert proibido not in texto


def test_situacao_leva_economia_para_admin(session, daune, cliente):
    import app.routers.workflow as rw

    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("5.00"))
    dados = corpo(chamar(rw.situacao, RequestFalsa(_novo_usuario("ADMIN")),
                         cotacao_id=cot.id, session=session))
    assert any(e.get("preco_recomendado") for e in dados["excecoes"])


def test_pdf_de_rascunho_e_marcado_e_nao_vaza_economia(session, daune, cliente):
    """§34 e §65: preview marcado, e sem custo/margem — a Sessão 4 não regride."""
    import app.pdf_bridge as pb
    from app.confidencial import encontrar_confidenciais

    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    session.commit()

    capturado = {}
    original = pb._gerar_cotacao.build_pdf
    pb._gerar_cotacao.build_pdf = lambda o, h, i, t: capturado.update(
        header=h, items=i, totals=t) or original(o, h, i, t)
    try:
        pb.gerar_pdf_para_cotacao(cot, cliente, ws.itens_de(session, cot.id),
                                  rascunho=True)
    finally:
        pb._gerar_cotacao.build_pdf = original

    assert "RASCUNHO" in capturado["header"]["numero"]
    assert encontrar_confidenciais(capturado) == []
    for item in capturado["items"]:
        for proibido in ("custo", "margem", "lucro", "cnet", "comissao"):
            assert proibido not in item


# ===========================================================================
# §66 — histórico legado
# ===========================================================================
def test_historico_legado_nao_e_falsificado(session):
    """Cotações de 2026 não passaram por approval workflow. Não inventar que passaram."""
    from sqlalchemy import create_engine
    import os
    from sqlmodel import Session as S

    caminho = os.path.abspath("data/anara.db")
    eng = create_engine(f"sqlite:///file:{caminho}?mode=ro&uri=true")
    with S(eng) as prod:
        cotacoes = prod.exec(select(Cotacao)).all()
        assert len(cotacoes) == 18
        assert {c.status for c in cotacoes} <= {"rascunho", "perdida"}
        assert all(c.revisao == 1 for c in cotacoes)
        assert all(c.fingerprint is None for c in cotacoes)
        assert all(c.issued_em is None and c.sent_em is None for c in cotacoes)
        assert prod.exec(select(AprovacaoCotacao)).all() == []
        assert prod.exec(select(SnapshotEmissao)).all() == []
        itens = prod.exec(select(CotacaoItem)).all()
        assert len(itens) == 45
        assert all(i.status_custo_item is None for i in itens)


# ===========================================================================
# PROVA 1 — desconto é detectado POR ITEM, com os números do escopo
# ===========================================================================
def _fixar_recomendado(session, item, recomendado):
    """Fixa o preço recomendado do item para o cenário exato do escopo.

    O recomendado é calculado pelo motor a partir do custo e da margem-alvo; para
    exercitar 100/95 e 100/110 de forma legível, ele é fixado aqui. O que está sob teste é
    a **detecção da exceção**, não a formação do recomendado — essa tem suíte própria.
    """
    item.preco_recomendado = float(D(recomendado))
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_prova_desconto_item_level_95_e_110(session, daune, cliente):
    """A 100→95 e B 100→110. Total 200→205, acima do recomendado — e A continua exceção.

    É o ponto inteiro da regra ser por item: sem ela, bastaria subir o preço de um SKU para
    fazer o desconto de outro desaparecer do radar de quem aprova.
    """
    a = novo_produto(session, daune, custo=50.0)
    b = novo_produto(session, daune, custo=50.0)
    cot = nova_cotacao(session, cliente)
    ia = add_item(session, cot, a, quantidade=1.0)
    ib = add_item(session, cot, b, quantidade=1.0)

    _fixar_recomendado(session, ia, "100.00")
    _fixar_recomendado(session, ib, "100.00")
    negociar(session, cot, ia, "95.00")
    _fixar_recomendado(session, ia, "100.00")     # a negociação não move o recomendado
    negociar(session, cot, ib, "110.00")
    _fixar_recomendado(session, ib, "100.00")

    resumo = wf.resumo_comercial(ws.itens_de(session, cot.id))
    assert D(resumo["total_recomendado"]) == D("200.00")
    assert D(resumo["total_negociado"]) == D("205.00")
    assert D(resumo["diferenca"]) == D("5.00")            # o total está ACIMA

    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao is True
    do_a = [e for e in prontidao.excecoes
            if e.motivo == wf.PRECO_ABAIXO and e.escopo == ia.nome_produto]
    assert len(do_a) == 1
    assert D(do_a[0].preco_recomendado) == D("100.00")
    assert D(do_a[0].preco_negociado) == D("95.00")
    assert D(do_a[0].diferenca) == D("-5.00")
    # e o item B, que subiu, não gera exceção de preço
    assert not [e for e in prontidao.excecoes
                if e.motivo == wf.PRECO_ABAIXO and e.escopo == ib.nome_produto]
    assert prontidao.pode_emitir is False


def test_prova_controle_sem_desconto_nao_exige_aprovacao(session, daune, cliente):
    """Controle: todos os itens no recomendado ou acima, margem no alvo → sem aprovação."""
    a = novo_produto(session, daune, custo=50.0)
    b = novo_produto(session, daune, custo=50.0)
    cot = nova_cotacao(session, cliente)
    ia = add_item(session, cot, a, quantidade=1.0)
    ib = add_item(session, cot, b, quantidade=1.0)

    # exatamente no recomendado, e acima dele
    negociar(session, cot, ia, recomendado_de(ia))
    negociar(session, cot, ib, recomendado_de(ib) + D("10.00"))

    prontidao = ws.avaliar(session, cot)
    assert prontidao.excecoes == []
    assert prontidao.precisa_aprovacao is False
    assert prontidao.pode_emitir is True
    with pytest.raises(ws.OperacaoInvalida):
        ws.solicitar_aprovacao(session, cot, ator=vendedor(), justificativa="sem exceção")


# ===========================================================================
# PROVA 2 — REVALIDAR: proposta sim, compromisso não, e a saída pela reconfirmação
# ===========================================================================
def test_prova_revalidar_proposta_compromisso_e_reconfirmacao(session, daune, cliente):
    """REVALIDAR não impede a proposta, impede o compromisso — e a reconfirmação libera.

    Sem a última parte, o bloqueio seria eterno: o item emitido é imutável, seu
    `status_custo_item` nunca mudaria, e reconfirmar o custo no cadastro não teria efeito
    nenhum. O preço do documento continua congelado; o que muda é a resposta a "posso me
    comprometer hoje?".
    """
    from app import admin_service as adm
    from app import custo_service as cs

    p = novo_produto(session, daune, custo=100.0)
    prop = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="REVALIDAR",
                                 fonte="tabela de 2025, envelhecida")
    adm.aplicar_custo_sku(session, p.id, prop, ator=aprovador(), cnet_brl="100.00",
                          status="REVALIDAR", fonte="tabela de 2025, envelhecida")
    session.commit()

    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    session.commit()
    assert item.status_custo_item == "REVALIDAR"

    # --- 1. a proposta sai ---
    prontidao = ws.avaliar(session, cot)
    assert prontidao.pode_emitir is True
    assert prontidao.precisa_aprovacao is False
    _emitir(session, cot)
    session.commit()
    assert cot.status == StatusCotacao.emitida.value

    # --- 2. o compromisso firme, não ---
    antes = ws.validar_compromisso_firme(session, cot)
    assert antes.pode is False
    assert any("REVALIDAR" in m for m in antes.impedimentos)

    # --- 3. a reconfirmação libera, sem tocar no preço ---
    congelado = (item.preco_negociado, item.custo_unitario, item.status_custo_item,
                 item.custo_referencia_id)
    prop2 = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="CONFIRMADO",
                                  fonte="fornecedor reconfirmou em 05/09")
    assert prop2.linhas[0].situacao in (adm.MUDANCA, adm.RECONFIRMACAO)
    adm.aplicar_custo_sku(session, p.id, prop2, ator=aprovador(), cnet_brl="100.00",
                          status="CONFIRMADO", fonte="fornecedor reconfirmou em 05/09")
    session.commit()

    depois = ws.validar_compromisso_firme(session, cot)
    assert not any("REVALIDAR" in m for m in depois.impedimentos)
    assert depois.pode is True

    # o item NÃO foi promovido: continua dizendo como o preço se formou
    session.refresh(item)
    assert (item.preco_negociado, item.custo_unitario, item.status_custo_item,
            item.custo_referencia_id) == congelado
    assert cs.referencia_vigente(session, p.id).status_custo == "CONFIRMADO"


def test_prova_estimado_tambem_libera_apos_confirmacao(session, daune, cliente):
    """A mesma porta vale para ESTIMADO — e o item continua marcado como estimado."""
    from app import admin_service as adm

    p = novo_produto(session, daune, custo=100.0)
    prop = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="ESTIMADO",
                                 fonte="curva de análogos")
    adm.aplicar_custo_sku(session, p.id, prop, ator=aprovador(), cnet_brl="100.00",
                          status="ESTIMADO", fonte="curva de análogos")
    session.commit()

    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    _emitir(session, cot)
    session.commit()
    assert item.confirmation_pending is True
    assert ws.validar_compromisso_firme(session, cot).pode is False

    prop2 = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="CONFIRMADO",
                                  fonte="KTC confirmou o EXW")
    adm.aplicar_custo_sku(session, p.id, prop2, ator=aprovador(), cnet_brl="100.00",
                          status="CONFIRMADO", fonte="KTC confirmou o EXW")
    session.commit()

    assert ws.validar_compromisso_firme(session, cot).pode is True
    session.refresh(item)
    assert item.confirmation_pending is True        # o item não foi reescrito


def test_reconfirmacao_para_estimado_nao_libera(session, daune, cliente):
    """Versão nova que continua ESTIMADA não é confirmação — e não abre a porta."""
    from app import admin_service as adm

    p = novo_produto(session, daune, custo=100.0)
    prop = adm.preview_custo_sku(session, p.id, cnet_brl="100.00", status="ESTIMADO",
                                 fonte="curva A")
    adm.aplicar_custo_sku(session, p.id, prop, ator=aprovador(), cnet_brl="100.00",
                          status="ESTIMADO", fonte="curva A")
    session.commit()
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    _emitir(session, cot)
    session.commit()

    prop2 = adm.preview_custo_sku(session, p.id, cnet_brl="105.00", status="ESTIMADO",
                                  fonte="curva B")
    adm.aplicar_custo_sku(session, p.id, prop2, ator=aprovador(), cnet_brl="105.00",
                          status="ESTIMADO", fonte="curva B")
    session.commit()
    assert ws.validar_compromisso_firme(session, cot).pode is False


def test_a_cotar_continua_bloqueando_o_compromisso(session, daune, cliente):
    """A porta da reconfirmação não vale para blocker duro — ele nem emite."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    item.status_custo_item = "A_COTAR"
    session.add(item)
    session.commit()
    c = ws.validar_compromisso_firme(session, cot)
    assert c.pode is False
    assert any("A_COTAR" in m or "não foi emitida" in m for m in c.impedimentos)


# ===========================================================================
# PROVA 3 — genealogia: R1 continua apontando para a aprovação A / fingerprint F1
# ===========================================================================
def test_prova_genealogia_da_aprovacao_usada_na_emissao(session, daune, cliente):
    """Depois de outras decisões e de uma revisão, R1 ainda diz QUAL aprovação a autorizou.

    A resposta não pode vir de "a aprovação aprovada mais recente": na genealogia existem
    várias, de revisões diferentes. Ela vem do pino no snapshot.
    """
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    item = add_item(session, cot, p)
    negociar(session, cot, item, recomendado_de(item) - D("7.00"))

    pedido_a = ws.solicitar_aprovacao(session, cot, ator=vendedor(),
                                      justificativa="volume anual")
    session.commit()
    f1 = pedido_a.fingerprint
    ws.decidir(session, cot, pedido_a.id, ator=aprovador(), aprovar=True,
               comentario="autorizado")
    session.commit()

    snap_r1 = _emitir(session, cot)
    session.commit()
    assert snap_r1.aprovacao_id == pedido_a.id
    assert snap_r1.aprovacao_fingerprint == f1 == snap_r1.fingerprint

    # --- a genealogia continua: revisão 2, com desconto diferente e OUTRA aprovação ---
    r2 = ws.criar_revisao(session, cot, ator=aprovador())
    session.commit()
    item2 = ws.itens_de(session, r2.id)[0]
    negociar(session, r2, item2, recomendado_de(item2) - D("25.00"))
    pedido_b = ws.solicitar_aprovacao(session, r2, ator=vendedor(),
                                      justificativa="cliente pediu mais desconto")
    session.commit()
    ws.decidir(session, r2, pedido_b.id, ator=aprovador(), aprovar=True)
    session.commit()
    snap_r2 = ws.emitir(session, r2, ator=aprovador())
    session.commit()

    assert pedido_b.id != pedido_a.id
    assert snap_r2.fingerprint != f1

    # --- reconstruindo R1: continua sendo a aprovação A, para o fingerprint F1 ---
    session.refresh(snap_r1)
    assert snap_r1.aprovacao_id == pedido_a.id
    assert snap_r1.aprovacao_fingerprint == f1
    assert snap_r1.revisao == 1
    usada = session.get(AprovacaoCotacao, snap_r1.aprovacao_id)
    assert usada.status == ws.APROVADA and usada.fingerprint == f1
    assert usada.justificativa == "volume anual" and usada.comentario == "autorizado"

    # e a "aprovada mais recente" da genealogia é OUTRA — o que provaria a busca errada
    todas = ws.aprovacoes_de(session, cot.id) + ws.aprovacoes_de(session, r2.id)
    aprovadas = [a for a in todas if a.status == ws.APROVADA]
    mais_recente = max(aprovadas, key=lambda a: (a.decidido_em, a.id))
    assert mais_recente.id == pedido_b.id
    assert snap_r1.aprovacao_id != mais_recente.id


def test_snapshot_sem_excecao_nao_inventa_aprovacao(session, daune, cliente):
    """Cotação sem exceção emite sem aprovação — e o snapshot diz isso, não finge."""
    p = novo_produto(session, daune, custo=100.0)
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    snap = _emitir(session, cot)
    session.commit()
    assert snap.aprovacao_id is None and snap.aprovacao_fingerprint is None

"""CR-01/CR-02/CR-03 — mudar o cenário reforma TODOS os preços; editar quantidade não congela
o preço; "Atualizar e recalcular" leva a alavanca à política vigente.

Bug reproduzido em 17/09/2026: a tela mandava `modo=preco` ao editar a quantidade; o item
virava preço fixo; a troca de destino SP→RJ não contribuinte deixava R$ 69,88 onde o motor
formava R$ 76,38 (R$ 6,50/un abaixo). E um item editado com o cenário bloqueado ficava com
R$ 0,00 para sempre.
"""
from decimal import Decimal

import pytest
from sqlmodel import select

from app import comercial_service as com
from app import workflow_service as ws
from app.dinheiro import D, dinheiro
from app.models import Cotacao, CotacaoItem
from tests.crisis.conftest import (add_item, editar_quantidade, nova_cotacao, produto_nacional,
                                   recomendado_para, salvar_cabecalho, tabela_para)

TRANSICOES = [
    ("Rio de Janeiro", "nao"), ("São Paulo", "sim"), ("Minas Gerais", "sim"),
    ("Rio de Janeiro", "sim"), ("São Paulo", "nao"),
]


def _item(session, item_id):
    session.expire_all()
    return session.get(CotacaoItem, item_id)


def test_cr01_mudar_destino_e_contribuinte_reforma_todos_os_itens(session, fornecedores, admin):
    cot = nova_cotacao(session)
    a = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    b = add_item(session, cot, produto_nacional(session, fornecedores, custo=250.0), 3)
    for destino, contribuinte in TRANSICOES:
        loc = salvar_cabecalho(session, cot, estado_destino=destino, contribuinte_icms=contribuinte)
        for it in (a, b):
            it = _item(session, it.id)
            esperado, ctx = recomendado_para(session, cot, it)
            assert ctx["status_fiscal"] == "OK", (destino, contribuinte, ctx.get("motivo_bloqueio"))
            assert dinheiro(D(it.preco_negociado)) == dinheiro(esperado), (destino, contribuinte, it.nome_produto)
            assert dinheiro(D(it.preco_recomendado)) == dinheiro(esperado)
            assert it.uf_destino_fiscal == ctx["uf_destino_fiscal"]
            assert it.modo_edicao == "margem"
        assert "cenario=atualizado" in loc


def test_cr01_preco_negociado_nao_sobrevive_ao_cenario_novo(session, fornecedores, admin):
    """Política de 21/09/2026: a alavanca que sobrevive é o DESCONTO sobre a tabela; o preço
    absoluto de outro cenário nunca fica. Aqui a proposta ficou ACIMA do B2B (desconto menor
    que 50%): no cenário novo, B2B e tabela são refeitos e o mesmo desconto é reaplicado."""
    from app.pricing_engine import desconto_vs_tabela, preco_por_desconto
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    assert it.modo_edicao == "margem" and D(it.desconto_vs_tabela_pct) == Decimal("0.5")
    negociado = float(dinheiro(D(it.preco_negociado) * Decimal("1.10")))   # acima do B2B
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: negociado}, ator=admin)
    session.commit()
    it = _item(session, it.id)
    assert it.modo_edicao == "desconto" and it.modo_negociacao == "preco"
    desconto = D(it.valor_editado)
    # a coluna é REAL: o desconto persistido é o exato até a precisão do float (≈1e-16)
    assert abs(desconto - desconto_vs_tabela(D(negociado), D(it.preco_tabela))) < Decimal("1e-12")
    assert desconto < Decimal("0.5")
    salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    it = _item(session, it.id)
    b2b, _ctx = recomendado_para(session, cot, it)
    tabela, _ctx = tabela_para(session, cot, it)
    assert dinheiro(D(it.preco_recomendado)) == dinheiro(b2b)
    assert dinheiro(D(it.preco_tabela)) == tabela
    assert dinheiro(D(it.preco_negociado)) == preco_por_desconto(tabela, desconto)
    assert it.preco_negociado != negociado                     # o preço absoluto mudou com o cenário
    assert it.modo_edicao == "desconto" and D(it.valor_editado) == desconto
    assert D(it.preco_negociado) >= D(it.preco_recomendado)   # continua dentro da autonomia


def test_cr01_desconto_abaixo_do_b2b_no_cenario_novo_vira_excecao_e_nao_e_ajustado(session, fornecedores, admin):
    """Se o desconto negociado levar abaixo do B2B do cenário novo, o preço NÃO é ajustado em
    silêncio: fica como negociado e a cotação passa a exigir aprovação."""
    from app.pricing_engine import preco_por_desconto
    cot = nova_cotacao(session, estado_destino="Minas Gerais", contribuinte_icms=True)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    # 52% de desconto: abaixo do B2B já neste cenário → exceção
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.52"}}, ator=admin)
    session.commit()
    it = _item(session, it.id)
    assert it.modo_edicao == "desconto" and it.modo_negociacao == "desconto"
    assert D(it.preco_negociado) < D(it.preco_recomendado)
    assert ws.avaliar(session, session.get(Cotacao, cot.id)).precisa_aprovacao is True
    salvar_cabecalho(session, cot, estado_destino="São Paulo", contribuinte_icms="nao")
    it = _item(session, it.id)
    tabela, _ctx = tabela_para(session, cot, it)
    assert dinheiro(D(it.preco_negociado)) == preco_por_desconto(tabela, Decimal("0.52"))
    assert D(it.preco_negociado) < D(it.preco_recomendado), "não foi puxado para o B2B"
    prontidao = ws.avaliar(session, session.get(Cotacao, cot.id))
    assert prontidao.precisa_aprovacao is True
    assert "PRECO_ABAIXO_B2B" in {e.motivo for e in prontidao.excecoes}


def test_cr01_salvar_sem_mudanca_nao_mexe_em_preco_negociado(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    negociado = float(dinheiro(D(it.preco_negociado) * Decimal("0.98")))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: negociado}, ator=admin)
    session.commit()
    loc = salvar_cabecalho(session, cot, observacoes="só uma nota interna")
    assert "salvo=1" in loc
    assert D(_item(session, it.id).preco_negociado) == D(negociado)


def test_cr01_finalidade_ou_origem_alteradas_fora_do_formulario_reformam_ao_salvar(session, fornecedores, admin):
    cot = nova_cotacao(session, estado_destino="Minas Gerais")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    assert _item(session, it.id).finalidade == "REVENDA"
    c = session.get(Cotacao, cot.id)
    c.finalidade = "USO_CONSUMO"
    session.add(c)
    session.commit()
    assert com.cenario_dos_itens_divergiu(session, session.get(Cotacao, cot.id))
    loc = salvar_cabecalho(session, cot)                        # nada mudou no formulário
    assert "cenario=atualizado" in loc
    it = _item(session, it.id)
    assert it.finalidade == "USO_CONSUMO" and it.consumidor_final is True
    assert not com.cenario_dos_itens_divergiu(session, session.get(Cotacao, cot.id))


def test_cr01_cenario_que_bloqueia_zera_o_preco_e_grava_o_bloqueio(session, fornecedores, admin):
    # Desde 21/09/2026 as 27 UFs resolvem para as famílias do escopo; o que continua
    # DESCONHECIDO (e bloqueia) é família fora do escopo reconciliado de FCP.
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0,
                                                 familia="Cortina Blackout"))
    assert it.preco_negociado > 0
    salvar_cabecalho(session, cot, estado_destino="Bahia", contribuinte_icms="nao")   # FCP desconhecido p/ a família
    it = _item(session, it.id)
    assert it.status_fiscal == "REVIEW_REQUIRED" and (it.preco_negociado or 0) == 0
    from app import workflow as wf
    assert any(b.codigo == "FISCAL_REVIEW_REQUIRED" for b in wf.blockers_do_item(it))
    salvar_cabecalho(session, cot, estado_destino="São Paulo", contribuinte_icms="sim")
    it = _item(session, it.id)
    assert it.status_fiscal == "OK" and it.preco_negociado > 0


def test_cr02_editar_quantidade_mantem_a_alavanca_do_item(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    editar_quantidade(session, cot, it, 25)
    it = _item(session, it.id)
    assert it.quantidade == 25 and it.modo_edicao == "margem"
    assert D(it.valor_editado) == D(it.margem_padrao_pct)
    # mesmo que a tela antiga mande modo=preco com o preço corrente, depois de um cenário novo
    # o preço é o do cenário
    editar_quantidade(session, cot, it, 30, modo="preco", valor=it.preco_negociado)
    # política 21/09: `modo=preco` vira desconto sobre a tabela (aqui 50% = o próprio B2B)
    it = _item(session, it.id)
    assert it.modo_edicao == "desconto" and D(it.valor_editado) == Decimal("0.5")
    salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    it = _item(session, it.id)
    esperado, _ctx = recomendado_para(session, cot, it)
    assert dinheiro(D(it.preco_negociado)) == dinheiro(esperado), "50% da tabela nova = B2B novo"


def test_cr02_preco_zero_nunca_fica_permanente(session, fornecedores, admin):
    cot = nova_cotacao(session, estado_destino="Bahia", contribuinte_icms=False, finalidade="USO_CONSUMO")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0,
                                                 familia="Cortina Blackout"))
    assert (it.preco_negociado or 0) == 0
    editar_quantidade(session, cot, it, 12)                    # edição com cenário bloqueado
    salvar_cabecalho(session, cot, estado_destino="São Paulo", contribuinte_icms="sim")
    it = _item(session, it.id)
    assert it.preco_negociado > 0 and it.status_fiscal == "OK"


def test_cr02_preco_ou_quantidade_nao_positivos_sao_recusados(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    antes = it.preco_negociado
    r = editar_quantidade(session, cot, it, 5, modo="preco", valor=0)
    assert r.status_code == 400
    r = editar_quantidade(session, cot, it, 0)
    assert r.status_code == 400
    r = editar_quantidade(session, cot, it, -3)
    assert r.status_code == 400
    it = _item(session, it.id)
    assert it.preco_negociado == antes and it.quantidade == 10


def test_cr03_atualizar_premissas_usa_a_margem_alvo_vigente(session, fornecedores, admin):
    """Item formado com alavanca 18% e regra hoje em 20%: atualizar leva a 20%, sem desconto
    fantasma nem corte de comissão."""
    from app.routers.cotacoes import atualizar_premissas
    from tests.crisis.conftest import RequestFalsa, chamar
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    it = _item(session, it.id)
    it.valor_editado = float(D(it.margem_padrao_pct) - Decimal("0.02"))   # alavanca antiga
    session.add(it)
    session.commit()
    chamar(atualizar_premissas, RequestFalsa(admin), cotacao_id=cot.id, session=session)
    session.commit()
    it = _item(session, it.id)
    assert D(it.valor_editado) == D(it.margem_padrao_pct)
    esperado, _ctx = recomendado_para(session, cot, it)
    assert dinheiro(D(it.preco_negociado)) == dinheiro(esperado)
    av = com.avaliar_negociacao(session, session.get(Cotacao, cot.id))
    assert av.desconto_pct in (None, Decimal(0)) or av.desconto_pct == 0


def test_cr01_aprovacao_cai_quando_o_cenario_muda(session, fornecedores, admin, owner):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    baixo = float(dinheiro(D(it.preco_negociado) * Decimal("0.80")))
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: baixo}, ator=admin)
    session.commit()
    assert av.requer_aprovacao
    pedido = ws.solicitar_aprovacao(session, session.get(Cotacao, cot.id), ator=admin, justificativa="teste")
    session.commit()
    ws.decidir(session, session.get(Cotacao, cot.id), pedido.id, ator=owner, aprovar=True)
    session.commit()
    assert ws.aprovacao_vigente(session, cot.id, ws.wf.fingerprint(session.get(Cotacao, cot.id), ws.itens_de(session, cot.id))) is not None
    salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    c = session.get(Cotacao, cot.id)
    assert ws.aprovacao_vigente(session, cot.id, ws.wf.fingerprint(c, ws.itens_de(session, cot.id))) is None
    assert session.exec(select(ws.AprovacaoCotacao).where(ws.AprovacaoCotacao.cotacao_id == cot.id)
                        .where(ws.AprovacaoCotacao.status == "INVALIDADA")).first() is not None

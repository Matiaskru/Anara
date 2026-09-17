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
                                   recomendado_para, salvar_cabecalho)

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


def test_cr01_preco_negociado_volta_ao_recomendado_do_cenario_novo(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    negociado = float(dinheiro(D(it.preco_negociado) * Decimal("0.97")))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: negociado}, ator=admin)
    session.commit()
    assert _item(session, it.id).modo_edicao == "preco"
    salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    it = _item(session, it.id)
    esperado, _ctx = recomendado_para(session, cot, it)
    assert dinheiro(D(it.preco_negociado)) == dinheiro(esperado)
    assert it.preco_negociado != negociado                     # o desconto de outro cenário caiu
    assert it.modo_edicao == "margem" and D(it.valor_editado) == D(it.margem_padrao_pct)


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
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    assert it.preco_negociado > 0
    salvar_cabecalho(session, cot, estado_destino="Bahia", contribuinte_icms="nao")   # FCP/base desconhecidos
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
    salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    it = _item(session, it.id)
    esperado, _ctx = recomendado_para(session, cot, it)
    assert dinheiro(D(it.preco_negociado)) == dinheiro(esperado)


def test_cr02_preco_zero_nunca_fica_permanente(session, fornecedores, admin):
    cot = nova_cotacao(session, estado_destino="Bahia", contribuinte_icms=False, finalidade="USO_CONSUMO")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
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

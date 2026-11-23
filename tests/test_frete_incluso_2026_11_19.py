"""Frete incluso (19/11/2026, pedido da Ines).

"Quando a entrega é em SP que o frete está incluso o sistema não deixa a gente gerar a proposta com o frete Anara
entrega." CIF só valia com valor > 0 confirmado; frete incluso (valor 0) caía no motor automático e bloqueava.
Agora "Frete incluso (sem cobrar à parte)" é decisão explícita: CIF + valor 0 + confirmado, com autoria; não
bloqueia, não soma ao total e a proposta diz "Frete nacional: incluso".
"""
import json

import pytest
from sqlmodel import select

from app import comercial_service as com
from app import pdf_bridge
from app import workflow as wf
from app import workflow_service as ws
from app.models import Cliente, Cotacao, Fornecedor
from conftest import _novo_usuario
from tests.crisis.conftest import add_item, nova_cotacao, produto_nacional, salvar_cabecalho


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def admin():
    u = _novo_usuario("ADMIN")
    u.can_approve_quotes = True
    return u


def _cotacao_cif(session, fornecedores):
    cot = nova_cotacao(session, freight_type="CIF")
    add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    return cot


def test_frete_incluso_nao_bloqueia_e_nao_soma_ao_total(session, fornecedores, admin):
    cot = _cotacao_cif(session, fornecedores)
    c = session.get(Cotacao, cot.id)
    assert ws.avaliar(session, c, frete=ws.frete_para_avaliar(session, c)).blockers   # CIF sem valor bloqueia
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="", frete_incluso="sim")
    c = session.get(Cotacao, cot.id)
    assert c.freight_valor == 0 and c.freight_manual_confirmado is True and c.freight_manual_por
    assert c.freight_manual_obs == "Frete incluso"
    frete = ws.frete_para_avaliar(session, c)
    assert frete["status"] == wf.FRETE_MANUAL_CONFIRMADO and frete["incluso"] and frete["valor"] == 0
    pront = ws.avaliar(session, c, frete=frete)
    assert pront.blockers == [] and pront.pode_emitir
    av = com.avaliar_negociacao(session, c)
    assert av.total_proposta == av.subtotal_negociado
    # proposta: rascunho e final dizem "incluso" e o total é só o dos produtos
    cliente = session.get(Cliente, c.cliente_id)
    header, _, totals = pdf_bridge.montar_documento(c, cliente, ws.itens_de(session, c.id), rascunho=True)
    assert header["frete"] == "Frete nacional: incluso" and totals["frete"] is None
    snap = ws.emitir(session, c, ator=admin, frete=frete)
    session.commit()
    assert json.loads(snap.frete_json)["incluso"] is True
    header, _, totals = pdf_bridge.montar_documento(c, cliente, ws.itens_de(session, c.id), snapshot=snap)
    assert header["frete"] == "Frete nacional: incluso" and totals["total_geral"] == totals["subtotal"]


def test_desmarcar_frete_incluso_volta_a_bloquear(session, fornecedores):
    cot = _cotacao_cif(session, fornecedores)
    salvar_cabecalho(session, cot, freight_type="CIF", frete_incluso="sim")
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="", frete_incluso="")
    c = session.get(Cotacao, cot.id)
    assert c.freight_manual_confirmado is False and c.freight_valor is None
    assert ws.avaliar(session, c, frete=ws.frete_para_avaliar(session, c)).blockers


def test_valor_zero_sem_marcar_incluso_continua_sem_confirmar(session, fornecedores):
    cot = _cotacao_cif(session, fornecedores)
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="0", freight_manual_confirmado="sim")
    assert session.get(Cotacao, cot.id).freight_manual_confirmado is False


def test_frete_incluso_so_vale_para_anara_entrega(session, fornecedores):
    cot = _cotacao_cif(session, fornecedores)
    salvar_cabecalho(session, cot, freight_type="FOB", frete_incluso="sim")
    c = session.get(Cotacao, cot.id)
    assert c.freight_manual_confirmado is False and ws.frete_para_avaliar(session, c) is None


def test_revisao_herda_venda_observacao_e_frete_incluso(session, fornecedores, admin):
    """23/11/2026: toda R2+ nascia "Legado — sem venda vinculada" e o frete incluso voltava a travar."""
    from app import crm_service as crm
    cot = _cotacao_cif(session, fornecedores)
    c = session.get(Cotacao, cot.id)
    dono = session.exec(select(__import__("app.models", fromlist=["Usuario"]).Usuario)).first() or admin
    op = crm.criar_oportunidade(session, ator=dono, cliente_id=c.cliente_id, titulo="Venda da revisão")
    c.oportunidade_id = op.id
    session.add(c)
    session.commit()
    salvar_cabecalho(session, cot, freight_type="CIF", frete_incluso="sim", observacao_cliente="Entrega na portaria")
    c = session.get(Cotacao, cot.id)
    ws.emitir(session, c, ator=admin, frete=ws.frete_para_avaliar(session, c))
    session.commit()
    r2 = ws.criar_revisao(session, session.get(Cotacao, cot.id), ator=admin)
    session.commit()
    assert r2.revisao == 2 and r2.oportunidade_id == op.id and r2.observacao_cliente == "Entrega na portaria"
    assert r2.freight_manual_confirmado is True and r2.freight_valor == 0
    assert ws.frete_para_avaliar(session, r2)["incluso"] is True

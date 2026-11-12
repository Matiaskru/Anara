"""Admin → Itens da Tabela de preços no catálogo (12/11/2026).

Os 28 itens da cotação KTC 20/10 que entraram na Tabela de preços (tapetes N53–N57, 600 fios N58–N80) precisam
existir como SKU para a vendedora cotar. O Admin mostra a prévia (sem gravar) e cadastra os que faltam pelo
caminho da calculadora, com a cotação registrada; o B2B sai o da Tabela de preços. Idempotente.
"""
import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import catalogo_tabela as ct
from app import pricing_service as ps
from app import tabela_comercial as tc
from app import tabela_precos as tp
from app.models import AuditLog, Produto
from app.routers import admin
from app.routers.calculadora import _cenario_do_form
from conftest import RequestFalsa, _novo_usuario
from tests.crisis.conftest import chamar


def _pagina(session, usuario, **kw):
    r = chamar(admin.catalogo_tabela, RequestFalsa(usuario, path="/admin/catalogo-tabela"), session=session, **kw)
    return bytes(r.body).decode()


def test_vendedora_nao_abre_nem_cadastra(session):
    vend = _novo_usuario("VENDEDOR_INTERNO")
    with pytest.raises(HTTPException) as e:
        _pagina(session, vend)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        chamar(admin.catalogo_tabela_cadastrar, RequestFalsa(vend), session=session)


def test_previa_nao_grava_e_cadastrar_cria_os_28_com_o_preco_da_tabela(session):
    dono = _novo_usuario("OWNER")
    chaves = list(tc.SKUS_KTC_20_10)
    antes = session.exec(select(Produto).where(Produto.sku_key.in_(chaves))).all()
    html = _pagina(session, dono)
    assert html.count("<strong>N") == 28
    assert session.exec(select(Produto).where(Produto.sku_key.in_(chaves))).all() == antes   # prévia não grava
    if not antes:
        assert "28 a cadastrar" in html and "Cadastrar 28 produto(s)" in html
    linhas = ct.previa(session)
    assert all(l.ok for l in linhas), [(l.codigo, l.b2b, l.b2b_tabela, l.status) for l in linhas if not l.ok]

    r = chamar(admin.catalogo_tabela_cadastrar, RequestFalsa(dono), session=session)
    assert r.status_code == 303
    criados = session.exec(select(Produto).where(Produto.sku_key.in_(chaves))).all()
    assert len(criados) == 28 and all(p.ativo and p.cost_method == "KTC_QUOTED" for p in criados)
    cen = _cenario_do_form(session, {"estado_destino": "Bahia", "contribuinte": "sim", "condicao_pagamento": "30/60"})
    for p in criados:
        codigo = tc.SKUS_KTC_20_10[p.sku_key]
        assert ps.memoria_do_preco(session, p, cen)["b2b"]["preco_b2b"] == tp.preco_b2b(codigo, "Bahia", True, "30/60")
    assert session.exec(select(AuditLog).where(AuditLog.origem == ct.ORIGEM,
                                               AuditLog.acao == "CRIAR_PRODUTO")).all()
    # idempotente: segunda vez não cria nada
    assert ct.cadastrar(session, dono)["criados"] == []
    assert "Todos já estão no catálogo" in _pagina(session, dono)

"""§24 PDF, §25 aprovações e §26 seller/admin — mesmo motor, nenhum dado interno, nada
stale no papel."""
import os
from decimal import Decimal

import pdfplumber
import pytest
from fastapi import HTTPException

from app import comercial_service as com
from app import workflow_service as ws
from app.dinheiro import D, dinheiro
from app.models import Cotacao
from tests.crisis.conftest import (RequestFalsa, add_item, chamar, editar_quantidade, nova_cotacao,
                                   produto_nacional, salvar_cabecalho)
from conftest import _novo_usuario

PROIBIDO = ("margem", "custo", "lucro", "markup", "comiss", "a_cotar", "review_required", "cnet",
            "exw", "piso", "fornecedor", "daune", "decor tricot", "ktc")


def _pdf_texto(resposta):
    caminho = getattr(resposta, "path", None)
    if caminho is None:
        pytest.skip("rota não devolveu arquivo")
    with pdfplumber.open(caminho) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages).lower()


def _gerar(session, cot, ator):
    from app.routers.cotacoes import gerar_pdf
    return chamar(gerar_pdf, RequestFalsa(ator, accept="text/html"), cotacao_id=cot.id, session=session)


def test_pdf_final_nao_sai_com_item_sem_preco_mas_rascunho_marcado_sai(session, fornecedores, admin, owner):
    """Decisão anterior mantida: a prévia é ferramenta de trabalho e sai marcada mesmo
    incompleta. O que não existe é EMISSÃO (e PDF final) com linha sem preço."""
    cot = nova_cotacao(session)
    add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    add_item(session, cot, produto_nacional(session, fornecedores, custo=None, preco_base=0.0))  # A_COTAR
    assert "rascunho" in _pdf_texto(_gerar(session, cot, admin))
    with pytest.raises(ws.OperacaoInvalida):
        ws.emitir(session, session.get(Cotacao, cot.id), ator=owner)


def test_pdf_rascunho_nao_sai_com_cenario_divergente(session, fornecedores, admin):
    cot = nova_cotacao(session, estado_destino="Minas Gerais")
    add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    c = session.get(Cotacao, cot.id)
    c.finalidade = "USO_CONSUMO"
    session.add(c)
    session.commit()
    r = _gerar(session, cot, admin)
    corpo = bytes(getattr(r, "body", b"")).decode("utf-8", "replace").lower()
    assert "cenário fiscal diferente" in corpo
    salvar_cabecalho(session, cot)                          # reforma
    r = _gerar(session, cot, admin)
    assert getattr(r, "path", None) is not None


@pytest.mark.parametrize("n_itens", [1, 10, 32])
def test_pdf_rascunho_e_final_sem_dado_interno_e_com_total_certo(session, fornecedores, admin, owner, n_itens):
    cot = nova_cotacao(session)
    for i in range(n_itens):
        add_item(session, cot, produto_nacional(session, fornecedores, custo=50.0 + i), quantidade=3 + i)
    itens = ws.itens_de(session, cot.id)
    total = sum(D(it.faturamento) for it in itens)
    texto = _pdf_texto(_gerar(session, cot, admin))
    assert "rascunho" in texto
    for termo in PROIBIDO:
        assert termo not in texto, termo
    total_txt = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    assert total_txt in texto, (total_txt, texto[-400:])
    snap = ws.emitir(session, session.get(Cotacao, cot.id), ator=owner)
    session.commit()
    texto = _pdf_texto(_gerar(session, cot, admin))
    assert "rascunho" not in texto and total_txt in texto
    for termo in PROIBIDO:
        assert termo not in texto, termo


def test_aprovacao_cai_com_quantidade_preco_cenario_pagamento_e_frete(session, fornecedores, admin, owner):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    baixo = float(dinheiro(D(it.preco_negociado) * Decimal("0.8")))

    def aprovar():
        c = session.get(Cotacao, cot.id)
        com.aplicar_negociacao(session, c, {it.id: baixo}, ator=admin); session.commit()
        pedido = ws.solicitar_aprovacao(session, session.get(Cotacao, cot.id), ator=admin, justificativa="x"); session.commit()
        ws.decidir(session, session.get(Cotacao, cot.id), pedido.id, ator=owner, aprovar=True); session.commit()
        c = session.get(Cotacao, cot.id)
        assert ws.aprovacao_vigente(session, cot.id, ws.wf.fingerprint(c, ws.itens_de(session, cot.id))) is not None

    def vigente():
        c = session.get(Cotacao, cot.id)
        return ws.aprovacao_vigente(session, cot.id, ws.wf.fingerprint(c, ws.itens_de(session, cot.id)))

    aprovar(); editar_quantidade(session, cot, it, 11); assert vigente() is None
    aprovar(); com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: baixo + 1}, ator=admin); session.commit(); assert vigente() is None
    aprovar(); salvar_cabecalho(session, cot, estado_destino="Rio de Janeiro", contribuinte_icms="nao"); assert vigente() is None
    aprovar(); salvar_cabecalho(session, cot, condicao_pagamento="30/60/90"); assert vigente() is None
    aprovar(); salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="150"); assert vigente() is None


def test_seller_e_admin_recebem_o_mesmo_preco_do_mesmo_motor(session, fornecedores, admin):
    from app.routers.negociacao import negociacao_atual as rota_negociacao
    cot = nova_cotacao(session)
    for i in range(3):
        add_item(session, cot, produto_nacional(session, fornecedores, custo=80.0 + 10 * i))
    vend = _novo_usuario("VENDEDOR_INTERNO")
    from tests.crisis.conftest import corpo
    pv = corpo(chamar(rota_negociacao, RequestFalsa(vend, accept="application/json"), cotacao_id=cot.id, session=session))
    pa = corpo(chamar(rota_negociacao, RequestFalsa(admin, accept="application/json"), cotacao_id=cot.id, session=session))
    assert pv["subtotal_negociado"] == pa["subtotal_negociado"] and pv["total_proposta"] == pa["total_proposta"]
    for iv, ia in zip(pv["itens"], pa["itens"]):
        assert iv["preco_negociado"] == ia["preco_negociado"] and iv["preco_recomendado"] == ia["preco_recomendado"]
        assert iv["total_linha"] == ia["total_linha"]
    chaves = set()
    def coleta(o):
        if isinstance(o, dict):
            for k, v in o.items():
                chaves.add(k.lower()); coleta(v)
        elif isinstance(o, list):
            for v in o: coleta(v)
    coleta(pv)
    assert not [k for k in chaves if any(t in k for t in ("margem", "lucro", "custo", "cnet", "exw", "markup", "piso"))]
    assert "economia" in pa and "economia" not in pv

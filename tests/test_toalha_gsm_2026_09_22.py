"""Toalha é gramatura, não fios (22/09/2026).

Uma toalha de banho 80×90 com 650 g/m² criada pela calculadora aparecia como
"Toalha de banho 80x90 · 200 fios". O GSM ia certo para o banco; o `thread_count` é que não
devia existir — e existia, copiado do **tecido**.

Causa: o `<select>` de tecido é escondido para toalha, mas continua dentro do `<form>`, e o
`FormData` envia campo escondido. `produto_simulado` recebia o `material_id` da primeira opção
(um 200TC) e copiava `thread_count`, `material_ref`, `weave` e a composição do tecido. O nome
canônico prefere fios quando eles existem — e a toalha virava "200 fios".

A correção é de semântica, no servidor: toalha não consome tecido (ela é cotada por área × GSM,
`ToalhaPreco` em US$/kg), então `material_id` é **ignorado** para a família toalha e
`thread_count` fica NULO. Lençol, capa duvet e fronha continuam com fios.
"""
import json

import pytest
from sqlmodel import select

from app import calculadora as calc
from app import workflow_service as ws
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, MaterialPreco, Produto
from conftest import RequestFalsa, _novo_usuario
from decimais import aprox
from tests.crisis.conftest import add_item, chamar, nova_cotacao

FAMILIAS_TOALHA = ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth")


@pytest.fixture
def material(session):
    """O primeiro tecido da lista — é o que o formulário manda quando o campo está escondido."""
    return session.exec(select(MaterialPreco).where(MaterialPreco.ativo == True)   # noqa: E712
                        .order_by(MaterialPreco.id)).first()


class RequestForm(RequestFalsa):
    def __init__(self, usuario, dados):
        super().__init__(usuario)
        self._dados = dados

    async def form(self):
        return self._dados


# ===========================================================================
# 11–12. A toalha personalizada persiste GSM e não inventa fios
# ===========================================================================
def test_11_toalha_personalizada_persiste_gsm_e_nao_thread_count(session, material):
    # exatamente o que o formulário manda: o material escondido vem junto
    p = calc.produto_simulado(session, "Bath Towel", 80, 90, material.id, 650, "plain")
    assert p.gsm == 650 and p.thread_count is None
    assert p.material_ref is None and p.weave is None
    salvo = calc.salvar_no_catalogo(session, "Bath Towel", 80, 90, material_id=material.id,
                                    gsm=650, plain_or_stripe="plain")
    session.commit()
    assert salvo.gsm == 650 and salvo.thread_count is None
    assert "650 GSM" in salvo.sku_key and "fios" not in (salvo.nome or "")


def test_12_nome_mostra_gramatura(session, material):
    p = calc.produto_simulado(session, "Bath Towel", 80, 90, material.id, 650, "plain")
    assert p.nome == "Toalha de banho 80x90 · 650 g/m² · 100% algodão"
    assert "200 fios" not in p.nome and "fios" not in p.nome


@pytest.mark.parametrize("familia", FAMILIAS_TOALHA)
def test_12b_vale_para_todas_as_familias_de_toalha(session, material, familia):
    p = calc.produto_simulado(session, familia, 70, 140, material.id, 500, "plain")
    assert p.thread_count is None and p.gsm == 500 and "fios" not in p.nome
    assert "g/m²" in p.nome


def test_12c_pela_rota_da_calculadora_como_a_tela_manda(session, material):
    """O caminho real: POST com o `material_id` escondido no formulário."""
    import asyncio
    from app.routers.calculadora import calcular, salvar
    dados = {"familia": "Bath Towel", "largura_cm": "80", "comprimento_cm": "90", "gsm": "650",
             "material_id": str(material.id), "quantidade": "5", "plain_or_stripe": "plain",
             "composicao_toalha": "100/0"}
    admin = _novo_usuario("ADMIN")
    r = json.loads(bytes(asyncio.run(calcular(RequestForm(admin, dados), session)).body))
    assert r["calculavel"] and "fios" not in r["produto"]["nome"]
    assert r["produto"]["gsm"] == 650
    corpo = json.loads(bytes(asyncio.run(salvar(RequestForm(admin, dados), session)).body))
    salvo = session.get(Produto, corpo["produto_id"])
    assert salvo.gsm == 650 and salvo.thread_count is None and "fios" not in salvo.nome


# ===========================================================================
# 13–14. Cotação, PDF e busca mostram a gramatura
# ===========================================================================
def test_13_14_cotacao_pdf_e_busca_mostram_a_gramatura(session, material):
    from app.pdf_bridge import montar_documento
    from app.routers.produtos import buscar
    salvo = calc.salvar_no_catalogo(session, "Bath Towel", 80, 90, material_id=material.id,
                                    gsm=650, plain_or_stripe="plain")
    session.commit()
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, salvo)
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert "fios" not in (it.nome_produto or "") and "650" in (it.nome_produto or "")
    memoria = json.loads(it.memoria_json)
    assert memoria["produto"]["gsm"] == 650 and memoria["produto"].get("thread_count") in (None, 0)
    c = session.get(Cotacao, cot.id)
    header, itens, _t = montar_documento(c, session.get(Cliente, c.cliente_id),
                                         ws.itens_de(session, c.id), rascunho=True)
    linha = json.dumps(itens, ensure_ascii=False)
    assert "650" in linha and "fios" not in linha
    corpo = json.loads(bytes(chamar(buscar, RequestFalsa(_novo_usuario("ADMIN")),
                                    q="Toalha de banho 80x90", session=session).body))
    linhas = corpo["produtos"] if isinstance(corpo, dict) else corpo
    achado = next(x for x in linhas if x["id"] == salvo.id)
    assert achado["gsm"] == 650 and achado["thread_count"] is None
    assert "fios" not in (achado["nome"] or "")


# ===========================================================================
# 15–16. As outras famílias continuam com fios
# ===========================================================================
def test_15_lencol_continua_com_fios(session):
    m = session.exec(select(MaterialPreco).where(MaterialPreco.thread_count == 200)).first()
    m = m or session.exec(select(MaterialPreco)).first()
    p = calc.produto_simulado(session, "Flat Sheet", 240, 260, m.id, None, "plain")
    assert p.thread_count == m.thread_count and f"{m.thread_count} fios" in p.nome
    assert p.material_ref == m.material


def test_16_fronha_continua_com_fios(session):
    m = session.exec(select(MaterialPreco)).first()
    p = calc.produto_simulado(session, "Pillow Case", 50, 70, m.id, None, "plain",
                              abas=4, flap_cm=20)
    assert p.thread_count == m.thread_count and "fios" in p.nome
    assert "abas" in (p.construcao or "")


def test_17_o_catalogo_existente_nao_muda(session, material):
    """A correção é do caminho de CRIAÇÃO: SKU que já tem fios continua com fios."""
    p = Produto(sku_key="TOALHA-LEGADA", nome="Toalha legada", familia="Bath Towel",
                thread_count=200, gsm=650, ativo=True)
    session.add(p); session.commit(); session.refresh(p)
    assert p.thread_count == 200 and p.gsm == 650      # nada é reescrito por esta correção

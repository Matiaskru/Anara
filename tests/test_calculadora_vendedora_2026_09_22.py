"""Calculadora para a vendedora (22/09/2026): mesma conta, resposta comercial.

A vendedora é quem monta a cotação, e cotação boa precisa de produto que ainda não está no
catálogo. Até 22/09/2026 `/calculadora*` exigia `exigir_admin` — bloqueio errado para a
operação. Agora é rota de operação: **todo papel autenticado calcula**; o que muda por papel é
o que a resposta carrega. Este arquivo prova as duas metades: ela consegue trabalhar, e ela
continua sem enxergar a economia — em HTML, em JSON, no produto salvo e no item da cotação.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import calculadora as calc
from app import comercial_service as com
from app import pricing_service as ps
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, dinheiro
from app.models import Cotacao, CotacaoItem, Fornecedor, MaterialPreco, Produto
from app.pricing_engine import preco_b2b, preco_de_tabela
from app.routers.calculadora import calcular, pagina, salvar
from conftest import RequestFalsa, _novo_usuario
from decimais import aprox
from tests.crisis.conftest import chamar, nova_cotacao

PERFIS_VENDEDORA = ("VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO")
#: O que NUNCA pode aparecer numa resposta da calculadora para a vendedora — chave de JSON ou
#: texto de HTML. Economia real, formação comercial e a mecânica que as produz.
PROIBIDO = ("custo", "cnet", "exw", "usd", "net_brl", "net_usd", "margem", "lucro", "markup",
            "memoria", "nacionalizacao", "industrial", "base_comercial", "protecao", "ii_pct",
            "ii_usd", "ncm", "premissa", "b2b_economico", "waterfall", "fx_usd_brl", "drivers",
            "quality_allowance", "waste", "shrinkage", "cmt")


class RequestForm(RequestFalsa):
    """Request com corpo de formulário, como o `fetch` da tela manda."""

    def __init__(self, usuario, dados):
        super().__init__(usuario)
        self._dados = dados

    async def form(self):
        return self._dados


@pytest.fixture
def material(session):
    return session.exec(select(MaterialPreco)
                        .where(MaterialPreco.material == "300TC Sateen 100% Cotton")
                        .where(MaterialPreco.plain_or_stripe == "plain")).first()


@pytest.fixture
def owner():
    u = _novo_usuario("OWNER"); u.can_approve_quotes = True; return u


def _form(material, **extra):
    dados = {"familia": "Flat Sheet", "largura_cm": "240", "comprimento_cm": "260",
             "material_id": str(material.id), "quantidade": "10", "plain_or_stripe": "plain"}
    dados.update(extra)
    return dados


def _corpo(resposta):
    return json.loads(bytes(resposta.body))


def _texto(obj):
    return json.dumps(obj, ensure_ascii=False).lower()


# ===========================================================================
# A/B — a tela abre para os dois papéis de vendedora (e continua abrindo para OWNER/ADMIN)
# ===========================================================================
@pytest.mark.parametrize("papel", PERFIS_VENDEDORA)
def test_a_b_vendedora_abre_a_calculadora(session, papel):
    r = chamar(pagina, RequestFalsa(_novo_usuario(papel)), cotacao_id=0, session=session)
    assert r.status_code == 200
    html = bytes(r.body).decode()
    assert "Produto personalizado" in html and 'id="form-calc"' in html
    assert 'data-economia="0"' in html


@pytest.mark.parametrize("papel", ("OWNER", "ADMIN"))
def test_k_owner_e_admin_mantem_a_calculadora_economica(session, papel):
    r = chamar(pagina, RequestFalsa(_novo_usuario(papel)), cotacao_id=0, session=session)
    html = bytes(r.body).decode()
    assert r.status_code == 200 and 'data-economia="1"' in html
    for bloco in ("Custo NET", "Lucro no total", "Margem", "memoria.js", "Ver memória do preço",
                  'name="margem_pct"', 'name="outros_custos_usd"', "ICMS"):
        assert bloco in html, bloco


def test_h_vendedora_continua_sem_as_telas_de_economia(session):
    from app.routers import admin, configuracoes, importar, relatorios_comerciais
    from app.routers.cotacoes import memoria_item
    from app.routers.produtos import memoria
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    for rota, kw in ((admin.hub, {}), (configuracoes.painel, {}), (importar.form, {}),
                     (relatorios_comerciais.economico, {}), (memoria, {"produto_id": 1}),
                     (memoria_item, {"cotacao_id": 1, "item_id": 1})):
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(vend), session=session, **kw)
        assert erro.value.status_code == 403, rota.__name__


def test_g_vendedora_nao_acessa_a_memoria_do_produto_criado(session, material):
    from app.routers.produtos import memoria
    vend = _novo_usuario("VENDEDOR_INTERNO")
    corpo = _corpo(asyncio.run(salvar(RequestForm(vend, _form(material)), session)))
    with pytest.raises(HTTPException) as erro:
        chamar(memoria, RequestFalsa(vend), produto_id=corpo["produto_id"], session=session)
    assert erro.value.status_code == 403


# ===========================================================================
# C/E — calcula, e o número é o mesmo do OWNER
# ===========================================================================
@pytest.mark.parametrize("papel", PERFIS_VENDEDORA)
def test_c_e_vendedora_calcula_e_o_b2b_e_o_mesmo_do_owner(session, material, papel):
    dados = _form(material)
    dono = _corpo(asyncio.run(calcular(RequestForm(_novo_usuario("OWNER"), dados), session)))
    dela = _corpo(asyncio.run(calcular(RequestForm(_novo_usuario(papel), dados), session)))
    assert dela["calculavel"] is True and dela["pode_adicionar"] is True
    # mesmo motor, mesmo cenário: B2B, tabela, preço e total idênticos ao que o OWNER vê
    assert dela["preco_b2b"] == dono["b2b"]["preco_b2b"]
    assert dela["preco_tabela"] == dono["b2b"]["preco_tabela"]
    assert dela["preco_proposto"] == dono["comercial"]["preco_negociado"]
    assert dela["total"] == dono["comercial"]["faturamento"]
    assert dela["preco_tabela"] == aprox(2 * dela["preco_b2b"], abs=0.011)
    # a comissão dela: a mesma função canônica da cotação (no B2B, 5% da escada)
    assert dela["comissao_estimada_pct"] == aprox(0.05) and dela["comissao_estimada_valor"] > 0
    assert dela["desconto_vs_tabela_pct"] == aprox(0.5, abs=1e-3)
    assert dela["situacao"] in ("CONFIRMADO", "ESTIMADO", "REVALIDAR", "REVIEW_REQUIRED")
    assert dela["situacao_rotulo"] and dela["nome"] and dela["especificacao"]


@pytest.mark.parametrize("papel", PERFIS_VENDEDORA)
def test_f_resposta_da_vendedora_nao_traz_campo_economico(session, material, papel):
    corpo = _corpo(asyncio.run(calcular(RequestForm(_novo_usuario(papel), _form(material)), session)))
    assert encontrar_confidenciais(corpo) == []
    texto = _texto(corpo)
    for termo in PROIBIDO:
        assert termo not in texto, termo
    assert set(corpo) <= set(calc.CAMPOS_RESULTADO_COMERCIAL)


def test_f_html_da_calculadora_da_vendedora_nao_traz_economia(session, material):
    r = chamar(pagina, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")), cotacao_id=0, session=session)
    html = bytes(r.body).decode().lower()
    for termo in ("custo net", "lucro", "margem", "cnet", "exw", "markup", "memoria.js",
                  "memória do preço", "icms", "outros custos", "proteção", "us$"):
        assert termo not in html, termo


def test_margem_forcada_e_outros_custos_sao_ignorados_para_a_vendedora(session, material):
    """A margem forçada não é só sigilo: é a alavanca que formaria um B2B abaixo do piso."""
    normal = _corpo(asyncio.run(calcular(
        RequestForm(_novo_usuario("VENDEDOR_INTERNO"), _form(material)), session)))
    forcado = _corpo(asyncio.run(calcular(
        RequestForm(_novo_usuario("VENDEDOR_INTERNO"),
                    _form(material, margem_pct="5", outros_custos_usd="10")), session)))
    assert forcado["preco_b2b"] == normal["preco_b2b"] and forcado["preco_tabela"] == normal["preco_tabela"]
    # e o OWNER continua podendo forçar
    dono = _corpo(asyncio.run(calcular(
        RequestForm(_novo_usuario("OWNER"), _form(material, margem_pct="5")), session)))
    assert dono["comercial"]["preco_negociado"] < normal["preco_b2b"]


# ===========================================================================
# D/I/J — adiciona à cotação pelo caminho canônico
# ===========================================================================
@pytest.mark.parametrize("papel", PERFIS_VENDEDORA)
def test_d_vendedora_adiciona_produto_personalizado_na_cotacao(session, material, papel):
    vend = _novo_usuario(papel)
    cot = nova_cotacao(session, condicao_pagamento="30")
    dados = _form(material, cotacao_id=str(cot.id))
    previa = _corpo(asyncio.run(calcular(RequestForm(vend, dados), session)))
    corpo = _corpo(asyncio.run(salvar(RequestForm(vend, dados), session)))
    assert corpo["item_id"] and corpo["cotacao_id"] == cot.id
    assert encontrar_confidenciais(corpo) == [] and "custo" not in _texto(corpo).replace("sem_custo", "")
    it = session.get(CotacaoItem, corpo["item_id"])
    # entrou pelo caminho canônico: política congelada, B2B = recomendado, tabela 2×, comissão
    assert it.politica_comercial and it.preco_recomendado == aprox(previa["preco_b2b"])
    assert it.preco_tabela == aprox(previa["preco_tabela"])
    assert it.preco_negociado == aprox(previa["preco_proposto"])
    assert it.comissao_faixa_pct == aprox(0.05) and it.base_comercial_precificacao
    assert it.custo_unitario > 0 and it.status_custo_item
    # e a cotação segue normal para ela
    payload = com.payload_vendedora(com.avaliar_negociacao(session, session.get(Cotacao, cot.id)))
    assert encontrar_confidenciais(payload) == []
    assert any(x["item_id"] == it.id for x in payload["itens"])


def test_e_mesmo_cenario_mesmo_numero_para_owner_e_vendedora_na_cotacao(session, material, owner):
    """O produto personalizado criado pela vendedora e o criado pelo OWNER precificam igual."""
    cot = nova_cotacao(session, condicao_pagamento="30/60/90", estado_destino="Minas Gerais")
    dados = _form(material, cotacao_id=str(cot.id), largura_cm="200", comprimento_cm="220")
    dela = _corpo(asyncio.run(salvar(RequestForm(_novo_usuario("VENDEDOR_COMISSIONADO"), dados), session)))
    produto = session.get(Produto, dela["produto_id"])
    custo, base, _m = ps.bases_de_preco(session, produto)
    margem = ps.margem_padrao(session, produto)
    regras, ctx = ps.regras_da_cotacao(session, session.get(Cotacao, cot.id), produto)
    b2b = preco_b2b(base, margem.margem_pct, regras)
    it = session.get(CotacaoItem, dela["item_id"])
    assert D(it.preco_recomendado) == b2b.preco_negociado
    assert D(it.preco_tabela) == preco_de_tabela(b2b.preco_negociado, ctx.get("fator_tabela") or 2)
    assert it.custo_unitario == aprox(custo)          # economia real, do mesmo motor


def test_i_abaixo_do_b2b_continua_exigindo_aprovacao(session, material, owner):
    vend = _novo_usuario("VENDEDOR_INTERNO")
    cot = nova_cotacao(session, condicao_pagamento="30")
    dados = _form(material, cotacao_id=str(cot.id), largura_cm="230", comprimento_cm="250")
    corpo = _corpo(asyncio.run(salvar(RequestForm(vend, dados), session)))
    it = session.get(CotacaoItem, corpo["item_id"])
    abaixo = dinheiro(D(it.preco_recomendado) * D("0.9"))
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id),
                                {it.id: {"preco": str(abaixo)}}, ator=vend)
    session.commit()
    assert av.requer_aprovacao
    assert any(e.motivo == "PRECO_ABAIXO_B2B" for a in av.itens for e in a.excecoes)
    assert ws.avaliar(session, session.get(Cotacao, cot.id)).precisa_aprovacao


def test_j_familia_sem_formula_registra_pedido_e_bloqueia_a_emissao(session):
    """A_COTAR continua bloqueando: a vendedora registra o pedido, não inventa preço."""
    vend = _novo_usuario("VENDEDOR_INTERNO")
    cot = nova_cotacao(session, condicao_pagamento="30")
    # 221x231 de propósito: 220x230 é a medida do BL-001 da cotação KTC de 29/07, e um SKU
    # com a mesma medida confundiria o casamento estrutural da ingestão daquelas amostras.
    previa = _corpo(asyncio.run(calcular(
        RequestForm(vend, {"familia": "Blanket", "largura_cm": "221", "comprimento_cm": "231",
                           "quantidade": "5", "cotacao_id": str(cot.id)}), session)))
    assert previa["calculavel"] is False and previa["pode_adicionar"] is False
    assert not (set(previa) & {"preco_b2b", "preco_tabela", "total"})
    corpo = _corpo(asyncio.run(salvar(
        RequestForm(vend, {"familia": "Blanket", "largura_cm": "221", "comprimento_cm": "231",
                           "quantidade": "5", "cotacao_id": str(cot.id), "calculavel": "nao",
                           "observacao": "Hotel X pediu"}), session)))
    it = session.get(CotacaoItem, corpo["item_id"])
    produto = session.get(Produto, corpo["produto_id"])
    assert produto.precisa_revisao and produto.custo_unitario is None
    assert it.preco_negociado in (0, 0.0) and it.status_custo_item == "A_COTAR"
    pront = ws.avaliar(session, session.get(Cotacao, cot.id))
    assert not pront.pode_emitir and pront.blockers


def test_produto_sem_regra_de_preco_nao_vira_numero_comercial(session, material, monkeypatch):
    """Sem política de preço para o produto, a vendedora recebe pendência — não um preço."""
    from app import margin_rules
    vend = _novo_usuario("VENDEDOR_INTERNO")
    monkeypatch.setattr(ps, "margem_padrao",
                        lambda *a, **k: margin_rules.MargemResolvida(
                            margem_pct=None, regra=margin_rules.MOTIVO_SEM_REGRA))
    corpo = _corpo(asyncio.run(calcular(RequestForm(vend, _form(material)), session)))
    assert corpo["pode_adicionar"] is False and corpo["pendencias"]
    assert not (set(corpo) & {"preco_b2b", "preco_tabela", "comissao_estimada_valor"})

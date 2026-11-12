"""B2B da cotação = preço fixo da Tabela de preços (12/11/2026).

Regra do Matias (10/11): "os preços agora se mantêm... a regra não seria manter a margem, mas sim o preço".
Em 12/11: "deixe a tabela e o sistema coerentes". Produto que está na tabela, em cenário que a tabela tem
(um dos 6 prazos, sem sinal, mesmo fiscal com que a tabela foi formada), forma o B2B pelo preço PUBLICADO;
a margem é a que esse preço dá sobre o custo real. Fora disso (sinal, outra finalidade fiscal, cartão,
produto fora da tabela, margem forçada) continua pela margem-alvo.
"""
import pytest

from app import pricing_service as ps
from app.dinheiro import D
from app import tabela_precos as tp
from app.models import Cotacao
from app.routers.calculadora import _cenario_do_form
from tests.crisis.conftest import add_item, nova_cotacao, produto_ktc_cotado, salvar_cabecalho

CHAVE_P111 = "KTC · Top Sheet · 250TC Sateen CVC 70/30 plain · 190x250"     # P111, lençol de cima single


@pytest.fixture
def fornecedores(session):
    from sqlmodel import select
    from app.models import Fornecedor
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def _p111(session, fornecedores, exw=9.89):
    """O SKU da P111 (o banco da suíte é um só e `sku_key` é único: reaproveita e volta ao EXW pedido)."""
    from sqlmodel import select
    from app.models import Produto
    p = session.exec(select(Produto).where(Produto.sku_key == CHAVE_P111)).first()
    if p is None:
        return produto_ktc_cotado(session, fornecedores, exw_usd=exw, peso_kg=0.8, familia="Top Sheet",
                                  thread_count=250, cotton_pct=0.7, sku_key=CHAVE_P111,
                                  largura_cm=190, comprimento_cm=250)
    p.exw_cotado_usd = exw
    session.add(p)
    session.commit()
    return p


def _b2b(session, produto, **cenario):
    form = {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"}
    form.update(cenario)
    return ps.memoria_do_preco(session, produto, _cenario_do_form(session, form))["b2b"]


def test_produto_da_tabela_tem_o_b2b_publicado_e_a_margem_absorve_o_custo(session, fornecedores):
    p = _p111(session, fornecedores)
    b = _b2b(session, p)
    assert b["preco_b2b"] == tp.preco_b2b("P111", "São Paulo", False, "30") == 116.25
    assert b["preco_tabela"] == 232.50 and b["origem_b2b"] == "TABELA_DE_PRECOS"
    # custo sobe 30%: o preço não se mexe, a margem cai
    p.exw_cotado_usd = 9.89 * 1.3
    session.add(p)
    session.commit()
    caro = _b2b(session, p)
    assert caro["preco_b2b"] == 116.25 and caro["margem_realizada_no_b2b"] < b["margem_realizada_no_b2b"]


@pytest.mark.parametrize("estado,contrib,prazo", [("Bahia", "sim", "30/60/90"), ("Rio de Janeiro", "nao", "À VISTA"),
                                                  ("Amazonas", "sim", "30/60/90/120/150")])
def test_todo_cenario_da_tabela_vale_o_preco_publicado(session, fornecedores, estado, contrib, prazo):
    b = _b2b(session, _p111(session, fornecedores), estado_destino=estado, contribuinte=contrib,
             condicao_pagamento=prazo)
    assert b["preco_b2b"] == tp.preco_b2b("P111", estado, contrib == "sim", prazo)
    assert b["origem_b2b"] == "TABELA_DE_PRECOS"


def test_cotacao_forma_recomendado_e_tabela_pelo_preco_publicado(session, fornecedores):
    p = _p111(session, fornecedores)
    padrao = ps.cenario_padrao_catalogo(session)
    cot = nova_cotacao(session, contribuinte_icms=False, finalidade=padrao.finalidade, condicao_pagamento="30")
    it = add_item(session, cot, p)
    assert (it.preco_recomendado, it.preco_tabela) == (116.25, 232.50)
    assert it.preco_negociado == 116.25                     # entra no B2B, como sempre
    # mudou o cenário: o preço volta a ser o da tabela no cenário novo
    salvar_cabecalho(session, cot, estado_destino="Bahia", contribuinte_icms="sim", condicao_pagamento="30/60/90")
    from app import workflow_service as ws
    it = ws.itens_de(session, cot.id)[-1]
    assert it.preco_recomendado == tp.preco_b2b("P111", "Bahia", True, "30/60/90")
    assert it.preco_tabela == round(it.preco_recomendado * 2, 2)


def test_fora_do_cenario_da_tabela_continua_pela_margem(session, fornecedores):
    p = _p111(session, fornecedores)
    # sinal: a tabela não tem esse preço
    cen = _cenario_do_form(session, {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"})
    cen.percentual_sinal = 0.3
    assert ps.memoria_do_preco(session, p, cen)["b2b"]["origem_b2b"] == "MARGEM_ALVO"
    # outra finalidade com efeito fiscal (contribuinte interestadual revenda × uso e consumo da tabela)
    padrao = ps.cenario_padrao_catalogo(session)
    outra = "REVENDA" if padrao.finalidade != "REVENDA" else "USO_CONSUMO"
    cen = Cotacao(cliente_id=0, estado_origem=padrao.estado_origem, uf_origem_fiscal=padrao.uf_origem_fiscal,
                  finalidade=outra, estado_destino="Bahia", condicao_pagamento="30", contribuinte_icms=True)
    _, ctx = ps.regras_da_cotacao(session, cen, p)
    _, ref = ps.regras_da_cotacao(session, _cenario_do_form(session, {"estado_destino": "Bahia", "contribuinte": "sim",
                                                                        "condicao_pagamento": "30"}), p)
    if (ctx["icms_pct"], ctx["difal_pct"]) != (ref["icms_pct"], ref["difal_pct"]):
        assert ctx["b2b_fixo_tabela"] is None
    # condição que a tabela não tem
    _, ctx = ps.regras_da_cotacao(session, Cotacao(cliente_id=0, estado_destino="São Paulo", condicao_pagamento="60",
                                                   contribuinte_icms=False), p)
    assert ctx.get("b2b_fixo_tabela") is None


def test_produto_fora_da_tabela_nao_usa_preco_fixo(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=9.89, familia="Top Sheet", thread_count=250)
    assert _b2b(session, p)["origem_b2b"] == "MARGEM_ALVO"


def test_margem_forcada_continua_pela_margem(session, fornecedores):
    from app.routers.cotacoes import _calcular
    p = _p111(session, fornecedores)
    cot = nova_cotacao(session, contribuinte_icms=False, finalidade=ps.cenario_padrao_catalogo(session).finalidade)
    regras, ctx = ps.regras_da_cotacao(session, cot, p)
    custo = ps.custo_net(session, p)
    base, net = custo.get("base_comercial_brl"), custo.get("net_brl")
    alvo = ps.margem_padrao(session, p).margem_pct
    normal = _calcular("margem", net, 1, alvo, regras, contexto=ctx, margem_alvo=alvo, base_comercial=base)
    forcada = _calcular("margem", net, 1, D(alvo) + D("0.05"), regras, contexto=ctx, margem_alvo=alvo,
                        base_comercial=base)
    assert float(normal.preco_negociado) == 116.25 and float(forcada.preco_negociado) != 116.25

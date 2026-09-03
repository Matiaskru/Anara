"""Motor comercial Anara: três modos, comissão por faixa e encargo financeiro."""
import pytest
from sqlmodel import select

from app.models import CondicaoPagamento
from app.payment_terms import resolver_encargo
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_markup, calcular_por_preco,
)

REGRAS = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016,
                    comissao_tabela=[(0.0, 0.05), (0.6, 0.06), (0.7, 0.07), (0.8, 0.08),
                                     (0.9, 0.09), (1.0, 0.10)])


def test_margem_alvo_entrega_a_margem_pedida():
    r = calcular_por_margem(50.0, 10, 0.18, REGRAS)
    assert r.margem_liquida == pytest.approx(0.18, abs=1e-9)


@pytest.mark.parametrize("alvo", [0.12, 0.14, 0.15, 0.16, 0.18])
def test_margem_alvo_bate_em_varias_faixas(alvo):
    r = calcular_por_margem(50.0, 1, alvo, REGRAS)
    assert r.margem_liquida == pytest.approx(alvo, abs=1e-9)


def test_margem_e_lucro_sobre_faturamento_nao_markup():
    r = calcular_por_margem(100.0, 1, 0.18, REGRAS)
    assert r.lucro == pytest.approx(r.faturamento * 0.18)
    assert r.markup_implicito > 0.18          # markup é sempre maior que a margem líquida
    assert r.lucro == pytest.approx(r.custo_total * r.markup_implicito, abs=1e-6)


def test_modo_preco_e_modo_margem_sao_o_mesmo_ponto():
    alvo = calcular_por_margem(50.0, 3, 0.16, REGRAS)
    volta = calcular_por_preco(50.0, 3, alvo.preco_negociado, REGRAS)
    assert volta.margem_liquida == pytest.approx(0.16, abs=1e-9)


def test_modo_markup_reproduz_a_formula_da_planilha():
    markup = 0.45
    r = calcular_por_markup(50.0, 1, markup, REGRAS)
    comissao = REGRAS.comissao_para_markup(markup)
    esperado = 50.0 * (1 + markup) / (1 - REGRAS.taxa_fixa() - comissao)
    assert r.preco_negociado == pytest.approx(esperado)


def test_comissao_sobe_por_faixa_de_markup():
    assert REGRAS.comissao_para_markup(0.30) == pytest.approx(0.05)
    assert REGRAS.comissao_para_markup(0.65) == pytest.approx(0.06)
    assert REGRAS.comissao_para_markup(0.95) == pytest.approx(0.09)
    assert REGRAS.comissao_para_markup(1.40) == pytest.approx(0.10)


def test_circularidade_da_comissao_fecha_na_propria_faixa():
    """O markup resolvido tem de ser consistente com a faixa de comissão usada."""
    for alvo in [0.05, 0.12, 0.18, 0.25, 0.30]:
        r = calcular_por_margem(50.0, 1, alvo, REGRAS)
        comissao = REGRAS.comissao_para_markup(r.markup_implicito)
        recomposto = 50.0 * (1 + r.markup_implicito) / (1 - REGRAS.taxa_fixa() - comissao)
        assert recomposto == pytest.approx(r.preco_negociado, rel=1e-6)


def test_sem_custo_nao_inventa_margem():
    r = calcular_por_preco(0.0, 5, 100.0, REGRAS)
    assert r.margem_liquida == 0 and r.lucro == 0


@pytest.mark.parametrize("codigo,esperado", [
    ("À VISTA", 0.0), ("30", 0.016), ("30/60", 0.032), ("30/60/90", 0.048),
    ("30/60/90/120", 0.064), ("30/60/90/120/150", 0.080),
])
def test_encargo_financeiro_vem_da_tabela(session, codigo, esperado):
    condicoes = session.exec(select(CondicaoPagamento)).all()
    assert resolver_encargo(condicoes, codigo).pct == pytest.approx(esperado)


def test_condicao_sem_taxa_confirmada_avisa_em_vez_de_estimar(session):
    condicoes = session.exec(select(CondicaoPagamento)).all()
    r = resolver_encargo(condicoes, "CARTAO")
    assert r.confirmado is False and r.pct == 0.0 and r.aviso


def test_condicao_nao_cadastrada_usa_regua_antiga_com_aviso(session):
    condicoes = session.exec(select(CondicaoPagamento)).all()
    r = resolver_encargo(condicoes, "30/60/90/120/150/180")
    assert r.origem == "legado" and r.pct == pytest.approx(0.096) and r.aviso

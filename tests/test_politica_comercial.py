"""Política comercial de 16/09/2026 — as contas puras e o motor.

Comissão proporcional, desconto ponderado por valor, comissão variável presa pelo piso, taxa
efetiva, e a comissão máxima que preserva o piso — derivada da decomposição canônica do
preço, não de uma fórmula paralela.
"""
from decimal import Decimal

import pytest

from app import politica_comercial as pol
from app.dinheiro import D, ZERO, dinheiro
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_preco, com_comissao_fixa,
    comissao_maxima_para_margem, pis_cofins_efetivo,
)
from decimais import MARGEM_DO_CENTAVO, aprox  # noqa: E402

BASE, MINIMO = D("0.10"), D("0.05")


def regras(icms="0.18", encargo="0.016", comissao="0.10"):
    return TaxRuleSet(icms_pct=D(icms), pis_cofins_pct=pis_cofins_efetivo(D("0.0925"), D(icms)),
                      encargo_financeiro_pct=D(encargo), comissao_tabela=[(ZERO, D(comissao))])


# ---------------------------------------------------------------------------
# §9 — comissão proporcional, os exemplos obrigatórios
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("desconto,esperado", [
    ("0", "0.10"), ("0.05", "0.095"), ("0.10", "0.09"), ("0.18", "0.082"), ("0.30", "0.07"),
    ("0.50", "0.05"), ("0.60", "0.05"),
])
def test_comissao_proporcional_exemplos(desconto, esperado):
    assert pol.comissao_proporcional(D(desconto), BASE, MINIMO) == D(esperado)


def test_preco_acima_da_tabela_nao_sobe_a_comissao():
    assert pol.desconto_ponderado(D("1000"), D("1100")) == ZERO
    assert pol.comissao_proporcional(ZERO, BASE, MINIMO) == D("0.10")
    # mesmo um desconto negativo passado direto não passa de 10%
    assert pol.comissao_proporcional(D("-0.2"), BASE, MINIMO) == D("0.10")


# ---------------------------------------------------------------------------
# §8 — desconto ponderado por valor, não média de percentuais
# ---------------------------------------------------------------------------
def test_desconto_e_ponderado_por_valor():
    # item A: R$ 1.000 recomendado, 20% de desconto; item B: R$ 100, sem desconto
    r = D("1000") + D("100")
    n = D("800") + D("100")
    ponderado = pol.desconto_ponderado(r, n)
    assert ponderado == D("200") / D("1100")
    assert ponderado != (D("0.20") + ZERO) / 2          # a média simples seria 10%


def test_universo_vazio_nao_tem_desconto():
    assert pol.desconto_ponderado(ZERO, ZERO) == ZERO


# ---------------------------------------------------------------------------
# §10 — comissão variável presa pelo piso
# ---------------------------------------------------------------------------
def test_comissao_variavel_e_o_minimo_entre_proporcional_e_pisos():
    assert pol.comissao_variavel(D("0.09"), [D("0.12"), D("0.11")], MINIMO) == D("0.09")
    assert pol.comissao_variavel(D("0.09"), [D("0.12"), D("0.07")], MINIMO) == D("0.07")
    assert pol.comissao_variavel(D("0.09"), [D("0.02")], MINIMO) == D("0.05")
    assert pol.comissao_variavel(D("0.09"), [D("-0.10")], MINIMO) == D("0.05")
    assert pol.comissao_variavel(D("0.09"), [], MINIMO) == D("0.09")


def test_taxa_efetiva_e_razao_de_reais_nunca_media():
    assert pol.taxa_efetiva(D("150"), D("2000")) == D("0.075")
    assert pol.taxa_efetiva(ZERO, ZERO) is None


# ---------------------------------------------------------------------------
# Motor — comissão máxima para o piso sai da decomposição canônica
# ---------------------------------------------------------------------------
def test_comissao_maxima_para_o_piso_reproduz_o_piso_no_motor():
    """Aplicando exatamente c_max, a margem realizada é o piso (a menos do centavo)."""
    r = regras()
    custo, qtd, piso = D("100"), D("10"), D("0.17")
    preco = dinheiro(calcular_por_margem(custo, 1, D("0.20"), r).preco_negociado * D("0.9"))
    c_max = comissao_maxima_para_margem(custo, qtd, preco, piso, r)
    assert c_max is not None
    res = calcular_por_preco(custo, qtd, preco, com_comissao_fixa(r, c_max))
    assert res.margem_liquida == aprox(piso, abs=MARGEM_DO_CENTAVO)
    # meio ponto acima do c_max já fura o piso
    furou = calcular_por_preco(custo, qtd, preco, com_comissao_fixa(r, c_max + D("0.005")))
    assert furou.margem_liquida < piso


def test_comissao_maxima_pode_ser_negativa_e_none_sem_custo():
    r = regras()
    # preço abaixo do custo + impostos: nem sem comissão o piso se sustenta
    assert comissao_maxima_para_margem(D("100"), 1, D("110"), D("0.10"), r) < ZERO
    assert comissao_maxima_para_margem(ZERO, 1, D("110"), D("0.10"), r) is None
    assert comissao_maxima_para_margem(D("100"), 0, D("110"), D("0.10"), r) is None


def test_com_comissao_fixa_e_uma_faixa_unica_e_o_gross_up_nao_muda():
    r = com_comissao_fixa(regras(comissao="0.07"), D("0.10"))
    assert r.comissao_para_markup(D("0")) == D("0.10")
    assert r.comissao_para_markup(D("5")) == D("0.10")
    a = calcular_por_margem(D("100"), 1, D("0.12"), r)
    # preço = custo ÷ (1 − t − c − m): a identidade do gross-up com comissão fixa
    t = r.rates_variaveis()
    esperado = dinheiro(D("100") / (1 - t - D("0.10") - D("0.12")))
    assert a.preco_negociado == esperado
    assert a.margem_liquida == aprox(D("0.12"), abs=MARGEM_DO_CENTAVO)


# ---------------------------------------------------------------------------
# Derivação das regras — Daune/Decor são exceções, o resto é +2/−1
# ---------------------------------------------------------------------------
def test_regra_da_politica_por_fornecedor():
    daune = pol.regra_da_politica(0.14, pol.CODIGO_DAUNE)
    assert (daune["margem_pct"], daune["piso_pct"], daune["comissao_formacao_pct"],
            daune["preco_travado"]) == (D("0.12"), D("0.12"), D("0.05"), True)
    decor = pol.regra_da_politica(0.14, pol.CODIGO_DECOR)
    assert (decor["margem_pct"], decor["piso_pct"], decor["comissao_formacao_pct"],
            decor["preco_travado"]) == (D("0.12"), D("0.10"), D("0.10"), False)
    ktc = pol.regra_da_politica(0.18, "KTC")
    assert (ktc["margem_pct"], ktc["piso_pct"]) == (D("0.20"), D("0.17"))
    geral = pol.regra_da_politica(0.15, None)
    assert (geral["margem_pct"], geral["piso_pct"]) == (D("0.17"), D("0.14"))
    with pytest.raises(ValueError):
        pol.regra_da_politica(None, "KTC")


def test_classificar_linha():
    travada = pol.classificar(pol.LinhaNegociada(
        item_id=1, produto_id=1, nome="d", quantidade=D("2"), preco_recomendado=D("100"),
        preco_negociado=D("100"), custo_unitario=D("50"), politica=pol.ROTULO,
        preco_travado=True, piso_pct=D("0.12"), comissao_formacao_pct=D("0.05")))
    assert travada.editavel is False and travada.elegivel_variavel is False
    assert travada.faturamento == D("200.00")
    sem_custo = pol.classificar(pol.LinhaNegociada(
        item_id=2, produto_id=2, nome="s", quantidade=D("1"), preco_recomendado=None,
        preco_negociado=D("100"), custo_unitario=ZERO, politica=pol.ROTULO))
    assert sem_custo.editavel is True and sem_custo.elegivel_variavel is False
    assert sem_custo.motivo_nao_editavel == pol.MOTIVO_SEM_CUSTO
    normal = pol.classificar(pol.LinhaNegociada(
        item_id=3, produto_id=3, nome="n", quantidade=D("1"), preco_recomendado=D("100"),
        preco_negociado=D("90"), custo_unitario=D("50"), politica=pol.ROTULO))
    assert normal.editavel and normal.elegivel_variavel
    anterior = pol.classificar(pol.LinhaNegociada(
        item_id=4, produto_id=4, nome="a", quantidade=D("1"), preco_recomendado=D("100"),
        preco_negociado=D("90"), custo_unitario=D("50"), politica=None))
    assert anterior.elegivel_variavel is False, "item anterior à política não entra"

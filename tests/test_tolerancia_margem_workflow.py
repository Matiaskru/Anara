"""A tolerância da margem no workflow é **monetária**, não percentual.

`excecoes_do_item` precisa distinguir duas coisas que se parecem num relatório e não se
parecem em nada no caixa:

* o **resíduo do arredondamento comercial** — o preço é quantizado ao centavo, impostos,
  comissão e frete são quantizados cada um sobre o preço já comercial, e o lucro fecha a linha
  por diferença. Sobra ou falta uma fração de centavo. Isso não é desconto;
* o **desconto de verdade**, em que a linha entrega menos margem porque alguém abriu mão de
  receita. Isso é decisão de quem tem alçada.

## Por que a versão percentual não servia

A primeira tentativa foi uma tolerância fixa de 5×10⁻⁴ em pontos de margem. Ela resolve o item
barato e abre um buraco no caro, porque o mesmo percentual vale valores completamente
diferentes conforme o preço:

    item de R$ 15,10   ·  5×10⁻⁴ de margem  =  R$ 0,0076 de lucro
    item de R$ 9.291   ·  4,9×10⁻⁴ de margem =  R$ 4,55 de lucro   ← passava sem aprovação

Um número em pontos percentuais não sabe quanto dinheiro representa. Um número em reais sabe.

## A regra

    déficit por unidade = preço × (margem_alvo − margem_real)

Até **um centavo** por unidade, é arredondamento. Acima disso, é exceção comercial.

A unidade é **por unidade** porque é aí que o arredondamento acontece: o preço é quantizado
uma vez, por unidade, e depois multiplicado pela quantidade — uma linha de 7 peças acumula
sete vezes o mesmo centavo, e medir a linha inteira confundiria quantidade com desconto.
"""
from decimal import Decimal

import pytest

from app.dinheiro import CENTAVO, D
from app.models import CotacaoItem
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_preco, pis_cofins_efetivo,
)
from app.workflow import (
    MARGEM_ABAIXO, PRECO_ABAIXO, TOLERANCIA_DEFICIT_UNITARIO, deficit_de_lucro_unitario,
    excecoes_do_item,
)

TABELA = [(D("0"), D("0.05")), (D("0.6"), D("0.06")), (D("0.7"), D("0.07")),
          (D("0.8"), D("0.08")), (D("0.9"), D("0.09")), (D("1.0"), D("0.10"))]


def regras(icms="0.18"):
    return TaxRuleSet(icms_pct=D(icms),
                      pis_cofins_pct=pis_cofins_efetivo(D("0.0925"), D(icms)),
                      encargo_financeiro_pct=D("0.016"), comissao_tabela=TABELA)


def item(preco, margem_real, alvo="0.14", qtd=1):
    """Item mínimo com o que `excecoes_do_item` lê. `preco_recomendado` = negociado para
    isolar a regra da margem: preço abaixo do recomendado é exceção por conta própria."""
    return CotacaoItem(
        cotacao_id=0, ordem=0, nome_produto="Item de prova", quantidade=qtd,
        custo_unitario=50.0, preco_base=float(preco), preco_negociado=float(preco),
        preco_recomendado=float(preco), margem_liquida=float(margem_real),
        margem_padrao_pct=float(D(alvo)), faturamento=float(D(str(preco)) * qtd),
        custo_total=50.0 * qtd, lucro=0.0, modo_edicao="preco", valor_editado=0.0)


def motivos(it):
    return [e.motivo for e in excecoes_do_item(it)]


# ---------------------------------------------------------------------------
# A tolerância é um centavo de lucro por unidade
# ---------------------------------------------------------------------------
def test_a_tolerancia_e_um_centavo_de_verdade():
    assert TOLERANCIA_DEFICIT_UNITARIO == CENTAVO == Decimal("0.01")


def test_deficit_e_a_identidade_da_linha():
    """`preço × (alvo − real)` é `(faturamento × alvo − lucro) ÷ quantidade`.

    A igualdade não é bit a bit porque `margem_liquida` já é uma divisão truncada nos 34
    dígitos da precisão interna, e multiplicá-la de volta reintroduz a diferença do último
    dígito. O que importa é a ordem de grandeza da divergência: 10⁻³¹ de real, contra uma
    tolerância de 10⁻² — trinta ordens de grandeza de folga.
    """
    r = calcular_por_margem(D("50"), 7, D("0.14"), regras())
    por_unidade = deficit_de_lucro_unitario(r.preco_negociado, D("0.14"), r.margem_liquida)
    pela_linha = (r.faturamento * D("0.14") - r.lucro) / 7
    assert abs(por_unidade - pela_linha) < Decimal("1e-25")


# --- CASO A: o resíduo real que a correção de PIS/COFINS produziu -----------
def test_caso_A_residuo_de_arredondamento_nao_exige_aprovacao():
    """R$ 92,91 com alvo de 14% entrega 13,99204% — R$ 0,0074 por unidade. É o centavo."""
    r = calcular_por_margem(D("50"), 1, D("0.14"), regras())
    assert r.preco_negociado == D("92.91")
    assert r.margem_liquida < D("0.14"), "o resíduo caiu do lado negativo — é este o caso"

    deficit = deficit_de_lucro_unitario(r.preco_negociado, D("0.14"), r.margem_liquida)
    assert deficit < CENTAVO and deficit > 0
    assert round(float(deficit), 4) == 0.0074
    assert MARGEM_ABAIXO not in motivos(item(r.preco_negociado, r.margem_liquida))


# --- CASO B: mesmo preço, déficit acima de um centavo -----------------------
def test_caso_B_mesmo_preco_com_deficit_acima_de_um_centavo_exige_aprovacao():
    """No mesmo R$ 92,91, faltar mais de um centavo de lucro já é exceção."""
    preco = D("92.91")
    real = D("0.14") - (D("0.011") / preco)          # exatamente R$ 0,011 de déficit
    deficit = deficit_de_lucro_unitario(preco, D("0.14"), real)
    assert deficit > CENTAVO
    assert MARGEM_ABAIXO in motivos(item(preco, real))


# --- CASO C: o que a tolerância fixa de 5e-4 deixava passar -----------------
def test_caso_C_item_caro_com_desvio_percentual_minusculo_exige_aprovacao():
    """R$ 9.291 a unidade, margem 4,9×10⁻⁴ abaixo do alvo: R$ 4,55 de lucro por unidade.

    Este é o teste que **reprova a solução anterior**. Com 5×10⁻⁴ fixos, 4,9×10⁻⁴ passava
    batido — e passava levando quatro reais e meio por peça.
    """
    r = calcular_por_margem(D("5000"), 1, D("0.14"), regras())
    preco = r.preco_negociado
    assert preco > D("9000")

    real = D("0.14") - D("0.00049")
    assert D("0.00049") < D("0.0005"), "abaixo da tolerância percentual antiga"

    deficit = deficit_de_lucro_unitario(preco, D("0.14"), real)
    assert deficit > D("4.5") and deficit > CENTAVO
    assert MARGEM_ABAIXO in motivos(item(preco, real))


def test_caso_C_generalizado_um_real_de_deficit_sempre_exige_aprovacao():
    """Em qualquer preço: se falta R$ 1,00 por unidade, é exceção. Sem exceção à regra."""
    for preco in (D("15.10"), D("92.91"), D("1000.00"), D("10000.00")):
        real = D("0.14") - (D("1.00") / preco)
        assert MARGEM_ABAIXO in motivos(item(preco, real)), f"preço {preco}"


# --- CASO D: desconto de verdade -------------------------------------------
def test_caso_D_meio_ponto_percentual_abaixo_do_alvo_exige_aprovacao():
    """0,5 p.p. abaixo do alvo é desconto, não centavo — em qualquer preço."""
    for preco in (D("15.10"), D("92.91"), D("9291.09")):
        assert MARGEM_ABAIXO in motivos(item(preco, D("0.135"))), f"preço {preco}"


def test_mudanca_de_regra_fiscal_continua_reprovada():
    """Trocar a alíquota move a margem em pontos percentuais. Mil vezes o centavo."""
    preco = calcular_por_margem(D("100"), 1, D("0.14"), regras("0.18")).preco_negociado
    outra = calcular_por_preco(D("100"), 1, preco, regras("0.12")).margem_liquida
    deficit = deficit_de_lucro_unitario(preco, D("0.14"), outra)
    assert abs(deficit) > CENTAVO * 50
    assert MARGEM_ABAIXO in motivos(item(preco, outra)) or outra > D("0.14")


# ---------------------------------------------------------------------------
# Nada mais mudou na regra de aprovação
# ---------------------------------------------------------------------------
def test_margem_acima_do_alvo_nunca_e_excecao():
    assert motivos(item(D("92.91"), D("0.18"))) == []


def test_preco_abaixo_do_recomendado_continua_sendo_excecao_com_margem_boa():
    """A autonomia de desconto do vendedor é zero — isto não foi tocado."""
    it = item(D("92.91"), D("0.18"))
    it.preco_recomendado = 100.0
    assert PRECO_ABAIXO in motivos(it)


def test_item_sem_preco_continua_sinalizando_margem_abaixo():
    """Item bloqueado tem preço zero: sem receita não há déficit a medir, e a comparação
    estrita continua valendo em vez de aprovar por omissão."""
    it = item(D("0"), D("0"))
    it.preco_negociado = 0.0
    assert MARGEM_ABAIXO in motivos(it)


def test_o_detalhe_diz_quanto_dinheiro_e():
    """O aprovador precisa do valor, não só do percentual."""
    preco = D("9291.09")
    real = D("0.14") - D("0.00049")
    excecao = [e for e in excecoes_do_item(item(preco, real)) if e.motivo == MARGEM_ABAIXO][0]
    assert "R$ 4,55".replace(",", ".") in excecao.detalhe or "4.55" in excecao.detalhe
    assert "por unidade" in excecao.detalhe

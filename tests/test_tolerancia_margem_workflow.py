"""A tolerância da margem no workflow é **derivada do arredondamento**, não escolhida.

`excecoes_do_item` precisa distinguir duas coisas que se parecem num relatório e não se
parecem em nada no caixa:

* o **resíduo do arredondamento comercial** — o preço é quantizado ao centavo, e impostos,
  comissão, custo total e frete são quantizados cada um sobre o preço já comercial. O lucro
  fecha a linha por diferença e absorve todos esses meios-centavos. Isso não é desconto;
* o **desconto de verdade**, em que a linha entrega menos margem porque alguém abriu mão de
  receita. Isso é decisão de quem tem alçada.

## Duas tentativas anteriores, e por que falharam

**5×10⁻⁴ em pontos de margem.** Um percentual não sabe quanto dinheiro representa: num item de
R$ 9.291 a unidade, 4,9×10⁻⁴ de margem são **R$ 4,55 de lucro** — e passavam sem aprovação.

**R$ 0,01 por unidade, constante.** Direção certa (monetária), valor insuficiente: uma varredura
de 2.700 combinações encontrou **9 casos** com déficit puramente de arredondamento entre
R$ 0,0101 e R$ 0,0140. Todos com quantidade 1, quase todos com frete RV — o quarto componente
quantizado que o número de R$ 0,01 não previa.

## O que vale agora

    tolerância_unitária = 0,005 × (1 + (1 + n)/q)

`n` é quantas quantias quantizadas caem no resíduo do lucro **deste item** (três sempre —
impostos, comissão, custo total —, mais frete CF e RV quando existem) e `q` é a quantidade.
A derivação completa está na docstring de `workflow.tolerancia_de_arredondamento`.

Duas propriedades que os testes abaixo cobram: o limite **cai com a quantidade**, porque os
resíduos são por linha e diluem; e continua reprovando qualquer perda economicamente material,
em qualquer preço.
"""
from decimal import Decimal

import pytest

from app.dinheiro import CENTAVO, D
from app.models import CotacaoItem
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_preco, pis_cofins_efetivo,
)
from app.workflow import (
    COMPONENTES_SEMPRE_QUANTIZADOS, MARGEM_ABAIXO, MEIO_CENTAVO, PRECO_ABAIXO,
    componentes_quantizados_do_item, deficit_de_lucro_unitario, excecoes_do_item,
    tolerancia_de_arredondamento,
)

TABELA = [(D("0"), D("0.05")), (D("0.6"), D("0.06")), (D("0.7"), D("0.07")),
          (D("0.8"), D("0.08")), (D("0.9"), D("0.09")), (D("1.0"), D("0.10"))]


def regras(icms="0.18", encargo="0.016", cf="0", rv="0"):
    return TaxRuleSet(icms_pct=D(icms),
                      pis_cofins_pct=pis_cofins_efetivo(D("0.0925"), D(icms)),
                      encargo_financeiro_pct=D(encargo), comissao_tabela=TABELA,
                      frete_cf_unitario=D(cf), frete_rv_pct=D(rv))


def item(preco, margem_real, alvo="0.14", qtd=1, cf=None, rv=None):
    """Item mínimo com o que `excecoes_do_item` lê.

    `preco_recomendado` = negociado para isolar a regra da margem: preço abaixo do recomendado
    é exceção por conta própria, e misturar as duas esconderia qual regra disparou.
    """
    return CotacaoItem(
        cotacao_id=0, ordem=0, nome_produto="Item de prova", quantidade=float(qtd),
        custo_unitario=50.0, preco_base=float(preco), preco_negociado=float(preco),
        preco_recomendado=float(preco), margem_liquida=float(margem_real),
        margem_padrao_pct=float(D(alvo)), faturamento=float(D(str(preco)) * qtd),
        custo_total=50.0 * qtd, lucro=0.0, modo_edicao="preco", valor_editado=0.0,
        frete_cf_unitario=cf, frete_rv_pct=rv)


def motivos(it):
    return [e.motivo for e in excecoes_do_item(it)]


def de_cenario(custo, qtd, alvo, icms, cf="0", rv="0", encargo="0.016"):
    """Roda o motor de verdade e devolve (item, déficit, tolerância) daquele cenário."""
    r = regras(icms, encargo, cf, rv)
    res = calcular_por_margem(D(str(custo)), qtd, D(alvo), r)
    it = item(res.preco_negociado, res.margem_liquida, alvo=alvo, qtd=qtd,
              cf=float(D(cf)) or None, rv=float(D(rv)) or None)
    deficit = deficit_de_lucro_unitario(res.preco_negociado, D(alvo), res.margem_liquida)
    return it, deficit, tolerancia_de_arredondamento(qtd, componentes_quantizados_do_item(it))


# ---------------------------------------------------------------------------
# A fórmula
# ---------------------------------------------------------------------------
def test_meio_centavo_e_o_erro_de_uma_quantizacao():
    assert MEIO_CENTAVO == CENTAVO / 2 == Decimal("0.005")
    assert COMPONENTES_SEMPRE_QUANTIZADOS == 3


@pytest.mark.parametrize("q,n,esperado", [
    (1, 5, "0.035"),        # pior caso: uma peça, frete CF e RV
    (1, 3, "0.025"),        # uma peça, sem frete
    (2, 3, "0.015"),
    (10, 3, "0.007"),
    (100, 3, "0.0052"),
    (2000, 3, "0.005010"),  # assintota: sobra só o meio centavo do preço unitário
])
def test_a_formula_do_limite(q, n, esperado):
    """0,005 × (1 + (1+n)/q), exato em `Decimal`."""
    assert tolerancia_de_arredondamento(q, n) == Decimal(esperado)


def test_o_limite_cai_com_a_quantidade():
    """Os resíduos são por LINHA e diluem: 100 peças não toleram 100 vezes o mesmo centavo."""
    anterior = None
    for q in (1, 2, 5, 10, 50, 100, 1000):
        atual = tolerancia_de_arredondamento(q, 3)
        if anterior is not None:
            assert atual < anterior, f"q={q} não apertou o limite"
        anterior = atual
    # e nunca desce abaixo do meio centavo do preço unitário, que não dilui
    assert tolerancia_de_arredondamento(10**9, 3) > MEIO_CENTAVO


def test_o_limite_cresce_com_os_componentes():
    """Frete CF e RV acrescentam quantização; sem eles, o limite é mais apertado."""
    assert tolerancia_de_arredondamento(1, 5) > tolerancia_de_arredondamento(1, 3)
    assert tolerancia_de_arredondamento(1, 3) == MEIO_CENTAVO * 5


def test_quantidade_zero_cai_no_pior_caso():
    """Sem quantidade não há diluição — vale o limite de uma peça, não uma divisão por zero."""
    assert tolerancia_de_arredondamento(0, 3) == tolerancia_de_arredondamento(1, 3)


def test_contagem_de_componentes_segue_o_item():
    assert componentes_quantizados_do_item(item(D("100"), D("0.14"))) == 3
    assert componentes_quantizados_do_item(item(D("100"), D("0.14"), cf=3.47)) == 4
    assert componentes_quantizados_do_item(item(D("100"), D("0.14"), cf=3.47, rv=0.003)) == 5
    assert componentes_quantizados_do_item(item(D("100"), D("0.14"), rv=0.003)) == 4


def test_deficit_e_a_identidade_da_linha():
    """`preço × (alvo − real)` é `(faturamento × alvo − lucro) ÷ quantidade`.

    A igualdade não é bit a bit porque `margem_liquida` já é uma divisão truncada nos 34
    dígitos da precisão interna, e multiplicá-la de volta reintroduz a diferença do último
    dígito. A divergência fica em 10⁻³¹ de real, contra um limite de 10⁻².
    """
    r = calcular_por_margem(D("50"), 7, D("0.14"), regras())
    por_unidade = deficit_de_lucro_unitario(r.preco_negociado, D("0.14"), r.margem_liquida)
    pela_linha = (r.faturamento * D("0.14") - r.lucro) / 7
    assert abs(por_unidade - pela_linha) < Decimal("1e-25")


# ---------------------------------------------------------------------------
# CASO A — os 9 casos que a constante de R$ 0,01 reprovava
# ---------------------------------------------------------------------------
#: Extraídos da varredura de 2.700 combinações que motivou esta correção: déficit PURO de
#: arredondamento entre R$ 0,0101 e R$ 0,0140. Todos com quantidade 1 — é aí que os resíduos
#: de linha não têm por onde diluir. (custo, qtd, alvo, ICMS, CF, RV, déficit aproximado)
CASOS_ACIMA_DE_UM_CENTAVO = [
    (10, 1, "0.14", "0.04", "0", "0.003", "0.0140"),
    (50, 1, "0.14", "0.07", "0", "0.003", "0.0136"),
    (400, 1, "0.18", "0.22", "0", "0.003", "0.0132"),
    (92, 1, "0.14", "0.04", "0", "0", "0.0120"),
    (10, 1, "0.11", "0.12", "3.47", "0.003", "0.0114"),
    (7, 1, "0.11", "0.07", "3.47", "0.003", "0.0114"),
    (92, 1, "0.14", "0.04", "0", "0.003", "0.0102"),
    (92, 1, "0.11", "0.04", "0", "0.003", "0.0101"),
    (10, 1, "0.11", "0.22", "0", "0.003", "0.0101"),
]


@pytest.mark.parametrize("custo,qtd,alvo,icms,cf,rv,deficit_esperado",
                         CASOS_ACIMA_DE_UM_CENTAVO)
def test_caso_A_os_nove_residuos_nao_exigem_aprovacao(custo, qtd, alvo, icms, cf, rv,
                                                      deficit_esperado):
    """Puro arredondamento acima de um centavo: dentro do limite derivado, sem aprovação."""
    it, deficit, tolerancia = de_cenario(custo, qtd, alvo, icms, cf, rv)

    assert deficit > CENTAVO, "este caso existe justamente por passar de R$ 0,01"
    assert round(float(deficit), 4) == float(deficit_esperado)
    assert deficit <= tolerancia, f"déficit {deficit} acima do limite {tolerancia}"
    assert MARGEM_ABAIXO not in motivos(it)


def test_caso_A_residuo_classico_de_noventa_e_dois_reais():
    """O caso que apareceu primeiro: R$ 92,91, alvo 14%, entrega 13,99204%."""
    it, deficit, tolerancia = de_cenario(50, 1, "0.14", "0.18")
    assert round(float(deficit), 4) == 0.0074
    assert deficit < tolerancia
    assert MARGEM_ABAIXO not in motivos(it)


# ---------------------------------------------------------------------------
# CASOS B e C — o limite exato, e um passo além dele
# ---------------------------------------------------------------------------
#: Quantidades cujo limite divide exato por R$ 500,00 — nelas o déficit pode ser construído
#: **igual** ao limite, sem resto. Em q = 7, 42 ou 300 o limite é dízima
#: (0,005 × (1 + 4/7) e afins) e o valor exato não é representável: a fronteira é testada
#: nesses casos por dentro e por fora, logo abaixo.
LIMITES_EXATOS = [(1, 3), (1, 5), (2, 3), (10, 3), (100, 3)]


@pytest.mark.parametrize("qtd,n", LIMITES_EXATOS)
def test_caso_B_exatamente_no_limite_nao_exige(qtd, n):
    """Déficit **igual** ao limite é arredondamento por definição: a comparação é `>`, não `>=`."""
    preco = D("500.00")
    tolerancia = tolerancia_de_arredondamento(qtd, n)
    real = D("0.14") - (tolerancia / preco)
    it = item(preco, real, qtd=qtd,
              cf=3.47 if n >= 4 else None, rv=0.003 if n >= 5 else None)

    assert componentes_quantizados_do_item(it) == n
    assert deficit_de_lucro_unitario(preco, D("0.14"), real) == tolerancia, "sem resto"
    assert MARGEM_ABAIXO not in motivos(it)


@pytest.mark.parametrize("qtd,n", LIMITES_EXATOS + [(7, 3), (42, 5), (300, 3)])
def test_caso_B_um_fio_abaixo_do_limite_nao_exige(qtd, n):
    """Inclusive nas quantidades de limite não representável, por dentro da fronteira."""
    preco = D("500.00")
    tolerancia = tolerancia_de_arredondamento(qtd, n)
    real = D("0.14") - ((tolerancia - D("0.000001")) / preco)
    it = item(preco, real, qtd=qtd,
              cf=3.47 if n >= 4 else None, rv=0.003 if n >= 5 else None)

    assert deficit_de_lucro_unitario(preco, D("0.14"), real) < tolerancia
    assert MARGEM_ABAIXO not in motivos(it)


@pytest.mark.parametrize("qtd,n", LIMITES_EXATOS + [(7, 3), (42, 5), (300, 3)])
def test_caso_C_um_centesimo_de_centavo_acima_do_limite_exige(qtd, n):
    """Imediatamente acima do que o arredondamento explica, vira decisão de alçada."""
    preco = D("500.00")
    tolerancia = tolerancia_de_arredondamento(qtd, n)
    real = D("0.14") - ((tolerancia + D("0.0001")) / preco)
    it = item(preco, real, qtd=qtd,
              cf=3.47 if n >= 4 else None, rv=0.003 if n >= 5 else None)
    assert deficit_de_lucro_unitario(preco, D("0.14"), real) > tolerancia
    assert MARGEM_ABAIXO in motivos(it)


def test_caso_C_a_diluicao_muda_o_veredito():
    """O mesmo déficit por unidade: perdoado em 1 peça, exceção em 100.

    É o coração da fórmula. Numa peça, R$ 0,02 cabe no resíduo dos três componentes; em cem,
    os resíduos já diluíram e R$ 0,02 por unidade são R$ 2,00 de lucro na linha.
    """
    preco, alvo = D("500.00"), D("0.14")
    real = alvo - (D("0.02") / preco)          # déficit de exatamente R$ 0,02 por unidade
    assert MARGEM_ABAIXO not in motivos(item(preco, real, qtd=1))
    assert MARGEM_ABAIXO in motivos(item(preco, real, qtd=100))


# ---------------------------------------------------------------------------
# CASOS D e E — perda material
# ---------------------------------------------------------------------------
def test_caso_D_item_caro_com_desvio_percentual_minusculo_exige_aprovacao():
    """R$ 9.291 a unidade, margem 4,9×10⁻⁴ abaixo do alvo: R$ 4,55 de lucro por unidade.

    Reprova a primeira solução, a percentual: 4,9×10⁻⁴ ficava abaixo dos 5×10⁻⁴ e passava.
    """
    preco = calcular_por_margem(D("5000"), 1, D("0.14"), regras()).preco_negociado
    assert preco > D("9000")
    real = D("0.14") - D("0.00049")

    deficit = deficit_de_lucro_unitario(preco, D("0.14"), real)
    assert deficit > D("4.5")
    assert deficit > tolerancia_de_arredondamento(1, 5), "acima até do pior limite possível"
    assert MARGEM_ABAIXO in motivos(item(preco, real))


def test_caso_D_generalizado_um_real_de_deficit_sempre_exige_aprovacao():
    """Em qualquer preço e qualquer quantidade: R$ 1,00 por unidade é exceção."""
    for preco in (D("15.10"), D("92.91"), D("1000.00"), D("10000.00")):
        for qtd in (1, 42, 2000):
            real = D("0.14") - (D("1.00") / preco)
            assert MARGEM_ABAIXO in motivos(item(preco, real, qtd=qtd)), f"{preco} × {qtd}"


def test_caso_E_meio_ponto_percentual_abaixo_do_alvo_exige_aprovacao():
    """0,5 p.p. abaixo do alvo é desconto, não centavo — em qualquer preço."""
    for preco in (D("15.10"), D("92.91"), D("9291.09")):
        assert MARGEM_ABAIXO in motivos(item(preco, D("0.135"))), f"preço {preco}"


def test_mudanca_de_regra_fiscal_continua_reprovada():
    """Trocar a alíquota move a margem em pontos percentuais. Ordens acima do centavo."""
    preco = calcular_por_margem(D("100"), 1, D("0.14"), regras("0.18")).preco_negociado
    outra = calcular_por_preco(D("100"), 1, preco, regras("0.12")).margem_liquida
    deficit = deficit_de_lucro_unitario(preco, D("0.14"), outra)
    assert abs(deficit) > tolerancia_de_arredondamento(1, 5) * 20
    assert MARGEM_ABAIXO in motivos(item(preco, outra)) or outra > D("0.14")


# ---------------------------------------------------------------------------
# CASOS F e G — o resto da regra de aprovação não mudou
# ---------------------------------------------------------------------------
def test_caso_F_preco_abaixo_do_recomendado_continua_sendo_excecao_com_margem_boa():
    """A autonomia de desconto do vendedor é zero — regra própria, intocada."""
    it = item(D("92.91"), D("0.18"))
    it.preco_recomendado = 100.0
    assert PRECO_ABAIXO in motivos(it)


def test_caso_F_preco_abaixo_do_recomendado_dispara_mesmo_dentro_da_tolerancia():
    """As duas regras são independentes: a de margem perdoar não faz a de preço perdoar."""
    it = item(D("92.91"), D("0.14"), qtd=1)
    it.preco_recomendado = 92.92
    assert PRECO_ABAIXO in motivos(it)
    assert MARGEM_ABAIXO not in motivos(it)


def test_caso_G_margem_acima_do_alvo_nunca_e_excecao():
    for qtd in (1, 42, 2000):
        assert motivos(item(D("92.91"), D("0.18"), qtd=qtd)) == []


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
    assert "4.55" in excecao.detalhe
    assert "por unidade" in excecao.detalhe


# ---------------------------------------------------------------------------
# A cota superior, verificada contra o motor
# ---------------------------------------------------------------------------
def test_nenhum_deficit_de_arredondamento_ultrapassa_o_limite():
    """Varredura compacta da mesma invariante que `scripts/verificar_tolerancia_arredondamento.py`
    exercita em 55.000 combinações.

    Aqui roda um subconjunto que cabe na suíte. Se esta falhar, **não** aumente a tolerância:
    procure a quantização que a derivação não previu, em `pricing_engine.calcular_por_preco`.
    """
    import itertools

    from app.workflow import COMPONENTES_SEMPRE_QUANTIZADOS

    piores = []
    for custo, qtd, alvo, icms, (cf, rv) in itertools.product(
            ["7.13", "50", "71.5255526059197", "399.99", "20000"],
            [1, 2, 7, 42, 300],
            ["0.11", "0.14", "0.18"],
            ["0.18", "0.12", "0.04", "0.22"],
            [("0", "0"), ("3.47", "0.003"), ("0", "0.008")]):
        res = calcular_por_margem(D(custo), qtd, D(alvo), regras(icms, "0.048", cf, rv))
        if res.preco_negociado <= 0:
            continue
        n = (COMPONENTES_SEMPRE_QUANTIZADOS
             + (1 if D(cf) > 0 else 0) + (1 if D(rv) > 0 else 0))
        deficit = deficit_de_lucro_unitario(res.preco_negociado, D(alvo), res.margem_liquida)
        limite = tolerancia_de_arredondamento(qtd, n)
        assert deficit <= limite, (
            f"déficit {deficit} > limite {limite} em custo={custo} qtd={qtd} alvo={alvo} "
            f"ICMS={icms} CF={cf} RV={rv}")
        piores.append(deficit)

    assert len(piores) >= 900, "a varredura encolheu — o domínio deixou de cobrir o motor"
    assert max(piores) > CENTAVO, "sem nenhum caso acima de R$ 0,01 o teste não prova nada"

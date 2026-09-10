"""Verifica que a tolerância de aprovação cobre TODO déficit de puro arredondamento.

    python3 scripts/verificar_tolerancia_arredondamento.py

Leitura pura: só funções do motor, nenhum banco, nenhuma escrita.

## O que está sendo verificado

`workflow.tolerancia_de_arredondamento(q, n)` afirma ser cota superior do déficit de lucro por
unidade que as quantizações do waterfall podem produzir sozinhas:

    tolerância = 0,005 × (1 + (1 + n)/q)

A derivação está na docstring daquela função. Este script é a contraprova empírica: roda o
motor de verdade em dezenas de milhares de combinações economicamente válidas, mede o déficit
que sobra **sem nenhum desconto envolvido** — margem-alvo pedida, preço formado por
`calcular_por_margem` — e confere que nenhuma delas ultrapassa o limite.

**VIOLAÇÕES = 0 é o critério.** Se aparecer violação, a resposta NÃO é aumentar a tolerância:
é reabrir a derivação, porque significa que existe uma quantização que ela não previu.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dinheiro import D, para_float  # noqa: E402
from app.pricing_engine import (  # noqa: E402
    TaxRuleSet, calcular_por_margem, pis_cofins_efetivo,
)
from app.workflow import (  # noqa: E402
    COMPONENTES_SEMPRE_QUANTIZADOS, deficit_de_lucro_unitario, tolerancia_de_arredondamento,
)

#: A tabela de comissão semeada. Faixas por markup — e a faixa muda o preço inteiro, então ela
#: participa do domínio como qualquer outra variável.
TABELA_COMISSAO = [(D("0"), D("0.05")), (D("0.6"), D("0.06")), (D("0.7"), D("0.07")),
                   (D("0.8"), D("0.08")), (D("0.9"), D("0.09")), (D("1.0"), D("0.10"))]

#: Domínios que o sistema realmente produz. Custos vão do item mais barato do catálogo ao
#: edredom de plumas; o `71,5255…` é um CNET real, com todas as casas, para exercitar
#: `custo_total = dinheiro(custo × q)` fora de valores redondos.
CUSTOS = ["7.13", "10", "25.5", "50", "71.5255526059197", "92.4", "150", "399.99",
          "1000", "5000", "20000"]
QUANTIDADES = [1, 2, 3, 5, 7, 10, 42, 100, 300, 2000]
MARGENS = ["0.11", "0.12", "0.14", "0.15", "0.18"]
#: ICMS que o motor fiscal resolve hoje: 4% importada, 7% e 12% interestaduais, 18% interna,
#: 22% no RJ a não contribuinte. O PIS/COFINS sai derivado de cada um.
ICMS = ["0.18", "0.12", "0.07", "0.04", "0.22"]
#: 30, 30/60, 30/60/90, 30/60/90/120 e 150 — 1,6% por parcela.
ENCARGOS = ["0.016", "0.032", "0.048", "0.064", "0.08"]
#: (frete CF unitário, rate variável). O primeiro é FOB; os demais, composições reais de
#: TRANSAL — ADV 0,20% + GRIS 0,10%, e a hipótese com fiel depositário em 0,80%.
FRETES = [("0", "0"), ("3.47", "0.003"), ("0", "0.008"), ("12.90", "0")]


def combinacoes():
    return itertools.product(CUSTOS, QUANTIDADES, MARGENS, ICMS, ENCARGOS, FRETES)


def medir():
    piores = []
    violacoes = []
    folga_minima = None
    total = 0
    por_quantidade = {}

    for custo, qtd, alvo, icms, encargo, (cf, rv) in combinacoes():
        total += 1
        regras = TaxRuleSet(
            icms_pct=D(icms), pis_cofins_pct=pis_cofins_efetivo(D("0.0925"), D(icms)),
            encargo_financeiro_pct=D(encargo), comissao_tabela=TABELA_COMISSAO,
            frete_cf_unitario=D(cf), frete_rv_pct=D(rv))
        resultado = calcular_por_margem(D(custo), qtd, D(alvo), regras)
        if resultado.preco_negociado <= 0:
            continue

        n = (COMPONENTES_SEMPRE_QUANTIZADOS
             + (1 if D(cf) > 0 else 0) + (1 if D(rv) > 0 else 0))
        deficit = deficit_de_lucro_unitario(
            resultado.preco_negociado, D(alvo), resultado.margem_liquida)
        limite = tolerancia_de_arredondamento(qtd, n)

        folga = limite - deficit
        if folga_minima is None or folga < folga_minima:
            folga_minima = folga
        anterior = por_quantidade.get(qtd)
        if anterior is None or deficit > anterior[0]:
            por_quantidade[qtd] = (deficit, limite)
        piores.append((deficit, limite, custo, qtd, alvo, icms, encargo, cf, rv,
                       resultado.preco_negociado, n))
        if deficit > limite:
            violacoes.append(piores[-1])

    piores.sort(reverse=True)
    return total, piores, violacoes, folga_minima, por_quantidade


def main() -> int:
    total, piores, violacoes, folga_minima, por_quantidade = medir()

    print("=" * 100)
    print("TOLERÂNCIA DE ARREDONDAMENTO — verificação da cota superior")
    print("  tolerância(q, n) = 0,005 × (1 + (1 + n)/q)")
    print("=" * 100)
    print(f"combinações economicamente válidas: {total}")
    print(f"folga mínima (limite − déficit):    R$ {folga_minima:.6f}")
    print()
    print("Maiores déficits de PURO arredondamento:")
    print(f"  {'déficit':>9} {'limite':>9} {'custo':>18} {'qtd':>5} {'alvo':>5} {'ICMS':>5} "
          f"{'enc':>6} {'CF':>6} {'RV':>6} {'preço':>11} {'n':>2}")
    for p in piores[:10]:
        print(f"  {p[0]:>9.6f} {p[1]:>9.6f} {p[2]:>18} {p[3]:>5} {p[4]:>5} {p[5]:>5} "
              f"{p[6]:>6} {p[7]:>6} {p[8]:>6} {p[9]:>11.2f} {p[10]:>2}")
    print()
    print("Pior déficit por quantidade — a diluição dos resíduos de linha:")
    print(f"  {'qtd':>6} {'pior déficit':>14} {'limite':>10} {'folga':>10}")
    for qtd in sorted(por_quantidade):
        deficit, limite = por_quantidade[qtd]
        print(f"  {qtd:>6} {deficit:>14.6f} {limite:>10.6f} {limite - deficit:>10.6f}")
    print()
    print(f"VIOLAÇÕES DO LIMITE TEÓRICO: {len(violacoes)}")
    for v in violacoes[:20]:
        print("   ", v)

    if violacoes:
        print()
        print("A derivação está incompleta. NÃO aumente a tolerância para acomodar: procure a")
        print("quantização que ela não previu, em pricing_engine.calcular_por_preco.")
        return 1
    print()
    print("OK — nenhuma combinação ultrapassa a cota superior derivada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

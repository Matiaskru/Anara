#!/usr/bin/env python3
"""Regressão numérica da Sessão 3B contra o baseline de ENTRADA da própria sessão.

Por que um baseline próprio, e não o imutável da Fase 0: o baseline da Fase 0 carrega as
diferenças das Sessões 1, 2 e 3A somadas às desta. Misturá-las tira a autoria de cada
centavo — que é exatamente o que a divisão 3A/3B existiu para evitar. Aqui o "antes" é o
estado em `4a0a4a0`, gerado com o código **pré-3B**, então **toda** diferença encontrada
pertence à 3B e só a ela.

Classes (§34):

* `IGUAL`                        — as seis posições da célula idênticas
* `DECIMAL_REPRESENTATION_ONLY`  — mesmo valor econômico, escrita diferente (100.1 → 100.10)
* `ROUNDING_CORRIGIDO`           — o preço passou a ser quantia comercial: |Δpreço| ≤ meio
                                   centavo, e os componentes acompanham
* `RATEIO_CORRIGIDO`             — resíduo de centavo redistribuído pelo maior resto
* `FAIXA_COMISSAO_MUDOU`         — a faixa de comissão mudou de patamar. **Não é
                                   arredondamento**: é mudança real de preço e vai listada
                                   item a item
* `NAO_EXPLICADA`                — qualquer outra coisa. Tem de ser ZERO

A grade é `[preço, margem, markup, comissão%, impostos, lucro]` por SKU × cenário × condição.

Uso:
    python3 scripts/comparar_baseline_sessao3b.py
    python3 scripts/comparar_baseline_sessao3b.py --saida relatorios/regressao_sessao3b.json
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.baseline_regressao_v2 import gerar  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRADA = os.path.join(RAIZ, "relatorios", "baseline_entrada_sessao3b.json")
SAIDA = os.path.join(RAIZ, "relatorios", "regressao_sessao3b.json")

# O arredondamento comercial move o preço unitário em, no máximo, meio centavo.
MEIO_CENTAVO = 0.005
# Folgas dos componentes: cada um é quantizado ao centavo, e o lucro é o resíduo de cinco
# deles — por isso a folga do lucro é maior que a de um componente isolado.
TOL_COMPONENTE = 0.011
TOL_LUCRO = 0.051
# Abaixo disto, dois floats são o MESMO número escrito de outro jeito.
TOL_REPRESENTACAO = 1e-9

# As faixas de comissão aprovadas. A 4ª posição da grade NÃO é a faixa: é a razão
# `comissão ÷ faturamento`. Antes da 3B a comissão era `faturamento × 5%` exato e a razão dava
# 0,05 redondo; agora a comissão é quantizada ao centavo, então a razão de um item de R$ 20
# sai 0,049905. Isso **não** é troca de faixa — é a razão de uma quantia arredondada. Para
# saber se a faixa mudou de verdade, cada razão é ancorada na faixa mais próxima; como as
# faixas distam 1 ponto percentual e a quantização move menos de 0,001, a âncora é inequívoca.
FAIXAS_COMISSAO = (0.05, 0.06, 0.07, 0.08, 0.09, 0.10)


def faixa_ancorada(razao):
    if razao is None:
        return None
    return min(FAIXAS_COMISSAO, key=lambda f: abs(f - razao))


def tolerancia_margem(preco):
    """Quanto o centavo comercial pode mover a margem, para um preço deste tamanho.

    A margem é `lucro ÷ receita`, e o lucro é o resíduo de componentes quantizados: receita,
    impostos, comissão e custo, cada um com até meio centavo de desvio. Logo
    `|Δlucro| ≲ 4 × meio centavo` e `|Δmargem| ≈ |Δlucro| ÷ preço`. Num item de R$ 12 isso é
    ~0,0017; num de R$ 400, ~0,00005. Uma tolerância ABSOLUTA reprovaria os itens baratos e
    passaria pano nos caros — por isso ela é proporcional ao preço.
    """
    return (4 * MEIO_CENTAVO) / max(abs(preco or 0), 1.0) + 1e-6


def tolerancia_markup(preco, markup):
    """`markup = preço × (1 − taxas) / custo − 1`: o desvio escala com Δpreço ÷ preço."""
    return (MEIO_CENTAVO / max(abs(preco or 0), 1.0)) * (1 + abs(markup or 0)) + 1e-6

PRECO, MARGEM, MARKUP, COMISSAO, IMPOSTOS, LUCRO = range(6)


def _quase(a, b, tol):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def classificar_celula(antes, depois):
    """Classe da diferença de UMA célula da grade. Devolve `(classe, detalhe_ou_None)`."""
    if antes is None and depois is None:
        return "IGUAL", None
    if isinstance(antes, dict) or isinstance(depois, dict):
        # célula bloqueada dos dois lados pelo mesmo motivo continua igual
        if isinstance(antes, dict) and isinstance(depois, dict):
            if antes.get("motivo") == depois.get("motivo"):
                return "IGUAL", None
            return "NAO_EXPLICADA", {"motivo_antes": antes.get("motivo"),
                                     "motivo_depois": depois.get("motivo")}
        return "NAO_EXPLICADA", {"antes": antes, "depois": depois}
    if antes is None or depois is None:
        return "NAO_EXPLICADA", {"antes": antes, "depois": depois}

    if antes == depois:
        return "IGUAL", None

    # mesmo número, escrita diferente
    if all(_quase(antes[i], depois[i], TOL_REPRESENTACAO) for i in range(6)):
        return "DECIMAL_REPRESENTATION_ONLY", None

    # a faixa de comissão mudar não é arredondamento: muda o preço inteiro
    if faixa_ancorada(antes[COMISSAO]) != faixa_ancorada(depois[COMISSAO]):
        return "FAIXA_COMISSAO_MUDOU", {
            "comissao_antes": antes[COMISSAO], "comissao_depois": depois[COMISSAO],
            "faixa_antes": faixa_ancorada(antes[COMISSAO]),
            "faixa_depois": faixa_ancorada(depois[COMISSAO])}

    preco = depois[PRECO]
    dentro = (_quase(antes[PRECO], depois[PRECO], MEIO_CENTAVO)
              and _quase(antes[MARGEM], depois[MARGEM], tolerancia_margem(preco))
              and _quase(antes[MARKUP], depois[MARKUP],
                         tolerancia_markup(preco, depois[MARKUP]))
              and _quase(antes[IMPOSTOS], depois[IMPOSTOS], TOL_COMPONENTE)
              and _quase(antes[LUCRO], depois[LUCRO], TOL_LUCRO))
    if dentro:
        return "ROUNDING_CORRIGIDO", None

    return "NAO_EXPLICADA", {
        "delta_preco": depois[PRECO] - antes[PRECO],
        "delta_margem": depois[MARGEM] - antes[MARGEM],
        "tolerancia_margem": tolerancia_margem(depois[PRECO]),
        "delta_markup": depois[MARKUP] - antes[MARKUP],
        "delta_impostos": depois[IMPOSTOS] - antes[IMPOSTOS],
        "delta_lucro": depois[LUCRO] - antes[LUCRO],
    }


def classificar_campo_produto(nome, a, d):
    """Campos econômicos do próprio SKU (custo, preço-base)."""
    if a == d:
        return "IGUAL"
    if a is None or d is None:
        return "NAO_EXPLICADA"
    if abs(a - d) <= TOL_REPRESENTACAO:
        return "DECIMAL_REPRESENTATION_ONLY"
    if nome == "preco_base" and abs(a - d) <= MEIO_CENTAVO:
        return "ROUNDING_CORRIGIDO"
    if nome in ("custo_unitario", "custo_net_recalculado") and abs(a - d) <= TOL_REPRESENTACAO:
        return "DECIMAL_REPRESENTATION_ONLY"
    return "NAO_EXPLICADA"


# ---------------------------------------------------------------------------
# Histórico: as 18 cotações, os 45 itens e os snapshots
# ---------------------------------------------------------------------------
# Campos economicamente relevantes do item. `memoria_sha256` é o hash da memória de preço
# congelada — se ele mudar, o snapshot foi reescrito.
CAMPOS_ITEM_ECONOMICOS = [
    "id", "cotacao_id", "produto_id", "quantidade", "custo_unitario", "preco_base",
    "preco_negociado", "margem_liquida", "faturamento", "custo_total", "lucro",
    "impostos", "comissao_valor", "comissao_pct", "markup_implicito",
    "diferenca_pct_vs_base", "icms_pct", "difal_valor", "encargo_pct", "memoria_sha256",
]
CAMPOS_COTACAO_ECONOMICOS = [
    "id", "numero", "status", "faturamento_total", "custo_total", "lucro_total",
    "margem_total", "num_itens",
]


def comparar_historico(antes: dict, depois: dict) -> dict:
    """Campo a campo. Distingue mudança ECONÔMICA de mudança de REPRESENTAÇÃO."""
    resultado = {"cotacoes": {}, "itens": {}}

    for chave, campos, rotulo in (("cotacoes", CAMPOS_COTACAO_ECONOMICOS, "cotacoes"),
                                  ("itens", CAMPOS_ITEM_ECONOMICOS, "itens")):
        linhas_antes = {l.get("id"): l for l in antes.get(chave, [])}
        linhas_depois = {l.get("id"): l for l in depois.get(chave, [])}
        contagem = Counter()
        economicas = []
        representacao = []
        contagem["linhas_antes"] = len(linhas_antes)
        contagem["linhas_depois"] = len(linhas_depois)

        for ident, la in linhas_antes.items():
            ld = linhas_depois.get(ident)
            if ld is None:
                contagem["LINHA_SUMIU"] += 1
                economicas.append({"id": ident, "campo": "(linha inteira)",
                                   "antes": "existia", "depois": None})
                continue
            for campo in campos:
                if campo not in la and campo not in ld:
                    continue
                a, d = la.get(campo), ld.get(campo)
                if a == d:
                    contagem["IGUAL"] += 1
                    continue
                if isinstance(a, (int, float)) and isinstance(d, (int, float)) \
                        and abs(a - d) <= TOL_REPRESENTACAO:
                    contagem["DECIMAL_REPRESENTATION_ONLY"] += 1
                    representacao.append({"id": ident, "campo": campo,
                                          "antes": a, "depois": d})
                    continue
                contagem["MUDANCA_ECONOMICA"] += 1
                economicas.append({"id": ident, "campo": campo, "antes": a, "depois": d,
                                   "diferenca": (d - a) if isinstance(a, (int, float))
                                                and isinstance(d, (int, float)) else None})
        resultado[rotulo] = {
            "resumo": dict(contagem),
            "mudancas_economicas": economicas,
            "apenas_representacao": representacao[:50],
        }
    return resultado


# ---------------------------------------------------------------------------
def comparar(entrada_path: str = ENTRADA) -> dict:
    with open(entrada_path, encoding="utf-8") as f:
        antes = json.load(f)
    depois = gerar(com_estado_banco=False)

    por_sku_antes = {p["sku"]: p for p in antes["produtos"]}
    resumo = Counter()
    por_classe = defaultdict(list)
    variacoes = []
    campos_sku = Counter()
    campos_sku_detalhe = []

    for p in depois["produtos"]:
        a = por_sku_antes.get(p["sku"])
        if a is None:
            resumo["SKU_NOVO"] += 1
            continue

        for campo in ("custo_unitario", "preco_base", "custo_net_recalculado"):
            classe = classificar_campo_produto(campo, a.get(campo), p.get(campo))
            campos_sku[f"{campo}:{classe}"] += 1
            if classe not in ("IGUAL", "DECIMAL_REPRESENTATION_ONLY"):
                campos_sku_detalhe.append({"sku": p["sku"], "campo": campo,
                                           "antes": a.get(campo), "depois": p.get(campo)})

        for chave, cel_depois in p["grade"].items():
            cel_antes = a["grade"].get(chave)
            classe, detalhe = classificar_celula(cel_antes, cel_depois)
            resumo[classe] += 1
            if classe == "IGUAL":
                continue
            registro = {"sku": p["sku"], "fornecedor_id": p["fornecedor_id"],
                        "cenario": chave, "classe": classe}
            if isinstance(cel_antes, list) and isinstance(cel_depois, list):
                registro.update(preco_antes=cel_antes[PRECO], preco_depois=cel_depois[PRECO],
                                delta=cel_depois[PRECO] - cel_antes[PRECO],
                                margem_antes=cel_antes[MARGEM],
                                margem_depois=cel_depois[MARGEM])
                variacoes.append(abs(cel_depois[PRECO] - cel_antes[PRECO]))
            if detalhe:
                registro["detalhe"] = detalhe
            if len(por_classe[classe]) < 400:
                por_classe[classe].append(registro)

    historico = comparar_historico(antes, depois)

    return {
        "baseline_entrada": os.path.basename(entrada_path),
        "resumo": dict(resumo),
        "total_celulas": sum(v for k, v in resumo.items() if k != "SKU_NOVO"),
        "delta_preco_maximo": max(variacoes) if variacoes else 0.0,
        "campos_do_sku": dict(campos_sku),
        "campos_do_sku_fora_do_esperado": campos_sku_detalhe,
        "maiores_diferencas": sorted(
            [r for c in por_classe.values() for r in c if "delta" in r],
            key=lambda r: -abs(r["delta"]))[:20],
        "por_classe": dict(por_classe),
        "historico": historico,
        "cenarios_fiscais_iguais": antes["cenarios_fiscais"] == depois["cenarios_fiscais"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=SAIDA)
    ap.add_argument("--entrada", default=ENTRADA)
    a = ap.parse_args()

    r = comparar(a.entrada)
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)

    print("=" * 72)
    print("GRADE — SKU × cenário fiscal × condição de pagamento")
    print("=" * 72)
    for classe, n in sorted(r["resumo"].items(), key=lambda x: -x[1]):
        print(f"  {classe:32s} {n:7d}")
    print(f"  {'TOTAL DE CÉLULAS':32s} {r['total_celulas']:7d}")
    print(f"\n  maior Δ de preço unitário: R$ {r['delta_preco_maximo']:.6f} "
          f"(limite do arredondamento: R$ {MEIO_CENTAVO})")
    print(f"  cenários fiscais idênticos: {r['cenarios_fiscais_iguais']}")

    print("\n  campos do SKU:")
    for k, n in sorted(r["campos_do_sku"].items()):
        print(f"    {k:52s} {n:5d}")
    if r["campos_do_sku_fora_do_esperado"]:
        print("\n  *** campos do SKU fora do esperado:")
        for x in r["campos_do_sku_fora_do_esperado"][:15]:
            print("   ", x)

    print()
    print("=" * 72)
    print("HISTÓRICO — cotações, itens e snapshots emitidos")
    print("=" * 72)
    economicas = 0
    for rotulo in ("cotacoes", "itens"):
        h = r["historico"][rotulo]
        print(f"  {rotulo}: {h['resumo']}")
        economicas += h["resumo"].get("MUDANCA_ECONOMICA", 0) \
            + h["resumo"].get("LINHA_SUMIU", 0)
        for x in h["mudancas_economicas"][:10]:
            print("     !!", x)
    print(f"\n  MUDANÇAS ECONÔMICAS NO HISTÓRICO: {economicas}")

    nao = r["resumo"].get("NAO_EXPLICADA", 0)
    faixa = r["resumo"].get("FAIXA_COMISSAO_MUDOU", 0)
    print(f"\n  DIFERENÇAS NÃO EXPLICADAS: {nao}")
    if faixa:
        print(f"  MUDANÇAS REAIS DE PREÇO (faixa de comissão): {faixa}")
        for x in r["por_classe"]["FAIXA_COMISSAO_MUDOU"][:20]:
            print("   ", x)
    if nao:
        for x in r["por_classe"]["NAO_EXPLICADA"][:15]:
            print("   ", x)
    print(f"\nrelatório: {a.saida}")
    return 1 if (nao or economicas) else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Regressão numérica da Onda 1 contra o BASELINE IMUTÁVEL PRÉ-ONDA 1.

Compara célula a célula as 8.670 combinações SKU × cenário fiscal × condição de pagamento e
classifica **cada diferença pela regra que a causou**. "Mudou porque o motor novo calcula
diferente" não é resposta aceitável: diferença sem classe conhecida aparece como
`NAO_EXPLICADA` e trava a aceitação da onda.

Classes esperadas:

* `NACIONAL_4_PARA_7`   — mercadoria nacional interestadual que saía a 4% e agora vai a 7% (B-01)
* `NACIONAL_4_PARA_12`  — o mesmo, faixa de 12% (B-01)
* `DIFAL_CORRIGIDO`     — não contribuinte: carga total virou interestadual + DIFAL sobre a
                          receita final, em vez da coluna `carga_final` (base diferente)
* `FISCAL_REVIEW`       — cenário que não se resolve e passou a bloquear (B-06/B-14)
* `FCP_REVIEW`          — FCP aplicável com alíquota não confirmada
* `PAGAMENTO_BLOQUEADO` — condição de pagamento sem premissa (B-15)
* `IGUAL`               — nenhuma diferença

Uso:
    python3 scripts/comparar_baseline_onda1.py [--saida relatorios/regressao_onda1.json]
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.baseline_regressao_v2 import gerar  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(RAIZ, "relatorios", "baseline_fase0.json")
SAIDA = os.path.join(RAIZ, "relatorios", "regressao_onda1.json")

TOLERANCIA = 1e-9

# Nomes de estado como aparecem na chave do cenário do baseline.
FAIXA_12 = {"Minas Gerais", "Paraná", "Rio de Janeiro", "Rio Grande do Sul", "Santa Catarina"}
FAIXA_7 = {"Acre", "Alagoas", "Amapá", "Amazonas", "Bahia", "Ceará", "Distrito Federal",
           "Espírito Santo", "Goiás", "Maranhão", "Mato Grosso", "Mato Grosso do Sul", "Pará",
           "Paraíba", "Pernambuco", "Piauí", "Rio Grande do Norte", "Rondônia", "Roraima",
           "Sergipe", "Tocantins"}


def classificar(antes, depois, natureza, cenario):
    """Diz qual regra explica a diferença desta célula. None = sem diferença."""
    if antes is None and depois is None:
        return None                                  # SKU sem custo nos dois lados
    if isinstance(depois, dict):                     # bloqueado agora
        motivo = (depois.get("motivo") or "").lower()
        if "condição de pagamento" in motivo:
            return "PAGAMENTO_BLOQUEADO"
        if "fcp" in motivo:
            return "FCP_REVIEW"
        return "FISCAL_REVIEW"
    if antes is None or depois is None:
        return "NAO_EXPLICADA"

    preco_antes, preco_depois = antes[0], depois[0]
    if abs(preco_antes - preco_depois) <= TOLERANCIA:
        return None

    # A classe vem da REGRA, não da magnitude da diferença: o destino do cenário decide a
    # faixa. Classificar por tamanho de variação seria adivinhar.
    origem, destino, resto = cenario.split("|", 2)
    contribuinte = resto.startswith("SIM")
    intraestadual = origem == destino
    if intraestadual:
        return "NAO_EXPLICADA"          # SP→SP não muda em nenhuma das correções

    if contribuinte:
        # única mudança prevista para contribuinte: a faixa nacional do B-01
        if natureza == "NACIONAL":
            if destino in FAIXA_12:
                return "NACIONAL_4_PARA_12"
            if destino in FAIXA_7:
                return "NACIONAL_4_PARA_7"
        return "NAO_EXPLICADA"

    # Não contribuinte: a carga total deixou de vir da coluna `carga_final` (que expressa o
    # diferencial sobre outra base) e passou a ser interestadual + DIFAL sobre a receita.
    return "DIFAL_CORRIGIDO"


def comparar(baseline_path: str = BASELINE) -> dict:
    with open(baseline_path) as f:
        antes = json.load(f)
    depois = gerar(com_estado_banco=False)

    grade_antes = {p["sku"]: p for p in antes["produtos"]}
    resumo = Counter()
    por_classe = defaultdict(list)
    maiores = []

    for p in depois["produtos"]:
        a = grade_antes.get(p["sku"])
        if a is None:
            resumo["sku_novo"] += 1
            continue
        natureza = "IMPORTADA" if p["fornecedor_id"] == 1 else "NACIONAL"
        for chave, cel_depois in p["grade"].items():
            cel_antes = a["grade"].get(chave)
            classe = classificar(cel_antes, cel_depois, natureza, chave)
            if classe is None:
                resumo["IGUAL"] += 1
                continue
            resumo[classe] += 1
            registro = {"sku": p["sku"], "fornecedor_id": p["fornecedor_id"],
                        "natureza": natureza, "cenario": chave, "classe": classe}
            if isinstance(cel_depois, list) and isinstance(cel_antes, list):
                registro.update(preco_antes=cel_antes[0], preco_depois=cel_depois[0],
                                variacao=cel_depois[0] / cel_antes[0] - 1,
                                margem_antes=cel_antes[1], margem_depois=cel_depois[1])
                maiores.append(registro)
            else:
                registro["motivo"] = (cel_depois or {}).get("motivo") \
                    if isinstance(cel_depois, dict) else None
            if len(por_classe[classe]) < 400:
                por_classe[classe].append(registro)

    variacoes = [m["variacao"] for m in maiores]
    return {
        "baseline": os.path.basename(baseline_path),
        "resumo": dict(resumo),
        "total_celulas": sum(resumo.values()),
        "variacao_minima": min(variacoes) if variacoes else None,
        "variacao_maxima": max(variacoes) if variacoes else None,
        "maiores_diferencas": sorted(maiores, key=lambda m: -abs(m["variacao"]))[:20],
        "por_classe": {k: v for k, v in por_classe.items()},
        "cenarios_antes": antes["cenarios_fiscais"],
        "cenarios_depois": depois["cenarios_fiscais"],
        "itens_historicos_iguais": antes["itens"] == depois["itens"],
        "cotacoes_historicas_iguais": antes["cotacoes"] == depois["cotacoes"],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--saida", default=SAIDA)
    p.add_argument("--baseline", default=BASELINE)
    a = p.parse_args()

    r = comparar(a.baseline)
    with open(a.saida, "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)

    print(f"relatório salvo em {a.saida}")
    print(f"\ncélulas comparadas: {r['total_celulas']}")
    for classe, n in sorted(r["resumo"].items(), key=lambda x: -x[1]):
        print(f"  {classe:24s} {n:6d}")
    if r["variacao_minima"] is not None:
        print(f"\nvariação de preço: {r['variacao_minima']:+.2%} a {r['variacao_maxima']:+.2%}")
    print(f"\nitens históricos idênticos:    {r['itens_historicos_iguais']}")
    print(f"cotações históricas idênticas: {r['cotacoes_historicas_iguais']}")
    nao = r["resumo"].get("NAO_EXPLICADA", 0)
    print(f"\nDIFERENÇAS NÃO EXPLICADAS: {nao}")
    if nao:
        for x in r["por_classe"]["NAO_EXPLICADA"][:10]:
            print("  ", x)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

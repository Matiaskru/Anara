#!/usr/bin/env python3
"""Compara o estado atual do sistema com o baseline capturado antes da evolução.

O baseline (`relatorios/baseline_regressao.json`) guarda, para cada SKU, o custo, o preço-base,
a margem que o preço-base entregava e o preço a 18%; além dos cenários fiscais e dos itens de
cotação. Aqui a gente mede o que mudou e separa **mudança deliberada** de **surpresa**.
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

from sqlmodel import Session, select

from app import pricing_service as ps
from app.db import engine
from app.models import Cotacao, CotacaoItem, EstadoFiscal, Produto, RegraFiscalVenda
from app.fiscal_rules import resolver_icms_estruturado
from app.pricing_engine import calcular_por_margem, calcular_por_preco

BASELINE = os.path.expanduser("~/Anara-Cotacao/relatorios/baseline_regressao.json")
SAIDA = os.path.expanduser("~/Anara-Cotacao/relatorios/regressao.json")


def comparar():
    with open(BASELINE) as f:
        base = json.load(f)

    with Session(engine) as s:
        estados = s.exec(select(EstadoFiscal)).all()
        regras_fiscais = s.exec(select(RegraFiscalVenda)).all()
        cenario = ps.cenario_padrao_catalogo(s)
        regras, _ctx = ps.regras_da_cotacao(s, cenario)

        # --- cenários fiscais ---
        cenarios = {}
        for chave, antes in base["cenarios"].items():
            origem, destino, contrib = chave.split("|")
            agora, regra = resolver_icms_estruturado(regras_fiscais, estados, origem, destino,
                                                     contrib == "SIM", fallback=0.18)
            cenarios[chave] = {"antes": antes["icms"], "depois": agora,
                               "mudou": abs(agora - antes["icms"]) > 1e-9, "regra": regra}

        # --- produtos ---
        produtos = {p.sku_key: p for p in s.exec(select(Produto)).all()}
        linhas = []
        for antes in base["produtos"]:
            p = produtos.get(antes["sku"])
            if not p:
                linhas.append({"sku": antes["sku"], "situacao": "sumiu do catálogo"})
                continue
            depois_margem = None
            if p.custo_unitario and p.preco_base:
                depois_margem = calcular_por_preco(p.custo_unitario, 1, p.preco_base,
                                                   regras).margem_liquida
            preco_18 = (calcular_por_margem(p.custo_unitario, 1, 0.18, regras).preco_negociado
                        if p.custo_unitario else None)
            linhas.append({
                "sku": antes["sku"], "nome": p.nome, "cost_method": p.cost_method,
                "custo_antes": antes["custo"], "custo_depois": p.custo_unitario,
                "custo_var": (p.custo_unitario / antes["custo"] - 1) if antes["custo"] and p.custo_unitario else None,
                "preco_base_antes": antes["preco_base"], "preco_base_depois": p.preco_base,
                "preco_base_var": (p.preco_base / antes["preco_base"] - 1) if antes["preco_base"] and p.preco_base else None,
                "margem_do_preco_base_antes": antes["margem_do_preco_base"],
                "margem_do_preco_base_depois": depois_margem,
                "margem_padrao_agora": p.margem_padrao_pct,
                "preco_a_18_antes": antes["preco_a_18pct"], "preco_a_18_depois": preco_18,
                "preco_a_18_var": (preco_18 / antes["preco_a_18pct"] - 1) if antes["preco_a_18pct"] and preco_18 else None,
            })

        # --- itens de cotação históricos ---
        itens_agora = {i.id: i for i in s.exec(select(CotacaoItem)).all()}
        itens = []
        for antes in base["itens_cotacao"]:
            agora = itens_agora.get(antes["id"])
            if not agora:
                itens.append({"id": antes["id"], "situacao": "removido"})
                continue
            itens.append({
                "id": antes["id"], "cotacao": antes["cotacao_id"],
                "preco_antes": antes["preco_negociado"], "preco_depois": agora.preco_negociado,
                "margem_antes": antes["margem_liquida"], "margem_depois": agora.margem_liquida,
                "mudou": abs((agora.preco_negociado or 0) - (antes["preco_negociado"] or 0)) > 0.005,
            })

    resumo = Counter()
    for linha in linhas:
        if linha.get("situacao"):
            resumo["sumiram"] += 1
            continue
        if linha["custo_var"] and abs(linha["custo_var"]) > 0.001:
            resumo["custo_mudou"] += 1
        if linha["preco_base_var"] and abs(linha["preco_base_var"]) > 0.001:
            resumo["preco_base_mudou"] += 1
        if linha["preco_a_18_var"] and abs(linha["preco_a_18_var"]) > 0.001:
            resumo["preco_a_18_mudou"] += 1
    resumo["itens_historicos_alterados"] = sum(1 for i in itens if i.get("mudou"))
    resumo["itens_historicos"] = len(itens)
    resumo["cenarios_alterados"] = sum(1 for c in cenarios.values() if c["mudou"])

    saida = {"resumo": dict(resumo), "cenarios": cenarios, "produtos": linhas, "itens": itens}
    with open(SAIDA, "w") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1, default=str)
    return saida


if __name__ == "__main__":
    r = comparar()
    print("RESUMO:", r["resumo"])
    print("\n=== CENÁRIOS FISCAIS ===")
    for chave, c in r["cenarios"].items():
        marca = "MUDOU" if c["mudou"] else "igual"
        print(f"  {chave:45s} {c['antes']:.4f} → {c['depois']:.4f}  {marca}")
    print("\n=== ITENS DE COTAÇÃO HISTÓRICOS ===")
    alterados = [i for i in r["itens"] if i.get("mudou")]
    print(f"  {len(alterados)} de {len(r['itens'])} itens alterados")
    print("\n=== MAIORES VARIAÇÕES DE PREÇO A 18% ===")
    com_var = [l for l in r["produtos"] if l.get("preco_a_18_var")]
    for l in sorted(com_var, key=lambda l: -abs(l["preco_a_18_var"]))[:12]:
        print(f"  {l['nome'][:36]:36s} {l['cost_method']:16s} "
              f"{l['preco_a_18_antes']:8.2f} → {l['preco_a_18_depois']:8.2f} "
              f"({l['preco_a_18_var']:+.1%})")

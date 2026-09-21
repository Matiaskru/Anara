#!/usr/bin/env python3
"""Baseline comercial e econômico de TODOS os SKUs KTC ativos precificáveis — antes e depois
da retirada do I.I. econômico (22/09/2026) — e a comparação entre os dois (paridade).

    ANARA_DB_URL=sqlite:////caminho/copia.db python3 scripts/ii_zero_2026_09_22/baseline_ktc.py gerar SAIDA.json
    python3 scripts/ii_zero_2026_09_22/baseline_ktc.py comparar ANTES.json DEPOIS.json [--relatorio X.md]

Por SKU × 9 cenários (SP→SP não contribuinte 30 dias · 30/60 · 30/60/90 · SP→MG contribuinte
revenda · SP→MG não contribuinte · SP→RJ não contribuinte · sinal 30 % · 50 % · 100 % sobre
30/60/90): custo econômico (CNET), base comercial de precificação (= CNET antes do patch; a
referência comercial depois), alíquota de I.I. efetivamente usada antes, margem-alvo, B2B
comercial, B2B econômico, tabela, preco_base, comissão no B2B e com 20 % de desconto, margem
realizada e lucro unitário no B2B (sempre com o CNET econômico), status do custo.

Paridade (o oracle principal): B2B comercial, tabela, preco_base e comissão em cenário idêntico
NÃO podem mudar nem R$ 0,01. Oracle econômico: onde havia I.I. > 0, CNET cai e margem/lucro no
mesmo preço sobem; onde era 0, nada muda. Nunca se atualiza o baseline para o teste passar.
"""
import json
import os
import sys
from collections import Counter
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

CENARIOS = [
    ("sp_sp_nc_30", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30", sinal=0.0)),
    ("sp_sp_nc_30_60", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30/60", sinal=0.0)),
    ("sp_sp_nc_30_60_90", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30/60/90", sinal=0.0)),
    ("sp_mg_contrib_revenda", dict(destino="Minas Gerais", contribuinte=True, finalidade="REVENDA", condicao="30", sinal=0.0)),
    ("sp_mg_nc", dict(destino="Minas Gerais", contribuinte=False, finalidade="USO_CONSUMO", condicao="30", sinal=0.0)),
    ("sp_rj_nc", dict(destino="Rio de Janeiro", contribuinte=False, finalidade="USO_CONSUMO", condicao="30", sinal=0.0)),
    ("sinal_30", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30/60/90", sinal=0.30)),
    ("sinal_50", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30/60/90", sinal=0.50)),
    ("sinal_100", dict(destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30/60/90", sinal=1.0)),
]
DESCONTO_TESTE = Decimal("0.20")


def f(v):
    return None if v is None else float(v)


def gerar(saida: str) -> int:
    from sqlmodel import Session, select
    from app import pricing_service as ps
    from app import politica_comercial as pol
    from app.db import caminho_do_banco, engine
    from app.dinheiro import D
    from app.models import Cotacao, Fornecedor, Produto
    from app.pricing_engine import calcular_por_preco, preco_b2b, preco_de_tabela, preco_por_desconto
    real = os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
    somente_leitura = os.path.realpath(caminho_do_banco()) == real
    linhas, resumo = [], Counter()
    with Session(engine) as s:
        ktc = s.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
        produtos = s.exec(select(Produto).where(Produto.fornecedor_id == ktc.id).where(Produto.ativo == True)).all()  # noqa: E712
        for p in produtos:
            custo, mem = ps.custo_para_precificar(s, p)
            status = ps.status_canonico_do_custo(custo, mem)
            m = ps.margem_padrao(s, p)
            ncm = mem.get("ncm") or {}
            nac = mem.get("nacionalizacao") or {}
            ref = mem.get("referencia_comercial") or {}
            base_comercial = mem.get("base_comercial_brl")          # None antes do patch
            base_sku = {
                "produto_id": p.id, "sku": p.sku_key, "familia": p.familia, "ncm": p.ncm or ncm.get("ncm"),
                "net_fonte": mem.get("net_fonte"),
                "thread_count": p.thread_count, "exw_usd": mem.get("exw_usd"), "peso_kg": nac.get("peso_kg") or p.peso_kg,
                "frete_usd": nac.get("frete_usd"), "ii_usd": nac.get("ii_usd"),
                # antes do patch: a alíquota que formou o custo; depois: a proteção comercial
                "ii_legado_pct": (ncm.get("ii") if ncm.get("ii") is not None else p.ii_aplicado),
                "ii_economico_pct": mem.get("ii_pct"), "protecao_comercial_pct": ref.get("protecao_pct"),
                "cnet": f(custo), "base_comercial": f(base_comercial), "margem_alvo": f(m.margem_pct) if m.tem_regra else None,
                "regra_margem": m.regra, "preco_base": p.preco_base, "status": status,
                "premissas_faltantes": mem.get("premissas_faltantes"), "cenarios": {},
            }
            if not custo or D(custo) <= 0 or not m.tem_regra:
                resumo["sem_preco"] += 1
                linhas.append(base_sku)
                continue
            for rot, c in CENARIOS:
                cot = Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino=c["destino"], contribuinte_icms=c["contribuinte"],
                              finalidade=c["finalidade"], condicao_pagamento=c["condicao"], freight_type="FOB", percentual_sinal=c["sinal"])
                regras, ctx = ps.regras_da_cotacao(s, cot, p)
                if regras is None:
                    base_sku["cenarios"][rot] = {"bloqueado": ctx.get("motivo_bloqueio")}
                    continue
                base = D(base_comercial) if base_comercial else D(custo)
                b2b_c = preco_b2b(base, m.margem_pct, regras)
                b2b_e = preco_b2b(D(custo), m.margem_pct, regras)
                tabela = preco_de_tabela(b2b_c.preco_negociado, ctx.get("fator_tabela") or 2)
                real_no_b2b = calcular_por_preco(D(custo), 1, b2b_c.preco_negociado, regras)
                com_b2b = pol.comissao_do_item(tabela, b2b_c.preco_negociado, b2b_c.preco_negociado, 1, regras.comissao_base_icms_pct)
                preco_20 = preco_por_desconto(tabela, DESCONTO_TESTE)
                com_20 = pol.comissao_do_item(tabela, b2b_c.preco_negociado, preco_20, 1, regras.comissao_base_icms_pct)
                base_sku["cenarios"][rot] = {
                    "icms_pct": f(regras.icms_pct), "encargo_pct": f(regras.encargo_financeiro_pct),
                    "b2b_comercial": f(b2b_c.preco_negociado), "b2b_economico": f(b2b_e.preco_negociado), "tabela": f(tabela),
                    "comissao_b2b_pct": f(com_b2b.taxa_pct), "comissao_b2b_valor": f(com_b2b.comissao_valor),
                    "preco_desc20": f(preco_20), "comissao_desc20_pct": f(com_20.taxa_pct), "comissao_desc20_valor": f(com_20.comissao_valor),
                    "margem_realizada_b2b": f(real_no_b2b.margem_liquida), "lucro_unitario_b2b": f(real_no_b2b.lucro),
                }
            resumo["precificados"] += 1
            linhas.append(base_sku)
    with open(saida, "w", encoding="utf-8") as fh:
        json.dump({"banco": caminho_do_banco(), "somente_leitura": somente_leitura, "cenarios": [c[0] for c in CENARIOS],
                   "resumo": dict(resumo), "skus": linhas}, fh, ensure_ascii=False, indent=1)
    print(f"baseline: {saida} · SKUs KTC ativos {len(linhas)} · {dict(resumo)}")
    return 0


def comparar(antes_path: str, depois_path: str, relatorio: str = None) -> int:
    antes = {x["produto_id"]: x for x in json.load(open(antes_path))["skus"]}
    depois = {x["produto_id"]: x for x in json.load(open(depois_path))["skus"]}
    viol, eco = [], Counter()
    comparados = cenarios = 0
    exemplos = []
    for pid, a in antes.items():
        d = depois.get(pid)
        if d is None:
            viol.append(("SKU_SUMIU", pid, a["sku"], "", ""))
            continue
        if not a["cenarios"]:
            continue                          # não era precificável antes: fora da paridade
        comparados += 1
        if a["preco_base"] is not None and d["preco_base"] != a["preco_base"]:
            viol.append(("PRECO_BASE_MUDOU", pid, a["sku"], a["preco_base"], d["preco_base"]))
        if a["margem_alvo"] != d["margem_alvo"]:
            viol.append(("MARGEM_ALVO_MUDOU", pid, a["sku"], a["margem_alvo"], d["margem_alvo"]))
        # a alíquota só "formava o custo" quando houve nacionalização (custo derivado); custo lido
        # do catálogo (sem EXW) não se decompõe e fica igual — o I.I. nunca esteve nele
        ii_antes = Decimal(str(a["ii_legado_pct"] or 0)) if (a.get("ii_usd") or 0) > 0 else Decimal(0)
        if d["cnet"] is None:
            viol.append(("CNET_SUMIU", pid, a["sku"], a["cnet"], None))
            continue
        # econômico: CNET cai exatamente o I.I. antigo (× câmbio) onde havia I.I.; igual onde era 0
        if ii_antes > 0:
            if not (d["cnet"] < a["cnet"]):
                viol.append(("CNET_NAO_CAIU", pid, a["sku"], a["cnet"], d["cnet"]))
            eco["cnet_caiu"] += 1
        else:
            if abs(d["cnet"] - a["cnet"]) > 1e-9:
                viol.append(("CNET_MUDOU_SEM_II", pid, a["sku"], a["cnet"], d["cnet"]))
            eco["cnet_igual"] += 1
        if d.get("ii_economico_pct") not in (0, 0.0):
            viol.append(("II_ECONOMICO_NAO_ZERO", pid, a["sku"], "", d.get("ii_economico_pct")))
        for rot, ca in a["cenarios"].items():
            cd = d["cenarios"].get(rot)
            if "bloqueado" in ca:
                continue
            if cd is None or "bloqueado" in cd:
                viol.append(("CENARIO_BLOQUEOU", pid, a["sku"], rot, cd))
                continue
            cenarios += 1
            for campo in ("b2b_comercial", "tabela", "comissao_b2b_pct", "comissao_b2b_valor", "preco_desc20",
                          "comissao_desc20_pct", "comissao_desc20_valor", "icms_pct", "encargo_pct"):
                if ca[campo] != cd[campo]:
                    viol.append((f"{campo.upper()}_MUDOU", pid, a["sku"], f"{rot}: {ca[campo]}", cd[campo]))
            if ii_antes > 0:
                if not (cd["margem_realizada_b2b"] > ca["margem_realizada_b2b"] and cd["lucro_unitario_b2b"] > ca["lucro_unitario_b2b"]):
                    viol.append(("MARGEM_OU_LUCRO_NAO_SUBIU", pid, a["sku"], f"{rot}: {ca['margem_realizada_b2b']}/{ca['lucro_unitario_b2b']}", f"{cd['margem_realizada_b2b']}/{cd['lucro_unitario_b2b']}"))
                if not (cd["b2b_economico"] <= cd["b2b_comercial"]):
                    viol.append(("B2B_ECONOMICO_ACIMA_DO_COMERCIAL", pid, a["sku"], rot, f"{cd['b2b_economico']} > {cd['b2b_comercial']}"))
                if rot == "sp_sp_nc_30" and len(exemplos) < 8:
                    exemplos.append({"sku": a["sku"][:60], "ii_antes": float(ii_antes), "cnet_antes": a["cnet"], "cnet_depois": d["cnet"],
                                     "b2b_comercial": cd["b2b_comercial"], "b2b_economico": cd["b2b_economico"], "tabela": cd["tabela"],
                                     "margem_antes": ca["margem_realizada_b2b"], "margem_depois": cd["margem_realizada_b2b"],
                                     "lucro_antes": ca["lucro_unitario_b2b"], "lucro_depois": cd["lucro_unitario_b2b"]})
            else:
                for campo in ("margem_realizada_b2b", "lucro_unitario_b2b", "b2b_economico"):
                    if abs((cd[campo] or 0) - (ca[campo] or 0)) > 1e-9:
                        viol.append((f"{campo.upper()}_MUDOU_SEM_II", pid, a["sku"], f"{rot}: {ca[campo]}", cd[campo]))
    novos = [d for pid, d in depois.items() if pid in antes and not antes[pid]["cenarios"] and d["cenarios"]]
    fontes = Counter((x.get("net_fonte"), str(x.get("ii_legado_pct"))) for x in antes.values() if x["cenarios"])
    saida = {"skus_antes": len(antes), "skus_comparados": comparados, "cenarios_comparados": cenarios, "violacoes": len(viol),
             "por_tipo": dict(Counter(v[0] for v in viol)), "economia": dict(eco),
             "fonte_do_custo_x_ii_legado": {f"{k[0]}|{k[1]}": v for k, v in fontes.items()},
             "skus_que_passaram_a_precificar": [(d["produto_id"], d["sku"][:60], d["status"]) for d in novos],
             "amostra_violacoes": [list(map(str, v)) for v in viol[:40]], "exemplos": exemplos}
    print(json.dumps(saida, ensure_ascii=False, indent=1))
    if relatorio:
        with open(relatorio, "w", encoding="utf-8") as fh:
            fh.write("# Paridade comercial × economia real — retirada do I.I. KTC (22/09/2026)\n\n")
            fh.write(f"Baseline ANTES: `{os.path.basename(antes_path)}` · DEPOIS: `{os.path.basename(depois_path)}` "
                     "(cópias em `relatorios/baseline_ktc_ii_zero_ANTES|DEPOIS_2026_09_22.json`; o ANTES foi gerado com o código anterior ao patch)\n\n")
            fh.write(f"* SKUs KTC ativos: {len(antes)} · comparados (precificáveis antes): **{comparados}** · cenários comparados: **{cenarios}**\n")
            fh.write(f"* Violações de paridade/economia: **{len(viol)}** {saida['por_tipo'] or ''}\n")
            fh.write(f"* CNET caiu (I.I. legado > 0 efetivamente no custo): {eco['cnet_caiu']} · CNET igual (I.I. 0 ou custo lido do catálogo, que não se decompõe): {eco['cnet_igual']}\n")
            fh.write(f"* fonte do custo × alíquota legada: {saida['fonte_do_custo_x_ii_legado']}\n")
            fh.write(f"* SKUs que passaram a precificar depois do patch: {saida['skus_que_passaram_a_precificar']}\n\n")
            fh.write("## Exemplos (SP→SP não contribuinte, 30 dias)\n\n| SKU | I.I. antes | CNET antes → depois | B2B comercial | B2B econômico | tabela | margem no B2B antes → depois | lucro unit. antes → depois |\n|---|---|---|---|---|---|---|---|\n")
            for e in exemplos:
                fh.write(f"| {e['sku']} | {e['ii_antes']:.2%} | {e['cnet_antes']:.2f} → {e['cnet_depois']:.2f} | {e['b2b_comercial']:.2f} | {e['b2b_economico']:.2f} | {e['tabela']:.2f} | {e['margem_antes']:.4%} → {e['margem_depois']:.4%} | {e['lucro_antes']:.2f} → {e['lucro_depois']:.2f} |\n")
            if viol:
                fh.write("\n## Violações\n\n")
                for v in viol[:100]:
                    fh.write(f"* {v}\n")
    return 1 if viol else 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "gerar":
        sys.exit(gerar(sys.argv[2]))
    if len(sys.argv) >= 4 and sys.argv[1] == "comparar":
        rel = sys.argv[sys.argv.index("--relatorio") + 1] if "--relatorio" in sys.argv else None
        sys.exit(comparar(sys.argv[2], sys.argv[3], rel))
    print(__doc__)
    sys.exit(2)

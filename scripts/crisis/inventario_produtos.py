#!/usr/bin/env python3
"""§4, §5 e §29 — Inventário econômico completo dos produtos, comparação e ranking de anomalias.

Somente leitura, numa CÓPIA do banco (`scripts.crisis.ambiente.preparar`). Nada é corrigido.
Toda linha do CSV responde "de onde veio o número": referência vigente, memória de custo,
regra de margem resolvida e cenário fiscal usado no preço benchmark A (SP→SP, não
contribuinte, USO_CONSUMO, condição "30", FOB).

Artefatos (em AUDIT):
    produtos_completos.csv     — uma linha por produto da tabela `produto`
    comparacao_produtos.csv    — grupos comparáveis ordenados por dimensão e TC/GSM
    sheets_tc.csv              — Flat/Top/Fitted Sheet por dimensão × 250/300/400 TC
    anomalies.csv              — violações (P0/P1/P2) + rankings (INFO)
    inventario_achados.json    — achados consolidados
    INVENTARIO_RESUMO.md       — resumo legível
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, preparar  # noqa: E402

session = preparar("inventario")

from sqlmodel import select  # noqa: E402

from app import custo_service as cs  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.models import Cotacao, Fornecedor, MargemRegra, Produto  # noqa: E402
from app.pricing_engine import calcular_por_margem  # noqa: E402

STATUS_COTAVEIS = {"CONFIRMADO", "ESTIMADO", "REVALIDAR"}
TOALHAS = {"Bath Towel", "Hand Towel", "Face Towel", "Pool Towel", "Beach Towel",
           "Bath Mat", "Wash Cloth", "Towel"}
SHEETS = ("Flat Sheet", "Top Sheet", "Fitted Sheet")
TC_SHEETS = (250, 300, 400)

# Cenário benchmark A — NUNCA gravado (não entra em session.add).
COTACAO_A = Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino="SP",
                    contribuinte_icms=False, finalidade="USO_CONSUMO",
                    condicao_pagamento="30", freight_type="FOB")


# ---------------------------------------------------------------------------
# utilitários (exibição e comparação — nunca round() em dinheiro)
# ---------------------------------------------------------------------------
def fmt(v, casas=None):
    """Texto para CSV. Decimal/float saem inteiros (sem quantizar); `casas` só para exibição."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (Decimal, float, int)):
        d = D(v)
        if casas == 2:
            return str(dinheiro(d))
        return format(d.normalize(), "f")
    return str(v)


def money(v):
    d = D(v)
    return "—" if d is None else f"R$ {dinheiro(d)}"


def pct(v):
    d = D(v)
    return "—" if d is None else f"{(d * 100).quantize(Decimal('0.01'))}%"


def area_m2(p):
    if p.largura_cm and p.comprimento_cm:
        return D(p.largura_cm) * D(p.comprimento_cm) / Decimal(10000)
    return None


def dimensao(p):
    if p.largura_cm and p.comprimento_cm:
        return f"{D(p.largura_cm).normalize():f}x{D(p.comprimento_cm).normalize():f}"
    return ""


def composicao(p):
    if p.cotton_pct is None and p.poliester_pct is None:
        return ""
    c = D(p.cotton_pct) if p.cotton_pct is not None else None
    po = D(p.poliester_pct) if p.poliester_pct is not None else None
    # o cadastro mistura fração (0.7) e percentual (100.0); normaliza para exibir
    def norm(x):
        if x is None:
            return None
        return x * 100 if x <= 1 else x
    c, po = norm(c), norm(po)
    return f"CO{c.normalize():f}" + (f"/PES{po.normalize():f}" if po is not None else "")


def material_nacional(p):
    """Daune/Decor: o 'material' vive no sku_key (`DAUNE · Edredom · 100% plumas · 180 g · 190x260`)."""
    partes = [s.strip() for s in (p.sku_key or "").split("·")]
    out = []
    for s in partes[2:]:
        if re.fullmatch(r"[\d.,]+\s*x\s*[\d.,]+", s.lower()):
            continue
        if re.fullmatch(r"\d+\s*g", s.lower()):
            continue
        out.append(s.lower())
    return " ".join(out)


def variante(p):
    """O que diferencia dois SKUs da mesma família/composição além de dimensão e TC/GSM."""
    return "|".join(str(x or "").strip().lower() for x in
                    (p.categoria, p.construcao, p.acabamento, p.plain_or_stripe))


def texto_fonte(ref):
    origem = (ref.origem_registro or "") if ref else ""
    return origem.rsplit(" · ", 1)[-1] if " · " in origem else origem


# ---------------------------------------------------------------------------
# 1. inventário
# ---------------------------------------------------------------------------
fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
regras_margem = {r.id: r for r in session.exec(select(MargemRegra)).all()}
produtos = session.exec(select(Produto).order_by(Produto.id)).all()
assert len(produtos) == 352, f"esperava 352 produtos, encontrei {len(produtos)}"

COLUNAS = [
    "id", "sku_key", "fornecedor", "familia", "categoria", "nome", "dimensao", "largura_cm",
    "comprimento_cm", "area_m2", "thread_count", "gsm", "composicao", "cotton_pct",
    "poliester_pct", "weave", "construcao", "acabamento", "plain_or_stripe", "origem_fiscal",
    "origem_fiscal_fonte", "ncm",
    "status_custo_canonico", "produto_status_custo", "produto_custo_confianca",
    "produto_precisa_revisao", "produto_revisao_motivo", "cost_method", "cost_method_memoria",
    "ref_id", "ref_versao", "ref_fonte", "ref_documento", "ref_data", "ref_status_custo",
    "ref_confirmation_pending", "ref_valor_bruto", "ref_cnet_brl", "ref_metodo",
    "exw_cotado_usd", "exw_cotado_data", "exw_cotado_fonte", "exw_calculado_usd",
    "exw_usado_usd", "exw_origem", "exw_diferenca_abs_usd", "exw_diferenca_pct",
    "material_ref", "material_usd_m2", "frescor_status", "frescor_dias",
    "valor_bruto_fornecedor", "peso_kg", "peso_tipo", "peso_fonte", "peso_estimado_no_calculo",
    "ii_aplicado_produto", "ii_memoria", "ii_confiavel", "frete_int_usd", "ii_usd",
    "custo_net_usd_produto", "net_usd_memoria", "custo_unitario_produto", "CNET",
    "cnet_diverge_do_cache_pct", "net_fonte", "caminho",
    "margem_anterior_pct", "margem_pct", "piso_pct", "comissao_formacao_pct", "preco_travado",
    "politica", "regra_id", "regra_nome", "margem_origem",
    "preco_base", "preco_benchmark_A", "margem_liquida_A", "markup_A", "icms_pct_A",
    "pis_cofins_pct_A", "encargo_pct_A", "status_fiscal_A", "delta_preco_base_vs_A_pct",
    "preco_A_por_m2", "cnet_por_m2", "preco_A_sobre_cnet",
    "gate_seller", "ativo", "observacoes",
]

linhas = []       # dicts por produto (valores nativos, Decimal onde é dinheiro)
por_id = {}


def inventariar(p: Produto) -> dict:
    forn = fornecedores.get(p.fornecedor_id)
    obs = []

    origem, origem_fonte = ps.origem_fiscal_do_produto(session, p)
    custo, memoria = ps.custo_para_precificar(session, p)
    status = ps.status_canonico_do_custo(custo, memoria)
    ref = cs.referencia_vigente(session, p.id)
    margem = ps.margem_padrao(session, p)
    regra = regras_margem.get(margem.regra_id) if margem.regra_id else None
    regras, ctx = ps.regras_da_cotacao(session, COTACAO_A, p)
    material_usd_m2 = material_ref = None
    if forn and forn.codigo == "KTC" and memoria.get("cost_method") == "KTC_CALCULATED":
        params, _faltando = ps.parametros_ktc_do_produto(session, p)
        material_usd_m2, material_ref = D(params.material_price_usd_m2), params.material_ref

    cnet = D(custo)
    # frescor da evidência que formou o EXW usado (cotado → data da cotação; calculado → n/a)
    exw_origem_txt = memoria.get("exw_origem") or ""
    frescor = None
    if exw_origem_txt.startswith("EXW cotado") and p.exw_cotado_data:
        frescor = ps.frescor(session, p.exw_cotado_data)
    elif forn and forn.codigo != "KTC" and ref is not None and ref.data_ref:
        frescor = ps.frescor(session, ref.data_ref)
    preco_a = margem_a = markup_a = None
    if cnet is not None and cnet > 0 and regras is not None:
        r = calcular_por_margem(cnet, 1, margem.margem_pct, regras)
        preco_a, margem_a, markup_a = r.preco_negociado, r.margem_liquida, r.markup_implicito
    gate = status in STATUS_COTAVEIS and cnet is not None and cnet > 0 and regras is not None

    if ctx.get("motivo_bloqueio"):
        obs.append(f"fiscal/pagamento bloqueado: {ctx['motivo_bloqueio']}")
    if memoria.get("motivo"):
        obs.append(f"memória: {memoria['motivo']}")
    for a in memoria.get("avisos", []) or []:
        obs.append(f"aviso: {a}")
    if memoria.get("industrial", {}).get("faltando"):
        obs.append("industrial faltando: " + "; ".join(memoria["industrial"]["faltando"]))
    if p.precisa_revisao:
        obs.append(f"precisa_revisao (legado): {p.revisao_motivo or 'sem motivo'}")
    if margem.origem == "fallback":
        obs.append("margem por fallback 15% — nenhuma regra bateu")
    if ref is None:
        obs.append("sem CustoReferencia versionada vigente")
    if not p.ativo:
        obs.append("produto INATIVO")

    nac = memoria.get("nacionalizacao") or {}
    ncm_mem = memoria.get("ncm") or {}
    peso_mem = memoria.get("peso") or {}
    exw_usado = memoria.get("exw_usd")
    exw_cot, exw_calc = D(p.exw_cotado_usd), D(p.exw_calculado_usd)
    dif_abs = dif_pct = None
    if exw_cot is not None and exw_calc is not None and exw_cot > 0:
        dif_abs = abs(exw_cot - exw_calc)
        dif_pct = (exw_calc - exw_cot) / exw_cot

    valor_bruto = D(ref.valor_bruto) if ref is not None else None
    area = area_m2(p)
    cache = D(p.custo_unitario)
    diverge = None
    if cnet is not None and cache is not None and cache > 0:
        diverge = (cnet - cache) / cache

    pb = D(p.preco_base)
    delta_pb = None
    if pb is not None and preco_a is not None and preco_a > 0:
        delta_pb = (pb - preco_a) / preco_a

    linha = {
        "id": p.id, "sku_key": p.sku_key, "fornecedor": forn.codigo if forn else "",
        "familia": p.familia, "categoria": p.categoria, "nome": p.nome, "dimensao": dimensao(p),
        "largura_cm": p.largura_cm, "comprimento_cm": p.comprimento_cm, "area_m2": area,
        "thread_count": p.thread_count, "gsm": p.gsm, "composicao": composicao(p),
        "cotton_pct": p.cotton_pct, "poliester_pct": p.poliester_pct, "weave": p.weave,
        "construcao": p.construcao, "acabamento": p.acabamento,
        "plain_or_stripe": p.plain_or_stripe,
        "origem_fiscal": origem, "origem_fiscal_fonte": origem_fonte, "ncm": p.ncm,
        "status_custo_canonico": status, "produto_status_custo": p.status_custo,
        "produto_custo_confianca": p.custo_confianca,
        "produto_precisa_revisao": p.precisa_revisao, "produto_revisao_motivo": p.revisao_motivo,
        "cost_method": p.cost_method, "cost_method_memoria": memoria.get("cost_method"),
        "ref_id": ref.id if ref else None, "ref_versao": ref.versao if ref else None,
        "ref_fonte": texto_fonte(ref), "ref_documento": ref.documento if ref else None,
        "ref_data": ref.data_ref.isoformat() if ref and ref.data_ref else None,
        "ref_status_custo": ref.status_custo if ref else None,
        "ref_confirmation_pending": ref.confirmation_pending if ref else None,
        "ref_valor_bruto": valor_bruto, "ref_cnet_brl": D(ref.cnet_brl) if ref else None,
        "ref_metodo": ref.metodo_custo if ref else None,
        "exw_cotado_usd": exw_cot,
        "exw_cotado_data": p.exw_cotado_data.isoformat() if p.exw_cotado_data else None,
        "exw_cotado_fonte": p.exw_cotado_fonte, "exw_calculado_usd": exw_calc,
        "exw_usado_usd": D(exw_usado), "exw_origem": memoria.get("exw_origem"),
        "exw_diferenca_abs_usd": dif_abs, "exw_diferenca_pct": dif_pct,
        "material_ref": material_ref, "material_usd_m2": material_usd_m2,
        "frescor_status": frescor["status"] if frescor else None,
        "frescor_dias": frescor["dias"] if frescor else None,
        "valor_bruto_fornecedor": valor_bruto if (forn and forn.codigo != "KTC") else None,
        "peso_kg": p.peso_kg, "peso_tipo": p.peso_tipo, "peso_fonte": p.peso_fonte,
        "peso_estimado_no_calculo": (f"{peso_mem.get('peso_kg')} ({peso_mem.get('tipo')}; "
                                     f"{peso_mem.get('fonte')})" if peso_mem else None),
        "ii_aplicado_produto": p.ii_aplicado, "ii_memoria": ncm_mem.get("ii"),
        "ii_confiavel": ncm_mem.get("confiavel"),
        "frete_int_usd": D(nac.get("frete_usd")), "ii_usd": D(nac.get("ii_usd")),
        "custo_net_usd_produto": p.custo_net_usd, "net_usd_memoria": D(memoria.get("net_usd")),
        "custo_unitario_produto": cache, "CNET": cnet, "cnet_diverge_do_cache_pct": diverge,
        "net_fonte": memoria.get("net_fonte"), "caminho": memoria.get("caminho"),
        "margem_anterior_pct": (D(regra.margem_anterior_pct) if regra is not None
                                and regra.margem_anterior_pct is not None
                                else margem.margem_anterior_pct),
        "margem_pct": margem.margem_pct, "piso_pct": margem.piso_pct,
        "comissao_formacao_pct": margem.comissao_formacao_pct,
        "preco_travado": margem.preco_travado, "politica": margem.politica,
        "regra_id": margem.regra_id, "regra_nome": margem.regra, "margem_origem": margem.origem,
        "preco_base": pb, "preco_benchmark_A": preco_a, "margem_liquida_A": margem_a,
        "markup_A": markup_a, "icms_pct_A": D(ctx.get("icms_pct")),
        "pis_cofins_pct_A": D(ctx.get("pis_cofins_pct")), "encargo_pct_A": D(ctx.get("encargo_pct")),
        "status_fiscal_A": ctx.get("status_fiscal"), "delta_preco_base_vs_A_pct": delta_pb,
        "preco_A_por_m2": (preco_a / area) if (preco_a is not None and area) else None,
        "cnet_por_m2": (cnet / area) if (cnet is not None and cnet > 0 and area) else None,
        "preco_A_sobre_cnet": (preco_a / cnet) if (preco_a is not None and cnet and cnet > 0) else None,
        "gate_seller": gate, "ativo": p.ativo, "observacoes": " || ".join(obs),
        # auxiliares (não vão para o CSV principal)
        "_p": p, "_variante": variante(p), "_material": material_nacional(p),
        "_peso_calc": D(peso_mem.get("peso_kg")) if peso_mem else D(p.peso_kg),
        "_ctx": ctx, "_memoria": memoria,
    }
    return linha


print(f"Inventariando {len(produtos)} produtos…", flush=True)
for i, p in enumerate(produtos, 1):
    linha = inventariar(p)
    linhas.append(linha)
    por_id[p.id] = linha
    if i % 50 == 0:
        print(f"  {i}/{len(produtos)}", flush=True)

os.makedirs(AUDIT, exist_ok=True)
caminho_csv = os.path.join(AUDIT, "produtos_completos.csv")
with open(caminho_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore")
    w.writeheader()
    for l in linhas:
        w.writerow({k: fmt(l.get(k)) for k in COLUNAS})
print("→", caminho_csv)

# contagens
cont_forn = Counter(l["fornecedor"] for l in linhas)
cont_fam = Counter(l["familia"] for l in linhas)
cont_status = Counter(l["status_custo_canonico"] for l in linhas)
cont_status_forn = Counter((l["fornecedor"], l["status_custo_canonico"]) for l in linhas)
cont_gate = Counter(l["gate_seller"] for l in linhas)
cont_ativo = Counter(l["ativo"] for l in linhas)
cont_fiscal = Counter(l["status_fiscal_A"] for l in linhas)
cont_net_fonte = Counter(l["net_fonte"] for l in linhas)
cont_cost_method = Counter(l["cost_method"] for l in linhas)
cont_legado = Counter((l["produto_custo_confianca"], l["status_custo_canonico"]) for l in linhas)
print("\nPor fornecedor:", dict(cont_forn))
print("Por família:", dict(cont_fam))
print("Por status_custo canônico:", dict(cont_status))
print("Por fornecedor × status:", dict(cont_status_forn))
print("gate_seller:", dict(cont_gate), "| ativo:", dict(cont_ativo))
print("status fiscal A:", dict(cont_fiscal))
print("net_fonte:", dict(cont_net_fonte))

# ---------------------------------------------------------------------------
# 2. comparação e anomalias
# ---------------------------------------------------------------------------
anomalias = []


def anomalia(tipo, a, b, campo, va, vb, detalhe, gravidade):
    anomalias.append({
        "tipo": tipo, "produto_a": a["sku_key"] if a else "", "produto_b": b["sku_key"] if b else "",
        "id_a": a["id"] if a else "", "id_b": b["id"] if b else "",
        "campo": campo, "valor_a": fmt(va), "valor_b": fmt(vb), "detalhe": detalhe,
        "gravidade": gravidade,
    })


def chave_tc_gsm(l):
    return l["thread_count"] if l["thread_count"] is not None else l["gsm"]


# grupos comparáveis (ordenados por dimensão e por TC/GSM) → comparacao_produtos.csv
grupos = defaultdict(list)
for l in linhas:
    if l["fornecedor"] == "KTC":
        chave = (l["fornecedor"], l["familia"], l["composicao"], l["weave"] or "", l["_variante"])
    else:
        chave = (l["fornecedor"], l["familia"], l["_material"], "", l["_variante"])
    grupos[chave].append(l)

COL_CMP = ["grupo", "fornecedor", "familia", "composicao", "weave", "variante", "id", "sku_key",
           "dimensao", "area_m2", "thread_count", "gsm", "status_custo_canonico", "exw_usado_usd",
           "exw_cotado_usd", "exw_calculado_usd", "valor_bruto_fornecedor", "peso_kg",
           "_peso_calc", "frete_int_usd", "CNET", "cnet_por_m2", "preco_benchmark_A",
           "preco_A_por_m2", "preco_base", "gate_seller"]
with open(os.path.join(AUDIT, "comparacao_produtos.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow([c.strip("_") if c != "_peso_calc" else "peso_usado_no_calculo" for c in COL_CMP])
    for gi, (chave, itens) in enumerate(sorted(grupos.items(), key=lambda kv: [str(x) for x in kv[0]]), 1):
        itens.sort(key=lambda l: (D(l["area_m2"] or 0), D(chave_tc_gsm(l) or 0)))
        for l in itens:
            w.writerow([gi, chave[0], chave[1], chave[2], chave[3], chave[4]] +
                       [fmt(l.get(c)) for c in COL_CMP[6:]])
print("→", os.path.join(AUDIT, "comparacao_produtos.csv"))


def maior(a, b):
    return a is not None and b is not None and a > b


def grav_inversao(a, b, delta):
    """P1 quando as duas pontas são custo vivo e a inversão passa de 5%; senão P2.

    Comparação que envolve custo de catálogo legado (REVIEW_REQUIRED, sem EXW) diz mais
    sobre o legado do que sobre o motor — vale registrar, mas não como bug do motor."""
    legado = "REVIEW_REQUIRED" in (a["status_custo_canonico"], b["status_custo_canonico"])
    if legado or abs(delta) < Decimal("0.05"):
        return "P2"
    return "P1"


def st(l):
    return f"{l['status_custo_canonico']}/{l['net_fonte']}"


def menor(a, b):
    return a is not None and b is not None and a < b


# --- regra 1: mesma família/composição/TC — área maior com CNET/EXW/preço menor -------------
for chave, itens in grupos.items():
    por_tc = defaultdict(list)
    for l in itens:
        por_tc[chave_tc_gsm(l)].append(l)
    for tc, lst in por_tc.items():
        lst = [l for l in lst if l["area_m2"]]
        lst.sort(key=lambda l: l["area_m2"])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if b["area_m2"] <= a["area_m2"]:
                    continue
                ctx = (f"{chave[1]} {chave[2] or chave[4]} TC/GSM={tc}: {a['dimensao']} "
                       f"({a['area_m2']:.3f} m²) vs {b['dimensao']} ({b['area_m2']:.3f} m²)")
                for campo in ("CNET", "exw_usado_usd", "preco_benchmark_A"):
                    va, vb = a[campo], b[campo]
                    if menor(vb, va) and va > 0:
                        delta = vb / va - 1
                        anomalia("INVERSAO_DIMENSAO", a, b, campo, va, vb,
                                 f"{ctx}: área maior com {campo} menor ({delta * 100:.1f}%) | A: {st(a)}; "
                                 f"{a['exw_origem'] or a['ref_documento']} | B: {st(b)}; {b['exw_origem'] or b['ref_documento']}",
                                 grav_inversao(a, b, delta))

# --- regra 2: mesma família/dimensão/composição — TC maior com EXW/CNET menor ---------------
for chave, itens in grupos.items():
    if chave[0] != "KTC":
        continue
    por_dim = defaultdict(list)
    for l in itens:
        if l["thread_count"]:
            por_dim[l["dimensao"]].append(l)
    for dim, lst in por_dim.items():
        lst.sort(key=lambda l: l["thread_count"])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if b["thread_count"] <= a["thread_count"]:
                    continue
                ctx = f"{chave[1]} {chave[2]} {dim}: {a['thread_count']}TC vs {b['thread_count']}TC"
                for campo in ("exw_usado_usd", "CNET", "preco_benchmark_A"):
                    va, vb = a[campo], b[campo]
                    if menor(vb, va) and va > 0:
                        delta = vb / va - 1
                        anomalia("INVERSAO_TC", a, b, campo, va, vb,
                                 f"{ctx}: TC maior com {campo} menor ({delta * 100:.1f}%)"
                                 f" | A: {st(a)}; {a['exw_origem']} | B: {st(b)}; {b['exw_origem']}",
                                 grav_inversao(a, b, delta))
        # material mais caro gerando EXW menor (só onde o motor industrial registrou material)
        for i in range(len(lst)):
            for j in range(len(lst)):
                a, b = lst[i], lst[j]
                ma, mb = a["material_usd_m2"], b["material_usd_m2"]
                if ma is None or mb is None:
                    continue
                if D(mb) > D(ma) and menor(b["exw_usado_usd"], a["exw_usado_usd"]):
                    anomalia("TECIDO_MAIS_CARO_EXW_MENOR", a, b, "exw_usado_usd",
                             a["exw_usado_usd"], b["exw_usado_usd"],
                             f"{ctx} material {ma}→{mb} USD/m² mas EXW caiu", "P1")

# --- regra 3: toalhas — mesma dimensão, GSM maior com CNET menor; peso maior com frete menor --
for chave, itens in grupos.items():
    if chave[0] != "KTC" or chave[1] not in TOALHAS:
        continue
    por_dim = defaultdict(list)
    for l in itens:
        por_dim[l["dimensao"]].append(l)
    for dim, lst in por_dim.items():
        for i in range(len(lst)):
            for j in range(len(lst)):
                a, b = lst[i], lst[j]
                if a is b:
                    continue
                if maior(b["gsm"], a["gsm"]) and menor(b["CNET"], a["CNET"]) and a["CNET"] > 0:
                    anomalia("TOALHA_GSM_MAIOR_CNET_MENOR", a, b, "CNET", a["CNET"], b["CNET"],
                             f"{chave[1]} {dim}: {a['gsm']}→{b['gsm']} gsm, CNET caiu "
                             f"({(b['CNET'] / a['CNET'] - 1) * 100:.1f}%) | A: {a['exw_origem']} | B: {b['exw_origem']}",
                             "P1")
# peso maior com frete internacional menor — qualquer KTC, mesma família (o frete é USD/kg × peso)
ktc_frete = [l for l in linhas if l["fornecedor"] == "KTC" and l["_peso_calc"] and l["frete_int_usd"] is not None]
por_fam = defaultdict(list)
for l in ktc_frete:
    por_fam[l["familia"]].append(l)
for fam, lst in por_fam.items():
    for i in range(len(lst)):
        for j in range(len(lst)):
            a, b = lst[i], lst[j]
            if a is b:
                continue
            if b["_peso_calc"] > a["_peso_calc"] and b["frete_int_usd"] < a["frete_int_usd"]:
                anomalia("PESO_MAIOR_FRETE_MENOR", a, b, "frete_int_usd", a["frete_int_usd"],
                         b["frete_int_usd"],
                         f"{fam}: peso {a['_peso_calc']} kg ({a['peso_tipo']}) → {b['_peso_calc']} kg "
                         f"({b['peso_tipo']}) com frete internacional menor — frete = USD/kg × peso, "
                         "isto só acontece se o peso usado no cálculo não for o gravado", "P0")

# --- regra 4: Daune — mesmo tamanho e material, gramatura maior com custo menor --------------
daune = [l for l in linhas if l["fornecedor"] == "DAUNE" and l["gsm"]]
por_dm = defaultdict(list)
for l in daune:
    por_dm[(l["familia"], l["_material"], l["dimensao"])].append(l)
for (fam, mat, dim), lst in por_dm.items():
    lst.sort(key=lambda l: l["gsm"])
    for i in range(len(lst)):
        for j in range(i + 1, len(lst)):
            a, b = lst[i], lst[j]
            if b["gsm"] <= a["gsm"]:
                continue
            for campo in ("valor_bruto_fornecedor", "CNET"):
                va, vb = a[campo], b[campo]
                if menor(vb, va):
                    anomalia("DAUNE_GRAMATURA_MAIOR_CUSTO_MENOR", a, b, campo, va, vb,
                             f"{fam} {mat} {dim}: {a['gsm']} g → {b['gsm']} g com {campo} menor "
                             f"| fonte A: {a['ref_documento']} | fonte B: {b['ref_documento']}", "P1")
                elif va is not None and vb is not None and va == vb:
                    anomalia("DAUNE_GRAMATURA_MAIOR_CUSTO_IGUAL", a, b, campo, va, vb,
                             f"{fam} {mat} {dim}: {a['gsm']} g e {b['gsm']} g com {campo} idêntico "
                             f"| fonte A: {a['ref_documento']} | fonte B: {b['ref_documento']} — dois SKUs, "
                             "duas fontes, o mesmo bruto ao centavo: provável mesmo produto com gramatura "
                             "rotulada diferente numa das planilhas", "P1")

# --- regra 5: preço × CNET, CNET ≤ 0 com preço, preco_base vs benchmark ---------------------
for l in linhas:
    cnet, pa, pb = l["CNET"], l["preco_benchmark_A"], l["preco_base"]
    if pa is not None and cnet is not None and cnet > 0 and pa < cnet:
        anomalia("PRECO_MENOR_QUE_CNET", l, None, "preco_benchmark_A", cnet, pa,
                 f"preço benchmark A abaixo do CNET (margem negativa); margem_liquida_A={l['margem_liquida_A']}", "P0")
    if (cnet is None or cnet <= 0) and pa is not None and pa > 0:
        anomalia("CNET_ZERO_COM_PRECO", l, None, "CNET", cnet, pa, "CNET ausente/zero mas preço formado", "P0")
    if (cnet is None or cnet <= 0) and pb is not None and pb > 0:
        anomalia("CNET_ZERO_COM_PRECO_BASE", l, None, "preco_base", cnet, pb,
                 f"CNET ausente/zero ({l['status_custo_canonico']}) mas preco_base do catálogo > 0 — "
                 "o catálogo exibe um preço sem custo vivo por trás", "P1")
    if pb is not None and pa is not None and pa > 0:
        delta = (pb - pa) / pa
        if abs(delta) > Decimal("0.15"):
            anomalia("PRECO_BASE_DIVERGE_BENCHMARK", l, None, "preco_base", pb, pa,
                     f"preco_base do catálogo diverge {delta * 100:.1f}% do benchmark A. Provável "
                     f"envelhecimento (câmbio 5,11→5,19, política 16/09 margem "
                     f"{pct(l['margem_anterior_pct'])}→{pct(l['margem_pct'])}, cenário fiscal do catálogo "
                     f"≠ SP/SP não contribuinte). Não é bug do motor até prova em contrário.", "P2")
    # coerência interna: status gravado no produto × canônico
    if l["produto_status_custo"] and l["produto_status_custo"] != l["status_custo_canonico"]:
        anomalia("STATUS_PRODUTO_DIVERGE_CANONICO", l, None, "status_custo",
                 l["produto_status_custo"], l["status_custo_canonico"],
                 f"produto.status_custo={l['produto_status_custo']} mas o resolvedor vivo devolve "
                 f"{l['status_custo_canonico']} (net_fonte={l['net_fonte']})", "P1")
    if l["ref_status_custo"] and l["ref_status_custo"] != l["status_custo_canonico"]:
        anomalia("REFERENCIA_DIVERGE_CANONICO", l, None, "status_custo",
                 l["ref_status_custo"], l["status_custo_canonico"],
                 f"CustoReferencia vigente v{l['ref_versao']} diz {l['ref_status_custo']} mas o custo "
                 f"vivo resolve {l['status_custo_canonico']} (net_fonte={l['net_fonte']})", "P1")
    if l["cnet_diverge_do_cache_pct"] is not None and abs(l["cnet_diverge_do_cache_pct"]) > Decimal("0.005"):
        anomalia("CNET_VIVO_DIVERGE_CACHE", l, None, "CNET", l["custo_unitario_produto"], l["CNET"],
                 f"custo_unitario gravado difere {l['cnet_diverge_do_cache_pct'] * 100:.2f}% do CNET vivo "
                 f"(câmbio/premissa mudou desde a gravação; esperado para KTC com EXW)", "P2")
    if l["ref_cnet_brl"] is not None and cnet is not None and D(l["ref_cnet_brl"]) > 0 and \
            abs((cnet - D(l["ref_cnet_brl"])) / D(l["ref_cnet_brl"])) > Decimal("0.005"):
        anomalia("CNET_VIVO_DIVERGE_REFERENCIA", l, None, "CNET", l["ref_cnet_brl"], cnet,
                 f"CustoReferencia vigente v{l['ref_versao']} tem cnet_brl={l['ref_cnet_brl']} mas o "
                 f"custo vivo é {cnet} (fonte {l['net_fonte']})", "P1")
    if l["fornecedor"] == "KTC" and l["ii_memoria"] is None and cnet and cnet > 0:
        anomalia("II_ZERO_SILENCIOSO", l, None, "ii", l["ii_aplicado_produto"], None,
                 "nacionalização sem I.I. confiável (tratado como zero pelo motor) — CLAUDE.md lista "
                 "'I.I. = 0' como bug conhecido", "P1")
    if l["fornecedor"] == "KTC" and cnet and cnet > 0 and not l["_peso_calc"]:
        anomalia("PESO_AUSENTE_FRETE_ZERO", l, None, "peso_kg", None, None,
                 "KTC sem peso: frete internacional considerado zero no CNET", "P1")
    if l["gate_seller"] and l["status_custo_canonico"] == "CONFIRMADO" and l["ref_id"] is None:
        anomalia("CONFIRMADO_SEM_REFERENCIA", l, None, "ref_id", None, l["status_custo_canonico"],
                 f"status vivo CONFIRMADO derivado de {l['net_fonte']} sem CustoReferencia versionada "
                 f"(o item pinaria custo_referencia_id=NULL); legado custo_confianca="
                 f"{l['produto_custo_confianca']}, exw_origem={l['exw_origem']}", "P2")
    # referência direta envelhecida sai CONFIRMADO — o resolvedor vivo nunca devolve REVALIDAR
    if l["status_custo_canonico"] == "CONFIRMADO" and l["frescor_status"] == "STALE":
        anomalia("STALE_PROMOVIDO_A_CONFIRMADO", l, None, "frescor", l["frescor_dias"], "CONFIRMADO",
                 f"EXW/referência direta com {l['frescor_dias']} dias (STALE > freshness_aging_dias) "
                 f"forma preço como CONFIRMADO; pela definição canônica é REVALIDAR (referência direta "
                 f"que envelheceu). status_canonico_do_custo só conhece CONFIRMADO/REVIEW_REQUIRED/A_COTAR. "
                 f"origem: {l['exw_origem'] or l['ref_documento']}; precisa_revisao={l['produto_precisa_revisao']}",
                 "P1")
    # motor industrial prevalece sobre cotação direta da KTC
    if (l["fornecedor"] == "KTC" and l["exw_cotado_usd"] and l["exw_calculado_usd"]
            and (l["exw_origem"] or "").startswith("EXW calculado")
            and l["exw_diferenca_pct"] is not None and abs(l["exw_diferenca_pct"]) > Decimal("0.05")):
        anomalia("EXW_CALCULADO_PREVALECE_SOBRE_COTADO", l, None, "exw_usado_usd",
                 l["exw_cotado_usd"], l["exw_calculado_usd"],
                 f"há EXW cotado pela KTC ({l['exw_cotado_usd']} USD em {l['exw_cotado_data']}, "
                 f"{l['exw_cotado_fonte']}) mas o CNET usa o calculado ({dinheiro(l['exw_calculado_usd'])} USD, "
                 f"{l['exw_diferenca_pct'] * 100:+.1f}% vs cotado) porque cost_method=KTC_CALCULATED. "
                 f"Composição {l['composicao']}"
                 + (f"; material={l['material_ref']} {l['material_usd_m2']} USD/m²" if l['material_ref'] else "") + ". "
                 "Se a cotação for específica (bordado/cliente), esperado; se for genérica, o motor está "
                 "cobrando acima do que a KTC cobra", "P1")
    if l["fornecedor"] == "DAUNE" and (l["ref_documento"] or "").lower().startswith("projeto"):
        anomalia("DAUNE_FONTE_DE_PROJETO", l, None, "ref_documento", l["ref_documento"], l["ref_status_custo"],
                 f"custo CONFIRMADO cuja fonte é planilha de projeto específico ({l['ref_documento']}, "
                 f"{l['ref_data']}), não a tabela Linha Hotelaria — preço negociado de um projeto virou "
                 "custo de catálogo", "P2")

# ---------------------------------------------------------------------------
# 2b. tabela de sheets: dimensão × 250/300/400 TC
# ---------------------------------------------------------------------------
sheets_rows = []
sheets = [l for l in linhas if l["fornecedor"] == "KTC" and l["familia"] in SHEETS
          and l["thread_count"] in TC_SHEETS]
por_sheet = defaultdict(dict)
for l in sheets:
    k = (l["familia"], l["dimensao"], l["_variante"])
    por_sheet[k].setdefault(l["thread_count"], []).append(l)
def _area_do_grupo(tcs):
    return next(iter(tcs.values()))[0]["area_m2"] or Decimal(0)


for (fam, dim, var), tcs in sorted(por_sheet.items(), key=lambda kv: (kv[0][0], _area_do_grupo(kv[1]), kv[0][2])):
    row = {"familia": fam, "dimensao": dim, "variante": var}
    for tc in TC_SHEETS:
        lst = sorted(tcs.get(tc, []), key=lambda l: l["composicao"], reverse=True)   # CO100 primeiro
        l = lst[0] if lst else None
        row[f"{tc}TC_sku"] = l["sku_key"] if l else ""
        row[f"{tc}TC_comp"] = l["composicao"] if l else ""
        row[f"{tc}TC_n"] = len(lst)
        row[f"{tc}TC_CNET"] = fmt(l["CNET"], 2) if l else ""
        row[f"{tc}TC_EXW"] = fmt(l["exw_usado_usd"]) if l else ""
        row[f"{tc}TC_precoA"] = fmt(l["preco_benchmark_A"], 2) if l else ""
        row[f"{tc}TC_status"] = l["status_custo_canonico"] if l else ""
    sheets_rows.append(row)
    # diferença 300→400 e 250→300 (ranking depois)
with open(os.path.join(AUDIT, "sheets_tc.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(sheets_rows[0].keys()) if sheets_rows else ["vazio"])
    w.writeheader()
    for r in sheets_rows:
        w.writerow(r)
print("→", os.path.join(AUDIT, "sheets_tc.csv"))

# ---------------------------------------------------------------------------
# 3. rankings (§29) — outliers com linha de investigação, gravidade INFO
# ---------------------------------------------------------------------------
def rank(tipo, itens, chave, detalhe_fn, n=100, reverso=False, campo=""):
    itens = [l for l in itens if chave(l) is not None]
    itens.sort(key=chave, reverse=reverso)
    for pos, l in enumerate(itens[:n], 1):
        anomalias.append({
            "tipo": tipo, "produto_a": l["sku_key"], "produto_b": "", "id_a": l["id"], "id_b": "",
            "campo": campo, "valor_a": fmt(chave(l)), "valor_b": "",
            "detalhe": f"#{pos}: " + detalhe_fn(l), "gravidade": "INFO",
        })


def investigacao(l):
    """Uma linha de leitura: de onde veio o número, para não confundir outlier com bug."""
    partes = [f"status={l['status_custo_canonico']}", f"fonte={l['net_fonte']}"]
    if l["fornecedor"] == "KTC":
        partes.append(f"exw={l['exw_origem']}")
        partes.append(f"peso={l['_peso_calc']}kg/{l['peso_tipo']}")
        partes.append(f"II={l['ii_memoria']}")
    else:
        partes.append(f"bruto={l['valor_bruto_fornecedor']} doc={l['ref_documento']}")
    partes.append(f"margem={pct(l['margem_pct'])} icms={pct(l['icms_pct_A'])}")
    return " · ".join(str(x) for x in partes)


com_preco = [l for l in linhas if l["preco_benchmark_A"] and l["CNET"] and l["CNET"] > 0]
rank("RANK_PRECO_SOBRE_CNET_MENOR", com_preco, lambda l: l["preco_A_sobre_cnet"],
     lambda l: f"preço/CNET={l['preco_A_sobre_cnet']:.4f} — investigar: {investigacao(l)}",
     campo="preco_A_sobre_cnet")
rank("RANK_PRECO_SOBRE_CNET_MAIOR", com_preco, lambda l: l["preco_A_sobre_cnet"],
     lambda l: f"preço/CNET={l['preco_A_sobre_cnet']:.4f} — investigar: {investigacao(l)}",
     reverso=True, campo="preco_A_sobre_cnet")
rank("RANK_MAIS_BARATO_POR_M2", [l for l in com_preco if l["preco_A_por_m2"]],
     lambda l: l["preco_A_por_m2"],
     lambda l: f"R$/m²={dinheiro(l['preco_A_por_m2'])} {l['familia']} {l['dimensao']} — {investigacao(l)}",
     campo="preco_A_por_m2")
rank("RANK_MAIS_CARO_POR_M2", [l for l in com_preco if l["preco_A_por_m2"]],
     lambda l: l["preco_A_por_m2"],
     lambda l: f"R$/m²={dinheiro(l['preco_A_por_m2'])} {l['familia']} {l['dimensao']} — {investigacao(l)}",
     reverso=True, campo="preco_A_por_m2")
rank("RANK_EXW_COTADO_VS_CALCULADO", [l for l in linhas if l["exw_diferenca_abs_usd"] is not None],
     lambda l: l["exw_diferenca_abs_usd"],
     lambda l: (f"|cotado {l['exw_cotado_usd']} − calculado {l['exw_calculado_usd']}| = "
                f"{l['exw_diferenca_abs_usd']:.4f} USD ({l['exw_diferenca_pct'] * 100:+.1f}% engine vs fornecedor); "
                f"o motor USA: {l['exw_origem']} — se o cotado é fonte direta específica (bordado, cliente), "
                f"a diferença é esperada; se ambos são genéricos, é premissa industrial a revisar"),
     reverso=True, campo="exw_diferenca_abs_usd")

# salto de CNET entre dimensões adjacentes (mesma família/TC/composição/variante)
saltos = []
inversoes = []
for chave, itens in grupos.items():
    por_tc = defaultdict(list)
    for l in itens:
        if l["area_m2"] and l["CNET"] and l["CNET"] > 0:
            por_tc[chave_tc_gsm(l)].append(l)
    for tc, lst in por_tc.items():
        lst.sort(key=lambda l: l["area_m2"])
        for a, b in zip(lst, lst[1:]):
            if b["area_m2"] == a["area_m2"]:
                continue
            salto = b["CNET"] / a["CNET"] - 1
            area_r = b["area_m2"] / a["area_m2"] - 1
            saltos.append((salto, salto - area_r, a, b, tc))
            if salto < 0:
                inversoes.append((salto, a, b, tc))
saltos.sort(key=lambda t: t[0], reverse=True)
for pos, (salto, excesso, a, b, tc) in enumerate(saltos[:100], 1):
    anomalias.append({"tipo": "RANK_SALTO_CNET_DIMENSAO", "produto_a": a["sku_key"], "produto_b": b["sku_key"],
                      "id_a": a["id"], "id_b": b["id"], "campo": "CNET", "valor_a": fmt(a["CNET"]),
                      "valor_b": fmt(b["CNET"]),
                      "detalhe": f"#{pos}: {a['familia']} TC/GSM={tc} {a['dimensao']}→{b['dimensao']}: CNET "
                                 f"{salto * 100:+.1f}% para área {(b['area_m2'] / a['area_m2'] - 1) * 100:+.1f}% "
                                 f"(excesso sobre área {excesso * 100:+.1f} p.p.) — A: {a['exw_origem']} | B: {b['exw_origem']}",
                      "gravidade": "INFO"})
inversoes.sort(key=lambda t: t[0])
for pos, (salto, a, b, tc) in enumerate(inversoes[:100], 1):
    anomalias.append({"tipo": "RANK_MAIOR_INVERSAO", "produto_a": a["sku_key"], "produto_b": b["sku_key"],
                      "id_a": a["id"], "id_b": b["id"], "campo": "CNET", "valor_a": fmt(a["CNET"]),
                      "valor_b": fmt(b["CNET"]),
                      "detalhe": f"#{pos}: {a['familia']} TC/GSM={tc} {a['dimensao']}→{b['dimensao']} CNET cai "
                                 f"{salto * 100:.1f}% — A: {a['exw_origem']} | B: {b['exw_origem']}",
                      "gravidade": "INFO"})

# diferença 300→400 TC (mesma família/dimensão/composição/variante)
difs = []
for (fam, dim, var), tcs in por_sheet.items():
    a = (tcs.get(300) or [None])[0]
    b = (tcs.get(400) or [None])[0]
    if a and b and a["CNET"] and b["CNET"] and a["CNET"] > 0:
        difs.append((b["CNET"] / a["CNET"] - 1, a, b))
difs.sort(key=lambda t: t[0])
for tipo, sel in (("RANK_MENOR_DIF_300_400TC", difs[:100]), ("RANK_MAIOR_DIF_300_400TC", list(reversed(difs))[:100])):
    for pos, (d, a, b) in enumerate(sel, 1):
        anomalias.append({"tipo": tipo, "produto_a": a["sku_key"], "produto_b": b["sku_key"],
                          "id_a": a["id"], "id_b": b["id"], "campo": "CNET", "valor_a": fmt(a["CNET"]),
                          "valor_b": fmt(b["CNET"]),
                          "detalhe": f"#{pos}: {a['familia']} {a['dimensao']} {a['composicao']}: CNET 300→400TC "
                                     f"{d * 100:+.1f}% (EXW {a['exw_usado_usd']}→{b['exw_usado_usd']}; preço A "
                                     f"{fmt(a['preco_benchmark_A'], 2)}→{fmt(b['preco_benchmark_A'], 2)}) — "
                                     f"300: {a['exw_origem']} | 400: {b['exw_origem']}",
                          "gravidade": "INFO"})

COL_ANOM = ["tipo", "gravidade", "produto_a", "produto_b", "id_a", "id_b", "campo", "valor_a", "valor_b", "detalhe"]
ordem_grav = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
anomalias.sort(key=lambda a: (ordem_grav[a["gravidade"]], a["tipo"]))
with open(os.path.join(AUDIT, "anomalies.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COL_ANOM)
    w.writeheader()
    for a in anomalias:
        w.writerow({k: a.get(k, "") for k in COL_ANOM})
print("→", os.path.join(AUDIT, "anomalies.csv"))

cont_tipo = Counter((a["gravidade"], a["tipo"]) for a in anomalias)
print("\nAnomalias por gravidade/tipo:")
for (g, t), n in sorted(cont_tipo.items()):
    print(f"  {g:4} {t:40} {n}")

# ---------------------------------------------------------------------------
# 4. resumo + achados
# ---------------------------------------------------------------------------
def leitura(a):
    t = a["tipo"]
    if t == "PESO_MAIOR_FRETE_MENOR":
        return "provável bug do motor (frete = USD/kg × peso; só inverte se o peso usado não for o gravado)"
    if t in ("PRECO_MENOR_QUE_CNET", "CNET_ZERO_COM_PRECO"):
        return "bug do motor ou premissa fiscal absurda — nunca aceitável"
    if t == "CNET_ZERO_COM_PRECO_BASE":
        return "dado de catálogo envelhecido: preco_base sobrevive sem custo vivo; a tela pode exibir preço de item A_COTAR/REVIEW_REQUIRED"
    if t == "II_ZERO_SILENCIOSO":
        return "fallback silencioso conhecido (I.I.=0) — o CNET está subestimado; premissa faltante, não outlier"
    if t == "PESO_AUSENTE_FRETE_ZERO":
        return "fallback silencioso (frete=0) — CNET subestimado"
    if t in ("INVERSAO_DIMENSAO", "INVERSAO_TC"):
        if "REVIEW_REQUIRED" in a["detalhe"]:
            return "envolve custo legado de catálogo (LEGACY_EXCEL, sem EXW): o legado está desalinhado do custo vivo — diz respeito ao dado herdado, não ao motor"
        if "Projeto" in a["detalhe"] or "Linha Hotelaria" in a["detalhe"]:
            return "dado de fonte (tabela do fornecedor nacional) — conferir a planilha"
        if "cotado pela KTC" in a["detalhe"] or "histórico" in a["detalhe"]:
            return "dado de fonte: EXW cotado/histórico de documentos diferentes; confirmar com a KTC"
        return "motor industrial: inversão entre produtos calculados pela mesma fórmula — revisar parâmetros (material/CMT/painéis)"
    if t == "TOALHA_GSM_MAIOR_CNET_MENOR":
        return "tabela USD/kg por GSM ou peso estimado — revisar premissa de toalha"
    if t.startswith("DAUNE_"):
        return "dado de fonte (tabela Daune) — conferir a planilha, não o motor"
    if t == "PRECO_BASE_DIVERGE_BENCHMARK":
        return "esperado: preco_base é foto antiga em outro cenário fiscal/câmbio/margem"
    if t in ("STATUS_PRODUTO_DIVERGE_CANONICO", "REFERENCIA_DIVERGE_CANONICO"):
        return "inconsistência de estado: o rótulo gravado não é o que o resolvedor vivo devolve — portões podem divergir da tela"
    if t == "CNET_VIVO_DIVERGE_REFERENCIA":
        return "referência versionada com cnet diferente do vivo — a genealogia pinada não bate com o número"
    if t == "CNET_VIVO_DIVERGE_CACHE":
        return "esperado (câmbio 5,11→5,19) — o cache custo_unitario não reage a premissa"
    if t == "STALE_PROMOVIDO_A_CONFIRMADO":
        return "bug de classificação do motor: status_canonico_do_custo não tem caminho para REVALIDAR; cotação envelhecida vira CONFIRMADO e passa em todos os portões (compromisso firme, WON)"
    if t == "EXW_CALCULADO_PREVALECE_SOBRE_COTADO":
        return "regra de precedência do motor (KTC_CALCULATED > cotado): premissa industrial acima da evidência direta; decidir qual é a fonte de verdade"
    if t == "DAUNE_FONTE_DE_PROJETO":
        return "dado de fonte: procedência é projeto específico, não tabela — pode estar negociado"
    if t == "CONFIRMADO_SEM_REFERENCIA":
        return "estado vivo CONFIRMADO sem versão de custo rastreável — viola 'toda referência responde de onde veio'"
    return "outlier — investigar"


PRIORIDADE_TIPO = ["STALE_PROMOVIDO_A_CONFIRMADO", "EXW_CALCULADO_PREVALECE_SOBRE_COTADO",
                   "INVERSAO_TC", "DAUNE_GRAMATURA_MAIOR_CUSTO_MENOR", "DAUNE_GRAMATURA_MAIOR_CUSTO_IGUAL",
                   "CNET_ZERO_COM_PRECO_BASE", "II_ZERO_SILENCIOSO", "INVERSAO_DIMENSAO"]


def _magnitude(a):
    try:
        va, vb = D(a["valor_a"]), D(a["valor_b"])
        if va and vb and va != 0:
            return abs(vb / va - 1)
    except Exception:
        pass
    return Decimal(0)


piores = [a for a in anomalias if a["gravidade"] in ("P0", "P1")]
piores.sort(key=lambda a: (ordem_grav[a["gravidade"]],
                           PRIORIDADE_TIPO.index(a["tipo"]) if a["tipo"] in PRIORIDADE_TIPO else 99,
                           -_magnitude(a)))
# uma linha por (tipo, par) e no máximo 3 por tipo nos 20 piores
vistos, pares, top20 = Counter(), set(), []
for a in piores:
    par = (a["tipo"], a["produto_a"], a["produto_b"])
    if par in pares or vistos[a["tipo"]] >= 3:
        continue
    pares.add(par)
    vistos[a["tipo"]] += 1
    top20.append(a)
    if len(top20) == 20:
        break

md = []
md.append("# Inventário econômico dos produtos — auditoria de crise 17/09/2026 (§4, §5, §29)\n")
md.append("Somente leitura sobre cópia do banco. Cenário benchmark A: SP→SP, não contribuinte, "
          "USO_CONSUMO, condição \"30\", FOB, margem = regra vigente hoje (política 16/09/2026).\n")
md.append("## Contagens\n")
md.append(f"- Produtos na tabela `produto`: **{len(linhas)}** (ativos {cont_ativo[True]}, inativos {cont_ativo[False]})")
md.append("- Por fornecedor: " + ", ".join(f"{k or '—'}={v}" for k, v in sorted(cont_forn.items())))
md.append("- Por status_custo canônico (resolvedor vivo): " + ", ".join(f"{k}={v}" for k, v in sorted(cont_status.items())))
md.append("- Por fornecedor × status: " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(cont_status_forn.items())))
md.append("- `gate_seller` (cotável: CONFIRMADO/ESTIMADO/REVALIDAR, CNET>0, regras fiscais resolvidas): "
          f"sim={cont_gate[True]}, não={cont_gate[False]}")
md.append("- Status fiscal no cenário A: " + ", ".join(f"{k}={v}" for k, v in sorted(cont_fiscal.items(), key=str)))
md.append("- Origem do CNET (`net_fonte`): " + ", ".join(f"{k}={v}" for k, v in sorted(cont_net_fonte.items(), key=str)))
md.append("- `cost_method` gravado: " + ", ".join(f"{k}={v}" for k, v in sorted(cont_cost_method.items(), key=str)))
md.append("- Rótulo legado `custo_confianca` × canônico vivo: " + ", ".join(f"{k[0]}→{k[1]}={v}" for k, v in sorted(cont_legado.items(), key=str)))
md.append("\n### Por família\n")
md.append("| família | n | CONFIRMADO | ESTIMADO | REVALIDAR | A_COTAR | REVIEW_REQUIRED | cotáveis |")
md.append("|---|---|---|---|---|---|---|---|")
for fam, n in sorted(cont_fam.items(), key=lambda kv: -kv[1]):
    st = Counter(l["status_custo_canonico"] for l in linhas if l["familia"] == fam)
    g = sum(1 for l in linhas if l["familia"] == fam and l["gate_seller"])
    md.append(f"| {fam} | {n} | {st['CONFIRMADO']} | {st['ESTIMADO']} | {st['REVALIDAR']} | {st['A_COTAR']} | {st['REVIEW_REQUIRED']} | {g} |")

md.append("\n## Anomalias por tipo\n")
md.append("| gravidade | tipo | n |\n|---|---|---|")
for (g, t), n in sorted(cont_tipo.items()):
    md.append(f"| {g} | {t} | {n} |")
md.append(f"\nTotal P0={sum(1 for a in anomalias if a['gravidade']=='P0')}, "
          f"P1={sum(1 for a in anomalias if a['gravidade']=='P1')}, "
          f"P2={sum(1 for a in anomalias if a['gravidade']=='P2')}, "
          f"INFO (rankings §29)={sum(1 for a in anomalias if a['gravidade']=='INFO')}. "
          "Rankings não declaram bug: cada linha traz a fonte do número para investigação.")

# --- leitura geral (números calculados, não opinião solta) ---
ambos = [l for l in linhas if l["exw_cotado_usd"] and l["exw_calculado_usd"]]
usa_calc = [l for l in ambos if (l["exw_origem"] or "").startswith("EXW calculado")]
por_data = defaultdict(list)
for l in ambos:
    por_data[l["exw_cotado_data"]].append(l["exw_diferenca_pct"])
difs_ord = sorted(l["exw_diferenca_pct"] for l in ambos)
razao_por_margem = defaultdict(list)
for l in linhas:
    if l["preco_A_sobre_cnet"]:
        razao_por_margem[(l["margem_pct"], l["comissao_formacao_pct"])].append(l["preco_A_sobre_cnet"])
stale = [l for l in linhas if l["status_custo_canonico"] == "CONFIRMADO" and l["frescor_status"] == "STALE"]
stale_por_data = Counter(l["exw_cotado_data"] or l["ref_data"] for l in stale)
rr_com_preco = [l for l in linhas if l["status_custo_canonico"] == "REVIEW_REQUIRED" and l["preco_benchmark_A"]]
fitted = [l for l in linhas if l["familia"] == "Fitted Sheet"]

md.append("\n## Leitura geral do auditor\n")
md.append(f"1. **O resolvedor vivo só conhece três estados.** `status_canonico_do_custo` devolve CONFIRMADO, "
          f"REVIEW_REQUIRED ou A_COTAR; ESTIMADO e REVALIDAR nunca aparecem (0 de 352). Consequência medida: "
          f"**{len(stale)} SKUs KTC formam preço sobre cotação direta STALE** (> freshness_aging_dias) e saem CONFIRMADO — "
          + ", ".join(f"{k}: {v}" for k, v in sorted(stale_por_data.items(), key=str)) +
          ". Pela definição do CLAUDE.md são REVALIDAR; hoje passam nos portões de compromisso firme e WON sem alerta. "
          "`precisa_revisao` (legado) marca 41 deles, mas o portão não lê esse campo.")
md.append(f"2. **Motor industrial acima da cotação direta.** {len(ambos)} SKUs têm EXW cotado E calculado; "
          f"{len(usa_calc)} usam o calculado (cost_method=KTC_CALCULATED tem precedência). Diferença calculado−cotado: "
          f"mín {difs_ord[0] * 100:+.1f}%, mediana {difs_ord[len(difs_ord) // 2] * 100:+.1f}%, máx {difs_ord[-1] * 100:+.1f}%. Por cotação: "
          + "; ".join(f"{k}: n={len(v)}, média {sum(v) / len(v) * 100:+.1f}%" for k, v in sorted(por_data.items(), key=str)) +
          ". Leitura: a tabela USD/kg de toalha reproduz a cotação de 23/08 ao centavo (0,0%), mas lençóis 100% algodão e CVC "
          "da cotação de 14/05 saem 13–16% acima, e toalhas 90/10 de 10/06 saem 8–20% acima (a tabela por kg é de 100% algodão). "
          "Não é bug aritmético: é escolha de fonte de verdade — e hoje a premissa vence a evidência.")
md.append("3. **preço/CNET é constante por regra de margem** (motor consistente): "
          + "; ".join(f"margem {pct(m)}/comissão {pct(c)}: {min(v):.4f}–{max(v):.4f} (n={len(v)})"
                      for (m, c), v in sorted(razao_por_margem.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0))) +
          ". Os rankings de preço/CNET, portanto, ordenam a regra de margem, não anomalia de produto. Sem margem negativa e sem CNET≤0 com preço (0 P0).")
md.append(f"4. **Preço formado sem custo vivo.** {len(rr_com_preco)} SKUs REVIEW_REQUIRED (catálogo legado, sem EXW) ainda recebem preço do "
          f"motor no benchmark A — `memoria_do_preco` cai para `produto.custo_unitario`; o bloqueio depende do portão de status, não do motor. "
          f"Além disso 9 SKUs Daune A_COTAR carregam `preco_base` > 0 no catálogo.")
md.append(f"5. **Fitted Sheet é a família mais exposta:** {sum(1 for l in fitted if l['gate_seller'])} de {len(fitted)} cotáveis; "
          f"{sum(1 for l in fitted if l['status_custo_canonico'] == 'REVIEW_REQUIRED')} REVIEW_REQUIRED (LEGACY_EXCEL sem EXW) e "
          f"{sum(1 for l in fitted if l['status_custo_canonico'] == 'A_COTAR')} A_COTAR. A família não tem fórmula industrial "
          "(só 'bottom sheet' sem elástico é calculável), e os custos legados 160x200/180x200 ficam acima da cotação real 200x290 de 10/08 — "
          "o legado parece inflado, não o motor.")
md.append("6. **Daune:** as 14 referências de `Projeto Anastacio.xlsx` (180 g e 250 g) conflitam com a Linha Hotelaria 12.08 (280 g): "
          "o bruto de 180 g Anastacio é idêntico ao de 280 g Linha Hotelaria em 3 tamanhos, e 250 g sai mais caro que 280 g. Dado de fonte "
          "(rótulo de gramatura ou preço negociado de projeto), não motor. Linha Hotelaria 12.08 tem 36 dias e Anastacio 43 (AGING); nenhuma referência Daune STALE.")
md.append(f"7. **Rastreabilidade versionada só existe para Daune.** 190 dos 242 cotáveis (174 KTC + 16 Decor) não têm `CustoReferencia` "
          "versionada: o item pinaria `custo_referencia_id = NULL`. A memória JSON responde 'de onde veio', mas a genealogia por versão não.")
md.append(f"8. **Esperado, não bug:** 170 SKUs KTC com `custo_unitario` defasado do CNET vivo (câmbio 5,11→5,19 — o próprio docstring de "
          f"`custo_para_precificar` documenta) e 43 `preco_base` divergindo > 15% do benchmark A (política 16/09 + câmbio + cenário fiscal).")

md.append("\n## 20 piores casos (P0/P1, uma linha por par, no máximo 3 por tipo) e leitura\n")
for i, a in enumerate(top20, 1):
    md.append(f"{i}. **[{a['gravidade']}] {a['tipo']}** — `{a['produto_a']}`"
              + (f" × `{a['produto_b']}`" if a["produto_b"] else "")
              + f" ({a['campo']}: {a['valor_a']} → {a['valor_b']})\n   - {a['detalhe']}\n   - Leitura: {leitura(a)}")

md.append("\n## Tabela de sheets (KTC): dimensão × 250 / 300 / 400 TC\n")
md.append("CNET em R$ (custo vivo), EXW em USD (o que o motor usou), preço A em R$ (benchmark). "
          "Célula vazia = não existe SKU nessa combinação.\n")
md.append("| família | dimensão | variante | 250TC CNET / EXW / preço A | 300TC CNET / EXW / preço A | 400TC CNET / EXW / preço A |")
md.append("|---|---|---|---|---|---|")
for r in sheets_rows:
    cel = []
    for tc in TC_SHEETS:
        if r[f"{tc}TC_sku"]:
            exw = r[f"{tc}TC_EXW"]
            exw = str(dinheiro(exw)) if exw else "—"
            cel.append(f"{r[f'{tc}TC_CNET']} / {exw} / {r[f'{tc}TC_precoA']} [{r[f'{tc}TC_status'][:4]}, {r[f'{tc}TC_comp']}]"
                       + (f" (+{r[f'{tc}TC_n'] - 1})" if r[f"{tc}TC_n"] > 1 else ""))
        else:
            cel.append("—")
    md.append(f"| {r['familia']} | {r['dimensao']} | {r['variante']} | " + " | ".join(cel) + " |")

md.append("\n## Artefatos\n")
for nome in ("produtos_completos.csv", "comparacao_produtos.csv", "sheets_tc.csv", "anomalies.csv", "inventario_achados.json"):
    md.append(f"- `{os.path.join(AUDIT, nome)}`")

with open(os.path.join(AUDIT, "INVENTARIO_RESUMO.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(md) + "\n")
print("→", os.path.join(AUDIT, "INVENTARIO_RESUMO.md"))

# achados consolidados por código
achados = []
por_tipo = defaultdict(list)
for a in anomalias:
    if a["gravidade"] == "INFO":
        continue
    por_tipo[(a["tipo"], a["gravidade"])].append(a)
for (tipo, grav), lst in sorted(por_tipo.items(), key=lambda kv: (ordem_grav[kv[0][1]], kv[0][0])):
    prods = sorted({x for a in lst for x in (a["produto_a"], a["produto_b"]) if x})
    achados.append({"codigo": f"INV-{tipo}", "gravidade": grav, "n": len(lst),
                    "detalhe": f"{len(lst)} ocorrência(s). Leitura: {leitura(lst[0])}. Exemplo: {lst[0]['detalhe']}",
                    "produtos": prods})
achados.append({"codigo": "INV-CONTAGENS", "gravidade": "INFO", "n": len(linhas),
                "detalhe": {"por_fornecedor": dict(cont_forn), "por_status": dict(cont_status),
                            "por_familia": dict(cont_fam), "gate_seller": {str(k): v for k, v in cont_gate.items()},
                            "status_fiscal_A": {str(k): v for k, v in cont_fiscal.items()},
                            "net_fonte": {str(k): v for k, v in cont_net_fonte.items()}},
                "produtos": []})
with open(os.path.join(AUDIT, "inventario_achados.json"), "w", encoding="utf-8") as f:
    json.dump(achados, f, ensure_ascii=False, indent=2, default=str)
print("→", os.path.join(AUDIT, "inventario_achados.json"))
print("\nConcluído.")

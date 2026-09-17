#!/usr/bin/env python3
"""§30 — CRISIS_PRODUCT_REVIEW.md a partir de produtos_completos.csv, anomalies.csv e sheets_tc.csv."""
import csv, os
from collections import defaultdict
A = os.path.expanduser("~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917")
prod = list(csv.DictReader(open(f"{A}/produtos_completos.csv", encoding="utf-8")))
anom = list(csv.DictReader(open(f"{A}/anomalies.csv", encoding="utf-8")))
sheets = list(csv.DictReader(open(f"{A}/sheets_tc.csv", encoding="utf-8")))
por_id = defaultdict(list)
for a in anom:
    if a["gravidade"] in ("P0", "P1", "P2"):
        for k in ("id_a", "id_b"):
            if a.get(k):
                por_id[a[k]].append(f"{a['tipo']} ({a['gravidade']})")
SECOES = [
    ("FLAT/TOP SHEETS", lambda p: p["familia"] in ("Flat Sheet", "Top Sheet", "Bottom Sheet")),
    ("FITTED SHEETS", lambda p: p["familia"] == "Fitted Sheet"),
    ("PILLOWCASES", lambda p: p["familia"] in ("Pillow Case", "Pillow Protector")),
    ("DUVET COVERS", lambda p: p["familia"] == "Duvet Cover"),
    ("DUVET INSERTS", lambda p: p["familia"] == "Duvet Insert"),
    ("TOWELS", lambda p: p["familia"] in ("Bath Towel", "Hand Towel", "Face Towel", "Wash Cloth", "Towel")),
    ("POOL TOWELS", lambda p: p["familia"] in ("Pool Towel", "Beach Towel")),
    ("BATH MATS", lambda p: p["familia"] == "Bath Mat"),
    ("BATHROBES", lambda p: p["familia"] == "Bathrobe"),
    ("PROTECTORS", lambda p: p["familia"] in ("Mattress Protector", "Mattress Topper")),
    ("DECOR", lambda p: p["fornecedor"] == "DECOR_TRICOT"),
    ("OUTROS", None),
]
usados = set()
def brl(v):
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError): return "—"
def usd(v):
    try: return f"US$ {float(v):.4f}"
    except (TypeError, ValueError): return "—"
def pct(v):
    try: return f"{float(v)*100:.1f}%"
    except (TypeError, ValueError): return "—"
out = ["# CRISIS PRODUCT REVIEW — 17/09/2026", "",
       "Fonte: `produtos_completos.csv` (352 produtos, 344 ativos), `anomalies.csv`, `sheets_tc.csv` — todos gerados pela auditoria de crise "
       "sobre uma cópia do banco, DEPOIS das correções de código. Preço benchmark A = SP→SP, não contribuinte, uso/consumo, 30 DD, "
       "na margem-alvo da política de 16/09/2026. Status canônico do custo é o que o runtime resolve hoje "
       "(CONFIRMADO / REVALIDAR / REVIEW_REQUIRED / A_COTAR).", ""]
from collections import Counter
out += ["## Resumo por status canônico", ""]
c = Counter((p["fornecedor"], p["status_custo_canonico"]) for p in prod if p["ativo"] in ("1", "True"))
out += ["| Fornecedor | CONFIRMADO | REVALIDAR | REVIEW_REQUIRED | A_COTAR |", "|---|---|---|---|---|"]
for f in ("KTC", "DAUNE", "DECOR_TRICOT"):
    out.append(f"| {f} | " + " | ".join(str(c.get((f, s), 0)) for s in ("CONFIRMADO", "REVALIDAR", "REVIEW_REQUIRED", "A_COTAR")) + " |")
out.append("")
for titulo, filtro in SECOES:
    itens = [p for p in prod if p["id"] not in usados and (filtro is None or filtro(p))]
    for p in itens: usados.add(p["id"])
    if not itens: continue
    out += [f"## {titulo}", "", f"{len(itens)} produto(s).", "",
            "| id | produto | especificação | CNET | fonte do custo | preço benchmark A | margem | status | anormalidade |",
            "|---|---|---|---|---|---|---|---|---|"]
    for p in sorted(itens, key=lambda p: (p["familia"], float(p["area_m2"] or 0), int(p["thread_count"] or 0), int(p["gsm"] or 0))):
        spec = " · ".join(x for x in (p["dimensao"], (p["thread_count"] + " fios") if p["thread_count"] else "", (p["gsm"] + " g/m²") if p["gsm"] else "", p["composicao"]) if x)
        fonte = p["exw_origem"] or p["ref_fonte"] or p["net_fonte"] or "—"
        if p["exw_cotado_data"]: fonte += f" ({p['exw_cotado_data']}, {p['frescor_status']})"
        ano = "; ".join(sorted(set(por_id.get(p["id"], []))))[:120]
        out.append(f"| {p['id']} | {p['nome'][:45]} | {spec[:50]} | {brl(p['CNET'])} | {fonte[:60]} | {brl(p['preco_benchmark_A'])} | {pct(p['margem_pct'])} | {p['status_custo_canonico']}{' (inativo)' if p["ativo"] not in ("1", "True") else ''} | {ano or '—'} |")
    out.append("")
out += ["## SHEETS — dimensão × 250TC / 300TC / 400TC (CNET · EXW · preço benchmark A)", "",
        "| família | dimensão | 250TC | 300TC | 400TC |", "|---|---|---|---|---|"]
for s in sheets:
    def cel(tc):
        if not s.get(f"{tc}_sku"): return "—"
        return f"CNET {brl(s[f'{tc}_CNET'])} · EXW {usd(s[f'{tc}_EXW']) if s[f'{tc}_EXW'] else '—'} · A {brl(s[f'{tc}_precoA'])} · {s[f'{tc}_status']}"
    out.append(f"| {s['familia']} | {s['dimensao']} | {cel('250TC')} | {cel('300TC')} | {cel('400TC')} |")
out.append("")
open(f"{A}/CRISIS_PRODUCT_REVIEW.md", "w", encoding="utf-8").write("\n".join(out))
print("linhas:", len(out), "produtos cobertos:", len(usados))

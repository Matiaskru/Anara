"""Backtest do motor industrial KTC contra cotações diretas (auditoria de crise, seção 7).

Fontes de EXW REAL (cotação direta):
  1. `produto.exw_cotado_usd` (+ data/fonte) dos produtos KTC;
  2. `custoreferencia` (tipo EXW_QUOTED, USD) ligada ao produto;
  3. `referencia/Anara - Precos Cotacao KTC 10-08-2026.xlsx` (34 linhas);
  4. `referencia/HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf` (22 linhas);
  5. `referencia/Anara Storage PI 23-08-2026.pdf` (34 linhas, com peso real).

Toda cotação de documento é convertida em CAMPOS ESTRUTURADOS (família, largura, comprimento,
TC, % algodão, liso/listrado, GSM, abas/flap) e o EXW MOTOR é obtido de duas formas:
  * pelo SKU do catálogo, quando há casamento INEQUÍVOCO por campos estruturados
    (`pricing_service.calcular_exw(session, produto)`), e
  * por um Produto simulado com os mesmos campos (`calculadora.produto_simulado`), o que
    permite testar o motor mesmo sem SKU. Nunca casa por nome.

Status: OK (|dif| ≤ 5%), CONSERVADOR (motor > +5%), REVISAR (motor entre −5% e −15%),
P0_SUBCUSTO (motor < −15%). Saída: `AUDIT/ktc_backtest_completo.csv` + distribuição no stdout.
"""
import csv
import os
import re
import statistics
import sys
from datetime import date
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.crisis.ambiente import preparar, AUDIT   # noqa: E402

session = preparar("ktc_backtest")

from sqlmodel import select   # noqa: E402
from app import calculadora   # noqa: E402
from app import pricing_service as ps   # noqa: E402
from app.dinheiro import D   # noqa: E402
from app.models import CustoReferencia, Fornecedor, MaterialPreco, Produto   # noqa: E402

REF = os.path.join(RAIZ, "referencia")
KTC = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()

# ---------------------------------------------------------------------------
# Parsing estruturado das cotações em documento
# ---------------------------------------------------------------------------
FAMILIA_DOC = [   # (regex no nome do item, família canônica do catálogo, família da calculadora)
    (r"top sheet", "Top Sheet", "Top Sheet"),
    (r"flat sheet|bed sheet", "Flat Sheet", "Flat Sheet"),
    (r"bottom sheet", "Fitted Sheet", "Bottom Sheet"),
    (r"pillow ?cases?|fronha", "Pillow Case", "Pillow Case"),
    (r"duvet cover", "Duvet Cover", "Duvet Cover"),
    (r"bath towel", "Bath Towel", "Bath Towel"),
    (r"hand towel|face towel", "Hand Towel", "Hand Towel"),
    (r"bath mat", "Bath Mat", "Bath Mat"),
    (r"pool", "Pool Towel", "Pool Towel"),
    (r"wash cloth", "Wash Cloth", "Wash Cloth"),
    (r"bathrobe", "Bathrobe", None),
    (r"slipper", "Slipper", None),
    (r"bed skirt", "Bed Skirt", None),
    (r"crib sheet", "Fitted Sheet", None),
    (r"mattress", "Mattress Protector", None),
]


def familia_de(nome: str):
    n = nome.lower()
    for rx, fam, calc in FAMILIA_DOC:
        if re.search(rx, n):
            return fam, calc
    return None, None


def dims_de(texto: str):
    m = re.search(r"(\d{2,3})\s*[xX]\s*(\d{2,3})", texto or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def estrutura(nome: str, spec: str, size: str) -> dict:
    """Campos estruturados de uma linha de cotação. O que não estiver escrito fica None."""
    s = (spec or "")
    fam, calc = familia_de(nome)
    W, L = dims_de(size or "")
    if W is None:
        W, L = dims_de(nome)
    tc = re.search(r"(\d{3})\s*TC", s, re.I)
    tc = int(tc.group(1)) if tc else None
    cotton = None
    m = re.search(r"(\d{2,3})\s*%\s*cotton", s, re.I)
    if m:
        cotton = int(m.group(1)) / 100
    elif re.search(r"polyester|polyster|microfiber", s, re.I) and not re.search(r"cotton", s, re.I):
        cotton = 0.0
    weave = "Sateen" if re.search(r"sateen|steen", s, re.I) else ("Percale" if re.search(r"percale", s, re.I) else None)
    stripe = "stripe" if re.search(r"stripe", f"{nome} {s}", re.I) else "plain"
    gsm = re.search(r"(\d{3})\s*g(?:sm)?\b", s, re.I)
    gsm = int(gsm.group(1)) if gsm else None
    abas = None
    flap = None
    m = re.search(r"oxford\s*\d*\s*cm?\s*from\s*(\d)\s*sides?", s, re.I)
    if m:
        abas = int(m.group(1))
    elif re.search(r"house ?wife|with flap", s, re.I):
        abas = 0
    m = re.search(r"flap\s*(\d+)\s*cm", s, re.I)
    if m:
        flap = float(m.group(1))
    elastico = bool(re.search(r"elastic|fitted", s, re.I))
    terry = bool(re.search(r"terry", s, re.I))
    return {"familia": fam, "familia_calc": calc, "largura": W, "comprimento": L, "tc": tc,
            "cotton_pct": cotton, "weave": weave, "plain_or_stripe": stripe, "gsm": gsm,
            "abas": abas, "flap_cm": flap, "elastico": elastico, "terry": terry, "spec": s}


def ler_xlsx_10_08():
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(REF, "Anara - Precos Cotacao KTC 10-08-2026.xlsx"), data_only=True)
    ws = wb["TABELA DE PREÇOS"]
    out = []
    for r in range(14, 48):
        nome, spec, size, preco, peso = (ws.cell(r, c).value for c in (2, 3, 4, 6, 7))
        if not nome or preco is None:
            continue
        e = estrutura(str(nome), str(spec or ""), str(size or ""))
        e.update({"fonte": "Cotação KTC ANARA10082026 (xlsx 10-08-2026)", "data": "2026-08-10",
                  "item": str(nome), "exw_direto": float(preco), "peso_real": float(peso) if peso else None})
        out.append(e)
    return out


def ler_pdf(caminho, fonte, data, col_peso=None):
    import pdfplumber
    out = []
    with pdfplumber.open(caminho) as pdf:
        for page in pdf.pages:
            for tabela in page.extract_tables():
                if not tabela or (tabela[0] and tabela[0][0] != "SN"):
                    continue
                for linha in tabela[1:]:
                    if not linha or not linha[0] or not str(linha[0]).strip().isdigit():
                        continue
                    nome, spec, cor, size, preco = linha[1], linha[2], linha[3], linha[4], linha[5]
                    preco = float(str(preco).replace("$", "").replace(",", "").strip())
                    spec = (spec or "").replace("\n", " ")
                    e = estrutura(nome, spec, size)
                    if cor and re.search(r"navy|taupe", cor, re.I):
                        e["plain_or_stripe"] = "stripe" if e["familia"] == "Pool Towel" else e["plain_or_stripe"]
                    peso = None
                    if col_peso is not None and len(linha) > col_peso and linha[col_peso]:
                        try:
                            peso = float(str(linha[col_peso]).strip())
                        except ValueError:
                            peso = None
                    e.update({"fonte": fonte, "data": data, "item": f"{nome} {size}",
                              "exw_direto": preco, "peso_real": peso})
                    out.append(e)
    return out


# ---------------------------------------------------------------------------
# Casamento com o catálogo — só por campos estruturados, e só se inequívoco
# ---------------------------------------------------------------------------
PRODUTOS_KTC = [p for p in session.exec(select(Produto)).all() if p.fornecedor_id == (KTC.id if KTC else None)]


def casar_sku(e: dict):
    cands = []
    for p in PRODUTOS_KTC:
        if (p.familia or "") != (e["familia"] or ""):
            continue
        if e["largura"] and (p.largura_cm != e["largura"] or p.comprimento_cm != e["comprimento"]):
            continue
        if e["tc"] is not None and p.thread_count != e["tc"]:
            continue
        if e["gsm"] is not None and p.gsm != e["gsm"]:
            continue
        if e["cotton_pct"] is not None and p.cotton_pct is not None and abs(p.cotton_pct - e["cotton_pct"]) > 0.001:
            continue
        if (p.plain_or_stripe or "plain") != e["plain_or_stripe"]:
            continue
        cands.append(p)
    if len(cands) == 1:
        return cands[0], "SKU único"
    if not cands:
        return None, "sem SKU equivalente"
    # desempate estrutural para fronha: número de abas
    if e["familia"] == "Pillow Case" and e["abas"] is not None:
        c2 = [p for p in cands if ps._abas_do_produto(p) == e["abas"]]
        if len(c2) == 1:
            return c2[0], "SKU único (abas)"
    return None, f"AMBÍGUO ({len(cands)} SKUs: {', '.join(str(p.id) for p in cands)})"


def material_para(e: dict):
    """MaterialPreco vigente por TC + composição + weave + liso/listrado. Ambíguo → None."""
    if e["tc"] is None:
        return None, "sem TC estruturado"
    cands = []
    for m in session.exec(select(MaterialPreco)).all():
        if not m.ativo or m.thread_count != e["tc"]:
            continue
        if (m.plain_or_stripe or "plain") != e["plain_or_stripe"]:
            continue
        if e["cotton_pct"] is not None:
            if m.cotton_pct is None or abs(m.cotton_pct - e["cotton_pct"]) > 0.001:
                continue
        if e["weave"] and m.weave and m.weave.lower() != e["weave"].lower():
            continue
        cands.append(m)
    if len(cands) == 1:
        return cands[0], "material único"
    if not cands:
        return None, f"sem material cadastrado para {e['tc']}TC {e['cotton_pct']} {e['plain_or_stripe']}"
    return None, "material ambíguo"


def exw_motor_simulado(e: dict, familia_calc: str):
    """EXW do motor para um produto simulado com os campos estruturados da cotação."""
    if familia_calc is None:
        return None, "família sem fórmula industrial (KTC_SPECIAL_QUOTED / A_COTAR_KTC)", None
    if e["elastico"]:
        return None, "com elástico: sem fórmula aprovada", None
    if familia_calc in ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth"):
        if e["gsm"] is None:
            return None, "toalha sem GSM", None
        prod = calculadora.produto_simulado(session, familia_calc, e["largura"], e["comprimento"],
                                            gsm=e["gsm"], plain_or_stripe=e["plain_or_stripe"])
    else:
        mat, motivo = material_para(e)
        if mat is None:
            return None, motivo, None
        kw = {}
        if familia_calc == "Pillow Case":
            kw = {"abas": e["abas"] or 0, "flap_cm": e["flap_cm"] or 20.0}
        prod = calculadora.produto_simulado(session, familia_calc, e["largura"], e["comprimento"],
                                            material_id=mat.id, plain_or_stripe=e["plain_or_stripe"], **kw)
    r = ps.calcular_exw(session, prod)
    if r.exw_usd is None:
        return None, "motor: " + "; ".join(r.avisos + r.faltando), prod
    return float(D(r.exw_usd)), r.status, prod


def classificar(direto, motor):
    if direto is None or motor is None or direto == 0:
        return None, None, None
    dif = motor - direto
    pct = dif / direto
    if abs(pct) <= 0.05:
        st = "OK"
    elif pct > 0.05:
        st = "CONSERVADOR"
    elif pct >= -0.15:
        st = "REVISAR"
    else:
        st = "P0_SUBCUSTO"
    return dif, pct, st


# ---------------------------------------------------------------------------
# Montagem das linhas
# ---------------------------------------------------------------------------
linhas = []


def construcao_txt(e_or_p):
    if isinstance(e_or_p, dict):
        e = e_or_p
        partes = [e.get("familia") or "", f"{e['tc']}TC" if e.get("tc") else "",
                  f"{e['cotton_pct']:.0%} algodão" if e.get("cotton_pct") is not None else "",
                  e.get("weave") or "", e.get("plain_or_stripe") or "",
                  f"{e['gsm']} GSM" if e.get("gsm") else "",
                  f"{e['abas']} abas" if e.get("abas") is not None else "",
                  f"flap {e['flap_cm']:g}" if e.get("flap_cm") else ""]
    else:
        p = e_or_p
        partes = [p.familia or "", f"{p.thread_count}TC" if p.thread_count else "",
                  f"{p.cotton_pct:.0%} algodão" if p.cotton_pct is not None else "",
                  p.weave or "", p.plain_or_stripe or "", f"{p.gsm} GSM" if p.gsm else "",
                  p.construcao or "", p.fechamento or "", p.acabamento or ""]
    return " · ".join(x for x in partes if x)


def linha_base(**kw):
    base = {"produto_id": "", "sku": "", "dimensao": "", "construcao": "", "fonte": "", "data": "",
            "item_documento": "", "exw_direto_usd": "", "exw_motor_usd": "", "caminho_motor": "",
            "status_motor": "", "diferenca_usd": "", "diferenca_pct": "", "motor_acima_abaixo": "",
            "status": "", "peso_real_kg": "", "casamento": "", "observacao": ""}
    base.update(kw)
    return base


def registrar(direto, motor, **kw):
    dif, pct, st = classificar(direto, motor)
    kw.update({"exw_direto_usd": f"{direto:.4f}" if direto is not None else "",
               "exw_motor_usd": f"{motor:.4f}" if motor is not None else "",
               "diferenca_usd": f"{dif:.4f}" if dif is not None else "",
               "diferenca_pct": f"{pct:.2%}" if pct is not None else "",
               "motor_acima_abaixo": ("ACIMA" if dif > 0 else ("ABAIXO" if dif < 0 else "IGUAL")) if dif is not None else "",
               "status": st or "NAO_COMPARAVEL"})
    linhas.append(linha_base(**kw))


# 1. produto.exw_cotado_usd
for p in sorted(PRODUTOS_KTC, key=lambda p: p.id):
    if not p.exw_cotado_usd:
        continue
    r = ps.calcular_exw(session, p)
    motor = float(D(r.exw_usd)) if r.exw_usd is not None else None
    obs = "" if motor is not None else "motor: " + "; ".join(r.avisos + r.faltando)
    registrar(p.exw_cotado_usd, motor, produto_id=p.id, sku=p.sku_key,
              dimensao=f"{p.largura_cm:g}x{p.comprimento_cm:g}" if p.largura_cm and p.comprimento_cm else "",
              construcao=construcao_txt(p), fonte=f"produto.exw_cotado_usd · {p.exw_cotado_fonte or '—'}",
              data=p.exw_cotado_data.isoformat() if p.exw_cotado_data else "",
              item_documento=p.nome, caminho_motor="SKU do catálogo", status_motor=r.status,
              peso_real_kg=f"{p.peso_kg:g}" if (p.peso_kg and p.peso_tipo == "REAL KTC") else "",
              casamento="o próprio SKU", observacao=obs + (f" | cost_method={p.cost_method}"))

# 2. custoreferencia (EXW_QUOTED USD)
for cr in session.exec(select(CustoReferencia)).all():
    if cr.fornecedor_id != (KTC.id if KTC else None) or cr.moeda != "USD" or cr.valor is None:
        continue
    p = session.get(Produto, cr.produto_id)
    if p is None:
        continue
    r = ps.calcular_exw(session, p)
    motor = float(D(r.exw_usd)) if r.exw_usd is not None else None
    obs = "" if motor is not None else "motor: " + "; ".join(r.avisos + r.faltando)
    if p.exw_cotado_usd and abs(p.exw_cotado_usd - cr.valor) > 1e-9:
        obs += f" | custoreferencia {cr.valor} ≠ produto.exw_cotado_usd {p.exw_cotado_usd} (aplicado={cr.aplicado}, vigente={cr.vigente})"
    registrar(cr.valor, motor, produto_id=p.id, sku=p.sku_key,
              dimensao=f"{p.largura_cm:g}x{p.comprimento_cm:g}" if p.largura_cm and p.comprimento_cm else "",
              construcao=construcao_txt(p), fonte=f"custoreferencia#{cr.id} {cr.tipo} · {cr.documento or '—'}",
              data=cr.data_ref.isoformat() if cr.data_ref else "", item_documento=p.nome,
              caminho_motor="SKU do catálogo", status_motor=r.status,
              peso_real_kg=f"{p.peso_kg:g}" if (p.peso_kg and p.peso_tipo == "REAL KTC") else "",
              casamento="produto_id da referência", observacao=obs.strip(" |"))

# 3–5. documentos
docs = []
docs += ler_xlsx_10_08()
docs += ler_pdf(os.path.join(REF, "HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf"),
                "HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf (HG25082026)", "2026-08-25")
docs += ler_pdf(os.path.join(REF, "Anara Storage PI 23-08-2026.pdf"),
                "Anara Storage PI 23-08-2026.pdf (ANARA23082026)", "2026-08-23", col_peso=8)

for e in docs:
    sku, como = casar_sku(e)
    motor_sku, st_sku = None, ""
    if sku is not None:
        r = ps.calcular_exw(session, sku)
        motor_sku = float(D(r.exw_usd)) if r.exw_usd is not None else None
        st_sku = r.status
    motor_sim, st_sim, prod_sim = exw_motor_simulado(e, e["familia_calc"])
    # motor preferencial: SKU casado (caminho real); senão, simulado
    if motor_sku is not None:
        motor, caminho, st = motor_sku, f"SKU do catálogo #{sku.id}", st_sku
    else:
        motor, caminho, st = motor_sim, "produto simulado (calculadora.produto_simulado)", st_sim
    obs = []
    if sku is not None and motor_sku is None:
        obs.append(f"SKU #{sku.id} não calculável ({st_sku})")
    if motor_sku is not None and motor_sim is not None and abs(motor_sku - motor_sim) > 1e-6:
        obs.append(f"motor SKU {motor_sku:.4f} ≠ motor simulado {motor_sim:.4f}")
    if motor is None:
        obs.append(st or "não calculável")
    if e["abas"] is not None and e["familia"] == "Pillow Case":
        obs.append(f"abas={e['abas']} flap={e['flap_cm']}")
    registrar(e["exw_direto"], motor, produto_id=sku.id if sku else "", sku=sku.sku_key if sku else "",
              dimensao=f"{e['largura']:g}x{e['comprimento']:g}" if e["largura"] else "",
              construcao=construcao_txt(e), fonte=e["fonte"], data=e["data"], item_documento=e["item"],
              caminho_motor=caminho if motor is not None else "", status_motor=st or "",
              peso_real_kg=f"{e['peso_real']:g}" if e.get("peso_real") else "",
              casamento=como, observacao=" | ".join(obs))
    # hipótese documentada: bottom sheet cotado, calculado como lençol plano (§15 "sem elástico")
    if e["familia_calc"] == "Bottom Sheet" and not e["elastico"]:
        m2, st2, _ = exw_motor_simulado(e, "Flat Sheet")
        registrar(e["exw_direto"], m2, produto_id=sku.id if sku else "", sku=sku.sku_key if sku else "",
                  dimensao=f"{e['largura']:g}x{e['comprimento']:g}", construcao=construcao_txt(e) + " · HIPÓTESE lençol plano",
                  fonte=e["fonte"], data=e["data"], item_documento=e["item"] + " [HIPÓTESE: motor de Flat Sheet]",
                  caminho_motor="produto simulado como Flat Sheet (hipótese §15 bottom sem elástico)",
                  status_motor=st2 or "", peso_real_kg=f"{e['peso_real']:g}" if e.get("peso_real") else "",
                  casamento=como, observacao="HIPÓTESE — não conta na distribuição principal; PI não diz se tem elástico")

# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------
os.makedirs(AUDIT, exist_ok=True)
destino = os.path.join(AUDIT, "ktc_backtest_completo.csv")
with open(destino, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
    w.writeheader()
    w.writerows(linhas)


def distribuicao(rows, titulo):
    pcts = [float(r["diferenca_pct"].rstrip("%")) / 100 for r in rows if r["diferenca_pct"]]
    print(f"\n== {titulo}: {len(rows)} linhas, {len(pcts)} comparáveis")
    if not pcts:
        return
    from collections import Counter
    c = Counter(r["status"] for r in rows)
    s = sorted(pcts)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    print(f"  média {statistics.mean(pcts):+.2%} · mediana {statistics.median(pcts):+.2%} · p95 {p95:+.2%} "
          f"· máx positivo {max(pcts):+.2%} · máx negativo {min(pcts):+.2%}")
    print("  contagem:", dict(sorted(c.items())))
    abaixo = [r for r in rows if r["motor_acima_abaixo"] == "ABAIXO"]
    print(f"  MOTOR ABAIXO da cotação direta: {len(abaixo)}")
    for r in sorted(abaixo, key=lambda r: float(r["diferenca_pct"].rstrip("%"))):
        print(f"    {r['diferenca_pct']:>8} #{r['produto_id'] or '—':>4} {r['item_documento'][:45]:45} {r['construcao'][:60]:60} "
              f"direto {r['exw_direto_usd']} motor {r['exw_motor_usd']} [{r['fonte'][:30]}]")


principal = [r for r in linhas if "HIPÓTESE" not in r["item_documento"]]
distribuicao(principal, "DISTRIBUIÇÃO PRINCIPAL (todas as fontes, sem hipóteses)")
distribuicao([r for r in principal if r["fonte"].startswith("produto.exw_cotado_usd")], "produto.exw_cotado_usd")
distribuicao([r for r in principal if r["fonte"].startswith("custoreferencia")], "custoreferencia")
distribuicao([r for r in principal if "xlsx" in r["fonte"]], "xlsx 10-08-2026")
distribuicao([r for r in principal if r["fonte"].startswith("HAMAN")], "HAMAN 25-08-2026")
distribuicao([r for r in principal if r["fonte"].startswith("Anara Storage PI")], "PI 23-08-2026")
distribuicao([r for r in linhas if "HIPÓTESE" in r["item_documento"]], "HIPÓTESE bottom sheet como flat")
nc = [r for r in principal if r["status"] == "NAO_COMPARAVEL"]
print(f"\nnão comparáveis (motor não calcula): {len(nc)}")
from collections import Counter
print(Counter(r["observacao"].split(" | ")[0][:70] for r in nc).most_common(12))
print("\ngravado:", destino)

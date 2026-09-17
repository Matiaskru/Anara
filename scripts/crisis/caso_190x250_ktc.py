"""Caso crítico 190x250 (auditoria de crise, seção 6) — Single/Top Sheet 190x250, 300TC e 400TC.

Quatro caminhos para o mesmo produto técnico:
  (A) cotação direta KTC (PI 23/08/2026: US$ 9,80 EXW, peso real 0,8 kg);
  (B) motor industrial num Produto simulado (`calculadora.produto_simulado` → `calcular_exw`);
  (C) SKU do catálogo equivalente (Top Sheet 190x250 300TC 100% algodão), pelo `custo_net`;
  (D) Produto Personalizado da calculadora (`calculadora.calcular(session, "Flat Sheet", 190, 250, …)`).

Para cada um: EXW, peso, frete, II, outras despesas, NET USD, FX, CNET. Depois 400TC e a
comparação 300 vs 400. Também sonda dois comportamentos da calculadora que apareceram na
seção 8: (i) SKU "· plain" gravado com `plain_or_stripe = stripe`; (ii) §15 — TC 200 é
calculado pelo motor apesar de a regra proibir. Grava `AUDIT/caso_190x250.json`.
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.crisis.ambiente import preparar, AUDIT   # noqa: E402

session = preparar("ktc_caso")

from sqlmodel import select   # noqa: E402
from app import calculadora   # noqa: E402
from app import config_service as cfg   # noqa: E402
from app import pricing_service as ps   # noqa: E402
from app.dinheiro import D   # noqa: E402
from app.models import MaterialPreco, Produto   # noqa: E402
from app.nationalization import nacionalizar   # noqa: E402

FX = cfg.num(session, "fx_usd_brl")
FRETE = cfg.num(session, "frete_int_usd_kg")
OUTRAS = cfg.num(session, "outras_desp_usd_un")
PREM = ps.premissas_nacionalizacao(session)
COTACAO_DIRETA = {300: {"exw": 9.80, "peso": 0.8, "fonte": "Anara Storage PI 23-08-2026.pdf, item 12 (Single Top Sheet 300TC 190x250)"}}


def material(tc: int, stripe="plain"):
    for m in session.exec(select(MaterialPreco)).all():
        if m.ativo and m.thread_count == tc and m.cotton_pct == 1.0 and (m.plain_or_stripe or "plain") == stripe:
            return m
    return None


def linha(nome, exw, peso, peso_tipo, ii, extra=None):
    nac = nacionalizar(exw, peso, ii, PREM) if exw is not None else None
    d = {"caminho": nome, "exw_usd": float(D(exw)) if exw is not None else None,
         "peso_kg": float(D(peso)) if peso is not None else None, "peso_tipo": peso_tipo,
         "frete_usd": float(nac.frete_usd) if nac else None, "ii_pct": ii,
         "ii_usd": float(nac.ii_usd) if nac else None, "outras_usd": OUTRAS,
         "net_usd": float(nac.net_usd) if nac else None, "fx": FX,
         "cnet_brl": float(nac.net_brl) if nac else None}
    if extra:
        d.update(extra)
    return d


def caso(tc: int):
    out = {"tc": tc, "caminhos": []}
    mat = material(tc)
    ii = ps.regra_ncm(session, Produto(familia="Top Sheet")).ii_preferencial

    # (A) cotação direta
    if tc in COTACAO_DIRETA:
        c = COTACAO_DIRETA[tc]
        out["caminhos"].append(linha("A · cotação direta KTC", c["exw"], c["peso"], "REAL KTC", ii, {"fonte": c["fonte"]}))

    # (B) engine industrial num produto simulado (Top Sheet)
    sim = calculadora.produto_simulado(session, "Top Sheet", 190, 250, material_id=mat.id)
    r = ps.calcular_exw(session, sim)
    peso = ps.peso_do_produto(session, sim)
    out["caminhos"].append(linha("B · motor industrial (Top Sheet simulado)", r.exw_usd, peso.peso_kg, peso.tipo, ii,
                                 {"status_motor": r.status, "material": sim.material_ref,
                                  "etapas": [f"{e.nome}: {e.formula} = {D(e.valor):.6f} {e.unidade}" for e in r.etapas]}))
    # (B') mesmo motor, mas com o peso real da KTC (0,8 kg) — isola o efeito do peso
    if tc in COTACAO_DIRETA:
        out["caminhos"].append(linha("B' · motor industrial + peso real KTC 0,8 kg", r.exw_usd, COTACAO_DIRETA[tc]["peso"], "REAL KTC", ii))

    # (C) SKU do catálogo equivalente — casamento por campos estruturados
    cands = [p for p in session.exec(select(Produto)).all()
             if p.fornecedor_id == 1 and (p.familia or "") in ("Top Sheet", "Flat Sheet")
             and p.largura_cm == 190 and p.comprimento_cm == 250 and p.thread_count == tc
             and (p.cotton_pct or 0) >= 0.999 and (p.plain_or_stripe or "plain") == "plain"]
    for p in cands:
        mem = ps.custo_net(session, p)
        nac = mem.get("nacionalizacao") or {}
        out["caminhos"].append({
            "caminho": f"C · SKU catálogo #{p.id} ({p.familia}, cost_method={p.cost_method})",
            "sku": p.sku_key, "exw_usd": mem.get("exw_usd"), "exw_origem": mem.get("exw_origem"),
            "exw_cotado_usd": p.exw_cotado_usd, "exw_cotado_data": str(p.exw_cotado_data), "exw_cotado_fonte": p.exw_cotado_fonte,
            "exw_calculado_usd": p.exw_calculado_usd,
            "peso_kg": p.peso_kg, "peso_tipo": p.peso_tipo, "frete_usd": nac.get("frete_usd"),
            "ii_pct": (mem.get("ncm") or {}).get("ii"), "ii_usd": nac.get("ii_usd"), "outras_usd": OUTRAS,
            "net_usd": nac.get("net_usd"), "fx": FX, "cnet_brl": mem.get("net_brl"),
            "custo_unitario_gravado": p.custo_unitario, "status_canonico": ps.status_canonico_do_custo(mem.get("net_brl"), mem),
        })
    if not cands:
        out["caminhos"].append({"caminho": "C · SKU catálogo", "resultado": "nenhum SKU 190x250 100% algodão plain com esse TC"})

    # (D) Produto Personalizado (Flat Sheet)
    memo = calculadora.calcular(session, "Flat Sheet", 190, 250, material_id=mat.id)
    custo = memo["custo"]
    nac = custo.get("nacionalizacao") or {}
    out["caminhos"].append({
        "caminho": "D · Produto Personalizado (calculadora.calcular, Flat Sheet)",
        "calculavel": memo.get("calculavel"), "exw_usd": custo.get("exw_usd"), "exw_origem": custo.get("exw_origem"),
        "peso_kg": (custo.get("peso") or {}).get("peso_kg"), "peso_tipo": (custo.get("peso") or {}).get("tipo"),
        "frete_usd": nac.get("frete_usd"), "ii_pct": (custo.get("ncm") or {}).get("ii"), "ii_usd": nac.get("ii_usd"),
        "outras_usd": OUTRAS, "net_usd": nac.get("net_usd"), "fx": FX, "cnet_brl": custo.get("net_brl"),
        "preco_recomendado_brl": (memo.get("comercial") or {}).get("preco_negociado"),
        "status_canonico": ps.status_canonico_do_custo(custo.get("net_brl"), custo),
        "aviso_preco": memo.get("aviso_preco"),
    })
    return out


resultado = {"premissas": {"fx_usd_brl": FX, "frete_int_usd_kg": FRETE, "outras_desp_usd_un": OUTRAS},
             "casos": [caso(300), caso(400)]}

# 300 vs 400: tudo o mais igual
b300 = next(c for c in resultado["casos"][0]["caminhos"] if c["caminho"].startswith("B ·"))
b400 = next(c for c in resultado["casos"][1]["caminhos"] if c["caminho"].startswith("B ·"))
resultado["comparacao_300_vs_400"] = {
    "exw_300": b300["exw_usd"], "exw_400": b400["exw_usd"], "400_maior_que_300": b400["exw_usd"] > b300["exw_usd"],
    "cnet_300": b300["cnet_brl"], "cnet_400": b400["cnet_brl"],
    "delta_exw_pct": (b400["exw_usd"] / b300["exw_usd"] - 1),
}

# Sondas de comportamento da calculadora
sondas = {}
# (i) SKU gravado como "plain" com plain_or_stripe = stripe (evidência: produtos 350/351 no banco)
for pid in (349, 350, 351, 352):
    p = session.get(Produto, pid)
    if p:
        mat_usado = cfg.material_preco(session, p.material_ref, p.plain_or_stripe or "plain")
        sondas[f"produto_{pid}"] = {"sku_key": p.sku_key, "plain_or_stripe_coluna": p.plain_or_stripe,
                                    "material_ref": p.material_ref, "preco_m2_usado": mat_usado.price_usd_m2 if mat_usado else None,
                                    "exw_calculado_usd": p.exw_calculado_usd, "custo_unitario": p.custo_unitario}
# reproduz o mecanismo: material listrado + parâmetro plain_or_stripe default → sku diz plain, produto é stripe
m_stripe = material(300, "stripe")
sim_stripe = calculadora.produto_simulado(session, "Flat Sheet", 190, 250, material_id=m_stripe.id)   # plain_or_stripe default "plain"
sim_plain = calculadora.produto_simulado(session, "Flat Sheet", 190, 250, material_id=material(300).id)
sondas["mecanismo_sku_plain_stripe"] = {
    "simulado_com_material_stripe": {"plain_or_stripe": sim_stripe.plain_or_stripe, "exw": float(D(ps.calcular_exw(session, sim_stripe).exw_usd))},
    "simulado_com_material_plain": {"plain_or_stripe": sim_plain.plain_or_stripe, "exw": float(D(ps.calcular_exw(session, sim_plain).exw_usd))},
    "sku_que_salvar_no_catalogo_gravaria": "CALC · Flat Sheet · 190x250 · 300TC Sateen 100% Cotton · plain  (o parâmetro `plain_or_stripe` da função, não o do produto)",
}
# (ii) §15: 200TC não é calculável automaticamente — o motor calcula?
m200 = next((m for m in session.exec(select(MaterialPreco)).all() if m.thread_count == 200), None)
if m200:
    memo200 = calculadora.calcular(session, "Flat Sheet", 190, 250, material_id=m200.id)
    sondas["s15_200tc"] = {"material": m200.material, "calculavel": memo200.get("calculavel"),
                           "exw_usd": memo200["custo"].get("exw_usd"), "cnet_brl": memo200["custo"].get("net_brl"),
                           "status_motor": (memo200["custo"].get("industrial") or {}).get("status"),
                           "aparece_nas_opcoes_da_calculadora": any(o["id"] == m200.id for o in calculadora.opcoes(session)["materiais"])}
resultado["sondas"] = sondas

os.makedirs(AUDIT, exist_ok=True)
destino = os.path.join(AUDIT, "caso_190x250.json")
with open(destino, "w", encoding="utf-8") as f:
    json.dump(resultado, f, ensure_ascii=False, indent=2, default=str)

for c in resultado["casos"]:
    print(f"\n=== 190x250 {c['tc']}TC 100% algodão ===")
    for k in c["caminhos"]:
        print(f"  {k['caminho']}")
        for campo in ("exw_usd", "exw_origem", "peso_kg", "peso_tipo", "frete_usd", "ii_pct", "ii_usd", "outras_usd", "net_usd", "fx", "cnet_brl", "status_canonico", "preco_recomendado_brl", "resultado"):
            if campo in k and k[campo] is not None:
                v = k[campo]
                print(f"      {campo:22} {v:.6f}" if isinstance(v, float) else f"      {campo:22} {v}")
print("\n300 vs 400:", json.dumps(resultado["comparacao_300_vs_400"], indent=1))
print("\nsondas:", json.dumps(sondas, indent=1, ensure_ascii=False, default=str))
print("gravado:", destino)

#!/usr/bin/env python3
"""§17 — Produto personalizado (calculadora): combinações válidas e inválidas, gates, e
reconciliação custom × catálogo equivalente (mesma spec estruturada → mesmo CNET e preço)."""
import asyncio, json, os, sys
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, RequestFalsa, preparar, usuario_admin
session = preparar("personalizado")
from sqlmodel import select
from app import calculadora as calc
from app import pricing_service as ps
from app.dinheiro import D, dinheiro
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, MaterialPreco, Produto
from app.routers.calculadora import salvar as rota_salvar
ADMIN = usuario_admin()
achados = []
def achado(c, g, d): achados.append({"codigo": c, "gravidade": g, "detalhe": d}); print(f"  [{g}] {c}: {d}")

class ReqForm(RequestFalsa):
    def __init__(self, usuario, form): super().__init__(usuario); self._form = form
    async def form(self): return self._form

materiais = {m.id: m for m in session.exec(select(MaterialPreco)).all()}
mat_300 = next(m.id for m in materiais.values() if m.thread_count == 300 and m.cotton_pct == 1.0 and m.plain_or_stripe == "plain")
mat_400 = next(m.id for m in materiais.values() if m.thread_count == 400 and m.cotton_pct == 1.0 and m.plain_or_stripe == "plain")
mat_250 = next(m.id for m in materiais.values() if m.thread_count == 250 and m.cotton_pct == 1.0 and m.plain_or_stripe == "plain")
cliente = session.exec(select(Cliente)).first()

print("== A. combinações válidas e inválidas (nunca podem estourar; inválida = calculavel False) ==")
casos = []
for fam in ("Flat Sheet", "Top Sheet", "Duvet Cover", "Pillow Case"):
    for (l, c) in ((1, 1), (50, 70), (190, 250), (300, 450), (600, 600), (0, 250), (-190, 250), (190, 0), (None, None), (190.5, 250.25)):
        for mat in (mat_250, mat_300, mat_400, 999, None):
            casos.append(dict(familia=fam, largura_cm=l, comprimento_cm=c, material_id=mat))
for fam in ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth"):
    for (l, c) in ((30, 50), (70, 140), (100, 150), (0, 150), (-1, 1), (None, None)):
        for gsm in (300, 450, 500, 550, 0, -5, 9999, None):
            casos.append(dict(familia=fam, largura_cm=l, comprimento_cm=c, gsm=gsm))
for fam in ("Fitted Sheet", "Bathrobe", "Duvet Insert", "Mattress Protector", "Slipper", "Inexistente", ""):
    casos.append(dict(familia=fam, largura_cm=190, comprimento_cm=250, material_id=mat_300))
for abas, flap, fest in ((0, None, False), (2, 20, False), (4, 25, True), (1, 5, True), (9, 200, True), (-1, -5, False)):
    casos.append(dict(familia="Pillow Case", largura_cm=50, comprimento_cm=70, material_id=mat_300, abas=abas, flap_cm=flap, festone=fest))
calculaveis = 0; recusadas = 0; erros = 0; precos = []
for k in casos:
    try:
        m = calc.calcular(session, quantidade=1, **k)
        if m.get("calculavel"):
            calculaveis += 1
            com = m.get("comercial") or {}
            cnet = D((m.get("custo") or {}).get("net_brl") or 0)
            preco = D(com.get("preco_negociado") or 0)
            if preco <= 0 or cnet <= 0:
                achado("PERSONALIZADO_CALCULAVEL_SEM_PRECO", "P1", f"{k}: cnet={cnet} preco={preco}")
            # dimensão inválida NUNCA pode produzir preço
            if (k.get("largura_cm") or 0) <= 0 or (k.get("comprimento_cm") or 0) <= 0 or (k.get("gsm") is not None and k.get("gsm", 1) <= 0) or (k.get("abas") or 0) < 0:
                achado("PERSONALIZADO_DIMENSAO_INVALIDA_COM_PRECO", "P0", f"{k}: preco={preco} cnet={cnet}")
            precos.append((k, cnet, preco))
        else:
            recusadas += 1
    except Exception as e:  # noqa: BLE001
        erros += 1
        achado("PERSONALIZADO_EXCECAO", "P1", f"{k}: {type(e).__name__}: {str(e)[:120]}")
print(f"   casos: {len(casos)} · calculáveis: {calculaveis} · recusados: {recusadas} · exceções: {erros}")

print("\n== B. monotonicidade: 400TC ≥ 300TC ≥ 250TC (mesma dimensão) e área maior ⇒ CNET maior ==")
def cnet_de(fam, l, c, mat):
    m = calc.calcular(session, fam, l, c, material_id=mat, quantidade=1)
    return D((m.get("custo") or {}).get("net_brl") or 0) if m.get("calculavel") else None
for fam in ("Flat Sheet", "Duvet Cover"):
    for (l, c) in ((160, 240), (190, 250), (240, 260), (300, 300)):
        c250, c300, c400 = cnet_de(fam, l, c, mat_250), cnet_de(fam, l, c, mat_300), cnet_de(fam, l, c, mat_400)
        if not (c250 < c300 < c400):
            achado("TC_NAO_MONOTONICO", "P1", f"{fam} {l}x{c}: 250={c250} 300={c300} 400={c400}")
    seq = [cnet_de(fam, l, c, mat_300) for (l, c) in ((100, 200), (160, 240), (190, 250), (240, 260), (300, 300))]
    if any(b <= a for a, b in zip(seq, seq[1:])):
        achado("AREA_NAO_MONOTONICA", "P1", f"{fam} 300TC: {seq}")
print("   ok" if not [a for a in achados if a["codigo"] in ("TC_NAO_MONOTONICO", "AREA_NAO_MONOTONICA")] else "   FALHA")

print("\n== C. custom × catálogo equivalente (spec estruturada igual) ==")
ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
comparados = 0
for p in session.exec(select(Produto).where(Produto.fornecedor_id == ktc.id, Produto.ativo == True)).all():  # noqa: E712
    if p.cost_method != "KTC_CALCULATED" or not p.largura_cm or not p.comprimento_cm:
        continue
    if p.familia not in ("Flat Sheet", "Top Sheet", "Duvet Cover"):
        continue
    mat = next((m.id for m in materiais.values() if m.thread_count == p.thread_count and (m.cotton_pct or 0) == (p.cotton_pct or 0)
                and m.plain_or_stripe == (p.plain_or_stripe or "plain") and (m.weave or "") == (p.weave or m.weave or "")), None)
    if mat is None:
        continue
    custo_cat, mem_cat = ps.custo_para_precificar(session, p)
    m = calc.calcular(session, p.familia, p.largura_cm, p.comprimento_cm, material_id=mat, plain_or_stripe=p.plain_or_stripe or "plain", quantidade=1)
    if not m.get("calculavel") or not custo_cat:
        continue
    cnet_custom = D((m.get("custo") or {}).get("net_brl") or 0)
    comparados += 1
    if abs(cnet_custom - D(custo_cat)) > Decimal("0.01"):
        # peso: catálogo pode ter peso REAL da KTC; o custom estima. Só é achado se a diferença
        # não for explicada pelo peso.
        peso_cat = p.peso_kg; peso_custom = ((m.get("custo") or {}).get("peso") or {}).get("peso_kg")
        achado("CUSTOM_DIVERGE_CATALOGO", "P1" if not (p.peso_tipo == "REAL KTC") else "P2",
               f"{p.sku_key[:50]}: catálogo CNET {D(custo_cat):.4f} × custom {cnet_custom:.4f} (peso cat {peso_cat}/{p.peso_tipo} × custom {peso_custom})")
print(f"   comparados: {comparados} · divergentes: {len([a for a in achados if a['codigo']=='CUSTOM_DIVERGE_CATALOGO'])}")

print("\n== D. família sem fórmula NÃO libera preço; entra A_COTAR e bloqueia ==")
cot = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA", condicao_pagamento="30", status="rascunho", freight_type="FOB", numero="PERS-1")
session.add(cot); session.commit(); session.refresh(cot)
for fam, form in (("Bathrobe", {"familia": "Bathrobe", "largura_cm": "", "comprimento_cm": "", "calculavel": "nao", "quantidade": "3"}),
                  ("Fitted Sheet", {"familia": "Fitted Sheet", "largura_cm": "180", "comprimento_cm": "200", "material_id": str(mat_300), "calculavel": "nao", "quantidade": "2"}),
                  ("Flat Sheet 190x250 300TC", {"familia": "Flat Sheet", "largura_cm": "190", "comprimento_cm": "250", "material_id": str(mat_300), "quantidade": "10"})):
    form = dict(form, cotacao_id=str(cot.id))
    r = asyncio.run(rota_salvar(ReqForm(ADMIN, form), session)); session.commit()
    body = json.loads(bytes(r.body))
    it = session.get(CotacaoItem, body.get("item_id")) if body.get("item_id") else None
    from app import workflow as wf
    bl = [b.codigo for b in wf.blockers_do_item(it)] if it else ["(sem item)"]
    print(f"   {fam}: preco={it.preco_negociado if it else None} status_custo={it.status_custo_item if it else None} blockers={bl}")
    if fam != "Flat Sheet 190x250 300TC" and it and (it.preco_negociado or 0) > 0 and not bl:
        achado("SEM_FORMULA_LIBEROU_PRECO", "P0", f"{fam} entrou com preço {it.preco_negociado} sem bloqueio")
    if fam == "Flat Sheet 190x250 300TC" and it and ((it.preco_negociado or 0) <= 0 or bl):
        achado("CALCULAVEL_BLOQUEADO", "P1", f"{fam}: preco={it.preco_negociado} blockers={bl}")

json.dump(achados, open(os.path.join(AUDIT, "personalizado_achados.json"), "w"), ensure_ascii=False, indent=2, default=str)
print("\nachados:", len(achados))
sys.exit(1 if [a for a in achados if a["gravidade"] in ("P0", "P1")] else 0)

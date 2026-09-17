#!/usr/bin/env python3
"""§14 — A política comercial que o RUNTIME resolve hoje, produto a produto, contra a decisão
de 16/09/2026. Não assume: consulta `margem_padrao` na cópia do banco."""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import preparar
session = preparar("politica")
from sqlmodel import select
from app import pricing_service as ps
from app.dinheiro import D
from app.models import Fornecedor, Produto
forn = {f.id: f.codigo for f in session.exec(select(Fornecedor)).all()}
TOALHAS = {"Bath Towel", "Hand Towel", "Face Towel", "Pool Towel", "Beach Towel", "Bath Mat", "Wash Cloth", "Towel", "Bathrobe"}
SHEETS = {"Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet"}
def esperado(p):
    f = forn.get(p.fornecedor_id)
    if f == "DAUNE": return ("0.12", "0.12", "0.05", True)
    if f == "DECOR_TRICOT": return ("0.12", "0.10", "0.10", False)
    if p.familia in TOALHAS: return ("0.14", "0.11", "0.10", False)
    if p.familia in SHEETS: return (("0.20", "0.17") if (p.thread_count or 0) >= 300 else ("0.18", "0.15")) + ("0.10", False)
    return ("0.17", "0.14", "0.10", False)
c = Counter(); erros = []
for p in session.exec(select(Produto).where(Produto.ativo == True)).all():  # noqa: E712
    m = ps.margem_padrao(session, p)
    got = (str(D(m.margem_pct)), str(D(m.piso_pct)) if m.piso_pct is not None else None, str(D(m.comissao_formacao_pct)) if m.comissao_formacao_pct is not None else None, bool(m.preco_travado))
    esp = esperado(p)
    ok = (D(m.margem_pct) == D(esp[0]) and D(m.piso_pct) == D(esp[1]) and D(m.comissao_formacao_pct) == D(esp[2])
          and bool(m.preco_travado) == esp[3] and m.politica == "POLITICA_COMERCIAL_2026-09-16")
    c[(forn.get(p.fornecedor_id), "sheets>=300" if p.familia in SHEETS and (p.thread_count or 0) >= 300 else ("sheets<300" if p.familia in SHEETS else ("toalhas/roupão" if p.familia in TOALHAS else "demais")), got, m.politica, ok)] += 1
    if not ok:
        erros.append((p.id, p.sku_key, p.familia, p.thread_count, got, esp, m.regra))
for k, v in sorted(c.items(), key=lambda x: -x[1]): print(v, k)
print("divergências:", len(erros)); [print("  ", e) for e in erros[:20]]
sys.exit(1 if erros else 0)

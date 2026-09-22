#!/usr/bin/env python3
"""Quem estava bloqueado só por peso, quem foi recuperado e quem continua bloqueado.

    ANARA_DB_URL=... python3 scripts/peso_2026_09_22/auditoria_peso.py [--json saida.json]

Só olha a CLASSE do problema: SKU ativo, com EXW/custo de origem conhecido, cuja única
premissa faltante é o peso logístico. Não é auditoria geral de catálogo.
"""
import argparse
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ANARA_SECRET_KEY", "auditoria-peso-2026-09-22-local-0123456789abcdef")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="grava o relatório neste arquivo")
    a = ap.parse_args()

    from sqlmodel import Session, select
    import app.db as db
    from app import pricing_service as ps
    from app.models import Fornecedor, Produto

    grupos = {"B_HERDADO": [], "D_SEM_BASE": [], "A_PESO_REAL": []}
    with Session(db.engine) as s:
        forn = {f.id: f.codigo for f in s.exec(select(Fornecedor)).all()}
        produtos = list(s.exec(select(Produto).where(Produto.ativo == True)).all())  # noqa: E712
        with ps.cache_de_leitura(s):
            for p in produtos:
                custo, mem = ps.custo_para_precificar(s, p)
                peso = mem.get("peso") or {}
                falt = list(mem.get("premissas_faltantes") or [])
                linha = {"sku": p.sku_key, "familia": p.familia, "fornecedor": forn.get(p.fornecedor_id),
                         "exw_usd": mem.get("exw_usd"), "exw_fonte": p.exw_cotado_fonte,
                         "cost_method": p.cost_method, "cnet_brl": custo,
                         "peso_kg": peso.get("peso_kg") or p.peso_kg,
                         "peso_tipo": peso.get("tipo") or p.peso_tipo,
                         "peso_origem": peso.get("origem"), "peso_fonte": peso.get("fonte"),
                         "sku_origem": peso.get("sku_origem"),
                         "status": ps.status_do_produto(s, p, custo, mem)}
                if mem.get("peso_por_analogia"):
                    grupos["B_HERDADO"].append(linha)
                elif falt == ["peso"]:
                    grupos["D_SEM_BASE"].append(linha)
                elif p.peso_tipo == "REAL KTC" and p.familia == "Bathrobe":
                    grupos["A_PESO_REAL"].append(linha)

    print(f"A) peso REAL documentado (roupão) ....... {len(grupos['A_PESO_REAL'])}")
    print(f"B) peso ESTIMADO herdado do histórico ... {len(grupos['B_HERDADO'])}")
    print(f"D) sem base — continua REVIEW_REQUIRED .. {len(grupos['D_SEM_BASE'])}")
    for g, titulo in (("B_HERDADO", "RECUPERADOS (REVIEW_REQUIRED → ESTIMADO)"),
                      ("D_SEM_BASE", "CONTINUAM REVIEW_REQUIRED")):
        print(f"\n== {titulo}")
        for l in grupos[g]:
            extra = (f"← {l['sku_origem']}" if l.get("sku_origem")
                     else "sem análogo de mesmo tamanho/gramatura/composição")
            peso = f"{l['peso_kg']:.3f} kg" if l["peso_kg"] else "—"
            print(f"   {l['status']:<16} EXW US$ {l['exw_usd']:<6} peso {peso:<10} {l['sku'][:54]}")
            print(f"   {'':<16} {extra}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(grupos, f, ensure_ascii=False, indent=1)
        print(f"\nrelatório: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

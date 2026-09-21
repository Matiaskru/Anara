#!/usr/bin/env python3
"""A política comercial que o RUNTIME resolve hoje, produto a produto, contra a decisão de
21/09/2026. Não assume: consulta `margem_padrao` no banco apontado por `ANARA_DB_URL` (cópia).

    ANARA_DB_URL=sqlite:////caminho/copia_aplicada.db python3 scripts/politica_2026_09_21/politica_vigente_v2.py
"""
import os
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from sqlmodel import Session, select  # noqa: E402

from app import pricing_service as ps  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import D  # noqa: E402
from app.models import Fornecedor, Produto  # noqa: E402
from app.politica_comercial import CODIGO_ELIS, ROTULO_2026_09_21  # noqa: E402

assert os.path.realpath(caminho_do_banco()) != os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
TOALHAS = {"Bath Towel", "Hand Towel", "Face Towel", "Pool Towel", "Beach Towel", "Bath Mat", "Wash Cloth", "Towel"}
SHEETS = {"Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet"}


def esperado(codigo, familia, tc):
    tc = tc or 0
    if codigo == "DAUNE":
        return "0.13"
    if codigo == "DECOR_TRICOT":
        return "0.13"
    if codigo == CODIGO_ELIS:
        return "0.14"
    if familia in TOALHAS:
        return "0.16"
    if familia == "Bathrobe":
        return "0.14"
    if familia in SHEETS:
        return "0.23" if tc >= 400 else ("0.22" if tc >= 300 else "0.20")
    if familia in ("Pillow Case", "Duvet Cover"):
        return "0.20" if tc >= 400 else "0.19"
    return "0.20" if tc >= 400 else "0.19"


def main():
    c, erros, sem_regra = Counter(), [], []
    with Session(engine) as s:
        forn = {f.id: f.codigo for f in s.exec(select(Fornecedor)).all()}
        for p in s.exec(select(Produto).where(Produto.ativo == True)).all():   # noqa: E712
            m = ps.margem_padrao(s, p)
            codigo = forn.get(p.fornecedor_id)
            if not m.tem_regra:
                sem_regra.append((p.id, p.sku_key, codigo, p.familia))
                continue
            esp = esperado(codigo, p.familia, p.thread_count)
            ok = (D(m.margem_pct) == D(esp) and m.piso_pct is None and D(m.comissao_formacao_pct) == D("0.05")
                  and not m.preco_travado and m.politica == ROTULO_2026_09_21)
            c[(codigo, str(D(m.margem_pct)), ok)] += 1
            if not ok:
                erros.append((p.id, p.sku_key, codigo, p.familia, p.thread_count, str(D(m.margem_pct)), esp, m.regra))
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print(v, k)
    print("sem regra (bloqueiam):", len(sem_regra), sem_regra[:10])
    print("divergências:", len(erros))
    for e in erros[:20]:
        print("  ", e)
    return 1 if erros or sem_regra else 0


if __name__ == "__main__":
    sys.exit(main())

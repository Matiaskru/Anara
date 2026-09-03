#!/usr/bin/env python3
"""Gera o nome de exibição de cada SKU a partir dos campos estruturados.

Guarda o nome antigo em `nome_original` (uma vez só — rodar de novo não sobrescreve o
original) e regrava `nome`. Cotação já emitida não muda: o item guarda o nome que usou.

    python3 scripts/renomear_catalogo.py --dry-run
    python3 scripts/renomear_catalogo.py
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

from sqlmodel import Session, select

from app.db import engine
from app.migrations import fazer_backup
from app.models import Produto
from app.nomes import nome_canonico


def renomear(dry_run=False):
    if not dry_run:
        fazer_backup("renomear-catalogo")

    mudancas, iguais = [], 0
    with Session(engine) as s:
        for p in s.exec(select(Produto)).all():
            if not p.nome_original:
                p.nome_original = p.nome
            novo = nome_canonico(p)
            if novo != p.nome:
                mudancas.append((p.id, p.nome, novo))
                p.nome = novo
            else:
                iguais += 1
            if not dry_run:
                s.add(p)
        if dry_run:
            s.rollback()
        else:
            s.commit()

    # nomes que continuam repetidos depois da troca
    with Session(engine) as s:
        nomes = [nome_canonico(p) for p in s.exec(select(Produto)).all()]
    repetidos = {n: c for n, c in Counter(nomes).items() if c > 1}
    return {"renomeados": mudancas, "iguais": iguais, "repetidos": repetidos}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = renomear(args.dry_run)
    print(f"{len(r['renomeados'])} renomeados · {r['iguais']} já estavam bons")
    print(f"nomes ainda repetidos depois da troca: {len(r['repetidos'])} "
          f"({sum(r['repetidos'].values())} SKUs)")
    for nome, n in list(r["repetidos"].items())[:8]:
        print(f"    {n}× {nome}")
    print("\nAmostra:")
    for _id, antigo, novo in r["renomeados"][:20]:
        print(f"  {antigo[:38]:38s} → {novo}")

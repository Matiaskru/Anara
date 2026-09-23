#!/usr/bin/env python3
"""Renomeia os SKUs cujo nome perdeu a família (23/09/2026).

    python3 scripts/drafts_2026_09_22/renomear_lencol_de_cima.py            # preview
    python3 scripts/drafts_2026_09_22/renomear_lencol_de_cima.py --aplicar

`Top Sheet` e `Flat Sheet` dividem a categoria "Lençol Plano", e o nome canônico era montado
pela categoria. Resultado: todo lençol **de cima** do catálogo foi gravado como "Lençol
plano" — a operação escolhia "Lençol de cima" na calculadora e recebia "Lençol plano" na
cotação. O motor de nomes já foi corrigido (a família desempata); isto reescreve o `nome` dos
SKUs que ficaram com o rótulo errado.

Mexe **só** em `Produto.nome`. Não toca em preço, custo, medida, família, categoria ou SKU, e
não reescreve `CotacaoItem.nome_produto`: o nome do item é pinado no momento em que ele entra
na proposta, e cotação emitida é imutável. Idempotente.
"""
import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ANARA_SECRET_KEY", "renomear-2026-09-23-local-0123456789abcdefgh")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    from sqlmodel import Session, select
    import app.db as db
    from app.models import Produto
    from app.nomes import ROTULO_POR_FAMILIA, nome_canonico

    familias = set(ROTULO_POR_FAMILIA)
    mudancas = []
    with Session(db.engine) as s:
        for p in s.exec(select(Produto)).all():
            if (p.familia or "").strip().lower() not in familias:
                continue
            novo = nome_canonico(p)
            if novo == p.nome:
                continue
            mudancas.append((p.sku_key, p.nome, novo))
            p.nome = novo
            s.add(p)
        if a.aplicar:
            s.commit()
        else:
            s.rollback()

    print(f"SKUs com nome a corrigir: {len(mudancas)}")
    for sku, antes, depois in mudancas:
        print(f"  {antes:<46} → {depois}")
        print(f"      {sku[:74]}")
    print("\nPREVIEW — nada gravado. Use --aplicar." if not a.aplicar else "\nAPLICADO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

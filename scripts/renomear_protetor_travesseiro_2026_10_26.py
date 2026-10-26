#!/usr/bin/env python3
"""Protetor de fronha → Protetor de travesseiro (26/10/2026, pedido da operação).

    python3 scripts/renomear_protetor_travesseiro_2026_10_26.py            # preview (não grava)
    python3 scripts/renomear_protetor_travesseiro_2026_10_26.py --aplicar

O motor de nomes (`app/nomes.py`) passou a chamar a peça de "Protetor de travesseiro"; isto reescreve
o `nome` GRAVADO dos SKUs da família Pillow Protector que ainda dizem "Protetor de fronha", pelo
mesmo `nome_canonico` que nomeia SKU novo.

Mexe **só** em `Produto.nome` (e grava a trilha no AuditLog). Não toca em preço, custo, medida,
família, categoria, `sku_key` nem `nome_original`, e não reescreve `CotacaoItem.nome_produto`: o nome
do item é pinado quando ele entra na proposta, e cotação emitida é imutável — num rascunho, basta
remover e adicionar o item de novo. Idempotente: rodar duas vezes não muda nada na segunda.
"""
import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ANARA_SECRET_KEY", "renomear-2026-10-26-local-0123456789abcdefgh")

ORIGEM = "script-renomear-protetor-travesseiro-2026-10-26"


def renomear(session) -> list:
    """Aplica na sessão (sem commit) e devolve [(id, sku_key, antes, depois)]."""
    from sqlmodel import select

    from app import admin_service as adm
    from app.models import Produto
    from app.nomes import nome_canonico

    mudancas = []
    for p in session.exec(select(Produto).where(Produto.familia == "Pillow Protector")).all():
        if not (p.nome or "").lower().startswith("protetor de fronha"):
            continue
        novo = nome_canonico(p)
        if novo == p.nome or not novo.lower().startswith("protetor de travesseiro"):
            continue
        mudancas.append((p.id, p.sku_key, p.nome, novo))
        adm.registrar(session, ator=None, acao="RENAME_PRODUCT", entidade="Produto", entidade_id=p.id,
                      escopo=p.sku_key, antes=p.nome, depois=novo, motivo="Protetor de fronha → Protetor de travesseiro",
                      origem=ORIGEM)
        p.nome = novo
        session.add(p)
    return mudancas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    from sqlmodel import Session

    import app.db as db

    with Session(db.engine) as s:
        mudancas = renomear(s)
        if a.aplicar:
            s.commit()
        else:
            s.rollback()

    print(f"Produtos a renomear: {len(mudancas)}")
    for pid, _sku, antes, depois in mudancas:
        print(f"  #{pid:<5} {antes:<52} → {depois}")
    print("\nPREVIEW — nada gravado. Para gravar: --aplicar" if not a.aplicar else "\nAPLICADO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

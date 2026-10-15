#!/usr/bin/env python3
"""Renomeia os modelos das peseiras Decor Tricot (15/10/2026, pedido do Matias).

    python3 scripts/renomear_peseiras_2026_10_15.py            # preview
    python3 scripts/renomear_peseiras_2026_10_15.py --aplicar

Arezzo → Nilo · Relevo → Tunes · Sissi → Alexandria · Sofia → Sinai.

Mexe **só** em `Produto.nome` e `Produto.especificacao` das peseiras da Decor Tricot (família Bed
Runner). Não toca em preço, custo, medida nem `sku_key` (a identidade do SKU não muda: o produto é
o mesmo, só o nome comercial do modelo), e não reescreve `CotacaoItem.nome_produto` — o nome do
item é pinado quando ele entra na proposta, e cotação emitida é imutável. Cada SKU renomeado vai
para o AuditLog. Idempotente.
"""
import argparse
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

NOVOS = {"Arezzo": "Nilo", "Relevo": "Tunes", "Sissi": "Alexandria", "Sofia": "Sinai"}
ORIGEM = "renomear-peseiras-2026-10-15"


def renomear(texto):
    for antigo, novo in NOVOS.items():
        texto = re.sub(rf"\bmodelo {antigo}\b", f"modelo {novo}", texto or "")
    return texto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    from sqlalchemy import text
    from sqlmodel import Session, select
    import app.db as db
    from app import admin_service as adm
    from app.models import Fornecedor, Usuario

    print("banco:", db.url_segura())
    mudancas = []
    with Session(db.engine) as s:
        decor = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).one()
        ator = s.exec(select(Usuario).where(Usuario.papel == "OWNER")).first()
        # SQL direto, só nas colunas tocadas: o script roda em qualquer versão do esquema de
        # produção (não depende das colunas novas que o modelo Produto do código local já espera)
        linhas = s.connection().execute(text(
            "SELECT id, sku_key, nome, especificacao FROM produto WHERE fornecedor_id = :f AND familia = 'Bed Runner' "
            "ORDER BY id"), {"f": decor.id}).all()
        for pid, sku, nome0, esp0 in linhas:
            nome, esp = renomear(nome0), renomear(esp0)
            if (nome, esp) == (nome0, esp0):
                continue
            mudancas.append((pid, sku, nome0, nome))
            s.connection().execute(text("UPDATE produto SET nome = :n, especificacao = :e WHERE id = :i"),
                                   {"n": nome, "e": esp, "i": pid})
            adm.registrar(s, ator=ator, acao="RENOMEAR_PRODUTO", entidade="Produto", entidade_id=pid,
                          escopo=f"SKU {sku}", antes=f"{nome0} | {esp0}", depois=f"{nome} | {esp}",
                          motivo="Novos nomes dos modelos de peseira: Arezzo→Nilo, Relevo→Tunes, Sissi→Alexandria, Sofia→Sinai",
                          origem=ORIGEM)
        if a.aplicar:
            s.commit()
        else:
            s.rollback()

    print(f"peseiras a renomear: {len(mudancas)}")
    for pid, sku, antes, depois in mudancas:
        print(f"  #{pid:<4} {antes:<40} → {depois}")
    print("\nPREVIEW — nada gravado. Use --aplicar." if not a.aplicar else "\nAPLICADO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

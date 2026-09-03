#!/usr/bin/env python3
"""Confere se as migrations do Alembic reproduzem o esquema do banco de produção.

A revisão inicial da Fase 0 tem uma obrigação: ser **fotografia**, não redesenho. Este
script prova isso — constrói um banco novo do zero aplicando as migrations e compara,
com o banco vivo, tabela a tabela: colunas, tipo declarado, NOT NULL, chave primária,
índices e chaves estrangeiras.

Diferenças conhecidas e aceitas ficam declaradas aqui em `TIPOS_TOLERADOS`, com o motivo.
Qualquer outra diferença é falha.

Uso:
    python3 scripts/conferir_esquema_alembic.py [--revisao head] [--db data/anara.db]
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.fundacao import DB_PATH  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Divergência de tipo declarado aceita, com motivo. No SQLite, BOOLEAN tem afinidade
# NUMERIC e INTEGER tem afinidade INTEGER; para os valores 0/1 que estas colunas guardam,
# leitura, escrita, comparação e ordenação são idênticas. As colunas estão INTEGER em
# produção porque foram acrescentadas por ALTER TABLE (ver B-13 no audit); ficam BOOLEAN
# nas migrations, que é o tipo do modelo e o tipo certo no PostgreSQL da Onda 4.
TIPOS_TOLERADOS = {
    ("cliente", "ativo"): ("INTEGER", "BOOLEAN"),
    ("cotacao", "freight_incluso"): ("INTEGER", "BOOLEAN"),
    ("produto", "precisa_revisao"): ("INTEGER", "BOOLEAN"),
    ("toalhapreco", "preco_final"): ("INTEGER", "BOOLEAN"),
}


def esquema(caminho: str, somente_leitura: bool = False) -> dict:
    con = sqlite3.connect(f"file:{caminho}?mode=ro" if somente_leitura else caminho,
                          uri=somente_leitura)
    tabelas = sorted(r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    fora = {"alembic_version"}
    dados = {}
    for t in tabelas:
        if t in fora:
            continue
        dados[t] = {
            "colunas": {r[1]: {"tipo": (r[2] or "").upper(), "notnull": r[3], "pk": r[5]}
                        for r in con.execute(f'PRAGMA table_info("{t}")')},
            "indices": {r[1] for r in con.execute(f'PRAGMA index_list("{t}")')
                        if not r[1].startswith("sqlite_autoindex")},
            "fks": {(r[3], r[2], r[4]) for r in con.execute(f'PRAGMA foreign_key_list("{t}")')},
        }
    con.close()
    return dados


def construir(revisao: str = "head") -> str:
    destino = os.path.join(tempfile.mkdtemp(prefix="anara-esquema-"), "novo.db")
    ambiente = dict(os.environ, ANARA_DB_URL=f"sqlite:///{destino}")
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revisao],
                       cwd=RAIZ, env=ambiente, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"alembic upgrade falhou:\n{r.stderr}")
    return destino


def comparar(vivo: dict, novo: dict) -> list:
    problemas = []
    for t in sorted(set(vivo) - set(novo)):
        problemas.append(f"tabela só no banco vivo: {t}")
    for t in sorted(set(novo) - set(vivo)):
        problemas.append(f"tabela só nas migrations: {t}")

    for t in sorted(set(vivo) & set(novo)):
        a, b = vivo[t], novo[t]
        for c in sorted(set(a["colunas"]) - set(b["colunas"])):
            problemas.append(f"{t}.{c}: coluna só no banco vivo")
        for c in sorted(set(b["colunas"]) - set(a["colunas"])):
            problemas.append(f"{t}.{c}: coluna só nas migrations")
        for c in sorted(set(a["colunas"]) & set(b["colunas"])):
            ca, cb = a["colunas"][c], b["colunas"][c]
            if ca["tipo"] != cb["tipo"] and TIPOS_TOLERADOS.get((t, c)) != (ca["tipo"], cb["tipo"]):
                problemas.append(f"{t}.{c}: tipo {ca['tipo']} (vivo) x {cb['tipo']} (migrations)")
            if ca["notnull"] != cb["notnull"]:
                problemas.append(f"{t}.{c}: NOT NULL {ca['notnull']} x {cb['notnull']}")
            if ca["pk"] != cb["pk"]:
                problemas.append(f"{t}.{c}: chave primária {ca['pk']} x {cb['pk']}")
        if a["indices"] != b["indices"]:
            problemas.append(f"{t}: índices {sorted(a['indices'])} x {sorted(b['indices'])}")
        if a["fks"] != b["fks"]:
            problemas.append(f"{t}: FKs {sorted(a['fks'])} x {sorted(b['fks'])}")
    return problemas


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--revisao", default="head")
    p.add_argument("--db", default=DB_PATH)
    a = p.parse_args()

    novo = construir(a.revisao)
    try:
        vivo_esquema = esquema(a.db, somente_leitura=True)
        novo_esquema = esquema(novo)
        problemas = comparar(vivo_esquema, novo_esquema)
    finally:
        shutil.rmtree(os.path.dirname(novo), ignore_errors=True)

    tabelas = len(set(vivo_esquema) & set(novo_esquema))
    colunas = sum(len(v["colunas"]) for k, v in vivo_esquema.items() if k in novo_esquema)
    indices = sum(len(v["indices"]) for v in vivo_esquema.values())
    fks = sum(len(v["fks"]) for v in vivo_esquema.values())
    print(f"revisão conferida: {a.revisao}")
    print(f"  {tabelas} tabelas · {colunas} colunas · {indices} índices · {fks} FKs")
    print(f"  diferenças toleradas e documentadas: {len(TIPOS_TOLERADOS)} "
          f"(tipo declarado BOOLEAN x INTEGER — ver B-13)")
    if problemas:
        print(f"\nPROBLEMAS: {len(problemas)}")
        for x in problemas:
            print("  -", x)
        return 1
    print("\nESQUEMA FIEL: as migrations reproduzem o banco de produção.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

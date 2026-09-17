#!/usr/bin/env python3
"""Migra os DADOS do SQLite da Anara para um PostgreSQL já migrado pelo Alembic.

O que este script é: a mudança de casa do banco no cutover — uma vez, controlada, com
prova. O que ele NÃO é: sincronização contínua, nem ferramenta de "ajuste" de dados. Ele
copia o que está na origem, preservando cada `id`, e depois **confere** que o destino diz
exatamente o mesmo que a origem.

Regras, na ordem em que o script as aplica:

1. **A origem é aberta somente-leitura** (`mode=ro`). Nada aqui apaga, altera ou sequer
   abre para escrita o arquivo SQLite. O backup final da origem é feito FORA daqui, antes.
2. **O destino precisa estar no mesmo `alembic_version` da origem.** O esquema é do
   Alembic (`alembic upgrade head` no destino, antes); este script não cria tabela.
3. **O destino precisa estar VAZIO** — fora as três tabelas que as próprias migrations
   semeiam (`aliquotainterestadual`, `premissa`, `regrafcp`), sempre substituídas pelas
   linhas da origem. Qualquer outra linha em qualquer tabela do modelo e o script recusa — a não ser que se passe `--substituir-destino NOME_DO_BANCO`, digitando
   o nome do banco de destino: aí ele esvazia as tabelas **na mesma transação** da carga.
   Errar o nome recusa. Não existe "merge".
4. **Uma transação.** Esvaziar (se pedido), carregar todas as tabelas em ordem de FK e
   acertar as sequences acontece num único `BEGIN … COMMIT`. Falhou no meio, o destino
   volta ao que era.
5. **IDs preservados**, e as sequences do PostgreSQL são reposicionadas em `MAX(id)`
   — senão a primeira cotação nova colidiria com uma histórica.
6. **Conferência, tabela a tabela:** contagem, conjunto de PKs, e comparação **linha a
   linha, coluna a coluna** dos valores normalizados. FKs são validadas no destino (linha
   filha apontando para pai inexistente). Tudo vai para um relatório JSON.
7. Sai com código 0 **só** se não houver divergência alguma. `--so-verificar` roda apenas
   a conferência (para repetir a prova depois, sem carregar nada).

Uso:
    python3 scripts/migrar_sqlite_para_postgres.py \\
        --origem /caminho/copia/anara.db \\
        --destino postgresql://usuario:senha@host:5432/anara \\
        --relatorio relatorios/migracao_postgres.json

    # repetir só a prova
    python3 scripts/migrar_sqlite_para_postgres.py --origem ... --destino ... --so-verificar

Teste SEMPRE em cópia do SQLite e num PostgreSQL de teste antes do cutover.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import warnings
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from sqlalchemy import create_engine, func, insert, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import app.models  # noqa: F401,E402  — registra todas as tabelas no metadata
from app.db import normalizar_url, url_segura  # noqa: E402

LOTE = 500

#: Tabelas que as próprias migrations semeiam (0003: alíquotas e premissas; 0005: FCP do
#: RJ). Um PostgreSQL recém-migrado NUNCA está vazio nelas — e a origem tem as mesmas
#: linhas, com seus ids. Por isso elas não contam como "destino ocupado": são sempre
#: esvaziadas na transação e recarregadas da origem, id por id.
SEMEADAS_POR_MIGRATION = {"aliquotainterestadual", "premissa", "regrafcp"}

warnings.filterwarnings("ignore", message="Cannot correctly sort tables")


# ---------------------------------------------------------------------------
# Conexões
# ---------------------------------------------------------------------------
def engine_origem(caminho_ou_url: str):
    """SQLite somente-leitura. Aceita caminho de arquivo ou `sqlite:///...`."""
    if caminho_ou_url.startswith("sqlite:///"):
        caminho = caminho_ou_url[len("sqlite:///"):].split("?", 1)[0]
    else:
        caminho = caminho_ou_url
    caminho = os.path.abspath(os.path.expanduser(caminho))
    if not os.path.exists(caminho):
        raise SystemExit(f"origem não existe: {caminho}")
    return create_engine(f"sqlite:///file:{caminho}?mode=ro&uri=true"), caminho


def engine_destino(url: str):
    url = normalizar_url(url)
    if url.startswith("sqlite"):
        # útil para a suíte de testes; em produção o destino é PostgreSQL
        return create_engine(url), "sqlite"
    if not url.startswith("postgresql"):
        raise SystemExit("destino precisa ser postgresql:// (ou sqlite:/// em teste)")
    return create_engine(url, pool_pre_ping=True), "postgresql"


def versao_alembic(con) -> str:
    try:
        linha = con.execute(text("select version_num from alembic_version")).first()
    except Exception:                                    # noqa: BLE001
        return ""
    return linha[0] if linha else ""


# ---------------------------------------------------------------------------
# Normalização para comparar origem × destino
# ---------------------------------------------------------------------------
def normalizar(valor):
    """Uma forma canônica por valor, para que `1` (SQLite) e `True` (Postgres) sejam iguais,
    `datetime` e sua string ISO também, e enum e seu nome também."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return bool(valor)
    if hasattr(valor, "name") and hasattr(valor, "value") and not isinstance(valor, (dt.date, dt.datetime)):
        return valor.name                                # Enum → nome, como está gravado
    if isinstance(valor, dt.datetime):
        return valor.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    if isinstance(valor, dt.date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, float):
        return valor
    if isinstance(valor, bytes):
        return valor.hex()
    return valor


def ler_tabela(con, tabela):
    """Linhas tipadas pelo metadata (booleans, datas e enums já convertidos)."""
    return [dict(r._mapping) for r in con.execute(select(tabela)).all()]


def chave_pk(tabela, linha):
    return tuple(linha[c.name] for c in tabela.primary_key.columns)


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def colunas_adiadas(tabela, posicao: dict) -> set:
    """FKs que apontam para tabela carregada DEPOIS desta (ciclo `cotacao ↔ oportunidade`)
    ou para ela mesma. Entram como NULL na carga e são preenchidas no fim, quando os dois
    lados existem — sem isso o PostgreSQL recusaria a primeira linha do ciclo."""
    adiadas = set()
    for fk in tabela.foreign_keys:
        pai = fk.column.table
        if pai is tabela or posicao.get(pai.name, -1) > posicao[tabela.name]:
            adiadas.add(fk.parent.name)
    return adiadas


def carregar(con_origem, con_destino, dialeto: str, substituir: bool, relatorio: dict):
    tabelas = SQLModel.metadata.sorted_tables           # ordem de FK: pai antes de filho
    posicao = {t.name: i for i, t in enumerate(tabelas)}
    # ordem inversa para apagar: filhos antes dos pais; as adiadas são zeradas antes
    for tabela in reversed(tabelas):
        if substituir or tabela.name in SEMEADAS_POR_MIGRATION:
            adiadas = colunas_adiadas(tabela, posicao)
            if adiadas:
                con_destino.execute(tabela.update().values({c: None for c in adiadas}))
    for tabela in reversed(tabelas):
        if substituir or tabela.name in SEMEADAS_POR_MIGRATION:
            con_destino.execute(tabela.delete())
    relatorio["destino_esvaziado"] = bool(substituir)

    pendentes = []                                       # (tabela, pk, {coluna: valor})
    for tabela in tabelas:
        linhas = ler_tabela(con_origem, tabela)
        colunas = [c.name for c in tabela.columns]
        adiadas = colunas_adiadas(tabela, posicao)
        for i in range(0, len(linhas), LOTE):
            lote = []
            for linha in linhas[i:i + LOTE]:
                registro = {c: linha.get(c) for c in colunas}
                depois = {c: registro[c] for c in adiadas if registro.get(c) is not None}
                if depois:
                    for c in depois:
                        registro[c] = None
                    pendentes.append((tabela, chave_pk(tabela, linha), depois))
                lote.append(registro)
            if lote:
                con_destino.execute(insert(tabela), lote)
        relatorio["carga"][tabela.name] = len(linhas)

    for tabela, pk, valores in pendentes:
        condicao = [c == v for c, v in zip(tabela.primary_key.columns, pk)]
        con_destino.execute(tabela.update().where(*condicao).values(**valores))
    relatorio["fks_adiadas"] = len(pendentes)

    if dialeto == "postgresql":
        for tabela in tabelas:
            for coluna in tabela.primary_key.columns:
                if coluna.autoincrement in (True, "auto") and coluna.type.python_type is int:
                    seq = con_destino.execute(text(
                        "select pg_get_serial_sequence(:t, :c)"),
                        {"t": tabela.name, "c": coluna.name}).scalar()
                    if not seq:
                        continue
                    maior = con_destino.execute(
                        select(func.max(coluna))).scalar()
                    con_destino.execute(text(
                        "select setval(:s, :v, :chamado)"),
                        {"s": seq, "v": maior or 1, "chamado": maior is not None})
                    relatorio["sequences"][f"{tabela.name}.{coluna.name}"] = maior or 0


# ---------------------------------------------------------------------------
# Conferência
# ---------------------------------------------------------------------------
def conferir(con_origem, con_destino, relatorio: dict) -> bool:
    ok = True
    for tabela in SQLModel.metadata.sorted_tables:
        origem = ler_tabela(con_origem, tabela)
        destino = ler_tabela(con_destino, tabela)
        item = {"origem": len(origem), "destino": len(destino), "divergencias": []}
        if len(origem) != len(destino):
            item["divergencias"].append(f"contagem {len(origem)} × {len(destino)}")
        mapa_o = {chave_pk(tabela, l): l for l in origem}
        mapa_d = {chave_pk(tabela, l): l for l in destino}
        so_origem = sorted(set(mapa_o) - set(mapa_d))
        so_destino = sorted(set(mapa_d) - set(mapa_o))
        if so_origem:
            item["divergencias"].append(f"PKs só na origem: {so_origem[:10]}")
        if so_destino:
            item["divergencias"].append(f"PKs só no destino: {so_destino[:10]}")
        diferentes = 0
        for pk in set(mapa_o) & set(mapa_d):
            lo, ld = mapa_o[pk], mapa_d[pk]
            for c in tabela.columns:
                a, b = normalizar(lo[c.name]), normalizar(ld[c.name])
                if isinstance(a, float) and isinstance(b, float):
                    if a != b and abs(a - b) > 1e-9 * max(1.0, abs(a)):
                        diferentes += 1
                        item["divergencias"].append(f"{pk} {c.name}: {a!r} × {b!r}")
                elif a != b:
                    diferentes += 1
                    if len(item["divergencias"]) < 20:
                        item["divergencias"].append(f"{pk} {c.name}: {a!r} × {b!r}")
        item["linhas_diferentes"] = diferentes
        if item["divergencias"]:
            ok = False
        relatorio["conferencia"][tabela.name] = item

    # FKs no destino: filho apontando para pai que não existe
    for tabela in SQLModel.metadata.sorted_tables:
        for fk in tabela.foreign_keys:
            filho = fk.parent
            pai_alias = fk.column.table.alias("pai")      # alias: cobre auto-referência
            pai = pai_alias.c[fk.column.name]
            orfaos = con_destino.execute(
                select(func.count()).select_from(
                    tabela.outerjoin(pai_alias, filho == pai))
                .where(filho.isnot(None)).where(pai.is_(None))).scalar()
            relatorio["fks"][f"{tabela.name}.{filho.name} → {fk.column.table.name}.{fk.column.name}"] = orfaos
            if orfaos:
                ok = False
            continue
    return ok


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--origem", required=True, help="arquivo SQLite (cópia!) ou sqlite:///…")
    ap.add_argument("--destino", required=True, help="postgresql://… já em `alembic upgrade head`")
    ap.add_argument("--relatorio", help="grava o relatório JSON neste arquivo")
    ap.add_argument("--so-verificar", action="store_true", help="não carrega; só confere")
    ap.add_argument("--substituir-destino", metavar="NOME_DO_BANCO",
                    help="esvazia o destino antes de carregar — exige digitar o nome do banco")
    args = ap.parse_args()

    inicio = time.time()
    eng_o, caminho_origem = engine_origem(args.origem)
    eng_d, dialeto = engine_destino(args.destino)
    relatorio = {"origem": caminho_origem, "destino": url_segura(str(eng_d.url)),
                 "quando": dt.datetime.now().isoformat(timespec="seconds"),
                 "carga": {}, "sequences": {}, "conferencia": {}, "fks": {},
                 "destino_esvaziado": False, "tabelas_ignoradas_na_origem": [],
                 "fks_adiadas": 0}
    print(f"origem : {caminho_origem} (somente leitura)")
    print(f"destino: {relatorio['destino']}")

    with eng_o.connect() as co, eng_d.connect() as cd:
        v_o, v_d = versao_alembic(co), versao_alembic(cd)
        relatorio["alembic"] = {"origem": v_o, "destino": v_d}
        if not v_d:
            print("RECUSADO: o destino não tem alembic_version — rode `alembic upgrade head` nele.")
            return 2
        if v_o != v_d:
            print(f"RECUSADO: alembic_version difere (origem {v_o!r}, destino {v_d!r}). "
                  "Migre os dois para o mesmo head antes.")
            return 2

        modelo = set(SQLModel.metadata.tables)
        from sqlalchemy import inspect
        na_origem = set(inspect(eng_o).get_table_names())
        relatorio["tabelas_ignoradas_na_origem"] = sorted(na_origem - modelo - {"alembic_version"})
        faltam = sorted(modelo - na_origem)
        if faltam:
            print(f"RECUSADO: a origem não tem as tabelas {faltam} — está em outro esquema.")
            return 2

        if not args.so_verificar:
            ocupadas = {t.name: cd.execute(select(func.count()).select_from(t)).scalar()
                        for t in SQLModel.metadata.sorted_tables
                        if t.name not in SEMEADAS_POR_MIGRATION}
            ocupadas = {k: v for k, v in ocupadas.items() if v}
            nome_banco = make_url(str(eng_d.url)).database or ""
            substituir = False
            if ocupadas:
                if args.substituir_destino and args.substituir_destino == nome_banco:
                    substituir = True
                    print(f"destino tem dados ({sum(ocupadas.values())} linhas em "
                          f"{len(ocupadas)} tabelas) e será ESVAZIADO na mesma transação "
                          f"— --substituir-destino {nome_banco!r} confere.")
                else:
                    print(f"RECUSADO: destino NÃO está vazio ({sum(ocupadas.values())} linhas em "
                          f"{len(ocupadas)} tabelas: {', '.join(sorted(ocupadas))}). "
                          f"Para substituir, passe --substituir-destino {nome_banco!r}.")
                    return 3
            cd.rollback()                                # fecha o autobegin das leituras
            with cd.begin():
                carregar(co, cd, dialeto, substituir, relatorio)
            print(f"carga: {sum(relatorio['carga'].values())} linhas em "
                  f"{len(relatorio['carga'])} tabelas; sequences: {len(relatorio['sequences'])}")

        ok = conferir(co, cd, relatorio)

    relatorio["ok"] = ok
    relatorio["segundos"] = round(time.time() - inicio, 2)
    divergentes = {t: i for t, i in relatorio["conferencia"].items() if i["divergencias"]}
    orfaos = {k: v for k, v in relatorio["fks"].items() if v}
    print(f"conferência: {len(relatorio['conferencia'])} tabelas, "
          f"{sum(i['origem'] for i in relatorio['conferencia'].values())} linhas na origem, "
          f"{len(divergentes)} com divergência, {len(orfaos)} FKs com órfãos")
    for t, i in divergentes.items():
        print(f"  ✗ {t}: " + "; ".join(i["divergencias"][:5]))
    for k, v in orfaos.items():
        print(f"  ✗ FK {k}: {v} órfão(s)")
    if relatorio["tabelas_ignoradas_na_origem"]:
        print(f"  ! tabelas na origem fora do modelo (não migradas): "
              f"{relatorio['tabelas_ignoradas_na_origem']}")
    if args.relatorio:
        os.makedirs(os.path.dirname(os.path.abspath(args.relatorio)), exist_ok=True)
        with open(args.relatorio, "w", encoding="utf-8") as f:
            json.dump(relatorio, f, ensure_ascii=False, indent=2, default=str)
        print(f"relatório: {args.relatorio}")
    print("RESULTADO: " + ("OK — destino idêntico à origem" if ok else "DIVERGÊNCIAS — não use este destino"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

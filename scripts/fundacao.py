"""Integridade do banco — ferramentas da Fase 0 (Fundação).

Tudo aqui é **somente leitura** sobre o banco: mede, compara e prova. Nenhuma função
deste módulo escreve no banco; a conexão é aberta em modo `ro` do SQLite, então uma
tentativa de escrita levanta erro em vez de passar despercebida.

Serve a três usos:

* registrar o estado do banco antes e depois de uma etapa (`estado_banco`);
* provar que nenhum valor herdado mudou (`comparar_estados`), inclusive quando a etapa
  acrescentou colunas novas — o digest é **por coluna**, então coluna nova aparece como
  nova e não contamina a prova das antigas;
* alimentar os testes da Fundação e o script de backup.

Sem dependência de app/, de SQLModel ou de FastAPI: só stdlib. Se a aplicação quebrar,
estas ferramentas continuam funcionando.
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/Anara-Cotacao/data/anara.db")

# Tabela de controle do Alembic: não é dado de negócio, e muda por desenho a cada
# migration. Fica fora do digest, mas é reportada à parte.
TABELAS_CONTROLE = ("alembic_version",)

NULO = "\x00NULL"
SEP = "\x1f"


# ---------------------------------------------------------------------------
# Arquivo
# ---------------------------------------------------------------------------
def _hash_arquivo(caminho: str, algoritmo: str) -> str:
    h = hashlib.new(algoritmo)
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def md5_arquivo(caminho: str) -> str:
    return _hash_arquivo(caminho, "md5")


def sha256_arquivo(caminho: str) -> str:
    return _hash_arquivo(caminho, "sha256")


# ---------------------------------------------------------------------------
# Conexão somente leitura
# ---------------------------------------------------------------------------
def conectar_ro(caminho: str = DB_PATH) -> sqlite3.Connection:
    """Conexão que o SQLite recusa usar para escrever."""
    if not os.path.exists(caminho):
        raise FileNotFoundError(caminho)
    uri = f"file:{os.path.abspath(caminho)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def tabelas(con: sqlite3.Connection) -> list:
    linhas = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [linha[0] for linha in linhas]


def colunas(con: sqlite3.Connection, tabela: str) -> list:
    return [linha["name"] for linha in con.execute(f'PRAGMA table_info("{tabela}")').fetchall()]


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------
def _normalizar(valor) -> str:
    """Texto canônico de um valor, para o hash não depender de formatação.

    `repr` de float é a representação curta que faz round-trip exato em Python — o
    digest não perde e não inventa casa decimal.
    """
    if valor is None:
        return NULO
    if isinstance(valor, float):
        return repr(valor)
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, bytes):
        return valor.hex()
    return str(valor)


def digest_tabela(con: sqlite3.Connection, tabela: str) -> dict:
    """Digest da tabela inteira e de cada coluna, com as linhas em ordem estável.

    Ordena por `id` quando existe (todas as tabelas do Anara têm), senão por todas as
    colunas — nunca por rowid, que pode ser renumerado se a tabela for reconstruída.
    """
    cols = colunas(con, tabela)
    if not cols:
        return {"linhas": 0, "colunas": [], "sha256": None, "por_coluna": {}}

    ordem = '"id"' if "id" in cols else ", ".join(f'"{c}"' for c in cols)
    lista = ", ".join(f'"{c}"' for c in cols)
    linhas = con.execute(f'SELECT {lista} FROM "{tabela}" ORDER BY {ordem}').fetchall()

    por_coluna = {c: hashlib.sha256() for c in cols}
    total = hashlib.sha256()
    for linha in linhas:
        campos = []
        for c in cols:
            texto = _normalizar(linha[c])
            por_coluna[c].update(texto.encode("utf-8") + b"\x1e")
            campos.append(texto)
        total.update(SEP.join(campos).encode("utf-8") + b"\x1e")

    return {
        "linhas": len(linhas),
        "colunas": cols,
        "sha256": total.hexdigest(),
        "por_coluna": {c: h.hexdigest() for c, h in por_coluna.items()},
    }


def estado_banco(caminho: str = DB_PATH) -> dict:
    """Retrato completo: arquivo, contagens e digest por tabela/coluna."""
    con = conectar_ro(caminho)
    try:
        nomes = tabelas(con)
        digests = {t: digest_tabela(con, t) for t in nomes if t not in TABELAS_CONTROLE}
        controle = {}
        for t in TABELAS_CONTROLE:
            if t in nomes:
                controle[t] = [dict(linha) for linha in con.execute(f'SELECT * FROM "{t}"')]
        integridade = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()

    return {
        "medido_em": datetime.now(timezone.utc).isoformat(),
        "arquivo": {
            "caminho": os.path.abspath(caminho),
            "bytes": os.path.getsize(caminho),
            "md5": md5_arquivo(caminho),
            "sha256": sha256_arquivo(caminho),
            "modificado_em": datetime.fromtimestamp(
                os.path.getmtime(caminho), timezone.utc).isoformat(),
        },
        "integridade": integridade,
        "tabelas": sorted(nomes),
        "contagens": {t: d["linhas"] for t, d in sorted(digests.items())},
        "digests": digests,
        "controle": controle,
    }


def contagens(caminho: str = DB_PATH) -> dict:
    con = conectar_ro(caminho)
    try:
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in tabelas(con) if t not in TABELAS_CONTROLE}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Comparação
# ---------------------------------------------------------------------------
def comparar_estados(antes: dict, depois: dict) -> dict:
    """Compara dois retratos e separa o que é mudança de esquema do que é mudança de dado.

    O critério que interessa ao projeto: **toda coluna que existia antes tem que ter o
    mesmo digest depois**. Coluna nova e tabela nova são listadas à parte — acrescentar
    não é alterar.
    """
    t_antes, t_depois = set(antes["digests"]), set(depois["digests"])
    resultado = {
        "tabelas_novas": sorted(t_depois - t_antes),
        "tabelas_removidas": sorted(t_antes - t_depois),
        "colunas_novas": {},
        "colunas_removidas": {},
        "colunas_alteradas": {},
        "contagens_alteradas": {},
        "arquivo_mudou": antes["arquivo"]["sha256"] != depois["arquivo"]["sha256"],
        "dados_herdados_intactos": True,
    }

    for tabela in sorted(t_antes & t_depois):
        a, d = antes["digests"][tabela], depois["digests"][tabela]
        c_antes, c_depois = set(a["colunas"]), set(d["colunas"])
        if c_depois - c_antes:
            resultado["colunas_novas"][tabela] = sorted(c_depois - c_antes)
        if c_antes - c_depois:
            resultado["colunas_removidas"][tabela] = sorted(c_antes - c_depois)
        if a["linhas"] != d["linhas"]:
            resultado["contagens_alteradas"][tabela] = {"antes": a["linhas"],
                                                        "depois": d["linhas"]}
        divergentes = [c for c in sorted(c_antes & c_depois)
                       if a["por_coluna"][c] != d["por_coluna"][c]]
        if divergentes:
            resultado["colunas_alteradas"][tabela] = divergentes

    resultado["dados_herdados_intactos"] = not (
        resultado["tabelas_removidas"] or resultado["colunas_removidas"]
        or resultado["colunas_alteradas"] or resultado["contagens_alteradas"])
    return resultado


def resumo_comparacao(comp: dict) -> str:
    linhas = []
    linhas.append("DADOS HERDADOS INTACTOS: "
                  + ("SIM" if comp["dados_herdados_intactos"] else "NÃO"))
    if comp["tabelas_novas"]:
        linhas.append(f"  tabelas novas: {', '.join(comp['tabelas_novas'])}")
    for tabela, cols in comp["colunas_novas"].items():
        linhas.append(f"  colunas novas em {tabela}: {', '.join(cols)}")
    for tabela, cols in comp["colunas_alteradas"].items():
        linhas.append(f"  ALTERADO em {tabela}: {', '.join(cols)}")
    for tabela, cont in comp["contagens_alteradas"].items():
        linhas.append(f"  CONTAGEM mudou em {tabela}: {cont['antes']} → {cont['depois']}")
    if comp["tabelas_removidas"]:
        linhas.append(f"  REMOVIDAS: {', '.join(comp['tabelas_removidas'])}")
    linhas.append("  arquivo do banco: "
                  + ("mudou (esperado quando houve migration)" if comp["arquivo_mudou"]
                     else "byte a byte idêntico"))
    return "\n".join(linhas)

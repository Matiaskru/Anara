"""Migrations incrementais e idempotentes do SQLite da Anara.

Regra do projeto: **nunca resetar o banco**. Aqui só se cria tabela nova e se acrescenta
coluna nova; nada é apagado nem renumerado. Antes de qualquer alteração de esquema é feito um
backup do arquivo do banco em `data/backups/`.

Roda sozinho no startup da aplicação (`init_db`), e também pode ser chamado direto:

    python3 -m app.migrations
"""
import os
import shutil
from datetime import datetime

from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from app.db import DB_PATH, engine
import app.models  # noqa: F401  — registra as tabelas no metadata

BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")
MAX_BACKUPS = 30


def fazer_backup(motivo: str = "migration") -> str:
    """Copia o arquivo do banco antes de mexer no esquema. Devolve o caminho do backup."""
    if not os.path.exists(DB_PATH):
        return ""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"anara.db.{motivo}-{stamp}")
    shutil.copy2(DB_PATH, destino)
    # Ordenar por DATA DE MODIFICAÇÃO, não pelo nome. O nome começa pelo motivo
    # ("exclusao", "migration", "fase0-pre"...), então a ordem alfabética fazia o motivo decidir
    # quem era apagado: um backup recém-criado com motivo de letra baixa era destruído na hora,
    # enquanto um antigo com motivo de letra alta sobrevivia. Encontrado em 03/09/2026, quando
    # a suíte apagou o próprio backup que acabara de criar.
    caminhos = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                if f.startswith("anara.db.")]
    for antigo in sorted(caminhos, key=os.path.getmtime)[:-MAX_BACKUPS]:
        os.remove(antigo)
    return destino


def _sqlite_tipo(coluna) -> str:
    tipo = coluna.type.__class__.__name__.upper()
    if "INT" in tipo or tipo == "BOOLEAN":
        return "INTEGER"
    if "FLOAT" in tipo or "NUMERIC" in tipo or "DECIMAL" in tipo:
        return "FLOAT"
    if "DATETIME" in tipo:
        return "DATETIME"
    if tipo == "DATE":
        return "DATE"
    return "VARCHAR"


def _default_sql(coluna):
    """Só usa DEFAULT quando é constante — SQLite não aceita default dinâmico em ADD COLUMN."""
    d = coluna.default
    if d is None or getattr(d, "is_callable", False):
        return None
    valor = getattr(d, "arg", None)
    if callable(valor) or valor is None:
        return None
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, (int, float)):
        return str(valor)
    if hasattr(valor, "value"):          # Enum
        return f"'{valor.value}'"
    return "'" + str(valor).replace("'", "''") + "'"


def migrar(verbose: bool = True) -> dict:
    """Cria tabelas novas e acrescenta colunas novas. Idempotente."""
    inspetor = inspect(engine)
    tabelas_existentes = set(inspetor.get_table_names())
    tabelas_novas = [t for t in SQLModel.metadata.tables if t not in tabelas_existentes]

    colunas_faltando = []
    for nome_tabela, tabela in SQLModel.metadata.tables.items():
        if nome_tabela not in tabelas_existentes:
            continue
        atuais = {c["name"] for c in inspetor.get_columns(nome_tabela)}
        for coluna in tabela.columns:
            if coluna.name not in atuais:
                colunas_faltando.append((nome_tabela, coluna))

    if not tabelas_novas and not colunas_faltando:
        return {"backup": "", "tabelas_criadas": [], "colunas_adicionadas": []}

    backup = fazer_backup()
    if verbose and backup:
        print(f"[migrations] backup do banco em {backup}")

    SQLModel.metadata.create_all(engine)

    adicionadas = []
    with engine.begin() as con:
        for nome_tabela, coluna in colunas_faltando:
            ddl = f'ALTER TABLE "{nome_tabela}" ADD COLUMN "{coluna.name}" {_sqlite_tipo(coluna)}'
            padrao = _default_sql(coluna)
            if padrao is not None:
                ddl += f" DEFAULT {padrao}"
            con.execute(text(ddl))
            adicionadas.append(f"{nome_tabela}.{coluna.name}")
            if verbose:
                print(f"[migrations] + {nome_tabela}.{coluna.name}")

    if verbose and tabelas_novas:
        print(f"[migrations] tabelas criadas: {', '.join(sorted(tabelas_novas))}")

    return {"backup": backup, "tabelas_criadas": sorted(tabelas_novas),
            "colunas_adicionadas": adicionadas}


def backfill(verbose: bool = True) -> dict:
    """Preenche o que as colunas novas precisam ter em registros antigos.

    Não altera valor comercial nenhum de cotação histórica: só numera cotações sem número e
    completa campos operacionais em branco.
    """
    from sqlmodel import Session, select

    from app.models import Cotacao, TipoFrete

    resultado = {"cotacoes_numeradas": 0, "frete_preenchido": 0}
    with Session(engine) as session:
        cotacoes = session.exec(select(Cotacao).order_by(Cotacao.id)).all()
        usados = {c.numero for c in cotacoes if c.numero}
        for c in cotacoes:
            if not c.numero:
                ano = (c.criado_em or datetime.utcnow()).year
                c.numero = _proximo_numero(ano, usados, sugestao=c.id)
                usados.add(c.numero)
                resultado["cotacoes_numeradas"] += 1
                session.add(c)
            if not c.freight_type:
                c.freight_type = TipoFrete.cif.value
                resultado["frete_preenchido"] += 1
                session.add(c)
        session.commit()
    if verbose and any(resultado.values()):
        print(f"[migrations] backfill: {resultado}")
    return resultado


def _proximo_numero(ano: int, usados: set, sugestao: int) -> str:
    """Numeração histórica preservada: usa o id da cotação como sequência, sem renumerar nada."""
    n = sugestao
    while f"ANARA-{ano}-{n:04d}" in usados:
        n += 1
    return f"ANARA-{ano}-{n:04d}"


if __name__ == "__main__":
    print(migrar())
    print(backfill())

"""Ambiente do Alembic para o banco da Anara.

Duas decisões que valem a pena estar explícitas:

1. **A URL vem do código, não do .ini.** O caminho do banco é o mesmo que a aplicação usa
   (`app.db.DB_PATH`); `ANARA_DB_URL` sobrepõe quando se quer rodar contra uma cópia
   (ensaio de restore, teste, banco temporário). Ninguém migra o banco errado por ter
   esquecido de editar um arquivo de configuração.

2. **`render_as_batch` só no SQLite.** Ele não tem ALTER TABLE completo; o modo batch do
   Alembic recria a tabela e copia os dados quando é preciso alterar ou remover coluna.
   Acrescentar coluna continua sendo ALTER simples. No PostgreSQL o ALTER é nativo e o
   batch é desligado — `op.batch_alter_table` nas migrations vira ALTER direto.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.models  # noqa: F401,E402  — registra todas as tabelas no metadata
from app.db import DB_URL, E_SQLITE  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A MESMA resolução da aplicação (`ANARA_DB_URL` > `DATABASE_URL` > SQLite local), com a
# normalização de `postgres://` inclusa. `%` é escapado porque o ConfigParser do Alembic
# interpola — uma senha com `%` quebraria a URL em silêncio.
config.set_main_option("sqlalchemy.url", DB_URL.replace("%", "%%"))

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=E_SQLITE)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=E_SQLITE, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

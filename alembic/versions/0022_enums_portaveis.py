"""Enums como VARCHAR — o mesmo esquema no SQLite e no PostgreSQL.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-17

Por que existe: `fornecedor.tipo`, `fornecedor.cost_method_padrao` e `cotacao.status` foram
declaradas em `0001` como `sa.Enum(..., name=...)`. No SQLite isso sempre foi um `VARCHAR`
sem restrição — e foi assim que `CostMethod` ganhou seis métodos e `StatusCotacao` ganhou o
workflow da Sessão 6 sem migration alguma. No PostgreSQL a mesma declaração cria um **tipo
ENUM nativo** congelado na lista de 2026-09-01: `daune_direct`, `aguardando_aprovacao`,
`emitida`… seriam recusados na primeira gravação.

Esta migration alinha o PostgreSQL ao que o SQLite sempre foi: `VARCHAR(64)`, com o valor
gravado sendo o **nome** do membro (`importado_ktc`, `rascunho`), exatamente como está no
banco histórico. Os modelos passaram a declarar `Enum(native_enum=False, length=64)`, e a
conversão de/para o `Enum` do Python continua no SQLAlchemy.

**No SQLite é no-op** — nada a converter, nada a recriar, nenhum dado tocado.
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

#: (tabela, coluna, tipo enum nativo criado por 0001)
COLUNAS = (
    ("fornecedor", "tipo", "tipofornecedor"),
    ("fornecedor", "cost_method_padrao", "costmethod"),
    ("cotacao", "status", "statuscotacao"),
)

#: Os nomes de membro de cada enum HOJE — só para o downgrade recriar o tipo sem perder
#: linha. Espelha `app.models`; se um enum ganhar membro, o downgrade é que envelhece, não
#: o upgrade.
MEMBROS = {
    "tipofornecedor": ("importado_ktc", "nacional", "outro"),
    "costmethod": ("ktc_calculated", "ktc_quoted", "ktc_special_quoted",
                   "ktc_estimated_from_quotes", "daune_direct", "decor_direct",
                   "national_supplier", "a_cotar_ktc", "a_cotar_nacional", "manual",
                   "legacy_excel"),
    "statuscotacao": ("rascunho", "aguardando_aprovacao", "aprovada", "emitida", "enviada",
                      "cancelada", "fechada", "pedido", "perdida"),
}


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    for tabela, coluna, tipo in COLUNAS:
        op.execute(f'ALTER TABLE "{tabela}" ALTER COLUMN "{coluna}" TYPE VARCHAR(64) '
                   f'USING "{coluna}"::text')
        op.execute(f'DROP TYPE IF EXISTS "{tipo}"')


def downgrade() -> None:
    if not _postgres():
        return
    for tabela, coluna, tipo in COLUNAS:
        valores = ", ".join(f"'{m}'" for m in MEMBROS[tipo])
        op.execute(f'CREATE TYPE "{tipo}" AS ENUM ({valores})')
        op.execute(f'ALTER TABLE "{tabela}" ALTER COLUMN "{coluna}" TYPE "{tipo}" '
                   f'USING "{coluna}"::"{tipo}"')

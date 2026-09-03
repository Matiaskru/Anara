"""referência de custo versionada, métodos e status canônicos

Sessão 2. Torna a referência de custo de cada SKU **versionada e não destrutiva**:

* `custoreferencia` ganha `versao`, `valid_from`, `valid_to`, `vigente`, `metodo_custo`,
  `status_custo`, `confirmation_pending`, `valor_bruto`, `cnet_brl`, `memoria_calculo`,
  `origem_registro` e `substitui_versao`;
* `produto` ganha `status_custo` como cache do status da versão vigente.

**Nada é preenchido.** As 105 linhas herdadas ficam com as colunas novas nulas — elas são
observações do modelo antigo e serão classificadas pela reconciliação SKU a SKU, nunca
convertidas por atalho. `Produto.custo_confianca` permanece intocado pelo mesmo motivo:
`legacy REVIEW_REQUIRED` não é o `REVIEW_REQUIRED` canônico.

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUNAS = {
    "custoreferencia": [
        ("versao", sa.Integer()),
        ("valid_from", sa.Date()),
        ("valid_to", sa.Date()),
        ("vigente", sa.Boolean()),
        ("metodo_custo", sqlmodel.sql.sqltypes.AutoString()),
        ("status_custo", sqlmodel.sql.sqltypes.AutoString()),
        ("confirmation_pending", sa.Boolean()),
        ("valor_bruto", sa.Float()),
        ("cnet_brl", sa.Float()),
        ("memoria_calculo", sqlmodel.sql.sqltypes.AutoString()),
        ("origem_registro", sqlmodel.sql.sqltypes.AutoString()),
        ("substitui_versao", sa.Integer()),
    ],
    "produto": [("status_custo", sqlmodel.sql.sqltypes.AutoString())],
}


def upgrade() -> None:
    for tabela, colunas in COLUNAS.items():
        for nome, tipo in colunas:
            op.add_column(tabela, sa.Column(nome, tipo, nullable=True))
    op.create_index("ix_custoreferencia_versao", "custoreferencia", ["versao"], unique=False)
    op.create_index("ix_custoreferencia_vigente", "custoreferencia", ["vigente"], unique=False)
    op.create_index("ix_custoreferencia_status_custo", "custoreferencia", ["status_custo"],
                    unique=False)


def downgrade() -> None:
    op.drop_index("ix_custoreferencia_status_custo", table_name="custoreferencia")
    op.drop_index("ix_custoreferencia_vigente", table_name="custoreferencia")
    op.drop_index("ix_custoreferencia_versao", table_name="custoreferencia")
    for tabela, colunas in COLUNAS.items():
        with op.batch_alter_table(tabela, schema=None) as batch_op:
            for nome, _tipo in reversed(colunas):
                batch_op.drop_column(nome)

"""DIFAL sobre a receita final e FCP configurável

Onda 1, fechamento. Duas correções de semântica:

1. A carga do DIFAL passa a ser `interna_destino − interestadual`, sobre a **mesma base** que o
   resto do waterfall usa — o preço final. `EstadoFiscal.carga_final` sai do motor: ela é o
   mesmo diferencial convertido para uma base anterior à inclusão do ICMS de destino
   (`(interna − 4%)/(1 − interna)`) e não é um percentual da receita final. A coluna e a tabela
   permanecem, para rastrear a apuração histórica.

2. O FCP/FEM deixa de ser lido da coluna `EstadoFiscal.fem`, que é por UF. Passa a exigir linha
   em `regrafcp`, com produto, NCM, família e vigência. A tabela nasce **vazia**: nenhuma
   aplicabilidade está comprovada hoje, e inferir seria inventar premissa fiscal.

Nada é preenchido em `cotacaoitem`: os itens históricos seguem com NULL nas colunas fiscais.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUNAS_ITEM = [
    ("aliquota_interestadual", sa.Float()),
    ("aliquota_interna_destino", sa.Float()),
    ("fcp_pct", sa.Float()),
]


def upgrade() -> None:
    op.create_table(
        "regrafcp",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uf_destino", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ncm", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("produto_id", sa.Integer(), nullable=True),
        sa.Column("familia", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fcp_pct", sa.Float(), nullable=False),
        sa.Column("exige_confirmacao", sa.Boolean(), nullable=False),
        sa.Column("prioridade", sa.Integer(), nullable=False),
        sa.Column("regra", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("fonte", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notas", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["produto_id"], ["produto.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_regrafcp_uf_destino", "regrafcp", ["uf_destino"], unique=False)
    op.create_index("ix_regrafcp_ncm", "regrafcp", ["ncm"], unique=False)
    for nome, tipo in COLUNAS_ITEM:
        op.add_column("cotacaoitem", sa.Column(nome, tipo, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cotacaoitem", schema=None) as batch_op:
        for nome, _ in reversed(COLUNAS_ITEM):
            batch_op.drop_column(nome)
    op.drop_index("ix_regrafcp_ncm", table_name="regrafcp")
    op.drop_index("ix_regrafcp_uf_destino", table_name="regrafcp")
    op.drop_table("regrafcp")

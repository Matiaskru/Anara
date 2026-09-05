"""Pinning das premissas no item de cotação (Sessão 5 — correção)

O item já guardava os **valores** que formaram o preço, e isso sempre protegeu o dinheiro.
O que faltava era a **identidade**: qual versão, exatamente, produziu aquele número.

Sem estas colunas, responder "de onde veio este preço" dependia de perguntar ao resolvedor
qual versão estaria valendo naquela data — e uma versão cadastrada depois, com vigência
retroativa, mudaria a resposta. O preço continuaria certo; a genealogia, não.

Aditiva, tudo nulo. Os 45 itens históricos ficam com as colunas NULAS: pinar retroativamente
seria **adivinhar** qual versão formou um preço de 2026, e adivinhação é exatamente o que
estas colunas existem para eliminar. Para eles continua valendo o snapshot de valores em
`memoria_json`, que é o que sempre os protegeu.

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("custo_referencia_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("custo_referencia_versao", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("margem_regra_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("condicao_pagamento_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("aliquota_interestadual_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("premissas_pinadas", sa.String(), nullable=True))
    op.create_index("ix_cotacaoitem_custo_referencia_id", "cotacaoitem",
                    ["custo_referencia_id"], unique=False)


def downgrade():
    op.drop_index("ix_cotacaoitem_custo_referencia_id", table_name="cotacaoitem")
    with op.batch_alter_table("cotacaoitem") as batch:
        for coluna in ("premissas_pinadas", "aliquota_interestadual_id",
                       "condicao_pagamento_id", "margem_regra_id",
                       "custo_referencia_versao", "custo_referencia_id"):
            batch.drop_column(coluna)

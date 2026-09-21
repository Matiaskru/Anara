"""Sinal / entrada como composição da condição de pagamento (21/09/2026)

Aditiva e reversível. Nenhuma coluna existente muda de tipo ou de significado.

`cotacao.percentual_sinal`: fração (0–1) paga à vista, sem encargo. Default 0 para TODA
cotação existente — a condição de pagamento que ela já tinha é preservada como está, e
`encargo_efetivo = (1 − 0) × encargo` é exatamente o encargo de sempre. Nada é reprecificado.

`cotacaoitem.percentual_sinal` e `cotacaoitem.encargo_saldo_pct`: o que formou o
`encargo_pct` do item quando havia sinal (fração e encargo da condição do saldo antes da
proporção). Nulos nos itens anteriores.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacao") as batch:
        batch.add_column(sa.Column("percentual_sinal", sa.Float(), nullable=False,
                                   server_default="0"))
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("percentual_sinal", sa.Float(), nullable=True))
        batch.add_column(sa.Column("encargo_saldo_pct", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("encargo_saldo_pct")
        batch.drop_column("percentual_sinal")
    with op.batch_alter_table("cotacao") as batch:
        batch.drop_column("percentual_sinal")

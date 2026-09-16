"""Observação para o cliente na cotação (Fase 3C)

Aditiva. `cotacao.observacao_cliente` é o texto que PODE ir ao PDF; `cotacao.observacoes`
passa a ser formalmente interna e nunca vai ao documento. Nada é copiado de uma para a
outra: uma anotação antiga só vira texto de proposta por decisão de quem a reler.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacao") as batch:
        batch.add_column(sa.Column("observacao_cliente", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacao") as batch:
        batch.drop_column("observacao_cliente")

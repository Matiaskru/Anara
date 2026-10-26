"""Cancelar acompanhamento (19/10/2026)

Aditiva. O acompanhamento criado por engano (ou que perdeu o sentido) sai da lista de pendentes
sem ser marcado como feito: `atividadecomercial.cancelada_em`, `cancelada_por` e
`motivo_cancelamento`. Nada é apagado — cancelado continua no histórico, como o concluído.

Nenhuma coluna existente é alterada ou removida.

Revision ID: 0032
Revises: 0031
"""
import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("atividadecomercial", sa.Column("cancelada_em", sa.DateTime(), nullable=True))
    op.add_column("atividadecomercial", sa.Column("cancelada_por", sa.String(), nullable=True))
    op.add_column("atividadecomercial", sa.Column("motivo_cancelamento", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("atividadecomercial") as b:
        b.drop_column("motivo_cancelamento")
        b.drop_column("cancelada_por")
        b.drop_column("cancelada_em")

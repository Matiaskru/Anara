"""Endereço do cliente (10/11/2026)

Aditiva. O relatório de visita não tinha onde mostrar o endereço da empresa visitada: o cadastro
só guardava cidade/UF (que é o destino fiscal das cotações). `cliente.endereco` é texto livre
(rua, número, bairro, CEP) — informativo, não entra em cálculo nenhum.

Nenhuma coluna existente é alterada ou removida.

Revision ID: 0033
Revises: 0032
"""
import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cliente", sa.Column("endereco", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("cliente") as b:
        b.drop_column("endereco")

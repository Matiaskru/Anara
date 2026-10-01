"""Amostra avulsa — peça que não existe no catálogo (01/10/2026)

Aditiva. `amostraproduto.produto_id` passa a aceitar vazio (só a amostra avulsa o deixa
vazio) e nascem `nome` e `especificacao`, usados apenas por ela. Nenhuma linha existente
muda: toda amostra já cadastrada continua ligada ao seu produto.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("amostraproduto") as batch:
        batch.alter_column("produto_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("nome", sa.String(), nullable=True))
        batch.add_column(sa.Column("especificacao", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("amostraproduto") as batch:
        batch.drop_column("especificacao")
        batch.drop_column("nome")
        batch.alter_column("produto_id", existing_type=sa.Integer(), nullable=False)

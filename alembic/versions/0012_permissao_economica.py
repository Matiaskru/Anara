"""Permissão granular de gestão econômica (Sessão 5)

`usuario.can_manage_economics` separa **ver** a economia de **poder alterá-la**. Um
administrativo consulta a formação do preço o dia inteiro sem precisar da caneta que troca o
custo de um SKU.

Vem `True` por default para não tirar acesso de ninguém: hoje não há um segundo administrador
de quem separar, e a flag existe para quando houver. OWNER ignora a flag — sempre pode.

Aditiva. Nenhum valor econômico é tocado.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usuario") as batch:
        batch.add_column(sa.Column("can_manage_economics", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("usuario") as batch:
        batch.drop_column("can_manage_economics")

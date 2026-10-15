"""Comissão fixa por regra de margem (13/10/2026) — Daune e Decor

Aditiva. `margemregra.comissao_fixa` (falso por padrão): quando verdadeira, a comissão do item
é a comissão de formação da regra (5%) em qualquer preço, sem a escada de desconto da política
de 21/09. `cotacaoitem.comissao_fixa` pina a marca no item (NULO = escada normal — todo item já
existente continua exatamente como está, inclusive no fingerprint).

Nenhuma linha muda. Ligar a marca para Daune/Decor é ato de dados (nova versão da regra pelo
admin_service), não desta migration.

Revision ID: 0029
Revises: 0028
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("margemregra") as batch:
        batch.add_column(sa.Column("comissao_fixa", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("comissao_fixa", sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("comissao_fixa")
    with op.batch_alter_table("margemregra") as batch:
        batch.drop_column("comissao_fixa")

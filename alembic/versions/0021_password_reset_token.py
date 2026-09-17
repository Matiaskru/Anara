"""Token de redefinição de senha (hardening de acesso, 17/09/2026)

Aditiva. Nasce `passwordresettoken`: só o HASH do token, com finalidade, criação,
expiração e uso. Nada em `usuario` muda; nenhuma tabela comercial é tocada.

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "passwordresettoken",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("finalidade", sa.String(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("expira_em", sa.DateTime(), nullable=False),
        sa.Column("usado_em", sa.DateTime(), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passwordresettoken_usuario_id", "passwordresettoken", ["usuario_id"])
    op.create_index("ix_passwordresettoken_token_hash", "passwordresettoken", ["token_hash"],
                    unique=True)


def downgrade():
    op.drop_index("ix_passwordresettoken_token_hash", table_name="passwordresettoken")
    op.drop_index("ix_passwordresettoken_usuario_id", table_name="passwordresettoken")
    op.drop_table("passwordresettoken")

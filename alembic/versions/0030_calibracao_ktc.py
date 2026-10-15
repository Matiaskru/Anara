"""Calibração do EXW da fórmula KTC por construção (13/10/2026)

Aditiva: tabela `calibracaoktc` (fator por construção/grupo, com vigência e evidência; ou
bloqueio da construção quando há evidência de que a fórmula é inadequada). Vazia,
o motor se comporta exatamente como antes (EXW de fórmula pura → ESTIMADO). Os fatores entram
por script de dados (`sincronizar_calibracao_rev3.py`), não por esta migration.

Revision ID: 0030
Revises: 0029
"""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calibracaoktc",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("construcao", sa.String(), nullable=False),
        sa.Column("grupo", sa.String(), nullable=False),
        sa.Column("familias", sa.String(), nullable=False),
        sa.Column("material", sa.String(), nullable=True),
        sa.Column("plain_or_stripe", sa.String(), nullable=True),
        sa.Column("abas", sa.Integer(), nullable=True),
        sa.Column("fator_base", sa.Float(), nullable=False),
        sa.Column("seguranca_pct", sa.Float(), nullable=False),
        sa.Column("fator_final", sa.Float(), nullable=False),
        sa.Column("n_ancoras", sa.Integer(), nullable=False),
        sa.Column("ratio_min", sa.Float(), nullable=True),
        sa.Column("ratio_media", sa.Float(), nullable=True),
        sa.Column("ratio_mediana", sa.Float(), nullable=True),
        sa.Column("ratio_max", sa.Float(), nullable=True),
        sa.Column("dispersao", sa.Float(), nullable=True),
        sa.Column("qa_pior_erro", sa.Float(), nullable=True),
        sa.Column("ancoras_json", sa.String(), nullable=True),
        sa.Column("bloqueio", sa.String(), nullable=True),
        sa.Column("provisoria", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("fonte", sa.String(), nullable=True),
        sa.Column("notas", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calibracaoktc_construcao", "calibracaoktc", ["construcao"])


def downgrade():
    op.drop_index("ix_calibracaoktc_construcao", table_name="calibracaoktc")
    op.drop_table("calibracaoktc")

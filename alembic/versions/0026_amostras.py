"""Controle de amostras (01/10/2026)

Aditiva. Nascem duas tabelas e nada existente muda:

* `amostraproduto` — qual produto DO CATÁLOGO é controlado como amostra (um por produto:
  índice único em `produto_id`). Não guarda saldo.
* `amostramovimentacao` — append-only: entrada, saída, retorno, baixa e ajuste. O saldo
  (disponível / em circulação) é derivado daqui, nunca de um campo editável.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "amostraproduto",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("observacao", sa.String(), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["produto_id"], ["produto.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_amostraproduto_produto_id", "amostraproduto", ["produto_id"], unique=True)

    op.create_table(
        "amostramovimentacao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amostra_produto_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("cliente_texto", sa.String(), nullable=True),
        sa.Column("motivo", sa.String(), nullable=True),
        sa.Column("observacao", sa.String(), nullable=True),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("usuario_nome", sa.String(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["amostra_produto_id"], ["amostraproduto.id"]),
        sa.ForeignKeyConstraint(["cliente_id"], ["cliente.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_amostramovimentacao_amostra_produto_id", "amostramovimentacao",
                    ["amostra_produto_id"])
    op.create_index("ix_amostramovimentacao_criado_em", "amostramovimentacao", ["criado_em"])


def downgrade():
    op.drop_index("ix_amostramovimentacao_criado_em", table_name="amostramovimentacao")
    op.drop_index("ix_amostramovimentacao_amostra_produto_id", table_name="amostramovimentacao")
    op.drop_table("amostramovimentacao")
    op.drop_index("ix_amostraproduto_produto_id", table_name="amostraproduto")
    op.drop_table("amostraproduto")

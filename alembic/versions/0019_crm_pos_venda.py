"""CRM comercial simples e pós-venda (Fase 3B)

Aditiva. `oportunidade` ganha os campos de pós-venda — status, entrega, faturamento,
documento fiscal, pagamento, observação —, todos nulos no que existe (0 oportunidades no
banco real na entrada). Nasce `atualizacaocomercial`, append-only.

As etapas do funil mudaram de vocabulário (RASCUNHO/ENVIADO/NEGOCIACAO) sem mudança de
esquema: `oportunidade.etapa` continua texto, e os valores anteriores ficam legíveis.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("oportunidade") as batch:
        batch.add_column(sa.Column("status_pos_venda", sa.String(), nullable=True))
        batch.add_column(sa.Column("entrega_prevista_em", sa.Date(), nullable=True))
        batch.add_column(sa.Column("entregue_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("faturado_em", sa.Date(), nullable=True))
        batch.add_column(sa.Column("numero_documento_fiscal", sa.String(), nullable=True))
        batch.add_column(sa.Column("pagamento_previsto_em", sa.Date(), nullable=True))
        batch.add_column(sa.Column("pago_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("observacao_pos_venda", sa.String(), nullable=True))
    op.create_index("ix_oportunidade_status_pos_venda", "oportunidade", ["status_pos_venda"])
    op.create_table(
        "atualizacaocomercial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("oportunidade_id", sa.Integer(), nullable=False),
        sa.Column("autor_id", sa.Integer(), nullable=True),
        sa.Column("autor_email", sa.String(), nullable=True),
        sa.Column("texto", sa.String(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["oportunidade_id"], ["oportunidade.id"]),
        sa.ForeignKeyConstraint(["autor_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_atualizacaocomercial_oportunidade_id", "atualizacaocomercial",
                    ["oportunidade_id"])
    op.create_index("ix_atualizacaocomercial_criado_em", "atualizacaocomercial", ["criado_em"])


def downgrade():
    op.drop_index("ix_atualizacaocomercial_criado_em", table_name="atualizacaocomercial")
    op.drop_index("ix_atualizacaocomercial_oportunidade_id", table_name="atualizacaocomercial")
    op.drop_table("atualizacaocomercial")
    op.drop_index("ix_oportunidade_status_pos_venda", table_name="oportunidade")
    with op.batch_alter_table("oportunidade") as batch:
        for c in ("observacao_pos_venda", "pago_em", "pagamento_previsto_em",
                  "numero_documento_fiscal", "faturado_em", "entregue_em",
                  "entrega_prevista_em", "status_pos_venda"):
            batch.drop_column(c)

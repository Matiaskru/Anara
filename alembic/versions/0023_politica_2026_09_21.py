"""Política comercial de 21/09/2026 — B2B, tabela, desconto, comissão por item, frete manual

Aditiva. Nenhuma coluna existente muda de tipo ou de significado.

`cotacaoitem`: a decomposição da política nova congelada no item — tabela (2 × B2B),
desconto negociado (a alavanca que sobrevive à mudança de cenário), o que foi digitado,
faixa de comissão, base comissionável e a parcela de ICMS deduzida dela.

`cotacao`: frete CIF informado manualmente e confirmado (valor já existia em
`freight_valor`); quem confirmou, quando e a observação.

Itens anteriores ficam com tudo nulo e continuam sendo avaliados pela política que
pinaram (`politica_comercial`). Nada é reprecificado por esta migration.

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("preco_tabela", sa.Float(), nullable=True))
        batch.add_column(sa.Column("desconto_vs_tabela_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("modo_negociacao", sa.String(), nullable=True))
        batch.add_column(sa.Column("desconto_editado_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("base_comissionavel", sa.Float(), nullable=True))
        batch.add_column(sa.Column("icms_base_comissao_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("comissao_faixa_pct", sa.Float(), nullable=True))
    with op.batch_alter_table("cotacao") as batch:
        batch.add_column(sa.Column("freight_manual_confirmado", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("freight_manual_por", sa.String(), nullable=True))
        batch.add_column(sa.Column("freight_manual_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("freight_manual_obs", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacao") as batch:
        batch.drop_column("freight_manual_obs")
        batch.drop_column("freight_manual_em")
        batch.drop_column("freight_manual_por")
        batch.drop_column("freight_manual_confirmado")
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("comissao_faixa_pct")
        batch.drop_column("icms_base_comissao_pct")
        batch.drop_column("base_comissionavel")
        batch.drop_column("desconto_editado_pct")
        batch.drop_column("modo_negociacao")
        batch.drop_column("desconto_vs_tabela_pct")
        batch.drop_column("preco_tabela")

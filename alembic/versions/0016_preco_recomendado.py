"""Preço recomendado do cenário, no item (Sessão 6)

`preco_base` é a referência do **catálogo**: formada com o cenário padrão, outro destino
fiscal e outra condição de pagamento. Usá-lo como "preço recomendado" faria toda venda
interestadual parecer desconto, e o aprovador seria chamado para autorizar uma exceção que
não existe.

`preco_recomendado` é o que o motor forma **para o cenário desta cotação**, na margem-alvo
do item. É contra ele que o desconto é medido.

Aditiva. Nulo no histórico: recalculá-lo para cotações de 2026 exigiria refazer o cenário
fiscal daquela data, e o resultado não seria o recomendado de então — seria o de hoje.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("preco_recomendado", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("preco_recomendado")

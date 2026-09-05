"""Confiança do custo congelada no item (Sessão 6)

O workflow precisa saber **como** o custo foi obtido, não só quanto ele é: `A_COTAR` não
forma preço, `ESTIMADO` forma proposta mas não compromisso firme, e `REVALIDAR` pede
reconfirmação antes de assumir obrigação.

Ler isso do produto no momento da emissão daria a resposta de **hoje** para uma pergunta
sobre **ontem** — o mesmo problema que o pinning da Sessão 5 resolveu para o valor.

Aditiva. Os 45 itens históricos ficam com `status_custo_item` NULO: inferi-lo agora seria
adivinhar em que estado de confiança estava um custo de 2026.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("status_custo_item", sa.String(), nullable=True))
        batch.add_column(sa.Column("confirmation_pending", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("confirmation_pending")
        batch.drop_column("status_custo_item")

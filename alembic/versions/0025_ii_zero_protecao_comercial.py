"""I.I. econômico KTC = 0% · proteção comercial de precificação separada do custo (22/09/2026)

Aditiva e reversível. Nenhuma coluna existente muda de tipo ou de significado.

`produto.protecao_comercial_pct` / `protecao_comercial_fonte`: a alíquota de I.I. preferencial
que formava o custo do SKU até 22/09/2026, preservada como fator de FORMAÇÃO DE PREÇO — não é
tributo, custo nem despesa; existe para que B2B, tabela e preco_base fiquem exatamente iguais.

`cotacaoitem.base_comercial_precificacao` / `protecao_comercial_pct` / `preco_b2b_economico`:
o que formou o B2B comercial do item (referência comercial em R$ e a proteção usada) e o B2B
que o CNET real daria (diagnóstico). `custo_unitario` continua sendo o custo — agora o real.

Itens e produtos anteriores ficam nulos: nada é reprecificado por esta migration; o script de
dados pina a proteção por SKU a partir da alíquota que cada um efetivamente usava.

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("produto") as batch:
        batch.add_column(sa.Column("protecao_comercial_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("protecao_comercial_fonte", sa.String(), nullable=True))
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("base_comercial_precificacao", sa.Float(), nullable=True))
        batch.add_column(sa.Column("protecao_comercial_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("preco_b2b_economico", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("preco_b2b_economico")
        batch.drop_column("protecao_comercial_pct")
        batch.drop_column("base_comercial_precificacao")
    with op.batch_alter_table("produto") as batch:
        batch.drop_column("protecao_comercial_fonte")
        batch.drop_column("protecao_comercial_pct")

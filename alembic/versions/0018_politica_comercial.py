"""Política comercial canônica — margem, piso, comissão de formação e preço travado (Fase 3A)

Decisão comercial ANARA de 16/09/2026. Aditiva: seis colunas em `margemregra` e quatro em
`cotacaoitem`, todas nulas (ou `false`) no que já existe.

`MargemRegra` passa a carregar a política inteira do escopo — margem-alvo, **piso** de
autonomia da vendedora, comissão que **forma** o preço recomendado, preço **travado** —, mais
a margem que a regra sucedeu e a fonte, para que "qual era, qual passou a ser, por decisão de
quem" seja resposta de coluna e não de memória. As 21 regras anteriores não são reescritas:
ganham `valid_to` pelo script de aplicação e continuam interpretando os itens que as pinaram.

`CotacaoItem` congela a política que formou o preço dele (`piso_margem_pct`,
`comissao_formacao_pct`, `preco_travado`, `politica_comercial`), como já congela o fiscal e
os pinos. Os itens históricos ficam NULOS: o workflow os avalia com a semântica anterior e a
detecção de premissa desatualizada os aponta. Nenhum preço muda.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("margemregra") as batch:
        batch.add_column(sa.Column("piso_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("comissao_formacao_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("preco_travado", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("margem_anterior_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("politica", sa.String(), nullable=True))
        batch.add_column(sa.Column("fonte", sa.String(), nullable=True))
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.add_column(sa.Column("piso_margem_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("comissao_formacao_pct", sa.Float(), nullable=True))
        batch.add_column(sa.Column("preco_travado", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("politica_comercial", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("cotacaoitem") as batch:
        batch.drop_column("politica_comercial")
        batch.drop_column("preco_travado")
        batch.drop_column("comissao_formacao_pct")
        batch.drop_column("piso_margem_pct")
    with op.batch_alter_table("margemregra") as batch:
        batch.drop_column("fonte")
        batch.drop_column("politica")
        batch.drop_column("margem_anterior_pct")
        batch.drop_column("preco_travado")
        batch.drop_column("comissao_formacao_pct")
        batch.drop_column("piso_pct")

"""Logística por caixa / CBM no produto (09/10/2026)

Aditiva e só estrutura. Nove colunas NULAS em `produto` para o rateio logístico futuro por
CBM / peso cubado / peso taxável (roupões, cobertores, travesseiros, edredons, pillow tops).
Nenhuma linha muda, nenhum valor é preenchido e nada no motor de preço lê estas colunas: o
cálculo continua por peso real/estimado. Dimensão de caixa entra quando vier da packing list.

Revision ID: 0028
Revises: 0027
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

COLUNAS = [
    ("quantity_per_carton", sa.Integer()),
    ("carton_length_cm", sa.Float()),
    ("carton_width_cm", sa.Float()),
    ("carton_height_cm", sa.Float()),
    ("carton_gross_weight_kg", sa.Float()),
    ("cbm_per_carton", sa.Float()),
    ("cbm_per_unit", sa.Float()),
    ("volumetric_weight", sa.Float()),
    ("chargeable_weight", sa.Float()),
]


def upgrade():
    with op.batch_alter_table("produto") as batch:
        for nome, tipo in COLUNAS:
            batch.add_column(sa.Column(nome, tipo, nullable=True))


def downgrade():
    with op.batch_alter_table("produto") as batch:
        for nome, _tipo in reversed(COLUNAS):
            batch.drop_column(nome)

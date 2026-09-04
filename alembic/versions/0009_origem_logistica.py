"""origem logística do fornecedor

Sessão 3A. `origem_logistica_cidade` e `origem_logistica_uf` em `fornecedor`.

Origem logística é de onde a carga **embarca**; origem fiscal é de onde a **NF sai**. São
coisas diferentes e o frete depende da primeira. Só a KTC recebe valor: Itajaí-SC é o ponto de
entrada da importação e é a origem declarada pela própria tabela TRANSAL.

Daune e Decor ficam **NULAS de propósito**. Não se sabe de onde elas embarcam, e usar a tabela
TRANSAL-Itajaí para elas só porque é a única cadastrada produziria um frete inventado. Sem
origem compatível, o grupo vai para `FRETE_A_COTAR`.

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fornecedor", sa.Column("origem_logistica_cidade",
                                          sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column("fornecedor", sa.Column("origem_logistica_uf",
                                          sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.get_bind().execute(sa.text(
        "UPDATE fornecedor SET origem_logistica_cidade = 'Itajaí', origem_logistica_uf = 'SC' "
        "WHERE codigo = 'KTC'"))


def downgrade() -> None:
    with op.batch_alter_table("fornecedor", schema=None) as batch_op:
        batch_op.drop_column("origem_logistica_uf")
        batch_op.drop_column("origem_logistica_cidade")

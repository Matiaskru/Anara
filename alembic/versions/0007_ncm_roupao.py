"""NCM de roupão — 6309 sai, 6208.91/92 entra

Sessão 2. `6309.00.10` é a posição de **artigos usados**: classificar roupão novo ali é erro de
classificação aduaneira, com risco de autuação e de perda do tratamento preferencial. Passa a:

* **6208.91.00** — 100% algodão;
* **6208.92.00** — fibras sintéticas ou artificiais.

O que **não** muda, e é o ponto delicado: o **I.I. preferencial de 3,5%** é override de
**família**, com prioridade 10, e por isso é imune à troca do código NCM. A migration altera o
código e deixa `ii_preferencial` exatamente onde está — há teste provando que a precedência
sobrevive à troca.

As duas regras deixam de estar marcadas `confiavel = 0`: o motivo daquele alerta era justamente
o 6309 suspeito.

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOTA = ("NCM corrigido na Sessão 2: 6309.00.10 é posição de artigos usados e não serve a "
        "mercadoria nova. I.I. preferencial de 3,5% preservado como override de família.")


def upgrade() -> None:
    con = op.get_bind()
    con.execute(sa.text(
        "UPDATE ncmregra SET ncm = '6208.91.00', confiavel = :sim, "
        "descricao_ncm = 'Roupões de algodão — 6208.91.00', notas = :n "
        "WHERE ncm = '6309.00.10'"), {"n": NOTA, "sim": True})
    # O NCM gravado nos produtos acompanha; o I.I. do produto NÃO é tocado.
    con.execute(sa.text(
        "UPDATE produto SET ncm = '6208.91.00' WHERE ncm = '6309.00.10'"))


def downgrade() -> None:
    con = op.get_bind()
    con.execute(sa.text(
        "UPDATE produto SET ncm = '6309.00.10' WHERE ncm = '6208.91.00'"))
    con.execute(sa.text(
        "UPDATE ncmregra SET ncm = '6309.00.10', confiavel = :nao WHERE ncm = '6208.91.00'"),
        {"nao": False})

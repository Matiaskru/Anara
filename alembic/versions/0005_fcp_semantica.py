"""semântica do FCP/FECP — base interna separada e ausência de regra != 0%

Onda 1, último P0. Duas correções:

1. `EstadoFiscal.aliquota_interna` não significa a mesma coisa em todas as UFs. Auditoria de
   03/09/2026: no RJ ela vale 22%, que é a base de 20% **mais** o FECP de 2% — e a coluna `fem`
   repete os mesmos 2%. O legado somava as duas, contando o FECP duas vezes. Ganha coluna
   própria `icms_interno_base` (base sem FCP) e `interna_inclui_fcp` para declarar a semântica.
   Nas UFs onde nada disso foi determinado, ambas ficam NULAS e o cenário bloqueia.

2. `regrafcp.exige_confirmacao` (booleano) vira `situacao` com três valores: APLICA,
   NAO_APLICA e DESCONHECIDO. **Ausência de linha passa a ser DESCONHECIDO, não 0%.** Zero é
   uma afirmação e precisa de fonte.

Semeia apenas o **RJ**, que é a única UF cuja composição a regra canônica fixou: base 20%,
FECP 2%, total 22%. Nenhuma outra é generalizada a partir dele — BA, PE e PI têm `fem` na tabela
legada e ficam DESCONHECIDO até que produto e vigência sejam verificados.

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FONTE_RJ = "Regra canônica Anara 03/09/2026 — RJ: ICMS 20% + FECP 2% = 22%"


def upgrade() -> None:
    op.add_column("estadofiscal", sa.Column("icms_interno_base", sa.Float(), nullable=True))
    op.add_column("estadofiscal", sa.Column("interna_inclui_fcp", sa.Boolean(), nullable=True))
    op.add_column("regrafcp", sa.Column(
        "situacao", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    con = op.get_bind()
    con.execute(sa.text(
        "UPDATE regrafcp SET situacao = CASE WHEN exige_confirmacao = :sim THEN 'DESCONHECIDO' "
        "ELSE 'APLICA' END WHERE situacao IS NULL"), {"sim": True})
    with op.batch_alter_table("regrafcp", schema=None) as batch_op:
        batch_op.drop_column("exige_confirmacao")

    # RJ é a única UF com composição fixada pela regra canônica.
    con.execute(sa.text(
        "UPDATE estadofiscal SET icms_interno_base = 0.20, interna_inclui_fcp = :sim "
        "WHERE uf = 'RJ'"), {"sim": True})
    con.execute(sa.text(
        "INSERT INTO regrafcp (uf_destino, fcp_pct, situacao, prioridade, regra, valid_from, "
        "ativo, fonte) VALUES ('RJ', 0.02, 'APLICA', 100, "
        "'FECP do Rio de Janeiro — regra geral', CURRENT_DATE, :sim, :f)"),
        {"f": FONTE_RJ, "sim": True})


def downgrade() -> None:
    con = op.get_bind()
    con.execute(sa.text("DELETE FROM regrafcp WHERE fonte = :f"), {"f": FONTE_RJ})
    op.add_column("regrafcp", sa.Column("exige_confirmacao", sa.Boolean(), nullable=True))
    con.execute(sa.text(
        "UPDATE regrafcp SET exige_confirmacao = CASE WHEN situacao = 'DESCONHECIDO' "
        "THEN 1 ELSE 0 END"))
    with op.batch_alter_table("regrafcp", schema=None) as batch_op:
        batch_op.drop_column("situacao")
    with op.batch_alter_table("estadofiscal", schema=None) as batch_op:
        batch_op.drop_column("interna_inclui_fcp")
        batch_op.drop_column("icms_interno_base")

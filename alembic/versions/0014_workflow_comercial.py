"""Workflow comercial: aprovação, emissão e revisão (Sessão 6)

Cria `aprovacaocotacao` e `snapshotemissao`, e acrescenta a `cotacao` os campos de revisão,
emissão, envio e cancelamento. `usuario` ganha `can_approve_quotes`.

**Aditiva.** Nenhum valor econômico é tocado, e nenhum status histórico é reescrito: as 18
cotações continuam com os status que sempre tiveram (16 `rascunho`, 2 `perdida`), e os
estados legados seguem no enum. Fingir que cotações de 2026 passaram por um fluxo de
aprovação que não existia seria falsificar histórico.

Os campos novos ficam nulos no histórico de propósito: inferir `fingerprint`, `revisao` ou
`aprovacao_id` retroativamente seria inventar evidência.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aprovacaocotacao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cotacao_id", sa.Integer(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDENTE"),
        sa.Column("motivos", sa.String(), nullable=True),
        sa.Column("excecoes_json", sa.String(), nullable=True),
        sa.Column("resumo_json", sa.String(), nullable=True),
        sa.Column("solicitante_id", sa.Integer(), nullable=True),
        sa.Column("solicitante_email", sa.String(), nullable=True),
        sa.Column("justificativa", sa.String(), nullable=True),
        sa.Column("aprovador_id", sa.Integer(), nullable=True),
        sa.Column("aprovador_email", sa.String(), nullable=True),
        sa.Column("comentario", sa.String(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("decidido_em", sa.DateTime(), nullable=True),
        sa.Column("invalidado_em", sa.DateTime(), nullable=True),
        sa.Column("invalidacao_motivo", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["cotacao_id"], ["cotacao.id"]),
        sa.ForeignKeyConstraint(["solicitante_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(["aprovador_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for c in ("cotacao_id", "fingerprint", "status", "criado_em"):
        op.create_index(f"ix_aprovacaocotacao_{c}", "aprovacaocotacao", [c], unique=False)

    op.create_table(
        "snapshotemissao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cotacao_id", sa.Integer(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("numero", sa.String(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("emitido_em", sa.DateTime(), nullable=True),
        sa.Column("emitido_por", sa.String(), nullable=True),
        sa.Column("aprovacao_id", sa.Integer(), nullable=True),
        sa.Column("aprovacao_fingerprint", sa.String(), nullable=True),
        sa.Column("cliente_json", sa.String(), nullable=True),
        sa.Column("itens_json", sa.String(), nullable=True),
        sa.Column("totais_json", sa.String(), nullable=True),
        sa.Column("fiscal_json", sa.String(), nullable=True),
        sa.Column("frete_json", sa.String(), nullable=True),
        sa.Column("premissas_json", sa.String(), nullable=True),
        sa.Column("memoria_json", sa.String(), nullable=True),
        sa.Column("pdf_caminho", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["cotacao_id"], ["cotacao.id"]),
        sa.ForeignKeyConstraint(["aprovacao_id"], ["aprovacaocotacao.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for c in ("cotacao_id", "fingerprint"):
        op.create_index(f"ix_snapshotemissao_{c}", "snapshotemissao", [c], unique=False)

    with op.batch_alter_table("cotacao") as batch:
        batch.add_column(sa.Column("revisao", sa.Integer(), nullable=False,
                                   server_default="1"))
        batch.add_column(sa.Column("cotacao_origem_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fingerprint", sa.String(), nullable=True))
        batch.add_column(sa.Column("issued_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("issued_por", sa.String(), nullable=True))
        batch.add_column(sa.Column("sent_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("sent_por", sa.String(), nullable=True))
        batch.add_column(sa.Column("cancelada_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("cancelada_por", sa.String(), nullable=True))
        batch.add_column(sa.Column("cancelamento_motivo", sa.String(), nullable=True))
        batch.add_column(sa.Column("premissas_mantidas_aprovadas", sa.Boolean(),
                                   nullable=False, server_default=sa.false()))
    op.create_index("ix_cotacao_cotacao_origem_id", "cotacao", ["cotacao_origem_id"],
                    unique=False)

    with op.batch_alter_table("usuario") as batch:
        batch.add_column(sa.Column("can_approve_quotes", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("usuario") as batch:
        batch.drop_column("can_approve_quotes")
    op.drop_index("ix_cotacao_cotacao_origem_id", table_name="cotacao")
    with op.batch_alter_table("cotacao") as batch:
        for c in ("premissas_mantidas_aprovadas", "cancelamento_motivo", "cancelada_por",
                  "cancelada_em", "sent_por", "sent_em", "issued_por", "issued_em",
                  "fingerprint", "cotacao_origem_id", "revisao"):
            batch.drop_column(c)
    for c in ("fingerprint", "cotacao_id"):
        op.drop_index(f"ix_snapshotemissao_{c}", table_name="snapshotemissao")
    op.drop_table("snapshotemissao")
    for c in ("criado_em", "status", "fingerprint", "cotacao_id"):
        op.drop_index(f"ix_aprovacaocotacao_{c}", table_name="aprovacaocotacao")
    op.drop_table("aprovacaocotacao")

"""Audit log e vigência da condição de pagamento (Sessão 5)

Duas coisas:

1. **`audit_log`** — tabela nova. Versionar o valor responde "qual era o número"; a trilha
   responde "quem decidiu trocar, e por quê". São perguntas diferentes.

2. **`condicaopagamento` ganha vigência** — e o índice de `codigo` deixa de ser único.
   A mesma condição ("30/60") precisa poder ter a linha que valeu até ontem e a que passa a
   valer amanhã; com índice único, a segunda linha não entra e não há como agendar troca de
   encargo. As 8 linhas herdadas ficam com `valid_from` NULO, que o resolvedor lê como
   "sempre valeu" — nenhuma delas muda de valor.

**Nenhum valor econômico é alterado por esta migration.** Nenhuma cotação, item, snapshot,
custo ou premissa é tocado.

O downgrade recria o índice único. Isso só é possível enquanto não houver duas versões da
mesma condição; se houver, o downgrade falha — e falhar é o certo, porque colapsar duas
versões numa apagaria a decisão de alguém.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auditlog",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ocorrido_em", sa.DateTime(), nullable=True),
        sa.Column("ator_id", sa.Integer(), nullable=True),
        sa.Column("ator_email", sa.String(), nullable=True),
        sa.Column("ator_papel", sa.String(), nullable=True),
        sa.Column("acao", sa.String(), nullable=False),
        sa.Column("entidade", sa.String(), nullable=False),
        sa.Column("entidade_id", sa.Integer(), nullable=True),
        sa.Column("escopo", sa.String(), nullable=True),
        sa.Column("versao_anterior", sa.String(), nullable=True),
        sa.Column("versao_nova", sa.String(), nullable=True),
        sa.Column("motivo", sa.String(), nullable=True),
        sa.Column("origem", sa.String(), nullable=True),
        sa.Column("resultado", sa.String(), nullable=False, server_default="OK"),
        sa.Column("correlacao", sa.String(), nullable=True),
        sa.Column("detalhe", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["ator_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("ocorrido_em", "ator_id", "acao", "entidade", "entidade_id", "correlacao"):
        op.create_index(f"ix_auditlog_{coluna}", "auditlog", [coluna], unique=False)

    # --- condição de pagamento: vigência ---
    with op.batch_alter_table("condicaopagamento") as batch:
        batch.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch.add_column(sa.Column("versao", sa.Integer(), nullable=False,
                                   server_default="1"))
        batch.add_column(sa.Column("substitui_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fonte", sa.String(), nullable=True))
        batch.add_column(sa.Column("criado_em", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("criado_por", sa.String(), nullable=True))

    # o índice único vira comum: duas versões da mesma condição precisam coexistir
    op.drop_index("ix_condicaopagamento_codigo", table_name="condicaopagamento")
    op.create_index("ix_condicaopagamento_codigo", "condicaopagamento", ["codigo"],
                    unique=False)


def downgrade():
    op.drop_index("ix_condicaopagamento_codigo", table_name="condicaopagamento")
    # Recriar o índice ÚNICO falha de propósito se houver mais de uma versão por código:
    # escolher qual delas sobreviveria seria descartar a decisão de um administrador.
    op.create_index("ix_condicaopagamento_codigo", "condicaopagamento", ["codigo"],
                    unique=True)
    with op.batch_alter_table("condicaopagamento") as batch:
        for coluna in ("criado_por", "criado_em", "fonte", "substitui_id", "versao",
                       "valid_to", "valid_from"):
            batch.drop_column(coluna)

    for coluna in ("correlacao", "entidade_id", "entidade", "acao", "ator_id", "ocorrido_em"):
        op.drop_index(f"ix_auditlog_{coluna}", table_name="auditlog")
    op.drop_table("auditlog")

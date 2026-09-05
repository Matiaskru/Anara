"""CRM: contatos, oportunidades, atividades e histórico de etapa (Sessão 7)

Aditiva. Nenhuma tabela econômica é reescrita e nenhum valor muda.

`cotacao.oportunidade_id` fica **NULO** nas 18 históricas: criar oportunidade para cotação
de 2026 seria inventar um negócio que nunca passou pelo funil. O vínculo manual continua
possível quando houver razão legítima.

`Cliente` ganha campos de ficha comercial — a mesma entidade representa prospect e cliente,
porque o que muda entre eles é quanto se sabe, não o que são.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contato",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("cargo", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("telefone", sa.String(), nullable=True),
        sa.Column("observacao", sa.String(), nullable=True),
        sa.Column("principal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["cliente_id"], ["cliente.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contato_cliente_id", "contato", ["cliente_id"], unique=False)

    op.create_table(
        "oportunidade",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=False),
        sa.Column("titulo", sa.String(), nullable=False),
        sa.Column("descricao", sa.String(), nullable=True),
        sa.Column("responsavel_id", sa.Integer(), nullable=True),
        sa.Column("etapa", sa.String(), nullable=False, server_default="PROSPECCAO"),
        sa.Column("status", sa.String(), nullable=False, server_default="ABERTA"),
        sa.Column("origem", sa.String(), nullable=True),
        sa.Column("origem_detalhe", sa.String(), nullable=True),
        sa.Column("valor_estimado", sa.Float(), nullable=True),
        sa.Column("data_prevista_fechamento", sa.Date(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.Column("won_em", sa.DateTime(), nullable=True),
        sa.Column("won_por", sa.String(), nullable=True),
        sa.Column("cotacao_vencedora_id", sa.Integer(), nullable=True),
        sa.Column("cotacao_vencedora_fingerprint", sa.String(), nullable=True),
        sa.Column("valor_fechado", sa.Float(), nullable=True),
        sa.Column("lost_em", sa.DateTime(), nullable=True),
        sa.Column("lost_por", sa.String(), nullable=True),
        sa.Column("motivo_perda", sa.String(), nullable=True),
        sa.Column("comentario_perda", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["cliente_id"], ["cliente.id"]),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(["cotacao_vencedora_id"], ["cotacao.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Índices dos filtros do dia a dia e das métricas da Sessão 8.
    for c in ("cliente_id", "responsavel_id", "etapa", "status", "criado_em",
              "data_prevista_fechamento"):
        op.create_index(f"ix_oportunidade_{c}", "oportunidade", [c], unique=False)

    op.create_table(
        "oportunidadeetapahistorico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("oportunidade_id", sa.Integer(), nullable=False),
        sa.Column("etapa_anterior", sa.String(), nullable=True),
        sa.Column("etapa_nova", sa.String(), nullable=False),
        sa.Column("ator_id", sa.Integer(), nullable=True),
        sa.Column("ator_email", sa.String(), nullable=True),
        sa.Column("ocorrido_em", sa.DateTime(), nullable=True),
        sa.Column("observacao", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["oportunidade_id"], ["oportunidade.id"]),
        sa.ForeignKeyConstraint(["ator_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for c in ("oportunidade_id", "ocorrido_em"):
        op.create_index(f"ix_oportunidadeetapahistorico_{c}", "oportunidadeetapahistorico",
                        [c], unique=False)

    op.create_table(
        "atividadecomercial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("oportunidade_id", sa.Integer(), nullable=True),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("contato_id", sa.Integer(), nullable=True),
        sa.Column("responsavel_id", sa.Integer(), nullable=True),
        sa.Column("tipo", sa.String(), nullable=False, server_default="FOLLOW_UP"),
        sa.Column("titulo", sa.String(), nullable=False),
        sa.Column("observacao", sa.String(), nullable=True),
        sa.Column("due_em", sa.DateTime(), nullable=True),
        sa.Column("concluida_em", sa.DateTime(), nullable=True),
        sa.Column("concluida_por", sa.String(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["oportunidade_id"], ["oportunidade.id"]),
        sa.ForeignKeyConstraint(["cliente_id"], ["cliente.id"]),
        sa.ForeignKeyConstraint(["contato_id"], ["contato.id"]),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for c in ("oportunidade_id", "cliente_id", "responsavel_id", "due_em"):
        op.create_index(f"ix_atividadecomercial_{c}", "atividadecomercial", [c],
                        unique=False)

    with op.batch_alter_table("cliente") as batch:
        for c in ("nome_fantasia", "segmento", "site", "origem",
                  "observacoes_comerciais"):
            batch.add_column(sa.Column(c, sa.String(), nullable=True))

    with op.batch_alter_table("cotacao") as batch:
        batch.add_column(sa.Column("oportunidade_id", sa.Integer(), nullable=True))
    op.create_index("ix_cotacao_oportunidade_id", "cotacao", ["oportunidade_id"],
                    unique=False)


def downgrade():
    op.drop_index("ix_cotacao_oportunidade_id", table_name="cotacao")
    with op.batch_alter_table("cotacao") as batch:
        batch.drop_column("oportunidade_id")
    with op.batch_alter_table("cliente") as batch:
        for c in ("observacoes_comerciais", "origem", "site", "segmento", "nome_fantasia"):
            batch.drop_column(c)
    for c in ("due_em", "responsavel_id", "cliente_id", "oportunidade_id"):
        op.drop_index(f"ix_atividadecomercial_{c}", table_name="atividadecomercial")
    op.drop_table("atividadecomercial")
    for c in ("ocorrido_em", "oportunidade_id"):
        op.drop_index(f"ix_oportunidadeetapahistorico_{c}",
                      table_name="oportunidadeetapahistorico")
    op.drop_table("oportunidadeetapahistorico")
    for c in ("data_prevista_fechamento", "criado_em", "status", "etapa", "responsavel_id",
              "cliente_id"):
        op.drop_index(f"ix_oportunidade_{c}", table_name="oportunidade")
    op.drop_table("oportunidade")
    op.drop_index("ix_contato_cliente_id", table_name="contato")
    op.drop_table("contato")

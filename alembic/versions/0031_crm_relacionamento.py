"""CRM de relacionamento: visitas, participantes, perfil de produtos do cliente (15/10/2026)

Aditiva. Reaproveita o que já existe — `contato` (agenda do cliente, Fase 3B) e
`atividadecomercial` (follow-up) — e acrescenta só o que faltava:

* `contato.whatsapp`, `contato.atualizado_em`, `contato.atualizado_por`;
* `visitacliente` — relatório de visita híbrido (texto livre obrigatório + campos opcionais),
  com autoria original imutável e exclusão lógica (`removida_em`);
* `visitaparticipante` — contatos do cliente que participaram (participante externo é texto
  livre na própria visita, não vira contato);
* `atividadecomercial.visita_id` / `atualizado_em` — o acompanhamento que nasceu da visita;
* `clienteprodutoutilizado` — o que o hotel USA (≠ o que a ANARA vendeu ou cotou), com vínculo
  opcional ao produto do catálogo e atributos estruturados para a busca.

Nenhuma coluna existente é alterada ou removida.

Revision ID: 0031
Revises: 0030
"""
import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contato", sa.Column("whatsapp", sa.String(), nullable=True))
    op.add_column("contato", sa.Column("atualizado_em", sa.DateTime(), nullable=True))
    op.add_column("contato", sa.Column("atualizado_por", sa.String(), nullable=True))

    op.create_table(
        "visitacliente",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("data_visita", sa.Date(), nullable=False),
        sa.Column("responsavel_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("assunto", sa.String(), nullable=True),
        sa.Column("relatorio", sa.String(), nullable=False),
        sa.Column("necessidades", sa.String(), nullable=True),
        sa.Column("produtos_discutidos", sa.String(), nullable=True),
        sa.Column("oportunidades", sa.String(), nullable=True),
        sa.Column("proximos_passos", sa.String(), nullable=True),
        sa.Column("observacoes", sa.String(), nullable=True),
        sa.Column("participantes_externos", sa.String(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("criado_por_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=True),
        sa.Column("atualizado_por", sa.String(), nullable=True),
        sa.Column("removida_em", sa.DateTime(), nullable=True),
        sa.Column("removida_por", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_visitacliente_cliente_id", "visitacliente", ["cliente_id"])
    op.create_index("ix_visitacliente_data_visita", "visitacliente", ["data_visita"])
    op.create_index("ix_visitacliente_responsavel_id", "visitacliente", ["responsavel_id"])

    op.create_table(
        "visitaparticipante",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("visita_id", sa.Integer(), sa.ForeignKey("visitacliente.id"), nullable=False),
        sa.Column("contato_id", sa.Integer(), sa.ForeignKey("contato.id"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("visita_id", "contato_id", name="uq_visitaparticipante_visita_contato"),
    )
    op.create_index("ix_visitaparticipante_visita_id", "visitaparticipante", ["visita_id"])
    op.create_index("ix_visitaparticipante_contato_id", "visitaparticipante", ["contato_id"])

    # modo batch: o SQLite não cria chave estrangeira por ALTER (copia e troca a tabela, com os
    # dados); no Postgres vira ALTER TABLE comum
    with op.batch_alter_table("atividadecomercial") as b:
        b.add_column(sa.Column("visita_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("atualizado_em", sa.DateTime(), nullable=True))
        b.create_foreign_key("fk_atividadecomercial_visita_id", "visitacliente", ["visita_id"], ["id"])
        b.create_index("ix_atividadecomercial_visita_id", ["visita_id"])

    op.create_table(
        "clienteprodutoutilizado",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("produto_id", sa.Integer(), sa.ForeignKey("produto.id"), nullable=True),
        sa.Column("familia", sa.String(), nullable=True),
        sa.Column("descricao", sa.String(), nullable=True),
        sa.Column("thread_count", sa.Integer(), nullable=True),
        sa.Column("composicao", sa.String(), nullable=True),
        sa.Column("tamanho", sa.String(), nullable=True),
        sa.Column("medidas", sa.String(), nullable=True),
        sa.Column("gsm", sa.Integer(), nullable=True),
        sa.Column("volume_aproximado", sa.String(), nullable=True),
        sa.Column("frequencia_reposicao", sa.String(), nullable=True),
        sa.Column("fornecedor_atual", sa.String(), nullable=True),
        sa.Column("observacoes", sa.String(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=True),
        sa.Column("atualizado_por", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("cliente_id", "produto_id", "familia", "thread_count", "composicao", "tamanho", "gsm"):
        op.create_index(f"ix_clienteprodutoutilizado_{col}", "clienteprodutoutilizado", [col])


def downgrade():
    for col in ("cliente_id", "produto_id", "familia", "thread_count", "composicao", "tamanho", "gsm"):
        op.drop_index(f"ix_clienteprodutoutilizado_{col}", table_name="clienteprodutoutilizado")
    op.drop_table("clienteprodutoutilizado")
    with op.batch_alter_table("atividadecomercial") as b:
        b.drop_index("ix_atividadecomercial_visita_id")
        b.drop_constraint("fk_atividadecomercial_visita_id", type_="foreignkey")
        b.drop_column("atualizado_em")
        b.drop_column("visita_id")
    op.drop_index("ix_visitaparticipante_contato_id", table_name="visitaparticipante")
    op.drop_index("ix_visitaparticipante_visita_id", table_name="visitaparticipante")
    op.drop_table("visitaparticipante")
    op.drop_index("ix_visitacliente_responsavel_id", table_name="visitacliente")
    op.drop_index("ix_visitacliente_data_visita", table_name="visitacliente")
    op.drop_index("ix_visitacliente_cliente_id", table_name="visitacliente")
    op.drop_table("visitacliente")
    with op.batch_alter_table("contato") as b:
        b.drop_column("atualizado_por")
        b.drop_column("atualizado_em")
        b.drop_column("whatsapp")

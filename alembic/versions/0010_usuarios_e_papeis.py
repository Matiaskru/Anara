"""Usuários, papéis e hash de senha (Sessão 4)

Cria a tabela `usuario`. **Aditiva**: nenhuma tabela existente é tocada, e nenhuma cotação,
item, snapshot ou base de importação muda.

O downgrade apaga a tabela — e com ela os acessos cadastrados. Isso é aceitável porque a
tabela é nova: antes desta migration não havia usuário nenhum, e voltar significa voltar à
senha compartilhada. O que o downgrade **não** faz é tocar em dado econômico.

Nenhum usuário é criado aqui. Bootstrap é ato explícito de operação, com senha vinda do
ambiente — ver `scripts/criar_usuario.py`. Migration que semeia credencial acaba virando
credencial no repositório.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("senha_hash", sa.String(), nullable=False),
        sa.Column("papel", sa.String(), nullable=False,
                  server_default="VENDEDOR_INTERNO"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_manage_users", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("sessao_versao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
        sa.Column("ultimo_login_em", sa.DateTime(), nullable=True),
        sa.Column("criado_por", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # e-mail é a credencial de login: o índice único é a regra, não otimização
    op.create_index("ix_usuario_email", "usuario", ["email"], unique=True)
    op.create_index("ix_usuario_papel", "usuario", ["papel"], unique=False)


def downgrade():
    op.drop_index("ix_usuario_papel", table_name="usuario")
    op.drop_index("ix_usuario_email", table_name="usuario")
    op.drop_table("usuario")

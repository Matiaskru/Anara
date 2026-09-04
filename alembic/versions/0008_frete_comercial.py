"""frete comercial: TRANSAL, grupos logísticos e rateio

Sessão 3A. Seis tabelas novas e o rateio no item.

O que a modelagem trava, e é a razão de ela existir assim:

* `TabelaFrete` **declara a semântica** em vez de deixá-la implícita no código —
  `tarifa_unidade`, `faixa_unidade`, `minimo_unidade`, `fator_cubagem_kg_m3` e `pedagio_base`.
  Nenhum desses é suposto no motor;
* `ComponenteFrete` separa o que é R$ fixo do que é percentual sobre a NF, porque os dois
  entram em lugares diferentes do waterfall, e cada um carrega sua própria aplicabilidade
  (APLICA / NAO_APLICA / DESCONHECIDO). Ausência de decisão não vira 0%;
* `GrupoLogistico` guarda a origem logística real. Uma tabela só resolve o grupo cuja origem e
  transportadora batem com as dela — não existe "tabela padrão" para origem incompatível.

Nada é preenchido aqui: a importação da TRANSAL é um script à parte, revisável antes de rodar.
Nenhuma coluna nova em `cotacaoitem` é NOT NULL, e os 45 itens históricos ficam com NULL.

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TXT = sqlmodel.sql.sqltypes.AutoString

COLUNAS_ITEM = [
    ("grupo_logistico_id", sa.Integer()),
    ("frete_cf_unitario", sa.Float()),
    ("frete_rv_pct", sa.Float()),
    ("frete_rv_valor", sa.Float()),
    ("frete_total_item", sa.Float()),
    ("frete_status", TXT()),
    ("frete_rateio_criterio", TXT()),
]


def upgrade() -> None:
    op.create_table(
        "transportadora",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo", TXT(), nullable=False),
        sa.Column("nome", TXT(), nullable=False),
        sa.Column("cnpj", TXT(), nullable=True),
        sa.Column("endereco", TXT(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("notas", TXT(), nullable=True),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_transportadora_codigo", "transportadora", ["codigo"], unique=True)

    op.create_table(
        "tabelafrete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transportadora_id", sa.Integer(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("origem_logistica_cidade", TXT(), nullable=False),
        sa.Column("origem_logistica_uf", TXT(), nullable=False),
        sa.Column("origem_regiao", TXT(), nullable=True),
        sa.Column("documento_fonte", TXT(), nullable=False),
        sa.Column("data_fonte", sa.Date(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("tarifa_unidade", TXT(), nullable=False),
        sa.Column("faixa_unidade", TXT(), nullable=False),
        sa.Column("minimo_unidade", TXT(), nullable=False),
        sa.Column("fator_cubagem_kg_m3", sa.Float(), nullable=True),
        sa.Column("pedagio_base", TXT(), nullable=False),
        sa.Column("icms_situacao", TXT(), nullable=False),
        sa.Column("icms_pct", sa.Float(), nullable=True),
        sa.Column("icms_notas", TXT(), nullable=True),
        sa.Column("notas", TXT(), nullable=True),
        sa.ForeignKeyConstraint(["transportadora_id"], ["transportadora.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tabelafrete_transportadora_id", "tabelafrete",
                    ["transportadora_id"], unique=False)

    op.create_table(
        "faixafrete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tabela_id", sa.Integer(), nullable=False),
        sa.Column("regiao_destino", TXT(), nullable=False),
        sa.Column("peso_de", sa.Float(), nullable=False),
        sa.Column("peso_ate", sa.Float(), nullable=True),
        sa.Column("tarifa", sa.Float(), nullable=True),
        sa.Column("frete_minimo", sa.Float(), nullable=True),
        sa.Column("prazo", TXT(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("notas", TXT(), nullable=True),
        sa.ForeignKeyConstraint(["tabela_id"], ["tabelafrete.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_faixafrete_tabela_id", "faixafrete", ["tabela_id"], unique=False)
    op.create_index("ix_faixafrete_regiao_destino", "faixafrete", ["regiao_destino"],
                    unique=False)

    op.create_table(
        "coberturafrete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tabela_id", sa.Integer(), nullable=False),
        sa.Column("cidade", TXT(), nullable=False),
        sa.Column("uf", TXT(), nullable=True),
        sa.Column("regiao_destino", TXT(), nullable=False),
        sa.Column("unidade", TXT(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tabela_id"], ["tabelafrete.id"]),
        sa.PrimaryKeyConstraint("id"))
    for col in ("tabela_id", "cidade", "uf", "regiao_destino"):
        op.create_index(f"ix_coberturafrete_{col}", "coberturafrete", [col], unique=False)

    op.create_table(
        "componentefrete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tabela_id", sa.Integer(), nullable=False),
        sa.Column("codigo", TXT(), nullable=False),
        sa.Column("nome", TXT(), nullable=False),
        sa.Column("tipo", TXT(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=True),
        sa.Column("unidade", TXT(), nullable=True),
        sa.Column("situacao", TXT(), nullable=False),
        sa.Column("automatico", sa.Boolean(), nullable=False),
        sa.Column("fonte", TXT(), nullable=True),
        sa.Column("regra", TXT(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tabela_id"], ["tabelafrete.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_componentefrete_tabela_id", "componentefrete", ["tabela_id"],
                    unique=False)
    op.create_index("ix_componentefrete_codigo", "componentefrete", ["codigo"], unique=False)

    op.create_table(
        "grupologistico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cotacao_id", sa.Integer(), nullable=False),
        sa.Column("nome", TXT(), nullable=True),
        sa.Column("origem_cidade", TXT(), nullable=True),
        sa.Column("origem_uf", TXT(), nullable=True),
        sa.Column("fornecedor_id", sa.Integer(), nullable=True),
        sa.Column("transportadora_id", sa.Integer(), nullable=True),
        sa.Column("tabela_id", sa.Integer(), nullable=True),
        sa.Column("destino_cidade", TXT(), nullable=True),
        sa.Column("destino_uf", TXT(), nullable=True),
        sa.Column("regiao_destino", TXT(), nullable=True),
        sa.Column("peso_real_kg", sa.Float(), nullable=True),
        sa.Column("volume_m3", sa.Float(), nullable=True),
        sa.Column("peso_cubado_kg", sa.Float(), nullable=True),
        sa.Column("peso_taxado_kg", sa.Float(), nullable=True),
        sa.Column("peso_taxado_fonte", TXT(), nullable=True),
        sa.Column("valor_mercadoria", sa.Float(), nullable=True),
        sa.Column("frete_peso", sa.Float(), nullable=True),
        sa.Column("cf_logistico", sa.Float(), nullable=True),
        sa.Column("rv_logistico_pct", sa.Float(), nullable=True),
        sa.Column("rv_logistico_valor", sa.Float(), nullable=True),
        sa.Column("frete_total", sa.Float(), nullable=True),
        sa.Column("status", TXT(), nullable=False),
        sa.Column("motivo", TXT(), nullable=True),
        sa.Column("memoria_json", TXT(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["cotacao_id"], ["cotacao.id"]),
        sa.ForeignKeyConstraint(["fornecedor_id"], ["fornecedor.id"]),
        sa.ForeignKeyConstraint(["tabela_id"], ["tabelafrete.id"]),
        sa.ForeignKeyConstraint(["transportadora_id"], ["transportadora.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_grupologistico_cotacao_id", "grupologistico", ["cotacao_id"],
                    unique=False)

    for nome, tipo in COLUNAS_ITEM:
        op.add_column("cotacaoitem", sa.Column(nome, tipo, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cotacaoitem", schema=None) as batch_op:
        for nome, _ in reversed(COLUNAS_ITEM):
            batch_op.drop_column(nome)
    for tabela in ("grupologistico", "componentefrete", "coberturafrete", "faixafrete",
                   "tabelafrete", "transportadora"):
        op.drop_table(tabela)

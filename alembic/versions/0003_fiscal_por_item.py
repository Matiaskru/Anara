"""fiscal por item — origem fiscal, finalidade, DIFAL e alíquotas interestaduais

Onda 1. O que esta migration cria:

1. `aliquotainterestadual` — a alíquota por par de UF × natureza da mercadoria vira **dado
   versionado**, não `if` no código. É o que permite tratar exceção (por NCM ou por produto)
   sem alterar programa;
2. as colunas que faltavam para o cenário fiscal ser resolvido e **congelado por item**;
3. as duas premissas versionadas que dão origem fiscal e finalidade padrão.

O que ela **não** faz, de propósito:

* **não** preenche nenhuma coluna nova em `cotacaoitem`. Os 45 itens históricos ficam com NULL,
  o que é a informação correta: eles foram calculados antes de o fiscal ser por item. Preencher
  com valor derivado hoje seria reescrever histórico;
* **não** infere `produto.origem_fiscal`. A natureza é derivada do tipo do fornecedor em tempo
  de cálculo; a coluna existe só para o override explícito;
* **não** apaga a premissa `icms_fallback_pct`. Ela deixa de ser lida por qualquer código —
  fica no banco como registro do que existia, sem efeito;
* nenhuma coluna nova é NOT NULL. Onde falta evidência, o valor é NULL e o cálculo bloqueia com
  `REVIEW_REQUIRED` — nunca um default para satisfazer constraint.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UFS_FAIXA_7 = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "PA",
               "PB", "PE", "PI", "RN", "RO", "RR", "SE", "TO"]
UFS_FAIXA_12 = ["MG", "PR", "RJ", "RS", "SC"]
FONTE = "Decisões Anara 03/09/2026 · Res. Senado 22/1989 e 13/2012"

COLUNAS = {
    "cliente": [("finalidade", sqlmodel.sql.sqltypes.AutoString())],
    "fornecedor": [("uf_origem_fiscal", sqlmodel.sql.sqltypes.AutoString())],
    "produto": [("origem_fiscal", sqlmodel.sql.sqltypes.AutoString())],
    "cotacao": [("uf_origem_fiscal", sqlmodel.sql.sqltypes.AutoString()),
                ("finalidade", sqlmodel.sql.sqltypes.AutoString())],
    "cotacaoitem": [
        ("origem_fiscal", sqlmodel.sql.sqltypes.AutoString()),
        ("uf_origem_fiscal", sqlmodel.sql.sqltypes.AutoString()),
        ("uf_destino_fiscal", sqlmodel.sql.sqltypes.AutoString()),
        ("finalidade", sqlmodel.sql.sqltypes.AutoString()),
        ("consumidor_final", sa.Boolean()),
        ("icms_pct", sa.Float()),
        ("icms_regra", sqlmodel.sql.sqltypes.AutoString()),
        ("icms_fonte", sqlmodel.sql.sqltypes.AutoString()),
        ("difal_pct", sa.Float()),
        ("difal_responsavel", sqlmodel.sql.sqltypes.AutoString()),
        ("difal_valor", sa.Float()),
        ("status_fiscal", sqlmodel.sql.sqltypes.AutoString()),
        ("motivo_fiscal", sqlmodel.sql.sqltypes.AutoString()),
        ("status_pagamento", sqlmodel.sql.sqltypes.AutoString()),
        ("motivo_pagamento", sqlmodel.sql.sqltypes.AutoString()),
        ("encargo_pct", sa.Float()),
    ],
}

PREMISSAS = [
    dict(chave="fiscal_uf_origem_padrao", valor_txt="SP", unidade="UF",
         descricao="Origem FISCAL padrão da operação quando nem a cotação nem o fornecedor a "
                   "definem. É default configurado, não evidência.",
         fonte="Decisões de 03/09/2026 — cenários nacionais SP→*"),
    dict(chave="fiscal_finalidade_padrao", valor_txt="USO_CONSUMO",
         descricao="Finalidade padrão da operação — hotel consome o enxoval, não revende",
         fonte="Decisões de 03/09/2026"),
]


def _linhas_aliquotas():
    linhas = []
    for uf in UFS_FAIXA_7:
        linhas.append(("SP", uf, "NACIONAL", 0.07,
                       f"Interestadual SP→{uf}, mercadoria nacional — 7%"))
    for uf in UFS_FAIXA_12:
        linhas.append(("SP", uf, "NACIONAL", 0.12,
                       f"Interestadual SP→{uf}, mercadoria nacional — 12%"))
    for uf in UFS_FAIXA_7 + UFS_FAIXA_12:
        linhas.append(("SP", uf, "IMPORTADA", 0.04,
                       f"Interestadual SP→{uf}, mercadoria importada — 4% "
                       "(Res. Senado 13/2012)"))
    return linhas


def upgrade() -> None:
    op.create_table(
        "aliquotainterestadual",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uf_origem", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("uf_destino", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("origem_fiscal", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("aliquota", sa.Float(), nullable=False),
        sa.Column("ncm", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("produto_id", sa.Integer(), nullable=True),
        sa.Column("prioridade", sa.Integer(), nullable=False),
        sa.Column("regra", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("fonte", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notas", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["produto_id"], ["produto.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aliquotainterestadual_uf_origem", "aliquotainterestadual",
                    ["uf_origem"], unique=False)
    op.create_index("ix_aliquotainterestadual_uf_destino", "aliquotainterestadual",
                    ["uf_destino"], unique=False)
    op.create_index("ix_aliquotainterestadual_origem_fiscal", "aliquotainterestadual",
                    ["origem_fiscal"], unique=False)
    op.create_index("ix_aliquotainterestadual_ncm", "aliquotainterestadual",
                    ["ncm"], unique=False)

    for tabela, colunas in COLUNAS.items():
        for nome, tipo in colunas:
            op.add_column(tabela, sa.Column(nome, tipo, nullable=True))

    con = op.get_bind()
    for uf_o, uf_d, natureza, aliquota, regra in _linhas_aliquotas():
        con.execute(sa.text(
            "INSERT INTO aliquotainterestadual (uf_origem, uf_destino, origem_fiscal, aliquota, "
            "prioridade, regra, valid_from, ativo, fonte) "
            "VALUES (:o, :d, :n, :a, 100, :r, CURRENT_DATE, :sim, :f)"),
            {"o": uf_o, "d": uf_d, "n": natureza, "a": aliquota, "r": regra, "f": FONTE,
             "sim": True})

    for p in PREMISSAS:
        existe = con.execute(sa.text("SELECT COUNT(*) FROM premissa WHERE chave = :c"),
                             {"c": p["chave"]}).scalar()
        if not existe:
            con.execute(sa.text(
                "INSERT INTO premissa (chave, valor_txt, unidade, descricao, valid_from, ativo, "
                "fonte, criado_em) VALUES (:c, :v, :u, :d, CURRENT_DATE, :sim, :f, "
                "CURRENT_TIMESTAMP)"),
                {"c": p["chave"], "v": p["valor_txt"], "u": p.get("unidade"),
                 "d": p["descricao"], "f": p["fonte"], "sim": True})


def downgrade() -> None:
    """Remove só o que o upgrade criou. Rollback de dado continua sendo por restore."""
    con = op.get_bind()
    con.execute(sa.text("DELETE FROM premissa WHERE chave IN "
                        "('fiscal_uf_origem_padrao', 'fiscal_finalidade_padrao')"))
    for tabela, colunas in COLUNAS.items():
        with op.batch_alter_table(tabela, schema=None) as batch_op:
            for nome, _tipo in reversed(colunas):
                batch_op.drop_column(nome)
    for indice in ("ix_aliquotainterestadual_ncm", "ix_aliquotainterestadual_origem_fiscal",
                   "ix_aliquotainterestadual_uf_destino", "ix_aliquotainterestadual_uf_origem"):
        op.drop_index(indice, table_name="aliquotainterestadual")
    op.drop_table("aliquotainterestadual")

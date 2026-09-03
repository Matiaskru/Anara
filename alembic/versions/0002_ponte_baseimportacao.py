"""ponte BaseImportacao — liga o legado ao mundo versionado (decisão H)

O que esta migration faz, e só isso:

1. dá à `BaseImportacao` a mesma vigência que as tabelas versionadas já têm
   (`valid_from`, `valid_to`, `ativo`, `fonte`) — metadado, nenhum valor econômico;
2. cria `basepremissaponte`, uma linha por campo de premissa de cada base, dizendo onde
   aquela premissa vive hoje no mundo versionado e se ela divergiu;
3. preenche as duas coisas para as bases existentes.

O que ela **não** faz, deliberadamente:

* não apaga nem reescreve nenhuma `BaseImportacao`;
* não toca em `cotacao`, `cotacaoitem` nem em `memoria_json` — snapshot é intocável;
* não muda nenhuma premissa vigente: a ponte é descritiva, e nenhum cálculo a lê;
* não conserta divergência que ela encontre. Quando o valor legado difere do vigente,
  isso vira `diverge = 1` e fica registrado. Corrigir é decisão das ondas seguintes.

O autogenerate do Alembic quis trazer junto a divergência pré-existente entre os modelos
e o banco (índices, NOT NULL, chaves estrangeiras e tipos que o `ALTER TABLE` de
`app/migrations.py` nunca criou). Tudo isso foi retirado daqui e registrado como **B-13**
no AUDIT_ANARA_MASTER.md: a Fase 0 fotografa, não conserta.

Revision ID: 0002
Revises: 0001
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel                     # tipos do SQLModel (AutoString)
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Onde cada campo legado da BaseImportacao vive hoje.
# (campo_legado, tabela_versionada, chave_versionada, escalar_comparavel, observação)
MAPA = [
    ("icms_pct", "regrafiscalvenda + estadofiscal",
     "icms_venda / carga_final (resolvido por cenário)", False,
     "O ICMS deixou de ser um escalar da base: hoje é resolvido por origem × destino × "
     "contribuinte. A Onda 1 ainda vai desdobrá-lo por item (B-02) e separar importado "
     "de nacional (B-01)."),
    ("pis_cofins_pct", "premissa", "pis_cofins_pct", True, None),
    ("encargo_financeiro_pct", "condicaopagamento", "encargo_pct[codigo=30]", True,
     "A base guardava um encargo único; hoje o encargo é por condição de pagamento. "
     "A comparação usa a condição 30 dias, que é a equivalente da base."),
    ("comissao_tabela_json", "premissa", "comissao_tabela", "json", None),
    ("origem_uf", "premissa", "catalogo_origem", "texto",
     "A base guarda a UF de origem da importação; a premissa versionada guarda a origem "
     "usada para formar o preço-base do catálogo. Divergir aqui é esperado e precisa "
     "estar visível, não corrigido."),
    ("icms_por_estado_json", "estadofiscal", "carga_final por UF", False,
     "Virou tabela versionada com 27 linhas."),
    ("cenarios_fiscais_json", "regrafiscalvenda + estadofiscal", "cenário resolvido", False,
     "Virou resolução por regra, não mais lista congelada."),
    ("cambio_usd_brl", "premissa", "fx_usd_brl", True, None),
    ("frete_usd_kg", "premissa", "frete_int_usd_kg", True, None),
    ("outras_desp_usd_un", "premissa", "outras_desp_usd_un", True, None),
]


def _premissa(con, chave):
    linha = con.execute(sa.text(
        "SELECT valor_num, valor_txt FROM premissa "
        "WHERE chave = :c AND ativo = 1 AND valid_to IS NULL "
        "ORDER BY valid_from DESC, id DESC LIMIT 1"), {"c": chave}).fetchone()
    return (linha[0], linha[1]) if linha else (None, None)


def _encargo_30(con):
    linha = con.execute(sa.text(
        "SELECT encargo_pct FROM condicaopagamento WHERE codigo = '30' LIMIT 1")).fetchone()
    return (linha[0] if linha else None), None


def _json_igual(a, b):
    try:
        return json.loads(a) == json.loads(b)
    except (TypeError, ValueError):
        return (a or "").strip() == (b or "").strip()


def _preencher(con):
    bases = con.execute(sa.text(
        "SELECT id, importado_em, nome_arquivo, icms_pct, pis_cofins_pct, "
        "encargo_financeiro_pct, comissao_tabela_json, origem_uf, icms_por_estado_json, "
        "cenarios_fiscais_json, cambio_usd_brl, frete_usd_kg, outras_desp_usd_un "
        "FROM baseimportacao ORDER BY importado_em, id")).mappings().all()
    if not bases:
        return

    # --- vigência: a base mais nova fecha a anterior, como nas tabelas versionadas ---
    for i, base in enumerate(bases):
        inicio = str(base["importado_em"])[:10]
        fim = str(bases[i + 1]["importado_em"])[:10] if i + 1 < len(bases) else None
        con.execute(sa.text(
            "UPDATE baseimportacao SET valid_from = :de, valid_to = :ate, ativo = :at, "
            "fonte = :fonte WHERE id = :id"),
            {"de": inicio, "ate": fim, "at": 1 if i == len(bases) - 1 else 0,
             "fonte": base["nome_arquivo"], "id": base["id"]})

    # --- ponte: recriada do zero, para a migration poder ser reaplicada sem duplicar ---
    con.execute(sa.text("DELETE FROM basepremissaponte WHERE origem = 'FASE_0_PONTE'"))

    for base in bases:
        for campo, tabela, chave, escalar, observacao in MAPA:
            legado = base[campo]
            legado_num = legado if isinstance(legado, (int, float)) else None
            legado_txt = None if legado_num is not None else (
                str(legado) if legado is not None else None)

            if tabela == "condicaopagamento":
                vig_num, vig_txt = _encargo_30(con)
            elif tabela == "premissa":
                vig_num, vig_txt = _premissa(con, chave)
            else:
                vig_num, vig_txt = None, None

            diverge = None
            if escalar is True and legado_num is not None and vig_num is not None:
                diverge = abs(float(legado_num) - float(vig_num)) > 1e-9
            elif escalar == "json" and legado_txt and vig_txt:
                diverge = not _json_igual(legado_txt, vig_txt)
            elif escalar == "texto" and legado_txt and vig_txt:
                diverge = legado_txt.strip() != vig_txt.strip()

            con.execute(sa.text(
                "INSERT INTO basepremissaponte (base_importacao_id, campo_legado, "
                "valor_legado_num, valor_legado_txt, premissa_tabela, premissa_chave, "
                "valor_vigente_num, valor_vigente_txt, diverge, observacao, origem, "
                "criado_em) VALUES (:base, :campo, :ln, :lt, :tab, :chave, :vn, :vt, "
                ":div, :obs, 'FASE_0_PONTE', CURRENT_TIMESTAMP)"),
                {"base": base["id"], "campo": campo, "ln": legado_num, "lt": legado_txt,
                 "tab": tabela, "chave": chave, "vn": vig_num, "vt": vig_txt,
                 "div": None if diverge is None else int(diverge), "obs": observacao})


def upgrade() -> None:
    op.create_table(
        "basepremissaponte",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_importacao_id", sa.Integer(), nullable=False),
        sa.Column("campo_legado", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("valor_legado_num", sa.Float(), nullable=True),
        sa.Column("valor_legado_txt", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("premissa_tabela", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("premissa_chave", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("valor_vigente_num", sa.Float(), nullable=True),
        sa.Column("valor_vigente_txt", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("diverge", sa.Boolean(), nullable=True),
        sa.Column("observacao", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("origem", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["base_importacao_id"], ["baseimportacao.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_basepremissaponte_base_importacao_id", "basepremissaponte",
                    ["base_importacao_id"], unique=False)
    op.create_index("ix_basepremissaponte_campo_legado", "basepremissaponte",
                    ["campo_legado"], unique=False)

    # ADD COLUMN simples: o SQLite acrescenta sem reconstruir a tabela, então nenhuma
    # linha de BaseImportacao é reescrita.
    op.add_column("baseimportacao", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("baseimportacao", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("baseimportacao", sa.Column("ativo", sa.Boolean(), nullable=True))
    op.add_column("baseimportacao",
                  sa.Column("fonte", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    _preencher(op.get_bind())


def downgrade() -> None:
    """Desfaz apenas o que o upgrade criou.

    Rollback de dado continua sendo por restore de backup (ver BACKUP.md) — este
    downgrade existe para o esquema, e remove só as colunas e a tabela desta revisão.
    """
    op.drop_index("ix_basepremissaponte_campo_legado", table_name="basepremissaponte")
    op.drop_index("ix_basepremissaponte_base_importacao_id", table_name="basepremissaponte")
    op.drop_table("basepremissaponte")
    with op.batch_alter_table("baseimportacao", schema=None) as batch_op:
        batch_op.drop_column("fonte")
        batch_op.drop_column("ativo")
        batch_op.drop_column("valid_to")
        batch_op.drop_column("valid_from")

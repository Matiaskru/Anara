"""Integração da Onda 1: o fiscal resolvido item a item dentro de uma cotação real.

O que estes testes cobram, e que nenhum outro arquivo cobre:

* uma cotação com KTC, Daune e Decor produz **três tratamentos fiscais diferentes** no mesmo
  documento — é a prova de que o ICMS deixou de ser um campo do cabeçalho;
* cenário irresolvido bloqueia o PDF final, sem impedir o rascunho;
* o `REVIEW_REQUIRED` **legado de custo** não bloqueia comercialmente — ele pertence à
  reconciliação da Sessão 2 e não pode derrubar o catálogo por acidente.
"""
import pytest
from sqlmodel import select

from app.models import (
    CondicaoPagamento, CostConfidence, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto,
    TipoFornecedor,
)
from app import pricing_service as ps


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def produto_de(session, fornecedor, sku, custo=100.0, **kw):
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia="Flat Sheet",
                cost_method=kw.pop("cost_method", CostMethod.national_supplier.value), **kw)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_para(destino="Minas Gerais", contribuinte=True, finalidade="REVENDA",
                 condicao="30"):
    return Cotacao(cliente_id=0, estado_origem="Santa Catarina", uf_origem_fiscal="SP",
                   estado_destino=destino, contribuinte_icms=contribuinte,
                   finalidade=finalidade, condicao_pagamento=condicao)


# ---------------------------------------------------------------------------
# Cotação mista — o coração da onda
# ---------------------------------------------------------------------------
def test_cotacao_mista_resolve_tres_aliquotas_no_mesmo_documento(session, fornecedores):
    """KTC importada 4% · Daune nacional 12% · Decor nacional 12%, tudo SP→MG."""
    ktc = produto_de(session, fornecedores["KTC"], "MISTA-KTC")
    daune = produto_de(session, fornecedores["DAUNE"], "MISTA-DAUNE")
    decor = produto_de(session, fornecedores["DECOR_TRICOT"], "MISTA-DECOR")
    cot = cotacao_para("Minas Gerais", contribuinte=True)

    r_ktc = ps.fiscal_do_item(session, cot, ktc)
    r_daune = ps.fiscal_do_item(session, cot, daune)
    r_decor = ps.fiscal_do_item(session, cot, decor)

    assert r_ktc.origem_fiscal == "IMPORTADA" and r_ktc.icms_pct == pytest.approx(0.04)
    assert r_daune.origem_fiscal == "NACIONAL" and r_daune.icms_pct == pytest.approx(0.12)
    assert r_decor.origem_fiscal == "NACIONAL" and r_decor.icms_pct == pytest.approx(0.12)


def test_cotacao_mista_muda_o_preco_de_cada_item(session, fornecedores):
    """Mesmo custo, mesmo destino, mesma condição: preços diferentes por natureza fiscal."""
    from app.pricing_engine import calcular_por_margem

    ktc = produto_de(session, fornecedores["KTC"], "PRECO-KTC", custo=100.0)
    daune = produto_de(session, fornecedores["DAUNE"], "PRECO-DAUNE", custo=100.0)
    cot = cotacao_para("Minas Gerais", contribuinte=True)

    regras_ktc, _ = ps.regras_da_cotacao(session, cot, ktc)
    regras_daune, _ = ps.regras_da_cotacao(session, cot, daune)
    preco_ktc = calcular_por_margem(100.0, 1, 0.15, regras_ktc).preco_negociado
    preco_daune = calcular_por_margem(100.0, 1, 0.15, regras_daune).preco_negociado

    assert preco_daune > preco_ktc, "8 p.p. a mais de ICMS têm de elevar o preço do item nacional"


def test_a_faixa_de_7_e_a_de_12_dao_precos_diferentes(session, fornecedores):
    from app.pricing_engine import calcular_por_margem
    daune = produto_de(session, fornecedores["DAUNE"], "FAIXA-DAUNE", custo=100.0)

    regras_ba, _ = ps.regras_da_cotacao(session, cotacao_para("Bahia"), daune)
    regras_mg, _ = ps.regras_da_cotacao(session, cotacao_para("Minas Gerais"), daune)
    assert regras_ba.icms_pct == pytest.approx(0.07)
    assert regras_mg.icms_pct == pytest.approx(0.12)
    assert (calcular_por_margem(100.0, 1, 0.14, regras_mg).preco_negociado
            > calcular_por_margem(100.0, 1, 0.14, regras_ba).preco_negociado)


# ---------------------------------------------------------------------------
# Origem fiscal ≠ origem logística
# ---------------------------------------------------------------------------
def test_origem_logistica_nao_altera_o_fiscal(session, fornecedores):
    daune = produto_de(session, fornecedores["DAUNE"], "ORIG-1", custo=100.0)
    a = cotacao_para("Bahia")
    a.estado_origem = "Santa Catarina"
    b = cotacao_para("Bahia")
    b.estado_origem = "São Paulo"
    assert (ps.fiscal_do_item(session, a, daune).icms_pct
            == ps.fiscal_do_item(session, b, daune).icms_pct)


def test_origem_fiscal_da_cotacao_vence_a_premissa(session, fornecedores):
    daune = produto_de(session, fornecedores["DAUNE"], "ORIG-2", custo=100.0)
    cot = cotacao_para("Bahia")
    cot.uf_origem_fiscal = "RJ"          # não há alíquota RJ→BA cadastrada
    r = ps.fiscal_do_item(session, cot, daune)
    assert r.bloqueado and "RJ→BA" in r.motivo


def test_origem_do_fornecedor_vence_a_premissa(session, fornecedores):
    """Fornecedor que fatura de outra UF: o cadastro dele manda, e a memória registra."""
    daune = fornecedores["DAUNE"]
    daune.uf_origem_fiscal = "RJ"
    session.add(daune)
    session.commit()
    try:
        p = produto_de(session, daune, "ORIG-3", custo=100.0)
        cot = cotacao_para("Bahia")
        cot.uf_origem_fiscal = None
        r = ps.fiscal_do_item(session, cot, p)
        assert r.uf_origem == "RJ"
        assert any("fornecedor" in a for a in r.avisos)
    finally:
        daune.uf_origem_fiscal = None
        session.add(daune)
        session.commit()


def test_memoria_registra_que_a_origem_veio_de_default(session, fornecedores):
    """Rastreabilidade: quando a origem vem da premissa, isso fica escrito."""
    p = produto_de(session, fornecedores["DAUNE"], "ORIG-4", custo=100.0)
    cot = cotacao_para("Bahia")
    cot.uf_origem_fiscal = None
    r = ps.fiscal_do_item(session, cot, p)
    assert any("default, não evidência" in a for a in r.avisos)


def test_produto_sem_fornecedor_bloqueia(session):
    p = Produto(sku_key="SEM-FORN", nome="Órfão", custo_unitario=100.0)
    session.add(p)
    session.commit()
    r = ps.fiscal_do_item(session, cotacao_para("Bahia"), p)
    assert r.bloqueado and "natureza fiscal" in r.motivo.lower()


def test_override_de_origem_fiscal_no_produto(session, fornecedores):
    """Um SKU nacionalizado pode ser marcado NACIONAL mesmo vindo do fornecedor importador."""
    p = produto_de(session, fornecedores["KTC"], "OVR-1", custo=100.0)
    assert ps.fiscal_do_item(session, cotacao_para("Bahia"), p).icms_pct == pytest.approx(0.04)
    p.origem_fiscal = "NACIONAL"
    session.add(p)
    session.commit()
    r = ps.fiscal_do_item(session, cotacao_para("Bahia"), p)
    assert r.icms_pct == pytest.approx(0.07)
    assert any("override do SKU" in a for a in r.avisos)


# ---------------------------------------------------------------------------
# Bloqueio e PDF
# ---------------------------------------------------------------------------
def test_cenario_irresolvido_nao_produz_regras(session, fornecedores):
    p = produto_de(session, fornecedores["DAUNE"], "BLOQ-1", custo=100.0)
    cot = cotacao_para("Bahia")
    cot.estado_destino = None
    regras, ctx = ps.regras_da_cotacao(session, cot, p)
    assert regras is None
    assert ctx["bloqueado"] and ctx["motivo_bloqueio"]


def test_condicao_desconhecida_bloqueia_o_calculo(session, fornecedores):
    p = produto_de(session, fornecedores["DAUNE"], "BLOQ-2", custo=100.0)
    cot = cotacao_para("Bahia", condicao="45 DD")
    regras, ctx = ps.regras_da_cotacao(session, cot, p)
    assert regras is None
    assert ctx["status_pagamento"] == "REVIEW_REQUIRED"


def test_pdf_bloqueia_com_item_irresolvido():
    from app.routers.cotacoes import bloqueios_fiscais
    ok = CotacaoItem(cotacao_id=1, ordem=0, nome_produto="A", quantidade=1, custo_unitario=1,
                     preco_base=1, preco_negociado=1, margem_liquida=0, faturamento=1,
                     custo_total=1, lucro=0, status_fiscal="OK", status_pagamento="OK")
    ruim = CotacaoItem(cotacao_id=1, ordem=1, nome_produto="B", quantidade=1, custo_unitario=1,
                       preco_base=1, preco_negociado=0, margem_liquida=0, faturamento=0,
                       custo_total=1, lucro=0, status_fiscal="REVIEW_REQUIRED",
                       motivo_fiscal="origem fiscal indeterminada")
    assert bloqueios_fiscais([ok]) == []
    motivos = bloqueios_fiscais([ok, ruim])
    assert len(motivos) == 1 and "origem fiscal" in motivos[0]


# ---------------------------------------------------------------------------
# O legado de custo NÃO pode bloquear comercialmente
# ---------------------------------------------------------------------------
def test_review_required_legado_de_custo_nao_bloqueia_o_fiscal(session, fornecedores):
    """`legacy REVIEW_REQUIRED` ≠ `REVIEW_REQUIRED` fiscal.

    121 SKUs do catálogo carregam `CostConfidence.REVIEW_REQUIRED` com o sentido antigo do
    rótulo. Se o blocker da Onda 1 olhasse esse campo, 36% do catálogo pararia de ser cotável
    de uma vez. A reconciliação desses status é da Sessão 2 — aqui só se garante que a Onda 1
    não os derruba por acidente.
    """
    p = produto_de(session, fornecedores["DAUNE"], "LEG-1", custo=100.0,
                   custo_confianca=CostConfidence.review_required.value,
                   cost_method=CostMethod.legacy_excel.value)
    p.precisa_revisao = True
    p.revisao_motivo = "Origem do custo não rastreada (legado)"
    session.add(p)
    session.commit()

    regras, ctx = ps.regras_da_cotacao(session, cotacao_para("Bahia"), p)
    assert regras is not None, "confiança legada de CUSTO não pode bloquear o cálculo fiscal"
    assert ctx["status_fiscal"] == "OK"
    assert ctx["bloqueado"] is False
    assert regras.icms_pct == pytest.approx(0.07)


def test_bloqueio_do_pdf_ignora_a_confianca_de_custo():
    """O blocker de emissão lê `status_fiscal`/`status_pagamento`, nunca `custo_confianca`."""
    from app.routers.cotacoes import bloqueios_fiscais
    it = CotacaoItem(cotacao_id=1, ordem=0, nome_produto="Legado", quantidade=1,
                     custo_unitario=1, preco_base=1, preco_negociado=1, margem_liquida=0,
                     faturamento=1, custo_total=1, lucro=0,
                     cost_method=CostMethod.legacy_excel.value,
                     status_fiscal="OK", status_pagamento="OK")
    assert bloqueios_fiscais([it]) == []


def test_os_dois_status_sao_campos_diferentes():
    """Garantia estrutural: confiança de custo e status fiscal não compartilham coluna."""
    colunas = CotacaoItem.model_fields
    assert "status_fiscal" in colunas
    assert "cost_method" in colunas
    assert colunas["status_fiscal"] is not colunas.get("cost_method")


# ---------------------------------------------------------------------------
# Condições de pagamento canônicas, ponta a ponta
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("codigo,esperado", [
    ("30", 0.016), ("30/60", 0.032), ("30/60/90", 0.048),
    ("30/60/90/120", 0.064), ("30/60/90/120/150", 0.080),
])
def test_as_cinco_condicoes_canonicas_chegam_ao_taxruleset(session, fornecedores,
                                                           codigo, esperado):
    p = produto_de(session, fornecedores["DAUNE"], f"PAG-{codigo.replace('/', '-')}",
                   custo=100.0)
    regras, _ = ps.regras_da_cotacao(session, cotacao_para("Bahia", condicao=codigo), p)
    assert regras.encargo_financeiro_pct == pytest.approx(esperado)

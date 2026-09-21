"""Política comercial de 21/09/2026 — B2B, tabela 2×, escada de comissão por item, base líquida
de ICMS, Daune sem trava, Decor como custo de compra, ELIS, fiscal 27 UFs, frete manual.

Cada seção corresponde a um bloco do enunciado (§41–§49). Os oracles são independentes do
motor: recomputam a escada, o B2B e a base do zero, em Decimal.
"""
import asyncio
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from app import comercial_service as com
from app import politica_comercial as pol
from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, ZERO, dinheiro
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, MargemRegra, Produto, TipoFornecedor,
)
from app.pricing_engine import (
    TaxRuleSet, calcular_por_preco, desconto_vs_tabela, preco_b2b, preco_de_tabela,
    preco_por_desconto,
)
from conftest import RequestFalsa, _novo_usuario
from decimais import MARGEM_DO_CENTAVO, aprox
from tests.crisis.conftest import (add_item, editar_quantidade, nova_cotacao, produto_ktc_cotado,
                                   produto_nacional, salvar_cabecalho)

X = Decimal


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def admin():
    u = _novo_usuario("ADMIN")
    u.can_approve_quotes = True
    return u


@pytest.fixture
def owner():
    u = _novo_usuario("OWNER")
    u.can_approve_quotes = True
    return u


# ===========================================================================
# §41 — escada de comissão (oracle independente)
# ===========================================================================
def escada_oracle(desconto: Decimal) -> Decimal:
    if desconto <= 0:
        return X("0.10")
    if desconto <= X("0.10"):
        return X("0.09")
    if desconto <= X("0.20"):
        return X("0.08")
    if desconto <= X("0.30"):
        return X("0.07")
    if desconto <= X("0.40"):
        return X("0.06")
    return X("0.05")


@pytest.mark.parametrize("desconto,esperado", [
    ("0", "0.10"), ("0.0001", "0.09"), ("0.10", "0.09"), ("0.1001", "0.08"), ("0.20", "0.08"),
    ("0.2001", "0.07"), ("0.30", "0.07"), ("0.3001", "0.06"), ("0.40", "0.06"), ("0.4001", "0.05"),
    ("0.50", "0.05"), ("0.60", "0.05"), ("0.99", "0.05"), ("-0.10", "0.10"),
])
def test_41_escada_de_comissao_nas_bordas(desconto, esperado):
    assert pol.faixa_comissao(X(desconto)) == X(esperado)
    assert pol.faixa_comissao(X(desconto)) == escada_oracle(X(desconto))


def test_41_escada_le_as_faixas_da_premissa():
    faixas = pol.faixas_de_json("[[0.10,0.09],[0.20,0.08],[0.30,0.07],[0.40,0.06]]")
    assert pol.faixa_comissao(X("0.15"), faixas=faixas) == X("0.08")
    assert pol.faixa_comissao(X("0.45"), faixas=faixas) == X("0.05")


def test_41_preco_acima_da_tabela_e_desconto_zero_e_10_por_cento():
    c = pol.comissao_do_item(X("200"), X("100"), X("250"), 1, X("0.18"))
    assert c.desconto_vs_tabela == ZERO and c.taxa_pct == X("0.10")
    assert c.base_comissionavel == dinheiro(X("250") * X("0.82"))
    assert c.comissao_valor == dinheiro(X("250") * X("0.82") * X("0.10"))


def test_41_cotacao_mista_tres_itens_tres_taxas(session, fornecedores, admin):
    """Três itens com descontos diferentes → três taxas; nenhum item limita o outro."""
    cot = nova_cotacao(session, contribuinte_icms=False, finalidade="USO_CONSUMO")
    a = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    b = add_item(session, cot, produto_nacional(session, fornecedores, custo=80.0))
    c = add_item(session, cot, produto_nacional(session, fornecedores, custo=60.0))
    propostas = {a.id: {"desconto": "0.05"}, b.id: {"desconto": "0.25"}, c.id: {"desconto": "0.50"}}
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), propostas, ator=admin)
    session.commit()
    por_id = {x.item.id: x for x in av.itens}
    assert por_id[a.id].comissao_item.taxa_pct == X("0.09")
    assert por_id[b.id].comissao_item.taxa_pct == X("0.07")
    assert por_id[c.id].comissao_item.taxa_pct == X("0.05")
    # cada comissão sai da SUA base; o total é a soma e a taxa efetiva é Σ ÷ Σ base
    soma = sum(x.comissao_item.comissao_valor for x in av.itens)
    base = sum(x.resultado.base_comissionavel for x in av.itens)
    assert av.comissao_total_valor == soma
    assert av.taxa_efetiva == soma / base
    assert av.requer_aprovacao is False
    # gravado por item, não uma taxa única
    session.expire_all()
    gravados = {it.id: it for it in ws.itens_de(session, cot.id)}
    assert gravados[a.id].comissao_faixa_pct == aprox(0.09)
    assert gravados[b.id].comissao_faixa_pct == aprox(0.07)
    assert gravados[c.id].comissao_faixa_pct == aprox(0.05)
    assert gravados[a.id].modo_edicao == "desconto" and gravados[a.id].modo_negociacao == "desconto"


# ===========================================================================
# §42 — B2B = menor centavo válido (candidato cumpre; candidato − 0,01 falha)
# ===========================================================================
def regras_sp_nao_contribuinte(comissao=X("0.05"), base_liquida=True):
    icms = X("0.18")
    return TaxRuleSet(icms_pct=icms, pis_cofins_pct=X("0.0925") * (1 - icms),
                      encargo_financeiro_pct=X("0.016"), comissao_tabela=[(0, comissao)],
                      comissao_base_icms_pct=icms if base_liquida else ZERO)


@pytest.mark.parametrize("rotulo,cnet,margem", [
    ("KTC sheet ≥ 400 fios", "61.12", "0.23"),
    ("KTC sheet 300 fios", "58.35", "0.22"),
    ("KTC sheet 250 fios", "49.90", "0.20"),
    ("Pillow case 250 fios", "12.34", "0.19"),
    ("Pillow case 400 fios", "15.01", "0.20"),
    ("Towel", "27.777", "0.16"),
    ("Bathrobe", "142.90", "0.14"),
    ("Daune", "79.86", "0.13"),
    ("Decor", "118.50135", "0.13"),
    ("ELIS blanket", "68.38", "0.14"),
])
def test_42_b2b_e_o_primeiro_centavo_valido(rotulo, cnet, margem):
    regras = regras_sp_nao_contribuinte()
    r = preco_b2b(X(cnet), X(margem), regras)
    candidato = r.preco_negociado
    assert r.margem_liquida >= X(margem), rotulo
    assert r.reconcilia()
    anterior = calcular_por_preco(X(cnet), 1, candidato - X("0.01"), regras)
    assert anterior.margem_liquida < X(margem), f"{rotulo}: o centavo anterior também cumpre"
    # componentes reconciliam: receita = custo + impostos + comissão + lucro
    assert r.faturamento == r.custo_total + r.impostos + r.comissao + r.lucro
    # comissão = 5% sobre a base líquida de ICMS, não sobre o bruto
    assert r.comissao == dinheiro(r.faturamento * (1 - X("0.18")) * X("0.05"))


def test_42_golden_elis_124_97_e_tabela_249_94():
    """Sanity do enunciado: CNET 68,38 · SP→SP não contribuinte · 30 DD · 14% → B2B ≈ 124,97."""
    r = preco_b2b(X("68.38"), X("0.14"), regras_sp_nao_contribuinte())
    assert abs(r.preco_negociado - X("124.97")) <= X("0.01")
    tabela = preco_de_tabela(r.preco_negociado, 2)
    assert abs(tabela - X("249.94")) <= X("0.02")
    assert calcular_por_preco(X("68.38"), 1, r.preco_negociado - X("0.01"),
                              regras_sp_nao_contribuinte()).margem_liquida < X("0.14")


def test_42_b2b_nao_depende_de_hardcode_varre_custos():
    """Propriedade em 300 custos: para todo CNET, candidato cumpre e o anterior falha."""
    regras = regras_sp_nao_contribuinte()
    cnet = X("3.17")
    for _ in range(300):
        r = preco_b2b(cnet, X("0.19"), regras)
        assert r.margem_liquida >= X("0.19")
        if r.preco_negociado > X("0.01"):
            assert calcular_por_preco(cnet, 1, r.preco_negociado - X("0.01"), regras).margem_liquida < X("0.19")
        cnet += X("2.731")


def test_42_tabela_e_derivada_do_b2b_e_desconto_reproduz():
    b2b = X("124.97")
    tabela = preco_de_tabela(b2b, 2)
    assert tabela == X("249.94")
    assert desconto_vs_tabela(b2b, tabela) == X("0.5")
    assert preco_por_desconto(tabela, X("0.5")) == b2b
    # desconto pedido nunca é ultrapassado pelo arredondamento (ROUND_UP)
    p20 = preco_por_desconto(X("358.98"), X("0.20"))
    assert desconto_vs_tabela(p20, X("358.98")) <= X("0.20")
    assert pol.faixa_comissao(desconto_vs_tabela(p20, X("358.98"))) == X("0.08")


# ===========================================================================
# §43 — fiscal: 27 UFs, contribuinte × não contribuinte, base da comissão em RJ
# ===========================================================================
def _regras_para(session, fornecedores, codigo, destino, contribuinte, finalidade, familia="Flat Sheet"):
    prod = Produto(sku_key=f"__fis_{codigo}_{destino}_{contribuinte}", nome="x",
                   fornecedor_id=fornecedores[codigo].id, familia=familia, thread_count=300)
    cot = Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino=destino,
                  contribuinte_icms=contribuinte, finalidade=finalidade, condicao_pagamento="30",
                  freight_type="FOB")
    return ps.regras_da_cotacao(session, cot, prod)


@pytest.mark.parametrize("codigo,destino,esperado", [
    ("KTC", "Minas Gerais", "0.04"), ("KTC", "Bahia", "0.04"),
    ("DAUNE", "Minas Gerais", "0.12"), ("DECOR_TRICOT", "Bahia", "0.07"),
])
def test_43_contribuinte_interestadual(session, fornecedores, codigo, destino, esperado):
    regras, ctx = _regras_para(session, fornecedores, codigo, destino, True, "REVENDA")
    assert regras is not None, ctx.get("motivo_bloqueio")
    assert regras.icms_pct == X(esperado) and ctx["difal_responsavel"] == "NAO_APLICAVEL"
    # base da comissão = ICMS próprio (não há DIFAL nem FCP da Anara)
    assert regras.comissao_base_icms_pct == X(esperado)


def test_43_contribuinte_uso_consumo_difal_do_destinatario_nao_reduz(session, fornecedores):
    regras, ctx = _regras_para(session, fornecedores, "DAUNE", "Minas Gerais", True, "USO_CONSUMO")
    assert regras.icms_pct == X("0.12") and ctx["difal_responsavel"] == "DESTINATARIO"
    assert ctx["difal_pct"] == aprox(0.06)                      # 18% − 12%, informativo
    assert regras.comissao_base_icms_pct == X("0.12"), "DIFAL do destinatário não sai da base"


@pytest.mark.parametrize("destino,icms_total,fcp,base_comissao", [
    ("São Paulo", "0.18", "0", "0.18"), ("Minas Gerais", "0.18", "0", "0.18"),
    ("Santa Catarina", "0.17", "0", "0.17"), ("Bahia", "0.205", "0", "0.205"),
    ("Pernambuco", "0.205", "0", "0.205"), ("Rio de Janeiro", "0.22", "0.02", "0.20"),
    ("Alagoas", "0.215", "0.01", "0.205"), ("Sergipe", "0.20", "0.01", "0.19"),
])
def test_43_nao_contribuinte_carga_e_base_da_comissao(session, fornecedores, destino, icms_total,
                                                       fcp, base_comissao):
    regras, ctx = _regras_para(session, fornecedores, "KTC", destino, False, "USO_CONSUMO")
    assert regras is not None, ctx.get("motivo_bloqueio")
    assert regras.icms_pct == X(icms_total)
    assert D(ctx["fcp_pct"]) == X(fcp)
    if destino != "São Paulo":
        assert ctx["difal_responsavel"] == "REMETENTE"
        assert regras.icms_pct == X("0.04") + D(ctx["difal_pct"]) + X(fcp)
    # base da comissão: ICMS próprio + DIFAL do remetente, SEM FCP
    assert regras.comissao_base_icms_pct == X(base_comissao)


def test_43_rj_nao_contribuinte_waterfall_22_base_20(session, fornecedores):
    regras, _ = _regras_para(session, fornecedores, "KTC", "Rio de Janeiro", False, "USO_CONSUMO")
    r = preco_b2b(X("58.35"), X("0.22"), regras)
    assert regras.icms_pct == X("0.22") and regras.comissao_base_icms_pct == X("0.20")
    assert r.comissao == dinheiro(r.faturamento * X("0.80") * X("0.05")), "deduz 20%, NÃO 22%"


def test_43_27_ufs_resolvem_para_nao_contribuinte_nas_familias_do_escopo(session, fornecedores):
    from app.fiscal_2026_09_21 import BASE_INTERNA, FAMILIAS_ESCOPO
    from app.models import EstadoFiscal
    estados = {e.uf: e.estado for e in session.exec(select(EstadoFiscal)).all()}
    for uf in BASE_INTERNA:
        for familia in ("Flat Sheet", "Bath Towel", "Duvet Insert", "Bed Runner", "Blanket"):
            assert familia in FAMILIAS_ESCOPO
            regras, ctx = _regras_para(session, fornecedores, "KTC", estados[uf], False,
                                       "USO_CONSUMO", familia=familia)
            assert regras is not None, (uf, familia, ctx.get("motivo_bloqueio"))
    # fora do escopo continua bloqueando
    regras, ctx = _regras_para(session, fornecedores, "KTC", "Bahia", False, "USO_CONSUMO",
                               familia="Cortina Blackout")
    assert regras is None and "FCP" in ctx["motivo_bloqueio"]


def test_43_carga_final_e_base_dupla_nao_entram_no_motor(session):
    from app.models import EstadoFiscal
    sp = session.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "SP")).first()
    assert sp.base_dupla == aprox(0.1707) and sp.carga_final == aprox(0.1707)   # benchmark intocado
    assert sp.icms_interno_base == aprox(0.18)                                # o que o motor lê


# ===========================================================================
# §6 — todo produto ativo tem regra ou blocker intencional; nada de 15%
# ===========================================================================
def test_06_produto_sem_regra_bloqueia_em_vez_de_15_por_cento(session, fornecedores):
    from app.margin_rules import resolver_margem
    regras = session.exec(select(MargemRegra)).all()
    r = resolver_margem(regras, fornecedor_id=None, familia="Família Nova Qualquer")
    assert r.margem_pct is None and not r.tem_regra
    # e na cotação: o item entra sem preço, com o blocker nomeado
    forn = Fornecedor(codigo=f"NOVO{id(session) % 1000}", nome="Fornecedor sem regra", tipo="nacional")
    session.add(forn); session.commit(); session.refresh(forn)
    p = Produto(sku_key=f"SEMREGRA-{forn.id}", nome="Sem regra", custo_unitario=50.0,
                fornecedor_id=forn.id, familia="Cortina", cost_method=CostMethod.national_supplier.value)
    session.add(p); session.commit(); session.refresh(p)
    cot = nova_cotacao(session)
    it = add_item(session, cot, p)
    assert (it.preco_negociado or 0) == 0 and it.margem_padrao_pct is None
    assert it.margem_regra == wf.SEM_REGRA_DE_MARGEM
    assert any(b.codigo == wf.SEM_REGRA_DE_MARGEM for b in wf.blockers_do_item(it))


def test_06_todas_as_familias_do_catalogo_tem_regra_hoje(session, fornecedores):
    """Cada família × fornecedor do escopo resolve para uma regra da política de 21/09."""
    from app.fiscal_2026_09_21 import FAMILIAS_ESCOPO
    for familia in FAMILIAS_ESCOPO:
        for codigo in ("KTC", "DAUNE", "DECOR_TRICOT", pol.CODIGO_ELIS):
            p = Produto(sku_key="__x", nome="x", fornecedor_id=fornecedores[codigo].id, familia=familia)
            m = ps.margem_padrao(session, p)
            assert m.tem_regra and m.politica == pol.ROTULO_2026_09_21, (codigo, familia)
            assert m.comissao_formacao_pct == X("0.05") and m.preco_travado is False


# ===========================================================================
# §20/§21/§22 — Daune sem trava, Decor custo de compra, ELIS
# ===========================================================================
def test_20_daune_negocia_e_abaixo_do_b2b_exige_aprovacao(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, codigo="DAUNE", custo=79.86,
                                                 familia="Pillow"))
    assert it.preco_travado is False and it.margem_padrao_pct == aprox(0.13)
    assert it.preco_tabela == aprox(2 * it.preco_recomendado, abs=0.011)
    abaixo = float(dinheiro(D(it.preco_recomendado) - X("1")))
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: abaixo}, ator=admin)
    session.commit()
    assert av.requer_aprovacao is True
    assert {e.motivo for a in av.itens for e in a.excecoes} == {wf.PRECO_ABAIXO_B2B}


def test_21_decor_migra_para_referencia_versionada_como_custo_de_compra(session, fornecedores, owner):
    from app import custo_service as cs
    from app import dados_2026_09_21 as dados
    p = produto_nacional(session, fornecedores, codigo="DECOR_TRICOT", custo=130.58,
                         familia="Bed Runner", custo_ref_valor=130.58,
                         custo_ref_documento="ORÇAMENTO ANARA - 240826 (Decor Tricot)",
                         custo_ref_data=date(2026, 8, 24), precisa_revisao=True,
                         revisao_motivo="Valor tratado como CUSTO DE COMPRA. Se for preço de venda, corrigir.")
    plano = [l for l in dados.plano_decor(session) if l["produto"].id == p.id]
    assert plano and plano[0]["cnet"] == X("130.58") * X("0.9075")
    dados.aplicar_decor(session, owner)
    session.commit()
    session.refresh(p)
    ref = cs.referencia_vigente(session, p.id)
    assert ref.metodo_custo == CostMethod.decor_direct.value and ref.status_custo == "CONFIRMADO"
    assert D(ref.valor_bruto) == X("130.58") and abs(D(ref.cnet_brl) - X("118.50135")) < X("1e-6")
    memoria = json.loads(ref.memoria_calculo)
    assert memoria["icms_credito_pct"] == 0 and memoria["pis_cofins_credito_pct"] == aprox(0.0925)
    assert "sem crédito" in memoria["formula"]
    assert p.precisa_revisao is False and abs(D(p.custo_unitario) - X("118.50135")) < X("1e-6")
    custo, mem = ps.custo_para_precificar(session, p)
    assert ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO"
    # segunda aplicação: no-op
    assert dados.aplicar_decor(session, owner)["registradas"] == 0


def test_22_elis_cobertor_entra_com_14_e_b2b_124_97(session, fornecedores, owner):
    from app import dados_2026_09_21 as dados
    r = dados.aplicar_elis(session, owner)
    session.commit()
    p = session.get(Produto, r["produto_id"])
    forn = session.get(Fornecedor, p.fornecedor_id)
    assert forn.codigo == pol.CODIGO_ELIS and forn.tipo == TipoFornecedor.nacional and forn.uf_origem_fiscal == "SP"
    m = ps.margem_padrao(session, p)
    assert m.margem_pct == X("0.14") and m.politica == pol.ROTULO_2026_09_21
    custo, mem = ps.custo_para_precificar(session, p)
    assert D(custo) == X("68.38") and ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO"
    cot = nova_cotacao(session, contribuinte_icms=False, finalidade="USO_CONSUMO", condicao_pagamento="30")
    it = add_item(session, cot, p)
    assert it.icms_pct == aprox(0.18) and it.origem_fiscal == "NACIONAL"
    assert abs(D(it.preco_recomendado) - X("124.97")) <= X("0.01")
    assert abs(D(it.preco_tabela) - X("249.94")) <= X("0.02")
    assert it.comissao_faixa_pct == aprox(0.05) and it.desconto_vs_tabela_pct == aprox(0.5, abs=1e-4)
    # idempotente
    assert dados.aplicar_elis(session, owner)["produto_criado"] is False


# ===========================================================================
# §24 — toalha 100/0 e 90/10: mesmo EXW, CNET e B2B
# ===========================================================================
def test_24_toalha_100_e_90_10_tem_o_mesmo_preco(session):
    from app.calculadora import calcular, salvar_no_catalogo
    cem = calcular(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="100/0")
    noventa = calcular(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="90/10")
    assert cem["custo"]["industrial"]["exw_usd"] == noventa["custo"]["industrial"]["exw_usd"]
    assert cem["custo"]["net_brl"] == noventa["custo"]["net_brl"]
    assert cem["b2b"]["preco_b2b"] == noventa["b2b"]["preco_b2b"]
    assert cem["produto"]["nome"] != noventa["produto"]["nome"]
    a = salvar_no_catalogo(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="100/0")
    b = salvar_no_catalogo(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="90/10")
    assert a.id != b.id and a.cotton_pct == 1.0 and b.cotton_pct == 0.9 and b.poliester_pct == 0.1
    assert a.custo_unitario == b.custo_unitario


# ===========================================================================
# §45 — calculadora → catálogo → cotação: mesmo número, e a cotação reprecifica
# ===========================================================================
def test_45_calculadora_catalogo_cotacao_coincidem_e_reprecificam(session, fornecedores, admin):
    from app.calculadora import calcular, salvar_no_catalogo
    from app.models import MaterialPreco
    m = session.exec(select(MaterialPreco).where(MaterialPreco.thread_count == 300)
                     .where(MaterialPreco.plain_or_stripe == "plain")).first()
    cot = nova_cotacao(session, contribuinte_icms=False, finalidade="USO_CONSUMO")
    calc = calcular(session, "Flat Sheet", 190, 250, material_id=m.id, cotacao=session.get(Cotacao, cot.id))
    lencol = salvar_no_catalogo(session, "Flat Sheet", 190, 250, material_id=m.id)
    fronha = salvar_no_catalogo(session, "Pillow Case", 50, 70, material_id=m.id, abas=4, flap_cm=20)
    toalha = salvar_no_catalogo(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="100/0")
    toalha_90 = salvar_no_catalogo(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="90/10")
    it = add_item(session, cot, lencol)
    assert it.preco_recomendado == aprox(calc["b2b"]["preco_b2b"])
    assert it.preco_tabela == aprox(calc["b2b"]["preco_tabela"])
    assert it.preco_negociado == aprox(calc["comercial"]["preco_negociado"])
    f = add_item(session, cot, fronha)
    assert "abas" in (fronha.construcao or "") and "ox" + "ford" not in fronha.nome.lower()
    t1, t2 = add_item(session, cot, toalha), add_item(session, cot, toalha_90)
    assert t1.preco_recomendado == t2.preco_recomendado and t1.custo_unitario == t2.custo_unitario
    # mudou o cenário: a cotação reprecifica (não fica com o preço da calculadora)
    antes = it.preco_negociado
    salvar_cabecalho(session, cot, condicao_pagamento="30/60/90")
    session.expire_all()
    it2 = session.get(CotacaoItem, it.id)
    assert it2.preco_negociado != antes and it2.preco_negociado == it2.preco_recomendado
    novo = calcular(session, "Flat Sheet", 190, 250, material_id=m.id, cotacao=session.get(Cotacao, cot.id))
    assert it2.preco_recomendado == aprox(novo["b2b"]["preco_b2b"])


# ===========================================================================
# §10/§19 — repricing automático preservando o desconto; condição e destino
# ===========================================================================
def test_10_mudar_condicao_mantem_desconto_e_muda_preco(session, fornecedores, admin):
    cot = nova_cotacao(session, condicao_pagamento="30/60/90", contribuinte_icms=False, finalidade="USO_CONSUMO")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.30"}}, ator=admin)
    session.commit()
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    b2b1, tab1, preco1 = it.preco_recomendado, it.preco_tabela, it.preco_negociado
    assert it.comissao_faixa_pct == aprox(0.07)
    salvar_cabecalho(session, cot, condicao_pagamento="30/60")
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert it.preco_recomendado < b2b1 and it.preco_tabela < tab1 and it.preco_negociado < preco1
    assert it.desconto_editado_pct == aprox(0.30) and it.desconto_vs_tabela_pct == aprox(0.30, abs=1e-3)
    assert it.preco_negociado == aprox(float(preco_por_desconto(D(it.preco_tabela), X("0.30"))))
    assert it.comissao_faixa_pct == aprox(0.07)
    assert ws.avaliar(session, session.get(Cotacao, cot.id)).precisa_aprovacao is False


def test_17_editar_quantidade_nao_congela_o_preco(session, fornecedores, admin):
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.10"}}, ator=admin)
    session.commit()
    editar_quantidade(session, cot, it, 37)
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert it.quantidade == 37 and it.modo_edicao == "desconto" and it.desconto_editado_pct == aprox(0.10)
    assert it.faturamento == aprox(float(dinheiro(D(it.preco_negociado) * 37)))
    salvar_cabecalho(session, cot, estado_destino="Minas Gerais", contribuinte_icms="nao")
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert it.uf_destino_fiscal == "MG" and it.desconto_vs_tabela_pct == aprox(0.10, abs=1e-3)
    assert it.preco_negociado == aprox(float(preco_por_desconto(D(it.preco_tabela), X("0.10"))))


# ===========================================================================
# §14 — aprovação presa ao fingerprint; mudança material invalida
# ===========================================================================
def test_14_aprovacao_cai_com_mudanca_de_cenario_e_emite_depois(session, fornecedores, admin, owner):
    from app.models import AprovacaoCotacao
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.55"}}, ator=admin)
    session.commit()
    c = session.get(Cotacao, cot.id)
    pront = ws.avaliar(session, c)
    assert pront.precisa_aprovacao and not pront.pode_emitir
    pedido = ws.solicitar_aprovacao(session, c, ator=admin, justificativa="cliente estratégico")
    session.commit()
    ws.decidir(session, c, pedido.id, ator=owner, aprovar=True, comentario="ok")
    session.commit()
    assert ws.avaliar(session, c).pode_emitir is True
    salvar_cabecalho(session, cot, condicao_pagamento="30/60/90")
    c = session.get(Cotacao, cot.id)
    assert ws.avaliar(session, c).aprovacao_valida is False, "aprovação era sobre outro cenário"
    pedido2 = ws.solicitar_aprovacao(session, c, ator=admin, justificativa="reaprovar")
    session.commit()
    ws.decidir(session, c, pedido2.id, ator=owner, aprovar=True, comentario="ok")
    session.commit()
    snap = ws.emitir(session, c, ator=owner)
    session.commit()
    itens = json.loads(snap.itens_json)
    assert itens[0]["preco_negociado"] == aprox(session.get(CotacaoItem, it.id).preco_negociado)
    assert snap.aprovacao_id == pedido2.id


# ===========================================================================
# §46 — confidencialidade da vendedora (JSON, HTML, PDF)
# ===========================================================================
PROIBIDO_NO_PAYLOAD = {"custo_unitario", "custo_total", "cnet", "lucro", "margem_liquida",
                       "margem_padrao_pct", "markup_implicito", "base_comissionavel",
                       "icms_base_comissao_pct", "comissao_faixa_pct", "comissao_item",
                       "exw_usd", "memoria_json", "economia", "politica_comercial"}


def _chaves(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k); _chaves(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _chaves(v, acc)
    return acc


def test_46_vendedora_ve_tabela_b2b_desconto_e_comissao_propria_e_nada_mais(session, fornecedores, admin):
    from app.routers.negociacao import negociacao_atual
    from app.routers.cotacoes import _item_para_json
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.35"}}, ator=admin)
    session.commit()
    resp = negociacao_atual(RequestFalsa(vend), cot.id, session)
    corpo = json.loads(bytes(resp.body))
    linha = corpo["itens"][0]
    assert linha["preco_tabela"] > linha["preco_b2b"] > 0 and linha["desconto_vs_tabela_pct"] == aprox(0.35, abs=1e-3)
    assert linha["comissao_estimada_pct"] == aprox(0.06) and linha["comissao_estimada_valor"] > 0
    assert linha["autonomia_item"] == "DENTRO_DA_AUTONOMIA"
    assert corpo["comissao_estimada_valor"] > 0 and corpo["autonomia_status"] == "DENTRO_DA_AUTONOMIA"
    assert not (_chaves(corpo) & PROIBIDO_NO_PAYLOAD)
    assert encontrar_confidenciais(corpo) == []
    item_json = _item_para_json(session.get(CotacaoItem, it.id), pode_ver_economia=False)
    assert not (set(item_json) & PROIBIDO_NO_PAYLOAD) and item_json["preco_tabela"] > 0
    # e o admin vê a decomposição
    adm_json = json.loads(bytes(negociacao_atual(RequestFalsa(admin), cot.id, session).body))
    eco = adm_json["economia"]["itens"][0]
    assert eco["comissao_item"]["comissao_faixa_pct"] == aprox(0.06) and eco["base_comissionavel"] > 0


def test_46_pdf_nao_leva_tabela_b2b_desconto_nem_comissao(session, fornecedores, admin):
    from app.pdf_bridge import montar_documento
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0), 3)
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.35"}}, ator=admin)
    session.commit()
    c = session.get(Cotacao, cot.id)
    cliente = session.get(Cliente, c.cliente_id)
    header, items, totals = montar_documento(c, cliente, ws.itens_de(session, c.id), rascunho=True)
    texto = json.dumps([header, items, totals], ensure_ascii=False).lower()
    for palavra in ("tabela", "b2b", "desconto", "comiss", "margem", "custo", "base_comission", "oxford"):
        assert palavra not in texto, palavra
    assert items[0]["preco_final"] == aprox(session.get(CotacaoItem, it.id).preco_negociado)


# ===========================================================================
# §26 — a palavra proibida não aparece em nome novo, spec, payload nem HTML relevante
# ===========================================================================
def test_26_nomenclatura_abas_sem_o_termo_legado(session):
    from app import spec_parser
    from app.calculadora import salvar_no_catalogo
    from app.models import MaterialPreco
    termo = "ox" + "ford"
    assert spec_parser.parse_construcao(f"fronha {termo} 4 lados") == "4 abas"
    m = session.exec(select(MaterialPreco).where(MaterialPreco.thread_count == 250)).first()
    p = salvar_no_catalogo(session, "Pillow Case", 50, 90, material_id=m.id, abas=3, flap_cm=20)
    for campo in (p.nome, p.especificacao or "", p.sku_key, p.construcao or ""):
        assert termo not in campo.lower()
    assert "3 abas" in p.construcao
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parents[1]
    for arquivo in list((raiz / "app" / "templates").glob("*.html")) + list((raiz / "app" / "static" / "js").glob("*.js")):
        assert termo not in arquivo.read_text(encoding="utf-8").lower(), arquivo.name


def test_26_normalizacao_de_dados_legados(session, fornecedores, owner):
    from app import dados_2026_09_21 as dados
    termo = "ox" + "ford"
    p = Produto(sku_key=f"Pillow Case 50x70+5 · listrado · {termo} 5cm + aba 20cm · {id(session)%997}",
                nome=f"Fronha com aba 50x70+5 · {termo}", especificacao=f"{termo} 5cm + aba 20cm",
                construcao=termo, familia="Pillow Case", fornecedor_id=fornecedores["KTC"].id,
                largura_cm=50, comprimento_cm=70, thread_count=250)
    session.add(p); session.commit(); session.refresh(p)
    assert ps._abas_do_produto(p) == 4
    dados.aplicar_fronhas(session, owner)
    session.commit(); session.refresh(p)
    assert termo not in (p.nome + p.especificacao + p.sku_key + p.construcao).lower()
    assert p.construcao == "4 abas" and ps._abas_do_produto(p) == 4


# ===========================================================================
# §48 — frete: FOB, A_COMBINAR, CIF manual confirmado, CIF automático irresolvido
# ===========================================================================
def test_48_frete_manual_confirmado_soma_ao_total_e_nao_bloqueia(session, fornecedores, admin):
    cot = nova_cotacao(session, freight_type="CIF")
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    c = session.get(Cotacao, cot.id)
    assert ws.avaliar(session, c, frete=ws.frete_para_avaliar(session, c)).blockers, "CIF automático irresolvido bloqueia"
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="350.00", freight_manual_confirmado="sim",
                     freight_manual_obs="cotação Transal por telefone")
    c = session.get(Cotacao, cot.id)
    assert c.freight_manual_confirmado is True and c.freight_manual_por and c.freight_manual_em
    frete = ws.frete_para_avaliar(session, c)
    assert frete["status"] == wf.FRETE_MANUAL_CONFIRMADO and frete["valor"] == 350.0
    pront = ws.avaliar(session, c, frete=frete)
    assert pront.blockers == [] and pront.pode_emitir
    av = com.avaliar_negociacao(session, c)
    assert av.frete_no_total and av.total_proposta == av.subtotal_negociado + X("350.00")
    # o frete não entra no preço unitário nem na comissão
    assert av.itens[0].comissao_item.base_comissionavel == dinheiro(av.itens[0].linha.faturamento * X("0.82"))
    snap = ws.emitir(session, c, ator=admin, frete=frete)
    session.commit()
    assert json.loads(snap.frete_json)["status"] == wf.FRETE_MANUAL_CONFIRMADO
    assert json.loads(snap.frete_json)["valor"] == 350.0


def test_48_fob_e_a_combinar_nao_bloqueiam_e_o_pdf_diz_a_combinar(session, fornecedores):
    from app.pdf_bridge import _texto_frete
    for tipo in ("FOB", "A_COMBINAR"):
        cot = nova_cotacao(session, freight_type=tipo)
        add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
        c = session.get(Cotacao, cot.id)
        assert ws.frete_para_avaliar(session, c) is None
        assert ws.avaliar(session, c).blockers == []
    rotulo, valor = _texto_frete("A_COMBINAR", None, None, rascunho=False)
    assert "combinar" in rotulo.lower() and "não incluído" in rotulo.lower() and valor is None
    assert "A_COMBINAR" not in rotulo


def test_48_desconfirmar_o_frete_volta_a_bloquear(session, fornecedores):
    cot = nova_cotacao(session, freight_type="CIF")
    add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="200", freight_manual_confirmado="sim")
    salvar_cabecalho(session, cot, freight_type="CIF", freight_valor="200", freight_manual_confirmado="")
    c = session.get(Cotacao, cot.id)
    assert c.freight_manual_confirmado is False and c.freight_manual_por is None
    assert ws.avaliar(session, c, frete=ws.frete_para_avaliar(session, c)).blockers


# ===========================================================================
# §49 — compatibilidade legada
# ===========================================================================
def test_49_item_da_politica_16_09_continua_avaliado_pela_mecanica_antiga(session, fornecedores, admin):
    """Um rascunho com item pinado em 16/09: piso, comissão única e travado continuam; a tela
    detecta a política anterior; nada é convertido sem ação explícita."""
    from politica_legada import politica_16_09_vigente
    with politica_16_09_vigente(session):
        cot = nova_cotacao(session)
        daune = add_item(session, cot, produto_nacional(session, fornecedores, codigo="DAUNE", custo=79.86, familia="Pillow"))
        decor = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    assert daune.politica_comercial == pol.ROTULO and daune.preco_travado is True
    assert decor.piso_margem_pct == aprox(0.10)
    # hoje (21/09 vigente): a mecânica de cada item é a que ele pinou
    c = session.get(Cotacao, cot.id)
    av = com.avaliar_negociacao(session, c)
    assert all(a.versao_politica == "2026-09-16" for a in av.itens)
    assert av.comissao is not None and av.comissao.variavel_pct == X("0.10")
    with pytest.raises(com.PrecoTravado):
        com.avaliar_negociacao(session, c, propostas={daune.id: float(daune.preco_recomendado) - 1})
    from app import admin_service as adm
    det = adm.premissas_desatualizadas(session, c, ws.itens_de(session, c.id))
    assert det["desatualizado"] and len(det["politica_anterior"]) == 2
    assert det["politica_anterior"][0]["politica_vigente"] == pol.ROTULO_2026_09_21
    # ação explícita: atualizar para a política vigente → recalculado integralmente
    from app.routers.cotacoes import atualizar_premissas
    from tests.crisis.conftest import chamar
    chamar(atualizar_premissas, RequestFalsa(admin), cotacao_id=c.id, session=session)
    session.commit(); session.expire_all()
    itens = {it.id: it for it in ws.itens_de(session, c.id)}
    assert all(it.politica_comercial == pol.ROTULO_2026_09_21 for it in itens.values())
    assert itens[daune.id].preco_travado is False and itens[daune.id].margem_padrao_pct == aprox(0.13)
    assert itens[daune.id].preco_tabela == aprox(2 * itens[daune.id].preco_recomendado, abs=0.011)
    assert itens[decor.id].comissao_faixa_pct == aprox(0.05) and itens[decor.id].piso_margem_pct is None


def test_49_snapshot_emitido_nao_muda_e_revisao_declara_politica(session, fornecedores, admin, owner):
    from app.models import SnapshotEmissao
    from politica_legada import politica_16_09_vigente
    with politica_16_09_vigente(session):
        cot = nova_cotacao(session)
        it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
        c = session.get(Cotacao, cot.id)
        snap = ws.emitir(session, c, ator=owner)
        session.commit()
    antes = (snap.itens_json, snap.totais_json, snap.fingerprint)
    # com a política nova vigente, nada no snapshot nem no item emitido muda
    c = session.get(Cotacao, cot.id)
    ws.avaliar(session, c)
    com.avaliar_negociacao(session, c)
    session.expire_all()
    snap2 = session.get(SnapshotEmissao, snap.id)
    assert (snap2.itens_json, snap2.totais_json, snap2.fingerprint) == antes
    it2 = session.get(CotacaoItem, it.id)
    assert it2.politica_comercial == pol.ROTULO and it2.preco_tabela is None
    # revisão: copia os itens como estão (política antiga pinada) e a detecção aponta
    rev = ws.criar_revisao(session, c, ator=owner)
    session.commit()
    from app import admin_service as adm
    det = adm.premissas_desatualizadas(session, rev, ws.itens_de(session, rev.id))
    assert det["politica_anterior"], "a revisão declara que ainda usa a política anterior"


def test_49_duplicar_cotacao_da_politica_nova_nao_cria_hibrido(session, fornecedores, admin):
    from app.routers.cotacoes import duplicar
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_nacional(session, fornecedores, custo=100.0))
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.30"}}, ator=admin)
    session.commit()
    resp = duplicar(cot.id, session)
    nova_id = int(resp.headers["location"].split("/cotacoes/")[1].split("?")[0])
    novo = ws.itens_de(session, nova_id)[0]
    assert novo.politica_comercial == pol.ROTULO_2026_09_21 and novo.modo_edicao == "desconto"
    assert novo.preco_negociado == aprox(session.get(CotacaoItem, it.id).preco_negociado)
    assert novo.desconto_editado_pct == aprox(0.30, abs=1e-3) and novo.preco_tabela > 0


# ===========================================================================
# §47 — primeiro acesso: perfil autorizado pelo servidor, sem elevação
# ===========================================================================
def test_47_primeiro_acesso_confirma_perfil_e_recusa_elevacao(session, owner):
    from app import recuperacao_senha as rs
    from app.auth import hash_senha
    from app.models import Usuario
    from app.routers.login import redefinir_senha_form, redefinir_senha_submit
    import secrets
    vend = Usuario(email=f"nova{secrets.token_hex(3)}@anara.test", nome="Nova Vendedora",
                   senha_hash=hash_senha(secrets.token_urlsafe(16)), papel="VENDEDOR_COMISSIONADO")
    admn = Usuario(email=f"novo{secrets.token_hex(3)}@anara.test", nome="Novo Administrativo",
                   senha_hash=hash_senha(secrets.token_urlsafe(16)), papel="ADMIN")
    session.add(vend); session.add(admn); session.commit()
    tok_v = rs.gerar(session, vend, rs.PRIMEIRO_ACESSO, ator=owner)
    tok_a = rs.gerar(session, admn, rs.PRIMEIRO_ACESSO, ator=owner)
    session.commit()
    # a tela mostra o perfil autorizado
    html = redefinir_senha_form(RequestFalsa(None), token=tok_v, session=session).body.decode()
    assert "Vendedora" in html and 'value="vendedora" checked' in html and "Administrativo" in html
    # vendedora tenta forjar "administrativo": recusado, papel intacto, token continua válido
    r = redefinir_senha_submit(RequestFalsa(None), token=tok_v, senha="senhaforte1", confirmar="senhaforte1",
                               perfil="administrativo", session=session)
    assert r.status_code == 403 and "autorizado como Vendedora" in r.body.decode()
    session.refresh(vend)
    assert vend.papel == "VENDEDOR_COMISSIONADO" and rs.validar(session, tok_v) is not None
    # confirmando o perfil autorizado, passa; e ela não vira admin nem owner
    r = redefinir_senha_submit(RequestFalsa(None), token=tok_v, senha="senhaforte1", confirmar="senhaforte1",
                               perfil="vendedora", session=session)
    assert r.status_code == 200 and "Acesso criado" in r.body.decode()
    session.refresh(vend)
    assert vend.papel == "VENDEDOR_COMISSIONADO" and vend.ve_economia is False and vend.administra is False
    # admin autorizado confirma "administrativo"
    r = redefinir_senha_submit(RequestFalsa(None), token=tok_a, senha="senhaforte2", confirmar="senhaforte2",
                               perfil="administrativo", session=session)
    assert r.status_code == 200
    session.refresh(admn)
    assert admn.papel == "ADMIN"
    # ninguém pede OWNER por aqui
    assert "owner" not in rs.PERFIS and "proprietário" not in [v.lower() for v in rs.PERFIS.values()]
    with pytest.raises(rs.PerfilNaoAutorizado):
        rs.conferir_perfil(vend, "owner")
    # a rota pública de primeiro OWNER continua fechada com usuários existentes
    from app.routers.login import primeiro_acesso_form
    assert primeiro_acesso_form(RequestFalsa(None), session=session).status_code == 303

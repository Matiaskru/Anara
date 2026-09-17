"""§18 — 60 goldens independentes (fórmulas em `decimal`, geradas fora do motor) × motor.

Cada golden traz o waterfall inteiro: CNET, ICMS (+DIFAL+FCP quando do remetente), PIS/COFINS
efetivo, encargo, comissão de formação, lucro, margem e preço. O motor tem de reproduzir
cada componente ao centavo, e o cenário fiscal tem de resolver para as mesmas alíquotas
quando montado pelo runtime (tabelas do banco de teste, semeadas iguais às de produção).
"""
import json
import os
from decimal import Decimal

import pytest

from app.dinheiro import D
from app.pricing_engine import TaxRuleSet, calcular_por_margem, calcular_por_preco, com_comissao_fixa

GOLDENS = json.load(open(os.path.join(os.path.dirname(__file__), "goldens.json"), encoding="utf-8"))


@pytest.mark.parametrize("g", GOLDENS, ids=[f"{g['id']:03d}-{g['politica']}-{g['cenario'][:12]}" for g in GOLDENS])
def test_golden_waterfall_ao_centavo(g):
    regras = TaxRuleSet(icms_pct=Decimal(g["icms_pct"]), pis_cofins_pct=Decimal(g["pis_cofins_efetivo_pct"]),
                        encargo_financeiro_pct=Decimal(g["encargo_pct"]),
                        comissao_tabela=[(Decimal(0), Decimal(g["comissao_formacao_pct"]))])
    r = calcular_por_margem(Decimal(g["cnet"]), g["quantidade"], Decimal(g["margem_alvo"]), regras)
    assert r.preco_negociado == Decimal(g["preco"])
    assert r.faturamento == Decimal(g["receita"])
    assert r.impostos == Decimal(g["impostos"])
    assert r.comissao == Decimal(g["comissao"])
    assert r.custo_total == Decimal(g["custo_total"])
    assert r.lucro == Decimal(g["lucro"])
    assert abs(r.margem_liquida - Decimal(g["margem_realizada"])) < Decimal("1e-20")
    assert r.reconcilia()
    # no piso com comissão mínima: o preço-piso do golden é exatamente o que o motor forma
    piso = calcular_por_margem(Decimal(g["cnet"]), 1, Decimal(g["piso"]), com_comissao_fixa(regras, Decimal("0.05")))
    assert piso.preco_negociado == Decimal(g["preco_no_piso_com_comissao_minima"])


@pytest.mark.parametrize("g", [g for g in GOLDENS if g["politica"] != "PERSONALIZADO_190x250_300TC"][::3],
                         ids=lambda g: f"{g['id']:03d}")
def test_golden_cenario_fiscal_resolve_igual_no_runtime(session, g):
    """As alíquotas do golden são as que o runtime resolve para (natureza, destino,
    contribuinte, finalidade, condição) nas tabelas semeadas."""
    from app import pricing_service as ps
    from app.models import Cotacao, Fornecedor, Produto
    from sqlmodel import select
    codigo = "KTC" if g["natureza"] == "IMPORTADA" else "DAUNE"
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == codigo)).first()
    produto = Produto(sku_key="__golden__", nome="golden", fornecedor_id=forn.id, familia="Flat Sheet")
    cot = Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino=g["destino"], contribuinte_icms=g["contribuinte"],
                  finalidade=g["finalidade"], condicao_pagamento=g["condicao"], freight_type="FOB")
    regras, ctx = ps.regras_da_cotacao(session, cot, produto, comissao_formacao_pct=float(Decimal(g["comissao_formacao_pct"])))
    assert regras is not None, ctx.get("motivo_bloqueio")
    assert regras.icms_pct == Decimal(g["icms_pct"])
    assert regras.pis_cofins_pct == Decimal(g["pis_cofins_efetivo_pct"])
    assert regras.encargo_financeiro_pct == Decimal(g["encargo_pct"])
    assert D(ctx["fcp_pct"] or 0) == Decimal(g["fcp_pct"])
    r = calcular_por_margem(Decimal(g["cnet"]), g["quantidade"], Decimal(g["margem_alvo"]), regras)
    assert r.preco_negociado == Decimal(g["preco"])

"""Rodada de correção pós-auditoria (13/10/2026).

1. Comissão FIXA por regra (Daune/Decor): 5% em qualquer preço, sem escada; KTC intacto.
2. Status: peso estimado do próprio SKU e EXW calculado pelo motor saem ESTIMADO (nunca
   CONFIRMADO); o registro de cotação KTC não grava CONFIRMADO com peso estimado.
"""
from datetime import date
from decimal import Decimal

from sqlmodel import select

from app import admin_service as adm
from app import calculadora as calc
from app import comercial_service as com
from app import governanca_produtos as gov
from app import politica_comercial as pol
from app import pricing_service as ps
from app.margin_rules import resolver_margem
from app.models import Cotacao, Fornecedor, MargemRegra, Produto
from conftest import _novo_usuario
from decimais import aprox
from tests.crisis.conftest import add_item, nova_cotacao, produto_nacional

X = Decimal


# ---------------------------------------------------------------- 1. comissão fixa
def test_comissao_fixa_nao_segue_a_escada():
    for preco, escada in (("200.00", "0.10"), ("180.00", "0.09"), ("140.00", "0.07"), ("100.00", "0.05")):
        normal = pol.comissao_do_item(X("200"), X("100"), X(preco), 1, X("0.18"))
        fixa = pol.comissao_do_item(X("200"), X("100"), X(preco), 1, X("0.18"), fixa_pct=X("0.05"))
        assert normal.taxa_pct == X(escada)
        assert fixa.taxa_pct == X("0.05")
        # base da comissão é a mesma (receita − ICMS): só a taxa muda
        assert fixa.base_comissionavel == normal.base_comissionavel


def test_resolvedor_propaga_a_marca_e_admin_herda():
    r = MargemRegra(id=1, nome="x", margem_pct=0.08, comissao_formacao_pct=0.05, politica="P",
                    comissao_fixa=True)
    m = resolver_margem([r])
    assert m.comissao_fixa is True and m.como_dict()["comissao_fixa"] is True
    r2 = MargemRegra(id=2, nome="y", margem_pct=0.2, comissao_formacao_pct=0.05, politica="P")
    assert resolver_margem([r2]).comissao_fixa is False


def test_item_daune_com_regra_fixa_paga_5pct_ate_na_tabela(session):
    forn = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    owner = _novo_usuario("OWNER")
    owner.can_manage_economics = True
    p = produto_nacional(session, forn, codigo="DAUNE", custo=79.86, familia="Pillow")
    p.sku_key = "__rev2_comissao_fixa__"
    session.add(p)
    session.commit()
    # regra de SKU (escopo mínimo, não afeta outros testes): 8% + comissão fixa
    kw = dict(margem_pct=0.08, nome="teste rev2 — fixa", sku_key=p.sku_key, prioridade=1,
              fonte="teste", vigente_a_partir_de=date.today(), comissao_formacao_pct=0.05,
              comissao_fixa=True)
    regra = adm.aplicar_margem(session, adm.preview_margem(session, **kw), ator=owner, **kw)
    regra.politica = pol.ROTULO_2026_09_21
    session.add(regra)
    session.commit()
    cot = nova_cotacao(session)
    it = add_item(session, cot, p)
    assert it.comissao_fixa is True and it.margem_padrao_pct == aprox(0.08)
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: float(it.preco_tabela)},
                                ator=owner)
    session.commit()
    item = next(a for a in av.itens if a.item.id == it.id)
    assert item.comissao_item.taxa_pct == X("0.05")        # na TABELA: 5%, não 10%
    assert item.comissao_item.desconto_vs_tabela == 0


def test_item_ktc_continua_na_escada(session):
    from tests.crisis.conftest import produto_ktc_cotado
    forn = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    owner = _novo_usuario("OWNER")
    cot = nova_cotacao(session)
    it = add_item(session, cot, produto_ktc_cotado(session, forn))
    assert it.comissao_fixa is None
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: float(it.preco_tabela)},
                                ator=owner)
    session.commit()
    item = next(a for a in av.itens if a.item.id == it.id)
    assert item.comissao_item.taxa_pct == X("0.10")


# ---------------------------------------------------------------- 2. status
def test_peso_estimado_proprio_sai_estimado(session):
    p = calc.produto_simulado(session, familia="Bath Towel", largura_cm=86, comprimento_cm=150,
                              gsm=550, composicao_toalha="90/10")
    p.cost_method, p.exw_cotado_usd, p.exw_cotado_data = "KTC_QUOTED", 6.03, date.today()
    p.peso_kg, p.peso_tipo = 0.71, "REAL KTC"
    c, m = ps.custo_para_precificar(session, p)
    assert ps.status_canonico_do_custo(c, m) == "CONFIRMADO"
    p.peso_tipo = "ESTIMADO"
    c2, m2 = ps.custo_para_precificar(session, p)
    assert m2["peso_estimado_proprio"] is True and c2 == c          # mesmo custo, outro status
    assert ps.status_canonico_do_custo(c2, m2) == "ESTIMADO"


def test_exw_do_motor_industrial_sai_estimado(session):
    p = calc.produto_simulado(session, familia="Top Sheet", largura_cm=190, comprimento_cm=250,
                              material_id=1)
    c, m = ps.custo_para_precificar(session, p)
    assert m.get("exw_calculado_pelo_motor") is True and c
    assert ps.status_canonico_do_custo(c, m) == "ESTIMADO"


def test_registrar_cotacao_ktc_com_peso_estimado_grava_estimado(session):
    from tests.crisis.conftest import produto_ktc_cotado
    forn = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    owner = _novo_usuario("OWNER")
    owner.can_manage_economics = True
    p = produto_ktc_cotado(session, forn)
    r = gov.registrar_exw_ktc(session, p, exw_usd=17.0, data_ref=date.today(), documento="doc",
                              fonte="teste", motivo="teste", ator=owner, peso_kg=0.58,
                              peso_tipo="ESTIMADO")
    assert r["status"] == "ESTIMADO" and r["status_vivo"] == "ESTIMADO"
    session.rollback()

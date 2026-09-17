"""CR-04/CR-05/CR-06 — motor recusa entrada inválida; status canônico do custo não promove
cotação envelhecida, custo com pedido de revisão, preço histórico sem evidência nem
nacionalização com premissa faltando a CONFIRMADO."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import calculadora as calc
from app import pricing_service as ps
from app.ktc_engine import ParametrosKTC, calcular_flat_sheet, calcular_toalha
from app.nationalization import PremissasNacionalizacao, nacionalizar
from tests.crisis.conftest import add_item, nova_cotacao, produto_ktc_cotado, produto_nacional


def _parametros():
    return ParametrosKTC(material_price_usd_m2=Decimal("1.4"), cmt_usd=Decimal("0.6"),
                         shrinkage=Decimal("0.05"), waste=Decimal("0.03"),
                         quality_allowance=Decimal("0.01"), ktc_margin=Decimal("0.15"),
                         hem_width_total_cm=Decimal("4"), hem_length_total_cm=Decimal("4"),
                         price_usd_kg=Decimal("6.5"))


@pytest.mark.parametrize("largura,comprimento", [(-190, 250), (0, 250), (190, 0), (190, -1), (None, 250), ("x", 250)])
def test_cr04_tecido_plano_recusa_medida_nao_positiva(largura, comprimento):
    r = calcular_flat_sheet(largura, comprimento, _parametros())
    assert r.status == "REVIEW_REQUIRED" and r.exw_usd is None


@pytest.mark.parametrize("largura,comprimento,gsm", [(-1, 1, 300), (70, 140, -5), (70, 140, 0), (0, 0, 450)])
def test_cr04_toalha_recusa_medida_ou_gsm_nao_positivos(largura, comprimento, gsm):
    r = calcular_toalha(largura, comprimento, gsm, _parametros())
    assert r.status == "REVIEW_REQUIRED" and r.exw_usd is None


def test_cr04_nacionalizacao_recusa_exw_nao_positivo():
    prem = PremissasNacionalizacao(fx_usd_brl=Decimal("5.19"), frete_usd_kg=Decimal("0.516"),
                                   outras_desp_usd_un=Decimal("0.25"))
    for exw in (0, -3.2, Decimal("-0.01")):
        r = nacionalizar(exw, 0.8, 0.35, prem)
        assert r.net_brl is None
    assert nacionalizar(10, 0.8, 0.35, prem).net_brl > 0


def test_cr04_calculadora_recusa_entrada_invalida_sem_preco(session):
    for kw in (dict(largura_cm=-190, comprimento_cm=250), dict(largura_cm=0, comprimento_cm=250),
               dict(largura_cm=190, comprimento_cm=None), dict(largura_cm=5000, comprimento_cm=250)):
        m = calc.calcular(session, "Flat Sheet", material_id=9, quantidade=1, **kw)
        assert m["calculavel"] is False and m.get("entrada_invalida")
    for gsm in (-5, 0, None):
        m = calc.calcular(session, "Bath Towel", 70, 140, gsm=gsm, quantidade=1)
        assert m["calculavel"] is False
    with pytest.raises(calc.EntradaInvalida):
        calc.salvar_no_catalogo(session, "Flat Sheet", -190, 250, material_id=9)
    with pytest.raises(calc.EntradaInvalida):
        calc.salvar_no_catalogo(session, "Pillow Case", 50, 70, material_id=9, abas=1)


def test_cr04_calculadora_valida_continua_calculando(session):
    m = calc.calcular(session, "Flat Sheet", 190, 250, material_id=9, quantidade=1)
    assert m["calculavel"] is True
    assert (m.get("custo") or {}).get("net_brl", 0) > 0


# ---------------------------------------------------------------------------
# Status canônico do custo
# ---------------------------------------------------------------------------
def test_cr06_cotacao_direta_fresca_e_confirmado(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, data=date.today())
    custo, mem = ps.custo_para_precificar(session, p)
    assert custo and ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO"


def test_cr06_cotacao_direta_envelhecida_e_revalidar_e_nao_bloqueia_proposta(session, fornecedores):
    from app import workflow as wf
    p = produto_ktc_cotado(session, fornecedores, data=date.today() - timedelta(days=120))
    custo, mem = ps.custo_para_precificar(session, p)
    assert custo and ps.status_canonico_do_custo(custo, mem) == "REVALIDAR"
    cot = nova_cotacao(session)
    it = add_item(session, cot, p)
    assert it.status_custo_item == "REVALIDAR" and it.preco_negociado > 0
    assert not wf.blockers_do_item(it)                        # emite proposta
    from app import workflow_service as ws
    comp = ws.validar_compromisso_firme(session, session.get(type(cot), cot.id))
    assert not comp.pode and any("REVALIDAR" in m for m in comp.impedimentos)   # não compromete


def test_cr06_cotacao_direta_sem_data_e_revalidar(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, data=None)
    p.exw_cotado_data = None
    session.add(p)
    session.commit()
    custo, mem = ps.custo_para_precificar(session, p)
    assert ps.status_canonico_do_custo(custo, mem) == "REVALIDAR"


def test_cr06_preco_ktc_historico_sem_evidencia_e_review_required(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=None)
    p.exw_cotado_usd = None
    p.preco_ktc_usd = 9.5
    session.add(p)
    session.commit()
    custo, mem = ps.custo_para_precificar(session, p)
    assert custo and mem["net_fonte"] == ps.CUSTO_HISTORICO_SEM_EVIDENCIA
    assert ps.status_canonico_do_custo(custo, mem) == "REVIEW_REQUIRED"


def test_cr05_nacionalizacao_com_ii_desconhecido_nao_e_confirmado(session, fornecedores):
    # Duvet Insert: a regra de NCM cadastrada não tem I.I. confiável (ii_preferencial nulo) e
    # o produto não traz `ii_aplicado` — até 17/09/2026 isso virava I.I. = 0 e CONFIRMADO.
    p = produto_ktc_cotado(session, fornecedores, familia="Duvet Insert", ncm="9404.40.00",
                           ii_aplicado=None, thread_count=None)
    custo, mem = ps.custo_para_precificar(session, p)
    assert "ii" in mem.get("premissas_faltantes", []), mem.get("avisos")
    assert ps.status_canonico_do_custo(custo, mem) == "REVIEW_REQUIRED"


def test_cr06_nacional_com_pedido_de_revisao_e_revalidar(session, fornecedores):
    p = produto_nacional(session, fornecedores, custo=120.0, precisa_revisao=True,
                         revisao_motivo="confirmar se é custo ou preço de venda")
    custo, mem = ps.custo_para_precificar(session, p)
    assert ps.status_canonico_do_custo(custo, mem) == "REVALIDAR"
    q = produto_nacional(session, fornecedores, custo=120.0, precisa_revisao=False)
    custo, mem = ps.custo_para_precificar(session, q)
    assert ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO"

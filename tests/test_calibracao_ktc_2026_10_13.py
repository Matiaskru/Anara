"""Calibração da fórmula KTC por construção (13/10/2026).

A fórmula continua sendo o motor; o fator corrige o nível de preço da construção contra as
cotações reais (mediana × 1,02), só para cima, e a cotação direta do SKU continua vencendo.
"""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import calculadora as calc
from app import calibracao_ktc as ck
from app import pricing_service as ps
from app.models import CalibracaoKTC, MaterialPreco

M250 = "250TC Sateen CVC 70/30"
M300 = "300TC Sateen 100% Cotton"


# ---------------------------------------------------------------- regra pura
def test_formula_aderente_ou_conservadora_nao_calibra():
    r = ck.calcular_fator([0.96, 0.97, 1.015])          # até 2% abaixo do real: aderente
    assert r["calibrou"] is False and r["fator_final"] == 1.0 and r["seguranca_pct"] == 0.0


def test_exemplo_do_matias_mediana_mais_2pct():
    r = ck.calcular_fator([1.096, 1.103, 1.111])
    assert r["fator_base"] == pytest.approx(1.103)
    assert r["fator_final"] == pytest.approx(1.1251)     # 1,103 × 1,02 = 1,12506 → teto 4 casas
    assert ck.qa([(11.11, 10.0), (11.03, 10.0), (10.96, 10.0)], r["fator_final"])["ok"]


def test_fator_nunca_reduz_e_qa_acusa_mistura():
    r = ck.calcular_fator([0.90, 0.95, 1.05])            # mediana < 1: calibrar não pode baixar
    assert r["fator_final"] == 1.0
    q = ck.qa([(10.5, 10.0)], r["fator_final"])          # a âncora 5% acima continua subestimada
    assert q["ok"] is False and q["pior_erro"] == pytest.approx(0.05)


def test_sem_ancora_nao_inventa_fator():
    with pytest.raises(ValueError):
        ck.calcular_fator([])


# ---------------------------------------------------------------- motor
def _mat(session, nome):
    return session.exec(select(MaterialPreco).where(MaterialPreco.material == nome,
                                                    MaterialPreco.plain_or_stripe == "plain")).first().id


def _cal(session, **kw):
    base = dict(construcao="TESTE", grupo="TODAS", familias="Top Sheet,Bottom Sheet,Duvet Cover,Pillow Case",
                material=M250, plain_or_stripe="plain", fator_base=1.1, seguranca_pct=0.02, fator_final=1.1314,
                n_ancoras=3, valid_from=date.today() - timedelta(days=1), ativo=True, fonte="teste")
    base.update(kw)
    c = CalibracaoKTC(**base)
    session.add(c)
    session.flush()
    return c


def _lencol(session, material=M250, familia="Top Sheet", **kw):
    return calc.produto_simulado(session, familia=familia, largura_cm=275, comprimento_cm=250,
                                 material_id=_mat(session, material), **kw)


def test_formula_calibrada_sobe_o_exw_e_vende_normalmente(session):
    _c0, m0 = ps.custo_para_precificar(session, _lencol(session))
    assert m0["metodo_exw"] == "KTC_CALCULATED" and ps.status_canonico_do_custo(_c0, m0) == "ESTIMADO"
    _cal(session)
    c, m = ps.custo_para_precificar(session, _lencol(session))
    assert m["metodo_exw"] == "KTC_CALCULATED_CALIBRATED"
    assert m["exw_usd"] == pytest.approx(m["exw_formula_usd"] * 1.1314)
    assert m["exw_formula_usd"] == pytest.approx(m0["exw_usd"])
    assert not m.get("exw_calculado_pelo_motor")
    assert ps.status_canonico_do_custo(c, m) == "CONFIRMADO" and c > _c0
    session.rollback()


def test_fator_menor_que_1_no_banco_nao_reduz(session):
    _c0, m0 = ps.custo_para_precificar(session, _lencol(session))
    _cal(session, fator_final=0.90)
    _c, m = ps.custo_para_precificar(session, _lencol(session))
    assert m["exw_usd"] == pytest.approx(m0["exw_usd"])
    session.rollback()


def test_cotacao_direta_vence_a_calibracao(session):
    _cal(session)
    p = _lencol(session)
    p.cost_method, p.exw_cotado_usd, p.exw_cotado_data = "KTC_QUOTED", 9.89, date.today()
    _c, m = ps.custo_para_precificar(session, p)
    assert m["exw_usd"] == pytest.approx(9.89) and m["metodo_exw"] == "KTC_DIRECT_QUOTE"
    assert "calibracao_ktc" not in m
    session.rollback()


def test_outra_construcao_nao_herda_o_fator(session):
    _cal(session)
    _c, m = ps.custo_para_precificar(session, _lencol(session, material=M300))
    assert m["metodo_exw"] == "KTC_CALCULATED" and m.get("exw_calculado_pelo_motor") is True
    session.rollback()


def test_registro_mais_especifico_vence_fronha_por_abas(session):
    _cal(session, material=M300, familias="Pillow Case", fator_final=1.0, grupo="FRONHA")
    _cal(session, material=M300, familias="Pillow Case", abas=2, fator_final=1.1213, grupo="FRONHA_2_ABAS")
    f2 = calc.produto_simulado(session, familia="Pillow Case", largura_cm=50, comprimento_cm=70,
                               material_id=_mat(session, M300), abas=2, flap_cm=20)
    f4 = calc.produto_simulado(session, familia="Pillow Case", largura_cm=50, comprimento_cm=70,
                               material_id=_mat(session, M300), abas=4, flap_cm=20)
    assert ps.calibracao_ktc(session, f2).grupo == "FRONHA_2_ABAS"
    assert ps.calibracao_ktc(session, f4).grupo == "FRONHA"
    session.rollback()


def test_vigencia_futura_nao_vale_hoje(session):
    _cal(session, valid_from=date.today() + timedelta(days=10))
    _c, m = ps.custo_para_precificar(session, _lencol(session))
    assert m["metodo_exw"] == "KTC_CALCULATED"
    session.rollback()


def test_construcao_bloqueada_vira_sob_consulta(session):
    _cal(session, bloqueio="fórmula abaixo de construção inferior cotada; sem âncora", fator_final=1.0)
    c, m = ps.custo_para_precificar(session, _lencol(session))
    assert ps.CALIBRACAO_BLOQUEADA in m["premissas_faltantes"]
    assert ps.status_canonico_do_custo(c, m) == "REVIEW_REQUIRED"
    assert "calibracao_ktc" not in m or m.get("bloqueio_calibracao")
    session.rollback()


def test_calibracao_provisoria_aplica_o_fator_mas_fica_estimado(session):
    _c0, m0 = ps.custo_para_precificar(session, _lencol(session))
    _cal(session, provisoria=True)
    c, m = ps.custo_para_precificar(session, _lencol(session))
    assert m["metodo_exw"] == "KTC_CALCULATED_CALIBRATED" and m["calibracao_ktc"]["provisoria"] is True
    assert m["exw_usd"] == pytest.approx(m0["exw_usd"] * 1.1314)       # o fator vale (preço conservador)…
    assert ps.status_canonico_do_custo(c, m) == "ESTIMADO"              # …mas sem cotação da construção
    session.rollback()

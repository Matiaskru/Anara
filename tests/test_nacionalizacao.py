"""Nacionalização KTC — do EXW ao custo NET em reais."""
import pytest

from app.nationalization import PremissasNacionalizacao, nacionalizar

PREMISSAS = PremissasNacionalizacao(frete_usd_kg=0.516, outras_desp_usd_un=0.2487532709,
                                    fx_usd_brl=5.11)


def test_cadeia_completa_de_nacionalizacao():
    r = nacionalizar(8.81, 0.80, 0.035, PREMISSAS)
    assert r.frete_usd == pytest.approx(0.4128)
    assert r.ii_usd == pytest.approx(0.322798)
    assert r.net_usd == pytest.approx(9.7943512709, abs=1e-9)
    assert r.net_brl == pytest.approx(50.0491, abs=0.001)


def test_imposto_incide_sobre_exw_mais_frete():
    r = nacionalizar(10.0, 1.0, 0.10, PREMISSAS)
    assert r.ii_usd == pytest.approx((10.0 + 0.516) * 0.10)


def test_sem_peso_avisa_em_vez_de_chutar():
    r = nacionalizar(10.0, None, 0.035, PREMISSAS)
    assert r.frete_usd == 0
    assert any("peso" in a.lower() for a in r.avisos)


def test_sem_ii_confiavel_avisa():
    r = nacionalizar(10.0, 1.0, None, PREMISSAS)
    assert any("importação" in a.lower() for a in r.avisos)


def test_waterfall_da_nacionalizacao_e_auditavel():
    r = nacionalizar(8.81, 0.80, 0.035, PREMISSAS)
    nomes = [e.nome for e in r.etapas]
    assert nomes == ["EXW KTC", "Frete internacional", "Base do Imposto de Importação",
                     "Imposto de Importação", "Custo NET", "Custo NET em reais"]

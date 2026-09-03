"""Cenário fiscal da venda: origem × destino × contribuinte, com três campos independentes."""
import pytest
from sqlmodel import select

from app.fiscal_rules import resolver_icms_estruturado
from app.models import EstadoFiscal, RegraFiscalVenda


@pytest.fixture
def tabelas(session):
    return (session.exec(select(RegraFiscalVenda)).all(),
            session.exec(select(EstadoFiscal)).all())


def icms(tabelas, origem, destino, contribuinte):
    regras, estados = tabelas
    return resolver_icms_estruturado(regras, estados, origem, destino, contribuinte)[0]


def test_sp_para_sp_contribuinte_e_18(tabelas):
    """Regra nova: dentro de SP é 18% mesmo para contribuinte (antes o sistema usava 4%)."""
    assert icms(tabelas, "São Paulo", "São Paulo", True) == pytest.approx(0.18)


def test_sp_para_sp_nao_contribuinte_e_18(tabelas):
    assert icms(tabelas, "São Paulo", "São Paulo", False) == pytest.approx(0.18)


def test_interestadual_contribuinte_e_4(tabelas):
    assert icms(tabelas, "São Paulo", "Minas Gerais", True) == pytest.approx(0.04)


@pytest.mark.parametrize("destino,esperado", [
    ("Minas Gerais", 0.1707), ("Bahia", 0.2275), ("Piauí", 0.2587),
    ("Rio de Janeiro", 0.20), ("Santa Catarina", 0.13), ("Acre", 0.1852),
    ("Mato Grosso", 0.13), ("Maranhão", 0.19), ("Amapá", 0.14),
])
def test_interestadual_nao_contribuinte_usa_a_carga_final(tabelas, destino, esperado):
    assert icms(tabelas, "São Paulo", destino, False) == pytest.approx(esperado)


def test_carga_final_nao_e_recalculada_por_base_dupla(tabelas):
    """Amapá tem base dupla 17,07% e carga final 14% — vale a carga final, como está."""
    _regras, estados = tabelas
    amapa = next(e for e in estados if e.uf == "AP")
    assert amapa.base_dupla == pytest.approx(0.1707)
    assert icms(tabelas, "São Paulo", "Amapá", False) == pytest.approx(0.14)


def test_mesmo_estado_fora_de_sp_usa_aliquota_interna(tabelas):
    assert icms(tabelas, "Paraná", "Paraná", False) == pytest.approx(0.195)


def test_contribuinte_nao_e_inferido_do_estado(tabelas):
    """Mesmo par de estados, respostas diferentes conforme o campo contribuinte."""
    assert icms(tabelas, "São Paulo", "Bahia", True) != icms(tabelas, "São Paulo", "Bahia", False)


def test_cenario_desconhecido_cai_no_fallback_com_aviso(tabelas):
    regras, estados = tabelas
    aliquota, regra = resolver_icms_estruturado(regras, estados, "São Paulo", "Lisboa", False,
                                                fallback=0.18)
    assert aliquota == pytest.approx(0.18)
    assert "não encontrado" in regra.lower()

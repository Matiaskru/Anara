"""Margens líquidas-alvo padrão por fornecedor e família."""
import pytest
from sqlmodel import select

from app.margin_rules import resolver_margem
from app.models import Fornecedor, MargemRegra
from decimais import MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture
def regras(session):
    return session.exec(select(MargemRegra)).all()


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f.id for f in session.exec(select(Fornecedor)).all()}


def margem(regras, fornecedores, codigo, familia=None, tc=None, sku=None, override=None):
    return resolver_margem(regras, fornecedor_id=fornecedores.get(codigo), familia=familia,
                           thread_count=tc, sku_key=sku, override_pct=override).margem_pct


@pytest.mark.parametrize("familia", ["Bath Towel", "Hand Towel", "Face Towel", "Pool Towel",
                                     "Beach Towel", "Bath Mat"])
def test_toalhas_ktc_ficam_em_12(regras, fornecedores, familia):
    assert margem(regras, fornecedores, "KTC", familia) == aprox(0.12)


def test_roupao_ktc_fica_em_12(regras, fornecedores):
    assert margem(regras, fornecedores, "KTC", "Bathrobe") == aprox(0.12)


@pytest.mark.parametrize("tc", [200, 230, 233, 250])
def test_lencol_abaixo_de_300tc_fica_em_16(regras, fornecedores, tc):
    assert margem(regras, fornecedores, "KTC", "Flat Sheet", tc) == aprox(0.16)


@pytest.mark.parametrize("tc", [300, 400, 500])
def test_lencol_de_300tc_para_cima_fica_em_18(regras, fornecedores, tc):
    assert margem(regras, fornecedores, "KTC", "Flat Sheet", tc) == aprox(0.18)


@pytest.mark.parametrize("familia", ["Top Sheet", "Bottom Sheet", "Fitted Sheet"])
def test_a_faixa_de_fios_vale_para_toda_a_familia_de_lencois(regras, fornecedores, familia):
    assert margem(regras, fornecedores, "KTC", familia, 250) == aprox(0.16)
    assert margem(regras, fornecedores, "KTC", familia, 300) == aprox(0.18)


@pytest.mark.parametrize("familia", ["Duvet Cover", "Pillow Case", "Duvet Insert", "Pillow",
                                     "Mattress Protector", "Slipper", "Bed Runner"])
def test_demais_familias_ktc_ficam_em_15(regras, fornecedores, familia):
    assert margem(regras, fornecedores, "KTC", familia, 300) == aprox(0.15)


def test_daune_fica_em_14_para_qualquer_familia(regras, fornecedores):
    for familia in ["Pillow", "Duvet Insert", "Mattress Topper", "Flat Sheet", None]:
        assert margem(regras, fornecedores, "DAUNE", familia, 300) == aprox(0.14)


def test_decor_tricot_fica_em_14(regras, fornecedores):
    assert margem(regras, fornecedores, "DECOR_TRICOT", "Bed Runner") == aprox(0.14)


def test_fornecedor_vence_regra_de_familia_ktc(regras, fornecedores):
    """Um lençol 300TC da Daune continua 14%, não 18%."""
    assert margem(regras, fornecedores, "DAUNE", "Flat Sheet", 300) == aprox(0.14)


def test_override_manual_vence_tudo(regras, fornecedores):
    assert margem(regras, fornecedores, "KTC", "Bath Towel", override=0.09) == aprox(0.09)


def test_regra_resolvida_diz_qual_foi(regras, fornecedores):
    r = resolver_margem(regras, fornecedor_id=fornecedores["KTC"], familia="Bath Towel")
    assert "KTC" in r.regra and r.regra_id is not None


def test_produto_sem_fornecedor_cai_na_regra_geral(regras):
    r = resolver_margem(regras, fornecedor_id=None, familia="Alguma Coisa")
    assert r.margem_pct == aprox(0.15)

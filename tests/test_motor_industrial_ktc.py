"""Motor industrial KTC — os números que a própria KTC demonstrou."""
import pytest

from app.ktc_engine import (
    CALCULATED, REVIEW_REQUIRED, ParametrosKTC, calcular_duvet_cover, calcular_flat_sheet,
    calcular_toalha, peso_toalha_kg,
)


def parametros(material, shrinkage=0.03, cmt=0.75, **kwargs):
    base = dict(material_price_usd_m2=material, cmt_usd=cmt, shrinkage=shrinkage, waste=0.03,
                quality_allowance=0.01, ktc_margin=0.15, hem_width_total_cm=4,
                hem_length_total_cm=4)
    base.update(kwargs)
    return ParametrosKTC(**base)


def test_flat_sheet_reproduz_o_exemplo_da_ktc():
    """180x310, 250TC CVC, tecido US$ 1,20/m² → EXW US$ 9,90235528."""
    r = calcular_flat_sheet(180, 310, parametros(1.20))
    assert r.status == CALCULATED
    assert r.exw_usd == pytest.approx(9.90235528, abs=1e-6)


def test_waterfall_do_flat_sheet_fica_auditavel():
    r = calcular_flat_sheet(180, 310, parametros(1.20))
    nomes = [e.nome for e in r.etapas]
    assert "Largura com bainha" in nomes and "Consumo com waste" in nomes
    assert "EXW KTC calculado" in nomes
    largura_hem = next(e for e in r.etapas if e.nome == "Largura com bainha")
    assert largura_hem.valor == pytest.approx(184.0)
    consumo = next(e for e in r.etapas if e.nome == "Consumo com waste")
    assert consumo.valor == pytest.approx(6.319027, abs=1e-5)


def test_waste_divide_e_nao_multiplica():
    """consumo / (1 - waste) — multiplicar por (1 + waste) daria menos consumo e menos custo."""
    p = parametros(1.20)
    r = calcular_flat_sheet(180, 310, p)
    bruto = next(e for e in r.etapas if e.nome == "Consumo bruto").valor
    com_waste = next(e for e in r.etapas if e.nome == "Consumo com waste").valor
    assert com_waste == pytest.approx(bruto / 0.97, abs=1e-9)
    assert com_waste > bruto * 1.03


def test_margem_ktc_e_sobre_o_preco_final():
    """EXW = custo / (1 - margem). Multiplicar por 1,15 daria menos."""
    p = parametros(1.20)
    r = calcular_flat_sheet(180, 310, p)
    antes = next(e for e in r.etapas if e.nome == "Após perda de 2ª qualidade").valor
    assert r.exw_usd == pytest.approx(antes / 0.85, abs=1e-9)
    assert r.exw_usd > antes * 1.15


def test_quality_allowance_tambem_divide():
    p = parametros(1.20)
    r = calcular_flat_sheet(180, 310, p)
    producao = next(e for e in r.etapas if e.nome == "Custo de produção").valor
    qualidade = next(e for e in r.etapas if e.nome == "Após perda de 2ª qualidade").valor
    assert qualidade == pytest.approx(producao / 0.99, abs=1e-9)


@pytest.mark.parametrize("largura,comprimento,material,shrinkage,real_ktc", [
    (190, 260, 1.20, 0.03, 17.58),
    (250, 260, 1.20, 0.03, 22.55),
    (270, 275, 1.20, 0.03, 25.50),
    (190, 260, 1.35, 0.05, 20.30),
    (250, 260, 1.35, 0.05, 26.11),
    (270, 275, 1.35, 0.05, 29.56),
])
def test_backtest_duvet_cover_contra_a_pi_de_23_08(largura, comprimento, material, shrinkage,
                                                   real_ktc):
    """Modelo reconstruído com parâmetros históricos fica a menos de 1,5% do preço real."""
    p = parametros(material, shrinkage=shrinkage, cmt=1.50, paineis=2)
    r = calcular_duvet_cover(largura, comprimento, p)
    assert abs(r.exw_usd / real_ktc - 1) < 0.015


def test_duvet_cover_usa_dois_paineis():
    p = parametros(1.20, cmt=1.50, paineis=1)   # mesmo pedindo 1, capa duvet tem duas faces
    r = calcular_duvet_cover(190, 260, p)
    assert r.detalhes["paineis"] == 2


def test_sem_parametro_nao_inventa_calculo():
    p = ParametrosKTC(material_price_usd_m2=1.20)   # falta CMT, shrinkage, etc.
    r = calcular_flat_sheet(180, 310, p)
    assert r.status == REVIEW_REQUIRED
    assert r.exw_usd is None
    assert "cmt_usd" in r.faltando


def test_peso_e_custo_de_toalha_por_peso():
    """50 × 85 × 550 ÷ 10.000.000 = 0,23375 kg; × US$ 8/kg = US$ 1,87."""
    assert peso_toalha_kg(50, 85, 550) == pytest.approx(0.23375)
    r = calcular_toalha(50, 85, 550, ParametrosKTC(price_usd_kg=8.0))
    assert r.exw_usd == pytest.approx(1.87)


def test_toalha_sem_preco_por_kg_vai_para_revisao():
    r = calcular_toalha(50, 85, 550, ParametrosKTC())
    assert r.status == REVIEW_REQUIRED
    assert r.exw_usd is None


# ---------------------------------------------------------------------------
# Toalhas — taxa por kg derivada da PI de 23/08/2026
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("largura,comprimento,gsm,taxa,preco_real", [
    (86, 150, 550, 8.50, 6.03),     # Oversized Bath Towel 90/10
    (90, 160, 650, 8.50, 7.96),     # Oversized Bath Towel 100% algodão
    (50, 85, 550, 9.00, 2.10),      # Hand Towel 90/10
    (50, 85, 650, 9.00, 2.49),      # Hand Towel 100% algodão
    (50, 80, 750, 9.00, 2.70),      # Bath Mat 750g
    (50, 80, 950, 9.00, 3.42),      # Bath Mat 950g
    (90, 170, 550, 14.00, 11.78),   # Pool Towel listrada
])
def test_taxa_por_kg_reproduz_os_precos_da_pi(largura, comprimento, gsm, taxa, preco_real):
    """A taxa por kg foi derivada da PI; o modelo tem de devolver o preço da própria PI."""
    r = calcular_toalha(largura, comprimento, gsm, ParametrosKTC(price_usd_kg=taxa))
    assert r.exw_usd == pytest.approx(preco_real, rel=0.002)


def test_taxa_por_kg_final_nao_leva_margem_por_cima():
    """A taxa derivada já é EXW. Aplicar qualidade e margem de novo inflaria ~19%."""
    so_peso = calcular_toalha(50, 80, 750, ParametrosKTC(price_usd_kg=9.00))
    com_margem = calcular_toalha(50, 80, 750, ParametrosKTC(price_usd_kg=9.00,
                                                            quality_allowance=0.01,
                                                            ktc_margin=0.15))
    assert so_peso.exw_usd == pytest.approx(2.70)
    assert com_margem.exw_usd == pytest.approx(2.70 / 0.99 / 0.85, rel=1e-6)
    assert com_margem.exw_usd / so_peso.exw_usd == pytest.approx(1.188, abs=0.001)


def test_peso_teorico_bate_com_o_peso_declarado_pela_ktc():
    """A conta W × L × GSM ÷ 10M reproduz os pesos da PI dentro do arredondamento."""
    declarados = [((86, 150, 550), 0.71), ((90, 160, 650), 0.94), ((50, 85, 550), 0.23),
                  ((50, 85, 650), 0.28), ((50, 80, 750), 0.30), ((50, 80, 950), 0.38),
                  ((90, 170, 550), 0.84)]
    for (largura, comprimento, gsm), declarado in declarados:
        assert peso_toalha_kg(largura, comprimento, gsm) == pytest.approx(declarado, abs=0.005)

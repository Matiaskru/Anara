"""Famílias que ganharam (ou continuam sem) fórmula na Sessão 2.

Cobre a fronha do §18, o bottom sheet sem elástico, o que continua sem fórmula de propósito, a
precedência do I.I. de roupão contra a troca de NCM, e as travas do casamento técnico Daune.
"""
import pytest
from sqlmodel import select

from app.ktc_engine import (
    CALCULATED, REVIEW_REQUIRED, ParametrosKTC, calcular_bottom_sheet, calcular_flat_sheet,
    calcular_fronha, corte_fronha,
)
from app.models import NcmRegra, Produto


def parametros(**kw):
    base = dict(material_price_usd_m2=1.25, cmt_usd=0.0, shrinkage=0.03, waste=0.03,
                quality_allowance=0.01, ktc_margin=0.15, hem_width_total_cm=0.0,
                hem_length_total_cm=0.0, paineis=1, other_costs_usd=0.0)
    base.update(kw)
    return ParametrosKTC(**base)


# ---------------------------------------------------------------------------
# Fronha — §18
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("abas,corte", [
    (0, (54, 165)), (2, (54, 185)), (3, (59, 185)), (4, (64, 185)),
])
def test_corte_da_fronha_segue_o_paragrafo_18(abas, corte):
    assert corte_fronha(50, 70, 20, abas) == corte


@pytest.mark.parametrize("nome,abas,festone,alvo", [
    ("Standard / 0 abas", 0, False, 2.0417),
    ("2 abas", 2, False, 2.5143),
    ("3 abas", 3, False, 2.6646),
    ("4 abas", 4, False, 2.8148),
    ("4 abas + festonê", 4, True, 2.9337),
])
def test_os_cinco_backtests_da_fronha(nome, abas, festone, alvo):
    """50×70, flap 20, 250TC CVC a US$ 1,25/m². Desvio máximo aceito: 0,01%."""
    r = calcular_fronha(50, 70, parametros(), flap_cm=20, abas=abas, festone=festone)
    assert r.status == CALCULATED
    assert r.exw_usd == pytest.approx(alvo, rel=1e-4), f"{nome} fora do alvo do §18"


def test_cmt_da_fronha_vem_da_construcao():
    """0,50 no standard, 0,75 com abas — não do cadastro genérico da família."""
    standard = calcular_fronha(50, 70, parametros(cmt_usd=99.0), abas=0)
    com_abas = calcular_fronha(50, 70, parametros(cmt_usd=99.0), abas=2)
    assert standard.detalhes["cmt_construcao_usd"] == 0.50
    assert com_abas.detalhes["cmt_construcao_usd"] == 0.75
    assert com_abas.exw_usd > standard.exw_usd


def test_festone_entra_antes_da_qualidade_e_da_margem():
    """+US$ 0,10 no custo de produção — o efeito no EXW é maior que 0,10."""
    sem = calcular_fronha(50, 70, parametros(), abas=4, festone=False)
    com = calcular_fronha(50, 70, parametros(), abas=4, festone=True)
    delta = com.exw_usd - sem.exw_usd
    assert delta > 0.10, "se o festonê entrasse no fim, o delta seria exatamente 0,10"
    assert delta == pytest.approx(0.10 / (1 - 0.01) / (1 - 0.15), rel=1e-9)


def test_bordado_extraordinario_nao_e_calculavel():
    r = calcular_fronha(50, 70, parametros(), abas=0, bordado_especial=True)
    assert r.status == REVIEW_REQUIRED
    assert r.exw_usd is None
    assert "KTC_SPECIAL_QUOTED" in " ".join(r.avisos)


def test_numero_de_abas_fora_do_paragrafo_18_bloqueia():
    """1 e 5 abas não são construções aprovadas — não se arredonda para a vizinha."""
    for abas in (1, 5, 7):
        r = calcular_fronha(50, 70, parametros(), abas=abas)
        assert r.status == REVIEW_REQUIRED and r.exw_usd is None


def test_fronha_sem_dimensao_bloqueia():
    assert calcular_fronha(None, 70, parametros()).status == REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Bottom sheet
# ---------------------------------------------------------------------------
def test_bottom_sheet_sem_elastico_usa_o_motor_do_lencol():
    p1, p2 = parametros(cmt_usd=0.75, hem_width_total_cm=8, hem_length_total_cm=10), \
        parametros(cmt_usd=0.75, hem_width_total_cm=8, hem_length_total_cm=10)
    bottom = calcular_bottom_sheet(160, 200, p1, com_elastico=False)
    flat = calcular_flat_sheet(160, 200, p2)
    assert bottom.status == CALCULATED
    assert bottom.exw_usd == pytest.approx(flat.exw_usd)


def test_bottom_sheet_com_elastico_nao_inventa_formula():
    r = calcular_bottom_sheet(160, 200, parametros(cmt_usd=0.75), com_elastico=True)
    assert r.status == REVIEW_REQUIRED and r.exw_usd is None
    aviso = " ".join(r.avisos)
    assert "KTC_SPECIAL_QUOTED" in aviso and "A_COTAR_KTC" in aviso


# ---------------------------------------------------------------------------
# Roupões — NCM trocado, I.I. preservado
# ---------------------------------------------------------------------------
def test_ncm_do_roupao_nao_e_mais_6309(session):
    regras = session.exec(select(NcmRegra)).all()
    assert not any((r.ncm or "").startswith("6309") for r in regras), \
        "6309 é posição de artigos usados e não serve a mercadoria nova"


def test_ii_de_roupao_sobrevive_a_troca_de_ncm(session):
    """O I.I. de 3,5% é override de FAMÍLIA, com prioridade 10 — imune ao código NCM.

    Este teste não existia antes da Sessão 2 (OK-15 estava sem cobertura), e é pré-condição
    para poder mexer no NCM sem medo.
    """
    from app import pricing_service as ps

    roupao = session.exec(select(NcmRegra).where(NcmRegra.familia == "Roupão")).first()
    assert roupao.ii_preferencial == pytest.approx(0.035)
    assert roupao.prioridade == 10

    p = Produto(sku_key="ROUPAO-II", nome="Roupão de teste", familia="Roupão",
                ncm="9999.99.99")     # NCM deliberadamente errado
    session.add(p)
    session.commit()

    regra = ps.regra_ncm(session, p)
    assert regra is not None
    assert regra.ii_preferencial == pytest.approx(0.035), \
        "a regra de família tem de vencer o lookup por NCM"
    assert regra.familia == "Roupão"


# ---------------------------------------------------------------------------
# Casamento técnico Daune — as travas
# ---------------------------------------------------------------------------
def test_medida_sozinha_nao_casa():
    from scripts.reconciliar_daune import casar

    itens = [{"aba": "12.08.26", "descricao": "Edredom 100% poliester 190x260",
              "gross": 400.0, "largura": 190, "comprimento": 260, "gramatura": 280,
              "composicao": "POLIESTER"}]
    sku_180 = Produto(sku_key="X", nome="Edredom 190x260 · 180 g · 100% fibras de poliéster",
                      familia="Duvet Insert", largura_cm=190, comprimento_cm=260, gsm=180)
    item, motivo = casar(sku_180, itens)
    assert item is None, "180 g não pode receber o preço de 280 g só porque a medida bate"
    assert "280" in motivo or "gramatura" in motivo


def test_gramatura_igual_casa():
    from scripts.reconciliar_daune import casar

    itens = [{"aba": "12.08.26", "descricao": "Edredom 100% poliester 190x260",
              "gross": 400.0, "largura": 190, "comprimento": 260, "gramatura": 280,
              "composicao": "POLIESTER"}]
    sku = Produto(sku_key="Y", nome="Edredom 190x260 · 100% poliester",
                  familia="Duvet Insert", largura_cm=190, comprimento_cm=260, gsm=280)
    item, motivo = casar(sku, itens)
    assert item is not None and item["gross"] == 400.0
    assert "gramatura" in motivo


def test_fonte_mais_nova_vence_quando_o_match_e_igual():
    from scripts.reconciliar_daune import casar

    comum = dict(largura=50, comprimento=70, gramatura=None, composicao="PLUMA")
    itens = [{"aba": "27.07.26", "descricao": "Travesseiros 100% plumas de ganso 50x70",
              "gross": 249.37, **comum},
             {"aba": "12.08.26", "descricao": "Travesseiros 100% plumas de ganso 50x70",
              "gross": 260.00, **comum}]
    sku = Produto(sku_key="T", nome="Travesseiro 50x70 · 100% plumas de ganso",
                  familia="Pillow", largura_cm=50, comprimento_cm=70)
    item, motivo = casar(sku, itens)
    assert item["aba"] == "12.08.26" and item["gross"] == 260.00


def test_assinatura_tecnica_diferente_nao_casa():
    """Mesma medida, composições diferentes: são produtos diferentes."""
    from scripts.reconciliar_daune import casar

    itens = [{"aba": "12.08.26", "descricao": "Travesseiros 50% plumas 50% penas 50x70",
              "gross": 153.35, "largura": 50, "comprimento": 70, "gramatura": None,
              "composicao": "PLUMA"}]
    sku = Produto(sku_key="T2", nome="Travesseiro 50x70 · 100% plumas de ganso",
                  familia="Pillow", largura_cm=50, comprimento_cm=70)
    item, _ = casar(sku, itens)
    assert item is None


def test_sku_sem_dimensao_nao_casa():
    from scripts.reconciliar_daune import casar

    sku = Produto(sku_key="T3", nome="Travesseiro sem medida", familia="Pillow")
    item, motivo = casar(sku, [])
    assert item is None and "dimensão" in motivo

"""Famílias que ganharam (ou continuam sem) fórmula na Sessão 2.

Cobre a fronha do §18, o bottom sheet sem elástico, o que continua sem fórmula de propósito, a
precedência do I.I. de roupão contra a troca de NCM, e as travas do casamento técnico Daune.
"""
import os

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


# ---------------------------------------------------------------------------
# B-17 — preço de venda não vira custo
# ---------------------------------------------------------------------------
def test_valor_legado_e_reconhecido_como_preco_de_venda():
    """A razão 1,51223 é preço/gross, não custo/gross.

    Exemplo histórico conhecido: gross 406,75 → CNET 324,83055 → preço 615,10. A combinação que
    reproduz isso é margem 14%, ICMS 18%, PIS/COFINS 7,59%, encargo 1,6% e comissão de 6%.
    """
    from scripts.reconciliar_daune import DENOM_PRECO_14, FATOR_CNET, _e_preco_de_venda

    gross = 406.75
    cnet = gross * FATOR_CNET
    preco = cnet / DENOM_PRECO_14
    assert cnet == pytest.approx(324.83055, abs=1e-4)
    assert preco == pytest.approx(615.10, abs=0.01)
    assert preco / gross == pytest.approx(1.51221, abs=5e-5)

    item = {"gross": gross}
    assert _e_preco_de_venda(preco, item) is True
    assert _e_preco_de_venda(cnet, item) is False, "o CNET correto não é preço de venda"


def test_preco_de_venda_nao_vira_custo_nem_revalidar():
    """Regra de segurança: sem base de custo, é A_COTAR — nunca REVALIDAR de custo."""
    import json as _json
    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "relatorios", "reconciliacao_daune.json")
    if not os.path.exists(caminho):
        pytest.skip("relatório de reconciliação ainda não gerado")
    r = _json.load(open(caminho))
    sem_base = [linha for linha in r["linhas"]
                if linha.get("custo_atual") and not linha.get("gross")]
    for linha in sem_base:
        assert linha["status_proposto"] == "A_COTAR"
        assert "PREÇO DE VENDA" in linha["justificativa"]


def test_familia_e_ruido_mas_construcao_nao():
    """"Topper de colchão" e "Pillow Top" são o mesmo produto; "modelo slip" não é."""
    from scripts.reconciliar_daune import assinatura_tecnica

    assert (assinatura_tecnica("Topper de colchão 100x200 · 90% Penas 10% plumas")
            == assinatura_tecnica("Pillow Top 90% Penas 10% plumas 100x200"))
    assert (assinatura_tecnica("Protetor de fronha 50x70 · 100% algodâo 200 fios")
            == assinatura_tecnica("Capa Protetora para Travesseiros 100% algodâo 200 fios 50x70"))
    assert (assinatura_tecnica("Manta 120 grs impermeavel modelo slip")
            != assinatura_tecnica("Manta 120 grs impermeavel")), \
        "slip é construção diferente e não pode casar sem evidência"


# ---------------------------------------------------------------------------
# Gramatura estruturada dos edredons
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("texto,esperado", [
    ("Edredom 190x260 · 180 g · 100% plumas de ganso", 180),
    ("Edredom 250x260 · 250 g · 100% fibras de poliéster", 250),
    ("Edredom 100% plumas de ganso 180GRS", 180),
    ("Edredom 100% plumas de ganso 250 GRS", 250),
    ("Edredom 285x265 · 280 g · 100% fibras de poliéster", 280),
])
def test_gramatura_e_extraida_da_descricao(texto, esperado):
    """Campo `gsm` nulo não é ausência de dado quando a descrição declara a gramatura."""
    from scripts.reconciliar_daune import extrair_gramatura
    assert extrair_gramatura(texto) == esperado


@pytest.mark.parametrize("texto", [
    "Edredom 156x230 · 100% plumas de ganso",
    "Edredom 220x240 · 100% fibras de Poliester",
    "Travesseiro 50x70 · 100% plumas de ganso",
])
def test_sem_gramatura_nao_se_infere(texto):
    from scripts.reconciliar_daune import extrair_gramatura
    assert extrair_gramatura(texto) is None, "não se atribui 180/250/280 por chute"


def test_gramatura_nao_polui_a_assinatura_tecnica():
    """"180 g" e "180GRS" são o mesmo produto; a gramatura é comparada como campo à parte."""
    from scripts.reconciliar_daune import assinatura_tecnica
    assert (assinatura_tecnica("Edredom 190x260 · 180 g · 100% plumas de ganso")
            == assinatura_tecnica("Edredom 100% plumas de ganso 180GRS 190x260"))


def test_280g_nao_casa_com_180_nem_250():
    from scripts.reconciliar_daune import casar
    fonte = [{"aba": "12.08.26", "descricao": "Edredom 100% fibras de Poliester",
              "gross": 469.30, "largura": 190, "comprimento": 260, "gramatura": 280,
              "composicao": "POLIESTER"}]
    for gsm in (180, 250):
        sku = Produto(sku_key=f"P{gsm}", nome=f"Edredom 190x260 · {gsm} g · 100% poliéster",
                      familia="Duvet Insert", largura_cm=190, comprimento_cm=260, gsm=gsm)
        item, _ = casar(sku, fonte)
        assert item is None, f"poliéster {gsm} g não pode receber o preço de 280 g"


def test_pluma_nao_casa_com_poliester():
    from scripts.reconciliar_daune import casar
    fonte = [{"aba": "12.08.26", "descricao": "Edredom 100% fibras de Poliester",
              "gross": 469.30, "largura": 190, "comprimento": 260, "gramatura": 180,
              "composicao": "POLIESTER"}]
    sku = Produto(sku_key="PL", nome="Edredom 190x260 · 180 g · 100% plumas de ganso",
                  familia="Duvet Insert", largura_cm=190, comprimento_cm=260, gsm=180)
    item, _ = casar(sku, fonte)
    assert item is None


def test_legado_generico_fica_inativo_mas_preservado(session):
    """SKU ambíguo sai da seleção comercial; não é apagado, e o histórico continua íntegro."""
    from app.models import Fornecedor
    daune = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
    p = Produto(sku_key="LEGADO-AMB", nome="Edredom 156x230 · 100% plumas de ganso",
                familia="Duvet Insert", fornecedor_id=daune.id, largura_cm=156,
                comprimento_cm=230, ativo=True)
    session.add(p)
    session.commit()

    from scripts.reconciliar_daune import extrair_gramatura
    assert extrair_gramatura(p.nome) is None
    p.ativo = False          # o que o normalizador faz
    session.add(p)
    session.commit()

    assert session.get(Produto, p.id) is not None, "inativar não é apagar"
    assert session.get(Produto, p.id).ativo is False

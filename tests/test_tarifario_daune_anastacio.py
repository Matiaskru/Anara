"""Tarifário Daune "Projeto Anastacio.xlsx" — só evidência direta altera custo.

O arquivo fechou a pendência dos edredons: 14 SKUs (4 de pluma, 10 de poliéster, nas medidas
250×260 e 270×265 para pluma e todas as cinco para poliéster) estavam sem referência porque a
fonte anterior não distinguia 180 g de 250 g. A aba `Nova Cotação 05.08.26` traz as duas
gramaturas explícitas, e o casamento é por campos estruturados — família, gramatura, medida —
mais a composição por igualdade exata de token normalizado.

O que esta suíte cobra:

* os 14 casamentos diretos, um a um, com o bruto exato da célula;
* que 180 e 250 g não se confundem, que pluma e poliéster não se confundem, e que medidas
  próximas (285×265 × 290×260, 190×260 × 193×203) não se fundem;
* que nenhuma aproximação cruza especificação — uma linha com medida que o catálogo não tem
  é `NO_MATCH`, não "o mais parecido";
* que o CNET da Daune continua saindo da fórmula fechada (249,37 → 199,146882);
* que os conflitos ficam conflitos: protetor de colchão (construção diferente), pillow top
  (medidas diferentes com os mesmos preços) e o rótulo 280 g do catálogo contra o 180GSM da
  fonte NÃO viram referência;
* que a referência nova é a que forma preço novo, e que a antiga, onde havia, ficou como estava.

Os SKUs são criados no banco temporário espelhando os campos estruturados do catálogo real —
inclusive a composição só na string de especificação (B-16), que é como está em produção.
"""
import os
from decimal import Decimal

import pytest
from sqlmodel import select

from app import custo_service as cs
from app import pricing_service as ps
from app.dinheiro import D
from app.models import CostMethod, Fornecedor, Produto, StatusCusto
from scripts.atualizar_daune_projeto_anastacio import (
    ABA_NOVA, DESTINO_REFERENCIA, classificar, composicao_canonica, conflitos_estruturais,
    dimensoes_cm, familia_canonica, gramatura, ler_fonte, catalogo_daune,
)

PLUMA = "100% plumas de ganso"
POLI = "100% fibras de poliéster"
MEDIDAS = [(190, 260), (250, 260), (270, 265), (285, 265), (290, 260)]

#: (composição, gsm, (l, c), bruto exato da fonte) — os 14 que fecham o A_COTAR.
ESPERADOS_NOVOS = [
    (PLUMA, 180, (250, 260), "1137.5"), (PLUMA, 180, (270, 265), "1216.35"),
    (POLI, 180, (190, 260), "469.3"), (POLI, 180, (250, 260), "585"),
    (POLI, 180, (270, 265), "643.95"), (POLI, 180, (285, 265), "679.72"),
    (POLI, 180, (290, 260), "678.6"),
    (PLUMA, 250, (250, 260), "1235"), (PLUMA, 250, (270, 265), "1359.45"),
    (POLI, 250, (190, 260), "518.7"), (POLI, 250, (250, 260), "682.5"),
    (POLI, 250, (270, 265), "751.27"), (POLI, 250, (285, 265), "793.01"),
    (POLI, 250, (290, 260), "791.7"),
]
#: Os que já tinham referência direta com o mesmo bruto — não ganham versão.
ESPERADOS_IGUAIS = [
    (PLUMA, 180, (190, 260), "863.51"), (PLUMA, 180, (285, 265), "1320.17"),
    (PLUMA, 180, (290, 260), "1219.82"), (PLUMA, 250, (190, 260), "938.6"),
    (PLUMA, 250, (285, 265), "1434.97"), (PLUMA, 250, (290, 260), "1325.9"),
]


@pytest.fixture(scope="module")
def fonte():
    if not os.path.exists(DESTINO_REFERENCIA):
        pytest.skip("referencia/Projeto Anastacio.xlsx não está no repositório")
    return ler_fonte(DESTINO_REFERENCIA)


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


def _edredom(session, daune, comp, gsm, dims, *, com_referencia=None):
    """Espelha o SKU real: composição só na especificação, gsm e medidas estruturados."""
    l, c = dims
    p = Produto(sku_key=f"TST-DAUNE · Edredom · {comp} · {gsm} g · {l}x{c}",
                nome=f"Edredom {l}x{c} · {gsm} g · {comp}",
                especificacao=f"{l}x{c} · {gsm} g · {comp}",
                familia="Duvet Insert", gsm=gsm, largura_cm=l, comprimento_cm=c,
                fornecedor_id=daune.id, cost_method=CostMethod.manual.value,
                status_custo=StatusCusto.review_required.value, precisa_revisao=True,
                margem_padrao_pct=0.14, preco_base=0.0)
    session.add(p)
    session.commit()
    session.refresh(p)
    if com_referencia is not None:
        cs.registrar_daune(session, p, com_referencia, fonte="fonte anterior",
                           documento="Linha Hotelaria - Daune - 12.08.26.xlsx")
        session.commit()
    return p


@pytest.fixture
def catalogo_espelho(session, daune):
    """Os 20 edredons 180/250 g como estão em produção: 6 com referência, 14 sem."""
    criados = {}
    for comp, gsm, dims, bruto in ESPERADOS_IGUAIS:
        criados[(comp, gsm, dims)] = _edredom(session, daune, comp, gsm, dims,
                                              com_referencia=bruto)
    for comp, gsm, dims, _ in ESPERADOS_NOVOS:
        criados[(comp, gsm, dims)] = _edredom(session, daune, comp, gsm, dims)
    # e os 280 g, rotulados a partir de informação de 03/09 — três coincidem em medida e preço
    for dims, bruto in (((190, 260), "469.3"), ((285, 265), "679.72"), ((290, 260), "678.6")):
        criados[(POLI, 280, dims)] = _edredom(session, daune, POLI, 280, dims,
                                              com_referencia=bruto)
    yield criados
    for p in criados.values():
        for ref in cs.versoes(session, p.id):
            session.delete(ref)
        session.delete(p)
    session.commit()


def _edredons_da_fonte(fonte):
    return [l for l in fonte[ABA_NOVA] if l["familia"] == "Duvet Insert"]


# ===========================================================================
# Leitura e normalização — a única aproximação, declarada
# ===========================================================================
def test_a_fonte_tem_as_duas_abas_e_42_linhas_novas(fonte):
    assert set(fonte) == {"Cotação 24.06.26", ABA_NOVA}
    assert len(fonte[ABA_NOVA]) == 42
    assert len(fonte["Cotação 24.06.26"]) == 32


def test_o_bruto_sai_exatamente_como_esta_na_celula(fonte):
    """Inclusive o valor de três casas: 1103,203 não vira 1103,20."""
    precos = {l["linha"]: l["preco"] for l in fonte[ABA_NOVA]}
    assert precos[20] == "1103.203"
    assert precos[27] == "1137.5"
    assert D(precos[20]) == Decimal("1103.203")


def test_dimensoes_em_metros_viram_centimetros_exatos():
    assert dimensoes_cm("1,90x2,60") == (190, 260)
    assert dimensoes_cm("2,85x2,65") == (285, 265)
    assert dimensoes_cm("1,93x2,03") == (193, 203)
    assert dimensoes_cm("2,03x2,03") == (203, 203)
    assert dimensoes_cm("50x70") == (50, 70)


def test_gramatura_vem_do_texto_e_e_inteira():
    assert gramatura("180GSM 100% pluma de ganso") == 180
    assert gramatura("250GSM 100% fibras de poliester") == 250
    assert gramatura("100% algodão 200 fios") is None


def test_a_normalizacao_nao_aproxima_o_que_importa():
    """Plural e acento são ortografia; pluma × poliéster e 90/10 × 100% são produto."""
    assert composicao_canonica("180GSM 100% pluma de ganso") == \
        composicao_canonica("100% plumas de ganso")
    assert composicao_canonica("100% fibras de poliester") == \
        composicao_canonica("100% fibras de poliéster")
    assert composicao_canonica("100% pluma de ganso") != composicao_canonica("100% fibras de poliester")
    assert composicao_canonica("90% pena e 10% plumas") != composicao_canonica("100% fibras de poliester")
    assert composicao_canonica("50% plumas 50% penas de ganso") != composicao_canonica("100% plumas de ganso")


def test_familias_reconhecidas():
    assert familia_canonica("Edredom Super King( Duvet)") == "Duvet Insert"
    assert familia_canonica("Capa Protetora para Travesseiros") == "Pillow Protector"
    assert familia_canonica("Protetor de Colchão Super King") == "Mattress Protector"
    assert familia_canonica("Pillow Top Queen") == "Mattress Topper"


# ===========================================================================
# Os 14 casamentos diretos, um a um
# ===========================================================================
@pytest.mark.parametrize("comp,gsm,dims,bruto", ESPERADOS_NOVOS)
def test_cada_match_direto_fecha_um_a_cotar(session, fonte, catalogo_espelho, comp, gsm,
                                            dims, bruto):
    catalogo = catalogo_daune(session)
    linha = next(l for l in _edredons_da_fonte(fonte)
                 if l["composicao"] == composicao_canonica(comp) and l["gsm"] == gsm
                 and l["dims"] == dims)
    r = classificar(linha, catalogo)

    assert r["classe"] == "EXACT_NEW", r["motivo"]
    assert r["sku"] == catalogo_espelho[(comp, gsm, dims)].id
    assert r["preco"] == bruto, "o bruto é o da célula, sem retoque"
    assert r["status_anterior"].startswith("A_COTAR")


@pytest.mark.parametrize("comp,gsm,dims,bruto", ESPERADOS_IGUAIS)
def test_referencia_igual_nao_ganha_versao(session, fonte, catalogo_espelho, comp, gsm, dims,
                                           bruto):
    catalogo = catalogo_daune(session)
    linha = next(l for l in _edredons_da_fonte(fonte)
                 if l["composicao"] == composicao_canonica(comp) and l["gsm"] == gsm
                 and l["dims"] == dims)
    r = classificar(linha, catalogo)
    assert r["classe"] == "EXACT_SAME"
    assert D(r["bruto_anterior"]) == D(bruto)


def test_o_mapa_inteiro_dos_edredons(session, fonte, catalogo_espelho):
    """20 linhas de edredom: 14 novas, 6 iguais, zero ambíguas, zero sem match."""
    catalogo = catalogo_daune(session)
    classes = {}
    for l in _edredons_da_fonte(fonte):
        c = classificar(l, catalogo)["classe"]
        classes[c] = classes.get(c, 0) + 1
    assert classes == {"EXACT_NEW": 14, "EXACT_SAME": 6}


# ===========================================================================
# O que NÃO se funde
# ===========================================================================
def test_180_e_250_nao_se_confundem(session, fonte, catalogo_espelho):
    """A mesma medida e composição em duas gramaturas são dois SKUs, com dois preços."""
    catalogo = catalogo_daune(session)
    l180 = next(l for l in _edredons_da_fonte(fonte)
                if l["gsm"] == 180 and l["dims"] == (250, 260) and "poli" in l["composicao"])
    l250 = next(l for l in _edredons_da_fonte(fonte)
                if l["gsm"] == 250 and l["dims"] == (250, 260) and "poli" in l["composicao"])
    r180, r250 = classificar(l180, catalogo), classificar(l250, catalogo)

    assert r180["sku"] != r250["sku"]
    assert r180["sku"] == catalogo_espelho[(POLI, 180, (250, 260))].id
    assert r250["sku"] == catalogo_espelho[(POLI, 250, (250, 260))].id
    assert (r180["preco"], r250["preco"]) == ("585", "682.5")


def test_pluma_e_poliester_nao_se_confundem(session, fonte, catalogo_espelho):
    catalogo = catalogo_daune(session)
    pl = next(l for l in _edredons_da_fonte(fonte)
              if l["gsm"] == 250 and l["dims"] == (270, 265) and "pluma" in l["composicao"])
    po = next(l for l in _edredons_da_fonte(fonte)
              if l["gsm"] == 250 and l["dims"] == (270, 265) and "poli" in l["composicao"])
    assert classificar(pl, catalogo)["sku"] == catalogo_espelho[(PLUMA, 250, (270, 265))].id
    assert classificar(po, catalogo)["sku"] == catalogo_espelho[(POLI, 250, (270, 265))].id


def test_medidas_proximas_nao_se_fundem(session, fonte, catalogo_espelho):
    """285×265 e 290×260 têm área quase igual e preços diferentes. Dois SKUs."""
    catalogo = catalogo_daune(session)
    a = next(l for l in _edredons_da_fonte(fonte)
             if l["gsm"] == 250 and l["dims"] == (285, 265) and "poli" in l["composicao"])
    b = next(l for l in _edredons_da_fonte(fonte)
             if l["gsm"] == 250 and l["dims"] == (290, 260) and "poli" in l["composicao"])
    ra, rb = classificar(a, catalogo), classificar(b, catalogo)
    assert ra["sku"] != rb["sku"]
    assert (ra["preco"], rb["preco"]) == ("793.01", "791.7")


def test_medida_que_o_catalogo_nao_tem_e_no_match_e_nao_o_vizinho(session, catalogo_espelho):
    """Nada de "o mais parecido": 193×203 não vira 190×260."""
    catalogo = catalogo_daune(session)
    linha = {"aba": ABA_NOVA, "linha": 0, "produto": "Edredom (Duvet)",
             "especificacao": "180GSM 100% pluma de ganso", "dimensao": "1,93x2,03",
             "preco_raw": 999.0, "preco": "999", "familia": "Duvet Insert",
             "composicao": composicao_canonica("100% pluma de ganso"), "gsm": 180,
             "dims": (193, 203), "construcao": None}
    r = classificar(linha, catalogo)
    assert r["classe"] == "NO_MATCH" and r["sku"] is None


def test_gramatura_que_o_catalogo_nao_tem_e_no_match(session, catalogo_espelho):
    """Mesma medida, mesma composição, gramatura inexistente: não vira o 180 nem o 250."""
    catalogo = catalogo_daune(session)
    linha = {"aba": ABA_NOVA, "linha": 0, "produto": "Edredom (Duvet)",
             "especificacao": "300GSM 100% fibras de poliester", "dimensao": "1,90x2,60",
             "preco_raw": 999.0, "preco": "999", "familia": "Duvet Insert",
             "composicao": composicao_canonica("100% fibras de poliester"), "gsm": 300,
             "dims": (190, 260), "construcao": None}
    assert classificar(linha, catalogo)["classe"] == "NO_MATCH"


# ===========================================================================
# Conflitos ficam conflitos
# ===========================================================================
def test_protetor_e_pillow_top_sao_conflict_e_nao_viram_referencia(fonte, session):
    catalogo = catalogo_daune(session)
    for l in fonte[ABA_NOVA]:
        if l["familia"] in ("Mattress Protector", "Mattress Topper"):
            r = classificar(l, catalogo)
            assert r["classe"] == "CONFLICT", (l["linha"], r)
            assert r["sku"] is None


def test_o_280g_do_catalogo_e_sinalizado_contra_o_180gsm_da_fonte(session, fonte,
                                                                  catalogo_espelho):
    """Mesmo bruto, mesma medida, dois rótulos de gramatura. Registra, não escolhe."""
    catalogo = catalogo_daune(session)
    achados = conflitos_estruturais(catalogo, _edredons_da_fonte(fonte))
    skus_280 = {catalogo_espelho[(POLI, 280, d)].id for d in ((190, 260), (285, 265), (290, 260))}

    assert {a["sku"] for a in achados} == skus_280
    assert all(a["gsm_catalogo"] == 280 and a["gsm_fonte"] == 180 for a in achados)
    # e o 180 g da mesma medida continua casando com o SKU de 180 g — o conflito é do rótulo 280
    l = next(x for x in _edredons_da_fonte(fonte)
             if x["gsm"] == 180 and x["dims"] == (190, 260) and "poli" in x["composicao"])
    assert classificar(l, catalogo)["sku"] == catalogo_espelho[(POLI, 180, (190, 260))].id


# ===========================================================================
# Aplicação: versionada, CNET pela fórmula fechada, e é ela que forma preço
# ===========================================================================
def test_cnet_daune_golden_inalterado():
    c = cs.cnet_nacional(D("249.37"))
    assert (c.icms_credito, c.base_pis_cofins, c.pis_cofins_credito, c.cnet) == \
        (D("29.9244"), D("219.4456"), D("20.298718"), D("199.146882"))


def test_aplicar_cria_v1_confirmada_e_o_preco_novo_usa_a_referencia(session, fonte,
                                                                     catalogo_espelho):
    catalogo = catalogo_daune(session)
    alvo = catalogo_espelho[(POLI, 250, (285, 265))]
    linha = next(l for l in _edredons_da_fonte(fonte)
                 if l["gsm"] == 250 and l["dims"] == (285, 265) and "poli" in l["composicao"])
    assert classificar(linha, catalogo)["classe"] == "EXACT_NEW"
    assert cs.referencia_vigente(session, alvo.id) is None

    ref = cs.registrar_daune(session, alvo, linha["preco"], fonte="teste",
                             documento="Projeto Anastacio.xlsx")
    session.commit()

    assert ref.versao == 1 and ref.vigente and ref.substitui_versao is None
    assert ref.status_custo == StatusCusto.confirmado.value
    assert D(ref.valor_bruto) == Decimal("793.01")
    assert D(ref.cnet_brl) == cs.cnet_nacional(D("793.01")).cnet
    assert D(ref.cnet_brl) == Decimal("633.297786")

    # a precificação nova lê a referência vigente, não o cache antigo
    cnet, memoria = ps.custo_para_precificar(session, alvo)
    assert D(cnet) == Decimal("633.297786")
    assert memoria["custo_referencia_id"] == ref.id


def test_a_referencia_anterior_fica_como_estava(session, fonte, catalogo_espelho):
    """Onde já havia referência direta com o mesmo bruto, nada muda — nem versão nova."""
    alvo = catalogo_espelho[(PLUMA, 180, (190, 260))]
    antes = cs.referencia_vigente(session, alvo.id)
    assert antes is not None and D(antes.valor_bruto) == D("863.51")

    catalogo = catalogo_daune(session)
    linha = next(l for l in _edredons_da_fonte(fonte)
                 if l["gsm"] == 180 and l["dims"] == (190, 260) and "pluma" in l["composicao"])
    assert classificar(linha, catalogo)["classe"] == "EXACT_SAME"
    assert cs.referencia_vigente(session, alvo.id).id == antes.id
    assert len(cs.versoes(session, alvo.id)) == 1

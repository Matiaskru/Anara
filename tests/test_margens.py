"""Margens líquidas-alvo padrão por fornecedor e família — três réguas, resolvidas por data.

* **véspera (15/09/2026)**: a régua anterior (Daune 14%, toalha KTC 12%…);
* **16/09 a 20/09/2026**: a política da Fase 3A (`anterior + 2 p.p.`, piso `− 1 p.p.`, Daune
  12/12 travado, Decor 12/10) — continua legível para os itens que a pinaram;
* **hoje (≥ 21/09/2026)**: a política B2B — margens FINAIS pós-comissão de 5%, sem piso
  separado (o B2B é o piso), sem preço travado, **+1 p.p. a partir de 400 fios**, e SEM
  regra geral nem fallback: produto que nenhuma regra alcança bloqueia.
"""
from datetime import date

import pytest
from sqlmodel import select

from app import politica_comercial as pol
from app.dinheiro import D
from app.margin_rules import SEM_VIGENCIA, resolver_margem
from app.models import Fornecedor, MargemRegra
from decimais import MEIO_CENTAVO, aprox  # noqa: E402

VESPERA = date(2026, 9, 15)     # a política de 16/09 entra em 16/09/2026
FASE_3A = date(2026, 9, 18)     # a política de 16/09/2026 vigente; a de 21/09 ainda não
HOJE = date.today()


@pytest.fixture
def regras(session):
    return session.exec(select(MargemRegra)).all()


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f.id for f in session.exec(select(Fornecedor)).all()}


def resolver(regras, fornecedores, codigo, familia=None, tc=None, sku=None, override=None,
             ref=None):
    return resolver_margem(regras, fornecedor_id=fornecedores.get(codigo), familia=familia,
                           thread_count=tc, sku_key=sku, override_pct=override, ref=ref)


def margem(regras, fornecedores, codigo, familia=None, tc=None, sku=None, override=None,
           ref=None):
    return resolver(regras, fornecedores, codigo, familia, tc, sku, override, ref).margem_pct


# ---------------------------------------------------------------------------
# A régua anterior continua legível — na véspera da política
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("familia", ["Bath Towel", "Hand Towel", "Face Towel", "Pool Towel",
                                     "Beach Towel", "Bath Mat"])
def test_toalhas_ktc_eram_12_ate_15_09(regras, fornecedores, familia):
    assert margem(regras, fornecedores, "KTC", familia, ref=VESPERA) == aprox(0.12)


def test_regua_anterior_completa_na_vespera(regras, fornecedores):
    assert margem(regras, fornecedores, "KTC", "Bathrobe", ref=VESPERA) == aprox(0.12)
    assert margem(regras, fornecedores, "KTC", "Flat Sheet", 250, ref=VESPERA) == aprox(0.16)
    assert margem(regras, fornecedores, "KTC", "Flat Sheet", 300, ref=VESPERA) == aprox(0.18)
    assert margem(regras, fornecedores, "KTC", "Duvet Cover", 300, ref=VESPERA) == aprox(0.15)
    assert margem(regras, fornecedores, "DAUNE", "Pillow", ref=VESPERA) == aprox(0.14)
    assert margem(regras, fornecedores, "DECOR_TRICOT", "Bed Runner", ref=VESPERA) == aprox(0.14)
    # na véspera a regra "Geral — sem fornecedor definido" (15%) estava vigente: é regra, não fallback
    geral = resolver_margem(regras, familia="Alguma Coisa", ref=VESPERA)
    assert geral.margem_pct == aprox(0.15) and geral.origem == "tabela"


# ---------------------------------------------------------------------------
# A política de 16/09/2026 — vigente entre 16/09 e 20/09; legível para quem a pinou
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("familia", ["Bath Towel", "Hand Towel", "Face Towel", "Pool Towel",
                                     "Beach Towel", "Bath Mat"])
def test_toalhas_ktc_ficaram_em_14_na_fase_3a(regras, fornecedores, familia):
    r = resolver(regras, fornecedores, "KTC", familia, ref=FASE_3A)
    assert r.margem_pct == aprox(0.14) and r.piso_pct == aprox(0.11)


def test_roupao_ktc_ficou_em_14_na_fase_3a(regras, fornecedores):
    r = resolver(regras, fornecedores, "KTC", "Bathrobe", ref=FASE_3A)
    assert r.margem_pct == aprox(0.14) and r.piso_pct == aprox(0.11)


@pytest.mark.parametrize("tc", [200, 230, 233, 250])
def test_lencol_abaixo_de_300tc_ficou_em_18_na_fase_3a(regras, fornecedores, tc):
    r = resolver(regras, fornecedores, "KTC", "Flat Sheet", tc, ref=FASE_3A)
    assert r.margem_pct == aprox(0.18) and r.piso_pct == aprox(0.15)


@pytest.mark.parametrize("tc", [300, 400, 500])
def test_lencol_de_300tc_para_cima_ficou_em_20_na_fase_3a(regras, fornecedores, tc):
    r = resolver(regras, fornecedores, "KTC", "Flat Sheet", tc, ref=FASE_3A)
    assert r.margem_pct == aprox(0.20) and r.piso_pct == aprox(0.17)


def test_daune_ficou_em_12_sem_colchao_e_travada_na_fase_3a(regras, fornecedores):
    for familia in ["Pillow", "Duvet Insert", "Mattress Topper", "Flat Sheet", None]:
        r = resolver(regras, fornecedores, "DAUNE", familia, 300, ref=FASE_3A)
        assert r.margem_pct == aprox(0.12)
        assert r.piso_pct == aprox(0.12)
        assert r.comissao_formacao_pct == aprox(0.05)
        assert r.preco_travado is True
        assert r.politica == pol.ROTULO


def test_decor_tricot_ficou_em_12_com_piso_10_na_fase_3a(regras, fornecedores):
    r = resolver(regras, fornecedores, "DECOR_TRICOT", "Bed Runner", ref=FASE_3A)
    assert r.margem_pct == aprox(0.12) and r.piso_pct == aprox(0.10)
    assert r.comissao_formacao_pct == aprox(0.10) and r.preco_travado is False


def test_regra_geral_existia_na_fase_3a(regras):
    r = resolver_margem(regras, fornecedor_id=None, familia="Alguma Coisa", ref=FASE_3A)
    assert r.margem_pct == aprox(0.17) and r.piso_pct == aprox(0.14)


# ---------------------------------------------------------------------------
# A política de 21/09/2026 — vigente hoje: margens FINAIS no B2B, +1 p.p. a partir de 400 fios
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("familia", ["Bath Towel", "Hand Towel", "Face Towel", "Pool Towel",
                                     "Beach Towel", "Bath Mat", "Wash Cloth", "Towel"])
def test_toalhas_ktc_b2b_16(regras, fornecedores, familia):
    r = resolver(regras, fornecedores, "KTC", familia)
    assert r.margem_pct == aprox(0.16) and r.piso_pct is None
    assert r.comissao_formacao_pct == aprox(0.05) and r.preco_travado is False
    assert r.politica == pol.ROTULO_2026_09_21


def test_roupao_ktc_b2b_14(regras, fornecedores):
    r = resolver(regras, fornecedores, "KTC", "Bathrobe")
    assert r.margem_pct == aprox(0.14) and r.comissao_formacao_pct == aprox(0.05)


@pytest.mark.parametrize("familia", ["Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet"])
@pytest.mark.parametrize("tc,esperado", [(200, 0.20), (233, 0.20), (250, 0.20), (299, 0.20),
                                         (300, 0.22), (350, 0.22), (399, 0.22),
                                         (400, 0.23), (500, 0.23), (600, 0.23)])
def test_lencois_ktc_20_22_23_por_faixa_de_fios(regras, fornecedores, familia, tc, esperado):
    r = resolver(regras, fornecedores, "KTC", familia, tc)
    assert r.margem_pct == aprox(esperado), f"{familia} {tc} fios"
    assert r.politica == pol.ROTULO_2026_09_21


@pytest.mark.parametrize("familia", ["Pillow Case", "Duvet Cover"])
@pytest.mark.parametrize("tc,esperado", [(200, 0.19), (300, 0.19), (399, 0.19), (400, 0.20),
                                         (600, 0.20), (None, 0.19)])
def test_fronhas_e_capas_19_e_20_a_partir_de_400_fios(regras, fornecedores, familia, tc, esperado):
    r = resolver(regras, fornecedores, "KTC", familia, tc)
    assert r.margem_pct == aprox(esperado), f"{familia} {tc} fios"


@pytest.mark.parametrize("familia", ["Duvet Insert", "Pillow", "Mattress Protector",
                                     "Mattress Topper", "Pillow Protector", "Slipper",
                                     "Bed Runner", "Blanket"])
def test_demais_familias_ktc_b2b_19_e_20_com_400_fios(regras, fornecedores, familia):
    assert margem(regras, fornecedores, "KTC", familia) == aprox(0.19)
    assert margem(regras, fornecedores, "KTC", familia, 300) == aprox(0.19)
    assert margem(regras, fornecedores, "KTC", familia, 400) == aprox(0.20), "+1 p.p. ≥ 400 fios"


def test_daune_b2b_13_sem_travar(regras, fornecedores):
    for familia in ["Pillow", "Duvet Insert", "Mattress Topper", "Flat Sheet", None]:
        r = resolver(regras, fornecedores, "DAUNE", familia, 300)
        assert r.margem_pct == aprox(0.13)
        assert r.piso_pct is None, "o B2B é o piso — não há piso separado"
        assert r.comissao_formacao_pct == aprox(0.05)
        assert r.preco_travado is False, "Daune deixou de ser travado em 21/09/2026"
        assert r.politica == pol.ROTULO_2026_09_21


def test_decor_tricot_b2b_13(regras, fornecedores):
    r = resolver(regras, fornecedores, "DECOR_TRICOT", "Bed Runner")
    assert r.margem_pct == aprox(0.13) and r.piso_pct is None
    assert r.comissao_formacao_pct == aprox(0.05) and r.preco_travado is False


def test_elis_cobertor_b2b_14(regras, fornecedores):
    r = resolver(regras, fornecedores, pol.CODIGO_ELIS, "Blanket")
    assert r.margem_pct == aprox(0.14) and r.comissao_formacao_pct == aprox(0.05)
    assert r.politica == pol.ROTULO_2026_09_21


def test_fornecedor_vence_regra_de_familia_ktc(regras, fornecedores):
    """Um lençol 300TC da Daune continua na regra Daune (13%), não na de lençol KTC (22%)."""
    assert margem(regras, fornecedores, "DAUNE", "Flat Sheet", 300) == aprox(0.13)


def test_override_manual_vence_tudo(regras, fornecedores):
    r = resolver(regras, fornecedores, "KTC", "Bath Towel", override=0.09)
    assert r.margem_pct == aprox(0.09) and r.origem == "override" and r.politica is None


def test_regra_resolvida_diz_qual_foi_e_de_onde_veio(regras, fornecedores):
    r = resolver(regras, fornecedores, "KTC", "Bath Towel")
    assert "KTC" in r.regra and "21/09/2026" in r.regra and r.regra_id is not None
    anterior = resolver(regras, fornecedores, "KTC", "Bath Towel", ref=FASE_3A)
    assert "16/09/2026" in anterior.regra and anterior.margem_anterior_pct == aprox(0.12)


def test_produto_sem_regra_nao_recebe_default_silencioso(regras):
    """Política 21/09: sem regra que alcance o produto, NÃO há 15% nem 'geral' — bloqueia."""
    r = resolver_margem(regras, fornecedor_id=None, familia="Alguma Coisa")
    assert r.margem_pct is None and r.tem_regra is False and r.origem == "sem_regra"
    assert "Cadastre" in r.regra or "cadastre" in r.regra


# ---------------------------------------------------------------------------
# A derivação — para CADA regra, e só uma vez
# ---------------------------------------------------------------------------
def _codigo(session, fornecedor_id):
    if fornecedor_id is None:
        return None
    return session.get(Fornecedor, fornecedor_id).codigo


def test_toda_regra_anterior_foi_sucedida_uma_vez(session, regras):
    """21 regras encerradas em 16/09, 21 sucessoras, mesmo escopo e prioridade."""
    antigas = [r for r in regras if r.politica is None]
    novas = [r for r in regras if r.politica == pol.ROTULO]
    assert len(antigas) == 21 and len(novas) == 21
    assert all(r.valid_to == pol.DATA_VIGENCIA for r in antigas)
    # as de 16/09 foram, por sua vez, encerradas em 21/09/2026 pela política B2B
    assert all(r.valid_from == pol.DATA_VIGENCIA and r.valid_to == pol.DATA_VIGENCIA_2026_09_21
               for r in novas)

    def escopo(r):
        return (r.fornecedor_id, (r.familia or "").lower(), r.sku_key, r.min_thread_count,
                r.max_thread_count, r.prioridade)

    por_escopo_antigo = {escopo(r): r for r in antigas}
    assert len(por_escopo_antigo) == 21
    vistos = set()
    for nova in novas:
        antiga = por_escopo_antigo[escopo(nova)]
        assert escopo(nova) not in vistos, "escopo sucedido duas vezes"
        vistos.add(escopo(nova))
        esperado = pol.regra_da_politica(antiga.margem_pct, _codigo(session, antiga.fornecedor_id))
        assert D(nova.margem_pct) == esperado["margem_pct"]
        assert D(nova.piso_pct) == esperado["piso_pct"]
        assert D(nova.comissao_formacao_pct) == esperado["comissao_formacao_pct"]
        assert nova.preco_travado == esperado["preco_travado"]
        assert D(nova.margem_anterior_pct) == D(antiga.margem_pct)
        assert nova.fonte == pol.FONTE
        if _codigo(session, antiga.fornecedor_id) not in (pol.CODIGO_DAUNE, pol.CODIGO_DECOR):
            assert D(nova.margem_pct) == D(antiga.margem_pct) + D("0.02")
            assert D(nova.piso_pct) == D(antiga.margem_pct) - D("0.01")
            assert D(nova.piso_pct) == D(nova.margem_pct) - D("0.03")


def test_nenhuma_regra_recebe_dois_pontos_duas_vezes(session, regras):
    """A sucessora nasce da anterior; derivar a sucessora de novo é erro de quem tentar."""
    novas = [r for r in regras if r.politica == pol.ROTULO]
    for nova in novas:
        codigo = _codigo(session, nova.fornecedor_id)
        if codigo in (pol.CODIGO_DAUNE, pol.CODIGO_DECOR):
            continue
        assert D(nova.margem_pct) - D(nova.margem_anterior_pct) == D("0.02")


def test_exemplo_ilustrativo_15_17_14():
    """old 15% → new 17%, piso 14% — a conta, não uma constante."""
    r = pol.regra_da_politica(0.15, "KTC")
    assert r["margem_pct"] == D("0.17") and r["piso_pct"] == D("0.14")
    assert r["comissao_formacao_pct"] == D("0.10") and r["preco_travado"] is False
    assert r["margem_anterior_pct"] == D("0.15")


# ---------------------------------------------------------------------------
# Resolução por data — C-NEW-13
# ---------------------------------------------------------------------------
def test_sem_ref_e_hoje_e_sem_vigencia_e_explicito(regras, fornecedores):
    """`ref=None` resolve por hoje; só `SEM_VIGENCIA` ignora datas — e aí a antiga aparece."""
    hoje = resolver(regras, fornecedores, "DAUNE")
    sem = resolver(regras, fornecedores, "DAUNE", ref=SEM_VIGENCIA)
    assert hoje.politica == pol.ROTULO_2026_09_21
    assert resolver(regras, fornecedores, "DAUNE", ref=FASE_3A).politica == pol.ROTULO
    # sem filtro de vigência as três regras Daune respondem; desempata a vigência mais recente
    assert sem.regra_id is not None


def test_margem_padrao_do_servico_resolve_por_hoje(session, fornecedores):
    """C-NEW-13: `pricing_service.margem_padrao` passava a chamada sem data."""
    from app import pricing_service as ps
    from app.models import Produto
    p = Produto(sku_key="MRG-HOJE", nome="x", fornecedor_id=fornecedores["DAUNE"],
                familia="Pillow", custo_unitario=10.0, preco_base=20.0)
    r = ps.margem_padrao(session, p)
    assert r.politica == pol.ROTULO_2026_09_21 and r.margem_pct == aprox(0.13)
    assert ps.margem_padrao(session, p, ref=FASE_3A).margem_pct == aprox(0.12)
    assert ps.margem_padrao(session, p, ref=VESPERA).margem_pct == aprox(0.14)

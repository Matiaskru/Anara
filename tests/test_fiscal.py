"""Cenário fiscal da venda, resolvido POR ITEM.

Reescrito na Onda 1. O que mudou em relação à versão anterior destes testes:

* `test_interestadual_contribuinte_e_4` afirmava 4% para **qualquer** fornecedor. Isso
  cristalizava o B-01. Agora a alíquota depende da natureza da mercadoria: importada 4%,
  nacional 7% ou 12% conforme a UF de destino.
* `test_cenario_desconhecido_cai_no_fallback_com_aviso` afirmava que cenário irresolvido
  devolvia 18% com aviso. Isso cristalizava o B-06. Agora devolve `REVIEW_REQUIRED`.

O que **não** mudou e continua sendo cobrado aqui: SP→SP é 18% para contribuinte e para não
contribuinte; a carga final entra como está, sem recálculo; e contribuinte nunca se infere do
estado.
"""
import pytest
from sqlmodel import select

from app.fiscal_rules import (
    OK, REVIEW_REQUIRED, consumidor_final_de, resolver_fiscal_item,
)
from app.models import AliquotaInterestadual, EstadoFiscal, RegraFiscalVenda


@pytest.fixture
def tabelas(session):
    return (session.exec(select(RegraFiscalVenda)).all(),
            session.exec(select(EstadoFiscal)).all(),
            session.exec(select(AliquotaInterestadual)).all())


def resolver(tabelas, destino, contribuinte, origem_fiscal="IMPORTADA",
             finalidade="REVENDA", origem="SP", **kw):
    regras, estados, aliquotas = tabelas
    return resolver_fiscal_item(regras, estados, aliquotas, uf_origem=origem,
                                uf_destino=destino, origem_fiscal=origem_fiscal,
                                contribuinte=contribuinte, finalidade=finalidade, **kw)


# ---------------------------------------------------------------------------
# SP → SP
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("natureza", ["IMPORTADA", "NACIONAL"])
@pytest.mark.parametrize("contribuinte", [True, False])
def test_sp_para_sp_e_18_para_todos(tabelas, natureza, contribuinte):
    """Dentro de SP é 18%, contribuinte ou não, importada ou nacional."""
    r = resolver(tabelas, "SP", contribuinte, origem_fiscal=natureza)
    assert r.status == OK
    assert r.icms_pct == pytest.approx(0.18)
    assert r.difal_responsavel == "NAO_APLICAVEL"


def test_sp_para_sp_nao_tem_difal(tabelas):
    r = resolver(tabelas, "SP", False, finalidade="USO_CONSUMO")
    assert r.difal_pct is None


# ---------------------------------------------------------------------------
# Nacional interestadual — as duas faixas (B-01)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("uf", ["BA", "DF", "ES", "GO"])
def test_nacional_interestadual_faixa_de_7(tabelas, uf):
    r = resolver(tabelas, uf, True, origem_fiscal="NACIONAL")
    assert r.status == OK
    assert r.icms_pct == pytest.approx(0.07), f"SP→{uf} nacional tem de ser 7%"


@pytest.mark.parametrize("uf", ["MG", "PR", "RJ", "RS", "SC"])
def test_nacional_interestadual_faixa_de_12(tabelas, uf):
    r = resolver(tabelas, uf, True, origem_fiscal="NACIONAL")
    assert r.status == OK
    assert r.icms_pct == pytest.approx(0.12), f"SP→{uf} nacional tem de ser 12%"


@pytest.mark.parametrize("uf", ["BA", "MG", "RJ", "TO"])
def test_importada_interestadual_e_4(tabelas, uf):
    r = resolver(tabelas, uf, True, origem_fiscal="IMPORTADA")
    assert r.status == OK
    assert r.icms_pct == pytest.approx(0.04)


def test_mesma_uf_muda_a_aliquota_conforme_a_natureza(tabelas):
    """O mesmo destino dá números diferentes para importada e nacional. É o coração do B-01."""
    importada = resolver(tabelas, "MG", True, origem_fiscal="IMPORTADA")
    nacional = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL")
    assert importada.icms_pct == pytest.approx(0.04)
    assert nacional.icms_pct == pytest.approx(0.12)


def test_os_4_por_cento_nao_sao_constante_universal(session, tabelas):
    """A arquitetura tem de admitir exceção sem alterar código.

    Uma linha de prioridade menor, por NCM, sobrepõe o par de UF — é assim que uma mercadoria
    importada que não se enquadre nos 4% é tratada.
    """
    regras, estados, aliquotas = tabelas
    excecao = AliquotaInterestadual(
        uf_origem="SP", uf_destino="MG", origem_fiscal="IMPORTADA", aliquota=0.12,
        ncm="9999.99.99", prioridade=10, regra="Exceção de teste — sem similar nacional")
    r = resolver_fiscal_item(regras, estados, list(aliquotas) + [excecao], uf_origem="SP",
                             uf_destino="MG", origem_fiscal="IMPORTADA", contribuinte=True,
                             finalidade="REVENDA", ncm="9999.99.99")
    assert r.icms_pct == pytest.approx(0.12), "a exceção por NCM tem de vencer o par de UF"
    # e o item sem esse NCM continua nos 4%
    normal = resolver_fiscal_item(regras, estados, list(aliquotas) + [excecao], uf_origem="SP",
                                  uf_destino="MG", origem_fiscal="IMPORTADA", contribuinte=True,
                                  finalidade="REVENDA", ncm="6302.21.00")
    assert normal.icms_pct == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# Finalidade e consumidor final derivado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("finalidade,esperado", [
    ("REVENDA", False), ("INDUSTRIALIZACAO", False),
    ("USO_CONSUMO", True), ("ATIVO_IMOBILIZADO", True),
])
def test_consumidor_final_e_derivado_da_finalidade(finalidade, esperado):
    assert consumidor_final_de(finalidade) is esperado


@pytest.mark.parametrize("finalidade,esperado", [
    ("REVENDA", False), ("INDUSTRIALIZACAO", False),
    ("USO_CONSUMO", True), ("ATIVO_IMOBILIZADO", True),
])
def test_contribuinte_com_as_quatro_finalidades(tabelas, finalidade, esperado):
    r = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL", finalidade=finalidade)
    assert r.status == OK
    assert r.consumidor_final is esperado
    # a alíquota destacada é a interestadual nos quatro casos
    assert r.icms_pct == pytest.approx(0.12)


def test_finalidade_invalida_bloqueia(tabelas):
    r = resolver(tabelas, "MG", True, finalidade="CONSUMIDOR_FINAL")
    assert r.status == REVIEW_REQUIRED
    assert "não é válida" in r.motivo


# ---------------------------------------------------------------------------
# DIFAL — todos os percentuais sobre a MESMA base: o preço final
# ---------------------------------------------------------------------------
# Fórmula canônica, fechada em 03/09/2026:
#
#   ICMS_origem  = preço × interestadual
#   DIFAL        = preço × (interna_destino − interestadual)      quando o remetente recolhe
#   total_Anara  = interestadual + DIFAL + FCP aplicável = interna_destino + FCP
#
# A coluna `EstadoFiscal.carga_final` NÃO entra: ela é o mesmo diferencial sobre uma base
# anterior à inclusão do ICMS de destino — (interna − 4%)/(1 − interna) — e somá-la a um
# percentual da receita mistura denominadores.

def test_D_contribuinte_revenda_so_paga_a_interestadual(tabelas):
    """Prova D: KTC 4% e Daune 12%, sem DIFAL de consumidor final."""
    ktc = resolver(tabelas, "MG", True, origem_fiscal="IMPORTADA", finalidade="REVENDA")
    daune = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL", finalidade="REVENDA")
    assert ktc.icms_pct == pytest.approx(0.04) and ktc.difal_pct is None
    assert daune.icms_pct == pytest.approx(0.12) and daune.difal_pct is None
    for r in (ktc, daune):
        assert r.difal_responsavel == "NAO_APLICAVEL"


@pytest.mark.parametrize("finalidade", ["USO_CONSUMO", "ATIVO_IMOBILIZADO"])
@pytest.mark.parametrize("natureza,inter", [("IMPORTADA", 0.04), ("NACIONAL", 0.12)])
def test_E_contribuinte_consumidor_final_difal_e_do_destinatario(tabelas, finalidade,
                                                                 natureza, inter):
    """Prova E: DIFAL registrado como responsabilidade do destinatário, fora da margem Anara."""
    r = resolver(tabelas, "MG", True, origem_fiscal=natureza, finalidade=finalidade)
    assert r.status == OK
    assert r.icms_pct == pytest.approx(inter), "só a interestadual reduz a receita da Anara"
    assert r.difal_pct == pytest.approx(0.18 - inter)
    assert r.difal_responsavel == "DESTINATARIO"
    assert r.difal_entra_na_margem is False
    assert r.fcp_pct == 0.0


def test_A_ktc_importada_mg_nao_contribuinte(tabelas):
    """Prova A: origem 4% + DIFAL 14% = 18% de carga total sobre a receita."""
    r = resolver(tabelas, "MG", False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    assert r.status == OK
    assert r.aliquota_interestadual == pytest.approx(0.04)
    assert r.difal_pct == pytest.approx(0.14)
    assert r.icms_pct == pytest.approx(0.18)
    assert r.difal_responsavel == "REMETENTE" and r.difal_entra_na_margem is True


def test_B_daune_nacional_mg_nao_contribuinte(tabelas):
    """Prova B: origem 12% + DIFAL 6% = 18%."""
    r = resolver(tabelas, "MG", False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert r.status == OK
    assert r.aliquota_interestadual == pytest.approx(0.12)
    assert r.difal_pct == pytest.approx(0.06)
    assert r.icms_pct == pytest.approx(0.18)


def test_C_mesmo_destino_divisao_diferente_carga_igual(tabelas):
    """Prova C: KTC e Daune repartem origem/destino de formas diferentes e somam o mesmo."""
    ktc = resolver(tabelas, "MG", False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    daune = resolver(tabelas, "MG", False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert ktc.aliquota_interestadual != daune.aliquota_interestadual
    assert ktc.difal_pct != daune.difal_pct
    assert ktc.icms_pct == pytest.approx(daune.icms_pct) == pytest.approx(0.18)


def test_o_1707_nao_aparece_mais_em_lugar_nenhum(tabelas, session):
    """A carga final legada saiu do motor. Nem como total, nem somada à interestadual."""
    mg = session.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "MG")).first()
    assert mg.carga_final == pytest.approx(0.1707), "a coluna continua na tabela, para histórico"
    for natureza in ("IMPORTADA", "NACIONAL"):
        r = resolver(tabelas, "MG", False, origem_fiscal=natureza, finalidade="USO_CONSUMO")
        assert r.icms_pct != pytest.approx(mg.carga_final)
        assert r.icms_pct != pytest.approx(0.04 + mg.carga_final)
        assert r.icms_pct == pytest.approx(mg.aliquota_interna)


@pytest.mark.parametrize("uf,interna", [("BA", 0.205), ("RJ", 0.22), ("MG", 0.18)])
@pytest.mark.parametrize("natureza", ["IMPORTADA", "NACIONAL"])
def test_carga_total_e_a_interna_do_destino(tabelas, uf, interna, natureza):
    """Sem FCP configurado, a carga total do não contribuinte é a alíquota interna do destino."""
    r = resolver(tabelas, uf, False, origem_fiscal=natureza, finalidade="USO_CONSUMO")
    assert r.status == OK
    assert r.icms_pct == pytest.approx(interna)
    assert r.aliquota_interestadual + r.difal_pct == pytest.approx(interna)


def test_F_sp_para_sp_nao_tem_difal(tabelas):
    """Prova F: intraestadual é 18%, sem DIFAL, em qualquer natureza."""
    for natureza in ("IMPORTADA", "NACIONAL"):
        for contribuinte in (True, False):
            r = resolver(tabelas, "SP", contribuinte, origem_fiscal=natureza,
                         finalidade="USO_CONSUMO")
            assert r.status == OK and r.icms_pct == pytest.approx(0.18)
            assert r.difal_pct is None
            assert r.difal_responsavel == "NAO_APLICAVEL"


# --- FCP: configurado, nunca inferido pela UF ---------------------------------
def test_G_fcp_nao_e_inferido_da_coluna_fem(tabelas, session):
    """A BA tem `fem = 2%` na tabela legada. Sem regra cadastrada, o FCP é zero."""
    ba = session.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "BA")).first()
    assert ba.fem == pytest.approx(0.02), "a coluna legada continua lá"
    r = resolver(tabelas, "BA", False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert r.fcp_pct == 0.0
    assert r.icms_pct == pytest.approx(0.205), "sem FCP configurado, é só a interna"


def test_G_fcp_entra_separado_quando_configurado(tabelas):
    """Prova G: o FCP soma por fora da diferença interna − interestadual."""
    from app.models import RegraFcp
    regras, estados, aliquotas = tabelas
    fcp = RegraFcp(uf_destino="BA", fcp_pct=0.02, prioridade=10,
                   regra="FCP-BA de teste, aplicável a este item")
    r = resolver_fiscal_item(regras, estados, aliquotas, uf_origem="SP", uf_destino="BA",
                             origem_fiscal="NACIONAL", contribuinte=False,
                             finalidade="USO_CONSUMO", regras_fcp=[fcp])
    assert r.aliquota_interestadual == pytest.approx(0.07)
    assert r.difal_pct == pytest.approx(0.205 - 0.07)
    assert r.fcp_pct == pytest.approx(0.02)
    assert r.icms_pct == pytest.approx(0.205 + 0.02)


def test_G_fcp_so_alcanca_o_item_que_a_regra_cobre(tabelas):
    """Regra por NCM não pega item de outro NCM — FCP não se generaliza pela UF."""
    from app.models import RegraFcp
    regras, estados, aliquotas = tabelas
    fcp = RegraFcp(uf_destino="BA", ncm="9999.99.99", fcp_pct=0.02, prioridade=10,
                   regra="FCP-BA só para este NCM")
    alcancado = resolver_fiscal_item(regras, estados, aliquotas, uf_origem="SP", uf_destino="BA",
                                     origem_fiscal="NACIONAL", contribuinte=False,
                                     finalidade="USO_CONSUMO", ncm="9999.99.99",
                                     regras_fcp=[fcp])
    outro = resolver_fiscal_item(regras, estados, aliquotas, uf_origem="SP", uf_destino="BA",
                                 origem_fiscal="NACIONAL", contribuinte=False,
                                 finalidade="USO_CONSUMO", ncm="6302.21.00", regras_fcp=[fcp])
    assert alcancado.fcp_pct == pytest.approx(0.02)
    assert outro.fcp_pct == 0.0


def test_G_fcp_sem_aliquota_confirmada_bloqueia(tabelas):
    """Quando o FCP é materialmente necessário e a alíquota não foi levantada: bloqueia."""
    from app.models import RegraFcp
    regras, estados, aliquotas = tabelas
    fcp = RegraFcp(uf_destino="BA", fcp_pct=0.0, exige_confirmacao=True, prioridade=10,
                   regra="FCP-BA aplicável, alíquota a levantar")
    r = resolver_fiscal_item(regras, estados, aliquotas, uf_origem="SP", uf_destino="BA",
                             origem_fiscal="NACIONAL", contribuinte=False,
                             finalidade="USO_CONSUMO", regras_fcp=[fcp])
    assert r.status == REVIEW_REQUIRED and r.icms_pct is None
    assert "não está confirmada" in r.motivo


def test_fcp_do_destinatario_nao_pesa_na_anara(tabelas):
    """Contribuinte consumidor final: DIFAL e FCP são do destinatário."""
    from app.models import RegraFcp
    regras, estados, aliquotas = tabelas
    fcp = RegraFcp(uf_destino="BA", fcp_pct=0.02, prioridade=10, regra="FCP-BA")
    r = resolver_fiscal_item(regras, estados, aliquotas, uf_origem="SP", uf_destino="BA",
                             origem_fiscal="NACIONAL", contribuinte=True,
                             finalidade="USO_CONSUMO", regras_fcp=[fcp])
    assert r.icms_pct == pytest.approx(0.07)
    assert r.fcp_pct == 0.0


# ---------------------------------------------------------------------------
# Sem fallback — o que não resolve, bloqueia
# ---------------------------------------------------------------------------
def test_origem_ausente_bloqueia(tabelas):
    r = resolver(tabelas, "MG", True, origem=None)
    assert r.status == REVIEW_REQUIRED
    assert "origem" in r.motivo.lower()
    assert r.icms_pct is None, "não pode devolver alíquota nenhuma"


def test_destino_ausente_bloqueia(tabelas):
    r = resolver(tabelas, None, True)
    assert r.status == REVIEW_REQUIRED
    assert r.icms_pct is None


def test_contribuinte_indefinido_bloqueia(tabelas):
    r = resolver(tabelas, "MG", None)
    assert r.status == REVIEW_REQUIRED
    assert "contribuinte" in r.motivo.lower()


def test_natureza_da_mercadoria_indefinida_bloqueia(tabelas):
    r = resolver(tabelas, "MG", True, origem_fiscal=None)
    assert r.status == REVIEW_REQUIRED
    assert "nacional ou importada" in r.motivo


def test_par_de_uf_sem_aliquota_cadastrada_bloqueia_em_vez_de_usar_4(tabelas):
    """O motor antigo devolvia 0.04 quando a UF não estava na tabela. Agora bloqueia."""
    r = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL", origem="RJ")
    assert r.status == REVIEW_REQUIRED
    assert r.icms_pct is None
    assert "RJ→MG" in r.motivo


def test_destino_fora_da_tabela_de_estados_bloqueia(tabelas):
    r = resolver(tabelas, "XX", True)
    assert r.status == REVIEW_REQUIRED
    assert r.icms_pct is None


def test_nenhum_cenario_bloqueado_devolve_18(tabelas):
    """Garantia explícita contra o retorno do B-06: nenhum bloqueio traz o antigo padrão."""
    bloqueios = [
        resolver(tabelas, "MG", True, origem=None),
        resolver(tabelas, None, True),
        resolver(tabelas, "MG", None),
        resolver(tabelas, "MG", True, origem_fiscal=None),
        resolver(tabelas, "XX", True),
    ]
    for r in bloqueios:
        assert r.status == REVIEW_REQUIRED
        assert r.icms_pct is None
        assert r.motivo


# ---------------------------------------------------------------------------
# Contribuinte não se infere do estado
# ---------------------------------------------------------------------------
def test_contribuinte_nao_e_inferido_do_estado(tabelas):
    a = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL")
    b = resolver(tabelas, "MG", False, origem_fiscal="NACIONAL")
    assert a.icms_pct != b.icms_pct, "o mesmo estado com contribuinte diferente muda a carga"

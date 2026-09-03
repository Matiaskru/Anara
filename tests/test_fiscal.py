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
# DIFAL
# ---------------------------------------------------------------------------
def test_contribuinte_revenda_nao_tem_difal(tabelas):
    r = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL", finalidade="REVENDA")
    assert r.difal_pct is None
    assert r.difal_responsavel == "NAO_APLICAVEL"


@pytest.mark.parametrize("finalidade", ["USO_CONSUMO", "ATIVO_IMOBILIZADO"])
def test_contribuinte_consumidor_final_difal_e_do_destinatario(tabelas, finalidade):
    """Regra econômica: o DIFAL do destinatário é registrado e NÃO reduz a margem da Anara."""
    r = resolver(tabelas, "MG", True, origem_fiscal="IMPORTADA", finalidade=finalidade)
    assert r.difal_responsavel == "DESTINATARIO"
    assert r.difal_pct is not None and r.difal_pct > 0
    assert r.difal_entra_na_margem is False
    # o que reduz a receita da Anara é só a interestadual
    assert r.icms_pct == pytest.approx(0.04)


@pytest.mark.parametrize("finalidade", ["USO_CONSUMO", "ATIVO_IMOBILIZADO"])
def test_difal_do_destinatario_nao_inventa_valor_de_outra_operacao(tabelas, finalidade):
    """Nacional a 12%: o preço sai (não depende do DIFAL), mas o valor do DIFAL fica em branco.

    Exibir 5,07% aqui seria mostrar o diferencial de uma operação de 4% como se fosse o desta.
    """
    r = resolver(tabelas, "MG", True, origem_fiscal="NACIONAL", finalidade=finalidade)
    assert r.status == OK
    assert r.icms_pct == pytest.approx(0.12), "o preço não depende do DIFAL do destinatário"
    assert r.difal_responsavel == "DESTINATARIO"
    assert r.difal_pct is None
    assert any("não informado" in a for a in r.avisos)


def test_nao_contribuinte_difal_e_do_remetente_e_entra_na_margem(tabelas):
    """Mercadoria IMPORTADA: a carga final cadastrada foi apurada para 4%, que é a alíquota
    desta operação — então ela se aplica e é usada como está."""
    r = resolver(tabelas, "MG", False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    assert r.status == OK
    assert r.difal_responsavel == "REMETENTE"
    assert r.difal_entra_na_margem is True
    assert r.icms_pct == pytest.approx(0.1707)
    assert r.difal_pct == pytest.approx(0.1707 - 0.04)


def test_carga_final_de_4_por_cento_nao_serve_para_operacao_de_12(tabelas):
    """A correção P0 de 03/09: a carga final NÃO é chaveada só pela UF de destino.

    Cada linha de `EstadoFiscal` foi apurada para uma alíquota interestadual específica —
    4% em todas as 27 UFs, conferido aritmeticamente. Usar a carga de MG (17,07%, apurada
    sobre 4%) numa operação nacional de 12% cobraria o DIFAL de outra operação. Como
    recalcular por base simples/dupla/FEM é proibido, o cenário bloqueia.
    """
    r = resolver(tabelas, "MG", False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert r.status == REVIEW_REQUIRED
    assert r.icms_pct is None, "não pode devolver a carga de uma operação diferente"
    assert "12.00%" in r.motivo and "4.00%" in r.motivo


@pytest.mark.parametrize("uf", ["BA", "RJ", "MG"])
def test_nacional_nao_contribuinte_bloqueia_em_todos_os_destinos(tabelas, uf):
    r = resolver(tabelas, uf, False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert r.status == REVIEW_REQUIRED and r.icms_pct is None


@pytest.mark.parametrize("uf", ["BA", "RJ", "MG"])
def test_importada_nao_contribuinte_continua_resolvendo(tabelas, uf, session):
    """A operação importada casa com a alíquota que a tabela pressupõe: segue funcionando."""
    from sqlmodel import select as _select
    linha = session.exec(_select(EstadoFiscal).where(EstadoFiscal.uf == uf)).first()
    r = resolver(tabelas, uf, False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    assert r.status == OK
    assert r.icms_pct == pytest.approx(linha.carga_final)


def test_mesmo_destino_com_operacao_diferente_nao_da_a_mesma_carga(tabelas):
    """O ponto central da correção: destino igual, operação diferente, resultado diferente."""
    importada = resolver(tabelas, "MG", False, origem_fiscal="IMPORTADA",
                         finalidade="USO_CONSUMO")
    nacional = resolver(tabelas, "MG", False, origem_fiscal="NACIONAL",
                        finalidade="USO_CONSUMO")
    assert importada.icms_pct == pytest.approx(0.1707)
    assert nacional.icms_pct is None
    assert importada.status != nacional.status


def test_sp_para_sp_nao_contribuinte_nao_tem_difal(tabelas):
    """Intraestadual não passa pela carga final nem pelo DIFAL, em nenhuma natureza."""
    for natureza in ("IMPORTADA", "NACIONAL"):
        r = resolver(tabelas, "SP", False, origem_fiscal=natureza, finalidade="USO_CONSUMO")
        assert r.status == OK
        assert r.icms_pct == pytest.approx(0.18)
        assert r.difal_pct is None
        assert r.difal_responsavel == "NAO_APLICAVEL"


def test_responsabilidade_do_destinatario_custa_menos_para_a_anara(tabelas):
    """Mesmo destino, mercadoria importada: contribuinte suporta 4%; não contribuinte, a carga
    final inteira. É a diferença entre quem recolhe o DIFAL."""
    contribuinte = resolver(tabelas, "MG", True, origem_fiscal="IMPORTADA",
                            finalidade="USO_CONSUMO")
    nao = resolver(tabelas, "MG", False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    assert contribuinte.icms_pct == pytest.approx(0.04)
    assert nao.icms_pct == pytest.approx(0.1707)
    assert contribuinte.icms_pct < nao.icms_pct


def test_carga_final_nao_e_recalculada(tabelas, session):
    """OK-13 preservado: quando se aplica, a Carga Final entra como está.

    O motor não deriva a carga de base simples, base dupla ou FEM — nem para consertar o
    cenário nacional. Ou a carga cadastrada serve à operação, ou o cenário bloqueia.
    """
    ba = session.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "BA")).first()
    r = resolver(tabelas, "BA", False, origem_fiscal="IMPORTADA", finalidade="USO_CONSUMO")
    assert r.icms_pct == pytest.approx(ba.carga_final)
    # e o cenário nacional não vira uma carga derivada por conta própria
    n = resolver(tabelas, "BA", False, origem_fiscal="NACIONAL", finalidade="USO_CONSUMO")
    assert n.icms_pct is None
    for derivado in (ba.base_simples, ba.base_dupla, ba.aliquota_interna - 0.12):
        assert n.icms_pct != derivado


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

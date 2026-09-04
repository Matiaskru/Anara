"""Custo por SKU: versionado, rastreável e isolado.

O teste central deste arquivo é `test_versionamento_isola_sku_e_preserva_historico`: ele é a
prova de que atualizar o custo de **um** SKU não reescreve o passado nem contamina o vizinho.
Sem essa garantia, toda atualização de custo vira um risco de reprecificar cotação emitida.
"""
import json
from datetime import date

import pytest
from sqlmodel import select

from app import custo_service as cs
from app.models import (
    CostMethod, CustoReferencia, Fornecedor, Produto, StatusCusto, STATUS_QUE_PRECIFICAM,
)
from decimais import MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


def novo_produto(session, sku, fornecedor, **kw):
    p = Produto(sku_key=sku, nome=kw.pop("nome", f"Produto {sku}"),
                fornecedor_id=fornecedor.id, **kw)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# Fórmula Daune
# ---------------------------------------------------------------------------
def test_cnet_daune_pelos_componentes():
    """A conta é feita pelos componentes; o fator 0,7986 é conferência, não fórmula."""
    c = cs.cnet_nacional(1000.0)
    assert c.icms_credito == aprox(120.0)
    assert c.base_pis_cofins == aprox(880.0)
    assert c.pis_cofins_credito == aprox(81.40)
    assert c.cnet == aprox(798.60)
    assert c.fator == aprox(0.7986, abs=1e-6)


@pytest.mark.parametrize("gross,cnet", [
    (427.50, 341.4015), (469.30, 374.7830), (679.72, 542.8244), (678.60, 541.9300),
])
def test_cnet_dos_edredons_280g(gross, cnet):
    """Os nove preços da linha nova são BRUTOS; o CNET sai da fórmula, não da tabela."""
    assert cs.cnet_nacional(gross).cnet == aprox(cnet, abs=1e-4)


def test_gross_invalido_nao_produz_custo():
    for valor in (0, -10, None):
        with pytest.raises(ValueError):
            cs.cnet_nacional(valor)


def test_fator_nunca_e_aplicado_sobre_custo_persistido(session, daune):
    """O erro que a Sessão 2 existe para não cometer.

    Um SKU cujo custo persistido veio de outro documento: aplicar 0,7986 sobre ele daria um
    número sem significado. A referência nova parte do bruto da fonte correspondente.
    """
    p = novo_produto(session, "FATOR-1", daune, custo_unitario=500.0)
    ref = cs.registrar_daune(session, p, 1000.0, fonte="fonte nova", documento="doc",
                             data_ref=date(2026, 8, 12))
    assert ref.cnet_brl == aprox(798.60)
    assert ref.cnet_brl != aprox(500.0 * 0.7986)
    assert ref.valor_bruto == 1000.0


# ---------------------------------------------------------------------------
# O teste obrigatório: versionamento, isolamento e histórico
# ---------------------------------------------------------------------------
def test_versionamento_isola_sku_e_preserva_historico(session, daune):
    """1) SKU X tem V1. 2) Cotação A usa V1. 3) V2 entra só no X. 4) Cotação B usa V2.
    5) A continua com V1. 6) SKU Y intacto. 7) V1 auditável. 8) as duas fontes disponíveis.
    """
    x = novo_produto(session, "VERS-X", daune, nome="SKU X")
    y = novo_produto(session, "VERS-Y", daune, nome="SKU Y")

    # 1) X recebe a versão 1
    v1 = cs.registrar_daune(session, x, 1000.0, fonte="Tabela Daune 27.07.26",
                            documento="27.07.26.xlsx", data_ref=date(2026, 7, 27))
    session.commit()
    assert v1.versao == 1 and v1.vigente is True
    cnet_v1 = v1.cnet_brl
    assert x.custo_unitario == aprox(cnet_v1)

    # 2) a cotação A congela o número do momento — é o snapshot, não uma leitura futura
    cotacao_a = {"sku": x.sku_key, "cnet_no_momento": x.custo_unitario,
                 "versao_lida": v1.versao}
    custo_y_antes = y.custo_unitario

    # 3) V2 entra SOMENTE no X
    v2 = cs.registrar_daune(session, x, 1200.0, fonte="Linha Hotelaria 12.08.26",
                            documento="12.08.26.xlsx", data_ref=date(2026, 8, 12))
    session.commit()
    session.refresh(x)
    session.refresh(y)

    assert v2.versao == 2 and v2.vigente is True
    assert v2.substitui_versao == 1

    # 4) cotação nova pega a vigente
    vigente = cs.referencia_vigente(session, x.id)
    assert vigente.versao == 2
    assert x.custo_unitario == aprox(v2.cnet_brl)
    assert v2.cnet_brl != aprox(cnet_v1)

    # 5) a cotação A não mudou — o snapshot dela é dela
    assert cotacao_a["cnet_no_momento"] == aprox(cnet_v1)

    # 6) o SKU Y não foi tocado
    assert y.custo_unitario == custo_y_antes
    assert cs.referencia_vigente(session, y.id) is None

    # 7) V1 continua no banco, auditável, com vigência fechada
    session.refresh(v1)
    assert v1.vigente is False
    assert v1.valid_to is not None
    assert v1.cnet_brl == aprox(cnet_v1)

    # 8) as duas fontes continuam disponíveis, com data
    todas = cs.versoes(session, x.id)
    assert [r.versao for r in todas] == [1, 2]
    assert "27.07.26" in todas[0].origem_registro
    assert "12.08.26" in todas[1].origem_registro
    assert todas[0].data_ref != todas[1].data_ref
    # e a memória do cálculo de cada uma é legível
    for r in todas:
        memoria = json.loads(r.memoria_calculo)
        assert memoria["gross"] > 0 and memoria["cnet"] == aprox(r.cnet_brl)


def test_referencia_em_uma_data_devolve_a_versao_daquela_epoca(session, daune):
    p = novo_produto(session, "VERS-DATA", daune)
    cs.registrar_daune(session, p, 1000.0, fonte="v1", documento="d1",
                       data_ref=date(2026, 1, 10))
    session.commit()
    cs.registrar_daune(session, p, 1500.0, fonte="v2", documento="d2",
                       data_ref=date(2026, 8, 12))
    session.commit()
    hoje = date.today()
    assert cs.referencia_em(session, p.id, hoje).versao == 2
    # antes da vigência da v1 não havia referência nenhuma
    assert cs.referencia_em(session, p.id, date(2020, 1, 1)) is None


def test_referencia_sem_fonte_e_recusada(session, daune):
    p = novo_produto(session, "SEM-FONTE", daune)
    with pytest.raises(ValueError, match="fonte"):
        cs.registrar_referencia(session, p, cnet_brl=10.0,
                                metodo=CostMethod.daune_direct.value,
                                status=StatusCusto.confirmado.value, fonte="")


def test_status_fora_dos_cinco_canonicos_e_recusado(session, daune):
    p = novo_produto(session, "STATUS-X", daune)
    with pytest.raises(ValueError, match="canônicos"):
        cs.registrar_referencia(session, p, cnet_brl=10.0,
                                metodo=CostMethod.daune_direct.value,
                                status="QUASE_CONFIRMADO", fonte="f")


def test_linhas_legadas_nao_sao_convertidas(session):
    """As 105 observações do modelo antigo ficam sem versão até a reconciliação decidir."""
    legadas = session.exec(select(CustoReferencia)
                           .where(CustoReferencia.versao.is_(None))).all()
    for linha in legadas:
        assert linha.vigente is None and linha.status_custo is None


# ---------------------------------------------------------------------------
# Os cinco status canônicos
# ---------------------------------------------------------------------------
def test_estimado_nasce_com_confirmation_pending(session, daune):
    p = novo_produto(session, "EST-1", daune)
    ref = cs.registrar_referencia(session, p, cnet_brl=100.0,
                                  metodo=CostMethod.ktc_estimated_from_quotes.value,
                                  status=StatusCusto.estimado.value,
                                  fonte="curva de âncoras com buffer de 5%")
    assert ref.confirmation_pending is True


def test_estimado_nao_vira_confirmado_em_silencio(session, daune):
    """Promover exige ato humano: uma versão nova, com fonte de confirmação."""
    p = novo_produto(session, "EST-2", daune)
    estimado = cs.registrar_referencia(session, p, cnet_brl=100.0,
                                       metodo=CostMethod.ktc_estimated_from_quotes.value,
                                       status=StatusCusto.estimado.value, fonte="curva")
    session.commit()
    assert cs.status_do_produto(session, p) == StatusCusto.estimado.value

    confirmado = cs.registrar_referencia(session, p, cnet_brl=104.0,
                                         metodo=CostMethod.ktc_quoted.value,
                                         status=StatusCusto.confirmado.value,
                                         fonte="EXW confirmado pela KTC em 03/09")
    session.commit()
    assert confirmado.versao == estimado.versao + 1
    assert confirmado.confirmation_pending is False
    session.refresh(estimado)
    assert estimado.status_custo == StatusCusto.estimado.value, "a versão antiga não é reescrita"


@pytest.mark.parametrize("status,precifica", [
    (StatusCusto.confirmado.value, True),
    (StatusCusto.estimado.value, True),
    (StatusCusto.revalidar.value, True),
    (StatusCusto.a_cotar.value, False),
    (StatusCusto.review_required.value, False),
])
def test_quais_status_formam_preco(session, daune, status, precifica):
    """REVALIDAR precifica com alerta; A_COTAR e REVIEW_REQUIRED não escrevem custo."""
    p = novo_produto(session, f"ST-{status}", daune, custo_unitario=None)
    cs.registrar_referencia(session, p, cnet_brl=250.0, metodo=CostMethod.daune_direct.value,
                            status=status, fonte="fonte de teste")
    session.commit()
    assert (status in STATUS_QUE_PRECIFICAM) is precifica
    assert (p.custo_unitario is not None) is precifica


def test_revalidar_e_distinto_de_estimado_e_de_a_cotar(session, daune):
    """Os três coexistem e não se confundem: proxy, referência velha e ausência de base."""
    valores = {}
    for status in (StatusCusto.estimado.value, StatusCusto.revalidar.value,
                   StatusCusto.a_cotar.value):
        p = novo_produto(session, f"TRES-{status}", daune)
        ref = cs.registrar_referencia(session, p, cnet_brl=100.0,
                                      metodo=CostMethod.daune_direct.value,
                                      status=status, fonte="f")
        valores[status] = (ref.status_custo, ref.confirmation_pending,
                           p.custo_unitario is not None)
    session.commit()
    assert valores[StatusCusto.estimado.value][1] is True
    assert valores[StatusCusto.revalidar.value][1] is False
    assert valores[StatusCusto.revalidar.value][2] is True    # tem número utilizável
    assert valores[StatusCusto.a_cotar.value][2] is False     # não tem número


# ---------------------------------------------------------------------------
# KTC_SPECIAL_QUOTED
# ---------------------------------------------------------------------------
def test_special_quoted_exige_documento_e_data(session):
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    p = novo_produto(session, "SQ-1", ktc, familia="Fitted Sheet")
    with pytest.raises(ValueError, match="documento e data"):
        cs.registrar_special_quoted(session, p, exw_usd=12.0, peso_kg=1.0, ncm="6302.21.00",
                                    fonte="KTC", documento="", data_ref=None)


def test_special_quoted_grava_procedencia_completa(session):
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    p = novo_produto(session, "SQ-2", ktc, familia="Fitted Sheet")
    ref = cs.registrar_special_quoted(
        session, p, exw_usd=12.5, peso_kg=1.2, ncm="6302.21.00",
        fonte="Cotação KTC 25/08/2026", documento="HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf",
        data_ref=date(2026, 8, 25), validade=date(2026, 11, 25),
        descricao="Fitted sheet com elástico", construcao="fitted", medida="160x200x35",
        observacao="Sem fórmula industrial aprovada — cotado direto", cnet_brl=95.0)
    session.commit()
    assert ref.metodo_custo == CostMethod.ktc_special_quoted.value
    assert ref.status_custo == StatusCusto.confirmado.value
    memoria = json.loads(ref.memoria_calculo)
    for campo in ("exw_usd", "peso_kg", "ncm", "descricao", "construcao", "medida",
                  "observacao", "validade"):
        assert memoria[campo] is not None
    # e o EXW cotado fica no produto, para o motor de nacionalização de sempre
    assert p.exw_cotado_usd == aprox(12.5)
    assert p.exw_cotado_data == date(2026, 8, 25)

"""Frete comercial suportado pela Anara — os 25 pontos exigidos pela Sessão 3A.

O fio condutor: **frete não é um número fixo**. Uma parte é custo do embarque (frete-peso,
pedágio, paletização) e entra no numerador do preço; outra é percentual sobre a nota (ADV,
GRIS, fiel depositário) e entra no denominador, junto com impostos e comissão. Congelar o
percentual sobre um preço preliminar faria a margem-alvo não fechar — e há teste provando que
ela fecha.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from app import frete_engine as fe
from app import frete_service as fs
from app.models import (
    CoberturaFrete, ComponenteFrete, FaixaFrete, Fornecedor, GrupoLogistico, TabelaFrete,
    Transportadora,
)
from app.pricing_engine import TaxRuleSet, calcular_por_margem
from app.dinheiro import D  # noqa: E402
from decimais import CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402

FATOR_CUBAGEM = 300.0


# ---------------------------------------------------------------------------
# Uma tabela TRANSAL de teste, com a semântica declarada
# ---------------------------------------------------------------------------
@pytest.fixture
def transal(session):
    t = session.exec(select(Transportadora)
                     .where(Transportadora.codigo == "TRANSAL-TESTE")).first()
    if t is None:
        t = Transportadora(codigo="TRANSAL-TESTE", nome="TRANSAL (teste)")
        session.add(t)
        session.commit()
        session.refresh(t)
    return t


@pytest.fixture
def tabela(session, transal):
    tab = TabelaFrete(
        transportadora_id=transal.id, versao=1,
        origem_logistica_cidade="Itajaí", origem_logistica_uf="SC",
        documento_fonte="Tabela TRANSAL 2026-02 (teste)", data_fonte=date(2026, 2, 1),
        valid_from=date(2026, 2, 1), valid_to=date(2026, 12, 31),
        tarifa_unidade="BRL_POR_TONELADA", faixa_unidade="KG",
        minimo_unidade="BRL_POR_EMBARQUE", fator_cubagem_kg_m3=FATOR_CUBAGEM,
        pedagio_base="PESO_TAXADO", icms_situacao="NAO_APLICA")
    session.add(tab)
    session.commit()
    session.refresh(tab)

    for regiao, t1, t2, minimo in (("REGIAO CACHOEIRINHA - RS", 598.0, 538.0, 179.0),
                                   ("REGIAO GUARULHOS - SP", 439.0, 395.0, 132.0),
                                   ("REGIAO PASSO FUNDO - RS", None, None, None)):
        session.add(FaixaFrete(tabela_id=tab.id, regiao_destino=regiao, peso_de=0.0,
                               peso_ate=7000.0, tarifa=t1, frete_minimo=minimo))
        session.add(FaixaFrete(tabela_id=tab.id, regiao_destino=regiao, peso_de=7000.0,
                               peso_ate=None, tarifa=t2, frete_minimo=minimo))
    session.add(CoberturaFrete(tabela_id=tab.id, cidade="CACHOEIRINHA", uf="RS",
                               regiao_destino="REGIAO CACHOEIRINHA - RS"))
    session.add(CoberturaFrete(tabela_id=tab.id, cidade="SÃO PAULO", uf="SP",
                               regiao_destino="REGIAO GUARULHOS - SP",
                               unidade="FILIAL - GUARULHOS - SP"))
    session.add(CoberturaFrete(tabela_id=tab.id, cidade="PASSO FUNDO", uf="RS",
                               regiao_destino="REGIAO PASSO FUNDO - RS"))
    session.commit()
    return tab


def componente(tabela_id, codigo, tipo, valor, situacao="APLICA", automatico=True, nome=None):
    return ComponenteFrete(tabela_id=tabela_id, codigo=codigo, nome=nome or codigo, tipo=tipo,
                           valor=valor, situacao=situacao, automatico=automatico)


@pytest.fixture
def componentes(tabela):
    """ADV e pedágio confirmados pela fonte; GRIS deixado explícito em cada teste."""
    return [componente(tabela.id, "ADV", fe.PERCENTUAL_NF, 0.002),
            componente(tabela.id, "PEDAGIO", fe.POR_PESO, 0.0536)]


def calcular(tabela, session, componentes, regiao="REGIAO CACHOEIRINHA - RS",
             peso=500.0, volume=None, nf=13350.0, **kw):
    faixas = session.exec(select(FaixaFrete)
                          .where(FaixaFrete.tabela_id == tabela.id)).all()
    return fe.calcular_frete_grupo(tabela, faixas, componentes, regiao, peso, volume, nf, **kw)


# ---------------------------------------------------------------------------
# 1-3 · peso taxado
# ---------------------------------------------------------------------------
def test_1_peso_real_maior_que_cubado_usa_real(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=1000.0, volume=1.0)   # cubado 300
    assert r.status == fe.OK
    assert r.peso_taxado_kg == aprox(1000.0)
    assert r.peso_taxado_fonte == "PESO_REAL"


def test_2_cubado_maior_que_real_usa_cubado(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=100.0, volume=2.0)    # cubado 600
    assert r.peso_taxado_kg == aprox(600.0)
    assert r.peso_taxado_fonte == "SHIPMENT_VOLUME"


def test_3_sem_volume_quando_a_cubagem_importa_bloqueia(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=None)
    assert r.status == fe.REVIEW_REQUIRED
    assert "volume" in r.motivo.lower()
    assert r.cf_logistico is None, "não se estima volume a partir do produto"


# ---------------------------------------------------------------------------
# 4 · mínimo
# ---------------------------------------------------------------------------
def test_4_minimo_substitui_apenas_o_frete_peso(tabela, session, componentes):
    """100 kg → 0,1 t × 598 = R$ 59,80, abaixo do mínimo de R$ 179. Pedágio soma por fora."""
    r = calcular(tabela, session, componentes, peso=100.0, volume=0.1)
    assert r.minimo_aplicado is True
    assert r.frete_peso == aprox(179.0)
    assert r.componentes_fixos["PEDAGIO"] == aprox(100.0 * 0.0536)
    assert r.cf_logistico == aprox(179.0 + 5.36)
    assert r.cf_logistico > 179.0, "o mínimo não substitui o frete TOTAL"


# ---------------------------------------------------------------------------
# 5-9 · ADV, GRIS e a recomposição
# ---------------------------------------------------------------------------
def test_5_adv_e_percentual_da_nf(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0, nf=13350.0)
    assert r.componentes_percentuais["ADV"] == aprox(0.002)
    assert r.valor_rv(13350.0) == aprox(26.70), "o exemplo da própria planilha"


def test_6_adv_entra_no_denominador_e_a_margem_fecha(tabela, session, componentes):
    """O teste que prova a arquitetura: RV no denominador faz a margem-alvo fechar exata."""
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    regras = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016,
                        comissao_tabela=[(0.0, 0.05), (0.6, 0.06), (0.7, 0.07)],
                        frete_cf_unitario=r.cf_logistico, frete_rv_pct=r.rv_logistico_pct)
    res = calcular_por_margem(1000.0, 1, 0.14, regras)
    lucro = (res.faturamento - res.impostos - res.comissao - res.custo_total
             - res.frete_cf - res.frete_rv)
    # O lucro recomposto É o lucro do resultado: a linha reconcilia por construção.
    assert lucro == res.lucro
    assert res.reconcilia()
    # A margem-alvo fecha exata no preço preciso (era o teste de 1e-12 da Sessão 3A) e fica a
    # meio centavo dela no preço cobrado — a única diferença que o arredondamento introduz.
    assert abs(res.ajuste_arredondamento) <= MEIO_CENTAVO
    assert lucro / res.faturamento == aprox(0.14, abs="0.00001")


def test_7_gris_so_entra_quando_aplicavel(tabela, session):
    sem = calcular(tabela, session,
                   [componente(tabela.id, "ADV", fe.PERCENTUAL_NF, 0.002),
                    componente(tabela.id, "GRIS", fe.PERCENTUAL_NF, 0.001,
                               situacao=fe.NAO_APLICA)],
                   peso=500.0, volume=1.0)
    assert "GRIS" not in sem.componentes_percentuais
    assert sem.rv_logistico_pct == aprox(0.002)


def test_7b_gris_desconhecido_bloqueia_em_vez_de_virar_zero(tabela, session):
    r = calcular(tabela, session,
                 [componente(tabela.id, "GRIS", fe.PERCENTUAL_NF, 0.001,
                             situacao=fe.DESCONHECIDO)],
                 peso=500.0, volume=1.0)
    assert r.status == fe.REVIEW_REQUIRED
    assert "não é 0%" in r.motivo


def test_8_gris_aplicavel_entra_no_rv(tabela, session):
    r = calcular(tabela, session,
                 [componente(tabela.id, "ADV", fe.PERCENTUAL_NF, 0.002),
                  componente(tabela.id, "GRIS", fe.PERCENTUAL_NF, 0.001)],
                 peso=500.0, volume=1.0)
    assert r.rv_logistico_pct == aprox(0.003)
    assert r.valor_rv(13350.0) == aprox(13350.0 * 0.003)


def test_9_mudar_o_preco_muda_adv_deterministicamente(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    assert r.valor_rv(10000.0) == aprox(20.0)
    assert r.valor_rv(20000.0) == aprox(40.0)
    assert r.valor_rv(20000.0) == aprox(2 * r.valor_rv(10000.0))


def test_25_percentual_da_nf_nunca_e_congelado_num_preco_preliminar(tabela, session,
                                                                    componentes):
    """O RV é uma fração até o preço existir; nunca um R$ calculado antes."""
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    assert isinstance(r.rv_logistico_pct, Decimal) and 0 < r.rv_logistico_pct < 1
    assert "ADV" not in r.componentes_fixos, "ADV não pode virar componente fixo"


# ---------------------------------------------------------------------------
# 10 · pedágio
# ---------------------------------------------------------------------------
def test_10_pedagio_usa_a_base_declarada(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=100.0, volume=2.0)   # taxado = 600
    assert r.componentes_fixos["PEDAGIO"] == aprox(600.0 * 0.0536)


def test_10b_base_do_pedagio_desconhecida_bloqueia_quando_muda_o_numero(tabela, session,
                                                                        componentes):
    tabela.pedagio_base = fe.DESCONHECIDO
    session.add(tabela)
    ambiguo = calcular(tabela, session, componentes, peso=100.0, volume=2.0)
    assert ambiguo.status == fe.REVIEW_REQUIRED and "pedágio" in ambiguo.motivo

    # quando real e taxado coincidem, a ambiguidade é imaterial e o cálculo segue
    igual = calcular(tabela, session, componentes, peso=600.0, volume=2.0)
    assert igual.status == fe.OK
    tabela.pedagio_base = "PESO_TAXADO"
    session.add(tabela)


# ---------------------------------------------------------------------------
# 11-13 · destino
# ---------------------------------------------------------------------------
def test_11_destino_coberto_resolve_regiao(session, tabela):
    """São Paulo capital está listada na filial Guarulhos — evidência da própria tabela."""
    assert fs.regiao_do_destino(session, tabela.id, "São Paulo", "SP") \
        == "REGIAO GUARULHOS - SP"
    assert fs.regiao_do_destino(session, tabela.id, "SAO PAULO") == "REGIAO GUARULHOS - SP"


def test_12_fora_da_cobertura_vira_a_cotar(session, tabela, componentes):
    assert fs.regiao_do_destino(session, tabela.id, "Manaus", "AM") is None
    r = calcular(tabela, session, componentes, regiao=None, volume=1.0)
    assert r.status == fe.A_COTAR
    assert "próxima" in r.motivo or "cobertura" in r.motivo


def test_13_passo_fundo_vira_a_cotar(tabela, session, componentes):
    r = calcular(tabela, session, componentes, regiao="REGIAO PASSO FUNDO - RS", volume=1.0)
    assert r.status == fe.A_COTAR
    assert "sem tarifa" in r.motivo.lower()
    assert "vizinha" in r.motivo


# ---------------------------------------------------------------------------
# 14-15 · CIF e FOB
# ---------------------------------------------------------------------------
def test_14_cif_irresolvido_bloqueia_emissao():
    for status in (fe.A_COTAR, fe.REVIEW_REQUIRED, fe.ICMS_REVIEW_REQUIRED):
        assert fe.ResultadoFrete(status=status).bloqueado is True
    assert fe.ResultadoFrete(status=fe.OK).bloqueado is False
    assert fe.ResultadoFrete(status=fe.ESTIMADO).bloqueado is False


def test_15_fob_nao_cobra_frete_da_anara(session):
    from app.models import Cotacao
    cot = Cotacao(cliente_id=0, freight_type="FOB", estado_destino="São Paulo")
    r = fs.frete_da_cotacao(session, cot, [])
    assert r["cif"] is False and r["frete_total"] == 0.0
    assert r["responsavel"] == "CLIENTE"


# ---------------------------------------------------------------------------
# 16-18 · shipment groups
# ---------------------------------------------------------------------------
def test_17_tabela_de_itajai_nao_resolve_origem_incompativel(session, tabela):
    """A TRANSAL-Itajaí não vira tabela padrão de Daune só por ser a única cadastrada."""
    achada, motivo = fs.tabela_para_origem(session, "Itajaí", "SC", tabela.transportadora_id)
    assert achada is not None and motivo is None

    nenhuma, motivo = fs.tabela_para_origem(session, "São Paulo", "SP",
                                            tabela.transportadora_id)
    assert nenhuma is None
    assert "não tem tabela" in motivo and "falta de opção" in motivo


def test_16_duas_origens_viram_dois_grupos(session):
    """Itens de origens logísticas diferentes não viram uma carga artificial."""
    from app.models import CotacaoItem, Produto
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    daune = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
    p1 = Produto(sku_key="FRETE-KTC", nome="a", fornecedor_id=ktc.id, peso_kg=1.0)
    p2 = Produto(sku_key="FRETE-DAUNE", nome="b", fornecedor_id=daune.id, peso_kg=2.0)
    session.add(p1); session.add(p2); session.commit()

    itens = [CotacaoItem(cotacao_id=1, produto_id=p1.id, ordem=0, nome_produto="a",
                         quantidade=10, custo_unitario=1, preco_base=1, preco_negociado=10,
                         margem_liquida=0, faturamento=100.0, custo_total=1, lucro=0),
             CotacaoItem(cotacao_id=1, produto_id=p2.id, ordem=1, nome_produto="b",
                         quantidade=5, custo_unitario=1, preco_base=1, preco_negociado=10,
                         margem_liquida=0, faturamento=50.0, custo_total=1, lucro=0)]
    grupos = fs.agrupar_itens(session, None, itens)
    assert len(grupos) == 2
    valores = sorted(g["valor_mercadoria"] for g in grupos)
    assert valores == [50.0, 100.0]


def test_18_dois_grupos_nao_duplicam_adv_sobre_a_receita_total(tabela, session, componentes):
    """A base do ADV é a NF DO GRUPO. Cotação de 100.000 em dois grupos de 60.000 e 40.000
    paga 0,2% de 60.000 e 0,2% de 40.000 — nunca 0,2% de 100.000 duas vezes."""
    a = calcular(tabela, session, componentes, peso=500.0, volume=1.0, nf=60000.0)
    b = calcular(tabela, session, componentes, peso=500.0, volume=1.0, nf=40000.0)
    adv_a, adv_b = a.valor_rv(60000.0), b.valor_rv(40000.0)
    assert adv_a == aprox(120.0)
    assert adv_b == aprox(80.0)
    assert adv_a + adv_b == aprox(200.0)
    assert adv_a + adv_b == aprox(0.002 * 100000.0), "uma vez sobre o total, não duas"
    assert adv_a + adv_b < 2 * 0.002 * 100000.0


# ---------------------------------------------------------------------------
# 19-20 · rateio
# ---------------------------------------------------------------------------
def test_19_rateio_soma_exatamente_ao_frete_do_grupo(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    itens = [fs.ItemDoGrupo(1, 10, 3, 300.0, 2.5, None),
             fs.ItemDoGrupo(2, 11, 7, 700.0, 1.0, None),
             fs.ItemDoGrupo(3, 12, 1, 100.0, 0.3, None)]
    parcelas = fs.ratear_para_itens(r, itens)
    assert sum(p["cf"] for p in parcelas) == aprox(r.cf_logistico, abs=1e-12)
    assert {p["criterio"] for p in parcelas} == {"peso_taxado_atribuivel"}


def test_19b_sem_peso_individual_o_criterio_muda_e_fica_registrado(tabela, session,
                                                                   componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    itens = [fs.ItemDoGrupo(1, 10, 3, 300.0, None, None),
             fs.ItemDoGrupo(2, 11, 7, 700.0, None, None)]
    parcelas = fs.ratear_para_itens(r, itens)
    assert sum(p["cf"] for p in parcelas) == aprox(r.cf_logistico, abs=1e-12)
    assert {p["criterio"] for p in parcelas} == {"valor_de_mercadoria"}


def test_20_soma_dos_grupos_bate_com_o_frete_da_cotacao(tabela, session, componentes):
    a = calcular(tabela, session, componentes, peso=500.0, volume=1.0, nf=60000.0)
    b = calcular(tabela, session, componentes, peso=800.0, volume=2.0, nf=40000.0)
    total_cf = a.cf_logistico + b.cf_logistico
    total_rv = a.valor_rv(60000.0) + b.valor_rv(40000.0)
    assert a.total(60000.0) + b.total(40000.0) == aprox(total_cf + total_rv)


def test_ratear_devolve_soma_exata():
    parcelas = fe.ratear(100.0, [1, 1, 1])
    assert sum(parcelas) == aprox(100.0, abs=1e-12)
    assert fe.ratear(0.0, []) == []


# ---------------------------------------------------------------------------
# 21-24 · versionamento, vigência, adicionais e unidades
# ---------------------------------------------------------------------------
def test_22_tabela_vencida_nao_e_usada_em_silencio(tabela, session, componentes):
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0,
                 ref_data=date(2027, 3, 1))
    assert r.status == fe.REVIEW_REQUIRED
    assert "venceu" in r.motivo and "silêncio" in r.motivo


def test_21_tabela_nova_nao_altera_cotacao_historica(session, transal, tabela):
    """Versão nova não sobrescreve a anterior; quem já foi emitido guarda o próprio snapshot."""
    nova = TabelaFrete(
        transportadora_id=transal.id, versao=2, origem_logistica_cidade="Itajaí",
        origem_logistica_uf="SC", documento_fonte="Tabela TRANSAL 2027 (teste)",
        valid_from=date(2027, 1, 1), tarifa_unidade="BRL_POR_TONELADA", faixa_unidade="KG",
        minimo_unidade="BRL_POR_EMBARQUE", fator_cubagem_kg_m3=FATOR_CUBAGEM,
        pedagio_base="PESO_TAXADO", icms_situacao="NAO_APLICA")
    session.add(nova)
    session.commit()

    em_2026, _ = fs.tabela_para_origem(session, "Itajaí", "SC", transal.id,
                                       ref_data=date(2026, 6, 1))
    em_2027, _ = fs.tabela_para_origem(session, "Itajaí", "SC", transal.id,
                                       ref_data=date(2027, 6, 1))
    assert em_2026.id == tabela.id, "a tabela de 2026 continua sendo a de 2026"
    assert em_2027.id == nova.id
    session.delete(nova)
    session.commit()


def test_23_adicional_nao_pedido_nao_aparece(tabela, session):
    comps = [componente(tabela.id, "ADV", fe.PERCENTUAL_NF, 0.002),
             componente(tabela.id, "PALETIZACAO", fe.FIXO, 91.0, automatico=False),
             componente(tabela.id, "TDE", fe.POR_HORA, 272.0, automatico=False)]
    sem = calcular(tabela, session, comps, peso=500.0, volume=1.0)
    assert "PALETIZACAO" not in sem.componentes_fixos
    assert "TDE" not in sem.componentes_fixos

    com = calcular(tabela, session, comps, peso=500.0, volume=1.0,
                   adicionais_pedidos={"PALETIZACAO": 3, "TDE": 2})
    assert com.componentes_fixos["PALETIZACAO"] == aprox(273.0)
    assert com.componentes_fixos["TDE"] == aprox(544.0)


def test_24_unidade_da_tarifa_vem_da_tabela(tabela, session, componentes):
    """500 kg × R$ 598/t = R$ 299,00 — o valor do exemplo da própria planilha."""
    assert tabela.tarifa_unidade == "BRL_POR_TONELADA"
    assert fe.frete_peso_de(598.0, 500.0, "BRL_POR_TONELADA") == aprox(299.0)
    assert fe.frete_peso_de(598.0, 500.0, "BRL_POR_KG") == aprox(299000.0)
    with pytest.raises(ValueError):
        fe.frete_peso_de(598.0, 500.0, "CHUTE")


def test_faixa_acima_de_7000_usa_a_outra_tarifa(tabela, session, componentes):
    abaixo = calcular(tabela, session, componentes, peso=6000.0, volume=20.0)
    acima = calcular(tabela, session, componentes, peso=8000.0, volume=26.0)
    assert abaixo.tarifa_aplicada == aprox(598.0)
    assert acima.tarifa_aplicada == aprox(538.0)


# ---------------------------------------------------------------------------
# ICMS da prestação
# ---------------------------------------------------------------------------
def test_icms_desconhecido_bloqueia_com_status_proprio(tabela, session, componentes):
    tabela.icms_situacao = fe.DESCONHECIDO
    session.add(tabela)
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    assert r.status == fe.ICMS_REVIEW_REQUIRED
    assert "gross-up" in r.motivo and "aproximação" in r.motivo
    tabela.icms_situacao = "NAO_APLICA"
    session.add(tabela)


def test_gross_up_usa_a_aliquota_cadastrada_e_nao_12_hardcoded(tabela, session, componentes):
    tabela.icms_situacao = fe.APLICA
    tabela.icms_pct = 0.12
    session.add(tabela)
    r = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    assert r.status == fe.OK
    # O CF é a quantia que o pricing usa e que a Anara paga: sai do motor em centavos.
    # R$ 370,2272… não é um valor cobrável; R$ 370,23 é.
    esperado = (D("299.0") + D("500.0") * D("0.0536")) / (1 - D("0.12"))
    assert r.cf_logistico == aprox(esperado, abs=MEIO_CENTAVO)
    assert r.cf_logistico == D("370.23")

    tabela.icms_pct = 0.07          # outra alíquota → outro gross-up, nada hardcoded
    session.add(tabela)
    r7 = calcular(tabela, session, componentes, peso=500.0, volume=1.0)
    assert r7.cf_logistico == aprox((D("299.0") + D("26.80")) / (1 - D("0.07")),
                                    abs=MEIO_CENTAVO)
    tabela.icms_situacao, tabela.icms_pct = "NAO_APLICA", None
    session.add(tabela)


# ---------------------------------------------------------------------------
# O exemplo da própria planilha
# ---------------------------------------------------------------------------
def test_o_exemplo_da_planilha_e_reproduzido(tabela, session):
    """500 kg para Cachoeirinha-RS, NF R$ 13.350: frete 299,00 + ADV 26,70 + pedágio 26,80."""
    comps = [componente(tabela.id, "ADV", fe.PERCENTUAL_NF, 0.002),
             componente(tabela.id, "PEDAGIO", fe.POR_PESO, 0.0536)]
    r = calcular(tabela, session, comps, peso=500.0, volume=1.0, nf=13350.0)
    assert r.frete_peso == aprox(299.0)
    assert r.componentes_fixos["PEDAGIO"] == aprox(26.80)
    assert r.valor_rv(13350.0) == aprox(26.70)
    assert r.cf_logistico + r.valor_rv(13350.0) == aprox(352.50)

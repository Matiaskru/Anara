"""Histórico reproduzível: mudar premissa hoje não pode mexer no que já foi emitido."""
from datetime import date

import pytest
from sqlmodel import select

from app import config_service as cfg
from app.models import MaterialPreco, MargemRegra, ParametroKTC, Premissa
from app.peso import resolver_peso
from decimais import MEIO_CENTAVO, aprox  # noqa: E402


def test_premissa_nova_fecha_a_anterior_sem_apagar(session):
    antes = cfg.num(session, "fx_usd_brl")
    cfg.definir(session, "fx_usd_brl", valor_num=5.50, fonte="teste")
    assert cfg.num(session, "fx_usd_brl") == aprox(5.50)

    historico = session.exec(select(Premissa).where(Premissa.chave == "fx_usd_brl")).all()
    fechadas = [p for p in historico if not p.ativo]
    assert any(p.valor_num == aprox(antes) for p in fechadas)
    assert all(p.valid_to is not None for p in fechadas)

    cfg.definir(session, "fx_usd_brl", valor_num=antes, fonte="teste — volta ao valor original")


def test_material_versionado_nao_reescreve_o_historico(session):
    material = session.exec(select(MaterialPreco)
                            .where(MaterialPreco.material == "250TC Sateen CVC 70/30")
                            .where(MaterialPreco.plain_or_stripe == "plain")).first()
    preco_original = material.price_usd_m2
    material.valid_to = date.today()
    material.ativo = False
    session.add(material)
    session.add(MaterialPreco(material=material.material, plain_or_stripe="plain",
                              price_usd_m2=1.40, fonte="teste"))
    session.commit()

    vigente = cfg.material_preco(session, "250TC Sateen CVC 70/30", "plain")
    assert vigente.price_usd_m2 == aprox(1.40)
    antigos = session.exec(select(MaterialPreco)
                           .where(MaterialPreco.material == material.material)).all()
    assert any(m.price_usd_m2 == aprox(preco_original) for m in antigos)


def test_cotacao_guarda_a_margem_padrao_que_usou(session):
    """Alterar a regra de margem depois não muda o que já está gravado no item."""
    from app.models import CotacaoItem
    item = CotacaoItem(cotacao_id=999, ordem=0, nome_produto="Teste", quantidade=1,
                       custo_unitario=50.0, preco_base=100.0, preco_negociado=100.0,
                       margem_liquida=0.16, faturamento=100.0, custo_total=50.0, lucro=16.0,
                       margem_padrao_pct=0.16, margem_regra="KTC — Flat Sheet < 300TC")
    session.add(item)
    session.commit()

    regra = session.exec(select(MargemRegra)
                         .where(MargemRegra.nome == "KTC — Flat Sheet < 300TC")).first()
    regra.margem_pct = 0.22
    session.add(regra)
    session.commit()

    session.refresh(item)
    assert item.margem_padrao_pct == aprox(0.16)
    assert item.margem_liquida == aprox(0.16)
    regra.margem_pct = 0.16
    session.add(regra)
    session.commit()


def test_peso_real_da_ktc_nunca_e_trocado_por_estimativa():
    r = resolver_peso(peso_real=0.8, largura_cm=190, comprimento_cm=250, gsm=None,
                      thread_count=250, familia="Flat Sheet",
                      gsm_por_tc={"250": 125}, gsm_por_familia={}, peso_por_familia={})
    assert r.peso_kg == aprox(0.8) and r.tipo == "REAL KTC"


def test_peso_estimado_usa_area_vezes_gsm():
    """190 × 250 cm = 4,75 m²; × 125 g/m² = 0,59375 kg."""
    r = resolver_peso(None, 190, 250, None, 250, "Flat Sheet",
                      {"250": 125}, {}, {})
    assert r.peso_kg == aprox(0.59375)
    assert r.tipo == "ESTIMADO" and "área × GSM" in r.fonte


def test_peso_tecnico_da_familia_e_ultimo_recurso():
    r = resolver_peso(None, None, None, None, None, "Slipper", {}, {}, {"Slipper": 0.15})
    assert r.peso_kg == aprox(0.15) and r.tipo == "TÉCNICO"


def test_sem_dado_nenhum_nao_inventa_peso():
    r = resolver_peso(None, None, None, None, None, "Coisa Nova", {}, {}, {})
    assert r.peso_kg is None and r.tipo is None


def test_parametro_ktc_com_escopo_ganha_do_global(session):
    session.add(ParametroKTC(chave="waste", escopo="Duvet Cover", valor=0.05, fonte="teste"))
    session.commit()
    assert cfg.parametro_ktc(session, "waste", "Duvet Cover") == aprox(0.05)
    assert cfg.parametro_ktc(session, "waste", "Flat Sheet") == aprox(0.03)


def test_peso_por_m2_da_familia_vence_a_conta_de_tecido():
    """Peso de embarque da KTC inclui bainha, painéis e embalagem — a calibração reflete isso."""
    r = resolver_peso(None, 190, 260, None, 250, "Duvet Cover",
                      gsm_por_tc={"250": 125}, gsm_por_familia={}, peso_por_familia={},
                      peso_kg_m2_familia={"Duvet Cover": 0.3070})
    assert r.peso_kg == aprox(4.94 * 0.3070, abs=1e-6)
    assert "peso/m² da família" in r.fonte
    # a conta de tecido puro daria menos da metade
    tecido = resolver_peso(None, 190, 260, None, 250, "Duvet Cover",
                           {"250": 125}, {}, {}, {})
    assert tecido.peso_kg < r.peso_kg / 2


def test_peso_real_continua_ganhando_da_calibracao():
    r = resolver_peso(1.5, 190, 260, None, 250, "Duvet Cover", {"250": 125}, {}, {},
                      {"Duvet Cover": 0.3070})
    assert r.peso_kg == aprox(1.5) and r.tipo == "REAL KTC"

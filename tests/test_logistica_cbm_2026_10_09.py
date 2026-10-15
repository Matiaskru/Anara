"""Estrutura de logística por caixa / CBM (09/10/2026, migration 0028).

Só estrutura: os campos existem, nascem nulos e o motor de preço não os lê — preencher uma
caixa não muda o custo nem o preço de um SKU. Quando o rateio por CBM for decidido, ESTE é o
teste que precisa mudar, de propósito.
"""
from app import calculadora as calc
from app import pricing_service as ps
from app.models import Produto

CAMPOS_CBM = ("quantity_per_carton", "carton_length_cm", "carton_width_cm", "carton_height_cm",
              "carton_gross_weight_kg", "cbm_per_carton", "cbm_per_unit", "volumetric_weight",
              "chargeable_weight")


def test_campos_cbm_existem_e_nascem_nulos():
    p = Produto(sku_key="teste-cbm", nome="Roupão")
    for campo in CAMPOS_CBM:
        assert campo in Produto.__table__.columns
        assert getattr(p, campo) is None


def test_caixa_preenchida_nao_muda_custo_nem_preco(session):
    def lencol():
        return calc.produto_simulado(session, familia="Top Sheet", largura_cm=190,
                                     comprimento_cm=250, material_id=3)

    sem = lencol()
    com = lencol()
    com.quantity_per_carton, com.carton_length_cm, com.carton_width_cm = 30, 57, 37
    com.carton_height_cm, com.carton_gross_weight_kg = 50, 26.0
    com.cbm_per_carton, com.cbm_per_unit = 0.105, 0.0035
    com.volumetric_weight, com.chargeable_weight = 17.5, 26.0

    m_sem = ps.memoria_do_preco(session, sem)
    m_com = ps.memoria_do_preco(session, com)
    assert m_sem["custo"]["net_brl"] == m_com["custo"]["net_brl"]
    assert m_sem["b2b"] == m_com["b2b"]
    session.rollback()

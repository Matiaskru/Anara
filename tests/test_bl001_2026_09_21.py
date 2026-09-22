"""BL-001 (KTC Samples Quotation 29/07/2026): preço confirmado, peso logístico ESTIMADO.

O EXW US$ 10,71 é dado da KTC e continua CONFIRMADO; o peso 2,40 kg é PREMISSA INTERNA
(proporção com o BL-003: 4,650 kg / US$ 20,86), gravada com essa rastreabilidade e usada pelo
motor de nacionalização como qualquer peso (frete = kg × US$/kg). Nada é hardcodado no preço:
o B2B sai do motor vigente (primeiro centavo com margem ≥ 19%).
"""
from decimal import Decimal

import pytest
from sqlmodel import select

from app import dados_2026_09_21 as dados
from app import pricing_service as ps
from app.dinheiro import D
from app.models import Cotacao, Produto
from app.pricing_engine import calcular_por_preco, preco_b2b, preco_de_tabela
from conftest import _novo_usuario
from decimais import aprox

X = Decimal


@pytest.fixture
def owner():
    return _novo_usuario("OWNER")


def _blankets(session):
    out = {}
    for p in session.exec(select(Produto)).all():
        for cod in ("BL-001", "BL-002", "BL-003"):
            if f"· {cod} ·" in (p.exw_cotado_fonte or ""):
                out[cod] = p
    return out


#: Os campos que a etapa `bl001` escreve. Zerá-los devolve o SKU ao estado que `aplicar_ktc`
#: deixa — o único estado em que `plano_bl001` tem trabalho a fazer.
CAMPOS_DE_PESO = ("peso_kg", "peso_tipo", "peso_fonte", "peso_data", "peso_documento")


def _reverter_etapa_bl001(session, produto):
    """Devolve o BL-001 ao estado pré-etapa — a pré-condição deste teste, explicitamente.

    A fixture `session` é de **escopo de sessão**: todos os arquivos de teste compartilham o
    mesmo banco. `aplicar_bl001` é uma etapa de migração de dados e é **idempotente**, então
    qualquer teste que a rode antes deste (é o caso de
    `test_ii_zero_2026_09_22.py::test_snapshot_novo_registra_economia_real_e_formacao_comercial`)
    deixa `plano_bl001` vazio — e a asserção "o plano tem trabalho" passava a depender da
    **ordem alfabética de coleta** do pytest (`bl001` antes de `ii_zero`), não da regra.

    Este teste é sobre "a etapa aplica uma vez e depois não mexe mais". Quem afirma isso
    precisa ser dono da pré-condição, em vez de torcer para ninguém ter passado antes.
    """
    for campo in CAMPOS_DE_PESO:
        setattr(produto, campo, None)
    session.add(produto)
    session.commit()


def _cenario():
    return Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=False,
                   finalidade="USO_CONSUMO", condicao_pagamento="30", freight_type="FOB")


def test_bl001_peso_estimado_preco_confirmado_e_idempotente(session, owner):
    dados.aplicar_ktc(session, owner)
    session.commit()
    antes = _blankets(session)
    assert set(antes) == {"BL-001", "BL-002", "BL-003"}
    _reverter_etapa_bl001(session, antes["BL-001"])
    outros_antes = {c: (p.peso_kg, p.peso_tipo, p.exw_cotado_usd) for c, p in antes.items() if c != "BL-001"}

    assert dados.plano_bl001(session) and dados.aplicar_bl001(session, owner)["atualizados"] == 1
    session.commit()
    p = _blankets(session)["BL-001"]
    # peso: premissa interna, com rastreabilidade — nunca "REAL KTC"
    assert p.peso_kg == aprox(2.40) and p.peso_tipo == "ESTIMADO"
    assert "PREÇO CONFIRMADO · PESO LOGÍSTICO ESTIMADO" in p.peso_fonte
    assert "NÃO informado pela KTC" in p.peso_fonte and "BL-003" in p.peso_fonte
    assert "premissa interna" in (p.peso_documento or "").lower()
    # EXW confirmado, intocado
    assert p.exw_cotado_usd == aprox(10.71) and str(p.exw_cotado_data) == "2026-07-29"
    assert "BL-001" in p.exw_cotado_fonte and p.cost_method == "KTC_QUOTED"
    # BL-002 / BL-003 não mudam
    depois = _blankets(session)
    assert {c: (q.peso_kg, q.peso_tipo, q.exw_cotado_usd) for c, q in depois.items() if c != "BL-001"} == outros_antes
    # idempotente
    assert dados.plano_bl001(session) == [] and dados.aplicar_bl001(session, owner)["atualizados"] == 0


def test_bl001_nacionaliza_com_o_peso_estimado_e_b2b_sai_do_motor(session, owner):
    dados.aplicar_ktc(session, owner)
    dados.aplicar_bl001(session, owner)
    session.commit()
    p = _blankets(session)["BL-001"]
    custo, mem = ps.custo_para_precificar(session, p)
    nac = mem["nacionalizacao"]
    prem = ps.premissas_nacionalizacao(session)
    frete_kg, fx = D(prem.frete_usd_kg), D(prem.fx_usd_brl)
    assert nac["frete_usd"] == aprox(float(X("2.40") * frete_kg))            # 2,40 × 0,516 = 1,2384
    assert mem["exw_usd"] == aprox(10.71) and "29/07/2026" in mem["exw_origem"]
    assert mem["net_fonte"] == ps.CUSTO_DERIVADO_AGORA
    # o peso deixou de ser premissa faltante; desde 22/09/2026 o I.I. econômico é 0% e a
    # família Blanket tem proteção comercial explícita de 0% (era precificada com I.I. 0):
    # nada falta, o status é CONFIRMADO e o B2B comercial coincide com o econômico
    assert not mem.get("premissas_faltantes")
    assert mem["ii_pct"] == 0 and mem["referencia_comercial"]["protecao_pct"] == 0
    assert mem["base_comercial_brl"] == mem["net_brl"]
    assert ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO"
    # margem-alvo 19% (KTC — demais famílias) e B2B = primeiro centavo válido do motor
    m = ps.margem_padrao(session, p)
    assert m.tem_regra and m.margem_pct == aprox(0.19)
    regras, ctx = ps.regras_da_cotacao(session, _cenario(), p)
    b2b = preco_b2b(custo, m.margem_pct, regras)
    assert b2b.margem_liquida >= X("0.19")
    anterior = calcular_por_preco(custo, 1, b2b.preco_negociado - X("0.01"), regras)
    assert anterior.margem_liquida < X("0.19")
    assert preco_de_tabela(b2b.preco_negociado, ctx.get("fator_tabela") or 2) == b2b.preco_negociado * 2
    # com o peso, o custo sobe exatamente o frete internacional (× câmbio) em relação a peso zero
    p.peso_kg = None; p.peso_tipo = None
    session.add(p); session.flush()
    custo_sem_peso, mem2 = ps.custo_para_precificar(session, p)
    session.rollback()
    assert "peso" in (mem2.get("premissas_faltantes") or [])
    assert D(custo) - D(custo_sem_peso) == aprox(X("2.40") * frete_kg * fx, abs=1e-6)

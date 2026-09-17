"""P0 — a origem do custo decide o status, e o status decide o que se pode fazer.

Três pontas soltas do mesmo assunto, fechadas juntas:

**P0.1 — "sem EXW" não é evidência.** Um SKU KTC sem EXW conhecido caía em
`Produto.custo_unitario` e seguia como se nada tivesse acontecido. Ter um número guardado
não diz de onde ele veio nem se ainda vale. Dos 100 SKUs nessa situação, nenhum tem fonte,
data ou documento de EXW: 71 são `LEGACY_EXCEL` com custo e sem rastro, 29 são `MANUAL` com
documento e **sem** custo. Nenhum é preço direto rastreável — logo nenhum pode usar o
catálogo como se fosse evidência.

**P0.2 — dois vocabulários competindo.** `CotacaoItem.status_custo_item` recebia ora
`StatusCusto` (`CONFIRMADO`, `A_COTAR`…), ora `CostConfidence` (`CALCULATED`, `QUOTED`…).
Os portões do workflow só entendem o primeiro. Que os 121 SKUs com `REVIEW_REQUIRED`
bloqueassem era coincidência de string — o mesmo texto existe nos dois. Os 210 com
`QUOTED`/`CALCULATED` passavam por tudo.

**P0.3 — `preco_base` como preço sem custo.** Hoje nenhum SKU explora isso (44 dos 45 sem
custo estão em `REVIEW_REQUIRED`, e o único `QUOTED` não tem preço-base), mas a proteção era
coincidência, não construção.

A correção é uma só: o status canônico é **derivado de como o custo foi resolvido**, e o
método continua onde sempre esteve — em `cost_method`, `custo_confianca` e na memória.
"""
import itertools
import json
from datetime import date

import pytest
from sqlmodel import select

from app import pricing_service as ps
from app import workflow as wf
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto, StatusCusto,
    TipoFornecedor,
)
from conftest import RequestFalsa, _novo_usuario

_SEQ = itertools.count(1)
CANONICOS = {s.value for s in StatusCusto}


@pytest.fixture
def ktc(session):
    f = session.exec(select(Fornecedor)
                     .where(Fornecedor.tipo == TipoFornecedor.importado_ktc)).first()
    if f is None:
        f = Fornecedor(codigo="KTC-C", nome="KTC", tipo=TipoFornecedor.importado_ktc,
                       pais="Egito", moeda_custo="USD",
                       cost_method_padrao=CostMethod.ktc_quoted)
        session.add(f)
        session.commit()
        session.refresh(f)
    return f


def _produto(session, fornecedor, **campos):
    n = next(_SEQ)
    # Desde 17/09/2026 uma cotação direta SEM DATA é REVALIDAR (não se sabe se envelheceu).
    # Os cenários deste módulo falam de cotação direta VÁLIDA: datada de hoje.
    if campos.get("exw_cotado_usd") and "exw_cotado_data" not in campos:
        from datetime import date
        campos["exw_cotado_data"] = date.today()
    p = Produto(sku_key=f"CONF-{n:03d}", nome=f"SKU de confiabilidade {n}",
                familia="Flat Sheet", fornecedor_id=fornecedor.id, ativo=True,
                margem_padrao_pct=0.18, **campos)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _item(session, produto):
    from app.routers.cotacoes import adicionar_item

    cliente = Cliente(nome=f"Cliente conf {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  estado_destino="São Paulo", estado_origem="São Paulo",
                  numero=f"CONF-{cliente.id}")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    adicionar_item(RequestFalsa(_novo_usuario("OWNER")), cot.id, produto_id=produto.id,
                   quantidade=5, modo="margem", valor=None, session=session)
    item = session.exec(select(CotacaoItem)
                        .where(CotacaoItem.cotacao_id == cot.id)).first()
    return cot, item


# ---------------------------------------------------------------------------
# 1-3 — de onde o custo veio
# ---------------------------------------------------------------------------
def test_ktc_com_exw_usa_custo_vivo(session, ktc):
    p = _produto(session, ktc, cost_method=CostMethod.ktc_quoted.value,
                 exw_cotado_usd=10.0, peso_kg=1.0, custo_unitario=1.0)
    cnet, memoria = ps.custo_para_precificar(session, p)
    assert memoria["net_fonte"] == ps.CUSTO_DERIVADO_AGORA
    assert cnet != pytest.approx(1.0), "não pode ter lido a coluna"


def test_ktc_direct_rastreavel_e_permitido(session, ktc):
    """Preço direto da KTC entra por `exw_cotado_usd` e é nacionalizado — é derivado."""
    p = _produto(session, ktc, cost_method=CostMethod.ktc_special_quoted.value,
                 exw_cotado_usd=25.0, exw_cotado_fonte="proposta KTC 2026-09",
                 exw_cotado_data=date.today(), peso_kg=2.0)
    cnet, memoria = ps.custo_para_precificar(session, p)
    assert memoria["net_fonte"] == ps.CUSTO_DERIVADO_AGORA
    assert ps.status_canonico_do_custo(cnet, memoria) == StatusCusto.confirmado.value


def test_ktc_sem_exw_nao_usa_catalogo_em_silencio(session, ktc):
    """Tem número, mas nada que diga de onde veio: revisão necessária, não confirmado."""
    p = _produto(session, ktc, cost_method=CostMethod.legacy_excel.value,
                 exw_cotado_usd=None, preco_ktc_usd=None, custo_unitario=88.0,
                 custo_confianca="QUOTED")
    cnet, memoria = ps.custo_para_precificar(session, p)
    assert memoria["net_fonte"] == ps.CUSTO_DO_CATALOGO
    assert ps.status_canonico_do_custo(cnet, memoria) == StatusCusto.review_required.value

    _, item = _item(session, p)
    assert item.status_custo_item == StatusCusto.review_required.value
    assert wf.blockers_do_item(item), "deveria bloquear"


# ---------------------------------------------------------------------------
# 4-6 — ausência de custo e os portões
# ---------------------------------------------------------------------------
def test_sem_custo_nenhum_vira_a_cotar(session, ktc):
    p = _produto(session, ktc, cost_method=CostMethod.a_cotar_ktc.value,
                 exw_cotado_usd=None, custo_unitario=None, custo_confianca="QUOTED")
    cnet, memoria = ps.custo_para_precificar(session, p)
    assert ps.status_canonico_do_custo(cnet, memoria) == StatusCusto.a_cotar.value


@pytest.mark.parametrize("status", [StatusCusto.a_cotar.value,
                                    StatusCusto.review_required.value])
def test_status_bloqueante_impede_o_documento(session, ktc, status):
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0)
    _, item = _item(session, p)
    item.status_custo_item = status
    codigos = {b.codigo for b in wf.blockers_do_item(item)}
    assert f"CUSTO_{status}" in codigos


# ---------------------------------------------------------------------------
# 7-9 — os estados que não bloqueiam o documento
# ---------------------------------------------------------------------------
def test_estimado_mantem_confirmation_pending(session, ktc):
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0)
    _, item = _item(session, p)
    item.status_custo_item = StatusCusto.estimado.value
    item.confirmation_pending = True
    assert not wf.blockers_do_item(item), "ESTIMADO propõe, não bloqueia o PDF"
    assert item.confirmation_pending is True


def test_revalidar_propoe_mas_nao_compromete(session, ktc):
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0)
    _, item = _item(session, p)
    item.status_custo_item = StatusCusto.revalidar.value
    assert not wf.blockers_do_item(item), "REVALIDAR emite proposta"


def test_confirmado_nao_bloqueia(session, ktc):
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0)
    _, item = _item(session, p)
    assert item.status_custo_item == StatusCusto.confirmado.value
    assert not wf.blockers_do_item(item)


# ---------------------------------------------------------------------------
# 10 — o vocabulário do método não entra mais
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("confianca", ["CALCULATED", "QUOTED", "MANUAL", "LEGACY"])
def test_vocabulario_de_metodo_nao_vira_status(session, ktc, confianca):
    """`CostConfidence` descreve o método. Nunca deve chegar ao status operacional."""
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0,
                 custo_confianca=confianca)
    _, item = _item(session, p)
    assert item.status_custo_item in CANONICOS, (
        f"'{item.status_custo_item}' não é status canônico — o método vazou para o portão")
    assert item.status_custo_item != confianca


def test_status_gravado_e_sempre_canonico(session, ktc):
    for confianca in (None, "CALCULATED", "QUOTED", "REVIEW_REQUIRED"):
        p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0,
                     custo_confianca=confianca)
        _, item = _item(session, p)
        assert item.status_custo_item in CANONICOS


# ---------------------------------------------------------------------------
# 11 — preço-base não substitui custo
# ---------------------------------------------------------------------------
def test_preco_base_nao_torna_item_sem_custo_seguro(session, ktc):
    """Com preço-base e sem custo, o item até nasce — mas não passa pelos portões."""
    p = _produto(session, ktc, cost_method=CostMethod.legacy_excel.value,
                 exw_cotado_usd=None, preco_ktc_usd=None, custo_unitario=None,
                 preco_base=250.0, custo_confianca="QUOTED")
    _, item = _item(session, p)

    assert item.custo_unitario in (0, 0.0, None)
    assert item.status_custo_item == StatusCusto.a_cotar.value
    codigos = {b.codigo for b in wf.blockers_do_item(item)}
    assert "CUSTO_A_COTAR" in codigos, "preço-base não pode mascarar ausência de custo"


def test_preco_base_continua_servindo_de_referencia(session, ktc):
    """O que ele faz bem continua: comparação e diferença versus base."""
    p = _produto(session, ktc, exw_cotado_usd=10.0, peso_kg=1.0, preco_base=999.0)
    _, item = _item(session, p)
    assert item.preco_base == pytest.approx(999.0)
    assert item.diferenca_pct_vs_base is not None


# ---------------------------------------------------------------------------
# 12 — o legado continua legível
# ---------------------------------------------------------------------------
def test_itens_historicos_continuam_legiveis():
    """Os valores herdados não foram reescritos por inferência."""
    import os
    from sqlalchemy import create_engine
    from sqlmodel import Session as S

    caminho = os.path.abspath("data/anara.db")
    if not os.path.exists(caminho):
        pytest.skip("banco operacional ausente")

    eng = create_engine(f"sqlite:///file:{caminho}?mode=ro&uri=true")
    with S(eng) as prod:
        itens = prod.exec(select(CotacaoItem)).all()
        assert itens, "o histórico não pode ter sumido"
        # Valores legados fora do vocabulário canônico continuam onde estavam: reescrevê-los
        # por inferência seria inventar uma classificação que ninguém apurou.
        legados = [i for i in itens
                   if i.status_custo_item and i.status_custo_item not in CANONICOS]
        for i in legados:
            assert i.custo_unitario is not None, "o custo histórico não pode ter sumido"

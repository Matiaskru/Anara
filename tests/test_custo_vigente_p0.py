"""P0 — precificação nova usa premissa vigente; precificação feita não muda.

## O defeito que estes testes fecham

`Produto.custo_unitario` é coluna persistida, gravada quando o custo foi calculado pela
última vez. Ela não é recalculada quando uma premissa versionada muda — e o câmbio é
premissa versionada.

Em 08/09/2026 o câmbio passou de R$ 5,11 para R$ 5,19 pela tela de administração. Os
caminhos que formavam preço liam a coluna, então uma cotação criada **depois** da mudança
saía com o custo de antes. E, como `memoria_json` e `premissas_pinadas` eram calculados
pelo resolvedor vivo, o item se contradizia: preço de 5,11, memória de 5,19, pinos de 5,19.

## O que os testes afirmam

* item novo usa a versão vigente da premissa;
* o custo gravado no item **reconcilia** com a memória e com os pinos;
* rascunho anterior não muda sozinho, mas detecta que há versão nova;
* cotação emitida nunca muda;
* SKU cujo custo não depende de câmbio não se mexe quando o câmbio muda.

Os valores são genéricos: `V1 → V2`. Nada aqui depende de 5,11 nem de 5,19.
"""
import itertools
import json
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import config_service as cfg
from app import pricing_service as ps
from app.models import (
    CostMethod, Cotacao, CotacaoItem, Fornecedor, Premissa, Produto, StatusCotacao,
    TipoFornecedor,
)
from conftest import RequestFalsa, _novo_usuario



@pytest.fixture(autouse=True)
def _restaurar_cambio(session):
    """Devolve o câmbio ao estado anterior depois de cada teste.

    A `session` do conftest é de escopo de sessão: uma premissa trocada aqui continua
    trocada para todo teste que rodar depois, e preço formado com outro câmbio derruba
    suítes que nada têm a ver com este arquivo. Trocar premissa é o assunto destes testes —
    vazá-la não é.
    """
    from app.models import Premissa

    antes = [(p.id, p.valor_num, p.valid_from, p.valid_to, p.ativo)
             for p in session.exec(select(Premissa)
                                   .where(Premissa.chave == "fx_usd_brl")).all()]
    ids_antes = {i for i, *_ in antes}
    yield
    for p in session.exec(select(Premissa).where(Premissa.chave == "fx_usd_brl")).all():
        if p.id not in ids_antes:
            session.delete(p)
    for pid, valor, inicio, fim, ativo in antes:
        p = session.get(Premissa, pid)
        if p is not None:
            p.valor_num, p.valid_from, p.valid_to, p.ativo = valor, inicio, fim, ativo
            session.add(p)
    session.commit()


# ---------------------------------------------------------------------------
# Cenário
# ---------------------------------------------------------------------------
def _fx(session, valor, desde=None):
    """Fecha a versão vigente do câmbio e abre outra. É o que o painel de admin faz."""
    inicio = desde or date.today()
    for p in session.exec(select(Premissa).where(Premissa.chave == "fx_usd_brl")).all():
        if p.ativo and (p.valid_to is None or p.valid_to >= inicio):
            p.valid_to = inicio
            p.ativo = False
            session.add(p)
    nova = Premissa(chave="fx_usd_brl", valor_num=valor, valid_from=inicio,
                    ativo=True, fonte="teste")
    session.add(nova)
    session.commit()
    session.refresh(nova)
    return nova


#: A `session` do conftest é de escopo de sessão — as linhas de um teste continuam vendo o
#: teste seguinte. Cada cenário usa chaves próprias para não colidir em `sku_key`.
_SEQ = itertools.count(1)


@pytest.fixture
def cenario(session):
    """Um SKU KTC cotado em dólar e um cliente, exclusivos deste teste."""
    from app.models import Cliente

    n = next(_SEQ)
    ktc = session.exec(select(Fornecedor)
                       .where(Fornecedor.tipo == TipoFornecedor.importado_ktc)).first()
    if ktc is None:
        ktc = Fornecedor(codigo="KTC-T", nome="KTC Teste",
                         tipo=TipoFornecedor.importado_ktc, pais="Egito",
                         moeda_custo="USD", cost_method_padrao=CostMethod.ktc_quoted)
        session.add(ktc)
        session.commit()
        session.refresh(ktc)

    produto = Produto(sku_key=f"P0-FX-{n:03d}", nome=f"Lençol de prova {n}",
                      familia="Flat Sheet", fornecedor_id=ktc.id,
                      cost_method=CostMethod.ktc_quoted.value,
                      exw_cotado_usd=10.0, peso_kg=1.0, custo_unitario=None,
                      margem_padrao_pct=0.18, ativo=True)
    session.add(produto)

    cliente = Cliente(nome=f"Hotel da Prova FX {n}")
    session.add(cliente)
    session.commit()
    session.refresh(produto)
    session.refresh(cliente)

    _fx(session, 5.00, desde=date.today() - timedelta(days=10))
    return {"produto": produto, "cliente": cliente, "ktc": ktc, "n": n}


def _cotacao(session, cliente, sufixo=""):
    c = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                estado_destino="São Paulo", estado_origem="São Paulo",
                numero=f"P0-{cliente.id}{sufixo}")
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def _adicionar(session, cotacao, produto, usuario):
    from app.routers.cotacoes import adicionar_item
    adicionar_item(RequestFalsa(usuario), cotacao.id, produto_id=produto.id,
                   quantidade=10, modo="margem", valor=None, session=session)
    return session.exec(select(CotacaoItem)
                        .where(CotacaoItem.cotacao_id == cotacao.id)
                        .order_by(CotacaoItem.id.desc())).first()


# ---------------------------------------------------------------------------
# 1-5 — o item novo
# ---------------------------------------------------------------------------
def test_item_novo_usa_a_versao_vigente(session, cenario):
    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)

    esperado, _ = ps.custo_para_precificar(session, cenario["produto"])
    assert item.custo_unitario == pytest.approx(esperado, abs=1e-6)


def test_custo_do_item_reconcilia_com_a_memoria(session, cenario):
    """A invariante que o defeito violava: custo gravado == custo da memória."""
    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)

    memoria = json.loads(item.memoria_json)
    net = (memoria.get("custo") or {}).get("net_brl")
    assert net is not None
    assert item.custo_unitario == pytest.approx(net, abs=1e-6), (
        "o item gravou um custo diferente do que a memória econômica declara")


def test_custo_do_item_reconcilia_com_os_pinos(session, cenario):
    """O CNET recalculado a partir do câmbio pinado tem de dar o custo gravado."""
    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)

    pinos = json.loads(item.premissas_pinadas or "{}")
    fx_pinado = pinos["fx_usd_brl"]["valor"]
    vigente = cfg.premissa(session, "fx_usd_brl")
    assert fx_pinado == pytest.approx(vigente.valor_num)
    assert pinos["fx_usd_brl"]["premissa_id"] == vigente.id


def test_nova_versao_muda_o_custo_do_proximo_item(session, cenario):
    """V1 → item usa V1. Cria V2 → o próximo item usa V2, e o preço muda junto."""
    owner = _novo_usuario("OWNER")
    produto, cliente = cenario["produto"], cenario["cliente"]

    cot_v1 = _cotacao(session, cliente)
    item_v1 = _adicionar(session, cot_v1, produto, owner)
    custo_v1, preco_v1 = item_v1.custo_unitario, item_v1.preco_recomendado

    v2 = _fx(session, 6.00)
    cot_v2 = _cotacao(session, cliente, sufixo="-v2")
    item_v2 = _adicionar(session, cot_v2, produto, owner)

    assert item_v2.custo_unitario > custo_v1, "o custo deveria ter subido com o câmbio"
    assert item_v2.preco_recomendado > preco_v1, "o preço deveria ter acompanhado"
    # a razão entre os custos é a razão entre os câmbios — nada mais mudou
    assert item_v2.custo_unitario / custo_v1 == pytest.approx(6.00 / 5.00, rel=1e-6)

    pinos = json.loads(item_v2.premissas_pinadas or "{}")
    assert pinos["fx_usd_brl"]["premissa_id"] == v2.id
    assert pinos["fx_usd_brl"]["valor"] == pytest.approx(6.00)


def test_item_anterior_nao_muda_quando_nasce_a_versao_nova(session, cenario):
    """O item de antes fica exatamente onde estava — nada é reescrito para trás."""
    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)
    antes = (item.custo_unitario, item.preco_recomendado, item.preco_negociado,
             item.premissas_pinadas, item.memoria_json)

    _fx(session, 7.00)
    session.refresh(item)

    assert (item.custo_unitario, item.preco_recomendado, item.preco_negociado,
            item.premissas_pinadas, item.memoria_json) == antes


# ---------------------------------------------------------------------------
# 6-7 — o rascunho detecta, mas não muda sozinho
# ---------------------------------------------------------------------------
def test_rascunho_detecta_premissa_nova_sem_mudar(session, cenario):
    from app import admin_service as adm

    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)
    custo_antes = item.custo_unitario

    _fx(session, 6.50)

    itens = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.cotacao_id == cot.id)).all()
    achado = adm.premissas_desatualizadas(session, cot, itens)
    assert achado["desatualizado"], "deveria detectar que há premissa mais recente"

    fx = [p for p in achado["premissas"] if p["chave"] == "fx_usd_brl"]
    assert fx, "o câmbio deveria estar entre as premissas mais novas"
    assert fx[0]["rotulo"] == "Câmbio do dólar", "o aviso não pode falar em chave técnica"
    assert fx[0]["no_item"] == "R$ 5,00" and fx[0]["vigente"] == "R$ 6,50"

    session.refresh(item)
    assert item.custo_unitario == custo_antes, "detectar não pode alterar"
    assert item.preco_recomendado is not None
    assert cot.status == StatusCotacao.rascunho


# ---------------------------------------------------------------------------
# 10 — emitida nunca muda
# ---------------------------------------------------------------------------
def test_cotacao_emitida_nao_muda_com_premissa_nova(session, cenario):
    from app import workflow_service as ws

    owner = _novo_usuario("OWNER")
    cot = _cotacao(session, cenario["cliente"])
    item = _adicionar(session, cot, cenario["produto"], owner)
    cot.status = StatusCotacao.emitida
    session.add(cot)
    session.commit()

    congelado = (item.custo_unitario, item.preco_recomendado, item.preco_negociado,
                 item.premissas_pinadas)

    _fx(session, 9.99)
    session.refresh(item)
    assert (item.custo_unitario, item.preco_recomendado, item.preco_negociado,
            item.premissas_pinadas) == congelado

    with pytest.raises(Exception):
        ws.exigir_editavel(cot, "adicionar item")


# ---------------------------------------------------------------------------
# 11-14 — cada fornecedor reage do jeito dele
# ---------------------------------------------------------------------------
def test_ktc_com_exw_reage_ao_cambio(session, cenario):
    produto = cenario["produto"]
    antes, _ = ps.custo_para_precificar(session, produto)
    _fx(session, 10.0)
    depois, _ = ps.custo_para_precificar(session, produto)
    assert depois > antes


def test_ktc_sem_exw_nao_recebe_formula_indevida(session, cenario):
    """Sem EXW conhecido não há o que nacionalizar: o custo do catálogo é preservado."""
    p = Produto(sku_key=f"P0-SEM-EXW-{cenario['n']}", nome="SKU sem EXW", familia="Bathrobe",
                fornecedor_id=cenario["ktc"].id, cost_method=CostMethod.ktc_quoted.value,
                exw_cotado_usd=None, preco_ktc_usd=None, custo_unitario=77.0, ativo=True)
    session.add(p)
    session.commit()
    session.refresh(p)

    antes, memoria = ps.custo_para_precificar(session, p)
    assert antes == pytest.approx(77.0)
    assert memoria["net_fonte"] == ps.CUSTO_DO_CATALOGO

    _fx(session, 12.0)
    depois, _ = ps.custo_para_precificar(session, p)
    assert depois == pytest.approx(77.0), "sem EXW, o câmbio não pode mexer no custo"


def test_fornecedor_nacional_nao_muda_com_o_cambio(session):
    """Daune e Decor: o custo já está em reais e não passa por nacionalização."""
    from app.models import Cliente  # noqa: F401  (mantém o import local coerente)

    nacional = session.exec(select(Fornecedor)
                            .where(Fornecedor.tipo == TipoFornecedor.nacional)).first()
    if nacional is None:
        nacional = Fornecedor(codigo="NAC-T", nome="Nacional Teste",
                              tipo=TipoFornecedor.nacional, pais="Brasil",
                              moeda_custo="BRL",
                              cost_method_padrao=CostMethod.national_supplier)
        session.add(nacional)
        session.commit()
        session.refresh(nacional)

    p = Produto(sku_key="P0-NACIONAL", nome="Peseira nacional", familia="Bed Runner",
                fornecedor_id=nacional.id, cost_method=CostMethod.daune_direct.value,
                custo_unitario=123.45, ativo=True)
    session.add(p)
    session.commit()
    session.refresh(p)

    antes, memoria = ps.custo_para_precificar(session, p)
    assert antes == pytest.approx(123.45)
    assert memoria["net_fonte"] == ps.CUSTO_DO_FORNECEDOR_NACIONAL

    _fx(session, 20.0)
    depois, _ = ps.custo_para_precificar(session, p)
    assert depois == pytest.approx(123.45), "câmbio não toca custo de fornecedor nacional"


# ---------------------------------------------------------------------------
# 15-16 — sem fallback silencioso, e um caminho canônico só
# ---------------------------------------------------------------------------
def test_a_fonte_do_custo_e_sempre_declarada(session, cenario):
    """Toda resolução diz de onde o número veio — derivado, nacional ou catálogo."""
    _, memoria = ps.custo_para_precificar(session, cenario["produto"])
    assert memoria["net_fonte"] in (ps.CUSTO_DERIVADO_AGORA,
                                    ps.CUSTO_DO_FORNECEDOR_NACIONAL,
                                    ps.CUSTO_DO_CATALOGO)


def test_nenhum_caminho_de_precificacao_le_a_coluna_do_produto():
    """Varredura: formar preço não pode voltar a ler `Produto.custo_unitario`."""
    import os
    import re

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alvos = [("app/routers/cotacoes.py", ("adicionar_item", "calcular_item", "duplicar")),
             ("app/routers/calculadora.py", ("salvar",))]
    # Só interessa a coluna **alimentando um cálculo**. Lê-la para dizer se o produto tem
    # custo (`sem_custo`) é exibição, não formação de preço, e continua válido.
    padrao = re.compile(r"produto(?:_atual)?\.custo_unitario")
    exibicao = re.compile(r'"sem_custo"|not bool\(|if not produto')

    achados = []
    for caminho, funcoes in alvos:
        texto = open(os.path.join(raiz, caminho), encoding="utf-8").read()
        for nome in funcoes:
            marca = f"def {nome}("
            if marca not in texto:
                continue
            corpo = texto.split(marca, 1)[1].split("\n@router")[0]
            for n, linha in enumerate(corpo.splitlines(), 1):
                if (padrao.search(linha) and "custo_para_precificar" not in linha
                        and not exibicao.search(linha)):
                    achados.append(f"{caminho}::{nome} +{n}: {linha.strip()}")
    assert achados == [], (
        "caminho de precificação voltou a ler a coluna do produto:\n" + "\n".join(achados))

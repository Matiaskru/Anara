"""Quatro caminhos completos, do jeito que uma pessoa percorre.

Os testes de unidade provam que cada peça faz o que promete. Estes provam que as peças
encaixam: que o cliente cadastrado chega na cotação, que o desconto vira pedido de
aprovação, que a premissa nova não muda nada sozinha, e que o bloqueio bloqueia mesmo.

Tudo em banco temporário (a `session` do conftest), sem tocar em `data/anara.db`.
"""
import itertools
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import crm_service as crm
from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Premissa, Produto,
    StatusCotacao, StatusCusto, StatusOportunidade, TipoFornecedor,
)
from conftest import RequestFalsa, _novo_usuario
from test_cotacao_e2e import chamar

_SEQ = itertools.count(1)



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
# Cenário compartilhado
# ---------------------------------------------------------------------------
@pytest.fixture
def mundo(session):
    """Um fornecedor KTC, um produto cotável e as pessoas dos quatro papéis."""
    n = next(_SEQ)
    ktc = session.exec(select(Fornecedor)
                       .where(Fornecedor.tipo == TipoFornecedor.importado_ktc)).first()
    if ktc is None:
        ktc = Fornecedor(codigo=f"KTC-E{n}", nome="KTC", tipo=TipoFornecedor.importado_ktc,
                         pais="Egito", moeda_custo="USD",
                         cost_method_padrao=CostMethod.ktc_quoted)
        session.add(ktc)
        session.commit()
        session.refresh(ktc)

    produto = Produto(sku_key=f"E2E-{n:03d}", nome=f"Lençol E2E {n}", familia="Flat Sheet",
                      fornecedor_id=ktc.id, cost_method=CostMethod.ktc_quoted.value,
                      exw_cotado_usd=10.0, exw_cotado_data=date.today(), peso_kg=1.0,
                      margem_padrao_pct=0.18, preco_base=100.0, ativo=True)
    session.add(produto)
    session.commit()
    session.refresh(produto)

    return {
        "produto": produto, "ktc": ktc,
        "vendedor": _novo_usuario("VENDEDOR_INTERNO"),
        "owner": _novo_usuario("OWNER"),
    }


def _fx(session, valor):
    """Fecha a versão vigente do câmbio e abre outra — o que o painel de admin faz."""
    hoje = date.today()
    for p in session.exec(select(Premissa).where(Premissa.chave == "fx_usd_brl")).all():
        if p.ativo and (p.valid_to is None or p.valid_to >= hoje):
            p.valid_to, p.ativo = hoje, False
            session.add(p)
    nova = Premissa(chave="fx_usd_brl", valor_num=valor, valid_from=hoje,
                    ativo=True, fonte="E2E")
    session.add(nova)
    session.commit()
    session.refresh(nova)
    return nova


def _cotacao_com_item(session, mundo, cliente, preco=None, oportunidade=None):
    from app.routers.cotacoes import adicionar_item, criar

    req = RequestFalsa(mundo["vendedor"])
    resposta = chamar(criar, request=req, cliente_id=cliente.id,
                      condicao_pagamento="30", estado_destino="São Paulo",
                      oportunidade_id=str(oportunidade.id) if oportunidade else "",
                      nova_venda="" if oportunidade else "Venda lote C",
                      session=session)
    cot_id = int(resposta.headers["location"].rsplit("/", 1)[1])
    adicionar_item(req, cot_id, produto_id=mundo["produto"].id, quantidade=10,
                   modo="margem", valor=None, session=session)
    item = session.exec(select(CotacaoItem)
                        .where(CotacaoItem.cotacao_id == cot_id)).first()
    if preco is not None:
        from app.routers.cotacoes import _aplicar_resultado, _calcular, montar_regras
        cot = session.get(Cotacao, cot_id)
        regras, _r, ctx = montar_regras(cot, session, mundo["produto"])
        res = _calcular("preco", item.custo_unitario, item.quantidade, preco, regras,
                        item.preco_base)
        item.modo_edicao, item.valor_editado = "preco", preco
        _aplicar_resultado(item, res, regras, ctx)
        session.add(item)
        session.commit()
    return session.get(Cotacao, cot_id), item


# ---------------------------------------------------------------------------
# E2E 1 — venda normal
# ---------------------------------------------------------------------------
def test_e2e_venda_normal(session, mundo):
    """Cliente → contato → oportunidade → atividade → etapa → cotação → PDF → emitir → GANHA."""
    vendedor, owner = mundo["vendedor"], mundo["owner"]
    # `_novo_usuario` devolve um objeto com id FIXO por papel. `add` numa sessão que já tem
    # esse id estoura a chave única e derruba a transação inteira — e, como a sessão é de
    # escopo de sessão, todo teste seguinte morre junto. `merge` insere ou atualiza.
    vendedor = session.merge(vendedor)
    session.commit()

    cliente = crm.criar_cliente(session, ator=vendedor, nome=f"Hotel E2E {next(_SEQ)}",
                                cnpj="12345678000199", cidade_uf="SP")
    contato = crm.criar_contato(session, ator=vendedor, cliente_id=cliente.id,
                                nome="Maria Compras", cargo="Compras", principal=True)
    op = crm.criar_oportunidade(session, ator=vendedor, cliente_id=cliente.id,
                                titulo="Enxoval 2026", responsavel_id=vendedor.id)
    crm.criar_atividade(session, ator=vendedor, titulo="Ligar para Maria",
                        tipo="LIGACAO", oportunidade_id=op.id, responsavel_id=vendedor.id,
                        due_em=datetime.utcnow() + timedelta(days=1))
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=vendedor)
    session.commit()

    cot, item = _cotacao_com_item(session, mundo, cliente, oportunidade=op)

    # heranças: o que já se sabia não foi digitado de novo
    assert cot.cliente_id == cliente.id
    assert cot.oportunidade_id == op.id
    assert cot.contato_nome == contato.nome, "o contato principal não foi herdado"
    assert cot.departamento_contato == contato.cargo
    assert cot.vendedor == vendedor.nome, "o responsável não foi herdado"

    # preço formado com o custo vigente
    assert item.custo_unitario > 0 and item.preco_recomendado > 0
    assert item.status_custo_item == StatusCusto.confirmado.value

    prontidao = ws.avaliar(session, cot)
    assert prontidao.pode_emitir, f"não deveria haver blocker: {prontidao.motivos}"

    ws.emitir(session, cot, ator=owner)
    session.commit()
    assert cot.status == StatusCotacao.emitida

    from app.models import SnapshotEmissao
    snapshots = session.exec(select(SnapshotEmissao)
                             .where(SnapshotEmissao.cotacao_id == cot.id)).all()
    assert snapshots, "emitir tinha de congelar o documento"

    crm.vincular_cotacao(session, op, cot, ator=vendedor)
    crm.marcar_ganha(session, op, cot.id, ator=vendedor)
    session.commit()

    assert op.status == StatusOportunidade.ganha.value
    assert op.cotacao_vencedora_id == cot.id
    assert op.valor_fechado == pytest.approx(item.faturamento, abs=0.01)


# ---------------------------------------------------------------------------
# E2E 2 — aprovação
# ---------------------------------------------------------------------------
def test_e2e_aprovacao(session, mundo):
    """Desconto → exceção → pedido → decisão → emissão. E quem não tem alçada não decide."""
    vendedor, owner = mundo["vendedor"], mundo["owner"]
    admin_sem_alcada = _novo_usuario("ADMIN")
    admin_sem_alcada.can_approve_quotes = False

    cliente = crm.criar_cliente(session, ator=vendedor, nome=f"Hotel desconto {next(_SEQ)}")
    cot, item = _cotacao_com_item(session, mundo, cliente)

    # negocia bem abaixo do recomendado. Desde 16/09/2026 a vendedora tem autonomia até o
    # piso de margem (aqui 20% − 3 p.p. = 17%); 25% de desconto fura o piso mesmo com a
    # comissão no mínimo, e é isso que vira exceção.
    abaixo = round(item.preco_recomendado * 0.75, 2)
    cot, item = _cotacao_com_item(session, mundo, cliente, preco=abaixo)
    from app import comercial_service as com
    com.recalcular_comissao(session, cot)
    session.commit()
    session.refresh(item)

    excecoes = wf.excecoes_do_item(item)
    assert any(e.motivo == wf.MARGEM_ABAIXO_PISO for e in excecoes), "o desconto não virou exceção"

    pedido = ws.solicitar_aprovacao(session, cot, ator=vendedor,
                                    justificativa="cliente pediu")
    session.commit()

    with pytest.raises(HTTPException) as erro:
        ws.decidir(session, cot, pedido.id, ator=vendedor, aprovar=True)
    assert erro.value.status_code == 403, "vendedor não pode decidir"

    with pytest.raises(HTTPException) as erro:
        ws.decidir(session, cot, pedido.id, ator=admin_sem_alcada, aprovar=True)
    assert erro.value.status_code == 403, "ADMIN sem alçada não pode decidir"

    decidido = ws.decidir(session, cot, pedido.id, ator=owner, aprovar=True,
                          fingerprint_visto=pedido.fingerprint)
    session.commit()
    assert decidido.aprovador_id == owner.id

    # alteração material posterior invalida a decisão
    item.quantidade = item.quantidade + 5
    session.add(item)
    session.commit()
    atual = wf.fingerprint(cot, ws.itens_de(session, cot.id))
    assert atual != decidido.fingerprint, "mudar a quantidade tinha de mudar a identidade"


# ---------------------------------------------------------------------------
# E2E 3 — premissa nova
# ---------------------------------------------------------------------------
def test_e2e_premissa_nova_manter(session, mundo):
    """Manter: nada muda, e a decisão fica registrada."""
    from app import admin_service as adm

    vendedor = mundo["vendedor"]
    _fx(session, 5.00)
    cliente = crm.criar_cliente(session, ator=vendedor, nome=f"Hotel manter {next(_SEQ)}")
    cot, item = _cotacao_com_item(session, mundo, cliente)
    antes = (item.custo_unitario, item.preco_recomendado, item.premissas_pinadas,
             item.memoria_json)

    _fx(session, 6.00)
    itens = ws.itens_de(session, cot.id)
    achado = adm.premissas_desatualizadas(session, cot, itens)
    assert achado["desatualizado"], "deveria avisar que há versão mais nova"

    session.refresh(item)
    assert (item.custo_unitario, item.preco_recomendado, item.premissas_pinadas,
            item.memoria_json) == antes, "detectar não pode alterar"


def test_e2e_premissa_nova_atualizar(session, mundo):
    """Atualizar: custo, preço, memória e pinos passam para a versão vigente."""
    from app.routers.cotacoes import atualizar_premissas

    vendedor = mundo["vendedor"]
    _fx(session, 5.00)
    cliente = crm.criar_cliente(session, ator=vendedor, nome=f"Hotel atualizar {next(_SEQ)}")
    cot, item = _cotacao_com_item(session, mundo, cliente)
    custo_v1, preco_v1 = item.custo_unitario, item.preco_recomendado

    v2 = _fx(session, 6.00)
    atualizar_premissas(RequestFalsa(mundo["owner"]), cot.id, session=session)

    session.refresh(item)
    assert item.custo_unitario > custo_v1
    assert item.preco_recomendado > preco_v1
    assert item.custo_unitario / custo_v1 == pytest.approx(6.00 / 5.00, rel=1e-6)

    pinos = json.loads(item.premissas_pinadas or "{}")
    assert pinos["fx_usd_brl"]["premissa_id"] == v2.id
    memoria = json.loads(item.memoria_json)
    assert memoria["custo"]["net_brl"] == pytest.approx(item.custo_unitario, abs=1e-6)


def test_e2e_emitida_nao_muda_com_premissa_nova(session, mundo):
    _fx(session, 5.00)
    cliente = crm.criar_cliente(session, ator=mundo["vendedor"],
                                nome=f"Hotel congelado {next(_SEQ)}")
    cot, item = _cotacao_com_item(session, mundo, cliente)
    ws.emitir(session, cot, ator=mundo["owner"])
    session.commit()
    congelado = (item.custo_unitario, item.preco_recomendado, item.premissas_pinadas)

    _fx(session, 9.99)
    session.refresh(item)
    assert (item.custo_unitario, item.preco_recomendado,
            item.premissas_pinadas) == congelado


# ---------------------------------------------------------------------------
# E2E 4 — blocker
# ---------------------------------------------------------------------------
def test_e2e_blocker_impede_emissao_e_nao_e_aprovavel(session, mundo):
    """Falta dado econômico: bloqueia, explica em português, e nenhuma alçada dispensa."""
    vendedor, owner = mundo["vendedor"], mundo["owner"]
    cliente = crm.criar_cliente(session, ator=vendedor, nome=f"Hotel travado {next(_SEQ)}")
    cot, item = _cotacao_com_item(session, mundo, cliente)

    item.status_custo_item = StatusCusto.a_cotar.value
    session.add(item)
    session.commit()

    blockers = wf.blockers_do_item(item)
    assert blockers, "A_COTAR tinha de bloquear"
    assert any(b.codigo == "CUSTO_A_COTAR" for b in blockers)

    prontidao = ws.avaliar(session, cot)
    assert not prontidao.pode_emitir

    with pytest.raises(Exception):
        ws.emitir(session, cot, ator=owner)

    # a mensagem que a tela mostra é frase, não código
    from app import rotulos
    texto = rotulos.blocker("CUSTO_A_COTAR")
    assert "A_COTAR" not in texto
    assert "consulta" in texto.lower()


def test_e2e_blocker_nao_tem_fallback_silencioso(session, mundo):
    """Sem custo resolvível, o item não vira 'confirmado' por causa do preço-base."""
    produto = Produto(sku_key=f"E2E-SEM-{next(_SEQ)}", nome="SKU sem base",
                      fornecedor_id=mundo["ktc"].id,
                      cost_method=CostMethod.legacy_excel.value,
                      exw_cotado_usd=None, preco_ktc_usd=None, custo_unitario=None,
                      preco_base=250.0, custo_confianca="QUOTED", ativo=True)
    session.add(produto)
    session.commit()
    session.refresh(produto)

    cnet, memoria = ps.custo_para_precificar(session, produto)
    assert ps.status_canonico_do_custo(cnet, memoria) == StatusCusto.a_cotar.value

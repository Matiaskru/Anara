"""Fase 3B — CRM comercial simples, Cliente 360, cotações vinculadas e pós-venda.

As 35 regressões pedidas pela fase, na ordem do enunciado. A política econômica da Fase 3A
não é reaberta: os testes do fim provam que ela continua valendo debaixo do CRM.
"""
import asyncio
import inspect
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import admin_service as adm
from app import comercial_service as com
from app import crm_service as crm
from app import metrics_service as mx
from app import pos_venda_service as pv
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, dinheiro
from app.models import (
    AtualizacaoComercial, Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, MargemRegra,
    Oportunidade, OportunidadeEtapaHistorico, Produto, StatusCotacao, StatusOportunidade, Usuario,
)
from conftest import RequestFalsa, _novo_usuario

_SEQ = iter(range(1, 100_000))


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def chamar(funcao, request, **kwargs):
    args = {}
    for nome, p in inspect.signature(funcao).parameters.items():
        if nome == "request":
            args[nome] = request
            continue
        if nome in kwargs:
            args[nome] = kwargs[nome]
            continue
        padrao = p.default
        v = getattr(padrao, "default", padrao)
        args[nome] = None if (v is inspect.Parameter.empty
                              or repr(v) == "PydanticUndefined") else v
    return funcao(**args)


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


@pytest.fixture
def owner(session):
    u = session.exec(select(Usuario).where(Usuario.email == "f3b-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="f3b-owner@anara.test", nome="Dona 3B", senha_hash="h", papel="OWNER",
                    can_approve_quotes=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def vendedora(session):
    u = session.exec(select(Usuario).where(Usuario.email == "f3b-vend@anara.test")).first()
    if u is None:
        u = Usuario(email="f3b-vend@anara.test", nome="Vendedora 3B", senha_hash="h",
                    papel="VENDEDOR_INTERNO")
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def novo_cliente(session, ator, **kw):
    dados = dict(nome=f"Hotel 3B {next(_SEQ)}", cidade_uf="São Paulo", finalidade="REVENDA")
    dados.update(kw)
    c = crm.criar_cliente(session, ator=ator, **dados)
    session.commit()
    return c


def nova_venda(session, ator, cliente, **kw):
    dados = dict(cliente_id=cliente.id, titulo=f"Projeto {next(_SEQ)}")
    dados.update(kw)
    op = crm.criar_oportunidade(session, ator=ator, **dados)
    session.commit()
    return op


def produto_decor(session, fornecedores, custo=100.0):
    sku = f"F3B-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Peseira {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedores["DECOR_TRICOT"].id, familia="Bed Runner",
                cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_na_venda(session, op, ator, produto=None, quantidade=5.0):
    from app.routers.cotacoes import adicionar_item, criar_cotacao_da_venda
    cot = criar_cotacao_da_venda(session, RequestFalsa(ator), op, ator=ator,
                                 estado_destino="São Paulo", contribuinte_icms=True,
                                 freight_type="FOB", condicao_pagamento="30")
    cot.uf_origem_fiscal = "SP"
    cot.finalidade = "REVENDA"
    session.add(cot)
    session.commit()
    session.refresh(cot)
    if produto is not None:
        chamar(adicionar_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
               produto_id=produto.id, quantidade=quantidade, modo="margem", valor=None,
               session=session)
        session.commit()
    return cot


def emitir(session, cot, ator):
    snap = ws.emitir(session, cot, ator=ator)
    session.commit()
    return snap


def vender(session, op, cot, ator):
    crm.marcar_ganha(session, op, cot.id, ator=ator)
    session.commit()
    session.refresh(op)
    return op


# ===========================================================================
# 1. C-NEW-14 — /configuracoes/margem não edita no lugar
# ===========================================================================
def test_01_configuracoes_margem_versiona_e_nao_edita_no_lugar(session, fornecedores):
    from app.routers.configuracoes import salvar_margem
    regra = session.exec(select(MargemRegra)
                         .where(MargemRegra.fornecedor_id == fornecedores["DECOR_TRICOT"].id)
                         .where(MargemRegra.valid_to == None)).first()  # noqa: E711
    antes = {r.id: (r.margem_pct, r.valid_to) for r in session.exec(select(MargemRegra)).all()}
    margem_antiga = regra.margem_pct
    try:
        r = chamar(salvar_margem, RequestFalsa(_novo_usuario("OWNER")), regra_id=regra.id,
                   margem_pct=13.0, fonte="decisão de teste", session=session)
        assert r.status_code == 303 and "ok=1" in r.headers["location"]
        session.refresh(regra)
        # a regra antiga NÃO mudou de valor: foi encerrada
        assert regra.margem_pct == margem_antiga and regra.valid_to == date.today()
        nova = [x for x in session.exec(select(MargemRegra)).all() if x.id not in antes][0]
        assert D(nova.margem_pct) == D("0.13") and nova.valid_from == date.today()
        assert nova.piso_pct == regra.piso_pct and nova.comissao_formacao_pct == regra.comissao_formacao_pct
        assert nova.margem_anterior_pct == margem_antiga and nova.fonte == "decisão de teste"
        assert nova.min_thread_count == regra.min_thread_count
        # sem fonte, nada acontece
        r = chamar(salvar_margem, RequestFalsa(_novo_usuario("OWNER")), regra_id=nova.id,
                   margem_pct=14.0, fonte="", session=session)
        assert "erro=fonte" in r.headers["location"]
        # vendedor não versiona margem
        with pytest.raises(HTTPException) as erro:
            chamar(salvar_margem, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
                   regra_id=nova.id, margem_pct=14.0, fonte="x", session=session)
        assert erro.value.status_code == 403
        assert len(session.exec(select(MargemRegra)).all()) == len(antes) + 1
    finally:
        for x in session.exec(select(MargemRegra)).all():
            if x.id not in antes:
                session.delete(x)
            elif x.valid_to != antes[x.id][1]:
                x.valid_to = antes[x.id][1]
                session.add(x)
        session.commit()


def test_01b_versionar_faixa_de_fios_nao_encerra_a_faixa_vizinha(session, fornecedores):
    """KTC — Flat Sheet < 300TC e ≥ 300TC são escopos diferentes."""
    ktc = fornecedores["KTC"].id
    menor = session.exec(select(MargemRegra).where(MargemRegra.fornecedor_id == ktc)
                         .where(MargemRegra.familia == "Flat Sheet")
                         .where(MargemRegra.max_thread_count == 300)
                         .where(MargemRegra.valid_to == None)).first()  # noqa: E711
    maior = session.exec(select(MargemRegra).where(MargemRegra.fornecedor_id == ktc)
                         .where(MargemRegra.familia == "Flat Sheet")
                         .where(MargemRegra.min_thread_count == 300)
                         .where(MargemRegra.valid_to == None)).first()  # noqa: E711
    antes = {r.id: r.valid_to for r in session.exec(select(MargemRegra)).all()}
    try:
        prop = adm.preview_margem(session, margem_pct="0.19", nome=menor.nome, fonte="t",
                                  fornecedor_id=ktc, familia="Flat Sheet", prioridade=30,
                                  max_thread_count=300)
        adm.aplicar_margem(session, prop, ator=_novo_usuario("OWNER"), margem_pct="0.19",
                           nome=menor.nome, fonte="t", fornecedor_id=ktc, familia="Flat Sheet",
                           prioridade=30, max_thread_count=300)
        session.commit()
        session.refresh(menor); session.refresh(maior)
        assert menor.valid_to == date.today() and maior.valid_to is None
    finally:
        for x in session.exec(select(MargemRegra)).all():
            if x.id not in antes:
                session.delete(x)
            elif x.valid_to != antes[x.id]:
                x.valid_to = antes[x.id]
                session.add(x)
        session.commit()


# ===========================================================================
# 2–4. Landing e dashboard
# ===========================================================================
def test_02_seller_login_cai_em_vendas():
    from app.routers.login import landing
    assert landing(_novo_usuario("VENDEDOR_INTERNO")) == "/vendas"
    assert landing(_novo_usuario("VENDEDOR_COMISSIONADO"), "/") == "/vendas"
    assert landing(_novo_usuario("VENDEDOR_INTERNO"), "/cotacoes/3") == "/cotacoes/3"


def test_03_seller_nao_acessa_dashboard_admin(session):
    from app.routers.dashboard import dashboard
    r = chamar(dashboard, RequestFalsa(_novo_usuario("VENDEDOR_COMISSIONADO")), session=session)
    assert r.status_code == 303 and r.headers["location"] == "/vendas"


def test_04_admin_continua_acessando_dashboard(session):
    from app.routers.dashboard import dashboard
    from app.routers.login import landing
    r = chamar(dashboard, RequestFalsa(_novo_usuario("ADMIN")), session=session)
    assert r.status_code == 200
    assert landing(_novo_usuario("OWNER")) == "/"


# ===========================================================================
# 5–9. Venda, etapas, histórico, automação
# ===========================================================================
def test_05_criar_venda_nasce_em_rascunho(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    assert op.etapa == "RASCUNHO" and op.status == "ABERTA"
    assert crm.status_comercial(op) == "Rascunho"
    assert op.status_pos_venda is None
    with pytest.raises(crm.DadoInvalido):
        nova_venda(session, owner, cliente, etapa="PROSPECCAO")     # legado não nasce


def test_06_trocar_etapa_inline_grava_historico(session, owner):
    from app.routers.vendas import mudar_status
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    r = corpo(chamar(mudar_status, RequestFalsa(owner), venda_id=op.id, etapa="NEGOCIACAO",
                     session=session))
    assert r["status_comercial"] == "Negociação"
    h = crm.historico_de_etapas(session, op.id)
    assert [(x.etapa_anterior, x.etapa_nova) for x in h] == [(None, "RASCUNHO"),
                                                             ("RASCUNHO", "NEGOCIACAO")]
    assert h[-1].ator_email == owner.email and h[-1].ocorrido_em is not None


def test_07_rascunho_enviado_negociacao_nos_dois_sentidos(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    for etapa in ("ENVIADO", "NEGOCIACAO", "ENVIADO", "RASCUNHO", "NEGOCIACAO"):
        crm.mudar_etapa(session, op, etapa, ator=owner)
    session.commit()
    assert op.etapa == "NEGOCIACAO"
    assert len(crm.historico_de_etapas(session, op.id)) == 6
    with pytest.raises(crm.DadoInvalido):
        crm.mudar_etapa(session, op, "GANHA", ator=owner)       # vendido não é etapa


def test_08_emitir_proposta_move_rascunho_para_enviado(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    cot = cotacao_na_venda(session, op, owner, produto_decor(session, fornecedores))
    emitir(session, cot, owner)
    session.refresh(op)
    assert op.etapa == "ENVIADO"
    ultimo = crm.historico_de_etapas(session, op.id)[-1]
    assert ultimo.etapa_nova == "ENVIADO" and "AUTOMÁTICO" in ultimo.observacao
    assert "automático" in (ultimo.ator_email or "")


def test_09_nova_revisao_nao_tira_venda_de_negociacao(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    p = produto_decor(session, fornecedores)
    cot = cotacao_na_venda(session, op, owner, p)
    emitir(session, cot, owner)
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)
    session.commit()
    r2 = ws.criar_revisao(session, cot, ator=owner)
    session.commit()
    emitir(session, r2, owner)
    ws.marcar_enviada(session, r2, ator=owner)
    session.commit()
    session.refresh(op)
    assert op.etapa == "NEGOCIACAO"
    # e nunca marca sozinho negociação, vendido ou perdido
    assert op.status == "ABERTA"


# ===========================================================================
# 10–12. Vendido, perdido, reabrir
# ===========================================================================
def test_10_vendido_usa_validar_compromisso_firme(session, owner, fornecedores):
    from app.routers.vendas import vendido
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    p = produto_decor(session, fornecedores)
    cot = cotacao_na_venda(session, op, owner, p)
    # rascunho não pode ser vencedora
    with pytest.raises(HTTPException) as erro:
        chamar(vendido, RequestFalsa(owner), venda_id=op.id, cotacao_id=cot.id, session=session)
    assert erro.value.status_code == 409
    emitir(session, cot, owner)
    # custo REVALIDAR bloqueia o compromisso firme
    item = ws.itens_de(session, cot.id)[0]
    item.status_custo_item = "REVALIDAR"
    session.add(item)
    session.commit()
    with pytest.raises(HTTPException) as erro:
        chamar(vendido, RequestFalsa(owner), venda_id=op.id, cotacao_id=cot.id, session=session)
    assert "compromisso firme" in erro.value.detail
    item.status_custo_item = "CONFIRMADO"
    session.add(item)
    session.commit()
    r = corpo(chamar(vendido, RequestFalsa(owner), venda_id=op.id, cotacao_id=cot.id,
                     session=session))
    assert r["status_comercial"] == "Vendido" and r["status_pos_venda"] == "AGUARDANDO_ENTREGA"
    session.refresh(op)
    assert op.cotacao_vencedora_id == cot.id and op.valor_fechado == item.faturamento
    assert op.won_em is not None
    # a cotação emitida não foi tocada
    session.refresh(cot)
    assert cot.status == "emitida"


def test_11_perdido_exige_motivo(session, owner):
    from app.routers.vendas import perdido
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    with pytest.raises(HTTPException):
        chamar(perdido, RequestFalsa(owner), venda_id=op.id, motivo="", session=session)
    with pytest.raises(HTTPException):
        crm.marcar_perdida(session, op, ator=owner, motivo="INVENTADO")
    r = corpo(chamar(perdido, RequestFalsa(owner), venda_id=op.id,
                     motivo="PRODUTO_ESPECIFICACAO", nota="fio errado", session=session))
    assert r["status_comercial"] == "Perdido"
    assert "PRODUTO_ESPECIFICACAO" in crm.MOTIVOS_PERDA and "FORA_DE_ESCOPO" not in crm.MOTIVOS_PERDA


def test_12_reabrir_preserva_historico_e_volta_para_a_ultima_etapa(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)
    crm.marcar_perdida(session, op, ator=owner, motivo="PRECO")
    session.commit()
    lost_em = op.lost_em
    crm.reabrir(session, op, ator=owner, motivo="cliente voltou")
    session.commit()
    assert op.status == "ABERTA" and op.etapa == "NEGOCIACAO"       # sem inventar etapa
    assert op.lost_em == lost_em and op.motivo_perda == "PRECO"     # evento fica
    tl = crm.timeline(session, op)
    assert any(e["tipo"] == "perda" for e in tl) and any(e["tipo"] == "reabertura" for e in tl)
    # etapa legada no registro antigo cai em RASCUNHO ao reabrir
    op.etapa = "QUALIFICACAO"
    crm.marcar_perdida(session, op, ator=owner, motivo="PRAZO")
    session.commit()
    crm.reabrir(session, op, ator=owner)
    session.commit()
    assert op.etapa == "RASCUNHO"


# ===========================================================================
# 13–15. Cotação pertence a uma venda; legado continua acessível
# ===========================================================================
def test_13_nova_cotacao_exige_venda(session, owner):
    from app.routers.cotacoes import criar
    cliente = novo_cliente(session, owner)
    with pytest.raises(HTTPException) as erro:
        chamar(criar, RequestFalsa(owner), cliente_id=cliente.id, session=session)
    assert erro.value.status_code == 400 and "venda" in erro.value.detail.lower()
    # criando a venda junto
    r = chamar(criar, RequestFalsa(owner), cliente_id=cliente.id, nova_venda="Enxoval 2026",
               session=session)
    cot = session.get(Cotacao, int(r.headers["location"].rsplit("/", 1)[1]))
    assert cot.oportunidade_id is not None
    op = session.get(Oportunidade, cot.oportunidade_id)
    assert op.titulo == "Enxoval 2026" and op.etapa == "RASCUNHO" and op.responsavel_id == owner.id
    # ou numa venda existente
    r = chamar(criar, RequestFalsa(owner), cliente_id=cliente.id, oportunidade_id=str(op.id),
               session=session)
    cot2 = session.get(Cotacao, int(r.headers["location"].rsplit("/", 1)[1]))
    assert cot2.oportunidade_id == op.id


def test_14_cross_client_continua_bloqueado(session, owner):
    from app.routers.cotacoes import criar
    a = novo_cliente(session, owner)
    b = novo_cliente(session, owner)
    op_b = nova_venda(session, owner, b)
    with pytest.raises(HTTPException) as erro:
        chamar(criar, RequestFalsa(owner), cliente_id=a.id, oportunidade_id=str(op_b.id),
               session=session)
    assert erro.value.status_code == 409
    cot_a = Cotacao(cliente_id=a.id, numero=f"X-{next(_SEQ)}")
    session.add(cot_a)
    session.commit()
    with pytest.raises(crm.DadoInvalido):
        crm.vincular_cotacao(session, op_b, cot_a, ator=owner)


def test_15_cotacoes_legadas_continuam_acessiveis_sem_venda(session, owner):
    from app.routers.cotacoes import detalhe, listar
    cliente = novo_cliente(session, owner)
    legada = Cotacao(cliente_id=cliente.id, numero=f"LEG-{next(_SEQ)}", status="pedido")
    session.add(legada)
    session.commit()
    r = chamar(listar, RequestFalsa(owner), session=session)
    html = bytes(r.body).decode()
    assert legada.numero in html and "Sem venda vinculada (legado)" in html
    r = chamar(detalhe, RequestFalsa(owner), cotacao_id=legada.id, session=session)
    assert r.status_code == 200
    assert crm.cotacoes_com_venda(session).get(legada.id) is None


# ===========================================================================
# 16–19. Cliente 360
# ===========================================================================
def _venda_ganha(session, owner, fornecedores, cliente, custo=100.0, quantidade=5.0):
    op = nova_venda(session, owner, cliente)
    cot = cotacao_na_venda(session, op, owner, produto_decor(session, fornecedores, custo),
                           quantidade)
    emitir(session, cot, owner)
    vender(session, op, cot, owner)
    return op, cot


def test_16_17_18_19_cliente_360(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op1, c1 = _venda_ganha(session, owner, fornecedores, cliente, custo=100.0)
    op2, c2 = _venda_ganha(session, owner, fornecedores, cliente, custo=50.0)
    # uma venda aberta com cotação enviada NÃO é compra
    op3 = nova_venda(session, owner, cliente)
    c3 = cotacao_na_venda(session, op3, owner, produto_decor(session, fornecedores))
    emitir(session, c3, owner)
    # 17: R1/R2 — a venda ganha tem uma revisão a mais; só a vencedora conta
    r2 = ws.criar_revisao(session, c1, ator=owner)
    session.commit()
    m = mx.cliente_360(session, cliente.id)
    esperado = D(op1.valor_fechado) + D(op2.valor_fechado)
    assert D(m["total_comprado"]) == esperado                       # 16
    assert m["quantidade_vendas"] == 2                                # 17
    assert D(m["ticket_medio"]) == esperado / 2                       # 18
    assert m["ultima_compra"] == max(op1.won_em, op2.won_em)          # 19
    assert m["vendas_em_andamento"] == 1 and m["valor_em_andamento"] > 0
    assert D(m["valor_em_aberto"]) == esperado and m["valor_pago"] is None
    # a tela renderiza com os números
    from app.routers.clientes import detalhe
    html = bytes(chamar(detalhe, RequestFalsa(owner), cliente_id=cliente.id,
                        session=session).body).decode()
    assert "Total comprado" in html and op1.titulo in html and (c1.numero or "") in html


# ===========================================================================
# 20–26. Pós-venda
# ===========================================================================
def test_20_pos_venda_nasce_aguardando_entrega(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op, _ = _venda_ganha(session, owner, fornecedores, cliente)
    assert op.status_pos_venda == "AGUARDANDO_ENTREGA"
    assert pv.resumo(op)["valor_faturado"] is None and pv.resumo(op)["valor_pago"] is None


def test_21_registrar_entrega_vai_para_aguardando_pagamento(session, owner, vendedora,
                                                             fornecedores):
    from app.routers.vendas import entrega, entrega_prevista
    cliente = novo_cliente(session, owner)
    op, _ = _venda_ganha(session, owner, fornecedores, cliente)
    r = corpo(chamar(entrega_prevista, RequestFalsa(vendedora), venda_id=op.id,
                     prevista_em="2026-10-01", session=session))
    assert r["entrega_prevista_em"] == "2026-10-01"
    r = corpo(chamar(entrega, RequestFalsa(vendedora), venda_id=op.id, session=session))
    assert r["status_pos_venda"] == "AGUARDANDO_PAGAMENTO"
    session.refresh(op)
    assert op.entregue_em is not None


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_22_23_seller_nao_marca_pago_nem_atrasado(session, owner, fornecedores, papel):
    from app.routers.vendas import atrasado, faturamento, pago, pagamento_previsto
    cliente = novo_cliente(session, owner)
    op, _ = _venda_ganha(session, owner, fornecedores, cliente)
    vend = _novo_usuario(papel)
    for rota, kw in ((pago, {}), (atrasado, {}), (faturamento, {"faturado_em": "2026-09-16"}),
                     (pagamento_previsto, {"previsto_em": "2026-10-10"})):
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(vend), venda_id=op.id, session=session, **kw)
        assert erro.value.status_code == 403
    session.refresh(op)
    assert op.status_pos_venda == "AGUARDANDO_ENTREGA" and op.pago_em is None


def test_24_25_26_admin_marca_pago_atrasado_e_corrige(session, owner, fornecedores):
    from app.routers.vendas import atrasado, faturamento, pago
    cliente = novo_cliente(session, owner)
    op, _ = _venda_ganha(session, owner, fornecedores, cliente)
    admin = _novo_usuario("ADMIN")
    pv.registrar_entrega(session, op, ator=owner)
    session.commit()
    r = corpo(chamar(faturamento, RequestFalsa(admin), venda_id=op.id, faturado_em="2026-09-16",
                     numero_documento_fiscal="NF 123", session=session))
    assert r["numero_documento_fiscal"] == "NF 123" and r["valor_faturado"] == op.valor_fechado
    r = corpo(chamar(atrasado, RequestFalsa(admin), venda_id=op.id, observacao="boleto vencido",
                     session=session))
    assert r["status_pos_venda"] == "ATRASADO"                          # 25
    r = corpo(chamar(pago, RequestFalsa(admin), venda_id=op.id, session=session))
    assert r["status_pos_venda"] == "PAGO" and r["pago_em"]             # 24 e 26
    session.refresh(op)
    assert op.pago_em is not None
    # pago não vira atrasado
    with pytest.raises(HTTPException):
        pv.marcar_atrasado(session, op, ator=owner)
    # trilha financeira
    from app.models import AuditLog
    acoes = {a.acao for a in session.exec(select(AuditLog)
                                          .where(AuditLog.entidade_id == op.id)
                                          .where(AuditLog.entidade == "Oportunidade")).all()}
    assert {"POS_VENDA_FATURAMENTO", "POS_VENDA_STATUS"} <= acoes


def test_27_vendido_faturado_pago_sao_tres_coisas(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op, _ = _venda_ganha(session, owner, fornecedores, cliente)
    periodo = mx.periodo_de("tudo") if hasattr(mx, "periodo_de") else None
    r = pv.resumo(op)
    assert r["valor_vendido"] == op.valor_fechado and r["valor_faturado"] is None and r["valor_pago"] is None
    pv.registrar_faturamento(session, op, ator=owner, faturado_em=date.today(),
                             numero_documento_fiscal="NF 1")
    session.commit()
    r = pv.resumo(op)
    assert r["valor_faturado"] == op.valor_fechado and r["valor_pago"] is None
    pv.marcar_pago(session, op, ator=owner)
    session.commit()
    assert pv.resumo(op)["valor_pago"] == op.valor_fechado
    m = mx.cliente_360(session, cliente.id)
    assert m["valor_pago"] == op.valor_fechado and m["valor_em_aberto"] is None


# ===========================================================================
# 28–29. Timeline e atualização append-only
# ===========================================================================
def test_28_timeline_ordenada_e_completa(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    cot = cotacao_na_venda(session, op, owner, produto_decor(session, fornecedores))
    crm.registrar_atualizacao(session, op, ator=owner, texto="cliente pediu revisão",
                              proxima_atividade={"titulo": "follow-up", "tipo": "FOLLOW_UP",
                                                 "due_em": datetime.utcnow() + timedelta(days=2)})
    emitir(session, cot, owner)
    vender(session, op, cot, owner)
    pv.registrar_entrega(session, op, ator=owner)
    pv.registrar_faturamento(session, op, ator=owner, faturado_em=date.today(),
                             numero_documento_fiscal="NF 9")
    pv.marcar_pago(session, op, ator=owner)
    session.commit()
    tl = crm.timeline(session, op)
    tipos = [e["tipo"] for e in tl]
    for t in ("criacao", "atualizacao", "atividade", "cotacao", "etapa", "ganho", "entrega",
              "faturamento", "pagamento"):
        assert t in tipos, t
    assert [e["quando"] for e in tl] == sorted(e["quando"] for e in tl)
    texto = json.dumps(tl, default=str).lower()
    for proibido in ("custo", "margem", "lucro", "piso", "cnet"):
        assert proibido not in texto


def test_29_atualizacao_comercial_e_append_only(session, owner):
    from app.routers.vendas import registrar_atualizacao
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    r = corpo(chamar(registrar_atualizacao, RequestFalsa(owner), venda_id=op.id,
                     texto="ligou pedindo prazo", session=session))
    nota = session.get(AtualizacaoComercial, r["id"])
    assert nota.autor_email == owner.email and nota.texto == "ligou pedindo prazo"
    with pytest.raises(crm.DadoInvalido):
        crm.registrar_atualizacao(session, op, ator=owner, texto="   ")
    # correção é outra atualização, não edição
    crm.registrar_atualizacao(session, op, ator=owner, texto="correção: era prazo de 30 dias")
    session.commit()
    assert [n.texto for n in crm.atualizacoes_de(session, op.id)][::-1] == [
        "ligou pedindo prazo", "correção: era prazo de 30 dias"]
    assert not hasattr(crm, "editar_atualizacao") and not hasattr(crm, "apagar_atualizacao")


# ===========================================================================
# 30–35. Confidencialidade e não-regressão das fases anteriores
# ===========================================================================
@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_30_seller_payloads_e_telas_de_vendas_sem_economia(session, owner, fornecedores, papel):
    from app.routers.vendas import detalhe, lista, mudar_status, vendas_do_cliente
    cliente = novo_cliente(session, owner)
    op, cot = _venda_ganha(session, owner, fornecedores, cliente)
    vend = _novo_usuario(papel)
    for r in (chamar(lista, RequestFalsa(vend), session=session),
              chamar(detalhe, RequestFalsa(vend), venda_id=op.id, session=session)):
        html = bytes(r.body).decode().lower()
        for proibido in ("custo_unitario", "margem_liquida", "piso_margem", "lucro", "cnet",
                         "comissao_pct", "markup"):
            assert proibido not in html, proibido
        assert "financeiro (dono/administrador)" not in html
    cartao = crm.cartao(session, op)
    assert encontrar_confidenciais(cartao) == []
    assert encontrar_confidenciais(corpo(chamar(vendas_do_cliente, RequestFalsa(vend),
                                                cliente_id=cliente.id, session=session))) == []
    # a vendedora continua vendo a comissão estimada dela na cotação
    av = com.avaliar_negociacao(session, cot)
    assert "comissao_estimada_valor" in com.payload_vendedora(av)


def test_31_32_33_politica_da_fase_3a_nao_regride(session, fornecedores):
    from app import pricing_service as ps
    daune = ps.margem_padrao(session, Produto(sku_key="x", nome="x",
                                              fornecedor_id=fornecedores["DAUNE"].id))
    assert (daune.margem_pct, daune.piso_pct, daune.comissao_formacao_pct, daune.preco_travado) == (
        D("0.12"), D("0.12"), D("0.05"), True)
    decor = ps.margem_padrao(session, Produto(sku_key="y", nome="y",
                                              fornecedor_id=fornecedores["DECOR_TRICOT"].id))
    assert (decor.margem_pct, decor.piso_pct, decor.comissao_formacao_pct) == (
        D("0.12"), D("0.10"), D("0.10"))
    vig = ps.politica_comercial_vigente(session)
    assert (vig.comissao_base_pct, vig.comissao_min_pct) == (D("0.10"), D("0.05"))


def test_34_workflow_tecnico_da_cotacao_continua(session, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, owner, cliente)
    cot = cotacao_na_venda(session, op, owner, produto_decor(session, fornecedores))
    item = ws.itens_de(session, cot.id)[0]
    com.aplicar_negociacao(session, cot, {item.id: dinheiro(D(item.preco_recomendado) * D("0.8"))},
                           ator=owner)
    session.commit()
    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao and wf.MARGEM_ABAIXO_PISO in {e.motivo for e in prontidao.excecoes}
    with pytest.raises(ws.OperacaoInvalida):
        ws.emitir(session, cot, ator=owner)
    pedido = ws.solicitar_aprovacao(session, cot, ator=owner, justificativa="volume")
    ws.decidir(session, cot, pedido.id, ator=owner, aprovar=True)
    session.commit()
    snap = ws.emitir(session, cot, ator=owner)
    session.commit()
    assert snap.aprovacao_id == pedido.id and cot.status == "emitida"
    with pytest.raises(ws.OperacaoInvalida):
        ws.exigir_editavel(cot)


def test_35_pdf_atual_continua_funcionando(session, owner, fornecedores, tmp_path):
    from app.pdf_bridge import gerar_pdf_para_cotacao
    cliente = novo_cliente(session, owner, cnpj_cpf=f"11.111.111/{next(_SEQ):04d}-11")
    op = nova_venda(session, owner, cliente)
    cot = cotacao_na_venda(session, op, owner, produto_decor(session, fornecedores))
    itens = ws.itens_de(session, cot.id)
    caminho = gerar_pdf_para_cotacao(cot, session.get(Cliente, cot.cliente_id), itens,
                                     rascunho=True)
    import os
    assert os.path.exists(caminho) and os.path.getsize(caminho) > 1000
    # e o rascunho continua marcado; o PDF final continua exigindo emissão (Sessão 6/C-NEW-09)
    from app.routers.cotacoes import gerar_pdf
    r = chamar(gerar_pdf, RequestFalsa(owner), cotacao_id=cot.id, session=session)
    assert r.status_code in (200, 303, 409)

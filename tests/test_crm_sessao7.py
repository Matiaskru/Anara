"""Sessão 7 — CRM: prospects, oportunidades, pipeline, atividades e fechamento.

O CRM é a camada que **consome** as seis sessões anteriores sem reabrir nenhuma. Por isso
boa parte destes testes verifica fronteiras: o que o CRM não pode atravessar (economia,
confidencialidade, compromisso firme) e o que ele não pode inventar (vínculo entre clientes
diferentes, oportunidade retroativa, ganho sobre proposta que ninguém pode sustentar).
"""
import inspect
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import crm_service as crm
from app import workflow_service as ws
from app.dinheiro import D, dinheiro
from app.models import (
    AtividadeComercial, Cliente, Contato, CostMethod, Cotacao, CotacaoItem, Fornecedor,
    Oportunidade, OportunidadeEtapaHistorico, Produto, StatusCotacao, StatusOportunidade,
    Usuario,
)
import legado
from conftest import RequestFalsa, _novo_usuario

AGORA = datetime.utcnow()
ONTEM = AGORA - timedelta(days=1)
AMANHA = AGORA + timedelta(days=1)
_SEQ = iter(range(1, 9999))


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


def corpo(r):
    return json.loads(bytes(r.body).decode())


def html(r):
    return bytes(r.body).decode()


def ator(papel="OWNER", **kw):
    u = _novo_usuario(papel)
    for k, v in kw.items():
        setattr(u, k, v)
    return u


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def owner(session):
    """Usuário real no banco — o responsável precisa existir para ser atribuível."""
    u = session.exec(select(Usuario).where(Usuario.email == "crm-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="crm-owner@anara.test", nome="Dona do CRM", senha_hash="h",
                    papel="OWNER", can_approve_quotes=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


def novo_cliente(session, ator_u, **kw):
    dados = dict(nome=f"Hotel {next(_SEQ)}", cidade_uf="São Paulo", finalidade="REVENDA")
    dados.update(kw)
    c = crm.criar_cliente(session, ator=ator_u, **dados)
    session.commit()
    return c


def nova_op(session, ator_u, cliente, **kw):
    dados = dict(cliente_id=cliente.id, titulo=f"Enxoval {next(_SEQ)}")
    dados.update(kw)
    op = crm.criar_oportunidade(session, ator=ator_u, **dados)
    session.commit()
    return op


def novo_produto(session, fornecedor, custo=100.0):
    sku = f"CRM-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia="Flat Sheet",
                cost_method=CostMethod.national_supplier.value, margem_padrao_pct=0.14)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_da_op(session, op, cliente, **kw):
    dados = dict(cliente_id=cliente.id, oportunidade_id=op.id, estado_origem="São Paulo",
                 uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=True,
                 finalidade="REVENDA", condicao_pagamento="30", freight_type="FOB",
                 numero=f"CRM-{next(_SEQ):04d}", status=StatusCotacao.rascunho.value)
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def add_item(session, cot, produto, quantidade=5.0):
    from app.routers.cotacoes import adicionar_item
    chamar(adicionar_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
           produto_id=produto.id, quantidade=quantidade, modo="margem", valor=None,
           session=session)
    session.commit()
    return ws.itens_de(session, cot.id)[-1]


# ===========================================================================
# P0 §56 — prospect mínimo
# ===========================================================================
def test_p0_prospect_sem_cnpj_funciona(session, owner):
    """CRM aceita empresa com o que se sabe hoje. Cotação continua exigindo o resto."""
    cliente = crm.criar_cliente(session, ator=owner, nome="Pousada da Serra",
                                telefone="(48) 99999-0000", segmento="hotelaria")
    session.commit()
    assert cliente.id and cliente.cnpj_cpf is None

    op = crm.criar_oportunidade(session, ator=owner, cliente_id=cliente.id,
                                titulo="Enxoval 40 UHs")
    session.commit()
    assert op.status == StatusOportunidade.aberta.value

    # a UX consegue explicar a pendência ANTES de montar a proposta inteira
    faltando = crm.dados_fiscais_faltando(cliente)
    assert any("cidade" in f for f in faltando)
    assert any("finalidade" in f for f in faltando)


def test_cliente_sem_nome_e_recusado(session, owner):
    with pytest.raises(crm.DadoInvalido):
        crm.criar_cliente(session, ator=owner, nome="   ")


def test_cnpj_duplicado_nao_cria_segundo_cliente(session, owner):
    """§48: CNPJ exato repetido não vira segunda ficha em silêncio."""
    crm.criar_cliente(session, ator=owner, nome="Rede Alfa", cnpj_cpf="11.222.333/0001-44")
    session.commit()
    with pytest.raises(crm.DadoInvalido) as erro:
        crm.criar_cliente(session, ator=owner, nome="Rede Alfa Hotelaria",
                          cnpj_cpf="11222333000144")
    assert erro.value.status_code == 409
    assert "já cadastrado" in erro.value.detail


def test_nome_parecido_sugere_mas_nao_bloqueia(session, owner):
    """"Hotel Praia" e "Hotel Praia Ltda" podem ser dois clientes de verdade."""
    crm.criar_cliente(session, ator=owner, nome="Hotel Praia Grande")
    session.commit()
    outro = crm.criar_cliente(session, ator=owner, nome="Hotel Praia Grande Ltda")
    session.commit()
    assert outro.id
    assert crm.clientes_parecidos(session, "Hotel Praia Grande")


# ===========================================================================
# P0 §57/§58 — oportunidade ↔ cotação
# ===========================================================================
def test_p0_cotacao_nasce_ligada_a_oportunidade(session, owner, daune):
    import app.routers.crm as rc

    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    resp = chamar(rc.nova_cotacao, RequestFalsa(owner), oportunidade_id=op.id,
                  session=session)
    session.commit()

    assert resp.status_code == 303
    cot = crm.cotacoes_de(session, op.id)[0]
    assert cot.cliente_id == cliente.id
    assert cot.oportunidade_id == op.id
    assert crm.cotacao_mais_recente(session, op.id).id == cot.id


def test_p0_revisao_continua_na_mesma_oportunidade(session, owner, daune):
    """R2 é a mesma proposta — não um negócio novo no funil."""
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    add_item(session, cot, novo_produto(session, daune))
    ws.emitir(session, cot, ator=ator())
    session.commit()

    r2 = ws.criar_revisao(session, cot, ator=ator())
    r2.oportunidade_id = cot.oportunidade_id
    session.add(r2)
    session.commit()

    cotacoes = crm.cotacoes_de(session, op.id)
    assert len(cotacoes) == 2
    assert {c.revisao for c in cotacoes} == {1, 2}
    # o funil conta UM negócio, não dois
    assert len(crm.listar_oportunidades(session, cliente_id=cliente.id)) == 1
    assert crm.cotacao_mais_recente(session, op.id).revisao == 2


def test_p0_cross_client_e_bloqueado(session, owner, daune):
    """Oportunidade do Hotel A não recebe a cotação do Hotel B — recusa do servidor."""
    a = novo_cliente(session, owner, nome="Hotel A")
    b = novo_cliente(session, owner, nome="Hotel B")
    op_a = nova_op(session, owner, a)
    cot_b = cotacao_da_op(session, nova_op(session, owner, b), b)
    cot_b.oportunidade_id = None
    session.add(cot_b)
    session.commit()

    with pytest.raises(crm.DadoInvalido) as erro:
        crm.vincular_cotacao(session, op_a, cot_b, ator=owner)
    assert erro.value.status_code == 409
    assert "outro cliente" in erro.value.detail

    # e pelo endpoint também
    import app.routers.crm as rc
    with pytest.raises(crm.DadoInvalido):
        chamar(rc.vincular, RequestFalsa(owner), oportunidade_id=op_a.id,
               cotacao_id=cot_b.id, session=session)


def test_cotacao_ja_de_outra_oportunidade_nao_e_roubada(session, owner):
    a = novo_cliente(session, owner)
    op1 = nova_op(session, owner, a)
    op2 = nova_op(session, owner, a)
    cot = cotacao_da_op(session, op1, a)
    with pytest.raises(crm.DadoInvalido):
        crm.vincular_cotacao(session, op2, cot, ator=owner)


# ===========================================================================
# P0 §59 — histórico de etapas
# ===========================================================================
def test_p0_historico_de_etapas_append_only(session, owner):
    """Pipeline não é máquina de estados: avança, pula e volta — e tudo fica registrado."""
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente, etapa="RASCUNHO")

    for etapa in ("ENVIADO", "NEGOCIACAO"):
        crm.mudar_etapa(session, op, etapa, ator=owner)
    session.commit()
    assert op.etapa == "NEGOCIACAO"

    # voltar é legítimo
    crm.mudar_etapa(session, op, "ENVIADO", ator=owner, observacao="cliente esfriou")
    session.commit()
    assert op.etapa == "ENVIADO"

    historico = crm.historico_de_etapas(session, op.id)
    caminho = [(h.etapa_anterior, h.etapa_nova) for h in historico]
    assert caminho == [(None, "RASCUNHO"), ("RASCUNHO", "ENVIADO"),
                       ("ENVIADO", "NEGOCIACAO"), ("NEGOCIACAO", "ENVIADO")]
    assert all(h.ocorrido_em and h.ator_email == owner.email for h in historico)
    assert historico[-1].observacao == "cliente esfriou"


def test_etapa_invalida_e_recusada(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    with pytest.raises(crm.DadoInvalido):
        crm.mudar_etapa(session, op, "ETAPA_INVENTADA", ator=owner)


def test_pular_etapa_e_permitido(session, owner):
    """Qualificação → negociação acontece no mesmo telefonema. Não travar isso."""
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente, etapa="RASCUNHO")
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)
    session.commit()
    assert op.etapa == "NEGOCIACAO"


# ===========================================================================
# P0 §60/§61 — ganha
# ===========================================================================
def _proposta_emitivel(session, owner, daune, **kw):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    item = add_item(session, cot, novo_produto(session, daune))
    for campo, valor in kw.items():
        setattr(item, campo, valor)
    if kw:
        session.add(item)
        session.commit()
    ws.emitir(session, cot, ator=ator())
    session.commit()
    return op, cot, item


def test_p0_marcar_ganha(session, owner, daune):
    op, cot, item = _proposta_emitivel(session, owner, daune)
    congelado = (item.preco_negociado, item.faturamento, item.custo_unitario)
    total = D(item.faturamento)

    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()

    assert op.status == StatusOportunidade.ganha.value
    assert op.cotacao_vencedora_id == cot.id
    assert op.cotacao_vencedora_fingerprint == cot.fingerprint
    assert D(op.valor_fechado) == total
    assert op.won_em and op.won_por == owner.email
    # a cotação e a economia não mudam
    session.refresh(item)
    assert (item.preco_negociado, item.faturamento, item.custo_unitario) == congelado
    assert cot.status == StatusCotacao.emitida.value


def test_valor_fechado_e_snapshot(session, owner, daune):
    """§21: se a tabela de preços mudar amanhã, o valor fechado não muda."""
    from app import admin_service as adm

    op, cot, item = _proposta_emitivel(session, owner, daune)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    fechado = op.valor_fechado

    produto = session.get(Produto, item.produto_id)
    prop = adm.preview_custo_sku(session, produto.id, cnet_brl="900.00",
                                 status="CONFIRMADO", fonte="reajuste violento")
    adm.aplicar_custo_sku(session, produto.id, prop, ator=ator(), cnet_brl="900.00",
                          status="CONFIRMADO", fonte="reajuste violento")
    session.commit()
    session.refresh(op)
    assert op.valor_fechado == fechado


def test_p0_ganha_bloqueada_por_estimado_e_liberada_apos_confirmacao(session, owner, daune):
    """A Sessão 6 é consultada de verdade: ESTIMADO não vira compromisso firme."""
    from app import admin_service as adm

    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    produto = novo_produto(session, daune)
    prop = adm.preview_custo_sku(session, produto.id, cnet_brl="100.00", status="ESTIMADO",
                                 fonte="curva de análogos")
    adm.aplicar_custo_sku(session, produto.id, prop, ator=ator(), cnet_brl="100.00",
                          status="ESTIMADO", fonte="curva de análogos")
    session.commit()

    cot = cotacao_da_op(session, op, cliente)
    add_item(session, cot, produto)
    ws.emitir(session, cot, ator=ator())
    session.commit()

    with pytest.raises(crm.DadoInvalido) as erro:
        crm.marcar_ganha(session, op, cot.id, ator=owner)
    assert erro.value.status_code == 409
    assert "compromisso firme" in erro.value.detail
    assert op.status == StatusOportunidade.aberta.value

    # reconfirmando o custo, o negócio pode fechar
    prop2 = adm.preview_custo_sku(session, produto.id, cnet_brl="100.00",
                                  status="CONFIRMADO", fonte="fornecedor confirmou")
    adm.aplicar_custo_sku(session, produto.id, prop2, ator=ator(), cnet_brl="100.00",
                          status="CONFIRMADO", fonte="fornecedor confirmou")
    session.commit()

    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    assert op.status == StatusOportunidade.ganha.value


def test_ganha_exige_cotacao_emitida(session, owner, daune):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    add_item(session, cot, novo_produto(session, daune))
    session.commit()
    with pytest.raises(crm.DadoInvalido) as erro:
        crm.marcar_ganha(session, op, cot.id, ator=owner)
    assert "emitida ou enviada" in erro.value.detail


def test_ganha_com_cotacao_de_outra_oportunidade_e_recusada(session, owner, daune):
    op_a, cot_a, _ = _proposta_emitivel(session, owner, daune)
    op_b, _cot_b, _ = _proposta_emitivel(session, owner, daune)
    with pytest.raises(crm.DadoInvalido):
        crm.marcar_ganha(session, op_b, cot_a.id, ator=owner)


def test_ganha_e_idempotente(session, owner, daune):
    op, cot, _ = _proposta_emitivel(session, owner, daune)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    primeiro = (op.won_em, op.valor_fechado)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    assert (op.won_em, op.valor_fechado) == primeiro


# ===========================================================================
# P0 §62/§63 — perdida e reabertura
# ===========================================================================
def test_p0_perdida_exige_motivo_estruturado(session, owner, daune):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    session.commit()

    with pytest.raises(crm.DadoInvalido):
        crm.marcar_perdida(session, op, ator=owner, motivo="")
    with pytest.raises(crm.DadoInvalido):
        crm.marcar_perdida(session, op, ator=owner, motivo="porque sim")

    crm.marcar_perdida(session, op, ator=owner, motivo="PRECO",
                       comentario="concorrente 8% abaixo")
    session.commit()
    assert op.status == StatusOportunidade.perdida.value
    assert op.motivo_perda == "PRECO" and op.comentario_perda
    assert op.lost_em and op.lost_por == owner.email
    session.refresh(cot)
    assert cot.status == StatusCotacao.rascunho.value      # cotação intacta


def test_p0_reabertura_preserva_o_evento_de_perda(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente, etapa="NEGOCIACAO")
    crm.marcar_perdida(session, op, ator=owner, motivo="SEM_RETORNO")
    session.commit()
    perda = (op.lost_em, op.lost_por, op.motivo_perda)

    crm.reabrir(session, op, ator=owner, etapa="ENVIADO", motivo="cliente voltou")
    session.commit()

    assert op.status == StatusOportunidade.aberta.value
    assert op.etapa == "ENVIADO"
    assert (op.lost_em, op.lost_por, op.motivo_perda) == perda   # a perda não é apagada
    historico = crm.historico_de_etapas(session, op.id)
    assert "reabertura" in (historico[-1].observacao or "")


def test_ganha_nao_se_reabre_casualmente(session, owner, daune):
    """§25: corrigir fechamento é ação administrativa, não edição de passagem."""
    op, cot, _ = _proposta_emitivel(session, owner, daune)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    with pytest.raises(crm.DadoInvalido) as erro:
        crm.reabrir(session, op, ator=owner)
    assert erro.value.status_code == 409


def test_oportunidade_fechada_nao_anda_no_funil(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    crm.marcar_perdida(session, op, ator=owner, motivo="PRAZO")
    session.commit()
    with pytest.raises(crm.DadoInvalido):
        crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)


# ===========================================================================
# P0 §64/§65 — atividades
# ===========================================================================
def test_p0_proxima_atividade_segue_a_data(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)

    amanha = crm.criar_atividade(session, ator=owner, titulo="Ligar amanhã",
                                 oportunidade_id=op.id, due_em=AMANHA)
    session.commit()
    assert crm.proxima_atividade(session, op.id).id == amanha.id

    hoje = crm.criar_atividade(session, ator=owner, titulo="Enviar proposta hoje",
                               oportunidade_id=op.id, due_em=AGORA + timedelta(minutes=5))
    session.commit()
    assert crm.proxima_atividade(session, op.id).id == hoje.id

    crm.concluir_atividade(session, hoje, ator=owner)
    session.commit()
    assert crm.proxima_atividade(session, op.id).id == amanha.id
    # nada foi apagado
    assert len(crm.atividades_de(session, oportunidade_id=op.id)) == 2
    assert hoje.concluida_em and hoje.concluida_por == owner.email


def test_p0_atividade_atrasada_e_derivada(session, owner):
    """Sem coluna e sem job: `due_em < agora` e não concluída."""
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    atrasada = crm.criar_atividade(session, ator=owner, titulo="Follow-up de ontem",
                                   oportunidade_id=op.id, due_em=ONTEM)
    session.commit()
    assert crm.esta_atrasada(atrasada) is True

    crm.concluir_atividade(session, atrasada, ator=owner)
    session.commit()
    assert crm.esta_atrasada(atrasada) is False
    # e o modelo não tem campo persistido de atraso
    assert not any("atras" in c for c in AtividadeComercial.model_fields)


def test_concluir_e_idempotente(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    a = crm.criar_atividade(session, ator=owner, titulo="X", oportunidade_id=op.id,
                            due_em=AMANHA)
    session.commit()
    crm.concluir_atividade(session, a, ator=owner)
    session.commit()
    primeiro = a.concluida_em
    crm.concluir_atividade(session, a, ator=owner)
    session.commit()
    assert a.concluida_em == primeiro


def test_oportunidade_sem_atividade_e_identificada(session, owner):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    session.commit()
    assert crm.proxima_atividade(session, op.id) is None
    sem = crm.listar_oportunidades(session, cliente_id=cliente.id,
                                   sem_proxima_atividade=True)
    assert op.id in {o.id for o in sem}


# ===========================================================================
# P0 §67 — responsável não é ACL
# ===========================================================================
def test_p0_responsavel_organiza_mas_nao_esconde(session, owner):
    """B enxerga a oportunidade de A; o filtro "minhas" é conveniência, não segurança."""
    a = Usuario(email="vendedor-a@anara.test", nome="A", senha_hash="h",
                papel="VENDEDOR_INTERNO")
    b = Usuario(email="vendedor-b@anara.test", nome="B", senha_hash="h",
                papel="VENDEDOR_INTERNO")
    session.add_all([a, b])
    session.commit()
    session.refresh(a)
    session.refresh(b)

    cliente = novo_cliente(session, owner)
    op_a = nova_op(session, owner, cliente, responsavel_id=a.id)
    session.commit()

    # B vê no pipeline compartilhado
    todas = crm.listar_oportunidades(session, cliente_id=cliente.id)
    assert op_a.id in {o.id for o in todas}
    # mas "minhas" de B não mostra
    de_b = crm.listar_oportunidades(session, responsavel_id=b.id)
    assert op_a.id not in {o.id for o in de_b}
    de_a = crm.listar_oportunidades(session, responsavel_id=a.id)
    assert op_a.id in {o.id for o in de_a}


def test_nao_atribui_a_usuario_inativo(session, owner):
    inativo = Usuario(email="saiu@anara.test", nome="Saiu", senha_hash="h",
                      papel="VENDEDOR_INTERNO", ativo=False)
    session.add(inativo)
    session.commit()
    session.refresh(inativo)
    cliente = novo_cliente(session, owner)
    with pytest.raises(crm.DadoInvalido) as erro:
        crm.criar_oportunidade(session, ator=owner, cliente_id=cliente.id,
                               titulo="X", responsavel_id=inativo.id)
    assert "inativo" in erro.value.detail


def test_responsavel_desativado_preserva_historico(session, owner):
    """A oportunidade antiga continua; o que muda é poder reatribuir."""
    u = Usuario(email="vai-sair@anara.test", nome="Vai sair", senha_hash="h",
                papel="VENDEDOR_INTERNO")
    session.add(u)
    session.commit()
    session.refresh(u)
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente, responsavel_id=u.id)
    u.ativo = False
    session.add(u)
    session.commit()

    session.refresh(op)
    assert op.responsavel_id == u.id            # histórico intacto
    crm.atribuir(session, op, owner.id, ator=owner)
    session.commit()
    assert op.responsavel_id == owner.id


# ===========================================================================
# Valores: estimado × cotado × fechado
# ===========================================================================
def test_tres_valores_nao_se_confundem(session, owner, daune):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente, valor_estimado="50000")
    session.commit()

    assert D(op.valor_estimado) == D("50000")
    assert crm.valor_cotado(session, op.id) is None      # ainda não há cotação
    assert op.valor_fechado is None

    cot = cotacao_da_op(session, op, cliente)
    item = add_item(session, cot, novo_produto(session, daune))
    session.commit()
    cotado = crm.valor_cotado(session, op.id)
    assert cotado is not None and D(cotado) == D(item.faturamento)
    assert D(op.valor_estimado) == D("50000")           # o estimado não é sobrescrito

    ws.emitir(session, cot, ator=ator())
    session.commit()
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    assert D(op.valor_fechado) == D(cotado)


def test_valor_cotado_nao_e_persistido(session):
    """§51: métrica derivável não vira coluna — ela envelheceria em silêncio."""
    campos = set(Oportunidade.model_fields)
    assert "valor_cotado" not in campos
    assert "numero_de_cotacoes" not in campos
    assert "dias_na_etapa" not in campos


def test_cotacao_mais_recente_ignora_cancelada(session, owner, daune):
    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    c1 = cotacao_da_op(session, op, cliente)
    add_item(session, c1, novo_produto(session, daune))
    ws.emitir(session, c1, ator=ator())
    session.commit()
    r2 = ws.criar_revisao(session, c1, ator=ator())
    r2.oportunidade_id = op.id
    session.add(r2)
    session.commit()
    assert crm.cotacao_mais_recente(session, op.id).id == r2.id

    ws.cancelar(session, r2, ator=ator(), motivo="cliente desistiu da revisão")
    session.commit()
    assert crm.cotacao_mais_recente(session, op.id).id == c1.id


# ===========================================================================
# P0 §66 — confidencialidade do vendedor
# ===========================================================================
PROIBIDOS = ("custo", "custo_unitario", "cnet", "lucro", "margem", "margem_liquida",
             "markup", "comissao", "exw", "memoria")


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_cartao_do_pipeline_nao_leva_economia(session, owner, daune, papel):
    from app.confidencial import encontrar_confidenciais

    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    add_item(session, cot, novo_produto(session, daune))
    session.commit()

    cartao = crm.cartao(session, op)
    assert encontrar_confidenciais(cartao) == []
    for chave in PROIBIDOS:
        assert chave not in cartao
    # o valor comercial, esse pode
    assert "valor_cotado" in cartao and cartao["valor_cotado"]


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_telas_do_crm_nao_vazam_economia(session, owner, daune, papel):
    import app.routers.crm as rc

    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    cot = cotacao_da_op(session, op, cliente)
    item = add_item(session, cot, novo_produto(session, daune, custo=377.11))
    session.commit()

    vendedor = _novo_usuario(papel)
    paginas = {
        "home": html(chamar(rc.home, RequestFalsa(vendedor), session=session)),
        "pipeline": html(chamar(rc.pipeline, RequestFalsa(vendedor), session=session)),
        "lista": html(chamar(rc.listar, RequestFalsa(vendedor), session=session)),
        "oportunidade": html(chamar(rc.detalhe, RequestFalsa(vendedor),
                                    oportunidade_id=op.id, session=session)),
    }
    for nome, pagina in paginas.items():
        for termo in ("377,11", "377.11", "Custo NET", "Margem líquida", "CNET",
                      "Lucro potencial", "data-lucro", "data-custo"):
            assert termo not in pagina, f"{nome} vazou '{termo}' para {papel}"
    # e a página da oportunidade continua servindo para vender
    assert op.titulo in paginas["oportunidade"]
    assert cliente.nome in paginas["oportunidade"]


def test_payload_das_rotas_novas_nao_leva_economia(session, owner, daune):
    from app.confidencial import encontrar_confidenciais
    import app.routers.crm as rc

    cliente = novo_cliente(session, owner)
    op = nova_op(session, owner, cliente)
    session.commit()
    vendedor = _novo_usuario("VENDEDOR_INTERNO")
    dados = corpo(chamar(rc.mover, RequestFalsa(vendedor), oportunidade_id=op.id,
                         etapa="ENVIADO", observacao="", session=session))
    assert encontrar_confidenciais(dados) == []


def test_anonimo_nao_entra_no_crm(session, owner):
    from app.permissoes import PrecisaLogin
    import app.routers.crm as rc

    for rota in (rc.home, rc.pipeline, rc.listar):
        with pytest.raises(PrecisaLogin):
            chamar(rota, RequestFalsa(None), session=session)


# ===========================================================================
# P0 §68 — legado
# ===========================================================================
@legado.sem_baseline
def test_p0_historico_legado_nao_ganha_oportunidade_ficticia(session):
    """§16 e §68: nenhuma das cotações herdadas passou pelo funil. Não inventar que passou.

    O que este teste **deixou de afirmar**, de propósito: que o CRM está vazio. Ele estava,
    quando o sistema não era usado, e afirmar isso era barato. Agora que o Matias usa o
    sistema, `Oportunidade`, `Contato` e `AtividadeComercial` legitimamente têm linhas — e
    exigir tabela vazia transformaria uso normal em suíte vermelha.

    O que continua sendo afirmado é a única coisa que o legado promete: **nenhuma cotação
    herdada aponta para oportunidade nenhuma**. Como toda `AtividadeComercial` e todo
    `OportunidadeEtapaHistorico` pendem de uma oportunidade, e nenhuma oportunidade alcança
    uma cotação herdada, o funil continua provadamente sem retroencaixe.
    """
    from sqlalchemy import create_engine
    from sqlmodel import Session as S
    import os

    eng = create_engine(f"sqlite:///file:{os.path.abspath('data/anara.db')}?mode=ro&uri=true")
    with S(eng) as prod:
        cotacoes = legado.somente_cotacoes(prod.exec(select(Cotacao)).all())
        assert len(cotacoes) == len(legado.COTACOES), "cotação herdada sumiu do banco"
        assert all(c.oportunidade_id is None for c in cotacoes)
        itens = legado.somente_por_cotacao(prod.exec(select(CotacaoItem)).all())
        assert len(itens) == len(legado.ITENS), "item de cotação herdada sumiu do banco"


# ===========================================================================
# Contatos e trilha
# ===========================================================================
def test_contato_principal_e_unico_por_cliente(session, owner):
    cliente = novo_cliente(session, owner)
    a = crm.criar_contato(session, ator=owner, cliente_id=cliente.id, nome="Ana",
                          principal=True)
    session.commit()
    b = crm.criar_contato(session, ator=owner, cliente_id=cliente.id, nome="Bruno",
                          principal=True)
    session.commit()
    session.refresh(a)
    assert a.principal is False and b.principal is True
    assert crm.contato_principal(session, cliente.id).id == b.id


def test_email_de_contato_nao_e_unico_globalmente(session, owner):
    """`compras@` legitimamente se repete entre fichas."""
    c1 = novo_cliente(session, owner)
    c2 = novo_cliente(session, owner)
    crm.criar_contato(session, ator=owner, cliente_id=c1.id, nome="Compras",
                      email="compras@grupo.com")
    crm.criar_contato(session, ator=owner, cliente_id=c2.id, nome="Compras",
                      email="compras@grupo.com")
    session.commit()


def test_trilha_registra_as_acoes_do_crm(session, owner, daune):
    from app import admin_service as adm

    op, cot, _ = _proposta_emitivel(session, owner, daune)
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()

    acoes = {t.acao for t in adm.trilha(session, limite=200)}
    for esperada in ("CREATE_CLIENT", "CREATE_OPPORTUNITY", "CHANGE_STAGE", "MARK_WON"):
        assert esperada in acoes


def test_timeline_compoe_as_fontes_existentes(session, owner, daune):
    op, cot, _ = _proposta_emitivel(session, owner, daune)
    crm.criar_atividade(session, ator=owner, titulo="Apresentar proposta",
                        oportunidade_id=op.id, due_em=AMANHA)
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()

    linha = crm.timeline(session, op)
    tipos = {e["tipo"] for e in linha}
    assert {"criacao", "atividade", "cotacao", "ganho"} <= tipos
    quando = [e["quando"] for e in linha]
    assert quando == sorted(quando)

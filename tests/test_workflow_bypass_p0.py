"""C-NEW-09 — a situação da cotação deixou de ser um campo que se escolhe.

## O defeito

`POST /cotacoes/{id}/status` validava a **transição** e nada mais. Como `TRANSICOES` permite
`rascunho → aguardando_aprovacao → aprovada → emitida → enviada`, um vendedor comissionado
percorria a cadeia inteira por essa rota. Medido em banco temporário, com um item de R$ 0,00
dentro:

    → aguardando_aprovacao   ATINGIU
    → aprovada               ATINGIU
    → emitida                ATINGIU
    → enviada                ATINGIU

    AprovacaoCotacao: 0 registros
    SnapshotEmissao : 0 registros
    PDF sairia FINAL, sem marca d'água, com o item de R$ 0,00

O registro original do C-NEW-09 dizia "alcança `aprovada`". Alcançava `enviada` — o estado
comercial terminal. E como `emitida` chegava sem passar por `ws.emitir()`, **o documento
congelado nunca era criado**: a cotação ficava "emitida" sem existir emissão.

E não era regra faltando. `wf.blockers_do_item` tem `SEM_PRECO` desde a Sessão 6 e
`ws.avaliar()` devolvia `pode_emitir=False` corretamente. O que faltava era alguém perguntar.

## Por que a tela levava a isso

`cotacao_detail.html` renderizava `wf.proximos_estados()` como botões que postavam o estado
desejado nessa rota genérica. As rotas canônicas existiam e **nenhuma era chamada por aquela
tela** — o único caminho que o usuário tinha era o bypass.

## O que passou a valer

O estado é consequência de uma ação, e cada ação tem dono canônico em `workflow_service`. Da
rota genérica sobrou uma transição: voltar para `rascunho`, que **retira** privilégio.
"""
import inspect
import json

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import politica_comercial as _pol
from app import workflow as wf
from app import workflow_service as ws
from app.models import (
    AprovacaoCotacao, Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto,
    SnapshotEmissao, StatusCotacao,
)
from app.routers.cotacoes import acoes_do_workflow, mudar_status
from app.routers.workflow import aprovar, emitir, enviar, solicitar
from conftest import RequestFalsa, _novo_usuario

PRIVILEGIADOS = ("aguardando_aprovacao", "aprovada", "emitida", "enviada", "cancelada")


def chamar(funcao, request, **kwargs):
    """Chama a função da rota preenchendo os `Form(...)` com o default declarado."""
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


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def cliente(session):
    c = session.exec(select(Cliente)).first()
    if c is None:
        c = Cliente(nome="Cliente bypass", estado="São Paulo")
        session.add(c)
        session.commit()
        session.refresh(c)
    return c


_SEQ = iter(range(1, 10_000))


def produto(session, fornecedor, custo=100.0, margem=0.14):
    sku = f"BYP-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia="Flat Sheet",
                cost_method=CostMethod.national_supplier.value, margem_padrao_pct=margem)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao(session, cliente, **kw):
    dados = dict(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                 estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                 condicao_pagamento="30", numero=f"BYP-{next(_SEQ):04d}",
                 status=StatusCotacao.rascunho.value, freight_type="FOB")
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def item(session, cot, prod, *, preco, qtd=10, recomendado=None, margem_real=None):
    """Item já calculado. `preco=0` reproduz o item 49 da cotação 21."""
    it = CotacaoItem(
        cotacao_id=cot.id, ordem=0, produto_id=prod.id, nome_produto=prod.nome,
        quantidade=qtd, custo_unitario=prod.custo_unitario, preco_base=prod.preco_base,
        preco_negociado=preco, preco_recomendado=recomendado if recomendado is not None else preco,
        margem_liquida=margem_real if margem_real is not None else prod.margem_padrao_pct,
        margem_padrao_pct=prod.margem_padrao_pct, faturamento=preco * qtd,
        custo_total=prod.custo_unitario * qtd, lucro=0.0, modo_edicao="preco",
        valor_editado=preco, status_fiscal="OK", status_pagamento="OK",
        status_custo_item="CONFIRMADO",
        # a política que um item Daune de hoje congela (21/09/2026): B2B como piso, 5%,
        # sem preço travado; a tabela é 2 × recomendado
        politica_comercial=_pol.ROTULO_2026_09_21, piso_margem_pct=None,
        comissao_formacao_pct=0.05, preco_travado=False,
        preco_tabela=(2 * recomendado) if recomendado else None)
    session.add(it)
    session.commit()
    session.refresh(it)
    return it


def status_de(cot):
    return cot.status.value if hasattr(cot.status, "value") else str(cot.status)


# ===========================================================================
# CASO A e B — a rota genérica não concede mais nada
# ===========================================================================
@pytest.mark.parametrize("destino", PRIVILEGIADOS)
def test_caso_A_rota_generica_recusa_todo_estado_privilegiado(session, cliente, daune,
                                                              destino):
    """Nenhum estado com dono canônico é alcançável por `/status` — nem para o OWNER."""
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=200.0)

    for papel in ("VENDEDOR_COMISSIONADO", "VENDEDOR_INTERNO", "ADMIN", "OWNER"):
        resposta = chamar(mudar_status, RequestFalsa(_novo_usuario(papel)),
                          cotacao_id=cot.id, status=destino, session=session)
        assert resposta.status_code == 403, f"{papel} conseguiu {destino}"
        session.refresh(cot)
        assert status_de(cot) == StatusCotacao.rascunho.value


def test_caso_B_a_cadeia_inteira_do_bypass_esta_fechada(session, cliente, daune):
    """A sequência exata que o vendedor percorreu em 09/09/2026, passo a passo."""
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=0.0, recomendado=200.0)

    for destino in ("aguardando_aprovacao", "aprovada", "emitida", "enviada"):
        resposta = chamar(mudar_status, RequestFalsa(vend), cotacao_id=cot.id,
                          status=destino, session=session)
        assert resposta.status_code == 403, f"chegou a {destino}"
        session.refresh(cot)
        assert status_de(cot) == StatusCotacao.rascunho.value, f"mudou ao tentar {destino}"

    assert session.exec(select(AprovacaoCotacao)
                        .where(AprovacaoCotacao.cotacao_id == cot.id)).all() == []
    assert session.exec(select(SnapshotEmissao)
                        .where(SnapshotEmissao.cotacao_id == cot.id)).all() == []


def test_a_recusa_diz_qual_e_a_acao_certa(session, cliente, daune):
    """Recusa que só nega ensina o usuário a procurar outra porta lateral."""
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=200.0)
    resposta = chamar(mudar_status, RequestFalsa(_novo_usuario("OWNER")),
                      cotacao_id=cot.id, status="emitida", session=session)
    corpo = bytes(resposta.body).decode()
    assert "Emitir" in corpo
    assert "snapshot" in corpo.lower()


def test_reabrir_para_rascunho_continua_permitido(session, cliente, daune):
    """A única transição que sobrou: ela RETIRA privilégio, então não precisa de alçada."""
    cot = cotacao(session, cliente, status=StatusCotacao.aguardando_aprovacao.value)
    item(session, cot, produto(session, daune), preco=200.0)

    resposta = chamar(mudar_status, RequestFalsa(_novo_usuario("VENDEDOR_COMISSIONADO")),
                      cotacao_id=cot.id, status="rascunho", session=session)
    assert resposta.status_code == 303
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.rascunho.value


def test_estado_legado_continua_intocavel(session, cliente, daune):
    """`pedido`, `fechada` e `perdida` vieram do sistema anterior e ficam onde estão."""
    cot = cotacao(session, cliente, status="pedido")
    resposta = chamar(mudar_status, RequestFalsa(_novo_usuario("OWNER")),
                      cotacao_id=cot.id, status="rascunho", session=session)
    assert resposta.status_code != 303
    session.refresh(cot)
    assert status_de(cot) == "pedido"


# ===========================================================================
# CASO C — a aprovação oficial, com trilha
# ===========================================================================
def test_caso_C_aprovacao_oficial_grava_decisao_fingerprint_e_estado(session, cliente, daune):
    """O caminho certo: pedido, decisão com alçada, fingerprint da configuração vista."""
    vend = _novo_usuario("VENDEDOR_INTERNO")
    dono = _novo_usuario("OWNER")
    cot = cotacao(session, cliente)
    p = produto(session, daune)
    # desconto de verdade: negociado abaixo do recomendado
    item(session, cot, p, preco=150.0, recomendado=200.0, margem_real=0.05)

    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao and not prontidao.blockers

    chamar(solicitar, RequestFalsa(vend), cotacao_id=cot.id,
           justificativa="desconto negociado com o cliente", session=session)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.aguardando_aprovacao.value
    pedido = ws.pedido_pendente(session, cot.id)
    assert pedido is not None and pedido.fingerprint == prontidao.fingerprint

    chamar(aprovar, RequestFalsa(dono), cotacao_id=cot.id, pedido_id=pedido.id,
           fingerprint=pedido.fingerprint, session=session)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.aprovada.value

    decisao = session.get(AprovacaoCotacao, pedido.id)
    assert decisao.status == "APROVADA"
    assert decisao.aprovador_email == dono.email
    assert decisao.fingerprint == prontidao.fingerprint
    assert ws.avaliar(session, cot).aprovacao_valida is True


def test_vendedor_nao_aprova_a_propria_excecao(session, cliente, daune):
    """Alçada é `can_approve_quotes`, e pedir não é decidir."""
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=150.0, recomendado=200.0,
         margem_real=0.05)

    chamar(solicitar, RequestFalsa(vend), cotacao_id=cot.id,
           justificativa="desconto negociado com o cliente", session=session)
    pedido = ws.pedido_pendente(session, cot.id)

    with pytest.raises(HTTPException) as erro:
        chamar(aprovar, RequestFalsa(vend), cotacao_id=cot.id, pedido_id=pedido.id,
               fingerprint=pedido.fingerprint, session=session)
    assert erro.value.status_code == 403
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.aguardando_aprovacao.value
    assert session.get(AprovacaoCotacao, pedido.id).status not in ("APROVADA",)


def test_caso_D_alteracao_material_pos_aprovacao_continua_invalidando(session, cliente, daune):
    """A garantia da Sessão 6 não foi tocada: decisão é sobre uma CONFIGURAÇÃO."""
    cot = cotacao(session, cliente)
    p = produto(session, daune)
    it = item(session, cot, p, preco=150.0, recomendado=200.0, margem_real=0.05)

    chamar(solicitar, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
           cotacao_id=cot.id, justificativa="desconto negociado", session=session)
    pedido = ws.pedido_pendente(session, cot.id)
    chamar(aprovar, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
           pedido_id=pedido.id, fingerprint=pedido.fingerprint, session=session)
    session.refresh(cot)
    assert ws.avaliar(session, cot).aprovacao_valida is True

    it.quantidade = 999
    session.add(it)
    session.commit()

    assert ws.avaliar(session, cot).aprovacao_valida is False, \
        "a decisão era sobre outra proposta"


# ===========================================================================
# As ações que a tela oferece saem da Prontidão, não da tabela de estados
# ===========================================================================
def rotas(acoes):
    return [a["rota"].rsplit("/", 1)[-1] for a in acoes]


def test_a_tela_nao_oferece_emitir_com_blocker(session, cliente, daune):
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=0.0, recomendado=200.0)
    prontidao = ws.avaliar(session, cot)

    assert "emitir" not in rotas(acoes_do_workflow(cot, prontidao))
    assert "solicitar" not in rotas(acoes_do_workflow(cot, prontidao)), \
        "pedir aprovação não resolve falta de preço"


def test_a_tela_oferece_pedir_aprovacao_quando_ha_excecao(session, cliente, daune):
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=150.0, recomendado=200.0,
         margem_real=0.05)
    acoes = acoes_do_workflow(cot, ws.avaliar(session, cot))

    assert "solicitar" in rotas(acoes)
    assert "emitir" not in rotas(acoes)


def test_a_tela_oferece_emitir_quando_esta_pronta(session, cliente, daune):
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=250.0, recomendado=200.0)
    acoes = acoes_do_workflow(cot, ws.avaliar(session, cot))

    assert "emitir" in rotas(acoes)
    assert "solicitar" not in rotas(acoes), "não há exceção: nada a aprovar"


def test_nenhuma_acao_da_tela_aponta_para_a_rota_generica_privilegiada(session, cliente, daune):
    """§16 — nenhum botão ativo pode continuar chamando o bypass."""
    for preco, recomendado in ((0.0, 200.0), (150.0, 200.0), (250.0, 200.0)):
        cot = cotacao(session, cliente)
        item(session, cot, produto(session, daune), preco=preco, recomendado=recomendado)
        for acao in acoes_do_workflow(cot, ws.avaliar(session, cot)):
            if acao["rota"].endswith("/status"):
                assert acao.get("estado") == wf.DRAFT, \
                    f"botão posta estado privilegiado: {acao}"


def test_cotacao_legada_nao_oferece_acao_nenhuma(session, cliente):
    cot = cotacao(session, cliente, status="perdida")
    assert acoes_do_workflow(cot, ws.avaliar(session, cot)) == []


# ===========================================================================
# §13 — cotação SEM exceção não exige autoaprovação
# ===========================================================================
def test_cotacao_sem_excecao_emite_sem_criar_aprovacao(session, cliente, daune):
    """Aprovação é para EXCEÇÃO. Sem exceção, ninguém aprova a própria cotação."""
    vend = _novo_usuario("VENDEDOR_INTERNO")
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=250.0, recomendado=200.0)

    prontidao = ws.avaliar(session, cot)
    assert prontidao.blockers == []
    assert prontidao.precisa_aprovacao is False
    assert prontidao.pode_emitir is True

    chamar(emitir, RequestFalsa(vend), cotacao_id=cot.id, session=session)
    session.refresh(cot)

    assert status_de(cot) == StatusCotacao.emitida.value
    assert session.exec(select(AprovacaoCotacao)
                        .where(AprovacaoCotacao.cotacao_id == cot.id)).all() == [], \
        "não se registra aprovação de quem não tinha o que aprovar"
    snapshot = session.exec(select(SnapshotEmissao)
                            .where(SnapshotEmissao.cotacao_id == cot.id)).first()
    assert snapshot is not None and snapshot.fingerprint == prontidao.fingerprint
    assert snapshot.aprovacao_id is None
    assert snapshot.emitido_por == vend.email


def test_emitida_avanca_para_enviada_pela_rota_canonica(session, cliente, daune):
    vend = _novo_usuario("VENDEDOR_INTERNO")
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=250.0, recomendado=200.0)
    chamar(emitir, RequestFalsa(vend), cotacao_id=cot.id, session=session)

    chamar(enviar, RequestFalsa(vend), cotacao_id=cot.id, session=session)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.enviada.value


def test_emissao_com_excecao_pendente_e_recusada(session, cliente, daune):
    """Emitir não é atalho para aprovação: exceção sem decisão barra a emissão."""
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=150.0, recomendado=200.0,
         margem_real=0.05)

    with pytest.raises(ws.OperacaoInvalida):
        chamar(emitir, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
               session=session)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.rascunho.value


def test_owner_com_excecao_precisa_da_trilha_igual(session, cliente, daune):
    """§6 — nem OWNER obtém `aprovada` pulando fingerprint e registro."""
    dono = _novo_usuario("OWNER")
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=150.0, recomendado=200.0,
         margem_real=0.05)

    # pelo atalho: recusado
    assert chamar(mudar_status, RequestFalsa(dono), cotacao_id=cot.id,
                  status="aprovada", session=session).status_code == 403

    # pela trilha: funciona, e deixa registro
    chamar(solicitar, RequestFalsa(dono), cotacao_id=cot.id,
           justificativa="desconto autorizado pela diretoria", session=session)
    pedido = ws.pedido_pendente(session, cot.id)
    chamar(aprovar, RequestFalsa(dono), cotacao_id=cot.id, pedido_id=pedido.id,
           fingerprint=pedido.fingerprint, session=session)
    session.refresh(cot)

    assert status_de(cot) == StatusCotacao.aprovada.value
    assert session.get(AprovacaoCotacao, pedido.id).aprovador_email == dono.email
    chamar(emitir, RequestFalsa(dono), cotacao_id=cot.id, session=session)
    session.refresh(cot)
    snapshot = session.exec(select(SnapshotEmissao)
                            .where(SnapshotEmissao.cotacao_id == cot.id)).first()
    assert snapshot.aprovacao_id == pedido.id, "o snapshot pina a decisão exata"


# ===========================================================================
# A resposta acompanha quem chamou
# ===========================================================================
def test_clique_volta_para_a_cotacao_e_fetch_recebe_json(session, cliente, daune):
    """Um clique não pode terminar num objeto JSON na barra de endereços."""
    cot = cotacao(session, cliente)
    item(session, cot, produto(session, daune), preco=250.0, recomendado=200.0)

    resposta = chamar(emitir, RequestFalsa(_novo_usuario("OWNER"), accept="text/html"),
                      cotacao_id=cot.id, session=session)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/cotacoes/{cot.id}"

    outra = cotacao(session, cliente)
    item(session, outra, produto(session, daune), preco=250.0, recomendado=200.0)
    resposta = chamar(emitir, RequestFalsa(_novo_usuario("OWNER"), accept="application/json"),
                      cotacao_id=outra.id, session=session)
    assert resposta.status_code == 200
    assert json.loads(bytes(resposta.body).decode())["snapshot_id"]

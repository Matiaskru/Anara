"""C-NEW-10 — item sem preço não é exceção comercial: é item incompleto.

## O caso real

Item 49 da `ANARA-2026-0021`, criado na tela em 09/09/2026:

    Lençol plano 240x250 · 300 fios      qtd 10
    custo_unitario     R$  71,52…        status_custo_item CONFIRMADO
    preco_recomendado  R$ 153,44         status_fiscal     OK
    preco_negociado    R$   0,00         modo_edicao       preco

Custo e fiscal resolvidos — não era bloqueio de dado econômico. O preço nunca foi
digitado. A cotação foi para `aprovada` e teve PDF gerado seis vezes com ele assim.

## A regra, que já existia

`wf.blockers_do_item` tem `SEM_PRECO` desde a Sessão 6, e `ws.avaliar()` devolvia
`pode_emitir=False` corretamente. **Não faltava regra: faltava alguém perguntar.** O caminho
que o usuário percorreu — a rota genérica de status — nunca chamou `ws.avaliar()`.

Fechado o C-NEW-09, a pergunta passa a ser feita. O que estes testes cobram é a fronteira:

* **rascunho aceita** o item sem preço, porque é assim que se monta uma cotação;
* nada além de rascunho aceita: aprovar não cria o número que falta, emitir recusa, e o PDF
  final não sai;
* preço **válido abaixo do recomendado** não se confunde com preço zero — aquilo é desconto,
  segue o fluxo de exceção e aprovação;
* corrigido o preço, o impedimento some sozinho.

## PDF final

Sair sem marca d'água é afirmar que existe documento emitido — e documento emitido é o
`SnapshotEmissao`. O PDF final passou a exigir esse registro em vez de confiar no campo
`status`: defesa em profundidade contra o próximo atalho.
"""
import inspect

import pytest
from sqlmodel import select

from app import workflow as wf
from app import workflow_service as ws
from app.models import (
    AprovacaoCotacao, Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto,
    SnapshotEmissao, StatusCotacao,
)
from app.routers.cotacoes import acoes_do_workflow, gerar_pdf
from app.routers.workflow import aprovar, emitir, solicitar
from conftest import RequestFalsa, _novo_usuario


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


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def cliente(session):
    c = session.exec(select(Cliente)).first()
    if c is None:
        c = Cliente(nome="Cliente emissão", estado="São Paulo")
        session.add(c)
        session.commit()
        session.refresh(c)
    return c


_SEQ = iter(range(1, 10_000))


def cenario(session, cliente, daune, *, preco, recomendado=200.0, margem_real=None, qtd=10):
    """Cotação em rascunho com um item já calculado. `preco=0` é o item 49."""
    n = next(_SEQ)
    p = Produto(sku_key=f"EMI-{n}", nome=f"Produto EMI-{n}", custo_unitario=100.0,
                preco_base=200.0, fornecedor_id=daune.id, familia="Flat Sheet",
                cost_method=CostMethod.national_supplier.value, margem_padrao_pct=0.14)
    session.add(p)
    session.commit()
    session.refresh(p)

    cot = Cotacao(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                  estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                  condicao_pagamento="30", numero=f"EMI-{n:04d}",
                  status=StatusCotacao.rascunho.value, freight_type="FOB")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    it = CotacaoItem(
        cotacao_id=cot.id, ordem=0, produto_id=p.id, nome_produto=p.nome, quantidade=qtd,
        custo_unitario=100.0, preco_base=200.0, preco_negociado=preco,
        preco_recomendado=recomendado,
        margem_liquida=margem_real if margem_real is not None else 0.14,
        margem_padrao_pct=0.14, faturamento=preco * qtd, custo_total=100.0 * qtd,
        lucro=0.0, modo_edicao="preco", valor_editado=preco, status_fiscal="OK",
        status_pagamento="OK", status_custo_item="CONFIRMADO")
    session.add(it)
    session.commit()
    session.refresh(it)
    return cot, it


def status_de(cot):
    return cot.status.value if hasattr(cot.status, "value") else str(cot.status)


def codigos(prontidao):
    return [b.codigo for b in prontidao.blockers]


# ===========================================================================
# A — rascunho aceita o item incompleto
# ===========================================================================
def test_A_rascunho_aceita_item_sem_preco(session, cliente, daune):
    """É assim que se monta uma cotação: adiciona o produto, precifica depois."""
    cot, it = cenario(session, cliente, daune, preco=0.0)

    assert status_de(cot) == StatusCotacao.rascunho.value
    assert it.id is not None and it.preco_negociado == 0.0
    assert session.get(CotacaoItem, it.id) is not None, "salvou e continua lá"


def test_A_o_impedimento_e_detectado_e_nomeado(session, cliente, daune):
    cot, _ = cenario(session, cliente, daune, preco=0.0)
    prontidao = ws.avaliar(session, cot)

    assert "SEM_PRECO" in codigos(prontidao)
    assert prontidao.pode_emitir is False
    assert any("preço comercial" in b.detalhe for b in prontidao.blockers)


# ===========================================================================
# B, C, D — nada além de rascunho aceita
# ===========================================================================
def test_B_emissao_recusada_com_item_sem_preco(session, cliente, daune):
    cot, _ = cenario(session, cliente, daune, preco=0.0)

    with pytest.raises(ws.OperacaoInvalida) as erro:
        chamar(emitir, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
               session=session)
    assert "não está pronta" in str(erro.value.detail)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.rascunho.value
    assert session.exec(select(SnapshotEmissao)
                        .where(SnapshotEmissao.cotacao_id == cot.id)).all() == []


def test_C_aprovacao_nao_transforma_item_invalido_em_valido(session, cliente, daune):
    """Blocker duro não é exceção comercial. Nenhuma alçada cria o número que falta."""
    dono = _novo_usuario("OWNER")
    cot, _ = cenario(session, cliente, daune, preco=0.0)

    # o pedido de aprovação nem é oferecido pela tela...
    acoes = [a["rota"].rsplit("/", 1)[-1] for a in acoes_do_workflow(cot, ws.avaliar(session, cot))]
    assert "solicitar" not in acoes and "emitir" not in acoes

    # ...e forçar o pedido pela rota não adianta: item sem preço não é exceção comercial, e
    # `solicitar_aprovacao` recusa abrir pedido sobre o que não é decisão de alçada.
    with pytest.raises(ws.OperacaoInvalida) as erro:
        chamar(solicitar, RequestFalsa(dono), cotacao_id=cot.id,
               justificativa="tentando aprovar item incompleto", session=session)
    assert "não tem exceção comercial" in str(erro.value.detail)
    assert ws.pedido_pendente(session, cot.id) is None
    assert session.exec(select(AprovacaoCotacao)
                        .where(AprovacaoCotacao.cotacao_id == cot.id)).all() == []

    # não há caminho para `aprovada`, e o impedimento segue de pé
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.rascunho.value
    prontidao = ws.avaliar(session, cot)
    assert "SEM_PRECO" in codigos(prontidao)
    assert prontidao.pode_emitir is False, "aprovação não dispensa impedimento econômico"
    with pytest.raises(ws.OperacaoInvalida):
        chamar(emitir, RequestFalsa(dono), cotacao_id=cot.id, session=session)


def test_C_pedido_de_aprovacao_sem_excecao_e_recusado(session, cliente, daune):
    """§5, no nível do serviço: não se abre pedido sobre o que não é decisão de alçada.

    É o que impede o ritual de "aprovar a própria cotação" — a recusa vem antes de existir
    registro, então não há autoaprovação nem para inventar.
    """
    cot, _ = cenario(session, cliente, daune, preco=250.0, recomendado=200.0)
    assert ws.avaliar(session, cot).precisa_aprovacao is False

    with pytest.raises(ws.OperacaoInvalida) as erro:
        chamar(solicitar, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
               justificativa="quero aprovar assim mesmo", session=session)
    assert "Não há o que aprovar" in str(erro.value.detail)
    assert session.exec(select(AprovacaoCotacao)
                        .where(AprovacaoCotacao.cotacao_id == cot.id)).all() == []


def test_D_pdf_final_nao_sai_sem_emissao_registrada(session, cliente, daune):
    """Mesmo com o campo `status` dizendo emitida: sem snapshot, não há documento."""
    cot, _ = cenario(session, cliente, daune, preco=0.0)
    # simula o resultado do bypass fechado: o campo forçado, sem passar por `ws.emitir()`
    cot.status = StatusCotacao.emitida.value
    session.add(cot)
    session.commit()

    resposta = chamar(gerar_pdf, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
                      session=session)
    assert resposta.status_code == 409
    corpo = bytes(resposta.body).decode()
    assert "emissão registrada" in corpo or "documento congelado" in corpo


def test_D_preview_de_rascunho_continua_saindo(session, cliente, daune):
    """A prévia é ferramenta de trabalho: sai em rascunho, marcada, mesmo incompleta."""
    cot, _ = cenario(session, cliente, daune, preco=0.0)

    resposta = chamar(gerar_pdf, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
                      session=session)
    assert resposta.status_code == 200
    assert getattr(resposta, "media_type", "") == "application/pdf"


# ===========================================================================
# E — corrigido o preço, o impedimento some
# ===========================================================================
def test_E_preco_corrigido_libera_a_emissao(session, cliente, daune):
    cot, it = cenario(session, cliente, daune, preco=0.0)
    assert "SEM_PRECO" in codigos(ws.avaliar(session, cot))

    it.preco_negociado = 250.0
    it.valor_editado = 250.0
    it.faturamento = 250.0 * it.quantidade
    session.add(it)
    session.commit()

    prontidao = ws.avaliar(session, cot)
    assert prontidao.blockers == []
    assert prontidao.precisa_aprovacao is False, "250 está acima do recomendado: sem exceção"
    assert prontidao.pode_emitir is True

    chamar(emitir, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id, session=session)
    session.refresh(cot)
    assert status_de(cot) == StatusCotacao.emitida.value


def test_E_depois_de_emitir_o_pdf_final_sai(session, cliente, daune):
    cot, _ = cenario(session, cliente, daune, preco=250.0)
    chamar(emitir, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id, session=session)
    session.refresh(cot)

    resposta = chamar(gerar_pdf, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
                      session=session)
    assert resposta.status_code == 200
    assert session.exec(select(SnapshotEmissao)
                        .where(SnapshotEmissao.cotacao_id == cot.id)).first() is not None


# ===========================================================================
# F — preço válido abaixo do recomendado NÃO é preço zero
# ===========================================================================
def test_F_preco_abaixo_do_recomendado_e_excecao_e_nao_blocker(session, cliente, daune):
    """Desconto é decisão de alçada; ausência de preço é falta de informação."""
    cot, _ = cenario(session, cliente, daune, preco=150.0, recomendado=200.0,
                     margem_real=0.05)
    prontidao = ws.avaliar(session, cot)

    assert prontidao.blockers == [], "há preço: não falta informação"
    assert prontidao.precisa_aprovacao is True
    assert prontidao.pode_emitir is False, "mas só depois de aprovado"
    assert "PRECO_ABAIXO_RECOMENDADO" in [e.motivo for e in prontidao.excecoes]


def test_F_desconto_aprovado_emite_normalmente(session, cliente, daune):
    """O contraste com o item de preço zero: aqui a aprovação resolve, e resolve com trilha."""
    dono = _novo_usuario("OWNER")
    cot, _ = cenario(session, cliente, daune, preco=150.0, recomendado=200.0,
                     margem_real=0.05)

    chamar(solicitar, RequestFalsa(dono), cotacao_id=cot.id,
           justificativa="desconto fechado com o cliente", session=session)
    pedido = ws.pedido_pendente(session, cot.id)
    chamar(aprovar, RequestFalsa(dono), cotacao_id=cot.id, pedido_id=pedido.id,
           fingerprint=pedido.fingerprint, session=session)
    session.refresh(cot)

    assert ws.avaliar(session, cot).pode_emitir is True
    chamar(emitir, RequestFalsa(dono), cotacao_id=cot.id, session=session)
    session.refresh(cot)

    snapshot = session.exec(select(SnapshotEmissao)
                            .where(SnapshotEmissao.cotacao_id == cot.id)).first()
    assert snapshot is not None
    assert snapshot.aprovacao_id == pedido.id, "o snapshot pina a decisão que o liberou"

    resposta = chamar(gerar_pdf, RequestFalsa(dono), cotacao_id=cot.id, session=session)
    assert resposta.status_code == 200


def test_quantidade_zero_nao_e_confundida_com_preco_zero(session, cliente, daune):
    """Item de quantidade zero tem preço; o que ele não tem é linha. Não é este blocker."""
    cot, it = cenario(session, cliente, daune, preco=250.0, qtd=0)
    prontidao = ws.avaliar(session, cot)
    assert "SEM_PRECO" not in codigos(prontidao)


def test_a_tela_mostra_o_impedimento(session, cliente, daune):
    """O sistema sabia do R$ 0,00 e não contava a ninguém — agora o card de Situação conta."""
    from app.routers.cotacoes import detalhe

    cot, _ = cenario(session, cliente, daune, preco=0.0)
    resposta = chamar(detalhe, RequestFalsa(_novo_usuario("OWNER")), cotacao_id=cot.id,
                      session=session)
    corpo = bytes(resposta.body).decode()

    assert "Falta informação para emitir" in corpo
    assert "preço comercial" in corpo
    assert "Aprovação não resolve isto" in corpo

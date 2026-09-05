"""Workflow comercial ligado ao banco — pedir, decidir, emitir, revisar.

`app/workflow.py` diz **o que é verdade** sobre uma configuração; este módulo é quem
**grava**. A separação importa porque as regras (o que é exceção, o que é blocker, qual o
fingerprint) precisam ser testáveis sem banco, e o que grava precisa de transação.

Três invariantes que este módulo protege:

1. **A necessidade de aprovação é derivada, sempre.** O navegador nunca manda
   `approval_required=false`: o servidor recalcula a partir dos itens. Não existe caminho
   em que um campo do formulário dispense a regra.
2. **Aprovação vale para um fingerprint.** Mudou item, quantidade, preço, destino fiscal ou
   condição — a decisão anterior vira `INVALIDADA`, e não por castigo: ela era sobre uma
   configuração que não existe mais.
3. **Emitido é imutável.** A recusa é do servidor, em `exigir_editavel`, e não do botão
   escondido na tela.
"""
import json
from datetime import date, datetime
from typing import List, Optional, Sequence

from fastapi import HTTPException
from sqlmodel import Session, select

from app import admin_service as adm
from app import workflow as wf
from app.dinheiro import para_float
from app.models import (
    AprovacaoCotacao, Cliente, Cotacao, CotacaoItem, SnapshotEmissao, StatusCotacao, Usuario,
)

PENDENTE = "PENDENTE"
APROVADA = "APROVADA"
REJEITADA = "REJEITADA"
INVALIDADA = "INVALIDADA"


class OperacaoInvalida(HTTPException):
    """Erro de workflow, com mensagem para o usuário — nunca stack trace."""

    def __init__(self, detalhe: str, status: int = 409):
        super().__init__(status_code=status, detail=detalhe)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def itens_de(session: Session, cotacao_id: int) -> List[CotacaoItem]:
    return session.exec(select(CotacaoItem)
                        .where(CotacaoItem.cotacao_id == cotacao_id)
                        .order_by(CotacaoItem.ordem, CotacaoItem.id)).all()


def aprovacoes_de(session: Session, cotacao_id: int) -> List[AprovacaoCotacao]:
    return session.exec(select(AprovacaoCotacao)
                        .where(AprovacaoCotacao.cotacao_id == cotacao_id)
                        .order_by(AprovacaoCotacao.criado_em.desc(),
                                  AprovacaoCotacao.id.desc())).all()


def pedido_pendente(session: Session, cotacao_id: int) -> Optional[AprovacaoCotacao]:
    return next((a for a in aprovacoes_de(session, cotacao_id) if a.status == PENDENTE), None)


def aprovacao_vigente(session: Session, cotacao_id: int,
                      fingerprint: str) -> Optional[AprovacaoCotacao]:
    """A decisão APROVADA que corresponde **exatamente** a esta configuração."""
    return next((a for a in aprovacoes_de(session, cotacao_id)
                 if a.status == APROVADA and a.fingerprint == fingerprint), None)


def estado_desatualizado(session: Session, cotacao: Cotacao,
                         itens: Sequence[CotacaoItem]) -> bool:
    """Há premissa mais nova que a snapshotada? (Sessão 5 — detecta, não recalcula.)"""
    return bool(adm.premissas_desatualizadas(session, cotacao, itens)["desatualizado"])


def avaliar(session: Session, cotacao: Cotacao, *, frete: Optional[dict] = None
            ) -> wf.Prontidao:
    """Prontidão da cotação agora, com a aprovação vigente resolvida do banco."""
    itens = itens_de(session, cotacao.id)
    fp = wf.fingerprint(cotacao, itens)
    desatualizada = estado_desatualizado(session, cotacao, itens) \
        and not cotacao.premissas_mantidas_aprovadas
    return wf.avaliar(cotacao, itens, frete=frete,
                      premissas_desatualizadas=desatualizada,
                      aprovacao_vigente=aprovacao_vigente(session, cotacao.id, fp))


# ---------------------------------------------------------------------------
# Imutabilidade
# ---------------------------------------------------------------------------
def exigir_editavel(cotacao: Cotacao, acao: str = "alterar"):
    """A barreira da imutabilidade. Server-side, e não o botão escondido.

    Emitido é documento: o cliente já tem uma cópia com aquele número. Alterar a revisão
    emitida faria a versão dele e a nossa divergirem em silêncio — que é o pior modo de
    divergir. O caminho é criar uma revisão nova.
    """
    if cotacao.status in wf.ESTADOS_IMUTAVEIS:
        raise OperacaoInvalida(
            f"Não é possível {acao}: a cotação está em '{cotacao.status}' e é imutável. "
            "Para mudar valores, crie uma revisão — a emitida continua íntegra, e é ela "
            "que o cliente tem em mãos.")


# ---------------------------------------------------------------------------
# Invalidação
# ---------------------------------------------------------------------------
def invalidar_aprovacoes_obsoletas(session: Session, cotacao: Cotacao, *,
                                   ator: Optional[Usuario] = None,
                                   motivo: str = "configuração alterada") -> int:
    """Mata toda decisão cujo fingerprint não corresponde mais ao estado atual.

    Chamada depois de **qualquer** recálculo. Aprovação pendente também morre: deixar o
    aprovador decidir sobre uma configuração que já não existe é pior que não ter pedido.
    """
    itens = itens_de(session, cotacao.id)
    atual = wf.fingerprint(cotacao, itens)
    invalidadas = 0
    for a in aprovacoes_de(session, cotacao.id):
        if a.status in (PENDENTE, APROVADA) and a.fingerprint != atual:
            a.status = INVALIDADA
            a.invalidado_em = datetime.utcnow()
            a.invalidacao_motivo = motivo
            session.add(a)
            invalidadas += 1
            adm.registrar(session, ator=ator, acao="INVALIDATE_APPROVAL",
                          entidade="AprovacaoCotacao", entidade_id=a.id,
                          escopo=f"cotação {cotacao.numero or cotacao.id}",
                          antes=f"{a.status} · {a.fingerprint[:12]}",
                          depois=f"INVALIDADA · atual {atual[:12]}",
                          motivo=motivo, origem="workflow")
    if invalidadas and cotacao.status in (StatusCotacao.aguardando_aprovacao.value,
                                          StatusCotacao.aprovada.value):
        cotacao.status = StatusCotacao.rascunho.value
        session.add(cotacao)
    return invalidadas


# ---------------------------------------------------------------------------
# Pedido de aprovação
# ---------------------------------------------------------------------------
def solicitar_aprovacao(session: Session, cotacao: Cotacao, *, ator: Usuario,
                        justificativa: str, frete: Optional[dict] = None
                        ) -> AprovacaoCotacao:
    """Abre um pedido para a configuração atual."""
    exigir_editavel(cotacao, "pedir aprovação")
    itens = itens_de(session, cotacao.id)
    prontidao = avaliar(session, cotacao, frete=frete)

    if not prontidao.precisa_aprovacao:
        raise OperacaoInvalida(
            "Esta cotação não tem exceção comercial: o preço não está abaixo do recomendado "
            "e a margem não está abaixo da meta. Não há o que aprovar.")
    if not (justificativa or "").strip():
        raise OperacaoInvalida(
            "Pedido de exceção exige justificativa comercial — quem decide precisa saber "
            "por quê.", status=400)

    invalidar_aprovacoes_obsoletas(session, cotacao, ator=ator,
                                   motivo="novo pedido de aprovação")
    # Um pedido pendente para a MESMA configuração não vira dois.
    existente = pedido_pendente(session, cotacao.id)
    if existente is not None and existente.fingerprint == prontidao.fingerprint:
        return existente

    pedido = AprovacaoCotacao(
        cotacao_id=cotacao.id, revisao=cotacao.revisao,
        fingerprint=prontidao.fingerprint, status=PENDENTE,
        motivos=json.dumps(sorted({e.motivo for e in prontidao.excecoes}),
                           ensure_ascii=False),
        excecoes_json=json.dumps([e.como_dict() for e in prontidao.excecoes],
                                 ensure_ascii=False),
        resumo_json=json.dumps(wf.resumo_comercial(itens), ensure_ascii=False),
        solicitante_id=ator.id, solicitante_email=ator.email,
        justificativa=justificativa.strip())
    session.add(pedido)
    cotacao.status = StatusCotacao.aguardando_aprovacao.value
    session.add(cotacao)
    session.flush()

    adm.registrar(session, ator=ator, acao="REQUEST_APPROVAL",
                  entidade="AprovacaoCotacao", entidade_id=pedido.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  depois=f"PENDENTE · {prontidao.fingerprint[:12]}",
                  motivo=justificativa, origem="workflow",
                  detalhe={"motivos": [e.motivo for e in prontidao.excecoes]})
    return pedido


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------
def _exigir_alcada(ator: Usuario):
    if not getattr(ator, "aprova_cotacoes", False):
        raise HTTPException(
            status_code=403,
            detail="Decidir sobre exceção comercial exige alçada de aprovação.")


def decidir(session: Session, cotacao: Cotacao, pedido_id: int, *, ator: Usuario,
            aprovar: bool, comentario: str = "",
            fingerprint_visto: Optional[str] = None) -> AprovacaoCotacao:
    """Aprova ou rejeita — conferindo que a configuração é a que o aprovador viu.

    O `fingerprint_visto` vem da tela do aprovador. Se o vendedor mexeu no preço enquanto o
    pedido estava aberto, a decisão seria sobre outra proposta — e é aí que aprovações
    erradas nascem, sem ninguém perceber.
    """
    _exigir_alcada(ator)
    pedido = session.get(AprovacaoCotacao, pedido_id)
    if pedido is None or pedido.cotacao_id != cotacao.id:
        raise OperacaoInvalida("Pedido de aprovação não encontrado.", status=404)

    # Idempotência: decidir duas vezes o mesmo pedido não cria decisão contraditória.
    if pedido.status in (APROVADA, REJEITADA):
        return pedido
    if pedido.status == INVALIDADA:
        raise OperacaoInvalida(
            "Este pedido foi invalidado porque a cotação mudou depois de ele ser aberto. "
            "Peça um pedido novo para a configuração atual.")

    itens = itens_de(session, cotacao.id)
    atual = wf.fingerprint(cotacao, itens)
    if pedido.fingerprint != atual:
        invalidar_aprovacoes_obsoletas(session, cotacao, ator=ator,
                                       motivo="configuração mudou antes da decisão")
        session.flush()
        raise OperacaoInvalida(
            "A cotação mudou depois que esta tela foi aberta. A decisão seria sobre uma "
            "proposta que não existe mais — recarregue e avalie a configuração atual.")
    if fingerprint_visto and fingerprint_visto != atual:
        raise OperacaoInvalida(
            "A configuração mudou desde que você abriu este pedido. Recarregue antes de "
            "decidir.")

    pedido.status = APROVADA if aprovar else REJEITADA
    pedido.aprovador_id = ator.id
    pedido.aprovador_email = ator.email
    pedido.comentario = (comentario or "").strip() or None
    pedido.decidido_em = datetime.utcnow()
    session.add(pedido)

    # Rejeitar não encerra nem apaga: devolve a cotação para edição.
    cotacao.status = (StatusCotacao.aprovada.value if aprovar
                      else StatusCotacao.rascunho.value)
    session.add(cotacao)

    adm.registrar(session, ator=ator, acao="APPROVE" if aprovar else "REJECT",
                  entidade="AprovacaoCotacao", entidade_id=pedido.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  antes=f"PENDENTE · {pedido.fingerprint[:12]}",
                  depois=f"{pedido.status} · {pedido.fingerprint[:12]}",
                  motivo=pedido.comentario, origem="workflow",
                  detalhe={"motivos": json.loads(pedido.motivos or "[]")})
    return pedido


# ---------------------------------------------------------------------------
# Premissas desatualizadas
# ---------------------------------------------------------------------------
def manter_premissas_antigas(session: Session, cotacao: Cotacao, *, ator: Usuario,
                             motivo: str):
    """Registra a escolha de emitir com premissa antiga. Vira exceção a aprovar.

    A alternativa — atualizar — é a da Sessão 5 e recalcula tudo, invalidando aprovação.
    O que não existe é a terceira via: emitir em silêncio com premissa que já se sabe
    ultrapassada.
    """
    _exigir_alcada(ator)
    if not (motivo or "").strip():
        raise OperacaoInvalida("Manter premissa desatualizada exige motivo registrado.",
                               status=400)
    exigir_editavel(cotacao, "manter premissas antigas")
    cotacao.premissas_mantidas_aprovadas = True
    session.add(cotacao)
    adm.registrar(session, ator=ator, acao="KEEP_STALE_PREMISES_APPROVED",
                  entidade="Cotacao", entidade_id=cotacao.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id}",
                  motivo=motivo, origem="workflow")


# ---------------------------------------------------------------------------
# Emissão
# ---------------------------------------------------------------------------
def emitir(session: Session, cotacao: Cotacao, *, ator: Usuario,
           frete: Optional[dict] = None, pdf_caminho: Optional[str] = None
           ) -> SnapshotEmissao:
    """Congela a revisão. Revalida tudo **agora**, não confia no que passou.

    O snapshot existe para que reconstruir o documento não dependa de nenhum lookup vivo:
    nem do catálogo, nem das premissas, nem da tabela de frete.
    """
    if cotacao.status in wf.ESTADOS_IMUTAVEIS:
        # Idempotência: emitir de novo devolve o snapshot que já existe, sem gerar outro.
        existente = session.exec(
            select(SnapshotEmissao)
            .where(SnapshotEmissao.cotacao_id == cotacao.id)
            .where(SnapshotEmissao.revisao == cotacao.revisao)).first()
        if existente is not None:
            return existente
        raise OperacaoInvalida(f"A cotação está em '{cotacao.status}' e não pode ser emitida.")

    wf.exigir_transicao(cotacao.status, StatusCotacao.emitida.value)
    itens = itens_de(session, cotacao.id)
    prontidao = avaliar(session, cotacao, frete=frete)
    if not prontidao.pode_emitir:
        raise OperacaoInvalida(
            "A cotação não está pronta para emissão: "
            + " ".join(prontidao.motivos or ["motivo não determinado"]))

    aprov = aprovacao_vigente(session, cotacao.id, prontidao.fingerprint) \
        if prontidao.precisa_aprovacao else None
    cliente = session.get(Cliente, cotacao.cliente_id) if cotacao.cliente_id else None

    snapshot = SnapshotEmissao(
        cotacao_id=cotacao.id, revisao=cotacao.revisao, numero=cotacao.numero,
        fingerprint=prontidao.fingerprint, emitido_por=ator.email,
        aprovacao_id=aprov.id if aprov else None,
        aprovacao_fingerprint=aprov.fingerprint if aprov else None,
        cliente_json=json.dumps({
            "id": getattr(cliente, "id", None), "nome": getattr(cliente, "nome", None),
            "cnpj_cpf": getattr(cliente, "cnpj_cpf", None),
            "cidade_uf": getattr(cliente, "cidade_uf", None),
            "estado": getattr(cliente, "estado", None)}, ensure_ascii=False),
        itens_json=json.dumps([{
            "id": it.id, "produto_id": it.produto_id, "nome": it.nome_produto,
            "especificacao": it.especificacao, "quantidade": para_float(it.quantidade),
            "preco_base": para_float(it.preco_base),
            "preco_negociado": para_float(it.preco_negociado),
            "faturamento": para_float(it.faturamento),
            "custo_referencia_id": it.custo_referencia_id,
            "custo_referencia_versao": it.custo_referencia_versao,
            "condicao_pagamento_id": it.condicao_pagamento_id,
            "aliquota_interestadual_id": it.aliquota_interestadual_id,
            "icms_pct": para_float(it.icms_pct), "encargo_pct": para_float(it.encargo_pct),
        } for it in itens], ensure_ascii=False),
        totais_json=json.dumps(wf.resumo_comercial(itens), ensure_ascii=False),
        fiscal_json=json.dumps({
            "uf_origem_fiscal": cotacao.uf_origem_fiscal,
            "estado_destino": cotacao.estado_destino,
            "contribuinte_icms": cotacao.contribuinte_icms,
            "finalidade": cotacao.finalidade,
            "condicao_pagamento": cotacao.condicao_pagamento}, ensure_ascii=False),
        frete_json=json.dumps(frete or {"cif": False}, ensure_ascii=False, default=str),
        premissas_json=json.dumps(
            [json.loads(it.premissas_pinadas) for it in itens if it.premissas_pinadas],
            ensure_ascii=False),
        # A memória interna fica no snapshot para auditoria — e NUNCA no PDF do cliente.
        memoria_json=json.dumps([it.memoria_json for it in itens if it.memoria_json],
                                ensure_ascii=False),
        pdf_caminho=pdf_caminho)
    session.add(snapshot)

    cotacao.status = StatusCotacao.emitida.value
    cotacao.fingerprint = prontidao.fingerprint
    cotacao.issued_em = datetime.utcnow()
    cotacao.issued_por = ator.email
    cotacao.emitida_em = cotacao.emitida_em or cotacao.issued_em
    session.add(cotacao)
    session.flush()

    adm.registrar(session, ator=ator, acao="ISSUE", entidade="Cotacao",
                  entidade_id=cotacao.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  depois=f"emitida · {prontidao.fingerprint[:12]}",
                  origem="workflow",
                  detalhe={"snapshot_id": snapshot.id,
                           "aprovacao_id": aprov.id if aprov else None})
    return snapshot


def marcar_enviada(session: Session, cotacao: Cotacao, *, ator: Usuario) -> Cotacao:
    """ISSUED → SENT. Não toca em economia."""
    if cotacao.status == StatusCotacao.enviada.value:
        return cotacao                       # idempotente
    wf.exigir_transicao(cotacao.status, StatusCotacao.enviada.value)
    cotacao.status = StatusCotacao.enviada.value
    cotacao.sent_em = datetime.utcnow()
    cotacao.sent_por = ator.email
    session.add(cotacao)
    adm.registrar(session, ator=ator, acao="MARK_SENT", entidade="Cotacao",
                  entidade_id=cotacao.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  depois="enviada", origem="workflow")
    return cotacao


def cancelar(session: Session, cotacao: Cotacao, *, ator: Usuario, motivo: str) -> Cotacao:
    """Cancela sem apagar nada — PDF, snapshot, aprovação e memória continuam."""
    if not (motivo or "").strip():
        raise OperacaoInvalida("Cancelar exige motivo registrado.", status=400)
    if cotacao.status == StatusCotacao.cancelada.value:
        return cotacao
    wf.exigir_transicao(cotacao.status, StatusCotacao.cancelada.value)
    cotacao.status = StatusCotacao.cancelada.value
    cotacao.cancelada_em = datetime.utcnow()
    cotacao.cancelada_por = ator.email
    cotacao.cancelamento_motivo = motivo.strip()
    session.add(cotacao)
    adm.registrar(session, ator=ator, acao="CANCEL", entidade="Cotacao",
                  entidade_id=cotacao.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  depois="cancelada", motivo=motivo, origem="workflow")
    return cotacao


# ---------------------------------------------------------------------------
# Revisão
# ---------------------------------------------------------------------------
def criar_revisao(session: Session, cotacao: Cotacao, *, ator: Usuario) -> Cotacao:
    """Cria a próxima revisão a partir de uma emitida. A original fica intacta.

    A revisão nova nasce **em rascunho e sem aprovação**: herdar a decisão da revisão
    anterior seria aprovar às cegas uma proposta que ainda nem foi escrita.
    """
    if cotacao.status not in (StatusCotacao.emitida.value, StatusCotacao.enviada.value):
        raise OperacaoInvalida(
            "Só se cria revisão a partir de uma cotação emitida. Enquanto ela está em "
            "rascunho, é só editar.")

    raiz_id = cotacao.cotacao_origem_id or cotacao.id
    irmas = session.exec(select(Cotacao)
                         .where((Cotacao.cotacao_origem_id == raiz_id)
                                | (Cotacao.id == raiz_id))).all()
    proxima = max((c.revisao or 1) for c in irmas) + 1

    nova = Cotacao(
        cliente_id=cotacao.cliente_id, numero=cotacao.numero, revisao=proxima,
        cotacao_origem_id=raiz_id, status=StatusCotacao.rascunho.value,
        estado_origem=cotacao.estado_origem, uf_origem_fiscal=cotacao.uf_origem_fiscal,
        estado_destino=cotacao.estado_destino, contribuinte_icms=cotacao.contribuinte_icms,
        finalidade=cotacao.finalidade, condicao_pagamento=cotacao.condicao_pagamento,
        freight_type=cotacao.freight_type, freight_valor=cotacao.freight_valor,
        frete=cotacao.frete, vendedor=cotacao.vendedor,
        contato_nome=cotacao.contato_nome,
        departamento_contato=cotacao.departamento_contato,
        prazo_entrega=cotacao.prazo_entrega, local_entrega=cotacao.local_entrega,
        observacoes=cotacao.observacoes, termos_texto=cotacao.termos_texto)
    session.add(nova)
    session.flush()

    for it in itens_de(session, cotacao.id):
        copia = CotacaoItem(**{k: v for k, v in it.model_dump().items()
                               if k not in ("id", "cotacao_id")})
        copia.cotacao_id = nova.id
        session.add(copia)
    session.flush()

    adm.registrar(session, ator=ator, acao="CREATE_REVISION", entidade="Cotacao",
                  entidade_id=nova.id,
                  escopo=f"cotação {cotacao.numero or cotacao.id}",
                  antes=f"r{cotacao.revisao} ({cotacao.status})",
                  depois=f"r{proxima} (rascunho)", origem="workflow",
                  detalhe={"origem_id": cotacao.id, "raiz_id": raiz_id})
    return nova


def revisoes_de(session: Session, cotacao: Cotacao) -> List[Cotacao]:
    raiz_id = cotacao.cotacao_origem_id or cotacao.id
    linhas = session.exec(select(Cotacao)
                          .where((Cotacao.cotacao_origem_id == raiz_id)
                                 | (Cotacao.id == raiz_id))).all()
    return sorted(linhas, key=lambda c: (c.revisao or 1, c.id or 0))


# ---------------------------------------------------------------------------
# Compromisso firme
# ---------------------------------------------------------------------------
def custos_reconfirmados(session: Session, itens: Sequence[CotacaoItem]) -> set:
    """Itens cuja referência de custo foi **reconfirmada depois** de o item ser formado.

    A pergunta que isto responde não é "qual era a confiança quando emitimos" — essa está
    congelada no item, e é ela que explica o documento. É "a confiança **hoje** ainda
    impede assumir compromisso?".

    Sem isto, um item emitido com custo em REVALIDAR ficaria bloqueado para sempre: o item
    é imutável, seu `status_custo_item` nunca mudaria, e reconfirmar o custo no cadastro não
    teria efeito nenhum — o que tornaria a reconfirmação inútil justamente onde ela importa.

    O que **não** acontece aqui: nada é promovido a CONFIRMADO, nem no item nem na
    referência. O item continua dizendo REVALIDAR, porque foi assim que o preço se formou.
    """
    from app import custo_service as cs
    from app.models import StatusCusto

    resolvidos = set()
    for it in itens:
        if not it.produto_id:
            continue
        pendente = (bool(getattr(it, "confirmation_pending", False))
                    or (it.status_custo_item or "").upper() == "REVALIDAR")
        if not pendente:
            continue
        vigente = cs.referencia_vigente(session, it.produto_id)
        if vigente is None:
            continue
        # A referência vigente precisa ser CONFIRMADA e não estar aguardando confirmação —
        # e precisa ser mais nova que a que formou o item, senão não houve reconfirmação
        # nenhuma, apenas a mesma linha sendo lida de novo.
        mais_nova = (it.custo_referencia_id is None
                     or (vigente.id or 0) != it.custo_referencia_id)
        confirmada = (vigente.status_custo == StatusCusto.confirmado.value
                      and not vigente.confirmation_pending)
        if mais_nova and confirmada:
            resolvidos.add(it.id)
    return resolvidos


def validar_compromisso_firme(session: Session, cotacao: Cotacao, *,
                              frete: Optional[dict] = None) -> wf.Compromisso:
    itens = itens_de(session, cotacao.id)
    fp = wf.fingerprint(cotacao, itens)
    return wf.validar_compromisso_firme(
        cotacao, itens, frete=frete,
        aprovacao_vigente=aprovacao_vigente(session, cotacao.id, fp),
        custos_reconfirmados=custos_reconfirmados(session, itens))


def fila_de_aprovacao(session: Session) -> List[dict]:
    """Pedidos pendentes, para a tela do aprovador."""
    pendentes = session.exec(select(AprovacaoCotacao)
                             .where(AprovacaoCotacao.status == PENDENTE)
                             .order_by(AprovacaoCotacao.criado_em)).all()
    saida = []
    for p in pendentes:
        cot = session.get(Cotacao, p.cotacao_id)
        cliente = session.get(Cliente, cot.cliente_id) if cot and cot.cliente_id else None
        saida.append({"pedido": p, "cotacao": cot,
                      "cliente": getattr(cliente, "nome", None),
                      "motivos": json.loads(p.motivos or "[]"),
                      "resumo": json.loads(p.resumo_json or "{}")})
    return saida

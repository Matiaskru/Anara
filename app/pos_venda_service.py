"""Pós-venda V1 — de VENDIDO até PAGO, sem virar ERP (Fase 3B).

    VENDIDO → AGUARDANDO_ENTREGA → (entrega) → AGUARDANDO_PAGAMENTO → PAGO
                                                       └→ ATRASADO → PAGO

Três valores, três fatos, três lugares — e **não se chamam pelo mesmo nome**:

    VALOR VENDIDO    `valor_fechado`, snapshot do momento em que a venda virou GANHA
    VALOR FATURADO   só existe quando `faturado_em` (+ documento) foi registrado
    VALOR PAGO       só existe quando `pago_em` foi registrado (status PAGO)

`ATRASADO` é marcado por quem tem alçada financeira. O sistema não chama ninguém de
inadimplente sozinho; "clientes inadimplentes" é análise derivada de outro lugar.

## Quem pode o quê

Vendedora: vê tudo isto; edita entrega prevista, registra entrega e observação.
OWNER/ADMIN (economia): faturamento, documento fiscal, pagamento previsto, PAGO, ATRASADO.
Toda alteração financeira vai para `AuditLog`.

## Limitação declarada da V1

Sem pagamento parcial: uma venda é PAGO ou não. Parcelas, adiantamentos e baixa parcial
ficam para uma versão que tenha integração financeira.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session

from app import admin_service as adm
from app.models import Oportunidade, StatusOportunidade, StatusPosVenda, Usuario

AGUARDANDO_ENTREGA = StatusPosVenda.aguardando_entrega.value
AGUARDANDO_PAGAMENTO = StatusPosVenda.aguardando_pagamento.value
PAGO = StatusPosVenda.pago.value
ATRASADO = StatusPosVenda.atrasado.value
STATUS = [s.value for s in StatusPosVenda]

#: Status em que a venda ainda tem dinheiro a receber.
EM_ABERTO = (AGUARDANDO_ENTREGA, AGUARDANDO_PAGAMENTO, ATRASADO)


class PosVendaInvalido(HTTPException):
    def __init__(self, detalhe: str, status: int = 409):
        super().__init__(status_code=status, detail=detalhe)


def _exigir_vendida(op: Oportunidade):
    if op.status != StatusOportunidade.ganha.value:
        raise PosVendaInvalido("Pós-venda só existe para venda marcada como vendida.")
    if op.status_pos_venda is None:
        # Venda ganha antes da Fase 3B (não há nenhuma no banco real, mas o caminho é
        # explícito): nasce em AGUARDANDO_ENTREGA no primeiro toque.
        op.status_pos_venda = AGUARDANDO_ENTREGA


def _exigir_financeiro(ator: Usuario, acao: str):
    """PAGO, ATRASADO, faturamento e pagamento previsto são alçada financeira (OWNER/ADMIN)."""
    if not getattr(ator, "ve_economia", False):
        raise HTTPException(status_code=403,
                            detail=f"{acao} é registro financeiro: só administrador ou dono.")


def _mudar(session: Session, op: Oportunidade, novo: str, *, ator: Usuario, motivo: str,
           financeiro: bool):
    anterior = op.status_pos_venda
    op.status_pos_venda = novo
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_STATUS", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=anterior, depois=novo,
                  motivo=motivo, origem="pos-venda:financeiro" if financeiro else "pos-venda")


# ---------------------------------------------------------------------------
# Vendedora
# ---------------------------------------------------------------------------
def definir_entrega_prevista(session: Session, op: Oportunidade, *, ator: Usuario,
                             prevista_em: Optional[date]) -> Oportunidade:
    _exigir_vendida(op)
    antes = op.entrega_prevista_em
    op.entrega_prevista_em = prevista_em
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_ENTREGA_PREVISTA", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=str(antes) if antes else None,
                  depois=str(prevista_em) if prevista_em else None, origem="pos-venda")
    return op


def registrar_entrega(session: Session, op: Oportunidade, *, ator: Usuario,
                      entregue_em: Optional[datetime] = None) -> Oportunidade:
    """Entregue: se ainda não está pago, passa a aguardar pagamento. Idempotente."""
    _exigir_vendida(op)
    if op.entregue_em is not None:
        return op
    op.entregue_em = entregue_em or datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_ENTREGA", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, depois=str(op.entregue_em),
                  origem="pos-venda")
    if op.status_pos_venda == AGUARDANDO_ENTREGA:
        _mudar(session, op, AGUARDANDO_PAGAMENTO, ator=ator, motivo="entrega registrada",
               financeiro=False)
    return op


def registrar_observacao(session: Session, op: Oportunidade, *, ator: Usuario,
                         texto: Optional[str]) -> Oportunidade:
    _exigir_vendida(op)
    op.observacao_pos_venda = (texto or "").strip() or None
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_OBSERVACAO", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, depois=(op.observacao_pos_venda or "")[:200],
                  origem="pos-venda")
    return op


# ---------------------------------------------------------------------------
# Financeiro — OWNER/ADMIN
# ---------------------------------------------------------------------------
def registrar_faturamento(session: Session, op: Oportunidade, *, ator: Usuario,
                          faturado_em: Optional[date], numero_documento_fiscal: Optional[str]
                          ) -> Oportunidade:
    """Faturado ≠ vendido: só existe quando alguém registra a data e o documento."""
    _exigir_financeiro(ator, "Registrar faturamento")
    _exigir_vendida(op)
    antes = (str(op.faturado_em) if op.faturado_em else None, op.numero_documento_fiscal)
    op.faturado_em = faturado_em or date.today()
    op.numero_documento_fiscal = (numero_documento_fiscal or "").strip() or None
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_FATURAMENTO", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=str(antes),
                  depois=f"{op.faturado_em} · {op.numero_documento_fiscal or 'sem documento'}",
                  origem="pos-venda:financeiro")
    return op


def definir_pagamento_previsto(session: Session, op: Oportunidade, *, ator: Usuario,
                               previsto_em: Optional[date]) -> Oportunidade:
    _exigir_financeiro(ator, "Definir pagamento previsto")
    _exigir_vendida(op)
    antes = op.pagamento_previsto_em
    op.pagamento_previsto_em = previsto_em
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="POS_VENDA_PAGAMENTO_PREVISTO",
                  entidade="Oportunidade", entidade_id=op.id, escopo=op.titulo,
                  antes=str(antes) if antes else None, depois=str(previsto_em) if previsto_em else None,
                  origem="pos-venda:financeiro")
    return op


def marcar_pago(session: Session, op: Oportunidade, *, ator: Usuario,
                pago_em: Optional[datetime] = None) -> Oportunidade:
    """PAGO a partir de qualquer status (inclusive ATRASADO). `pago_em` obrigatório: default agora."""
    _exigir_financeiro(ator, "Marcar pago")
    _exigir_vendida(op)
    if op.status_pos_venda == PAGO:
        return op
    op.pago_em = pago_em or datetime.utcnow()
    session.add(op)
    _mudar(session, op, PAGO, ator=ator, motivo=f"pago em {op.pago_em:%d/%m/%Y}", financeiro=True)
    return op


def marcar_atrasado(session: Session, op: Oportunidade, *, ator: Usuario,
                    observacao: Optional[str] = None) -> Oportunidade:
    """ATRASADO é decisão de quem tem alçada. Mantém a data prevista; anota o motivo."""
    _exigir_financeiro(ator, "Marcar atrasado")
    _exigir_vendida(op)
    if op.status_pos_venda == PAGO:
        raise PosVendaInvalido("Venda paga não fica atrasada. Corrija o pagamento antes.")
    if observacao:
        op.observacao_pos_venda = observacao.strip()
    _mudar(session, op, ATRASADO, ator=ator,
           motivo=observacao or "marcado atrasado pelo financeiro", financeiro=True)
    return op


def corrigir_status(session: Session, op: Oportunidade, *, ator: Usuario, novo: str,
                    motivo: str) -> Oportunidade:
    """Correção explícita de status financeiro (ex.: ATRASADO → AGUARDANDO_PAGAMENTO)."""
    _exigir_financeiro(ator, "Corrigir status financeiro")
    _exigir_vendida(op)
    if novo not in STATUS:
        raise PosVendaInvalido(f"Status '{novo}' não existe. Válidos: {', '.join(STATUS)}.", 400)
    if not (motivo or "").strip():
        raise PosVendaInvalido("Corrigir status financeiro exige motivo.", 400)
    if novo == PAGO:
        return marcar_pago(session, op, ator=ator)
    _mudar(session, op, novo, ator=ator, motivo=f"correção: {motivo.strip()}", financeiro=True)
    return op


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def resumo(op: Oportunidade) -> dict:
    """O bloco de pós-venda que a tela mostra — sem economia interna (nada aqui é custo)."""
    return {
        "status_pos_venda": op.status_pos_venda,
        "entrega_prevista_em": op.entrega_prevista_em,
        "entregue_em": op.entregue_em,
        "faturado_em": op.faturado_em,
        "numero_documento_fiscal": op.numero_documento_fiscal,
        "pagamento_previsto_em": op.pagamento_previsto_em,
        "pago_em": op.pago_em,
        "observacao_pos_venda": op.observacao_pos_venda,
        "valor_vendido": op.valor_fechado,
        "valor_faturado": op.valor_fechado if op.faturado_em else None,
        "valor_pago": op.valor_fechado if op.status_pos_venda == PAGO else None,
    }

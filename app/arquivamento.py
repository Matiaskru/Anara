"""Arquivar e apagar — o jeito seguro de limpar teste sem perder histórico.

A regra do projeto sempre foi não apagar cotação. Ela continua valendo para o que é registro
comercial; o que mudou é que teste também vira lixo, e lixo na lista atrapalha.

Então são dois níveis:

* **arquivar** — a cotação sai da lista, dos filtros e dos totais do dashboard, mas continua no
  banco e pode voltar a qualquer momento. É o que resolve 99% dos casos;
* **apagar de vez** — remove mesmo, e só funciona no que já está arquivado. Antes de apagar,
  o banco inteiro é copiado para `data/backups/`, então dá para voltar atrás.

Cotação que já foi emitida, enviada, fechada, virou pedido ou teve aceite registrado não é
teste: continua podendo ser apagada, mas o sistema avisa antes, por escrito.
"""
from datetime import datetime
from typing import List, Optional

from sqlmodel import Session, select

from app.migrations import fazer_backup
from app.models import Cliente, Cotacao, CotacaoItem, StatusCotacao


def motivos_para_pensar_duas_vezes(cotacao: Cotacao, itens: int) -> List[str]:
    """O que faz essa cotação parecer registro de verdade, e não teste."""
    avisos = []
    if cotacao.status in (StatusCotacao.enviada, StatusCotacao.fechada, StatusCotacao.pedido):
        avisos.append(f"está com status '{cotacao.status.value}'")
    if cotacao.emitida_em:
        avisos.append(f"foi emitida em {cotacao.emitida_em:%d/%m/%Y}")
    if cotacao.pdf_gerado_em:
        avisos.append(f"teve PDF gerado em {cotacao.pdf_gerado_em:%d/%m/%Y}")
    if cotacao.aceite_em:
        avisos.append(f"teve aceite registrado por {cotacao.aceite_responsavel or 'alguém'}")
    if itens:
        avisos.append(f"tem {itens} item(ns)")
    return avisos


def arquivar(session: Session, cotacao_id: int, motivo: Optional[str] = None) -> Optional[Cotacao]:
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao or cotacao.arquivada_em:
        return cotacao
    cotacao.arquivada_em = datetime.utcnow()
    cotacao.arquivada_motivo = motivo or None
    session.add(cotacao)
    session.commit()
    session.refresh(cotacao)
    return cotacao


def restaurar(session: Session, cotacao_id: int) -> Optional[Cotacao]:
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return None
    cotacao.arquivada_em = None
    cotacao.arquivada_motivo = None
    session.add(cotacao)
    session.commit()
    session.refresh(cotacao)
    return cotacao


def apagar(session: Session, cotacao_id: int, fazer_copia: bool = True) -> dict:
    """Apaga de vez — só o que está arquivado. Copia o banco antes."""
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return {"ok": False, "erro": "Cotação não encontrada."}
    if not cotacao.arquivada_em:
        return {"ok": False, "erro": "Arquive antes de apagar. Serve de confirmação e evita "
                                     "apagar por engano."}

    backup = fazer_backup("exclusao") if fazer_copia else ""
    itens = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.cotacao_id == cotacao_id)).all()
    numero = cotacao.numero
    for item in itens:
        session.delete(item)
    session.delete(cotacao)
    session.commit()
    return {"ok": True, "numero": numero, "itens": len(itens), "backup": backup}


def arquivar_em_lote(session: Session, ids: List[int], motivo: Optional[str] = None) -> int:
    return sum(1 for i in ids if arquivar(session, i, motivo))


def apagar_em_lote(session: Session, ids: List[int]) -> dict:
    """Uma cópia do banco só, para o lote inteiro."""
    backup = fazer_backup("exclusao-lote")
    apagadas, recusadas = [], []
    for cotacao_id in ids:
        resultado = apagar(session, cotacao_id, fazer_copia=False)
        (apagadas if resultado.get("ok") else recusadas).append(cotacao_id)
    return {"apagadas": len(apagadas), "recusadas": recusadas, "backup": backup}


def arquivar_cliente(session: Session, cliente_id: int, arquivar_junto: bool = False) -> dict:
    """Cliente sai da lista. As cotações dele continuam, a menos que se peça para arquivar junto."""
    cliente = session.get(Cliente, cliente_id)
    if not cliente:
        return {"ok": False, "erro": "Cliente não encontrado."}
    cliente.ativo = False
    session.add(cliente)
    cotacoes = session.exec(select(Cotacao).where(Cotacao.cliente_id == cliente_id)).all()
    if arquivar_junto:
        for c in cotacoes:
            if not c.arquivada_em:
                c.arquivada_em = datetime.utcnow()
                c.arquivada_motivo = f"Cliente {cliente.nome} arquivado"
                session.add(c)
    session.commit()
    return {"ok": True, "cotacoes": len(cotacoes)}


def candidatas_a_teste(session: Session) -> List[dict]:
    """Cotações com cara de teste: rascunho, **sem nenhum item**, sem PDF e sem aceite.

    Exigir zero item é o que faz a sugestão ser confiável — rascunho com 25 itens e R$ 40 mil
    de faturamento é trabalho de verdade que ainda não foi enviado, não lixo de teste. A
    sugestão continua sendo só sugestão: quem marca e arquiva é quem está olhando.
    """
    itens_por_cotacao = {}
    for item in session.exec(select(CotacaoItem)).all():
        itens_por_cotacao[item.cotacao_id] = itens_por_cotacao.get(item.cotacao_id, 0) + 1

    sugestoes = []
    for c in session.exec(select(Cotacao)).all():
        if c.arquivada_em:
            continue
        if c.status != StatusCotacao.rascunho or c.pdf_gerado_em or c.aceite_em:
            continue
        if itens_por_cotacao.get(c.id, 0) > 0:
            continue
        sugestoes.append({
            "id": c.id, "numero": c.numero, "criado_em": c.criado_em,
            "itens": itens_por_cotacao.get(c.id, 0), "vendedor": c.vendedor,
        })
    return sugestoes

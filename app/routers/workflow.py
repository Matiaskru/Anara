"""Rotas do workflow comercial — pedir aprovação, decidir, emitir, revisar.

Nenhum valor de decisão vem do navegador. `precisa_aprovacao`, `pode_emitir` e o
`fingerprint` são **derivados** no servidor a cada chamada: mandar
`approval_required=false` num POST não muda nada, porque nada aqui lê esse campo.

A alçada de aprovação é `can_approve_quotes`, e é uma permissão própria — quem administra
premissa não vira, por isso, quem autoriza desconto.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import workflow as wf
from app import workflow_service as ws
from app.db import get_session
from app.models import AprovacaoCotacao, Cliente, Cotacao, SnapshotEmissao, Usuario
from app.permissoes import exigir_autenticado, exigir_economia, usuario_da_request
from app.templating import templates

router = APIRouter()


def _cotacao(session: Session, cotacao_id: int) -> Cotacao:
    cot = session.get(Cotacao, cotacao_id)
    if cot is None:
        raise ws.OperacaoInvalida("Cotação não encontrada.", status=404)
    return cot


def _veio_de_um_clique(request: Request) -> bool:
    """A chamada nasceu de um form na tela, ou de `fetch`?

    Mesma distinção que `templating.pagina_de_erro` já fazia para os erros: navegação de
    topo manda `Accept: text/html`; JavaScript, não. A recusa dessas rotas já saía em HTML
    pelo handler global de `HTTPException` — o que faltava era o **sucesso**, que devolvia
    JSON e deixava o usuário olhando para um objeto na barra de endereços.
    """
    return "text/html" in (request.headers.get("accept") or "")


def _resposta(request: Request, cotacao_id: int, payload: dict):
    """JSON para quem chamou por `fetch`; volta para a cotação para quem clicou."""
    if _veio_de_um_clique(request):
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)
    return JSONResponse(payload)


#: O frete entra na avaliação, e a **tela** precisa avaliar exatamente como a emissão avalia
#: — senão ela promete um botão que a emissão recusa. Por isso a resolução mora no serviço.
_frete = ws.frete_para_avaliar


# ---------------------------------------------------------------------------
# Situação
# ---------------------------------------------------------------------------
@router.get("/cotacoes/{cotacao_id}/situacao")
def situacao(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    """Prontidão da cotação. O vendedor vê o comercial; economia continua restrita."""
    exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    prontidao = ws.avaliar(session, cot, frete=_frete(session, cot))
    pendente = ws.pedido_pendente(session, cotacao_id)
    from app.permissoes import ve_economia

    corpo = {
        "status": cot.status, "revisao": cot.revisao,
        "pode_emitir": prontidao.pode_emitir,
        "precisa_aprovacao": prontidao.precisa_aprovacao,
        "aprovacao_valida": prontidao.aprovacao_valida,
        "motivos": prontidao.motivos,
        "blockers": [b.como_dict() for b in prontidao.blockers],
        "pedido_pendente_id": pendente.id if pendente else None,
        "fingerprint": prontidao.fingerprint,
    }
    if ve_economia(request):
        # A exceção traz preço recomendado, margem alvo e margem real — economia interna.
        corpo["excecoes"] = [e.como_dict() for e in prontidao.excecoes]
    else:
        # O vendedor precisa saber QUE há exceção e de que tipo, para pedir aprovação —
        # não os números internos que a sustentam.
        corpo["excecoes"] = [{"motivo": e.motivo, "escopo": e.escopo}
                             for e in prontidao.excecoes]
    return JSONResponse(corpo)


@router.get("/cotacoes/{cotacao_id}/compromisso")
def compromisso(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    """Esta cotação poderia virar pedido? (Não cria pedido — isso é da Sessão 7.)"""
    exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    return JSONResponse(
        ws.validar_compromisso_firme(session, cot, frete=_frete(session, cot)).como_dict())


# ---------------------------------------------------------------------------
# Pedido de aprovação
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/aprovacao/solicitar")
def solicitar(request: Request, cotacao_id: int, justificativa: str = Form(""),
              session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    pedido = ws.solicitar_aprovacao(session, cot, ator=ator, justificativa=justificativa,
                                    frete=_frete(session, cot))
    session.commit()
    return _resposta(request, cotacao_id,
                     {"pedido_id": pedido.id, "status": pedido.status,
                      "fingerprint": pedido.fingerprint,
                      "mensagem": "Pedido de aprovação registrado."})


@router.post("/cotacoes/{cotacao_id}/aprovacao/{pedido_id}/aprovar")
def aprovar(request: Request, cotacao_id: int, pedido_id: int, comentario: str = Form(""),
            fingerprint: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    pedido = ws.decidir(session, cot, pedido_id, ator=ator, aprovar=True,
                        comentario=comentario, fingerprint_visto=fingerprint or None)
    session.commit()
    return _resposta(request, cotacao_id,
                     {"pedido_id": pedido.id, "status": pedido.status,
                      "mensagem": "Exceção aprovada para esta configuração."})


@router.post("/cotacoes/{cotacao_id}/aprovacao/{pedido_id}/rejeitar")
def rejeitar(request: Request, cotacao_id: int, pedido_id: int, comentario: str = Form(""),
             fingerprint: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    pedido = ws.decidir(session, cot, pedido_id, ator=ator, aprovar=False,
                        comentario=comentario, fingerprint_visto=fingerprint or None)
    session.commit()
    return _resposta(request, cotacao_id,
                     {"pedido_id": pedido.id, "status": pedido.status,
                      "mensagem": "Pedido rejeitado. A cotação voltou a ser editável."})


@router.post("/cotacoes/{cotacao_id}/premissas/manter")
def manter_premissas(request: Request, cotacao_id: int, motivo: str = Form(""),
                     session: Session = Depends(get_session)):
    """Assume emitir com premissa antiga — vira exceção, e exige alçada."""
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    ws.manter_premissas_antigas(session, cot, ator=ator, motivo=motivo)
    session.commit()
    return JSONResponse({"ok": True,
                         "mensagem": "Registrada a decisão de manter as premissas atuais."})


# ---------------------------------------------------------------------------
# Emissão, envio, cancelamento, revisão
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/emitir")
def emitir(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    snapshot = ws.emitir(session, cot, ator=ator, frete=_frete(session, cot))
    session.commit()
    return _resposta(request, cotacao_id,
                     {"snapshot_id": snapshot.id, "status": cot.status,
                      "revisao": cot.revisao, "fingerprint": snapshot.fingerprint,
                      "mensagem": "Cotação emitida e congelada."})


@router.post("/cotacoes/{cotacao_id}/enviar")
def enviar(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    ws.marcar_enviada(session, cot, ator=ator)
    session.commit()
    return _resposta(request, cotacao_id,
                     {"status": cot.status, "mensagem": "Marcada como enviada."})


@router.post("/cotacoes/{cotacao_id}/cancelar")
def cancelar(request: Request, cotacao_id: int, motivo: str = Form(""),
             session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    ws.cancelar(session, cot, ator=ator, motivo=motivo)
    session.commit()
    return _resposta(request, cotacao_id,
                     {"status": cot.status, "mensagem": "Cancelada. Nada foi apagado."})


@router.post("/cotacoes/{cotacao_id}/revisao")
def nova_revisao(request: Request, cotacao_id: int,
                 session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    nova = ws.criar_revisao(session, cot, ator=ator)
    session.commit()
    # A revisão é uma cotação NOVA: quem clicou vai para ela, não para a que ficou congelada.
    return _resposta(request, nova.id,
                     {"cotacao_id": nova.id, "revisao": nova.revisao,
                      "mensagem": f"Revisão {nova.revisao} criada em rascunho. "
                                  "A emitida continua íntegra."})


# ---------------------------------------------------------------------------
# Fila do aprovador
# ---------------------------------------------------------------------------
@router.get("/aprovacoes", response_class=HTMLResponse)
def fila(request: Request, session: Session = Depends(get_session)):
    """Lista de pendências. Restrita a quem vê economia — a tela mostra preço e margem."""
    exigir_economia(request)
    ator = usuario_da_request(request)
    return templates.TemplateResponse(request, "aprovacoes_fila.html", {
        "active": "aprovacoes", "pendentes": ws.fila_de_aprovacao(session),
        "pode_decidir": bool(getattr(ator, "aprova_cotacoes", False)),
    })


@router.get("/aprovacoes/{pedido_id}", response_class=HTMLResponse)
def detalhe_pedido(request: Request, pedido_id: int,
                   session: Session = Depends(get_session)):
    exigir_economia(request)
    ator = usuario_da_request(request)
    pedido = session.get(AprovacaoCotacao, pedido_id)
    if pedido is None:
        raise ws.OperacaoInvalida("Pedido não encontrado.", status=404)
    cot = session.get(Cotacao, pedido.cotacao_id)
    itens = ws.itens_de(session, cot.id)
    atual = wf.fingerprint(cot, itens)
    return templates.TemplateResponse(request, "aprovacao_detalhe.html", {
        "active": "aprovacoes", "pedido": pedido, "cotacao": cot, "itens": itens,
        "cliente": session.get(Cliente, cot.cliente_id) if cot.cliente_id else None,
        "excecoes": json.loads(pedido.excecoes_json or "[]"),
        "motivos": json.loads(pedido.motivos or "[]"),
        "resumo": json.loads(pedido.resumo_json or "{}"),
        "fingerprint_atual": atual,
        "desatualizado": atual != pedido.fingerprint,
        "pode_decidir": bool(getattr(ator, "aprova_cotacoes", False)),
        "prontidao": ws.avaliar(session, cot, frete=_frete(session, cot)),
    })

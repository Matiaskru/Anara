"""Negociação da cotação — o preview reativo e a gravação, para a UI que vem depois.

Três rotas, todas sobre a mesma avaliação (`comercial_service.avaliar_negociacao`):

    GET  /cotacoes/{id}/negociacao           o estado atual, avaliado
    POST /cotacoes/{id}/negociacao/preview   "e se estes fossem os preços?" — não grava
    POST /cotacoes/{id}/negociacao           grava os preços, recalcula tudo, invalida aprovação

O corpo é JSON: `{"itens": [{"item_id": 12, "preco_negociado": "1234.56"}, ...]}` — ou, na
política de 21/09/2026, `{"item_id": 12, "desconto_pct": "0.25"}` (desconto sobre a TABELA do
item, fração). As duas formas são equivalentes: o servidor converte uma na outra e persiste o
desconto como alavanca. Item que não vier fica com o preço gravado. Preço de item travado
(política de 16/09) diferente do recomendado é recusado com 409 antes de qualquer gravação.

O que volta depende do papel — e é decidido aqui, no servidor, por lista de permissão:
a vendedora recebe `payload_vendedora` (preço, total, comissão estimada da cotação, status de
autonomia); OWNER/ADMIN recebem também a economia. A rota não é negada à vendedora porque ela
precisa dela para negociar; é filtrada.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app import comercial_service as com
from app.db import get_session
from app.dinheiro import D
from app.models import Cotacao
from app.permissoes import exigir_autenticado, ve_economia

router = APIRouter()


def _cotacao(session: Session, cotacao_id: int) -> Cotacao:
    cot = session.get(Cotacao, cotacao_id)
    if cot is None:
        raise HTTPException(status_code=404, detail="Cotação não encontrada.")
    return cot


def _payload(request: Request, av: com.Avaliacao) -> dict:
    return com.payload_admin(av) if ve_economia(request) else com.payload_vendedora(av)


async def _propostas(request: Request) -> dict:
    """`{item_id: {"preco": Decimal | None, "desconto": Decimal | None}}` do corpo.

    Preço é quantia comercial (positivo, 2 casas); desconto é fração sobre a tabela do item
    (0 ≤ d < 1). Cada linha traz um dos dois. A validação econômica (item sem tabela, travado)
    é do serviço.
    """
    try:
        corpo = await request.json()
    except Exception:                                   # noqa: BLE001
        raise HTTPException(status_code=400, detail="Corpo JSON inválido.")
    linhas = corpo.get("itens") if isinstance(corpo, dict) else None
    if not isinstance(linhas, list):
        raise HTTPException(status_code=400, detail="Esperado {\"itens\": [...]}")
    propostas = {}
    for linha in linhas:
        try:
            item_id = int(linha["item_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Cada item precisa de item_id.")
        preco = desconto = None
        try:
            if linha.get("preco_negociado") not in (None, ""):
                preco = D(str(linha["preco_negociado"]))
            if linha.get("desconto_pct") not in (None, ""):
                desconto = D(str(linha["desconto_pct"]))
        except (TypeError, ValueError, ArithmeticError):
            raise HTTPException(status_code=400,
                                detail=f"Item {item_id}: preço ou desconto inválido.")
        if preco is None and desconto is None:
            raise HTTPException(status_code=400,
                                detail=f"Item {item_id}: informe preco_negociado ou desconto_pct.")
        if preco is not None and preco <= 0:
            raise HTTPException(status_code=400,
                                detail=f"Preço unitário do item {item_id} deve ser positivo.")
        if desconto is not None and not (0 <= desconto < 1):
            raise HTTPException(status_code=400,
                                detail=f"Desconto do item {item_id} deve estar entre 0% e 99,99%.")
        propostas[item_id] = {"preco": preco, "desconto": desconto}
    return propostas


@router.get("/cotacoes/{cotacao_id}/negociacao")
def negociacao_atual(request: Request, cotacao_id: int,
                     session: Session = Depends(get_session)):
    exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    return JSONResponse(_payload(request, com.avaliar_negociacao(session, cot)))


@router.post("/cotacoes/{cotacao_id}/negociacao/preview")
async def preview(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    propostas = await _propostas(request)
    return JSONResponse(_payload(request, com.avaliar_negociacao(session, cot,
                                                                 propostas=propostas)))


@router.post("/cotacoes/{cotacao_id}/negociacao")
async def aplicar(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    cot = _cotacao(session, cotacao_id)
    propostas = await _propostas(request)
    av = com.aplicar_negociacao(session, cot, propostas, ator=ator)
    session.commit()
    return JSONResponse(_payload(request, com.avaliar_negociacao(session, cot)))

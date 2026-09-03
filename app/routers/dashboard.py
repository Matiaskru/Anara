from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Cliente, Cotacao, CotacaoItem, StatusCotacao
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    # cotação arquivada não conta em nada: é teste ou lixo que o Matias tirou da vista
    cotacoes = [c for c in session.exec(select(Cotacao)).all() if not c.arquivada_em]
    itens = session.exec(select(CotacaoItem)).all()
    clientes = {c.id: c for c in session.exec(select(Cliente)).all()}

    itens_por_cotacao = {}
    for it in itens:
        itens_por_cotacao.setdefault(it.cotacao_id, []).append(it)

    valor_total = 0.0
    lucro_total = 0.0
    faturamento_total_para_margem = 0.0
    lucro_total_para_margem = 0.0
    diffs = []
    tickets = []
    por_status = Counter()
    por_cliente = Counter()
    por_produto = Counter()
    por_mes = Counter()

    for c in cotacoes:
        por_status[c.status.value if hasattr(c.status, "value") else c.status] += 1
        seus_itens = itens_por_cotacao.get(c.id, [])
        total_cotacao = sum(i.faturamento for i in seus_itens)
        valor_total += total_cotacao
        lucro_total += sum(i.lucro for i in seus_itens)
        faturamento_total_para_margem += total_cotacao
        lucro_total_para_margem += sum(i.lucro for i in seus_itens)
        if total_cotacao:
            tickets.append(total_cotacao)
        if c.cliente_id in clientes:
            por_cliente[clientes[c.cliente_id].nome] += total_cotacao
        mes_key = c.criado_em.strftime("%Y-%m") if c.criado_em else "—"
        por_mes[mes_key] += total_cotacao
        for i in seus_itens:
            por_produto[i.nome_produto] += i.quantidade
            if i.diferenca_pct_vs_base is not None:
                diffs.append(i.diferenca_pct_vs_base)

    margem_media_ponderada = (lucro_total_para_margem / faturamento_total_para_margem) if faturamento_total_para_margem else 0.0
    ticket_medio = (sum(tickets) / len(tickets)) if tickets else 0.0
    diferenca_media = (sum(diffs) / len(diffs)) if diffs else 0.0

    principais_clientes = por_cliente.most_common(5)
    produtos_mais_cotados = por_produto.most_common(5)
    comparacao_mensal = sorted(por_mes.items())[-6:]

    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard",
        "valor_total": valor_total, "lucro_total": lucro_total,
        "margem_media_ponderada": margem_media_ponderada, "ticket_medio": ticket_medio,
        "num_cotacoes": len(cotacoes), "por_status": dict(por_status),
        "principais_clientes": principais_clientes, "produtos_mais_cotados": produtos_mais_cotados,
        "comparacao_mensal": comparacao_mensal, "diferenca_media": diferenca_media,
    })

"""Dashboard OWNER/ADMIN (Fase 3C) — gestão comercial, com economia.

A raiz `/` é o dashboard para quem vê economia; a vendedora é redirecionada para Vendas
(a raiz é o que se digita para "abrir o sistema", então redirecionar é melhor que negar).

Nenhum número é calculado aqui: tudo vem de `metrics_service.dashboard_admin` e
`metrics_service.serie_mensal`, as mesmas definições dos relatórios e dos testes.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import metrics_service as mx
from app.db import get_session
from app.models import Cliente, Fornecedor, Produto, Usuario
from app.permissoes import exigir_autenticado, ve_economia
from app.templating import templates

router = APIRouter()

#: Atalhos de período do dashboard. "Personalizado" é qualquer combinação de `inicio`/`fim`.
ATALHOS = [("mes", "Mês"), ("trimestre", "Trimestre"), ("ano", "Ano"), ("12m", "12 meses")]


def _int_ou_none(valor: str) -> Optional[int]:
    return int(valor) if (valor or "").isdigit() else None


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, periodo: str = "12m", inicio: str = "", fim: str = "",
              vendedora: str = "", cliente_id: str = "", fornecedor_id: str = "",
              familia: str = "", session: Session = Depends(get_session)):
    exigir_autenticado(request)
    if not ve_economia(request):
        return RedirectResponse(url="/vendas", status_code=303)

    personalizado = bool(inicio or fim)
    try:
        janela = mx.periodo_de("" if personalizado else periodo, inicio or None, fim or None)
    except ValueError:
        janela = mx.periodo_de(periodo)
        personalizado = False
    filtros = mx.FiltrosDashboard(
        responsavel_id=_int_ou_none(vendedora), cliente_id=_int_ou_none(cliente_id),
        fornecedor_id=_int_ou_none(fornecedor_id), familia=familia or None)

    painel = mx.dashboard_admin(session, janela, filtros)
    serie = mx.serie_mensal(session, meses=12, fim=janela.fim, filtros=filtros)

    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard", "painel": painel, "serie": serie, "periodo": janela,
        "atalho": "" if personalizado else periodo, "personalizado": personalizado,
        "inicio": inicio, "fim": fim, "atalhos": ATALHOS,
        "filtros": {"vendedora": vendedora, "cliente_id": cliente_id,
                    "fornecedor_id": fornecedor_id, "familia": familia},
        "usuarios": session.exec(select(Usuario).where(Usuario.ativo == True)  # noqa: E712
                                 .order_by(Usuario.nome)).all(),
        "clientes": session.exec(select(Cliente).where(Cliente.ativo == True)  # noqa: E712
                                 .order_by(Cliente.nome)).all(),
        "fornecedores": session.exec(select(Fornecedor).order_by(Fornecedor.nome)).all(),
        "familias": sorted({p.familia for p in produtos if p.familia}),
        "qs": getattr(request.url, "query", ""),
    })

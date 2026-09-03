import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import COOKIE_NAME, usuario_autenticado
from app.db import init_db
from app.migrations import backfill, migrar
from app.seeds import semear
from app.routers import (
    calculadora, clientes, configuracoes, cotacoes, dashboard, importar, login, produtos,
    relatorios,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Anara Cotações")

PUBLIC_PATHS = {"/login", "/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)
        if not usuario_autenticado(request):
            return RedirectResponse(url=f"/login?next={path}", status_code=303)
        return await call_next(request)


app.add_middleware(AuthMiddleware)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.include_router(login.router)
app.include_router(dashboard.router)
app.include_router(clientes.router)
app.include_router(produtos.router)
app.include_router(importar.router)
app.include_router(cotacoes.router)
app.include_router(calculadora.router)
app.include_router(configuracoes.router)
app.include_router(relatorios.router)


@app.on_event("startup")
def on_startup():
    """Sobe o esquema, aplica migrations pendentes e garante as tabelas de configuração.

    Tudo idempotente e não destrutivo: migration só cria tabela/coluna nova (com backup do
    banco antes), e as seeds só inserem o que ainda não existe.
    """
    init_db()
    migrar(verbose=False)
    backfill(verbose=False)
    semear(verbose=False)


@app.get("/health")
def health():
    return {"ok": True}

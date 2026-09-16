import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import COOKIE_NAME, ler_cookie
from app.db import engine, init_db
from app.migrations import backfill, migrar
from app.seeds import semear
from app.routers import (
    admin, calculadora, clientes, configuracoes, cotacoes, crm, dashboard, importar,
    login, negociacao, produtos, relatorios, relatorios_comerciais, usuarios, vendas,
    workflow,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Anara Cotações")

#: `/primeiro-acesso` é público porque só existe quando **não há** conta alguma — não há
#: cookie possível para autenticá-lo. A própria rota se recusa a responder assim que a
#: primeira conta é criada; o portão é o estado do banco, não esta lista.
PUBLIC_PATHS = {"/login", "/logout", "/health", "/primeiro-acesso"}


def _usuario_do_cookie(request: Request):
    """Resolve a identidade do cookie **contra o banco**, a cada request.

    O cookie carrega só `id` e `versao`; papel, `ativo` e nome vêm da tabela. É o que faz
    "desativei o vendedor" ter efeito imediato em vez de esperar o cookie expirar — e o que
    garante que promover alguém a ADMIN não dependa de a pessoa relogar.
    """
    from app.models import Usuario

    dados = ler_cookie(request.cookies.get(COOKIE_NAME, ""))
    if not dados:
        return None
    with Session(engine) as s:
        u = s.get(Usuario, dados["uid"])
        if u is None or not u.ativo:
            return None
        # sessão emitida antes de uma troca de senha / desativação não vale mais
        if int(dados.get("v", 0)) != int(u.sessao_versao):
            return None
        s.expunge(u)
        return u


class AuthMiddleware(BaseHTTPMiddleware):
    """Porta de entrada: resolve quem é, ou manda para o login.

    Autenticação é aqui, uma vez, para todas as rotas — nada de rota nova nascer aberta por
    esquecimento. **Autorização não é aqui**: quem pode ver o quê depende do endpoint e do
    payload, e mora em `app.permissoes` e `app.confidencial`.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.usuario = None
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)

        usuario = _usuario_do_cookie(request)
        if usuario is None:
            return RedirectResponse(url=f"/login?next={path}", status_code=303)
        request.state.usuario = usuario
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.exception_handler(HTTPException)
async def tratar_http_exception(request: Request, exc: HTTPException):
    """401 leva ao login; 403 é 403 — nunca uma página que finge funcionar.

    Um vendedor que abre `/configuracoes` recebe uma recusa explícita, e não o painel de
    premissas com os campos escondidos por CSS.
    """
    from app.permissoes import resposta_de_negacao

    redirect = resposta_de_negacao(exc)
    if redirect is not None:
        return redirect

    aceita_html = "text/html" in (request.headers.get("accept") or "")
    if exc.status_code == 403 and aceita_html:
        from app.templating import templates
        return templates.TemplateResponse(
            request, "403.html", {"detalhe": exc.detail}, status_code=403)

    # Um clique numa tela não pode terminar em `{"erro": ...}` na barra de endereços. Quem
    # chamou por `fetch` continua recebendo JSON — a diferença está em quem pediu, não no
    # tipo do erro. O `Accept` do navegador em navegação de topo traz `text/html`; o de uma
    # chamada de JavaScript, não.
    if aceita_html:
        from app.templating import pagina_de_erro

        return pagina_de_erro(
            request, titulo="Não foi possível concluir a ação",
            motivos=[str(exc.detail)],
            voltar=request.headers.get("referer") or "/",
            rotulo_voltar="Voltar",
            status_code=exc.status_code)
    return JSONResponse({"erro": exc.detail}, status_code=exc.status_code)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.include_router(login.router)
app.include_router(dashboard.router)
app.include_router(clientes.router)
app.include_router(produtos.router)
app.include_router(importar.router)
app.include_router(cotacoes.router)
app.include_router(negociacao.router)
app.include_router(calculadora.router)
app.include_router(configuracoes.router)
app.include_router(relatorios.router)
app.include_router(admin.router)
app.include_router(workflow.router)
app.include_router(vendas.router)
app.include_router(crm.router)
app.include_router(relatorios_comerciais.router)
app.include_router(usuarios.router)


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
    """Healthcheck **público e mínimo**: o app subiu e o banco responde.

    Não devolve versão de migration, caminho de arquivo nem contagem de dados. Um endpoint
    aberto que descreve a instalação é reconhecimento gratuito para quem estiver sondando —
    e o detalhe existe em `/health/detalhe`, atrás de autenticação.
    """
    from sqlalchemy import text

    from app.db import engine

    try:
        with engine.connect() as conexao:
            conexao.execute(text("select 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:                               # noqa: BLE001
        # A causa vai para o log do servidor, não para a resposta.
        return JSONResponse({"status": "degradado", "database": "erro"}, status_code=503)

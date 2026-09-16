"""Autorização — um lugar só, e ele é o backend.

Esconder um campo no HTML não é autorização: quem recebeu o payload pode lê-lo no devtools.
Por isso toda decisão de acesso mora aqui e é aplicada **antes** de a resposta ser montada,
nunca depois, na tela.

Três funções resolvem quase tudo:

    usuario_da_request(request)   quem está falando (ou None)
    exigir(request, ...)          barra quem não pode — 403, não uma página capenga
    ve_economia(request)          o payload leva custo/margem/lucro, ou não

O default é **negar**. Papel desconhecido, usuário inativo, sessão vencida ou request sem
usuário caem todos no mesmo lugar: sem economia, sem administração.
"""
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import PAPEIS_ADMINISTRATIVOS, PAPEIS_ECONOMICOS, Papel, Usuario


class PrecisaLogin(HTTPException):
    """401 semântico: não há sessão. Quem trata devolve o redirect para o login."""

    def __init__(self, destino: str = "/"):
        super().__init__(status_code=401, detail="Autenticação necessária")
        self.destino = destino


def usuario_da_request(request: Request) -> Optional[Usuario]:
    """O usuário que o middleware já resolveu do cookie. `None` se não houver sessão."""
    return getattr(request.state, "usuario", None) if request is not None else None


# ---------------------------------------------------------------------------
# Predicados — a matriz de acesso, escrita uma vez
# ---------------------------------------------------------------------------
def autenticado(request: Request) -> bool:
    u = usuario_da_request(request)
    return bool(u and u.ativo)


def ve_economia(request: Request) -> bool:
    """Pode receber custo, CNET, EXW, margem, lucro, markup e a memória do preço.

    OWNER e ADMIN. Os dois papéis de vendedor, não. Desde 16/09/2026 (Fase 3A) a vendedora
    vê a **comissão estimada da cotação** (R$ e taxa efetiva) — decisão deliberada da
    política comercial —, mas continua sem ver a comissão por item, o piso, a margem e a
    mecânica de proteção; esses seguem sendo economia.
    """
    u = usuario_da_request(request)
    return bool(u and u.ativo and u.papel in PAPEIS_ECONOMICOS)


def administra(request: Request) -> bool:
    """Pode alterar premissa, custo, fiscal, frete, margem e importar base."""
    u = usuario_da_request(request)
    return bool(u and u.ativo and u.papel in PAPEIS_ADMINISTRATIVOS)


def gerencia_economia(request: Request) -> bool:
    """Pode versionar premissa econômica — custo, câmbio, margem, encargo.

    É mais estreito que `administra`: ver a economia e poder alterá-la são permissões
    distintas, e a segunda é a que muda o preço de amanhã.
    """
    u = usuario_da_request(request)
    return bool(u and u.gerencia_economia)


def gerencia_usuarios(request: Request) -> bool:
    u = usuario_da_request(request)
    return bool(u and u.gerencia_usuarios)


def aprova_cotacoes(request: Request) -> bool:
    """Tem alçada para decidir sobre exceção comercial.

    **Independente de `ve_economia` e de `gerencia_economia`**, e é isso que importa aqui.
    Ver o custo é uma permissão; versionar a premissa é outra; autorizar abrir mão de
    receita numa venda específica é uma terceira. Quem mantém o cadastro do câmbio não
    herda, por isso, o direito de aprovar um desconto.

    Serve para a **navegação** — a fila é o painel de quem decide, e anunciá-la a quem não
    decide oferece uma tela que não leva a lugar nenhum. A autorização de verdade continua
    em `workflow_service._exigir_alcada`, no servidor, e a fila permanece legível em modo
    consulta para os papéis econômicos: ela mostra preço e margem, e há quem precise
    acompanhar sem decidir.
    """
    u = usuario_da_request(request)
    return bool(u and u.aprova_cotacoes)


def opera_cotacao(request: Request) -> bool:
    """Pode montar e editar cotação. Todos os papéis autenticados podem — o que muda entre
    eles não é o direito de cotar, é o que a resposta carrega de volta."""
    return autenticado(request)


# ---------------------------------------------------------------------------
# Barreiras — usadas nos endpoints
# ---------------------------------------------------------------------------
def exigir_autenticado(request: Request) -> Usuario:
    u = usuario_da_request(request)
    if not u or not u.ativo:
        raise PrecisaLogin(destino=str(request.url.path) if request else "/")
    return u


def exigir_economia(request: Request) -> Usuario:
    """Barra o vendedor em endpoint que **só existe** para expor economia.

    A distinção importa: um endpoint comercial com campos confidenciais é filtrado; um
    endpoint cuja razão de existir é a memória do preço é **negado**. Devolver a memória
    "sem os números" seria uma página que finge funcionar.
    """
    u = exigir_autenticado(request)
    if u.papel not in PAPEIS_ECONOMICOS:
        raise HTTPException(status_code=403,
                            detail="Este conteúdo é restrito a administradores.")
    return u


def exigir_admin(request: Request) -> Usuario:
    u = exigir_autenticado(request)
    if u.papel not in PAPEIS_ADMINISTRATIVOS:
        raise HTTPException(status_code=403,
                            detail="Ação restrita a administradores.")
    return u


def exigir_economia_gerenciavel(request: Request) -> Usuario:
    """Barreira das rotas que **alteram** premissa econômica."""
    u = exigir_autenticado(request)
    if not u.gerencia_economia:
        raise HTTPException(
            status_code=403,
            detail="Alterar premissa econômica exige permissão de gestão econômica.")
    return u


def exigir_papel(request: Request, *papeis: str) -> Usuario:
    """Barreira explícita por papel, para o caso que as três acima não cobrem."""
    u = exigir_autenticado(request)
    alvos = {p.value if isinstance(p, Papel) else str(p) for p in papeis}
    if u.papel not in alvos:
        raise HTTPException(status_code=403, detail="Ação não permitida para este papel.")
    return u


def resposta_de_negacao(exc: HTTPException):
    """401 vira redirect para o login; 403 continua 403.

    A diferença é a que o usuário precisa entender: "faça login" é acionável; "você não tem
    permissão" é definitivo. Mandar um 403 para o login faria o usuário logado dar voltas.
    """
    if isinstance(exc, PrecisaLogin):
        destino = getattr(exc, "destino", "/") or "/"
        return RedirectResponse(url=f"/login?next={destino}", status_code=303)
    return None

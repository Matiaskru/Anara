"""Autenticação simples: uma senha compartilhada, sem cadastro de usuário."""
import hashlib
import hmac
import os

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

SENHA = "[SENHA-LEGADA-REMOVIDA]"
SECRET_KEY = os.environ.get("ANARA_SECRET_KEY", "[SECRET-LEGADO-REMOVIDO]")
COOKIE_NAME = "anara_session"

_serializer = URLSafeSerializer(SECRET_KEY, salt="anara-auth")


def senha_confere(tentativa: str) -> bool:
    return hmac.compare_digest(tentativa or "", SENHA)


def criar_cookie_valor() -> str:
    return _serializer.dumps({"ok": True})


def cookie_valido(valor: str) -> bool:
    if not valor:
        return False
    try:
        dados = _serializer.loads(valor)
        return bool(dados.get("ok"))
    except BadSignature:
        return False


def usuario_autenticado(request: Request) -> bool:
    return cookie_valido(request.cookies.get(COOKIE_NAME, ""))


def exigir_login(request: Request):
    """Dependency: redireciona pra /login se não estiver autenticado."""
    if not usuario_autenticado(request):
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
    return None

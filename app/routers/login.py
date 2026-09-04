"""Login, logout e a sessão do usuário.

A senha compartilhada saiu na Sessão 4. Agora é e-mail + senha por pessoa, com o hash argon2
guardado no banco e o cookie carregando identidade.

Duas escolhas que parecem detalhe e não são:

* **a mensagem de erro é a mesma** para e-mail inexistente, senha errada e conta desativada.
  Distinguir "esse e-mail não existe" de "senha errada" entrega ao atacante metade do
  trabalho: a lista de quem tem conta;
* **a verificação do hash roda mesmo quando o e-mail não existe.** Sem isso, um e-mail
  inválido responderia em microssegundos e um válido em ~50 ms — e o tempo de resposta
  viraria o mesmo oráculo que a mensagem genérica evita.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.auth import aplicar_cookie, hash_senha, limpar_cookie, senha_confere
from app.db import get_session
from app.models import Usuario
from app.templating import templates

router = APIRouter()

MENSAGEM_GENERICA = "E-mail ou senha inválidos."

#: Hash descartável, usado só para gastar o mesmo tempo quando o e-mail não existe.
_HASH_ISCA = hash_senha("hash-de-comparacao-para-tempo-constante")


def _destino_seguro(destino: str) -> str:
    """Só redireciona para caminho interno.

    `next=https://sitedeoutro/` num link de phishing transformaria a tela de login da Anara
    em trampolim para outro domínio. Caminho relativo, começando com uma barra só.
    """
    d = (destino or "/").strip()
    if not d.startswith("/") or d.startswith("//"):
        return "/"
    return d


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html",
                                      {"erro": None, "next": _destino_seguro(next)})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(""), senha: str = Form(""),
                 next: str = Form("/"), session: Session = Depends(get_session)):
    destino = _destino_seguro(next)
    alvo = (email or "").strip().lower()
    usuario = session.exec(select(Usuario).where(Usuario.email == alvo)).first() if alvo else None

    # tempo constante: sem usuário, verifica contra o hash-isca e falha do mesmo jeito
    confere = senha_confere(senha, usuario.senha_hash if usuario else _HASH_ISCA)

    if usuario is None or not usuario.ativo or not confere:
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": MENSAGEM_GENERICA, "next": destino, "email": alvo},
            status_code=401)

    usuario.ultimo_login_em = datetime.utcnow()
    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    resp = RedirectResponse(url=destino, status_code=303)
    return aplicar_cookie(resp, usuario.id, usuario.sessao_versao)


@router.get("/logout")
def logout():
    return limpar_cookie(RedirectResponse(url="/login", status_code=303))

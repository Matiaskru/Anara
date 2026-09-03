from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import COOKIE_NAME, criar_cookie_valor, senha_confere
from app.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"erro": None, "next": next})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, senha: str = Form(...), next: str = Form("/")):
    if not senha_confere(senha):
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": "Senha incorreta.", "next": next},
            status_code=401,
        )
    resp = RedirectResponse(url=next or "/", status_code=303)
    resp.set_cookie(COOKIE_NAME, criar_cookie_valor(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp

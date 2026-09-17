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
from app.models import Papel, Usuario
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
def login_form(request: Request, next: str = "/",
               session: Session = Depends(get_session)):
    # Banco sem nenhuma conta: não existe senha para acertar. Mostrar o formulário de login
    # seria oferecer uma porta que não abre — o caminho honesto é a criação do OWNER.
    if _sem_usuarios(session):
        return RedirectResponse(url="/primeiro-acesso", status_code=303)
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

    resp = RedirectResponse(url=landing(usuario, destino), status_code=303)
    return aplicar_cookie(resp, usuario.id, usuario.sessao_versao)


def landing(usuario, destino: str = "/") -> str:
    """Onde a pessoa cai depois de entrar. **O papel decide, não a pessoa.**

    Vendedora → `/vendas`, a superfície do trabalho dela. OWNER/ADMIN → `/dashboard`. Um
    `next` explícito para outra tela continua valendo (o login foi pedido a partir dela);
    só a raiz é trocada — e a raiz também redireciona por papel (`/` → `/vendas` para a
    vendedora, `/` → `/dashboard` para quem vê economia).
    """
    from app.models import PAPEIS_ECONOMICOS
    if destino in ("", "/"):
        return "/dashboard" if usuario.papel in PAPEIS_ECONOMICOS else "/vendas"
    return destino


@router.get("/logout")
def logout():
    return limpar_cookie(RedirectResponse(url="/login", status_code=303))


# ---------------------------------------------------------------------------
# Primeiro acesso — criação do OWNER inicial
# ---------------------------------------------------------------------------
#: Mesmo mínimo do bootstrap por linha de comando. Sem política de complexidade: regra que
#: exige símbolo e maiúscula produz senha anotada em post-it, não senha forte.
SENHA_MINIMA = 8


def _sem_usuarios(session: Session) -> bool:
    """Instalação virgem — nenhuma conta existe ainda.

    É o **único** portão do primeiro acesso, e ele fecha sozinho: no instante em que a
    primeira conta é gravada, a rota deixa de abrir. Não há flag para lembrar de desligar
    nem variável de ambiente que alguém possa esquecer ligada.
    """
    return session.exec(select(Usuario)).first() is None


@router.get("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso_form(request: Request, session: Session = Depends(get_session)):
    """Tela de criação do primeiro OWNER.

    Existe porque a alternativa era o prompt sem eco do `criar_usuario.py` — que funciona,
    mas em terminal que não devolve o que se digita falha **em silêncio**, e o sistema fica
    inacessível sem nenhuma mensagem que explique o motivo.
    """
    if not _sem_usuarios(session):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "primeiro_acesso.html",
                                      {"erro": None, "nome": "", "email": ""})


@router.post("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso_criar(request: Request, nome: str = Form(""), email: str = Form(""),
                          senha: str = Form(""), confirmar: str = Form(""),
                          session: Session = Depends(get_session)):
    """Cria o OWNER e já entrega a sessão.

    A checagem de banco vazio é refeita **aqui**, e não herdada do GET: entre abrir o
    formulário e enviá-lo, alguém pode ter criado a primeira conta pelo `criar_usuario.py`.
    Sem esta segunda checagem o POST seria uma rota pública e permanente de criação de
    OWNER — exatamente o que o portão existe para impedir.
    """
    if not _sem_usuarios(session):
        return RedirectResponse(url="/login", status_code=303)

    nome_limpo = (nome or "").strip()
    alvo = (email or "").strip().lower()

    def recusar(motivo: str):
        # A senha nunca volta para o formulário — nem no HTML, nem no log.
        return templates.TemplateResponse(
            request, "primeiro_acesso.html",
            {"erro": motivo, "nome": nome_limpo, "email": alvo}, status_code=400)

    if not nome_limpo:
        return recusar("Informe seu nome.")
    if "@" not in alvo or "." not in alvo.split("@")[-1]:
        return recusar("Informe um e-mail válido.")
    if len(senha) < SENHA_MINIMA:
        return recusar(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    if senha != confirmar:
        return recusar("As duas senhas não são iguais.")

    usuario = Usuario(
        email=alvo, nome=nome_limpo, papel=Papel.owner.value,
        senha_hash=hash_senha(senha), can_manage_users=True,
        criado_por="primeiro-acesso",
    )
    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    resp = RedirectResponse(url="/", status_code=303)
    return aplicar_cookie(resp, usuario.id, usuario.sessao_versao)


# ---------------------------------------------------------------------------
# Esqueci minha senha / redefinir senha — por token, uso único, hash no banco
# ---------------------------------------------------------------------------
from app import recuperacao_senha as rs  # noqa: E402


def _base_url(request: Request) -> str:
    """Onde o link de redefinição aponta. `ANARA_BASE_URL` manda; senão, a origem do pedido."""
    import os
    configurada = os.environ.get("ANARA_BASE_URL", "").strip()
    if configurada:
        return configurada
    base = getattr(request, "base_url", None)
    return str(base).rstrip("/") if base else "http://127.0.0.1:8420"


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha_form(request: Request):
    return templates.TemplateResponse(request, "esqueci_senha.html", {"enviado": False})


@router.post("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha_submit(request: Request, email: str = Form(""),
                         session: Session = Depends(get_session)):
    """Sempre a mesma resposta, exista a conta ou não — e o mesmo tempo de resposta.

    Se a conta existe e está ativa, o token nasce (só o hash fica no banco) e o e-mail sai
    pelo backend configurado. Se não existe, nada acontece — e a tela não diz isso.
    """
    alvo = (email or "").strip().lower()
    usuario = session.exec(select(Usuario).where(Usuario.email == alvo)).first() if alvo else None
    if usuario is not None and usuario.ativo:
        try:
            rs.enviar_instrucoes(session, usuario, _base_url(request), finalidade=rs.RESET,
                                 criado_por="esqueci-senha")
            session.commit()
        except Exception:                                # noqa: BLE001
            # e-mail indisponível (produção sem SMTP) ou falha de envio: a resposta ao
            # usuário continua genérica; a causa vai para o log, não para a tela
            session.rollback()
            import logging
            logging.getLogger("anara.mail").exception("falha ao enviar redefinição de senha")
    else:
        # mesmo custo de tempo de quem tem conta: gera e descarta um token
        hash_senha("hash-de-comparacao-para-tempo-constante")
    return templates.TemplateResponse(request, "esqueci_senha.html",
                                      {"enviado": True, "mensagem": rs.RESPOSTA_GENERICA})


@router.get("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha_form(request: Request, token: str = "",
                         session: Session = Depends(get_session)):
    valido = rs.validar(session, token) is not None
    return templates.TemplateResponse(request, "redefinir_senha.html",
                                      {"token": token if valido else "", "valido": valido,
                                       "erro": None, "concluido": False}, status_code=200 if valido else 400)


@router.post("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha_submit(request: Request, token: str = Form(""), senha: str = Form(""),
                           confirmar: str = Form(""), session: Session = Depends(get_session)):
    def recusar(motivo: str, valido: bool = True, status: int = 400):
        return templates.TemplateResponse(
            request, "redefinir_senha.html",
            {"token": token if valido else "", "valido": valido, "erro": motivo,
             "concluido": False}, status_code=status)

    if rs.validar(session, token) is None:
        return recusar("Este link de redefinição não é válido ou já expirou. Peça um novo.",
                       valido=False)
    if len(senha) < SENHA_MINIMA:
        return recusar(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    if senha != confirmar:
        return recusar("As duas senhas não são iguais.")
    try:
        rs.consumir(session, token, senha)
    except rs.TokenInvalido as erro:
        session.rollback()
        return recusar(str(erro), valido=False)
    session.commit()
    return templates.TemplateResponse(request, "redefinir_senha.html",
                                      {"token": "", "valido": True, "erro": None, "concluido": True})

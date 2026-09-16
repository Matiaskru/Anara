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
    """Onde a pessoa cai depois de entrar (Fase 3B).

    Vendedora → **Vendas**, a superfície do trabalho dela. OWNER/ADMIN → o dashboard atual
    (o novo é da Fase 3C). Um `next` explícito para outra tela continua valendo; só a raiz
    é trocada — e, para a vendedora, a raiz também redireciona (`/` → `/vendas`).
    """
    from app.models import PAPEIS_ECONOMICOS
    if destino in ("", "/") and usuario.papel not in PAPEIS_ECONOMICOS:
        return "/vendas"
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

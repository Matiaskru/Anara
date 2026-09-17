"""Gestão de usuários — a tela mínima que faltava.

Até aqui, criar acesso, trocar papel ou desativar alguém só era possível por
`scripts/criar_usuario.py`, no terminal. Para tarefa recorrente de dono de operação isso é
barreira, não segurança.

## O que esta tela **não** faz, de propósito

Sem SSO, sem OAuth, sem convite por e-mail, sem 2FA, sem recuperação automática de senha.
Cada um desses traz um fluxo de confiança próprio, e nenhum é necessário para um sistema que
roda em `127.0.0.1` com meia dúzia de pessoas.

## As três regras que a tela não pode afrouxar

**Papel e flags são coisas diferentes.** `can_manage_users`, `can_manage_economics` e
`can_approve_quotes` continuam independentes entre si e do papel. Gerir gente não concede
gerir número; versionar premissa não concede aprovar desconto.

**Flag não promove ninguém.** Ligar `can_approve_quotes` num vendedor não lhe dá alçada — as
propriedades do modelo só consultam as flags dentro de ADMIN, e a tela recusa a combinação
em vez de gravar algo que não terá efeito e confundiria quem lê depois.

**Desativar não apaga.** Some do acesso, e o histórico da pessoa continua inteiro. Toda
troca de senha e toda desativação incrementam `sessao_versao`, o que derruba na hora os
cookies já emitidos — sem isso, alguém desativado continuaria navegando até o cookie vencer.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

import secrets

from app import admin_service as adm
from app import recuperacao_senha as rs
from app.auth import hash_senha
from app.db import get_session
from app.routers.login import _base_url
from app.models import Papel, Usuario
from app.permissoes import exigir_autenticado, gerencia_usuarios, usuario_da_request
from app.templating import pagina_de_erro, templates

router = APIRouter()

#: Flags que só têm efeito dentro de ADMIN. OWNER já pode tudo por propriedade do modelo, e
#: vendedor não é promovido por flag nenhuma.
FLAGS_DE_ADMIN = ("can_manage_users", "can_manage_economics", "can_approve_quotes")

PAPEIS_QUE_ACEITAM_FLAG = {Papel.owner.value, Papel.admin.value}

ROTULO_FLAG = {
    "can_manage_users": "Gerenciar usuários",
    "can_manage_economics": "Alterar premissas econômicas",
    "can_approve_quotes": "Aprovar descontos",
}


def _exigir_gestor(request: Request) -> Usuario:
    """Só quem gerencia usuários entra. OWNER sempre; ADMIN conforme a flag."""
    exigir_autenticado(request)
    if not gerencia_usuarios(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=403,
                            detail="Gerenciar usuários exige permissão própria.")
    return usuario_da_request(request)


def _erro(request, motivos, voltar="/admin/usuarios"):
    return pagina_de_erro(request, titulo="Não foi possível salvar",
                          motivos=motivos, voltar=voltar,
                          rotulo_voltar="Voltar para usuários", status_code=400)


@router.get("/admin/usuarios", response_class=HTMLResponse)
def listar(request: Request, session: Session = Depends(get_session)):
    _exigir_gestor(request)
    usuarios = session.exec(select(Usuario).order_by(Usuario.nome)).all()
    return templates.TemplateResponse(request, "admin_usuarios.html", {
        "active": "admin", "usuarios": usuarios,
        "papeis": [p.value for p in Papel],
        "flags": [(c, ROTULO_FLAG[c]) for c in FLAGS_DE_ADMIN],
        "eu": usuario_da_request(request),
    })


@router.post("/admin/usuarios")
def criar(request: Request, nome: str = Form(""), email: str = Form(""),
          papel: str = Form(""), senha: str = Form(""),
          session: Session = Depends(get_session)):
    _exigir_gestor(request)
    alvo = (email or "").strip().lower()
    nome_limpo = (nome or "").strip()

    if not nome_limpo:
        return _erro(request, ["Informe o nome da pessoa."])
    if "@" not in alvo or "." not in alvo.split("@")[-1]:
        return _erro(request, ["Informe um e-mail válido."])
    if papel not in {p.value for p in Papel}:
        return _erro(request, ["Escolha um papel."])
    if senha and len(senha) < 8:
        return _erro(request, ["A senha inicial precisa de pelo menos 8 caracteres."])
    if session.exec(select(Usuario).where(Usuario.email == alvo)).first():
        return _erro(request, [f"Já existe um acesso com o e-mail {alvo}."])

    ator = usuario_da_request(request)
    # Sem senha informada, a conta nasce com um hash que ninguém conhece (segredo aleatório
    # descartado) e a pessoa define a própria senha pelo link de primeiro acesso — a mesma
    # infraestrutura do "Esqueci minha senha". O gestor nunca combina senha com ninguém.
    novo = Usuario(email=alvo, nome=nome_limpo, papel=papel,
                   senha_hash=hash_senha(senha) if senha else hash_senha(secrets.token_urlsafe(32)),
                   criado_por=getattr(ator, "email", None))
    session.add(novo)
    session.flush()
    adm.registrar(session, ator=ator, acao="CREATE_USER", entidade="Usuario", entidade_id=novo.id,
                  escopo=alvo, depois=f"{papel} · {'senha inicial definida' if senha else 'primeiro acesso por link'}",
                  origem="admin-ui")
    resultado = "criado"
    if not senha:
        try:
            rs.enviar_instrucoes(session, novo, _base_url(request), finalidade=rs.PRIMEIRO_ACESSO,
                                 ator=ator)
            resultado = "criado_link"
        except Exception:                                # noqa: BLE001
            import logging
            logging.getLogger("anara.mail").exception("falha ao enviar primeiro acesso")
            resultado = "criado_sem_email"
    session.commit()
    return RedirectResponse(url=f"/admin/usuarios?ok={resultado}", status_code=303)


@router.post("/admin/usuarios/{usuario_id}/redefinir")
def iniciar_redefinicao(request: Request, usuario_id: int,
                        session: Session = Depends(get_session)):
    """Envia à pessoa um link de redefinição (30 minutos, uso único). Nenhuma senha passa
    pelo gestor: quem define a senha nova é quem vai usá-la."""
    ator = _exigir_gestor(request)
    alvo = session.get(Usuario, usuario_id)
    if alvo is None:
        return _erro(request, ["Usuário não encontrado."])
    if not alvo.ativo:
        return _erro(request, ["Reative o acesso antes de enviar a redefinição de senha."])
    try:
        backend = rs.enviar_instrucoes(session, alvo, _base_url(request), finalidade=rs.RESET,
                                       ator=ator)
    except Exception:                                    # noqa: BLE001
        session.rollback()
        import logging
        logging.getLogger("anara.mail").exception("falha ao enviar redefinição")
        return _erro(request, ["Não foi possível enviar o e-mail de redefinição. O envio de "
                               "e-mail não está configurado neste ambiente."])
    session.commit()
    return RedirectResponse(url=f"/admin/usuarios?ok={'redefinicao' if backend != 'dev' else 'redefinicao_dev'}",
                            status_code=303)


@router.post("/admin/usuarios/{usuario_id}/papel")
def mudar_papel(request: Request, usuario_id: int, papel: str = Form(""),
                session: Session = Depends(get_session)):
    """Troca o papel e limpa as flags que aquele papel não comporta.

    Rebaixar um ADMIN para vendedor sem limpar as flags deixaria linhas dizendo que ele
    aprova desconto — coisa que o modelo não honra e que quem lesse a tela acreditaria.
    """
    ator = _exigir_gestor(request)
    alvo = session.get(Usuario, usuario_id)
    if alvo is None:
        return _erro(request, ["Usuário não encontrado."])
    if papel not in {p.value for p in Papel}:
        return _erro(request, ["Papel inválido."])
    if alvo.papel == Papel.owner.value and papel != Papel.owner.value:
        if len(_owners_ativos(session)) <= 1:
            return _erro(request, [
                "Este é o único dono ativo. Promova outra pessoa antes de rebaixá-lo — "
                "sem nenhum dono, ninguém consegue criar acesso."])

    alvo.papel = papel
    if papel not in PAPEIS_QUE_ACEITAM_FLAG:
        for flag in FLAGS_DE_ADMIN:
            setattr(alvo, flag, False)
    alvo.sessao_versao += 1
    session.add(alvo)
    session.commit()
    return RedirectResponse(url="/admin/usuarios?ok=papel", status_code=303)


@router.post("/admin/usuarios/{usuario_id}/permissao")
def mudar_permissao(request: Request, usuario_id: int, flag: str = Form(""),
                    valor: str = Form(""), session: Session = Depends(get_session)):
    _exigir_gestor(request)
    alvo = session.get(Usuario, usuario_id)
    if alvo is None or flag not in FLAGS_DE_ADMIN:
        return _erro(request, ["Permissão desconhecida."])
    if alvo.papel not in PAPEIS_QUE_ACEITAM_FLAG:
        return _erro(request, [
            f"'{ROTULO_FLAG[flag]}' só vale para dono ou administrador. "
            f"Mude o papel de {alvo.nome} antes."])

    setattr(alvo, flag, valor == "sim")
    session.add(alvo)
    session.commit()
    return RedirectResponse(url="/admin/usuarios?ok=permissao", status_code=303)


@router.post("/admin/usuarios/{usuario_id}/situacao")
def mudar_situacao(request: Request, usuario_id: int, ativo: str = Form(""),
                   session: Session = Depends(get_session)):
    ator = _exigir_gestor(request)
    alvo = session.get(Usuario, usuario_id)
    if alvo is None:
        return _erro(request, ["Usuário não encontrado."])

    ligar = (ativo == "sim")
    if not ligar:
        if alvo.id == getattr(ator, "id", None):
            return _erro(request, ["Você não pode desativar o próprio acesso."])
        if alvo.papel == Papel.owner.value and len(_owners_ativos(session)) <= 1:
            return _erro(request, ["Este é o único dono ativo. Desativá-lo deixaria o "
                                   "sistema sem ninguém capaz de criar acesso."])

    alvo.ativo = ligar
    alvo.sessao_versao += 1        # derruba os cookies já emitidos
    session.add(alvo)
    session.commit()
    return RedirectResponse(url="/admin/usuarios?ok=situacao", status_code=303)


@router.post("/admin/usuarios/{usuario_id}/senha")
def trocar_senha(request: Request, usuario_id: int, senha: str = Form(""),
                 session: Session = Depends(get_session)):
    """Define uma senha nova para alguém. A anterior não é lida nem exibida em lugar nenhum."""
    _exigir_gestor(request)
    alvo = session.get(Usuario, usuario_id)
    if alvo is None:
        return _erro(request, ["Usuário não encontrado."])
    if len(senha) < 8:
        return _erro(request, ["A senha precisa de pelo menos 8 caracteres."])

    alvo.senha_hash = hash_senha(senha)
    alvo.sessao_versao += 1        # sessões abertas com a senha antiga deixam de valer
    session.add(alvo)
    rs.encerrar_ativos(session, alvo.id)      # links de redefinição pendentes caem junto
    adm.registrar(session, ator=usuario_da_request(request), acao="SET_PASSWORD",
                  entidade="Usuario", entidade_id=alvo.id, escopo=alvo.email,
                  depois="senha definida pelo gestor", origem="admin-ui")
    session.commit()
    return RedirectResponse(url="/admin/usuarios?ok=senha", status_code=303)


def _owners_ativos(session: Session):
    return session.exec(select(Usuario)
                        .where(Usuario.papel == Papel.owner.value)
                        .where(Usuario.ativo == True)).all()  # noqa: E712

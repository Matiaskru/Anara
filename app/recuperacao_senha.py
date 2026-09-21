"""Redefinição e definição de senha por token — um mecanismo só, com uma regra só.

    gerar(session, usuario, finalidade, ator)      → token PURO (vai uma vez para o e-mail)
    validar(session, token)                        → PasswordResetToken vivo, ou None
    consumir(session, token, nova_senha)           → troca a senha pelo hash canônico

O que fica no banco é `sha256(token)`. O token puro tem 32 bytes aleatórios
(`secrets.token_urlsafe`). Validade: 30 minutos para "esqueci minha senha"; 48 horas para
o primeiro acesso de uma conta nova (a pessoa pode não abrir o e-mail na hora). Uso único.
Gerar um token novo encerra os anteriores ainda ativos da mesma pessoa. Trocar a senha
incrementa `sessao_versao` (derruba os cookies abertos) e registra em `AuditLog` — sem
senha e sem token, nem em hash.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app import admin_service as adm
from app import mail
from app.auth import hash_senha
from app.models import PasswordResetToken, Usuario

RESET = "reset"
PRIMEIRO_ACESSO = "primeiro_acesso"
VALIDADE = {RESET: timedelta(minutes=30), PRIMEIRO_ACESSO: timedelta(hours=48)}

#: Resposta da tela "Esqueci minha senha" — a mesma para e-mail existente e inexistente.
RESPOSTA_GENERICA = ("Se existir uma conta com este e-mail, enviaremos as instruções para "
                     "redefinir a senha.")


class TokenInvalido(ValueError):
    """Token ausente, desconhecido, vencido ou já usado. A mensagem é a mesma para todos."""


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def encerrar_ativos(session: Session, usuario_id: int, agora: Optional[datetime] = None) -> int:
    """Marca como usados os tokens ainda válidos da pessoa. Um token de cada vez."""
    agora = agora or datetime.utcnow()
    n = 0
    for t in session.exec(select(PasswordResetToken)
                          .where(PasswordResetToken.usuario_id == usuario_id)
                          .where(PasswordResetToken.usado_em == None)).all():   # noqa: E711
        if t.expira_em > agora:
            t.usado_em = agora
            session.add(t)
            n += 1
    return n


def gerar(session: Session, usuario: Usuario, finalidade: str = RESET, *,
          ator: Optional[Usuario] = None, criado_por: Optional[str] = None,
          agora: Optional[datetime] = None) -> str:
    """Cria o token, grava só o hash e devolve o token puro — para ir ao e-mail e sumir."""
    if finalidade not in VALIDADE:
        raise ValueError(f"finalidade desconhecida: {finalidade}")
    agora = agora or datetime.utcnow()
    encerrar_ativos(session, usuario.id, agora)
    token = secrets.token_urlsafe(32)
    session.add(PasswordResetToken(
        usuario_id=usuario.id, token_hash=_hash(token), finalidade=finalidade,
        criado_em=agora, expira_em=agora + VALIDADE[finalidade],
        criado_por=criado_por or getattr(ator, "email", None) or "esqueci-senha"))
    adm.registrar(session, ator=ator, acao="PASSWORD_RESET_TOKEN", entidade="Usuario",
                  entidade_id=usuario.id, escopo=usuario.email,
                  depois=f"{finalidade} · expira em {VALIDADE[finalidade]}",
                  origem="auth")
    session.flush()
    return token


def validar(session: Session, token: str, agora: Optional[datetime] = None
            ) -> Optional[PasswordResetToken]:
    """O registro do token, se ele existe, não foi usado, não venceu e a conta está ativa."""
    if not token:
        return None
    agora = agora or datetime.utcnow()
    registro = session.exec(select(PasswordResetToken)
                            .where(PasswordResetToken.token_hash == _hash(token))).first()
    if registro is None or registro.usado_em is not None or registro.expira_em <= agora:
        return None
    usuario = session.get(Usuario, registro.usuario_id)
    if usuario is None or not usuario.ativo:
        return None
    return registro


def consumir(session: Session, token: str, nova_senha: str, *,
             agora: Optional[datetime] = None) -> Usuario:
    """Troca a senha pelo mecanismo canônico e queima o token. Uso único, sem exceção."""
    agora = agora or datetime.utcnow()
    registro = validar(session, token, agora)
    if registro is None:
        raise TokenInvalido("Este link de redefinição não é válido ou já expirou. "
                            "Peça um novo em Esqueci minha senha.")
    usuario = session.get(Usuario, registro.usuario_id)
    usuario.senha_hash = hash_senha(nova_senha)          # valida o mínimo de 8 caracteres
    usuario.sessao_versao += 1                            # derruba cookies antigos
    registro.usado_em = agora
    session.add(usuario)
    session.add(registro)
    encerrar_ativos(session, usuario.id, agora)           # nenhum outro link continua vivo
    adm.registrar(session, ator=usuario, acao="PASSWORD_RESET", entidade="Usuario",
                  entidade_id=usuario.id, escopo=usuario.email,
                  depois=f"senha redefinida ({registro.finalidade})", origem="auth")
    session.flush()
    return usuario


#: Perfis de acesso como a pessoa os vê no primeiro acesso. O papel técnico (OWNER, ADMIN,
#: VENDEDOR_*) é decidido pelo gestor ao criar a conta; a tela só CONFIRMA o perfil — quem
#: escolher "Administrativo" sem ter sido autorizado é recusado, e ninguém vira OWNER por
#: autoatendimento.
PERFIL_VENDEDORA = "vendedora"
PERFIL_ADMINISTRATIVO = "administrativo"
PERFIS = {PERFIL_VENDEDORA: "Vendedora", PERFIL_ADMINISTRATIVO: "Administrativo"}


def perfil_autorizado(usuario: Usuario) -> str:
    """O perfil que o gestor autorizou para esta conta — derivado do papel, nunca digitado."""
    if usuario.papel in ("OWNER", "ADMIN"):
        return PERFIL_ADMINISTRATIVO
    return PERFIL_VENDEDORA


class PerfilNaoAutorizado(ValueError):
    """A pessoa escolheu um perfil diferente do que o gestor autorizou para a conta."""


def conferir_perfil(usuario: Usuario, perfil_escolhido: Optional[str]) -> str:
    """Aceita o perfil escolhido só se for o autorizado. Não altera papel nenhum.

    `None`/vazio significa "confirmo o que foi autorizado" e passa. Qualquer outro valor é
    recusado com mensagem amigável — o caminho para mudar de perfil é o gestor, em
    Administração › Usuários.
    """
    autorizado = perfil_autorizado(usuario)
    escolhido = (perfil_escolhido or "").strip().lower()
    if not escolhido or escolhido == autorizado:
        return autorizado
    if escolhido not in PERFIS:
        raise PerfilNaoAutorizado("Perfil desconhecido. Confirme o perfil indicado na tela.")
    raise PerfilNaoAutorizado(
        f"Este acesso foi autorizado como {PERFIS[autorizado]}. Para ter acesso "
        f"{PERFIS[escolhido].lower()}, peça ao gestor da sua conta — a mudança é feita por "
        "quem administra os usuários, não no primeiro acesso.")


def link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/redefinir-senha?token={token}"


def enviar_instrucoes(session: Session, usuario: Usuario, base_url: str, *,
                      finalidade: str = RESET, ator: Optional[Usuario] = None,
                      criado_por: Optional[str] = None) -> str:
    """Gera o token e manda o e-mail. Devolve o backend usado (`smtp`, `dev`, `memoria`)."""
    token = gerar(session, usuario, finalidade, ator=ator, criado_por=criado_por)
    url = link(base_url, token)
    if finalidade == PRIMEIRO_ACESSO:
        assunto = "Anara — defina sua senha de acesso"
        corpo = (f"Olá, {usuario.nome}.\n\nSeu acesso ao sistema Anara foi criado. Defina sua "
                 f"senha por este link (válido por 48 horas):\n\n{url}\n\nSe você não esperava "
                 "este e-mail, ignore-o.")
    else:
        assunto = "Anara — redefinição de senha"
        corpo = (f"Olá, {usuario.nome}.\n\nRecebemos um pedido para redefinir a sua senha. "
                 f"Use este link, válido por 30 minutos:\n\n{url}\n\nSe não foi você, ignore "
                 "este e-mail: a sua senha continua a mesma.")
    return mail.enviar(usuario.email, assunto, corpo)

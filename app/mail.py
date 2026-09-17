"""Envio de e-mail — uma abstração pequena, configurada por ambiente.

    ANARA_MAIL_HOST, ANARA_MAIL_PORT, ANARA_MAIL_USER, ANARA_MAIL_PASSWORD,
    ANARA_MAIL_FROM, ANARA_MAIL_TLS (1/0)

Sem host configurado:

* em **desenvolvimento**, o backend DEV escreve a mensagem (com o link) **só no log local**
  (`anara.mail`) — nunca na página de quem pediu. Testes podem pedir o backend em memória
  com `ANARA_MAIL_BACKEND=memoria`, que guarda as mensagens em `ENVIADAS`;
* em **produção**, `enviar()` levanta `MailNaoConfigurado`, e o startup avisa que a
  recuperação de senha por e-mail não está operacional.

Nenhuma credencial mora aqui. Nenhuma integração externa específica: SMTP padrão.
"""
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import List

log = logging.getLogger("anara.mail")
# O uvicorn só configura os loggers dele; sem handler próprio, o INFO deste logger cairia no
# `lastResort` (WARNING+) e o link do backend DEV se perderia em silêncio.
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

#: Mensagens enviadas pelo backend em memória (só com ANARA_MAIL_BACKEND=memoria).
ENVIADAS: List[dict] = []


class MailNaoConfigurado(RuntimeError):
    """Produção sem SMTP: recuperação por e-mail não está operacional."""


def _cfg(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao).strip()


def configurado() -> bool:
    return bool(_cfg("ANARA_MAIL_HOST"))


def backend() -> str:
    """`smtp` | `memoria` | `dev` | `indisponivel`."""
    if _cfg("ANARA_MAIL_BACKEND").lower() == "memoria":
        return "memoria"
    if configurado():
        return "smtp"
    from app.auth import PRODUCAO
    return "indisponivel" if PRODUCAO else "dev"


def situacao() -> dict:
    b = backend()
    return {"backend": b, "operacional": b in ("smtp", "memoria"),
            "aviso": (None if b in ("smtp", "memoria")
                      else ("Recuperação de senha por e-mail NÃO está operacional: configure "
                            "ANARA_MAIL_HOST/PORT/USER/PASSWORD/FROM." if b == "indisponivel"
                            else "Sem SMTP configurado: em desenvolvimento o e-mail vai só para "
                                 "o log do servidor (anara.mail)."))}


def enviar(para: str, assunto: str, corpo: str) -> str:
    """Envia e devolve o backend usado. Levanta `MailNaoConfigurado` em produção sem SMTP."""
    b = backend()
    if b == "memoria":
        ENVIADAS.append({"para": para, "assunto": assunto, "corpo": corpo})
        return b
    if b == "dev":
        log.info("[MAIL-DEV] para=%s assunto=%s\n%s", para, assunto, corpo)
        return b
    if b == "indisponivel":
        raise MailNaoConfigurado(situacao()["aviso"])

    msg = EmailMessage()
    msg["From"] = _cfg("ANARA_MAIL_FROM") or _cfg("ANARA_MAIL_USER")
    msg["To"] = para
    msg["Subject"] = assunto
    msg.set_content(corpo)
    porta = int(_cfg("ANARA_MAIL_PORT", "587") or 587)
    usa_tls = _cfg("ANARA_MAIL_TLS", "1") not in ("0", "false", "nao", "não")
    with smtplib.SMTP(_cfg("ANARA_MAIL_HOST"), porta, timeout=20) as smtp:
        if usa_tls:
            smtp.starttls()
        usuario, senha = _cfg("ANARA_MAIL_USER"), _cfg("ANARA_MAIL_PASSWORD")
        if usuario:
            smtp.login(usuario, senha)
        smtp.send_message(msg)
    return b

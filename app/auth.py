"""Autenticação: identidade, senha e sessão.

Reescrito na Sessão 4. O que existia antes: **uma senha compartilhada em texto claro no
código** (uma constante `SENHA` de módulo), um `SECRET_KEY` com fallback fixo também no
código, e um cookie que carregava apenas `{"ok": True}` — sem identidade, sem papel, sem
expiração e sem como ser invalidado. Quem entrava era todo mundo, e o sistema não sabia quem
era.

O valor daquela senha **não é repetido aqui de propósito**. Ele continua no histórico do Git
(commits `413d6bd` e `165d75e`) e é isso que mantém a publicação remota bloqueada; copiá-lo
para o código atual só aumentaria a superfície, sem explicar nada que a frase acima já não
explique.

O que existe agora:

* **senha por usuário, guardada como hash argon2id.** O hash embute salt próprio: duas contas
  com a mesma senha produzem hashes diferentes, e nenhum deles volta a ser a senha;
* **segredo de sessão vindo do ambiente.** Sem `ANARA_SECRET_KEY`, o processo **não** cai num
  valor conhecido: em produção ele se recusa a subir, e em desenvolvimento gera uma chave
  aleatória por processo — que é segura, e cujo efeito colateral (a sessão não sobrevive ao
  restart) é justamente o lembrete de configurar a variável;
* **cookie assinado E datado**, carregando `id` do usuário e a versão da sessão dele. Trocar a
  senha ou desativar a conta incrementa `sessao_versao` e derruba todo cookie já emitido.

O cookie continua sendo **assinado, não criptografado**: o conteúdo é legível por quem o tem.
Por isso ele carrega só `id` e `versao` — nada de papel, nome ou e-mail. O papel é lido do
banco a cada request, senão bastaria editar o cookie para virar OWNER... o que a assinatura já
impediria, mas confiar na assinatura para transportar autorização é uma camada de risco que não
precisa existir.
"""
import os
import secrets
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "anara_session"
#: 12 horas. Um turno de trabalho — não os 30 dias que o cookie antigo concedia.
SESSAO_MAX_IDADE_S = 12 * 60 * 60

AMBIENTE = os.environ.get("ANARA_ENV", "desenvolvimento").strip().lower()
PRODUCAO = AMBIENTE in ("producao", "produção", "production", "prod")


class ConfiguracaoInsegura(RuntimeError):
    """Falta configuração de segurança obrigatória. Falhar aqui é o comportamento correto."""


#: Valores que já apareceram em documentação, `.env.example` ou demonstração. Um deles
#: em produção é o mesmo que um segredo publicado.
_PLACEHOLDERS = ("cole-aqui", "exemplo", "example", "changeme", "change-me", "troque",
                 "secret", "segredo", "demo-", "placeholder", "xxxxxxxx")


def chave_e_placeholder(chave: str) -> bool:
    """Rejeita o que não é segredo: texto de exemplo, repetição de um caractere, ou tão
    pouca variedade que se adivinha. Não substitui gerar a chave — só barra o óbvio."""
    baixa = chave.lower()
    if any(p in baixa for p in _PLACEHOLDERS):
        return True
    return len(set(chave)) < 12


def _resolver_secret() -> str:
    """Segredo de sessão. **Nunca** um default conhecido.

    Em produção, ausência de `ANARA_SECRET_KEY` é erro de deploy e o processo não sobe:
    subir com um segredo público significaria qualquer pessoa forjar o cookie de um OWNER.

    Em desenvolvimento, gera-se uma chave aleatória por processo. Ela é tão segura quanto a
    de produção; o que ela não é, é estável — reiniciar derruba as sessões. Isso é
    deliberado: o incômodo aponta para a variável que falta, em vez de esconder o problema.
    """
    chave = os.environ.get("ANARA_SECRET_KEY", "").strip()
    if chave:
        if len(chave) < 32:
            raise ConfiguracaoInsegura(
                "ANARA_SECRET_KEY tem menos de 32 caracteres. Gere uma com "
                "`python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"`.")
        if chave_e_placeholder(chave):
            raise ConfiguracaoInsegura(
                "ANARA_SECRET_KEY é um valor de exemplo ou de demonstração, não um segredo. "
                "Gere uma com `python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"`.")
        return chave
    if PRODUCAO:
        raise ConfiguracaoInsegura(
            "ANARA_SECRET_KEY não está definida e ANARA_ENV=producao. O sistema não sobe com "
            "segredo de sessão default — seria o mesmo que não ter assinatura. Gere uma com "
            "`python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"` e exporte a "
            "variável.")
    return secrets.token_urlsafe(48)


SECRET_KEY = _resolver_secret()
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="anara-auth-v2")


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------
def _hasher():
    from argon2 import PasswordHasher
    return PasswordHasher()


def hash_senha(senha: str) -> str:
    """Hash argon2id da senha. O salt vem embutido no próprio hash."""
    if not senha or len(senha) < 8:
        raise ValueError("A senha precisa de pelo menos 8 caracteres.")
    return _hasher().hash(senha)


def senha_confere(senha: str, senha_hash: str) -> bool:
    """Verifica a senha contra o hash. Qualquer falha é `False` — nunca exceção vazando.

    Não distingue "hash malformado" de "senha errada" de propósito: a resposta ao usuário é a
    mesma nos dois casos, e a diferença só serviria para quem está sondando.
    """
    if not senha or not senha_hash:
        return False
    try:
        from argon2.exceptions import VerificationError, VerifyMismatchError
        try:
            return bool(_hasher().verify(senha_hash, senha))
        except (VerifyMismatchError, VerificationError):
            return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sessão
# ---------------------------------------------------------------------------
def criar_cookie_valor(usuario_id: int, sessao_versao: int = 1) -> str:
    """Valor assinado e datado do cookie. Carrega identidade, nunca autorização."""
    return _serializer.dumps({"uid": int(usuario_id), "v": int(sessao_versao)})


def ler_cookie(valor: str) -> Optional[dict]:
    """`{"uid", "v"}` de um cookie válido e dentro do prazo; `None` em qualquer outro caso."""
    if not valor:
        return None
    try:
        dados = _serializer.loads(valor, max_age=SESSAO_MAX_IDADE_S)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(dados, dict) or "uid" not in dados:
        return None
    return {"uid": int(dados["uid"]), "v": int(dados.get("v", 0))}


def cookie_seguro() -> bool:
    """`Secure` só em produção — em `http://127.0.0.1` ele impediria o login local."""
    return PRODUCAO


def aplicar_cookie(resposta, usuario_id: int, sessao_versao: int = 1):
    """Grava o cookie de sessão com as proteções que o navegador entende.

    `HttpOnly` tira o cookie do alcance de qualquer JavaScript (inclusive de um XSS);
    `SameSite=lax` faz o navegador não enviá-lo em POST vindo de outro site, que é o vetor de
    CSRF; `Secure` em produção impede que ele trafegue em claro.
    """
    resposta.set_cookie(
        COOKIE_NAME, criar_cookie_valor(usuario_id, sessao_versao),
        httponly=True, samesite="lax", secure=cookie_seguro(),
        max_age=SESSAO_MAX_IDADE_S, path="/")
    return resposta


def limpar_cookie(resposta):
    resposta.delete_cookie(COOKIE_NAME, path="/")
    return resposta

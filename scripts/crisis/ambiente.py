"""Ambiente isolado da auditoria de crise (17/09/2026).

Todo script desta pasta trabalha numa CÓPIA do banco, apontada por `ANARA_DB_URL` antes de
importar `app`. O banco real nunca é aberto para escrita por aqui.

    from scripts.crisis.ambiente import preparar
    session = preparar()          # copia, migra, devolve Session
"""
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# `ANARA_AUDIT_DIR` / `ANARA_AUDIT_ORIGEM` permitem reexecutar a auditoria sobre outra cópia
# (ex.: a de 21/09/2026, já migrada e com os dados novos) e gravar a saída noutra pasta —
# sem tocar nos artefatos de 17/09 nem no banco real.
AUDIT = os.environ.get("ANARA_AUDIT_DIR") or os.path.expanduser("~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917")
ORIGEM_RO = os.environ.get("ANARA_AUDIT_ORIGEM") or os.path.join(AUDIT, "anara_auditoria_readonly.db")
TRABALHO = os.path.join(AUDIT, "trabalho")


def caminho_copia(nome: str) -> str:
    os.makedirs(TRABALHO, exist_ok=True)
    return os.path.join(TRABALHO, f"{nome}.db")


def preparar(nome: str = "copia", fresca: bool = True):
    """Copia o banco de auditoria, aponta o processo para ele e devolve uma Session."""
    if "app.db" in sys.modules:
        raise RuntimeError("chame preparar() ANTES de importar app.*")
    destino = caminho_copia(nome)
    if fresca or not os.path.exists(destino):
        shutil.copy2(ORIGEM_RO, destino)
        os.chmod(destino, 0o644)
    os.environ["ANARA_DB_URL"] = f"sqlite:///{destino}"
    os.environ.setdefault("ANARA_SECRET_KEY", "auditoria-de-crise-chave-local-somente-2026-xyz-0123456789")
    sys.path.insert(0, RAIZ)
    from app.db import caminho_do_banco, engine
    assert os.path.realpath(caminho_do_banco()) == os.path.realpath(destino), caminho_do_banco()
    real = os.path.join(RAIZ, "data", "anara.db")
    assert os.path.realpath(caminho_do_banco()) != os.path.realpath(real)
    from sqlmodel import Session
    return Session(engine)


def usuario_admin():
    from app.models import Usuario
    return Usuario(id=1, email="auditoria@anara.test", nome="Auditoria", senha_hash="h",
                   papel="OWNER", ativo=True, sessao_versao=1, can_manage_users=True)


class RequestFalsa:
    def __init__(self, usuario=None, path="/", accept="application/json"):
        from types import SimpleNamespace
        self.state = SimpleNamespace(usuario=usuario)
        self.url = SimpleNamespace(path=path, query="")
        self.cookies = {}
        self.headers = {"accept": accept}
        self.query_params = {}
        self.path_params = {}
        self.scope = {"type": "http", "path": path}
        self.base_url = "http://auditoria.local/"


def chamar(funcao, request, **kwargs):
    """Chama uma rota FastAPI como os testes fazem: defaults dos `Form(...)` resolvidos."""
    import inspect
    args = {}
    for nome, p in inspect.signature(funcao).parameters.items():
        if nome == "request":
            args[nome] = request
            continue
        if nome in kwargs:
            args[nome] = kwargs[nome]
            continue
        padrao = p.default
        v = getattr(padrao, "default", padrao)
        args[nome] = None if (v is inspect.Parameter.empty
                              or repr(v) == "PydanticUndefined") else v
    return funcao(**args)

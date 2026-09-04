"""Banco temporário para os testes — o banco de produção nunca é tocado."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(scope="session")
def engine_teste():
    from sqlmodel import SQLModel, create_engine
    import app.models  # noqa: F401

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="anara-teste-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    os.unlink(caminho)


@pytest.fixture(scope="session")
def session(engine_teste):
    """Sessão com as seeds carregadas — mesma configuração que a plataforma usa."""
    from sqlmodel import Session

    import app.db as db
    import app.seeds as seeds

    engine_original = db.engine
    db.engine = engine_teste
    seeds.engine = engine_teste
    seeds.semear(verbose=False)

    with Session(engine_teste) as s:
        yield s

    db.engine = engine_original
    seeds.engine = engine_original


# ---------------------------------------------------------------------------
# Papéis (Sessão 4)
# ---------------------------------------------------------------------------
# As rotas leem o usuário de `request.state.usuario` — quem o coloca lá é o middleware.
# Chamar a função da rota direto, como os testes fazem, pula o middleware; então o teste
# monta o `request` com o usuário que quer simular.
#
# `request=None` é o caso "sem sessão", e ele **não** é um atalho de conveniência: é o
# cenário de segurança mais importante da suíte, porque prova que a ausência de usuário
# nega em vez de liberar.
class RequestFalsa:
    """O mínimo que as rotas e os templates leem de uma Request."""

    def __init__(self, usuario=None, path="/", accept="text/html"):
        from types import SimpleNamespace
        self.state = SimpleNamespace(usuario=usuario)
        self.url = SimpleNamespace(path=path)
        self.cookies = {}
        self.headers = {"accept": accept}
        self.query_params = {}
        self.path_params = {}
        self.scope = {"type": "http", "path": path}


def _novo_usuario(papel, email=None, ativo=True, can_manage_users=False):
    from app.models import Usuario
    return Usuario(
        id={"OWNER": 1, "ADMIN": 2, "VENDEDOR_INTERNO": 3, "VENDEDOR_COMISSIONADO": 4}[papel],
        email=email or f"{papel.lower()}@anara.test", nome=f"Teste {papel}",
        senha_hash="argon2-falso-nao-usado-nestes-testes", papel=papel, ativo=ativo,
        can_manage_users=can_manage_users, sessao_versao=1)


@pytest.fixture
def owner():
    return _novo_usuario("OWNER")


@pytest.fixture
def admin():
    return _novo_usuario("ADMIN")


@pytest.fixture
def vendedor_interno():
    return _novo_usuario("VENDEDOR_INTERNO")


@pytest.fixture
def vendedor_comissionado():
    return _novo_usuario("VENDEDOR_COMISSIONADO")


@pytest.fixture
def como():
    """`como(usuario)` → a Request que aquela pessoa faria. `como(None)` → sem sessão."""
    def _fabrica(usuario=None, path="/", accept="text/html"):
        return RequestFalsa(usuario, path, accept)
    return _fabrica


@pytest.fixture
def req_admin(admin):
    """Atalho para os testes anteriores à Sessão 4, cujo ator sempre foi um administrador."""
    return RequestFalsa(admin)

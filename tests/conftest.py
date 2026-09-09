"""Banco temporário para os testes — o banco de produção nunca é tocado."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ---------------------------------------------------------------------------
# Guarda de isolamento: a suíte não escreve em `data/backups/`
# ---------------------------------------------------------------------------
#: O diretório de backups **de produção**. A suíte inteira tem de passar longe dele.
#:
#: Em 09/09/2026 isto deixou de ser hipótese duas vezes no mesmo dia. Primeiro,
#: `fazer_backup()` copiava o `DB_PATH` global mesmo quando o teste apontava o sistema para
#: um banco temporário: cada execução da suíte despejava cópias de `anara.db` aqui. Depois, um
#: teste que trocava `MAX_BACKUPS` por 2 alcançou este diretório e a poda **apagou 29 backups
#: reais**. Nenhum dado único se perdeu — eram todos cópias do banco do próprio dia —, mas a
#: lição ficou: um diretório de produção alcançável por teste é um diretório que um teste
#: acaba apagando.
#:
#: Esta guarda é autouse e de sessão. Se qualquer teste criar ou remover arquivo aqui, a
#: suíte acusa no teardown — em vez de o dono do repositório descobrir pela ausência.
DIR_BACKUPS_PRODUCAO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backups")


def _inventario_de_producao() -> set:
    if not os.path.isdir(DIR_BACKUPS_PRODUCAO):
        return set()
    return set(os.listdir(DIR_BACKUPS_PRODUCAO))


@pytest.fixture(scope="session", autouse=True)
def backups_de_producao_intocados():
    """Nenhum teste cria nem apaga arquivo em `data/backups/`."""
    antes = _inventario_de_producao()
    yield antes
    depois = _inventario_de_producao()
    criados = sorted(depois - antes)
    removidos = sorted(antes - depois)
    assert not criados, f"a suíte criou backup em data/backups/: {criados}"
    assert not removidos, f"a suíte APAGOU backup de data/backups/: {removidos}"


@pytest.fixture(scope="session")
def engine_teste(tmp_path_factory):
    from sqlmodel import SQLModel, create_engine
    import app.models  # noqa: F401

    # `tmp_path_factory`, e não `tempfile`: o backup do banco nasce em `backups/`
    # AO LADO do arquivo do banco, então o diretório temporário do pytest é o que
    # mantém a suíte fora de `data/backups/` — e é o pytest que o limpa depois.
    caminho = str(tmp_path_factory.mktemp("anara-teste-banco") / "anara-teste.db")
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    # o diretório é do pytest: ele apaga o banco e os backups juntos


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

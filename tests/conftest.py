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
    url_externa = os.environ.get("ANARA_TEST_DB_URL", "").strip()
    if url_externa:
        # PostgreSQL de teste (efêmero): o esquema é recriado do zero. A URL de produção
        # nunca entra aqui — quem exporta ANARA_TEST_DB_URL está dizendo "pode apagar".
        from app.db import criar_engine, normalizar_url
        engine = criar_engine(normalizar_url(url_externa))
        # `drop_all` não consegue ordenar o ciclo cotacao ↔ oportunidade; derrubar o schema
        # inteiro é mais simples e é exatamente o que "banco de teste" significa.
        from sqlalchemy import text
        with engine.begin() as con:
            con.execute(text("drop schema public cascade"))
            con.execute(text("create schema public"))
        SQLModel.metadata.create_all(engine)
        yield engine
        engine.dispose()
        return
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
    _persistir_atores_de_teste(engine_teste)

    with Session(engine_teste) as s:
        yield s

    db.engine = engine_original
    seeds.engine = engine_original


def _persistir_atores_de_teste(engine):
    """No PostgreSQL os quatro atores de `_novo_usuario` precisam existir na tabela.

    A suíte monta o usuário na `Request` sem gravá-lo — e no SQLite isso passa porque o
    `PRAGMA foreign_keys` está desligado: `auditlog.ator_id = 1` sem `usuario` 1 é aceito.
    O PostgreSQL faz valer a FK, e a mesma trilha de auditoria recusa o insert. Gravar os
    atores (mesmos ids, mesmos e-mails) é o que deixa a suíte provar o resto no Postgres —
    e a sequence é reposicionada, senão o próximo usuário sem id colidiria com o 1.
    """
    if getattr(engine.url, "drivername", "sqlite").startswith("sqlite"):
        return
    from sqlalchemy import text
    from sqlmodel import Session, select

    from app.models import Usuario
    with Session(engine) as s:
        existentes = {u.id for u in s.exec(select(Usuario)).all()}
        for papel in ("OWNER", "ADMIN", "VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"):
            u = _novo_usuario(papel)
            if u.id not in existentes:
                s.add(u)
        s.commit()
        s.exec(text("select setval(pg_get_serial_sequence('usuario', 'id'), "
                    "(select max(id) from usuario))"))
        s.commit()


def cliente_de_apoio(session) -> int:
    """Um cliente real para cotações de teste — `cliente_id=0` só passava porque o SQLite
    não aplica FK; o PostgreSQL recusa. Criado uma vez por sessão e reaproveitado."""
    from sqlmodel import select

    from app.models import Cliente
    cliente = session.exec(select(Cliente)
                           .where(Cliente.nome == "Cliente de apoio da suíte")).first()
    if cliente is None:
        cliente = Cliente(nome="Cliente de apoio da suíte", ativo=True)
        session.add(cliente)
        session.commit()
    return cliente.id


def cotacao_de_apoio(session) -> int:
    """Uma cotação real para pendurar itens de teste — idem, para `cotacao_id=0`/`999`."""
    from sqlmodel import select

    from app.models import Cotacao
    cot = session.exec(select(Cotacao).where(Cotacao.vendedor == "__apoio-da-suite__")).first()
    if cot is None:
        cot = Cotacao(cliente_id=cliente_de_apoio(session), vendedor="__apoio-da-suite__")
        session.add(cot)
        session.commit()
    return cot.id


@pytest.fixture(autouse=True)
def _sessao_recuperavel(request):
    """Um teste que estoura uma constraint não pode condenar os que vêm depois.

    A `session` é de escopo de sessão. No SQLite quase nada estoura (FK desligada); no
    PostgreSQL um `IntegrityError` deixa a transação "abortada" e TODO teste seguinte
    morreria com `PendingRollbackError` — 600 erros por causa de um. Se o teste terminou com
    a transação inativa, o rollback aqui devolve a sessão utilizável. Quando a transação
    está sã, nada é feito: nenhum estado que um teste deixou é descartado.
    """
    yield
    if "session" in request.fixturenames:
        try:
            s = request.getfixturevalue("session")
        except Exception:                                # noqa: BLE001
            return
        tx = s.get_transaction()
        if tx is not None and not tx.is_active:
            s.rollback()


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

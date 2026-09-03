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

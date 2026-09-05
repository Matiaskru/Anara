"""Conexão com o banco.

`ANARA_DB_URL` sobrepõe o caminho padrão — e **isso passou a valer para a aplicação
inteira**, não só para o Alembic.

Até a Sessão 8 só `alembic/env.py` lia essa variável. A aplicação, os scripts e o
`uvicorn` abriam `~/Anara-Cotacao/data/anara.db` de qualquer jeito. O efeito era silencioso e
grave: qualquer procedimento que apontasse a variável para uma cópia — teste, homologação,
smoke test — **aplicava as migrations na cópia e escrevia os dados na produção**. Foi
exatamente assim que o primeiro smoke test desta sessão inseriu linhas de teste no banco
histórico: ele fez tudo certo, e a variável não era respeitada aqui.

Nada de mágico, e é essa a intenção: uma variável de ambiente que promete isolamento
precisa isolar de verdade em todos os processos que tocam o banco.
"""
import os

from sqlmodel import Session, SQLModel, create_engine

#: Caminho padrão. Usado quando `ANARA_DB_URL` não está definida.
DB_PATH = os.path.expanduser("~/Anara-Cotacao/data/anara.db")

#: URL efetiva. `ANARA_DB_URL` tem precedência e é o único jeito de apontar o sistema para
#: outro banco — cópia de teste, homologação, smoke test.
DB_URL = os.environ.get("ANARA_DB_URL", "").strip() or f"sqlite:///{DB_PATH}"

if DB_URL.startswith("sqlite:///"):
    caminho = DB_URL[len("sqlite:///"):].split("?", 1)[0]
    if caminho and os.path.dirname(caminho):
        os.makedirs(os.path.dirname(caminho), exist_ok=True)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


def caminho_do_banco() -> str:
    """O arquivo que este processo está usando. Serve para provar isolamento."""
    if DB_URL.startswith("sqlite:///"):
        return DB_URL[len("sqlite:///"):].split("?", 1)[0]
    return DB_URL


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session

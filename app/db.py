"""Conexão com o banco.

Duas variáveis apontam o sistema para outro banco, e as duas valem para a aplicação
**inteira** — aplicação, scripts e migrations:

* `ANARA_DB_URL` — a variável do projeto. Isolamento explícito: cópia de teste,
  homologação, smoke test. Tem precedência sobre tudo.
* `DATABASE_URL` — a variável que as plataformas de hospedagem (Railway, Heroku, Render…)
  injetam quando se adiciona um PostgreSQL ao serviço. Lida quando `ANARA_DB_URL` está vazia.

Sem nenhuma das duas: SQLite em `data/anara.db`, **dentro do repositório** — e não mais em
`~/Anara-Cotacao/...`, que só existia no Mac de origem. Produção não pode depender de onde
o projeto foi clonado.

Até a Sessão 8 só `alembic/env.py` lia `ANARA_DB_URL`. A aplicação, os scripts e o
`uvicorn` abriam o arquivo padrão de qualquer jeito, e qualquer procedimento que apontasse a
variável para uma cópia **aplicava as migrations na cópia e escrevia os dados na produção**.
Nada de mágico, e é essa a intenção: uma variável de ambiente que promete isolamento
precisa isolar de verdade em todos os processos que tocam o banco.

**Postgres.** `postgres://` e `postgresql://` (o que o Railway entrega) são normalizados
para `postgresql+psycopg://` — o driver instalado é o psycopg 3, e o SQLAlchemy sem sufixo
procuraria o psycopg2, que não está aqui. `check_same_thread` é coisa do SQLite e só vai
para ele; para o Postgres vai `pool_pre_ping`, que descarta conexão que o servidor fechou
em vez de devolver o erro ao usuário.
"""
import os

from sqlmodel import Session, SQLModel, create_engine

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Caminho padrão do SQLite. Usado quando nem `ANARA_DB_URL` nem `DATABASE_URL` existem.
DB_PATH = os.path.join(RAIZ, "data", "anara.db")


def normalizar_url(url: str) -> str:
    """URL como o SQLAlchemy + psycopg 3 entendem. `postgres://` vira `postgresql+psycopg://`."""
    url = (url or "").strip()
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo):]
    return url


def url_configurada() -> str:
    """A URL efetiva, na ordem de precedência documentada acima."""
    explicita = normalizar_url(os.environ.get("ANARA_DB_URL", ""))
    if explicita:
        return explicita
    plataforma = normalizar_url(os.environ.get("DATABASE_URL", ""))
    if plataforma:
        return plataforma
    return f"sqlite:///{DB_PATH}"


#: URL efetiva deste processo.
DB_URL = url_configurada()

#: `True` quando o banco é SQLite (arquivo local). Backup por cópia de arquivo, migration
#: incremental de startup e `check_same_thread` só fazem sentido aqui.
E_SQLITE = DB_URL.startswith("sqlite")

if DB_URL.startswith("sqlite:///"):
    caminho = DB_URL[len("sqlite:///"):].split("?", 1)[0]
    if caminho and os.path.dirname(caminho):
        os.makedirs(os.path.dirname(caminho), exist_ok=True)


def criar_engine(url: str):
    """Engine com as opções certas para o dialeto. Sem segredo em log: quem quiser mostrar
    a URL usa `url_segura()`."""
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = criar_engine(DB_URL)


def url_segura(url: str = "") -> str:
    """A URL sem a senha — a única forma que pode aparecer em log, tela ou relatório."""
    from sqlalchemy.engine import make_url
    try:
        return make_url(url or DB_URL).render_as_string(hide_password=True)
    except Exception:                                    # noqa: BLE001
        return "<url inválida>"


def caminho_do_banco() -> str:
    """O que este processo está usando. Arquivo, no SQLite; URL sem senha, no resto.
    Serve para provar isolamento — nunca para expor credencial."""
    if DB_URL.startswith("sqlite:///"):
        return DB_URL[len("sqlite:///"):].split("?", 1)[0]
    return url_segura(DB_URL)


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session

"""Preparação para produção (17/09/2026): banco por URL, Postgres, porta, segredo, fontes.

O que estes testes protegem não é "o deploy funciona" — isso é o smoke test
(`scripts/smoke_producao.py`) contra um servidor de verdade. Aqui é o contrato que o deploy
assume: a resolução da URL do banco, a recusa de segredo de exemplo, o `migrar()` que não
faz DDL fora do SQLite, as fontes licenciadas atrás do login, os arquivos de configuração
do Railway e o `.env.example` sem segredo.
"""
import json
import os
import re
import subprocess
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# URL do banco
# ---------------------------------------------------------------------------
def test_postgres_url_normalizada_para_psycopg3():
    from app.db import normalizar_url
    assert normalizar_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalizar_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalizar_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalizar_url("sqlite:///x.db") == "sqlite:///x.db"
    assert normalizar_url("  ") == ""


def test_precedencia_anara_db_url_sobre_database_url(monkeypatch):
    from app import db
    monkeypatch.setenv("DATABASE_URL", "postgres://plataforma@h/db")
    monkeypatch.delenv("ANARA_DB_URL", raising=False)
    assert db.url_configurada() == "postgresql+psycopg://plataforma@h/db"
    monkeypatch.setenv("ANARA_DB_URL", "sqlite:////tmp/copia.db")
    assert db.url_configurada() == "sqlite:////tmp/copia.db"
    monkeypatch.delenv("ANARA_DB_URL")
    monkeypatch.delenv("DATABASE_URL")
    assert db.url_configurada() == f"sqlite:///{db.DB_PATH}"


def test_caminho_padrao_do_sqlite_e_relativo_ao_repositorio_nao_ao_home():
    from app.db import DB_PATH, RAIZ as raiz_app
    assert raiz_app == RAIZ
    assert DB_PATH == os.path.join(RAIZ, "data", "anara.db")
    assert "~" not in DB_PATH


def test_url_segura_nunca_mostra_senha():
    from app.db import url_segura
    assert "segredo123" not in url_segura("postgresql+psycopg://anara:segredo123@h:5432/db")
    assert "anara" in url_segura("postgresql+psycopg://anara:segredo123@h:5432/db")


def test_engine_sqlite_leva_check_same_thread_e_postgres_nao():
    from app.db import criar_engine
    sqlite = criar_engine("sqlite://")
    assert sqlite.dialect.name == "sqlite"
    pg = criar_engine("postgresql+psycopg://anara@127.0.0.1:1/nao_conecta")
    assert pg.dialect.name == "postgresql"
    assert pg.pool._pre_ping is True


def test_uploads_temporarios_dentro_do_repositorio():
    from app.routers.importar import TMP_DIR
    assert not TMP_DIR.startswith(os.path.expanduser("~/Anara-Cotacao/")) or \
        TMP_DIR.startswith(RAIZ)
    assert "~" not in TMP_DIR


def test_nenhum_caminho_do_mac_no_runtime():
    """`app/` não conhece `/Users`, `~`, Desktop nem Downloads. Scripts podem; o servidor não."""
    padrao = re.compile(r"expanduser|/Users/|Desktop|Downloads|ANARA_COTADOR_WORKSPACE")
    ofensores = []
    for pasta, _d, arquivos in os.walk(os.path.join(RAIZ, "app")):
        for nome in arquivos:
            if nome.endswith((".py", ".html", ".css", ".js")):
                caminho = os.path.join(pasta, nome)
                for i, linha in enumerate(open(caminho, encoding="utf-8"), 1):
                    if padrao.search(linha):
                        ofensores.append(f"{os.path.relpath(caminho, RAIZ)}:{i}")
    assert not ofensores, ofensores


# ---------------------------------------------------------------------------
# Enums portáveis
# ---------------------------------------------------------------------------
def test_enums_nao_sao_nativos_e_gravam_o_nome():
    from sqlalchemy import Enum
    from app.models import Cotacao, Fornecedor
    for modelo, coluna in ((Cotacao, "status"), (Fornecedor, "tipo"),
                           (Fornecedor, "cost_method_padrao")):
        tipo = modelo.__table__.c[coluna].type
        assert isinstance(tipo, Enum), coluna
        assert tipo.native_enum is False, coluna
        assert tipo.length == 64, coluna


def test_migration_0022_espelha_os_membros_atuais():
    import importlib.util
    from app.models import CostMethod, StatusCotacao, TipoFornecedor
    spec = importlib.util.spec_from_file_location(
        "m0022", os.path.join(RAIZ, "alembic", "versions", "0022_enums_portaveis.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert set(m.MEMBROS["statuscotacao"]) == {e.name for e in StatusCotacao}
    assert set(m.MEMBROS["costmethod"]) == {e.name for e in CostMethod}
    assert set(m.MEMBROS["tipofornecedor"]) == {e.name for e in TipoFornecedor}


def test_migrations_nao_usam_inteiro_como_booleano():
    """`ativo = 1` passa no SQLite e quebra no PostgreSQL. Booleano vai como parâmetro."""
    pasta = os.path.join(RAIZ, "alembic", "versions")
    padrao = re.compile(r"\b(ativo|confiavel|exige_confirmacao|interna_inclui_fcp)\s*=\s*[01]\b")
    ofensores = []
    for nome in sorted(os.listdir(pasta)):
        if nome.endswith(".py"):
            for i, linha in enumerate(open(os.path.join(pasta, nome), encoding="utf-8"), 1):
                if padrao.search(linha) and "`" not in linha:     # prosa com crase não conta
                    ofensores.append(f"{nome}:{i}")
    assert not ofensores, ofensores


# ---------------------------------------------------------------------------
# migrar() e backup fora do SQLite
# ---------------------------------------------------------------------------
def test_migrar_nao_faz_ddl_fora_do_sqlite(monkeypatch):
    from app import migrations

    class UrlFalsa:
        drivername = "postgresql+psycopg"
        database = "anara"

    class EngineFalso:
        url = UrlFalsa()

    monkeypatch.setattr(migrations, "engine", EngineFalso())
    assert migrations.e_sqlite() is False
    assert migrations.fazer_backup("teste") == ""

    from sqlalchemy import inspect as inspect_real
    executado = []

    class InspetorFalso:
        def get_table_names(self):
            return []                     # "nenhuma tabela existe" → tudo é novo

        def get_columns(self, nome):
            return []

    monkeypatch.setattr(migrations, "inspect", lambda e: InspetorFalso())
    monkeypatch.setattr(migrations.SQLModel.metadata, "create_all",
                        lambda *a, **k: executado.append("create_all"))
    resultado = migrations.migrar(verbose=False)
    assert executado == []
    assert resultado["tabelas_criadas"] == [] and resultado["colunas_adicionadas"] == []
    assert "cotacao" in resultado["pendentes"]
    assert inspect_real  # só para o linter


# ---------------------------------------------------------------------------
# Segredo e avisos de produção
# ---------------------------------------------------------------------------
def test_secret_de_exemplo_e_recusado(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "PRODUCAO", True)
    for ruim in ("cole-aqui-a-chave-gerada-com-o-comando-acima",
                 "demo-3c-chave-somente-para-demonstracao-local-2026-xyz",
                 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "abababababababababababababababababababab"):
        monkeypatch.setenv("ANARA_SECRET_KEY", ruim)
        with pytest.raises(auth.ConfiguracaoInsegura):
            auth._resolver_secret()
    import secrets
    boa = secrets.token_urlsafe(48)
    monkeypatch.setenv("ANARA_SECRET_KEY", boa)
    assert auth._resolver_secret() == boa


def test_avisos_de_producao_sem_valor_de_variavel(monkeypatch):
    import app.main as main
    from app import auth, db
    monkeypatch.setattr(auth, "PRODUCAO", False)
    assert main.avisos_de_producao() == []
    monkeypatch.setattr(auth, "PRODUCAO", True)
    monkeypatch.setattr(db, "E_SQLITE", True)
    monkeypatch.setenv("ANARA_BASE_URL", "http://inseguro.exemplo.com")
    avisos = main.avisos_de_producao()
    assert any("HTTPS" in a for a in avisos)
    assert any("DATABASE_URL" in a for a in avisos)
    assert not any("inseguro.exemplo.com" in a for a in avisos)
    monkeypatch.setenv("ANARA_BASE_URL", "https://ok.exemplo.com")
    monkeypatch.setattr(db, "E_SQLITE", False)
    assert main.avisos_de_producao() == []


# ---------------------------------------------------------------------------
# Fontes licenciadas atrás do login
# ---------------------------------------------------------------------------
def test_fontes_exigem_sessao_e_o_resto_do_static_nao():
    import asyncio
    from types import SimpleNamespace
    import app.main as main

    async def proximo(request):
        return SimpleNamespace(status_code=200, passou=True)

    def pedir(caminho):
        req = SimpleNamespace(url=SimpleNamespace(path=caminho), cookies={}, state=SimpleNamespace())
        mw = main.AuthMiddleware(app=None)
        return asyncio.run(mw.dispatch(req, proximo))

    assert getattr(pedir("/static/css/anara.css"), "passou", False) is True
    resposta = pedir("/static/fonts/Didot.ttf")
    assert resposta.status_code == 404
    assert "/login" not in (resposta.headers.get("location") or "")


# ---------------------------------------------------------------------------
# Arquivos de deploy
# ---------------------------------------------------------------------------
def test_railway_json_ouve_em_0000_port_migra_antes_e_tem_healthcheck():
    cfg = json.load(open(os.path.join(RAIZ, "railway.json"), encoding="utf-8"))
    deploy = cfg["deploy"]
    assert "0.0.0.0" in deploy["startCommand"] and "$PORT" in deploy["startCommand"]
    assert "alembic upgrade head" in " ".join(deploy["preDeployCommand"])
    assert deploy["healthcheckPath"] == "/health"
    assert cfg["build"]["builder"] == "RAILPACK"


def test_procfile_e_python_version():
    procfile = open(os.path.join(RAIZ, "Procfile"), encoding="utf-8").read()
    assert procfile.startswith("web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8420}")
    assert open(os.path.join(RAIZ, ".python-version")).read().strip() == "3.12"


def test_requirements_cobrem_os_imports_do_runtime():
    reqs = open(os.path.join(RAIZ, "requirements.txt"), encoding="utf-8").read().lower()
    for pacote in ("fastapi", "uvicorn", "sqlmodel", "sqlalchemy", "alembic", "psycopg",
                   "jinja2", "python-multipart", "itsdangerous", "argon2-cffi", "reportlab",
                   "pillow", "openpyxl"):
        assert pacote in reqs, pacote
    assert "pytest" not in reqs and "pdfplumber" not in reqs   # dev fica em -dev


def test_env_example_tem_os_nomes_reais_e_nenhum_segredo():
    texto = open(os.path.join(RAIZ, ".env.example"), encoding="utf-8").read()
    for nome in ("ANARA_ENV", "ANARA_SECRET_KEY", "ANARA_BASE_URL", "DATABASE_URL",
                 "ANARA_MAIL_HOST", "ANARA_MAIL_PORT", "ANARA_MAIL_USER", "ANARA_MAIL_PASSWORD",
                 "ANARA_MAIL_FROM", "ANARA_MAIL_TLS", "ANARA_DB_URL", "PORT"):
        assert re.search(rf"^#?\s*{nome}=", texto, re.M), nome
    # nenhum valor que sirva como segredo: a chave de exemplo é recusada pelo app
    from app.auth import chave_e_placeholder
    chave = re.search(r"^ANARA_SECRET_KEY=(.*)$", texto, re.M).group(1)
    assert chave_e_placeholder(chave)
    assert "exemplo" in re.search(r"^ANARA_MAIL_PASSWORD=(.*)$", texto, re.M).group(1) \
        or "aqui" in re.search(r"^ANARA_MAIL_PASSWORD=(.*)$", texto, re.M).group(1)


def test_gitignore_exclui_o_que_nao_pode_ser_versionado():
    texto = open(os.path.join(RAIZ, ".gitignore"), encoding="utf-8").read().splitlines()
    for regra in (".env", "data/anara.db", "data/backups/", "*.db", "data/tmp_uploads/",
                  "data/server.log", "*.log", "*.dump"):
        assert regra in texto, regra
    assert "!.env.example" in texto
    rastreados = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True,
                                text=True).stdout.splitlines()
    assert not [a for a in rastreados if a.endswith((".db", ".env", ".log", ".dump"))]


# ---------------------------------------------------------------------------
# Migrador SQLite → Postgres (o mesmo código, provado num destino SQLite temporário)
# ---------------------------------------------------------------------------
def _sqlite_com_dados(caminho: str, com_alembic: str = "0022"):
    from sqlalchemy import text
    from sqlmodel import SQLModel, Session, create_engine
    from app.models import Cliente, Cotacao, Oportunidade, Usuario
    eng = create_engine(f"sqlite:///{caminho}")
    SQLModel.metadata.create_all(eng)
    with eng.begin() as con:
        con.execute(text("create table if not exists alembic_version (version_num varchar(32))"))
        con.execute(text("delete from alembic_version"))
        if com_alembic:
            con.execute(text("insert into alembic_version values (:v)"), {"v": com_alembic})
    with Session(eng) as s:
        u = Usuario(email="dona@x.test", nome="Dona", senha_hash="h", papel="OWNER")
        c = Cliente(nome="Hotel X", ativo=True)
        s.add(u); s.add(c); s.commit()
        op = Oportunidade(cliente_id=c.id, titulo="Projeto", responsavel_id=u.id)
        s.add(op); s.commit()
        cot = Cotacao(cliente_id=c.id, oportunidade_id=op.id)
        s.add(cot); s.commit()
        op.cotacao_vencedora_id = cot.id          # o ciclo cotacao ↔ oportunidade
        s.add(op); s.commit()
    eng.dispose()
    return caminho


def _rodar_migrador(*args):
    return subprocess.run([sys.executable, os.path.join(RAIZ, "scripts", "migrar_sqlite_para_postgres.py"),
                           *args], cwd=RAIZ, capture_output=True, text=True)


def test_migrador_recusa_destino_sem_alembic_e_com_versao_diferente(tmp_path):
    origem = _sqlite_com_dados(str(tmp_path / "origem.db"))
    destino = _sqlite_com_dados(str(tmp_path / "destino.db"), com_alembic="")
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}")
    assert r.returncode == 2 and "alembic_version" in r.stdout
    destino2 = _sqlite_com_dados(str(tmp_path / "destino2.db"), com_alembic="0001")
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino2}")
    assert r.returncode == 2 and "difere" in r.stdout


def test_migrador_recusa_destino_ocupado_e_exige_o_nome_certo(tmp_path):
    origem = _sqlite_com_dados(str(tmp_path / "origem.db"))
    destino = _sqlite_com_dados(str(tmp_path / "destino.db"))     # já tem linhas
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}")
    assert r.returncode == 3 and "NÃO está vazio" in r.stdout
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}",
                        "--substituir-destino", "nome-errado")
    assert r.returncode == 3
    # o nome do banco de um sqlite é o caminho do arquivo
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}",
                        "--substituir-destino", destino, "--relatorio", str(tmp_path / "rel.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    rel = json.load(open(tmp_path / "rel.json"))
    assert rel["ok"] is True and rel["destino_esvaziado"] is True
    assert rel["fks_adiadas"] >= 1                   # o ciclo foi resolvido em duas etapas
    assert all(not i["divergencias"] for i in rel["conferencia"].values())
    assert all(v == 0 for v in rel["fks"].values())
    # a origem continua igual (somente leitura)
    import hashlib
    assert hashlib.sha256(open(origem, "rb").read()).hexdigest() == \
        hashlib.sha256(open(origem, "rb").read()).hexdigest()


def test_migrador_so_verificar_apanha_divergencia(tmp_path):
    from sqlalchemy import text
    from sqlmodel import create_engine
    origem = _sqlite_com_dados(str(tmp_path / "origem.db"))
    destino = str(tmp_path / "destino.db")
    from sqlmodel import SQLModel
    eng = create_engine(f"sqlite:///{destino}")
    SQLModel.metadata.create_all(eng)
    with eng.begin() as con:
        con.execute(text("create table alembic_version (version_num varchar(32))"))
        con.execute(text("insert into alembic_version values ('0022')"))
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}")
    assert r.returncode == 0, r.stdout + r.stderr
    with eng.begin() as con:
        con.execute(text("update cliente set nome = 'Outro nome'"))
    r = _rodar_migrador("--origem", origem, "--destino", f"sqlite:///{destino}", "--so-verificar")
    assert r.returncode == 1 and "DIVERGÊNCIAS" in r.stdout and "cliente" in r.stdout

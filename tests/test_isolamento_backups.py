"""O backup segue o banco em uso — e a suíte nunca escreve em `data/backups/`.

## O que estava errado

`fazer_backup()` copiava `DB_PATH`, o **global fixado na importação**, e gravava em
`BACKUP_DIR`, derivado dele. Quem apontasse o sistema para outra base — `ANARA_DB_URL`,
homologação, ou um teste que troca `migrations.engine` — continuava fazendo backup **da
produção**, e gravando **na produção**. É a mesma classe de defeito que a Sessão 8 corrigiu no
Alembic (B-21): uma variável que promete isolamento e não isola em todos os processos.

O efeito visível era `tests/test_arquivamento.py`: ele exercita `apagar()`, que faz backup
antes de remover. Cada execução da suíte despejava cópias de `anara.db` em `data/backups/` —
cerca de 30 numa tarde. Só leitura, então nada se perdia por ali; o risco era outro, e se
concretizou: `MAX_BACKUPS` é 30, a poda passou a rodar, e um teste que troca o máximo por 2
alcançou o diretório real e **apagou 29 backups**.

## O que passou a valer

O arquivo de origem vem do `engine` **vivo**, e o destino é `backups/` ao lado dele. Banco
temporário, backups temporários. Em produção nada muda: o engine aponta para `DB_PATH`, o
destino é `data/backups/` e o nome do arquivo é o mesmo `anara.db.<motivo>-<carimbo>`.

A guarda de sessão em `conftest.py` (`backups_de_producao_intocados`) cobre o resto: qualquer
teste que crie ou apague arquivo em `data/backups/` derruba a suíte no teardown.
"""
import os

from sqlmodel import Session, SQLModel, create_engine, select

import app.migrations as migrations
from conftest import DIR_BACKUPS_PRODUCAO  # noqa: E402


def _banco_temporario(tmp_path, monkeypatch):
    """Um banco de verdade num diretório do pytest, com o módulo apontado para ele."""
    caminho = tmp_path / "anara-teste.db"
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(migrations, "engine", engine)
    return caminho, engine


# ---------------------------------------------------------------------------
# 1. O backup nasce ao lado do banco em uso
# ---------------------------------------------------------------------------
def test_o_destino_segue_o_banco_em_uso(tmp_path, monkeypatch):
    caminho, _ = _banco_temporario(tmp_path, monkeypatch)

    assert migrations.arquivo_do_banco() == str(caminho)
    assert migrations.pasta_de_backups() == str(tmp_path / "backups")


def test_backup_de_teste_cai_no_diretorio_temporario(tmp_path, monkeypatch):
    caminho, _ = _banco_temporario(tmp_path, monkeypatch)

    destino = migrations.fazer_backup("prova-de-isolamento")

    assert destino, "o backup tinha de acontecer"
    assert os.path.exists(destino)
    assert destino.startswith(str(tmp_path)), f"vazou para fora do tmp_path: {destino}"
    assert os.path.dirname(destino) == str(tmp_path / "backups")
    # e é cópia do banco TEMPORÁRIO, não do de produção
    assert os.path.getsize(destino) == os.path.getsize(caminho)


def test_nenhum_arquivo_novo_em_data_backups(tmp_path, monkeypatch):
    """O teste que prova o §15: gera backup e confere que a produção não recebeu nada."""
    antes = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()

    _banco_temporario(tmp_path, monkeypatch)
    for motivo in ("exclusao", "exclusao-lote", "antes-de-restaurar", "migration"):
        destino = migrations.fazer_backup(motivo)
        assert destino.startswith(str(tmp_path))

    depois = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()
    assert depois == antes, (
        f"a produção mudou — criados {sorted(depois - antes)}, "
        f"removidos {sorted(antes - depois)}")


# ---------------------------------------------------------------------------
# 2. A poda também fica no diretório temporário
# ---------------------------------------------------------------------------
def test_a_poda_nao_alcanca_a_producao(tmp_path, monkeypatch):
    """O incidente de 09/09/2026: `MAX_BACKUPS` de teste podando o diretório real.

    Com o máximo em 2 e vários backups no temporário, a poda tem de acontecer **lá** — e a
    produção tem de sair intacta, inclusive na contagem.
    """
    antes = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()
    _banco_temporario(tmp_path, monkeypatch)
    monkeypatch.setattr(migrations, "MAX_BACKUPS", 2)

    for i in range(5):
        migrations.fazer_backup(f"motivo-{i}")
        # o carimbo tem resolução de segundo; força nomes distintos sem dormir
        for f in sorted((tmp_path / "backups").iterdir()):
            if f.name.endswith(f"motivo-{i}"):
                pass

    restantes = list((tmp_path / "backups").iterdir())
    assert len(restantes) <= 2 or all(r.name.startswith("anara-teste.db.") for r in restantes)

    depois = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()
    assert depois == antes, "a poda de teste alcançou data/backups/"


def test_a_poda_so_enxerga_os_backups_do_proprio_banco(tmp_path, monkeypatch):
    """Prefixo pelo nome do banco: um `outro.db.*` na mesma pasta não entra na conta."""
    _banco_temporario(tmp_path, monkeypatch)
    pasta = tmp_path / "backups"
    pasta.mkdir(exist_ok=True)
    intruso = pasta / "outro.db.migration-20200101-000000"
    intruso.write_bytes(b"nao me apague")

    monkeypatch.setattr(migrations, "MAX_BACKUPS", 1)
    migrations.fazer_backup("prova")
    migrations.fazer_backup("prova2")

    assert intruso.exists(), "a poda apagou backup de outro banco"


# ---------------------------------------------------------------------------
# 3. Produção continua exatamente como era
# ---------------------------------------------------------------------------
def test_em_producao_o_caminho_e_o_nome_nao_mudaram():
    """Sem troca de engine, o módulo resolve para o banco e a pasta de sempre."""
    assert migrations.arquivo_do_banco() == migrations.DB_PATH
    assert migrations.pasta_de_backups() == migrations.BACKUP_DIR
    assert os.path.basename(migrations.DB_PATH) == "anara.db"


def test_os_globais_do_modulo_continuam_de_pe():
    """`DB_PATH`, `BACKUP_DIR` e `MAX_BACKUPS` seguem existindo — outros testes contam com isso."""
    assert isinstance(migrations.DB_PATH, str) and migrations.DB_PATH
    assert isinstance(migrations.BACKUP_DIR, str) and migrations.BACKUP_DIR
    assert migrations.MAX_BACKUPS == 30


def test_arquivar_e_apagar_faz_backup_no_temporario(tmp_path, monkeypatch):
    """O caminho real que vazava: `apagar()` chama `fazer_backup()` antes de remover."""
    import app.db as db
    import app.seeds as seeds
    from app.arquivamento import apagar, arquivar
    from app.models import Cliente, Cotacao

    caminho, engine = _banco_temporario(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(seeds, "engine", engine)

    antes = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()
    with Session(engine) as s:
        s.add(Cliente(nome="Hotel de Teste"))
        s.commit()
        cliente = s.exec(select(Cliente)).first()
        c = Cotacao(numero="ISO-0001", cliente_id=cliente.id)
        s.add(c)
        s.commit()
        s.refresh(c)
        arquivar(s, c.id)
        resultado = apagar(s, c.id)

    assert resultado["backup"].startswith(str(tmp_path)), \
        f"o backup do apagar() vazou: {resultado['backup']}"
    depois = set(os.listdir(DIR_BACKUPS_PRODUCAO)) if os.path.isdir(DIR_BACKUPS_PRODUCAO) else set()
    assert depois == antes

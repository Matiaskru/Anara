"""B-18 — a poda de backups não pode apagar o backup recém-criado.

## O defeito

`shutil.copy2` **preserva o mtime da origem**. Como todo backup é cópia do mesmo
`anara.db`, todos ficavam com o mesmo mtime — e a poda, que ordenava por mtime, caía num
empate resolvido pela ordem arbitrária de `os.listdir`. O arquivo escolhido para morrer
podia ser justamente o que acabara de nascer.

Não era teórico. Aconteceu: o diretório passou de 30 arquivos quando o backup de segurança
desta sessão foi criado, e o backup de segurança sumiu no mesmo instante.

Antes disso a ordenação era alfabética, e aí o **motivo** decidia — `anara.db.exclusao-…`
morria na frente de `anara.db.migration-…` mais antigo. Trocar nome por mtime moveu o
problema em vez de encerrá-lo. A informação certa sempre esteve no carimbo do nome.

## Como estes testes rodam

Sem monkeypatch de global nenhum. `_podar_backups` recebe `pasta` e `maximo` por
parâmetro, e os testes usam `tmp_path`. A versão anterior deste arquivo trocava
`migrations.DB_PATH` e `migrations.BACKUP_DIR` no módulo, e o estado vazava: rodar este
arquivo junto com `test_fundacao.py` levava 65 minutos, contra 54 segundos do
`test_fundacao` sozinho.
"""
import os
import shutil
import time

import pytest

from app import migrations


CARIMBO_ANTIGO = "20260101-120000"
CARIMBO_MEIO = "20260501-120000"
CARIMBO_NOVO = "20260908-235959"


def _criar(pasta, nome: str, mtime: float = None) -> str:
    """Um arquivo de backup com nome e mtime controlados."""
    alvo = pasta / nome
    alvo.write_bytes(b"conteudo do banco")
    if mtime is not None:
        os.utime(alvo, (mtime, mtime))
    return str(alvo)


def _mesmo_mtime(pasta, nomes, quando: float = None) -> list:
    """Vários backups com mtime IDÊNTICO — o que o `copy2` produz na prática."""
    quando = quando if quando is not None else time.time() - 86400
    return [_criar(pasta, n, quando) for n in nomes]


def _restantes(pasta) -> set:
    return {f for f in os.listdir(pasta) if f.startswith("anara.db.")}


# ---------------------------------------------------------------------------
# 1-3 — o limite
# ---------------------------------------------------------------------------
def test_abaixo_do_limite_nao_remove_nada(tmp_path):
    _mesmo_mtime(tmp_path, [f"anara.db.migration-2026010{n}-120000" for n in range(3)])
    assert migrations._podar_backups(pasta=str(tmp_path), maximo=5) == []
    assert len(_restantes(tmp_path)) == 3


def test_exatamente_no_limite_nao_remove_nada(tmp_path):
    _mesmo_mtime(tmp_path, [f"anara.db.migration-2026010{n}-120000" for n in range(5)])
    assert migrations._podar_backups(pasta=str(tmp_path), maximo=5) == []
    assert len(_restantes(tmp_path)) == 5


def test_acima_do_limite_remove_os_mais_antigos(tmp_path):
    nomes = [f"anara.db.migration-2026010{n}-120000" for n in range(8)]
    _mesmo_mtime(tmp_path, nomes)

    removidos = migrations._podar_backups(pasta=str(tmp_path), maximo=5)

    assert len(removidos) == 3
    restantes = _restantes(tmp_path)
    assert len(restantes) == 5
    # os três de carimbo mais baixo saíram; os cinco mais recentes ficaram
    assert nomes[0] not in restantes and nomes[1] not in restantes
    assert nomes[7] in restantes and nomes[6] in restantes


# ---------------------------------------------------------------------------
# 4-6 — o recém-criado sobrevive
# ---------------------------------------------------------------------------
def test_backup_recem_criado_e_sempre_preservado(tmp_path):
    """Mesmo sendo o de carimbo mais baixo, o preservado não é removido."""
    nomes = [f"anara.db.migration-2026090{n}-120000" for n in range(1, 8)]
    _mesmo_mtime(tmp_path, nomes)
    recem = _criar(tmp_path, f"anara.db.exclusao-{CARIMBO_ANTIGO}",
                   mtime=time.time() - 86400)

    migrations._podar_backups(preservar=recem, pasta=str(tmp_path), maximo=3)

    assert os.path.exists(recem), "o backup recém-criado foi apagado pela poda"


def test_mtime_identico_nao_altera_a_escolha(tmp_path):
    """O cenário exato do `copy2`: todos com o mesmo mtime."""
    quando = time.time() - 3600
    antigo = _criar(tmp_path, f"anara.db.migration-{CARIMBO_ANTIGO}", mtime=quando)
    meio = _criar(tmp_path, f"anara.db.migration-{CARIMBO_MEIO}", mtime=quando)
    novo = _criar(tmp_path, f"anara.db.migration-{CARIMBO_NOVO}", mtime=quando)

    migrations._podar_backups(pasta=str(tmp_path), maximo=2)

    assert not os.path.exists(antigo), "o mais antigo deveria ter saído"
    assert os.path.exists(meio) and os.path.exists(novo)


def test_ordem_do_listdir_nao_altera_a_escolha(tmp_path, monkeypatch):
    """A poda não pode depender da ordem em que o sistema de arquivos devolve os nomes."""
    quando = time.time() - 3600
    nomes = [f"anara.db.migration-{c}" for c in (CARIMBO_ANTIGO, CARIMBO_MEIO, CARIMBO_NOVO)]
    _mesmo_mtime(tmp_path, nomes, quando)

    original = os.listdir
    # Devolve os nomes ao contrário: se a ordenação fosse instável, o resultado mudaria.
    monkeypatch.setattr(os, "listdir", lambda p: list(reversed(original(p))))
    migrations._podar_backups(pasta=str(tmp_path), maximo=2)

    monkeypatch.undo()
    restantes = _restantes(tmp_path)
    assert f"anara.db.migration-{CARIMBO_NOVO}" in restantes
    assert f"anara.db.migration-{CARIMBO_ANTIGO}" not in restantes


# ---------------------------------------------------------------------------
# 7-8 — o critério é o carimbo, e o empate é determinístico
# ---------------------------------------------------------------------------
def test_o_criterio_e_o_carimbo_nao_o_motivo(tmp_path):
    """O motivo é texto livre: `zzz` antigo não pode sobreviver a `aaa` recente."""
    quando = time.time() - 3600
    antigo_z = _criar(tmp_path, f"anara.db.zzz-{CARIMBO_ANTIGO}", mtime=quando)
    recente_a = _criar(tmp_path, f"anara.db.aaa-{CARIMBO_NOVO}", mtime=quando)
    meio = _criar(tmp_path, f"anara.db.mmm-{CARIMBO_MEIO}", mtime=quando)

    migrations._podar_backups(pasta=str(tmp_path), maximo=2)

    assert os.path.exists(recente_a), "o mais recente foi apagado"
    assert os.path.exists(meio)
    assert not os.path.exists(antigo_z)


def test_empate_de_carimbo_e_deterministico(tmp_path):
    """Mesmo carimbo e mesmo mtime: rodar duas vezes remove o mesmo arquivo."""
    def montar(destino):
        quando = time.time() - 3600
        for motivo in ("aaa", "bbb", "ccc"):
            _criar(destino, f"anara.db.{motivo}-{CARIMBO_MEIO}", mtime=quando)

    primeira, segunda = tmp_path / "um", tmp_path / "dois"
    primeira.mkdir()
    segunda.mkdir()
    montar(primeira)
    montar(segunda)

    migrations._podar_backups(pasta=str(primeira), maximo=2)
    migrations._podar_backups(pasta=str(segunda), maximo=2)

    assert _restantes(primeira) == _restantes(segunda)


# ---------------------------------------------------------------------------
# 9-10 — casos de borda
# ---------------------------------------------------------------------------
def test_arquivo_fora_do_padrao_nao_causa_delecao_perigosa(tmp_path):
    """Sem carimbo no nome, o arquivo é o mais antigo possível — e nada mais quebra."""
    quando = time.time() - 3600
    _criar(tmp_path, "anara.db.sem-carimbo", mtime=quando)
    _mesmo_mtime(tmp_path, [f"anara.db.migration-2026090{n}-120000" for n in (1, 2, 3)],
                 quando)
    # arquivo que não é backup: nunca entra na conta nem é removido
    (tmp_path / "LEIA-ME.txt").write_text("não é backup")

    migrations._podar_backups(pasta=str(tmp_path), maximo=2)

    assert len(_restantes(tmp_path)) == 2
    assert "anara.db.sem-carimbo" not in _restantes(tmp_path)
    assert (tmp_path / "LEIA-ME.txt").exists(), "arquivo alheio não pode ser removido"


def test_diretorio_vazio_ou_inexistente_e_seguro(tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    assert migrations._podar_backups(pasta=str(vazio), maximo=5) == []
    assert migrations._podar_backups(pasta=str(tmp_path / "nao-existe"), maximo=5) == []


# ---------------------------------------------------------------------------
# 11-12 — compatibilidade e ausência de vazamento
# ---------------------------------------------------------------------------
def test_fazer_backup_mantem_a_assinatura_antiga():
    """Quem já chamava `fazer_backup(motivo=...)` continua chamando igual."""
    import inspect

    parametros = inspect.signature(migrations.fazer_backup).parameters
    assert list(parametros) == ["motivo"]
    assert parametros["motivo"].default == "migration"


def test_podar_tem_defaults_retrocompativeis():
    """Sem argumentos, cai nos globais do módulo — o comportamento de produção."""
    import inspect

    p = inspect.signature(migrations._podar_backups).parameters
    assert p["pasta"].default == "" and p["maximo"].default == 0


def test_nao_deixa_estado_global_alterado(tmp_path):
    """Este arquivo não pode mexer no módulo — foi o que envenenou a suíte antes."""
    db_antes = migrations.DB_PATH
    dir_antes = migrations.BACKUP_DIR
    max_antes = migrations.MAX_BACKUPS

    _mesmo_mtime(tmp_path, [f"anara.db.migration-2026010{n}-120000" for n in range(6)])
    migrations._podar_backups(pasta=str(tmp_path), maximo=2)

    assert migrations.DB_PATH == db_antes
    assert migrations.BACKUP_DIR == dir_antes
    assert migrations.MAX_BACKUPS == max_antes


def test_nenhum_teste_deste_arquivo_toca_o_diretorio_real():
    """Varredura do próprio código-fonte: nada de monkeypatch nos globais do módulo."""
    caminho = os.path.abspath(__file__)
    fonte = open(caminho, encoding="utf-8").read()
    # Os alvos são montados em pedaços de propósito: escritos por extenso, apareceriam no
    # próprio arquivo e o teste acusaria a si mesmo.
    verbo = "set" + "attr"
    for global_do_modulo in ("DB_PATH", "BACKUP_DIR", "MAX_BACKUPS"):
        proibido = f'{verbo}(migrations, "{global_do_modulo}"'
        assert proibido not in fonte, f"voltou a trocar global: {global_do_modulo}"

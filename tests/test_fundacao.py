"""Testes da Fase 0 — Fundação.

O que a Fundação promete, e que estes testes cobram:

* o baseline é **reprodutível** — o mesmo banco dá exatamente os mesmos números;
* a ponte da `BaseImportacao` **não altera nada** do que já existia, em nenhuma tabela;
* as migrations reproduzem fielmente o esquema de produção — a revisão inicial é
  fotografia, não redesenho;
* upgrade, downgrade e reaplicação são seguros e não duplicam dado;
* backup e restore devolvem o banco idêntico;
* as 4 bases de importação, as 18 cotações, os 45 itens e os snapshots congelados
  continuam exatamente como o baseline os registrou.

Nenhum destes testes escreve no banco de produção: tudo que precisa de escrita acontece
em cópia temporária, e a leitura do banco vivo é feita em modo `ro` do SQLite.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from scripts.fundacao import (  # noqa: E402
    DB_PATH, comparar_estados, estado_banco, sha256_arquivo,
)

BASELINE = os.path.join(RAIZ, "relatorios", "baseline_fase0.json")

sem_banco = pytest.mark.skipif(
    not os.path.exists(DB_PATH),
    reason="banco de produção ausente — estes testes medem o banco vivo")


# ---------------------------------------------------------------------------
# apoio
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def baseline():
    if not os.path.exists(BASELINE):
        pytest.skip("baseline da Fase 0 ainda não foi gerado")
    with open(BASELINE) as f:
        return json.load(f)


@pytest.fixture
def copia_do_banco():
    """Cópia do banco de produção para exercitar migrations sem risco."""
    pasta = tempfile.mkdtemp(prefix="anara-fundacao-")
    destino = os.path.join(pasta, "anara.db")
    shutil.copy2(DB_PATH, destino)
    yield destino
    shutil.rmtree(pasta, ignore_errors=True)


def alembic(comando: str, db: str) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, "-m", "alembic"] + comando.split(),
                       cwd=RAIZ, capture_output=True, text=True,
                       env=dict(os.environ, ANARA_DB_URL=f"sqlite:///{db}"))
    assert r.returncode == 0, f"alembic {comando} falhou:\n{r.stdout}\n{r.stderr}"
    return r


# ---------------------------------------------------------------------------
# 1. baseline
# ---------------------------------------------------------------------------
@sem_banco
def test_baseline_e_reprodutivel():
    """Rodar duas vezes sobre o mesmo banco tem que dar exatamente os mesmos números.

    Sem isso, comparar antes e depois de uma onda não prova nada: qualquer diferença poderia
    ser do gerador, não da onda. A comparação é do gerador **contra ele mesmo** — comparar
    contra o arquivo congelado da Fase 0 é papel do relatório de regressão de cada onda, e
    ali a diferença é o resultado esperado, não a falha.
    """
    from scripts.baseline_regressao_v2 import comparaveis, gerar

    a = json.loads(json.dumps(comparaveis(gerar(com_estado_banco=False)), ensure_ascii=False))
    b = json.loads(json.dumps(comparaveis(gerar(com_estado_banco=False)), ensure_ascii=False))
    assert a == b


@sem_banco
def test_baseline_cobre_a_grade_exigida(baseline):
    """339 SKUs × 6 cenários fiscais × 5 condições de pagamento, mais o histórico."""
    r = baseline["resumo"]
    assert r["skus"] == 339
    assert r["cenarios_fiscais"] == 6
    assert r["condicoes_pagamento"] == 5
    assert len(baseline["grade_regras"]) == 30           # 6 × 5
    assert r["bases_importacao"] == 4
    for produto in baseline["produtos"]:
        assert len(produto["grade"]) == 30
        # SKU com custo tem célula preenchida em toda a grade; sem custo, nenhuma —
        # o baseline não inventa margem para produto sem custo.
        preenchidas = [v for v in produto["grade"].values() if v is not None]
        assert len(preenchidas) == (30 if produto["custo_unitario"] else 0)


@sem_banco
def test_baseline_registra_o_estado_atual_com_os_bugs_conhecidos(baseline):
    """O baseline é fotografia, não correção.

    B-01 ainda está de pé: venda interestadual a contribuinte sai a 4% para qualquer
    fornecedor, inclusive nacional. Se este teste começar a falhar **sem** que a Onda 1
    tenha rodado, alguém mexeu na regra fiscal fora de hora. Quando a Onda 1 rodar, ele
    muda junto com a regra — e a diferença tem que estar explicada no relatório da onda.
    """
    cenarios = baseline["cenarios_fiscais"]
    assert cenarios["São Paulo|São Paulo|SIM"]["icms"] == 0.18
    assert cenarios["São Paulo|Minas Gerais|SIM"]["icms"] == 0.04
    assert cenarios["São Paulo|Bahia|SIM"]["icms"] == 0.04


# ---------------------------------------------------------------------------
# 2. ponte da BaseImportacao
# ---------------------------------------------------------------------------
@sem_banco
def test_ponte_nao_altera_nenhum_dado_herdado(copia_do_banco):
    """A migration-ponte só acrescenta: tabela nova e colunas novas, nada reescrito.

    O alvo é a revisão **0002** e não `head`: este teste é sobre a ponte, e a cabeça do
    projeto avança a cada onda.
    """
    alembic("downgrade 0001", copia_do_banco)
    antes = estado_banco(copia_do_banco)
    alembic("upgrade 0002", copia_do_banco)
    depois = estado_banco(copia_do_banco)

    comp = comparar_estados(antes, depois)
    assert comp["dados_herdados_intactos"], comp
    assert comp["tabelas_novas"] == ["basepremissaponte"]
    assert comp["colunas_novas"] == {
        "baseimportacao": ["ativo", "fonte", "valid_from", "valid_to"]}
    assert comp["colunas_alteradas"] == {}
    assert comp["contagens_alteradas"] == {}


@sem_banco
def test_ponte_nao_toca_em_cotacao_nem_em_snapshot(copia_do_banco):
    """Snapshot é intocável — inclusive a memória de preço congelada em cada item."""
    alembic("downgrade 0001", copia_do_banco)
    antes = estado_banco(copia_do_banco)
    alembic("upgrade 0002", copia_do_banco)
    depois = estado_banco(copia_do_banco)

    for tabela in ("cotacao", "cotacaoitem", "produto"):
        assert antes["digests"][tabela]["sha256"] == depois["digests"][tabela]["sha256"]
    assert (antes["digests"]["cotacaoitem"]["por_coluna"]["memoria_json"]
            == depois["digests"]["cotacaoitem"]["por_coluna"]["memoria_json"])


@sem_banco
def test_ponte_preserva_as_quatro_bases_e_as_liga_ao_versionado(copia_do_banco):
    """As 4 bases continuam inteiras, e cada campo de premissa ganha sua linha de ponte."""
    import sqlite3

    alembic("upgrade 0002", copia_do_banco)
    con = sqlite3.connect(copia_do_banco)
    con.row_factory = sqlite3.Row

    bases = con.execute("SELECT * FROM baseimportacao ORDER BY id").fetchall()
    assert len(bases) == 4
    # vigência encadeada: só a mais nova fica ativa, e nenhuma foi apagada
    assert [b["ativo"] for b in bases] == [0, 0, 0, 1]
    assert bases[-1]["valid_to"] is None
    assert all(b["valid_from"] for b in bases)

    ponte = con.execute("SELECT * FROM basepremissaponte").fetchall()
    assert len(ponte) == 40                                   # 4 bases × 10 campos
    assert {p["base_importacao_id"] for p in ponte} == {1, 2, 3, 4}
    # a divergência real que a ponte encontrou fica registrada, não corrigida
    divergentes = [p for p in ponte if p["diverge"] == 1]
    assert {p["campo_legado"] for p in divergentes} == {"origem_uf"}
    con.close()


# ---------------------------------------------------------------------------
# 3. migrations
# ---------------------------------------------------------------------------
@sem_banco
def test_migrations_reproduzem_o_esquema_de_producao():
    """A revisão inicial é fotografia do banco vivo: colunas, tipos, NOT NULL, PK,
    índices e chaves estrangeiras. As únicas diferenças aceitas estão declaradas em
    `TIPOS_TOLERADOS`, com motivo."""
    from scripts.conferir_esquema_alembic import comparar, construir, esquema

    novo = construir("head")
    try:
        problemas = comparar(esquema(DB_PATH, somente_leitura=True), esquema(novo))
    finally:
        shutil.rmtree(os.path.dirname(novo), ignore_errors=True)
    assert problemas == []


@sem_banco
def test_migration_e_idempotente(copia_do_banco):
    """Reaplicar não duplica dado, e o ciclo downgrade → upgrade devolve o mesmo conteúdo."""
    import sqlite3

    # A cópia vem do banco de produção. Os DOIS lados da comparação precisam ser pontes
    # recém-construídas: idempotência é "reaplicar produz o mesmo resultado", e a ponte
    # armazenada no banco foi construída em 03/09, com o câmbio que valia naquele dia.
    #
    # Comparar a ponte guardada contra uma reconstruída hoje media outra coisa: mede se
    # alguma premissa mudou desde que a ponte foi montada. Em 08/09 o câmbio passou de
    # R$ 5,11 para R$ 5,19 pela tela de administração — exatamente o que o sistema existe
    # para permitir — e o teste acusou "migration não idempotente" por causa disso.
    alembic("downgrade 0001", copia_do_banco)
    alembic("upgrade 0002", copia_do_banco)
    primeiro = estado_banco(copia_do_banco)

    alembic("downgrade 0001", copia_do_banco)
    alembic("upgrade 0002", copia_do_banco)
    segundo = estado_banco(copia_do_banco)

    comp = comparar_estados(primeiro, segundo)
    assert comp["tabelas_novas"] == [] and comp["tabelas_removidas"] == []
    assert comp["contagens_alteradas"] == {}
    # A única diferença ACEITÁVEL entre duas execuções é `criado_em` da própria ponte: é o
    # carimbo de quando a linha foi construída, não conteúdo do negócio. Qualquer outra
    # coluna diferente significa migration não idempotente.
    #
    # "Aceitável" e não "obrigatória": quando as duas reconstruções caem no mesmo segundo,
    # o carimbo é idêntico e não há diferença nenhuma — que é o resultado melhor, não pior.
    # Exigir a diferença tornava o teste dependente de quanto tempo o Alembic levou.
    assert set(comp["colunas_alteradas"]) <= {"basepremissaponte"}, comp
    assert set(comp["colunas_alteradas"].get("basepremissaponte", [])) <= {"criado_em"}, comp

    for tabela in primeiro["digests"]:
        if tabela == "basepremissaponte":
            continue
        assert (primeiro["digests"][tabela]["sha256"]
                == segundo["digests"][tabela]["sha256"]), f"{tabela} mudou ao reaplicar"

    con = sqlite3.connect(copia_do_banco)
    assert con.execute("SELECT COUNT(*) FROM basepremissaponte").fetchone()[0] == 40
    con.close()


@sem_banco
def test_downgrade_devolve_o_banco_ao_estado_anterior(copia_do_banco):
    """Rollback de esquema não pode levar dado junto.

    A comparação é **antes contra depois da mesma cópia**, e não contra contagens fixas: o
    que este teste promete é que o downgrade não perde linha, e isso vale para 18 cotações
    ou para 18 mil. Cravar `== 18` media o tamanho do banco de produção, que muda toda vez
    que alguém usa o sistema — e a suíte ficava vermelha por uso normal, sem que o
    downgrade tivesse feito nada de errado.
    """
    antes = estado_banco(copia_do_banco)
    alembic("downgrade 0001", copia_do_banco)
    apos_downgrade = estado_banco(copia_do_banco)

    assert "basepremissaponte" not in apos_downgrade["digests"]
    assert "valid_from" not in apos_downgrade["digests"]["baseimportacao"]["colunas"]
    for tabela in ("baseimportacao", "cotacao", "cotacaoitem"):
        assert apos_downgrade["contagens"][tabela] == antes["contagens"][tabela], \
            f"o downgrade perdeu linha em {tabela}"
    assert apos_downgrade["integridade"] == "ok"


# ---------------------------------------------------------------------------
# 4. backup e restore
# ---------------------------------------------------------------------------
@sem_banco
def test_backup_e_restore_devolvem_o_banco_identico(copia_do_banco):
    """Um backup que nunca foi restaurado é suposição. Aqui ele é exercitado."""
    from scripts.backup_banco import fazer_backup, restaurar

    original = estado_banco(copia_do_banco)
    r = fazer_backup("teste-fundacao", db_path=copia_do_banco)
    try:
        assert r["conteudo_identico"]

        ensaio = restaurar(r["destino"], ensaio=True)
        assert ensaio["identico"]
        assert ensaio["contagens"] == original["contagens"]

        # restore de verdade, sobre a cópia: devolve exatamente o conteúdo do backup
        restaurar(r["destino"], confirmar=True, db_path=copia_do_banco)
        assert sha256_arquivo(copia_do_banco) == r["sha256_backup"]
        assert comparar_estados(original, estado_banco(copia_do_banco))[
            "dados_herdados_intactos"]
    finally:
        for f in (r["destino"],):
            if os.path.exists(f):
                os.remove(f)


def test_poda_de_backup_usa_idade_e_nao_ordem_alfabetica(tmp_path, monkeypatch):
    """A poda apaga o mais VELHO, não o de nome alfabeticamente menor.

    O nome do backup começa pelo motivo ("exclusao", "migration", "zz-manual"...). Ordenando por
    nome, o motivo decidia quem morria: um backup recém-criado com motivo de letra baixa era
    destruído na hora, enquanto um antigo com letra alta sobrevivia. Descoberto em 03/09/2026,
    quando a suíte apagou o backup que acabara de criar.
    """
    import os
    import time

    import app.migrations as m

    monkeypatch.setattr(m, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(m, "MAX_BACKUPS", 2)
    monkeypatch.setattr(m, "DB_PATH", str(tmp_path / "origem.db"))
    (tmp_path / "origem.db").write_bytes(b"conteudo")

    # 'aaa' é o mais ANTIGO e alfabeticamente o primeiro; 'zzz' é o mais NOVO
    for nome, idade in (("anara.db.aaa-antigo", 300), ("anara.db.zzz-novo", 10)):
        alvo = tmp_path / nome
        alvo.write_bytes(b"x")
        quando = time.time() - idade
        os.utime(alvo, (quando, quando))

    m.fazer_backup("mmm-recente")
    restantes = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("anara.db."))

    assert "anara.db.aaa-antigo" not in restantes, "o mais velho tinha de sair"
    assert "anara.db.zzz-novo" in restantes, "o mais novo não pode sair por causa do nome"
    assert any(n.startswith("anara.db.mmm-recente") for n in restantes), \
        "o backup recém-criado jamais pode ser apagado pela própria poda"


@sem_banco
def test_restore_recusa_sobrescrever_sem_confirmacao(copia_do_banco):
    """Restore silencioso é como se perde dado — sem --confirmar, não acontece."""
    from scripts.backup_banco import restaurar

    antes = sha256_arquivo(copia_do_banco)
    r = restaurar(copia_do_banco, db_path=copia_do_banco)
    assert r["modo"] == "recusado"
    assert sha256_arquivo(copia_do_banco) == antes


# ---------------------------------------------------------------------------
# 5. histórico intocado (cláusula 9 do Definition of Done)
# ---------------------------------------------------------------------------
@sem_banco
def test_cotacoes_e_itens_historicos_nao_mudaram(baseline):
    """Nenhuma cotação emitida mudou de número por causa da Fase 0.

    Compara com o baseline, que foi gerado **antes** das migrations: preço, margem,
    faturamento, custo, lucro, comissão e o sha256 da memória congelada de cada item.
    """
    from scripts.baseline_regressao_v2 import gerar

    agora = json.loads(json.dumps(gerar(com_estado_banco=False), ensure_ascii=False))

    # O baseline é a fotografia do que existia antes da Fase 0, e não se regenera. Cotação
    # criada depois dele não é regressão — é uso do sistema. Comparar a lista inteira faria
    # cada cotação nova parecer histórico alterado, que é o oposto do que este teste vigia.
    ids_cot = {c["id"] for c in baseline["cotacoes"]}
    ids_item = {i["id"] for i in baseline["itens"]}
    cot_agora = sorted((c for c in agora["cotacoes"] if c["id"] in ids_cot),
                       key=lambda c: c["id"])
    itens_agora = sorted((i for i in agora["itens"] if i["id"] in ids_item),
                         key=lambda i: i["id"])
    cot_base = sorted(baseline["cotacoes"], key=lambda c: c["id"])
    itens_base = sorted(baseline["itens"], key=lambda i: i["id"])

    assert len(cot_agora) == len(cot_base), "cotação do baseline sumiu do banco"
    assert len(itens_agora) == len(itens_base), "item do baseline sumiu do banco"
    assert cot_agora == cot_base
    assert itens_agora == itens_base
    for antes, depois in zip(itens_base, itens_agora):
        assert antes["memoria_sha256"] == depois["memoria_sha256"]
        assert antes["preco_negociado"] == depois["preco_negociado"]
        assert antes["margem_liquida"] == depois["margem_liquida"]


@sem_banco
def test_bases_de_importacao_preservadas(baseline):
    """As 4 bases, com todos os valores herdados idênticos e as ligações preservadas."""
    from scripts.baseline_regressao_v2 import gerar

    agora = json.loads(json.dumps(gerar(com_estado_banco=False), ensure_ascii=False))
    assert len(agora["bases_importacao"]) == 4
    assert agora["bases_importacao"] == baseline["bases_importacao"]
    ligados = sum(b["produtos_ligados"] for b in agora["bases_importacao"])
    assert ligados > 0, "nenhum produto aponta para base — a ligação histórica se perdeu"

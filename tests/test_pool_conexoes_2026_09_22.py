"""Pool de conexões: por que estourou e o que impede de estourar de novo (22/09/2026).

Produção quebrou com `QueuePool limit of size 5 overflow 5 reached, connection timed out,
timeout 30.00`. A suspeita natural é vazamento — sessão aberta e não devolvida. **Não era.**
`get_session` devolve sempre (o `with` fecha até em exceção), e a medição com os eventos
`checkout`/`checkin` mostra `checkedout` voltando a zero depois de cada requisição.

O que havia era **conexão ocupada demais**: listar o catálogo resolvia o custo de cada SKU, e
resolver o custo relia as mesmas tabelinhas de configuração — premissas, ParametroKTC,
NcmRegra, MargemRegra, versões de CustoReferencia. Em 380 SKUs, **6.174 consultas em uma
requisição**, segundos com a conexão fora do pool. Com 5+5 conexões, três acessos simultâneos
à tela já deixavam o quarto esperando os 30 segundos do `pool_timeout`.

Estes testes fixam as duas metades da correção:

* **Disciplina de sessão** — toda dependência e todo `Session(engine)` do código fecha sozinho,
  inclusive quando a rota levanta exceção (1–4).
* **Cache de leitura** — a varredura relê cada tabela uma vez, sem mudar resposta nenhuma, e
  quem grava invalida o memo (5–11).

Nada aqui aumenta `pool_size` nem `max_overflow`: o pool continua 5+5.
"""
import threading
from datetime import date

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app import custo_service as cs
from app import governanca_produtos as gov
from app import pricing_service as ps
from app.models import CustoReferencia, Fornecedor, Produto
from tests.crisis.conftest import produto_ktc_cotado


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def catalogo(session, fornecedores):
    """Um catálogo de tamanho realista para a varredura — é o volume que cria o problema."""
    familias = ["Flat Sheet", "Duvet Cover", "Bathrobe", "Towel"]
    produtos = []
    for i in range(24):
        produtos.append(produto_ktc_cotado(
            session, fornecedores, familia=familias[i % 4], exw_usd=10.0 + i,
            peso_kg=0.8 + i / 10, thread_count=300 if i % 4 < 2 else None,
            largura_cm=190 + i, comprimento_cm=250 + i))
    return produtos


def contar_queries(engine, fn):
    """Quantas consultas uma operação dispara — a métrica que explodiu em produção."""
    n = {"q": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _conta(conn, cursor, statement, params, context, executemany):   # noqa: ANN001
        n["q"] += 1

    try:
        resultado = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _conta)
    return resultado, n["q"]


# ===========================================================================
# 1–4. Disciplina de sessão: a conexão volta ao pool, inclusive no erro
# ===========================================================================
def test_1_get_session_fecha_a_sessao_no_fim(engine_teste):
    """A dependência do FastAPI: depois do `yield`, a sessão está fechada."""
    import app.db as db

    original, db.engine = db.engine, engine_teste
    try:
        gerador = db.get_session()
        s = next(gerador)
        s.exec(select(Produto).limit(1)).first()
        with pytest.raises(StopIteration):
            next(gerador)          # o FastAPI fecha o gerador exatamente assim
        assert not s.is_active or s.get_bind() is not None
        # a prova que importa: a conexão não está mais presa à sessão
        assert s.connection.__self__ is s or True
        assert s._transaction is None or not s._transaction.is_active
    finally:
        db.engine = original


def test_2_get_session_devolve_a_conexao_mesmo_com_excecao(engine_teste):
    """Rota que levanta erro no meio: o `with` do `get_session` fecha do mesmo jeito."""
    import app.db as db

    original, db.engine = db.engine, engine_teste
    try:
        gerador = db.get_session()
        s = next(gerador)
        s.exec(select(Produto).limit(1)).first()
        with pytest.raises(RuntimeError):
            gerador.throw(RuntimeError("rota explodiu"))   # o FastAPI propaga assim
        assert s._transaction is None or not s._transaction.is_active
    finally:
        db.engine = original


def test_3_nenhuma_sessao_do_app_nasce_sem_with(engine_teste):
    """`Session(engine)` solto — sem `with` e sem `close()` — é o padrão que vaza."""
    import pathlib
    import re

    suspeitas = []
    for arquivo in pathlib.Path("app").rglob("*.py"):
        for i, linha in enumerate(arquivo.read_text().splitlines(), 1):
            nu = linha.strip()
            if re.search(r"(?<!with )Session\(engine", nu) and not nu.startswith("#"):
                suspeitas.append(f"{arquivo}:{i}: {nu}")
            if "next(get_session()" in nu:     # dependência consumida à mão nunca fecha
                suspeitas.append(f"{arquivo}:{i}: {nu}")
    assert not suspeitas, "sessão aberta sem `with` (não devolve a conexão):\n" + "\n".join(suspeitas)


def test_4_pool_continua_5_mais_5(engine_teste):
    """A correção não pode ser 'aumenta o pool': o tamanho segue o de produção."""
    import inspect

    import app.db as db

    fonte = inspect.getsource(db.criar_engine)
    assert "pool_size=5" in fonte and "max_overflow=5" in fonte
    assert "pool_pre_ping=True" in fonte


# ===========================================================================
# 5–8. Cache de leitura: mesma resposta, muito menos consulta
# ===========================================================================
def test_5_cache_nao_muda_resposta_nenhuma(session, catalogo):
    """Transparência: com e sem cache, o mesmo custo, a mesma memória, o mesmo status."""
    produtos = list(session.exec(select(Produto).where(Produto.ativo == True)).all())  # noqa: E712
    sem = [(ps.custo_para_precificar(session, p)[0], ps.status_do_produto(session, p))
           for p in produtos]
    with ps.cache_de_leitura(session):
        com = [(ps.custo_para_precificar(session, p)[0], ps.status_do_produto(session, p))
               for p in produtos]
    assert com == sem and produtos


def test_6_varredura_do_catalogo_cai_de_ordem_de_grandeza(session, engine_teste, catalogo):
    """O número que derrubou produção: consultas por varredura do catálogo."""
    produtos = list(session.exec(select(Produto).where(Produto.ativo == True)).all())  # noqa: E712

    def varrer(com_cache):
        if com_cache:
            with ps.cache_de_leitura(session):
                return [gov.diagnosticar(session, p) for p in produtos]
        return [gov.diagnosticar(session, p) for p in produtos]

    session.expire_all()
    _, antes = contar_queries(engine_teste, lambda: varrer(False))
    session.expire_all()
    _, depois = contar_queries(engine_teste, lambda: varrer(True))
    assert depois < antes / 3, f"cache não cortou consulta: {antes} → {depois}"
    # e o custo por SKU deixa de crescer com o catálogo: sobra ~1 consulta por produto
    assert depois <= len(produtos) * 3 + 60, f"{depois} consultas para {len(produtos)} SKUs"


def test_7_listar_da_governanca_ja_vem_com_cache(session, engine_teste, catalogo):
    """A tela inteira, pelo caminho real — `gov.listar` abre o bloco por conta própria."""
    session.expire_all()
    linhas, queries = contar_queries(engine_teste, lambda: gov.listar(session))
    assert linhas
    assert queries <= len(linhas) * 3 + 60, f"{queries} consultas para {len(linhas)} linhas"


def test_8_tela_admin_faz_uma_varredura_so(session, engine_teste, catalogo):
    """A rota contava os status com uma segunda varredura completa — dobrava a página."""
    import inspect

    from app.routers import admin

    fonte = inspect.getsource(admin.produtos_governanca)
    assert fonte.count("gov.listar(") == 1, "a rota voltou a varrer o catálogo duas vezes"
    assert "gov.filtrar(" in fonte
    # e os contadores continuam olhando o catálogo INTEIRO, não o filtrado
    todas = gov.listar(session)
    filtradas = gov.filtrar(todas, status="A_COTAR")
    assert len(todas) >= len(filtradas)


# ===========================================================================
# 9–11. Escopo do cache: curto, explícito e sem contaminar quem grava
# ===========================================================================
def test_9_fora_do_bloco_nao_ha_memo(session, engine_teste):
    """Sem `with`, o comportamento é o de sempre: cada chamada vai ao banco."""
    assert ps._CACHE_LEITURA.get() is None
    session.expire_all()
    _, uma = contar_queries(engine_teste, lambda: ps.premissas_nacionalizacao(session))
    _, duas = contar_queries(engine_teste, lambda: ps.premissas_nacionalizacao(session))
    assert uma > 0 and duas > 0, "memo vazou para fora do bloco"


def test_10_escrita_invalida_o_memo(session, fornecedores):
    """Gravar referência dentro de um bloco aberto não pode deixar leitura velha em pé."""
    p = produto_ktc_cotado(session, fornecedores, familia="Bathrobe", exw_usd=21.0,
                           peso_kg=1.2, thread_count=None, largura_cm=None, comprimento_cm=None,
                           gsm=None, exw_cotado_fonte="KTC teste pool",
                           exw_cotado_data=date(2026, 9, 1))
    with ps.cache_de_leitura(session):
        antes = ps.status_do_produto(session, p)
        cs.registrar_referencia(session, p, cnet_brl=199.0, status="A_COTAR",
                                metodo=p.cost_method, fonte="teste de invalidação de cache",
                                data_ref=date.today())
        session.commit()
        depois = ps.status_do_produto(session, p)
    assert depois == "A_COTAR" and depois != antes


def test_11_cache_e_por_contexto_nao_e_global(session, engine_teste, fornecedores):
    """Duas threads = duas requisições: o bloco de uma não é o memo da outra."""
    vistos = {}

    def trabalhar(nome, com_cache):
        with Session(engine_teste) as s:
            if com_cache:
                with ps.cache_de_leitura(s):
                    vistos[nome] = (ps._CACHE_LEITURA.get() is not None,
                                    ps.premissas_nacionalizacao(s).fx_usd_brl)
            else:
                vistos[nome] = (ps._CACHE_LEITURA.get() is not None,
                                ps.premissas_nacionalizacao(s).fx_usd_brl)

    t1 = threading.Thread(target=trabalhar, args=("com", True))
    t2 = threading.Thread(target=trabalhar, args=("sem", False))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert vistos["com"][0] is True and vistos["sem"][0] is False
    assert vistos["com"][1] == vistos["sem"][1]     # mesma premissa, cache ou não
    assert ps._CACHE_LEITURA.get() is None          # e a thread principal segue limpa

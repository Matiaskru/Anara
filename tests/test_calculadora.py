"""Calculadora de custo KTC — mesmo motor da cotação, sem conta paralela."""
import os
import tempfile

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture(scope="module")
def ambiente():
    import app.db as db
    import app.seeds as seeds

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="anara-calc-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    originais = (db.engine, seeds.engine)
    db.engine = seeds.engine = engine
    SQLModel.metadata.create_all(engine)
    seeds.semear(verbose=False)
    yield engine
    db.engine, seeds.engine = originais
    os.unlink(caminho)


@pytest.fixture
def s(ambiente):
    with Session(ambiente) as sessao:
        yield sessao


def material(s, nome, listrado="plain"):
    from app.models import MaterialPreco
    return s.exec(select(MaterialPreco).where(MaterialPreco.material == nome)
                  .where(MaterialPreco.plain_or_stripe == listrado)).first()


def test_calcula_lencol_que_nao_esta_no_catalogo(s):
    from app.calculadora import calcular
    m = material(s, "300TC Sateen 100% Cotton")
    r = calcular(s, "Flat Sheet", 240, 260, material_id=m.id)
    assert r["calculavel"] is True
    assert r["custo"]["industrial"]["exw_usd"] == aprox(13.07, abs=0.01)
    assert r["custo"]["net_brl"] == aprox(73.09, abs=0.05)
    assert r["margem"]["margem_pct"] == aprox(0.18)      # lençol ≥ 300TC
    assert r["comercial"]["preco_negociado"] == aprox(146.73, abs=0.1)


def test_o_preco_da_calculadora_e_o_mesmo_da_cotacao(s):
    """Se o número divergisse do motor da cotação, a calculadora não serviria para nada."""
    from app.calculadora import calcular, produto_simulado
    from app import pricing_service as ps
    m = material(s, "250TC Sateen CVC 70/30")
    r = calcular(s, "Flat Sheet", 190, 250, material_id=m.id)
    produto = produto_simulado(s, "Flat Sheet", 190, 250, material_id=m.id)
    memoria = ps.memoria_do_preco(s, produto)
    assert r["comercial"]["preco_negociado"] == aprox(
        memoria["comercial"]["preco_negociado"])


def test_calcula_toalha_pela_taxa_por_kg(s):
    from app.calculadora import calcular
    r = calcular(s, "Bath Towel", 70, 140, gsm=500)
    assert r["calculavel"] is True
    assert r["custo"]["industrial"]["exw_usd"] == aprox(4.165, abs=0.01)  # 0,49 kg × 8,50
    assert r["margem"]["margem_pct"] == aprox(0.12)


def test_toalha_listrada_usa_a_taxa_de_piscina(s):
    from app.calculadora import calcular
    r = calcular(s, "Pool Towel", 90, 170, gsm=550, plain_or_stripe="stripe")
    assert r["custo"]["industrial"]["exw_usd"] == aprox(11.781, abs=0.01)


def test_familia_sem_formula_nao_inventa(s):
    from app.calculadora import calcular
    for familia in ("Fitted Sheet", "Pillow Case", "Bathrobe", "Duvet Insert"):
        r = calcular(s, familia, 50, 70)
        assert r["calculavel"] is False
        assert "não calcula" in r["motivo"]


def test_margem_pode_ser_forcada(s):
    from app.calculadora import calcular
    m = material(s, "300TC Sateen 100% Cotton")
    r = calcular(s, "Flat Sheet", 240, 260, material_id=m.id, margem_override=0.25)
    assert r["comercial"]["margem_liquida"] == aprox(0.25, abs=MARGEM_DO_CENTAVO)


def test_quantidade_multiplica_o_faturamento(s):
    from app.calculadora import calcular
    m = material(s, "300TC Sateen 100% Cotton")
    um = calcular(s, "Flat Sheet", 240, 260, material_id=m.id, quantidade=1)
    cem = calcular(s, "Flat Sheet", 240, 260, material_id=m.id, quantidade=100)
    assert cem["comercial"]["faturamento"] == aprox(
        um["comercial"]["faturamento"] * 100, rel=1e-9)


def test_salvar_no_catalogo_cria_produto_cotavel(s):
    from app.calculadora import salvar_no_catalogo
    from app.models import Produto
    m = material(s, "400TC Sateen 100% Cotton")
    produto = salvar_no_catalogo(s, "Flat Sheet", 200, 280, material_id=m.id)
    assert produto.id and produto.custo_unitario and produto.preco_base
    assert produto.precisa_revisao is False
    assert produto.nome.startswith("Lençol plano 200x280")
    # rodar de novo não duplica
    de_novo = salvar_no_catalogo(s, "Flat Sheet", 200, 280, material_id=m.id)
    assert de_novo.id == produto.id
    assert len(s.exec(select(Produto).where(Produto.largura_cm == 200)).all()) == 1


def test_pedido_sem_formula_entra_como_a_cotar(s):
    from app.calculadora import salvar_no_catalogo
    produto = salvar_no_catalogo(s, "Bathrobe", None, None, calculavel=False,
                                 observacao="Hotel Teste pediu")
    assert produto.custo_unitario is None
    assert produto.precisa_revisao is True
    assert "cotação da KTC" in produto.revisao_motivo
    assert produto.custo_ref_nota == "Hotel Teste pediu"


def test_opcoes_so_oferecem_tecido_que_a_ktc_cotou(s):
    from app.calculadora import opcoes
    o = opcoes(s)
    assert len(o["materiais"]) == 12
    assert all(m["price_usd_m2"] > 0 for m in o["materiais"])
    assert any(m["plain_or_stripe"] == "stripe" for m in o["materiais"])

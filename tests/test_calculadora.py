"""Calculadora de custo KTC — mesmo motor da cotação, sem conta paralela."""
import os
import tempfile

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture(scope="module")
def ambiente(tmp_path_factory):
    import app.db as db
    import app.seeds as seeds

    # `tmp_path_factory`, e não `tempfile`: o backup do banco nasce em `backups/`
    # AO LADO do arquivo do banco, então o diretório temporário do pytest é o que
    # mantém a suíte fora de `data/backups/` — e é o pytest que o limpa depois.
    caminho = str(tmp_path_factory.mktemp("anara-calc-banco") / "anara-teste.db")
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    originais = (db.engine, seeds.engine)
    db.engine = seeds.engine = engine
    SQLModel.metadata.create_all(engine)
    seeds.semear(verbose=False)
    yield engine
    db.engine, seeds.engine = originais
    # o diretório é do pytest: ele apaga o banco e os backups juntos


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
    # Política comercial de 16/09/2026: lençol ≥ 300TC 18% → 20%, comissão de formação 10%.
    assert r["margem"]["margem_pct"] == aprox(0.20)
    assert r["margem"]["comissao_formacao_pct"] == aprox(0.10)
    # preço = NET ÷ (1 − ICMS − PIS/COFINS − encargo − comissão − margem): a identidade do
    # gross-up com comissão fixa, sobre os componentes que a própria memória declara
    from app.dinheiro import D, dinheiro
    f = r["fiscal"]
    denominador = (1 - D(f["icms_pct"]) - D(f["pis_cofins_pct"]) - D(f["encargo_pct"])
                   - D("0.10") - D("0.20"))
    assert r["comercial"]["preco_negociado"] == aprox(
        dinheiro(D(r["custo"]["net_brl"]) / denominador), abs=0.01)


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
    assert r["margem"]["margem_pct"] == aprox(0.14)      # toalha: 12% → 14% em 16/09/2026


def test_toalha_listrada_usa_a_taxa_de_piscina(s):
    from app.calculadora import calcular
    r = calcular(s, "Pool Towel", 90, 170, gsm=550, plain_or_stripe="stripe")
    assert r["custo"]["industrial"]["exw_usd"] == aprox(11.781, abs=0.01)


def test_familia_sem_formula_nao_inventa(s):
    from app.calculadora import calcular
    # Fronha saiu desta lista: o §18 fechou a regra (Sessão 2) e a calculadora passou a usá-la.
    for familia in ("Fitted Sheet", "Bathrobe", "Duvet Insert"):
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


def test_produto_personalizado_entra_na_cotacao_com_preco_formado(s):
    """Fase 3C: o item da calculadora entra pelo caminho canônico de "adicionar item".

    Antes, a rota de salvar montava o item por conta própria e resolvia o cenário fiscal
    sem o produto — para KTC isso não resolve — e o item entrava com preço R$ 0,00, sem
    recomendado e sem a comissão da cotação reaplicada.
    """
    import asyncio
    import json
    from types import SimpleNamespace

    from app.models import Cliente, Cotacao, CotacaoItem, Usuario
    from app.routers.calculadora import salvar

    cliente = s.exec(select(Cliente)).first()
    if cliente is None:
        cliente = Cliente(nome="Hotel calculadora", cidade_uf="São Paulo", finalidade="REVENDA")
        s.add(cliente)
        s.commit()
        s.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="São Paulo",
                  contribuinte_icms=True, finalidade="REVENDA", condicao_pagamento="30",
                  freight_type="FOB", numero="CALC-3C-0001")
    s.add(cot)
    s.commit()
    s.refresh(cot)
    m = material(s, "300TC Sateen 100% Cotton")
    dona = Usuario(id=1, email="calc@anara.test", nome="Dona", senha_hash="h", papel="OWNER")

    class Req:
        def __init__(self, form):
            self.state = SimpleNamespace(usuario=dona)
            self._form = form
            self.url = SimpleNamespace(path="/")
            self.headers = {"accept": "application/json"}
            self.cookies = {}

        async def form(self):
            return self._form

    form = {"familia": "Flat Sheet", "largura_cm": "200", "comprimento_cm": "400",
            "material_id": str(m.id), "plain_or_stripe": "stripe", "quantidade": "3",
            "cotacao_id": str(cot.id), "calculavel": "sim"}
    resposta = json.loads(bytes(asyncio.run(salvar(Req(form), s)).body))
    item = s.get(CotacaoItem, resposta["item_id"])
    assert item.custo_unitario > 0
    assert item.preco_recomendado and item.preco_recomendado > item.custo_unitario
    assert item.preco_negociado == item.preco_recomendado
    assert item.faturamento == aprox(item.preco_negociado * 3)
    assert item.politica_comercial and item.icms_pct is not None


def test_fronha_calcula_pelo_paragrafo_18_com_abas_flap_e_festone(s):
    """A calculadora usa a mesma regra de fronha do catálogo (§18): construção muda o corte e o CMT."""
    from app.calculadora import calcular, salvar_no_catalogo
    m = material(s, "250TC Sateen CVC 70/30")
    standard = calcular(s, "Pillow Case", 50, 70, material_id=m.id, abas=0, flap_cm=20)
    assert standard["calculavel"] is True
    industrial = standard["custo"]["industrial"]
    assert industrial["exw_usd"] == aprox(2.0417, abs=0.002)          # backtest §18, 0 abas
    assert industrial["detalhes"]["corte_cm"].startswith("54x165")
    quatro = calcular(s, "Pillow Case", 50, 70, material_id=m.id, abas=4, flap_cm=20)
    assert quatro["custo"]["industrial"]["exw_usd"] == aprox(2.8148, abs=0.002)
    festone = calcular(s, "Pillow Case", 50, 70, material_id=m.id, abas=4, flap_cm=20, festone=True)
    assert festone["custo"]["industrial"]["exw_usd"] == aprox(2.9337, abs=0.002)
    # construção fora do §18 não é arredondada: o motor recusa
    uma = calcular(s, "Pillow Case", 50, 70, material_id=m.id, abas=1, flap_cm=20)
    assert uma["calculavel"] is False
    # construções diferentes viram SKUs diferentes no catálogo
    a = salvar_no_catalogo(s, "Pillow Case", 50, 70, material_id=m.id, abas=0, flap_cm=20)
    b = salvar_no_catalogo(s, "Pillow Case", 50, 70, material_id=m.id, abas=4, flap_cm=20, festone=True)
    assert a.id != b.id and a.custo_unitario < b.custo_unitario
    assert "4 abas" in (b.construcao or "") and "festonê" in (b.acabamento or "")

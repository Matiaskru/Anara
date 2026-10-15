"""Filtros só com o que está na Tabela comercial (15/10/2026, pedido do Matias).

"Quero calcular pela calculadora, tudo bem. Quero ver o preço via filtro: apenas o que tem na
tabela comercial." A busca da cotação, os filtros dela, a consulta da calculadora e a página
Produtos passam a mostrar só os produtos de `app/tabela_comercial.py`. Continua igual: a
calculadora (qualquer medida), a cotação que já tem item fora da tabela (o item aponta para o
produto pelo id) e a administração, que mostra o catálogo inteiro — e que, no mesmo dia, deixou
de cortar o catálogo em 400 SKUs.
"""
import json
import os
from itertools import count

import pytest
from sqlmodel import select

from app import calculadora as calc
from app import governanca_produtos as gov
from app import tabela_comercial as tc
from app.models import CostMethod, Fornecedor, MaterialPreco, Produto
from conftest import RequestFalsa, _novo_usuario
from tests.crisis.conftest import add_item, chamar, nova_cotacao

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEQ = count(1)


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


def html(resposta):
    return bytes(resposta.body).decode()


def novo_produto(session, familia, nome):
    decor = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).first()
    p = Produto(sku_key=f"TC-{next(_SEQ)}-{nome}", nome=nome, familia=familia, custo_unitario=100.0,
                preco_base=200.0, fornecedor_id=decor.id if decor else None,
                especificacao="0,60x1,90 · tricô", cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def dois(session, na_tabela_comercial):
    """Um produto na tabela e outro fora, com nome parecido e família própria cada um."""
    dentro = novo_produto(session, "FamiliaNaTabelaTC", "Peseira zebrina na tabela")
    fora = novo_produto(session, "FamiliaForaTC", "Peseira zebrina fora da tabela")
    na_tabela_comercial(dentro)
    return dentro, fora


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "OWNER"])
def test_busca_da_cotacao_e_da_calculadora_so_traz_o_que_esta_na_tabela(session, dois, papel):
    from app.routers.produtos import buscar
    dentro, fora = dois
    ids = {x["id"] for x in corpo(chamar(buscar, RequestFalsa(_novo_usuario(papel)), q="zebrina",
                                          session=session))}
    assert dentro.id in ids and fora.id not in ids
    # o filtro (sem texto) também: família de produto fora da tabela não devolve nada
    assert corpo(chamar(buscar, RequestFalsa(_novo_usuario(papel)), familia="FamiliaForaTC",
                        session=session)) == []


def test_filtros_da_cotacao_vem_so_da_tabela(session, dois):
    from app.routers.produtos import facetas
    familias = {f["valor"] for f in corpo(chamar(facetas, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
                                                 session=session))["familias"]}
    assert "FamiliaNaTabelaTC" in familias and "FamiliaForaTC" not in familias


def test_pagina_produtos_so_mostra_o_que_esta_na_tabela(session, dois):
    from app.routers.produtos import listar
    dentro, fora = dois
    pagina = html(chamar(listar, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")), q="zebrina",
                         session=session))
    assert dentro.nome in pagina and fora.nome not in pagina


def test_administracao_continua_com_o_catalogo_inteiro(session, dois):
    from app.routers.admin import produtos_governanca
    dentro, fora = dois
    pagina = html(chamar(produtos_governanca, RequestFalsa(_novo_usuario("OWNER")), q="zebrina",
                         session=session))
    assert dentro.nome in pagina and fora.nome in pagina


def test_administracao_nao_corta_o_catalogo_em_400(session, monkeypatch):
    """Regressão: a tela chamava `gov.listar` com o limite padrão de 400 e, com o catálogo maior
    que isso, SKUs sumiam da tela e dos contadores."""
    from app.routers import admin
    pedido = {}

    def listar_falso(sess, **kw):
        pedido.update(kw)
        return []
    monkeypatch.setattr(admin.gov, "listar", listar_falso)
    chamar(admin.produtos_governanca, RequestFalsa(_novo_usuario("OWNER")), session=session)
    assert "limite" in pedido and pedido["limite"] is None


def test_listar_sem_limite_traz_todo_o_catalogo_ativo(session):
    ativos = session.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
    assert len(gov.listar(session, limite=None)) == len(ativos)


def test_cotacao_com_produto_fora_da_tabela_continua_funcionando(session, dois):
    """Esconder do filtro não mexe no que já está em cotação: o item aponta para o produto pelo id."""
    _dentro, fora = dois
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, fora)
    assert it.produto_id == fora.id and it.nome_produto


def test_lista_da_tabela_e_coerente_com_os_itens_calculados():
    calculados = json.load(open(os.path.join(RAIZ, "scripts", "tabela_comercial_2026_10_15",
                                             "itens_calculados.json"), encoding="utf-8"))
    codigos = list(tc.SKUS.values())
    assert len(tc.SKUS) == 187 and len(set(codigos)) == len(codigos)
    assert {c["sku_key"] for c in calculados} == {k for k in tc.SKUS if k.startswith("CALC · ")}
    assert {c["codigo"] for c in calculados} <= set(codigos) and len(calculados) == 37
    # N50/N51 saíram da tabela (rev. 5); BL-001..003 estão fora da oferta
    assert not {"N50", "N51", "BL-001", "BL-002", "BL-003"} & set(codigos)


def test_na_tabela_ignora_espaco_repetido_na_chave():
    chave = next(k for k in tc.SKUS if "  " in k)
    assert tc.na_tabela(Produto(sku_key=" ".join(chave.split())))


@pytest.mark.parametrize("familia,kw", [
    ("Top Sheet", dict(largura_cm=190, comprimento_cm=250)),
    ("Pillow Case", dict(largura_cm=50, comprimento_cm=70, abas=3, flap_cm=20)),
    ("Pool Towel", dict(largura_cm=90, comprimento_cm=170, gsm=600, plain_or_stripe="stripe",
                        composicao_toalha="100/0")),
])
def test_chave_da_calculadora_e_a_que_salvar_grava(session, familia, kw):
    """`sku_key_da_calculadora` (usada pela lista da tabela e pelo script de produção) dá
    exatamente a chave que `salvar_no_catalogo` grava."""
    m = session.exec(select(MaterialPreco).where(MaterialPreco.ativo == True)   # noqa: E712
                     .order_by(MaterialPreco.id)).first()
    kw = dict(kw)
    if familia != "Pool Towel":
        kw["material_id"] = m.id
    simulado = calc.produto_simulado(session, familia, kw["largura_cm"], kw["comprimento_cm"],
                                     kw.get("material_id"), kw.get("gsm"), kw.get("plain_or_stripe", "plain"),
                                     abas=kw.get("abas"), flap_cm=kw.get("flap_cm"),
                                     composicao_toalha=kw.get("composicao_toalha"))
    chave = calc.sku_key_da_calculadora(simulado, familia, kw["largura_cm"], kw["comprimento_cm"], kw.get("gsm"),
                                        kw.get("plain_or_stripe", "plain"),
                                        composicao_toalha=kw.get("composicao_toalha"))
    salvo = calc.salvar_no_catalogo(session, familia, kw["largura_cm"], kw["comprimento_cm"],
                                    material_id=kw.get("material_id"), gsm=kw.get("gsm"),
                                    plain_or_stripe=kw.get("plain_or_stripe", "plain"), abas=kw.get("abas"),
                                    flap_cm=kw.get("flap_cm"), composicao_toalha=kw.get("composicao_toalha"))
    assert salvo.sku_key == chave

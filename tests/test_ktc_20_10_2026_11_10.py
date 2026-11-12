"""Cotação KTC ANARA20102026 (20/10/2026) no sistema — 10/11/2026.

`app/ktc_20_10_2026_itens.json` é a fonte única de três coisas: o custo novo dos 21 SKUs 250 fios
70/30 da tabela, os 28 itens novos do catálogo (tapetes N53–N57, 600 fios N58–N80) e — via
`precos_ktc_20_10.py` — o preço fixo deles na Tabela de preços. Estes testes amarram as três pontas.
A paridade preço da cotação × preço fixo foi conferida numa cópia do runtime a 5,19 (9.072 células) e o
`aplicar.py` confere de novo em produção.
"""
import json
import os
import sys

import pytest

from app import tabela_comercial as tc
from app import tabela_precos as tp
from app.tabela_comercial_layout import LINHAS

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA = os.path.join(RAIZ, "scripts", "ktc_20_10_2026_11_10")
DADOS = json.load(open(os.path.join(RAIZ, "app", "ktc_20_10_2026_itens.json"), encoding="utf-8"))
ITENS_LAYOUT = {x["codigo"]: x for x in LINHAS if x["tipo"] == "item"}
#: SN → EXW US$ da cotação (PDF "Anara  Quotation 20-10-2026.pdf", linhas 1–51)
COTACAO = {1: 9.89, 2: 12.47, 3: 20.42, 4: 12.20, 5: 15.14, 6: 26.14, 7: 13.12, 8: 16.74, 9: 28.55, 10: 13.81,
           11: 17.27, 12: 30.01, 13: 14.96, 14: 18.91, 15: 29.96, 16: 2.25, 17: 3.19, 18: 3.31, 19: 3.42, 20: 2.65,
           21: 3.65, 22: 3.77, 23: 3.89, 24: 13.82, 25: 17.25, 26: 28.54, 27: 17.13, 28: 21.08, 29: 36.78,
           30: 18.46, 31: 23.38, 32: 40.24, 33: 19.45, 34: 24.14, 35: 42.34, 36: 21.11, 37: 26.50, 38: 42.27,
           39: 2.96, 40: 4.03, 41: 4.20, 42: 4.37, 43: 3.53, 44: 4.70, 45: 4.87, 46: 5.03, 47: 9.21, 48: 11.99,
           49: 18.21, 50: 27.61, 51: 31.66}


def _secao(codigo):
    secao = None
    for x in LINHAS:
        if x["tipo"] == "secao":
            secao = x["texto"]
        elif x["codigo"] == codigo:
            return secao


def test_todo_exw_e_o_da_cotacao_e_cada_linha_entra_uma_vez():
    usados = [x["sn"] for x in DADOS["atualizar"] + DADOS["novos"]]
    assert len(usados) == len(set(usados)) == 49
    assert all(COTACAO[x["sn"]] == x["exw_usd"] for x in DADOS["atualizar"] + DADOS["novos"])
    # fora: SN 19 (fronha 50x70 4 abas) e 20 (50x90 sem abas) — 250 70/30 que não estão na tabela
    assert set(COTACAO) - set(usados) == {19, 20}


def test_atualizar_sao_exatamente_os_250_70_30_da_tabela():
    codigos_250 = {c for c in ITENS_LAYOUT if _secao(c) in ("250 fios 70% algodão e 30% poliester acabamento Classic",
                                                           "250 fios 70% algodão e 30% poliéster")}
    assert {x["codigo"] for x in DADOS["atualizar"]} == codigos_250 and len(codigos_250) == 21
    assert all(tc.SKUS[x["sku_key"]] == x["codigo"] for x in DADOS["atualizar"])


def test_novos_estao_na_tabela_com_preco_fixo_e_chave_propria():
    novos = DADOS["novos"]
    assert [x["codigo"] for x in novos] == [f"N{n}" for n in range(53, 81)]
    rev5 = {" ".join(k.split()) for k in tc.SKUS if k not in tc.SKUS_KTC_20_10}
    assert not {" ".join(x["sku_key"].split()) for x in novos} & rev5
    for x in novos:
        linha = ITENS_LAYOUT[x["codigo"]]
        assert (linha["descricao"], linha["medida"], linha["especificacao"]) == \
               (x["descricao"], x["medida"], x["especificacao"]) and not linha["sob_consulta"]
        assert len(tp.PRECOS[x["codigo"]]) == 54                     # 27 UFs × contribuinte
        assert all(v is not None for linha in tp.PRECOS[x["codigo"]].values() for v in linha)
        assert tc.SKUS_KTC_20_10[x["sku_key"]] == x["codigo"]


@pytest.fixture
def itens():
    sys.path.insert(0, PASTA)
    try:
        import itens as modulo
        yield modulo
    finally:
        sys.path.remove(PASTA)


@pytest.mark.parametrize("codigo,familia,fios,construcao", [
    ("N58", "Top Sheet", 600, None), ("N66", "Duvet Cover", 600, None),
    ("N73", "Pillow Case", 600, "standard"), ("N75", "Pillow Case", 600, "3 abas"),
    ("N55", "Bath Mat", None, None),
])
def test_produto_novo_tem_a_identidade_do_item(session, itens, codigo, familia, fios, construcao):
    dados = itens.carregar()
    item = next(x for x in dados["novos"] if x["codigo"] == codigo)
    p = itens.produto_novo(session, item, dados)
    assert p not in session                                       # montado em memória, nada gravado
    assert p.familia == familia and p.thread_count == fios and p.cost_method == "KTC_QUOTED"
    assert (p.exw_cotado_usd, p.exw_cotado_data) == (item["exw_usd"], dados["data_ref"])
    assert p.exw_cotado_fonte == f"{dados['documento']} · SN {item['sn']}"
    if fios:
        assert p.material_ref == "600TC Sateen 100% Cotton" and "600 fios" in p.nome
    else:
        assert p.gsm == 2050 and "2050 g/m²" in p.nome
    if construcao:
        assert p.construcao == construcao and p.fechamento == "flap 20 cm"

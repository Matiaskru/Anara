"""Tabela de preços dentro do sistema (10/11/2026).

Pedido do Matias: "tem que ser exatamente os produtos dessa tabela, nada mais nada menos" — as 191 linhas da
Tabela comercial REV5, na ordem da planilha, com os 10 "sob consulta" — mais os itens da cotação KTC 20/10:
tapetes ("até o tapete também") e 600 fios ("incluo os 600 fios na tabela? pode"). O preço é FIXO, o publicado ("a regra agora é manter o preço, não a margem"): câmbio
ou custo novos não mexem na página. A vendedora não vê economia.
"""
import openpyxl
import pytest

from app import config_service as cfg
from app import tabela_precos as tp
from app.dinheiro import D, para_float
from app.permissoes import PrecisaLogin
from app.pricing_engine import preco_por_desconto
from app.routers.tabela_precos import pagina
from app.tabela_comercial_layout import LINHAS
from conftest import RequestFalsa, _novo_usuario
from tests.crisis.conftest import chamar

ITENS = [x for x in LINHAS if x["tipo"] == "item"]
TAPETES = ["N53", "N54", "N55", "N56", "N57"]
LENCOIS_600 = [f"N{n}" for n in range(58, 73)]
FRONHAS_600 = [f"N{n}" for n in range(73, 81)]
SOB_CONSULTA = {"R029", "R030", "R031", "P042", "P043", "P044", "P045", "P046", "N49", "N52"}
PALAVRAS_ECONOMICAS = ("custo", "cnet", "exw", "margem", "lucro", "markup", "câmbio", "memória")
REV5 = "/Users/matiaskrueder/Downloads/Tabela comercial Anara 2026 - 15-10 REV5.xlsx"


def _html(session, usuario, **params):
    r = chamar(pagina, RequestFalsa(usuario, path="/tabela-precos"), session=session, **params)
    return bytes(r.body).decode()


def _linha(codigo, *cenario):
    return next(x for x in tp.montar(*cenario) if x.codigo == codigo)


def _secao_de(codigo):
    secao = None
    for x in LINHAS:
        if x["tipo"] == "secao":
            secao = x
        elif x["codigo"] == codigo:
            return secao


def test_linhas_sao_a_rev5_mais_os_itens_da_cotacao_ktc_20_10():
    codigos = [x["codigo"] for x in ITENS]
    assert len(codigos) == 191 + 28 and len(set(codigos)) == len(codigos)
    assert codigos[:3] == ["P001", "P002", "P003"] and codigos[-6:] == ["N52"] + TAPETES
    # 600 fios: lençóis/capas logo antes da 400 fios; fronhas no fim das fronhas Classic
    i = codigos.index("N58")
    assert codigos[i:i + 16] == LENCOIS_600 + ["P069"]
    i = codigos.index("N73")
    assert codigos[i - 1] == "R208" and codigos[i:i + 9] == FRONHAS_600 + ["P149"]
    assert {_secao_de(c)["texto"] for c in LENCOIS_600 + FRONHAS_600} == {"600 fios 100% algodão"}
    assert {x["codigo"] for x in ITENS if x["sob_consulta"]} == SOB_CONSULTA
    assert [x["texto"] for x in LINHAS if x["tipo"] == "secao"][-1] == "TAPETES DE BANHEIRO"
    # todo item com preço tem preço fixo publicado
    assert all(x["codigo"] in tp.PRECOS for x in ITENS if not x["sob_consulta"])
    assert len(tp.ESTADOS) == 27 and tp.PRAZOS[1] == "30"


def test_vendedora_ve_todos_os_codigos_na_ordem_e_nenhuma_economia(session):
    html = _html(session, _novo_usuario("VENDEDOR_INTERNO"))
    posicoes = [html.find(f'<td class="codigo">{x["codigo"]}</td>') for x in ITENS]
    assert -1 not in posicoes and posicoes == sorted(posicoes)
    assert html.count('<tr class="item">') == 219
    assert html.count("Preço sob consulta") == 10 and "Sem preço neste cenário" not in html
    texto = html.lower()
    assert not [p for p in PALAVRAS_ECONOMICAS if p in texto]


@pytest.mark.parametrize("codigo,estado,contrib,prazo,b2b", [
    ("P001", "São Paulo", False, "30", 67.62),          # toalha de banho Cairo
    ("P112", "São Paulo", False, "30", 119.93),         # lençol de baixo 250 70/30
    ("N55", "São Paulo", False, "30", 193.98),          # tapete 0,61x0,91 — o que a Ines passou ao cliente
    ("N56", "São Paulo", False, "30", 292.89),          # tapete 0,69x1,22
    ("N58", "São Paulo", False, "30", 169.76),          # lençol de cima 600 fios single (= planilha de 30/10)
    ("N80", "São Paulo", False, "30", 59.70),           # fronha 600 fios 4 abas 50x90
])
def test_preco_fixo_publicado(codigo, estado, contrib, prazo, b2b):
    linha = _linha(codigo, estado, contrib, prazo)
    tabela = D(b2b) * 2
    assert linha.situacao == "PRECO" and linha.b2b == b2b and linha.tabela == para_float(tabela)
    assert linha.escada == [para_float(preco_por_desconto(tabela, D(d))) for d in ("0.10", "0.20", "0.30", "0.40")]


def test_cada_celula_bate_com_a_planilha():
    """B2B, tabela e escada de toda célula da BASE da REV5 (27 UFs × contribuinte × 6 prazos)."""
    import os
    if not os.path.exists(REV5):
        pytest.skip("planilha REV5 não está nesta máquina")
    base = openpyxl.load_workbook(REV5, data_only=True)["BASE"]
    conferidas = 0
    for r in base.iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        for k, prazo in enumerate(tp.PRAZOS):
            bloco = r[4 + 6 * k:10 + 6 * k]
            if not all(isinstance(x, (int, float)) for x in bloco):
                continue
            b2b = tp.preco_b2b(r[1], r[2], r[3] == "SIM", prazo)
            tabela = D(b2b) * 2
            escada = [float(preco_por_desconto(tabela, D(d))) for d in ("0.10", "0.20", "0.30", "0.40")]
            assert [float(tabela), *escada, b2b] == pytest.approx([float(x) for x in bloco], abs=0.001), (r[0], prazo)
            conferidas += 1
    assert conferidas == 58644


def test_cambio_novo_nao_mexe_no_preco(session):
    antes = [(x.codigo, x.b2b) for x in tp.montar("São Paulo", False, "30")]
    cfg.definir(session, "fx_usd_brl", valor_num=9.99, fonte="teste: câmbio absurdo")
    try:
        assert [(x.codigo, x.b2b) for x in tp.montar("São Paulo", False, "30")] == antes
    finally:
        cfg.definir(session, "fx_usd_brl", valor_num=5.19, fonte="teste: volta")


def test_sob_consulta_e_cenario_desconhecido(session):
    assert all(_linha(c, "Bahia", True, "30/60/90").situacao == "SOB_CONSULTA" for c in SOB_CONSULTA)
    html = _html(session, _novo_usuario("VENDEDOR_INTERNO"), estado="Narnia", contribuinte="talvez",
                 condicao="CARTAO")
    assert '<option value="São Paulo" selected>' in html
    assert '<option value="nao" selected>' in html
    assert '<option value="30" selected>' in html
    assert 'value="CARTAO"' not in html                 # cartão: consultar o administrativo


def test_sem_login_nao_abre(session):
    with pytest.raises(PrecisaLogin):
        _html(session, None)

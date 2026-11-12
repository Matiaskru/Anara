"""Tabela de preços: escolher as colunas de preço e imprimir / salvar PDF (pedido da Ines, 12/11/2026).

"Seria possível deixar ela com opção de ocultar algumas colunas de desconto para impressão em PDF? ... enviarmos
para algumas redes que pedem para processo de homologação, então não queremos passar o percentual do desconto e
todos os preços." A tela continua igual para a vendedora; a impressão leva só as colunas escolhidas, sem a
comissão, sem a nota interna e sem os filtros, com o cenário (destino, contribuinte, pagamento) no cabeçalho.
"""
import re

from app.routers.tabela_precos import pagina
from conftest import RequestFalsa, _novo_usuario
from tests.crisis.conftest import chamar


def _html(session, **params):
    r = chamar(pagina, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO"), path="/tabela-precos"), session=session,
               **params)
    return bytes(r.body).decode()


def test_cada_coluna_de_preco_tem_caixa_e_classe_propria(session):
    html = _html(session)
    assert [int(x) for x in re.findall(r'data-coluna="(\d)" checked', html)] == list(range(6))
    for i in range(6):
        assert f'<th class="num pc pc-{i}">' in html
        assert f".tp-tabela.sem-{i} .pc-{i} {{ display: none; }}" in html
    # cada linha com preço tem as 6 células marcadas; a de "sob consulta" se ajusta às colunas visíveis
    assert html.count('<td class="num pc pc-0">') == html.count('<td class="num b2b pc pc-5">') == 219 - 10
    assert "Imprimir / salvar PDF" in html and "window.print()" in html


def test_impressao_tem_o_cenario_e_nao_leva_comissao_nem_nota_interna(session):
    html = _html(session, estado="Bahia", contribuinte="sim", condicao="30/60/90")
    cabecalho = re.search(r'<div class="tp-impressao">(.*?)</div>', html, re.S).group(1)
    assert "Destino: Bahia · Contribuinte de ICMS · Pagamento: 30/60/90" in cabecalho
    assert "Frete nacional não incluído" in cabecalho
    assert not re.search(r"comiss|B2B|autonomia|desconto", cabecalho, re.I)
    estilo = re.search(r"@media print \{(.*?)\n  \}\n</style>", html, re.S).group(1)
    assert ".filters, .tp-nota, .tp-colunas" in estilo           # filtros, nota interna e caixas não imprimem
    assert ".tp-tabela th .com" in estilo                          # o "% com." do cabeçalho não imprime
    assert ".tp-tabela.uma-coluna th .rot-unico { display: inline; }" in estilo   # uma coluna só: "Preço (R$)"
    assert html.count('<span class="so-impressao">Sob consulta</span>') == 10

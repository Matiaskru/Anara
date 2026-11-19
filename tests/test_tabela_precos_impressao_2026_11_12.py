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
    # a nota "· COMISSÃO FIXA 5%" das seções Daune/Decor é da vendedora: na tela aparece, na impressão não
    assert html.count('<span class="so-tela"> · COMISSÃO FIXA 5% EM QUALQUER PREÇO</span>') == 8
    assert "PESEIRAS DE TRICOT<span" in html


# --- Excel para mandar às redes (pedido da Ines, 12/11/2026) ---------------------------------------------
def _excel(session, usuario=None, **params):
    import io

    import openpyxl

    from app.routers.tabela_precos import excel
    r = chamar(excel, RequestFalsa(usuario or _novo_usuario("VENDEDOR_INTERNO"), path="/tabela-precos/excel"),
               session=session, **params)
    return r, openpyxl.load_workbook(io.BytesIO(r.body))


def test_excel_com_uma_coluna_nao_tem_percentual_nem_comissao_nem_aba_escondida(session):
    from app import tabela_precos as tp
    r, wb = _excel(session, estado="Bahia", contribuinte="sim", condicao="30/60/90", colunas="0")
    assert r.media_type.endswith("spreadsheetml.sheet") and "attachment" in r.headers["content-disposition"]
    assert wb.sheetnames == ["Tabela de preços"]                       # nada escondido: só a aba que se vê
    ws = wb.active
    textos = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    assert "Destino: Bahia · Contribuinte de ICMS · Pagamento: 30/60/90" in textos
    assert not re.search(r"comiss|com\.|B2B|autonomia|−\d+%", textos, re.I)
    assert not any(isinstance(c.value, str) and c.value.startswith("=") for row in ws.iter_rows() for c in row)
    cab = next(row for row in ws.iter_rows(values_only=True) if row[0] == "Código")
    assert list(cab) == ["Código", "Descrição", "Medidas", "Composição / especificação", "Preço (R$)"]
    precos = {row[0]: row[4] for row in ws.iter_rows(values_only=True) if row[0] in ("P001", "N55", "N58", "R029")}
    esperado = {x.codigo: x for x in tp.montar("Bahia", True, "30/60/90")}
    assert precos["P001"] == esperado["P001"].tabela and precos["N58"] == esperado["N58"].tabela
    assert precos["R029"] == "Sob consulta"


def test_excel_leva_so_as_colunas_marcadas_e_todos_os_produtos(session):
    r, wb = _excel(session, colunas="1,3")
    linhas = list(wb.active.iter_rows(values_only=True))
    cab = next(row for row in linhas if row[0] == "Código")
    assert list(cab)[4:] == ["−10%", "−30%"]
    assert sum(1 for row in linhas if isinstance(row[0], str) and re.match(r"^(P|N|R|E|BR-)\d", row[0])) == 219
    assert "São Paulo" in r.headers["content-disposition"] or "S%C3%A3o%20Paulo" in r.headers["content-disposition"]


def test_excel_coluna_invalida_vira_todas_e_sem_login_nao_baixa(session):
    import pytest

    from app.permissoes import PrecisaLogin
    _, wb = _excel(session, colunas="x,9")
    cab = next(row for row in wb.active.iter_rows(values_only=True) if row[0] == "Código")
    assert len(cab) == 4 + 6
    from app.routers.tabela_precos import excel
    with pytest.raises(PrecisaLogin):
        chamar(excel, RequestFalsa(None, path="/tabela-precos/excel"), session=session)

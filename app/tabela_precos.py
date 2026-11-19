"""Tabela de preços das vendedoras, dentro do sistema (10/11/2026).

As linhas são EXATAMENTE as da Tabela comercial publicada (`app/tabela_comercial_layout.py`, gerado do xlsx):
mesmas seções, mesma ordem, mesmos textos, mesmos itens "sob consulta" — mais os itens da cotação KTC 20/10
que o Matias mandou incluir (tapetes de banheiro e 600 fios 100% algodão).

**O preço é FIXO** (decisão do Matias, 10/11/2026: "a regra agora é manter o preço, não a margem"): o B2B
publicado na planilha, por código, estado, contribuinte e prazo (`app/tabela_comercial_precos.json`). Câmbio
e custo mudam a margem, não esta página. Tabela cheia = 2 × B2B; a escada −10…−40% sai de
`preco_por_desconto`, como na planilha (o gerador conferiu as duas regras em todas as células).

Só preço comercial: nenhum custo, margem ou memória entra aqui.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.dinheiro import D, para_float
from app.pricing_engine import preco_por_desconto
from app.tabela_comercial_layout import LINHAS, VERSAO

DESCONTOS = ("0.10", "0.20", "0.30", "0.40")
_ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabela_comercial_precos.json")
with open(_ARQUIVO, encoding="utf-8") as _f:
    _DADOS = json.load(_f)
PRAZOS: List[str] = _DADOS["prazos"]
PRECOS: dict = _DADOS["precos"]
ESTADOS: List[str] = sorted({chave.split("|")[0] for por_cenario in PRECOS.values() for chave in por_cenario})


@dataclass
class LinhaTabela:
    tipo: str                                  # "secao" | "item"
    texto: str = ""                            # seção
    texto_cliente: str = ""                    # seção sem a nota interna de comissão (PDF/Excel para fora)
    nivel: int = 1
    codigo: str = ""
    descricao: str = ""
    medida: str = ""
    especificacao: str = ""
    situacao: str = "PRECO"                    # PRECO | SOB_CONSULTA | SEM_PRECO
    tabela: Optional[float] = None
    escada: List[Optional[float]] = field(default_factory=list)   # −10, −20, −30, −40
    b2b: Optional[float] = None


def preco_b2b(codigo: str, estado: str, contribuinte: bool, condicao: str) -> Optional[float]:
    """B2B fixo publicado, ou None (sob consulta / cenário sem preço na tabela)."""
    if condicao not in PRAZOS:
        return None
    linha = PRECOS.get(codigo, {}).get(f"{estado}|{'SIM' if contribuinte else 'NÃO'}")
    return linha[PRAZOS.index(condicao)] if linha else None


def montar(estado: str, contribuinte: bool, condicao: str) -> List[LinhaTabela]:
    linhas = []
    for item in LINHAS:
        if item["tipo"] == "secao":
            linhas.append(LinhaTabela(tipo="secao", texto=item["texto"], nivel=item["nivel"],
                                      texto_cliente=secao_para_cliente(item["texto"])))
            continue
        linha = LinhaTabela(tipo="item", codigo=item["codigo"], descricao=item["descricao"],
                            medida=item["medida"], especificacao=item["especificacao"])
        b2b = None if item["sob_consulta"] else preco_b2b(item["codigo"], estado, contribuinte, condicao)
        if item["sob_consulta"]:
            linha.situacao = "SOB_CONSULTA"
        elif b2b is None:
            linha.situacao = "SEM_PRECO"
        else:
            tabela = D(b2b) * 2
            linha.b2b = para_float(D(b2b))
            linha.tabela = para_float(tabela)
            linha.escada = [para_float(preco_por_desconto(tabela, D(d))) for d in DESCONTOS]
        linhas.append(linha)
    return linhas


#: "PESEIRAS DE TRICOT · COMISSÃO FIXA 5% EM QUALQUER PREÇO": a nota de comissão é da vendedora, não do cliente
_NOTA_INTERNA_SECAO = re.compile(r"\s*·\s*COMISS[ÃA]O\b.*$", re.IGNORECASE)


def secao_para_cliente(texto: str) -> str:
    """Título da seção sem a nota interna de comissão — é o que vai no PDF impresso e no Excel para a rede."""
    return _NOTA_INTERNA_SECAO.sub("", texto or "").strip()


def versao() -> str:
    return VERSAO


#: rótulo de cada coluna de preço, na ordem da página (o "% com." é interno e não vai para fora)
ROTULOS_COLUNAS = ("Tabela cheia", "−10%", "−20%", "−30%", "−40%", "B2B · −50%")


def colunas_do_pedido(texto: str) -> List[int]:
    """'0,5' → [0, 5]. Inválido ou vazio → todas (nunca uma planilha sem preço)."""
    try:
        cols = sorted({int(x) for x in (texto or "").split(",") if x.strip() != ""})
    except ValueError:
        cols = []
    cols = [c for c in cols if 0 <= c < len(ROTULOS_COLUNAS)]
    return cols or list(range(len(ROTULOS_COLUNAS)))


def planilha_para_cliente(estado: str, contribuinte: bool, condicao: str, colunas: List[int],
                          rotulo_condicao: str, hoje: str) -> bytes:
    """Excel para mandar a cliente/rede (pedido da Ines, 12/11/2026: "planilha em excel para envio às redes").

    Valores FIXOS de um cenário só (sem fórmula, sem aba escondida, sem base de preços): quem recebe vê só as
    colunas escolhidas. Sem comissão, sem nota interna. Uma coluna só → título "Preço (R$)", sem percentual.
    """
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Tabela de preços"
    azul, creme = "30354F", "ECE4DD"
    ncol = 4 + len(colunas)
    ws.append(["Anara — Tabela de preços"])
    ws["A1"].font = Font(bold=True, size=14, color=azul)
    ws.append([f"Destino: {estado} · {'Contribuinte de ICMS' if contribuinte else 'Não contribuinte de ICMS'} · "
               f"Pagamento: {rotulo_condicao} · {hoje}"])
    ws.append(["Valores em R$ por unidade, para o estado de destino e a condição de pagamento indicados. "
               "Frete nacional não incluído."])
    for r in (2, 3):
        ws.cell(r, 1).font = Font(size=10, color="555555")
    ws.append([])
    rotulos = ["Preço (R$)"] if len(colunas) == 1 else [ROTULOS_COLUNAS[c] for c in colunas]
    ws.append(["Código", "Descrição", "Medidas", "Composição / especificação"] + rotulos)
    cab = ws.max_row
    for c in range(1, ncol + 1):
        cel = ws.cell(cab, c)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor=azul)
        cel.alignment = Alignment(horizontal="center" if c > 4 else "left", vertical="center", wrap_text=True)
    for linha in montar(estado, contribuinte, condicao):
        if linha.tipo == "secao":
            ws.append([linha.texto_cliente])
            r = ws.max_row
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            ws.cell(r, 1).font = Font(bold=True, color="FFFFFF" if linha.nivel == 1 else azul)
            ws.cell(r, 1).fill = PatternFill("solid", fgColor=azul if linha.nivel == 1 else creme)
            continue
        base = [linha.codigo, linha.descricao, linha.medida, linha.especificacao]
        if linha.situacao == "PRECO":
            valores = [linha.tabela, *linha.escada, linha.b2b]
            ws.append(base + [valores[c] for c in colunas])
            for c in range(5, ncol + 1):
                ws.cell(ws.max_row, c).number_format = 'R$ #,##0.00'
        else:
            ws.append(base + ["Sob consulta"] + [None] * (len(colunas) - 1))
            r = ws.max_row
            if len(colunas) > 1:
                ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=ncol)
            ws.cell(r, 5).alignment = Alignment(horizontal="center")
            ws.cell(r, 5).font = Font(italic=True, color="777777")
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    for col, largura in zip("ABCD", (9, 42, 16, 44)):
        ws.column_dimensions[col].width = largura
    for i in range(len(colunas)):
        ws.column_dimensions[chr(ord("E") + i)].width = 15
    ws.freeze_panes = ws.cell(cab + 1, 1)
    ws.print_title_rows = f"{cab}:{cab}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    saida = BytesIO()
    wb.save(saida)
    return saida.getvalue()

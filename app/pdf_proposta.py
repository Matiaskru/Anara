"""PROPOSTA COMERCIAL ANARA — o documento que o cliente recebe (Fase 3C).

Este módulo só sabe desenhar. Ele recebe três dicionários já **filtrados** por
`app.pdf_bridge` (`header`, `items`, `totals`) e nunca consulta banco, motor ou cotação:
se um dado não estiver no dicionário, ele não existe para o papel. É o que permite provar,
em teste, campo a campo, que nada interno chega aqui.

Identidade: Midnight Blue, Copper, Sand Beige e Cotton, com as fontes Didot e Futura que já
estavam licenciadas e embarcadas em `assets/fonts` (nada novo é distribuído).

Estrutura:

    faixa de cabeçalho     ANARA · PROPOSTA COMERCIAL · nº / revisão / data / validade
    bloco do cliente       cliente · CNPJ · cidade/UF · contato · unidade/local de entrega
    tabela de itens        # · produto/especificação · qtd. · valor unitário · total
    fechamento             subtotal · frete · TOTAL DA PROPOSTA (nas últimas linhas da tabela,
                           para nunca ficarem órfãos do último item)
    condições comerciais   pagamento · prazo de entrega · frete · validade · local de entrega
    observação             só a observação PARA O CLIENTE, quando houver
    termos e condições     texto congelado na cotação
    contato comercial      responsável, quando houver
    aceite                 linha de assinatura
    rodapé                 aviso curto · "Página X de Y"

Rascunho sai com marca d'água forte em toda página: RASCUNHO — NÃO ENVIAR AO CLIENTE.
"""
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from app.dinheiro import D0, dinheiro

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo", "anara_mark_copper.png")
FONTS_DIR = os.path.join(BASE_DIR, "assets", "fonts")

MIDNIGHT = colors.HexColor("#30354F")
COPPER = colors.HexColor("#C49281")
COPPER_DARK = colors.HexColor("#A97A68")
SAND = colors.HexColor("#D6C8BE")
SAND_SOFT = colors.HexColor("#EFE8E2")
COTTON = colors.HexColor("#F9F6F2")
GRAY = colors.HexColor("#6B6E7C")
ROW_ALT = colors.HexColor("#F6F2EE")
DRAFT_RED = colors.Color(0.71, 0.34, 0.25, alpha=0.16)

for nome, arquivo in (("Didot", "Didot.ttf"), ("Didot-Bold", "Didot-Bold.ttf"),
                      ("Futura", "Futura-Medium.ttf"), ("Futura-Bold", "Futura-Bold.ttf")):
    if nome not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(nome, os.path.join(FONTS_DIR, arquivo)))

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
HEADER_H = 30 * mm
FOOTER_H = 15 * mm

RASCUNHO_TEXTO = "RASCUNHO — NÃO ENVIAR AO CLIENTE"


def brl(v) -> str:
    """Quantia em reais, pela régua do sistema (2 casas, ROUND_HALF_UP)."""
    if v in (None, ""):
        return ""
    try:
        v = dinheiro(D0(v))
    except Exception:                                   # noqa: BLE001
        return str(v)
    s = f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {s}"


def qtd(v) -> str:
    if v in (None, ""):
        return ""
    try:
        d = D0(v)
    except Exception:                                   # noqa: BLE001
        return str(v)
    if d == d.to_integral_value():
        return f"{int(d):,}".replace(",", ".")
    return f"{d:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _esc(texto) -> str:
    """Texto livre dentro de Paragraph: escapa o que o mini-HTML do ReportLab interpretaria."""
    return (str(texto if texto is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class _CanvasNumerado(rl_canvas.Canvas):
    """Canvas que sabe o total de páginas — para "Página X de Y" no rodapé.

    O documento é desenhado uma vez em memória e só no `save()` cada página recebe o total.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estados = []

    def showPage(self):
        self._estados.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._estados)
        for estado in self._estados:
            self.__dict__.update(estado)
            self._rodape_numero(total)
            super().showPage()
        super().save()

    def _rodape_numero(self, total):
        self.saveState()
        self.setFillColor(GRAY)
        self.setFont("Futura", 7)
        self.drawRightString(PAGE_W - MARGIN, FOOTER_H - 8.5 * mm,
                             f"Página {self._pageNumber} de {total}")
        self.restoreState()


def _cabecalho(canv, header: dict):
    canv.saveState()
    canv.setFillColor(MIDNIGHT)
    canv.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, stroke=0, fill=1)

    logo = 14 * mm
    y0 = PAGE_H - HEADER_H
    if os.path.exists(LOGO_PATH):
        canv.drawImage(LOGO_PATH, MARGIN, y0 + (HEADER_H - logo) / 2, width=logo, height=logo,
                       mask="auto")
    x = MARGIN + logo + 5 * mm
    canv.setFillColor(COPPER)
    canv.setFont("Didot-Bold", 21)
    canv.drawString(x, y0 + 16.5 * mm, "A N A R A")
    canv.setFillColor(COTTON)
    canv.setFont("Futura", 7)
    canv.drawString(x, y0 + 11.5 * mm, "A  H I G H E R  S T A N D A R D  O F  C O M F O R T")

    canv.setFillColor(COTTON)
    canv.setFont("Futura-Bold", 11.5)
    canv.drawRightString(PAGE_W - MARGIN, y0 + 20.5 * mm, "PROPOSTA COMERCIAL")
    canv.setFont("Futura", 8.2)
    linhas = []
    if header.get("numero"):
        rev = header.get("revisao")
        linhas.append(f"Nº {header['numero']}" + (f"  ·  Revisão {rev}" if rev and int(rev) > 1 else ""))
    if header.get("data"):
        linhas.append(f"Data: {header['data']}")
    if header.get("validade"):
        linhas.append(f"Validade: {header['validade']}")
    y = y0 + 15.3 * mm
    for linha in linhas:
        canv.drawRightString(PAGE_W - MARGIN, y, linha)
        y -= 4.1 * mm
    canv.restoreState()


def _rodape(canv, header: dict):
    canv.saveState()
    canv.setStrokeColor(COPPER)
    canv.setLineWidth(0.7)
    canv.line(MARGIN, FOOTER_H, PAGE_W - MARGIN, FOOTER_H)
    canv.setFillColor(GRAY)
    canv.setFont("Futura", 6.4)
    # Nada de composição interna aqui: o cliente vê preço final, e só.
    canv.drawString(MARGIN, FOOTER_H - 4.5 * mm,
                    "Preços em reais, com impostos inclusos, válidos para as condições comerciais "
                    "desta proposta. Alteração de prazo, destino, quantidade ou condição de "
                    "pagamento pode alterar os preços.")
    canv.drawString(MARGIN, FOOTER_H - 8.5 * mm, "Anara — enxoval hoteleiro")
    canv.restoreState()


_IMAGEM_RASCUNHO = None


def _imagem_rascunho():
    """O carimbo diagonal, desenhado UMA vez como imagem (PIL) e reaproveitado.

    Imagem, e não texto, de propósito: um texto rotulado no meio da página entra na
    extração de texto misturado às linhas dos itens ("R$ 54, 44") e atrapalha quem copia
    o conteúdo. A faixa vermelha no topo continua sendo texto — é ela que diz RASCUNHO a
    quem lê ou busca.
    """
    global _IMAGEM_RASCUNHO
    if _IMAGEM_RASCUNHO is None:
        from PIL import Image, ImageDraw, ImageFont
        from reportlab.lib.utils import ImageReader
        fonte = ImageFont.truetype(os.path.join(FONTS_DIR, "Futura-Bold.ttf"), 120)
        caixa = fonte.getbbox(RASCUNHO_TEXTO)
        w, h = caixa[2] - caixa[0] + 40, caixa[3] - caixa[1] + 40
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((20 - caixa[0], 20 - caixa[1]), RASCUNHO_TEXTO, font=fonte,
                                 fill=(181, 87, 63, 46))
        _IMAGEM_RASCUNHO = ImageReader(img.rotate(35, expand=True, resample=Image.BICUBIC))
    return _IMAGEM_RASCUNHO


def _marca_rascunho(canv):
    canv.saveState()
    imagem = _imagem_rascunho()
    iw, ih = imagem.getSize()
    largura = PAGE_W * 0.86
    altura = largura * ih / iw
    canv.drawImage(imagem, (PAGE_W - largura) / 2, (PAGE_H - altura) / 2, width=largura,
                   height=altura, mask="auto")
    canv.restoreState()
    # faixa legível (o watermark diagonal é visual; a faixa é o que a extração de texto lê)
    canv.saveState()
    canv.setFillColor(colors.HexColor("#B5573F"))
    canv.rect(0, PAGE_H - HEADER_H - 5.2 * mm, PAGE_W, 5.2 * mm, stroke=0, fill=1)
    canv.setFillColor(colors.white)
    canv.setFont("Futura-Bold", 7.6)
    canv.drawCentredString(PAGE_W / 2, PAGE_H - HEADER_H - 3.6 * mm,
                           RASCUNHO_TEXTO + "  ·  documento não emitido, valores sujeitos a alteração")
    canv.restoreState()


def build_pdf(out_path: str, header: dict, items: list, totals: dict) -> str:
    """Desenha a proposta. `header`, `items` e `totals` chegam filtrados pela ponte."""
    rascunho = bool(header.get("rascunho"))
    top_extra = 6 * mm + (5.2 * mm if rascunho else 0)
    doc = BaseDocTemplate(
        out_path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=HEADER_H + top_extra, bottomMargin=FOOTER_H + 4 * mm,
        title=f"Proposta comercial Anara {header.get('numero') or ''}".strip(),
        author="Anara")
    frame = Frame(MARGIN, FOOTER_H + 4 * mm, PAGE_W - 2 * MARGIN,
                  PAGE_H - HEADER_H - FOOTER_H - top_extra - 4 * mm, id="corpo",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def on_page(canv, _doc):
        _cabecalho(canv, header)
        _rodape(canv, header)
        if rascunho:
            _marca_rascunho(canv)

    doc.addPageTemplates([PageTemplate(id="anara", frames=[frame], onPage=on_page)])

    rotulo = ParagraphStyle("rotulo", fontName="Futura", fontSize=6.8, leading=8.5, textColor=GRAY)
    valor = ParagraphStyle("valor", fontName="Futura-Bold", fontSize=9.2, leading=11.5, textColor=MIDNIGHT)
    texto = ParagraphStyle("texto", fontName="Futura", fontSize=8.4, leading=11.2, textColor=MIDNIGHT)
    texto_cinza = ParagraphStyle("texto_cinza", parent=texto, fontSize=7.6, leading=10.2, textColor=GRAY)
    secao = ParagraphStyle("secao", fontName="Futura-Bold", fontSize=7.6, leading=10, textColor=COPPER_DARK)

    def campo(rot, val):
        return Paragraph(f'<font name="Futura" size="6.8" color="#6B6E7C">{_esc(rot).upper()}</font><br/>'
                         f'<font name="Futura-Bold" size="9.2" color="#30354F">{_esc(val) or "—"}</font>', valor)

    story = []

    # --- cliente ------------------------------------------------------------
    cliente_linhas = [header.get("cliente") or "—"]
    if header.get("cnpj"):
        cliente_linhas.append(f"CNPJ {header['cnpj']}")
    if header.get("cidade"):
        cliente_linhas.append(header["cidade"])
    contato = header.get("contato") or ""
    if header.get("departamento"):
        contato = f"{contato} · {header['departamento']}" if contato else header["departamento"]
    bloco_cliente = Paragraph(
        '<font name="Futura" size="6.8" color="#6B6E7C">CLIENTE</font><br/>'
        f'<font name="Didot-Bold" size="12.5" color="#30354F">{_esc(cliente_linhas[0])}</font><br/>'
        + "<br/>".join(f'<font name="Futura" size="8.4" color="#30354F">{_esc(l)}</font>'
                       for l in cliente_linhas[1:]), valor)
    direita = [campo("Contato", contato), campo("Unidade / local de entrega", header.get("local_entrega"))]
    tabela_cliente = Table([[bloco_cliente, direita[0]], ["", direita[1]]],
                           colWidths=[105 * mm, 73 * mm])
    tabela_cliente.setStyle(TableStyle([
        ("SPAN", (0, 0), (0, 1)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, SAND),
    ]))
    story.append(tabela_cliente)
    story.append(Spacer(1, 5 * mm))

    # --- itens ----------------------------------------------------------------
    head = ParagraphStyle("head", fontName="Futura-Bold", fontSize=7, leading=9, textColor=COTTON)
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    cel = ParagraphStyle("cel", fontName="Futura", fontSize=8.2, leading=10.4, textColor=MIDNIGHT)
    cel_r = ParagraphStyle("cel_r", parent=cel, alignment=TA_RIGHT)
    cel_n = ParagraphStyle("cel_n", parent=cel, textColor=GRAY, fontSize=7.4)

    col = [8 * mm, 95 * mm, 16 * mm, 28 * mm, 31 * mm]
    dados = [[Paragraph("#", head), Paragraph("PRODUTO / ESPECIFICAÇÃO", head),
              Paragraph("QTD.", head_r), Paragraph("VALOR UNITÁRIO", head_r), Paragraph("TOTAL", head_r)]]
    for it in items:
        produto = f'<font name="Futura-Bold">{_esc(it.get("produto"))}</font>'
        if it.get("spec"):
            produto += f'<br/><font name="Futura" size="7.2" color="#6B6E7C">{_esc(it["spec"])}</font>'
        dados.append([Paragraph(str(it.get("n") or ""), cel_n), Paragraph(produto, cel),
                      Paragraph(qtd(it.get("qtd")), cel_r), Paragraph(brl(it.get("preco_final")), cel_r),
                      Paragraph(brl(it.get("total")), cel_r)])
    n_itens = len(dados)

    # Fechamento nas últimas linhas da MESMA tabela: subtotal, frete e total nunca se separam
    # do que fecham (e o cabeçalho repete em cada página que a tabela ocupar).
    tot_l = ParagraphStyle("tot_l", fontName="Futura", fontSize=8.4, textColor=GRAY, alignment=TA_RIGHT)
    tot_v = ParagraphStyle("tot_v", fontName="Futura-Bold", fontSize=8.8, textColor=MIDNIGHT, alignment=TA_RIGHT)
    tot_lb = ParagraphStyle("tot_lb", fontName="Futura-Bold", fontSize=9.4, textColor=COTTON, alignment=TA_RIGHT)
    tot_vb = ParagraphStyle("tot_vb", fontName="Didot-Bold", fontSize=12.5, textColor=COTTON, alignment=TA_RIGHT)
    frete_txt = totals.get("frete_texto") or (brl(totals["frete"]) if totals.get("frete") not in (None, "") else "—")
    dados.append(["", "", Paragraph("Subtotal dos produtos", tot_l), "", Paragraph(brl(totals.get("subtotal")), tot_v)])
    dados.append(["", "", Paragraph("Frete", tot_l), "", Paragraph(_esc(frete_txt), tot_v)])
    dados.append(["", "", Paragraph("TOTAL DA PROPOSTA", tot_lb), "", Paragraph(brl(totals.get("total_geral")), tot_vb)])

    tabela = Table(dados, colWidths=col, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), MIDNIGHT),
        ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, n_itens - 1), 3.6), ("BOTTOMPADDING", (0, 1), (-1, n_itens - 1), 3.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, n_itens - 1), 0.4, SAND),
        # fechamento
        ("SPAN", (0, n_itens), (1, n_itens)), ("SPAN", (0, n_itens + 1), (1, n_itens + 1)),
        ("SPAN", (0, n_itens + 2), (1, n_itens + 2)),
        ("SPAN", (2, n_itens), (3, n_itens)), ("SPAN", (2, n_itens + 1), (3, n_itens + 1)),
        ("SPAN", (2, n_itens + 2), (3, n_itens + 2)),
        ("LINEABOVE", (2, n_itens), (-1, n_itens), 0.8, MIDNIGHT),
        ("TOPPADDING", (2, n_itens), (-1, n_itens + 1), 3), ("BOTTOMPADDING", (2, n_itens), (-1, n_itens + 1), 3),
        ("BACKGROUND", (2, n_itens + 2), (-1, n_itens + 2), MIDNIGHT),
        ("TOPPADDING", (2, n_itens + 2), (-1, n_itens + 2), 6), ("BOTTOMPADDING", (2, n_itens + 2), (-1, n_itens + 2), 6),
        ("RIGHTPADDING", (2, n_itens + 2), (-1, n_itens + 2), 6),
    ]
    for i in range(1, n_itens):
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    tabela.setStyle(TableStyle(estilo))
    story.append(tabela)
    story.append(Spacer(1, 6 * mm))

    # --- condições comerciais ------------------------------------------------
    condicoes = [
        campo("Pagamento", header.get("condicao_pagamento")),
        campo("Prazo de entrega", header.get("prazo_entrega")),
        campo("Frete", header.get("frete")),
        campo("Validade da proposta", header.get("validade")),
    ]
    bloco_cond = [Paragraph("CONDIÇÕES COMERCIAIS", secao), Spacer(1, 1.5 * mm),
                  Table([condicoes], colWidths=[44.5 * mm] * 4, style=TableStyle([
                      ("VALIGN", (0, 0), (-1, -1), "TOP"),
                      ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                      ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))]
    if header.get("local_entrega"):
        bloco_cond.append(Spacer(1, 2 * mm))
        bloco_cond.append(campo("Local de entrega", header["local_entrega"]))
    story.append(KeepTogether(bloco_cond))

    if header.get("obs"):
        story.append(Spacer(1, 5 * mm))
        story.append(KeepTogether([Paragraph("OBSERVAÇÕES", secao), Spacer(1, 1.2 * mm),
                                   Paragraph(_esc(header["obs"]).replace("\n", "<br/>"), texto)]))

    termos = (header.get("termos") or "").strip()
    if termos:
        story.append(Spacer(1, 5 * mm))
        paragrafos = [Paragraph(_esc(t.strip()), texto_cinza) for t in termos.split("\n") if t.strip()]
        story.append(KeepTogether([Paragraph("TERMOS E CONDIÇÕES", secao), Spacer(1, 1.2 * mm)] + paragrafos[:1]))
        story.extend(paragrafos[1:])

    if header.get("vendedor"):
        story.append(Spacer(1, 5 * mm))
        story.append(KeepTogether([Paragraph("CONTATO COMERCIAL", secao), Spacer(1, 1.2 * mm),
                                   Paragraph(_esc(header["vendedor"]), texto)]))

    if header.get("mostrar_aceite", True):
        aceite = ParagraphStyle("aceite", fontName="Futura", fontSize=6.8, textColor=GRAY, leading=9)
        linha = Table([[Paragraph("DE ACORDO — NOME E CARGO", aceite), Paragraph("DATA", aceite)]],
                      colWidths=[122 * mm, 56 * mm])
        linha.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.5, SAND),
                                   ("TOPPADDING", (0, 0), (-1, 0), 3),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(KeepTogether([Spacer(1, 12 * mm), linha]))

    doc.build(story, canvasmaker=_CanvasNumerado)
    return out_path

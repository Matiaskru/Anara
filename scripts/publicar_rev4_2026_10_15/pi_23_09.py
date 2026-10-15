"""Itens da PI KTC IQA23092026 (23/09/2026): EXW US$ e peso líquido por peça, como a KTC declarou.

Cada código da tabela → (base do produto, EXW, peso). A base dá família/fios/composição/proteção;
o EXW e o peso vêm da PI. O produto é montado em memória e vai pelo motor (memoria_do_preco)
como KTC_QUOTED — o mesmo caminho de um SKU com cotação KTC cadastrada. Nada é gravado.
"""
from datetime import date

from app import calculadora as calc
from app.models import CostMethod, Produto

DATA_PI = date(2026, 9, 23)
FONTE_PI = "PI KTC IQA23092026 (23/09/2026)"
M300, M250 = 9, 3


def T(fam, l, c, mat, **kw):
    return ("calc", dict(familia=fam, largura_cm=l, comprimento_cm=c, material_id=mat, **kw))


def F(l, c, mat, abas):
    return ("calc", dict(familia="Pillow Case", largura_cm=l, comprimento_cm=c, material_id=mat, abas=abas, flap_cm=20))


def W(fam, l, c, gsm, comp):
    return ("calc", dict(familia=fam, largura_cm=l, comprimento_cm=c, gsm=gsm, composicao_toalha=comp))


def K(pid, **muda):
    return ("sku", pid, muda)


PI = {  # código: (base, EXW US$, peso kg, linha da PI)
    "P111": (T("Top Sheet", 190, 250, M250), 8.48, 0.8, 1),
    "P112": (T("Bottom Sheet", 200, 290, M250), 10.17, 0.9, 2),
    "P114": (T("Top Sheet", 240, 250, M250), 10.49, 0.9, 3),
    "P115": (T("Bottom Sheet", 250, 290, M250), 12.49, 1.1, 4),
    "P117": (T("Top Sheet", 260, 250, M250), 11.29, 1.0, 5),
    "P118": (T("Bottom Sheet", 280, 290, M250), 13.88, 1.3, 6),
    "E01": (F(50, 70, M250, 2), 2.77, 0.20, 7), "E02": (F(50, 70, M250, 3), 2.77, 0.20, 7),
    "E03": (F(50, 70, M250, 0), 2.77, 0.20, 7),
    "E04": (F(50, 90, M250, 2), 3.17, 0.3, 8), "E05": (F(50, 90, M250, 3), 3.17, 0.3, 8),
    "E06": (F(50, 90, M250, 4), 3.17, 0.3, 8),
    "P113": (T("Duvet Cover", 190, 260, M250), 17.58, 1.5, 9),
    "P116": (T("Duvet Cover", 250, 260, M250), 22.55, 2.0, 10),
    "E07": (T("Duvet Cover", 270, 275, M250), 24.64, 2.3, 11),
    "P119": (T("Duvet Cover", 270, 265, M250), 24.64, 2.3, 11),
    "P081": (T("Top Sheet", 190, 250, M300), 9.80, 0.8, 12),
    "P082": (T("Bottom Sheet", 200, 290, M300), 11.77, 0.9, 13),
    "P084": (T("Top Sheet", 240, 250, M300), 12.14, 0.9, 14),
    "P085": (T("Bottom Sheet", 250, 290, M300), 14.48, 1.1, 15),
    "P087": (T("Top Sheet", 260, 250, M300), 13.07, 1.0, 16),
    "P088": (T("Bottom Sheet", 280, 290, M300), 16.10, 1.3, 17),
    "R197": (F(50, 70, M300, 2), 3.08, 0.20, 18), "R199": (F(50, 70, M300, 3), 3.08, 0.20, 18),
    "R201": (F(50, 70, M300, 4), 3.08, 0.20, 18),
    "R198": (F(50, 90, M300, 2), 3.55, 0.3, 19), "R200": (F(50, 90, M300, 3), 3.55, 0.3, 19),
    "R202": (F(50, 90, M300, 4), 3.55, 0.3, 19),
    "P083": (T("Duvet Cover", 190, 260, M300), 20.30, 1.5, 20),
    "P086": (T("Duvet Cover", 250, 260, M300), 26.11, 2.0, 21),
    "E08": (T("Duvet Cover", 270, 275, M300), 28.55, 2.3, 22),
    "P089": (T("Duvet Cover", 270, 265, M300), 28.55, 2.3, 22),
    "P001": (W("Bath Towel", 86, 150, 550, "90/10"), 6.03, 0.71, 23),
    "P004": (K(233), 7.96, 0.94, 24),
    "P002": (W("Hand Towel", 50, 85, 550, "90/10"), 2.10, 0.23, 25),
    "P005": (K(235), 2.49, 0.28, 26),
    "P003": (K(236), 2.70, 0.30, 27),
    "P006": (K(237), 3.42, 0.38, 28),
    "P016": (K(238), 11.78, 0.84, 29),
    "E09": (K(382, subcategoria="XL"), 18.00, 1.75, 31),
    "BR-008": (K(382), 17.00, 1.45, 32),
    "P015": (K(241), 25.00, 1.75, 33),
    "P014": (K(206), 24.00, 1.45, 34),
}

CAMPOS_CLONE = ["categoria", "familia", "fornecedor_id", "ncm", "ii_aplicado", "gsm", "cotton_pct",
                "poliester_pct", "construcao", "acabamento", "subcategoria", "origem_fiscal", "thread_count",
                "weave", "largura_cm", "comprimento_cm", "protecao_comercial_pct", "protecao_comercial_fonte",
                "plain_or_stripe", "material_ref", "nome", "fechamento"]


def produto_pi(session, cod):
    base, exw, peso, _linha = PI[cod]
    if base[0] == "calc":
        p = calc.produto_simulado(session, **base[1])
    else:
        orig = session.get(Produto, base[1])
        p = Produto(sku_key="__pi__", **{c: getattr(orig, c) for c in CAMPOS_CLONE})
        for k, v in base[2].items():
            setattr(p, k, v)
    p.cost_method = CostMethod.ktc_quoted.value
    p.exw_cotado_usd = exw
    p.exw_cotado_data = DATA_PI
    p.exw_cotado_fonte = FONTE_PI
    p.peso_kg = peso
    p.peso_tipo = "REAL KTC"
    p.peso_fonte = FONTE_PI + " — net weight per unit"
    return p

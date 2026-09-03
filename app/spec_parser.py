"""Leitura da descrição textual do catálogo antigo para preencher os campos estruturados.

Isto aqui é **migração de dados**, não regra de cálculo: o texto livre é lido uma vez para
popular família, dimensões, fios, GSM, composição e listrado/liso. Depois disso o cálculo usa
só os campos estruturados — nunca a descrição.

Quando um campo não é reconhecido com segurança, fica `None` e o produto é marcado para
revisão. Nada é adivinhado.
"""
import re
from typing import Optional

# categoria do catálogo → família estruturada
FAMILIA_POR_CATEGORIA = {
    "lençol plano": "Flat Sheet",
    "lençol plano (hotel)": "Flat Sheet",
    "lençol hospitalar": "Flat Sheet",
    "lençol hospitalar (maca)": "Flat Sheet",
    "lençol com elástico": "Fitted Sheet",
    "capa duvet": "Duvet Cover",
    "fronha com aba": "Pillow Case",
    "fronha sem aba": "Pillow Case",
    "fronha (hotel)": "Pillow Case",
    "fronha hospitalar": "Pillow Case",
    "euro sham": "Pillow Case",
    "pillow sham": "Pillow Case",
    "protetor de fronha": "Pillow Protector",
    "toalha banho": "Bath Towel",
    "toalha rosto": "Hand Towel",
    "toalha piso": "Bath Mat",
    "toalha piscina": "Pool Towel",
    "toalha lavabo": "Wash Cloth",
    "roupão": "Bathrobe",
    "chinelos": "Slipper",
    "topper colchão": "Mattress Topper",
    "protetor colchão": "Mattress Protector",
    "edredom / insert": "Duvet Insert",
    "bed runner": "Bed Runner",
    "travesseiro": "Pillow",
    "travesseiros": "Pillow",
    "pillow top": "Mattress Topper",
    "capa protetora para travesseiros": "Pillow Protector",
    "manta": "Blanket",
    "peseira": "Bed Runner",
}

# famílias com fórmula industrial confirmada pela KTC
FAMILIAS_CALCULAVEIS = {"Flat Sheet", "Top Sheet", "Duvet Cover",
                        # Sessão 2: a fronha ganhou fórmula (§18, cinco backtests com desvio
                        # ≤0,002%) e o bottom sheet SEM elástico usa o mesmo motor do lençol
                        # plano. Fitted/com elástico continua fora — não há fórmula aprovada.
                        "Pillow Case", "Bottom Sheet",
                        # toalhas passam a ser calculáveis: custo por peso, com preço/kg por
                        # construção derivado da PI 23/08/2026
                        "Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth"}

# TC + composição → material da tabela de preços KTC (só combinações que existem na tabela)
MATERIAIS_CONHECIDOS = {
    (230, "COTTON"): "230TC Percale 100% Cotton",
    (200, "CVC"): "200TC Percale Polycotton",
    (250, "CVC"): "250TC Sateen CVC 70/30",
    (250, "COTTON"): "250TC Sateen 100% Cotton",
    (300, "CVC"): "300TC Sateen CVC 70/30",
    (300, "COTTON"): "300TC Sateen 100% Cotton",
    (400, "COTTON"): "400TC Sateen 100% Cotton",
}


def parse_dimensoes(texto: str):
    """Devolve (largura, comprimento, altura, flap) em cm. Entende '50x70+5' e '100x200x30'."""
    if not texto:
        return None, None, None, None
    m = re.search(r"(\d{2,3})\s*[xX]\s*(\d{2,3})(?:\s*[xX]\s*(\d{1,3}))?(?:\s*\+\s*(\d{1,2}))?", texto)
    if not m:
        return None, None, None, None
    largura = float(m.group(1))
    comprimento = float(m.group(2))
    altura = float(m.group(3)) if m.group(3) else None
    flap = float(m.group(4)) if m.group(4) else None
    return largura, comprimento, altura, flap


def parse_thread_count(texto: str) -> Optional[int]:
    if not texto:
        return None
    m = re.search(r"(\d{2,4})\s*fios", texto, re.I) or re.search(r"(\d{2,4})\s*TC\b", texto, re.I)
    return int(m.group(1)) if m else None


def parse_gsm(texto: str) -> Optional[int]:
    if not texto:
        return None
    m = re.search(r"(\d{2,4})\s*(?:GSM|gr?/m²|g/m2)", texto, re.I)
    return int(m.group(1)) if m else None


def parse_composicao(texto: str):
    """Devolve (algodao_pct, poliester_pct, rotulo) — None quando não dá para afirmar."""
    if not texto:
        return None, None, None
    t = texto.lower()
    if "100% algod" in t or "100% cotton" in t:
        return 1.0, 0.0, "COTTON"
    if "100% poli" in t or "100% poly" in t:
        return 0.0, 1.0, "POLY"
    m = re.search(r"cvc\s*(\d{2})\s*/\s*(\d{2})", t)
    if m:
        return int(m.group(1)) / 100, int(m.group(2)) / 100, "CVC"
    m = re.search(r"\b(\d{2})\s*/\s*(\d{2})\b", t)
    if m and int(m.group(1)) + int(m.group(2)) == 100:
        return int(m.group(1)) / 100, int(m.group(2)) / 100, "CVC"
    m = re.search(r"(\d{2})%\s*(?:algod|cotton)", t)
    if m:
        algodao = int(m.group(1)) / 100
        return algodao, round(1 - algodao, 4), "CVC"
    return None, None, None


def parse_listrado(texto: str) -> str:
    t = (texto or "").lower()
    return "stripe" if ("listrad" in t or "stripe" in t) else "plain"


def parse_construcao(texto: str) -> Optional[str]:
    t = (texto or "").lower()
    if "oxford" in t:
        return "oxford"
    if "open bag" in t:
        return "open bag"
    if "housewife" in t or "house wife" in t:
        return "housewife"
    return None


def parse_acabamento(texto: str) -> Optional[str]:
    t = (texto or "").lower()
    achados = []
    for chave, rotulo in [("bordad", "bordado"), ("embroider", "bordado"), ("zipper", "zíper"),
                          ("botão", "botões"), ("button", "botões"), ("snap", "snap/botões"),
                          ("flange", "flange")]:
        if chave in t and rotulo not in achados:
            achados.append(rotulo)
    return ", ".join(achados) if achados else None


def material_para(thread_count: Optional[int], rotulo_composicao: Optional[str]) -> Optional[str]:
    """Só devolve material quando a combinação TC + composição existe na tabela da KTC."""
    if thread_count is None or not rotulo_composicao:
        return None
    return MATERIAIS_CONHECIDOS.get((int(thread_count), rotulo_composicao))


def parse_produto(categoria: Optional[str], nome: str, especificacao: Optional[str]) -> dict:
    """Extrai tudo o que der do par (categoria, especificação) — o resto fica None."""
    texto = f"{nome or ''} · {especificacao or ''}"
    familia = FAMILIA_POR_CATEGORIA.get((categoria or "").strip().lower())
    largura, comprimento, altura, flap = parse_dimensoes(especificacao or nome)
    tc = parse_thread_count(texto)
    gsm = parse_gsm(texto)
    algodao, poliester, rotulo = parse_composicao(texto)

    # lençol de baixo/alto: o catálogo separa por categoria, o nome refina
    if familia == "Flat Sheet" and re.search(r"bottom sheet", texto, re.I):
        familia = "Bottom Sheet"
    if familia == "Flat Sheet" and re.search(r"top sheet", texto, re.I):
        familia = "Top Sheet"

    return {
        "familia": familia,
        "subcategoria": categoria,
        "largura_cm": largura,
        "comprimento_cm": comprimento,
        "gsm": gsm,
        "thread_count": tc,
        "cotton_pct": algodao,
        "poliester_pct": poliester,
        "weave": ("Sateen" if tc and tc >= 250 else ("Percale" if tc else None)),
        "plain_or_stripe": parse_listrado(texto),
        "construcao": parse_construcao(texto),
        "acabamento": parse_acabamento(texto),
        "material_ref": material_para(tc, rotulo),
        "altura_cm": altura,
        "flap_cm": flap,
        "composicao_rotulo": rotulo,
    }

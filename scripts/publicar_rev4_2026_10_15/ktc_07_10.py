"""Cotações KTC de 07/10/2026 (nº ANARA07102026): EXW US$ direto, por código da tabela.

Três documentos: "Anara Quotation 07-10-2026.pdf" (mantas, roupões, protetores, 250TC, 233TC),
"Anara bathrobes Quotation 07-10-2026.pdf" (7 roupões × M/L/XL/XXL) e
"Anara samples Quotation 07-10-2026.pdf" (amostras). O produto é montado em memória e vai pelo
motor (memoria_do_preco) como KTC_QUOTED — o mesmo caminho da PI 23/09 (pi_23_09.py). Nada é
gravado no banco.

Só entra aqui o que tem match técnico EXATO (produto, medida, composição, fios, trama,
acabamento, aba/elástico/open bag). Ver NAO_APLICADO no fim do arquivo.

PESO: a KTC disse que ainda não tem o peso unitário definitivo dos roupões. Peso logístico
ESTIMADO (decisão do Matias, 09/10/2026):
    peso_tamanho = peso_âncora × (EXW_tamanho ÷ EXW_âncora) × (1 + proteção)
proteção 10% (construção com âncora), ~20% (piqué/jacquard, análogo), 25% (spa, análogo).
Mantas: largura × comprimento × 0,310 kg/m² × 1,10. Lençóis/capas 250TC: peso líquido por peça
da PI 23/09 (REAL KTC, mesmo produto) — peso real nunca vira estimativa.
"""
from datetime import date

from pi_23_09 import CAMPOS_CLONE, K, T  # noqa: F401  (mesmos construtores da PI)

from app import calculadora as calc
from app.models import CostMethod, Produto

DATA_0710 = date(2026, 10, 7)
DOC_ROUPOES = "KTC Quotation ANARA07102026 — bathrobes (07/10/2026)"
DOC_GERAL = "KTC Quotation ANARA07102026 (07/10/2026)"
M250 = 3   # 250TC Sateen CVC 70/30 plain — o tecido da cotação

FONTE_PESO_ROUPAO = "KTC historical weight anchor + proportional size/EXW scaling + safety buffer"
OBS_PESO = "Replace estimated weight with real weight/CBM when packing list becomes available."
OBS_WAFFLE = "Weight discrepancy pending real packing-list validation."

# ---- âncoras (kg, EXW US$ da âncora na cotação 07/10) -------------------------------------
ANCORAS = {
    "velour": (1.45, 25.50, "Velour 420 L = 1,45 kg (Plain Bathrobe L 420, SKU 206, REAL KTC)", 0.10),
    "waffle_velour": (1.45, 25.50, "Waffle velour 420 L = 1,45 kg (Waffle Velour Bathrobe L 420, SKU 207, REAL KTC)", 0.10),
    "waffle_kimono": (0.528, 17.00, "Waffle kimono 240 L = 0,528 kg (Light Waffle Bathrobe L 240, SKU 203)", 0.10),
    "microfibra": (0.660, 24.00, "Microfibra/fleece L = 0,660 kg (Microfiber Fleece Robe L, SKU 209)", 0.10),
    # sem âncora própria — análogo técnico KTC, proteção maior
    "pique": (0.528, 17.00, "análogo: waffle kimono 240 L 0,528 kg (mesma gramatura 240, algodão), escala por EXW", 0.20),
    "jacquard": (0.528, 17.00, "análogo: waffle kimono 240 L 0,528 kg (kimono de algodão mais próximo), escala por EXW", 0.20),
    "spa": (0.660, 24.00, "análogo: microfibra/fleece L 0,660 kg (construção dupla, externo sintético), escala por EXW", 0.25),
}


def peso_roupao(linha, exw):
    peso, exw_anc, texto, protecao = ANCORAS[linha]
    base = peso * exw / exw_anc
    return round(base * (1 + protecao), 4), base, texto, protecao


# ---- roupões: (código, linha, tamanho, EXW, SKU-base para clonar, item da cotação) --------
ROUPOES = [
    # Velour 420 g/m² · gola xale (100% Cotton velour plain 420gsm shawl collar)
    ("R032", "velour", "M", 24.00, 206, 17), ("P014", "velour", "L", 25.50, 206, 18),
    ("P015", "velour", "XL", 27.00, 241, 19), ("N16", "velour", "XXL", 28.50, 241, 20),
    # Waffle velour 420 g/m² · gola xale
    ("N17", "waffle_velour", "M", 24.00, 380, 1), ("N18", "waffle_velour", "L", 25.50, 207, 2),
    ("N19", "waffle_velour", "XL", 27.00, 240, 3), ("N20", "waffle_velour", "XXL", 28.50, 240, 4),
    # Waffle piqué 240 g/m² · gola xale em velour
    ("N21", "pique", "M", 19.00, 381, 5), ("N22", "pique", "L", 20.00, 381, 6),
    ("N23", "pique", "XL", 21.00, 381, 7), ("BR-019", "pique", "XXL", 22.00, 381, 8),
    # Waffle kimono 240 g/m²
    ("N24", "waffle_kimono", "M", 16.00, 382, 9), ("BR-008", "waffle_kimono", "L", 17.00, 382, 10),
    ("E09", "waffle_kimono", "XL", 18.00, 382, 11), ("N25", "waffle_kimono", "XXL", 19.00, 382, 12),
    # Jacquard kimono (sateen texturizado, vivo preto)
    ("N26", "jacquard", "M", 22.00, 383, 13), ("BR-003", "jacquard", "L", 23.00, 383, 14),
    ("N27", "jacquard", "XL", 24.00, 383, 15), ("N28", "jacquard", "XXL", 25.00, 383, 16),
    # Microfibra com forro fleece · kimono
    ("N29", "microfibra", "M", 23.00, 385, 21), ("BR-001", "microfibra", "L", 24.00, 385, 22),
    ("N30", "microfibra", "XL", 25.00, 385, 23), ("N31", "microfibra", "XXL", 26.00, 385, 24),
    # Spa (85% poliéster / 15% nylon 160 g + forro jacquard 100% algodão 110 g · gola xale)
    ("N32", "spa", "M", 26.00, 386, 25), ("N33", "spa", "L", 27.00, 386, 26),
    ("N34", "spa", "XL", 28.00, 386, 27), ("N35", "spa", "XXL", 29.00, 386, 28),
]

KTC = {}   # código → dict(base, exw, data, doc, peso, peso_tipo, peso_fonte, obs, linha_doc)
PESOS = []  # memória dos pesos estimados, para o relatório

for cod, linha, tam, exw, base, item in ROUPOES:
    peso, base_kg, ancora, prot = peso_roupao(linha, exw)
    obs = OBS_PESO + (" " + OBS_WAFFLE if linha == "waffle_kimono" else "")
    KTC[cod] = dict(base=K(base, subcategoria=tam), exw=exw, data=DATA_0710, doc=DOC_ROUPOES,
                    peso=peso, peso_tipo="ESTIMADO", peso_fonte=FONTE_PESO_ROUPAO, obs=obs,
                    linha_doc=f"bathrobes #{item}", linha=linha, tamanho=tam)
    PESOS.append(dict(codigo=cod, linha=linha, tamanho=tam, exw=exw, ancora=ancora,
                      peso_base=round(base_kg, 4), protecao=prot, peso=peso))

# BR-028 (spa tamanho único, amostra 35 de 29/07): não há "único" na cotação 07/10 — o EXW
# continua o de 29/07 (US$ 27,00, envelhecido); só ganha o peso estimado do spa, que é o que
# o bloqueava. EXW 27 = EXW do spa L, então o peso é o do L.
_p, _b, _a, _pr = peso_roupao("spa", 27.00)
KTC["BR-028"] = dict(base=K(386), exw=None, data=None, doc=None, peso=_p, peso_tipo="ESTIMADO",
                     peso_fonte=FONTE_PESO_ROUPAO, obs=OBS_PESO, linha_doc="29/07 amostra 35 (EXW mantido)",
                     linha="spa", tamanho="Único")
PESOS.append(dict(codigo="BR-028", linha="spa", tamanho="Único", exw=27.00, ancora=_a,
                  peso_base=round(_b, 4), protecao=_pr, peso=_p))

# ---- mantas Softcozy (Blanket 100% polyester fleece 310 g, Caramel) -----------------------
for cod, (l, c), exw, item in (("R024", (180, 230), 11.50, 1), ("R025", (250, 230), 15.75, 2),
                               ("R026", (290, 230), 17.75, 3)):
    teorico = l / 100 * c / 100 * 0.310
    peso = round(teorico * 1.10, 4)
    KTC[cod] = dict(base=K(363, largura_cm=l, comprimento_cm=c, gsm=310, cotton_pct=0.0,
                           poliester_pct=1.0, construcao="fleece · caramelo"),
                    exw=exw, data=DATA_0710, doc=DOC_GERAL, peso=peso, peso_tipo="ESTIMADO",
                    peso_fonte="Estimado: largura × comprimento × 0,310 kg/m² (310 g/m²) + 10% de segurança logística",
                    obs=OBS_PESO, linha_doc=f"quotation #{item}", linha="manta", tamanho=f"{l}x{c}")
    PESOS.append(dict(codigo=cod, linha="manta", tamanho=f"{l}x{c}", exw=exw,
                      ancora=f"teórico {l/100:g} × {c/100:g} × 0,310 = {teorico:.4f} kg",
                      peso_base=round(teorico, 4), protecao=0.10, peso=peso))

# ---- 250TC Sateen 70/30 plain white — match exato com a PI 23/09 (mesma especificação) ----
#: código → (base, EXW 07/10, peso REAL KTC da PI 23/09, item da cotação, EXW da PI 23/09)
LENCOIS_250 = {
    "P111": (T("Top Sheet", 190, 250, M250), 9.89, 0.8, 14, 8.48),
    "P114": (T("Top Sheet", 240, 250, M250), 12.20, 0.9, 19, 10.49),
    "P117": (T("Top Sheet", 260, 250, M250), 13.12, 1.0, 24, 11.29),
    "P113": (T("Duvet Cover", 190, 260, M250), 20.42, 1.5, 18, 17.58),
    "P116": (T("Duvet Cover", 250, 260, M250), 26.14, 2.0, 23, 22.55),
    # capa King: a KTC fatura 270x265 (PI 23/09 linha 11 e cotação 07/10 linha 28) — é o
    # produto do E07 (pedido diz 270x275) e do P119 (270x265)
    "E07": (T("Duvet Cover", 270, 275, M250), 28.55, 2.3, 28, 24.64),
    "P119": (T("Duvet Cover", 270, 265, M250), 28.55, 2.3, 28, 24.64),
}
for cod, (base, exw, peso, item, _pi) in LENCOIS_250.items():
    KTC[cod] = dict(base=base, exw=exw, data=DATA_0710, doc=DOC_GERAL, peso=peso,
                    peso_tipo="REAL KTC", peso_fonte="PI KTC IQA23092026 — net weight per unit (mesmo produto)",
                    obs=None, linha_doc=f"quotation #{item}", linha="250TC", tamanho=None)

# ---- o que NÃO entrou (sem match exato na tabela) — vai para o relatório -------------------
NAO_APLICADO = [
    ("quotation #12", "Waterproof mattress protector 100x200 · topo 233TC percal 100% algodão · PU · 4 elásticos · 200 g", 15.63,
     "referência KTC; não substitui protetor Daune (P059); SKU KTC 190 (100x200x30 microfibra 200 g) não descreve topo percal/PU/elásticos"),
    ("quotation #13", "Mattress protector with straps 200x200 · mesma construção", 31.25,
     "referência KTC; não substitui o matelassado com alça Daune (P064)"),
    ("quotation #15–17, #20–22, #25–27", "Lençol com elástico 250TC sateen 70/30 (100/160/180x200 + 20/25/30)", None,
     "a tabela não tem lençol com elástico; SKUs de catálogo 50/58/66 (x30) são candidatos a atualizar no sistema"),
    ("quotation #29–30", "Fronha envelope 250TC sateen 70/30 · aba 20 (50x70 US$ 2,25 · 50x90 US$ 2,65)", None,
     "não aplicada à E03: a PI 23/09 fatura as 3.600 fronhas 50x70 (inclusive as 'sem abas') como oxford 5 cm + aba 20"),
    ("quotation #31–47", "233TC PERCAL 100% ALGODÃO (lençol, elástico, capa, fronha envelope)", None,
     "NÃO é 233 fios 70/30 — R181–R189/R193/R194 continuam sob consulta; guardado como referência"),
    ("samples #5", "Roupão piqué externo 240 g + forro atoalhado 300 g · L", 28.00,
     "construção fora da lista A–G aprovada; sem âncora de peso — referência"),
    ("samples #7", "Toalha de banho 90/10 550 g · 90x160", 6.73, "sem código na tabela (P004 é 650 g 100%; P001 é 86x150)"),
    ("samples #8", "Toalha de rosto 90/10 550 g · 50x85", 2.10, "= P002 (PI 23/09 já US$ 2,10) — sem mudança"),
    ("samples #1–2", "Toalha de piscina listrada 550 g 90x170 (branco/azul, branco/cinza)", 11.78,
     "= P016 (PI 23/09 já US$ 11,78) — sem mudança; N13 (600 g) segue calculada"),
    ("samples #9", "Fronha 300TC 50x70 oxford 5 cm + aba 20 + bordado 2 linhas", 5.59,
     "sem SKU com bordado na tabela — não aplicar a fronha padrão"),
]


def produto_ktc(session, cod):
    e = KTC[cod]
    base = e["base"]
    if base[0] == "calc":
        p = calc.produto_simulado(session, **base[1])
    else:
        orig = session.get(Produto, base[1])
        p = Produto(sku_key="__ktc_0710__", **{c: getattr(orig, c) for c in CAMPOS_CLONE})
        for k, v in base[2].items():
            setattr(p, k, v)
        if e["exw"] is None:   # BR-028: EXW/data/fonte do próprio SKU
            p.exw_cotado_usd, p.exw_cotado_data = orig.exw_cotado_usd, orig.exw_cotado_data
            p.exw_cotado_fonte = orig.exw_cotado_fonte
    p.cost_method = CostMethod.ktc_quoted.value
    if e["exw"] is not None:
        p.exw_cotado_usd = e["exw"]
        p.exw_cotado_data = e["data"]
        p.exw_cotado_fonte = f"{e['doc']} · {e['linha_doc']}"
    p.peso_kg = e["peso"]
    p.peso_tipo = e["peso_tipo"]
    p.peso_fonte = e["peso_fonte"]
    return p

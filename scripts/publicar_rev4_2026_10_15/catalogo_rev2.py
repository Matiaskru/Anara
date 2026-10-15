"""Identidade canônica KTC da REV2: código da tabela → SKU do catálogo (existente ou novo).

Existente só quando a identidade é EXATA (mesmo produto, construção e medida) e é o SKU que a
tabela 13-10 já usava como base. Os demais ganham SKU novo, com `sku_key` de identidade
(construção + medida), nunca de data/fonte — a fonte fica na referência de custo versionada.
"""
from ktc_07_10 import KTC, OBS_PESO, OBS_WAFFLE
from pi_23_09 import PI

M = {3: ("250TC Sateen CVC 70/30", "250 fios · 70% algodão / 30% poliéster", 250),
     9: ("300TC Sateen 100% algodão", "300 fios · 100% algodão", 300),
     11: ("400TC Sateen 100% algodão", "400 fios · 100% algodão", 400)}
PECA = {"Top Sheet": "Lençol de cima", "Bottom Sheet": "Lençol de baixo", "Duvet Cover": "Capa de duvet",
        "Pillow Case": "Fronha"}
NCM_CAMA, NCM_TOALHA, NCM_MANTA = "6302.31.00", "6302.60.00", "6301.40.00"

# código → SKU existente (identidade exata, base que a tabela 13-10 já usava)
EXISTENTE = {"P003": 236, "P004": 233, "P005": 235, "P006": 237, "P016": 238,
             "P014": 206, "P015": 241, "N17": 380, "N18": 207, "N19": 240, "BR-019": 381,
             "BR-008": 382, "BR-003": 383, "BR-001": 385, "BR-028": 386}
ROUPAO = {"velour": "velour 420 g/m² · gola xale", "waffle_velour": "waffle velour 420 g/m² · gola xale",
          "pique": "waffle piqué 240 g/m² · gola xale em velour", "waffle_kimono": "waffle 240 g/m² · kimono",
          "jacquard": "jacquard sateen texturizado · vivo preto · kimono",
          "microfibra": "microfibra com forro fleece · kimono",
          "spa": "spa 85% poliéster/15% nylon 160 g + forro jacquard algodão 110 g · gola xale"}
REBAIXAR = {
    "P112": "Linha 250TC possui referências de datas diferentes (lençol de baixo na PI 23/09; lençol de cima e capa na cotação 07/10)",
    "P115": "Linha 250TC possui referências de datas diferentes (lençol de baixo na PI 23/09; lençol de cima e capa na cotação 07/10)",
    "P118": "Linha 250TC possui referências de datas diferentes (lençol de baixo na PI 23/09; lençol de cima e capa na cotação 07/10)",
    "E03": "Confirmar especificação/peso/medida na chegada do estoque: pedido 'without flanges' × PI 23/09 'oxford 5cm and flap 20cm'",
    "E07": "Confirmar especificação/peso/medida na chegada do estoque: pedido 270x275 × KTC fatura 270x265",
    "E08": "Confirmar especificação/peso/medida na chegada do estoque: pedido 270x275 × KTC fatura 270x265",
    "BR-028": "Tamanho único não cotado em 07/10; EXW de 29/07 sem documento localizado; peso estimado (análogo)",
}


def _spec(base):
    return base[1] if base[0] == "calc" else None


def _ident_calc(spec, abas=None):
    fam, l, c = spec["familia"], spec["largura_cm"], spec["comprimento_cm"]
    if fam in PECA:
        tc, txt, _n = M[spec["material_id"]]
        extra = ""
        if fam == "Pillow Case":
            extra = f" · oxford 5 cm + aba 20 · {spec['abas']} abas" if spec["abas"] else " · sem abas (faturada como oxford na PI)"
        chave = f"KTC · {fam} · {tc} plain{extra} · {l}x{c}"
        nome = f"{PECA[fam]} {l}x{c} · {txt}{extra.replace(' · ', ' · ', 1)}"
        return chave, nome, f"{l}x{c} · {txt}{extra}", NCM_CAMA
    # toalhas
    comp = spec.get("composicao_toalha", "100/0")
    pt = {"Bath Towel": "Toalha de banho", "Hand Towel": "Toalha de rosto"}[fam]
    chave = f"KTC · {fam} · {spec['gsm']} g/m² · {comp} · {l}x{c}"
    return chave, f"{pt} {l}x{c} · {spec['gsm']} g/m² · {comp}", f"{l}x{c} · {spec['gsm']} g/m² · {comp}", NCM_TOALHA


KTC_SKUS = {}
for cod, (base, exw, peso, linha) in PI.items():
    if cod in ("P119", "P089") or cod in KTC:          # P119/P089 fora da tabela; 07/10 manda onde houver
        continue
    d = dict(origem="pi")
    if cod in EXISTENTE:
        d["sku_id"] = EXISTENTE[cod]
    else:
        spec = _spec(base)
        d["sku_key"], d["nome"], d["especificacao"], d["ncm"] = _ident_calc(spec)
        if cod == "E08":
            d["sku_key"] += " (pedido 270x275; KTC fatura 270x265)"
    if cod in REBAIXAR:
        d["rebaixar"] = REBAIXAR[cod]
    KTC_SKUS[cod] = d

for cod, e in KTC.items():
    if cod == "P119":
        continue
    d = dict(origem="ktc")
    if cod in EXISTENTE:
        d["sku_id"] = EXISTENTE[cod]
    elif e["linha"] == "250TC":
        d["sku_key"], d["nome"], d["especificacao"], d["ncm"] = _ident_calc(_spec(e["base"]))
        if cod == "E07":
            d["sku_key"] += " (pedido 270x275; KTC fatura 270x265)"
    elif e["linha"] == "manta":
        l, c = e["tamanho"].split("x")
        d["sku_key"] = f"KTC · Blanket · Softcozy 100% poliéster fleece 310 g/m² · caramelo · {l}x{c}"
        d["nome"] = f"Manta Softcozy {l}x{c} · 310 g/m² · 100% poliéster (fleece) · caramelo"
        d["especificacao"] = f"{l}x{c} · 310 g/m² · 100% poliéster fleece · caramelo"
        d["ncm"] = NCM_MANTA
    else:                                             # roupão de tamanho que o catálogo não tinha
        d["sku_key"] = f"KTC · Bathrobe · {ROUPAO[e['linha']]} · {e['tamanho']}"
        d["nome"] = f"Roupão {ROUPAO[e['linha']]} · {e['tamanho']}"
        d["especificacao"] = f"{e['tamanho']} · {ROUPAO[e['linha']]}"
        d["campos"] = {"subcategoria": e["tamanho"]}
    if e.get("peso_tipo") == "ESTIMADO":
        d["obs"] = OBS_PESO + (" " + OBS_WAFFLE + " (histórico 0,528 kg × PI 23/09 1,45 kg)" if e["linha"] == "waffle_kimono" else "")
    if cod in REBAIXAR:
        d["rebaixar"] = REBAIXAR[cod]
    KTC_SKUS[cod] = d

# P016 na tabela = taupe (SKU 238); a azul marinho (SKU 239) é a mesma linha da PI (#30), mesmo
# preço — recebe a mesma cotação para o sistema ficar coerente (não é linha da tabela)
KTC_SKUS["P016#azul"] = dict(origem="pi", sku_id=239, pi_de="P016")

# fórmula de ALTO RISCO → SKU próprio em REVIEW_REQUIRED (sob consulta até cotação direta)
M250, M400 = 3, 11
KTC_REVIEW = {}
for cod, fam, l, c in (("P120", "Top Sheet", 275, 250), ("P121", "Bottom Sheet", 285, 295), ("P122", "Duvet Cover", 285, 265),
                       ("P123", "Top Sheet", 300, 250), ("P124", "Bottom Sheet", 300, 310), ("P125", "Duvet Cover", 290, 260)):
    spec = dict(familia=fam, largura_cm=l, comprimento_cm=c, material_id=M250)
    k, n, e, _ = _ident_calc(spec)
    KTC_REVIEW[cod] = dict(spec=spec, sku_key=k, nome=n, especificacao=e,
                           motivo="250 fios Super King só por fórmula, ~11% abaixo da cotação direta 07/10 da mesma linha — aguardando cotação KTC")
for cod, c in (("R203", 70), ("R204", 90)):
    spec = dict(familia="Pillow Case", largura_cm=50, comprimento_cm=c, material_id=M400, abas=2, flap_cm=20)
    k, n, e, _ = _ident_calc(spec)
    KTC_REVIEW[cod] = dict(spec=spec, sku_key=k, nome=n, especificacao=e,
                           motivo="Fronha 400 fios 2 abas só por fórmula: ~10% abaixo do cotado e mais barata que a 300 — aguardando cotação KTC")

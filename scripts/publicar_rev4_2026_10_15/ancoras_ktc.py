"""Âncoras da calibração KTC (13/10/2026) — toda cotação direta com match técnico calculável.

Fontes recentes (≤ 60 dias, o limite de frescor do sistema), conferidas no PDF:
  PI KTC ANARA23082026 (23/08) · PI KTC IQA23092026 (23/09) · Quotation KTC ANARA07102026 (07/10)
  + Samples quotation 07/10 (toalhas). Histórico (> 60 dias, catálogo): só informativo.

Match técnico = mesma família de corte (lençol plano/de baixo SEM elástico/capa/fronha), mesmo
tecido (fios + composição + trama), mesmo acabamento (liso/listrado), mesma medida e, na fronha,
mesmo número de lados com oxford. Fora: lençol COM elástico (a fórmula não calcula), amostra com
bordado, 233TC percal (o motor só tem 230TC — não é o mesmo tecido).

NÍVEL ATUAL: dentro de uma construção, o fator usa as âncoras do documento MAIS RECENTE que a
cotou. Cotação mais antiga do mesmo item, superada por uma nova, entra só no QA e no histórico.
"""
from datetime import date

D0823, D0923, D1007 = date(2026, 8, 23), date(2026, 9, 23), date(2026, 10, 7)
DOC = {D0823: "PI KTC ANARA23082026 (23/08/2026)", D0923: "PI KTC IQA23092026 (23/09/2026)",
       D1007: "Quotation KTC ANARA07102026 (07/10/2026)"}
M250, M300 = "250TC Sateen CVC 70/30", "300TC Sateen 100% Cotton"
M300_7030, M400 = "300TC Sateen CVC 70/30", "400TC Sateen 100% Cotton"
CAMA = "Top Sheet,Flat Sheet,Bottom Sheet,Duvet Cover"
CAMA_E_FRONHA = CAMA + ",Pillow Case"

ANCORAS = []


def _a(data, linha, familia, l, c, material, exw, item, abas=None, sku=None, gsm=None, comp=None,
       ps_="plain", recente=True, doc=None):
    ANCORAS.append(dict(data=data, documento=doc or DOC[data], linha=linha, familia=familia, largura=l,
                        comprimento=c, material=material, plain_or_stripe=ps_, abas=abas, exw=exw,
                        item=item, sku=sku, gsm=gsm, composicao=comp, recente=recente))


# ---- 250TC e 300TC 100% algodão nas duas PIs (lençóis e capas: mesmo preço em 23/08 e 23/09) ----
_LIN = {M250: dict(top=(8.48, 10.49, 11.29), bot=(10.17, 12.49, 13.88)),
        M300: dict(top=(9.80, 12.14, 13.07), bot=(11.77, 14.48, 16.10))}
_DUV = {(M250, D0823): ((190, 260, 17.58, 9), (250, 260, 22.55, 10), (270, 275, 25.50, 11)),
        (M250, D0923): ((190, 260, 17.58, 9), (250, 260, 22.55, 10), (270, 265, 24.64, 11)),
        (M300, D0823): ((190, 260, 20.30, 20), (250, 260, 26.11, 21), (270, 275, 29.56, 22)),
        (M300, D0923): ((190, 260, 20.30, 20), (250, 260, 26.11, 21), (270, 265, 28.55, 22))}
for mat, n0 in ((M250, 1), (M300, 12)):
    tc = mat[:5]
    for d in (D0823, D0923):
        for k, ((l, c), p) in enumerate(zip(((190, 250), (240, 250), (260, 250)), _LIN[mat]["top"])):
            _a(d, f"#{n0 + 2 * k}", "Top Sheet", l, c, mat, p, f"{tc} top sheet plain white {l}x{c}")
        for k, ((l, c), p) in enumerate(zip(((200, 290), (250, 290), (280, 290)), _LIN[mat]["bot"])):
            _a(d, f"#{n0 + 1 + 2 * k}", "Bottom Sheet", l, c, mat, p, f"{tc} bottom sheet (sem elástico) plain white {l}x{c}")
        for l, c, p, n in _DUV[(mat, d)]:
            _a(d, f"#{n}", "Duvet Cover", l, c, mat, p, f"{tc} duvet cover open bag {l}x{c}")

# ---- fronhas: 23/08 por nº de lados com oxford; 23/09 preço ÚNICO "oxford 5cm and flap 20cm" ----
_F0823 = {M250: {(50, 70): ((2.17, "#7"), (2.31, "#8"), (2.46, "#9")), (50, 90): ((2.51, "#8"), (2.68, "#9"), (2.86, "#10"))},
          M300: {(50, 70): ((2.43, "#18"), (2.60, "#19"), (2.77, "#20")), (50, 90): ((2.83, "#19"), (3.03, "#20"), (3.24, "#21"))}}
for mat, porm in _F0823.items():
    for (l, c), linhas in porm.items():
        for abas, (p, n) in zip((2, 3, 4), linhas):
            _a(D0823, n, "Pillow Case", l, c, mat, p, f"{mat[:5]} pillow oxford 5cm from {abas} sides + flap 20cm {l}x{c}", abas=abas)
# 23/09: a KTC faturou as SKUs de 2/3/4 abas do pedido por este preço único — é o custo real delas
for mat, (p70, p90, n70, n90), skus in (
        (M250, (2.77, 3.17, "#7", "#8"), {(70, 2): "E01", (70, 3): "E02", (90, 2): "E04", (90, 3): "E05", (90, 4): "E06"}),
        (M300, (3.08, 3.55, "#18", "#19"), {(70, 2): "R197", (70, 3): "R199", (70, 4): "R201",
                                             (90, 2): "R198", (90, 3): "R200", (90, 4): "R202"})):
    for (c, abas), sku in skus.items():
        _a(D0923, n70 if c == 70 else n90, "Pillow Case", 50, c, mat, p70 if c == 70 else p90,
           f"{mat[:5]} pillow 'oxford 5cm and flap 20cm' (lados não especificados) — {sku} pedido com {abas} abas", abas=abas, sku=sku)

# ---- 07/10: linha 250TC 70/30 cotada de novo ----
for (l, c), p, n in (((190, 250), 9.89, 14), ((240, 250), 12.20, 19), ((260, 250), 13.12, 24)):
    _a(D1007, f"#{n}", "Top Sheet", l, c, M250, p, f"250TC flat sheet plain white {l}x{c}")
for (l, c), p, n in (((190, 260), 20.42, 18), ((250, 260), 26.14, 23), ((270, 265), 28.55, 28)):
    _a(D1007, f"#{n}", "Duvet Cover", l, c, M250, p, f"250TC duvet cover open bag {l}x{c}")
for (l, c), p, n in (((50, 70), 2.25, 29), ((50, 90), 2.65, 30)):
    _a(D1007, f"#{n}", "Pillow Case", l, c, M250, p, f"250TC pillowcase envelope (house wife) flap 20cm, sem oxford {l}x{c}", abas=0)

# ---- toalha de piscina listrada 100% algodão (motor de toalha, US$/kg) ----
for d, doc, n in ((D0923, None, "#29/#30"), (D1007, "Samples quotation KTC ANARA07102026 (07/10/2026)", "amostras #1/#2")):
    _a(d, n, "Pool Towel", 90, 170, None, 11.78, "Stripe pool towel 100% cotton terry 550 g/m² 90x170", gsm=550,
       comp="100/0", ps_="stripe", doc=doc)

# ---- histórico > 60 dias (catálogo; nível de preço anterior) — só informativo -----------------
for d, doc, fam, l, c, mat, p in (
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 190, 290, M250, 8.96),
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 300, 300, M250, 14.07),
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 190, 290, M300, 10.06),
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 300, 300, M300, 15.85),
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 190, 290, M400, 10.84),
        (date(2026, 5, 14), "KTC IQA14052026", "Top Sheet", 300, 300, M400, 17.11),
        (date(2026, 5, 14), "KTC IQA14052026", "Duvet Cover", 180, 240, M400, 17.36),
        (date(2026, 5, 14), "KTC IQA14052026", "Duvet Cover", 220, 240, M400, 20.81),
        (date(2026, 5, 14), "KTC IQA14052026", "Duvet Cover", 240, 260, M400, 24.27),
        (date(2026, 5, 14), "KTC IQA14052026", "Duvet Cover", 260, 280, M400, 28.01),
        (date(2026, 7, 15), "KTC HE15072026", "Top Sheet", 160, 310, M300, 9.76),
        (date(2026, 7, 29), "KTC Samples 29/07 KTC-011", "Top Sheet", 260, 280, M250, 12.53),
        (date(2026, 7, 29), "KTC Samples 29/07 KTC-001", "Top Sheet", 260, 280, M300, 14.02),
        (date(2026, 7, 29), "KTC Samples 29/07 (sem código)", "Top Sheet", 229, 305, M400, 14.47)):
    _a(d, "", fam, l, c, mat, p, f"{mat} {fam} {l}x{c} (catálogo)", recente=False, doc=f"{doc} ({d:%d/%m})")
for d, doc, l, c, mat, p, abas in ((date(2026, 5, 14), "KTC IQA14052026", 50, 70, M400, 1.93, 2),
                                   (date(2026, 5, 14), "KTC IQA14052026", 50, 90, M400, 2.31, 2)):
    _a(d, "", "Pillow Case", l, c, mat, p, f"{mat} fronha 'com aba' (catálogo; nº de lados não registrado)", abas=abas,
       recente=False, doc=f"{doc} ({d:%d/%m})")


def _e(x, mat=None, fam=None, abas="*", datas=None, recente=True):
    return ((mat is None or x["material"] == mat) and (fam is None or x["familia"] in fam.split(","))
            and (abas == "*" or x["abas"] == abas) and (datas is None or x["data"] in datas)
            and x["recente"] == recente)


#: Grupos calibrados. `nivel` = âncoras do nível de preço atual (formam o fator);
#: `qa` = todas as âncoras recentes do grupo (nenhuma pode ficar > 2% acima do EXW operacional).
GRUPOS = [
    dict(construcao="250TC_SATEEN_70_30_PLAIN", grupo="TODAS", familias=CAMA_E_FRONHA, material=M250,
         plain_or_stripe="plain", abas=None,
         nivel=lambda x: _e(x, M250, CAMA_E_FRONHA, datas={D1007}),
         qa=lambda x: _e(x, M250, CAMA_E_FRONHA),
         porque="07/10 recotou a linha: as 8 âncoras de 07/10 (3 lençóis, 3 capas, 2 fronhas) formam o nível "
                "atual; as de 23/08 e 23/09 (nível anterior, inclusive lençol de baixo e fronha oxford) entram no QA."),
    dict(construcao="300TC_SATEEN_100_COTTON_PLAIN", grupo="LENCOL_CAPA", familias=CAMA, material=M300,
         plain_or_stripe="plain", abas=None,
         nivel=lambda x: _e(x, M300, CAMA, datas={D0923}), qa=lambda x: _e(x, M300, CAMA),
         porque="PI 23/09 (= PI 23/08): fórmula 3,5–4,0% ACIMA do real em todas as 9 peças — validada, sem ajuste."),
    dict(construcao="300TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_2_ABAS", familias="Pillow Case", material=M300,
         plain_or_stripe="plain", abas=2,
         nivel=lambda x: _e(x, M300, "Pillow Case", 2, {D0923}), qa=lambda x: _e(x, M300, "Pillow Case", 2),
         porque="Desde 23/09 a KTC cobra preço único pela fronha oxford, qualquer que seja o nº de lados; a fórmula "
                "cobra menos com menos lados → 2 abas fica ~10% abaixo. Separada por nº de abas (a PI 23/08 trata "
                "2/3/4 lados como itens diferentes)."),
    dict(construcao="300TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_3_ABAS", familias="Pillow Case", material=M300,
         plain_or_stripe="plain", abas=3,
         nivel=lambda x: _e(x, M300, "Pillow Case", 3, {D0923}), qa=lambda x: _e(x, M300, "Pillow Case", 3),
         porque="Mesmo motivo; 3 abas fica ~3% abaixo."),
    dict(construcao="300TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_4_ABAS", familias="Pillow Case", material=M300,
         plain_or_stripe="plain", abas=4,
         nivel=lambda x: _e(x, M300, "Pillow Case", 4, {D0923}), qa=lambda x: _e(x, M300, "Pillow Case", 4),
         porque="4 abas: fórmula 2,6–2,8% ACIMA do real — validada, sem ajuste."),
    dict(construcao="TOALHA_PISCINA_LISTRADA_100_ALGODAO", grupo="TODAS", familias="Pool Towel", material=None,
         plain_or_stripe="stripe", abas=None,
         nivel=lambda x: _e(x, None, "Pool Towel", datas={D1007}), qa=lambda x: _e(x, None, "Pool Towel"),
         porque="Toalha de piscina listrada 550 g: real = fórmula (US$/kg) na PI 23/09 e nas amostras 07/10 — validada."),
]

#: REV4 (13/10, pendências da Silvana): construções SEM âncora própria que recebem fator DERIVADO de
#: construção/acabamento adjacente — preço conservador, status ESTIMADO (`provisoria`). Substituem os
#: bloqueios da REV3 (histórico em BLOQUEIOS_REV3). `de` = grupo calibrado cujo fator final é herdado.
PROVISORIAS = [
    dict(construcao="300TC_SATEEN_70_30_PLAIN", grupo="LENCOL_CAPA", familias=CAMA, material=M300_7030,
         plain_or_stripe="plain", abas=None, de=("250TC_SATEEN_70_30_PLAIN", "TODAS"),
         porque="Calibração PROVISÓRIA derivada da atualização de mercado da linha 250TC 70/30 de 07/10 (mesma "
                "composição CVC 70/30, construção adjacente; sem cotação da 300 70/30). Testes: ≥ 250 equivalente nas "
                "15 medidas (+7,4% a +27%); US$/m² cai com a medida, sem salto; margem 22% (300–399 fios) inalterada. "
                "Fica 5,4%–10,1% ACIMA da 300 100% algodão (PI 23/09): nenhum fator único fica entre as duas "
                "(ex.: 190x250 exige ≥ 1,037 pela 250 e ≤ 1,028 pela 300 algodão) — o conflito é entre as datas de "
                "cotação; indício de que a 300 algodão está no nível anterior a 07/10 (pergunta à KTC)."),
    dict(construcao="400TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_2_ABAS", familias="Pillow Case", material=M400,
         plain_or_stripe="plain", abas=2, de=("300TC_SATEEN_100_COTTON_PLAIN", "FRONHA_2_ABAS"),
         porque="Fator de ACABAMENTO (nº de abas), não de tecido: desde 23/09 a KTC cobra preço único pela fronha "
                "oxford, qualquer nº de lados, e a fórmula cobra menos com menos lados. O desvio por abas é o mesmo nas "
                "duas construções cotadas (2 abas: 250 ×1,091/1,095 · 300 ×1,097/1,102; 3 abas: ×1,029–1,033; "
                "4 abas: ×0,971–0,974). Usa o fator da 300 algodão (construção 100% algodão mais próxima). O nível da "
                "400 continua sem âncora → PROVISÓRIA (ESTIMADO)."),
    dict(construcao="400TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_3_ABAS", familias="Pillow Case", material=M400,
         plain_or_stripe="plain", abas=3, de=("300TC_SATEEN_100_COTTON_PLAIN", "FRONHA_3_ABAS"),
         porque="Mesmo fator de acabamento (3 abas). Sem ele a 400 de 3 abas sairia mais barata que a 400 de 2 abas "
                "corrigida — a KTC cobra o mesmo pelas duas."),
]

#: Histórico: bloqueios da REV3 (substituídos pelas PROVISORIAS acima em 13/10, REV4).
BLOQUEIOS_REV3 = [
    dict(construcao="300TC_SATEEN_70_30_PLAIN", grupo="LENCOL_CAPA", familias=CAMA, material=M300_7030,
         plain_or_stripe="plain", abas=None,
         motivo="Sem cotação KTC da construção. A fórmula da 300 fios 70/30 sai ABAIXO da 250 fios 70/30 cotada em "
                "07/10 na mesma peça e medida (−3,2% a −3,5%) e da 250 calibrada nas demais medidas (~−5%): 300 fios "
                "não pode custar menos que 250 da mesma composição. Sob consulta até cotar."),
    dict(construcao="400TC_SATEEN_100_COTTON_PLAIN", grupo="FRONHA_2_ABAS", familias="Pillow Case", material=M400,
         plain_or_stripe="plain", abas=2,
         motivo="Sem cotação KTC recente da 400 fios. A fórmula da fronha 2 abas fica 9–10% abaixo do preço real nas "
                "duas construções cotadas (250 e 300) e a 400 fios 2 abas sai mais barata que a 300 fios 2 abas cotada "
                "(R197/R198). Sob consulta até cotar."),
]

#: bloqueios vigentes (nenhum desde a REV4)
BLOQUEIOS = []

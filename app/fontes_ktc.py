"""Documentos de preço da KTC, transcritos como dados estruturados.

Hierarquia de confiança (a de menor `prioridade` ganha):

1. **PI ANARA 23/08/2026** — proforma invoice emitida para a ANARA. Fonte mais confiável.
2. **Cotação KTC ANARA 10/08/2026** — cotação anterior, também emitida para a ANARA.
3. **HAMAN GLOBAL 25/08/2026** — cotação recente da mesma fábrica, mas para outro cliente.
   Só entra como referência; nunca é aplicada automaticamente.
4. Preços antigos que já estavam no catálogo.

Cada item traz os campos estruturados (família, dimensões, fios/GSM, composição, liso/listrado),
porque o casamento com o catálogo é feito por especificação — nunca por nome.
"""
from datetime import date

PI_23_08 = {
    "documento": "Anara Storage PI 23-08-2026.pdf",
    "referencia": "ANARA23082026",
    "data": date(2026, 8, 23),
    "cliente": "ANARA",
    "tipo": "PROFORMA_INVOICE",
    "prioridade": 1,
    "itens": [
        # família, largura, comprimento, tc, gsm, algodão, listrado, tamanho, construção, preço, peso
        ("Top Sheet", 190, 250, 250, None, 0.70, "plain", None, None, 8.48, 0.8),
        ("Bottom Sheet", 200, 290, 250, None, 0.70, "plain", None, None, 10.17, 0.9),
        ("Top Sheet", 240, 250, 250, None, 0.70, "plain", None, None, 10.49, 0.9),
        ("Bottom Sheet", 250, 290, 250, None, 0.70, "plain", None, None, 12.49, 1.1),
        ("Top Sheet", 260, 250, 250, None, 0.70, "plain", None, None, 11.29, 1.0),
        ("Bottom Sheet", 280, 290, 250, None, 0.70, "plain", None, None, 13.88, 1.3),
        ("Pillow Case", 50, 70, 250, None, 0.70, "plain", None, "oxford 2 lados", 2.17, 0.20),
        ("Pillow Case", 50, 70, 250, None, 0.70, "plain", None, "oxford 3 lados", 2.31, 0.20),
        ("Pillow Case", 50, 70, 250, None, 0.70, "plain", None, "oxford 4 lados", 2.46, 0.20),
        ("Pillow Case", 50, 90, 250, None, 0.70, "plain", None, "oxford 2 lados", 2.51, 0.3),
        ("Pillow Case", 50, 90, 250, None, 0.70, "plain", None, "oxford 3 lados", 2.68, 0.3),
        ("Pillow Case", 50, 90, 250, None, 0.70, "plain", None, "oxford 4 lados", 2.86, 0.3),
        ("Duvet Cover", 190, 260, 250, None, 0.70, "plain", None, "open bag", 17.58, 1.5),
        ("Duvet Cover", 250, 260, 250, None, 0.70, "plain", None, "open bag", 22.55, 2.0),
        ("Duvet Cover", 270, 275, 250, None, 0.70, "plain", None, "open bag", 25.50, 2.3),
        ("Top Sheet", 190, 250, 300, None, 1.0, "plain", None, None, 9.80, 0.8),
        ("Bottom Sheet", 200, 290, 300, None, 1.0, "plain", None, None, 11.77, 0.9),
        ("Top Sheet", 240, 250, 300, None, 1.0, "plain", None, None, 12.14, 0.9),
        ("Bottom Sheet", 250, 290, 300, None, 1.0, "plain", None, None, 14.48, 1.1),
        ("Top Sheet", 260, 250, 300, None, 1.0, "plain", None, None, 13.07, 1.0),
        ("Bottom Sheet", 280, 290, 300, None, 1.0, "plain", None, None, 16.10, 1.3),
        ("Pillow Case", 50, 70, 300, None, 1.0, "plain", None, "oxford 2 lados", 2.43, 0.20),
        ("Pillow Case", 50, 70, 300, None, 1.0, "plain", None, "oxford 3 lados", 2.60, 0.20),
        ("Pillow Case", 50, 70, 300, None, 1.0, "plain", None, "oxford 4 lados", 2.77, 0.20),
        ("Pillow Case", 50, 90, 300, None, 1.0, "plain", None, "oxford 2 lados", 2.83, 0.3),
        ("Pillow Case", 50, 90, 300, None, 1.0, "plain", None, "oxford 3 lados", 3.03, 0.3),
        ("Pillow Case", 50, 90, 300, None, 1.0, "plain", None, "oxford 4 lados", 3.24, 0.3),
        ("Duvet Cover", 190, 260, 300, None, 1.0, "plain", None, "open bag", 20.30, 1.5),
        ("Duvet Cover", 250, 260, 300, None, 1.0, "plain", None, "open bag", 26.11, 2.0),
        ("Duvet Cover", 270, 275, 300, None, 1.0, "plain", None, "open bag", 29.56, 2.3),
        ("Bath Towel", 86, 150, None, 550, 0.90, "plain", None, "terry", 6.03, 0.71),
        ("Bath Towel", 90, 160, None, 650, 1.0, "plain", None, "terry", 7.96, 0.94),
        ("Hand Towel", 50, 85, None, 550, 0.90, "plain", None, "terry", 2.10, 0.23),
        ("Hand Towel", 50, 85, None, 650, 1.0, "plain", None, "terry", 2.49, 0.28),
        ("Bath Mat", 50, 80, None, 750, 1.0, "plain", None, "terry", 2.70, 0.30),
        ("Bath Mat", 50, 80, None, 950, 1.0, "plain", None, "terry", 3.42, 0.38),
        ("Pool Towel", 90, 170, None, 550, 1.0, "stripe", None, "terry taupe", 11.78, 0.84),
        ("Pool Towel", 90, 170, None, 550, 1.0, "stripe", None, "terry navy", 11.78, 0.84),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "XL", "waffle velour shawl", 26.00, 1.75),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "L", "waffle velour shawl", 25.00, 1.45),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "XL", "velour shawl", 25.00, 1.75),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "L", "velour shawl", 24.00, 1.45),
    ],
}

HAMAN_25_08 = {
    "documento": "HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf",
    "referencia": "HG25082026",
    "data": date(2026, 8, 25),
    "cliente": "HAMAN GLOBAL",
    "tipo": "QUOTATION_OUTRO_CLIENTE",
    "prioridade": 3,
    "itens": [
        ("Bed Skirt", 193, 203, None, None, 1.0, "plain", None, "top algodão / drop chenille", 31.92, None),
        ("Pillow Case", 50, 90, 400, None, 1.0, "plain", None, "housewife flap 20cm", 2.85, None),
        ("Top Sheet", 260, 200, 400, None, 1.0, "plain", None, None, 11.98, None),
        ("Flat Sheet", 260, 200, 400, None, 1.0, "plain", None, None, 11.98, None),
        ("Duvet Cover", 273, 243, 400, None, 1.0, "plain", None, "snap com botões", 30.29, None),
        ("Pool Towel", 86, 172, None, 550, 1.0, "stripe", None, "terry navy", 11.39, None),
        ("Bath Mat", 50, 80, None, 600, 1.0, "plain", None, "terry", 2.16, None),
        ("Wash Cloth", 33, 33, None, 550, 1.0, "plain", None, "terry", 0.54, None),
        ("Hand Towel", 50, 80, None, 450, 1.0, "plain", None, "terry", 1.62, None),
        ("Bath Towel", 70, 140, None, 500, 1.0, "plain", None, "terry", 4.17, None),
        ("Bathrobe", None, None, None, 450, 1.0, "plain", "S", "terry kimono", 19.00, None),
        ("Bathrobe", None, None, None, 450, 1.0, "plain", "M", "terry kimono", 20.00, None),
        ("Bathrobe", None, None, None, 450, 1.0, "plain", "L", "terry kimono", 21.00, None),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "S", "waffle velour shawl", 23.00, None),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "M", "waffle velour shawl", 23.50, None),
        ("Bathrobe", None, None, None, 420, 1.0, "plain", "L", "waffle velour shawl", 24.00, None),
        ("Slipper", None, None, None, None, 1.0, "plain", "L", "terry closed toe", 1.50, None),
        ("Slipper", None, None, None, None, 1.0, "plain", "L", "waffle velour closed toe", 2.00, None),
        ("Slipper", None, None, None, None, 0.0, "plain", "L", "coral fleece closed toe", 1.00, None),
        ("Fitted Sheet", 76, 80, 230, None, 1.0, "plain", None, "crib com elástico", 5.74, None),
        ("Mattress Protector", 183, 213, None, None, 0.0, "plain", None,
         "microfibra PU com zíper", 18.77, None),
        ("Pool Towel", 100, 160, None, 550, 1.0, "stripe", None, "terry navy", 12.32, None),
    ],
}

CAMPOS = ("familia", "largura_cm", "comprimento_cm", "thread_count", "gsm", "cotton_pct",
          "plain_or_stripe", "tamanho", "construcao", "preco_usd", "peso_kg")


def itens(documento: dict):
    """Itera os itens de um documento como dicionários."""
    for linha in documento["itens"]:
        item = dict(zip(CAMPOS, linha))
        item["documento"] = documento["documento"]
        item["data"] = documento["data"]
        item["cliente"] = documento["cliente"]
        item["prioridade"] = documento["prioridade"]
        item["tipo"] = documento["tipo"]
        yield item


DOCUMENTOS = [PI_23_08, HAMAN_25_08]

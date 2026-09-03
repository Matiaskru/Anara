#!/usr/bin/env python3
"""Cria os SKUs que o catálogo institucional anuncia e o sistema ainda não tinha.

Todos entram **sem custo**, marcados como `REVIEW_REQUIRED`, com a especificação já
estruturada. Aparecem na busca da cotação e na lista de revisão: dá para cotar digitando o
preço, e o custo entra quando o fornecedor responder. Nenhum preço é inventado aqui.

De onde vem cada medida:

* **Edredons Daune** — as 20 combinações do bloco "TAMANHOS DE EDREDOM SOLICITADOS" da própria
  planilha da Daune, que foram pedidas e voltaram sem preço.
* **Roupa de cama** — as medidas já usadas na mesma peça em outra gramatura de fios. O catálogo
  promete as quatro peças em 250/300/400/500/800; o que falta é medida, não conceito.
* **Toalha de piscina** — as duas medidas que já existem, nas gramaturas e na composição que o
  catálogo anuncia (500-750 GSM, algodão ou 90/10).
* **Roupões** — os dois modelos do catálogo que não estavam cadastrados, nos tamanhos já usados.
* **Capa de almofada Decor Tricot** — o catálogo lista a peça, mas nenhum documento traz medida
  nem preço; entra um SKU de referência, para virar pergunta ao fornecedor.

Uso:
    python3 scripts/adicionar_skus_do_catalogo.py --dry-run
    python3 scripts/adicionar_skus_do_catalogo.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

from sqlmodel import Session, select

from app import pricing_service as ps
from app.db import engine
from app.migrations import fazer_backup
from app.models import CostConfidence, CostMethod, Fornecedor, Produto

CATALOGO = "Catálogo Anara A4 ago/2026"
PLANILHA_DAUNE = "tabela de preços Daune — bloco 'TAMANHOS DE EDREDOM SOLICITADOS'"

# --- roupa de cama: peça, família, categoria, fios, composição, medidas -------------------
MEDIDAS_LENCOL_BAIXO = ["100x200", "160x200", "180x200"]
MEDIDAS_LENCOL_CIMA = ["180x280", "190x290", "240x280", "260x280", "300x300"]
MEDIDAS_DUVET = ["180x240", "220x240", "240x260", "260x280"]
MEDIDAS_FRONHA = ["50x70", "50x90"]

CAMA = [
    ("Lençol com elástico", "Fitted Sheet", "Lençol com Elástico", 500, MEDIDAS_LENCOL_BAIXO),
    ("Lençol com elástico", "Fitted Sheet", "Lençol com Elástico", 800, MEDIDAS_LENCOL_BAIXO),
    ("Lençol plano", "Flat Sheet", "Lençol Plano", 800, MEDIDAS_LENCOL_CIMA[:3]),
    ("Capa duvet", "Duvet Cover", "Capa Duvet", 800, MEDIDAS_DUVET),
    ("Fronha com aba", "Pillow Case", "Fronha com Aba", 800, MEDIDAS_FRONHA),
    ("Fronha sem aba", "Pillow Case", "Fronha sem Aba", 800, MEDIDAS_FRONHA),
]

# --- toalha de piscina: catálogo pede 500-750 GSM, algodão ou 90/10 -----------------------
PISCINA = [("70x150", 650), ("70x150", 750), ("90x170", 650), ("90x170", 750)]
PISCINA_9010 = [("70x150", 550), ("90x170", 550)]

# --- roupões do catálogo que faltavam ----------------------------------------------------
ROUPOES = [
    ("Roupão clássico atoalhado", "380 GSM · 100% algodão egípcio · atoalhado clássico", 380, ("M", "L", "XL")),
    ("Roupão kimono piquet", "Piquet · kimono", None, ("M", "L", "XL")),
]

# --- edredons pedidos à Daune sem preço --------------------------------------------------
EDREDOM_GRAMATURAS = ["180 g", "250 g"]
EDREDOM_COMPOSICOES = ["100% plumas de ganso", "100% fibras de poliéster"]
EDREDOM_MEDIDAS = ["190x260", "250x260", "270x265", "285x265", "290x260"]


def _dimensoes(medida: str):
    partes = medida.split("x")
    return float(partes[0]), float(partes[1])


def _novo(session, sku, nome, categoria, especificacao, familia, fornecedor_id, motivo,
          documento, **extras):
    existente = session.exec(select(Produto).where(Produto.sku_key == sku)).first()
    if existente:
        return None
    produto = Produto(
        sku_key=sku, nome=nome, categoria=categoria, especificacao=especificacao,
        familia=familia, fornecedor_id=fornecedor_id, ativo=True,
        custo_unitario=None, preco_base=None,
        cost_method=CostMethod.manual.value,
        custo_confianca=CostConfidence.review_required.value,
        precisa_revisao=True, revisao_motivo=motivo,
        custo_ref_documento=documento, custo_ref_tipo="A_COTAR", **extras)
    session.add(produto)
    return produto


def adicionar(dry_run=False):
    if not dry_run:
        fazer_backup("skus-do-catalogo")

    criados = []
    with Session(engine) as s:
        fornecedores = {f.codigo: f.id for f in s.exec(select(Fornecedor)).all()}
        ktc, daune, decor = (fornecedores["KTC"], fornecedores["DAUNE"],
                             fornecedores["DECOR_TRICOT"])

        # roupa de cama
        for rotulo, familia, categoria, fios, medidas in CAMA:
            for medida in medidas:
                largura, comprimento = _dimensoes(medida)
                sku = f"{rotulo} {medida}  ·  {medida} · {fios} fios · 100% algodão"
                p = _novo(s, sku, f"{rotulo} {medida}", categoria,
                          f"{medida} · {fios} fios · 100% algodão", familia, ktc,
                          f"Peça anunciada no catálogo em {fios} fios e ainda sem cotação da KTC. "
                          "A tabela de tecidos da KTC também não tem preço para essa gramatura de "
                          "fios — pedir os dois juntos.", CATALOGO,
                          thread_count=fios, cotton_pct=1.0, poliester_pct=0.0,
                          weave="Sateen", plain_or_stripe="plain",
                          largura_cm=largura, comprimento_cm=comprimento)
                if p is not None:
                    criados.append(("cama", p.nome))

        # toalha de piscina
        for medida, gsm in PISCINA:
            largura, comprimento = _dimensoes(medida)
            sku = f"Toalha piscina {medida}  ·  {medida} · {gsm} GSM · 100% algodão"
            p = _novo(s, sku, f"Toalha piscina {medida}", "Toalha Piscina",
                      f"{medida} · {gsm} GSM · 100% algodão", "Pool Towel", ktc,
                      "Gramatura anunciada no catálogo (500-750 GSM) e ainda sem cotação. "
                      "Toalha de piscina LISA não tem preço por kg em nenhum documento — só a "
                      "listrada tem.", CATALOGO,
                      gsm=gsm, cotton_pct=1.0, poliester_pct=0.0, plain_or_stripe="plain",
                      largura_cm=largura, comprimento_cm=comprimento)
            if p is not None:
                criados.append(("piscina", p.nome))
        for medida, gsm in PISCINA_9010:
            largura, comprimento = _dimensoes(medida)
            sku = f"Toalha piscina {medida}  ·  {medida} · {gsm} GSM · 90/10"
            p = _novo(s, sku, f"Toalha piscina {medida} 90/10", "Toalha Piscina",
                      f"{medida} · {gsm} GSM · 90/10", "Pool Towel", ktc,
                      "Composição 90% algodão / 10% poliéster anunciada no catálogo e ainda sem "
                      "cotação.", CATALOGO,
                      gsm=gsm, cotton_pct=0.90, poliester_pct=0.10, plain_or_stripe="plain",
                      largura_cm=largura, comprimento_cm=comprimento)
            if p is not None:
                criados.append(("piscina", p.nome))

        # roupões
        for nome_base, especificacao, gsm, tamanhos in ROUPOES:
            for tamanho in tamanhos:
                sku = f"{nome_base} {tamanho}  ·  {tamanho} · {especificacao}"
                p = _novo(s, sku, f"{nome_base} {tamanho}", "Roupão",
                          f"{tamanho} · {especificacao}", "Bathrobe", ktc,
                          "Modelo anunciado no catálogo e ainda sem cotação da KTC.", CATALOGO,
                          gsm=gsm, cotton_pct=1.0, poliester_pct=0.0)
                if p is not None:
                    criados.append(("roupão", p.nome))

        # edredons pedidos à Daune
        for gramatura in EDREDOM_GRAMATURAS:
            for composicao in EDREDOM_COMPOSICOES:
                for medida in EDREDOM_MEDIDAS:
                    largura, comprimento = _dimensoes(medida)
                    sku = f"DAUNE · Edredom · {composicao} · {gramatura} · {medida}"
                    p = _novo(s, sku, f"Edredom {gramatura} {medida}", "Edredom / Insert",
                              f"{medida} · {gramatura} · {composicao}", "Duvet Insert", daune,
                              "Medida e gramatura pedidas à Daune no bloco 'TAMANHOS DE EDREDOM "
                              "SOLICITADOS' e que voltaram sem preço. O catálogo anuncia duvet de "
                              "250 g nessas medidas.", PLANILHA_DAUNE,
                              largura_cm=largura, comprimento_cm=comprimento)
                    if p is not None:
                        criados.append(("edredom Daune", p.nome))

        # capa de almofada Decor Tricot
        p = _novo(s, "DECOR_TRICOT · Capa de almofada · medida a definir",
                  "Capa de almofada Decor Tricot", "Capa de Almofada",
                  "50% algodão / 50% acrílico · medida a definir", "Cushion Cover", decor,
                  "O catálogo lista capa de almofada na linha Decor Tricot, mas nenhum documento "
                  "traz medida nem preço. Pedir ao fornecedor as medidas disponíveis e o "
                  "orçamento — depois é só desdobrar este SKU nas medidas reais.", CATALOGO,
                  cotton_pct=0.50)
        if p is not None:
            criados.append(("decor tricot", p.nome))

        # margem-alvo dos novos (não depende de custo)
        if not dry_run:
            s.flush()
            for _grupo, nome in criados:
                produto = s.exec(select(Produto).where(Produto.nome == nome)).first()
                if produto:
                    produto.margem_padrao_pct = ps.margem_padrao(s, produto).margem_pct
                    s.add(produto)
            s.commit()
        else:
            s.rollback()

    return criados


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    criados = adicionar(args.dry_run)
    from collections import Counter
    print(f"{len(criados)} SKUs criados")
    for grupo, n in Counter(g for g, _ in criados).most_common():
        print(f"  {n:3d}  {grupo}")

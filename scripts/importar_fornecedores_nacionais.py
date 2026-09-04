#!/usr/bin/env python3
"""Traz Daune e Decor Tricot para o mesmo catálogo — sem passar por nada de KTC.

Fornecedor nacional não tem motor industrial, não tem EXW, não tem frete internacional, não
tem Imposto de Importação e não tem despesa de nacionalização. O que ele tem é um custo (ou,
no caso da Daune, hoje só uma referência de preço) e, daí em diante, o mesmo motor comercial
da Anara: ICMS do cenário, PIS/COFINS, encargo financeiro, comissão e margem líquida — que
para os dois é 14% por regra de fornecedor.

Fontes:
* Daune — `tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx`. A coluna "Preço Final Anara
  (R$)" é o **preço que a Daune fatura para a Anara**, confirmado pelo próprio fornecedor:
  "esse preço que eu passei é o preço da Daune faturando para vocês; tudo que colocar em cima
  é lucro de vocês e imposto". Ou seja, é **custo de compra** — entra como custo NET, e o preço
  de venda sai do motor comercial com a margem de 14%.

  As colunas de Trousseau e Fio a Fio ao lado são preços de concorrente, para comparação.

  Uma coisa fica em aberto de propósito: a Daune falou em "se creditar dos impostos" na compra.
  Crédito de ICMS na aquisição não está cadastrado em lugar nenhum e **não foi inventado aqui** —
  o custo entra cheio. Se a Anara aproveita esse crédito, o custo real é menor do que o sistema
  mostra, e isso precisa virar configuração explícita.
* Decor Tricot — orçamento "ORÇAMENTO ANARA - 240826" (24/08/2026), peseiras nos modelos
  Relevo, Sofia, Arezzo e Sissi. Valor tratado como **custo de compra**; se for preço de
  venda, é só corrigir no cadastro do produto (o SKU fica marcado para confirmação).

Uso:
    python3 scripts/importar_fornecedores_nacionais.py --dry-run
    python3 scripts/importar_fornecedores_nacionais.py
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

import openpyxl
from sqlmodel import Session, select

from app import pricing_service as ps
from app.db import engine
from app.migrations import fazer_backup
from app.models import CostConfidence, CostMethod, CustoReferencia, Fornecedor, Produto
from app.pricing_engine import calcular_por_margem
from app.dinheiro import D, divide, para_float  # noqa: E402

ARQ_DAUNE = os.path.expanduser(
    "~/Anara-Cotacao/referencia/tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx")
DOC_DAUNE = "tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx"
DATA_DAUNE = date(2026, 8, 21)

FAMILIA_DAUNE = {
    "travesseiros": ("Pillow", "Travesseiros"),
    "capa protetora para travesseiros": ("Pillow Protector", "Protetor de Fronha"),
    "protetor de colchão": ("Mattress Protector", "Protetor Colchão"),
    "pillow top": ("Mattress Topper", "Topper Colchão"),
    "edredom": ("Duvet Insert", "Edredom / Insert"),
    "edredom qtas gramas?": ("Duvet Insert", "Edredom / Insert"),
}

# Orçamento Decor Tricot 24/08/2026 — peseiras (bed runners), preço por medida e modelo.
DECOR_TRICOT = {
    "documento": "ORÇAMENTO ANARA - 240826 (Decor Tricot)",
    "data": date(2026, 8, 24),
    "modelos": ["RELEVO", "SOFIA", "AREZZO", "SISSI"],
    "linhas": [
        ("0,60x1,90", 60, 190, [130.58, 128.36, 128.36, 127.32]),
        ("0,60x2,40", 60, 240, [164.95, 162.14, 162.14, 160.83]),
        ("0,60x2,70", 60, 270, [185.56, 182.41, 182.41, 180.94]),
        ("0,60x2,85", 60, 285, [195.87, 192.54, 192.54, 190.99]),
    ],
}


def _norm(texto):
    return (texto or "").strip().lower()


def ler_daune():
    wb = openpyxl.load_workbook(ARQ_DAUNE, data_only=True)
    ws = wb["Tabela de Preços"]
    itens = []
    categoria_atual = None
    for r in range(4, ws.max_row + 1):
        categoria = ws[f"A{r}"].value
        descricao = ws[f"B{r}"].value
        tamanho = ws[f"C{r}"].value
        preco = ws[f"D{r}"].value
        if categoria:
            categoria_atual = categoria
        if not descricao or not isinstance(preco, (int, float)) or not tamanho:
            continue
        familia, cat_catalogo = FAMILIA_DAUNE.get(_norm(categoria_atual), (None, categoria_atual))
        # colunas E e F: o que a Daune cobra de Trousseau e de Fio a Fio pelo mesmo item.
        # Servem de comparação de compra — o que a Anara paga contra o que os outros pagam.
        concorrentes = {}
        for coluna, cliente in (("E", "Trousseau"), ("F", "Fio a Fio")):
            valor = ws[f"{coluna}{r}"].value
            if isinstance(valor, (int, float)):
                concorrentes[cliente] = float(valor)
        itens.append({
            "categoria": (cat_catalogo or "").strip(), "familia": familia,
            "descricao": str(descricao).strip(), "tamanho": str(tamanho).strip(),
            "custo_compra": float(preco), "concorrentes": concorrentes,
        })
    return itens


def _dimensoes(tamanho: str):
    import re
    m = re.search(r"(\d+)\s*[xX]\s*(\d+)", tamanho.replace(",", "."))
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def importar(dry_run=False):
    if not dry_run:
        fazer_backup("fornecedores-nacionais")

    criados = {"DAUNE": 0, "DECOR_TRICOT": 0}
    atualizados = {"DAUNE": 0, "DECOR_TRICOT": 0}

    with Session(engine) as s:
        daune = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
        decor = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).first()
        cenario = ps.cenario_padrao_catalogo(s)
        regras, _ = ps.regras_da_cotacao(s, cenario)

        # ---------------- Daune ----------------
        for item in ler_daune():
            nome = f"{item['descricao']} {item['tamanho']}".strip()
            sku = f"DAUNE · {item['categoria']} · {item['descricao']} · {item['tamanho']}"
            largura, comprimento = _dimensoes(item["tamanho"])
            existente = s.exec(select(Produto).where(Produto.sku_key == sku)).first()
            p = existente or Produto(sku_key=sku, nome=nome)
            p.nome = nome
            p.categoria = item["categoria"]
            p.especificacao = f"{item['tamanho']} · {item['descricao']}"
            p.familia = item["familia"]
            p.subcategoria = item["categoria"]
            p.largura_cm, p.comprimento_cm = largura, comprimento
            p.fornecedor_id = daune.id
            p.cost_method = CostMethod.national_supplier.value
            p.custo_confianca = CostConfidence.quoted.value
            p.ativo = True
            p.custo_unitario = item["custo_compra"]      # preço da Daune faturando para a Anara
            p.custo_ref_valor = item["custo_compra"]
            p.custo_ref_moeda = "BRL"
            p.custo_ref_data = DATA_DAUNE
            p.custo_ref_documento = DOC_DAUNE
            p.custo_ref_cliente = "ANARA"
            p.custo_ref_tipo = "SUPPLIER_COST"
            p.custo_ref_nota = ("Preço da Daune faturando para a Anara (confirmado pelo "
                                "fornecedor). É custo de compra, não preço de venda.")
            p.precisa_revisao = False
            p.revisao_motivo = ("Custo cheio, sem crédito de imposto na aquisição — o crédito de "
                                "ICMS de compra nacional não está cadastrado no sistema. Se a "
                                "Anara aproveita esse crédito, o custo real é menor.")
            margem = ps.margem_padrao(s, p)
            p.margem_padrao_pct = para_float(margem.margem_pct)
            res = calcular_por_margem(p.custo_unitario, 1, margem.margem_pct, regras)
            p.preco_base = para_float(res.preco_negociado)
            if not dry_run:
                s.add(p)
                s.flush()
                _referencia(s, p, daune.id, item["custo_compra"], "BRL", "SUPPLIER_COST",
                            DATA_DAUNE, DOC_DAUNE,
                            "Preço da Daune faturando para a Anara — custo de compra.")
                for cliente, valor in item.get("concorrentes", {}).items():
                    _referencia(s, p, daune.id, valor, "BRL", "SUPPLIER_COST_OUTRO_CLIENTE",
                                DATA_DAUNE, DOC_DAUNE,
                                f"O que a Daune cobra de {cliente} pelo mesmo item — comparação "
                                "de compra, não entra em preço.", cliente=cliente, aplicado=False)
            criados["DAUNE"] += 0 if existente else 1
            atualizados["DAUNE"] += 1 if existente else 0

        # ---------------- Decor Tricot ----------------
        for tamanho, largura, comprimento, precos in DECOR_TRICOT["linhas"]:
            for modelo, preco in zip(DECOR_TRICOT["modelos"], precos):
                nome = f"Peseira {modelo.title()} {tamanho}"
                sku = f"DECOR_TRICOT · Peseira · {modelo} · {tamanho}"
                existente = s.exec(select(Produto).where(Produto.sku_key == sku)).first()
                p = existente or Produto(sku_key=sku, nome=nome)
                p.nome = nome
                p.categoria = "Bed Runner"
                p.especificacao = f"{tamanho} · tricô modelo {modelo.title()}"
                p.familia = "Bed Runner"
                p.subcategoria = "Peseira"
                p.largura_cm, p.comprimento_cm = largura, comprimento
                p.fornecedor_id = decor.id
                p.cost_method = CostMethod.national_supplier.value
                p.custo_confianca = CostConfidence.quoted.value
                p.ativo = True
                p.custo_unitario = preco
                p.custo_ref_valor = preco
                p.custo_ref_moeda = "BRL"
                p.custo_ref_data = DECOR_TRICOT["data"]
                p.custo_ref_documento = DECOR_TRICOT["documento"]
                p.custo_ref_cliente = "ANARA"
                p.custo_ref_tipo = "SUPPLIER_COST"
                p.precisa_revisao = True
                p.revisao_motivo = ("Valor do orçamento Decor Tricot de 24/08/2026 tratado como "
                                    "CUSTO DE COMPRA. Se na verdade for preço de venda, corrigir "
                                    "no cadastro — o preço calculado sai de cima desse número.")
                margem = ps.margem_padrao(s, p)
                p.margem_padrao_pct = para_float(margem.margem_pct)
                res = calcular_por_margem(preco, 1, margem.margem_pct, regras)
                p.preco_base = para_float(res.preco_negociado)
                if not dry_run:
                    s.add(p)
                    s.flush()
                    _referencia(s, p, decor.id, preco, "BRL", "SUPPLIER_COST",
                                DECOR_TRICOT["data"], DECOR_TRICOT["documento"],
                                "Orçamento do fornecedor — confirmar se é custo de compra.")
                criados["DECOR_TRICOT"] += 0 if existente else 1
                atualizados["DECOR_TRICOT"] += 1 if existente else 0

        if dry_run:
            s.rollback()
        else:
            s.commit()

    return {"criados": criados, "atualizados": atualizados}


def _referencia(session, produto, fornecedor_id, valor, moeda, tipo, data_ref, documento, notas,
                cliente="ANARA", aplicado=True):
    ja = session.exec(select(CustoReferencia)
                      .where(CustoReferencia.produto_id == produto.id)
                      .where(CustoReferencia.documento == documento)
                      .where(CustoReferencia.valor == valor)).first()
    if ja:
        return
    session.add(CustoReferencia(produto_id=produto.id, sku_key=produto.sku_key,
                                fornecedor_id=fornecedor_id, tipo=tipo, valor=valor, moeda=moeda,
                                data_ref=data_ref, documento=documento, cliente_documento=cliente,
                                confianca=CostConfidence.quoted.value, aplicado=aplicado,
                                notas=notas))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(importar(args.dry_run))

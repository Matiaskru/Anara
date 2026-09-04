#!/usr/bin/env python3
"""Linha Daune — Edredom 100% poliéster 280 g. Fonte nova, SKUs próprios.

Os nove preços abaixo são **preço BRUTO DO FORNECEDOR**, informados pelo responsável do projeto
em 03/09/2026. Não são preço final Anara: cada um percorre bruto → créditos de entrada → CUSTO
NET → motor comercial → margem de 14% → preço recomendado.

A trava que este script existe para respeitar: **280 g é uma linha própria.** Três das nove
dimensões coincidem com SKUs que o catálogo já tem — 190×260, 285×265 e 290×260 — e esses SKUs
são de 180 g ou 250 g. Coincidir a medida não é ser o mesmo produto. Sobrescrevê-los daria a um
edredom de 180 g o preço de um de 280 g.

Por isso o script **cria SKUs 280 g separados** e não toca em nenhum existente. Se um SKU 280 g
já existir com a mesma medida e composição, ele recebe uma versão nova de custo — que é o
caminho não destrutivo do `custo_service`.

Uso:
    python3 scripts/cadastrar_edredom_280g.py            # simulação, não escreve
    python3 scripts/cadastrar_edredom_280g.py --aplicar
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.custo_service import cnet_nacional, registrar_daune, referencia_vigente  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Fornecedor, Produto, StatusCusto  # noqa: E402
from app.dinheiro import D, divide, para_float  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.path.join(RAIZ, "relatorios", "edredom_280g.json")

GRAMATURA = 280
COMPOSICAO = "100% fibras de poliéster"
FONTE = ("Tabela Daune de edredom 100% poliéster 280 g, fornecida pelo responsável do projeto "
         "em 03/09/2026")
DOCUMENTO = "Daune — edredom poliéster 280g (03/09/2026)"
DATA_REF = date(2026, 9, 3)

# dimensão (largura × comprimento) → preço BRUTO do fornecedor, em R$
PRECOS_BRUTOS = [
    (180, 250, 427.50),
    (190, 260, 469.30),
    (220, 250, 495.00),
    (230, 260, 538.20),
    (250, 250, 562.50),
    (260, 260, 608.40),
    (285, 265, 679.72),
    (290, 245, 639.45),
    (290, 260, 678.60),
]


def sku_de(largura: int, comprimento: int) -> str:
    return f"DAUNE · Edredom / Insert · {COMPOSICAO} · {GRAMATURA} g · {largura}x{comprimento}"


def cadastrar(aplicar: bool = False) -> dict:
    linhas = []
    with Session(engine) as s:
        daune = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
        if daune is None:
            raise RuntimeError("fornecedor DAUNE não encontrado")

        existentes = s.exec(select(Produto).where(Produto.fornecedor_id == daune.id)).all()

        for largura, comprimento, gross in PRECOS_BRUTOS:
            conta = cnet_nacional(gross)
            sku = sku_de(largura, comprimento)

            # match exato: mesma medida E mesma gramatura. Medida sozinha não basta — é
            # exatamente o erro que esta linha nova poderia causar.
            exato = next((p for p in existentes
                          if p.sku_key == sku
                          or (p.largura_cm == largura and p.comprimento_cm == comprimento
                              and p.gsm == GRAMATURA)), None)
            mesma_medida_outra_gramatura = [
                p for p in existentes
                if p.largura_cm == largura and p.comprimento_cm == comprimento
                and (p.gsm or 0) != GRAMATURA]

            registro = {
                "medida": f"{largura}x{comprimento}", "gross": gross,
                "cnet": para_float(conta.cnet),
                "icms_credito": para_float(conta.icms_credito),
                "pis_cofins_credito": para_float(conta.pis_cofins_credito),
                "sku": sku,
                "acao": "versao_nova" if exato else "sku_novo",
                "skus_de_outra_gramatura_na_mesma_medida": [
                    {"sku": p.sku_key, "gramatura": p.gsm,
                     "nome": p.nome[:60]} for p in mesma_medida_outra_gramatura],
                "preservados": len(mesma_medida_outra_gramatura),
            }
            linhas.append(registro)

            if not aplicar:
                continue

            produto = exato
            if produto is None:
                produto = Produto(
                    sku_key=sku,
                    nome=f"Edredom {largura}x{comprimento} · {GRAMATURA} g · {COMPOSICAO}",
                    categoria="Edredom", familia="Duvet Insert",
                    especificacao=f"{largura}x{comprimento} · {GRAMATURA} g · {COMPOSICAO}",
                    largura_cm=float(largura), comprimento_cm=float(comprimento),
                    gsm=GRAMATURA, poliester_pct=100.0, cotton_pct=0.0,
                    fornecedor_id=daune.id, ativo=True)
                s.add(produto)
                s.flush()
                registro["produto_id"] = produto.id
            else:
                registro["produto_id"] = produto.id

            ref = registrar_daune(
                s, produto, gross, fonte=FONTE, documento=DOCUMENTO, data_ref=DATA_REF,
                status=StatusCusto.confirmado.value,
                origem_registro="cadastro-linha-280g",
                notas="Match direto exato: fornecedor, família, composição, gramatura e medida")
            produto.status_custo = StatusCusto.confirmado.value
            s.add(produto)
            registro["versao"] = ref.versao
        if aplicar:
            s.commit()

    return {"gerado_em": date.today().isoformat(), "gramatura": GRAMATURA,
            "composicao": COMPOSICAO, "fonte": FONTE, "aplicado": aplicar, "linhas": linhas}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--saida", default=SAIDA)
    a = p.parse_args()

    r = cadastrar(aplicar=a.aplicar)
    with open(a.saida, "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)

    print(f"relatório em {a.saida}")
    print(f"{'medida':>10} {'bruto R$':>10} {'CNET R$':>10}  ação            preservados")
    for linha in r["linhas"]:
        print(f"{linha['medida']:>10} {linha['gross']:>10.2f} {linha['cnet']:>10.4f}  "
              f"{linha['acao']:<15} {linha['preservados']} SKU(s) de outra gramatura")
    print("\nOs valores brutos NÃO são preço final Anara — falta fiscal, financeiro, "
          "comissão e a margem de 14%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

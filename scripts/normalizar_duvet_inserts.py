#!/usr/bin/env python3
"""Normaliza os edredons Daune: gramatura estruturada e legado ambíguo fora da seleção ativa.

Dois problemas distintos, com tratamentos distintos:

**A) 8 SKUs legados genéricos.** "Edredom 156x230 · 100% plumas de ganso" — sem gramatura em
lugar nenhum. Não dá para atribuir 180, 250 ou 280 sem chutar, e chutar aqui trocaria o produto.
Eles são duplicatas históricas das medidas que os SKUs estruturados já cobrem. Tratamento:
`ativo = False` — saem da seleção comercial de cotação nova, **continuam no banco** e continuam
referenciáveis pelo histórico. Nada é apagado.

**B) 20 SKUs estruturados.** "Edredom 190x260 · 180 g · 100% plumas de ganso" — a gramatura está
na descrição, de forma inequívoca; só o campo `gsm` está nulo. Campo nulo não é ausência de
informação quando o próprio produto declara o dado. Tratamento: backfill determinístico de `gsm`
a partir da descrição, aceitando apenas o padrão explícito.

O script mostra a tabela antes de escrever. Sem `--aplicar`, não toca em nada.

Uso:
    python3 scripts/normalizar_duvet_inserts.py
    python3 scripts/normalizar_duvet_inserts.py --aplicar
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import CotacaoItem, Fornecedor, Produto  # noqa: E402
from scripts.reconciliar_daune import extrair_composicao, extrair_gramatura  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.path.join(RAIZ, "relatorios", "duvet_inserts.json")

MOTIVO_LEGADO = ("SKU legado genérico: a descrição não declara gramatura, e edredom sem "
                 "gramatura não é um produto determinado. As mesmas medidas estão cobertas por "
                 "SKUs estruturados. Inativado para cotação nova; preservado para histórico.")


def normalizar(aplicar: bool = False) -> dict:
    linhas = []
    with Session(engine) as s:
        daune = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
        produtos = s.exec(select(Produto)
                          .where(Produto.fornecedor_id == daune.id)
                          .where(Produto.familia == "Duvet Insert")
                          .order_by(Produto.id)).all()

        usados = {i.produto_id for i in s.exec(select(CotacaoItem)).all() if i.produto_id}

        for p in produtos:
            texto = f"{p.nome} {p.especificacao or ''}"
            extraida = extrair_gramatura(texto)
            registro = {
                "produto_id": p.id, "sku": p.sku_key, "descricao": p.nome[:70],
                "gsm_atual": p.gsm, "gsm_extraido": extraida,
                "composicao": extrair_composicao(texto),
                "medida": (f"{p.largura_cm:g}x{p.comprimento_cm:g}"
                           if p.largura_cm and p.comprimento_cm else None),
                "ativo_antes": bool(p.ativo), "usado_em_cotacao": p.id in usados,
            }
            if extraida is None:
                registro.update(grupo="legado_generico", acao="inativar", gsm_final=None,
                                justificativa=MOTIVO_LEGADO)
                if aplicar and p.ativo:
                    p.ativo = False
                    p.revisao_motivo = MOTIVO_LEGADO
                    s.add(p)
            elif p.gsm == extraida:
                registro.update(grupo="estruturado", acao="ja_estava_correto",
                                gsm_final=p.gsm, justificativa="gsm já batia com a descrição")
            else:
                registro.update(grupo="estruturado", acao="backfill", gsm_final=extraida,
                                justificativa=("Gramatura declarada de forma inequívoca na "
                                               "descrição do SKU; campo `gsm` estava nulo"))
                if aplicar:
                    p.gsm = extraida
                    s.add(p)
            linhas.append(registro)
        if aplicar:
            s.commit()

    resumo = {}
    for linha in linhas:
        chave = f"{linha['grupo']}/{linha['acao']}"
        resumo[chave] = resumo.get(chave, 0) + 1
    return {"gerado_em": date.today().isoformat(), "aplicado": aplicar,
            "resumo": resumo, "linhas": linhas}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--saida", default=SAIDA)
    a = p.parse_args()

    r = normalizar(aplicar=a.aplicar)
    with open(a.saida, "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)

    print(f"relatório em {a.saida}")
    print(f"{'SKU':<52}{'gsm':>6}{'extraído':>10}  ação")
    for linha in r["linhas"]:
        print(f"{linha['descricao'][:52]:<52}{str(linha['gsm_atual']):>6}"
              f"{str(linha['gsm_extraido']):>10}  {linha['acao']}")
    print("\nresumo:", r["resumo"])
    if not a.aplicar:
        print("(nada foi escrito — use --aplicar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

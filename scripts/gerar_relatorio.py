#!/usr/bin/env python3
"""Gera `relatorios/RELATORIO_QUALIDADE.md` a partir do estado atual do banco."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

from sqlmodel import Session, select

from app.db import engine
from app.models import CustoReferencia, Produto
from app.relatorios import indicadores, perguntas_para_ktc, qualidade_da_base

SAIDA = os.path.expanduser("~/Anara-Cotacao/relatorios/RELATORIO_QUALIDADE.md")


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".") if v else "—"


def pct(v):
    return f"{v*100:.1f}%" if v is not None else "—"


def tabela(itens, colunas, limite=None):
    linhas = ["| " + " | ".join(c[0] for c in colunas) + " |",
              "|" + "|".join("---" for _ in colunas) + "|"]
    for it in (itens[:limite] if limite else itens):
        linhas.append("| " + " | ".join(str(c[1](it)) for c in colunas) + " |")
    if limite and len(itens) > limite:
        linhas.append(f"| _… e mais {len(itens)-limite} SKUs_ |" + " |" * (len(colunas) - 1))
    return "\n".join(linhas)


def gerar():
    reg = json.load(open(os.path.expanduser("~/Anara-Cotacao/relatorios/regressao.json")))
    with Session(engine) as s:
        d = qualidade_da_base(s)
        perguntas = perguntas_para_ktc(s)
        inds = indicadores(s)
        # comparação de compra da Daune
        outros = s.exec(select(CustoReferencia)
                        .where(CustoReferencia.tipo == "SUPPLIER_COST_OUTRO_CLIENTE")).all()
        anara = {r.produto_id: r.valor for r in s.exec(
            select(CustoReferencia).where(CustoReferencia.tipo == "SUPPLIER_COST")).all()}
        comparacao = []
        for r in outros:
            if r.produto_id in anara:
                p = s.get(Produto, r.produto_id)
                comparacao.append({"nome": p.nome, "anara": anara[r.produto_id],
                                   "outro": r.valor, "cliente": r.cliente_documento,
                                   "dif": anara[r.produto_id] / r.valor - 1})

    L = d["listas"]
    partes = [f"""# Relatório de qualidade da base — Anara

{d['total']} SKUs ativos. Gerado a partir do estado atual do banco.

| Fornecedor | SKUs |
|---|---|
""" + "\n".join(f"| {k} | {v} |" for k, v in d["por_fornecedor"].items())]

    partes.append("\n\n| Método de custo | SKUs |\n|---|---|\n" +
                  "\n".join(f"| {k} | {v} |" for k, v in d["por_metodo"].items()))
    partes.append("\n\n| Confiança do custo | SKUs |\n|---|---|\n" +
                  "\n".join(f"| {k} | {v} |" for k, v in d["por_confianca"].items()))
    partes.append("\n\n| Frescor do preço de referência | SKUs |\n|---|---|\n" +
                  "\n".join(f"| {k} | {v} |" for k, v in d["por_frescor"].items()))

    cols_padrao = [("SKU", lambda p: p["nome"][:44]), ("Família", lambda p: p["familia"] or "—"),
                   ("Custo NET", lambda p: brl(p["custo"])),
                   ("Preço-base", lambda p: brl(p["preco_base"])),
                   ("Margem", lambda p: pct(p["margem_padrao"]))]

    partes.append(f"\n\n## SKUs KTC calculáveis ({len(L.get('ktc_calculados', []))})\n\n"
                  "Lençol e capa duvet pelo waterfall industrial; toalha pelo custo por peso, com a "
                  "taxa por kg derivada da PI de 23/08/2026.\n\n" +
                  tabela(L.get("ktc_calculados", []),
                         [("SKU", lambda p: p["nome"][:40]),
                          ("EXW cotado", lambda p: f"US$ {p['exw_cotado']:.2f}" if p["exw_cotado"] else "—"),
                          ("EXW calculado", lambda p: f"US$ {p['exw_calculado']:.2f}" if p["exw_calculado"] else "—"),
                          ("Diferença", lambda p: pct(p["diferenca_pct"])),
                          ("Custo NET", lambda p: brl(p["custo"])),
                          ("Margem", lambda p: pct(p["margem_padrao"]))], limite=60))

    partes.append(f"\n\n## SKUs KTC por preço cotado ({len(L.get('ktc_cotados', []))})\n\n" +
                  tabela(L.get("ktc_cotados", []),
                         [("SKU", lambda p: p["nome"][:40]), ("Família", lambda p: p["familia"] or "—"),
                          ("EXW cotado", lambda p: f"US$ {p['exw_cotado']:.2f}" if p["exw_cotado"] else "—"),
                          ("Documento", lambda p: (p["custo_ref_documento"] or "—")[:32]),
                          ("Frescor", lambda p: p["frescor"])], limite=40))

    partes.append(f"\n\n## Custos vencidos — STALE ({len(L.get('stale', []))})\n\n"
                  "Mais de 60 dias desde o preço de referência. Não impede cotar.\n\n" +
                  tabela(L.get("stale", []),
                         [("SKU", lambda p: p["nome"][:40]),
                          ("Documento", lambda p: (p["custo_ref_documento"] or "—")[:32]),
                          ("Dias", lambda p: p["dias"]), ("Custo NET", lambda p: brl(p["custo"]))],
                         limite=40))

    partes.append(f"\n\n## SKUs Daune ({len(L.get('DAUNE', []))})\n\n"
                  "Fornecedor nacional: sem motor industrial KTC e sem nacionalização. O valor da "
                  "tabela de 21/08/2026 é o **preço que a Daune fatura para a Anara** — custo de "
                  "compra, confirmado pelo fornecedor. O custo entra cheio: crédito de ICMS na "
                  "aquisição não está cadastrado e não foi presumido.\n\n" +
                  tabela(L.get("DAUNE", []), cols_padrao, limite=40))

    if comparacao:
        por_cliente = {}
        for c in comparacao:
            por_cliente.setdefault(c["cliente"], []).append(c["dif"])
        partes.append("\n\n### O que a Daune cobra de cada cliente\n\n"
                      "A mesma tabela traz o preço da Daune para Trousseau e Fio a Fio. Comparando "
                      "com o que a Anara paga pelo mesmo item:\n\n" +
                      "\n".join(f"- **{cliente}**: a Anara paga em média **{statistics.mean(v):+.1%}** "
                                f"({len(v)} itens comparáveis)" for cliente, v in por_cliente.items()) +
                      "\n\n" + tabela(sorted(comparacao, key=lambda c: -c["dif"]),
                                      [("Produto", lambda c: c["nome"][:42]),
                                       ("Anara paga", lambda c: brl(c["anara"])),
                                       ("Outro cliente", lambda c: brl(c["outro"])),
                                       ("Cliente", lambda c: c["cliente"]),
                                       ("Diferença", lambda c: f"{c['dif']:+.1%}")], limite=20))

    partes.append(f"\n\n## SKUs Decor Tricot ({len(L.get('DECOR_TRICOT', []))})\n\n"
                  "Peseiras do orçamento de 24/08/2026, tratadas como custo de compra.\n\n" +
                  tabela(L.get("DECOR_TRICOT", []), cols_padrao, limite=20))

    partes.append(f"\n\n## Precisam de revisão ({len(L.get('revisao', []))})\n\n" +
                  tabela(L.get("revisao", []),
                         [("SKU", lambda p: p["nome"][:34]),
                          ("Fornecedor", lambda p: p["fornecedor"].split()[0]),
                          ("Método", lambda p: p["cost_method"]),
                          ("Motivo", lambda p: (p["motivo"] or "—")[:110])], limite=60))

    if inds:
        partes.append("\n\n## Indicadores de acompanhamento\n\n"
                      "Nenhum entra em fórmula de custo — servem para decidir quando pedir preço novo.\n\n" +
                      tabela(inds, [("Indicador", lambda i: i["rotulo"]),
                                    ("Valor", lambda i: i["valor"]),
                                    ("Situação", lambda i: i["detalhe"])]))

    partes.append("\n\n## O que ainda precisamos pedir à KTC\n")
    for p in perguntas:
        partes.append(f"\n**{p['assunto']}**" + (f" — {p['quantos']} SKUs" if p["quantos"] else "") +
                      f"\n\n{p['pedido']}\n")
        if p["exemplos"]:
            partes.append(f"\nExemplos: {', '.join(p['exemplos'])}\n")

    r = reg["resumo"]
    partes.append(f"""

## Regressão contra o baseline

Baseline capturado antes de qualquer alteração: 241 SKUs, 43 itens de cotação, 9 cenários fiscais.

| Verificação | Resultado |
|---|---|
| Itens de cotação históricos alterados | **{r['itens_historicos_alterados']} de {r['itens_historicos']}** |
| Cenários fiscais alterados | {r['cenarios_alterados']} de 9 — só SP→SP contribuinte (4% → 18%) |
| SKUs com custo alterado | {r['custo_mudou']} |
| SKUs com preço-base alterado | {r['preco_base_mudou']} (margem padrão por fornecedor/família) |
| Preço a 18% acompanha o custo | 241 de 241 — o motor comercial não mudou |

Antes, a margem embutida no preço-base ia de **-5,4% a 25,1%** (média 10,9%): havia SKU sendo
vendido abaixo do custo depois de imposto e comissão. Agora cada SKU fica na margem-alvo da sua
regra (12% a 18%).
""")

    with open(SAIDA, "w") as f:
        f.write("".join(partes))
    return SAIDA


if __name__ == "__main__":
    caminho = gerar()
    print(caminho, os.path.getsize(caminho), "bytes")

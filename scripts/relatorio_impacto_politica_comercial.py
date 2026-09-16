#!/usr/bin/env python3
"""Relatório de impacto da política comercial de 16/09/2026 sobre o preço recomendado.

    python3 scripts/relatorio_impacto_politica_comercial.py            # do banco, por data
    python3 scripts/relatorio_impacto_politica_comercial.py --proposta # regras novas em memória

Para TODOS os SKUs ativos com custo calculável, no cenário canônico do catálogo (a
premissa `catalogo_*`: SP → SP, não contribuinte, 30 dias):

    SKU · fornecedor · família · margem antiga · margem nova · piso ·
    comissão recomendada antiga · nova · preço recomendado antigo · novo · Δ R$ · Δ %

"Antigo" é o preço formado pela regra vigente em **15/09/2026** com a comissão por faixa de
markup; "novo", pela regra vigente em **16/09/2026** com a comissão de formação da política.
Os dois saem do **mesmo motor** (`calcular_por_margem`), com o mesmo CNET, o mesmo fiscal e
o mesmo encargo — só margem-alvo e comissão mudam. Nada é gravado; nenhum item de cotação é
tocado.

`--proposta` existe para o preview ANTES de aplicar a política no banco: as regras novas são
derivadas em memória das regras vigentes (`politica_comercial.regra_da_politica`) e as
premissas de comissão vêm das constantes da decisão. Depois de aplicada, o relatório sem
flag lê tudo do banco — e os dois têm de coincidir.

Saída: `relatorios/impacto_politica_comercial_2026-09-16.md` e `.json`.
"""
import argparse
import json
import os
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app import config_service as cfg  # noqa: E402
from app import politica_comercial as pol  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.db import engine  # noqa: E402
from app.dinheiro import D, D0, ZERO, dinheiro, divide, para_float  # noqa: E402
from app.margin_rules import resolver_margem  # noqa: E402
from app.models import Fornecedor, MargemRegra, Produto  # noqa: E402
from app.pricing_engine import TaxRuleSet, calcular_por_margem, com_comissao_fixa  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA_MD = os.path.join(RAIZ, "relatorios", "impacto_politica_comercial_2026-09-16.md")
SAIDA_JSON = os.path.join(RAIZ, "relatorios", "impacto_politica_comercial_2026-09-16.json")
VESPERA = date(2026, 9, 15)


def _regras_propostas(session, regras_antigas):
    """As sucessoras, em memória, derivadas das regras vigentes na véspera."""
    codigos = {f.id: f.codigo for f in session.exec(select(Fornecedor)).all()}
    novas = []
    for r in regras_antigas:
        if r.politica is not None:
            continue
        p = pol.regra_da_politica(r.margem_pct, codigos.get(r.fornecedor_id))
        novas.append(MargemRegra(
            id=None, nome=pol.nome_da_regra_nova(r.nome), fornecedor_id=r.fornecedor_id,
            familia=r.familia, sku_key=r.sku_key, min_thread_count=r.min_thread_count,
            max_thread_count=r.max_thread_count, margem_pct=para_float(p["margem_pct"]),
            prioridade=r.prioridade, valid_from=pol.DATA_VIGENCIA, ativo=True,
            piso_pct=para_float(p["piso_pct"]),
            comissao_formacao_pct=para_float(p["comissao_formacao_pct"]),
            preco_travado=p["preco_travado"], margem_anterior_pct=r.margem_pct,
            politica=pol.ROTULO, fonte=pol.FONTE))
    return novas


def gerar(proposta: bool = False) -> dict:
    linhas = []
    with Session(engine) as s:
        fornecedores = {f.id: f for f in session_forn(s)}
        regras = s.exec(select(MargemRegra)).all()
        antigas = [r for r in regras if r.politica is None]
        if proposta:
            novas = _regras_propostas(s, antigas)
            # cópias DESLIGADAS da sessão: nada aqui pode virar UPDATE, nem por autoflush
            antigas = [MargemRegra(**{**r.model_dump(),
                                      "valid_to": r.valid_to or pol.DATA_VIGENCIA})
                       for r in antigas]
        else:
            novas = [r for r in regras if r.politica is not None]
        cenario = ps.cenario_padrao_catalogo(s)
        produtos = s.exec(select(Produto).where(Produto.ativo == True)  # noqa: E712
                          .order_by(Produto.id)).all()
        for p in produtos:
            custo, _mem = ps.custo_para_precificar(s, p)
            if not custo or D0(custo) <= ZERO:
                continue
            # o TaxRuleSet do cenário, com a tabela de faixas (comissão antiga)
            regras_faixa, ctx = ps.regras_da_cotacao(s, cenario, p, comissao_formacao_pct=None)
            if regras_faixa is None:
                continue
            antiga = resolver_margem(antigas, fornecedor_id=p.fornecedor_id, familia=p.familia,
                                     thread_count=p.thread_count, sku_key=p.sku_key,
                                     ref=VESPERA)
            nova = resolver_margem(antigas + novas, fornecedor_id=p.fornecedor_id,
                                   familia=p.familia, thread_count=p.thread_count,
                                   sku_key=p.sku_key, ref=pol.DATA_VIGENCIA)
            if nova.comissao_formacao_pct is None:
                continue
            r_ant = calcular_por_margem(custo, 1, antiga.margem_pct, regras_faixa)
            r_nov = calcular_por_margem(custo, 1, nova.margem_pct,
                                        com_comissao_fixa(regras_faixa,
                                                          nova.comissao_formacao_pct))
            preco_ant, preco_nov = r_ant.preco_negociado, r_nov.preco_negociado
            delta = preco_nov - preco_ant
            forn = fornecedores.get(p.fornecedor_id)
            linhas.append({
                "produto_id": p.id, "sku": p.sku_key, "nome": p.nome,
                "fornecedor": forn.codigo if forn else None,
                "familia": p.familia,
                "margem_antiga": para_float(antiga.margem_pct),
                "margem_nova": para_float(nova.margem_pct),
                "piso": para_float(nova.piso_pct),
                "comissao_antiga": para_float(regras_faixa.comissao_para_markup(
                    r_ant.markup_implicito)),
                "comissao_nova": para_float(nova.comissao_formacao_pct),
                "preco_antigo": para_float(preco_ant), "preco_novo": para_float(preco_nov),
                "delta_brl": para_float(delta),
                "delta_pct": para_float(divide(delta, preco_ant)),
            })
    grupos = {"DAUNE": [], "DECOR_TRICOT": [], "OUTROS": []}
    for l in linhas:
        grupos.get(l["fornecedor"], grupos["OUTROS"]).append(l) \
            if l["fornecedor"] in grupos else grupos["OUTROS"].append(l)
    resumo = {g: _estatisticas([l["delta_pct"] for l in ls]) for g, ls in grupos.items()}
    resumo["TOTAL"] = _estatisticas([l["delta_pct"] for l in linhas])
    return {"gerado_em": date.today().isoformat(), "modo": "proposta" if proposta else "banco",
            "cenario": {"origem": cenario.uf_origem_fiscal, "destino": cenario.estado_destino,
                        "contribuinte": cenario.contribuinte_icms,
                        "condicao": cenario.condicao_pagamento},
            "linhas": linhas, "resumo": resumo}


def session_forn(s):
    return s.exec(select(Fornecedor)).all()


def _percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p
    i, f = int(k), k - int(k)
    if i + 1 < len(ordenados):
        return ordenados[i] + (ordenados[i + 1] - ordenados[i]) * f
    return ordenados[i]


def _estatisticas(valores):
    valores = [v for v in valores if v is not None]
    if not valores:
        return {"quantidade": 0}
    return {"quantidade": len(valores), "media": statistics.fmean(valores),
            "mediana": statistics.median(valores), "minimo": min(valores),
            "maximo": max(valores), "p10": _percentil(valores, 0.10),
            "p90": _percentil(valores, 0.90)}


def _pct(v):
    return "—" if v is None else f"{v * 100:+.2f}%".replace(".", ",")


def _brl(v):
    return "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def escrever(rel: dict):
    os.makedirs(os.path.dirname(SAIDA_MD), exist_ok=True)
    with open(SAIDA_JSON, "w", encoding="utf-8") as f:
        json.dump(rel, f, ensure_ascii=False, indent=1)
    md = ["# Impacto da política comercial de 16/09/2026 no preço recomendado", "",
          f"Gerado em {rel['gerado_em']} · modo **{rel['modo']}** · cenário canônico do catálogo: "
          f"{rel['cenario']['origem']} → {rel['cenario']['destino']}, "
          f"{'contribuinte' if rel['cenario']['contribuinte'] else 'não contribuinte'}, "
          f"condição {rel['cenario']['condicao']}.", "",
          "Antigo = regra vigente em 15/09/2026 + comissão por faixa de markup. "
          "Novo = regra vigente em 16/09/2026 + comissão de formação da política "
          "(Daune 5%, demais 10%). Mesmo CNET, mesmo fiscal, mesmo encargo. Nada foi gravado.",
          "", "## Resumo (variação % do preço recomendado)", "",
          "| Grupo | SKUs | Média | Mediana | Mín | Máx | P10 | P90 |", "|---|---|---|---|---|---|---|---|"]
    for g in ("DAUNE", "DECOR_TRICOT", "OUTROS", "TOTAL"):
        e = rel["resumo"][g]
        if e.get("quantidade"):
            md.append(f"| {g} | {e['quantidade']} | {_pct(e['media'])} | {_pct(e['mediana'])} | "
                      f"{_pct(e['minimo'])} | {_pct(e['maximo'])} | {_pct(e['p10'])} | {_pct(e['p90'])} |")
        else:
            md.append(f"| {g} | 0 | — | — | — | — | — | — |")
    for g, titulo in (("DAUNE", "Daune"), ("DECOR_TRICOT", "Decor Tricot"), ("OUTROS", "Outros (KTC)")):
        ls = [l for l in rel["linhas"] if (l["fornecedor"] == g if g != "OUTROS"
                                            else l["fornecedor"] not in ("DAUNE", "DECOR_TRICOT"))]
        md += ["", f"## {titulo} — {len(ls)} SKUs", "",
               "| SKU | Família | Margem antiga | Margem nova | Piso | Comissão antiga | Comissão nova | "
               "Preço antigo | Preço novo | Δ R$ | Δ % |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for l in ls:
            md.append(f"| {l['sku']} | {l['familia'] or '—'} | {l['margem_antiga'] * 100:.0f}% | "
                      f"{l['margem_nova'] * 100:.0f}% | {l['piso'] * 100:.0f}% | "
                      f"{l['comissao_antiga'] * 100:.0f}% | {l['comissao_nova'] * 100:.0f}% | "
                      f"{_brl(l['preco_antigo'])} | {_brl(l['preco_novo'])} | "
                      f"{_brl(l['delta_brl'])} | {_pct(l['delta_pct'])} |")
    with open(SAIDA_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposta", action="store_true",
                    help="derivar as regras novas em memória (preview antes de aplicar)")
    ap.add_argument("--sem-arquivos", action="store_true")
    a = ap.parse_args()
    rel = gerar(proposta=a.proposta)
    if not a.sem_arquivos:
        escrever(rel)
        print(f"→ {SAIDA_MD}\n→ {SAIDA_JSON}")
    for g in ("DAUNE", "DECOR_TRICOT", "OUTROS", "TOTAL"):
        e = rel["resumo"][g]
        if e.get("quantidade"):
            print(f"{g:13s} n={e['quantidade']:3d}  média {_pct(e['media'])}  mediana {_pct(e['mediana'])}  "
                  f"mín {_pct(e['minimo'])}  máx {_pct(e['maximo'])}  P10 {_pct(e['p10'])}  P90 {_pct(e['p90'])}")

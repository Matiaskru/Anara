#!/usr/bin/env python3
"""Proposta READ-ONLY de agrupamento das cotações históricas em vendas (Fase 3B, §11).

    python3 scripts/relatorio_backfill_cotacoes.py

Nada é gravado. Para cada cotação sem venda vinculada, o relatório diz o que ela é (número,
cliente, revisão/genealogia, status, valor, data), com quais outras provavelmente forma a
mesma venda, e com que confiança:

    ALTA    genealogia explícita (`cotacao_origem_id`) — revisões da mesma proposta
    MEDIA   mesmo cliente, itens em comum e criadas com até 30 dias de intervalo
    BAIXA   mesmo cliente, sem item em comum ou intervalo maior; ou cotação com cara de
            teste (rascunho sem item, arquivada)

**Decisão de aplicar é humana.** Este script nunca cria oportunidade nem toca em cotação.

Saída: `relatorios/fase3b_proposta_backfill_cotacoes.md`.
"""
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app import workflow as wf  # noqa: E402
from app import workflow_service as ws  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.models import Cliente, Cotacao, CotacaoItem  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.path.join(RAIZ, "relatorios", "fase3b_proposta_backfill_cotacoes.md")
JANELA_DIAS = 30


def _brl(v):
    return "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def analisar(session: Session) -> list:
    clientes = {c.id: c.nome for c in session.exec(select(Cliente)).all()}
    cotacoes = [c for c in session.exec(select(Cotacao).order_by(Cotacao.id)).all()
                if c.oportunidade_id is None]
    itens = defaultdict(list)
    for it in session.exec(select(CotacaoItem)).all():
        itens[it.cotacao_id].append(it)

    # 1. genealogias explícitas → grupo ALTA
    grupos = {}
    for c in cotacoes:
        raiz = c.cotacao_origem_id or c.id
        grupos.setdefault(raiz, []).append(c)

    # 2. por cliente: cotações de genealogias diferentes com produto em comum e ≤ 30 dias
    produtos_de = {c.id: {it.produto_id for it in itens[c.id] if it.produto_id} for c in cotacoes}
    linhas = []
    for c in cotacoes:
        raiz = c.cotacao_origem_id or c.id
        irmas = [x for x in grupos[raiz] if x.id != c.id]
        parecidas = []
        for outra in cotacoes:
            if outra.id == c.id or (outra.cotacao_origem_id or outra.id) == raiz:
                continue
            if outra.cliente_id != c.cliente_id:
                continue
            dias = abs(((outra.criado_em or c.criado_em) - c.criado_em).days) if c.criado_em else 999
            comum = produtos_de[c.id] & produtos_de[outra.id]
            if comum and dias <= JANELA_DIAS:
                parecidas.append((outra, dias, len(comum)))
        sem_item = not itens[c.id]
        status = c.status.value if hasattr(c.status, "value") else c.status
        teste = sem_item and status == "rascunho"
        if irmas:
            confianca, proposta = "ALTA", (f"mesma venda das revisões "
                                           f"{', '.join(x.numero or str(x.id) for x in irmas)} "
                                           f"(genealogia #{raiz})")
        elif parecidas:
            confianca = "MEDIA"
            proposta = ("mesma venda de " + ", ".join(
                f"{o.numero or o.id} ({n} produto(s) em comum, {d} dias)"
                for o, d, n in parecidas))
        elif teste or c.arquivada_em:
            confianca, proposta = "BAIXA", ("cara de teste / arquivada — provavelmente não é "
                                            "venda; arquivar em vez de vincular")
        else:
            confianca, proposta = "BAIXA", "venda própria (uma cotação só) — confirmar com o comercial"
        total = wf.resumo_comercial(itens[c.id])["total_negociado"] if itens[c.id] else None
        linhas.append({
            "id": c.id, "numero": c.numero or f"#{c.id}", "cliente": clientes.get(c.cliente_id, "—"),
            "revisao": c.revisao or 1, "genealogia": raiz,
            "status": c.status.value if hasattr(c.status, "value") else c.status,
            "valor": total, "data": c.criado_em.date() if c.criado_em else None,
            "itens": len(itens[c.id]), "arquivada": bool(c.arquivada_em),
            "relacao": ("revisões: " + ", ".join(x.numero or str(x.id) for x in irmas)) if irmas
                       else (", ".join(o.numero or str(o.id) for o, _, _ in parecidas) or "—"),
            "proposta": proposta, "confianca": confianca,
        })
    return linhas


def escrever(linhas: list):
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    por_conf = {k: len([l for l in linhas if l["confianca"] == k]) for k in ("ALTA", "MEDIA", "BAIXA")}
    md = ["# Fase 3B — proposta de backfill das cotações históricas em vendas (READ-ONLY)", "",
          f"Gerado em {date.today().isoformat()} sobre `{caminho_do_banco()}`. **Nada foi aplicado.** "
          f"{len(linhas)} cotações sem venda vinculada · confiança ALTA {por_conf['ALTA']} · "
          f"MEDIA {por_conf['MEDIA']} · BAIXA {por_conf['BAIXA']}.", "",
          "Critérios: ALTA = revisões da mesma genealogia (`cotacao_origem_id`); MEDIA = mesmo "
          f"cliente, produto em comum e ≤ {JANELA_DIAS} dias; BAIXA = o resto (inclui cotações com "
          "cara de teste). Uma venda por genealogia; revisões nunca viram vendas separadas.", "",
          "| id | número | cliente | rev. | geneal. | status | itens | valor | data | relação provável | proposta | confiança |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for l in linhas:
        md.append(f"| {l['id']} | {l['numero']} | {l['cliente']} | r{l['revisao']} | #{l['genealogia']} | "
                  f"{l['status']}{' (arquivada)' if l['arquivada'] else ''} | {l['itens']} | {_brl(l['valor'])} | "
                  f"{l['data'] or '—'} | {l['relacao']} | {l['proposta']} | **{l['confianca']}** |")
    md += ["", "## Decisão pendente", "",
           "Aplicar o agrupamento exige decisão humana por linha. O caminho, quando decidido, é "
           "`POST /oportunidades/{id}/vincular` (mesmo cliente, nunca cross-client) — sem script "
           "de backfill automático."]
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


if __name__ == "__main__":
    with Session(engine) as s:
        linhas = analisar(s)
    escrever(linhas)
    print(f"→ {SAIDA} ({len(linhas)} cotações sem venda)")
    for k in ("ALTA", "MEDIA", "BAIXA"):
        print(f"  {k}: {len([l for l in linhas if l['confianca'] == k])}")

#!/usr/bin/env python3
"""§20–§23 — Auditoria de TODAS as cotações do banco (cópia), item a item.

Para cada item: recompõe o preço recomendado e a economia para o CENÁRIO SALVO no cabeçalho
(destino, contribuinte, finalidade resolvida como o runtime resolve, condição de pagamento) com
o custo pinado no item e com o custo VIGENTE; compara com o que está gravado; classifica risco.
Cotações emitidas: compara o snapshot com os itens. Produz `cotacoes_auditoria.csv`,
`propostas_enviadas_risco.csv` e `cotacoes_auditoria_resumo.json`.
"""
import csv
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, preparar  # noqa: E402

session = preparar("cotacoes")
from sqlmodel import select  # noqa: E402

from app import comercial_service as com  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app import workflow_service as ws  # noqa: E402
from app import custo_service as cs  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.fiscal_rules import FINALIDADES_CONSUMIDOR_FINAL  # noqa: E402
from app.models import Cliente, Cotacao, CotacaoItem, Oportunidade, Produto, SnapshotEmissao  # noqa: E402
from app.pricing_engine import calcular_por_margem, calcular_por_preco, com_comissao_fixa  # noqa: E402

linhas_cot = []
linhas_item = []
risco_envio = []
resumo = {"total": 0, "por_status": {}, "riscos": {}}
STATUS_CLIENTE = {"emitida", "enviada", "pedido", "fechada", "perdida"}   # já pode ter chegado a cliente


def classificar_item(cot, it, produto, regras, ctx):
    riscos = []
    # cenário do cabeçalho × cenário congelado no item
    if it.uf_destino_fiscal is None and it.status_fiscal is None:
        riscos.append("SNAPSHOT_INCOMPLETO")          # item legado sem fiscal por item
    else:
        if (ctx["uf_destino_fiscal"] or "") != (it.uf_destino_fiscal or ""):
            riscos.append("CENARIO_CABECALHO_DIVERGE_ITEM:destino")
        if (ctx["finalidade"] or "") != (it.finalidade or ""):
            riscos.append("CENARIO_CABECALHO_DIVERGE_ITEM:finalidade")
        if ctx["status_fiscal"] == "OK" and it.status_fiscal == "OK" and D(ctx["icms_pct"]) != D(it.icms_pct or 0):
            riscos.append("FISCAL_STALE")
        if ctx["status_fiscal"] != "OK" and it.status_fiscal == "OK":
            riscos.append("FISCAL_STALE:cenario_hoje_bloqueia")
        if ctx["status_pagamento"] == "OK" and it.status_pagamento == "OK" and D(ctx["encargo_pct"]) != D(it.encargo_pct or 0):
            riscos.append("PAGAMENTO_STALE")
    # preço/margem gravados × motor no cenário salvo, com o custo pinado
    rec_pinado = None
    if regras is not None and it.custo_unitario and it.margem_padrao_pct is not None:
        rec_pinado = calcular_por_margem(it.custo_unitario, 1, it.margem_padrao_pct, regras).preco_negociado
        if it.preco_recomendado is not None and dinheiro(D(it.preco_recomendado)) != rec_pinado:
            riscos.append("PRECO_STALE:recomendado")
        if it.modo_edicao == "margem" and it.valor_editado is not None and D(it.valor_editado) != D(it.margem_padrao_pct):
            riscos.append("POLITICA_STALE:margem_alvo_do_item≠valor_editado")
        # margem gravada × recomposta no preço negociado com a comissão gravada
        if it.preco_negociado and it.comissao_pct is not None:
            r = calcular_por_preco(it.custo_unitario, it.quantidade, it.preco_negociado,
                                   com_comissao_fixa(regras, it.comissao_pct))
            if abs(r.margem_liquida - D(it.margem_liquida or 0)) > Decimal("1e-6"):
                riscos.append("PRECO_STALE:margem_gravada≠recomposta")
    elif regras is None and (it.preco_negociado or 0) > 0 and it.status_fiscal == "OK":
        riscos.append("FISCAL_STALE:cenario_hoje_bloqueia_mas_item_tem_preco")
    # custo pinado × vigente
    if produto is not None:
        custo_hoje, mem = ps.custo_para_precificar(session, produto)
        if custo_hoje and it.custo_unitario and abs(D(custo_hoje) - D(it.custo_unitario)) > Decimal("0.005"):
            riscos.append("CNET_STALE")
        st = ps.status_canonico_do_custo(custo_hoje, mem) if custo_hoje else "A_COTAR"
        if st in ("A_COTAR", "REVIEW_REQUIRED") and (it.preco_negociado or 0) > 0:
            riscos.append(f"CUSTO_NAO_PUBLICAVEL:{st}")
    if it.politica_comercial is None and cot.criado_em and str(cot.criado_em) >= "2026-09-16":
        riscos.append("POLITICA_STALE:item_sem_politica_apos_16-09")
    return riscos, rec_pinado


for cot in session.exec(select(Cotacao).order_by(Cotacao.id)).all():
    cliente = session.get(Cliente, cot.cliente_id)
    itens = ws.itens_de(session, cot.id)
    fin, fonte_fin = ps.finalidade_da_operacao(session, cot)
    origem, fonte_orig = ps.uf_origem_fiscal(session, cot, None)
    total_salvo = sum(D(i.faturamento or 0) for i in itens)
    total_rec = Decimal(0); custo_tot = Decimal(0); lucro_salvo = Decimal(0)
    riscos_cot = set(); n_bloq = 0
    dado_cliente_ok = bool(cot.estado_destino) and cot.contribuinte_icms is not None and bool(fin) and "definida" in (fonte_fin or "") or "cadastro do cliente" in (fonte_fin or "")
    for it in itens:
        produto = session.get(Produto, it.produto_id) if it.produto_id else None
        regras, _r, ctx = (None, None, None)
        try:
            from app.routers.cotacoes import montar_regras
            regras, _r, ctx = montar_regras(cot, session, produto, item=it)
        except Exception as e:  # noqa: BLE001
            ctx = {"status_fiscal": "ERRO", "motivo_bloqueio": str(e), "uf_destino_fiscal": None, "finalidade": None, "icms_pct": None, "status_pagamento": None, "encargo_pct": None}
        riscos, rec = classificar_item(cot, it, produto, regras, ctx)
        if regras is None:
            n_bloq += 1
        riscos_cot.update(r.split(":")[0] for r in riscos)
        total_rec += dinheiro((rec or D(it.preco_negociado or 0)) * D(it.quantidade or 0))
        custo_tot += D(it.custo_total or 0); lucro_salvo += D(it.lucro or 0)
        linhas_item.append(dict(cotacao=cot.id, numero=cot.numero, item=it.id, produto=it.nome_produto, qtd=it.quantidade,
                                custo_pinado=it.custo_unitario, preco_negociado=it.preco_negociado, preco_recomendado_gravado=it.preco_recomendado,
                                preco_recomendado_recomposto=str(rec) if rec else "", margem_gravada=it.margem_liquida, modo=it.modo_edicao,
                                valor_editado=it.valor_editado, status_custo=it.status_custo_item, status_fiscal_item=it.status_fiscal,
                                status_fiscal_hoje=ctx.get("status_fiscal"), bloqueio_hoje=ctx.get("motivo_bloqueio", ""),
                                icms_item=it.icms_pct, icms_hoje=ctx.get("icms_pct"), riscos=";".join(riscos)))
    # snapshot
    snap = session.exec(select(SnapshotEmissao).where(SnapshotEmissao.cotacao_id == cot.id).order_by(SnapshotEmissao.id.desc())).first()
    snap_ok = ""
    if snap is not None:
        its = json.loads(snap.itens_json or "[]")
        tot_snap = sum(D(i.get("faturamento") or i.get("total_linha") or 0) for i in its)
        snap_ok = "OK" if tot_snap == total_salvo and len(its) == len(itens) else f"DIVERGE snapshot {tot_snap}/{len(its)} × itens {total_salvo}/{len(itens)}"
        if snap_ok != "OK":
            riscos_cot.add("SNAPSHOT_DIVERGE")
    op = session.get(Oportunidade, cot.oportunidade_id) if cot.oportunidade_id else None
    st = cot.status.value if hasattr(cot.status, "value") else str(cot.status)
    pode_ter_ido = st in STATUS_CLIENTE or cot.issued_em or cot.sent_em
    fiscal_confirmado = bool(cot.estado_destino) and bool(fin) and ("definida" in (fonte_fin or "") or "cadastro do cliente" in (fonte_fin or ""))
    selo = "DADO_FISCAL_CLIENTE_NÃO_CONFIRMADO" if not fiscal_confirmado else ("BLOQUEADA" if n_bloq else ("RISCO" if riscos_cot else "SAFE"))
    linha = dict(id=cot.id, numero=cot.numero, revisao=cot.revisao, cliente=cliente.nome if cliente else "", status=st,
                 arquivada=bool(cot.arquivada_em), issued=bool(cot.issued_em), sent=bool(cot.sent_em),
                 ganha=bool(op and op.cotacao_vencedora_id == cot.id), origem_fiscal=f"{origem} ({fonte_orig})",
                 destino=cot.estado_destino, contribuinte=cot.contribuinte_icms, finalidade=f"{fin} ({fonte_fin})",
                 pagamento=cot.condicao_pagamento, itens=len(itens), itens_bloqueados_hoje=n_bloq,
                 total_salvo=str(total_salvo), total_recomendado_cenario=str(total_rec), diferenca=str(total_salvo - total_rec),
                 margem_salva=str((lucro_salvo / total_salvo) if total_salvo else ""), snapshot=snap_ok,
                 riscos=";".join(sorted(riscos_cot)), selo=selo, pode_ter_ido_ao_cliente=bool(pode_ter_ido))
    linhas_cot.append(linha)
    resumo["por_status"][st] = resumo["por_status"].get(st, 0) + 1
    for r in riscos_cot:
        resumo["riscos"][r] = resumo["riscos"].get(r, 0) + 1
    if pode_ter_ido:
        risco_envio.append(dict(linha, exposicao_por_unidade="", exposicao_total=str(total_salvo - total_rec)))

resumo["total"] = len(linhas_cot)
os.makedirs(AUDIT, exist_ok=True)
with open(os.path.join(AUDIT, "cotacoes_auditoria.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(linhas_cot[0].keys())); w.writeheader(); w.writerows(linhas_cot)
with open(os.path.join(AUDIT, "cotacoes_auditoria_itens.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(linhas_item[0].keys())); w.writeheader(); w.writerows(linhas_item)
with open(os.path.join(AUDIT, "propostas_enviadas_risco.csv"), "w", newline="", encoding="utf-8") as f:
    if risco_envio:
        w = csv.DictWriter(f, fieldnames=list(risco_envio[0].keys())); w.writeheader(); w.writerows(risco_envio)
json.dump(resumo, open(os.path.join(AUDIT, "cotacoes_auditoria_resumo.json"), "w"), ensure_ascii=False, indent=2, default=str)
print(json.dumps(resumo, ensure_ascii=False, indent=1, default=str))
print("\n== cotações que podem ter ido ao cliente ==")
for l in risco_envio:
    print(f"  {l['numero']} [{l['status']}] {l['destino']} contrib={l['contribuinte']} {l['finalidade'][:40]} pag={l['pagamento']} itens={l['itens']} total={l['total_salvo']} rec={l['total_recomendado_cenario']} dif={l['diferenca']} snap={l['snapshot'] or '-'} riscos={l['riscos'] or '-'} → {l['selo']}")
print("\n== todas ==")
for l in linhas_cot:
    print(f"  {l['numero']} [{l['status']}{' arq' if l['arquivada'] else ''}] {l['destino']} c={l['contribuinte']} {l['finalidade'][:28]} pag={l['pagamento']} it={l['itens']}/{l['itens_bloqueados_hoje']}bl total={l['total_salvo']} dif={l['diferenca']} riscos={l['riscos'] or '-'} → {l['selo']}")

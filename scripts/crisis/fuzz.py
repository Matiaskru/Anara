#!/usr/bin/env python3
"""§28 — Fuzz/property testing reprodutível (seed fixa) sobre o motor comercial inteiro.

10.000 combinações de produto × quantidade × destino × contribuinte × finalidade × pagamento ×
desconto (dentro e fora da autonomia) × personalizado dentro dos limites. Cada caso passa pelo
caminho real (cotação → `adicionar_item` → `aplicar_negociacao` → `avaliar`), e um oracle
independente confere invariantes econômicos. Falhas mínimas vão para `fuzz_failures.json`.
"""
import json
import os
import random
import sys
import time
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, RequestFalsa, chamar, preparar, usuario_admin  # noqa: E402

SEED = int(os.environ.get("FUZZ_SEED", "20260917"))
N = int(os.environ.get("FUZZ_N", "10000"))
session = preparar("fuzz")
rnd = random.Random(SEED)

from sqlmodel import select  # noqa: E402

from app import calculadora as calc  # noqa: E402
from app import comercial_service as com  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app import workflow as wf  # noqa: E402
from app import workflow_service as ws  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.models import Cliente, Cotacao, CotacaoItem, EstadoFiscal, Fornecedor, MaterialPreco, Produto  # noqa: E402
from app.routers.cotacoes import adicionar_item, montar_regras  # noqa: E402

ADMIN = usuario_admin(); REQ = RequestFalsa(ADMIN)
C = lambda x: Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
UFS = [e.estado for e in session.exec(select(EstadoFiscal)).all() if e.ativo]
FINS = ["REVENDA", "INDUSTRIALIZACAO", "USO_CONSUMO", "ATIVO_IMOBILIZADO"]
CONDS = ["30", "30/60", "30/60/90", "30/60/90/120", "30/60/90/120/150", "À VISTA", "CARTAO"]
cliente = session.exec(select(Cliente)).first()
materiais = [m.id for m in session.exec(select(MaterialPreco)).all() if m.ativo]

produtos = []
for p in session.exec(select(Produto).where(Produto.ativo == True)).all():  # noqa: E712
    custo, mem = ps.custo_para_precificar(session, p)
    produtos.append((p, custo, ps.status_canonico_do_custo(custo, mem)))
publicaveis = [x for x in produtos if x[2] in ("CONFIRMADO", "ESTIMADO", "REVALIDAR") and x[1]]
nao_publicaveis = [x for x in produtos if x not in publicaveis]
print(f"produtos: {len(produtos)} · publicáveis {len(publicaveis)} · não {len(nao_publicaveis)}")

# alguns personalizados dentro dos limites, gravados no catálogo (cópia)
personalizados = []
for fam, l, c in [("Flat Sheet", 160, 240), ("Flat Sheet", 300, 450), ("Top Sheet", 190, 250), ("Duvet Cover", 240, 260), ("Pillow Case", 50, 70)]:
    try:
        prod = calc.salvar_no_catalogo(session, fam, l, c, material_id=rnd.choice(materiais))
        session.commit()
        custo, mem = ps.custo_para_precificar(session, prod)
        if custo:
            personalizados.append((prod, custo, ps.status_canonico_do_custo(custo, mem)))
    except Exception as e:  # noqa: BLE001
        print("  personalizado falhou:", fam, l, c, e)
for fam, l, c, g in [("Bath Towel", 70, 140, 450), ("Bath Towel", 100, 150, 550), ("Hand Towel", 50, 80, 450), ("Bath Mat", 50, 80, 750)]:
    try:
        prod = calc.salvar_no_catalogo(session, fam, l, c, gsm=g)
        session.commit()
        custo, mem = ps.custo_para_precificar(session, prod)
        if custo:
            personalizados.append((prod, custo, ps.status_canonico_do_custo(custo, mem)))
    except Exception as e:  # noqa: BLE001
        print("  personalizado toalha falhou:", fam, e)
print(f"personalizados calculáveis: {len(personalizados)}")

falhas = []
stats = {"casos": 0, "itens": 0, "bloqueados": 0, "com_excecao": 0, "travado_recusado": 0}
t0 = time.time()
cot = None
por_cotacao = 0


def nova_cotacao():
    uf = rnd.choice(UFS); contrib = rnd.random() < 0.5; fin = rnd.choice(FINS); cond = rnd.choice(CONDS)
    frete = rnd.choice(["FOB", "CIF", "A_COMBINAR"])
    c = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino=uf, contribuinte_icms=contrib,
                finalidade=fin, condicao_pagamento=cond, status="rascunho", freight_type=frete,
                freight_valor=(float(C(Decimal(rnd.uniform(50, 2000)))) if frete == "CIF" and rnd.random() < 0.5 else None),
                numero=f"FUZZ-{stats['casos']}")
    session.add(c); session.commit(); session.refresh(c)
    return c


def falha(tipo, **d):
    falhas.append({"tipo": tipo, "seed": SEED, "caso": stats["casos"], **{k: str(v) for k, v in d.items()}})


while stats["casos"] < N:
    if cot is None or por_cotacao >= rnd.choice([1, 2, 3, 5, 10]):
        cot = nova_cotacao(); por_cotacao = 0
    stats["casos"] += 1; por_cotacao += 1
    universo = personalizados if (personalizados and rnd.random() < 0.15) else (nao_publicaveis if rnd.random() < 0.1 else publicaveis)
    p, custo, status = rnd.choice(universo)
    qtd = rnd.choice([1, 2, 3, 7, 10, 25, 99, 100, 500, 1000, rnd.randint(1, 3000)])
    try:
        r = chamar(adicionar_item, REQ, cotacao_id=cot.id, produto_id=p.id, quantidade=float(qtd), modo="margem", valor=None, session=session)
        session.commit()
    except Exception as e:  # noqa: BLE001
        session.rollback(); falha("EXCECAO_ADICIONAR", produto=p.id, cotacao=cot.id, erro=f"{type(e).__name__}: {str(e)[:120]}"); continue
    it = ws.itens_de(session, cot.id)[-1]
    stats["itens"] += 1
    c = session.get(Cotacao, cot.id)
    regras, _r, ctx = montar_regras(c, session, p, item=it)
    # --- invariantes do item recém-formado -----------------------------------------------
    if regras is None:
        stats["bloqueados"] += 1
        if (it.preco_negociado or 0) > 0 and status in ("CONFIRMADO", "ESTIMADO", "REVALIDAR"):
            falha("CENARIO_BLOQUEADO_COM_PRECO", produto=p.id, cotacao=cot.id, preco=it.preco_negociado, motivo=ctx.get("motivo_bloqueio"))
        if "REVIEW_REQUIRED" not in (it.status_fiscal or "", it.status_pagamento or ""):
            falha("BLOQUEIO_NAO_GRAVADO", produto=p.id, cotacao=cot.id)
    else:
        if status in ("CONFIRMADO", "ESTIMADO", "REVALIDAR"):
            icms, pc, enc = regras.icms_pct, regras.pis_cofins_pct, regras.encargo_financeiro_pct
            cform = D(it.comissao_formacao_pct) if it.comissao_formacao_pct is not None else None
            if cform is not None:
                rec = (D(custo) / (1 - icms - pc - enc - cform - D(it.margem_padrao_pct))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if dinheiro(D(it.preco_negociado)) != rec or dinheiro(D(it.preco_recomendado)) != rec:
                    falha("PRECO_INICIAL_DIVERGE_ORACLE", produto=p.id, cotacao=cot.id, preco=it.preco_negociado, rec=it.preco_recomendado, oracle=rec)
                if D(it.faturamento) != C(D(it.preco_negociado) * D(qtd)):
                    falha("LINHA_NAO_E_UNIT_X_QTD", produto=p.id, cotacao=cot.id)
                if D(it.margem_liquida) < 0:
                    falha("MARGEM_NEGATIVA_NO_RECOMENDADO", produto=p.id, cotacao=cot.id, margem=it.margem_liquida)
                if not (D(it.custo_unitario) > 0 and D(it.preco_negociado) > D(it.custo_unitario)):
                    falha("PRECO_NAO_COBRE_CUSTO", produto=p.id, cotacao=cot.id)
            if it.status_custo_item != status:
                falha("STATUS_CUSTO_ITEM_DIVERGE", produto=p.id, cotacao=cot.id, item=it.status_custo_item, esperado=status)
        else:
            if it.status_custo_item not in ("A_COTAR", "REVIEW_REQUIRED"):
                falha("NAO_PUBLICAVEL_SEM_BLOQUEIO", produto=p.id, cotacao=cot.id, status=it.status_custo_item)
    # --- negociação: desconto dentro/fora da autonomia --------------------------------------
    if regras is not None and it.preco_negociado and not it.preco_travado and rnd.random() < 0.7:
        desc = rnd.choice([0, 0.01, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5, -0.1])
        novo = float(C(D(it.preco_negociado) * (1 - Decimal(str(desc)))))
        if novo <= 0:
            continue
        try:
            av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: novo}, ator=ADMIN); session.commit()
        except com.PrecoTravado:
            stats["travado_recusado"] += 1; continue
        except Exception as e:  # noqa: BLE001
            session.rollback(); falha("EXCECAO_NEGOCIAR", produto=p.id, cotacao=cot.id, erro=f"{type(e).__name__}: {str(e)[:120]}"); continue
        a = next(x for x in av.itens if x.item.id == it.id)
        if a.resultado is not None:
            if not a.resultado.reconcilia():
                falha("LINHA_NAO_RECONCILIA", produto=p.id, cotacao=cot.id)
            piso = D(it.piso_margem_pct) if it.piso_margem_pct is not None else None
            if piso is not None and a.linha.politica is not None:
                # abaixo do piso (além da tolerância de centavos) ⇒ exceção ⇒ requer aprovação
                deficit = (piso - a.resultado.margem_liquida) * D(novo)
                if deficit > Decimal("0.02") and not av.requer_aprovacao:
                    falha("ABAIXO_DO_PISO_SEM_APROVACAO", produto=p.id, cotacao=cot.id, margem=a.resultado.margem_liquida, piso=piso, desc=desc)
                if a.resultado.margem_liquida >= piso and a.viola_piso:
                    falha("VIOLA_PISO_FALSO", produto=p.id, cotacao=cot.id, margem=a.resultado.margem_liquida, piso=piso)
            if av.comissao is not None:
                cv = av.comissao.variavel_pct
                if not (Decimal("0.05") <= cv <= Decimal("0.10")):
                    falha("COMISSAO_FORA_DA_FAIXA", produto=p.id, cotacao=cot.id, comissao=cv)
                if desc < 0 and av.comissao.desconto_ratio != 0 and av.comissao.negociado_variavel >= av.comissao.recomendado_variavel:
                    falha("ACIMA_DO_RECOMENDADO_CONTA_COMO_DESCONTO", produto=p.id, cotacao=cot.id)
            if av.requer_aprovacao:
                stats["com_excecao"] += 1
        # total = Σ linhas (+ frete CIF com valor)
        its = ws.itens_de(session, cot.id)
        soma = sum(D(i.faturamento) for i in its)
        c = session.get(Cotacao, cot.id)
        frete = C(D(c.freight_valor)) if (c.freight_type == "CIF" and c.freight_valor) else Decimal(0)
        if av.subtotal_negociado != soma or av.total_proposta != soma + frete:
            falha("TOTAL_NAO_E_SOMA", produto=p.id, cotacao=cot.id, subtotal=av.subtotal_negociado, soma=soma, total=av.total_proposta, frete=frete)
    # --- emissão: blockers duros nunca passam ------------------------------------------------
    if rnd.random() < 0.05:
        c = session.get(Cotacao, cot.id)
        pront = ws.avaliar(session, c)
        its = ws.itens_de(session, c.id)
        duros = [i for i in its if (i.status_custo_item in ("A_COTAR", "REVIEW_REQUIRED")) or (i.preco_negociado or 0) <= 0 or "REVIEW_REQUIRED" in (i.status_fiscal or "", i.status_pagamento or "")]
        if duros and pront.pode_emitir:
            falha("EMITE_COM_BLOCKER_DURO", cotacao=c.id, itens=[i.id for i in duros])
        if pront.pode_emitir:
            try:
                ws.emitir(session, c, ator=ADMIN); session.commit()
            except Exception as e:  # noqa: BLE001
                session.rollback(); falha("EMISSAO_FALHOU_APESAR_DE_PRONTA", cotacao=c.id, erro=f"{type(e).__name__}: {str(e)[:120]}")
            cot = None
    if stats["casos"] % 1000 == 0:
        print(f"  {stats['casos']} casos · {len(falhas)} falhas · {time.time()-t0:.0f}s")

print(json.dumps(stats, ensure_ascii=False))
print(f"falhas: {len(falhas)} em {time.time()-t0:.0f}s (seed {SEED})")
from collections import Counter
print(Counter(f["tipo"] for f in falhas))
json.dump({"seed": SEED, "n": N, "stats": stats, "falhas": falhas[:500]}, open(os.path.join(AUDIT, "fuzz_failures.json"), "w"), ensure_ascii=False, indent=1)
sys.exit(1 if falhas else 0)

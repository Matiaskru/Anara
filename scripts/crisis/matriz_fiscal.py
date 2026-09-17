#!/usr/bin/env python3
"""§11/§12 — Matriz fiscal exaustiva + scanner de invariantes, com oracle independente.

Para todo produto PUBLICÁVEL (custo canônico CONFIRMADO/ESTIMADO/REVALIDAR e custo > 0):
27 UFs × contribuinte {sim, não} × 4 finalidades × condições de pagamento ativas.
Origem fiscal resolvida pelo runtime (`uf_origem_fiscal` da cotação em branco → fornecedor →
premissa `fiscal_uf_origem_padrao`).

Motor OFICIAL: `fiscal_rules.resolver_fiscal_item` + `payment_terms.resolver_encargo` +
`pricing_service` (PIS/COFINS efetivo) + `pricing_engine.calcular_por_margem` — chamados como
funções puras sobre as linhas já carregadas (é o que `regras_da_cotacao` faz por dentro; a
equivalência é conferida numa amostra chamando `regras_da_cotacao` de verdade).

ORACLE independente (só `decimal`, lendo as MESMAS tabelas): regra explícita → intra → inter
contribuinte (revenda: interestadual; consumo: interestadual, DIFAL do destinatário) → inter
não contribuinte (interestadual + (base interna − interestadual) + FCP; base ou FCP
desconhecidos BLOQUEIAM). PIS/COFINS efetivo = nominal × (1 − (icms − fcp)).
preço = CNET / (1 − icms − pc − encargo − comissão_formação − margem_alvo), ROUND_HALF_UP.

Saída: `cenario_fiscal_exaustivo.csv` (fora do repo) + `matriz_fiscal_violacoes.csv` + resumo.
"""
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal, getcontext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, preparar  # noqa: E402

session = preparar("matriz")
getcontext().prec = 34

from sqlmodel import select  # noqa: E402

from app import config_service as cfg  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.fiscal_rules import resolver_fiscal_item  # noqa: E402
from app.models import (AliquotaInterestadual, CondicaoPagamento, Cotacao, EstadoFiscal, Fornecedor,  # noqa: E402
                        Produto, RegraFcp, RegraFiscalVenda)
from app.payment_terms import resolver_encargo  # noqa: E402
from app.pricing_engine import (TaxRuleSet, calcular_por_margem, icms_excluido_da_base,  # noqa: E402
                                pis_cofins_efetivo)

FINALIDADES = ["REVENDA", "INDUSTRIALIZACAO", "USO_CONSUMO", "ATIVO_IMOBILIZADO"]
CONSUMIDOR_FINAL = {"USO_CONSUMO", "ATIVO_IMOBILIZADO"}
PUBLICAVEIS = {"CONFIRMADO", "ESTIMADO", "REVALIDAR"}

estados = session.exec(select(EstadoFiscal)).all()
explicitas = session.exec(select(RegraFiscalVenda)).all()
aliquotas = session.exec(select(AliquotaInterestadual)).all()
fcps = session.exec(select(RegraFcp)).all()
condicoes = session.exec(select(CondicaoPagamento)).all()
fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
pis_nominal = D(cfg.num(session, "pis_cofins_nominal_pct", 0.0925))
origem_padrao = cfg.txt(session, "fiscal_uf_origem_padrao")
UFS = sorted(e.uf for e in estados if e.ativo)
CONDICOES = sorted({c.codigo for c in condicoes if c.ativo})
por_uf = {e.uf: e for e in estados}


# ---------------------------------------------------------------------------
# ORACLE independente
# ---------------------------------------------------------------------------
def oracle_fiscal(uf_o, uf_d, natureza, contribuinte, finalidade, ncm=None, familia=None):
    """→ dict(status, icms, fcp, difal, resp, motivo) — só tabelas + Decimal."""
    if not uf_o or not uf_d or contribuinte is None or not finalidade or not natureza:
        return {"status": "REVIEW_REQUIRED", "motivo": "variável obrigatória ausente"}
    cf = finalidade in CONSUMIDOR_FINAL
    dest = por_uf.get(uf_d)
    if dest is None or not dest.ativo:
        return {"status": "REVIEW_REQUIRED", "motivo": "UF fora da tabela"}
    nome = {e.uf: e.estado for e in estados}
    exp = [r for r in explicitas if r.ativo and (r.origem == uf_o or r.origem == nome.get(uf_o))
           and (r.destino == uf_d or r.destino == nome.get(uf_d)) and bool(r.contribuinte) == bool(contribuinte)]
    if exp:
        r = sorted(exp, key=lambda r: (r.prioridade, r.id))[0]
        return {"status": "OK", "icms": D(r.icms_venda), "fcp": Decimal(0), "difal": None, "resp": "NAO_APLICAVEL"}
    if uf_o == uf_d:
        return {"status": "OK", "icms": D(dest.aliquota_interna), "fcp": Decimal(0), "difal": None, "resp": "NAO_APLICAVEL"}
    linhas = [a for a in aliquotas if a.ativo and a.uf_origem == uf_o and a.uf_destino == uf_d and a.origem_fiscal == natureza]
    esp = [a for a in linhas if (a.produto_id and a.produto_id == -1) or (a.ncm and ncm and a.ncm.strip() == ncm.strip())]
    gerais = [a for a in linhas if not a.produto_id and not a.ncm]
    escolhidas = esp or gerais
    if not escolhidas:
        return {"status": "REVIEW_REQUIRED", "motivo": f"sem alíquota {uf_o}→{uf_d} {natureza}"}
    inter = D(sorted(escolhidas, key=lambda a: (a.prioridade, a.id))[0].aliquota)
    base = D(dest.icms_interno_base) if dest.icms_interno_base is not None else (
        D(dest.aliquota_interna) if dest.interna_inclui_fcp is False else None)
    regras_fcp = [r for r in fcps if r.ativo and r.uf_destino == uf_d and (
        (r.produto_id and r.produto_id == -1) or (r.ncm and ncm and r.ncm.strip() == ncm.strip())
        or (r.familia and familia and r.familia.strip().lower() == familia.strip().lower())
        or (not r.produto_id and not r.ncm and not r.familia))]
    fcp = None
    if regras_fcp:
        r = sorted(regras_fcp, key=lambda r: (r.prioridade, r.id))[0]
        sit = (r.situacao or "DESCONHECIDO").upper()
        fcp = Decimal(0) if sit == "NAO_APLICA" else (D(r.fcp_pct) if sit == "APLICA" else None)
    difal = (base - inter) if base is not None and base > inter else (Decimal(0) if base is not None else None)
    if contribuinte and not cf:
        return {"status": "OK", "icms": inter, "fcp": Decimal(0), "difal": None, "resp": "NAO_APLICAVEL"}
    if contribuinte:
        return {"status": "OK", "icms": inter, "fcp": Decimal(0), "difal": difal, "resp": "DESTINATARIO"}
    if base is None:
        return {"status": "REVIEW_REQUIRED", "motivo": f"base interna de {uf_d} indeterminada"}
    if fcp is None:
        return {"status": "REVIEW_REQUIRED", "motivo": f"FCP de {uf_d} desconhecido"}
    return {"status": "OK", "icms": inter + difal + fcp, "fcp": fcp, "difal": difal, "resp": "REMETENTE"}


def oracle_encargo(codigo):
    cands = [c for c in condicoes if c.ativo and (c.codigo or "").strip().lower() == codigo.strip().lower()]
    if not cands:
        return None, "condição inexistente"
    c = sorted(cands, key=lambda c: (c.valid_from is not None, c.valid_from or __import__("datetime").date.min, c.id))[-1]
    if c.encargo_pct is None:
        return None, "encargo não confirmado"
    return D(c.encargo_pct), None


def oracle_preco(cnet, icms, fcp, encargo, comissao, margem):
    pc = pis_nominal * (Decimal(1) - max(icms - fcp, Decimal(0)))
    denom = Decimal(1) - icms - pc - encargo - comissao - margem
    if denom <= 0:
        return None, pc, denom
    preco = (cnet / denom).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return preco, pc, denom


def oracle_margem_realizada(preco, cnet, icms, pc, encargo, comissao):
    impostos = (preco * (icms + pc + encargo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    com = (preco * comissao).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    lucro = preco - impostos - com - cnet.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return lucro / preco


# ---------------------------------------------------------------------------
# Produtos publicáveis
# ---------------------------------------------------------------------------
t0 = time.time()
produtos = []
for p in session.exec(select(Produto).where(Produto.ativo == True)).all():  # noqa: E712
    custo, mem = ps.custo_para_precificar(session, p)
    status = ps.status_canonico_do_custo(custo, mem)
    if status in PUBLICAVEIS and custo and custo > 0:
        m = ps.margem_padrao(session, p)
        natureza, _f = ps.origem_fiscal_do_produto(session, p)
        forn = fornecedores.get(p.fornecedor_id)
        produtos.append({"p": p, "custo": D(custo), "status": status, "margem": m, "natureza": natureza,
                         "fornecedor": forn.codigo if forn else None})
print(f"produtos publicáveis: {len(produtos)} (de {session.exec(select(Produto)).all().__len__()}) em {time.time()-t0:.1f}s")
print(" por fornecedor:", Counter(x["fornecedor"] for x in produtos))
print(" por status:", Counter(x["status"] for x in produtos))

# equivalência do caminho puro com regras_da_cotacao numa amostra (prova de que a matriz
# reproduz o runtime)
amostra = produtos[:3] + produtos[-3:]
for x in amostra:
    for uf, contrib, fin, cond in [("SP", False, "USO_CONSUMO", "30"), ("RJ", False, "USO_CONSUMO", "30/60"),
                                    ("MG", True, "REVENDA", "30/60/90"), ("BA", True, "USO_CONSUMO", "À VISTA")]:
        cot = Cotacao(cliente_id=1, estado_destino=uf, contribuinte_icms=contrib, finalidade=fin,
                      condicao_pagamento=cond, freight_type="FOB")
        regras, ctx = ps.regras_da_cotacao(session, cot, x["p"])
        fiscal = resolver_fiscal_item(explicitas, estados, aliquotas, uf_origem=origem_padrao, uf_destino=uf,
                                      origem_fiscal=x["natureza"], contribuinte=contrib, finalidade=fin,
                                      ncm=x["p"].ncm, produto_id=x["p"].id, familia=x["p"].familia, regras_fcp=fcps)
        assert fiscal.status == ctx["status_fiscal"], (x["p"].sku_key, uf, fiscal.status, ctx["status_fiscal"])
        if regras is not None:
            assert regras.icms_pct == fiscal.icms_pct
            enc = resolver_encargo(condicoes, cond)
            assert regras.encargo_financeiro_pct == enc.pct
print(" equivalência caminho puro × regras_da_cotacao: ok na amostra")

# ---------------------------------------------------------------------------
# Matriz
# ---------------------------------------------------------------------------
cache_fiscal = {}
cache_oracle = {}
cache_enc = {}
CAMPOS = ["produto_id", "sku", "fornecedor", "familia", "status_custo", "origem", "destino", "natureza",
          "contribuinte", "finalidade", "pagamento", "icms_proprio", "aliquota_interna", "difal", "difal_resp",
          "fcp", "pis_cofins", "encargo", "comissao", "margem_alvo", "cnet", "preco", "margem_realizada",
          "status", "bloqueio", "oracle_status", "oracle_icms", "oracle_preco", "diverge"]
saida = os.path.join(AUDIT, "cenario_fiscal_exaustivo.csv")
viol = []
n = 0
divergencias = 0
bloqueados = 0
por_bloqueio = Counter()
precos_por_produto = defaultdict(dict)   # (uf, contrib, fin, cond) → (icms_total, encargo, preco)
with open(saida, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(CAMPOS)
    for x in produtos:
        p, custo, m = x["p"], x["custo"], x["margem"]
        comissao = D(m.comissao_formacao_pct) if m.tem_politica else None
        for uf in UFS:
            for contrib in (True, False):
                for fin in FINALIDADES:
                    chave = (x["natureza"], uf, contrib, fin, p.ncm, p.familia)
                    if chave not in cache_fiscal:
                        cache_fiscal[chave] = resolver_fiscal_item(
                            explicitas, estados, aliquotas, uf_origem=origem_padrao, uf_destino=uf,
                            origem_fiscal=x["natureza"], contribuinte=contrib, finalidade=fin,
                            ncm=p.ncm, produto_id=None, familia=p.familia, regras_fcp=fcps)
                        cache_oracle[chave] = oracle_fiscal(origem_padrao, uf, x["natureza"], contrib, fin, p.ncm, p.familia)
                    fiscal = cache_fiscal[chave]
                    orc = cache_oracle[chave]
                    for cond in CONDICOES:
                        n += 1
                        if cond not in cache_enc:
                            cache_enc[cond] = (resolver_encargo(condicoes, cond), oracle_encargo(cond))
                        enc, (orc_enc, orc_enc_motivo) = cache_enc[cond]
                        bloqueio = fiscal.motivo if fiscal.bloqueado else (enc.motivo if enc.bloqueado else "")
                        linha = dict.fromkeys(CAMPOS, "")
                        linha.update(produto_id=p.id, sku=p.sku_key, fornecedor=x["fornecedor"], familia=p.familia,
                                     status_custo=x["status"], origem=origem_padrao, destino=uf, natureza=x["natureza"],
                                     contribuinte=int(contrib), finalidade=fin, pagamento=cond, cnet=str(custo),
                                     margem_alvo=str(D(m.margem_pct)), comissao=str(comissao) if comissao is not None else "faixa",
                                     oracle_status=orc["status"])
                        if fiscal.bloqueado or enc.bloqueado:
                            bloqueados += 1
                            por_bloqueio[(bloqueio or "")[:60]] += 1
                            linha.update(status="REVIEW_REQUIRED", bloqueio=bloqueio)
                            # invariante 6: cenário não confirmado NÃO devolve preço
                            if orc["status"] == "OK" and orc_enc is not None:
                                viol.append(("ORACLE_RESOLVE_MOTOR_BLOQUEIA", p.id, uf, contrib, fin, cond, bloqueio))
                            w.writerow([linha[c] for c in CAMPOS]); continue
                        if orc["status"] != "OK" or orc_enc is None:
                            viol.append(("MOTOR_RESOLVE_ORACLE_BLOQUEIA", p.id, uf, contrib, fin, cond, orc.get("motivo") or orc_enc_motivo))
                            divergencias += 1
                        icms = fiscal.icms_pct
                        fcp = fiscal.fcp_pct or Decimal(0)
                        excl = icms_excluido_da_base(icms, fcp)
                        pc = pis_cofins_efetivo(pis_nominal, excl)
                        tabela = [(Decimal(0), comissao)] if comissao is not None else cfg.tabela_comissao(session)
                        regras = TaxRuleSet(icms_pct=icms, pis_cofins_pct=pc, encargo_financeiro_pct=enc.pct,
                                            comissao_tabela=tabela, origem_uf=origem_padrao)
                        res = calcular_por_margem(custo, 1, D(m.margem_pct), regras)
                        preco = res.preco_negociado
                        com_aplicada = regras.comissao_para_markup(res.markup_implicito)
                        # oracle
                        o_preco, o_pc, o_denom = (None, None, None)
                        if orc["status"] == "OK" and orc_enc is not None and comissao is not None:
                            o_preco, o_pc, o_denom = oracle_preco(custo, orc["icms"], orc["fcp"], orc_enc, comissao, D(m.margem_pct))
                        diverge = ""
                        if orc["status"] == "OK" and orc["icms"] != icms:
                            diverge = f"icms motor {icms} × oracle {orc['icms']}"
                        elif o_preco is not None and o_preco != preco:
                            diverge = f"preco motor {preco} × oracle {o_preco}"
                        elif orc.get("resp") and orc["resp"] != fiscal.difal_responsavel:
                            diverge = f"difal_resp motor {fiscal.difal_responsavel} × oracle {orc['resp']}"
                        if diverge:
                            divergencias += 1
                            viol.append(("DIVERGE_ORACLE", p.id, uf, contrib, fin, cond, diverge))
                        # invariantes numéricos
                        margem_real = res.margem_liquida
                        if preco is None or preco <= 0:
                            viol.append(("PRECO_NAO_POSITIVO", p.id, uf, contrib, fin, cond, str(preco)))
                        if margem_real < 0:
                            viol.append(("MARGEM_NEGATIVA", p.id, uf, contrib, fin, cond, str(margem_real)))
                        if res.faturamento != res.custo_total + res.impostos + res.comissao + res.lucro:
                            viol.append(("LINHA_NAO_RECONCILIA", p.id, uf, contrib, fin, cond, ""))
                        # preço, impostos, comissão e custo total são quantizados (4 × meio
                        # centavo) — é a mesma régua de `workflow.tolerancia_de_arredondamento`
                        tol = Decimal("0.02") / preco if preco else Decimal(1)
                        if abs(margem_real - D(m.margem_pct)) > tol:
                            viol.append(("MARGEM_REALIZADA_LONGE_DO_ALVO", p.id, uf, contrib, fin, cond, f"{margem_real} vs {m.margem_pct}"))
                        if str(preco) in ("NaN", "Infinity") or str(margem_real) in ("NaN", "Infinity"):
                            viol.append(("NAN_INF", p.id, uf, contrib, fin, cond, ""))
                        if uf == origem_padrao and fiscal.difal_pct not in (None, Decimal(0)):
                            viol.append(("DIFAL_INTRAESTADUAL", p.id, uf, contrib, fin, cond, str(fiscal.difal_pct)))
                        if contrib and fin in ("REVENDA", "INDUSTRIALIZACAO") and fiscal.difal_responsavel != "NAO_APLICAVEL":
                            viol.append(("DIFAL_EM_REVENDA_CONTRIBUINTE", p.id, uf, contrib, fin, cond, fiscal.difal_responsavel))
                        if not contrib and uf != origem_padrao and fiscal.difal_responsavel != "REMETENTE":
                            viol.append(("NAO_CONTRIBUINTE_SEM_DIFAL_REMETENTE", p.id, uf, contrib, fin, cond, fiscal.difal_responsavel))
                        if contrib and fin in CONSUMIDOR_FINAL and uf != origem_padrao and fiscal.difal_entra_na_margem:
                            viol.append(("DIFAL_DESTINATARIO_REDUZ_MARGEM", p.id, uf, contrib, fin, cond, ""))
                        precos_por_produto[p.id][(uf, contrib, fin, cond)] = (icms, enc.pct, preco, x["natureza"])
                        linha.update(icms_proprio=str(fiscal.aliquota_interestadual or icms), aliquota_interna=str(fiscal.aliquota_interna_destino or ""),
                                     difal=str(fiscal.difal_pct or ""), difal_resp=fiscal.difal_responsavel, fcp=str(fcp),
                                     pis_cofins=str(pc), encargo=str(enc.pct), comissao=str(com_aplicada),
                                     preco=str(preco), margem_realizada=str(margem_real), status="OK",
                                     oracle_icms=str(orc.get("icms", "")), oracle_preco=str(o_preco or ""), diverge=diverge)
                        w.writerow([linha[c] for c in CAMPOS])

# invariantes 8 e 9: monotonicidade por produto (tudo o mais constante)
mono = 0
for pid, cen in precos_por_produto.items():
    # 9: condição mais onerosa não reduz o preço (mesma UF/contrib/fin)
    grupos = defaultdict(list)
    for (uf, c, fin, cond), (icms, enc, preco, nat) in cen.items():
        grupos[(uf, c, fin)].append((enc, preco, cond))
    for k, lst in grupos.items():
        lst.sort()
        for (e1, p1, c1), (e2, p2, c2) in zip(lst, lst[1:]):
            if e2 > e1 and p2 < p1:
                mono += 1; viol.append(("PAGAMENTO_MAIS_ONEROSO_REDUZ_PRECO", pid, k[0], k[1], k[2], f"{c1}→{c2}", f"{p1}→{p2}"))
    # 8: maior carga tributária (mesma condição) não reduz o preço
    grupos = defaultdict(list)
    for (uf, c, fin, cond), (icms, enc, preco, nat) in cen.items():
        grupos[cond].append((icms, preco, uf, c, fin))
    for cond, lst in grupos.items():
        lst.sort()
        for (i1, p1, *a), (i2, p2, *b) in zip(lst, lst[1:]):
            if i2 > i1 and p2 < p1:
                mono += 1; viol.append(("MAIOR_CARGA_REDUZ_PRECO", pid, f"{a}→{b}", "", "", cond, f"icms {i1}→{i2} preco {p1}→{p2}"))

print(f"\ncenários: {n} · bloqueados: {bloqueados} ({bloqueados/n:.1%}) · divergências oracle: {divergencias} · violações: {len(viol)} em {time.time()-t0:.0f}s")
print("motivos de bloqueio:")
for k, v in por_bloqueio.most_common(12):
    print(f"   {v:7d}  {k}")
print("violações por tipo:", Counter(v[0] for v in viol))
with open(os.path.join(AUDIT, "matriz_fiscal_violacoes.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["tipo", "produto_id", "destino", "contribuinte", "finalidade", "pagamento", "detalhe"])
    for v in viol:
        w.writerow(v)
resumo = {"cenarios": n, "produtos": len(produtos), "bloqueados": bloqueados, "divergencias_oracle": divergencias,
          "violacoes": len(viol), "por_tipo": dict(Counter(v[0] for v in viol)),
          "bloqueios": {k: v for k, v in por_bloqueio.most_common(20)},
          "ufs_nao_contribuinte_ok": sorted({v[1] for v in []})}
# UFs seguras/bloqueadas por perfil
ok_por = defaultdict(set); bl_por = defaultdict(set)
for chave, fis in cache_fiscal.items():
    nat, uf, contrib, fin, _n, _f = chave
    (bl_por if fis.bloqueado else ok_por)[(nat, "contribuinte" if contrib else "nao_contribuinte", fin)].add(uf)
resumo["ufs_ok"] = {" | ".join(k): sorted(v) for k, v in ok_por.items()}
resumo["ufs_bloqueadas"] = {" | ".join(k): sorted(v) for k, v in bl_por.items()}
with open(os.path.join(AUDIT, "matriz_fiscal_resumo.json"), "w", encoding="utf-8") as f:
    json.dump(resumo, f, ensure_ascii=False, indent=2, default=str)
print("\nUFs OK por perfil:")
for k, v in sorted(resumo["ufs_ok"].items()):
    print(f"   {k}: {len(v)} UFs")
print("UFs bloqueadas por perfil:")
for k, v in sorted(resumo["ufs_bloqueadas"].items()):
    print(f"   {k}: {len(v)} UFs {v if len(v) < 8 else ''}")
print("saída:", saida)
sys.exit(1 if viol else 0)

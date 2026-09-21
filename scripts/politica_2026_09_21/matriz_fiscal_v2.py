#!/usr/bin/env python3
"""Matriz fiscal × B2B da política de 21/09/2026 contra o oracle independente.

    ANARA_DB_URL=sqlite:////caminho/copia_aplicada.db python3 scripts/politica_2026_09_21/matriz_fiscal_v2.py [--produtos 60]

Para uma amostra estratificada de produtos publicáveis (todas as famílias e fornecedores) ×
27 UFs × contribuinte × 4 finalidades × condições ativas × sinais (`--sinais`, padrão
0/30/50/100%): o motor (`regras_da_cotacao` + `preco_b2b`)
tem de reproduzir o oracle em ICMS, DIFAL, responsável, FCP, parcela dedutível da comissão,
PIS/COFINS efetivo, B2B e tabela; e respeitar invariantes (reconciliação ao centavo, B2B é o
primeiro centavo válido, carga maior não reduz preço, encargo maior não reduz preço, sinal
maior nunca aumenta encargo nem B2B, sinal 100% = encargo zero mesmo com saldo sem taxa, não
contribuinte resolve nas 27 UFs para família do escopo e bloqueia fora dele). Sinal válido
nunca é blocker; só a condição sem taxa (CARTÃO) bloqueia — e só com sinal < 100%.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21 import oracle_v2 as orc  # noqa: E402

from sqlmodel import Session, select  # noqa: E402

from app import pricing_service as ps  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import D  # noqa: E402
from app.fiscal_2026_09_21 import FAMILIAS_ESCOPO  # noqa: E402
from app.models import CondicaoPagamento, Cotacao, EstadoFiscal, Fornecedor, Produto  # noqa: E402
from app.pricing_engine import calcular_por_preco, preco_b2b, preco_de_tabela  # noqa: E402

FINS = ["REVENDA", "INDUSTRIALIZACAO", "USO_CONSUMO", "ATIVO_IMOBILIZADO"]
PUBLICAVEIS = {"CONFIRMADO", "ESTIMADO", "REVALIDAR"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--produtos", type=int, default=60)
    ap.add_argument("--sinais", default="0,0.3,0.5,1", help="frações de sinal a cruzar")
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "matriz_fiscal_v2_2026_09_21.json"))
    a = ap.parse_args()
    sinais = [Decimal(x.strip()) for x in a.sinais.split(",") if x.strip()]
    real = os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
    assert os.path.realpath(caminho_do_banco()) != real, "nunca contra o banco real"
    t0 = time.time()
    viol, n, bloqueados = [], 0, 0
    with Session(engine) as s:
        forns = {f.id: f for f in s.exec(select(Fornecedor)).all()}
        estados = {e.uf: e.estado for e in s.exec(select(EstadoFiscal)).all()}
        condicoes = [c for c in s.exec(select(CondicaoPagamento)).all() if c.ativo]
        pis_nominal = D(ps.cfg.num(s, "pis_cofins_nominal_pct"))
        # amostra estratificada: até 3 produtos publicáveis por (fornecedor, família)
        por_grupo = defaultdict(list)
        for p in s.exec(select(Produto).where(Produto.ativo == True)).all():  # noqa: E712
            # 22/09/2026: B2B/tabela pela BASE COMERCIAL; economia real pelo custo (I.I. 0%)
            custo, base, mem = ps.bases_de_preco(s, p)
            status = ps.status_canonico_do_custo(custo, mem)
            if status in PUBLICAVEIS and custo and custo > 0 and ps.margem_padrao(s, p).tem_regra:
                por_grupo[(forns[p.fornecedor_id].codigo, p.familia)].append((p, D(base), D(custo)))
        amostra = []
        for k in sorted(por_grupo):
            amostra.extend(por_grupo[k][:3])
        amostra = amostra[:a.produtos] if len(amostra) > a.produtos else amostra
        print(f"produtos na amostra: {len(amostra)} ({len(por_grupo)} grupos fornecedor×família)")
        precos = defaultdict(dict)
        for p, custo, custo_real in amostra:
            forn = forns[p.fornecedor_id]
            natureza, _ = ps.origem_fiscal_do_produto(s, p)
            m = ps.margem_padrao(s, p)
            no_escopo = p.familia in FAMILIAS_ESCOPO
            for uf, estado in estados.items():
                for contrib in (True, False):
                    for fin in FINS:
                        o = orc.fiscal(uf, natureza, contrib, fin, no_escopo)
                        for cond in condicoes:
                            for sinal in sinais:
                                n += 1
                                rot = f"{cond.codigo}+sinal{sinal}"
                                cot = Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino=estado,
                                              contribuinte_icms=contrib, finalidade=fin,
                                              condicao_pagamento=cond.codigo, freight_type="FOB",
                                              percentual_sinal=float(sinal))
                                regras, ctx = ps.regras_da_cotacao(s, cot, p)
                                enc = orc.encargo_efetivo(sinal, cond.encargo_pct)
                                bloqueado_oracle = o.get("bloqueado") or enc is None
                                if regras is None:
                                    bloqueados += 1
                                    if not bloqueado_oracle:
                                        viol.append(("MOTOR_BLOQUEIA_ORACLE_RESOLVE", p.id, uf, contrib, fin, rot, ctx.get("motivo_bloqueio")))
                                    continue
                                if bloqueado_oracle:
                                    viol.append(("MOTOR_RESOLVE_ORACLE_BLOQUEIA", p.id, uf, contrib, fin, rot, ""))
                                    continue
                                if D(ctx["percentual_sinal"]) != sinal or (
                                        sinal < 1 and D(ctx["encargo_saldo_pct"]) != D(cond.encargo_pct)):
                                    viol.append(("SINAL_DIVERGE", p.id, uf, contrib, fin, rot, f"{ctx['percentual_sinal']} {ctx['encargo_saldo_pct']}"))
                                pc = orc.pis_cofins(pis_nominal, o["icms"], o["fcp"])
                                if regras.icms_pct != o["icms"] or D(ctx["fcp_pct"] or 0) != o["fcp"] \
                                        or ctx["difal_responsavel"] != o["resp"] or regras.pis_cofins_pct != pc \
                                        or regras.encargo_financeiro_pct != enc \
                                        or regras.comissao_base_icms_pct != o["icms_ded"]:
                                    viol.append(("FISCAL_DIVERGE", p.id, uf, contrib, fin, rot,
                                                 f"motor icms={regras.icms_pct} fcp={ctx['fcp_pct']} resp={ctx['difal_responsavel']} ded={regras.comissao_base_icms_pct} × oracle {o}"))
                                    continue
                                if o["difal"] is not None and D(ctx["difal_pct"]) != o["difal"]:
                                    viol.append(("DIFAL_DIVERGE", p.id, uf, contrib, fin, rot, f"{ctx['difal_pct']} × {o['difal']}"))
                                r = preco_b2b(custo, m.margem_pct, regras)
                                ob = orc.b2b(custo, D(m.margem_pct), o["icms"], pc, enc, o["icms_ded"])
                                if ob is None or r.preco_negociado != ob:
                                    viol.append(("B2B_DIVERGE", p.id, uf, contrib, fin, rot, f"motor {r.preco_negociado} × oracle {ob}"))
                                    continue
                                ol = orc.linha(custo, ob, 1, o["icms"], pc, enc, Decimal("0.05"), o["icms_ded"])
                                if (r.impostos, r.comissao, r.lucro, r.base_comissionavel) != (ol["impostos"], ol["comissao"], ol["lucro"], ol["base"]):
                                    viol.append(("DECOMPOSICAO_DIVERGE", p.id, uf, contrib, fin, rot, ""))
                                if not r.reconcilia() or r.margem_liquida < D(m.margem_pct):
                                    viol.append(("B2B_INVALIDO", p.id, uf, contrib, fin, rot, str(r.margem_liquida)))
                                # economia REAL no B2B comercial: custo menor ou igual à base → margem ≥ alvo; e o
                                # oracle da linha com o custo real reproduz lucro/impostos/comissão do motor
                                real = calcular_por_preco(custo_real, 1, r.preco_negociado, regras)
                                olr = orc.linha(custo_real, ob, 1, o["icms"], pc, enc, Decimal("0.05"), o["icms_ded"])
                                if real.margem_liquida < D(m.margem_pct) or real.lucro < r.lucro or \
                                        (real.impostos, real.comissao, real.lucro) != (olr["impostos"], olr["comissao"], olr["lucro"]):
                                    viol.append(("ECONOMIA_REAL_DIVERGE", p.id, uf, contrib, fin, rot, f"real {real.margem_liquida} × base {r.margem_liquida}"))
                                ant = calcular_por_preco(custo, 1, r.preco_negociado - Decimal("0.01"), regras)
                                if r.preco_negociado > Decimal("0.01") and ant.margem_liquida >= D(m.margem_pct):
                                    viol.append(("B2B_NAO_E_O_PRIMEIRO_CENTAVO", p.id, uf, contrib, fin, rot, ""))
                                tab = preco_de_tabela(r.preco_negociado, ctx.get("fator_tabela") or 2)
                                if tab != orc.tabela(ob):
                                    viol.append(("TABELA_DIVERGE", p.id, uf, contrib, fin, rot, f"{tab} × {orc.tabela(ob)}"))
                                precos[p.id][(uf, contrib, fin, rot)] = (o["icms"], enc, r.preco_negociado, cond.codigo, sinal)
            if not no_escopo:
                continue
            # invariante: não contribuinte resolve nas 27 UFs
            nao_contrib = {uf for (uf, c, fin, _cond) in precos[p.id] if not c and fin == "USO_CONSUMO"}
            if len(nao_contrib) != 27:
                viol.append(("NAO_CONTRIBUINTE_NAO_RESOLVE_27_UFS", p.id, "", "", "", "", sorted(set(estados) - nao_contrib)))
        # monotonicidade
        for pid, cen in precos.items():
            grupos = defaultdict(list)
            for (uf, c, fin, rot), (icms, enc, preco, cond, sinal) in cen.items():
                grupos[(uf, c, fin)].append((enc, preco, rot))
            for k, lst in grupos.items():
                lst.sort()
                for (e1, p1, c1), (e2, p2, c2) in zip(lst, lst[1:]):
                    if e2 > e1 and p2 < p1:
                        viol.append(("ENCARGO_MAIOR_REDUZ_PRECO", pid, k[0], k[1], k[2], f"{c1}→{c2}", f"{p1}→{p2}"))
            grupos = defaultdict(list)
            for (uf, c, fin, rot), (icms, enc, preco, cond, sinal) in cen.items():
                grupos[rot].append((icms, preco))
            for rot, lst in grupos.items():
                lst.sort()
                for (i1, p1), (i2, p2) in zip(lst, lst[1:]):
                    if i2 > i1 and p2 < p1:
                        viol.append(("CARGA_MAIOR_REDUZ_PRECO", pid, "", "", "", rot, f"icms {i1}→{i2} preco {p1}→{p2}"))
            # sinal: dentro de (uf, contrib, fin, condição do saldo), sinal maior nunca aumenta
            # encargo nem B2B; sinal 100% = encargo zero (independe da condição do saldo)
            grupos = defaultdict(list)
            for (uf, c, fin, rot), (icms, enc, preco, cond, sinal) in cen.items():
                grupos[(uf, c, fin, cond)].append((sinal, enc, preco))
                if sinal >= 1 and enc != 0:
                    viol.append(("SINAL_100_COM_ENCARGO", pid, uf, c, fin, rot, str(enc)))
            for k, lst in grupos.items():
                lst.sort()
                for (s1, e1, p1), (s2, e2, p2) in zip(lst, lst[1:]):
                    if s2 > s1 and (e2 > e1 or p2 > p1):
                        viol.append(("SINAL_MAIOR_AUMENTA_ENCARGO_OU_B2B", pid, k[0], k[1], k[2], f"{k[3]} {s1}→{s2}", f"enc {e1}→{e2} b2b {p1}→{p2}"))
            b2b_100 = {(uf, c, fin): preco for (uf, c, fin, rot), (icms, enc, preco, cond, sinal) in cen.items() if sinal >= 1}
            for (uf, c, fin, rot), (icms, enc, preco, cond, sinal) in cen.items():
                if sinal >= 1 and b2b_100.get((uf, c, fin)) != preco:
                    viol.append(("SINAL_100_B2B_DEPENDE_DO_SALDO", pid, uf, c, fin, rot, f"{preco} × {b2b_100.get((uf, c, fin))}"))
    resumo = {"banco": caminho_do_banco(), "produtos": len(amostra), "sinais": [str(x) for x in sinais],
              "cenarios": n, "bloqueados": bloqueados,
              "violacoes": len(viol), "por_tipo": dict(Counter(v[0] for v in viol)),
              "amostra_violacoes": [list(map(str, v)) for v in viol[:50]], "segundos": round(time.time() - t0)}
    os.makedirs(os.path.dirname(a.saida), exist_ok=True)
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: v for k, v in resumo.items() if k != "amostra_violacoes"}, ensure_ascii=False))
    for v in viol[:10]:
        print("  ", v)
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())

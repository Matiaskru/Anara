"""Passo 2 — calibração da fórmula KTC (REV3 + REV4, 13/10/2026): 6 grupos com âncora, 3 calibrações
provisórias, liberação de P120–P125 (CONFIRMADO) e R203/R204 (ESTIMADO).

Portado de `~/Anara-Cotacao-Backups/tabela_comercial_gerador/sincronizar_calibracao_rev3.py` sem mudar
a lógica. Calcula a fórmula de cada âncora NO BANCO ALVO e para se o fator diferir do esperado.
Não faz commit — quem decide é `aplicar.py`.
"""
import json
from datetime import date

from sqlmodel import select

from app import admin_service as adm
from app import calculadora as calc
from app import calibracao_ktc as ck
from app import custo_service as cs
from app import governanca_produtos as gov
from app import pricing_service as ps
from app.models import CalibracaoKTC, MaterialPreco, Produto

import ancoras_ktc as ak
import catalogo_rev2 as cat

HOJE = date.today()
ORIGEM = "rev3-calibracao-ktc-2026-10-13"
FONTE = "Calibração KTC 13/10/2026 — âncoras: PI ANARA23082026, PI IQA23092026, Quotation ANARA07102026"
#: fator final esperado por (construção, grupo) — calculado no banco de ensaio em 13/10/2026
ESPERADO = {("250TC_SATEEN_70_30_PLAIN", "TODAS"): 1.1315,
            ("300TC_SATEEN_100_COTTON_PLAIN", "LENCOL_CAPA"): 1.0,
            ("300TC_SATEEN_100_COTTON_PLAIN", "FRONHA_2_ABAS"): 1.1214,
            ("300TC_SATEEN_100_COTTON_PLAIN", "FRONHA_3_ABAS"): 1.0531,
            ("300TC_SATEEN_100_COTTON_PLAIN", "FRONHA_4_ABAS"): 1.0,
            ("TOALHA_PISCINA_LISTRADA_100_ALGODAO", "TODAS"): 1.0}
LIBERAR = ("P120", "P121", "P122", "P123", "P124", "P125")
#: REV4: SKUs da REV2 em REVIEW_REQUIRED que a calibração provisória libera como ESTIMADO
LIBERAR_ESTIMADO = ("R203", "R204")
LOG = []


def log(m):
    LOG.append(m)
    print(m)


def formula(s, x, mats):
    kw = dict(familia=x["familia"], largura_cm=x["largura"], comprimento_cm=x["comprimento"],
              plain_or_stripe=x["plain_or_stripe"])
    if x["material"]:
        kw["material_id"] = mats[(x["material"], x["plain_or_stripe"])]
    if x["familia"] == "Pillow Case":
        kw.update(abas=x["abas"], flap_cm=20)
    if x["gsm"]:
        kw.update(gsm=x["gsm"], composicao_toalha=x["composicao"])
    r = ps.calcular_exw(s, calc.produto_simulado(s, **kw))     # fórmula PURA (sem calibração)
    assert r.exw_usd is not None, (x, r.faltando)
    return float(r.exw_usd)


def calcular(s):
    mats = {(m.material, m.plain_or_stripe): m.id for m in s.exec(select(MaterialPreco)).all()
            if m.ativo and m.valid_to is None}
    for x in ak.ANCORAS:
        x["formula"] = formula(s, x, mats)
        x["ratio"] = x["exw"] / x["formula"]
    out = []
    for g in ak.GRUPOS:
        nivel = [x for x in ak.ANCORAS if g["nivel"](x)]
        qa_set = [x for x in ak.ANCORAS if g["qa"](x)]
        f = ck.calcular_fator([x["ratio"] for x in nivel])
        q = ck.qa([(x["exw"], x["formula"]) for x in qa_set], f["fator_final"])
        out.append(dict(g=g, fator=f, qa=q, nivel=nivel, qa_set=qa_set))
        log(f"{g['construcao']} · {g['grupo']}: {f['n']} âncoras de nível · mediana {f['ratio_mediana']:.4f} "
            f"(min {f['ratio_min']:.4f} · max {f['ratio_max']:.4f}) → fator {f['fator_final']:.4f} "
            f"{'(calibrado)' if f['calibrou'] else '(validado, sem ajuste)'} · QA {q['n']} âncoras, pior erro "
            f"{q['pior_erro']:+.2%} {'OK' if q['ok'] else 'FALHOU'}")
    return out


def gravar(s, ator, r):
    g, f = r["g"], r["fator"]
    chave = (g["construcao"], g["grupo"])
    assert abs(f["fator_final"] - ESPERADO[chave]) < 1e-9, (chave, f["fator_final"], ESPERADO[chave])
    vig = [c for c in s.exec(select(CalibracaoKTC).where(CalibracaoKTC.construcao == g["construcao"],
                                                         CalibracaoKTC.grupo == g["grupo"])).all()
           if c.ativo and c.valid_to is None]
    if vig and abs(vig[0].fator_final - f["fator_final"]) < 1e-9 and not vig[0].bloqueio:
        log(f"  já vigente #{vig[0].id} — nada a fazer")
        return
    for c in vig:
        c.valid_to = HOJE
        s.add(c)
    ancoras = [dict(documento=x["documento"], linha=x["linha"], data=x["data"].isoformat(), item=x["item"],
                    exw_real=x["exw"], exw_formula=round(x["formula"], 6), ratio=round(x["ratio"], 6),
                    nivel=x in r["nivel"]) for x in r["qa_set"]]
    c = CalibracaoKTC(construcao=g["construcao"], grupo=g["grupo"], familias=g["familias"], material=g["material"],
                      plain_or_stripe=g["plain_or_stripe"], abas=g["abas"], fator_base=f["fator_base"],
                      seguranca_pct=f["seguranca_pct"], fator_final=f["fator_final"], n_ancoras=f["n"],
                      ratio_min=f["ratio_min"], ratio_media=f["ratio_media"], ratio_mediana=f["ratio_mediana"],
                      ratio_max=f["ratio_max"], dispersao=f["dispersao"], qa_pior_erro=r["qa"]["pior_erro"],
                      ancoras_json=json.dumps(ancoras, ensure_ascii=False), valid_from=HOJE, ativo=True,
                      fonte=FONTE, notas=g["porque"])
    s.add(c)
    s.flush()
    adm.registrar(s, ator=ator, acao="CALIBRAR_KTC", entidade="CalibracaoKTC", entidade_id=c.id,
                  escopo=f"{g['construcao']} · {g['grupo']}", antes=(f"#{vig[0].id} fator {vig[0].fator_final}" if vig else "sem calibração"),
                  depois=f"fator {c.fator_final:.4f} (base {c.fator_base:.4f}, {c.n_ancoras} âncoras, QA pior erro {c.qa_pior_erro:+.2%})",
                  motivo=g["porque"], origem=ORIGEM)
    log(f"  gravado #{c.id}")


def bloquear(s, ator, b):
    vig = [c for c in s.exec(select(CalibracaoKTC).where(CalibracaoKTC.construcao == b["construcao"],
                                                         CalibracaoKTC.grupo == b["grupo"])).all()
           if c.ativo and c.valid_to is None]
    if vig and vig[0].bloqueio == b["motivo"]:
        log(f"bloqueio {b['construcao']} · {b['grupo']}: já vigente #{vig[0].id}")
        return
    for c in vig:
        c.valid_to = HOJE
        s.add(c)
    c = CalibracaoKTC(construcao=b["construcao"], grupo=b["grupo"], familias=b["familias"], material=b["material"],
                      plain_or_stripe=b["plain_or_stripe"], abas=b["abas"], fator_base=1.0, seguranca_pct=0.0,
                      fator_final=1.0, n_ancoras=0, bloqueio=b["motivo"], valid_from=HOJE, ativo=True, fonte=FONTE)
    s.add(c)
    s.flush()
    adm.registrar(s, ator=ator, acao="BLOQUEAR_FORMULA_KTC", entidade="CalibracaoKTC", entidade_id=c.id,
                  escopo=f"{b['construcao']} · {b['grupo']}", antes="fórmula pura", depois="sob consulta (bloqueio)",
                  motivo=b["motivo"], origem=ORIGEM)
    log(f"bloqueio {b['construcao']} · {b['grupo']}: gravado #{c.id}")


def provisoria(s, ator, pv, fatores):
    f_ref = fatores[pv["de"]]
    vig = [c for c in s.exec(select(CalibracaoKTC).where(CalibracaoKTC.construcao == pv["construcao"],
                                                         CalibracaoKTC.grupo == pv["grupo"])).all()
           if c.ativo and c.valid_to is None]
    if vig and vig[0].provisoria and not vig[0].bloqueio and abs(vig[0].fator_final - f_ref["fator_final"]) < 1e-9:
        log(f"provisória {pv['construcao']} · {pv['grupo']}: já vigente #{vig[0].id}")
        return
    for c in vig:
        c.valid_to = HOJE
        s.add(c)
    c = CalibracaoKTC(construcao=pv["construcao"], grupo=pv["grupo"], familias=pv["familias"], material=pv["material"],
                      plain_or_stripe=pv["plain_or_stripe"], abas=pv["abas"], fator_base=f_ref["fator_base"],
                      seguranca_pct=f_ref["seguranca_pct"], fator_final=f_ref["fator_final"], n_ancoras=0,
                      provisoria=True, valid_from=HOJE, ativo=True, fonte=FONTE + f" · fator herdado de {pv['de'][0]} · {pv['de'][1]}",
                      notas=pv["porque"])
    s.add(c)
    s.flush()
    adm.registrar(s, ator=ator, acao="CALIBRAR_KTC_PROVISORIA", entidade="CalibracaoKTC", entidade_id=c.id,
                  escopo=f"{pv['construcao']} · {pv['grupo']}",
                  antes=(f"#{vig[0].id} {'bloqueio' if vig[0].bloqueio else 'fator ' + str(vig[0].fator_final)}" if vig else "fórmula pura"),
                  depois=f"fator provisório {c.fator_final:.4f} (de {pv['de'][0]} · {pv['de'][1]}) · status ESTIMADO",
                  motivo=pv["porque"], origem=ORIGEM)
    log(f"provisória {pv['construcao']} · {pv['grupo']}: fator {c.fator_final:.4f} gravado #{c.id}")


def liberar_estimado(s, ator):
    for cod in LIBERAR_ESTIMADO:
        p = s.exec(select(Produto).where(Produto.sku_key == cat.KTC_REVIEW[cod]["sku_key"])).one()
        vig = cs.referencia_vigente(s, p.id)
        custo, mem = ps.custo_para_precificar(s, p)
        cal = mem.get("calibracao_ktc") or {}
        assert cal.get("provisoria") and not mem.get("premissas_faltantes"), (cod, mem.get("metodo_exw"))
        if vig is not None and vig.status_custo == "ESTIMADO":
            log(f"{cod} {p.id}: já ESTIMADO (V{vig.versao})")
            continue
        ref = cs.registrar_referencia(
            s, p, cnet_brl=float(custo), metodo="KTC_CALCULATED", status="ESTIMADO",
            fonte=f"KTC_CALCULATED_CALIBRATED (PROVISÓRIA) — fórmula × {cal['fator_final']:.4f} ({cal['construcao']} · {cal['grupo']})",
            documento=None, data_ref=HOJE, origem_registro=ORIGEM,
            notas="REV4 13/10/2026: fator de acabamento de abas (medido na 300 algodão, mesmo padrão na 250); nível da 400 sem âncora",
            memoria={"exw_formula_usd": mem.get("exw_formula_usd"), "exw_usd": mem.get("exw_usd"), "provisoria": True})
        log(f"{cod} {p.id}: REVIEW_REQUIRED → ESTIMADO (V{ref.versao}) · EXW {mem['exw_formula_usd']:.4f} → {mem['exw_usd']:.4f}")


def liberar(s, ator):
    for cod in LIBERAR:
        p = s.exec(select(Produto).where(Produto.sku_key == cat.KTC_REVIEW[cod]["sku_key"])).one()
        vig = cs.referencia_vigente(s, p.id)
        custo, mem = ps.custo_para_precificar(s, p)
        assert mem.get("metodo_exw") == ps.METODO_EXW_CALIBRADO, (cod, mem.get("metodo_exw"))
        if vig is not None and vig.status_custo == "CONFIRMADO":
            log(f"{cod} {p.id}: já liberado (V{vig.versao})")
            continue
        cal = mem["calibracao_ktc"]
        gov.confirmar_referencia(
            s, p, ator=ator,
            fonte=f"KTC_CALCULATED_CALIBRATED — fórmula × {cal['fator_final']:.4f} ({cal['construcao']} · {cal['grupo']}, "
                  f"{cal['n_ancoras']} âncoras da cotação KTC 07/10/2026)",
            motivo="REV3 13/10/2026: construção calibrada e aprovada no QA (nenhuma cotação conhecida > 2% acima do "
                   "EXW operacional) — sai de 'sob consulta'")
        log(f"{cod} {p.id}: liberado · EXW fórmula {mem['exw_formula_usd']:.4f} → operacional {mem['exw_usd']:.4f} · CNET {custo:.4f}")

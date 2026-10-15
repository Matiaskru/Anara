#!/usr/bin/env python3
"""Publica no banco os dados da tabela comercial REV4 (13/10/2026): catálogo, calibração KTC e SKU novo.

    python3 scripts/publicar_rev4_2026_10_15/aplicar.py            # PRÉVIA: faz tudo, confere e DESFAZ
    python3 scripts/publicar_rev4_2026_10_15/aplicar.py --aplicar  # faz tudo, confere e GRAVA

Pré-requisito: código no commit eedef73 (ou posterior) e migration 0030 aplicada — o deploy do
Railway faz as duas coisas. Usa o banco que a aplicação usa (ANARA_DB_URL > DATABASE_URL > SQLite
local). Uma transação só: se qualquer conferência falhar, NADA é gravado. No fim, compara o preço
de cada código da tabela REV4 (São Paulo · não contribuinte · 30 dias) com o da tabela.

Passos (todos idempotentes — rodar de novo não muda nada):
  1. catálogo REV2 — passo1_catalogo.py
  2. calibração KTC REV3/REV4 — passo2_calibracao.py
  3. SKU lençol de baixo King 400 fios — passo3_rev4.py
"""
import argparse
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
sys.path.insert(0, RAIZ)

#: ids de MaterialPreco que os dados KTC (pi_23_09, ktc_07_10, catalogo_rev2, esperado) usam
MATERIAIS = {3: ("250TC Sateen CVC 70/30", "plain"), 7: ("300TC Sateen CVC 70/30", "plain"),
             9: ("300TC Sateen 100% Cotton", "plain"), 11: ("400TC Sateen 100% Cotton", "plain")}


def conferir_esquema(engine):
    from sqlalchemy import inspect
    ins = inspect(engine)
    falta = []
    if "calibracaoktc" not in ins.get_table_names():
        falta.append("tabela calibracaoktc (migration 0030)")
    else:
        if "provisoria" not in {c["name"] for c in ins.get_columns("calibracaoktc")}:
            falta.append("calibracaoktc.provisoria (migration 0030)")
    if "comissao_fixa" not in {c["name"] for c in ins.get_columns("margemregra")}:
        falta.append("margemregra.comissao_fixa (migration 0029)")
    if "quantity_per_carton" not in {c["name"] for c in ins.get_columns("produto")}:
        falta.append("produto.quantity_per_carton (migration 0028)")
    if falta:
        raise SystemExit("ESQUEMA DESATUALIZADO — rode o deploy (alembic upgrade head) antes. Falta: " + "; ".join(falta))


def conferir_materiais(s):
    from app.models import MaterialPreco
    erros = []
    for i, (nome, ps_) in MATERIAIS.items():
        m = s.get(MaterialPreco, i)
        if m is None or m.material != nome or m.plain_or_stripe != ps_:
            erros.append(f"MaterialPreco {i}: esperado '{nome} {ps_}', banco tem '{getattr(m, 'material', None)}'")
    if erros:
        raise SystemExit("PREFLIGHT FALHOU — nada foi gravado:\n  " + "\n  ".join(erros))
    print(f"preflight ok: {len(MATERIAIS)} tecidos conferidos por nome")


def verificar(s):
    """Preço de cada código da REV4 no cenário SP · não contribuinte · 30 dias, pelo mesmo caminho do
    gerador da tabela (export_v2.py). Devolve a lista de divergências."""
    from sqlmodel import select
    from app import calculadora as calc
    from app import pricing_service as ps
    from app.models import Produto
    from app.routers.calculadora import _cenario_do_form
    from app.workflow import CUSTO_BLOQUEIA
    esperado = json.load(open(os.path.join(AQUI, "esperado_rev4.json")))
    cen = _cenario_do_form(s, {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"})
    dif, n = [], 0
    for cod, e in esperado.items():
        if e.get("sku_key"):
            prod = s.exec(select(Produto).where(Produto.sku_key == e["sku_key"])).first()
            if prod is None or not prod.ativo:
                dif.append(f"{cod}: SKU '{e['sku_key']}' ausente ou inativo")
                continue
        else:
            prod = calc.produto_simulado(s, **e["spec"])
        custo, mem0 = ps.custo_para_precificar(s, prod)
        status = ps.status_do_produto(s, prod, custo, mem0)
        faltantes = mem0.get("premissas_faltantes") or []
        mem = calc.calcular(s, cotacao=cen, **e["spec"]) if e.get("spec") else ps.memoria_do_preco(s, prod, cen)
        b = mem.get("b2b") or {}
        ok = bool(b.get("preco_b2b")) and status not in CUSTO_BLOQUEIA and not mem.get("bloqueado") and not faltantes
        b2b = round(float(b["preco_b2b"]), 2) if ok else None
        n += 1
        if (status, ok, b2b) != (e["status"], e["utilizavel"], e["b2b"]):
            dif.append(f"{cod}: esperado {e['status']} / {'com preço' if e['utilizavel'] else 'sob consulta'} / B2B {e['b2b']}"
                       f" — banco dá {status} / {'com preço' if ok else 'sob consulta'} / B2B {b2b}")
    print(f"verificação: {n} códigos da tabela REV4 conferidos (SP · não contribuinte · 30 dias) · {len(dif)} divergência(s)")
    return dif


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isto é prévia: faz, confere e desfaz)")
    a = ap.parse_args()

    from sqlmodel import Session, select
    from app import pricing_service as ps
    from app.db import engine, url_segura
    from app.models import Usuario
    import passo1_catalogo as p1
    import passo2_calibracao as p2
    import passo3_rev4 as p3
    import ancoras_ktc as ak

    print("banco:", url_segura())
    print("modo:", "APLICAR (grava)" if a.aplicar else "PRÉVIA (desfaz no fim)")
    conferir_esquema(engine)
    with Session(engine) as s:
        conferir_materiais(s)
        p1.preflight(s)
        ator = s.exec(select(Usuario).where(Usuario.papel == "OWNER")).first()
        if ator is None:
            raise SystemExit("Nenhum usuário OWNER no banco — nada foi gravado.")
        print("\n== 1. catálogo REV2")
        p1.regras(s, ator)
        p1.daune(s, ator)
        p1.decor_elis(s, ator)
        p1.ktc(s, ator)
        s.flush()
        ps.invalidar_cache_de_leitura()
        print("\n== 2. calibração KTC")
        res = p2.calcular(s)
        falhas = [r for r in res if not r["qa"]["ok"]]
        if falhas:
            raise SystemExit("QA DA CALIBRAÇÃO FALHOU — nada foi gravado: "
                             + ", ".join(f"{r['g']['construcao']}·{r['g']['grupo']}" for r in falhas))
        for r in res:
            p2.gravar(s, ator, r)
        fatores = {(r["g"]["construcao"], r["g"]["grupo"]): r["fator"] for r in res}
        for pv in ak.PROVISORIAS:
            p2.provisoria(s, ator, pv, fatores)
        for b in ak.BLOQUEIOS:
            p2.bloquear(s, ator, b)
        ps.invalidar_cache_de_leitura()
        p2.liberar(s, ator)
        p2.liberar_estimado(s, ator)
        print("\n== 3. REV4")
        p3.criar_king_400(s, ator)
        s.flush()
        ps.invalidar_cache_de_leitura()
        print("\n== 4. conferência contra a tabela REV4")
        dif = verificar(s)
        if dif:
            for d in dif[:60]:
                print("  ", d)
            raise SystemExit(f"VERIFICAÇÃO FALHOU ({len(dif)} divergências) — nada foi gravado.")
        if a.aplicar:
            s.commit()
            print("\nAPLICADO — preços do sistema = tabela REV4.")
        else:
            s.rollback()
            print("\nPRÉVIA OK — tudo conferido e desfeito. Para gravar: --aplicar")


if __name__ == "__main__":
    main()

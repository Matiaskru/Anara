#!/usr/bin/env python3
"""Itens calculados da Tabela comercial viram produtos do catálogo (15/10/2026, pedido do Matias).

    python3 scripts/tabela_comercial_2026_10_15/aplicar.py              # prévia: não grava nada
    python3 scripts/tabela_comercial_2026_10_15/aplicar.py --aplicar

Os filtros (cotação, calculadora, página Produtos) passaram a mostrar só o que está na tabela
(`app/tabela_comercial.py`). 37 itens da tabela — lençóis e capas 400 fios, 300 fios algodão
Super King, 300 fios 70/30, fronha 400 com 3 e 4 abas, toalha de piscina listras finas — eram
calculados na hora e não tinham produto, então não apareceriam no filtro. Este script:

1. cria esses 37 pelo MESMO caminho do "Salvar no catálogo" da calculadora
   (`calculadora.salvar_no_catalogo`; o que já existe com a mesma chave é reaproveitado);
2. confere que o B2B de cada um (SP · não contribuinte · 30 dias) é o da tabela;
3. recalcula o "B2B de referência" do catálogo (`Produto.preco_base`, cache que a página
   Produtos mostra), que ficou do cenário de 21/09;
4. confere que todo produto da lista da tabela existe e está ativo.

A prévia calcula tudo sem gravar (não chama `salvar_no_catalogo`, que grava por dentro). O
`--aplicar` só começa se a prévia não tiver divergência. Idempotente.
"""
import argparse
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))

ORIGEM = "tabela-comercial-2026-10-15"
CENARIO = {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"}


def b2b_de(s, prod, cen):
    from app import pricing_service as ps
    from app.workflow import CUSTO_BLOQUEIA
    custo, mem0 = ps.custo_para_precificar(s, prod)
    status = ps.status_do_produto(s, prod, custo, mem0)
    mem = ps.memoria_do_preco(s, prod, cen)
    b = (mem.get("b2b") or {}).get("preco_b2b")
    ok = bool(b) and status not in CUSTO_BLOQUEIA and not mem.get("bloqueado") \
        and not mem0.get("premissas_faltantes")
    return (round(float(b), 2) if ok else None), status


def previa(s, itens, cen):
    """O que seria criado e com que preço — sem gravar."""
    from sqlmodel import select
    from app import calculadora as calc
    from app.models import Produto
    dif = []
    for it in itens:
        par = it["parametros"]
        existente = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first()
        if existente is not None:
            b2b, status = b2b_de(s, existente, cen)
            origem = f"já existe (#{existente.id}{'' if existente.ativo else ', INATIVO'})"
            if not existente.ativo:
                dif.append(f"{it['codigo']}: produto #{existente.id} existe mas está inativo")
        else:
            mem = calc.calcular(s, cotacao=cen, **par)
            b = (mem.get("b2b") or {}).get("preco_b2b")
            b2b, status, origem = (round(float(b), 2) if b else None), "novo", "novo"
        esperado = it["b2b_sp_nao_30"]
        marca = "ok" if b2b == esperado else "DIVERGE"
        print(f"  {it['codigo']:5s} {origem:22s} B2B {b2b} (tabela {esperado}) {marca} · {it['nome']}")
        if b2b != esperado:
            dif.append(f"{it['codigo']}: B2B {b2b} ≠ tabela {esperado}")
    return dif


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isto é prévia)")
    a = ap.parse_args()

    from sqlmodel import Session, select
    from app import admin_service as adm
    from app import calculadora as calc
    from app import pricing_service as ps
    from app import tabela_comercial as tc
    from app.dados_2026_09_21 import recalcular_preco_base_catalogo
    from app.db import engine, url_segura
    from app.models import Produto, Usuario
    from app.routers.calculadora import _cenario_do_form

    print("banco:", url_segura())
    itens = json.load(open(os.path.join(AQUI, "itens_calculados.json"), encoding="utf-8"))
    print(f"tabela: {tc.VERSAO} · {len(tc.SKUS)} produtos na lista · {len(itens)} calculados")

    with Session(engine) as s:
        cen = _cenario_do_form(s, CENARIO)
        print("\n== 1. prévia dos itens calculados")
        with ps.cache_de_leitura(s):
            dif = previa(s, itens, cen)
        s.rollback()
        if dif:
            print(f"\nPRÉVIA COM {len(dif)} DIVERGÊNCIA(S) — nada foi gravado:")
            for d in dif:
                print("  ", d)
            return 1
        if not a.aplicar:
            ausentes = [k for k in tc.SKUS if not s.exec(select(Produto).where(
                Produto.sku_key == k, Produto.ativo == True)).first()]   # noqa: E712
            print(f"\nlista da tabela: {len(tc.SKUS) - len(ausentes)} de {len(tc.SKUS)} já existem e estão "
                  f"ativos; {len(ausentes)} serão criados pelo --aplicar")
            print("\nPRÉVIA OK — nada foi gravado. Para gravar: --aplicar")
            return 0

        ator = s.exec(select(Usuario).where(Usuario.papel == "OWNER")).first()
        if ator is None:
            raise SystemExit("Nenhum usuário OWNER no banco — nada foi gravado.")
        print("\n== 2. criando os itens calculados (caminho da calculadora)")
        criados = 0
        for it in itens:
            par = it["parametros"]
            antes = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first()
            prod = calc.salvar_no_catalogo(s, par["familia"], par["largura_cm"], par["comprimento_cm"],
                                           material_id=par.get("material_id"), gsm=par.get("gsm"),
                                           plain_or_stripe=par.get("plain_or_stripe", "plain"),
                                           abas=par.get("abas"), flap_cm=par.get("flap_cm"),
                                           composicao_toalha=par.get("composicao_toalha"),
                                           observacao=f"Item {it['codigo']} da Tabela comercial ({tc.VERSAO})")
            assert prod.sku_key == it["sku_key"], (prod.sku_key, it["sku_key"])
            if antes is None:
                criados += 1
                adm.registrar(s, ator=ator, acao="CRIAR_PRODUTO", entidade="Produto", entidade_id=prod.id,
                              escopo=f"SKU {prod.sku_key}", antes="—", depois=prod.nome,
                              motivo=f"Item {it['codigo']} da Tabela comercial: produto do catálogo para "
                                     "aparecer no filtro (antes só pela calculadora)", origem=ORIGEM)
                s.commit()
            print(f"  {it['codigo']:5s} #{prod.id} {'criado' if antes is None else 'já existia'} · {prod.nome}")

        print("\n== 3. B2B de referência do catálogo (cache da página Produtos)")
        print("  ", recalcular_preco_base_catalogo(s))
        s.commit()

        print("\n== 4. conferência")
        erros = []
        with ps.cache_de_leitura(s):
            for it in itens:
                prod = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).one()
                b2b, status = b2b_de(s, prod, cen)
                if b2b != it["b2b_sp_nao_30"]:
                    erros.append(f"{it['codigo']}: B2B {b2b} ({status}) ≠ tabela {it['b2b_sp_nao_30']}")
        ausentes = [f"{c} ({k})" for k, c in tc.SKUS.items() if not s.exec(select(Produto).where(
            Produto.sku_key == k, Produto.ativo == True)).first()]   # noqa: E712
        erros += [f"fora do catálogo ativo: {x}" for x in ausentes]
        print(f"   {len(itens)} calculados conferidos · lista da tabela: {len(tc.SKUS) - len(ausentes)} de "
              f"{len(tc.SKUS)} no catálogo ativo · {criados} criado(s) agora")
        if erros:
            print(f"\nATENÇÃO — {len(erros)} problema(s):")
            for e in erros:
                print("  ", e)
            return 1
        print("\nAPLICADO — todo produto da tabela está no catálogo e aparece no filtro.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

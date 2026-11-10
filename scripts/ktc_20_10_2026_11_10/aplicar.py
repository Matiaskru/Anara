#!/usr/bin/env python3
"""Cotação KTC ANARA20102026 (20/10/2026) no catálogo — pedido do Matias em 10/11/2026.

    python3 scripts/ktc_20_10_2026_11_10/aplicar.py              # prévia: não grava nada
    python3 scripts/ktc_20_10_2026_11_10/aplicar.py --aplicar

1. **Atualizar custo** ("pode fazer o 2"): os 21 SKUs 250 fios 70/30 da Tabela comercial recebem o EXW de
   20/10 pelo caminho do Admin → Produtos e custos (`governanca_produtos.registrar_exw_ktc`: nova versão da
   referência, com fonte, documento e auditoria; o peso que o SKU já tem continua). Os 6 Super King
   (P120–P125) eram formados pela fórmula calibrada: passam a usar a cotação direta (no `custo_net`, SKU com
   método "calculado" ignora o EXW cotado — o método muda para KTC_QUOTED, auditado).
   Atenção: a Tabela de preços fica parada (preço fixo), mas a COTAÇÃO forma o B2B pela margem — onde o EXW
   mudou, o B2B da cotação muda junto. A prévia mostra antes → depois.
2. **Cadastrar o que está na tabela e não estava no sistema** ("pode colocar no sistema o que tem na tabela e
   não tem lá"): 5 tapetes (N53–N57) e 23 itens 600 fios (N58–N80), montados como a calculadora monta
   (`itens.produto_novo`), com o EXW cotado registrado pelo mesmo caminho do item 1. Peso: estimativa da
   família (a KTC não informou) — status ESTIMADO: cota e emite PDF; fechar venda pede o peso real.
3. Recalcula o "B2B de referência" do catálogo (`Produto.preco_base`).
4. Confere: todo item novo com B2B (SP · não contribuinte · 30 dias) = preço fixo da Tabela de preços;
   todo produto da lista da tabela (`app/tabela_comercial.py`) existe e está ativo.

A prévia calcula tudo sem gravar (produto em memória, fora da sessão). `--aplicar` só começa se a prévia
estiver limpa, grava tudo numa transação só e é idempotente (SKU que já existe não é recriado; cotação já
registrada com o mesmo EXW, data e documento não vira versão nova).
"""
import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
sys.path.insert(0, AQUI)

CENARIO = {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"}
MOTIVO_ATUALIZAR = ("Cotação KTC ANARA20102026 de 20/10/2026 — custo atualizado a pedido do Matias em "
                    "10/11/2026 (preço da Tabela de preços fica fixo; a margem absorve)")
MOTIVO_NOVO = ("Item {codigo} da Tabela de preços (cotação KTC ANARA20102026 de 20/10/2026), cadastrado a "
               "pedido do Matias em 10/11/2026")


def b2b(s, prod, cen):
    from app import pricing_service as ps
    from app.workflow import CUSTO_BLOQUEIA
    custo, mem0 = ps.custo_para_precificar(s, prod)
    status = ps.status_do_produto(s, prod, custo, mem0)
    mem = ps.memoria_do_preco(s, prod, cen)
    valor = (mem.get("b2b") or {}).get("preco_b2b")
    ok = bool(valor) and status not in CUSTO_BLOQUEIA and not mem.get("bloqueado") \
        and not mem0.get("premissas_faltantes")
    return (round(float(valor), 2) if ok else None), status, mem0


def ja_registrada(prod, item, dados):
    return (prod.exw_cotado_usd is not None and abs(prod.exw_cotado_usd - item["exw_usd"]) < 1e-9
            and prod.exw_cotado_data == dados["data_ref"] and prod.custo_ref_documento == dados["documento"])


def copia_com_exw(prod, item, dados):
    """Cópia em memória do SKU com o EXW novo (para a prévia — nada entra na sessão)."""
    from app.models import CostMethod, Produto
    import itens
    c = Produto(**prod.model_dump(exclude={"id"}))
    c.cost_method = CostMethod.ktc_quoted.value
    c.exw_cotado_usd, c.exw_cotado_data = item["exw_usd"], dados["data_ref"]
    c.exw_cotado_fonte = itens.fonte(item, dados)
    return c


def previa(s, dados, cen):
    from sqlmodel import select
    from app import tabela_precos as tp
    from app.models import Produto
    import itens
    problemas = []
    print("\n== 1. custo do 250 fios 70/30 (B2B da COTAÇÃO, SP · não contribuinte · 30 dias; a Tabela de preços não muda)")
    for it in dados["atualizar"]:
        achados = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).all()
        if len(achados) != 1 or not achados[0].ativo:
            problemas.append(f"{it['codigo']}: {len(achados)} SKU(s) com a chave (esperado 1 ativo)")
            continue
        prod = achados[0]
        antes, st_antes, _ = b2b(s, prod, cen)
        depois, st_depois, _ = b2b(s, copia_com_exw(prod, it, dados), cen)
        tabela = tp.preco_b2b(it["codigo"], "São Paulo", False, "30")
        feito = " (já registrada)" if ja_registrada(prod, it, dados) else ""
        print(f"  {it['codigo']:5s} #{prod.id:<4d} EXW {prod.exw_cotado_usd or '—'} ({prod.cost_method}) → {it['exw_usd']}"
              f"  B2B cotação {antes} → {depois} ({st_depois})  · tabela fixa {tabela}{feito}")
        if depois is None:
            problemas.append(f"{it['codigo']}: com o EXW de 20/10 o SKU fica sem preço ({st_depois})")
    print("\n== 2. itens novos (B2B da cotação = preço fixo da Tabela de preços?)")
    for it in dados["novos"]:
        existente = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first()
        prod = existente if existente is not None else itens.produto_novo(s, it, dados)
        valor, status, _ = b2b(s, prod, cen)
        tabela = tp.preco_b2b(it["codigo"], "São Paulo", False, "30")
        origem = f"já existe #{existente.id}" if existente is not None else "novo"
        marca = "ok" if valor == tabela else "DIVERGE"
        print(f"  {it['codigo']:4s} {origem:14s} B2B {valor} (tabela {tabela}) {marca} · {status} · {prod.nome}")
        if valor != tabela:
            problemas.append(f"{it['codigo']}: B2B {valor} ≠ tabela {tabela}")
        if existente is not None and not existente.ativo:
            problemas.append(f"{it['codigo']}: SKU #{existente.id} existe e está inativo")
    return problemas


def aplicar(s, dados, ator):
    from sqlmodel import select
    from app import admin_service as adm
    from app import governanca_produtos as gov
    from app import pricing_service as ps
    from app.models import CostMethod, Produto
    import itens
    print("\n== aplicando: custo do 250 fios 70/30")
    for it in dados["atualizar"]:
        prod = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).one()
        if ja_registrada(prod, it, dados):
            print(f"  {it['codigo']:5s} #{prod.id} já tinha esta cotação — nada a fazer")
            continue
        if prod.cost_method != CostMethod.ktc_quoted.value:
            antes = prod.cost_method
            prod.cost_method = CostMethod.ktc_quoted.value
            s.add(prod)
            s.flush()
            adm.registrar(s, ator=ator, acao="MUDAR_METODO_CUSTO", entidade="Produto", entidade_id=prod.id,
                          escopo=f"SKU {prod.sku_key}", antes=antes, depois=CostMethod.ktc_quoted.value,
                          motivo="Cotação direta da KTC (20/10/2026) passa a formar o EXW no lugar da fórmula "
                                 "calibrada (hierarquia: cotação do SKU > fórmula calibrada)",
                          origem=dados["origem"])
        r = gov.registrar_exw_ktc(s, prod, exw_usd=it["exw_usd"], data_ref=dados["data_ref"],
                                  documento=dados["documento"], fonte=itens.fonte(it, dados),
                                  motivo=MOTIVO_ATUALIZAR, ator=ator)
        print(f"  {it['codigo']:5s} #{prod.id} EXW {it['exw_usd']} → versão {r['versao']} · CNET {r['cnet']:.2f} · {r['status']}")
    print("\n== aplicando: itens novos")
    for it in dados["novos"]:
        prod = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first()
        criado = prod is None
        if criado:
            prod = itens.produto_novo(s, it, dados)
            prod.sku_key = it["sku_key"]
            prod.precisa_revisao = False
            prod.custo_ref_nota = f"Item {it['codigo']} da Tabela de preços (cotação KTC 20/10, SN {it['sn']})"
            peso = ps.peso_do_produto(s, prod)
            if peso.peso_kg:
                prod.peso_kg, prod.peso_tipo, prod.peso_fonte = peso.peso_kg, peso.tipo, peso.fonte
            prod.margem_padrao_pct = ps.margem_padrao(s, prod).margem_pct
            s.add(prod)
            s.flush()
            adm.registrar(s, ator=ator, acao="CRIAR_PRODUTO", entidade="Produto", entidade_id=prod.id,
                          escopo=f"SKU {prod.sku_key}", antes="—", depois=prod.nome,
                          motivo=MOTIVO_NOVO.format(codigo=it["codigo"]), origem=dados["origem"])
        if ja_registrada(prod, it, dados) and not criado:
            print(f"  {it['codigo']:4s} #{prod.id} já existia com esta cotação — nada a fazer")
            continue
        r = gov.registrar_exw_ktc(s, prod, exw_usd=it["exw_usd"], data_ref=dados["data_ref"],
                                  documento=dados["documento"], fonte=itens.fonte(it, dados),
                                  motivo=MOTIVO_NOVO.format(codigo=it["codigo"]), ator=ator)
        print(f"  {it['codigo']:4s} #{prod.id} {'criado' if criado else 'atualizado'} · {prod.nome} · "
              f"CNET {r['cnet']:.2f} · {r['status']}")


def conferir(s, dados, cen):
    from sqlmodel import select
    from app import tabela_comercial as tc
    from app import tabela_precos as tp
    from app.models import Produto
    erros = []
    for it in dados["novos"] + dados["atualizar"]:
        prod = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).one()
        if abs((prod.exw_cotado_usd or 0) - it["exw_usd"]) > 1e-9 or prod.exw_cotado_data != dados["data_ref"]:
            erros.append(f"{it['codigo']}: EXW gravado {prod.exw_cotado_usd} ({prod.exw_cotado_data})")
    for it in dados["novos"]:
        prod = s.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).one()
        valor, status, _ = b2b(s, prod, cen)
        tabela = tp.preco_b2b(it["codigo"], "São Paulo", False, "30")
        if valor != tabela:
            erros.append(f"{it['codigo']}: B2B {valor} ({status}) ≠ tabela {tabela}")
    ausentes = [f"{c} ({k})" for k, c in tc.SKUS.items() if not s.exec(select(Produto).where(
        Produto.sku_key == k, Produto.ativo == True)).first()]   # noqa: E712
    erros += [f"fora do catálogo ativo: {x}" for x in ausentes]
    print(f"   {len(dados['atualizar'])} atualizados e {len(dados['novos'])} novos conferidos · lista da tabela: "
          f"{len(tc.SKUS) - len(ausentes)} de {len(tc.SKUS)} no catálogo ativo")
    return erros


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isto é prévia)")
    a = ap.parse_args()

    from sqlmodel import Session, select
    from app import config_service as cfg
    from app import pricing_service as ps
    from app.dados_2026_09_21 import recalcular_preco_base_catalogo
    from app.db import engine, url_segura
    from app.models import Usuario
    from app.routers.calculadora import _cenario_do_form
    import itens

    dados = itens.carregar()
    print("banco:", url_segura())
    with Session(engine) as s:
        print(f"câmbio do sistema: {cfg.num(s, 'fx_usd_brl')} · {dados['documento']} · "
              f"{len(dados['atualizar'])} a atualizar, {len(dados['novos'])} novos")
        cen = _cenario_do_form(s, CENARIO)
        with ps.cache_de_leitura(s):
            problemas = previa(s, dados, cen)
        s.rollback()
        if problemas:
            print(f"\nPRÉVIA COM {len(problemas)} PROBLEMA(S) — nada foi gravado:")
            for p in problemas:
                print("  ", p)
            return 1
        if not a.aplicar:
            print("\nPRÉVIA OK — nada foi gravado. Para gravar: --aplicar")
            return 0

        ator = s.exec(select(Usuario).where(Usuario.papel == "OWNER", Usuario.ativo == True)   # noqa: E712
                      .order_by(Usuario.id)).first()
        if ator is None:
            raise SystemExit("Nenhum usuário OWNER ativo no banco — nada foi gravado.")
        try:
            aplicar(s, dados, ator)
            print("\n== B2B de referência do catálogo (cache da página Produtos)")
            print("  ", recalcular_preco_base_catalogo(s))
            s.commit()
        except Exception:
            s.rollback()
            print("\nERRO — nada foi gravado (rollback da transação inteira).")
            raise

        print("\n== conferência")
        ps.invalidar_cache_de_leitura()
        with ps.cache_de_leitura(s):
            erros = conferir(s, dados, cen)
        if erros:
            print(f"\nATENÇÃO — {len(erros)} problema(s):")
            for e in erros:
                print("  ", e)
            return 1
        print("\nAPLICADO — custo de 20/10 no 250 fios 70/30 e os 28 itens novos no catálogo, com o preço da tabela.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

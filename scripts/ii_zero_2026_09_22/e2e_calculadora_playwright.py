#!/usr/bin/env python3
"""Calculadora pela TELA (Playwright), como OWNER, VENDEDOR_INTERNO e VENDEDOR_COMISSIONADO.

    python3 scripts/ii_zero_2026_09_22/e2e_calculadora_playwright.py --db /caminho/copia.db [--porta 8458]

Para cada papel: abre /calculadora, escolhe família, preenche medida e tecido, calcula e lê o
resultado NA TELA. OWNER vê a economia (custo, margem, lucro, memória do preço). A vendedora vê
o comercial (tabela, B2B, preço, desconto, comissão dela, total, situação) e **nada** de custo,
margem, lucro, memória, US$ ou proteção — no HTML e no corpo da resposta de rede.

Depois, o fluxo real da vendedora: cliente → venda → cotação → produto personalizado →
adicionar à cotação → negociar abaixo do B2B → pedir aprovação. E a prova de paridade: o mesmo
produto personalizado calculado pela vendedora e pelo OWNER dá o MESMO B2B e a MESMA tabela.
"""
import argparse
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21.e2e_playwright import (  # noqa: E402
    ADMIN, DONA, VEND, brl, login, negociacao, registrar, resultados, semear_usuarios, situacao,
    subir_servidor,
)
from scripts.politica_2026_09_21.e2e_sinal_playwright import COMISSIONADA, semear_comissionada  # noqa: E402

PROIBIDO_TELA_CALC = ("custo", "cnet", "exw", "us$", "margem", "lucro", "markup", "memória",
                      "memoria do preço", "proteção", "imposto de importação", "nacionaliz")


def preencher(page, familia="Lençol plano", largura="240", comprimento="260"):
    page.wait_for_selector(".tile")
    alvo = None
    for tile in page.query_selector_all(".tile"):
        if tile.query_selector(".t-nome").inner_text().strip().lower().startswith(familia.lower()):
            alvo = tile
            break
    alvo.click()
    page.wait_for_selector("#bloco-medida:not([style*='display: none'])")
    page.fill("#largura_cm", largura)
    page.fill("#comprimento_cm", comprimento)
    page.fill("#quantidade", "10")


def calcular(page):
    page.click("#btn-calcular")
    page.wait_for_selector("#resultado:not([style*='display: none'])", timeout=15000)
    page.wait_for_timeout(300)


def kpis(page):
    return {c.query_selector(".label").text_content().strip():
            c.query_selector(".value").text_content().strip()
            for c in page.query_selector_all("#resultado .kpi")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--porta", type=int, default=8458)
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "e2e_2026_09_21"))
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    assert os.path.realpath(db) != os.path.realpath(os.path.join(RAIZ, "data", "anara.db")), "nunca contra o banco real"
    os.makedirs(a.saida, exist_ok=True)
    semear_usuarios(db)
    semear_comissionada(db)
    base = f"http://127.0.0.1:{a.porta}"
    proc = subir_servidor(db, a.porta)
    numeros = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for perfil, cred in (("OWNER", DONA), ("VENDEDOR_INTERNO", VEND),
                                 ("VENDEDOR_COMISSIONADO", COMISSIONADA)):
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                respostas = []
                page.on("response", lambda r: respostas.append(r) if "/calculadora/calcular" in r.url else None)
                login(page, base, cred[0], cred[2])
                page.goto(f"{base}/calculadora"); page.wait_for_load_state("networkidle")
                registrar(f"[{perfil}] GET /calculadora abre (200)", page.query_selector("#form-calc") is not None, page.url)
                preencher(page)
                calcular(page)
                k = kpis(page)
                corpo = respostas[-1].json() if respostas else {}
                if perfil == "OWNER":
                    registrar("[OWNER] vê a economia na tela (custo, margem, lucro) e a memória",
                              all(x in k for x in ("Custo NET", "Margem", "Lucro no total", "Preço sugerido"))
                              and page.query_selector("#btn-memoria") is not None, list(k))
                    page.click("#btn-memoria"); page.wait_for_timeout(300)
                    registrar("[OWNER] memória do preço abre com o waterfall",
                              "memória" in page.inner_text("#memoria-inline").lower()
                              or len(page.inner_text("#memoria-inline")) > 200)
                    numeros["OWNER"] = {"preco": brl(k["Preço sugerido"]), "json": corpo.get("b2b", {})}
                else:
                    registrar(f"[{perfil}] vê o comercial (tabela, B2B, comissão, total)",
                              all(x in k for x in ("Preço da proposta", "Preço de tabela",
                                                   "B2B (menor preço sem aprovação)", "Sua comissão",
                                                   "Desconto sobre a tabela", "Total da linha")), list(k))
                    registrar(f"[{perfil}] tela sem economia", not [t for t in PROIBIDO_TELA_CALC if t in page.content().lower()],
                              [t for t in PROIBIDO_TELA_CALC if t in page.content().lower()])
                    texto_json = json.dumps(corpo, ensure_ascii=False).lower()
                    registrar(f"[{perfil}] resposta de rede sem campo econômico",
                              not [t for t in ("custo", "cnet", "exw", "margem", "lucro", "markup", "memoria",
                                               "base_comercial", "protecao", "ii_pct", "usd") if t in texto_json],
                              list(corpo))
                    registrar(f"[{perfil}] situação em linguagem comercial",
                              bool(page.inner_text("#r-situacao").strip()) and "custo" not in page.inner_text("#r-situacao").lower(),
                              page.inner_text("#r-situacao")[:80])
                    numeros[perfil] = {"preco": brl(k["Preço da proposta"]), "tabela": brl(k["Preço de tabela"]),
                                       "b2b": brl(k["B2B (menor preço sem aprovação)"]),
                                       "json": {x: corpo.get(x) for x in ("preco_b2b", "preco_tabela", "total")}}
                page.screenshot(path=os.path.join(a.saida, f"calculadora_{perfil}.png"), full_page=True)
                ctx.close()

            # paridade: o mesmo produto, o mesmo número para os três papéis
            registrar("mesmo produto personalizado: B2B/tabela iguais para OWNER e as duas vendedoras",
                      numeros["OWNER"]["json"].get("preco_b2b") == numeros["VENDEDOR_INTERNO"]["json"]["preco_b2b"]
                      == numeros["VENDEDOR_COMISSIONADO"]["json"]["preco_b2b"]
                      and numeros["OWNER"]["json"].get("preco_tabela") == numeros["VENDEDOR_INTERNO"]["json"]["preco_tabela"],
                      numeros)

            # fluxo real da vendedora, de ponta a ponta
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page(); login(page, base, VEND[0], VEND[2])
            r = ctx.request.post(f"{base}/clientes", form={"nome": "Hotel Personalizado", "cnpj_cpf": "88.999.000/0001-11", "cidade_uf": "São Paulo"}, max_redirects=0)
            cliente_id = int(re.search(r"/clientes/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/vendas", form={"cliente_id": str(cliente_id), "titulo": "Enxoval sob medida"}, max_redirects=0)
            venda_id = int(re.search(r"/vendas/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/cotacoes", form={"cliente_id": str(cliente_id), "condicao_pagamento": "30",
                                                             "estado_destino": "São Paulo", "contribuinte_icms": "nao",
                                                             "freight_type": "FOB", "oportunidade_id": str(venda_id)}, max_redirects=0)
            cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers["location"]).group(1))
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            link = page.query_selector('a[href*="/calculadora?cotacao_id="]')
            registrar("[VENDEDOR_INTERNO] a cotação oferece 'Produto personalizado'", link is not None)
            link.click(); page.wait_for_selector("#form-calc")
            preencher(page, largura="235", comprimento="255")
            calcular(page)
            k = kpis(page)
            b2b_tela = brl(k["B2B (menor preço sem aprovação)"])
            page.click("#btn-salvar")
            page.wait_for_url(f"**/cotacoes/{cot_id}", timeout=20000)
            page.wait_for_selector("#cotacao")
            p = negociacao(ctx, base, cot_id)
            item = p["itens"][-1]
            registrar("[VENDEDOR_INTERNO] produto personalizado entrou na cotação com B2B/tabela/comissão",
                      abs(item["preco_b2b"] - b2b_tela) < 0.006 and abs(item["preco_tabela"] - 2 * item["preco_b2b"]) < 0.011
                      and item["comissao_estimada_pct"] == 0.05 and item["autonomia_item"] == "DENTRO_DA_AUTONOMIA",
                      item)
            # negocia abaixo do B2B → precisa de aprovação
            campo = page.query_selector(f'tr[data-item-id="{item["item_id"]}"] [data-desconto-input]')
            campo.fill("60.00"); campo.press("Enter"); page.wait_for_timeout(900)
            p = negociacao(ctx, base, cot_id)
            sit = situacao(ctx, base, cot_id)
            registrar("[VENDEDOR_INTERNO] abaixo do B2B no personalizado → precisa de aprovação",
                      p["requer_aprovacao"] and sit["precisa_aprovacao"], p["autonomia_status"])
            r = ctx.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/solicitar", form={"justificativa": "cliente âncora"}, max_redirects=0)
            registrar("[VENDEDOR_INTERNO] pediu aprovação", r.status in (200, 303), r.status)
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            achados = [t for t in PROIBIDO_TELA_CALC if t in page.content().lower()]
            registrar("[VENDEDOR_INTERNO] cotação com o personalizado continua sem economia", not achados, achados)
            page.screenshot(path=os.path.join(a.saida, "calculadora_fluxo_vendedora.png"), full_page=True)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    with open(os.path.join(a.saida, "resultado_calculadora.json"), "w", encoding="utf-8") as f:
        json.dump({"passos": resultados, "numeros": numeros}, f, ensure_ascii=False, indent=1)
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} passos ok · saída: {a.saida}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

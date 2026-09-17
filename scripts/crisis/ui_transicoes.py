#!/usr/bin/env python3
"""§19 — Transições de cenário pela TELA (Playwright), como a vendedora faz.

Sobe-se antes um servidor numa cópia (porta 8451). Aqui: login, abrir cotação, ler a tabela de
itens, mudar UMA variável, clicar "Atualizar cenário e recalcular", ler de novo e comparar com
o que o backend diz ser o preço do cenário novo (`/cotacoes/{id}/negociacao`).
"""
import json
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8451")
AUDIT = os.path.expanduser("~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917")
EMAIL, SENHA = "auditor@anara.test", "auditoria-crise-2026-xyz"
resultados = []


def brl(txt):
    m = re.search(r"R\$\s*([\d\.]+),(\d{2})", txt or "")
    return float(m.group(1).replace(".", "") + "." + m.group(2)) if m else None


def ler_tabela(page):
    linhas = {}
    for tr in page.query_selector_all("tr[data-item-id]"):
        iid = int(tr.get_attribute("data-item-id"))
        celulas = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        rec = tr.query_selector("[data-rec]")
        inp = tr.query_selector("[data-preco-input]")
        fixo = tr.query_selector("[data-preco-fixo]")
        total = tr.query_selector("[data-total]")
        linhas[iid] = {
            "recomendado": brl(rec.inner_text()) if rec else None,
            "seu_preco": (float(inp.input_value().replace(",", ".")) if inp and inp.input_value() else (brl(fixo.inner_text()) if fixo else None)),
            "total": brl(total.inner_text()) if total else None,
            "celulas": celulas,
        }
    topo = page.query_selector("#topo-valor")
    return linhas, (brl(topo.inner_text()) if topo else None)


def backend_negociacao(context, cot_id):
    r = context.request.get(f"{BASE}/cotacoes/{cot_id}/negociacao")
    return r.json()


def registrar(rotulo, ok, detalhe=""):
    resultados.append({"rotulo": rotulo, "ok": ok, "detalhe": detalhe})
    print(("  ok  " if ok else "  FALHA ") + rotulo + (f" — {detalhe}" if detalhe else ""))


def transicao(page, context, cot_id, rotulo, acao):
    page.goto(f"{BASE}/cotacoes/{cot_id}")
    page.wait_for_selector("#cotacao")
    antes, topo_antes = ler_tabela(page)
    acao(page)
    # o aviso de "alterações não aplicadas" precisa aparecer antes de salvar
    pendente = page.query_selector("#pendente")
    aviso_visivel = pendente is not None and pendente.is_visible()
    page.click("#btn-salvar")
    page.wait_for_load_state("networkidle")
    url = page.url
    depois, topo_depois = ler_tabela(page)
    back = backend_negociacao(context, cot_id)
    itens_back = {it["item_id"]: it for it in back.get("itens", [])}
    problemas = []
    for iid, d in depois.items():
        b = itens_back.get(iid)
        if not b:
            problemas.append(f"item {iid} sem resposta do backend"); continue
        if d["recomendado"] is not None and abs(d["recomendado"] - float(b["preco_recomendado"] or 0)) > 0.005:
            problemas.append(f"item {iid} recomendado tela {d['recomendado']} ≠ backend {b['preco_recomendado']}")
        if d["seu_preco"] is not None and abs(d["seu_preco"] - float(b["preco_negociado"] or 0)) > 0.005:
            problemas.append(f"item {iid} preço tela {d['seu_preco']} ≠ backend {b['preco_negociado']}")
        if d["total"] is not None and abs(d["total"] - float(b["total_linha"] or 0)) > 0.005:
            problemas.append(f"item {iid} total tela {d['total']} ≠ backend {b['total_linha']}")
    if topo_depois is not None and abs(topo_depois - float(back.get("total_proposta") or 0)) > 0.005:
        problemas.append(f"total topo {topo_depois} ≠ backend {back.get('total_proposta')}")
    mudou = any(antes.get(i, {}).get("recomendado") != depois[i]["recomendado"] or antes.get(i, {}).get("seu_preco") != depois[i]["seu_preco"] for i in depois)
    registrar(rotulo, not problemas, f"url={url.split('?')[-1]} aviso_pendente={aviso_visivel} itens_mudaram={mudou} " + "; ".join(problemas))
    return antes, depois, back


def main():
    cot_id = int(sys.argv[1]) if len(sys.argv) > 1 else 23
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{BASE}/login")
        page.fill("input[name=email]", EMAIL); page.fill("input[name=senha]", SENHA)
        page.click("button[type=submit]"); page.wait_for_load_state("networkidle")
        print("logado em", page.url)

        page.goto(f"{BASE}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
        inicial, topo = ler_tabela(page)
        print(f"cotação {cot_id}: {len(inicial)} itens, total topo {topo}")
        for iid, d in inicial.items():
            print("   ", iid, d["recomendado"], d["seu_preco"], d["total"])

        transicao(page, context, cot_id, "destino → SP", lambda pg: pg.select_option("#c-destino", label="São Paulo"))
        transicao(page, context, cot_id, "contribuinte → NÃO", lambda pg: pg.click("button[data-contribuinte='nao']"))
        transicao(page, context, cot_id, "destino → RJ", lambda pg: pg.select_option("#c-destino", label="Rio de Janeiro"))
        transicao(page, context, cot_id, "pagamento → 30/60/90/120/150", lambda pg: pg.select_option("#c-pagamento", "30/60/90/120/150"))
        transicao(page, context, cot_id, "contribuinte → SIM", lambda pg: pg.click("button[data-contribuinte='sim']"))
        transicao(page, context, cot_id, "destino → MG (contribuinte)", lambda pg: pg.select_option("#c-destino", label="Minas Gerais"))
        transicao(page, context, cot_id, "pagamento → À VISTA", lambda pg: pg.select_option("#c-pagamento", "À VISTA"))
        transicao(page, context, cot_id, "destino → RO (volta)", lambda pg: pg.select_option("#c-destino", label="Rondônia"))

        # o caminho do bug de 17/09: editar QUANTIDADE pela tela e negociar um preço, depois mudar o cenário
        page.goto(f"{BASE}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
        linhas = page.query_selector_all("tr[data-item-id]")
        alvo = next((tr for tr in linhas if tr.get_attribute("data-travado") != "1"), None)
        if alvo is not None:
            q = alvo.query_selector("[data-qtd-input]"); q.fill("17"); q.press("Enter"); page.wait_for_timeout(800)
            pr = alvo.query_selector("[data-preco-input]")
            atual = float(pr.input_value()); pr.fill(f"{atual * 0.95:.2f}"); pr.press("Enter"); page.wait_for_timeout(1000)
            registrar("editar quantidade e negociar preço pela tela", True, f"qtd=17 preço {atual:.2f}→{atual*0.95:.2f}")
        antes, depois, back = transicao(page, context, cot_id, "após quantidade+negociação: destino → RJ não contribuinte",
                                        lambda pg: (pg.select_option("#c-destino", label="Rio de Janeiro"), pg.click("button[data-contribuinte='nao']")))
        # todo item não travado tem seu preço == recomendado do cenário novo (nenhum preço de outro cenário sobrou)
        sobras = [it["item_id"] for it in back.get("itens", []) if not it.get("preco_travado") and it.get("preco_recomendado")
                  and abs(float(it["preco_negociado"]) - float(it["preco_recomendado"])) > 0.005]
        registrar("nenhum preço negociado de outro cenário sobreviveu", not sobras, f"itens com preço ≠ recomendado: {sobras}")

        # o que acontece se mudar o destino e NÃO salvar: PDF e negociação
        page.goto(f"{BASE}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
        page.select_option("#c-destino", label="São Paulo")
        page.wait_for_timeout(200)
        pendente = page.query_selector("#pendente")
        saidas = page.query_selector_all(".acao-de-saida")
        escondidas = all("hidden-soft" in (s.get_attribute("class") or "") for s in saidas) if saidas else None
        registrar("mudança não salva: aviso visível e saídas escondidas", bool(pendente and pendente.is_visible()) and bool(escondidas), f"saidas={len(saidas)} escondidas={escondidas}")
        page.screenshot(path=os.path.join(AUDIT, "ui_cenario_pendente.png"), full_page=True)
        browser.close()
    with open(os.path.join(AUDIT, "ui_transicoes_resultado.json"), "w") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados)-len(falhas)} ok · {len(falhas)} falhas")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

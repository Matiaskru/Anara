#!/usr/bin/env python3
"""Admin → Produtos e custos pela TELA (Playwright): achar o SKU travado e destravá-lo.

    python3 scripts/governanca_2026_09_22/e2e_governanca_playwright.py --db /caminho/copia.db [--porta 8459]

Roteiro (OWNER): abre a governança, busca BR-001, lê a situação e o motivo, registra o peso com
fonte/documento/motivo pela gaveta, confere que o SKU vira CONFIRMADO e então adiciona o mesmo
produto a uma cotação NOVA — sem "Revisão necessária" — e gera o PDF rascunho.

Depois, as recusas que importam: confirmar referência de um SKU com premissa faltando é negado
com o motivo; um SKU sem custo continua A_COTAR e bloqueia a emissão. E a vendedora não vê a
tela nem os endpoints (403).
"""
import argparse
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21.e2e_playwright import (  # noqa: E402
    DONA, VEND, login, negociacao, registrar, resultados, semear_usuarios, situacao, subir_servidor,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--porta", type=int, default=8459)
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "e2e_2026_09_21"))
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    assert os.path.realpath(db) != os.path.realpath(os.path.join(RAIZ, "data", "anara.db")), "nunca contra o banco real"
    os.makedirs(a.saida, exist_ok=True)
    semear_usuarios(db)
    base = f"http://127.0.0.1:{a.porta}"
    proc = subir_servidor(db, a.porta)
    try:
        from playwright.sync_api import sync_playwright
        from sqlmodel import Session, select
        from app.db import criar_engine
        from app.models import Produto
        eng = criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
        with Session(eng) as s:
            alvo = next(p for p in s.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
                        if "BR-001" in (p.exw_cotado_fonte or ""))
            sku, produto_id = alvo.sku_key, alvo.id
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 950})
            page = ctx.new_page()
            login(page, base, DONA[0], DONA[2])
            page.goto(f"{base}/admin"); page.wait_for_load_state("networkidle")
            registrar("[OWNER] Admin oferece 'Produtos e custos'",
                      page.query_selector('a[href="/admin/produtos"]') is not None)
            page.goto(f"{base}/admin/produtos?q=BR-001"); page.wait_for_selector("#tabela-governanca")
            linha = page.query_selector(f'tr[data-produto="{produto_id}"]')
            texto = re.sub(r"\s+", " ", linha.inner_text())
            # 22/09/2026: o BR-001 deixou de ficar travado por falta de peso — ele HERDA o peso
            # logístico do Microfiber Fleece Robe L (mesmo modelo, tamanho e composição) e sai
            # como ESTIMADO, com o aviso de confirmar antes do pedido. A tela precisa mostrar
            # a estimativa e a sua procedência, não um bloqueio.
            registrar("[OWNER] a tela mostra SKU, situação ESTIMADO, peso herdado, EXW e o porquê",
                      "Custo estimado" in texto and "US$ 24" in texto
                      and "0.660 kg" in texto and "ESTIMADO" in texto
                      and "confirmar antes do ped" in texto.lower()
                      and "29/07/2026" in texto.replace("2026-07-29", "29/07/2026"), texto[:200])
            page.screenshot(path=os.path.join(a.saida, "governanca_01_br001_estimado.png"), full_page=True)

            # promover a CONFIRMADO com peso emprestado é recusado: peso de outro SKU não é
            # evidência deste. A recusa é administrativa — cotar e emitir continuam liberados.
            r = ctx.request.post(f"{base}/admin/produtos/{produto_id}/confirmar",
                                 form={"fonte": "conferi", "motivo": "tentativa"}, max_redirects=0)
            registrar("[OWNER] confirmar referência é RECUSADO com peso estimado por analogia",
                      r.status == 400 and "analogia" in r.text().lower()
                      and "peso próprio" in r.text(), r.text()[:160])

            # registrar o peso pela gaveta da tela
            linha.query_selector("button").click()
            page.wait_for_selector("#gov-drawer.aberto")
            page.fill('#gov-campos input[name="peso_kg"]', "1.85")
            page.fill('#gov-campos input[name="fonte"]', "e-mail KTC 22/09/2026")
            page.fill('#gov-campos input[name="documento"]', "KTC e-mail 22/09")
            page.fill("#gov-motivo", "peso do roupão confirmado pela fábrica")
            page.click('#gov-form button.btn-copper')
            # a tela recarrega sozinha depois do toast; a URL já é esta, então espera-se o
            # conteúdo mudar, não a navegação
            page.wait_for_function(
                f"!document.querySelector('tr[data-produto=\"{produto_id}\"]')"
                f" || document.querySelector('tr[data-produto=\"{produto_id}\"]').innerText.includes('1.850')",
                timeout=20000)
            page.wait_for_selector("#tabela-governanca")
            texto = re.sub(r"\s+", " ", page.query_selector(f'tr[data-produto="{produto_id}"]').inner_text())
            registrar("[OWNER] depois do peso próprio, o SKU fica pronto para cotar",
                      "1.850 kg" in texto and "analogia" not in texto.lower(), texto[:160])
            page.screenshot(path=os.path.join(a.saida, "governanca_02_br001_liberado.png"), full_page=True)

            # e agora a confirmação passa: o peso é do próprio SKU, documentado
            r = ctx.request.post(f"{base}/admin/produtos/{produto_id}/confirmar",
                                 form={"fonte": "cotação KTC conferida em 22/09/2026",
                                       "motivo": "peso próprio registrado"}, max_redirects=0)
            registrar("[OWNER] com peso próprio, confirmar referência é PERMITIDO",
                      r.status in (200, 303), f"status={r.status} · {r.text()[:120]}")

            # cotação NOVA com o mesmo produto: sem revisão, com PDF
            r = ctx.request.post(f"{base}/clientes", form={"nome": "Hotel Governança", "cnpj_cpf": "99.888.777/0001-66", "cidade_uf": "São Paulo"}, max_redirects=0)
            cliente_id = int(re.search(r"/clientes/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/vendas", form={"cliente_id": str(cliente_id), "titulo": "Roupões"}, max_redirects=0)
            venda_id = int(re.search(r"/vendas/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/cotacoes", form={"cliente_id": str(cliente_id), "condicao_pagamento": "30",
                                                             "estado_destino": "São Paulo", "contribuinte_icms": "nao",
                                                             "freight_type": "FOB", "oportunidade_id": str(venda_id)}, max_redirects=0)
            cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers["location"]).group(1))
            ctx.request.post(f"{base}/cotacoes/{cot_id}/itens", form={"produto_id": str(produto_id), "quantidade": "10", "modo": "margem"}, max_redirects=0)
            p = negociacao(ctx, base, cot_id)
            sit = situacao(ctx, base, cot_id)
            registrar("[OWNER] BR-001 entra na cotação com preço e sem blocker de custo",
                      p["itens"][0]["preco_b2b"] > 0
                      and not [b for b in sit["blockers"] if "CUSTO" in b["codigo"]],
                      [b["codigo"] for b in sit["blockers"]])
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            registrar("[OWNER] a cotação não mostra 'Revisão necessária' para este item",
                      "Revisão necessária" not in page.inner_text("#tabela-itens"))
            resp = ctx.request.get(f"{base}/cotacoes/{cot_id}/pdf?rascunho=1")
            registrar("[OWNER] PDF de rascunho sai", resp.status == 200 and len(resp.body()) > 10000,
                      f"{resp.status} · {len(resp.body())} bytes")

            # SKU sem custo continua bloqueando
            with Session(eng) as s:
                sem = next((p for p in s.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
                            if not p.custo_unitario and not p.exw_cotado_usd), None)
            if sem is not None:
                ctx.request.post(f"{base}/cotacoes/{cot_id}/itens", form={"produto_id": str(sem.id), "quantidade": "1", "modo": "margem"}, max_redirects=0)
                sit = situacao(ctx, base, cot_id)
                registrar("[OWNER] SKU realmente sem custo continua bloqueando a emissão",
                          not sit["pode_emitir"] and any("CUSTO" in b["codigo"] for b in sit["blockers"]),
                          [b["codigo"] for b in sit["blockers"]])

            # vendedora não entra
            ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
            pv = ctx2.new_page(); login(pv, base, VEND[0], VEND[2])
            r = ctx2.request.get(f"{base}/admin/produtos", max_redirects=0)
            r2 = ctx2.request.post(f"{base}/admin/produtos/{produto_id}/peso",
                                   form={"peso_kg": "9", "fonte": "x", "motivo": "y"}, max_redirects=0)
            registrar("[VENDEDORA] governança de custo → 403 na tela e no endpoint",
                      r.status == 403 and r2.status == 403, f"{r.status}/{r2.status}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    with open(os.path.join(a.saida, "resultado_governanca.json"), "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} passos ok · saída: {a.saida}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

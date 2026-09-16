#!/usr/bin/env python3
"""Inspeção visual da Fase 3C com Playwright/Chromium — screenshots das telas redesenhadas.

    python3 scripts/visual_3c.py --base http://127.0.0.1:8431 --saida /tmp/visual-3c

Pressupõe o servidor de demonstração de `scripts/demo_3c.py` no ar (usuários `dona@anara.demo`
e `vendedora@anara.demo`). Nunca aponta para o banco real: o que ele faz é navegar e
fotografar — em desktop (1440×900) e em celular (390×844) — e acusar erro de console e
resposta HTTP ≥ 400 em qualquer página visitada.

Também prova, pelo texto renderizado, que a vendedora não recebe economia interna em
nenhuma das telas fotografadas.
"""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

DONA = ("dona@anara.demo", "demo-dona-2026")
VENDEDORA = ("vendedora@anara.demo", "demo-vend-2026")

TELAS_VENDEDORA = ["/vendas", "/vendas?vista=quadro", "/vendas/3", "/vendas/4", "/clientes",
                   "/clientes/2", "/cotacoes", "/cotacoes/23", "/produtos"]
TELAS_ADMIN = ["/", "/?periodo=mes", "/cotacoes/23", "/aprovacoes", "/admin", "/relatorios"]
TELAS_MOBILE = ["/vendas", "/vendas/3", "/clientes/2", "/cotacoes/23"]

#: "piso" sozinho não entra: "Toalha de piso" é produto de catálogo, não economia interna.
ECONOMIA = ("custo", "cnet", "exw", "lucro", "piso de margem", "abaixo do piso", "markup",
            "margem", "memória do preço")


def entrar(page, base, usuario):
    page.goto(f"{base}/login")
    page.fill('input[name="email"]', usuario[0])
    page.fill('input[name="senha"]', usuario[1])
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8431")
    ap.add_argument("--saida", default="/tmp/visual-3c")
    args = ap.parse_args()
    os.makedirs(args.saida, exist_ok=True)
    problemas = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def fotografar(contexto, telas, prefixo, usuario, verificar_economia):
            page = contexto.new_page()
            erros_console = []
            page.on("console", lambda m: erros_console.append(m.text) if m.type == "error" else None)
            respostas = []
            page.on("response", lambda r: respostas.append((r.url, r.status)))
            entrar(page, args.base, usuario)
            for tela in telas:
                page.goto(args.base + tela)
                page.wait_for_load_state("networkidle")
                nome = prefixo + "_" + (tela.strip("/").replace("/", "_").replace("?", "_").replace("=", "-") or "home") + ".png"
                page.screenshot(path=os.path.join(args.saida, nome), full_page=True)
                if verificar_economia:
                    texto = page.inner_text("body").lower()
                    achados = [t for t in ECONOMIA if t in texto]
                    if achados:
                        problemas.append(f"{prefixo} {tela}: vendedora leu {achados}")
                print(f"  {nome}")
            for url, status in respostas:
                if status >= 400 and "/favicon" not in url:
                    problemas.append(f"{prefixo}: {url} → {status}")
            for e in erros_console:
                problemas.append(f"{prefixo}: console: {e[:120]}")
            page.close()

        desktop = browser.new_context(viewport={"width": 1440, "height": 900})
        print("vendedora · desktop")
        fotografar(desktop, TELAS_VENDEDORA, "vendedora", VENDEDORA, True)
        desktop.close()

        desktop = browser.new_context(viewport={"width": 1440, "height": 900})
        print("dona · desktop")
        fotografar(desktop, TELAS_ADMIN, "admin", DONA, False)
        desktop.close()

        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True,
                                     device_scale_factor=2)
        print("vendedora · celular")
        fotografar(mobile, TELAS_MOBILE, "mobile", VENDEDORA, True)
        # no celular: trocar status e registrar atualização precisam funcionar
        page = mobile.new_page()
        entrar(page, args.base, VENDEDORA)
        page.goto(args.base + "/vendas/1")
        page.wait_for_load_state("networkidle")
        page.fill("#at-rapido", "Teste no celular: cliente confirmou a visita.")
        page.click(".quick button.btn-copper")
        page.wait_for_timeout(900)
        if "cliente confirmou a visita" not in page.inner_text("body"):
            problemas.append("mobile: atualização rápida não entrou na timeline")
        page.screenshot(path=os.path.join(args.saida, "mobile_venda_atualizada.png"), full_page=True)
        page.close()
        mobile.close()
        browser.close()

    if problemas:
        print("\nPROBLEMAS:")
        for pr in problemas:
            print(" -", pr)
        sys.exit(1)
    print(f"\nOK — screenshots em {args.saida}")


if __name__ == "__main__":
    main()

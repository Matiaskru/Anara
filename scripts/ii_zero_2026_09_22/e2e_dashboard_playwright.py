#!/usr/bin/env python3
"""Dashboard OWNER/ADMIN pela TELA (Playwright) depois da retirada do I.I.: a mesma venda,
antes e depois, com os KPIs lidos do endpoint real e conferidos contra o serviço.

    python3 scripts/ii_zero_2026_09_22/e2e_dashboard_playwright.py --db /caminho/copia.db [--porta 8457]

Roteiro (na cópia): cria cliente → venda → cotação KTC (item no B2B comercial) → emite → marca
GANHA. Lê o dashboard (OWNER e ADMIN): Valor vendido, Lucro, Margem agregada, Ticket médio,
Comissão. Em seguida simula a mesma venda com a economia ANTIGA (custo com I.I. embutido = base
comercial, mesmo preço) diretamente no item da cotação vencedora, lê de novo, e compara:
vendido/ticket/comissão IGUAIS; lucro e margem MENORES na economia antiga (ou seja, maiores agora).
Também confere que a tela mostra o mesmo número que `metrics_service.dashboard_admin`.
"""
import argparse
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21.e2e_playwright import (  # noqa: E402
    ADMIN, DONA, brl, login, negociacao, registrar, resultados, semear_usuarios, situacao, subir_servidor,
)


def kpis(page):
    out = {}
    for card in page.query_selector_all(".kpi"):
        # text_content: o CSS deixa o rótulo em caixa alta, o texto do template não
        label = card.query_selector(".label").text_content().strip()
        valor = card.query_selector(".value").text_content().strip()
        out[label] = valor
    return out


def pct(txt):
    m = re.search(r"([\d,]+)\s*%", txt or "")
    return float(m.group(1).replace(",", ".")) / 100 if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--porta", type=int, default=8457)
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
        from app import metrics_service as mx
        from app import pricing_service as ps
        from app.db import criar_engine
        from app.models import Cotacao, CotacaoItem, Oportunidade, Produto
        from app.pricing_engine import calcular_por_preco
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page(); login(page, base, DONA[0], DONA[2])
            r = ctx.request.post(f"{base}/clientes", form={"nome": "Hotel Dashboard II", "cnpj_cpf": "66.777.888/0001-99", "cidade_uf": "São Paulo"}, max_redirects=0)
            cliente_id = int(re.search(r"/clientes/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/vendas", form={"cliente_id": str(cliente_id), "titulo": "Venda dashboard II"}, max_redirects=0)
            venda_id = int(re.search(r"/vendas/(\d+)", r.headers["location"]).group(1))
            r = ctx.request.post(f"{base}/cotacoes", form={"cliente_id": str(cliente_id), "condicao_pagamento": "30", "estado_destino": "São Paulo",
                                                             "contribuinte_icms": "nao", "freight_type": "FOB", "oportunidade_id": str(venda_id)}, max_redirects=0)
            cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers["location"]).group(1))
            eng_ro = criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
            with Session(eng_ro) as s:
                prod = next(p for p in s.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
                            if p.thread_count == 300 and "190x250" in (p.sku_key or "") and p.preco_base)
            ctx.request.post(f"{base}/cotacoes/{cot_id}/itens", form={"produto_id": str(prod.id), "quantidade": "10", "modo": "margem"}, max_redirects=0)
            ctx.request.post(f"{base}/cotacoes/{cot_id}/emitir", max_redirects=0)
            r = ctx.request.post(f"{base}/vendas/{venda_id}/vendido", form={"cotacao_id": str(cot_id)}, max_redirects=0)
            registrar("venda marcada como GANHA (cotação KTC emitida)", r.status in (200, 303), r.status)
            with Session(eng_ro) as s:
                op = s.get(Oportunidade, venda_id)
                registrar("venda aponta para a cotação vencedora", op.cotacao_vencedora_id == cot_id, (op.status, op.cotacao_vencedora_id))
                it = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot_id)).first()
                registrar("item vencedor: custo real < base comercial; margem realizada > alvo",
                          it.base_comercial_precificacao > it.custo_unitario and it.margem_liquida > it.margem_padrao_pct,
                          (it.custo_unitario, it.base_comercial_precificacao, it.margem_liquida, it.margem_padrao_pct))
                custo_real, base_com, lucro_real, margem_real = it.custo_unitario, it.base_comercial_precificacao, it.lucro, it.margem_liquida

            def ler(perfil, cred, sufixo):
                c2 = browser.new_context(viewport={"width": 1440, "height": 900}); pg = c2.new_page()
                erros = []
                login(pg, base, cred[0], cred[2])
                # a tela de login serve 404 para as fontes licenciadas (só autenticado as recebe) —
                # o que interessa é o console do dashboard já autenticado
                pg.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
                pg.goto(f"{base}/dashboard?periodo=12m&cliente_id={cliente_id}"); pg.wait_for_load_state("networkidle")
                k = kpis(pg)
                pg.screenshot(path=os.path.join(a.saida, f"dashboard_{perfil}_{sufixo}.png"), full_page=True)
                registrar(f"[{perfil}] dashboard abre com os KPIs normais ({sufixo})",
                          all(x in k for x in ("Valor vendido", "Lucro", "Margem agregada", "Vendas fechadas", "Ticket médio", "Conversão")) and not erros, (list(k)[:8], erros))
                c2.close()
                return k

            with Session(criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")) as s:
                svc_depois = mx.dashboard_admin(s, mx.periodo_de("12m"), mx.FiltrosDashboard(cliente_id=cliente_id))
            k_owner = ler("OWNER", DONA, "economia_real"); k_admin = ler("ADMIN", ADMIN, "economia_real")
            registrar("tela = serviço (Lucro, Valor vendido, Margem) — OWNER",
                      abs(brl(k_owner["Lucro"]) - svc_depois["lucro"]) < 0.006 and abs(brl(k_owner["Valor vendido"]) - svc_depois["valor_vendido"]) < 0.006
                      and abs(pct(k_owner["Margem agregada"]) - svc_depois["margem_agregada"]) < 0.0006, (k_owner.get("Lucro"), svc_depois["lucro"], k_owner.get("Margem agregada"), svc_depois["margem_agregada"]))
            registrar("ADMIN vê os mesmos KPIs que OWNER", k_admin == k_owner)
            depois = {"vendido": brl(k_owner["Valor vendido"]), "lucro": brl(k_owner["Lucro"]), "margem": pct(k_owner["Margem agregada"]), "ticket": brl(k_owner["Ticket médio"]), "comissao": svc_depois.get("comissao_estimada")}

            # a MESMA venda com a economia ANTIGA (custo com I.I. embutido = base comercial), mesmo preço
            from app.db import criar_engine as _ce
            eng_rw = _ce(f"sqlite:///{db}")
            with Session(eng_rw) as s:
                it = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot_id)).first()
                cot = s.get(Cotacao, cot_id); p = s.get(Produto, it.produto_id)
                regras, _ = ps.regras_da_cotacao(s, cot, p, comissao_formacao_pct=it.comissao_formacao_pct, politica=it.politica_comercial)
                antigo = calcular_por_preco(base_com, it.quantidade, it.preco_negociado, regras)
                it.custo_unitario = base_com; it.lucro = float(antigo.lucro); it.margem_liquida = float(antigo.margem_liquida); it.custo_total = float(antigo.custo_total)
                s.add(it); s.commit()
                svc_antes = mx.dashboard_admin(s, mx.periodo_de("12m"), mx.FiltrosDashboard(cliente_id=cliente_id))
            k_antes = ler("OWNER", DONA, "economia_antiga")
            antes = {"vendido": brl(k_antes["Valor vendido"]), "lucro": brl(k_antes["Lucro"]), "margem": pct(k_antes["Margem agregada"]), "ticket": brl(k_antes["Ticket médio"]), "comissao": svc_antes.get("comissao_estimada")}
            registrar("MESMA venda: vendido, ticket e comissão iguais; lucro e margem maiores com a economia real",
                      antes["vendido"] == depois["vendido"] and antes["ticket"] == depois["ticket"] and abs((antes["comissao"] or 0) - (depois["comissao"] or 0)) < 0.006
                      and depois["lucro"] > antes["lucro"] and depois["margem"] > antes["margem"], {"antes": antes, "depois": depois})
            with open(os.path.join(a.saida, "dashboard_antes_depois.json"), "w", encoding="utf-8") as f:
                json.dump({"antes_economia_antiga": antes, "depois_economia_real": depois, "item": {"custo_real": custo_real, "base_comercial": base_com, "lucro_real": lucro_real, "margem_real": margem_real}}, f, ensure_ascii=False, indent=1)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} passos ok · saída: {a.saida}")
    print(json.dumps({"antes": antes, "depois": depois}, ensure_ascii=False))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

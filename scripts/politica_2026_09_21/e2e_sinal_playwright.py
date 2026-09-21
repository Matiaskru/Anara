#!/usr/bin/env python3
"""Sinal / entrada de ponta a ponta pela TELA (Playwright), como dona, administrativo e
vendedora (interna e comissionada).

    python3 scripts/politica_2026_09_21/e2e_sinal_playwright.py --db /caminho/copia_aplicada.db [--porta 8453]

Para cada perfil: cliente → venda → cotação (SP→SP · não contribuinte · 30 dias · FOB) → item
KTC 300 fios → preço no B2B → marca "Possui sinal / entrada", 30% + saldo 30/60/90 → preço muda
(encargo efetivo 3,36%) → comissão → desconto 55% (abaixo do B2B) → aprovação → sinal 50% →
aprovação cai (encargo 2,40%) → reaprova → emite → snapshot guarda sinal/saldo/texto/encargo →
PDF mostra "50% de sinal + 50% em 30/60/90 dias" e nada de encargo. A vendedora nunca vê
encargo, fórmula ou fator. Cada passo é conferido contra o backend e contra o banco (leitura).
"""
import argparse
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21.e2e_playwright import (  # noqa: E402
    ADMIN, DONA, PROIBIDO_TELA, VEND, conferir_tela_contra_backend, login, negociacao, registrar,
    resultados, semear_usuarios, situacao, subir_servidor, varrer_confidencial,
)

COMISSIONADA = ("comissionada@anara.e2e", "Vendedora Externa E2E", "e2e-com-2026-forte")
PROIBIDO_VENDEDORA_SINAL = ("encargo", "3,36%", "2,40%", "0,0336", "0.0336", "0,024", "0.024",
                            "fator", "encargo_efetivo", "encargo_saldo")


def semear_comissionada(db):
    from sqlmodel import Session, select
    from app.auth import hash_senha
    from app.db import engine
    from app.models import Usuario
    with Session(engine) as s:
        if s.exec(select(Usuario).where(Usuario.email == COMISSIONADA[0])).first() is None:
            s.add(Usuario(email=COMISSIONADA[0], nome=COMISSIONADA[1], senha_hash=hash_senha(COMISSIONADA[2]),
                          papel="VENDEDOR_COMISSIONADO", criado_por="e2e_2026_09_21"))
        s.commit()


def ler_banco(db, cot_id):
    from sqlmodel import Session, select
    from app.db import criar_engine
    from app.models import Cotacao, CotacaoItem, SnapshotEmissao
    eng = criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
    with Session(eng) as s:
        cot = s.get(Cotacao, cot_id)
        itens = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot_id)).all()
        snap = s.exec(select(SnapshotEmissao).where(SnapshotEmissao.cotacao_id == cot_id)).first()
        return ({"percentual_sinal": cot.percentual_sinal, "condicao_pagamento": cot.condicao_pagamento},
                [{"id": i.id, "encargo_pct": i.encargo_pct, "percentual_sinal": i.percentual_sinal,
                  "encargo_saldo_pct": i.encargo_saldo_pct, "preco_negociado": i.preco_negociado,
                  "preco_recomendado": i.preco_recomendado, "desconto_editado_pct": i.desconto_editado_pct}
                 for i in itens],
                snap)


def definir_sinal(page, percentual, saldo):
    """Como a pessoa faz na tela: marca a checkbox, digita o percentual, escolhe o saldo, salva."""
    cb = page.query_selector("#c-possui-sinal")
    if percentual and not cb.is_checked():
        cb.check()
    if not percentual and cb.is_checked():
        cb.uncheck()
    if percentual:
        page.wait_for_selector("#c-sinal-wrap:not([hidden])")
        page.fill("#c-sinal", str(percentual))
    page.select_option("#c-pagamento", saldo)
    texto_vivo = page.inner_text("#c-condicao-texto")
    page.click("#btn-salvar")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#cotacao")
    return texto_vivo


def fluxo(browser, base, db, perfil, credenciais, saida, aprovador):
    rotulo = f"[{perfil}]"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    login(page, base, credenciais[0], credenciais[2])
    registrar(f"{rotulo} login", "/login" not in page.url, page.url)
    cnpj = {"OWNER": "12.345.678/0001-90", "ADMIN": "23.456.789/0001-01", "VENDEDOR_INTERNO": "34.567.890/0001-12",
            "VENDEDOR_COMISSIONADO": "45.678.901/0001-23"}[perfil]
    apelido = {"OWNER": "Dona", "ADMIN": "Adm", "VENDEDOR_INTERNO": "Interna", "VENDEDOR_COMISSIONADO": "Externa"}[perfil]
    r = ctx.request.post(f"{base}/clientes", form={"nome": f"Hotel Sinal {apelido}", "cnpj_cpf": cnpj,
                                                     "cidade_uf": "São Paulo", "telefone": "(11) 4000-0001"}, max_redirects=0)
    m = re.search(r"/clientes/(\d+)", r.headers.get("location", ""))
    if m is None:
        registrar(f"{rotulo} cliente criado", False, f"status {r.status} — use uma cópia limpa do banco")
        ctx.close()
        return None
    cliente_id = int(m.group(1))
    r = ctx.request.post(f"{base}/vendas", form={"cliente_id": str(cliente_id), "titulo": f"Enxoval sinal {perfil}"}, max_redirects=0)
    venda_id = int(re.search(r"/vendas/(\d+)", r.headers.get("location", "")).group(1))
    r = ctx.request.post(f"{base}/cotacoes", form={"cliente_id": str(cliente_id), "condicao_pagamento": "30",
                                                     "estado_destino": "São Paulo", "contribuinte_icms": "nao",
                                                     "freight_type": "FOB", "oportunidade_id": str(venda_id)}, max_redirects=0)
    cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers.get("location", "")).group(1))
    registrar(f"{rotulo} cliente → venda → cotação (30 dias)", bool(cot_id))
    page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
    registrar(f"{rotulo} campo 'Possui sinal / entrada' existe e começa desmarcado",
              page.query_selector("#c-possui-sinal") is not None and not page.is_checked("#c-possui-sinal")
              and page.query_selector("#c-sinal-wrap[hidden]") is not None)
    registrar(f"{rotulo} texto da condição sem sinal = rótulo da condição",
              page.inner_text("#c-condicao-texto").strip() == "30 dias", page.inner_text("#c-condicao-texto"))

    # item KTC 300 fios pela busca
    if page.query_selector("#painel-busca[hidden]") is not None:
        page.click("#btn-abrir-busca")
    page.wait_for_selector("#busca-produto", state="visible")
    page.fill("#busca-produto", "190x250")
    page.select_option("#f-fornecedor", "KTC")
    page.select_option("#f-fios", "300")
    page.wait_for_selector("#resultados-busca .item", timeout=15000)
    page.query_selector_all("#resultados-busca .item")[0].click()
    page.wait_for_selector("#form-add-item:not([hidden])")
    page.fill("#add-qtd", "10")
    page.wait_for_timeout(500)
    page.click("#form-add-item button.btn-copper")
    page.wait_for_function("document.querySelectorAll('tr[data-item-id]').length > 0", timeout=20000)
    page.wait_for_load_state("networkidle"); page.wait_for_selector("#cotacao")
    p0 = conferir_tela_contra_backend(page, ctx, base, cot_id, f"{rotulo} item KTC no B2B: tela = backend")
    it_id = p0["itens"][0]["item_id"]
    b2b0, preco0, com0 = p0["itens"][0]["preco_b2b"], p0["itens"][0]["preco_negociado"], p0["itens"][0]["comissao_estimada_valor"]
    _c, itens_db, _s = ler_banco(db, cot_id)
    registrar(f"{rotulo} sem sinal: encargo do item = 1,6% (30 dias), sinal 0",
              abs(itens_db[0]["encargo_pct"] - 0.016) < 1e-9 and (itens_db[0]["percentual_sinal"] or 0) == 0, itens_db[0])

    # 30% de sinal + 70% em 30/60/90
    texto_vivo = definir_sinal(page, "30", "30/60/90")
    registrar(f"{rotulo} texto ao vivo antes de salvar", texto_vivo.strip() == "30% de sinal + 70% em 30/60/90 dias", texto_vivo)
    registrar(f"{rotulo} banner de cenário atualizado", "Cenário atualizado" in page.content())
    registrar(f"{rotulo} tela mostra 'Saldo' e o texto composto após salvar",
              page.inner_text("#c-pagamento-label").strip().lower() == "saldo"
              and page.inner_text("#c-condicao-texto").strip() == "30% de sinal + 70% em 30/60/90 dias"
              and page.is_checked("#c-possui-sinal") and page.input_value("#c-sinal") == "30",
              (page.inner_text("#c-pagamento-label"), page.inner_text("#c-condicao-texto"), page.input_value("#c-sinal")))
    p1 = conferir_tela_contra_backend(page, ctx, base, cot_id, f"{rotulo} após sinal 30%: tela = backend")
    cab, itens_db, _s = ler_banco(db, cot_id)
    registrar(f"{rotulo} sinal 30% gravado como fração 0,30 com saldo 30/60/90",
              abs(cab["percentual_sinal"] - 0.30) < 1e-9 and cab["condicao_pagamento"] == "30/60/90", cab)
    registrar(f"{rotulo} encargo efetivo do item = 3,36% (0,7 × 4,8%), saldo 4,8% pinado",
              abs(itens_db[0]["encargo_pct"] - 0.0336) < 1e-9 and abs(itens_db[0]["encargo_saldo_pct"] - 0.048) < 1e-9
              and abs(itens_db[0]["percentual_sinal"] - 0.30) < 1e-9, itens_db[0])
    registrar(f"{rotulo} preço mudou (1,6% → 3,36%: B2B sobe) e comissão recalculada",
              p1["itens"][0]["preco_b2b"] > b2b0 and p1["itens"][0]["preco_negociado"] > preco0
              and p1["itens"][0]["comissao_estimada_valor"] != com0
              and abs(p1["itens"][0]["preco_tabela"] - round(p1["itens"][0]["preco_b2b"] * 2, 2)) < 0.006,
              (b2b0, p1["itens"][0]["preco_b2b"], com0, p1["itens"][0]["comissao_estimada_valor"]))
    # oracle: B2B com sinal = B2B calculado pelo motor puro com encargo 3,36% (regras do cenário)
    from decimal import Decimal
    from sqlmodel import Session
    from app import pricing_service as ps
    from app.db import criar_engine
    from app.models import Cotacao, CotacaoItem, Produto
    from app.pricing_engine import preco_b2b
    eng = criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
    with Session(eng) as s:
        cot = s.get(Cotacao, cot_id); it = s.get(CotacaoItem, it_id); prod = s.get(Produto, it.produto_id)
        regras, ctx_p = ps.regras_da_cotacao(s, cot, prod, comissao_formacao_pct=it.comissao_formacao_pct, politica=it.politica_comercial)
        # 22/09/2026: o B2B comercial forma-se sobre a BASE COMERCIAL pinada no item (custo real só na economia)
        base_item = it.base_comercial_precificacao or it.custo_unitario
        esperado = preco_b2b(Decimal(str(base_item)), ps.margem_padrao(s, prod).margem_pct, regras).preco_negociado
    registrar(f"{rotulo} B2B com sinal = primeiro centavo válido com encargo 3,36% (motor puro)",
              regras.encargo_financeiro_pct == Decimal("0.0336") and abs(float(esperado) - p1["itens"][0]["preco_b2b"]) < 1e-9,
              (str(regras.encargo_financeiro_pct), str(esperado), p1["itens"][0]["preco_b2b"]))
    page.screenshot(path=os.path.join(saida, f"sinal_{perfil}_01_30pct.png"), full_page=True)

    # desconto 55% → abaixo do B2B → aprovação
    campo = page.query_selector(f'tr[data-item-id="{it_id}"] [data-desconto-input]')
    campo.fill("55.00"); campo.press("Enter"); page.wait_for_timeout(900)
    p2 = negociacao(ctx, base, cot_id)
    registrar(f"{rotulo} 55% (abaixo do B2B) → requer aprovação; comissão 5%",
              p2["requer_aprovacao"] and p2["itens"][0]["comissao_estimada_pct"] == 0.05, p2["itens"][0])
    ctx.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/solicitar", form={"justificativa": "sinal e2e"}, max_redirects=0)
    sit = situacao(aprovador, base, cot_id)
    aprovador.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/{sit['pedido_pendente_id']}/aprovar",
                           form={"comentario": "ok", "fingerprint_visto": sit["fingerprint"]}, max_redirects=0)
    sit = situacao(aprovador, base, cot_id)
    registrar(f"{rotulo} aprovada com sinal 30%", sit["aprovacao_valida"] and sit["pode_emitir"], sit)

    # sinal 50% → aprovação cai; desconto preservado; encargo 2,40%
    page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
    definir_sinal(page, "50", "30/60/90")
    sit = situacao(ctx, base, cot_id)
    registrar(f"{rotulo} sinal 50% invalidou a aprovação", sit["precisa_aprovacao"] and not sit["aprovacao_valida"] and not sit["pode_emitir"], sit)
    p3 = conferir_tela_contra_backend(page, ctx, base, cot_id, f"{rotulo} após sinal 50%: tela = backend, desconto 55% preservado", {it_id: 0.55})
    cab, itens_db, _s = ler_banco(db, cot_id)
    registrar(f"{rotulo} encargo efetivo 2,40% (0,5 × 4,8%) e B2B menor que com 30%",
              abs(itens_db[0]["encargo_pct"] - 0.024) < 1e-9 and p3["itens"][0]["preco_b2b"] < p1["itens"][0]["preco_b2b"]
              and abs(itens_db[0]["desconto_editado_pct"] - 0.55) < 1e-9, (itens_db[0], p3["itens"][0]["preco_b2b"]))
    registrar(f"{rotulo} texto 50% de sinal + 50% em 30/60/90 dias",
              page.inner_text("#c-condicao-texto").strip() == "50% de sinal + 50% em 30/60/90 dias", page.inner_text("#c-condicao-texto"))

    # sinal inválido pela rota (a tela impede com max=100; o servidor recusa de qualquer modo)
    form_ruim = {"condicao_pagamento": "30/60/90", "possui_sinal": "sim", "percentual_sinal": "150",
                 "estado_destino": "São Paulo", "contribuinte_icms": "nao", "freight_type": "FOB"}
    r = ctx.request.post(f"{base}/cotacoes/{cot_id}/atualizar", form=form_ruim, max_redirects=0)
    cab2, itens_db2, _s = ler_banco(db, cot_id)
    registrar(f"{rotulo} sinal 150% recusado (400) sem alterar nada",
              r.status == 400 and cab2 == cab and itens_db2 == itens_db, (r.status, cab2))
    r = ctx.request.post(f"{base}/cotacoes/{cot_id}/atualizar", form={**form_ruim, "percentual_sinal": "-5"}, max_redirects=0)
    registrar(f"{rotulo} sinal -5% recusado (400)", r.status == 400, r.status)
    r = ctx.request.post(f"{base}/cotacoes/{cot_id}/atualizar", form={**form_ruim, "percentual_sinal": "abc"}, max_redirects=0)
    registrar(f"{rotulo} sinal 'abc' recusado (400)", r.status == 400, r.status)

    # reaprovar e emitir
    ctx.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/solicitar", form={"justificativa": "reaprovar 50%"}, max_redirects=0)
    sit = situacao(aprovador, base, cot_id)
    aprovador.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/{sit['pedido_pendente_id']}/aprovar",
                           form={"comentario": "ok", "fingerprint_visto": sit["fingerprint"]}, max_redirects=0)
    aprovador.request.post(f"{base}/cotacoes/{cot_id}/emitir", max_redirects=0)
    sit = situacao(aprovador, base, cot_id)
    registrar(f"{rotulo} emitida com sinal 50%", sit["status"] == "emitida", sit)
    cab, itens_db, snap = ler_banco(db, cot_id)
    fiscal = json.loads(snap.fiscal_json) if snap else {}
    linha_snap = json.loads(snap.itens_json)[0] if snap else {}
    registrar(f"{rotulo} snapshot guarda sinal, saldo, texto e encargo efetivo",
              abs(fiscal.get("percentual_sinal", -1) - 0.5) < 1e-9 and fiscal.get("condicao_saldo") == "30/60/90"
              and fiscal.get("condicao_pagamento_texto") == "50% de sinal + 50% em 30/60/90 dias"
              and abs(fiscal.get("encargo_efetivo_pct", -1) - 0.024) < 1e-9
              and abs(linha_snap.get("percentual_sinal", -1) - 0.5) < 1e-9 and abs(linha_snap.get("encargo_saldo_pct", -1) - 0.048) < 1e-9,
              fiscal)
    # PDF final
    resp = ctx.request.get(f"{base}/cotacoes/{cot_id}/pdf")
    pdf_path = os.path.join(saida, f"proposta_sinal_{perfil}_{cot_id}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(resp.body())
    import fitz
    texto_pdf = "\n".join(pg.get_text() for pg in fitz.open(pdf_path))
    texto_pdf_plano = " ".join(texto_pdf.split())
    registrar(f"{rotulo} PDF mostra a condição legível com sinal",
              "50% de sinal + 50% em 30/60/90 dias" in texto_pdf_plano, [l for l in texto_pdf.splitlines() if "sinal" in l.lower()][:3])
    registrar(f"{rotulo} PDF sem encargo/tabela/B2B/desconto/comissão",
              not any(t in texto_pdf.lower() for t in ("encargo", "2,40%", "3,36%", "tabela", "b2b", "desconto", "comiss", "custo", "margem")))
    # confidencialidade da tela e do payload
    page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
    html = page.content()
    from app.confidencial import encontrar_confidenciais
    pj = negociacao(ctx, base, cot_id)
    if perfil.startswith("VENDEDOR"):
        registrar(f"{rotulo} payload sem campo confidencial", encontrar_confidenciais(pj) == [], encontrar_confidenciais(pj))
        varrer_confidencial(html, f"{rotulo} HTML emitida sem economia nem jargão")
        achados = [t for t in PROIBIDO_VENDEDORA_SINAL if t in html.lower()]
        registrar(f"{rotulo} HTML da vendedora sem encargo/fórmula/fator", not achados, achados)
        texto_json = json.dumps(pj, ensure_ascii=False).lower()
        registrar(f"{rotulo} JSON da vendedora sem encargo/fórmula", not any(t in texto_json for t in ("encargo", "0.0336", "0.024", "fator")))
        registrar(f"{rotulo} vendedora vê sinal, saldo e texto da condição",
                  'id="c-sinal"' in html and "50% de sinal + 50% em 30/60/90 dias" in html)
    else:
        registrar(f"{rotulo} {perfil} vê a economia e o encargo efetivo no cenário fiscal",
                  "economia" in pj and re.search(r"encargo\s+2,4", html.replace("\xa0", " ")) is not None,
                  re.findall(r"encargo\s+[\d,]+%", html.replace("\xa0", " "))[:2])
    page.screenshot(path=os.path.join(saida, f"sinal_{perfil}_02_emitida.png"), full_page=True)
    ctx.close()
    return cot_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--porta", type=int, default=8453)
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "e2e_2026_09_21"))
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    real = os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
    assert os.path.realpath(db) != real, "nunca contra o banco real"
    os.makedirs(a.saida, exist_ok=True)
    semear_usuarios(db)
    semear_comissionada(db)
    base = f"http://127.0.0.1:{a.porta}"
    proc = subir_servidor(db, a.porta)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx_dona = browser.new_context(viewport={"width": 1440, "height": 900})
            pd = ctx_dona.new_page(); login(pd, base, DONA[0], DONA[2])
            for perfil, cred in (("OWNER", DONA), ("ADMIN", ADMIN), ("VENDEDOR_INTERNO", VEND),
                                 ("VENDEDOR_COMISSIONADO", COMISSIONADA)):
                fluxo(browser, base, db, perfil, cred, a.saida, ctx_dona)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    with open(os.path.join(a.saida, "resultado_sinal.json"), "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} passos ok · saída: {a.saida}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""§44 — cotação realista de ponta a ponta pela TELA (Playwright), com a política de 21/09/2026.

    python3 scripts/politica_2026_09_21/e2e_playwright.py --db /caminho/copia_aplicada.db [--porta 8452]

Sobe um servidor numa CÓPIA do banco (já migrada e com os dados de 21/09 aplicados), cria a
vendedora e a dona de demonstração se não existirem, e executa como a vendedora:

cliente → venda → cotação SP→SP · não contribuinte · 30/60/90 · FOB → KTC 300 fios, toalha,
Daune, Decor → confere B2B/tabela → desconto % diferente em cada item → comissão por item →
30/60 → MG → contribuinte SIM → quantidade → item abaixo do B2B → aprovação (dona) → muda o
cenário → aprovação cai → reaprova → emite → snapshot → PDF → nenhum campo confidencial.

Também tira as capturas de tela do §54 (dona, administrativo, vendedora; desktop e celular).
Cada passo confere a tela CONTRA o backend (`/cotacoes/{id}/negociacao`), nunca contra uma
conta feita aqui. Resultado em JSON no diretório de saída.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

DONA = ("dona@anara.e2e", "Dona E2E", "e2e-dona-2026-forte")
ADMIN = ("adm@anara.e2e", "Administrativo E2E", "e2e-adm-2026-forte")
VEND = ("vendedora@anara.e2e", "Vendedora E2E", "e2e-vend-2026-forte")
PROIBIDO_TELA = ("cnet", "exw", "custo", "margem", "markup", "lucro", "piso", "base comission",
                 "c_max", "fallback", "oracle", "crisis", "politica_comercial", "POLITICA_COMERCIAL",
                 "PRECO_ABAIXO", "MARGEM_ABAIXO", "REVIEW_REQUIRED", "A_COTAR", "ox" + "ford")
resultados = []


def registrar(rotulo, ok, detalhe=""):
    resultados.append({"passo": rotulo, "ok": bool(ok), "detalhe": str(detalhe)[:300]})
    print(("  ok    " if ok else "  FALHA ") + rotulo + (f" — {detalhe}" if detalhe and not ok else ""))


def porta_livre(porta):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def brl(txt):
    m = re.search(r"R\$\s*([\d\.]+),(\d{2})", txt or "")
    return float(m.group(1).replace(".", "") + "." + m.group(2)) if m else None


def semear_usuarios(db):
    os.environ["ANARA_DB_URL"] = f"sqlite:///{db}"
    from sqlmodel import Session, select
    from app.auth import hash_senha
    from app.db import engine
    from app.models import Usuario
    with Session(engine) as s:
        for email, nome, senha, papel, extra in (
                (*DONA, "OWNER", dict(can_manage_users=True, can_approve_quotes=True)),
                (*ADMIN, "ADMIN", dict(can_approve_quotes=True)),
                (*VEND, "VENDEDOR_INTERNO", {})):
            if s.exec(select(Usuario).where(Usuario.email == email)).first() is None:
                s.add(Usuario(email=email, nome=nome, senha_hash=hash_senha(senha), papel=papel,
                              criado_por="e2e_2026_09_21", **extra))
        s.commit()


def subir_servidor(db, porta):
    env = dict(os.environ, ANARA_DB_URL=f"sqlite:///{db}",
               ANARA_SECRET_KEY="e2e-politica-2026-09-21-chave-local-0123456789abcdef")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(porta), "--log-level", "warning"], cwd=RAIZ, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for _ in range(80):
        if not porta_livre(porta):
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise SystemExit("servidor não subiu:\n" + (proc.stderr.read() or "")[-1500:])


def login(page, base, email, senha):
    page.goto(f"{base}/login")
    page.fill("#email", email)
    page.fill("#senha", senha)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def negociacao(context, base, cot_id):
    return context.request.get(f"{base}/cotacoes/{cot_id}/negociacao").json()


def situacao(context, base, cot_id):
    return context.request.get(f"{base}/cotacoes/{cot_id}/situacao").json()


def ler_linha(page, item_id):
    tr = page.query_selector(f'tr[data-item-id="{item_id}"]')
    return {
        "tabela": brl(tr.query_selector("[data-tabela-cel]").inner_text()),
        "b2b": brl(tr.query_selector("[data-rec]").inner_text()),
        "preco": float(tr.query_selector("[data-preco-input]").input_value()),
        "desconto": float(tr.query_selector("[data-desconto-input]").input_value()),
        "comissao": tr.query_selector("[data-comissao-item]").inner_text(),
        "total": brl(tr.query_selector("[data-total]").inner_text()),
    }


def salvar_cabecalho(page, **campos):
    for nome, valor in campos.items():
        if nome == "contribuinte":
            page.click(f'button[data-contribuinte="{valor}"]')
        else:
            page.select_option(f'#form-cabecalho select[name="{nome}"]', valor)
    page.click("#btn-salvar")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#cotacao")


def conferir_tela_contra_backend(page, context, base, cot_id, rotulo, checar_desconto=None):
    p = negociacao(context, base, cot_id)
    ok_tudo = True
    detalhes = []
    for it in p["itens"]:
        linha = ler_linha(page, it["item_id"])
        m = re.search(r"([\d,]+)\s*%", linha["comissao"])
        taxa_tela = float(m.group(1).replace(",", ".")) / 100 if m else None
        ok = (abs(linha["b2b"] - it["preco_b2b"]) < 0.006 and abs(linha["tabela"] - it["preco_tabela"]) < 0.006
              and abs(linha["preco"] - it["preco_negociado"]) < 0.006 and abs(linha["total"] - it["total_linha"]) < 0.006
              and abs(linha["desconto"] / 100 - it["desconto_vs_tabela_pct"]) < 0.0006
              and taxa_tela is not None and abs(taxa_tela - it["comissao_estimada_pct"]) < 0.0001)
        if checar_desconto and it["item_id"] in checar_desconto:
            ok = ok and abs(it["desconto_vs_tabela_pct"] - checar_desconto[it["item_id"]]) < 0.0011
        ok_tudo &= ok
        detalhes.append(f"{it['nome_produto'][:22]}: tela {linha['b2b']}/{linha['tabela']}/{linha['preco']}/{linha['desconto']}% "
                        f"backend {it['preco_b2b']}/{it['preco_tabela']}/{it['preco_negociado']}/{it['desconto_vs_tabela_pct']}")
    registrar(rotulo, ok_tudo, " | ".join(detalhes))
    return p


def varrer_confidencial(texto, rotulo):
    baixo = texto.lower()
    achados = [t for t in PROIBIDO_TELA if t.lower() in baixo]
    # "custo" aparece legitimamente em "custo do frete"? não — a tela da vendedora não fala em custo
    registrar(rotulo, not achados, f"achados: {achados}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--porta", type=int, default=8452)
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "e2e_2026_09_21"))
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    real = os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
    assert os.path.realpath(db) != real, "nunca contra o banco real"
    os.makedirs(a.saida, exist_ok=True)
    semear_usuarios(db)
    base = f"http://127.0.0.1:{a.porta}"
    proc = subir_servidor(db, a.porta)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page = ctx.new_page()
            login(page, base, VEND[0], VEND[2])
            registrar("login vendedora cai em /vendas", page.url.endswith("/vendas"), page.url)

            # 1–2. cliente e venda (formulários reais, via request autenticada)
            r = ctx.request.post(f"{base}/clientes", form={"nome": "Hotel E2E 21/09", "cnpj_cpf": "45.678.901/0001-23",
                                                             "cidade_uf": "São Paulo", "telefone": "(11) 4000-0000"},
                                 max_redirects=0)
            loc = r.headers.get("location", "")
            cliente_id = int(re.search(r"/clientes/(\d+)", loc).group(1)) if "/clientes/" in loc else None
            registrar("cliente criado", cliente_id is not None, loc)
            r = ctx.request.post(f"{base}/vendas", form={"cliente_id": str(cliente_id), "titulo": "Enxoval E2E"},
                                 max_redirects=0)
            venda_id = int(re.search(r"/vendas/(\d+)", r.headers.get("location", "")).group(1))
            registrar("venda criada", bool(venda_id))
            # 3–6. cotação SP→SP · não contribuinte · 30/60/90 · FOB
            r = ctx.request.post(f"{base}/cotacoes", form={"cliente_id": str(cliente_id), "condicao_pagamento": "30/60/90",
                                                             "estado_destino": "São Paulo", "contribuinte_icms": "nao",
                                                             "freight_type": "FOB", "oportunidade_id": str(venda_id)},
                                 max_redirects=0)
            cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers.get("location", "")).group(1))
            registrar("cotação criada", bool(cot_id))

            # 7. adicionar produtos pela busca da tela
            page.goto(f"{base}/cotacoes/{cot_id}")
            page.wait_for_selector("#cotacao")
            html0 = page.content()
            varrer_confidencial(html0, "tela da cotação (vazia) sem economia nem jargão")

            def adicionar(termo, fornecedor="", familia="", fios="", indice=0):
                n_antes = len(page.query_selector_all("tr[data-item-id]"))
                if page.query_selector("#painel-busca[hidden]") is not None:
                    page.click("#btn-abrir-busca")
                page.wait_for_selector("#busca-produto", state="visible")
                page.fill("#busca-produto", termo)
                if fornecedor:
                    page.select_option("#f-fornecedor", fornecedor)
                if familia:
                    page.select_option("#f-familia", familia)
                if fios:
                    page.select_option("#f-fios", fios)
                page.wait_for_selector("#resultados-busca .item", timeout=15000)
                itens = page.query_selector_all("#resultados-busca .item")
                itens[indice].click()
                page.wait_for_selector("#form-add-item:not([hidden])")
                page.fill("#add-qtd", "10")
                page.wait_for_timeout(600)
                previa = page.inner_text("#add-preview")
                page.click("#form-add-item button.btn-copper")
                # a tela recarrega depois de gravar: esperar a linha nova aparecer
                page.wait_for_function(f"document.querySelectorAll('tr[data-item-id]').length > {n_antes}",
                                       timeout=20000)
                page.wait_for_load_state("networkidle")
                page.wait_for_selector("#cotacao")
                return previa

            previa = adicionar("190x250", fornecedor="KTC", fios="300")
            registrar("prévia ao adicionar mostra tabela e B2B", "tabela" in previa and "B2B" in previa, previa)
            adicionar("toalha banho 70x140", fornecedor="KTC")
            adicionar("travesseiro", fornecedor="DAUNE")
            adicionar("peseira", fornecedor="DECOR_TRICOT")
            p = negociacao(ctx, base, cot_id)
            registrar("4 itens na cotação (KTC 300, toalha, Daune, Decor)", len(p["itens"]) == 4,
                      [i["nome_produto"][:30] for i in p["itens"]])
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            varrer_confidencial(page.content(), "tela da cotação (4 itens) sem economia nem jargão")
            # 8. B2B/tabela: todos entram no B2B (50% da tabela), comissão 5%
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "B2B, tabela, preço e desconto da tela = backend")
            registrar("todo item nasce no B2B = 50% da tabela com comissão 5%",
                      all(abs(i["desconto_vs_tabela_pct"] - 0.5) < 0.0011 and i["comissao_estimada_pct"] == 0.05
                          and abs(i["preco_negociado"] - i["preco_b2b"]) < 0.006 for i in p["itens"]))
            registrar("dentro da autonomia no B2B", p["autonomia_status"] == "DENTRO_DA_AUTONOMIA")
            page.screenshot(path=os.path.join(a.saida, "01_vendedora_cotacao_b2b.png"), full_page=True)

            # 9. desconto % diferente em cada item, pela tela
            ids = [i["item_id"] for i in p["itens"]]
            descontos = {ids[0]: 0.05, ids[1]: 0.15, ids[2]: 0.25, ids[3]: 0.35}
            for iid, d in descontos.items():
                campo = page.query_selector(f'tr[data-item-id="{iid}"] [data-desconto-input]')
                campo.fill(f"{d * 100:.2f}")
                campo.press("Enter")
                page.wait_for_timeout(700)
            page.wait_for_load_state("networkidle")
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "descontos digitados gravados e refletidos", descontos)
            # 10. comissão individual por item (faixa do desconto do item)
            esperado = {0.05: 0.09, 0.15: 0.08, 0.25: 0.07, 0.35: 0.06}
            registrar("comissão por item pela faixa (9/8/7/6%)",
                      all(i["comissao_estimada_pct"] == esperado[descontos[i["item_id"]]] for i in p["itens"]),
                      {i["nome_produto"][:18]: i["comissao_estimada_pct"] for i in p["itens"]})
            total_antes = p["total_proposta"]
            precos_antes = {i["item_id"]: i["preco_negociado"] for i in p["itens"]}
            comissao_antes = p["comissao_estimada_valor"]
            page.screenshot(path=os.path.join(a.saida, "02_vendedora_descontos.png"), full_page=True)

            # 11–12. 30/60/90 → 30/60: B2B, tabela e preço mudam; desconto fica; comissão recalcula
            salvar_cabecalho(page, condicao_pagamento="30/60")
            registrar("banner de cenário atualizado", "Cenário atualizado" in page.content())
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "após 30/60: tela = backend e descontos preservados", descontos)
            registrar("30/60: preço absoluto mudou em todos os itens (mais barato)",
                      all(i["preco_negociado"] < precos_antes[i["item_id"]] for i in p["itens"]),
                      {i["nome_produto"][:18]: (precos_antes[i["item_id"]], i["preco_negociado"]) for i in p["itens"]})
            registrar("30/60: comissão recalculada (valor menor, faixas iguais)",
                      p["comissao_estimada_valor"] < comissao_antes
                      and all(i["comissao_estimada_pct"] == esperado[descontos[i["item_id"]]] for i in p["itens"]))
            precos_antes = {i["item_id"]: i["preco_negociado"] for i in p["itens"]}

            # 13–14. destino MG (não contribuinte): B2B do KTC cai? ICMS 18% igual em MG (base 18%) → preço igual p/ KTC; nacional 18%
            salvar_cabecalho(page, estado_destino="Minas Gerais")
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "após MG não contribuinte: tela = backend, descontos preservados", descontos)
            sit = situacao(ctx, base, cot_id)
            registrar("MG não contribuinte resolve (sem blocker fiscal)", not any(b["codigo"].startswith("FISCAL") for b in sit["blockers"]), sit["blockers"])

            # 15–16. contribuinte NÃO → SIM: ICMS 4%/12%, preços caem
            salvar_cabecalho(page, contribuinte="sim")
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "após contribuinte SIM: tela = backend, descontos preservados", descontos)
            registrar("contribuinte: preço caiu em todos os itens", all(i["preco_negociado"] < precos_antes[i["item_id"]] for i in p["itens"]))

            # 17–18. quantidade
            alvo = ids[0]
            campo = page.query_selector(f'tr[data-item-id="{alvo}"] [data-qtd-input]')
            campo.fill("25"); campo.press("Enter"); page.wait_for_timeout(900)
            p = negociacao(ctx, base, cot_id)
            linha = next(i for i in p["itens"] if i["item_id"] == alvo)
            registrar("quantidade 25 mantém preço/desconto e refaz total e comissão",
                      linha["quantidade"] == 25 and abs(linha["total_linha"] - round(linha["preco_negociado"] * 25, 2)) < 0.006
                      and abs(linha["desconto_vs_tabela_pct"] - 0.05) < 0.0011
                      and abs(linha["comissao_estimada_valor"] - round(linha["total_linha"] * 0.96 * 0.09, 2)) < 0.02,
                      linha)

            # 19–20. baixar item abaixo do B2B → exige aprovação
            campo = page.query_selector(f'tr[data-item-id="{ids[3]}"] [data-desconto-input]')
            campo.fill("55.00"); campo.press("Enter"); page.wait_for_timeout(900)
            p = negociacao(ctx, base, cot_id)
            registrar("55% de desconto (abaixo do B2B) → precisa de aprovação",
                      p["requer_aprovacao"] and next(i for i in p["itens"] if i["item_id"] == ids[3])["autonomia_item"] == "REQUER_APROVACAO")
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            painel = page.inner_text("#situacao")
            registrar("painel da vendedora fala em aprovação sem jargão", "aprova" in painel.lower()
                      and not any(t in painel for t in ("PRECO_ABAIXO", "MARGEM", "B2B_")), painel[:200])
            varrer_confidencial(page.content(), "tela com exceção não expõe mecânica")
            page.screenshot(path=os.path.join(a.saida, "03_vendedora_precisa_aprovacao.png"), full_page=True)
            r = ctx.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/solicitar", form={"justificativa": "cliente âncora"}, max_redirects=0)
            registrar("vendedora pediu aprovação", r.status in (200, 303), r.status)

            # 21. dona aprova
            ctx_dona = browser.new_context(viewport={"width": 1440, "height": 900})
            pd = ctx_dona.new_page()
            login(pd, base, DONA[0], DONA[2])
            registrar("login dona cai em /dashboard", pd.url.endswith("/dashboard"), pd.url)
            pd.goto(f"{base}/aprovacoes"); pd.wait_for_load_state("networkidle")
            pd.screenshot(path=os.path.join(a.saida, "04_dona_aprovacoes.png"), full_page=True)
            sit = situacao(ctx_dona, base, cot_id)
            pedido = sit["pedido_pendente_id"]
            r = ctx_dona.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/{pedido}/aprovar",
                                      form={"comentario": "ok", "fingerprint_visto": sit["fingerprint"]}, max_redirects=0)
            sit = situacao(ctx_dona, base, cot_id)
            registrar("aprovada e pronta para emitir", sit["aprovacao_valida"] and sit["pode_emitir"], sit)
            pd.goto(f"{base}/cotacoes/{cot_id}"); pd.wait_for_selector("#cotacao")
            pd.screenshot(path=os.path.join(a.saida, "05_dona_cotacao_economia.png"), full_page=True)
            registrar("dona vê a economia da proposta", "Economia da proposta" in pd.content() and "Base comissionável" in pd.content())

            # 22–23. vendedora muda o cenário de novo → aprovação cai
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            salvar_cabecalho(page, condicao_pagamento="30")
            sit = situacao(ctx, base, cot_id)
            registrar("mudar o cenário invalidou a aprovação", sit["precisa_aprovacao"] and not sit["aprovacao_valida"] and not sit["pode_emitir"], sit)
            p = conferir_tela_contra_backend(page, ctx, base, cot_id, "após 30 dias: descontos preservados (inclusive 55%)",
                                             {**descontos, ids[3]: 0.55})
            # 24–26. reaprovar e emitir (dona), snapshot
            ctx.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/solicitar", form={"justificativa": "reaprovar"}, max_redirects=0)
            sit = situacao(ctx_dona, base, cot_id)
            ctx_dona.request.post(f"{base}/cotacoes/{cot_id}/aprovacao/{sit['pedido_pendente_id']}/aprovar",
                                  form={"comentario": "ok", "fingerprint_visto": sit["fingerprint"]}, max_redirects=0)
            r = ctx_dona.request.post(f"{base}/cotacoes/{cot_id}/emitir", max_redirects=0)
            sit = situacao(ctx_dona, base, cot_id)
            registrar("emitida", sit["status"] == "emitida", sit)
            os.environ["ANARA_DB_URL"] = f"sqlite:///{db}"
            from sqlmodel import Session, select
            from app.db import criar_engine
            from app.models import CotacaoItem, SnapshotEmissao
            eng = criar_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
            with Session(eng) as s:
                snap = s.exec(select(SnapshotEmissao).where(SnapshotEmissao.cotacao_id == cot_id)).first()
                itens_db = {i.id: i for i in s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot_id)).all()}
            itens_snap = json.loads(snap.itens_json)
            registrar("snapshot congela os preços finais e o fingerprint aprovado",
                      snap.aprovacao_fingerprint == snap.fingerprint == sit["fingerprint"]
                      and all(abs(x["preco_negociado"] - itens_db[x["id"]].preco_negociado) < 1e-9 for x in itens_snap))
            texto_snap = (snap.itens_json + snap.totais_json).lower()
            registrar("snapshot guarda tabela/desconto/comissão internamente (para auditoria)", "comissao" in texto_snap)
            # 27–29. PDF final
            resp = ctx.request.get(f"{base}/cotacoes/{cot_id}/pdf")
            pdf_path = os.path.join(a.saida, f"proposta_{cot_id}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(resp.body())
            import fitz
            texto_pdf = "\n".join(pg.get_text() for pg in fitz.open(pdf_path))
            precos_finais = [f"{itens_db[x['id']].preco_negociado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") for x in itens_snap]
            registrar("PDF final traz os preços finais", all(pf in texto_pdf for pf in precos_finais), precos_finais)
            registrar("PDF sem RASCUNHO, sem tabela/B2B/desconto/comissão/custo/fornecedor",
                      "RASCUNHO" not in texto_pdf and not any(t in texto_pdf.lower() for t in
                                                             ("tabela", "b2b", "desconto", "comiss", "custo", "margem", "kazareen", "daune", "decor tricot", "ox" + "ford")))
            # payload da vendedora e HTML final
            from app.confidencial import encontrar_confidenciais
            p = negociacao(ctx, base, cot_id)
            registrar("payload da vendedora sem campo confidencial", encontrar_confidenciais(p) == [], encontrar_confidenciais(p))
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_selector("#cotacao")
            varrer_confidencial(page.content(), "tela emitida sem economia")
            page.screenshot(path=os.path.join(a.saida, "06_vendedora_emitida.png"), full_page=True)

            # §54 — capturas: administrativo, vendedora mobile, produtos, vendas
            ctx_adm = browser.new_context(viewport={"width": 1440, "height": 900})
            pa = ctx_adm.new_page(); login(pa, base, ADMIN[0], ADMIN[2])
            pa.goto(f"{base}/cotacoes/{cot_id}"); pa.wait_for_selector("#cotacao")
            pa.screenshot(path=os.path.join(a.saida, "07_admin_cotacao.png"), full_page=True)
            pa.goto(f"{base}/produtos"); pa.wait_for_load_state("networkidle")
            pa.screenshot(path=os.path.join(a.saida, "08_admin_produtos.png"), full_page=True)
            registrar("catálogo mostra 'B2B de referência' e não a palavra proibida",
                      "B2B de referência" in pa.content() and ("ox" + "ford") not in pa.content().lower())
            pa.goto(f"{base}/calculadora"); pa.wait_for_load_state("networkidle")
            registrar("calculadora oferece 90/10 e '4 abas' sem a palavra proibida",
                      "90/10" in pa.content() and "4 abas" in pa.content() and ("ox" + "ford") not in pa.content().lower())
            pa.screenshot(path=os.path.join(a.saida, "09_admin_calculadora.png"), full_page=True)
            mob = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            pm = mob.new_page(); login(pm, base, VEND[0], VEND[2])
            pm.goto(f"{base}/cotacoes/{cot_id}"); pm.wait_for_selector("#cotacao")
            largura = pm.evaluate("document.documentElement.scrollWidth")
            registrar("celular: sem rolagem horizontal da página", largura <= 400, largura)
            pm.screenshot(path=os.path.join(a.saida, "10_vendedora_celular.png"), full_page=True)
            pm.goto(f"{base}/vendas/{venda_id}"); pm.wait_for_load_state("networkidle")
            pm.screenshot(path=os.path.join(a.saida, "11_vendedora_venda_celular.png"), full_page=True)
            erros_console = []
            page.on("console", lambda m: erros_console.append(m.text) if m.type == "error" else None)
            page.goto(f"{base}/cotacoes/{cot_id}"); page.wait_for_load_state("networkidle")
            registrar("sem erro de console na cotação", not erros_console, erros_console)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    with open(os.path.join(a.saida, "resultado.json"), "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    falhas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} passos ok · saída: {a.saida}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Auditoria de confidencialidade da vendedora — com PROVA, sobre uma cópia do banco.

    python3 scripts/politica_2026_09_21/auditoria_confidencialidade.py --db /caminho/copia.db

Sobe a aplicação (uvicorn, porta local) sobre a cópia, monta uma cotação real (KTC 300 fios, 30% de sinal
+ 30/60/90, 20% de desconto) e uma emitida, e então, autenticada como VENDEDORA (interna e
comissionada), percorre TODAS as rotas GET da aplicação e os endpoints JSON que a tela usa
(negociação, preview, calc, situação, painel, busca, facetas), os arquivos estáticos (JS/CSS),
os atributos `data-*` e os `input hidden` do HTML, e o texto dos PDFs (rascunho e final).

Para cada superfície registra status, tamanho, sha256, os TERMOS proibidos encontrados (com
contexto) e os NÚMEROS confidenciais da própria cotação (custo, lucro, margem, base
comissionável, encargo efetivo e do saldo) em todas as formatações — se o custo unitário é
58,35, procura "58,35", "58.35", "R$ 58,35" etc. E confere o que a vendedora TEM de ver:
tabela, B2B, preço da proposta, desconto %, taxa e valor da comissão dela, total, condição
(com sinal/saldo) e status. OWNER/ADMIN entram só como contraste (veem a economia).

Saída: relatorios/confidencialidade_2026_09_21.{json,md}.
"""
import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

TERMOS_PROIBIDOS = (
    "cnet", "custo", "exw", "margem", "lucro", "markup", "encargo", "piso", "memoria", "memória",
    "waterfall", "credito", "crédito", "base comission", "base_comission", "icms_base",
    "comissao_base", "fator_tabela", "politica_comercial", "premissas_pinadas", "comissao_faixa",
    "comissao_item", "comissao_formacao", "usd", "kazareen", "fornecedor", "fonte do custo",
    "ox" + "ford",
    # 22/09/2026 — economia real × formação comercial
    "protecao", "proteção comercial", "base_comercial", "base comercial", "referencia_comercial",
    "referência comercial", "b2b_economico", "b2b econômico", "ii_pct", "imposto de importação",
)
# termo → padrão (não casa dentro de outra palavra: "custom" não é "custo")
PADROES = {t: re.compile(r"(?<![a-zà-ú])" + re.escape(t) + r"(?![a-zà-ú])", re.I) for t in TERMOS_PROIBIDOS}
#: Ocorrências ESTRUTURAIS aceitas de propósito — cada uma justificada no relatório. Nenhuma
#: delas carrega valor: são nomes de produto, rótulos de condição, ids de elemento, flags
#: booleanas ou código JavaScript que só renderiza o que o servidor manda.
ESTRUTURAIS = (
    ("piso", "toalha de piso"),                 # família Bath Mat — nome do produto
    ("crédito", "cartão de crédito"),           # rótulo da condição CARTÃO (bloqueada)
    ("memoria", "drawer-memoria"),              # gaveta vazia; o botão e o script só saem com economia
    ("memoria", "conteudo-memoria"),
    ("memoria", "fecharmemoria"),
    ("memoria", "abrirmemoria"),
    ("custo", "sem_custo"),                     # flag booleana: "produto sem custo registrado → não cota"
    ("fornecedor", "f-fornecedor"),             # filtro da busca (o PDF não leva fornecedor)
    ("fornecedor", '"fornecedor":'),            # idem, no JSON da busca — nome do fornecedor é filtro de catálogo
    ("fornecedor", "fornecedor local"),         # placeholder de motivo de perda
    ("fornecedor", "fornecedores"),             # facetas da busca
    ("fornecedor", 'id="f-fornecedor"'),
    ("fornecedor", "<label for=\"f-fornecedor\">fornecedor"),
    ("kazareen", "kazareen textile company"),   # nome do fornecedor no filtro/JSON da busca (não no PDF)
    ("kazareen", 'tag-ktc">kazareen'),          # etiqueta do fornecedor no catálogo (a vendedora filtra por ele)
    ("fornecedor", "fornecedor: todos"),        # rótulo do filtro de fornecedor na busca da cotação
    ("fornecedor", 'aria-label="fornecedor"'),
)
ESTATICOS_OK = ("/static/js/", "/static/css/")  # código: renderiza só o que o payload traz
OBRIGATORIO_JSON = ("preco_tabela", "preco_b2b", "preco_negociado", "desconto_vs_tabela_pct",
                    "comissao_estimada_pct", "comissao_estimada_valor", "total_proposta", "autonomia_status")
DONA = ("dona@anara.e2e", "Dona E2E", "e2e-dona-2026-forte")
ADMIN = ("adm@anara.e2e", "Administrativo E2E", "e2e-adm-2026-forte")
VEND = ("vendedora@anara.e2e", "Vendedora E2E", "e2e-vend-2026-forte")
COMISSIONADA = ("comissionada@anara.e2e", "Vendedora Externa E2E", "e2e-com-2026-forte")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def brl(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatos(v) -> set:
    """Todas as formas em que um número confidencial poderia aparecer num texto.

    Fração (< 1): 4 casas e percentual (2 e 1 casas). Dinheiro: 2 casas, BRL, com "R$". As
    formas curtas demais ("0.03") não entram — casariam com qualquer coisa.
    """
    if v is None:
        return set()
    v = float(v)
    if abs(v) < 1:
        out = {repr(v), f"{v:.4f}", f"{v:.4f}".replace(".", ","),
               f"{v * 100:.2f}".replace(".", ",") + "%", f"{v * 100:.1f}".replace(".", ",") + "%", f"{v * 100:.2f}%"}
    else:
        out = {repr(v), f"{v:.2f}", brl(v), f"R$ {brl(v)}", f"R$\xa0{brl(v)}"}
    return {f for f in out if len(f.replace("R$", "").strip()) >= 5 and f not in ("0.0000", "0,0000", "0,00%", "0,0%", "0.00%")}


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Resposta:
    def __init__(self, status, headers, content):
        self.status_code, self.headers, self.content = status, headers, content

    @property
    def text(self):
        try:
            return self.content.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def json(self):
        return json.loads(self.content)


class Http:
    """Cliente HTTP mínimo com cookies, sem seguir redirects (o TestClient exige httpx2)."""

    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.abrir = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _SemRedirect)

    def _req(self, metodo, url, data=None, json_body=None, params=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        corpo, cab = None, {"Accept": "text/html,application/json"}
        if json_body is not None:
            corpo = json.dumps(json_body).encode(); cab["Content-Type"] = "application/json"
        elif data is not None:
            corpo = urllib.parse.urlencode({k: ("" if v is None else v) for k, v in data.items()}).encode()
            cab["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + url, data=corpo, method=metodo, headers=cab)
        try:
            with self.abrir.open(req, timeout=120) as r:
                return Resposta(r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            return Resposta(e.code, dict(e.headers), e.read())

    def get(self, url, params=None):
        return self._req("GET", url, params=params)

    def post(self, url, data=None, json=None):
        return self._req("POST", url, data=data, json_body=json)

    def close(self):
        pass


class Atributos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data_attrs, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for k, v in attrs:
            if k.startswith("data-"):
                self.data_attrs.append((k, (v or "")[:80]))
        if tag == "input" and (d.get("type") or "").lower() == "hidden":
            self.hidden.append((d.get("name") or d.get("id") or "?", (d.get("value") or "")[:80]))


def achados_termos(texto: str, url: str = "") -> tuple:
    """(vazamentos, estruturais): cada ocorrência de termo proibido, separada entre o que é
    vazamento e o que é ocorrência estrutural aceita (com a justificativa)."""
    vaz, estr = [], []
    baixo = texto.lower()
    for t, pad in PADROES.items():
        for m in pad.finditer(texto):
            i = m.start()
            ctx = texto[max(0, i - 50):i + 50].replace("\n", " ")
            trecho = baixo[max(0, i - 40):i + 40]
            just = next((j for tt, j in ESTRUTURAIS if tt == t.lower() and j in trecho), None)
            if just is None and url.startswith(ESTATICOS_OK):
                just = "código estático: renderiza só o que o payload da vendedora traz"
            (estr if just else vaz).append({"termo": t, "contexto": ctx, **({"aceito": just} if just else {})})
            if len(vaz) + len(estr) > 60:
                return vaz, estr
    return vaz, estr


def achados_numeros(texto: str, numeros: dict) -> list:
    out = []
    for nome, valores in numeros.items():
        for f in valores:
            m = re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", texto)
            if m:
                i = m.start()
                out.append({"campo": nome, "forma": f, "contexto": texto[max(0, i - 40):i + 40].replace("\n", " ")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--saida", default=os.path.join(RAIZ, "relatorios", "confidencialidade_2026_09_21"))
    ap.add_argument("--porta", type=int, default=8454)
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    assert os.path.realpath(db) != os.path.realpath(os.path.join(RAIZ, "data", "anara.db")), "nunca contra o banco real"
    os.environ["ANARA_DB_URL"] = f"sqlite:///{db}"
    os.environ.setdefault("ANARA_SECRET_KEY", "auditoria-confidencialidade-2026-09-21-chave-local-0123456789")
    from sqlmodel import Session, select
    from app.auth import hash_senha
    from app.db import engine
    from app.main import app
    from app.models import CotacaoItem, Produto, Usuario
    from scripts.politica_2026_09_21.e2e_playwright import subir_servidor
    proc = subir_servidor(db, a.porta)
    base = f"http://127.0.0.1:{a.porta}"
    try:
        return _auditar(a, db, base, Session, select, hash_senha, engine, app, CotacaoItem, Produto, Usuario)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def _auditar(a, db, base, Session, select, hash_senha, engine, app, CotacaoItem, Produto, Usuario):

    with Session(engine) as s:
        for email, nome, senha, papel, extra in (
                (*DONA, "OWNER", dict(can_manage_users=True, can_approve_quotes=True)),
                (*ADMIN, "ADMIN", dict(can_approve_quotes=True)),
                (*VEND, "VENDEDOR_INTERNO", {}), (*COMISSIONADA, "VENDEDOR_COMISSIONADO", {})):
            if s.exec(select(Usuario).where(Usuario.email == email)).first() is None:
                s.add(Usuario(email=email, nome=nome, senha_hash=hash_senha(senha), papel=papel, criado_por="auditoria"))
        s.commit()
        ktc = next(p for p in s.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
                   if p.thread_count == 300 and "190x250" in (p.sku_key or "") and p.preco_base)

    def cliente(cred):
        c = Http(base)
        r = c.post("/login", data={"email": cred[0], "senha": cred[2]})
        assert r.status_code in (200, 303), r.status_code
        return c

    def montar(c, sufixo, cnpj):
        r = c.post("/clientes", data={"nome": f"Hotel Auditoria {sufixo}", "cnpj_cpf": cnpj, "cidade_uf": "São Paulo"})
        cliente_id = int(re.search(r"/clientes/(\d+)", r.headers["location"]).group(1))
        r = c.post("/vendas", data={"cliente_id": cliente_id, "titulo": f"Auditoria {sufixo}"})
        venda_id = int(re.search(r"/vendas/(\d+)", r.headers["location"]).group(1))
        r = c.post("/cotacoes", data={"cliente_id": cliente_id, "condicao_pagamento": "30/60/90", "estado_destino": "São Paulo",
                                      "contribuinte_icms": "nao", "freight_type": "FOB", "oportunidade_id": venda_id})
        cot_id = int(re.search(r"/cotacoes/(\d+)", r.headers["location"]).group(1))
        c.post(f"/cotacoes/{cot_id}/itens", data={"produto_id": ktc.id, "quantidade": 10, "modo": "margem"})
        c.post(f"/cotacoes/{cot_id}/atualizar", data={"condicao_pagamento": "30/60/90", "possui_sinal": "sim", "percentual_sinal": "30",
                                                     "estado_destino": "São Paulo", "contribuinte_icms": "nao", "freight_type": "FOB"})
        with Session(engine) as s:
            it = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot_id)).first()
            item_id = it.id
        c.post(f"/cotacoes/{cot_id}/negociacao", json={"itens": [{"item_id": item_id, "desconto_pct": "0.20"}]})
        return cliente_id, venda_id, cot_id, item_id

    adm = cliente(ADMIN)
    dona = cliente(DONA)
    cliente_id, venda_id, cot_id, item_id = montar(adm, "Rascunho", "11.222.333/0001-44")
    _c2, venda2, cot_emitida, item2 = montar(adm, "Emitida", "22.333.444/0001-55")
    # cotação separada para os POSTs da calculadora: adicionar item mudaria os totais da
    # cotação de referência e a checagem "o que a vendedora TEM de ver" compararia outra coisa
    _c3, venda3, cot_calc, item3 = montar(adm, "Calculadora", "33.444.555/0001-66")
    r = dona.post(f"/cotacoes/{cot_emitida}/emitir")
    assert r.status_code in (200, 303), (r.status_code, r.text[:300])

    # os números confidenciais DESTA cotação, lidos do banco
    with Session(engine) as s:
        it = s.get(CotacaoItem, item_id)
        numeros = {"custo_unitario": formatos(it.custo_unitario), "custo_total": formatos((it.custo_unitario or 0) * (it.quantidade or 0)),
                   "base_comercial_precificacao": formatos(it.base_comercial_precificacao),
                   "preco_b2b_economico": formatos(it.preco_b2b_economico),
                   "lucro": formatos(it.lucro), "margem_liquida": formatos(it.margem_liquida),
                   "base_comissionavel": formatos(it.base_comissionavel), "encargo_pct": formatos(it.encargo_pct),
                   "encargo_saldo_pct": formatos(it.encargo_saldo_pct), "icms_base_comissao_pct": formatos(it.icms_base_comissao_pct)}
        valores_item = {"preco_tabela": it.preco_tabela, "preco_b2b": it.preco_recomendado, "preco_negociado": it.preco_negociado,
                        "desconto_vs_tabela_pct": it.desconto_vs_tabela_pct, "comissao_pct": it.comissao_pct,
                        "comissao_valor": it.comissao_valor, "faturamento": it.faturamento, "encargo_pct": it.encargo_pct,
                        "percentual_sinal": it.percentual_sinal}
    # "encargo_pct" 0,0336 e o desconto 0,20 não colidem; mas 4,80% (saldo) poderia aparecer como "4,80%"? só se vazasse.

    def rotas():
        def walk(rs, acc):
            for r in rs:
                if hasattr(r, "original_router"):
                    walk(r.original_router.routes, acc)
                elif hasattr(r, "routes"):
                    walk(r.routes, acc)
                elif getattr(r, "methods", None):
                    acc.append((tuple(sorted(r.methods - {"HEAD", "OPTIONS"})), r.path))
        acc = []
        walk(app.routes, acc)
        return sorted(set(acc), key=lambda x: x[1])

    subst = {"{cotacao_id}": str(cot_id), "{item_id}": str(item_id), "{cliente_id}": str(cliente_id),
             "{venda_id}": str(venda_id), "{oportunidade_id}": str(venda_id), "{produto_id}": str(ktc.id),
             "{pedido_id}": "1", "{usuario_id}": "1", "{atividade_id}": "1"}

    superficies = []

    def registrar(perfil, metodo, url, resp, extra=None):
        ctype = resp.headers.get("content-type", "")
        corpo = resp.content or b""
        texto = ""
        if "pdf" in ctype:
            import fitz
            texto = "\n".join(pg.get_text() for pg in fitz.open(stream=corpo, filetype="pdf"))
        elif corpo:
            try:
                texto = corpo.decode("utf-8")
            except UnicodeDecodeError:
                texto = ""
        linha = {"perfil": perfil, "metodo": metodo, "url": url, "status": resp.status_code, "tipo": ctype.split(";")[0],
                 "bytes": len(corpo), "sha256": sha(corpo), "termos": [], "estruturais": [], "numeros": []}
        if resp.status_code == 200 and texto and perfil.startswith("VENDEDOR"):
            linha["termos"], linha["estruturais"] = achados_termos(texto, url)
            linha["numeros"] = achados_numeros(texto, numeros)
        if extra:
            linha.update(extra)
        superficies.append(linha)
        return texto

    for perfil, cred in (("VENDEDOR_INTERNO", VEND), ("VENDEDOR_COMISSIONADO", COMISSIONADA)):
        c = cliente(cred)
        # 1. todas as rotas GET
        for metodos, path in rotas():
            if "GET" not in metodos or path.startswith(("/docs", "/redoc", "/openapi", "/logout")):
                continue
            url = path
            for k, v in subst.items():
                url = url.replace(k, v)
            if "{" in url:
                continue
            for u in ([url] if url != f"/cotacoes/{cot_id}" else [url, f"/cotacoes/{cot_emitida}"]):
                registrar(perfil, "GET", u, c.get(u))
        registrar(perfil, "GET", f"/cotacoes/{cot_emitida}/pdf", c.get(f"/cotacoes/{cot_emitida}/pdf"))
        registrar(perfil, "GET", f"/cotacoes/{cot_emitida}/negociacao", c.get(f"/cotacoes/{cot_emitida}/negociacao"))
        registrar(perfil, "GET", f"/cotacoes/{cot_emitida}/situacao", c.get(f"/cotacoes/{cot_emitida}/situacao"))
        registrar(perfil, "GET", "/produtos/buscar?q=190x250", c.get("/produtos/buscar", params={"q": "190x250"}))
        # 2. endpoints JSON/POST que a tela usa
        registrar(perfil, "POST", f"/cotacoes/{cot_id}/calc", c.post(f"/cotacoes/{cot_id}/calc", data={"produto_id": ktc.id, "quantidade": 10, "modo": "margem", "valor": 0}))
        # calculadora (22/09/2026): a vendedora calcula produto personalizado — a resposta é a
        # comercial. Vai o formulário completo, inclusive as alavancas econômicas, para provar
        # que o servidor as ignora e nada de custo volta.
        material = None
        with Session(engine) as s2:
            from app.models import MaterialPreco
            material = s2.exec(select(MaterialPreco).where(MaterialPreco.plain_or_stripe == "plain")).first()
        form_calc = {"familia": "Flat Sheet", "largura_cm": "240", "comprimento_cm": "260",
                     "material_id": str(material.id), "quantidade": "10", "plain_or_stripe": "plain",
                     "margem_pct": "5", "outros_custos_usd": "10"}
        registrar(perfil, "POST", "/calculadora/calcular", c.post("/calculadora/calcular", data=form_calc))
        registrar(perfil, "POST", "/calculadora/calcular(na cotação)",
                  c.post("/calculadora/calcular", data={**form_calc, "cotacao_id": str(cot_calc)}))
        registrar(perfil, "POST", "/calculadora/salvar(na cotação)",
                  c.post("/calculadora/salvar", data={**form_calc, "cotacao_id": str(cot_calc),
                                                      "largura_cm": "241", "comprimento_cm": "261"}))
        registrar(perfil, "POST", f"/cotacoes/{cot_id}/calc(desconto)", c.post(f"/cotacoes/{cot_id}/calc", data={"produto_id": ktc.id, "quantidade": 10, "modo": "desconto", "valor": 0.25}))
        registrar(perfil, "POST", f"/cotacoes/{cot_id}/negociacao/preview", c.post(f"/cotacoes/{cot_id}/negociacao/preview", json={"itens": [{"item_id": item_id, "desconto_pct": "0.35"}]}))
        registrar(perfil, "POST", f"/cotacoes/{cot_id}/negociacao/preview(abaixo do B2B)", c.post(f"/cotacoes/{cot_id}/negociacao/preview", json={"itens": [{"item_id": item_id, "desconto_pct": "0.60"}]}))
        registrar(perfil, "POST", f"/cotacoes/{cot_id}/negociacao", c.post(f"/cotacoes/{cot_id}/negociacao", json={"itens": [{"item_id": item_id, "desconto_pct": "0.20"}]}))
        # 3. estáticos
        for arq in sorted(os.listdir(os.path.join(RAIZ, "app", "static", "js"))) + ["../css/anara.css"]:
            caminho = f"/static/js/{arq}" if not arq.startswith("..") else "/static/css/anara.css"
            registrar(perfil, "GET", caminho, c.get(caminho))
        # 4. data-* e hidden do HTML da cotação (rascunho e emitida)
        for cid in (cot_id, cot_emitida):
            html = c.get(f"/cotacoes/{cid}").text
            p = Atributos(); p.feed(html)
            nomes = sorted({k for k, _ in p.data_attrs})
            texto_attrs = " ".join(f"{k}={v}" for k, v in p.data_attrs + p.hidden)
            vaz, estr = achados_termos(texto_attrs)
            superficies.append({"perfil": perfil, "metodo": "HTML", "url": f"/cotacoes/{cid} data-* e hidden", "status": 200,
                                "tipo": "atributos", "bytes": len(html), "sha256": sha(html.encode()),
                                "data_attrs": nomes, "hidden_inputs": sorted({k for k, _ in p.hidden}),
                                "termos": vaz, "estruturais": estr, "numeros": achados_numeros(texto_attrs, numeros)})
        # 4b. memória do preço e memória do produto: 403 para a vendedora, sempre
        # a calculadora com cotação também é varrida como página
        registrar(perfil, "GET", f"/calculadora?cotacao_id={cot_calc}", c.get(f"/calculadora?cotacao_id={cot_calc}"))
        for u in (f"/cotacoes/{cot_id}/itens/{item_id}/memoria", f"/produtos/{ktc.id}/memoria",
                  f"/admin/cotacao/{cot_id}/premissas", "/relatorios/economico", "/configuracoes", "/admin/premissas"):
            r = c.get(u)
            superficies.append({"perfil": perfil, "metodo": "403?", "url": u, "status": r.status_code, "tipo": "gate",
                                "bytes": len(r.content), "sha256": sha(r.content), "termos": [] if r.status_code == 403 else [{"termo": "GATE", "contexto": f"esperado 403, veio {r.status_code}"}],
                                "estruturais": [], "numeros": []})
        # 5. o que a vendedora TEM de ver
        html = c.get(f"/cotacoes/{cot_id}").text
        pj = c.get(f"/cotacoes/{cot_id}/negociacao").json()
        it_j = pj["itens"][0]
        deve_ver = {
            "tabela na tela": "data-tabela-cel" in html and brl(valores_item["preco_tabela"]) in html,
            "B2B na tela": "data-rec" in html and brl(valores_item["preco_b2b"]) in html,
            "preço da proposta na tela": "data-preco-input" in html,
            "desconto % na tela": "data-desconto-input" in html,
            "sua comissão (taxa e valor) na tela": "Sua comissão" in html and "data-comissao-item" in html and brl(valores_item["comissao_valor"]) in html,
            "total na tela": "data-total" in html and "Resumo da proposta" in html,
            "condição com sinal e saldo na tela": "30% de sinal + 70% em 30/60/90 dias" in html and 'id="c-sinal"' in html and 'id="c-pagamento"' in html,
            "status na tela": "Rascunho" in html,
            "JSON traz tabela/B2B/proposta/desconto/comissão/total/autonomia": all(k in it_j or k in pj for k in OBRIGATORIO_JSON),
            "calculadora abre e é operável": ("form-calc" in c.get("/calculadora").text
                                              and "Adicionar à cotação" in c.get(f"/calculadora?cotacao_id={cot_calc}").text),
            "calculadora devolve B2B, tabela, comissão e total": (lambda r: all(
                k in r for k in ("preco_b2b", "preco_tabela", "comissao_estimada_valor", "total", "situacao_rotulo")))(
                    c.post("/calculadora/calcular", data=form_calc).json()),
            "JSON: valores batem com o banco": (abs(it_j["preco_tabela"] - valores_item["preco_tabela"]) < 1e-9
                                                and abs(it_j["preco_b2b"] - valores_item["preco_b2b"]) < 1e-9
                                                and abs(it_j["comissao_estimada_valor"] - valores_item["comissao_valor"]) < 1e-9),
        }
        superficies.append({"perfil": perfil, "metodo": "DEVE_VER", "url": f"/cotacoes/{cot_id}", "status": 200, "tipo": "checagem",
                            "bytes": 0, "sha256": "", "termos": [], "estruturais": [], "numeros": [], "deve_ver": deve_ver})
        c.close()

    # contraste: OWNER e ADMIN veem a economia
    contraste = {}
    for perfil, c in (("OWNER", dona), ("ADMIN", adm)):
        pj = c.get(f"/cotacoes/{cot_id}/negociacao").json()
        html = c.get(f"/cotacoes/{cot_id}").text
        contraste[perfil] = {"economia_no_json": "economia" in pj and "base_comissionavel_total" in pj["economia"],
                             "economia_na_tela": "Economia da proposta" in html,
                             "encargo_no_cenario_fiscal": re.search(r"encargo\s+3,(36|4)", html.replace("\xa0", " ")) is not None,
                             "memoria_do_item_200": c.get(f"/cotacoes/{cot_id}/itens/{item_id}/memoria").status_code == 200}
    dona.close(); adm.close()

    # aliases seguros × campos internos (a "contradição aparente")
    from app.confidencial import CAMPOS_CONFIDENCIAIS, CAMPOS_ITEM_COMERCIAL
    aliases = {"preco_b2b": "b2b" in CAMPOS_CONFIDENCIAIS and "preco_b2b" in CAMPOS_ITEM_COMERCIAL,
               "comissao_estimada_pct/valor": "comissao_item" in CAMPOS_CONFIDENCIAIS and "comissao_pct" in CAMPOS_CONFIDENCIAIS,
               "desconto_vs_tabela_pct": "desconto_vs_tabela_pct" in CAMPOS_ITEM_COMERCIAL,
               "preco_tabela": "preco_tabela" in CAMPOS_ITEM_COMERCIAL}

    vazamentos = [s_ for s_ in superficies if s_["perfil"].startswith("VENDEDOR") and (s_["termos"] or s_["numeros"])]
    deve_ver_falhas = [(s_["perfil"], k) for s_ in superficies if s_.get("deve_ver") for k, v in s_["deve_ver"].items() if not v]
    resumo = {"banco": db, "cotacao": cot_id, "cotacao_emitida": cot_emitida, "item": item_id, "produto": ktc.sku_key,
              "valores_item": valores_item, "numeros_procurados": {k: sorted(v) for k, v in numeros.items()},
              "superficies": len(superficies), "vazamentos": len(vazamentos), "deve_ver_falhas": deve_ver_falhas,
              "contraste_owner_admin": contraste, "aliases_seguros": aliases,
              "status_por_perfil": {}}
    for s_ in superficies:
        if s_["metodo"] in ("GET", "POST"):
            resumo["status_por_perfil"].setdefault(s_["perfil"], {}).setdefault(str(s_["status"]), 0)
            resumo["status_por_perfil"][s_["perfil"]][str(s_["status"])] += 1
    os.makedirs(os.path.dirname(a.saida), exist_ok=True)
    with open(a.saida + ".json", "w", encoding="utf-8") as f:
        json.dump({"resumo": resumo, "superficies": superficies}, f, ensure_ascii=False, indent=1, default=str)

    # relatório em Markdown
    linhas = ["# Auditoria de confidencialidade da vendedora — 21/09/2026 (com prova)", "",
              f"Banco (cópia): `{db}` · cotação {cot_id} (rascunho, 30% de sinal + 30/60/90, desconto 20%) · cotação {cot_emitida} (emitida) · produto `{ktc.sku_key}`", "",
              "## Veredito", "",
              f"* Superfícies auditadas: **{len(superficies)}** (rotas GET, endpoints JSON/POST da tela, estáticos, atributos `data-*`/hidden, PDFs) × 2 perfis de vendedora.",
              f"* Vazamentos (termo proibido ou número confidencial da própria cotação em resposta 200 para vendedora): **{len(vazamentos)}**.",
              f"* Itens que a vendedora TEM de ver e não viu: **{len(deve_ver_falhas)}** {deve_ver_falhas or ''}.", "",
              "## Números confidenciais procurados (todas as formatações)", ""]
    for k, v in numeros.items():
        linhas.append(f"* `{k}`: {', '.join(sorted(v)) or '—'}")
    linhas += ["", "## O que a vendedora vê (valores da cotação, do banco)", "",
               "| campo | valor |", "|---|---|"]
    for k, v in valores_item.items():
        linhas.append(f"| {k} | {v} |")
    linhas += ["", "## Superfícies (vendedora)", "", "| perfil | método | URL | status | tipo | bytes | sha256 | vazamentos | estruturais aceitos | números |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s_ in superficies:
        if s_["metodo"] in ("GET", "POST", "HTML", "403?"):
            linhas.append(f"| {s_['perfil']} | {s_['metodo']} | `{s_['url']}` | {s_['status']} | {s_['tipo']} | {s_['bytes']} | {s_['sha256']} | {len(s_['termos'])} | {len(s_.get('estruturais', []))} | {len(s_['numeros'])} |")
    linhas += ["", "## Ocorrências estruturais aceitas (sem valor confidencial) — justificativa", ""]
    vistos = set()
    for s_ in superficies:
        for e in s_.get("estruturais", []):
            chave = (e["termo"], e.get("aceito"))
            if chave in vistos:
                continue
            vistos.add(chave)
            linhas.append(f"* `{e['termo']}` — {e.get('aceito')} (ex.: `{s_['url']}`: …{e['contexto'][:80]}…)")
    if vazamentos:
        linhas += ["", "## Vazamentos encontrados", ""]
        for s_ in vazamentos:
            linhas.append(f"* **{s_['perfil']} {s_['metodo']} `{s_['url']}`**")
            for t in s_["termos"][:10]:
                linhas.append(f"  * termo `{t['termo']}`: …{t['contexto']}…")
            for n in s_["numeros"][:10]:
                linhas.append(f"  * número `{n['campo']}` = `{n['forma']}`: …{n['contexto']}…")
    attrs = next((s_ for s_ in superficies if s_["metodo"] == "HTML"), {})
    linhas += ["", "## Atributos `data-*` e `input hidden` no HTML da cotação (vendedora)", "",
               f"* data-*: {', '.join('`' + x + '`' for x in attrs.get('data_attrs', []))}",
               f"* hidden: {', '.join('`' + x + '`' for x in attrs.get('hidden_inputs', []))}", "",
               "## O que a vendedora TEM de ver", ""]
    for s_ in superficies:
        if s_.get("deve_ver"):
            for k, v in s_["deve_ver"].items():
                linhas.append(f"* [{s_['perfil']}] {'ok' if v else 'FALHA'} — {k}")
    linhas += ["", "## Contraste OWNER/ADMIN (veem a economia)", ""]
    for p_, d in contraste.items():
        linhas.append(f"* {p_}: {d}")
    linhas += ["", "## Contradição aparente resolvida", "",
               "`b2b` e `comissao_item` em `CAMPOS_CONFIDENCIAIS` são os nomes dos DICIONÁRIOS INTERNOS (o `b2b` da memória do preço traz custo, margem-alvo e regras; `comissao_item` traz a decomposição com base comissionável e ICMS deduzido). "
               "A vendedora recebe os ALIASES seguros `preco_b2b`, `preco_tabela`, `desconto_vs_tabela_pct`, `comissao_estimada_pct`, `comissao_estimada_valor` — presentes em `CAMPOS_ITEM_COMERCIAL`/`payload_vendedora`. `encontrar_confidenciais` casa por nome EXATO de chave, então o alias passa e o dicionário interno não: "
               f"{aliases}.", "",
               "## Status por perfil", ""]
    for p_, d in resumo["status_por_perfil"].items():
        linhas.append(f"* {p_}: {d}")
    with open(a.saida + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    print(json.dumps({k: v for k, v in resumo.items() if k not in ("numeros_procurados", "valores_item")}, ensure_ascii=False, default=str, indent=1))
    for s_ in vazamentos[:20]:
        print("VAZAMENTO", s_["perfil"], s_["metodo"], s_["url"], s_["termos"][:3], s_["numeros"][:3])
    return 1 if (vazamentos or deve_ver_falhas) else 0


if __name__ == "__main__":
    sys.exit(main())

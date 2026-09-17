#!/usr/bin/env python3
"""Smoke test PRODUCTION-LIKE — o servidor sobe como no Railway e é atacado como na internet.

Diferença para `scripts/smoke_test.py` (que continua valendo para o dia a dia): aqui o
processo sobe com **a configuração de produção** e o roteiro cobre o que só aparece nela:

* `ANARA_ENV=producao`, `ANARA_SECRET_KEY` gerada, `ANARA_BASE_URL` https, **sem SMTP**;
* o comando de start é o do `Procfile`, lido do arquivo — `0.0.0.0:$PORT`, `--proxy-headers`;
* o cookie de sessão sai `Secure; HttpOnly; SameSite=lax` (o cliente simula o proxy TLS do
  Railway mandando `X-Forwarded-Proto: https` e devolvendo o cookie à mão — um `CookieJar`
  descartaria um cookie Secure recebido por http, e é isso que se quer provar);
* **OWNER**: login → `/dashboard`, economia visível, fluxo comercial completo, PDF rascunho
  e final com o texto extraído e varrido;
* **SELLER**: login → `/vendas`; acesso DIRETO às URLs proibidas responde 403; HTML e JSON
  sem margem/lucro/custo/CNET/piso; a própria comissão aparece;
* "Esqueci minha senha" responde a mesma frase para e-mail existente e inexistente;
* `/static` público, `/static/fonts` só autenticado;
* o log do servidor não contém a SECRET_KEY nem senha alguma.

**Nunca toca no banco de produção.** SQLite: cópia temporária. `--postgres URL`: aplica
`alembic upgrade head` nesse Postgres (cujo nome PRECISA conter `teste`, `test` ou `smoke`),
carrega nele a cópia do SQLite com `scripts/migrar_sqlite_para_postgres.py` — o ensaio do
cutover — e sobe o servidor a partir dele.

Uso:
    python3 scripts/smoke_producao.py
    python3 scripts/smoke_producao.py --postgres postgresql://anara@127.0.0.1:55432/anara_smoke
    python3 scripts/smoke_producao.py --manter
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts"))

import smoke_test as base  # noqa: E402  — reaproveita Resultado, fluxos e preparação

from app.db import normalizar_url, url_segura  # noqa: E402

BASE_URL_PUBLICA = "https://smoke.anara.test"
ECONOMIA_HTML = ("margem", "lucro", "cnet", "exw", "markup", "custo net", "custo unit")
ECONOMIA_JSON = ("margem", "lucro", "custo", "cnet", "exw", "markup", "piso", "commission_max")
PDF_PROIBIDO = ("margem", "custo", "lucro", "markup", "comiss", "a_cotar", "review_required",
                "cnet", "exw", "piso", "fornecedor")
URLS_PROIBIDAS_SELLER = [
    "/admin", "/admin/usuarios", "/admin/trilha", "/configuracoes",
    "/calculadora", "/importar", "/relatorios/economico", "/saude", "/health/detalhe",
    "/aprovacoes", "/produtos/1/memoria",
]


class ClienteProducao(base.Cliente):
    """Cookie à mão: prova o `Secure` sem precisar de TLS de verdade."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.cookie = None
        self.ultimo_set_cookie = ""
        self.opener = urllib.request.build_opener(base.SemRedirect())

    def _req(self, caminho, dados=None, metodo=None, accept="text/html,application/json"):
        corpo = urllib.parse.urlencode(dados).encode() if dados is not None else None
        req = urllib.request.Request(self.base + caminho, data=corpo, method=metodo)
        req.add_header("Accept", accept)
        req.add_header("X-Forwarded-Proto", "https")      # o proxy TLS do Railway faz isso
        if corpo is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if self.cookie:
            req.add_header("Cookie", f"anara_session={self.cookie}")
        return req

    def _guardar_cookie(self, headers):
        sc = headers.get("set-cookie") or headers.get("Set-Cookie") or ""
        if "anara_session=" in sc:
            self.ultimo_set_cookie = sc
            valor = sc.split("anara_session=", 1)[1].split(";", 1)[0]
            self.cookie = valor or None

    def pedir(self, caminho, dados=None, metodo=None):
        try:
            with self.opener.open(self._req(caminho, dados, metodo), timeout=30) as resp:
                headers = dict(resp.headers)
                self._guardar_cookie(headers)
                return resp.status, resp.read().decode("utf-8", "replace"), headers
        except urllib.error.HTTPError as erro:
            headers = dict(erro.headers)
            self._guardar_cookie(headers)
            return erro.code, erro.read().decode("utf-8", "replace"), headers
        except Exception as erro:                       # noqa: BLE001
            return 0, f"{type(erro).__name__}: {erro}", {}

    def pedir_bytes(self, caminho):
        try:
            with self.opener.open(self._req(caminho, accept="application/pdf"), timeout=60) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read(), dict(erro.headers)
        except Exception as erro:                       # noqa: BLE001
            return 0, str(erro).encode(), {}


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------
def comando_do_procfile() -> list:
    """O comando `web:` do Procfile, com `${PORT:-8420}` expandido pelo shell — é o próprio
    arquivo que precisa funcionar, não uma cópia dele aqui."""
    for linha in open(os.path.join(RAIZ, "Procfile"), encoding="utf-8"):
        if linha.startswith("web:"):
            return ["sh", "-c", linha[len("web:"):].strip()]
    raise SystemExit("Procfile sem linha web:")


def subir_servidor_producao(url_banco: str, porta: int, secret: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANARA_MAIL_")}
    env.update(ANARA_DB_URL=url_banco, ANARA_SECRET_KEY=secret, ANARA_ENV="producao",
               ANARA_BASE_URL=BASE_URL_PUBLICA, PORT=str(porta), PYTHONPATH=RAIZ,
               PATH=os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", ""))
    return subprocess.Popen(comando_do_procfile(), cwd=RAIZ, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def criar_usuario(url: str, email: str, senha: str, papel: str, nome: str, r: base.Resultado) -> bool:
    env = dict(os.environ, ANARA_DB_URL=url, ANARA_SENHA_BOOTSTRAP=senha)
    args = [sys.executable, "scripts/criar_usuario.py", "--email", email, "--nome", nome,
            "--papel", papel]
    if papel == "OWNER":
        args.append("--gerencia-usuarios")
    proc = subprocess.run(args, cwd=RAIZ, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        r.falhou(f"criar {papel}", proc.stderr.strip()[-300:])
        return False
    r.passou(f"{papel} criado pelo script de bootstrap", email)
    return True


def preparar_postgres(url_pg: str, copia_sqlite: str, r: base.Resultado) -> bool:
    """`alembic upgrade head` no Postgres de teste e carga da cópia do SQLite nele."""
    from sqlalchemy.engine import make_url
    nome = make_url(url_pg).database or ""
    if not any(p in nome.lower() for p in ("teste", "test", "smoke")):
        r.falhou("Postgres de teste", f"o banco {nome!r} não tem 'teste'/'test'/'smoke' no nome — "
                 "recusado por segurança")
        return False
    if not base.migrar(url_pg, r):
        return False
    proc = subprocess.run(
        [sys.executable, "scripts/migrar_sqlite_para_postgres.py", "--origem", copia_sqlite,
         "--destino", url_pg, "--substituir-destino", nome], cwd=RAIZ,
        capture_output=True, text=True)
    ultima = (proc.stdout.strip().splitlines() or [""])[-1]
    if proc.returncode != 0:
        r.falhou("migrador SQLite → Postgres", (proc.stdout + proc.stderr).strip()[-400:])
        return False
    r.passou("dados carregados no Postgres pelo migrador", ultima)
    return True


# ---------------------------------------------------------------------------
# Verificações
# ---------------------------------------------------------------------------
def visivel(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html).lower()


def chaves(obj, acc=None):
    acc = acc if acc is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(str(k).lower())
            chaves(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            chaves(v, acc)
    return acc


def texto_do_pdf(conteudo: bytes) -> str:
    try:
        import io
        import pdfplumber
    except ImportError:
        return ""
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def conferir_login(cliente: ClienteProducao, email: str, senha: str, destino: str,
                   papel: str, r: base.Resultado) -> bool:
    status, _c, headers = cliente.pedir("/login", {"email": email, "senha": senha})
    location = headers.get("location") or headers.get("Location") or ""
    if status != 303 or not cliente.cookie:
        r.falhou(f"login {papel}", f"{status} sem cookie")
        return False
    if location.rstrip("/") == destino:
        r.passou(f"login {papel} → {destino}")
    else:
        r.falhou(f"login {papel}", f"redirecionou para {location!r}, esperava {destino}")
    sc = cliente.ultimo_set_cookie.lower()
    faltam = [f for f in ("secure", "httponly", "samesite=lax") if f not in sc]
    if faltam:
        r.falhou(f"cookie {papel}", f"sem {faltam}: {cliente.ultimo_set_cookie[:120]}")
    else:
        r.passou(f"cookie {papel} Secure; HttpOnly; SameSite=lax")
    return True


def conferir_anonimo(porta: int, r: base.Resultado):
    c = ClienteProducao(f"http://127.0.0.1:{porta}")
    status, corpo, _h = c.pedir("/health")
    saude = base.json_de(corpo) or {}
    if status == 200 and saude.get("status") == "ok" and "version" not in corpo:
        r.passou("/health público e mínimo", corpo.strip())
    else:
        r.falhou("/health", f"{status} {corpo[:80]}")
    for caminho in ("/dashboard", "/vendas", "/cotacoes/1/pdf", "/admin"):
        status, _c, headers = c.pedir(caminho)
        loc = headers.get("location") or headers.get("Location") or ""
        if status == 303 and "/login" in loc:
            r.passou(f"anônimo em {caminho} → login")
        else:
            r.falhou(f"anônimo em {caminho}", f"{status} {loc}")
    status, corpo, headers = c.pedir("/static/css/anara.css")
    if status == 200 and "/Users/" not in corpo and "file://" not in corpo:
        r.passou("CSS público, sem caminho local")
    else:
        r.falhou("CSS", f"{status}")
    status, _c, _h = c.pedir("/static/js/ui.js")
    r.passou("JS público") if status == 200 else r.falhou("JS", f"{status}")
    status, _c, _h = c.pedir("/static/fonts/Didot.ttf")
    if status == 404:
        r.passou("fonte licenciada NÃO é pública (404 anônimo)")
    else:
        r.falhou("fonte pública", f"{status}")
    # esqueci minha senha: mesma resposta com e sem conta, sem SMTP em produção
    s1, c1, _ = c.pedir("/esqueci-senha", {"email": "seller-smoke@anara.test"})
    s2, c2, _ = c.pedir("/esqueci-senha", {"email": "ninguem-por-aqui@anara.test"})
    if s1 == s2 == 200 and visivel(c1) == visivel(c2) and "token" not in visivel(c1):
        r.passou("esqueci minha senha responde igual para conta existente e inexistente")
    else:
        r.falhou("esqueci minha senha", f"{s1}/{s2} corpos {'iguais' if c1 == c2 else 'DIFERENTES'}")


def conferir_owner(cliente: ClienteProducao, r: base.Resultado) -> dict:
    for caminho in ("/dashboard", "/configuracoes", "/admin", "/relatorios/economico",
                    "/aprovacoes", "/admin/usuarios", "/calculadora"):
        status, _c, _h = cliente.pedir(caminho)
        if status == 200:
            r.passou(f"OWNER GET {caminho}")
        else:
            r.falhou(f"OWNER GET {caminho}", f"{status}")
    status, corpo, _h = cliente.pedir("/health/detalhe")
    detalhe = base.json_de(corpo) or {}
    email = detalhe.get("recuperacao_senha_por_email") or {}
    if status == 200 and email.get("operacional") is False and email.get("backend") == "indisponivel":
        r.passou("/health/detalhe declara e-mail NÃO operacional (sem SMTP em produção)")
    else:
        r.falhou("/health/detalhe", f"{status} {corpo[:120]}")
    status, _c, headers = cliente.pedir("/static/fonts/Didot.ttf")
    r.passou("fonte servida a quem está autenticado") if status == 200 \
        else r.falhou("fonte autenticada", f"{status}")

    contexto = base.fluxo_comercial(cliente, r)
    base.fluxo_cotacao(cliente, contexto, r)
    return contexto


def conferir_pdfs(cliente: ClienteProducao, cot_id, r: base.Resultado):
    """Rascunho (nova revisão) e final (emitida): PDF de verdade, texto varrido."""
    status, final, headers = cliente.pedir_bytes(f"/cotacoes/{cot_id}/pdf")
    if status != 200 or not final.startswith(b"%PDF"):
        r.falhou("PDF final", f"{status} {headers.get('content-type')}")
        return
    r.passou("PDF final é um PDF", f"{len(final)} bytes")
    texto = texto_do_pdf(final).lower()
    if not texto:
        r.aviso("PDF final", "pdfplumber ausente — texto não varrido")
    else:
        achados = [t for t in PDF_PROIBIDO if t in texto]
        if achados:
            r.falhou("PDF final com dado interno", str(achados))
        else:
            r.passou("PDF final sem custo/margem/lucro/comissão/código interno")
        if "rascunho" in texto:
            r.falhou("PDF final", "marcado como rascunho")
        else:
            r.passou("PDF final sem marca de rascunho")
    # revisão → rascunho
    status, corpo, headers = cliente.pedir(f"/cotacoes/{cot_id}/revisao", {})
    novo = None
    loc = headers.get("location") or headers.get("Location") or ""
    m = re.search(r"/cotacoes/(\d+)", loc)
    if m:
        novo = int(m.group(1))
    else:
        dados = base.json_de(corpo) or {}
        novo = dados.get("id") or dados.get("cotacao_id") or (dados.get("cotacao") or {}).get("id")
    if not novo:
        r.aviso("PDF rascunho", f"não obtive a revisão ({status})")
        return
    status, rascunho, _h = cliente.pedir_bytes(f"/cotacoes/{novo}/pdf")
    if status != 200 or not rascunho.startswith(b"%PDF"):
        r.falhou("PDF rascunho", f"{status}")
        return
    texto = texto_do_pdf(rascunho).lower()
    if texto:
        if "rascunho" in texto:
            r.passou("PDF rascunho vem marcado como rascunho")
        else:
            r.falhou("PDF rascunho", "sem a marca de rascunho")
        achados = [t for t in PDF_PROIBIDO if t in texto]
        r.falhou("PDF rascunho com dado interno", str(achados)) if achados \
            else r.passou("PDF rascunho sem dado interno")
    return novo


def conferir_seller(cliente: ClienteProducao, contexto: dict, r: base.Resultado):
    cot_id = contexto.get("cotacao_id")
    for caminho in ("/vendas", "/clientes", "/cotacoes", "/produtos"):
        status, corpo, _h = cliente.pedir(caminho)
        if status != 200:
            r.falhou(f"SELLER GET {caminho}", f"{status}")
            continue
        achados = [t for t in ECONOMIA_HTML if t in visivel(corpo)]
        r.falhou(f"SELLER {caminho} expõe economia", str(achados)) if achados \
            else r.passou(f"SELLER GET {caminho} sem economia")
    # /dashboard não é 403 para a vendedora: é redirect para /vendas (o papel decide o
    # destino). O que não pode acontecer é o HTML do dashboard ser servido.
    status, corpo, headers = cliente.pedir("/dashboard")
    loc = headers.get("location") or headers.get("Location") or ""
    if status == 303 and loc.rstrip("/") == "/vendas":
        r.passou("SELLER /dashboard → redirect para /vendas (sem Dashboard)")
    else:
        r.falhou("SELLER /dashboard", f"esperava 303 → /vendas, veio {status} {loc}")
    for caminho in URLS_PROIBIDAS_SELLER + (
            [f"/cotacoes/{cot_id}/itens/1/memoria"] if cot_id else []):
        status, _c, _h = cliente.pedir(caminho)
        if status == 403:
            r.passou(f"SELLER {caminho} → 403")
        else:
            r.falhou(f"SELLER {caminho}", f"esperava 403, veio {status}")
    if not cot_id:
        r.aviso("SELLER cotação", "sem cotação do fluxo para conferir")
        return
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}")
    if status == 200:
        achados = [t for t in ECONOMIA_HTML if t in visivel(corpo)]
        r.falhou("SELLER cotação HTML expõe economia", str(achados)) if achados \
            else r.passou("SELLER cotação HTML sem margem/lucro/custo/CNET")
        if "comiss" in visivel(corpo):
            r.passou("SELLER vê a própria comissão estimada na cotação")
        else:
            r.aviso("SELLER comissão", "texto 'comissão' não encontrado no HTML")
    else:
        r.falhou("SELLER GET cotação", f"{status}")
    for caminho in (f"/cotacoes/{cot_id}/negociacao", f"/cotacoes/{cot_id}/situacao"):
        status, corpo, _h = cliente.pedir(caminho)
        dados = base.json_de(corpo)
        if status != 200 or dados is None:
            r.falhou(f"SELLER {caminho}", f"{status}")
            continue
        ks = chaves(dados)
        achados = sorted(k for k in ks if any(t in k for t in ECONOMIA_JSON))
        r.falhou(f"SELLER {caminho} expõe chave", str(achados)) if achados \
            else r.passou(f"SELLER {caminho} sem chave econômica")
        if "negociacao" in caminho:
            if any("comissao" in k for k in ks):
                r.passou("SELLER negociação traz a comissão própria")
            else:
                r.falhou("SELLER negociação", "sem comissão própria")
        if "situacao" in caminho and "MARGEM_ABAIXO_PISO" in corpo:
            r.falhou("SELLER situação", "vaza MARGEM_ABAIXO_PISO")
    status, pdf, _h = cliente.pedir_bytes(f"/cotacoes/{cot_id}/pdf")
    if status == 200 and pdf.startswith(b"%PDF"):
        texto = texto_do_pdf(pdf).lower()
        achados = [t for t in PDF_PROIBIDO if t in texto] if texto else []
        r.falhou("SELLER PDF com dado interno", str(achados)) if achados \
            else r.passou("SELLER gera PDF sem dado interno")
    elif status == 403:
        r.passou("SELLER PDF negado (403) — permissão não concedida")
    else:
        r.falhou("SELLER PDF", f"{status}")


def conferir_log(saida: str, segredos: list, r: base.Resultado):
    vazou = [nome for nome, valor in segredos if valor and valor in saida]
    if vazou:
        r.falhou("LOG DO SERVIDOR CONTÉM SEGREDO", ", ".join(vazou))
    else:
        r.passou("log do servidor sem SECRET_KEY nem senhas")
    if "NÃO está operacional" in saida:
        r.passou("log avisa que o e-mail não está operacional sem SMTP")
    else:
        r.aviso("log", "aviso de e-mail não operacional não encontrado")
    if "Uvicorn running on http://0.0.0.0:" in saida:
        r.passou("servidor ouviu em 0.0.0.0:$PORT (comando do Procfile)")
    else:
        r.aviso("bind", "linha 'Uvicorn running on http://0.0.0.0' não encontrada no log")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--postgres", help="URL de um PostgreSQL DE TESTE (nome com 'teste'/'test'/'smoke')")
    ap.add_argument("--manter", action="store_true", help="não apagar a cópia SQLite")
    args = ap.parse_args()

    import secrets
    r = base.Resultado()
    owner_email, seller_email = "owner-smoke@anara.test", "seller-smoke@anara.test"
    owner_senha, seller_senha = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    secret = secrets.token_urlsafe(48)
    producao = os.path.join(RAIZ, "data", "anara.db")
    antes = (os.path.getsize(producao), os.path.getmtime(producao)) if os.path.exists(producao) else None

    print("\n=== ANARA · SMOKE TEST PRODUCTION-LIKE ===\n")
    print("1) Ambiente isolado" + (" (PostgreSQL)" if args.postgres else " (SQLite, cópia)"))
    copia = base.preparar_banco(r)
    url_copia = f"sqlite:///{copia}"
    if not base.provar_isolamento(url_copia, copia, r):
        return 1
    if not base.migrar(url_copia, r):
        return 1
    url_banco = url_copia
    if args.postgres:
        url_banco = normalizar_url(args.postgres)
        print(f"   destino: {url_segura(url_banco)}")
        if not preparar_postgres(url_banco, copia, r):
            return 1
    if not criar_usuario(url_banco, owner_email, owner_senha, "OWNER", "Owner do Smoke", r):
        return 1
    if not criar_usuario(url_banco, seller_email, seller_senha, "VENDEDOR_INTERNO", "Vendedora do Smoke", r):
        return 1

    porta = base.porta_livre()
    print(f"\n2) Subindo com o comando do Procfile em 0.0.0.0:{porta} (ANARA_ENV=producao)")
    processo = subir_servidor_producao(url_banco, porta, secret)
    sonda = ClienteProducao(f"http://127.0.0.1:{porta}")
    saida_log = ""
    try:
        if not base.esperar_subir(sonda, processo, r):
            return 1

        print("\n3) Anônimo: health, static, fontes, esqueci minha senha")
        conferir_anonimo(porta, r)

        print("\n4) OWNER: login, dashboard, economia, fluxo comercial, emissão")
        owner = ClienteProducao(f"http://127.0.0.1:{porta}")
        if not conferir_login(owner, owner_email, owner_senha, "/dashboard", "OWNER", r):
            return 1
        contexto = conferir_owner(owner, r)

        print("\n5) PDF final e rascunho")
        if contexto.get("cotacao_id"):
            conferir_pdfs(owner, contexto["cotacao_id"], r)
        else:
            r.aviso("PDF", "fluxo não produziu cotação")

        print("\n6) SELLER: destino, URLs proibidas, HTML/JSON sem economia, comissão própria")
        seller = ClienteProducao(f"http://127.0.0.1:{porta}")
        if conferir_login(seller, seller_email, seller_senha, "/vendas", "SELLER", r):
            conferir_seller(seller, contexto, r)
    finally:
        processo.terminate()
        try:
            processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processo.kill()
        saida_log = processo.stdout.read() if processo.stdout else ""
        if not args.manter and os.path.exists(copia):
            os.unlink(copia)

    print("\n7) Log do servidor")
    conferir_log(saida_log, [("ANARA_SECRET_KEY", secret), ("senha OWNER", owner_senha),
                             ("senha SELLER", seller_senha)], r)

    if antes is not None:
        depois = (os.path.getsize(producao), os.path.getmtime(producao))
        if depois != antes:
            r.falhou("BANCO DE PRODUÇÃO FOI ALTERADO", "o smoke escreveu fora do isolamento")
        else:
            r.passou("banco de produção intocado")

    print(f"\n=== {len(r.ok)} ok · {len(r.falhas)} falhas · {len(r.avisos)} avisos ===")
    for nome, detalhe in r.falhas:
        print(f"  ✗ {nome}: {detalhe}")
    for nome, detalhe in r.avisos:
        print(f"  ! {nome}: {detalhe}")
    return 1 if r.falhas else 0


if __name__ == "__main__":
    sys.exit(main())

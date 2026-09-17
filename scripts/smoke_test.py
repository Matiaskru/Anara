#!/usr/bin/env python3
"""Smoke test em runtime REAL — sobe o servidor e faz requisições HTTP de verdade.

Por que isto existe, e por que pytest não substitui: os testes chamam funções de rota
diretamente. Eles não exercitam o middleware de autenticação, o roteamento do FastAPI, a
serialização das respostas, o carregamento dos templates nem os arquivos estáticos. Um
sistema pode ter 682 testes verdes e devolver 500 em `/cotacoes` no navegador — e é
exatamente esse buraco que este script fecha.

**Nunca toca no banco de produção.** Trabalha numa cópia temporária, apontada por
`ANARA_DB_URL`, e a apaga no fim. O `data/anara.db` histórico não é lido para escrita nem
uma vez.

O que ele faz, em ordem:

1. copia o banco, aplica as migrations e cria um OWNER com senha efêmera;
2. sobe o `uvicorn` num processo separado, numa porta livre;
3. faz login de verdade, com cookie de sessão;
4. percorre as rotas principais e reprova qualquer 5xx;
5. executa dois fluxos de ponta a ponta — um comercial simples e um com aprovação;
6. gera um PDF real e confere que ele é um PDF;
7. imprime um resumo e sai com código != 0 se algo falhou.

Uso:
    python3 scripts/smoke_test.py
    python3 scripts/smoke_test.py --manter    # não apaga o banco temporário
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

VERDE, VERMELHO, AMARELO, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


class Resultado:
    def __init__(self):
        self.ok, self.falhas, self.avisos = [], [], []

    def passou(self, nome, detalhe=""):
        self.ok.append(nome)
        print(f"  {VERDE}ok{FIM}   {nome}{(' · ' + detalhe) if detalhe else ''}")

    def falhou(self, nome, detalhe=""):
        self.falhas.append((nome, detalhe))
        print(f"  {VERMELHO}FALHA{FIM} {nome}{(' · ' + detalhe) if detalhe else ''}")

    def aviso(self, nome, detalhe=""):
        self.avisos.append((nome, detalhe))
        print(f"  {AMARELO}aviso{FIM} {nome}{(' · ' + detalhe) if detalhe else ''}")


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Cliente HTTP com sessão
# ---------------------------------------------------------------------------
class Cliente:
    """Cliente mínimo com cookie jar. Sem dependência nova — `urllib` basta."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            SemRedirect())

    def pedir(self, caminho: str, dados: dict = None, metodo: str = None):
        """Devolve `(status, corpo, headers)`. Nunca levanta em 4xx/5xx."""
        url = self.base + caminho
        corpo = None
        if dados is not None:
            corpo = urllib.parse.urlencode(dados).encode()
        req = urllib.request.Request(url, data=corpo, method=metodo)
        req.add_header("Accept", "text/html,application/json")
        if corpo is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8", "replace"), dict(erro.headers)
        except Exception as erro:                   # noqa: BLE001
            return 0, f"{type(erro).__name__}: {erro}", {}


class SemRedirect(urllib.request.HTTPRedirectHandler):
    """Não seguir redirects: o 303 do login e do POST é informação, não obstáculo."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = \
        lambda self, req, fp, code, msg, headers: None


# ---------------------------------------------------------------------------
# Preparação do ambiente
# ---------------------------------------------------------------------------
def preparar_banco(resultado: Resultado) -> str:
    """Cópia do banco de produção — **somente cópia**. O original não é aberto para escrita."""
    origem = os.path.join(RAIZ, "data", "anara.db")
    fd, destino = tempfile.mkstemp(prefix="anara-smoke-", suffix=".db")
    os.close(fd)
    if os.path.exists(origem):
        shutil.copy2(origem, destino)
        resultado.passou("banco de teste copiado", os.path.basename(destino))
    else:
        resultado.aviso("banco de produção não encontrado", "smoke rodará em banco vazio")
    return destino


def migrar(url: str, resultado: Resultado) -> bool:
    env = dict(os.environ, ANARA_DB_URL=url)
    proc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                          cwd=RAIZ, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        resultado.falhou("alembic upgrade head", proc.stderr.strip()[-300:])
        return False
    atual = subprocess.run([sys.executable, "-m", "alembic", "current"],
                           cwd=RAIZ, env=env, capture_output=True, text=True)
    resultado.passou("migrations aplicadas", atual.stdout.strip().splitlines()[-1])
    return True


def provar_isolamento(url: str, banco: str, resultado: Resultado) -> bool:
    """**Trava de segurança**: prova que o processo filho abre a CÓPIA, não a produção.

    Existe porque a primeira versão deste script não tinha essa prova e escreveu linhas de
    teste no banco histórico: `app/db.py` ignorava `ANARA_DB_URL`, as migrations iam para a
    cópia e os dados iam para a produção. Nada no roteiro acusou — tudo "passou".

    Agora nada acontece antes desta verificação, e ela compara o caminho REAL que o processo
    filho abriu com o esperado. Falhar aqui aborta o smoke inteiro.
    """
    env = dict(os.environ, ANARA_DB_URL=url, PYTHONPATH=RAIZ)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from app.db import caminho_do_banco; print(caminho_do_banco())"],
        cwd=RAIZ, env=env, capture_output=True, text=True)
    usado = (proc.stdout or "").strip()
    if proc.returncode != 0 or not usado:
        resultado.falhou("prova de isolamento", (proc.stderr or "").strip()[-200:])
        return False
    if os.path.realpath(usado) != os.path.realpath(banco):
        resultado.falhou(
            "PROVA DE ISOLAMENTO",
            f"o processo abriria {usado} em vez da cópia. ABORTANDO antes de escrever.")
        return False
    producao = os.path.join(RAIZ, "data", "anara.db")
    if os.path.realpath(usado) == os.path.realpath(producao):
        resultado.falhou("PROVA DE ISOLAMENTO", "apontaria para a produção. ABORTANDO.")
        return False
    resultado.passou("isolamento provado", "o processo filho abre a cópia")
    return True


def criar_owner(url: str, email: str, senha: str, resultado: Resultado) -> bool:
    """Usa o script de bootstrap REAL — é ele que precisa funcionar, não um atalho."""
    env = dict(os.environ, ANARA_DB_URL=url, ANARA_SENHA_BOOTSTRAP=senha)
    proc = subprocess.run(
        [sys.executable, "scripts/criar_usuario.py", "--email", email,
         "--nome", "Owner do Smoke", "--papel", "OWNER", "--gerencia-usuarios"],
        cwd=RAIZ, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        resultado.falhou("criar_usuario.py", proc.stderr.strip()[-300:])
        return False
    resultado.passou("OWNER criado pelo script de bootstrap", email)
    return True


def subir_servidor(url: str, porta: int, secret: str):
    env = dict(os.environ, ANARA_DB_URL=url, ANARA_SECRET_KEY=secret,
               ANARA_ENV="desenvolvimento", PYTHONPATH=RAIZ)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(porta), "--log-level", "warning"],
        cwd=RAIZ, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def esperar_subir(cliente: Cliente, processo, resultado: Resultado, limite=45) -> bool:
    inicio = time.time()
    while time.time() - inicio < limite:
        if processo.poll() is not None:
            saida = processo.stdout.read() if processo.stdout else ""
            resultado.falhou("o servidor morreu ao subir", saida.strip()[-600:])
            return False
        status, corpo, _ = cliente.pedir("/health")
        if status == 200:
            resultado.passou("servidor no ar", f"/health → {corpo.strip()[:60]}")
            return True
        time.sleep(0.4)
    resultado.falhou("servidor não respondeu", f"{limite}s sem /health")
    return False


# ---------------------------------------------------------------------------
# Verificações
# ---------------------------------------------------------------------------
#: Rotas principais. `esperado` lista os status aceitáveis — um 303 para o login é resposta
#: correta antes de autenticar, e um 403 é resposta correta para papel sem permissão.
ROTAS = [
    ("/health", "healthcheck público"),
    ("/", "dashboard"),
    ("/comercial", "home comercial"),
    ("/pipeline", "pipeline"),
    ("/oportunidades", "lista de oportunidades"),
    ("/clientes", "clientes"),
    ("/cotacoes", "histórico de cotações"),
    ("/cotacoes/nova", "nova cotação"),
    ("/produtos", "produtos e preços"),
    ("/relatorios", "relatórios comerciais"),
    ("/relatorios/economico", "relatório econômico"),
    ("/saude", "saúde operacional"),
    ("/aprovacoes", "fila de aprovações"),
    ("/admin", "administração de premissas"),
    ("/admin/trilha", "trilha de auditoria"),
    ("/calculadora", "calculadora KTC"),
    ("/importar", "importar Excel"),
    ("/relatorios/qualidade", "qualidade da base"),
    ("/configuracoes", "configurações"),
    ("/health/detalhe", "healthcheck detalhado"),
    ("/relatorios/oportunidades.csv", "CSV de oportunidades"),
    ("/relatorios/atividades.csv", "CSV de atividades"),
    ("/relatorios/cotacoes.csv", "CSV de cotações"),
]


def testar_rotas(cliente: Cliente, resultado: Resultado):
    for caminho, nome in ROTAS:
        status, corpo, _ = cliente.pedir(caminho)
        if status >= 500 or status == 0:
            trecho = corpo.strip().splitlines()[-1][:200] if corpo else ""
            resultado.falhou(f"GET {caminho}", f"{status} — {trecho}")
        elif status == 303 and "/login" in corpo:
            resultado.falhou(f"GET {caminho}", "redirecionou para o login já autenticado")
        else:
            resultado.passou(f"GET {caminho}", f"{status} · {nome}")


def login(cliente: Cliente, email: str, senha: str, resultado: Resultado) -> bool:
    status, _corpo, headers = cliente.pedir("/login", {"email": email, "senha": senha,
                                                       "next": "/comercial"})
    if status != 303:
        resultado.falhou("login", f"esperava 303, veio {status}")
        return False
    if "anara_session" not in (headers.get("set-cookie") or ""):
        resultado.falhou("login", "sem cookie de sessão")
        return False
    resultado.passou("login com cookie de sessão")

    # e a senha errada precisa ser recusada — no runtime, não só no teste unitário
    outro = Cliente(cliente.base)
    status_ruim, _c, _h = outro.pedir("/login", {"email": email, "senha": "errada-de-proposito",
                                                 "next": "/"})
    if status_ruim == 401:
        resultado.passou("senha errada é recusada em runtime")
    else:
        resultado.falhou("senha errada", f"esperava 401, veio {status_ruim}")

    # e sem sessão, rota protegida manda para o login
    anonimo = Cliente(cliente.base)
    status_anon, _c, _h = anonimo.pedir("/comercial")
    if status_anon == 303:
        resultado.passou("rota protegida exige login")
    else:
        resultado.falhou("acesso anônimo", f"esperava 303, veio {status_anon}")
    return True


def json_de(corpo: str):
    import json
    try:
        return json.loads(corpo)
    except ValueError:
        return None


def fluxo_comercial(cliente: Cliente, resultado: Resultado) -> dict:
    """E2E: cliente → contato → oportunidade → atividade → cotação → emissão → ganho."""
    contexto = {}

    status, corpo, headers = cliente.pedir(
        "/clientes", {"nome": "Hotel do Smoke Test", "cidade_uf": "São Paulo",
                      "finalidade": "REVENDA", "telefone": "(11) 90000-0000"})
    if status not in (200, 303):
        resultado.falhou("criar cliente", f"{status} — {corpo[:200]}")
        return contexto
    resultado.passou("cliente criado")

    # descobre o id pelo redirect ou pela listagem
    destino = headers.get("location", "")
    cliente_id = destino.rstrip("/").split("/")[-1] if "/clientes/" in destino else None
    if not (cliente_id or "").isdigit():
        _s, lista, _h = cliente.pedir("/clientes")
        import re
        achados = re.findall(r'/clientes/(\d+)', lista)
        cliente_id = achados[-1] if achados else None
    if not cliente_id:
        resultado.falhou("descobrir id do cliente", "não achei o link na listagem")
        return contexto
    contexto["cliente_id"] = int(cliente_id)

    status, corpo, _h = cliente.pedir(f"/clientes/{cliente_id}/contatos",
                                      {"nome": "Ana Compras", "cargo": "Compras",
                                       "email": "ana@hoteldosmoke.test", "principal": "sim"})
    (resultado.passou if status in (200, 303) else resultado.falhou)(
        "contato criado", "" if status in (200, 303) else f"{status}")

    status, corpo, headers = cliente.pedir(
        "/oportunidades", {"cliente_id": cliente_id, "titulo": "Enxoval do smoke",
                           "etapa": "RASCUNHO", "origem": "INBOUND",
                           "valor_estimado": "50000"})
    if status != 303:
        resultado.falhou("criar oportunidade", f"{status} — {corpo[:200]}")
        return contexto
    op_id = headers.get("location", "").rstrip("/").split("/")[-1]
    contexto["oportunidade_id"] = int(op_id)
    resultado.passou("oportunidade criada", f"#{op_id}")

    status, corpo, _h = cliente.pedir("/atividades",
                                      {"titulo": "Ligar para a Ana", "oportunidade_id": op_id,
                                       "tipo": "LIGACAO"})
    (resultado.passou if status == 200 else resultado.falhou)(
        "atividade criada", "" if status == 200 else f"{status} — {corpo[:150]}")

    status, corpo, _h = cliente.pedir(f"/oportunidades/{op_id}/etapa",
                                      {"etapa": "NEGOCIACAO"})
    (resultado.passou if status == 200 else resultado.falhou)(
        "etapa movida", "" if status == 200 else f"{status}")

    status, _corpo, headers = cliente.pedir(f"/oportunidades/{op_id}/cotacao", {})
    if status != 303:
        resultado.falhou("criar cotação pela oportunidade", f"{status}")
        return contexto
    cot_id = headers.get("location", "").rstrip("/").split("/")[-1]
    contexto["cotacao_id"] = int(cot_id)
    resultado.passou("cotação criada e vinculada", f"#{cot_id}")

    status, corpo, _h = cliente.pedir(f"/oportunidades/{op_id}")
    if status >= 400:
        resultado.falhou("abrir oportunidade", f"{status}")
    else:
        resultado.passou("oportunidade 360 renderiza")
    return contexto


def escolher_produto(cliente: Cliente, resultado: Resultado):
    """Um SKU real do catálogo, com custo — nada de valor inventado."""
    status, corpo, _h = cliente.pedir("/produtos/buscar?q=")
    dados = json_de(corpo) or []
    com_custo = [p for p in dados if not p.get("sem_custo")]
    if not com_custo:
        resultado.aviso("nenhum produto com custo", "o E2E de emissão será pulado")
        return None
    return com_custo[0]


def margem_do_produto(produto: dict) -> str:
    """A margem-alvo **da regra daquele SKU**, não um número escolhido a esmo.

    Cotar a 14% um produto cuja regra manda 16% é, corretamente, um desconto — o sistema
    marca exceção e pede aprovação. Para exercitar também o caminho limpo (sem exceção), o
    smoke usa a margem que a política já define para o item.
    """
    return str(produto.get("margem_padrao_pct") or 0.14)


def fluxo_cotacao(cliente: Cliente, contexto: dict, resultado: Resultado):
    """Adiciona item, calcula, gera preview, emite, envia e fecha o negócio."""
    cot_id = contexto.get("cotacao_id")
    op_id = contexto.get("oportunidade_id")
    if not cot_id:
        return

    produto = escolher_produto(cliente, resultado)
    if produto is None:
        return

    status, corpo, _h = cliente.pedir(
        f"/cotacoes/{cot_id}/atualizar",
        {"condicao_pagamento": "30", "estado_destino": "São Paulo",
         "contribuinte_icms": "sim", "finalidade": "REVENDA", "freight_type": "FOB",
         "vendedor": "Smoke"})
    (resultado.passou if status in (200, 303) else resultado.falhou)(
        "cabeçalho da cotação", "" if status in (200, 303) else f"{status}")

    margem = margem_do_produto(produto)
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/calc",
                                      {"produto_id": produto["id"], "quantidade": "10",
                                       "modo": "margem", "valor": margem})
    dados = json_de(corpo)
    if status != 200 or dados is None:
        resultado.falhou("cálculo ao vivo", f"{status} — {corpo[:200]}")
        return
    resultado.passou("cálculo ao vivo", f"preço {dados.get('preco_negociado')}")

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/itens",
                                      {"produto_id": produto["id"], "quantidade": "10",
                                       "modo": "margem", "valor": margem})
    if status not in (200, 303):
        resultado.falhou("adicionar item", f"{status} — {corpo[:200]}")
        return
    resultado.passou("item adicionado", f"na margem da regra ({margem})")

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}")
    (resultado.passou if status < 400 else resultado.falhou)(
        "cotação renderiza", "" if status < 400 else f"{status}")

    # --- PDF de rascunho ---
    status, corpo, headers = cliente.pedir(f"/cotacoes/{cot_id}/pdf")
    if status == 200 and corpo.startswith("%PDF"):
        resultado.passou("PDF de rascunho gerado", f"{len(corpo)} bytes")
    elif status == 409:
        resultado.aviso("PDF bloqueado", "há pendência fiscal no item — comportamento correto")
    else:
        resultado.falhou("PDF", f"{status} — {corpo[:150]}")

    # --- situação e emissão ---
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/situacao")
    situacao = json_de(corpo) or {}
    if status != 200:
        resultado.falhou("situação da cotação", f"{status}")
        return
    resultado.passou("situação da cotação",
                     f"pode_emitir={situacao.get('pode_emitir')} "
                     f"aprovacao={situacao.get('precisa_aprovacao')}")

    if not situacao.get("pode_emitir"):
        resultado.aviso("emissão pulada", "; ".join(situacao.get("motivos") or [])[:160])
        return

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/emitir", {})
    if status not in (200, 303):        # 303: o navegador volta para a cotação (Fase 3C)
        resultado.falhou("emitir", f"{status} — {corpo[:200]}")
        return
    resultado.passou("cotação emitida")

    status, corpo, headers = cliente.pedir(f"/cotacoes/{cot_id}/pdf")
    if status == 200 and corpo.startswith("%PDF"):
        marcado = "RASCUNHO" in corpo[:4000]
        (resultado.falhou if marcado else resultado.passou)(
            "PDF final", "não deveria estar marcado como rascunho" if marcado
            else f"{len(corpo)} bytes")
    else:
        resultado.falhou("PDF final", f"{status}")

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/enviar", {})
    enviada = status in (200, 303)      # 303: o navegador volta para a cotação (Fase 3C)
    (resultado.passou if enviada else resultado.falhou)(
        "marcada como enviada", "" if enviada else f"{status}")

    # --- imutabilidade em runtime ---
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/atualizar",
                                      {"condicao_pagamento": "30/60"})
    if status == 409:
        resultado.passou("emitida é imutável em runtime", "409 ao tentar editar")
    else:
        resultado.falhou("imutabilidade", f"esperava 409, veio {status}")

    # --- compromisso firme e ganho ---
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/compromisso")
    compromisso = json_de(corpo) or {}
    resultado.passou("compromisso firme avaliado",
                     f"pode={compromisso.get('pode')}")

    if op_id and compromisso.get("pode"):
        status, corpo, _h = cliente.pedir(f"/oportunidades/{op_id}/ganha",
                                          {"cotacao_id": cot_id})
        if status == 200:
            ganho = json_de(corpo) or {}
            resultado.passou("oportunidade GANHA",
                             f"valor fechado {ganho.get('valor_fechado')}")
            contexto["valor_ganho"] = ganho.get("valor_fechado")
        else:
            resultado.falhou("marcar ganha", f"{status} — {corpo[:200]}")
    elif op_id:
        impedimentos = "; ".join(compromisso.get("impedimentos") or [])[:160]
        resultado.aviso("ganho pulado", impedimentos)


def fluxo_aprovacao(cliente: Cliente, contexto: dict, resultado: Resultado):
    """Segundo E2E: negociar abaixo do recomendado → aprovação → emissão."""
    cliente_id = contexto.get("cliente_id")
    if not cliente_id:
        return
    produto = escolher_produto(cliente, resultado)
    if produto is None:
        return

    status, _c, headers = cliente.pedir(
        "/oportunidades", {"cliente_id": cliente_id, "titulo": "Negócio com desconto",
                           "etapa": "NEGOCIACAO", "origem": "OUTBOUND"})
    if status != 303:
        resultado.falhou("oportunidade do fluxo de aprovação", f"{status}")
        return
    op_id = headers.get("location", "").rstrip("/").split("/")[-1]

    status, _c, headers = cliente.pedir(f"/oportunidades/{op_id}/cotacao", {})
    if status != 303:
        resultado.falhou("cotação do fluxo de aprovação", f"{status}")
        return
    cot_id = headers.get("location", "").rstrip("/").split("/")[-1]

    cliente.pedir(f"/cotacoes/{cot_id}/atualizar",
                  {"condicao_pagamento": "30", "estado_destino": "São Paulo",
                   "contribuinte_icms": "sim", "finalidade": "REVENDA",
                   "freight_type": "FOB", "vendedor": "Smoke"})
    margem = margem_do_produto(produto)
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/itens",
                                      {"produto_id": produto["id"], "quantidade": "5",
                                       "modo": "margem", "valor": margem})
    if status not in (200, 303):
        resultado.falhou("item do fluxo de aprovação", f"{status}")
        return

    # negocia abaixo do recomendado
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/situacao")
    situacao = json_de(corpo) or {}
    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/calc",
                                      {"produto_id": produto["id"], "quantidade": "5",
                                       "modo": "margem", "valor": margem})
    calculo = json_de(corpo) or {}
    recomendado = calculo.get("preco_negociado")
    if not recomendado:
        resultado.aviso("fluxo de aprovação pulado", "não obtive o preço recomendado")
        return

    import json as _json
    _s, lista, _h = cliente.pedir(f"/cotacoes/{cot_id}")
    import re
    item_ids = re.findall(r'data-item-id="(\d+)"', lista)
    if not item_ids:
        resultado.aviso("fluxo de aprovação pulado", "não achei o item no HTML")
        return
    item_id = item_ids[0]

    desconto = round(float(recomendado) * 0.8, 2)
    req = urllib.request.Request(
        f"{cliente.base}/cotacoes/{cot_id}/itens/{item_id}",
        data=urllib.parse.urlencode({"quantidade": "5", "modo": "preco",
                                     "valor": str(desconto)}).encode(),
        method="PUT")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with cliente.opener.open(req, timeout=30) as resp:
            resp.read()
        resultado.passou("preço negociado abaixo do recomendado", f"R$ {desconto}")
    except Exception as erro:                       # noqa: BLE001
        resultado.falhou("negociar preço", str(erro)[:150])
        return

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/situacao")
    situacao = json_de(corpo) or {}
    if not situacao.get("precisa_aprovacao"):
        resultado.aviso("aprovação não exigida", "o desconto não gerou exceção")
        return
    resultado.passou("exceção comercial detectada",
                     f"{len(situacao.get('excecoes') or [])} motivo(s)")

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/aprovacao/solicitar",
                                      {"justificativa": "cliente fechou volume maior"})
    pedido = json_de(corpo) or {}
    if status != 200:
        resultado.falhou("solicitar aprovação", f"{status} — {corpo[:200]}")
        return
    resultado.passou("aprovação solicitada", f"pedido #{pedido.get('pedido_id')}")

    status, corpo, _h = cliente.pedir("/aprovacoes")
    (resultado.passou if status == 200 else resultado.falhou)(
        "fila de aprovações", "" if status == 200 else f"{status}")

    status, corpo, _h = cliente.pedir(
        f"/cotacoes/{cot_id}/aprovacao/{pedido.get('pedido_id')}/aprovar",
        {"comentario": "aprovado no smoke", "fingerprint": pedido.get("fingerprint", "")})
    if status != 200:
        resultado.falhou("aprovar", f"{status} — {corpo[:200]}")
        return
    resultado.passou("exceção aprovada")

    status, corpo, _h = cliente.pedir(f"/cotacoes/{cot_id}/emitir", {})
    if status == 200:
        resultado.passou("emitida após aprovação")
    else:
        resultado.aviso("emissão após aprovação", f"{status} — {corpo[:160]}")


def conferir_relatorios(cliente: Cliente, contexto: dict, resultado: Resultado):
    """Dashboard e CSV precisam contar a mesma coisa."""
    status, corpo, _h = cliente.pedir("/relatorios")
    if status != 200:
        resultado.falhou("relatórios", f"{status}")
        return
    resultado.passou("relatórios renderizam")

    status, csv_corpo, _h = cliente.pedir("/relatorios/oportunidades.csv")
    if status != 200:
        resultado.falhou("CSV de oportunidades", f"{status}")
        return
    linhas = [linha for linha in csv_corpo.strip().splitlines() if linha]
    resultado.passou("CSV de oportunidades", f"{len(linhas) - 1} linha(s)")

    if contexto.get("valor_ganho"):
        # o ganho precisa aparecer no relatório
        if "GANHA" in csv_corpo:
            resultado.passou("ganho aparece no CSV")
        else:
            resultado.falhou("ganho no CSV", "não encontrei a oportunidade ganha")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manter", action="store_true", help="não apagar o banco temporário")
    args = ap.parse_args()

    import secrets
    resultado = Resultado()
    email = "smoke-owner@anara.test"
    senha = secrets.token_urlsafe(16)               # efêmera; some com o processo
    secret = secrets.token_urlsafe(48)

    producao = os.path.join(RAIZ, "data", "anara.db")
    antes_producao = (os.path.getsize(producao), os.path.getmtime(producao)) \
        if os.path.exists(producao) else None

    print("\n=== ANARA · SMOKE TEST EM RUNTIME REAL ===\n")
    print("1) Ambiente isolado")
    banco = preparar_banco(resultado)
    url_banco = f"sqlite:///{banco}"
    # A prova de isolamento vem ANTES de qualquer escrita. Sem ela, um bug de configuração
    # faz o smoke test contaminar o banco histórico sem que nada acuse.
    if not provar_isolamento(url_banco, banco, resultado):
        if os.path.exists(banco):
            os.unlink(banco)
        return 1
    if not migrar(url_banco, resultado):
        return 1
    if not criar_owner(url_banco, email, senha, resultado):
        return 1

    porta = porta_livre()
    print(f"\n2) Subindo o servidor em 127.0.0.1:{porta}")
    processo = subir_servidor(url_banco, porta, secret)
    cliente = Cliente(f"http://127.0.0.1:{porta}")
    try:
        if not esperar_subir(cliente, processo, resultado):
            return 1

        print("\n3) Autenticação")
        if not login(cliente, email, senha, resultado):
            return 1

        print("\n4) Rotas principais")
        testar_rotas(cliente, resultado)

        print("\n5) Fluxo comercial de ponta a ponta")
        contexto = fluxo_comercial(cliente, resultado)

        print("\n6) Cotação, emissão, PDF e fechamento")
        fluxo_cotacao(cliente, contexto, resultado)

        print("\n7) Fluxo com aprovação")
        fluxo_aprovacao(cliente, contexto, resultado)

        print("\n8) Relatórios")
        conferir_relatorios(cliente, contexto, resultado)
    finally:
        processo.terminate()
        try:
            processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processo.kill()
        if not args.manter and os.path.exists(banco):
            os.unlink(banco)
            print(f"\nbanco temporário removido")
        elif args.manter:
            print(f"\nbanco temporário mantido: {banco}")

    # Conferência final: o banco histórico não pode ter sido tocado.
    if antes_producao is not None:
        depois = (os.path.getsize(producao), os.path.getmtime(producao))
        if depois != antes_producao:
            resultado.falhou("BANCO DE PRODUÇÃO FOI ALTERADO",
                             "o smoke test escreveu fora do isolamento")
        else:
            resultado.passou("banco de produção intocado", "tamanho e mtime idênticos")

    print("\n" + "=" * 60)
    print(f"  {VERDE}{len(resultado.ok)} ok{FIM} · "
          f"{AMARELO}{len(resultado.avisos)} aviso(s){FIM} · "
          f"{VERMELHO}{len(resultado.falhas)} falha(s){FIM}")
    if resultado.avisos:
        print("\n  avisos (não reprovam):")
        for nome, detalhe in resultado.avisos:
            print(f"    · {nome}: {detalhe}")
    if resultado.falhas:
        print("\n  FALHAS:")
        for nome, detalhe in resultado.falhas:
            print(f"    · {nome}: {detalhe}")
        return 1
    print("\n  SMOKE TEST PASSOU — o sistema sobe e responde.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

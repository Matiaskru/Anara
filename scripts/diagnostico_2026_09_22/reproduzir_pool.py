#!/usr/bin/env python3
"""Reprodução do `QueuePool limit of size 5 overflow 5 reached` (22/09/2026).

    ANARA_DB_URL=postgresql://... python3 scripts/diagnostico_2026_09_22/reproduzir_pool.py [--concorrentes 12]

Sobe o uvicorn **no mesmo processo** (thread), para poder ler `engine.pool.status()` — o pool
vive no processo do servidor, então medir de fora não prova nada. Depois:

* faz N requisições **sequenciais** em várias rotas e mede `checkedout` depois de cada uma:
  se sobra conexão presa, o número cresce e não volta a zero;
* faz uma rajada **concorrente** maior que `pool_size + max_overflow` e mede de novo;
* repete a rajada em rotas que abrem sessão fora da dependência (login/middleware, PDF).

Sai com código 1 se sobrar conexão presa (checkedout > 0 com o servidor ocioso) ou se alguma
requisição devolver 500 por timeout de pool.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ANARA_SECRET_KEY", "diagnostico-pool-2026-09-22-chave-local-0123456789abcdef")


def _cliente(base):
    import http.cookiejar
    jar = http.cookiejar.CookieJar()

    class _SemRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    abrir = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _SemRedirect)

    def pedir(metodo, caminho, dados=None):
        corpo = urllib.parse.urlencode(dados).encode() if dados else None
        req = urllib.request.Request(base + caminho, data=corpo, method=metodo,
                                     headers={"Accept": "text/html"})
        try:
            with abrir.open(req, timeout=120) as r:
                return r.status, len(r.read())
        except urllib.error.HTTPError as e:
            return e.code, len(e.read())
        except Exception as e:                                   # noqa: BLE001
            return 0, str(e)[:120]
    return pedir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8470)
    ap.add_argument("--sequenciais", type=int, default=3)
    ap.add_argument("--concorrentes", type=int, default=12)
    ap.add_argument("--latencia-ms", type=float, default=0.0,
                    help="atraso artificial por consulta, para emular o RTT de rede de um "
                         "PostgreSQL gerenciado (local ≈ 0,1 ms; Railway ≈ 1–3 ms). É o que "
                         "transforma 'muitas consultas' em 'conexão presa por 30 segundos'.")
    ap.add_argument("--banco-descartavel", action="store_true",
                    help="confirma que ANARA_DB_URL aponta para uma CÓPIA que pode ser escrita")
    a = ap.parse_args()

    import uvicorn
    from app.db import DB_URL, engine, url_segura
    from app.main import app

    # Este diagnóstico GRAVA no banco: cria um OWNER de teste para poder autenticar as rajadas.
    # Um OWNER com senha conhecida em produção é uma porta dos fundos, não um diagnóstico —
    # então o script recusa rodar sem a confirmação explícita de que o banco é descartável.
    if not a.banco_descartavel:
        print("RECUSADO: este diagnóstico cria um usuário OWNER de teste e escreve no banco.\n"
              f"           banco atual: {url_segura()}\n"
              "           Aponte ANARA_DB_URL para uma CÓPIA e repita com --banco-descartavel.",
              file=sys.stderr)
        return 2
    if os.environ.get("ANARA_ENV", "").strip().lower() == "producao":
        print("RECUSADO: ANARA_ENV=producao. Diagnóstico não roda contra produção.", file=sys.stderr)
        return 2

    config = uvicorn.Config(app, host="127.0.0.1", port=a.porta, log_level="warning")
    servidor = uvicorn.Server(config)
    threading.Thread(target=servidor.run, daemon=True).start()
    base = f"http://127.0.0.1:{a.porta}"
    for _ in range(80):
        if servidor.started:
            break
        time.sleep(0.25)

    pedir = _cliente(base)
    from sqlmodel import Session, select
    from app.auth import hash_senha
    from app.models import Usuario
    with Session(engine) as s:
        u = s.exec(select(Usuario).where(Usuario.email == "pool@anara.test")).first()
        if u is None:
            s.add(Usuario(email="pool@anara.test", nome="Pool", senha_hash=hash_senha("pool-2026-diagnostico"),
                          papel="OWNER", ativo=True, can_manage_users=True, can_manage_economics=True,
                          can_approve_quotes=True, sessao_versao=1))
            s.commit()
    pedir("POST", "/login", {"email": "pool@anara.test", "senha": "pool-2026-diagnostico"})

    def status():
        p = engine.pool
        return {"checkedout": p.checkedout(), "checkedin": p.checkedin(),
                "overflow": p.overflow(), "size": p.size()}

    # instrumentação: quantas consultas cada request dispara e quanto tempo a conexão fica
    # fora do pool. Pool estourado raramente é "vazou"; quase sempre é "ficou fora tempo demais".
    from sqlalchemy import event
    contador = {"queries": 0, "checkouts": 0, "tempo_fora": 0.0}
    _saida = {}

    atraso = max(a.latencia_ms, 0.0) / 1000.0

    @event.listens_for(engine, "before_cursor_execute")
    def _conta(conn, cursor, statement, params, context, executemany):   # noqa: ARG001
        contador["queries"] += 1
        if atraso:
            time.sleep(atraso)   # só no diagnóstico: o custo de rede que a máquina local não tem

    @event.listens_for(engine, "checkout")
    def _checkout(dbapi_con, rec, proxy):                                # noqa: ARG001
        contador["checkouts"] += 1
        _saida[id(rec)] = time.perf_counter()

    @event.listens_for(engine, "checkin")
    def _checkin(dbapi_con, rec):                                        # noqa: ARG001
        t0 = _saida.pop(id(rec), None)
        if t0:
            contador["tempo_fora"] += time.perf_counter() - t0

    def zerar():
        contador.update(queries=0, checkouts=0, tempo_fora=0.0)

    rotas = ["/dashboard", "/produtos", "/cotacoes", "/vendas", "/clientes", "/admin/produtos",
             "/relatorios/economico", "/health", "/calculadora"]
    saida = {"banco": url_segura(DB_URL), "sequencial": [], "concorrente": [], "falhas": []}
    print(f"banco: {url_segura(DB_URL)}")
    print(f"pool inicial: {status()}")

    # --- sequencial -------------------------------------------------------
    for i in range(a.sequenciais):
        for rota in rotas:
            zerar()
            t0 = time.perf_counter()
            code, tam = pedir("GET", rota)
            ms = (time.perf_counter() - t0) * 1000
            time.sleep(0.05)                     # a devolução ao pool acontece no fim do request
            st = status()
            linha = {"volta": i + 1, "rota": rota, "status": code, "pool": st,
                     "ms": round(ms), "queries": contador["queries"],
                     "checkouts": contador["checkouts"],
                     "ms_fora_do_pool": round(contador["tempo_fora"] * 1000)}
            saida["sequencial"].append(linha)
            if code >= 500 or code == 0:
                saida["falhas"].append(linha)
            print(f"  {rota:22} → {code} · {ms:7.0f} ms · {contador['queries']:5} queries · "
                  f"{contador['checkouts']} checkouts · {contador['tempo_fora']*1000:7.0f} ms fora do pool "
                  f"· checkedout={st['checkedout']}")

    ocioso = status()
    print(f"pool com o servidor ocioso (depois das sequenciais): {ocioso}")

    # --- concorrente ------------------------------------------------------
    def rajada(rota, n):
        """Rajada AUTENTICADA: cada thread com sua sessão de login — é o cenário real, várias
        pessoas usando ao mesmo tempo. Cliente anônimo só toma redirect e não mede nada."""
        clientes = []
        for _ in range(n):
            c = _cliente(base)
            c("POST", "/login", {"email": "pool@anara.test", "senha": "pool-2026-diagnostico"})
            clientes.append(c)
        with ThreadPoolExecutor(max_workers=n) as ex:
            return list(ex.map(lambda c: c("GET", rota), clientes))

    for rota in ("/produtos", "/dashboard", "/admin/produtos", "/cotacoes"):
        zerar()
        t0 = time.perf_counter()
        resultados = rajada(rota, a.concorrentes)
        ms = (time.perf_counter() - t0) * 1000
        erros = [r for r in resultados if r[0] >= 500 or r[0] == 0]
        time.sleep(1.0)
        st = status()
        linha = {"rota": rota, "n": a.concorrentes, "erros": len(erros), "ms_total": round(ms),
                 "amostra_erro": erros[:2], "pool_depois": st,
                 "queries": contador["queries"], "checkouts": contador["checkouts"]}
        saida["concorrente"].append(linha)
        if erros:
            saida["falhas"].append(linha)
        print(f"  rajada {a.concorrentes}× {rota:18} → erros={len(erros)} · {ms:7.0f} ms · "
              f"{contador['queries']} queries · checkedout depois={st['checkedout']}")

    time.sleep(1.5)
    final = status()
    saida["pool_final"] = final
    print(f"pool final (ocioso): {final}")
    print(json.dumps({k: v for k, v in saida.items() if k in ("falhas", "pool_final")},
                     ensure_ascii=False, indent=1, default=str))
    destino = os.path.join(RAIZ, "relatorios", "diagnostico_pool_2026_09_22.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1, default=str)
    print(f"detalhe: {destino}")
    vazou = final["checkedout"] > 0
    return 1 if (vazou or saida["falhas"]) else 0


if __name__ == "__main__":
    sys.exit(main())

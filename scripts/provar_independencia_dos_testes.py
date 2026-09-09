#!/usr/bin/env python3
"""Prova que a suíte não depende de quanta coisa existe no banco operacional.

## O que este script responde

Quatro testes de guardião leem o banco de **produção** para provar que o histórico herdado
não foi falsificado. A pergunta é legítima. A âncora, até esta rodada, não era: eles
afirmavam `len(cotacoes) == 18`, a contagem **total** da tabela. Funcionou enquanto ninguém
usava o sistema. No dia em que a `ANARA-2026-0019` foi criada, os quatro ficaram vermelhos
sem que nada do histórico tivesse mudado — e passariam a ficar vermelhos a cada cotação nova.

A reancoragem trocou a contagem pela **identidade** do conjunto herdado, que vem de
`relatorios/baseline_fase0.json`. Este script existe para provar que a troca funcionou, e
para que a prova possa ser repetida por qualquer pessoa, a qualquer momento.

## Como a prova é feita

Sem tocar no banco operacional. O script monta um **sandbox**: um diretório temporário onde
todo o repositório aparece por link simbólico, exceto `data/`, que recebe uma **cópia** do
banco. As cotações novas são inseridas na cópia. A suíte roda com o sandbox como diretório
de trabalho, então o `data/anara.db` que os guardiões abrem é a cópia — com registros a mais
do que o banco real tem.

Se a suíte passar assim, ela não mede o tamanho do banco.

No fim, o tamanho, o mtime e o sha256 do banco de produção são conferidos contra os valores
lidos no começo. Qualquer diferença é falha, não aviso.

    python3 scripts/provar_independencia_dos_testes.py
    python3 scripts/provar_independencia_dos_testes.py --cotacoes 25
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANCO = os.path.join(RAIZ, "data", "anara.db")

#: Nunca linkados para dentro do sandbox: `data` é substituído pela cópia, e os caches de
#: bytecode confundiriam o pytest ao rodar de outro diretório.
NAO_LINKAR = {"data", "__pycache__", ".pytest_cache", ".git"}


def _retrato(caminho: str) -> dict:
    """Tamanho, mtime e sha256 — o suficiente para detectar qualquer escrita."""
    with open(caminho, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    st = os.stat(caminho)
    return {"bytes": st.st_size, "mtime": st.st_mtime, "sha256": digest}


def montar_sandbox(destino: str) -> str:
    """Repositório por link simbólico, `data/` por cópia."""
    for nome in os.listdir(RAIZ):
        if nome in NAO_LINKAR:
            continue
        os.symlink(os.path.join(RAIZ, nome), os.path.join(destino, nome))

    os.makedirs(os.path.join(destino, "data"), exist_ok=True)
    copia = os.path.join(destino, "data", "anara.db")
    origem = sqlite3.connect(f"file:{BANCO}?mode=ro", uri=True)
    alvo = sqlite3.connect(copia)
    with alvo:
        origem.backup(alvo)          # API de backup do SQLite: cópia consistente
    origem.close()
    alvo.close()
    return copia


def poluir(copia: str, quantas: int) -> dict:
    """Insere cliente e cotações NOVAS na cópia — o que o uso real do sistema faz.

    Direto em SQL de propósito: o objetivo é simular o estado do banco depois de alguém
    trabalhar, não exercitar o caminho de criação da aplicação. O que está sob teste aqui é
    a suíte, não o cadastro.
    """
    con = sqlite3.connect(copia)
    cur = con.cursor()
    agora = datetime.utcnow().isoformat(" ")

    cur.execute("insert into cliente (nome, ativo, criado_em) values (?, 1, ?)",
                ("Hotel da Prova de Independência", agora))
    cliente_id = cur.lastrowid

    ultimo = cur.execute("select coalesce(max(id), 0) from cotacao").fetchone()[0]
    criadas = []
    for n in range(1, quantas + 1):
        numero = f"PROVA-{n:04d}"
        cur.execute(
            "insert into cotacao (numero, cliente_id, status, condicao_pagamento, "
            "criado_em, revisao, premissas_mantidas_aprovadas) "
            "values (?, ?, 'rascunho', '30', ?, 1, 0)", (numero, cliente_id, agora))
        criadas.append(cur.lastrowid)

    con.commit()
    totais = {
        "cliente_id": cliente_id,
        "cotacoes_criadas": len(criadas),
        "cotacoes_no_banco": cur.execute("select count(*) from cotacao").fetchone()[0],
        "id_antes": ultimo,
    }
    con.close()
    return totais


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cotacoes", type=int, default=7,
                    help="quantas cotações novas inserir na cópia (padrão: 7)")
    a = ap.parse_args()

    if not os.path.exists(BANCO):
        print("banco operacional não encontrado:", BANCO)
        return 2

    antes = _retrato(BANCO)
    print(f"produção  {BANCO}")
    print(f"          {antes['bytes']} bytes · sha256 {antes['sha256'][:16]}…")

    with tempfile.TemporaryDirectory(prefix="anara-prova-") as sandbox:
        copia = montar_sandbox(sandbox)
        print(f"sandbox   {sandbox}")

        con = sqlite3.connect(f"file:{copia}?mode=ro", uri=True)
        original = con.execute("select count(*) from cotacao").fetchone()[0]
        con.close()

        info = poluir(copia, a.cotacoes)
        print(f"cópia     {original} cotações → {info['cotacoes_no_banco']} "
              f"(+{info['cotacoes_criadas']} novas, +1 cliente)")
        print()
        print("rodando a suíte contra a cópia poluída, sem editar nenhum teste…")
        print()

        ambiente = dict(os.environ)
        ambiente["ANARA_DB_URL"] = f"sqlite:///{copia}"
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                              cwd=sandbox, env=ambiente,
                              capture_output=True, text=True)
        saida = (proc.stdout or "") + (proc.stderr or "")
        ultima = [l for l in saida.strip().splitlines() if l.strip()][-1]
        print("   ", ultima)

        falhou = proc.returncode != 0
        if falhou:
            print(f"    (pytest saiu com código {proc.returncode})")
            print()
            for linha in saida.splitlines():
                if linha.startswith(("FAILED", "ERROR")):
                    print("   ", linha)

    depois = _retrato(BANCO)
    intacto = (antes == depois)

    print()
    print("=" * 68)
    print(f"suíte contra banco com +{a.cotacoes} cotações : "
          f"{'PASSOU' if not falhou else 'FALHOU'}")
    print(f"nenhum teste editado                     : sim (a suíte é a do repositório)")
    print(f"banco de produção intacto                : {'sim' if intacto else 'NÃO'}")
    if not intacto:
        print(f"  antes  {antes}")
        print(f"  depois {depois}")
    print("=" * 68)

    if falhou or not intacto:
        print("PROVA FALHOU")
        return 1
    print("PROVA OK — a suíte não depende da quantidade de registros do banco operacional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

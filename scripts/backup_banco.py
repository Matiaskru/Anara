#!/usr/bin/env python3
"""Backup, restore e verificação do banco da Anara — procedimento da Fase 0.

Política curta (a longa está em BACKUP.md):

* backup **nunca apaga** nada por conta própria — retenção só acontece se pedida;
* todo backup é verificado por sha256 logo depois de copiado;
* restore **não sobrescreve** o banco de produção sem `--confirmar`;
* o restore de ensaio (`--ensaio`) restaura numa cópia temporária e compara o digest,
  que é como se prova que o backup presta sem arriscar o banco vivo.

Uso:

    python3 scripts/backup_banco.py estado   [--json ARQ]
    python3 scripts/backup_banco.py backup   [--motivo MOTIVO] [--reter N]
    python3 scripts/backup_banco.py listar
    python3 scripts/backup_banco.py verificar ARQUIVO
    python3 scripts/backup_banco.py restaurar ARQUIVO --ensaio
    python3 scripts/backup_banco.py restaurar ARQUIVO --confirmar
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.fundacao import (  # noqa: E402
    DB_PATH, comparar_estados, estado_banco, resumo_comparacao, sha256_arquivo,
)

BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")
PREFIXO = "anara.db."


# ---------------------------------------------------------------------------
def caminho_backup(motivo: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(BACKUP_DIR, f"{PREFIXO}{motivo}-{stamp}")


def fazer_backup(motivo: str = "manual", reter: int = 0, db_path: str = DB_PATH) -> dict:
    """Copia o banco e confere o sha256 da cópia. `reter=0` = não apaga nada."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    destino = caminho_backup(motivo)
    # Usa a API de backup do próprio SQLite: consistente mesmo com a plataforma aberta,
    # o que `cp` não garante. Cai para cópia de arquivo se o banco não abrir.
    try:
        origem_con = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
        destino_con = sqlite3.connect(destino)
        with destino_con:
            origem_con.backup(destino_con)
        origem_con.close()
        destino_con.close()
        metodo = "sqlite_backup_api"
    except sqlite3.Error:
        shutil.copy2(db_path, destino)
        metodo = "copia_de_arquivo"

    origem_hash = sha256_arquivo(db_path)
    destino_hash = sha256_arquivo(destino)
    # A API de backup reescreve as páginas, então o arquivo pode não bater byte a byte
    # com a origem. O que precisa bater é o **conteúdo**, e isso é o digest por tabela.
    conteudo_igual = comparar_estados(estado_banco(db_path), estado_banco(destino))

    removidos = []
    if reter > 0:
        existentes = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(PREFIXO))
        for antigo in existentes[:max(len(existentes) - reter, 0)]:
            os.remove(os.path.join(BACKUP_DIR, antigo))
            removidos.append(antigo)

    return {
        "origem": os.path.abspath(db_path),
        "destino": destino,
        "metodo": metodo,
        "bytes": os.path.getsize(destino),
        "sha256_origem": origem_hash,
        "sha256_backup": destino_hash,
        "arquivo_identico": origem_hash == destino_hash,
        "conteudo_identico": conteudo_igual["dados_herdados_intactos"]
                             and not conteudo_igual["tabelas_novas"],
        "backups_removidos": removidos,
    }


def listar_backups() -> list:
    if not os.path.isdir(BACKUP_DIR):
        return []
    saida = []
    for nome in sorted(os.listdir(BACKUP_DIR)):
        if not nome.startswith(PREFIXO):
            continue
        caminho = os.path.join(BACKUP_DIR, nome)
        saida.append({"nome": nome, "bytes": os.path.getsize(caminho),
                      "modificado_em": datetime.fromtimestamp(
                          os.path.getmtime(caminho)).isoformat(timespec="seconds")})
    return saida


def verificar(caminho: str) -> dict:
    """`PRAGMA integrity_check` + contagens — um backup ilegível é descoberto aqui."""
    estado = estado_banco(caminho)
    return {"arquivo": caminho, "integridade": estado["integridade"],
            "sha256": estado["arquivo"]["sha256"], "contagens": estado["contagens"]}


def restaurar(caminho_backup_: str, confirmar: bool = False, ensaio: bool = False,
              db_path: str = DB_PATH) -> dict:
    """Restaura um backup.

    `--ensaio` restaura numa cópia temporária e compara com o backup: prova que o
    procedimento funciona sem tocar no banco de produção. Sem `--confirmar` e sem
    `--ensaio` não faz nada — restore silencioso é como se perde dado.
    """
    if not os.path.exists(caminho_backup_):
        raise FileNotFoundError(caminho_backup_)

    estado_origem = estado_banco(caminho_backup_)
    if estado_origem["integridade"] != "ok":
        raise RuntimeError(f"backup corrompido: integrity_check = {estado_origem['integridade']}")

    if ensaio:
        destino = os.path.join(tempfile.mkdtemp(prefix="anara-restore-"), "anara.db")
        shutil.copy2(caminho_backup_, destino)
        estado_destino = estado_banco(destino)
        comp = comparar_estados(estado_origem, estado_destino)
        return {"modo": "ensaio", "backup": caminho_backup_, "destino": destino,
                "integridade": estado_destino["integridade"],
                "sha256_backup": estado_origem["arquivo"]["sha256"],
                "sha256_restaurado": estado_destino["arquivo"]["sha256"],
                "identico": not comp["arquivo_mudou"],
                "contagens": estado_destino["contagens"], "comparacao": comp}

    if not confirmar:
        return {"modo": "recusado", "motivo": "restore sobre o banco de produção exige "
                                              "--confirmar (ou use --ensaio)"}

    seguranca = fazer_backup("antes-de-restaurar", db_path=db_path)
    shutil.copy2(caminho_backup_, db_path)
    estado_final = estado_banco(db_path)
    return {"modo": "producao", "backup_de_seguranca": seguranca["destino"],
            "restaurado_de": caminho_backup_, "integridade": estado_final["integridade"],
            "sha256": estado_final["arquivo"]["sha256"],
            "contagens": estado_final["contagens"]}


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("estado", help="retrato do banco (hash, contagens, digests)")
    s.add_argument("--json", dest="json_saida")
    s.add_argument("--db", default=DB_PATH)

    s = sub.add_parser("backup", help="cópia verificada do banco")
    s.add_argument("--motivo", default="manual")
    s.add_argument("--reter", type=int, default=0,
                   help="quantos backups manter (0 = não apagar nenhum)")
    s.add_argument("--db", default=DB_PATH)

    sub.add_parser("listar", help="backups existentes")

    s = sub.add_parser("verificar", help="integridade de um backup")
    s.add_argument("arquivo")

    s = sub.add_parser("restaurar", help="restaura um backup")
    s.add_argument("arquivo")
    s.add_argument("--ensaio", action="store_true", help="restaura numa cópia e compara")
    s.add_argument("--confirmar", action="store_true", help="sobrescreve o banco de produção")
    s.add_argument("--db", default=DB_PATH)

    a = p.parse_args()

    if a.comando == "estado":
        estado = estado_banco(a.db)
        if a.json_saida:
            os.makedirs(os.path.dirname(os.path.abspath(a.json_saida)), exist_ok=True)
            with open(a.json_saida, "w") as f:
                json.dump(estado, f, ensure_ascii=False, indent=1)
            print(f"estado salvo em {a.json_saida}")
        print(f"arquivo    {estado['arquivo']['caminho']}")
        print(f"md5        {estado['arquivo']['md5']}")
        print(f"sha256     {estado['arquivo']['sha256']}")
        print(f"bytes      {estado['arquivo']['bytes']}")
        print(f"integridade {estado['integridade']}")
        print("contagens:")
        for tabela, n in estado["contagens"].items():
            print(f"  {tabela:24s} {n:6d}")
        if estado["controle"]:
            print(f"controle: {estado['controle']}")

    elif a.comando == "backup":
        r = fazer_backup(a.motivo, reter=a.reter, db_path=a.db)
        print(f"backup     {r['destino']}")
        print(f"metodo     {r['metodo']}")
        print(f"bytes      {r['bytes']}")
        print(f"sha256     {r['sha256_backup']}")
        print(f"conteudo   {'IDÊNTICO' if r['conteudo_identico'] else 'DIVERGENTE'}")
        if r["backups_removidos"]:
            print(f"removidos  {r['backups_removidos']}")

    elif a.comando == "listar":
        for b in listar_backups():
            print(f"  {b['modificado_em']}  {b['bytes']:>9d}  {b['nome']}")

    elif a.comando == "verificar":
        r = verificar(a.arquivo)
        print(f"integridade {r['integridade']}")
        print(f"sha256      {r['sha256']}")
        for tabela, n in r["contagens"].items():
            print(f"  {tabela:24s} {n:6d}")

    elif a.comando == "restaurar":
        r = restaurar(a.arquivo, confirmar=a.confirmar, ensaio=a.ensaio, db_path=a.db)
        print(json.dumps({k: v for k, v in r.items() if k != "comparacao"},
                         ensure_ascii=False, indent=1))
        if r.get("comparacao"):
            print(resumo_comparacao(r["comparacao"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

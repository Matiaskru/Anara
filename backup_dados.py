#!/usr/bin/env python3
"""Cópia diária do banco de dados da plataforma Anara pra uma pasta de backup local.
Mantém os últimos 30 backups (um por dia); apaga os mais antigos automaticamente.
"""
import datetime
import glob
import os
import shutil

DB_PATH = os.path.expanduser("~/Anara-Cotacao/data/anara.db")
BACKUP_DIR = os.path.expanduser("~/Anara-Cotacao-Backups")
MANTER_DIAS = 30


def main():
    if not os.path.exists(DB_PATH):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    hoje = datetime.date.today().isoformat()
    destino = os.path.join(BACKUP_DIR, f"anara_{hoje}.db")
    shutil.copy2(DB_PATH, destino)

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "anara_*.db")))
    excedente = len(backups) - MANTER_DIAS
    for antigo in backups[:max(excedente, 0)]:
        os.remove(antigo)


if __name__ == "__main__":
    main()

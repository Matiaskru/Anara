#!/usr/bin/env python3
"""Sobe a plataforma Anara Cotações (se ainda não estiver rodando) e abre no navegador."""
import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8420
URL = f"http://127.0.0.1:{PORT}/"
LOG_PATH = os.path.join(BASE_DIR, "data", "server.log")


def esta_rodando() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def iniciar_servidor():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    log_file = open(LOG_PATH, "a")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=BASE_DIR, stdout=log_file, stderr=log_file, start_new_session=True,
    )


def main():
    if not esta_rodando():
        iniciar_servidor()
        for _ in range(40):
            time.sleep(0.25)
            if esta_rodando():
                break
    subprocess.run(["open", URL], check=False)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Cria ou atualiza um usuário. É o bootstrap do primeiro OWNER e a ferramenta do dia a dia.

**A senha nunca vem do código nem de argumento de linha de comando.** Argumento aparece no
`ps` e fica no histórico do shell; por isso a senha vem de `ANARA_SENHA_BOOTSTRAP` ou é
digitada sem eco. Não existe senha default: um sistema que nasce com `admin123` nasce aberto.

Uso:

    # primeiro acesso — o OWNER
    ANARA_SENHA_BOOTSTRAP='...' python3 scripts/criar_usuario.py \\
        --email dono@anara.com.br --nome "Matias" --papel OWNER

    # digitando a senha, sem eco e sem ficar no histórico
    python3 scripts/criar_usuario.py --email ana@anara.com.br --nome "Ana" \\
        --papel VENDEDOR_INTERNO

    # trocar a senha de alguém (derruba as sessões abertas dessa pessoa)
    python3 scripts/criar_usuario.py --email ana@anara.com.br --trocar-senha

    # desativar o acesso sem apagar o histórico da pessoa
    python3 scripts/criar_usuario.py --email ana@anara.com.br --desativar
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.auth import hash_senha  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Papel, Usuario  # noqa: E402

PAPEIS = [p.value for p in Papel]


def obter_senha(confirmar: bool = True) -> str:
    """Ambiente primeiro (para automação), depois prompt sem eco. Nunca argumento."""
    senha = os.environ.get("ANARA_SENHA_BOOTSTRAP", "")
    if senha:
        print("  senha lida de ANARA_SENHA_BOOTSTRAP")
        return senha
    senha = getpass.getpass("  senha (não aparece na tela): ")
    if confirmar:
        if senha != getpass.getpass("  repita a senha: "):
            raise SystemExit("As senhas não conferem.")
    return senha


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--nome")
    ap.add_argument("--papel", choices=PAPEIS)
    ap.add_argument("--gerencia-usuarios", action="store_true",
                    help="permissão granular dentro de ADMIN (decisão B)")
    ap.add_argument("--trocar-senha", action="store_true")
    ap.add_argument("--desativar", action="store_true")
    ap.add_argument("--reativar", action="store_true")
    a = ap.parse_args()

    init_db()
    email = a.email.strip().lower()

    with Session(engine) as s:
        u = s.exec(select(Usuario).where(Usuario.email == email)).first()

        if a.desativar or a.reativar:
            if u is None:
                raise SystemExit(f"Usuário {email} não existe.")
            u.ativo = bool(a.reativar)
            # derruba as sessões abertas: desativar sem invalidar o cookie deixaria a
            # pessoa navegando até o cookie expirar
            u.sessao_versao += 1
            s.add(u)
            s.commit()
            print(f"{email}: {'reativado' if u.ativo else 'DESATIVADO'} · "
                  f"sessões anteriores invalidadas")
            return 0

        if u is None:
            if not a.nome or not a.papel:
                raise SystemExit("Usuário novo exige --nome e --papel.")
            print(f"criando {email} ({a.papel})")
            senha = obter_senha()
            u = Usuario(email=email, nome=a.nome, papel=a.papel,
                        senha_hash=hash_senha(senha),
                        can_manage_users=bool(a.gerencia_usuarios),
                        criado_por=os.environ.get("USER", "cli"))
            s.add(u)
            s.commit()
            s.refresh(u)
            print(f"  criado: id={u.id} papel={u.papel} "
                  f"gerencia_usuarios={u.gerencia_usuarios}")
            return 0

        print(f"atualizando {email}")
        if a.nome:
            u.nome = a.nome
        if a.papel:
            u.papel = a.papel
            print(f"  papel → {a.papel}")
        if a.gerencia_usuarios:
            u.can_manage_users = True
        if a.trocar_senha:
            u.senha_hash = hash_senha(obter_senha())
            u.sessao_versao += 1          # senha nova invalida cookie antigo
            print("  senha trocada · sessões anteriores invalidadas")
        u.criado_em = u.criado_em or datetime.utcnow()
        s.add(u)
        s.commit()
        print("  ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

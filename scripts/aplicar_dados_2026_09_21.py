#!/usr/bin/env python3
"""Aplica os dados da virada de 21/09/2026 — preview por padrão, gravação só com `--aplicar`.

    ANARA_DB_URL=sqlite:////caminho/copia.db python3 scripts/aplicar_dados_2026_09_21.py            # preview
    ANARA_DB_URL=sqlite:////caminho/copia.db python3 scripts/aplicar_dados_2026_09_21.py --aplicar  # grava
    python3 scripts/aplicar_dados_2026_09_21.py --etapas politica,fiscal                              # só algumas

Etapas (todas idempotentes — ver `app/dados_2026_09_21.py`):
politica · fiscal · decor · elis · ktc · bl001 · ii_zero · fronhas · catalogo · sinal

Pré-condições conferidas antes de gravar: esquema em 0025 (`alembic upgrade head`), porta 8420
livre (servidor parado), um OWNER para assinar a trilha. O backup é feito antes de gravar
(no diretório do banco e numa cópia fora da poda). Sem `--aplicar`, nada é escrito — nem
mesmo o AuditLog. Cotações, itens, snapshots e aprovações nunca são tocados.

O relatório (JSON) sai em `relatorios/dados_2026_09_21_<preview|aplicado>_<timestamp>.json`.
"""
import argparse
import json
import os
import shutil
import socket
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import dados_2026_09_21 as dados  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import para_float  # noqa: E402
from app.models import Usuario  # noqa: E402

PASTA_MARCOS = os.path.expanduser("~/Anara-Cotacao-Backups")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def porta_livre(porta: int = 8420) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def esquema_pronto() -> bool:
    colunas = {c["name"] for c in inspect(engine).get_columns("cotacaoitem")}
    colunas_cotacao = {c["name"] for c in inspect(engine).get_columns("cotacao")}
    colunas_produto = {c["name"] for c in inspect(engine).get_columns("produto")}
    return ({"preco_tabela", "desconto_vs_tabela_pct", "comissao_faixa_pct",
             "percentual_sinal", "encargo_saldo_pct", "base_comercial_precificacao"} <= colunas
            and "percentual_sinal" in colunas_cotacao and "protecao_comercial_pct" in colunas_produto)


def preview(session: Session, etapas) -> dict:
    saida = {}
    if "politica" in etapas:
        p = dados.plano_politica(session)
        saida["politica"] = {"encerrar_16_09": [r.nome for r in p["encerrar_16_09"]],
                             "criar_21_09": [r["nome"] for r in p["criar_21_09"]],
                             "premissas": [x[0] for x in p["premissas"]]}
    if "fiscal" in etapas:
        saida["fiscal"] = {"ufs": len(dados.fis.BASE_INTERNA), "fcp_aplica": dados.fis.FCP_APLICA,
                           "familias_escopo": len(dados.fis.FAMILIAS_ESCOPO)}
    if "decor" in etapas:
        saida["decor"] = [{"sku": l["produto"].sku_key, "bruto": para_float(l["bruto"]),
                           "cnet": para_float(l["cnet"]), "acao": l["acao"]}
                          for l in dados.plano_decor(session)]
    if "elis" in etapas:
        saida["elis"] = {"sku": dados.ELIS["sku_key"], "custo": dados.ELIS["custo_bruto"]}
    if "ktc" in etapas:
        saida["ktc"] = [{"n": l["linha"][0], "familia": l["linha"][1], "codigo": l["linha"][11],
                         "exw": l["linha"][12],
                         "produto": (l["produto"].sku_key if l["produto"] else None), "acao": l["acao"]}
                        for l in dados.plano_ktc(session)]
    if "bl001" in etapas:
        saida["bl001"] = [{"tipo": a["tipo"], "id": getattr(a["objeto"], "id", None), "sku": a["objeto"].sku_key,
                           "campos": {k: [str(v[0]), str(v[1])[:80]] for k, v in a["campos"].items()}}
                          for a in dados.plano_bl001(session)]
    if "ii_zero" in etapas:
        pz = dados.plano_ii_zero(session)
        from collections import Counter
        saida["ii_zero"] = {"pinar_skus": len(pz["pinar"]),
                            "pinar_por_aliquota": dict(Counter(str(a["pct"]) for a in pz["pinar"])),
                            "pinar_amostra": [{"id": a["produto"].id, "sku": a["produto"].sku_key[:60], "pct": str(a["pct"]), "origem": a["origem"]}
                                              for a in pz["pinar"][:8]],
                            "criar_familias": [(f, v) for f, v in pz["criar_familias"]],
                            "encerrar_ncm": [(r.id, r.familia, r.ncm, r.ii_preferencial) for r in pz["encerrar_ncm"]],
                            "criar_ncm": [(r.familia, r.ncm) for r in pz["criar_ncm"]]}
    if "fronhas" in etapas:
        saida["fronhas"] = [{"tipo": a["tipo"], "id": getattr(a["objeto"], "id", None),
                             "campos": {k: list(v) for k, v in a["campos"].items()}}
                            for a in dados.plano_fronhas(session)]
    if "catalogo" in etapas:
        saida["catalogo"] = "recalcular preco_base = B2B de referência (cenário padrão) de todo ativo"
    if "sinal" in etapas:
        saida["sinal"] = {"desativar": [{"id": c.id, "codigo": c.codigo, "label": c.label}
                                        for c in dados.plano_sinal(session)]}
    return saida


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--etapas", default=",".join(dados.ETAPAS))
    ap.add_argument("--ator-email", default=None)
    ap.add_argument("--sem-backup", action="store_true", help="só para cópias de teste")
    a = ap.parse_args()
    etapas = tuple(e.strip() for e in a.etapas.split(",") if e.strip())
    desconhecidas = set(etapas) - set(dados.ETAPAS)
    if desconhecidas:
        print(f"ERRO: etapas desconhecidas: {sorted(desconhecidas)}")
        return 2

    banco = caminho_do_banco()
    print(f"banco: {banco}")
    if not esquema_pronto():
        print("ERRO: esquema sem as colunas de 21/09 — rode `alembic upgrade head` (0025).")
        return 2
    os.makedirs(os.path.join(RAIZ, "relatorios"), exist_ok=True)
    carimbo = f"{datetime.now():%Y%m%d-%H%M%S}"
    with Session(engine) as s:
        plano = preview(s, etapas)
        caminho = os.path.join(RAIZ, "relatorios", f"dados_2026_09_21_preview_{carimbo}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"banco": banco, "etapas": etapas, "plano": plano}, f, ensure_ascii=False,
                      indent=2, default=str)
        print(f"preview: {caminho}")
        for etapa, conteudo in plano.items():
            resumo = (len(conteudo) if isinstance(conteudo, list) else conteudo)
            print(f"  {etapa}: {resumo if not isinstance(resumo, dict) else json.dumps(resumo, ensure_ascii=False, default=str)[:300]}")
        if not a.aplicar:
            print("\n(preview) — nada foi gravado. Use --aplicar para gravar.")
            return 0
        if not porta_livre():
            print("ERRO: a porta 8420 está em uso — pare o servidor antes de aplicar.")
            return 3
        owners = s.exec(select(Usuario).where(Usuario.papel == "OWNER")).all()
        ator = next((u for u in owners if not a.ator_email or u.email == a.ator_email), None)
        if ator is None:
            print("ERRO: nenhum OWNER encontrado para assinar a trilha.")
            return 4
        if not a.sem_backup and banco.endswith(".db") and os.path.exists(banco):
            from scripts.backup_banco import fazer_backup
            backup = fazer_backup(motivo="antes-dados-2026-09-21", db_path=banco)
            os.makedirs(PASTA_MARCOS, exist_ok=True)
            marco = os.path.join(PASTA_MARCOS, f"anara_dados_2026_09_21_pre_{carimbo}.db")
            shutil.copy2(backup["destino"], marco)
            print(f"backup: {backup['destino']} · marco: {marco}")
        resultado = dados.aplicar_tudo(s, ator, etapas)
        s.commit()
        caminho = os.path.join(RAIZ, "relatorios", f"dados_2026_09_21_aplicado_{carimbo}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"banco": banco, "etapas": etapas, "resultado": resultado}, f,
                      ensure_ascii=False, indent=2, default=str)
        print(f"aplicado: {json.dumps(resultado, ensure_ascii=False, default=str)}")
        print(f"relatório: {caminho}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

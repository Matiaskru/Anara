#!/usr/bin/env python3
"""Aplica no banco de configuração a política comercial de 16/09/2026 — versionando.

    python3 scripts/aplicar_politica_comercial_2026_09_16.py              # preview, nada grava
    python3 scripts/aplicar_politica_comercial_2026_09_16.py --aplicar    # grava, com backup

O que faz, e só isto:

1. **encerra** as 21 regras de margem anteriores (`valid_to = 2026-09-16`) — não apaga, não
   edita o valor: os itens que as pinaram continuam sendo lidos por elas;
2. **cria** uma sucessora por escopo, derivada da anterior (`politica_comercial.regra_da_politica`):
   Daune 12/12/5% travada · Decor 12/10/10% · demais anterior+2 / anterior−1 / 10%;
3. **cria** as premissas `comissao_base_pct = 0,10` e `comissao_min_pct = 0,05`, vigentes de
   16/09/2026;
4. registra cada versão na trilha (`AuditLog`), com fonte e correlação do lote.

Pré-condições, todas conferidas: servidor parado (porta 8420 livre), esquema em 0018,
política ainda não aplicada. O backup é feito antes de gravar, no diretório de produção e
numa cópia fora da poda (`~/Anara-Cotacao-Backups/`). Idempotente: rodar de novo sobre um
banco já aplicado não cria nada.

Nenhuma cotação, item, snapshot ou aprovação é tocado. Nenhum SQL direto.
"""
import argparse
import os
import shutil
import socket
import sys
import uuid
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import admin_service as adm  # noqa: E402
from app import config_service as cfg  # noqa: E402
from app import politica_comercial as pol  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import D, para_float  # noqa: E402
from app.models import Fornecedor, MargemRegra, Usuario  # noqa: E402
from scripts.backup_banco import fazer_backup  # noqa: E402
from scripts.fundacao import sha256_arquivo  # noqa: E402

PASTA_MARCOS = os.path.expanduser("~/Anara-Cotacao-Backups")


def porta_livre(porta: int = 8420) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def esquema_pronto() -> bool:
    colunas = {c["name"] for c in inspect(engine).get_columns("margemregra")}
    return {"piso_pct", "comissao_formacao_pct", "preco_travado", "politica"} <= colunas


def diagnostico(session: Session) -> dict:
    regras = session.exec(select(MargemRegra)).all()
    return {
        "abertas_sem_politica": [r for r in regras if r.politica is None and r.valid_to is None
                                 and r.ativo],
        "com_politica": [r for r in regras if r.politica == pol.ROTULO],
        "premissa_base": cfg.premissa(session, pol.CHAVE_COMISSAO_BASE),
        "premissa_min": cfg.premissa(session, pol.CHAVE_COMISSAO_MINIMA),
    }


def plano(session: Session) -> list:
    codigos = {f.id: f.codigo for f in session.exec(select(Fornecedor)).all()}
    linhas = []
    for r in diagnostico(session)["abertas_sem_politica"]:
        p = pol.regra_da_politica(r.margem_pct, codigos.get(r.fornecedor_id))
        linhas.append((r, p))
    return linhas


def imprimir_plano(session: Session):
    d = diagnostico(session)
    print(f"regras abertas sem política: {len(d['abertas_sem_politica'])} · "
          f"com política: {len(d['com_politica'])} · "
          f"premissas: base={'ok' if d['premissa_base'] else 'ausente'} "
          f"min={'ok' if d['premissa_min'] else 'ausente'}")
    for r, p in plano(session):
        print(f"  #{r.id:<3} {r.nome:42s} {r.margem_pct * 100:5.1f}% → "
              f"{float(p['margem_pct']) * 100:5.1f}%  piso {float(p['piso_pct']) * 100:5.1f}%  "
              f"comissão {float(p['comissao_formacao_pct']) * 100:4.1f}%"
              f"{'  TRAVADO' if p['preco_travado'] else ''}")


def aplicar(session: Session, ator: Usuario) -> dict:
    correlacao = f"politica-comercial-2026-09-16-{uuid.uuid4().hex[:8]}"
    criadas, encerradas = [], []
    for antiga, p in plano(session):
        antiga.valid_to = pol.DATA_VIGENCIA
        session.add(antiga)
        nova = MargemRegra(
            nome=pol.nome_da_regra_nova(antiga.nome), fornecedor_id=antiga.fornecedor_id,
            familia=antiga.familia, sku_key=antiga.sku_key,
            min_thread_count=antiga.min_thread_count, max_thread_count=antiga.max_thread_count,
            margem_pct=para_float(p["margem_pct"]), prioridade=antiga.prioridade,
            valid_from=pol.DATA_VIGENCIA, valid_to=None, ativo=True,
            piso_pct=para_float(p["piso_pct"]),
            comissao_formacao_pct=para_float(p["comissao_formacao_pct"]),
            preco_travado=bool(p["preco_travado"]),
            margem_anterior_pct=antiga.margem_pct, politica=pol.ROTULO, fonte=pol.FONTE,
            notas=f"{pol.FONTE}. Sucede '{antiga.nome}' ({antiga.margem_pct * 100:.0f}%).")
        session.add(nova)
        session.flush()
        adm.registrar(
            session, ator=ator, acao="CRIAR_VERSAO", entidade="MargemRegra",
            entidade_id=nova.id, escopo=f"{nova.nome} (sucede #{antiga.id})",
            antes=(f"{antiga.margem_pct * 100:.2f}% · comissão por faixa · autonomia zero"),
            depois=(f"{nova.margem_pct * 100:.2f}% · piso {nova.piso_pct * 100:.2f}% · "
                    f"comissão de formação {nova.comissao_formacao_pct * 100:.2f}%"
                    f"{' · preço travado' if nova.preco_travado else ''}"),
            motivo=pol.FONTE, origem="script:aplicar_politica_comercial_2026_09_16",
            correlacao=correlacao,
            detalhe={"regra_anterior_id": antiga.id, "valid_from": str(pol.DATA_VIGENCIA),
                     "politica": pol.ROTULO})
        criadas.append(nova)
        encerradas.append(antiga)

    premissas = []
    for chave, valor, descricao in (
            (pol.CHAVE_COMISSAO_BASE, pol.COMISSAO_BASE_PCT,
             "Comissão-base da comissão variável da cotação (itens não-Daune): vale com "
             "desconto zero e cai proporcionalmente ao desconto ponderado por valor."),
            (pol.CHAVE_COMISSAO_MINIMA, pol.COMISSAO_MINIMA_PCT,
             "Comissão mínima da cotação: a comissão variável nunca cai sozinha abaixo disto. "
             "Se com ela algum item ficar abaixo do piso, é exceção a aprovar.")):
        if cfg.premissa(session, chave) is not None:
            continue
        nova = adm.definir_com_vigencia(session, chave, valor_num=para_float(valor),
                                        fonte=pol.FONTE,
                                        notas="Estimativa de pricing sobre a receita comercial "
                                              "(preço × quantidade). Não é a comissão pagável.",
                                        vigente_a_partir_de=pol.DATA_VIGENCIA)
        nova.descricao = descricao
        nova.unidade = "%"
        session.add(nova)
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="Premissa",
                      entidade_id=nova.id, escopo=f"premissa '{chave}' — {adm.ALCANCE_PREMISSA[chave]}",
                      antes="não cadastrada", depois=str(para_float(valor)), motivo=pol.FONTE,
                      origem="script:aplicar_politica_comercial_2026_09_16",
                      correlacao=correlacao,
                      detalhe={"chave": chave, "vigencia_inicio": str(pol.DATA_VIGENCIA)})
        premissas.append(nova)
    return {"correlacao": correlacao, "criadas": criadas, "encerradas": encerradas,
            "premissas": premissas}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--ator-email", default=None,
                    help="e-mail do usuário que assina a trilha (default: o único OWNER)")
    a = ap.parse_args()

    banco = caminho_do_banco()
    print(f"banco: {banco}")
    if not esquema_pronto():
        print("ERRO: esquema sem as colunas da política — rode `alembic upgrade head` (0018).")
        return 2
    with Session(engine) as s:
        imprimir_plano(s)
        d = diagnostico(s)
        if not d["abertas_sem_politica"] and d["premissa_base"] and d["premissa_min"]:
            print("política já aplicada — nada a fazer.")
            return 0
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

        sha_antes = sha256_arquivo(banco)
        backup = fazer_backup(motivo="antes-politica-comercial-2026-09-16", db_path=banco)
        os.makedirs(PASTA_MARCOS, exist_ok=True)
        marco = os.path.join(PASTA_MARCOS,
                             f"anara_fase3a_pre_{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(backup["destino"], marco)
        print(f"backup: {backup['destino']} (sha256 {backup['sha256_backup'][:16]}…, "
              f"conteúdo idêntico: {backup['conteudo_identico']})")
        print(f"marco fora da poda: {marco}")
        print(f"sha256 do banco antes: {sha_antes}")

        resultado = aplicar(s, ator)
        s.commit()
        print(f"\ncorrelação: {resultado['correlacao']}")
        print(f"regras encerradas: {len(resultado['encerradas'])} · criadas: "
              f"{len(resultado['criadas'])} · premissas: {len(resultado['premissas'])}")
        imprimir_plano(s)
    print(f"sha256 do banco depois: {sha256_arquivo(banco)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

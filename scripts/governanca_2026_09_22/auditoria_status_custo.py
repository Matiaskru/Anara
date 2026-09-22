#!/usr/bin/env python3
"""Auditoria de coerência entre o que o CATÁLOGO mostra e o que a COTAÇÃO resolve (22/09/2026).

    ANARA_DB_URL=sqlite:////caminho/copia.db python3 scripts/governanca_2026_09_22/auditoria_status_custo.py [saida.json]

O caso que motivou: BR-001 aparece "preço disponível" no catálogo e "Revisão necessária" ao
entrar na cotação. São duas perguntas diferentes sendo respondidas por duas fontes diferentes —
a coluna `Produto.status_custo` (cache, muitas vezes NULA) e o status canônico que o motor
resolve agora (`status_canonico_do_custo`). Este script compara, SKU a SKU:

* situação que o catálogo mostra hoje (`routers.produtos.situacao_comercial`);
* status canônico vivo, o mesmo que `adicionar_item` congela em `status_custo_item`;
* de onde veio o custo (`net_fonte`), EXW, premissas faltantes, referência vigente;
* se existe `CustoReferencia` vigente que o motor NÃO está usando;
* se existe evidência direta (EXW cotado com data e documento) apesar do status.

Não escreve nada. Classifica cada SKU num diagnóstico e conta os casos.
"""
import json
import os
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)


def diagnosticar(s, p):
    from app import custo_service as cs
    from app import pricing_service as ps
    from app.routers.produtos import situacao_comercial
    custo, mem = ps.custo_para_precificar(s, p)
    vivo = ps.status_canonico_do_custo(custo, mem)
    vigente = cs.referencia_vigente(s, p.id)
    faltantes = mem.get("premissas_faltantes") or []
    # o que a tela MOSTRA agora (motor) e o que ela mostrava pelo cache, lado a lado
    catalogo = situacao_comercial(p, s)
    catalogo_cache = situacao_comercial(p)
    linha = {
        "produto_id": p.id, "sku": p.sku_key, "nome": p.nome, "familia": p.familia,
        "fornecedor_id": p.fornecedor_id, "cost_method": p.cost_method, "ativo": p.ativo,
        "coluna_status_custo": p.status_custo, "precisa_revisao": bool(p.precisa_revisao),
        "custo_unitario_cache": p.custo_unitario, "preco_base": p.preco_base,
        "custo_vivo": custo, "status_vivo": vivo, "net_fonte": mem.get("net_fonte"),
        "exw_usd": mem.get("exw_usd"), "exw_cotado_usd": p.exw_cotado_usd,
        "exw_cotado_data": str(p.exw_cotado_data) if p.exw_cotado_data else None,
        "exw_cotado_fonte": p.exw_cotado_fonte, "exw_frescor": mem.get("exw_frescor"),
        "premissas_faltantes": faltantes, "peso_kg": p.peso_kg, "peso_tipo": p.peso_tipo,
        "situacao_catalogo": catalogo, "situacao_catalogo_pelo_cache": catalogo_cache,
        "referencia_vigente": ({"id": vigente.id, "versao": vigente.versao,
                                "status": vigente.status_custo, "valor": vigente.cnet_brl,
                                "tipo": vigente.tipo, "metodo": vigente.metodo_custo,
                                "documento": vigente.documento, "data": str(vigente.valid_from),
                                "valor_origem": vigente.valor, "moeda": vigente.moeda}
                               if vigente else None),
    }
    # --- diagnósticos (um SKU pode ter mais de um) ---
    d = []
    if catalogo == "DISPONIVEL" and vivo in ("REVIEW_REQUIRED", "A_COTAR"):
        d.append("CATALOGO_DIZ_DISPONIVEL_MOTOR_BLOQUEIA")
    if catalogo in ("REVISAR", "SOB_CONSULTA") and vivo in ("CONFIRMADO", "ESTIMADO"):
        d.append("CATALOGO_ALARMA_MOTOR_RESOLVE")
    if catalogo_cache != catalogo:
        d.append("CACHE_DIVERGIA_DO_MOTOR")
    if vivo == "REVIEW_REQUIRED" and faltantes:
        d.append("REVIEW_POR_PREMISSA_" + "_".join(sorted(faltantes)).upper())
    if vivo == "REVIEW_REQUIRED" and not faltantes and mem.get("net_fonte") == ps.CUSTO_DO_CATALOGO:
        d.append("REVIEW_POR_CUSTO_DE_CATALOGO_SEM_EXW")
    if vivo == "REVIEW_REQUIRED" and not faltantes and mem.get("net_fonte") == ps.CUSTO_HISTORICO_SEM_EVIDENCIA:
        d.append("REVIEW_POR_PRECO_KTC_HISTORICO")
    if p.exw_cotado_usd and p.exw_cotado_data and p.exw_cotado_fonte and vivo == "REVIEW_REQUIRED":
        d.append("TEM_EVIDENCIA_DIRETA_MAS_ESTA_EM_REVIEW")
    if vigente is not None and custo and abs(float(vigente.cnet_brl or 0) - float(custo)) > 0.005:
        d.append("REFERENCIA_VIGENTE_NAO_USADA_PELO_MOTOR")
    if vivo == "A_COTAR":
        d.append("SEM_CUSTO_A_COTAR")
    linha["diagnosticos"] = d or ["OK"]
    return linha


def main(saida=None):
    from sqlmodel import Session, select
    from app.db import caminho_do_banco, engine
    from app.models import Fornecedor, Produto
    linhas, por_diag, por_par = [], Counter(), Counter()
    with Session(engine) as s:
        forn = {f.id: f.codigo for f in s.exec(select(Fornecedor)).all()}
        for p in s.exec(select(Produto).where(Produto.ativo == True)).all():   # noqa: E712
            linha = diagnosticar(s, p)
            linha["fornecedor"] = forn.get(p.fornecedor_id)
            linhas.append(linha)
            for x in linha["diagnosticos"]:
                por_diag[x] += 1
            por_par[(linha["situacao_catalogo"], linha["status_vivo"])] += 1
    incoerentes = [l for l in linhas if "CATALOGO_DIZ_DISPONIVEL_MOTOR_BLOQUEIA" in l["diagnosticos"]
                   or "CATALOGO_ALARMA_MOTOR_RESOLVE" in l["diagnosticos"]]
    resumo = {"banco": caminho_do_banco(), "skus_ativos": len(linhas),
              "por_diagnostico": dict(por_diag),
              "catalogo_x_motor": {f"{k[0]} → {k[1]}": v for k, v in sorted(por_par.items())},
              "incoerentes": len(incoerentes),
              "cache_divergia_do_motor": sum(1 for l in linhas if "CACHE_DIVERGIA_DO_MOTOR" in l["diagnosticos"]),
              "com_evidencia_direta_em_review": sum(
                  1 for l in linhas if "TEM_EVIDENCIA_DIRETA_MAS_ESTA_EM_REVIEW" in l["diagnosticos"]),
              "referencia_nao_usada": sum(
                  1 for l in linhas if "REFERENCIA_VIGENTE_NAO_USADA_PELO_MOTOR" in l["diagnosticos"]),
              "a_cotar": sum(1 for l in linhas if l["status_vivo"] == "A_COTAR")}
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    if saida:
        with open(saida, "w", encoding="utf-8") as f:
            json.dump({"resumo": resumo, "skus": linhas}, f, ensure_ascii=False, indent=1, default=str)
        print(f"detalhe: {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

#!/usr/bin/env python3
"""Classifica e enriquece os SKUs que já existiam no catálogo.

O que faz, por produto:

1. lê a descrição textual **uma única vez** e preenche os campos estruturados;
2. atribui o fornecedor (todo o catálogo atual veio de cotações da KTC);
3. cruza com os documentos de preço, na ordem de confiança: PI 23/08 → cotação 10/08 →
   HAMAN 25/08 (só referência) → preço histórico do próprio catálogo;
4. calcula o EXW industrial quando a família tem fórmula validada;
5. recalcula o CUSTO NET e o preço-base com a margem padrão da regra que se aplica ao produto;
6. marca confiança, frescor e o que precisa de revisão — nada de preço antigo passando por atual.

Uso:
    python3 scripts/classificar_base.py --dry-run     # só relata
    python3 scripts/classificar_base.py               # aplica
"""
import argparse
import os
import re
import sys
from collections import Counter
from datetime import date, datetime

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

from sqlmodel import Session, select

from app import config_service as cfg
from app import pricing_service as ps
from app.db import engine
from app.fontes_ktc import HAMAN_25_08, PI_23_08, itens
from app.matching import COM_DIVERGENCIA, EXATO, melhor_match
from app.migrations import fazer_backup
from app.models import (
    CostConfidence, CostMethod, CustoReferencia, Fornecedor, Produto,
)
from app.pricing_engine import calcular_por_margem
from app.spec_parser import FAMILIAS_CALCULAVEIS, parse_produto
from app.dinheiro import D, divide, para_float  # noqa: E402

DATAS_COTACOES_ANTIGAS = {
    "IQA14052026": date(2026, 5, 14),
    "ELISBRAZIL10062026": date(2026, 6, 10),
    "HE15072026": date(2026, 7, 15),
    "ANARA10082026": date(2026, 8, 10),
}


def data_da_origem(origem: str):
    if not origem:
        return None
    for chave, data in DATAS_COTACOES_ANTIGAS.items():
        if chave in origem:
            return data
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", origem)
    if m:
        return date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def classificar(dry_run: bool = False) -> dict:
    if not dry_run:
        fazer_backup("classificacao")

    itens_pi = list(itens(PI_23_08))
    itens_haman = list(itens(HAMAN_25_08))
    resumo = Counter()
    relatorio = []

    with Session(engine) as s:
        ktc = s.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
        # só o catálogo KTC passa por aqui: fornecedor nacional tem outro caminho de custo e
        # não pode ser reclassificado como se fosse importado
        produtos = [p for p in s.exec(select(Produto)).all()
                    if p.fornecedor_id in (None, ktc.id)]
        cenario = ps.cenario_padrao_catalogo(s)
        regras_comerciais, _ctx = ps.regras_da_cotacao(s, cenario)

        for p in produtos:
            texto = f"{p.nome} · {p.especificacao or ''}"
            dados = parse_produto(p.categoria, p.nome, p.especificacao)
            for campo in ("familia", "subcategoria", "largura_cm", "comprimento_cm", "gsm",
                          "thread_count", "cotton_pct", "poliester_pct", "weave",
                          "plain_or_stripe", "construcao", "acabamento", "material_ref"):
                if getattr(p, campo, None) in (None, "") and dados.get(campo) is not None:
                    setattr(p, campo, dados[campo])
            if p.fornecedor_id is None:
                p.fornecedor_id = ktc.id

            motivos_revisao = []   # bloqueiam: custo não confiável
            observacoes = []       # avisam, mas não impedem cotar

            # ---------- 1. preço cotado: PI 23/08 tem prioridade ----------
            aplicado_pi = False
            match_pi = melhor_match(p, itens_pi, texto)
            if match_pi.casou:
                item = match_pi.item
                preco_atual = p.preco_ktc_usd
                seguro = (match_pi.status == EXATO) or (
                    preco_atual is not None and abs(preco_atual - item["preco_usd"]) < 0.005)
                _registrar_referencia(s, p, item, aplicado=seguro and not dry_run,
                                      confianca=CostConfidence.quoted.value,
                                      notas="; ".join(match_pi.divergencias) or None)
                if seguro:
                    # o documento manda na especificação discriminante quando o casamento foi
                    # aceito: liso/listrado do catálogo era leitura de texto, o da PI é da fábrica
                    if item["plain_or_stripe"] and p.plain_or_stripe != item["plain_or_stripe"]:
                        p.plain_or_stripe = item["plain_or_stripe"]
                    p.exw_cotado_usd = item["preco_usd"]
                    p.exw_cotado_data = item["data"]
                    p.exw_cotado_fonte = item["documento"]
                    p.exw_cotado_cliente = item["cliente"]
                    if item["peso_kg"]:
                        p.peso_kg = item["peso_kg"]
                        p.peso_tipo = "REAL KTC"
                        p.peso_fonte = "PI ANARA 23/08/2026"
                        p.peso_data = item["data"]
                        p.peso_documento = item["documento"]
                    aplicado_pi = True
                    resumo["pi_aplicada"] += 1
                    if match_pi.divergencias:
                        motivos_revisao.append(
                            "PI 23/08 casou com divergência de especificação, mas com o mesmo preço: "
                            + "; ".join(match_pi.divergencias))
                        resumo["pi_divergencia_mesmo_preco"] += 1
                else:
                    motivos_revisao.append(
                        f"PI 23/08 traz US$ {item['preco_usd']:.2f} para um item parecido, mas com "
                        f"divergência técnica ({'; '.join(match_pi.divergencias)}). Não aplicado "
                        "automaticamente — confirmar se é o mesmo produto.")
                    resumo["pi_conflito"] += 1

            # ---------- 2. HAMAN: só referência ----------
            match_haman = melhor_match(p, itens_haman, texto)
            if match_haman.casou:
                _registrar_referencia(s, p, match_haman.item, aplicado=False,
                                      confianca=CostConfidence.estimated.value,
                                      notas="Cotação da KTC para outro cliente (HAMAN GLOBAL) — "
                                            "referência recente, não aplicada. "
                                            + ("; ".join(match_haman.divergencias) or ""))
                resumo["referencia_haman"] += 1

            # ---------- 3. preço histórico do catálogo ----------
            if not aplicado_pi and p.preco_ktc_usd:
                p.exw_cotado_usd = p.preco_ktc_usd
                p.exw_cotado_data = data_da_origem(p.cotacao_origem)
                p.exw_cotado_fonte = p.cotacao_origem or "Catálogo histórico (planilha)"
                p.exw_cotado_cliente = "ANARA"

            # ---------- 3b. peso (real da KTC nunca é substituído) ----------
            if not p.peso_kg or p.peso_tipo != "REAL KTC":
                peso = ps.peso_do_produto(s, p)
                if peso.peso_kg:
                    if p.peso_kg is None or p.peso_tipo != "REAL KTC":
                        p.peso_kg = peso.peso_kg
                        p.peso_tipo = peso.tipo
                        p.peso_fonte = peso.fonte
                        if peso.tipo != "REAL KTC":
                            resumo["peso_estimado"] += 1

            # ---------- 4. EXW calculado ----------
            familia_toalha = p.familia in ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel",
                                           "Wash Cloth", "Face Towel", "Beach Towel")
            calculavel = (p.familia in FAMILIAS_CALCULAVEIS and p.largura_cm and p.comprimento_cm
                          and (p.gsm if familia_toalha else p.material_ref))
            # Acabamento sem custo cadastrado (bordado, zíper, botões...): a base industrial
            # sai, mas ela não é o custo do produto. Fica como referência e o custo continua
            # sendo o preço cotado — melhor "precisa validar" do que um custo por baixo.
            calculo_parcial = bool(calculavel and p.acabamento)
            if calculavel:
                resultado = ps.calcular_exw(s, p)
                if resultado.exw_usd:
                    p.exw_calculado_usd = para_float(resultado.exw_usd)
                    p.exw_calculado_em = datetime.utcnow()
                    if p.exw_cotado_usd:
                        p.exw_diferenca_usd = para_float(
                            D(p.exw_calculado_usd) - D(p.exw_cotado_usd))
                        p.exw_diferenca_pct = (p.exw_calculado_usd / p.exw_cotado_usd) - 1
                    if calculo_parcial:
                        calculavel = False
                        resumo["calculado_parcial"] += 1
                        motivos_revisao.append(
                            f"Acabamento '{p.acabamento}' sem custo cadastrado. O EXW industrial "
                            f"(US$ {p.exw_calculado_usd:.2f}) cobre só a base, sem o acabamento — "
                            "por isso o custo continua sendo o preço cotado. Cadastrar o custo do "
                            "acabamento em 'outros custos' para calcular de verdade.")
                    else:
                        p.cost_method = CostMethod.ktc_calculated.value
                        p.custo_confianca = CostConfidence.calculated.value
                        resumo["calculavel"] += 1
                else:
                    calculavel = False
                    motivos_revisao.extend(resultado.avisos)

            if not calculavel:
                if p.exw_cotado_usd:
                    p.cost_method = CostMethod.ktc_quoted.value
                    p.custo_confianca = CostConfidence.quoted.value
                    resumo["cotado"] += 1
                else:
                    p.cost_method = CostMethod.legacy_excel.value
                    p.custo_confianca = CostConfidence.legacy.value
                    motivos_revisao.append(
                        "Custo veio da planilha antiga sem preço KTC rastreável. Precisa de "
                        "cotação nova ou de dados técnicos para cálculo.")
                    resumo["legado_sem_exw"] += 1

            # ---------- 5. custo de referência exibido ----------
            if p.cost_method == CostMethod.ktc_calculated.value:
                p.custo_ref_valor = p.exw_calculado_usd
                p.custo_ref_tipo = "EXW_CALCULATED"
                p.custo_ref_data = date.today()
                p.custo_ref_documento = "Motor industrial KTC (parâmetros vigentes)"
                p.custo_ref_cliente = "ANARA"
            elif p.exw_cotado_usd:
                p.custo_ref_valor = p.exw_cotado_usd
                p.custo_ref_tipo = "EXW_QUOTED"
                p.custo_ref_data = p.exw_cotado_data
                p.custo_ref_documento = p.exw_cotado_fonte
                p.custo_ref_cliente = p.exw_cotado_cliente
            p.custo_ref_moeda = "USD"

            # ---------- 6. custo NET e preço-base ----------
            custo_antigo = p.custo_unitario
            preco_base_antigo = p.preco_base
            memoria = ps.custo_net(s, p)
            if memoria.get("net_brl"):
                p.custo_unitario = memoria["net_brl"]
                p.custo_net_usd = memoria.get("net_usd")
                if memoria.get("nacionalizacao"):
                    p.frete_usd_un = memoria["nacionalizacao"].get("frete_usd")
                if memoria.get("ncm"):
                    p.ncm = memoria["ncm"]["ncm"] or p.ncm
                    if memoria["ncm"]["ii"] is not None:
                        p.ii_aplicado = memoria["ncm"]["ii"]
            for aviso in memoria.get("avisos", []):
                if aviso not in motivos_revisao:
                    motivos_revisao.append(aviso)

            margem = ps.margem_padrao(s, p)
            p.margem_padrao_pct = para_float(margem.margem_pct)
            if p.custo_unitario:
                res = calcular_por_margem(p.custo_unitario, 1, margem.margem_pct,
                                          regras_comerciais)
                p.preco_base = para_float(res.preco_negociado)

            frescor = ps.frescor(s, p.custo_ref_data)
            if frescor["status"] == "STALE":
                observacoes.append(f"Preço de referência com {frescor['dias']} dias (STALE) — "
                                   "vale confirmar com a KTC antes de cotar volume. Não impede cotar.")
                resumo["stale"] += 1
            elif frescor["status"] == "UNKNOWN":
                resumo["sem_data"] += 1

            p.precisa_revisao = bool(motivos_revisao) or p.cost_method == CostMethod.legacy_excel.value
            texto_motivos = motivos_revisao[:3] + observacoes[:2]
            p.revisao_motivo = " | ".join(texto_motivos) if texto_motivos else None
            if p.precisa_revisao and p.custo_confianca == CostConfidence.legacy.value:
                p.custo_confianca = CostConfidence.review_required.value

            resumo["processados"] += 1
            relatorio.append({
                "id": p.id, "nome": p.nome, "familia": p.familia,
                "cost_method": p.cost_method, "confianca": p.custo_confianca,
                "exw_cotado": p.exw_cotado_usd, "exw_calculado": p.exw_calculado_usd,
                "custo_antes": custo_antigo, "custo_depois": p.custo_unitario,
                "preco_base_antes": preco_base_antigo, "preco_base_depois": p.preco_base,
                "margem_padrao": p.margem_padrao_pct, "frescor": frescor["status"],
                "revisao": p.revisao_motivo,
            })
            if not dry_run:
                s.add(p)

        if dry_run:
            s.rollback()
        else:
            s.commit()

    return {"resumo": dict(resumo), "relatorio": relatorio}


def _registrar_referencia(session, produto, item, aplicado, confianca, notas=None):
    """Guarda o preço observado no histórico do SKU — sem sobrescrever nada."""
    ja_existe = session.exec(
        select(CustoReferencia)
        .where(CustoReferencia.produto_id == produto.id)
        .where(CustoReferencia.documento == item["documento"])
        .where(CustoReferencia.valor == item["preco_usd"])).first()
    if ja_existe:
        return
    session.add(CustoReferencia(
        produto_id=produto.id, sku_key=produto.sku_key, fornecedor_id=produto.fornecedor_id,
        tipo="EXW_QUOTED", valor=item["preco_usd"], moeda="USD", data_ref=item["data"],
        documento=item["documento"], cliente_documento=item["cliente"], confianca=confianca,
        aplicado=aplicado, notas=notas))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    saida = classificar(args.dry_run)
    print("RESUMO:", saida["resumo"])
    mudou = [r for r in saida["relatorio"]
             if r["custo_antes"] and r["custo_depois"]
             and abs(r["custo_depois"] / r["custo_antes"] - 1) > 0.001]
    print(f"\nSKUs com custo alterado: {len(mudou)}")
    for r in sorted(mudou, key=lambda r: -abs(r['custo_depois']/r['custo_antes']-1))[:15]:
        print(f"  [{r['id']:3d}] {r['nome'][:34]:34s} {r['cost_method']:16s} "
              f"{r['custo_antes']:8.2f} → {r['custo_depois']:8.2f} "
              f"({r['custo_depois']/r['custo_antes']-1:+.1%})")

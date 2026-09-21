#!/usr/bin/env python3
"""Fuzz reprodutível do caminho real (cotação → item → negociação → cenário → emissão) sob a
política de 21/09/2026, com oracle independente (`oracle_v2`).

    ANARA_DB_URL=sqlite:////caminho/copia_aplicada.db python3 scripts/politica_2026_09_21/fuzz_v2.py [N] [SEED]

Cada caso: produto × quantidade × destino × contribuinte × finalidade × pagamento × sinal
(0–100%, inclusive nas bordas) × negociação (por preço ou por desconto, dentro e abaixo do B2B)
× troca de cenário (inclusive do sinal) × emissão. Invariantes:

* item nasce no B2B do oracle, tabela = 2 × B2B, desconto 50%, comissão 5%;
* após negociar: faixa = escada(desconto efetivo), comissão = base líquida × faixa, linha
  reconcilia, `requer_aprovacao` ⇔ algum item abaixo do B2B, total = Σ linhas (+ frete manual);
* após mudar o cenário: B2B/tabela refeitos, desconto negociado preservado, preço rederivado;
* emissão: blocker duro nunca passa; CIF automático bloqueia; CIF manual confirmado não;
* sinal: encargo do item = (1 − sinal) × encargo do saldo; sinal 100% zera o encargo mesmo com
  CARTÃO; sinal < 100% com CARTÃO continua bloqueado; sinal válido nunca é blocker; sinal
  maior nunca aumenta o B2B; mudar o sinal preserva o desconto e refaz B2B/tabela/preço.
"""
import json
import os
import random
import sys
import time
from collections import Counter
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from scripts.politica_2026_09_21 import oracle_v2 as orc  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 20260921
rnd = random.Random(SEED)

from sqlmodel import Session, select  # noqa: E402
from tests.crisis.conftest import RequestFalsa, chamar  # noqa: E402

from app import comercial_service as com  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app import workflow_service as ws  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.fiscal_2026_09_21 import FAMILIAS_ESCOPO  # noqa: E402
from app.models import Cliente, CondicaoPagamento, Cotacao, CotacaoItem, EstadoFiscal, Produto, Usuario  # noqa: E402
from app.politica_comercial import ROTULO_2026_09_21  # noqa: E402
from app.routers.cotacoes import adicionar_item, atualizar_cabecalho, editar_item  # noqa: E402

real = os.path.realpath(os.path.join(RAIZ, "data", "anara.db"))
assert os.path.realpath(caminho_do_banco()) != real, "nunca contra o banco real"
UFS = None
FINS = ["REVENDA", "INDUSTRIALIZACAO", "USO_CONSUMO", "ATIVO_IMOBILIZADO"]
CONDS = ["À VISTA", "30", "30/60", "30/60/90", "30/60/90/120", "30/60/90/120/150", "CARTAO"]
falhas, stats = [], Counter()


def falha(tipo, **d):
    falhas.append({"tipo": tipo, "caso": stats["casos"], **{k: str(v)[:160] for k, v in d.items()}})


def main():
    global UFS
    t0 = time.time()
    with Session(engine) as s:
        UFS = [e.estado for e in s.exec(select(EstadoFiscal)).all() if e.ativo]
        pis_nominal = D(ps.cfg.num(s, "pis_cofins_nominal_pct"))
        admin = s.exec(select(Usuario).where(Usuario.papel == "OWNER")).first() or Usuario(
            id=1, email="fuzz@anara.test", nome="Fuzz", senha_hash="h", papel="OWNER", ativo=True, sessao_versao=1)
        admin.can_approve_quotes = True
        cliente = s.exec(select(Cliente)).first()
        condicoes = [x for x in s.exec(select(CondicaoPagamento)).all() if x.ativo]
        produtos = []
        for p in s.exec(select(Produto).where(Produto.ativo == True)).all():   # noqa: E712
            # 22/09/2026: custo REAL (I.I. 0%) forma lucro; a BASE COMERCIAL forma B2B/tabela
            custo, base, mem = ps.bases_de_preco(s, p)
            produtos.append((p, custo, ps.status_canonico_do_custo(custo, mem), base))
        publicaveis = [x for x in produtos if x[2] in ("CONFIRMADO", "ESTIMADO", "REVALIDAR") and x[1]]
        outros = [x for x in produtos if x not in publicaveis]
        print(f"produtos {len(produtos)} · publicáveis {len(publicaveis)} · não {len(outros)}")
        cot, por_cot = None, 0

        def sortear_sinal():
            return rnd.choice([0, 0, 0, 0.1, 0.3, 0.5, 0.75, 1.0, round(rnd.uniform(0.0001, 0.9999), 4),
                               rnd.randint(1, 99) / 100])

        def nova():
            uf = rnd.choice(UFS); contrib = rnd.random() < 0.5; fin = rnd.choice(FINS); cond = rnd.choice(CONDS)
            frete = rnd.choice(["FOB", "FOB", "A_COMBINAR", "CIF", "CIF"])
            manual = frete == "CIF" and rnd.random() < 0.5
            c = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino=uf, contribuinte_icms=contrib,
                        finalidade=fin, condicao_pagamento=cond, status="rascunho", freight_type=frete,
                        percentual_sinal=sortear_sinal(),
                        freight_valor=(float(dinheiro(Decimal(rnd.uniform(50, 2000)))) if manual else None),
                        freight_manual_confirmado=manual, freight_manual_por="fuzz" if manual else None,
                        numero=f"FUZZ21-{stats['casos']}")
            s.add(c); s.commit(); s.refresh(c)
            return c

        def esperado_b2b(p, custo, c, regras):
            natureza, _ = ps.origem_fiscal_do_produto(s, p)
            uf = next(e.uf for e in s.exec(select(EstadoFiscal)).all() if e.estado == c.estado_destino)
            o = orc.fiscal(uf, natureza, c.contribuinte_icms, c.finalidade or "USO_CONSUMO", p.familia in FAMILIAS_ESCOPO)
            if o.get("bloqueado"):
                return None, o
            pc = orc.pis_cofins(pis_nominal, o["icms"], o["fcp"])
            m = ps.margem_padrao(s, p)
            return orc.b2b(D(custo), D(m.margem_pct), o["icms"], pc, regras.encargo_financeiro_pct, o["icms_ded"]), o

        while stats["casos"] < N:
            if cot is None or por_cot >= rnd.choice([1, 2, 3, 5]):
                cot, por_cot = nova(), 0
            stats["casos"] += 1; por_cot += 1
            p, custo, status, base = rnd.choice(outros if rnd.random() < 0.1 else publicaveis)
            qtd = rnd.choice([1, 2, 3, 7, 10, 25, 99, 100, 500, rnd.randint(1, 3000)])
            try:
                chamar(adicionar_item, RequestFalsa(admin), cotacao_id=cot.id, produto_id=p.id, quantidade=float(qtd),
                       modo="margem", valor=None, session=s); s.commit()
            except Exception as e:                                       # noqa: BLE001
                s.rollback(); falha("EXCECAO_ADICIONAR", produto=p.id, erro=f"{type(e).__name__}: {e}"); continue
            it = ws.itens_de(s, cot.id)[-1]
            c = s.get(Cotacao, cot.id)
            regras, ctx = ps.regras_da_cotacao(s, c, p, comissao_formacao_pct=it.comissao_formacao_pct, politica=it.politica_comercial)
            if regras is None or not status in ("CONFIRMADO", "ESTIMADO", "REVALIDAR") or it.politica_comercial != ROTULO_2026_09_21:
                if regras is None and (it.preco_negociado or 0) > 0:
                    falha("BLOQUEADO_COM_PRECO", produto=p.id)
                if regras is None and c.condicao_pagamento == "CARTAO" and D(c.percentual_sinal or 0) >= 1 \
                        and ctx.get("status_pagamento") != "OK":
                    falha("SINAL_100_BLOQUEADO_POR_CARTAO", produto=p.id)
                if regras is None and c.condicao_pagamento != "CARTAO" and ctx.get("status_pagamento") != "OK":
                    falha("SINAL_VALIDO_BLOQUEIA", produto=p.id, sinal=c.percentual_sinal, motivo=ctx.get("motivo_bloqueio"))
                stats["nao_precificados"] += 1
            elif c.condicao_pagamento == "CARTAO" and D(c.percentual_sinal or 0) < 1:
                falha("CARTAO_RESOLVEU_SEM_SINAL_100", produto=p.id, sinal=c.percentual_sinal)
            else:
                ob, o = esperado_b2b(p, base, c, regras)
                if it.custo_unitario is None or dinheiro(D(it.custo_unitario)) != dinheiro(D(custo)):
                    falha("CUSTO_REAL_DIVERGE", produto=p.id, item=it.custo_unitario, esperado=custo)
                if base != custo and (it.base_comercial_precificacao is None or dinheiro(D(it.base_comercial_precificacao)) != dinheiro(D(base))):
                    falha("BASE_COMERCIAL_DIVERGE", produto=p.id, item=it.base_comercial_precificacao, esperado=base)
                if it.preco_b2b_economico is not None and D(it.preco_b2b_economico) > D(it.preco_recomendado):
                    falha("B2B_ECONOMICO_ACIMA_DO_COMERCIAL", produto=p.id)
                if ob is None:
                    falha("ORACLE_BLOQUEIA_MOTOR_RESOLVE", produto=p.id, uf=c.estado_destino); continue
                if dinheiro(D(it.preco_recomendado)) != ob or dinheiro(D(it.preco_negociado)) != ob:
                    falha("B2B_DIVERGE", produto=p.id, motor=it.preco_recomendado, oracle=ob)
                if dinheiro(D(it.preco_tabela)) != orc.tabela(ob):
                    falha("TABELA_DIVERGE", produto=p.id, tabela=it.preco_tabela, oracle=orc.tabela(ob))
                if abs(D(it.desconto_vs_tabela_pct) - Decimal("0.5")) > Decimal("0.0011") or D(it.comissao_faixa_pct) != Decimal("0.05"):
                    falha("NASCE_FORA_DO_B2B", produto=p.id, desc=it.desconto_vs_tabela_pct, faixa=it.comissao_faixa_pct)
                if D(it.icms_base_comissao_pct) != o["icms_ded"]:
                    falha("BASE_COMISSAO_DIVERGE", produto=p.id, motor=it.icms_base_comissao_pct, oracle=o["icms_ded"])
                # sinal: encargo do item = (1 − sinal) × encargo do saldo (tabela), pinos coerentes
                cond_saldo = next((x for x in condicoes if x.codigo == c.condicao_pagamento), None)
                enc_oracle = orc.encargo_efetivo(D(c.percentual_sinal or 0), cond_saldo.encargo_pct if cond_saldo else None)
                if enc_oracle is None or D(it.encargo_pct) != enc_oracle or D(it.percentual_sinal) != D(c.percentual_sinal or 0):
                    falha("ENCARGO_SINAL_DIVERGE", produto=p.id, sinal=c.percentual_sinal, motor=it.encargo_pct, oracle=enc_oracle)
                if D(c.percentual_sinal or 0) < 1 and D(it.encargo_saldo_pct) != D(cond_saldo.encargo_pct):
                    falha("ENCARGO_SALDO_DIVERGE", produto=p.id, motor=it.encargo_saldo_pct, oracle=cond_saldo.encargo_pct)
                stats["com_sinal" if (c.percentual_sinal or 0) > 0 else "sem_sinal"] += 1
                # negociação: preço ou desconto, dentro e abaixo do B2B
                if rnd.random() < 0.8:
                    d = rnd.choice([0, 0.0001, 0.05, 0.1, 0.1001, 0.2, 0.25, 0.3, 0.33, 0.4, 0.45, 0.5, 0.55, 0.7])
                    if rnd.random() < 0.5:
                        prop = {it.id: {"desconto": str(d)}}
                    else:
                        prop = {it.id: {"preco": str(orc.preco_por_desconto(D(it.preco_tabela), Decimal(str(d))))}}
                    try:
                        av = com.aplicar_negociacao(s, s.get(Cotacao, cot.id), prop, ator=admin); s.commit()
                    except Exception as e:                               # noqa: BLE001
                        s.rollback(); falha("EXCECAO_NEGOCIAR", produto=p.id, erro=f"{type(e).__name__}: {e}"); continue
                    a = next(x for x in av.itens if x.item.id == it.id)
                    s.refresh(it)
                    tab = D(it.preco_tabela)
                    d_ef = orc.desconto(D(it.preco_negociado), tab)
                    if D(it.preco_negociado) != orc.preco_por_desconto(tab, Decimal(str(d))):
                        falha("PRECO_DO_DESCONTO_DIVERGE", produto=p.id, d=d, preco=it.preco_negociado, oracle=orc.preco_por_desconto(tab, Decimal(str(d))))
                    if D(it.comissao_faixa_pct) != orc.faixa(d_ef):
                        falha("FAIXA_DIVERGE", produto=p.id, d=d_ef, faixa=it.comissao_faixa_pct, oracle=orc.faixa(d_ef))
                    pc = orc.pis_cofins(pis_nominal, o["icms"], o["fcp"])
                    ol = orc.linha(D(custo), D(it.preco_negociado), D(qtd), o["icms"], pc, regras.encargo_financeiro_pct, orc.faixa(d_ef), o["icms_ded"])
                    if D(it.comissao_valor) != ol["comissao"] or D(it.lucro) != ol["lucro"] or D(it.faturamento) != ol["fat"]:
                        falha("LINHA_DIVERGE", produto=p.id, com=(it.comissao_valor, ol["comissao"]), lucro=(it.lucro, ol["lucro"]))
                    if not a.resultado.reconcilia():
                        falha("NAO_RECONCILIA", produto=p.id)
                    abaixo = D(it.preco_negociado) < D(it.preco_recomendado)
                    if abaixo != (a.linha.comissao_aplicada_pct is not None and any(e.motivo == "PRECO_ABAIXO_B2B" for e in a.excecoes)):
                        falha("EXCECAO_B2B_INCOERENTE", produto=p.id, abaixo=abaixo, exc=[e.motivo for e in a.excecoes])
                    itens = ws.itens_de(s, cot.id)
                    if av.requer_aprovacao != any((i.preco_negociado or 0) > 0 and i.preco_recomendado and D(i.preco_negociado) < D(i.preco_recomendado)
                                                   and i.politica_comercial == ROTULO_2026_09_21 for i in itens):
                        falha("REQUER_APROVACAO_INCOERENTE", cotacao=cot.id)
                    soma = sum(D(i.faturamento) for i in itens)
                    c = s.get(Cotacao, cot.id)
                    frete = dinheiro(D(c.freight_valor)) if (c.freight_type == "CIF" and c.freight_valor) else Decimal(0)
                    if av.subtotal_negociado != soma or av.total_proposta != soma + frete:
                        falha("TOTAL_NAO_E_SOMA", cotacao=cot.id)
                    stats["negociados"] += 1
                    if rnd.random() < 0.3:
                        # quantidade não muda a alavanca (o desconto persistido — digitado ou
                        # efetivo do preço digitado — continua o mesmo)
                        from tests.crisis.conftest import editar_quantidade
                        alavanca = (it.modo_edicao, it.desconto_editado_pct)
                        nova_q = rnd.randint(1, 500)
                        editar_quantidade(s, cot, it, nova_q)
                        s.refresh(it)
                        if (it.modo_edicao, it.desconto_editado_pct) != alavanca or it.quantidade != nova_q:
                            falha("QUANTIDADE_MUDOU_ALAVANCA", produto=p.id, antes=alavanca, depois=(it.modo_edicao, it.desconto_editado_pct))
                # troca de cenário: desconto preservado, B2B refeito
                if rnd.random() < 0.35:
                    from tests.crisis.conftest import salvar_cabecalho
                    c = s.get(Cotacao, cot.id)
                    campo = rnd.choice(["condicao_pagamento", "estado_destino", "contribuinte_icms", "sinal", "sinal"])
                    novo_sinal = sortear_sinal()
                    valor = {"condicao_pagamento": rnd.choice(CONDS[:6]), "estado_destino": rnd.choice(UFS),
                             "contribuinte_icms": "nao" if c.contribuinte_icms else "sim", "sinal": novo_sinal}[campo]
                    d_antes = D(it.desconto_editado_pct) if it.modo_edicao == "desconto" else None
                    b2b_antes, sinal_antes = D(it.preco_recomendado), D(c.percentual_sinal or 0)
                    try:
                        if campo == "sinal":
                            salvar_cabecalho(s, cot, possui_sinal="sim" if novo_sinal > 0 else "",
                                             percentual_sinal=str(round(novo_sinal * 100, 4)) if novo_sinal > 0 else "")
                        else:
                            salvar_cabecalho(s, cot, **{campo: valor})
                    except Exception as e:                               # noqa: BLE001
                        s.rollback(); falha("EXCECAO_CENARIO", erro=f"{type(e).__name__}: {e}"); continue
                    s.expire_all(); it = s.get(CotacaoItem, it.id); c = s.get(Cotacao, cot.id)
                    regras2, ctx2 = ps.regras_da_cotacao(s, c, p, comissao_formacao_pct=it.comissao_formacao_pct, politica=it.politica_comercial)
                    if campo == "sinal" and abs(D(c.percentual_sinal or 0) - D(str(novo_sinal))) > Decimal("0.000001"):
                        falha("SINAL_NAO_GRAVADO", cotacao=cot.id, esperado=novo_sinal, gravado=c.percentual_sinal)
                    if regras2 is not None and it.custo_unitario:
                        ob2, o2 = esperado_b2b(p, it.base_comercial_precificacao or it.custo_unitario, c, regras2)
                        if ob2 is not None and dinheiro(D(it.preco_recomendado)) != ob2:
                            falha("B2B_POS_CENARIO_DIVERGE", produto=p.id, motor=it.preco_recomendado, oracle=ob2, campo=campo)
                        if campo == "sinal" and ob2 is not None and regras is not None:
                            # sinal maior nunca aumenta o B2B (mesma condição do saldo, mesmo fiscal)
                            if D(c.percentual_sinal or 0) > sinal_antes and D(it.preco_recomendado) > b2b_antes:
                                falha("SINAL_MAIOR_AUMENTOU_B2B", produto=p.id, antes=(sinal_antes, b2b_antes), depois=(c.percentual_sinal, it.preco_recomendado))
                            if D(c.percentual_sinal or 0) < sinal_antes and D(it.preco_recomendado) < b2b_antes:
                                falha("SINAL_MENOR_REDUZIU_B2B", produto=p.id, antes=(sinal_antes, b2b_antes), depois=(c.percentual_sinal, it.preco_recomendado))
                        if d_antes is not None:
                            if it.modo_edicao != "desconto" or D(it.desconto_editado_pct) != d_antes:
                                falha("DESCONTO_NAO_PRESERVADO", produto=p.id)
                            if D(it.preco_negociado) != orc.preco_por_desconto(D(it.preco_tabela), d_antes):
                                falha("PRECO_POS_CENARIO_DIVERGE", produto=p.id, preco=it.preco_negociado, tabela=it.preco_tabela, d=d_antes)
                        elif dinheiro(D(it.preco_negociado)) != dinheiro(D(it.preco_recomendado)):
                            falha("ITEM_SEM_NEGOCIACAO_NAO_VOLTOU_AO_B2B", produto=p.id)
                    elif (it.preco_negociado or 0) > 0 and regras2 is None:
                        falha("CENARIO_BLOQUEADO_COM_PRECO", produto=p.id, campo=campo)
                    stats["cenarios"] += 1
            # emissão
            if rnd.random() < 0.08:
                c = s.get(Cotacao, cot.id)
                frete = ws.frete_para_avaliar(s, c)
                pront = ws.avaliar(s, c, frete=frete)
                its = ws.itens_de(s, c.id)
                duros = [i for i in its if i.status_custo_item in ("A_COTAR", "REVIEW_REQUIRED") or (i.preco_negociado or 0) <= 0
                         or "REVIEW_REQUIRED" in (i.status_fiscal or "", i.status_pagamento or "") or i.margem_regra == "SEM_REGRA_DE_MARGEM"]
                cif_auto = c.freight_type == "CIF" and not c.freight_manual_confirmado
                if (duros or cif_auto) and pront.pode_emitir:
                    falha("EMITE_COM_BLOCKER", cotacao=c.id, duros=[i.id for i in duros], cif_auto=cif_auto)
                if c.freight_type == "CIF" and c.freight_manual_confirmado and any(b.codigo.startswith("FRETE") for b in pront.blockers):
                    falha("FRETE_MANUAL_BLOQUEADO", cotacao=c.id)
                if pront.pode_emitir:
                    try:
                        ws.emitir(s, c, ator=admin, frete=frete); s.commit(); stats["emitidas"] += 1
                    except Exception as e:                               # noqa: BLE001
                        s.rollback(); falha("EMISSAO_FALHOU", cotacao=c.id, erro=f"{type(e).__name__}: {e}")
                cot = None
            if stats["casos"] % 250 == 0:
                print(f"  {stats['casos']} casos · {len(falhas)} falhas · {time.time() - t0:.0f}s", flush=True)
    print(json.dumps(dict(stats), ensure_ascii=False))
    print(f"falhas: {len(falhas)} (seed {SEED}, N {N}) em {time.time() - t0:.0f}s")
    print(Counter(f["tipo"] for f in falhas))
    saida = os.path.join(RAIZ, "relatorios", "fuzz_v2_2026_09_21.json")
    with open(saida, "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "n": N, "stats": dict(stats), "falhas": falhas[:300]}, f, ensure_ascii=False, indent=1)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

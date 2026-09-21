#!/usr/bin/env python3
"""Relatórios da virada de 21/09/2026 — sobre uma CÓPIA do banco já migrada e aplicada.

    ANARA_DB_URL=sqlite:////caminho/copia.db python3 scripts/relatorios_2026_09_21.py

Gera em `relatorios/`:
  A. impacto_politica_2026_09_21.md      — produtos ativos: CNET, margem nova, B2B, tabela, status
  B. impacto_ktc_samples_2026_07_29.md   — cotação de amostras KTC × motor (Δ, status)
  C. matriz_fiscal_2026_09_21.md         — 27 UFs × natureza × contribuinte: ICMS, DIFAL, FCP, responsável
  D. migracao_dados_2026_09_21.md        — Decor, ELIS, cotações KTC, fronhas (o que a trilha registrou)
  E. sanity_2026_09_21.md                — os casos-âncora do enunciado, número a número

Só leitura sobre o banco apontado (nada é gravado além dos arquivos em `relatorios/`).
"""
import json
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app import dados_2026_09_21 as dados  # noqa: E402
from app import fiscal_2026_09_21 as fis  # noqa: E402
from app import politica_comercial as pol  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.db import caminho_do_banco, engine  # noqa: E402
from app.dinheiro import D, dinheiro, para_float  # noqa: E402
from app.models import AuditLog, Cotacao, EstadoFiscal, Fornecedor, Produto  # noqa: E402
from app.pricing_engine import calcular_por_preco, preco_b2b, preco_de_tabela  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.path.join(RAIZ, "relatorios")


def brl(v):
    return "—" if v is None else f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v, casas=2):
    return "—" if v is None else f"{float(v) * 100:.{casas}f}%"


def cenario(session, destino="São Paulo", contribuinte=False, finalidade="USO_CONSUMO", condicao="30",
            sinal=0.0):
    return Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino=destino,
                   contribuinte_icms=contribuinte, finalidade=finalidade,
                   condicao_pagamento=condicao, freight_type="FOB", percentual_sinal=sinal)


def precificar(session, produto, cot):
    """(custo REAL, status, regras, ctx, margem, b2b COMERCIAL, tabela) para um produto num cenário.

    22/09/2026: o B2B forma-se sobre a BASE COMERCIAL (referência comercial: EXW + frete +
    proteção comercial + outras); o custo devolvido é o real (I.I. 0%) — é ele que entra em
    lucro e margem realizada. `ultima_base_comercial` guarda a base do último cálculo.
    """
    global ultima_base_comercial
    custo, base, mem = ps.bases_de_preco(session, produto)
    ultima_base_comercial = base
    status = ps.status_canonico_do_custo(custo, mem)
    margem = ps.margem_padrao(session, produto)
    regras, ctx = ps.regras_da_cotacao(session, cot, produto)
    if not custo or custo <= 0 or not margem.tem_regra or regras is None:
        return custo, status, regras, ctx, margem, None, None
    b2b = preco_b2b(base, margem.margem_pct, regras)
    return custo, status, regras, ctx, margem, b2b, preco_de_tabela(b2b.preco_negociado, ctx.get("fator_tabela") or 2)


ultima_base_comercial = None


# ---------------------------------------------------------------------------
# A. impacto nos produtos ativos
# ---------------------------------------------------------------------------
def relatorio_a(session) -> str:
    cot = cenario(session)
    forns = {f.id: f.codigo for f in session.exec(select(Fornecedor)).all()}
    linhas, sem_regra, por_status = [], [], {}
    for p in sorted(session.exec(select(Produto).where(Produto.ativo == True)).all(),  # noqa: E712
                    key=lambda x: (forns.get(x.fornecedor_id, ""), x.familia or "", x.sku_key)):
        custo, status, regras, ctx, margem, b2b, tabela = precificar(session, p, cot)
        por_status[status] = por_status.get(status, 0) + 1
        if not margem.tem_regra:
            sem_regra.append(p)
        linhas.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            forns.get(p.fornecedor_id, "—"), p.familia or "—", p.sku_key[:70].replace("|", "/"),
            brl(custo) if custo else "—", pct(margem.margem_pct) if margem.tem_regra else "SEM REGRA",
            brl(b2b.preco_negociado) if b2b else "—", brl(tabela) if tabela else "—",
            pct(b2b.margem_liquida, 3) if b2b else "—", status))
    cabecalho = [
        "# Impacto da política comercial de 21/09/2026 nos produtos ativos", "",
        f"Banco: `{caminho_do_banco()}` · gerado em {date.today():%d/%m/%Y}.", "",
        "Cenário: SP→SP · não contribuinte · USO_CONSUMO · 30 dias · FOB (cenário padrão do catálogo).",
        "B2B = menor centavo com margem ≥ alvo, comissão 5% sobre a receita líquida de ICMS; tabela = 2 × B2B.", "",
        f"Produtos ativos: **{len(linhas)}** · por status de custo: " + " · ".join(f"{k} {v}" for k, v in sorted(por_status.items())),
        f"Sem regra de margem (bloqueiam a formação automática): **{len(sem_regra)}**"
        + ("" if not sem_regra else " — " + ", ".join(p.sku_key for p in sem_regra)), "",
        "| Fornecedor | Família | SKU | CNET | Margem nova | B2B | Tabela | Margem no B2B | Status custo |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    return "\n".join(cabecalho + linhas) + "\n"


# ---------------------------------------------------------------------------
# B. KTC samples × motor
# ---------------------------------------------------------------------------
def relatorio_b(session) -> str:
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    produtos = session.exec(select(Produto).where(Produto.fornecedor_id == ktc.id)).all()
    linhas, revisar = [], 0
    for linha in dados.KTC_SAMPLES_2026_07_29:
        n, familia, desc, w, l, tc, gsm, alg, poli, listrado, tamanho, cod, exw, obs = linha
        p = dados.casar_produto_ktc(produtos, linha)
        motor = None
        status = "não calculável"
        if p is not None:
            r = ps.calcular_exw(session, p)
            if r.exw_usd is not None:
                motor = D(r.exw_usd)
                delta = motor - D(exw)
                dpct = delta / D(exw)
                if abs(dpct) > D("0.10"):
                    status = "REVISAR (>10%)"; revisar += 1
                elif dpct >= 0:
                    status = "conservador" if dpct > D("0.02") else "próximo"
                else:
                    status = "próximo" if dpct > D("-0.02") else "abaixo — revisar"
                    if dpct <= D("-0.02"):
                        revisar += 1
            else:
                status = "não calculável: " + ", ".join(r.faltando[:2])
        linhas.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            n, familia, desc.replace("|", "/"), cod or "—", f"US$ {exw:.2f}",
            f"US$ {motor:.4f}" if motor is not None else "—",
            f"{(motor - D(exw)):+.4f}" if motor is not None else "—",
            f"{((motor - D(exw)) / D(exw)) * 100:+.2f}%" if motor is not None else "—", status))
    sanity = []
    for rot, w, l, gsm, taxa, esperado in (("Pool towel 100×200 700 g", 100, 200, 700, 14.0, 19.60),
                                          ("Bath towel 70×140 600 g", 70, 140, 600, 8.5, 5.00),
                                          ("Bath towel 70×140 500 g", 70, 140, 500, 8.5, 4.17),
                                          ("Face towel 33×33 600 g", 33, 33, 600, 9.0, 0.59)):
        kg = D(w) * D(l) * D(gsm) / 10_000_000
        sanity.append(f"- {rot}: {kg:.5f} kg × US$ {taxa:.2f}/kg = **US$ {kg * D(taxa):.3f}** (cotação US$ {esperado:.2f})")
    return "\n".join([
        "# Cotação de amostras KTC 29/07/2026 × motor industrial", "",
        "Fonte: Samples Quotation KTC, 29/07/2026, EXW USD, Egito, cliente ANARA. A data da evidência é 29/07/2026.",
        "O motor continua sendo a fonte para produto calculável; a cotação é benchmark. Item não calculável usa a cotação",
        "direta como EXW, sujeito aos gates (peso, II/NCM, frescor — 29/07 já está STALE → REVALIDAR).", "",
        f"Linhas: {len(linhas)} · marcadas para revisão explícita (|Δ| > 10% ou motor abaixo): **{revisar}**", "",
        "| # | Família | Descrição | Código | EXW cotado | EXW motor | Δ | Δ% | Status |",
        "|---:|---|---|---|---:|---:|---:|---:|---|", *linhas, "",
        "## Sanity da fórmula de toalhas (peso × US$/kg)", *sanity, ""])


# ---------------------------------------------------------------------------
# C. fiscal
# ---------------------------------------------------------------------------
def relatorio_c(session) -> str:
    forns = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    estados = sorted(session.exec(select(EstadoFiscal)).all(), key=lambda e: e.uf)
    linhas = []
    for e in estados:
        for natureza, codigo in (("IMPORTADA", "KTC"), ("NACIONAL", "DAUNE")):
            for contrib, fin in ((True, "REVENDA"), (True, "USO_CONSUMO"), (False, "USO_CONSUMO")):
                p = Produto(sku_key="__r", nome="r", fornecedor_id=forns[codigo].id, familia="Flat Sheet")
                regras, ctx = ps.regras_da_cotacao(session, cenario(session, e.estado, contrib, fin), p)
                linhas.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    e.uf, natureza, "sim" if contrib else "não", fin,
                    pct(ctx["icms_pct"]) if regras else "BLOQUEADO",
                    pct(ctx["aliquota_interestadual"]) if ctx.get("aliquota_interestadual") is not None else "—",
                    pct(ctx["difal_pct"]) if ctx.get("difal_pct") is not None else "—",
                    pct(ctx["fcp_pct"]) if ctx.get("fcp_pct") is not None else "—",
                    ctx.get("difal_responsavel") or "—"))
    bench = ["| UF | Base interna (motor) | Interna c/ FCP | FCP escopo | benchmark base simples | benchmark base dupla | benchmark carga final | benchmark FEM |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in fis.resumo(session):
        bench.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["uf"], pct(r["icms_interno_base"]), pct(r["aliquota_interna"]), pct(r["fcp_escopo"]),
            pct(r["benchmark_base_simples"]), pct(r["benchmark_base_dupla"]), pct(r["benchmark_carga_final"]),
            pct(r["benchmark_fem"])))
    return "\n".join([
        "# Matriz fiscal 21/09/2026 — origem SP", "",
        "Base única para não contribuinte: ICMS próprio (interestadual) + DIFAL (interna − interestadual) + FCP, tudo do remetente.",
        "Contribuinte revenda/industrialização: só a interestadual. Contribuinte uso/ativo: interestadual; DIFAL do destinatário (não reduz a margem).",
        "Colunas `benchmark_*` são a tabela 'Projeto Anara — DIFAL' — rastreabilidade; NÃO entram no motor.", "",
        "## O que o motor resolve (família do escopo: Flat Sheet)", "",
        "| UF | Natureza | Contribuinte | Finalidade | ICMS que reduz a receita | Interestadual | DIFAL | FCP | Responsável DIFAL |",
        "|---|---|---|---|---:|---:|---:|---:|---|", *linhas, "",
        "## Base interna cadastrada × benchmark", "", *bench, ""])


# ---------------------------------------------------------------------------
# D. dados migrados
# ---------------------------------------------------------------------------
def relatorio_d(session) -> str:
    trilha = session.exec(select(AuditLog).where(AuditLog.origem == dados.ORIGEM)).all()
    por_acao = {}
    for t in trilha:
        por_acao.setdefault((t.entidade, t.acao), []).append(t)
    partes = ["# Dados migrados em 21/09/2026 (trilha do script)", "",
              f"Banco: `{caminho_do_banco()}` · linhas de AuditLog com origem `{dados.ORIGEM}`: **{len(trilha)}**", ""]
    for (entidade, acao), itens in sorted(por_acao.items()):
        partes.append(f"## {entidade} · {acao} — {len(itens)}")
        partes.append("")
        partes.append("| Escopo | Antes | Depois |")
        partes.append("|---|---|---|")
        for t in itens[:60]:
            partes.append(f"| {(t.escopo or '')[:80].replace('|', '/')} | {(t.versao_anterior or '')[:80].replace('|', '/')} | {(t.versao_nova or '')[:90].replace('|', '/')} |")
        if len(itens) > 60:
            partes.append(f"| … | … | (+{len(itens) - 60}) |")
        partes.append("")
    return "\n".join(partes)


# ---------------------------------------------------------------------------
# E. sanity numérico (casos-âncora do enunciado)
# ---------------------------------------------------------------------------
def relatorio_e(session) -> str:
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    daune = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
    decor = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).first()
    elis = session.exec(select(Fornecedor).where(Fornecedor.codigo == pol.CODIGO_ELIS)).first()

    def acha(fornecedor, familia=None, tc=None, contem=None, largura=None, comprimento=None, gsm=None):
        q = select(Produto).where(Produto.fornecedor_id == fornecedor.id).where(Produto.ativo == True)  # noqa: E712
        for p in session.exec(q).all():
            if familia and (p.familia or "") != familia:
                continue
            if tc is not None and p.thread_count != tc:
                continue
            if largura and (p.largura_cm != largura or p.comprimento_cm != comprimento):
                continue
            if gsm is not None and p.gsm != gsm:
                continue
            if contem and contem.lower() not in (p.nome or "").lower():
                continue
            custo, _ = ps.custo_para_precificar(session, p)
            if custo:
                return p
        return None

    casos = [
        ("1. KTC lençol 190×250 300 fios (#223, Top Sheet) · SP→SP não contribuinte · 30 DD",
         acha(ktc, "Top Sheet", 300, largura=190, comprimento=250), cenario(session), "0.50"),
        ("2. mesmo item · SP→MG contribuinte (revenda)",
         acha(ktc, "Top Sheet", 300, largura=190, comprimento=250), cenario(session, "Minas Gerais", True, "REVENDA"), "0.50"),
        ("3. mesmo item · SP→MG não contribuinte",
         acha(ktc, "Top Sheet", 300, largura=190, comprimento=250), cenario(session, "Minas Gerais", False), "0.50"),
        ("4. Daune", acha(daune), cenario(session), "0.35"),
        ("5. Decor", acha(decor), cenario(session), "0.20"),
        ("6. KTC 400 fios", acha(ktc, "Flat Sheet", 400) or acha(ktc, None, 400), cenario(session), "0.50"),
        ("7a. Toalha KTC (100%)", acha(ktc, "Bath Towel", largura=70, comprimento=140) or acha(ktc, "Bath Towel"), cenario(session), "0.50"),
        ("8a. Fronha standard", acha(ktc, "Pillow Case", contem="fronha"), cenario(session), "0.50"),
        ("9. ELIS cobertor", acha(elis) if elis else None, cenario(session), "0.50"),
        ("10. KTC lençol · SP→RJ não contribuinte (FCP 2%)",
         acha(ktc, "Top Sheet", 300, largura=190, comprimento=250), cenario(session, "Rio de Janeiro", False), "0.50"),
    ]
    partes = ["# Sanity check numérico — 21/09/2026", "",
              "Para cada caso: CNET vivo, cenário fiscal, margem-alvo, B2B (primeiro centavo válido), tabela,",
              "desconto testado, comissão (faixa × base líquida de ICMS), lucro/margem no preço testado, status do custo.", ""]
    for rot, p, cot, desc in casos:
        partes.append(f"## {rot}")
        if p is None:
            partes.append("_produto não encontrado com custo neste banco_\n")
            continue
        custo, status, regras, ctx, margem, b2b, tabela = precificar(session, p, cot)
        partes.append(f"- SKU: `{p.sku_key}` · status do custo: **{status}**")
        if b2b is None:
            partes.append(f"- bloqueado: {ctx.get('motivo_bloqueio') or margem.regra}\n")
            continue
        preco = dinheiro(D(tabela) * (1 - D(desc)))
        c = pol.comissao_do_item(tabela, b2b.preco_negociado, preco, 1, regras.comissao_base_icms_pct)
        r = calcular_por_preco(custo, 1, preco, regras.__class__(
            icms_pct=regras.icms_pct, pis_cofins_pct=regras.pis_cofins_pct,
            encargo_financeiro_pct=regras.encargo_financeiro_pct, comissao_tabela=[(0, c.taxa_pct)],
            comissao_base_icms_pct=regras.comissao_base_icms_pct))
        base = ultima_base_comercial
        anterior = calcular_por_preco(base, 1, b2b.preco_negociado - Decimal("0.01"), regras)
        real_no_b2b = calcular_por_preco(custo, 1, b2b.preco_negociado, regras)
        b2b_eco = preco_b2b(custo, margem.margem_pct, regras)
        mem_custo = ps.custo_net(session, p)
        partes += [
            f"- CNET real: {brl(custo)} (I.I. econômico {pct(mem_custo.get('ii_pct', 0) or 0)}) · fonte: {mem_custo.get('net_fonte')}",
            (f"- base comercial de precificação: {brl(base)} (proteção comercial "
             f"{pct((mem_custo.get('referencia_comercial') or {}).get('protecao_pct'))} — não é custo) · "
             f"B2B econômico (custo real): {brl(b2b_eco.preco_negociado)} · margem realizada no B2B comercial: {pct(real_no_b2b.margem_liquida, 3)}"),
            f"- fiscal: ICMS {pct(ctx['icms_pct'])} (interestadual {pct(ctx.get('aliquota_interestadual'))}, DIFAL {pct(ctx.get('difal_pct'))} {ctx.get('difal_responsavel')}, FCP {pct(ctx.get('fcp_pct'))}) · PIS/COFINS efetivo {pct(ctx['pis_cofins_pct'], 3)} · encargo {pct(ctx['encargo_pct'])} · base da comissão exclui ICMS {pct(regras.comissao_base_icms_pct)}",
            f"- margem-alvo: **{pct(margem.margem_pct)}** ({margem.regra})",
            f"- B2B comercial: **{brl(b2b.preco_negociado)}** (margem sobre a base {pct(b2b.margem_liquida, 4)}; centavo anterior {brl(anterior.preco_negociado)} → {pct(anterior.margem_liquida, 4)} < alvo) · tabela: **{brl(tabela)}**",
            f"- desconto testado {pct(desc)} → preço {brl(preco)} · faixa {pct(c.taxa_pct)} · base {brl(c.base_comissionavel)} · comissão {brl(c.comissao_valor)}",
            f"- no preço testado: lucro real {brl(r.lucro)} · margem realizada {pct(r.margem_liquida, 3)} · {'DENTRO DA AUTONOMIA' if preco >= b2b.preco_negociado else 'REQUER APROVAÇÃO (abaixo do B2B comercial)'}",
            "",
        ]
    # 7b e 8b: toalha 90/10 e fronhas 2/3/4 abas pela calculadora
    from app.calculadora import calcular
    from app.models import MaterialPreco
    t100 = calcular(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="100/0")
    t90 = calcular(session, "Bath Towel", 70, 140, gsm=500, composicao_toalha="90/10")
    partes += ["## 7b. Toalha 70×140 500 g — 100% × 90/10 (calculadora)",
               f"- EXW: US$ {t100['custo']['industrial']['exw_usd']:.4f} × US$ {t90['custo']['industrial']['exw_usd']:.4f} · CNET {brl(t100['custo']['net_brl'])} × {brl(t90['custo']['net_brl'])} · B2B {brl(t100['b2b']['preco_b2b'])} × {brl(t90['b2b']['preco_b2b'])}", ""]
    m = session.exec(select(MaterialPreco).where(MaterialPreco.thread_count == 250).where(MaterialPreco.plain_or_stripe == "plain").where(MaterialPreco.cotton_pct < 1)).first()
    partes.append("## 8b. Fronha 50×70 250 fios CVC 70/30 flap 20 — standard / 2 / 3 / 4 abas (calculadora)")
    for abas in (0, 2, 3, 4):
        f = calcular(session, "Pillow Case", 50, 70, material_id=m.id, abas=abas, flap_cm=20)
        partes.append(f"- {abas} abas: EXW US$ {f['custo']['industrial']['exw_usd']:.10f} · CNET {brl(f['custo']['net_brl'])} · B2B {brl(f['b2b']['preco_b2b'])} · tabela {brl(f['b2b']['preco_tabela'])} · margem {pct(f['margem']['margem_pct'])}")
    partes.append("")
    # 11. sinal / entrada: encargo efetivo = (1 − sinal) × encargo do saldo, no item-âncora
    p223 = acha(ktc, "Top Sheet", 300, largura=190, comprimento=250)
    partes += ["## 11. Sinal / entrada — KTC lençol 190×250 300 fios · SP→SP não contribuinte",
               "encargo efetivo = (1 − sinal) × encargo da condição do saldo; o sinal não carrega encargo e não dá desconto adicional.", ""]
    if p223 is not None:
        for rot, cond, sinal in (("0% + 30/60/90", "30/60/90", 0.0), ("30% de sinal + 70% em 30/60/90", "30/60/90", 0.30),
                                 ("50% de sinal + 50% em 30/60/90", "30/60/90", 0.50), ("50% de sinal + 50% em 30/60", "30/60", 0.50),
                                 ("100% à vista (sinal) — saldo 30/60/90", "30/60/90", 1.0), ("À vista sem sinal", "À VISTA", 0.0)):
            custo, status, regras, ctx, margem, b2b, tabela = precificar(session, p223, cenario(session, condicao=cond, sinal=sinal))
            if b2b is None:
                partes.append(f"- {rot}: bloqueado ({ctx.get('motivo_bloqueio')})")
                continue
            partes.append(f"- {rot}: encargo efetivo **{pct(ctx['encargo_pct'], 2)}** (saldo {pct(ctx.get('encargo_saldo_pct'), 2)}) · "
                           f"B2B **{brl(b2b.preco_negociado)}** · tabela {brl(tabela)} · texto: \"{ctx['condicao_pagamento_texto']}\"")
    partes.append("")
    return "\n".join(partes)


def main():
    os.makedirs(SAIDA, exist_ok=True)
    with Session(engine) as s:
        for nome, fn in (("impacto_politica_2026_09_21.md", relatorio_a),
                         ("impacto_ktc_samples_2026_07_29.md", relatorio_b),
                         ("matriz_fiscal_2026_09_21.md", relatorio_c),
                         ("migracao_dados_2026_09_21.md", relatorio_d),
                         ("sanity_2026_09_21.md", relatorio_e)):
            caminho = os.path.join(SAIDA, nome)
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(fn(s))
            print("gerado:", caminho)


if __name__ == "__main__":
    main()

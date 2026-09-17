#!/usr/bin/env python3
"""Auditoria de crise — seções 9 e 10: fornecedores nacionais (Daune e Decor Tricot).

Somente leitura sobre uma CÓPIA do banco de auditoria (`scripts.crisis.ambiente.preparar`).
Nada em `app/` é alterado; nada é gravado no banco real.

Para cada produto Daune e Decor Tricot:

* lê a referência de custo vigente (`custo_service.referencia_vigente`) e o cache do produto;
* recalcula o CNET **independentemente**, em `decimal`, pelo modelo aprovado no
  SUPER_PROMPT v2 §21 (ICMS crédito 12% sobre o bruto; PIS/COFINS crédito 9,25% sobre o bruto
  líquido de ICMS) — sem chamar `cnet_nacional`;
* casa o bruto gravado com as planilhas-fonte por campos estruturados (família, composição,
  gramatura, dimensão) — nunca por nome; ambiguidade não é resolvida;
* confere a política comercial resolvida e forma o preço recomendado nos benchmarks
  D (SP→SP contribuinte, REVENDA) e E (SP→SP não contribuinte, USO_CONSUMO) pelo motor
  oficial e por fórmula independente, ao centavo (ROUND_HALF_UP).

Saídas (fora do repositório, em AUDIT):
    daune_reconciliacao.csv · decor_reconciliacao.csv · NACIONAIS_RESUMO.md · nacionais_achados.json
"""
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, getcontext

sys.path.insert(0, "/Users/matiaskrueder/Anara-Cotacao")
from scripts.crisis.ambiente import preparar, AUDIT, RAIZ  # noqa: E402

session = preparar("nacionais")

import openpyxl  # noqa: E402
from sqlmodel import select  # noqa: E402

from app import custo_service as cs  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.models import Cotacao, CustoReferencia, Fornecedor, Produto  # noqa: E402
from app.pricing_engine import calcular_por_margem  # noqa: E402

getcontext().prec = 34

# ---------------------------------------------------------------------------
# Premissas independentes (lidas da fonte de verdade de negócio, não do código)
# ---------------------------------------------------------------------------
# SUPER_PROMPT_ANARA_v2.txt §21 — modelo econômico aprovado da Daune:
#   ICMS_credit = gross × 12% ; base_pc = gross − ICMS_credit ; PIS_COFINS_credit = base_pc × 9,25%
#   custo_NET = gross − ICMS_credit − PIS_COFINS_credit
# Confirmado pela própria Daune na aba "Informações" da planilha 12.08.26: "o credito de ICMS é 12%".
ICMS_CREDITO = Decimal("0.12")
PIS_COFINS_CREDITO = Decimal("0.0925")
# SUPER_PROMPT §22 — Decor: "Preço informado pela Decor = custo de aquisição utilizado pelo sistema".
# Nenhum crédito de entrada está aprovado para a Decor: CNET = preço informado.

# Política comercial 16/09/2026 (CLAUDE.md, "Aprovação e política comercial"):
POLITICA = "POLITICA_COMERCIAL_2026-09-16"
ESPERADO = {
    "DAUNE": dict(margem=Decimal("0.12"), piso=Decimal("0.12"), comissao=Decimal("0.05"), travado=True),
    "DECOR_TRICOT": dict(margem=Decimal("0.12"), piso=Decimal("0.10"), comissao=Decimal("0.10"), travado=False),
}

CENT = Decimal("0.01")


def dinheiro(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def D(x):
    """Decimal pela representação textual (nunca Decimal(float))."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def cnet_independente(bruto: Decimal) -> dict:
    icms = bruto * ICMS_CREDITO
    base = bruto - icms
    pc = base * PIS_COFINS_CREDITO
    return {"icms": icms, "base": base, "pis_cofins": pc, "cnet": bruto - icms - pc}


def preco_independente(cnet: Decimal, icms: Decimal, fcp: Decimal, encargo: Decimal,
                       comissao: Decimal, margem: Decimal) -> tuple:
    """preco = CNET / (1 − icms − pis_cofins_efetivo − encargo − comissao − margem),
    com pis_cofins_efetivo = 9,25% × (1 − (icms − fcp))."""
    pc_efetivo = Decimal("0.0925") * (Decimal("1") - (icms - fcp))
    denom = Decimal("1") - icms - pc_efetivo - encargo - comissao - margem
    return dinheiro(cnet / denom), pc_efetivo, denom


# ---------------------------------------------------------------------------
# Fontes — leitura estruturada
# ---------------------------------------------------------------------------
REF = os.path.join(RAIZ, "referencia")
ARQ_LINHA = os.path.join(REF, "Linha Hotelaria - Daune - 12.08.26.xlsx")
ARQ_ANASTACIO = os.path.join(REF, "Projeto Anastacio.xlsx")
ARQ_TROUSSEAU = os.path.join(REF, "tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx")


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def dimensao(texto: str):
    """'190x260' → (190, 260); '1,90x2,60' → (190, 260)."""
    t = (texto or "").strip()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)", t)
    if not m:
        return None, None
    a, b = m.group(1).replace(",", "."), m.group(2).replace(",", ".")
    fa, fb = float(a), float(b)
    if fa < 10:  # metros
        fa, fb = fa * 100, fb * 100
    return int(round(fa)), int(round(fb))


def gramatura(texto: str):
    m = re.search(r"(\d{2,4})\s*(?:gsm|grs|gramas?|gr|g)\b", sem_acento(texto))
    return int(m.group(1)) if m else None


FAMILIA_FONTE = (
    ("capa protetora", "Pillow Protector"),
    ("travesseiro", "Pillow"),
    ("protetor de colch", "Mattress Protector"),
    ("pillow top", "Mattress Topper"),
    ("edredom", "Duvet Insert"),
)


def familia_de(item: str):
    t = sem_acento(item)
    for chave, fam in FAMILIA_FONTE:
        if chave in t:
            return fam
    return None


RUIDO = {"de", "do", "da", "e", "com", "para", "cm", "edredom", "edredons", "insert", "duvet", "travesseiro",
         "travesseiros", "protetor", "protetores", "protetora", "capa", "capas", "colchao", "fronha", "topper",
         "top", "pillow", "single", "queen", "king", "super", "gsm", "grs", "g", "gr", "tamanho"}


def assinatura(texto: str) -> frozenset:
    """Composição/construção normalizada: tira dimensão, gramatura, ruído de família; singulariza."""
    t = sem_acento(texto)
    t = re.sub(r"\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?", " ", t)
    t = re.sub(r"\d{2,4}\s*(?:gsm|grs|gramas?|gr|g)\b", " ", t)
    t = re.sub(r"[^a-z0-9%?]+", " ", t)
    fichas = set()
    for f in t.split():
        if not f or f in RUIDO or f.isdigit():
            continue
        if not f.endswith("%") and len(f) > 3 and f.endswith("s"):
            f = f[:-1]
        fichas.add(f)
    return frozenset(fichas)


def preco_celula(v):
    if isinstance(v, (int, float)):
        return D(v)
    if isinstance(v, str):
        limpo = v.replace("\xa0", " ").replace("R$", "").strip().replace(".", "").replace(",", ".")
        try:
            return Decimal(limpo)
        except Exception:
            return None
    return None


def ler_fontes() -> list:
    itens = []
    wb = openpyxl.load_workbook(ARQ_LINHA, data_only=True)
    for aba in ("Preços Daune 12.08.26", "Preços Daune 27.07.26"):
        for linha in wb[aba].iter_rows(values_only=True):
            item, espec, dim, preco = (list(linha) + [None] * 4)[:4]
            p = preco_celula(preco)
            if p is None or p <= 1 or not isinstance(espec, str):
                continue
            la, co = dimensao(str(dim))
            itens.append(dict(fonte=f"Linha Hotelaria 12.08.26.xlsx · aba {aba.split()[-1]}", aba=aba.split()[-1],
                              familia=familia_de(item), assin=assinatura(f"{item} {espec}"),
                              gram=gramatura(f"{item} {espec}"), larg=la, comp=co, bruto=p,
                              texto=f"{item.strip()} {espec.strip()} {dim}", tipo="BRUTO"))
    wb = openpyxl.load_workbook(ARQ_ANASTACIO, data_only=True)
    for aba in ("Nova Cotação 05.08.26", "Cotação 24.06.26"):
        for linha in wb[aba].iter_rows(values_only=True):
            item, espec, dim, preco = (list(linha) + [None] * 4)[:4]
            p = preco_celula(preco)
            if p is None or p <= 1 or not isinstance(espec, str):
                continue
            la, co = dimensao(str(dim))
            itens.append(dict(fonte=f"Projeto Anastacio.xlsx · aba {aba}", aba=aba,
                              familia=familia_de(item), assin=assinatura(f"{item} {espec}"),
                              gram=gramatura(f"{item} {espec}"), larg=la, comp=co, bruto=p,
                              texto=f"{item.strip()} {espec.strip()} {dim}", tipo="BRUTO"))
    wb = openpyxl.load_workbook(ARQ_TROUSSEAU, data_only=True)
    cat = None
    for linha in wb["Tabela de Preços"].iter_rows(min_row=4, values_only=True):
        c0, desc, tam, preco = (list(linha) + [None] * 6)[:4]
        if c0:
            cat = c0
        p = preco_celula(preco)
        if p is None or not isinstance(desc, str) or not tam:
            continue
        la, co = dimensao(str(tam))
        itens.append(dict(fonte="tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx (Preço Final Anara = VENDA legado, B-17)",
                          aba="trousseau", familia=familia_de(cat), assin=assinatura(f"{cat} {desc}"),
                          gram=gramatura(f"{cat} {desc}"), larg=la, comp=co, bruto=p,
                          texto=f"{cat.strip()} {desc.strip()} {tam}", tipo="VENDA_LEGADO"))
    return itens


FAMILIAS_COM_GRAMATURA = {"Duvet Insert"}

# Metodologia legada (até 09/09/2026) que produziu a coluna "Preço Final Anara" da tabela
# Trousseau: preço a 14% de margem, ICMS 18%, PIS/COFINS 7,59%, encargo 1,6%, comissão 6%
# (B-17, provado em 13 de 13 SKUs). Serve só para reconhecer o número — não para precificar.
FATOR_VENDA_LEGADO = (Decimal("1") - ICMS_CREDITO) * (Decimal("1") - PIS_COFINS_CREDITO) / (
    Decimal("1") - Decimal("0.18") - Decimal("0.0759") - Decimal("0.016") - Decimal("0.06") - Decimal("0.14"))


def casar(produto: Produto, fontes: list) -> dict:
    """Casamento por campos estruturados. Devolve por fonte: {fonte: (status, valores, textos)}.

    status ∈ {MATCH, AMBIGUO, GRAMATURA_NAO_DECLARADA, SEM_MATCH}
    """
    nome = f"{produto.nome} {produto.especificacao or ''}"
    la = int(produto.largura_cm) if produto.largura_cm else None
    co = int(produto.comprimento_cm) if produto.comprimento_cm else None
    if not (la and co):
        la, co = dimensao(nome)
    gram = produto.gsm or gramatura(nome)
    assin = assinatura(nome)
    fam = produto.familia
    resultado = {}
    for fonte in sorted({f["fonte"] for f in fontes}):
        mesma_medida = [f for f in fontes if f["fonte"] == fonte and f["familia"] == fam
                        and f["larg"] == la and f["comp"] == co]
        cands = [f for f in mesma_medida if f["assin"] == assin]
        if not cands:
            resultado[fonte] = ("SEM_MATCH", [], [f"{c['texto']} = {fmt(c['bruto'], 2)}" for c in mesma_medida])
            continue
        if fam in FAMILIAS_COM_GRAMATURA:
            if gram is None:
                resultado[fonte] = ("AMBIGUO", [c["bruto"] for c in cands], [c["texto"] for c in cands])
                continue
            exatos = [c for c in cands if c["gram"] == gram]
            sem_gram = [c for c in cands if c["gram"] is None]
            if exatos:
                cands = exatos
            elif sem_gram:
                resultado[fonte] = ("GRAMATURA_NAO_DECLARADA", [c["bruto"] for c in sem_gram],
                                    [c["texto"] for c in sem_gram])
                continue
            else:
                resultado[fonte] = ("GRAMATURA_DIFERENTE", [c["bruto"] for c in cands],
                                    [f"{c['texto']} ({c['gram']} g) = {fmt(c['bruto'], 2)}" for c in cands])
                continue
        valores = sorted({c["bruto"] for c in cands})
        if len(valores) > 1:
            resultado[fonte] = ("AMBIGUO", valores, [c["texto"] for c in cands])
        else:
            resultado[fonte] = ("MATCH", valores, [c["texto"] for c in cands])
    return resultado


# ---------------------------------------------------------------------------
# Cenários benchmark (não gravados)
# ---------------------------------------------------------------------------
def cenarios():
    return {
        "D": Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino="SP", contribuinte_icms=True,
                     finalidade="REVENDA", condicao_pagamento="30", freight_type="FOB"),
        "E": Cotacao(cliente_id=1, uf_origem_fiscal="SP", estado_destino="SP", contribuinte_icms=False,
                     finalidade="USO_CONSUMO", condicao_pagamento="30", freight_type="FOB"),
    }


def preco_benchmark(produto, custo, margem_pct, cot):
    regras, ctx = ps.regras_da_cotacao(session, cot, produto)
    if regras is None or custo is None or D(custo) <= 0:
        return None, None, ctx
    r = calcular_por_margem(custo, 1, margem_pct, regras)
    return r, regras, ctx


# ---------------------------------------------------------------------------
# Auditoria por produto
# ---------------------------------------------------------------------------
def fmt(x, casas=6):
    if x is None:
        return ""
    if isinstance(x, Decimal):
        if x == 0:
            return "0"
        if abs(x) < Decimal("0.000001"):
            return f"{x:.2E}"
        return f"{x:.{casas}f}".rstrip("0").rstrip(".") if casas else str(x)
    return str(x)


def auditar_fornecedor(codigo: str, fontes: list, achados: list) -> list:
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == codigo)).first()
    produtos = session.exec(select(Produto).where(Produto.fornecedor_id == forn.id).order_by(Produto.id)).all()
    esperado = ESPERADO[codigo]
    cots = cenarios()
    linhas = []
    for p in produtos:
        notas, problemas = [], set()
        vig = cs.referencia_vigente(session, p.id)
        todas = session.exec(select(CustoReferencia).where(CustoReferencia.produto_id == p.id)).all()
        legados = [r for r in todas if r.versao is None and (r.tipo or "") == "SUPPLIER_COST"]
        dim = (f"{int(p.largura_cm)}x{int(p.comprimento_cm)}" if p.largura_cm and p.comprimento_cm else "")
        gram = p.gsm or (gramatura(f"{p.nome} {p.especificacao or ''}") if p.familia in FAMILIAS_COM_GRAMATURA else None)

        # --- custo gravado ---
        bruto_grav = D(vig.valor_bruto) if vig and vig.valor_bruto is not None else None
        cnet_grav = D(vig.cnet_brl) if vig and vig.cnet_brl is not None else None
        status = vig.status_custo if vig else None
        custo_cache = D(p.custo_unitario) if p.custo_unitario is not None else None
        custo_vivo, mem = ps.custo_para_precificar(session, p)
        custo_vivo = D(custo_vivo) if custo_vivo is not None else None

        # --- CNET independente ---
        cnet_ind = None
        if codigo == "DAUNE":
            if bruto_grav is not None:
                cnet_ind = cnet_independente(bruto_grav)["cnet"]
            elif vig is None and custo_cache is not None:
                notas.append("sem referência versionada; custo_unitario sem bruto → não há como derivar CNET")
        else:  # DECOR: CNET = preço informado (SUPER_PROMPT §22)
            fonte_custo = D(p.custo_ref_valor) if p.custo_ref_valor is not None else None
            bruto_grav = fonte_custo
            cnet_ind = fonte_custo
            if vig is None:
                notas.append("Decor sem CustoReferencia versionada (vigente=None); custo vem só de Produto.custo_unitario")
                status = f"(sem versão; Produto.status_custo={p.status_custo}; item novo recebe {ps.status_canonico_do_custo(p.custo_unitario, mem)} via status_canonico_do_custo)"
            if fonte_custo is not None:
                cnet_grav = custo_cache
        dif = (cnet_grav - cnet_ind) if (cnet_grav is not None and cnet_ind is not None) else None
        if dif is not None and abs(dif) > Decimal("0.000001"):
            problemas.add("DIVERGE"); notas.append(f"CNET gravado ≠ recalculado (Δ={dif})")
        if cnet_ind is not None and custo_cache is not None and abs(custo_cache - cnet_ind) > Decimal("0.000001"):
            problemas.add("DIVERGE"); notas.append(f"Produto.custo_unitario ({custo_cache}) ≠ CNET recalculado ({cnet_ind})")
        if cnet_ind is not None and custo_vivo is not None and abs(custo_vivo - cnet_ind) > Decimal("0.000001"):
            problemas.add("DIVERGE"); notas.append(f"custo_para_precificar ({custo_vivo}) ≠ CNET recalculado ({cnet_ind})")
        if vig is not None and custo_cache is not None and cnet_grav is not None and abs(custo_cache - cnet_grav) > Decimal("0.000001"):
            problemas.add("DIVERGE"); notas.append("cache Produto.custo_unitario ≠ cnet_brl da versão vigente")
        if p.custo_ref_valor is not None and bruto_grav is not None and codigo == "DAUNE" and abs(D(p.custo_ref_valor) - bruto_grav) > Decimal("0.005"):
            notas.append(f"Produto.custo_ref_valor ({p.custo_ref_valor}) ≠ valor_bruto vigente ({bruto_grav}) [cache]")

        # --- fonte ---
        bruto_fonte, fontes_txt = "", []
        if codigo == "DAUNE":
            cas = casar(p, fontes)
            brutos_fonte = {}
            ha_match_declarado = any(st == "MATCH" and "VENDA" not in fonte for fonte, (st, _, _) in cas.items())
            outras_medidas = []
            for fonte, (st, valores, textos) in cas.items():
                if st == "SEM_MATCH":
                    if textos and "VENDA" not in fonte:
                        outras_medidas.append(f"{fonte.split('aba ')[-1]}: {textos}")
                    continue
                if st == "GRAMATURA_DIFERENTE":
                    notas.append(f"{fonte.split('aba ')[-1]}: mesma medida e composição, mas gramatura declarada DIFERENTE "
                                 f"de {gram} g: {textos}")
                    continue
                rot = fonte.split(" · ")[0].replace(".xlsx", "")
                if "trousseau" in fonte.lower():
                    rot = "Trousseau(VENDA legado)"
                if "Anastacio" in fonte:
                    rot = "Anastacio " + fonte.split("aba ")[-1]
                elif "Linha Hotelaria" in fonte:
                    rot = "Linha " + fonte.split("aba ")[-1]
                fontes_txt.append(f"{rot}: {st} {[fmt(v, 2) for v in valores]}")
                if st in ("AMBIGUO",):
                    problemas.add("AMBIGUO"); notas.append(f"{rot}: casamento ambíguo {[fmt(v,2) for v in valores]} — {textos}")
                elif st == "GRAMATURA_NAO_DECLARADA":
                    # o mesmo preço aparece em alguma fonte COM gramatura declarada?
                    declarados = sorted({f"{f['gram']} g em {f['fonte'].split('aba ')[-1]}" for f in fontes
                                         if f["tipo"] == "BRUTO" and f["gram"] is not None and f["familia"] == p.familia
                                         and f["larg"] == int(p.largura_cm) and f["comp"] == int(p.comprimento_cm)
                                         and f["assin"] == assinatura(f"{p.nome} {p.especificacao or ''}")
                                         and f["bruto"] == valores[0]})
                    coinc = (f"; o MESMO preço {fmt(valores[0],2)} está declarado como {declarados}" if declarados else "")
                    if ha_match_declarado:
                        rel = ("= bruto vigente" if bruto_grav is not None and valores[0] == bruto_grav
                               else f"≠ bruto vigente {fmt(bruto_grav,2)}")
                        notas.append(f"{rot}: também lista {dim} sem gramatura a {fmt(valores[0],2)} ({rel}; {textos[0]}){coinc}")
                    else:
                        problemas.add("AMBIGUO")
                        notas.append(f"{rot}: única fonte com o bruto {fmt(valores[0],2)} NÃO declara gramatura ({textos[0]}); "
                                     f"SKU é {gram} g — casamento não conclusivo{coinc}")
                elif st == "MATCH":
                    if "VENDA" in fonte:
                        implicito = valores[0] / FATOR_VENDA_LEGADO
                        if bruto_grav is not None:
                            desvio = abs(implicito - bruto_grav) / bruto_grav
                            notas.append(f"Trousseau 'Preço Final Anara' {fmt(valores[0],2)} = venda legada; bruto implícito {fmt(implicito,2)} "
                                         f"({'coerente' if desvio < Decimal('0.002') else 'INCOERENTE'} com bruto vigente)")
                            if desvio >= Decimal("0.002"):
                                problemas.add("DIVERGE")
                    else:
                        brutos_fonte[rot] = valores[0]
            if brutos_fonte:
                distintos = sorted(set(brutos_fonte.values()))
                if len(distintos) > 1:
                    problemas.add("DIVERGE"); notas.append(f"fontes BRUTAS discordam entre si: {brutos_fonte}")
                bruto_fonte = "; ".join(f"{k}={fmt(v,2)}" for k, v in brutos_fonte.items())
                if bruto_grav is not None and any(abs(v - bruto_grav) > Decimal("0.005") for v in brutos_fonte.values()):
                    problemas.add("DIVERGE"); notas.append(f"bruto gravado {fmt(bruto_grav,2)} ≠ bruto de fonte {brutos_fonte}")
                if bruto_grav is None and status == "A_COTAR":
                    notas.append(f"há bruto na fonte ({brutos_fonte}) mas o SKU está A_COTAR")
            elif bruto_grav is not None:
                if "AMBIGUO" not in problemas:
                    problemas.add("SEM_FONTE"); notas.append("bruto gravado não casa com nenhuma planilha em referencia/ por campos estruturados")
            if outras_medidas and not brutos_fonte:
                notas.append(f"candidatos na mesma medida com assinatura técnica diferente (não casados): {' / '.join(outras_medidas)}")
            # a fonte declarada pela versão vigente bate com uma fonte casada?
            if vig is not None and vig.documento and brutos_fonte:
                doc = vig.documento.lower()
                if "anastacio" in doc and not any("Anastacio" in k for k in brutos_fonte):
                    problemas.add("DIVERGE"); notas.append("versão vigente declara Projeto Anastacio, mas o SKU não casa nessa planilha")
                if "linha hotelaria" in doc and not any("Linha" in k for k in brutos_fonte):
                    problemas.add("DIVERGE"); notas.append("versão vigente declara Linha Hotelaria, mas o SKU não casa nessa planilha")
                if vig.origem_registro and "27.07.26" in vig.origem_registro and vig.data_ref and vig.data_ref.isoformat() == "2026-08-12":
                    notas.append("rastreabilidade: valor vem da aba 27.07.26 mas data_ref gravada = 12/08/2026 (data do arquivo)")
        else:
            # Decor: a fonte declarada é "ORÇAMENTO ANARA - 240826 (Decor Tricot)" — não está em referencia/.
            # O único rastro é o literal em scripts/importar_fornecedores_nacionais.py:62-73.
            literal = LITERAL_DECOR.get(p.sku_key)
            if literal is not None:
                bruto_fonte = f"literal script={fmt(literal,2)}"
                if bruto_grav is not None and abs(literal - bruto_grav) > Decimal("0.005"):
                    problemas.add("DIVERGE"); notas.append("custo gravado ≠ literal do script de importação")
                problemas.add("SEM_FONTE")
                notas.append("documento 'ORÇAMENTO ANARA - 240826' não existe em referencia/; valor rastreável só ao literal em scripts/importar_fornecedores_nacionais.py:62-73")
            else:
                problemas.add("SEM_FONTE"); notas.append("sem custo, sem documento, sem medida (custo_ref_documento=%r)" % p.custo_ref_documento)

        # --- política ---
        m = ps.margem_padrao(session, p)
        pol = f"{m.politica} · margem {fmt(m.margem_pct,4)} · piso {fmt(m.piso_pct,4)} · comissão {fmt(m.comissao_formacao_pct,4)} · travado {m.preco_travado} · regra#{m.regra_id}"
        pol_ok = (m.politica == POLITICA and m.margem_pct == esperado["margem"] and m.piso_pct == esperado["piso"]
                  and m.comissao_formacao_pct == esperado["comissao"] and bool(m.preco_travado) == esperado["travado"])
        if not pol_ok:
            problemas.add("DIVERGE"); notas.append(f"política resolvida ≠ esperada {esperado}")
        if p.margem_padrao_pct is not None and D(p.margem_padrao_pct) != m.margem_pct:
            notas.append(f"cache Produto.margem_padrao_pct={p.margem_padrao_pct} ≠ política vigente {fmt(m.margem_pct,2)}")

        # --- preços benchmark ---
        precos = {}
        for k, cot in cots.items():
            r, regras, ctx = preco_benchmark(p, custo_vivo, m.margem_pct, cot)
            if r is None:
                precos[k] = (None, None, ctx.get("motivo_bloqueio") or ctx.get("status_fiscal"))
                continue
            oficial = D(r.preco_negociado)
            ind, pc_ef, denom = preco_independente(custo_vivo, D(ctx["icms_pct"]), D(ctx["fcp_pct"] or 0),
                                                   D(ctx["encargo_pct"]), m.comissao_formacao_pct, m.margem_pct)
            # conferências cruzadas de premissa
            if D(regras.pis_cofins_pct) != pc_ef:
                problemas.add("DIVERGE"); notas.append(f"[{k}] PIS/COFINS efetivo do motor {regras.pis_cofins_pct} ≠ independente {pc_ef}")
            if D(ctx["comissao_formacao_pct"]) != m.comissao_formacao_pct:
                problemas.add("DIVERGE"); notas.append(f"[{k}] comissão de formação no contexto {ctx['comissao_formacao_pct']} ≠ política {m.comissao_formacao_pct}")
            if oficial != ind:
                problemas.add("DIVERGE"); notas.append(f"[{k}] preço oficial {oficial} ≠ independente {ind}")
            precos[k] = (oficial, ind, f"icms {ctx['icms_pct']} fcp {ctx['fcp_pct']} enc {ctx['encargo_pct']} pc_ef {fmt(pc_ef,6)} denom {fmt(denom,6)}")
        if custo_vivo is None or D(custo_vivo) <= 0 or status == "A_COTAR":
            problemas.add("BLOQUEADO")
            if p.preco_base:
                notas.append(f"sem custo/A_COTAR mas Produto.preco_base={p.preco_base:.2f} (usado como preço do item em cotacoes.py:957-960)")
        # preco_base cache × recomendado
        if p.preco_base and precos.get("D", (None,))[0]:
            pb, rec = D(p.preco_base), precos["D"][0]
            if abs(pb - rec) > Decimal("0.005"):
                notas.append(f"Produto.preco_base={fmt(dinheiro(pb),2)} (cache, exibido ao vendedor) ≠ recomendado D {rec} ({fmt((pb/rec-1)*100,1)}%)")
        # flags de cache
        if p.precisa_revisao and status == "CONFIRMADO":
            notas.append(f"precisa_revisao=True com vigente CONFIRMADO — motivo: {(p.revisao_motivo or '')[:90]}")
        if p.custo_ref_tipo == "A_COTAR" and status == "CONFIRMADO":
            notas.append("cache Produto.custo_ref_tipo='A_COTAR' com vigente CONFIRMADO")

        # --- situação ---
        for s in ("BLOQUEADO", "DIVERGE", "AMBIGUO", "SEM_FONTE"):
            if s in problemas:
                situacao = s
                break
        else:
            situacao = "OK"
        pD, pE = precos.get("D", (None, None, None)), precos.get("E", (None, None, None))
        linhas.append({
            "id": p.id, "sku": p.sku_key, "nome": p.nome, "ativo": p.ativo, "familia": p.familia,
            "dimensao": dim, "gramatura": gram or "",
            "bruto_gravado": fmt(bruto_grav, 2), "bruto_fonte": bruto_fonte,
            "cnet_gravado": fmt(cnet_grav, 10), "cnet_recalculado": fmt(cnet_ind, 10),
            "diferenca_cnet": fmt(dif, 14), "custo_unitario_cache": fmt(custo_cache, 10),
            "custo_para_precificar": fmt(custo_vivo, 10),
            "status_custo": status or "", "ref_id": vig.id if vig else "", "versao": vig.versao if vig else "",
            "documento": vig.documento if vig else (p.custo_ref_documento or ""),
            "data_ref": vig.data_ref.isoformat() if vig and vig.data_ref else (p.custo_ref_data.isoformat() if p.custo_ref_data else ""),
            "origem_registro": (vig.origem_registro or "") if vig else "",
            "politica_aplicada": pol,
            "preco_D_oficial": fmt(pD[0], 2), "preco_D_independente": fmt(pD[1], 2),
            "preco_E_oficial": fmt(pE[0], 2), "preco_E_independente": fmt(pE[1], 2),
            "premissas_D": pD[2] or "", "preco_base_cache": fmt(D(p.preco_base), 2) if p.preco_base else "",
            "fontes_casadas": " | ".join(fontes_txt),
            "situacao": situacao, "notas": " || ".join(notas),
        })
    return linhas


# Literal do script de importação (scripts/importar_fornecedores_nacionais.py:62-73) — a única
# "fonte" da Decor presente no repositório.
LITERAL_DECOR = {}
for _tam, _l, _c, _precos in (("0,60x1,90", 60, 190, ["130.58", "128.36", "128.36", "127.32"]),
                              ("0,60x2,40", 60, 240, ["164.95", "162.14", "162.14", "160.83"]),
                              ("0,60x2,70", 60, 270, ["185.56", "182.41", "182.41", "180.94"]),
                              ("0,60x2,85", 60, 285, ["195.87", "192.54", "192.54", "190.99"])):
    for _modelo, _preco in zip(["RELEVO", "SOFIA", "AREZZO", "SISSI"], _precos):
        LITERAL_DECOR[f"DECOR_TRICOT · Peseira · {_modelo} · {_tam}"] = Decimal(_preco)


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------
COLS = ["id", "sku", "nome", "ativo", "familia", "dimensao", "gramatura", "bruto_gravado", "bruto_fonte",
        "cnet_gravado", "cnet_recalculado", "diferenca_cnet", "custo_unitario_cache", "custo_para_precificar",
        "status_custo", "ref_id", "versao", "documento", "data_ref", "origem_registro", "politica_aplicada",
        "preco_D_oficial", "preco_D_independente", "preco_E_oficial", "preco_E_independente", "premissas_D",
        "preco_base_cache", "fontes_casadas", "situacao", "notas"]


def escrever_csv(caminho, linhas):
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for l in linhas:
            w.writerow({k: l.get(k, "") for k in COLS})


def tabela_md(linhas, cols, cab):
    out = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for l in linhas:
        out.append("| " + " | ".join(str(l.get(c, "")).replace("|", "/") for c in cols) + " |")
    return "\n".join(out)


def main():
    fontes = ler_fontes()
    achados = []
    daune = auditar_fornecedor("DAUNE", fontes, achados)
    decor = auditar_fornecedor("DECOR_TRICOT", fontes, achados)
    escrever_csv(os.path.join(AUDIT, "daune_reconciliacao.csv"), daune)
    escrever_csv(os.path.join(AUDIT, "decor_reconciliacao.csv"), decor)

    cd, ce = Counter(l["situacao"] for l in daune), Counter(l["situacao"] for l in decor)

    # ---------------- achados ----------------
    def ids(pred, base):
        return [l["id"] for l in base if pred(l)]

    # NAC-01 — 280 g sem fonte documental e com preço idêntico ao 180 g do fornecedor
    p280 = [l for l in daune if l["gramatura"] == 280]
    por_dim_180 = {l["dimensao"]: l["bruto_gravado"] for l in daune if l["gramatura"] == 180 and "poli" in l["sku"].lower()}
    coincid = [(l["id"], l["dimensao"], l["bruto_gravado"]) for l in p280 if por_dim_180.get(l["dimensao"]) == l["bruto_gravado"]]
    achados.append({
        "codigo": "NAC-01", "gravidade": "P1",
        "detalhe": ("Linha 'Edredom 100% poliéster 280 g' (9 SKUs) não tem fonte documental: a aba 12.08.26 da Linha "
                    "Hotelaria lista esses nove preços SEM gramatura; a atribuição de 280 g é literal de código "
                    "(scripts/reconciliar_daune.py:76 GRAMATURA_POLIESTER_12_08 = 280; scripts/cadastrar_edredom_280g.py:39-56, "
                    "'informados pelo responsável do projeto em 03/09/2026'). A cotação da própria Daune em "
                    "'Projeto Anastacio.xlsx · Nova Cotação 05.08.26' precifica '180GSM 100% fibras de poliester' com EXATAMENTE "
                    f"os mesmos valores nas três medidas coincidentes {coincid} e cobra MAIS pelo 250GSM (518,70/793,01/791,70). "
                    "Nenhuma fonte em referencia/ menciona 280 g (a tabela Trousseau pergunta 'qtas gramas?' e só pede 180 gr e 250 gr). "
                    "Consequência: o catálogo tem dois SKUs (180 g e 280 g) com o mesmo custo e o mesmo preço recomendado para a mesma "
                    "medida/composição — ou o 280 g está rotulado errado, ou a Daune cobra o mesmo por 180 g e 280 g, o que contradiz "
                    "a própria escala de preços dela. Casamento por gramatura é AMBIGUO; não concluir sem confirmação escrita da Daune."),
        "produtos": [l["id"] for l in p280],
    })
    # NAC-02 — Decor: custo não versionado + precisa_revisao → CONFIRMADO no item
    achados.append({
        "codigo": "NAC-02", "gravidade": "P1",
        "detalhe": ("Decor Tricot: os 16 SKUs de peseira não têm CustoReferencia versionada (referencia_vigente=None, "
                    "Produto.status_custo=None) e estão marcados precisa_revisao=True com o motivo 'confirmar se é custo de compra "
                    "ou preço de venda'. Mesmo assim, ao entrar numa cotação, o item recebe status CONFIRMADO: "
                    "app/routers/cotacoes.py:931 chama app/pricing_service.py:status_canonico_do_custo, que em "
                    "app/pricing_service.py:557-558 traduz net_fonte=CUSTO_CADASTRADO_DO_FORNECEDOR com valor positivo em "
                    "CONFIRMADO sem olhar precisa_revisao nem a ausência de versão. Um custo cuja semântica (custo × venda) ainda "
                    "não foi confirmada passa por todos os portões (compromisso firme, WON) como se tivesse evidência viva. "
                    "Viola 'ESTIMADO nunca é promovido a CONFIRMADO em silêncio' e 'toda referência de custo responde de onde veio'."),
        "produtos": ids(lambda l: l["bruto_gravado"] != "", decor),
    })
    achados.append({
        "codigo": "NAC-03", "gravidade": "P2",
        "detalhe": ("Decor Tricot SEM_FONTE: o documento declarado em custo_ref_documento ('ORÇAMENTO ANARA - 240826 (Decor Tricot)') "
                    "não existe em referencia/ nem em nenhum lugar do repositório; os 16 valores só existem como literal em "
                    "scripts/importar_fornecedores_nacionais.py:62-73 (coincidem ao centavo com o banco). "
                    "'tabela de preços Anara não contribuinte em português 21-08-26.xlsx' lista as peseiras (Relevo/Sofia/Arezzo/Sissi, "
                    "50% algodão/50% acrílico) sem preço. A referência de custo não responde 'de onde veio este número' com documento."),
        "produtos": ids(lambda l: l["bruto_gravado"] != "", decor),
    })
    achados.append({
        "codigo": "NAC-04", "gravidade": "P2",
        "detalhe": ("Decor Tricot: CNET = preço bruto informado, sem crédito de entrada (ICMS/PIS/COFINS). É o que o "
                    "SUPER_PROMPT v2 §22 manda ('preço informado pela Decor = custo de aquisição'), mas contradiz o princípio "
                    "'preço bruto de fornecedor nacional não é preço de venda: bruto → créditos → CUSTO NET' (CLAUDE.md) e o "
                    "tratamento dado à Daune (crédito 12% + 9,25%). Se a Anara se credita na compra da Decor como se credita na "
                    "Daune, o custo real é ~20% menor que o usado e o preço recomendado está ~20% acima; se não se credita, a Daune é "
                    "que precisa de justificativa fiscal. Premissa não está cadastrada nem documentada — decisão aberta, não corrigir."),
        "produtos": ids(lambda l: l["bruto_gravado"] != "", decor),
    })
    stale_pb = [l for l in daune if "Produto.preco_base=" in l["notas"]]
    acotar_pb = [l for l in daune if "sem custo/A_COTAR mas Produto.preco_base" in l["notas"]]
    achados.append({
        "codigo": "NAC-05", "gravidade": "P2",
        "detalhe": ("Produto.preco_base da Daune é cache obsoleto: foi formado pelo importador de 21/08 sobre o valor da tabela "
                    "Trousseau (que o B-17 provou ser PREÇO DE VENDA legado, não custo), a 14% e com PIS/COFINS 7,59% — ex.: "
                    "SKU 242 preco_base 700,82 contra recomendado atual 356,80 (+96%). Esse campo está na lista de permissão do "
                    "vendedor (app/confidencial.py:73 CAMPOS_PRODUTO_COMERCIAL), aparece em /produtos "
                    "(app/templates/produtos_list.html:63) e alimenta diferenca_pct_vs_base. Pior: para SKU A_COTAR sem custo "
                    "(id 254, ATIVO), app/routers/cotacoes.py:850 e :957-960 usam preco_base (339,81 — número derivado de um preço "
                    "de venda tratado como custo) como preço do item no rascunho. O A_COTAR bloqueia a emissão, mas o rascunho "
                    "exibe um preço fabricado. Na Decor o mesmo cache está defasado na direção oposta (ex.: SKU 274 preco_base "
                    "242,67 × recomendado 256,97, −5,6%: formado a 14% com comissão de faixa e PIS/COFINS 7,59%, antes da política "
                    "de 16/09 com comissão 10%) — a vendedora vê em /produtos um preço menor que o recomendado da cotação."),
        "produtos": [l["id"] for l in stale_pb] + [l["id"] for l in acotar_pb] + [l["id"] for l in decor if "Produto.preco_base=" in l["notas"]],
    })
    rastr = [l for l in daune if "aba 27.07.26 mas data_ref" in l["notas"]]
    achados.append({
        "codigo": "NAC-06", "gravidade": "P3",
        "detalhe": (f"Rastreabilidade: {len(rastr)} versões vigentes (travesseiros, protetores, pillow tops) têm "
                    "documento='Linha Hotelaria - Daune - 12.08.26.xlsx' e data_ref=2026-08-12, mas o valor vem da aba "
                    "'Preços Daune 27.07.26' (origem_registro diz isso). A data da evidência é 27/07/2026, não 12/08. "
                    "Causa: scripts/reconciliar_daune.py grava data_ref pela data do arquivo, não pela aba casada. "
                    "Afeta o cálculo de envelhecimento (warning Daune após 60 dias, SUPER_PROMPT §35): pela data real, "
                    "essas referências já passam de 50 dias."),
        "produtos": [l["id"] for l in rastr],
    })
    caches = [l for l in daune if ("cache Produto." in l["notas"] or "precisa_revisao=True com vigente CONFIRMADO" in l["notas"])]
    achados.append({
        "codigo": "NAC-07", "gravidade": "P3",
        "detalhe": ("Colunas-cache de Produto contradizem a versão vigente: margem_padrao_pct=0.14 (política vigente é 12%), "
                    "custo_ref_tipo='A_COTAR' em edredons CONFIRMADOS, custo_confianca=REVIEW_REQUIRED (vocabulário legado), e "
                    "precisa_revisao=True com motivo obsoleto ('voltaram sem preço') em SKUs que já têm preço na aba 12.08.26 e no "
                    "Projeto Anastacio (ids 319, 322, 323, 329, 332, 333). /produtos exibe 'precisa revisão' e a margem antiga a "
                    "quem vê economia (app/routers/produtos.py:27,60). Não altera preço, mas confunde a leitura operacional."),
        "produtos": [l["id"] for l in caches],
    })
    achados.append({
        "codigo": "NAC-08", "gravidade": "P3",
        "detalhe": ("Protetor de colchão: a cotação Daune mais recente (Projeto Anastacio · 05.08.26, 'Matelassado com alça') "
                    "atribui 157,49 a 1,60x2,00 e 171,24 a 1,80x2,00, enquanto a aba 27.07.26 (fonte vigente) atribui 157,49 a "
                    "140x200 e 171,24 a 160x200. A assinatura técnica difere ('Manta 120 grs impermeavel' × 'Matelassado com "
                    "alça'), então não é o mesmo produto por campos estruturados — mas a medida 160x200 tem dois preços possíveis "
                    "em fontes do mesmo fornecedor (171,24 vigente × 157,49). Mesmo padrão nos pillow tops (1,03x2,03 × 100x200). "
                    "Confirmar com a Daune qual tabela vale; até lá, REVALIDAR é mais honesto que CONFIRMADO para 255-257."),
        "produtos": [255, 256, 257],
    })
    achados.append({
        "codigo": "NAC-09", "gravidade": "P3",
        "detalhe": ("SUPER_PROMPT_ANARA_v2.txt §7, §21, §22 e §58 continuam dizendo 'Daune 14%' e 'Decor 14%', mas a política "
                    "vigente (MargemRegra#22/#23, POLITICA_COMERCIAL_2026-09-16) é 12% com piso 12%/10%. A fonte de verdade de "
                    "negócio está desatualizada em relação à decisão de 16/09/2026; auditor que ler só o SUPER PROMPT recalcula a "
                    "14% e acha divergência de preço que não existe."),
        "produtos": [],
    })
    inativos = [l for l in daune if not l["ativo"]]
    achados.append({
        "codigo": "NAC-10", "gravidade": "P3",
        "detalhe": ("8 SKUs Daune inativos (edredons 156x230/220x240/240x260/260x290, pluma e poliéster) estão A_COTAR embora a "
                    "aba 27.07.26 tenha preço bruto para essas medidas (790,80/995,87/1.087,07/1.365,35 e 406,75/460,60/504,68/590,10). "
                    "Sem gramatura declarada na fonte e sem gramatura no SKU, o casamento é AMBIGUO — A_COTAR é a classificação "
                    "correta. preco_base cache (ex.: 2.222,38) continua gravado e derivado do preço de venda legado."),
        "produtos": [l["id"] for l in inativos],
    })
    ruido = [l for l in daune if l["diferenca_cnet"] not in ("", "0") and abs(Decimal(l["diferenca_cnet"])) <= Decimal("0.000001")]
    achados.append({
        "codigo": "NAC-11", "gravidade": "P3",
        "detalhe": (f"Ruído binário em cnet_brl/custo_unitario de {len(ruido)} SKUs (ex.: 45.440340000000006 em vez de 45.44034): "
                    "a memória de cálculo gravada pela reconciliação de 03/09 foi serializada em float. Nenhum efeito no centavo "
                    "do preço (Δ ≤ 1e-14), e o motor atual é Decimal; registrado para que ninguém interprete como divergência."),
        "produtos": [l["id"] for l in ruido],
    })

    with open(os.path.join(AUDIT, "nacionais_achados.json"), "w", encoding="utf-8") as f:
        json.dump(achados, f, ensure_ascii=False, indent=2)

    # ---------------- resumo ----------------
    d_ativos = [l for l in daune if l["ativo"]]
    cols = ["id", "nome", "gramatura", "bruto_gravado", "bruto_fonte", "cnet_gravado", "cnet_recalculado",
            "diferenca_cnet", "status_custo", "preco_D_oficial", "preco_D_independente", "preco_E_oficial",
            "preco_E_independente", "situacao"]
    cab = ["id", "produto", "g", "bruto grav.", "bruto fonte", "CNET grav.", "CNET recalc.", "Δ", "status",
           "D oficial", "D indep.", "E oficial", "E indep.", "situação"]
    md = []
    md.append("# Auditoria de crise — seções 9 e 10: fornecedores nacionais (Daune e Decor Tricot)\n")
    md.append(f"Gerado em {date.today().isoformat()} por `scripts/crisis/auditoria_nacionais.py` sobre a cópia "
              f"`{AUDIT}/trabalho/nacionais.db`. Somente leitura; nada foi corrigido.\n")
    md.append("## Método\n")
    md.append("- **CNET independente (Daune)**: `CNET = bruto − bruto×12% − (bruto − bruto×12%)×9,25%`, em `decimal` "
              "(SUPER_PROMPT v2 §21; Daune confirma crédito de ICMS 12% na aba 'Informações'). Não chama `cnet_nacional`.\n"
              "- **CNET (Decor)**: `CNET = preço informado` (SUPER_PROMPT v2 §22) — ver NAC-04.\n"
              "- **Preço independente**: `preço = CNET / (1 − ICMS − PIS/COFINS_ef − encargo − comissão − margem)`, "
              "`PIS/COFINS_ef = 9,25% × (1 − (ICMS − FCP))`, ROUND_HALF_UP a 2 casas. Benchmark D: SP→SP contribuinte REVENDA "
              "30 dias FOB; E: SP→SP não contribuinte USO_CONSUMO. Nos dois o motor resolve ICMS 18% (RegraFiscalVenda#1/#2), "
              "FCP 0, encargo 1,6% (30 dias), PIS/COFINS efetivo 7,585%. Daune: comissão 5%, margem 12% → denominador 0,55815. "
              "Decor: comissão 10%, margem 12% → denominador 0,50815.\n"
              "- **Casamento com a fonte**: família + composição/construção (assinatura normalizada) + dimensão + gramatura "
              "(quando a família discrimina por gramatura). Nunca por nome. Ambíguo = AMBIGUO.\n"
              "- **Situação**: BLOQUEADO (sem custo/A_COTAR) > DIVERGE (qualquer centavo ou premissa) > AMBIGUO > SEM_FONTE > OK.\n")
    md.append("## Contagens\n")
    md.append(f"**Daune** — {len(daune)} produtos ({len(d_ativos)} ativos, {len(inativos)} inativos): " +
              ", ".join(f"{k} {v}" for k, v in sorted(cd.items())) + "\n")
    md.append(f"**Decor Tricot** — {len(decor)} produtos: " + ", ".join(f"{k} {v}" for k, v in sorted(ce.items())) + "\n")
    md.append("## Daune — reconciliação (uma linha por produto)\n")
    md.append(tabela_md(daune, cols, cab) + "\n")
    md.append("### Validação explícita das gramaturas de edredom\n")
    for g in (180, 250, 280):
        sub = [l for l in daune if l["gramatura"] == g]
        md.append(f"**{g} g** — {len(sub)} SKUs: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(l['situacao'] for l in sub).items())))
        for l in sub:
            md.append(f"- [{l['id']}] {l['nome']}: bruto {l['bruto_gravado']} · fontes: {l['fontes_casadas'] or '—'} · D {l['preco_D_oficial']} / indep. {l['preco_D_independente']} · **{l['situacao']}**"
                      + (f" — {l['notas']}" if l["notas"] else ""))
        md.append("")
    md.append("## Decor Tricot — reconciliação\n")
    md.append(tabela_md(decor, cols, cab) + "\n")
    md.append("## Notas por produto (Daune)\n")
    for l in daune:
        if l["notas"]:
            md.append(f"- [{l['id']}] {l['nome']} — **{l['situacao']}** — {l['notas']}")
    md.append("\n## Notas por produto (Decor)\n")
    for l in decor:
        if l["notas"]:
            md.append(f"- [{l['id']}] {l['nome']} — **{l['situacao']}** — {l['notas']}")
    md.append("\n## Achados\n")
    for a in achados:
        md.append(f"### {a['codigo']} — {a['gravidade']}\n\n{a['detalhe']}\n\nProdutos: {a['produtos'] or '—'}\n")
    md.append("## Conclusões\n")
    md.append("1. **A matemática está íntegra onde há fonte**: para todos os SKUs Daune com bruto gravado, CNET recalculado em "
              "Decimal bate com `cnet_brl`, com `Produto.custo_unitario` e com `custo_para_precificar` (Δ ≤ 1e-14, ruído binário), "
              "e o preço recomendado do motor nos benchmarks D e E bate ao centavo com a fórmula independente. A política "
              "12%/12%/5%/travado (Daune) e 12%/10%/10% (Decor) é a resolvida em todos os produtos.\n"
              "2. **O problema é de evidência, não de aritmética**: a linha 280 g (9 SKUs) não tem documento e coincide ao centavo "
              "com o que a Daune cobra por 180 g (NAC-01, P1); a Decor não tem documento no repositório, não tem versão de custo "
              "e ainda assim entra como CONFIRMADO (NAC-02/03, P1/P2); a premissa de crédito zero da Decor não está registrada "
              "(NAC-04, P2).\n"
              "3. **Caches enganam quem lê a tela**: `preco_base` da Daune é o preço de venda legado grossed-up (até +96% sobre o "
              "recomendado) e é o que o vendedor vê em /produtos; para o SKU 254 (A_COTAR, ativo) vira o preço do item no rascunho "
              "(NAC-05, P2). margem 14%, custo_ref_tipo A_COTAR e precisa_revisao obsoletos (NAC-07, P3).\n"
              "4. Nada foi corrigido. Cada achado aponta arquivo:linha da causa raiz.\n")
    with open(os.path.join(AUDIT, "NACIONAIS_RESUMO.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print("Daune:", dict(cd))
    print("Decor:", dict(ce))
    print("Achados:", [(a["codigo"], a["gravidade"], len(a["produtos"])) for a in achados])
    print("Artefatos em", AUDIT)


if __name__ == "__main__":
    main()

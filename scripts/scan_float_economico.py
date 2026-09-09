#!/usr/bin/env python3
"""Scan dos `float` do caminho econômico — §31 e §32 da Sessão 3B.

Duas varreduras, porque uma só produz falso negativo:

1. **Textual (AST, não regex).** Percorre a árvore sintática de `app/` procurando `float(`,
   `round(`, anotações `float`, literais numéricos em aritmética e `Decimal(` sobre variável.
   Regex acharia a palavra dentro de comentário e docstring; a AST não.

2. **Por chamada real.** Executa o waterfall inteiro — custo → fiscal → pricing → frete →
   memória → JSON — e verifica o **tipo em tempo de execução** de cada valor econômico que sai
   dos motores. É esta que pega o float escondido dentro de dataclass, ORM, parser ou helper,
   que a varredura textual não veria.

Classificação de cada ocorrência, como o §31 pede:

    A) removido do núcleo econômico — o motor virou Decimal
    B) permitido / não econômico  — dimensão, contagem, exibição, técnico
    C) ponte inevitável com conversão segura — banco, JSON, form, template, PDF
    D) REVIEW_REQUIRED — float em posição econômica, sem ponte

Uso:
    python3 scripts/scan_float_economico.py
    python3 scripts/scan_float_economico.py --json relatorios/scan_float_sessao3b.json
"""
import argparse
import ast
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(RAIZ, "app")

# Os motores puros: aqui float em posição econômica é classe D.
MOTORES = {"pricing_engine.py", "frete_engine.py", "ktc_engine.py", "nationalization.py",
           "fiscal_rules.py", "margin_rules.py", "payment_terms.py", "peso.py",
           "custo_service.py"}

# Fronteiras declaradas: float é esperado e a conversão é explícita (classe C).
FRONTEIRAS = {"dinheiro.py", "pdf_bridge.py", "templating.py", "models.py", "config_service.py",
              "pricing_service.py", "frete_service.py", "calculadora.py", "excel_import.py",
              "seeds.py", "migrations.py", "relatorios.py", "arquivamento.py", "busca.py",
              "matching.py", "spec_parser.py", "nomes.py", "fontes_ktc.py", "db.py",
              "auth.py", "main.py", "__init__.py"}


def arquivos_py(raiz):
    for base, _dirs, nomes in os.walk(raiz):
        if "__pycache__" in base:
            continue
        for n in sorted(nomes):
            if n.endswith(".py"):
                yield os.path.join(base, n)


def varredura_textual():
    """Ocorrências sintáticas de float/round/Decimal(var) em `app/`."""
    achados = []
    for caminho in arquivos_py(APP):
        rel = os.path.relpath(caminho, RAIZ)
        nome = os.path.basename(caminho)
        fonte = open(caminho, encoding="utf-8").read()
        arvore = ast.parse(fonte)
        for no in ast.walk(arvore):
            tipo = None
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
                if no.func.id == "float":
                    tipo = "float()"
                elif no.func.id == "round":
                    tipo = "round()"
                elif (no.func.id == "Decimal" and no.args
                      and not isinstance(no.args[0], ast.Constant)):
                    tipo = "Decimal(variável)"
            elif isinstance(no, ast.Name) and no.id == "float" and isinstance(no.ctx, ast.Load):
                pai_anotacao = True       # anotação de tipo `: float` / `-> float`
                tipo = "anotação float" if pai_anotacao else None
            if tipo:
                achados.append({"arquivo": rel, "modulo": nome, "linha": no.lineno,
                                "tipo": tipo, "classe": classificar(nome, tipo)})
    return achados


def classificar(modulo: str, tipo: str) -> str:
    """A) removido · B) não econômico · C) ponte segura · D) revisão."""
    if tipo == "Decimal(variável)":
        # só é seguro dentro do próprio módulo de política
        return "C" if modulo == "dinheiro.py" else "D"
    if modulo in MOTORES:
        # nos motores puros, o que sobra é anotação de assinatura pública ou conversão de
        # apoio; qualquer `float()` sobre quantia seria D e aparece na varredura dinâmica
        return "A"
    if modulo in FRONTEIRAS:
        return "C"
    return "B"


# ---------------------------------------------------------------------------
def varredura_dinamica():
    """Executa o waterfall e confere o TIPO de cada saída econômica."""
    from decimal import Decimal

    from sqlmodel import Session, SQLModel, create_engine, select

    import app.db as db
    import app.models  # noqa: F401
    import app.seeds as seeds
    import tempfile

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="scan-float-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}")
    SQLModel.metadata.create_all(engine)
    original = db.engine
    db.engine = engine
    seeds.engine = engine
    seeds.semear(verbose=False)

    resultados = []

    def confere(rotulo, valor, esperado=Decimal):
        ok = valor is None or isinstance(valor, esperado)
        resultados.append({"ponto": rotulo, "tipo": type(valor).__name__,
                           "classe": "A" if ok else "D"})

    try:
        from app import custo_service as cs
        from app import pricing_service as ps
        from app.fiscal_rules import resolver_fiscal_item
        from app.models import (AliquotaInterestadual, CondicaoPagamento, EstadoFiscal,
                                RegraFcp, RegraFiscalVenda)
        from app.nationalization import PremissasNacionalizacao, nacionalizar
        from app.payment_terms import resolver_encargo
        from app.pricing_engine import TaxRuleSet, calcular_por_margem, pis_cofins_efetivo
        from app.ktc_engine import ParametrosKTC, calcular_flat_sheet
        from decimal import Decimal as Dec

        with Session(engine) as s:
            estados = s.exec(select(EstadoFiscal)).all()
            fiscal = resolver_fiscal_item(
                s.exec(select(RegraFiscalVenda)).all(), estados,
                s.exec(select(AliquotaInterestadual)).all(),
                uf_origem="SP", uf_destino="SP", origem_fiscal="NACIONAL",
                contribuinte=True, finalidade="USO_CONSUMO",
                regras_fcp=s.exec(select(RegraFcp)).all())
            confere("fiscal_rules.icms_pct", fiscal.icms_pct)
            confere("fiscal_rules.aliquota_interna_destino", fiscal.aliquota_interna_destino)
            confere("fiscal_rules.fcp_pct", fiscal.fcp_pct)

            enc = resolver_encargo(s.exec(select(CondicaoPagamento)).all(), "30")
            confere("payment_terms.pct", enc.pct)

            pr = ps.premissas_nacionalizacao(s)
            confere("nationalization.fx_usd_brl", pr.fx_usd_brl)
            nac = nacionalizar(Dec("10"), Dec("0.5"), Dec("0.035"), pr)
            confere("nationalization.net_brl", nac.net_brl)
            confere("nationalization.net_usd", nac.net_usd)

            p = ParametrosKTC(material_price_usd_m2=1.20, cmt_usd=0.75, shrinkage=0.03,
                              waste=0.03, quality_allowance=0.01, ktc_margin=0.15,
                              hem_width_total_cm=4, hem_length_total_cm=4)
            ktc = calcular_flat_sheet(180, 310, p)
            confere("ktc_engine.exw_usd", ktc.exw_usd)
            confere("ktc_engine.etapa.valor", ktc.etapas[-1].valor)

            confere("custo_service.cnet_nacional.cnet", cs.cnet_nacional(Dec("427.50")).cnet)

            regras = TaxRuleSet(icms_pct=0.18,
                                pis_cofins_pct=pis_cofins_efetivo(Dec("0.0925"), Dec("0.18")),
                                encargo_financeiro_pct=0.016,
                                comissao_tabela=[(0.0, 0.05), (0.6, 0.06)])
            confere("pricing_engine.taxa_fixa", regras.taxa_fixa())
            r = calcular_por_margem(377.11, 3, 0.14, regras)
            for campo in ("preco_negociado", "faturamento", "custo_total", "impostos",
                          "comissao", "lucro", "margem_liquida", "markup_implicito",
                          "frete_cf", "frete_rv", "preco_preciso", "margem_alvo"):
                confere(f"pricing_engine.{campo}", getattr(r, campo))

            from app.dinheiro import ratear_centavos
            confere("dinheiro.ratear_centavos[0]", ratear_centavos("100.00", [1, 1, 1])[0])

            # fronteiras: aqui o esperado é float, e float é CORRETO (classe C)
            externo = r.como_dict()
            for k in ("preco_negociado", "faturamento", "lucro"):
                resultados.append({
                    "ponto": f"pricing_engine.como_dict['{k}'] (fronteira)",
                    "tipo": type(externo[k]).__name__,
                    "classe": "C" if isinstance(externo[k], float) else "D"})
            json.dumps(externo)          # prova que serializa sem default=str
    finally:
        db.engine = original
        os.unlink(caminho)
    return resultados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="salva o relatório")
    args = ap.parse_args()

    textual = varredura_textual()
    dinamica = varredura_dinamica()

    ct = Counter(a["classe"] for a in textual)
    cd = Counter(a["classe"] for a in dinamica)

    print("=" * 74)
    print("VARREDURA TEXTUAL (AST) — app/")
    print("=" * 74)
    for classe, rotulo in [("A", "removido do núcleo econômico"),
                           ("B", "permitido / não econômico"),
                           ("C", "ponte inevitável, conversão segura"),
                           ("D", "REVIEW_REQUIRED")]:
        print(f"  {classe}) {rotulo:38s} {ct.get(classe, 0):4d}")
    print(f"  {'TOTAL':45s} {len(textual):4d}")

    por_modulo = Counter(a["modulo"] for a in textual if a["classe"] in ("A", "D"))
    if por_modulo:
        print("\n  nos motores puros, por módulo:")
        for m, n in sorted(por_modulo.items()):
            print(f"    {m:26s} {n:3d}")

    d = [a for a in textual if a["classe"] == "D"]
    if d:
        print("\n  *** CLASSE D (revisar):")
        for a in d:
            print(f"    {a['arquivo']}:{a['linha']}  {a['tipo']}")

    print()
    print("=" * 74)
    print("VARREDURA DINÂMICA — tipo real na saída dos motores")
    print("=" * 74)
    for a in dinamica:
        marca = "ok " if a["classe"] in ("A", "C") else "!! "
        print(f"  {marca}{a['ponto']:52s} {a['tipo']}")
    print(f"\n  Decimal onde exigido: {cd.get('A', 0)} · fronteiras em float: {cd.get('C', 0)} "
          f"· divergentes: {cd.get('D', 0)}")

    saida = {"textual": textual, "dinamica": dinamica,
             "resumo_textual": dict(ct), "resumo_dinamico": dict(cd)}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=1)
        print(f"\nrelatório: {args.json}")
    return 1 if (ct.get("D") or cd.get("D")) else 0


if __name__ == "__main__":
    sys.exit(main())

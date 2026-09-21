"""Matriz fiscal de 21/09/2026 — base interna das 27 UFs e FCP por UF × família.

Até 20/09/2026 só o RJ tinha `EstadoFiscal.icms_interno_base` e linha de `RegraFcp`; venda a
não contribuinte fora de SP/RJ bloqueava por cadastro faltante (FIS-01). A decisão de
21/09/2026 cadastra, para o **escopo de produtos reconciliado** (cama, mesa e banho do
catálogo atual), a alíquota interna SEM FCP de cada UF e a incidência de FCP/FECP:

    base interna (sem FCP)  — tabela abaixo, por UF;
    FCP/FECP                — AL 1%, RJ 2%, SE 1%; demais UFs 0% para as famílias do escopo.

O que esta matriz **não** faz, de propósito:

* não usa `carga_final` nem `base_dupla` da tabela-benchmark ("Projeto Anara — DIFAL") como
  entrada da fórmula — o motor (`fiscal_rules`) é base única: `DIFAL = interna − interestadual`,
  FCP por fora, ambos do remetente quando o cliente não é contribuinte;
* não cria "FCP zero universal": a linha de FCP é **por família**; produto de família fora do
  escopo continua DESCONHECIDO → `REVIEW_REQUIRED`, como antes;
* não altera as colunas de benchmark (`base_simples`, `base_dupla`, `fem`, `carga_final`).

Idempotente: reaplicar não cria linha nem versão nova. Fonte: matriz fiscal Anara /
contabilidade, validada em 21/09/2026 para o escopo atual.
"""
from datetime import date
from typing import Dict, List

from sqlmodel import Session, select

from app.models import EstadoFiscal, RegraFcp

DATA_MATRIZ = date(2026, 9, 21)
FONTE_MATRIZ = "Matriz fiscal Anara / contabilidade — validada em 21/09/2026 (escopo cama e banho)"

#: Alíquota interna SEM FCP por UF (fração da receita final).
BASE_INTERNA: Dict[str, float] = {
    "AC": 0.19, "AL": 0.205, "AP": 0.18, "AM": 0.20, "BA": 0.205, "CE": 0.20, "DF": 0.20,
    "ES": 0.17, "GO": 0.19, "MA": 0.23, "MT": 0.17, "MS": 0.17, "MG": 0.18, "PA": 0.19,
    "PB": 0.20, "PR": 0.195, "PE": 0.205, "PI": 0.225, "RJ": 0.20, "RN": 0.20, "RS": 0.17,
    "RO": 0.195, "RR": 0.20, "SC": 0.17, "SP": 0.18, "SE": 0.19, "TO": 0.20,
}

#: FCP/FECP geral validado para o escopo comum de cama e banho. UF ausente = 0% (NAO_APLICA).
FCP_APLICA: Dict[str, float] = {"AL": 0.01, "RJ": 0.02, "SE": 0.01}

#: Famílias do catálogo Anara em 21/09/2026 para as quais a incidência de FCP foi reconciliada.
#: Família nova fora desta lista NÃO herda 0%: cai em DESCONHECIDO e bloqueia até cadastro.
FAMILIAS_ESCOPO: List[str] = [
    # KTC — cama
    "Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet", "Duvet Cover", "Pillow Case",
    "Duvet Insert", "Pillow", "Pillow Protector", "Mattress Protector", "Mattress Topper",
    "Bed Runner", "Blanket",
    # KTC — banho
    "Bath Towel", "Hand Towel", "Face Towel", "Pool Towel", "Beach Towel", "Bath Mat",
    "Wash Cloth", "Towel", "Bath Rug", "Bathrobe", "Slipper",
    # Decor Tricot
    "Cushion Cover",
]

REGRA_FCP_2026_09_21 = "FCP/FECP — matriz fiscal 21/09/2026"


def aplicar_matriz(session: Session) -> int:
    """Cadastra base interna e FCP por família. Devolve quantas linhas/colunas foram escritas."""
    escritos = 0
    estados = {e.uf: e for e in session.exec(select(EstadoFiscal)).all() if e.ativo}
    for uf, base in BASE_INTERNA.items():
        e = estados.get(uf)
        if e is None:
            continue
        fcp = FCP_APLICA.get(uf, 0.0)
        interna = round(base + fcp, 6)
        mudou = False
        if e.icms_interno_base is None or abs((e.icms_interno_base or 0) - base) > 1e-9:
            e.icms_interno_base = base
            mudou = True
        if e.interna_inclui_fcp != bool(fcp):
            e.interna_inclui_fcp = bool(fcp)
            mudou = True
        if abs((e.aliquota_interna or 0) - interna) > 1e-9:
            e.aliquota_interna = interna
            mudou = True
        if mudou:
            e.notas = ((e.notas + " · ") if e.notas else "") + \
                f"Base interna sem FCP {base:.3%} e FCP {fcp:.2%} cadastrados pela {FONTE_MATRIZ}."
            session.add(e)
            escritos += 1

    existentes = {(r.uf_destino, (r.familia or "").strip().lower())
                  for r in session.exec(select(RegraFcp)).all() if r.familia}
    for uf in BASE_INTERNA:
        fcp = FCP_APLICA.get(uf, 0.0)
        for familia in FAMILIAS_ESCOPO:
            if (uf, familia.lower()) in existentes:
                continue
            session.add(RegraFcp(
                uf_destino=uf, familia=familia, fcp_pct=fcp, prioridade=50,
                situacao="APLICA" if fcp > 0 else "NAO_APLICA",
                regra=f"{REGRA_FCP_2026_09_21} — {uf} · {familia}: "
                      + (f"{fcp:.2%}" if fcp > 0 else "não incide"),
                fonte=FONTE_MATRIZ, valid_from=DATA_MATRIZ,
                notas="Linha por família do escopo reconciliado. Família fora do escopo continua "
                      "DESCONHECIDA (bloqueia) até cadastro próprio."))
            escritos += 1
    session.flush()
    return escritos


def resumo(session: Session) -> List[dict]:
    """Uma linha por UF: base, FCP e o que o motor resolve para não contribuinte."""
    linhas = []
    for e in sorted(session.exec(select(EstadoFiscal)).all(), key=lambda x: x.uf):
        linhas.append({"uf": e.uf, "estado": e.estado, "aliquota_interna": e.aliquota_interna,
                       "icms_interno_base": e.icms_interno_base,
                       "interna_inclui_fcp": e.interna_inclui_fcp,
                       "fcp_escopo": FCP_APLICA.get(e.uf, 0.0),
                       "benchmark_carga_final": e.carga_final, "benchmark_fem": e.fem,
                       "benchmark_base_simples": e.base_simples,
                       "benchmark_base_dupla": e.base_dupla})
    return linhas

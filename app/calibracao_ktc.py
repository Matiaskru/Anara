"""Calibração da fórmula KTC contra cotações reais — a regra, num lugar só (13/10/2026).

Puro: sem banco, sem FastAPI. Quem grava o fator é o script de dados; quem aplica é
`pricing_service.custo_net` (via `calibracao_ktc`). A regra, decidida pelo Matias em 13/10/2026:

* ratio = EXW real ÷ EXW da fórmula, por âncora (cotação direta com match técnico exato);
* nenhuma âncora mais de 2% acima da fórmula → fórmula aderente/conservadora: fator 1 (validada,
  sem ajuste — a sobra positiva é margem de segurança e NUNCA é tirada);
* alguma âncora mais de 2% acima → fator = mediana dos ratios × 1,02 (segurança só para cima);
* o fator nunca é menor que 1: calibrar nunca reduz o EXW da fórmula;
* QA: depois do fator, nenhuma cotação real conhecida pode ficar mais de 2% acima do EXW
  operacional (fórmula × fator). Falhou → a construção precisa ser separada (ex.: por tipo de
  peça) ou não é calibrável.
"""
import math
from statistics import mean, median, pstdev
from typing import Iterable, Sequence, Tuple

TOLERANCIA = 0.02   # fórmula até 2% abaixo do real é aderente
SEGURANCA = 0.02    # buffer aplicado sobre o fator calibrado, só para cima


def _teto(x: float, casas: int = 4) -> float:
    """Arredonda PARA CIMA (o fator nunca pode sair menor que a conta)."""
    f = 10 ** casas
    return math.ceil(round(x * f, 9)) / f


def estatisticas(ratios: Sequence[float]) -> dict:
    r = sorted(float(x) for x in ratios)
    return {"n": len(r), "ratio_min": r[0], "ratio_media": mean(r), "ratio_mediana": median(r),
            "ratio_max": r[-1], "dispersao": pstdev(r) if len(r) > 1 else 0.0}


def calcular_fator(ratios: Sequence[float]) -> dict:
    """Fator de uma construção a partir dos ratios real ÷ fórmula do nível de preço atual."""
    if not ratios:
        raise ValueError("Sem âncora não há calibração — a fórmula fica pura (KTC_CALCULATED).")
    est = estatisticas(ratios)
    if est["ratio_max"] <= 1 + TOLERANCIA:
        return {**est, "fator_base": est["ratio_mediana"], "seguranca_pct": 0.0, "fator_final": 1.0,
                "calibrou": False}
    base = est["ratio_mediana"]
    return {**est, "fator_base": base, "seguranca_pct": SEGURANCA,
            "fator_final": max(1.0, _teto(base * (1 + SEGURANCA))), "calibrou": True}


def qa(ancoras: Iterable[Tuple[float, float]], fator_final: float) -> dict:
    """`ancoras` = [(EXW real, EXW fórmula)]. Erro = real ÷ (fórmula × fator) − 1 (positivo =
    o sistema subestima). Passa se nenhum erro passa de 2%."""
    fator = max(1.0, float(fator_final))
    erros = [float(real) / (float(formula) * fator) - 1 for real, formula in ancoras]
    pior = max(erros) if erros else None
    return {"pior_erro": pior, "ok": pior is not None and pior <= TOLERANCIA + 1e-12,
            "n": len(erros)}

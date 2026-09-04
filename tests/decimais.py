"""Comparação numérica dos testes depois da Sessão 3B.

Por que este módulo existe: os motores devolvem `Decimal`, e `pytest.approx(0.18)` comparado
com um `Decimal` levanta `TypeError` — o pytest tenta `float - Decimal`. Pior: comparar
`Decimal("0.18") == 0.18` diretamente devolve **False**, porque o `float` 0,18 é
0,179999999999999993338661852249060757458209991455078125 e a comparação com Decimal é exata.

Então cada asserção de valor precisa de duas coisas: os dois lados convertidos para `Decimal`
pela ponte segura (`D`, via texto) e uma **tolerância explícita**, também em `Decimal`.

`aprox()` faz as duas, e funciona com qualquer combinação — `Decimal` do motor contra
`float` literal do teste, `float` de coluna do banco contra esperado `Decimal`, ou os dois
`Decimal`. As tolerâncias default são as do próprio pytest (`rel=1e-6`, `abs=1e-12`), só que
em `Decimal`; quem precisa de outra passa a sua, à vista.
"""
from decimal import Decimal

from app.dinheiro import D

REL_PADRAO = Decimal("1e-6")
ABS_PADRAO = Decimal("1e-12")

# Meio centavo: o desvio máximo que o arredondamento comercial pode introduzir num preço
# unitário. Uma asserção com esta tolerância está dizendo "aceito a diferença do centavo,
# e nada além dela".
MEIO_CENTAVO = Decimal("0.005")

# Um centavo: o desvio máximo aceitável entre dois caminhos que chegam à mesma quantia
# quando cada um passa por seu próprio arredondamento.
CENTAVO = Decimal("0.01")

# Tolerância da MARGEM depois do arredondamento comercial.
#
# Pedir 16% e cobrar R$ 106,84 (em vez de R$ 106,8384…) entrega 16,0015%. O desvio é o meio
# centavo do preço dividido pelo preço: em itens da ordem de R$ 100 fica na casa de 5×10⁻⁵.
# 5×10⁻⁴ deixa uma ordem de grandeza de folga e ainda reprova qualquer mudança de REGRA —
# trocar faixa de comissão, alíquota ou encargo move a margem em pontos percentuais, mil
# vezes mais do que isto.
MARGEM_DO_CENTAVO = Decimal("0.0005")


class _Aprox:
    """Igualdade com tolerância explícita, normalizando os dois lados para `Decimal`."""

    def __init__(self, esperado, tol_abs: Decimal, tol_rel: Decimal):
        self.esperado = D(esperado)
        self.tol_abs = tol_abs
        self.tol_rel = tol_rel

    def tolerancia(self) -> Decimal:
        return max(self.tol_abs, self.tol_rel * abs(self.esperado))

    def __eq__(self, obtido) -> bool:
        if obtido is None or self.esperado is None:
            return obtido is self.esperado
        return abs(D(obtido) - self.esperado) <= self.tolerancia()

    def __repr__(self) -> str:
        return f"{self.esperado} ± {self.tolerancia():.2E}"


def aprox(esperado, abs=None, rel=None) -> _Aprox:
    """`approx` que funciona com `Decimal` e `float` misturados nos dois lados."""
    return _Aprox(esperado,
                  D(abs) if abs is not None else ABS_PADRAO,
                  D(rel) if rel is not None else REL_PADRAO)

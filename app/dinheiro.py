"""Política monetária única do sistema — precisão, conversão, arredondamento e resíduo.

**Este é o único lugar onde a regra de arredondamento existe.** `pricing_engine`,
`frete_engine`, `ktc_engine`, `nationalization`, os serviços, os templates e o PDF importam
daqui. Nenhum deles tem régua própria: duas réguas produzem dois preços para o mesmo item, e
foi exatamente isso que a Sessão 3B veio encerrar.

## Por que Decimal

`float` é binário: `0.1 + 0.2` não é `0.3`, e `1.005` não arredonda para `1.01`. Num sistema
em que a faixa de comissão muda em `E >= 0.60`, um markup que deveria ser exatamente 60% pode
sair como `0.5999999999999999` e derrubar a comissão de 6% para 5%. Isso não é hipótese: é o
comportamento de `float` em qualquer soma de percentuais.

## As três regras

1. **Nunca `Decimal(float)`.** `Decimal(0.1)` é
   `0.1000000000000000055511151231257827021181583404541015625` — carrega o erro binário para
   dentro do Decimal e o torna permanente. Use `D()`, que passa pela representação **textual**
   do float (`repr`), a menor string decimal que reproduz aquele float exatamente.

2. **Precisão interna alta, arredondamento só no fim.** Nada é quantizado no meio da cadeia.
   EXW, FX, consumo de tecido, taxas e divisões trabalham com `PRECISAO_INTERNA` dígitos.
   Quantizar etapa a etapa acumularia erro em favor de ninguém.

3. **Dinheiro comercial: 2 casas, `ROUND_HALF_UP`.** É a regra do comércio brasileiro e a que
   o usuário confere na calculadora: `1.005 → 1.01`. O `round()` do Python faz banker's
   rounding sobre binário e devolve `1.0` — não serve.

## Onde fica a fronteira

O núcleo econômico é todo `Decimal`. O mundo de fora — SQLite, JSON, formulário HTML — é
`float`/`str`. A conversão acontece **na fronteira, explicitamente**:

    entrada:  D(valor_do_banco)      → normaliza antes de entrar no cálculo
    saída:    para_float(resultado)  → só na hora de persistir ou serializar

## Persistência: por que as colunas continuam REAL

Medido, não presumido (Sessão 3B): SQLite **não tem tipo decimal nativo**. Uma coluna
`Numeric(18,2)` do SQLAlchemy vira afinidade REAL do mesmo jeito que `Float`, e o
`Numeric` ainda **trunca na leitura** para a escala declarada — `34.71540940423179` volta
`34.72`, e uma alíquota de `0.0759` numa coluna de escala 2 volta `0.08`.

Como o histórico guarda preços com a precisão cheia do float (há itens de 2026 com
`preco_negociado = 34.71540940423179`), migrar essas colunas para `Numeric` **reescreveria
economicamente cotações já emitidas** — o que o projeto proíbe. Por isso o armazenamento
continua REAL e a ponte é `D()`, que é exata nos dois sentidos: todo float volta como o
mesmo decimal que o representa, e todo valor monetário já quantizado (2 casas) cabe em float
sem perda.
"""
from decimal import (
    Context, Decimal, DefaultContext, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN,
    ROUND_HALF_UP, setcontext,
)
from typing import Iterable, List, Optional, Sequence, Union

# --- política de precisão interna -----------------------------------------------------
# 34 dígitos significativos (o mesmo do IEEE 754 decimal128). O maior valor comercial do
# sistema tem 8 dígitos inteiros; sobram 26 casas para as divisões da cadeia — consumo de
# tecido, gross-up fiscal, rateio. Nenhum resultado comercial depende de truncamento
# intermediário nessa precisão.
#
# O arredondamento DO CONTEXTO é ROUND_HALF_EVEN — o default do padrão decimal, sem viés,
# e irrelevante a 34 dígitos. Ele não tem relação com o arredondamento COMERCIAL: esse é
# ROUND_HALF_UP e acontece uma única vez, em `dinheiro()`.
PRECISAO_INTERNA = 34

CONTEXTO_INTERNO = Context(prec=PRECISAO_INTERNA, rounding=ROUND_HALF_EVEN)
DefaultContext.prec = PRECISAO_INTERNA
DefaultContext.rounding = ROUND_HALF_EVEN
setcontext(CONTEXTO_INTERNO.copy())

# --- constantes -----------------------------------------------------------------------
CENTAVO = Decimal("0.01")
CASAS_MOEDA = 2
ARREDONDAMENTO_COMERCIAL = ROUND_HALF_UP

ZERO = Decimal("0")
UM = Decimal("1")
CEM = Decimal("100")

Numero = Union[Decimal, float, int, str, None]


# ---------------------------------------------------------------------------
# Conversão — a única porta de entrada
# ---------------------------------------------------------------------------
def D(valor: Numero, padrao: Optional[Decimal] = None) -> Optional[Decimal]:
    """Converte qualquer coisa para `Decimal` **sem herdar erro binário**.

    `None` devolve `padrao` (por default, `None`) — ausência de valor não vira zero em
    silêncio; quem chama decide o que fazer com a falta.

    O caso que importa é o `float`: a conversão passa por `repr()`, que em Python 3 devolve
    a menor string decimal que reproduz o float exatamente. É por isso que
    `D(0.1) == Decimal("0.1")` e não o dízimo binário que `Decimal(0.1)` produziria.
    """
    if valor is None:
        return padrao
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, bool):
        # bool é subclasse de int; deixar passar transformaria True em 1 sem intenção
        raise TypeError("valor booleano não é quantia")
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            raise ValueError(f"valor não finito não é quantia: {valor!r}")
        return Decimal(repr(valor))          # ponte segura: float → texto → Decimal
    if isinstance(valor, str):
        texto = valor.strip().replace(" ", "")
        if not texto:
            return padrao
        try:
            return Decimal(texto)
        except InvalidOperation as erro:
            raise ValueError(f"texto não numérico: {valor!r}") from erro
    raise TypeError(f"tipo não conversível para quantia: {type(valor).__name__}")


def D0(valor: Numero) -> Decimal:
    """`D()` com zero como default. Use só onde ausência REALMENTE significa zero."""
    return D(valor, ZERO)


def para_float(valor: Numero) -> Optional[float]:
    """Fronteira de saída: banco, JSON, template. `None` continua `None`."""
    if valor is None:
        return None
    return float(D(valor))


# ---------------------------------------------------------------------------
# Arredondamento comercial
# ---------------------------------------------------------------------------
def dinheiro(valor: Numero) -> Optional[Decimal]:
    """Quantia comercial: 2 casas, `ROUND_HALF_UP`. **O único arredondamento de moeda.**

    `dinheiro("1.005")` → `Decimal("1.01")`, que é o que a régua do comércio manda e o que
    `round(1.005, 2)` **não** faz.
    """
    d = D(valor)
    if d is None:
        return None
    return d.quantize(CENTAVO, rounding=ARREDONDAMENTO_COMERCIAL)


def quantizar(valor: Numero, casas: int, rounding: str = ARREDONDAMENTO_COMERCIAL) -> Optional[Decimal]:
    """Quantização explícita para casos não monetários (peso, percentual exibido)."""
    d = D(valor)
    if d is None:
        return None
    return d.quantize(Decimal(1).scaleb(-casas), rounding=rounding)


def soma(valores: Iterable[Numero]) -> Decimal:
    """Soma em Decimal. Um `None` no meio não vira zero silencioso: é ignorado por decisão."""
    total = ZERO
    for v in valores:
        d = D(v)
        if d is not None:
            total += d
    return total


def divide(numerador: Numero, denominador: Numero) -> Optional[Decimal]:
    """Divisão protegida: denominador zero/ausente devolve `None`, nunca 0 nem exceção."""
    n, d = D(numerador), D(denominador)
    if n is None or d is None or d == 0:
        return None
    return n / d


# ---------------------------------------------------------------------------
# Rateio com resíduo de centavos
# ---------------------------------------------------------------------------
def ratear_centavos(total: Numero, pesos: Sequence[Numero]) -> List[Decimal]:
    """Rateia `total` em quantias de 2 casas que somam **exatamente** `total`.

    O problema que isto resolve: R$ 100,00 entre três itens iguais dá 33,3333… cada um.
    Arredondar cada parcela produz 33,33 × 3 = R$ 99,99 e some um centavo — que numa cotação
    reconciliada por soma vira diferença inexplicada.

    Método: **maior resto** (largest remainder). Cada parcela recebe o piso em centavos; os
    centavos que sobram vão, um a um, para as parcelas de maior resto fracionário. Empate
    desempata pela **posição canônica** na lista — o chamador ordena por ID antes, e o mesmo
    input distribui sempre o mesmo centavo para o mesmo item.

        ratear_centavos(100, [1, 1, 1]) → [33.34, 33.33, 33.33]

    Peso total zero ou negativo divide igualmente e ainda fecha na soma: sem base de rateio
    não se inventa proporção, mas também não se perde dinheiro.
    """
    n = len(pesos)
    if n == 0:
        return []

    total_d = dinheiro(total) or ZERO
    total_centavos = int((total_d / CENTAVO).to_integral_value(rounding=ROUND_HALF_UP))

    ps = [D0(p) for p in pesos]
    soma_pesos = sum(ps)
    if soma_pesos <= 0:
        ps = [UM] * n
        soma_pesos = Decimal(n)

    ideais = [Decimal(total_centavos) * p / soma_pesos for p in ps]
    pisos = [int(i.to_integral_value(rounding=ROUND_FLOOR)) for i in ideais]
    restos = [ideais[k] - pisos[k] for k in range(n)]

    sobra = total_centavos - sum(pisos)
    # `sobra` é sempre >= 0 porque o piso nunca supera o ideal — inclusive com total negativo,
    # em que ROUND_FLOOR desce e a sobra volta positiva.
    ordem = sorted(range(n), key=lambda k: (-restos[k], k))
    for k in ordem[:sobra]:
        pisos[k] += 1

    return [(Decimal(c) * CENTAVO).quantize(CENTAVO) for c in pisos]


def reconcilia(parcelas: Iterable[Numero], total: Numero) -> bool:
    """Prova de reconciliação: a soma das parcelas bate com o total, ao centavo."""
    return soma(parcelas) == (dinheiro(total) or ZERO)

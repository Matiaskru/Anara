"""Política comercial canônica de 16/09/2026 — margem, piso, comissão e alçada da vendedora.

Motor **puro**: nada aqui importa FastAPI, Jinja nem banco. Recebe números e devolve números;
quem lê o banco é `comercial_service`, quem forma preço é `pricing_engine`.

## Os quatro conceitos, e por que não se misturam

    PREÇO RECOMENDADO   o que o motor forma para o cenário da cotação, na margem-alvo do item
                        e com a COMISSÃO DE FORMAÇÃO da política (10% não-Daune, 5% Daune)
    PREÇO NEGOCIADO     o unitário que a vendedora propõe ao cliente — editável dentro da política
    MARGEM REALIZADA    lucro ÷ receita no preço negociado, com TODOS os componentes canônicos
                        (custo, fiscal, financeiro, frete, comissão aplicada)
    COMISSÃO ESTIMADA   remuneração comercial estimada da negociação no cenário atual

A margem continua sendo **margem sobre receita**, como no motor. Não é markup.

## A decisão de 16/09/2026, escopo a escopo

| Escopo | Margem-alvo | Piso de autonomia | Comissão de formação | Preço |
|---|---|---|---|---|
| Daune | 12% | 12% (sem colchão) | 5%, **fixa** | **travado** |
| Decor Tricot | 12% | 10% | 10% → 5% (variável) | editável |
| Demais (KTC, geral) | anterior + 2 p.p. | anterior − 1 p.p. | 10% → 5% (variável) | editável |

"Anterior" é a margem que a regra do MESMO escopo tinha imediatamente antes de 16/09 — lida
da regra encerrada, nunca digitada. Nenhuma regra recebe +2 p.p. duas vezes: a regra nova
carrega `margem_anterior_pct`, e um escopo que já tem regra com `politica` preenchida não é
derivado de novo.

## Comissão — contínua, ponderada por valor, uma só por cotação

Para o **universo variável** (itens não-Daune com custo e preço recomendado):

    R              = Σ preço_recomendado × quantidade
    N              = Σ preço_negociado  × quantidade
    desconto       = max(0, 1 − N ÷ R)                    preço acima da tabela conta como 0
    proporcional   = max(5%, 10% × (1 − desconto))        nunca acima de 10%
    variável       = max(5%, min(proporcional, min_i comissão_máxima_para_o_piso_i))

A comissão máxima que preserva o piso de um item sai da **decomposição canônica** do preço
(`pricing_engine.comissao_maxima_para_margem`): lucro sem comissão ÷ receita − piso. Não há
segunda fórmula econômica aqui.

Leitura da regra: o desconto primeiro reduz a comissão na proporção; depois consome o colchão
de margem permitido; quando algum item encosta no piso, o desconto adicional é absorvido pela
comissão; a comissão nunca cai sozinha abaixo de 5%; se com 5% ainda houver item abaixo do
piso, a negociação sai da autonomia automática — **exceção comercial**, não blocker.

Daune fica fora do universo variável: preço travado, comissão fixa de 5%.

## O que a vendedora vê

Uma comissão só por cotação: o total estimado em R$ e a **taxa efetiva** = comissão total ÷
receita dos itens comissionáveis (Daune + universo variável; frete fora; item sem custo
fora). Nunca uma média simples de percentuais.

**Isto é comissão ESTIMADA de pricing**, calculada sobre a base canônica do gross-up (receita
comercial = preço × quantidade). A comissão realizada/pagável depende de faturamento e
recebimento e é assunto do financeiro — ver OQ-01 no `AUDIT_ANARA_MASTER.md`.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from app.dinheiro import D, D0, ZERO, dinheiro, divide

# ---------------------------------------------------------------------------
# A decisão — constantes da política, com data e fonte
# ---------------------------------------------------------------------------
DATA_VIGENCIA = date(2026, 9, 16)
FONTE = "Decisão comercial ANARA — 16/09/2026"
ROTULO = "POLITICA_COMERCIAL_2026-09-16"

#: Premissas versionadas que a política lê (chave → valor da decisão). São semeadas e
#: pinadas no item; o código lê o banco, não estas constantes, para formar preço.
COMISSAO_BASE_PCT = Decimal("0.10")
COMISSAO_MINIMA_PCT = Decimal("0.05")
CHAVE_COMISSAO_BASE = "comissao_base_pct"
CHAVE_COMISSAO_MINIMA = "comissao_min_pct"

DAUNE = dict(margem=Decimal("0.12"), piso=Decimal("0.12"), comissao=Decimal("0.05"),
             travado=True)
DECOR = dict(margem=Decimal("0.12"), piso=Decimal("0.10"), comissao=COMISSAO_BASE_PCT,
             travado=False)
DELTA_MARGEM_DEMAIS = Decimal("0.02")
DELTA_PISO_DEMAIS = Decimal("-0.01")

CODIGO_DAUNE = "DAUNE"
CODIGO_DECOR = "DECOR_TRICOT"

#: Status de autonomia que a vendedora vê.
DENTRO_DA_AUTONOMIA = "DENTRO_DA_AUTONOMIA"
REQUER_APROVACAO = "REQUER_APROVACAO"

#: Por que um item não é editável.
MOTIVO_TRAVADO = "Preço Daune travado pela política comercial de 16/09/2026."
MOTIVO_SEM_CUSTO = "Produto sem custo confirmado: não entra na negociação nem na comissão."


class PoliticaInconsistente(RuntimeError):
    """Regra com política mas premissa de comissão ausente — não se inventa 10%/5%."""


# ---------------------------------------------------------------------------
# Derivação das regras novas a partir das antigas
# ---------------------------------------------------------------------------
def regra_da_politica(margem_anterior, codigo_fornecedor: Optional[str]) -> dict:
    """Os campos de política do escopo, dada a margem anterior e o fornecedor.

    Devolve `Decimal` — quem grava converte na fronteira. `codigo_fornecedor=None` é a regra
    geral (sem fornecedor): segue "demais".
    """
    anterior = D(margem_anterior)
    if codigo_fornecedor == CODIGO_DAUNE:
        return dict(margem_pct=DAUNE["margem"], piso_pct=DAUNE["piso"],
                    comissao_formacao_pct=DAUNE["comissao"], preco_travado=True,
                    margem_anterior_pct=anterior)
    if codigo_fornecedor == CODIGO_DECOR:
        return dict(margem_pct=DECOR["margem"], piso_pct=DECOR["piso"],
                    comissao_formacao_pct=DECOR["comissao"], preco_travado=False,
                    margem_anterior_pct=anterior)
    if anterior is None:
        raise ValueError("regra sem margem anterior não pode receber +2 p.p.")
    return dict(margem_pct=anterior + DELTA_MARGEM_DEMAIS,
                piso_pct=anterior + DELTA_PISO_DEMAIS,
                comissao_formacao_pct=COMISSAO_BASE_PCT, preco_travado=False,
                margem_anterior_pct=anterior)


def nome_da_regra_nova(nome_antigo: str) -> str:
    return f"{nome_antigo} · política 16/09/2026"


# ---------------------------------------------------------------------------
# Comissão — as três contas
# ---------------------------------------------------------------------------
def desconto_ponderado(recomendado_total, negociado_total) -> Decimal:
    """`max(0, 1 − N/R)`. Universo vazio (R = 0) → 0: não há desconto sobre nada."""
    r = D0(recomendado_total)
    n = D0(negociado_total)
    if r <= ZERO:
        return ZERO
    razao = D("1") - n / r
    return razao if razao > ZERO else ZERO


def comissao_proporcional(desconto, base, minimo) -> Decimal:
    """`max(mínimo, base × (1 − desconto))`, nunca acima da base."""
    base_d = D0(base)
    minimo_d = D0(minimo)
    prop = base_d * (D("1") - D0(desconto))
    if prop > base_d:
        prop = base_d
    return prop if prop > minimo_d else minimo_d


def comissao_variavel(proporcional, maximas_para_o_piso: Sequence, minimo) -> Decimal:
    """`max(mínimo, min(proporcional, min_i máxima_i))`.

    `maximas_para_o_piso` pode estar vazia (nenhum item variável com custo): vale a
    proporcional. Uma máxima negativa (o item já está abaixo do piso com comissão zero) leva
    ao mínimo — e a violação do piso aparece na avaliação do item, não aqui.
    """
    candidato = D0(proporcional)
    for m in maximas_para_o_piso:
        md = D(m)
        if md is not None and md < candidato:
            candidato = md
    minimo_d = D0(minimo)
    return candidato if candidato > minimo_d else minimo_d


def taxa_efetiva(comissao_total, receita_comissionavel) -> Optional[Decimal]:
    """Comissão total ÷ receita dos itens comissionáveis. Sem receita → `None`, não 0."""
    return divide(comissao_total, receita_comissionavel)


# ---------------------------------------------------------------------------
# A avaliação de uma negociação — só números, já resolvidos por quem tem banco
# ---------------------------------------------------------------------------
@dataclass
class LinhaNegociada:
    """Um item já com o que o serviço resolveu: preços, custo, política e comissão máxima."""
    item_id: Optional[int]
    produto_id: Optional[int]
    nome: str
    quantidade: Decimal
    preco_recomendado: Optional[Decimal]
    preco_negociado: Decimal
    custo_unitario: Decimal
    politica: Optional[str]                  # None = item anterior à política
    preco_travado: bool = False
    piso_pct: Optional[Decimal] = None
    margem_alvo_pct: Optional[Decimal] = None
    comissao_formacao_pct: Optional[Decimal] = None
    comissao_maxima_piso_pct: Optional[Decimal] = None   # do motor; None se não elegível
    # --- derivados ---
    faturamento: Decimal = ZERO
    faturamento_recomendado: Decimal = ZERO
    elegivel_variavel: bool = False
    editavel: bool = True
    motivo_nao_editavel: Optional[str] = None
    comissao_aplicada_pct: Optional[Decimal] = None


@dataclass
class ComissaoDaCotacao:
    base_pct: Decimal
    minima_pct: Decimal
    recomendado_variavel: Decimal
    negociado_variavel: Decimal
    desconto_ratio: Decimal
    proporcional_pct: Decimal
    maxima_para_o_piso_pct: Optional[Decimal]
    variavel_pct: Decimal
    limitada_pelo_piso: bool
    limitada_por: Optional[str] = None       # nome do item que segurou a comissão
    linhas: List[LinhaNegociada] = field(default_factory=list)


def classificar(linha: LinhaNegociada) -> LinhaNegociada:
    """Elegibilidade e editabilidade de uma linha, sem tocar em dinheiro."""
    linha.faturamento = dinheiro(linha.preco_negociado * linha.quantidade) or ZERO
    linha.faturamento_recomendado = (
        dinheiro(linha.preco_recomendado * linha.quantidade)
        if linha.preco_recomendado is not None else ZERO)
    tem_custo = linha.custo_unitario > ZERO
    tem_recomendado = bool(linha.preco_recomendado and linha.preco_recomendado > ZERO)
    if linha.preco_travado:
        linha.editavel = False
        linha.motivo_nao_editavel = MOTIVO_TRAVADO
    elif not tem_custo:
        linha.editavel = True
        linha.motivo_nao_editavel = MOTIVO_SEM_CUSTO
    linha.elegivel_variavel = (linha.politica is not None and not linha.preco_travado
                               and tem_custo and tem_recomendado)
    return linha


def avaliar_comissao(linhas: Sequence[LinhaNegociada], base_pct, minima_pct
                     ) -> ComissaoDaCotacao:
    """A comissão única do bloco variável, dadas as linhas já classificadas."""
    base = D0(base_pct)
    minimo = D0(minima_pct)
    variaveis = [l for l in linhas if l.elegivel_variavel]
    r = sum((l.faturamento_recomendado for l in variaveis), ZERO)
    n = sum((l.faturamento for l in variaveis), ZERO)
    desconto = desconto_ponderado(r, n)
    proporcional = comissao_proporcional(desconto, base, minimo)

    maximas = [(l.comissao_maxima_piso_pct, l.nome) for l in variaveis
               if l.comissao_maxima_piso_pct is not None]
    menor = min(maximas, key=lambda x: x[0]) if maximas else (None, None)
    variavel = comissao_variavel(proporcional, [m for m, _ in maximas], minimo)
    limitada = menor[0] is not None and menor[0] < proporcional

    for l in linhas:
        if l.preco_travado and l.comissao_formacao_pct is not None:
            l.comissao_aplicada_pct = l.comissao_formacao_pct      # Daune: fixa
        elif l.elegivel_variavel:
            l.comissao_aplicada_pct = variavel
        else:
            l.comissao_aplicada_pct = None
    return ComissaoDaCotacao(
        base_pct=base, minima_pct=minimo, recomendado_variavel=r, negociado_variavel=n,
        desconto_ratio=desconto, proporcional_pct=proporcional,
        maxima_para_o_piso_pct=menor[0], variavel_pct=variavel,
        limitada_pelo_piso=limitada, limitada_por=menor[1] if limitada else None,
        linhas=list(linhas))

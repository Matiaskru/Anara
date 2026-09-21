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


# ===========================================================================
# POLÍTICA COMERCIAL DE 21/09/2026 — B2B, tabela 2×, escada de comissão por item
# ===========================================================================
# A política de 16/09 continua acima, intacta, porque há itens que a pinaram. A de 21/09 é
# uma política NOVA, com rótulo próprio: o sistema decide qual mecânica aplicar pelo rótulo
# congelado no item (`versao_da_politica`), nunca por "tem política ou não".
#
#   PREÇO B2B (recomendado)  menor preço em centavos com margem ≥ alvo, formado com 5% de
#                            comissão sobre a receita líquida do ICMS suportado pela Anara.
#                            É também o PISO DE AUTONOMIA da vendedora.
#   PREÇO DE TABELA          2 × B2B (100% de markup sobre o B2B — não "100% de margem").
#   PREÇO DA PROPOSTA        o negociado; abaixo do B2B exige aprovação.
#   COMISSÃO                 por ITEM, pela faixa do desconto do item sobre a SUA tabela:
#                            0% → 10 · (0,10] → 9 · (10,20] → 8 · (20,30] → 7 · (30,40] → 6 ·
#                            >40 → 5. Nunca abaixo de 5% automaticamente. Nenhum item limita
#                            a taxa de outro.
#   BASE COMISSIONÁVEL       receita − ICMS próprio − DIFAL do remetente. FCP, PIS/COFINS,
#                            encargo, frete, CNET e margem NÃO saem da base.
#
# Fontes: contrato ID Hospitality/Inês, cláusulas 6.1, 6.1.1, 6.1.2 e 6.3; e-mail do Jan
# Krueder de 17/06/2026 ("Tabela cheia 10% / Desc até 10% 9% / 10,1 até 20% 8%"); decisões
# operacionais de 20–21/09/2026.
DATA_VIGENCIA_2026_09_21 = date(2026, 9, 21)
FONTE_2026_09_21 = "Decisão comercial ANARA — 21/09/2026 (B2B, tabela 2×, comissão por item)"
ROTULO_2026_09_21 = "POLITICA_COMERCIAL_2026-09-21"

#: Premissas versionadas da política nova — lidas do banco e pinadas no item.
CHAVE_COMISSAO_B2B = "comissao_b2b_pct"            # 5% no B2B
CHAVE_FATOR_TABELA = "fator_tabela"                # tabela = fator × B2B
CHAVE_FAIXAS_COMISSAO = "comissao_faixas_desconto" # JSON: [[limite_desconto, taxa], ...]
COMISSAO_B2B_PCT = Decimal("0.05")
FATOR_TABELA = Decimal("2")
#: `[(limite superior do desconto, taxa)]`, em ordem. A faixa vale para desconto ≤ limite.
#: Desconto exatamente 0 é caso próprio: `COMISSAO_BASE_PCT` (10%). Acima do último limite,
#: `COMISSAO_MINIMA_PCT` (5%).
FAIXAS_COMISSAO_2026_09_21 = (
    (Decimal("0.10"), Decimal("0.09")),
    (Decimal("0.20"), Decimal("0.08")),
    (Decimal("0.30"), Decimal("0.07")),
    (Decimal("0.40"), Decimal("0.06")),
)

#: Exceção comercial da política nova: proposta abaixo do B2B.
PRECO_ABAIXO_B2B = "PRECO_ABAIXO_B2B"
#: Modos de negociação persistidos no item. A alavanca da política nova é o DESCONTO sobre
#: a tabela — é o que sobrevive a uma mudança de cenário (o preço absoluto é rederivado).
MODO_PRECO = "preco"
MODO_DESCONTO = "desconto"


def versao_da_politica(item) -> Optional[str]:
    """Qual política formou o item: `"2026-09-21"`, `"2026-09-16"` ou `None` (anterior).

    Decidida pelo RÓTULO congelado em `politica_comercial`. Um item com rótulo desconhecido
    é tratado como o mais antigo que ele pode ser (16/09) — nunca promovido à política nova.
    """
    rotulo = getattr(item, "politica_comercial", None)
    if rotulo is None:
        return None
    if rotulo == ROTULO_2026_09_21:
        return "2026-09-21"
    return "2026-09-16"


def item_da_politica_2026_09_21(item) -> bool:
    return versao_da_politica(item) == "2026-09-21"


def item_da_politica_2026_09_16(item) -> bool:
    return versao_da_politica(item) == "2026-09-16"


def faixas_de_json(texto) -> tuple:
    """Desserializa a premissa `comissao_faixas_desconto`. Sem texto → a constante."""
    import json
    if not texto:
        return FAIXAS_COMISSAO_2026_09_21
    bruto = json.loads(texto) if isinstance(texto, str) else texto
    faixas = tuple((D(lim), D(taxa)) for lim, taxa in bruto)
    if not faixas or any(l is None or t is None for l, t in faixas):
        raise PoliticaInconsistente("faixas de comissão inválidas na premissa")
    return tuple(sorted(faixas))


def faixa_comissao(desconto, base=COMISSAO_BASE_PCT, minima=COMISSAO_MINIMA_PCT,
                   faixas=FAIXAS_COMISSAO_2026_09_21) -> Decimal:
    """A taxa de comissão do item pela escada de desconto sobre a tabela.

        desconto == 0            → base (10%)          preço na tabela ou acima dela
        0 < desconto ≤ 0,10      → 9%
        0,10 < desconto ≤ 0,20   → 8%
        0,20 < desconto ≤ 0,30   → 7%
        0,30 < desconto ≤ 0,40   → 6%
        desconto > 0,40          → mínima (5%)        inclui o próprio B2B (50%)

    Comparação exata em Decimal: 10,00% cai em 9%, 10,01% em 8%. Desconto negativo (preço
    acima da tabela) vale zero. Nada aqui reduz abaixo da mínima.
    """
    d = D0(desconto)
    if d <= ZERO:
        return D0(base)
    for limite, taxa in faixas:
        if d <= limite:
            return taxa
    return D0(minima)


@dataclass
class ComissaoDoItem:
    """A conta de comissão de UM item da política de 21/09/2026 — completa e auditável."""
    preco_tabela: Decimal
    preco_b2b: Decimal
    preco_negociado: Decimal
    desconto_vs_tabela: Decimal
    taxa_pct: Decimal
    icms_base_comissao_pct: Decimal
    base_comissionavel: Decimal
    comissao_valor: Decimal
    abaixo_do_b2b: bool

    def como_dict(self) -> dict:
        from app.dinheiro import para_float
        return {"preco_tabela": para_float(self.preco_tabela), "preco_b2b": para_float(self.preco_b2b),
                "preco_negociado": para_float(self.preco_negociado),
                "desconto_vs_tabela_pct": para_float(self.desconto_vs_tabela),
                "comissao_faixa_pct": para_float(self.taxa_pct),
                "icms_base_comissao_pct": para_float(self.icms_base_comissao_pct),
                "base_comissionavel": para_float(self.base_comissionavel),
                "comissao_valor": para_float(self.comissao_valor),
                "abaixo_do_b2b": self.abaixo_do_b2b}


def comissao_do_item(preco_tabela, preco_b2b, preco_negociado, quantidade,
                     icms_base_comissao_pct, base=COMISSAO_BASE_PCT,
                     minima=COMISSAO_MINIMA_PCT, faixas=FAIXAS_COMISSAO_2026_09_21
                     ) -> ComissaoDoItem:
    """Taxa, base e valor da comissão de um item, sem consultar nada de fora.

    A base é `receita × (1 − ICMS dedutível)` com a receita já ao centavo; a comissão vira
    centavo uma vez, no fim — a mesma ordem de `pricing_engine.calcular_por_preco`, para
    que o valor aqui e o gravado no item sejam o mesmo número.
    """
    from app.pricing_engine import desconto_vs_tabela
    tabela = dinheiro(preco_tabela) or ZERO
    b2b = dinheiro(preco_b2b) or ZERO
    negociado = dinheiro(preco_negociado) or ZERO
    desconto = desconto_vs_tabela(negociado, tabela)
    taxa = faixa_comissao(desconto, base, minima, faixas)
    receita = dinheiro(negociado * D0(quantidade)) or ZERO
    fator = D("1") - D0(icms_base_comissao_pct)
    base_cheia = receita * fator
    return ComissaoDoItem(
        preco_tabela=tabela, preco_b2b=b2b, preco_negociado=negociado,
        desconto_vs_tabela=desconto, taxa_pct=taxa,
        icms_base_comissao_pct=D0(icms_base_comissao_pct),
        base_comissionavel=dinheiro(base_cheia) or ZERO,
        comissao_valor=dinheiro(base_cheia * taxa) or ZERO,
        abaixo_do_b2b=(negociado < b2b) if b2b > ZERO else False)


def taxa_efetiva_por_base(comissao_total, base_total) -> Optional[Decimal]:
    """Σ comissão ÷ Σ base comissionável. Nunca se reaplica uma taxa única aos itens."""
    return divide(comissao_total, base_total)


# --- margens da política de 21/09/2026 (dados; as regras nascem em `seeds`/script) ---------
#: (nome, fornecedor, família, min_tc, max_tc_exclusivo, margem)
#: Todas margens FINAIS no B2B, pós-comissão de 5%. Nada de default silencioso: produto sem
#: regra bloqueia. A "regra dos 400 fios" está escrita linha a linha (+1 p.p.), não como
#: multiplicador.
FAMILIAS_LENCOL_2026_09_21 = ("Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet")
FAMILIAS_TOALHA_2026_09_21 = ("Bath Towel", "Hand Towel", "Face Towel", "Pool Towel",
                              "Beach Towel", "Bath Mat", "Wash Cloth", "Towel")
CODIGO_ELIS = "ELIS"


def margens_2026_09_21() -> list:
    regras = []
    prio = 20
    regras.append(dict(nome="Daune — B2B 13% · política 21/09/2026", fornecedor_codigo=CODIGO_DAUNE,
                       margem_pct=Decimal("0.13"), prioridade=prio))
    regras.append(dict(nome="Decor Tricot — B2B 13% · política 21/09/2026",
                       fornecedor_codigo=CODIGO_DECOR, margem_pct=Decimal("0.13"), prioridade=prio))
    regras.append(dict(nome="ELIS (cobertores) — B2B 14% · política 21/09/2026",
                       fornecedor_codigo=CODIGO_ELIS, margem_pct=Decimal("0.14"), prioridade=prio))
    for f in FAMILIAS_LENCOL_2026_09_21:
        regras.append(dict(nome=f"KTC — {f} < 300 fios · política 21/09/2026", fornecedor_codigo="KTC",
                           familia=f, max_thread_count=300, margem_pct=Decimal("0.20"), prioridade=30))
        regras.append(dict(nome=f"KTC — {f} 300–399 fios · política 21/09/2026", fornecedor_codigo="KTC",
                           familia=f, min_thread_count=300, max_thread_count=400,
                           margem_pct=Decimal("0.22"), prioridade=30))
        regras.append(dict(nome=f"KTC — {f} ≥ 400 fios · política 21/09/2026", fornecedor_codigo="KTC",
                           familia=f, min_thread_count=400, margem_pct=Decimal("0.23"), prioridade=30))
    for f in ("Pillow Case", "Duvet Cover"):
        regras.append(dict(nome=f"KTC — {f} < 400 fios · política 21/09/2026", fornecedor_codigo="KTC",
                           familia=f, max_thread_count=400, margem_pct=Decimal("0.19"), prioridade=30))
        regras.append(dict(nome=f"KTC — {f} ≥ 400 fios · política 21/09/2026", fornecedor_codigo="KTC",
                           familia=f, min_thread_count=400, margem_pct=Decimal("0.20"), prioridade=30))
        # fronha/capa sem fios estruturados não pode cair fora da regra: vale a margem base
        regras.append(dict(nome=f"KTC — {f} sem fios estruturados · política 21/09/2026",
                           fornecedor_codigo="KTC", familia=f, margem_pct=Decimal("0.19"), prioridade=35))
    for f in FAMILIAS_TOALHA_2026_09_21:
        regras.append(dict(nome=f"KTC — {f} (toalha) B2B 16% · política 21/09/2026",
                           fornecedor_codigo="KTC", familia=f, margem_pct=Decimal("0.16"), prioridade=40))
    regras.append(dict(nome="KTC — Bathrobe (roupão) B2B 14% · política 21/09/2026",
                       fornecedor_codigo="KTC", familia="Bathrobe", margem_pct=Decimal("0.14"), prioridade=40))
    regras.append(dict(nome="KTC — demais famílias ≥ 400 fios B2B 20% · política 21/09/2026",
                       fornecedor_codigo="KTC", min_thread_count=400, margem_pct=Decimal("0.20"),
                       prioridade=55))
    regras.append(dict(nome="KTC — demais famílias B2B 19% · política 21/09/2026",
                       fornecedor_codigo="KTC", margem_pct=Decimal("0.19"), prioridade=60))
    for r in regras:
        r.update(piso_pct=None, comissao_formacao_pct=COMISSAO_B2B_PCT, preco_travado=False,
                 politica=ROTULO_2026_09_21, fonte=FONTE_2026_09_21,
                 valid_from=DATA_VIGENCIA_2026_09_21)
    return regras


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

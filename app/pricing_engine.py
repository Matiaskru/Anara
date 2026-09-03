"""Módulo isolado de precificação.

Reproduz a lógica já existente na planilha Anara (aba 02_RESUMO_VISUAL,
colunas D-L): a partir do custo NET, aplica-se um markup E (alavanca
interna) para chegar num preço intermediário F = custo*(1+E); esse F é
"grossed up" pra cobrir ICMS + PIS/COFINS + Encargos Financeiros + comissão
comercial (por faixa de markup) e vira o preço final ao cliente:

    F = custo * (1 + E)
    preco = F / (1 - icms% - pis_cofins% - encargo_fin% - comissao%(E))
    lucro = faturamento - impostos - comissao - custo_total   (== custo_total * E)
    margem_liquida = lucro / faturamento

A interface nova expõe **preço** (Modo 1) ou **margem líquida** (Modo 2)
como os campos editáveis — o markup E vira uma variável interna, resolvida
por busca na tabela de faixas de comissão (ela é pequena, poucas faixas),
só pra reproduzir o resultado da planilha. Nada aqui sabe de FastAPI, HTML
ou banco — só números + uma TaxRuleSet. As regras fiscais vêm de fora
(snapshot salvo em BaseImportacao/Cotacao), nunca hardcoded aqui, pra
permitir estender no futuro por UF origem/destino, NCM etc. sem alterar
telas.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class TaxRuleSet:
    icms_pct: float
    pis_cofins_pct: float
    encargo_financeiro_pct: float
    # ordenada por markup_min ascendente: (markup_min, comissao_pct)
    comissao_tabela: List[Tuple[float, float]] = field(default_factory=list)
    origem_uf: str = "SC"

    def taxa_fixa(self) -> float:
        return self.icms_pct + self.pis_cofins_pct + self.encargo_financeiro_pct

    def comissao_para_markup(self, markup: float) -> float:
        if not self.comissao_tabela:
            return 0.0
        pct = self.comissao_tabela[0][1]
        for markup_min, comissao_pct in sorted(self.comissao_tabela):
            if markup >= markup_min:
                pct = comissao_pct
            else:
                break
        return pct

    def faixas(self):
        """(markup_min, markup_max_exclusivo_ou_None, comissao_pct) em ordem."""
        tabela = sorted(self.comissao_tabela)
        out = []
        for i, (mmin, pct) in enumerate(tabela):
            mmax = tabela[i + 1][0] if i + 1 < len(tabela) else None
            out.append((mmin, mmax, pct))
        return out


@dataclass
class ResultadoPrecificacao:
    preco_negociado: float
    quantidade: float
    faturamento: float
    custo_total: float
    impostos: float
    comissao: float
    lucro: float
    margem_liquida: float
    markup_implicito: float
    diferenca_pct_vs_base: Optional[float] = None


def _vazio(preco: float, qtd: float) -> ResultadoPrecificacao:
    return ResultadoPrecificacao(preco, qtd, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None)


def _sem_custo(preco: float, qtd: float) -> ResultadoPrecificacao:
    """Produto sem custo cadastrado: o faturamento é real, o resto não dá para afirmar.

    Acontece com fornecedor nacional cujo custo de compra ainda não entrou. O item precisa
    continuar cotável — o que não se faz é inventar margem para ele.
    """
    return ResultadoPrecificacao(preco, qtd, preco * qtd, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None)


def _resolve_markup_de_preco(custo: float, preco: float, regras: TaxRuleSet):
    """Acha o markup E (e a faixa de comissão) consistente com um preço final dado."""
    taxa_fixa = regras.taxa_fixa()
    faixas = regras.faixas() or [(0.0, None, 0.0)]
    for mmin, mmax, pct in faixas:
        e = preco * (1 - taxa_fixa - pct) / custo - 1
        if e >= mmin - 1e-9 and (mmax is None or e < mmax - 1e-9):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    e = preco * (1 - taxa_fixa - pct) / custo - 1
    return e, pct


def _resolve_markup_de_margem(margem_alvo: float, regras: TaxRuleSet):
    """Acha o markup E (e a faixa de comissão) consistente com uma margem líquida alvo."""
    taxa_fixa = regras.taxa_fixa()
    faixas = regras.faixas() or [(0.0, None, 0.0)]
    for mmin, mmax, pct in faixas:
        denom = (1 - taxa_fixa - pct) - margem_alvo
        if denom <= 1e-9:
            continue
        e = margem_alvo / denom
        if e >= mmin - 1e-9 and (mmax is None or e < mmax - 1e-9):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    denom = max((1 - taxa_fixa - pct) - margem_alvo, 1e-6)
    e = margem_alvo / denom
    return e, pct


def calcular_por_preco(custo: float, qtd: float, preco_negociado: float,
                        regras: TaxRuleSet, preco_base: Optional[float] = None
                        ) -> ResultadoPrecificacao:
    """Modo 1: usuário define o preço unitário final; margem é derivada."""
    custo = custo or 0.0
    qtd = qtd or 0.0
    preco_negociado = preco_negociado or 0.0
    if custo <= 0 or preco_negociado <= 0:
        r = _sem_custo(preco_negociado, qtd) if preco_negociado > 0 else _vazio(preco_negociado, qtd)
        r.diferenca_pct_vs_base = ((preco_negociado / preco_base) - 1) if preco_base else None
        return r

    markup, comissao_pct = _resolve_markup_de_preco(custo, preco_negociado, regras)
    faturamento = preco_negociado * qtd
    custo_total = custo * qtd
    impostos = faturamento * regras.taxa_fixa()
    comissao = faturamento * comissao_pct
    lucro = faturamento - impostos - comissao - custo_total
    margem = (lucro / faturamento) if faturamento else 0.0
    diff = ((preco_negociado / preco_base) - 1) if preco_base else None

    return ResultadoPrecificacao(
        preco_negociado=preco_negociado, quantidade=qtd, faturamento=faturamento,
        custo_total=custo_total, impostos=impostos, comissao=comissao, lucro=lucro,
        margem_liquida=margem, markup_implicito=markup, diferenca_pct_vs_base=diff,
    )


def calcular_por_margem(custo: float, qtd: float, margem_alvo: float,
                         regras: TaxRuleSet, preco_base: Optional[float] = None
                         ) -> ResultadoPrecificacao:
    """Modo B: usuário define a margem efetiva desejada ("quero ficar com 12%
    depois de tudo"); preço é derivado:
        preco = custo / (1 - icms - pis_cofins - encargos - comissao - margem_alvo)
    """
    custo = custo or 0.0
    qtd = qtd or 0.0
    if custo <= 0:
        return _vazio(0.0, qtd)

    markup, comissao_pct = _resolve_markup_de_margem(margem_alvo, regras)
    taxa_fixa = regras.taxa_fixa()
    f = custo * (1 + markup)
    denom = 1 - taxa_fixa - comissao_pct
    preco = f / denom if denom > 1e-9 else 0.0

    return calcular_por_preco(custo, qtd, preco, regras, preco_base)


def calcular_por_markup(custo: float, qtd: float, markup: float,
                         regras: TaxRuleSet, preco_base: Optional[float] = None
                         ) -> ResultadoPrecificacao:
    """Modo A: usuário define o markup sobre o custo NET; preço é derivado:
        preco = custo * (1 + markup) / (1 - icms - pis_cofins - encargos - comissao(markup))
    Mesma fórmula da coluna J de 02_RESUMO_VISUAL no Excel.
    """
    custo = custo or 0.0
    qtd = qtd or 0.0
    if custo <= 0:
        return _vazio(0.0, qtd)

    comissao_pct = regras.comissao_para_markup(markup)
    denom = 1 - regras.taxa_fixa() - comissao_pct
    preco = custo * (1 + markup) / denom if denom > 1e-9 else 0.0

    return calcular_por_preco(custo, qtd, preco, regras, preco_base)

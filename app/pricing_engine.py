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

## Precisão (Sessão 3B)

Todo o cálculo é `Decimal`, com a política de `app.dinheiro`. Os parâmetros continuam
podendo chegar como `float` — a normalização acontece na entrada, uma vez.

**A ordem importa, e é esta:**

1. forma-se o **preço preciso**, sem arredondar nada no caminho;
2. o preço vira **preço comercial** — 2 casas, `ROUND_HALF_UP`. É o que o cliente paga;
3. impostos, comissão, frete e lucro são recalculados **sobre o preço comercial**, nunca
   sobre o preço pré-arredondamento.

Por isso `margem_alvo` e `margem_liquida` são campos **separados**: pedir 14% e cobrar
R$ 377,12 (em vez de R$ 377,1149…) entrega 13,9998%, e é 13,9998% que a memória do preço
registra. Fingir que continua exatamente 14% seria reportar um número que ninguém cobrou.

O lucro é apurado como **resíduo** — receita menos todos os componentes já quantizados.
É o que faz a linha reconciliar ao centavo por construção, sem sobra "aproximada":

    receita = custo_total + impostos + comissão + frete_cf + frete_rv + lucro
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Tuple

from app.dinheiro import D, D0, ZERO, dinheiro, divide, para_float


@dataclass
class TaxRuleSet:
    icms_pct: Decimal
    pis_cofins_pct: Decimal
    encargo_financeiro_pct: Decimal
    # ordenada por markup_min ascendente: (markup_min, comissao_pct)
    comissao_tabela: List[Tuple[Decimal, Decimal]] = field(default_factory=list)
    origem_uf: str = "SC"
    # --- frete comercial (Sessão 3A) ---
    # CF entra no NUMERADOR (custo fixo do embarque, rateado ao item); RV entra no
    # DENOMINADOR (percentuais sobre a NF: ADV, GRIS, fiel depositário). Congelar o RV a partir
    # de um preço preliminar não fecharia com a margem-alvo.
    frete_cf_unitario: Decimal = ZERO
    frete_rv_pct: Decimal = ZERO

    def __post_init__(self):
        """Fronteira de entrada: aceita float do banco, guarda Decimal.

        É aqui que o `float` para. Um TaxRuleSet montado com `icms_pct=0.18` guarda
        `Decimal("0.18")`, e não o dízimo binário de 0,18 — nenhum chamador precisa saber
        disso.
        """
        self.icms_pct = D0(self.icms_pct)
        self.pis_cofins_pct = D0(self.pis_cofins_pct)
        self.encargo_financeiro_pct = D0(self.encargo_financeiro_pct)
        self.frete_cf_unitario = D0(self.frete_cf_unitario)
        self.frete_rv_pct = D0(self.frete_rv_pct)
        self.comissao_tabela = [(D0(mmin), D0(pct)) for mmin, pct in self.comissao_tabela]

    def taxa_fixa(self) -> Decimal:
        return self.icms_pct + self.pis_cofins_pct + self.encargo_financeiro_pct

    def rates_variaveis(self) -> Decimal:
        """Tudo que é percentual da receita e não é comissão — impostos + rate logístico."""
        return self.taxa_fixa() + self.frete_rv_pct

    def comissao_para_markup(self, markup) -> Decimal:
        """Faixa de comissão do markup E.

        A comparação é `Decimal`, exata: um E de exatamente 60% cai na faixa de 6%, e não
        na de 5% porque o binário devolveu 0,5999999999999999. Era o risco real de fazer
        isso em `float` — a faixa muda o preço inteiro, não um centavo.
        """
        if not self.comissao_tabela:
            return ZERO
        markup = D0(markup)
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
    preco_negociado: Decimal          # preço COMERCIAL — 2 casas, é o que se cobra
    quantidade: Decimal
    faturamento: Decimal              # preço comercial × quantidade, ao centavo
    custo_total: Decimal
    impostos: Decimal
    comissao: Decimal
    lucro: Decimal
    margem_liquida: Decimal           # margem REAL, sobre a receita efetivamente cobrada
    markup_implicito: Decimal
    diferenca_pct_vs_base: Optional[Decimal] = None
    frete_cf: Decimal = ZERO          # custo fixo do embarque atribuído a esta linha
    frete_rv: Decimal = ZERO          # rate variável logístico, recomposto sobre a receita
    # --- precisão (Sessão 3B) ---
    preco_preciso: Optional[Decimal] = None   # antes do arredondamento comercial
    margem_alvo: Optional[Decimal] = None     # a que foi pedida; ≠ da que saiu

    @property
    def ajuste_arredondamento(self) -> Optional[Decimal]:
        """Quanto o centavo comercial moveu o preço. Sempre ≤ meio centavo."""
        if self.preco_preciso is None:
            return None
        return self.preco_negociado - self.preco_preciso

    def reconcilia(self) -> bool:
        """Prova do §12: a receita é exatamente a soma dos componentes da linha."""
        return self.faturamento == (self.custo_total + self.impostos + self.comissao
                                    + self.frete_cf + self.frete_rv + self.lucro)

    def como_dict(self) -> dict:
        """Representação externa (JSON, template, banco): `float`, não string.

        Os valores monetários já estão quantizados em 2 casas, e um decimal de 2 casas cabe
        em `float` sem perda — a ida e a volta reproduzem o mesmo centavo. As frações
        (margem, markup) seguem como float pelo mesmo motivo de sempre: são exibição.
        """
        return {
            "preco_negociado": para_float(self.preco_negociado),
            "quantidade": para_float(self.quantidade),
            "faturamento": para_float(self.faturamento),
            "custo_total": para_float(self.custo_total),
            "impostos": para_float(self.impostos),
            "comissao": para_float(self.comissao),
            "lucro": para_float(self.lucro),
            "margem_liquida": para_float(self.margem_liquida),
            "markup_implicito": para_float(self.markup_implicito),
            "diferenca_pct_vs_base": para_float(self.diferenca_pct_vs_base),
            "frete_cf": para_float(self.frete_cf),
            "frete_rv": para_float(self.frete_rv),
            "preco_preciso": para_float(self.preco_preciso),
            "margem_alvo": para_float(self.margem_alvo),
        }


def _vazio(preco, qtd) -> ResultadoPrecificacao:
    return ResultadoPrecificacao(dinheiro(preco) or ZERO, D0(qtd), ZERO, ZERO, ZERO, ZERO,
                                 ZERO, ZERO, ZERO, None)


def _sem_custo(preco, qtd) -> ResultadoPrecificacao:
    """Produto sem custo cadastrado: o faturamento é real, o resto não dá para afirmar.

    Acontece com fornecedor nacional cujo custo de compra ainda não entrou. O item precisa
    continuar cotável — o que não se faz é inventar margem para ele.
    """
    preco_c = dinheiro(preco) or ZERO
    qtd_d = D0(qtd)
    return ResultadoPrecificacao(preco_c, qtd_d, dinheiro(preco_c * qtd_d), ZERO, ZERO, ZERO,
                                 ZERO, ZERO, ZERO, None)


def _resolve_markup_de_preco(custo: Decimal, preco: Decimal, regras: TaxRuleSet):
    """Acha o markup E (e a faixa de comissão) consistente com um preço final dado.

    Sem épsilon: a comparação de faixa é exata em Decimal. O `1e-9` que existia aqui era
    tolerância para erro binário de float — com Decimal ele não protege nada e só borraria
    a fronteira que o §9 exige que seja nítida.
    """
    taxa_fixa = regras.rates_variaveis()
    faixas = regras.faixas() or [(ZERO, None, ZERO)]
    for mmin, mmax, pct in faixas:
        e = preco * (1 - taxa_fixa - pct) / custo - 1
        if e >= mmin and (mmax is None or e < mmax):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    e = preco * (1 - taxa_fixa - pct) / custo - 1
    return e, pct


def _resolve_markup_de_margem(margem_alvo: Decimal, regras: TaxRuleSet):
    """Acha o markup E (e a faixa de comissão) consistente com uma margem líquida alvo."""
    taxa_fixa = regras.rates_variaveis()
    faixas = regras.faixas() or [(ZERO, None, ZERO)]
    for mmin, mmax, pct in faixas:
        denom = (1 - taxa_fixa - pct) - margem_alvo
        if denom <= 0:
            continue
        e = margem_alvo / denom
        if e >= mmin and (mmax is None or e < mmax):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    denom = (1 - taxa_fixa - pct) - margem_alvo
    if denom <= 0:
        denom = Decimal("0.000001")
    e = margem_alvo / denom
    return e, pct


def calcular_por_preco(custo, qtd, preco_negociado,
                       regras: TaxRuleSet, preco_base=None) -> ResultadoPrecificacao:
    """Modo 1: usuário define o preço unitário final; margem é derivada.

    **O preço que chega aqui é a receita real** — inclusive quando vem de `calcular_por_margem`,
    onde já foi arredondado para centavos. Todo componente é recomposto sobre ele.
    """
    custo = D0(custo)
    qtd = D0(qtd)
    # Um preço digitado é sempre uma quantia comercial: "99,9" é R$ 99,90.
    preco = dinheiro(preco_negociado) or ZERO
    base = D(preco_base)

    if custo <= 0 or preco <= 0:
        r = _sem_custo(preco, qtd) if preco > 0 else _vazio(preco, qtd)
        r.diferenca_pct_vs_base = (divide(preco, base) - 1) if base else None
        return r

    # O CF do frete é custo do embarque, não CNET: entra na formação do preço junto com o
    # custo, mas é reportado à parte para o waterfall ficar legível linha a linha.
    custo_efetivo = custo + regras.frete_cf_unitario
    markup, comissao_pct = _resolve_markup_de_preco(custo_efetivo, preco, regras)

    # §13: o total da linha é o preço unitário comercial × quantidade, quantizado ao centavo.
    # Nunca um total teórico arredondado por conta própria — os dois divergem, e o cliente
    # confere o primeiro.
    faturamento = dinheiro(preco * qtd)
    custo_total = dinheiro(custo * qtd)
    impostos = dinheiro(faturamento * regras.taxa_fixa())
    comissao = dinheiro(faturamento * comissao_pct)
    # O rate variável logístico só vira reais AGORA, sobre a receita final — nunca sobre um
    # preço preliminar.
    frete_rv = dinheiro(faturamento * regras.frete_rv_pct)
    frete_cf = dinheiro(regras.frete_cf_unitario * qtd)
    # Lucro por resíduo: é o que fecha a identidade da linha ao centavo, sem "aproximadamente".
    lucro = faturamento - impostos - comissao - custo_total - frete_cf - frete_rv
    margem = divide(lucro, faturamento) or ZERO
    diff = (divide(preco, base) - 1) if base else None

    return ResultadoPrecificacao(
        preco_negociado=preco, quantidade=qtd, faturamento=faturamento,
        custo_total=custo_total, impostos=impostos, comissao=comissao, lucro=lucro,
        margem_liquida=margem, markup_implicito=markup, diferenca_pct_vs_base=diff,
        frete_cf=frete_cf, frete_rv=frete_rv, preco_preciso=preco,
    )


def calcular_por_margem(custo, qtd, margem_alvo,
                        regras: TaxRuleSet, preco_base=None) -> ResultadoPrecificacao:
    """Modo B: usuário define a margem efetiva desejada ("quero ficar com 12%
    depois de tudo"); preço é derivado:
        preco = custo / (1 - icms - pis_cofins - encargos - comissao - margem_alvo)

    O preço sai preciso, é arredondado para centavos **uma vez**, e a margem devolvida é a
    que resulta desse centavo — não a que foi pedida.
    """
    custo = D0(custo)
    qtd = D0(qtd)
    margem_alvo = D0(margem_alvo)
    if custo <= 0:
        return _vazio(ZERO, qtd)

    markup, comissao_pct = _resolve_markup_de_margem(margem_alvo, regras)
    taxa_fixa = regras.rates_variaveis()
    f = (custo + regras.frete_cf_unitario) * (1 + markup)
    denom = 1 - taxa_fixa - comissao_pct
    preco_preciso = (f / denom) if denom > 0 else ZERO

    r = calcular_por_preco(custo, qtd, preco_preciso, regras, preco_base)
    r.preco_preciso = preco_preciso
    r.margem_alvo = margem_alvo
    return r


def calcular_por_markup(custo, qtd, markup,
                        regras: TaxRuleSet, preco_base=None) -> ResultadoPrecificacao:
    """Modo A: usuário define o markup sobre o custo NET; preço é derivado:
        preco = custo * (1 + markup) / (1 - icms - pis_cofins - encargos - comissao(markup))
    Mesma fórmula da coluna J de 02_RESUMO_VISUAL no Excel.
    """
    custo = D0(custo)
    qtd = D0(qtd)
    markup = D0(markup)
    if custo <= 0:
        return _vazio(ZERO, qtd)

    comissao_pct = regras.comissao_para_markup(markup)
    denom = 1 - regras.rates_variaveis() - comissao_pct
    preco_preciso = ((custo + regras.frete_cf_unitario) * (1 + markup) / denom) \
        if denom > 0 else ZERO

    r = calcular_por_preco(custo, qtd, preco_preciso, regras, preco_base)
    r.preco_preciso = preco_preciso
    return r

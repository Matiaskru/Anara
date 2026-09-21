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


# ---------------------------------------------------------------------------
# PIS/COFINS da venda — nominal × exclusão do ICMS da base
# ---------------------------------------------------------------------------
def icms_excluido_da_base(icms_pct, fcp_pct) -> Decimal:
    """A parcela da carga de ICMS que reduz a base de PIS/COFINS: **o total menos o FCP**.

    ## Por que o FCP fica de fora

    O que a contabilidade da Indústria Química Anastacio confirmou em 09/09/2026 foi que *"o
    percentual de 7,59% muda em função do ICMS"*. Isso estabelece a exclusão do **ICMS** —
    próprio, interestadual e o DIFAL que a remetente suporta. **Não estabelece** o tratamento
    do FCP/FECP, que é adicional de destinação específica e tem discussão própria.

    Enquanto essa validação não vier, a política é **conservadora**: o FCP permanece na base
    de PIS/COFINS. Manter o FCP na base produz uma alíquota efetiva **maior** — ou seja, o
    sistema reconhece mais imposto, não menos. Se a contabilidade depois confirmar que o FCP
    também sai da base, o efetivo cai e o preço cai; o caminho inverso teria emitido proposta
    com imposto subestimado, que é o erro que esta correção inteira existe para não repetir.

    Isto é **política operacional temporária**, não conclusão jurídica.

    ## O que NÃO muda

    O FCP continua entrando **integralmente no gross-up** através de `TaxRuleSet.icms_pct`:
    ele é tributo que reduz a receita e segue reduzindo. A segregação aqui é exclusivamente
    sobre *qual parcela da carga reduz a base de outro tributo*.

    ## Como é calculado

    Pelo resultado consolidado do motor fiscal, **subtraindo** o FCP que ele mesmo separou:

        excluído = icms_pct − fcp_pct

    Não se recompõe `interestadual + DIFAL` aqui. Recompor duplicaria a lógica fiscal e é
    justamente como se conta o FECP do RJ duas vezes — a coluna `aliquota_interna` do RJ vale
    22% já com o FECP dentro, e a base é 20%.

    Exemplo, SP→RJ não contribuinte (vale para as duas naturezas, que repartem diferente e
    somam o mesmo):

        KTC importada   4% + 16% DIFAL + 2% FCP = 22% total → excluído 20%
        nacional       12% +  8% DIFAL + 2% FCP = 22% total → excluído 20%
    """
    icms = D(icms_pct)
    fcp = D(fcp_pct)
    if icms is None:
        raise ValueError(
            "ICMS da operação não resolvido — o cenário fiscal precisa resolver antes de "
            "formar preço.")
    if fcp is None:
        # O motor fiscal devolve `fcp_pct` sempre que resolve o cenário: `ZERO` onde o FCP não
        # é ônus da Anara, e o valor cadastrado onde é — e **bloqueia** quando o FCP é material
        # e desconhecido. Se mesmo assim chegar `None` aqui, é cenário que o motor considerou
        # indeterminado: assumir zero excluiria o FCP da base sem saber se ele existe.
        raise ValueError(
            "FCP não resolvido — sem ele não se sabe qual parcela da carga de ICMS reduz a "
            "base de PIS/COFINS. O cenário fiscal precisa resolver antes de formar preço.")
    return max(icms - fcp, ZERO)


def pis_cofins_efetivo(nominal_pct, icms_pct) -> Decimal:
    """Alíquota EFETIVA de PIS/COFINS sobre a receita, com o ICMS excluído da base.

        efetivo = nominal × (1 − ICMS da operação)

    O ICMS é excluído da base de cálculo de PIS/COFINS, então o percentual efetivo **depende
    da alíquota de ICMS da operação** — não é constante. Confirmado pela contabilidade da
    Indústria Química Anastacio (Brendo Simão, 09/09/2026), com a planilha
    "Fator Cálculo Exclusão ICMS .xlsx" como evidência.

    Os 7,59% que o sistema usava como constante global eram apenas a aproximação do cenário
    de ICMS 18% (7,585%). Aplicá-los a uma venda interestadual — onde o ICMS cai para 12%, 7%
    ou 4% — subestimava o encargo em até 1,29 ponto percentual.

    É **fórmula, não tabela**: qualquer alíquota de ICMS resolve, inclusive as que ainda não
    existem no cadastro.

        18%  → 9,25% × 0,82 = 7,585%
        12%  → 9,25% × 0,88 = 8,14%
         7%  → 9,25% × 0,93 = 8,6025%
         4%  → 9,25% × 0,96 = 8,88%
        20%  → 9,25% × 0,80 = 7,40%    ← RJ não contribuinte: 22% de carga menos 2% de FCP

    ## Qual ICMS entra aqui

    O que `icms_excluido_da_base()` devolve — a carga de ICMS **menos o FCP**. Não é o
    `ResultadoFiscal.icms_pct` cru: ver a docstring daquela função para o porquê.

    `EstadoFiscal.carga_final` **não** entra: ela expressa o diferencial sobre uma base
    anterior à inclusão do ICMS de destino e não é percentual da receita final.

    ## Precisão

    Nada é quantizado: o resultado é uma alíquota, não dinheiro, e alíquota arredondada no meio
    da cadeia é exatamente o erro que 7,59% representava. `Decimal` puro, 34 dígitos, até o
    denominador do gross-up.
    """
    nominal = D(nominal_pct)
    icms = D(icms_pct)
    if nominal is None:
        raise ValueError("PIS/COFINS nominal ausente — premissa econômica não se inventa.")
    if icms is None:
        # Sem ICMS resolvido não existe efetivo. Devolver o nominal aqui seria assumir ICMS
        # zero, que é justamente o fallback silencioso que o projeto proíbe.
        raise ValueError(
            "ICMS da operação não resolvido — sem ele não há PIS/COFINS efetivo. "
            "O cenário fiscal precisa resolver antes de formar preço.")
    return nominal * (D("1") - icms)


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
    # --- base comissionável (política comercial de 21/09/2026) ---
    # Fração da receita que NÃO entra na base da comissão: o ICMS próprio suportado pela
    # Anara mais o DIFAL que ela recolhe — nunca o FCP, o PIS/COFINS, o encargo ou o frete.
    # Zero (default) reproduz a regra anterior: comissão sobre o faturamento bruto. Quem
    # decide o valor é `pricing_service.regras_da_cotacao`, a partir do que o motor fiscal
    # decompôs para o item (`icms_pct − fcp_pct`).
    comissao_base_icms_pct: Decimal = ZERO

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
        self.comissao_base_icms_pct = D0(self.comissao_base_icms_pct)
        self.comissao_tabela = [(D0(mmin), D0(pct)) for mmin, pct in self.comissao_tabela]

    def fator_base_comissao(self) -> Decimal:
        """Quanto de cada real de receita é base comissionável: `1 − ICMS dedutível`."""
        return D("1") - self.comissao_base_icms_pct

    def comissao_efetiva_sobre_receita(self, comissao_pct) -> Decimal:
        """A comissão como fração da RECEITA, dada a taxa sobre a base comissionável.

        É o que entra no denominador do gross-up: `taxa × (1 − ICMS dedutível)`. Com base
        bruta (legado) o fator é 1 e a conta é a de sempre.
        """
        return D0(comissao_pct) * self.fator_base_comissao()

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
    # --- política 21/09/2026: a comissão tem base própria ---
    base_comissionavel: Optional[Decimal] = None   # receita − ICMS dedutível, ao centavo
    comissao_pct: Optional[Decimal] = None         # taxa aplicada sobre a base
    icms_base_comissao_pct: Optional[Decimal] = None   # a parcela deduzida (memória)

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
            "base_comissionavel": para_float(self.base_comissionavel),
            "comissao_pct": para_float(self.comissao_pct),
            "icms_base_comissao_pct": para_float(self.icms_base_comissao_pct),
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
        e = preco * (1 - taxa_fixa - regras.comissao_efetiva_sobre_receita(pct)) / custo - 1
        if e >= mmin and (mmax is None or e < mmax):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    e = preco * (1 - taxa_fixa - regras.comissao_efetiva_sobre_receita(pct)) / custo - 1
    return e, pct


def _resolve_markup_de_margem(margem_alvo: Decimal, regras: TaxRuleSet):
    """Acha o markup E (e a faixa de comissão) consistente com uma margem líquida alvo."""
    taxa_fixa = regras.rates_variaveis()
    faixas = regras.faixas() or [(ZERO, None, ZERO)]
    for mmin, mmax, pct in faixas:
        denom = (1 - taxa_fixa - regras.comissao_efetiva_sobre_receita(pct)) - margem_alvo
        if denom <= 0:
            continue
        e = margem_alvo / denom
        if e >= mmin and (mmax is None or e < mmax):
            return e, pct
    mmin, mmax, pct = faixas[-1]
    denom = (1 - taxa_fixa - regras.comissao_efetiva_sobre_receita(pct)) - margem_alvo
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
    # Política de 21/09/2026: a comissão incide sobre a receita LÍQUIDA do ICMS que a Anara
    # suporta (próprio + DIFAL do remetente), nunca sobre FCP, PIS/COFINS, encargo ou frete.
    # A base fica cheia até a comissão virar centavo — quantizar a base e depois a comissão
    # arredondaria duas vezes o mesmo número. Com `comissao_base_icms_pct = 0` (política
    # anterior) a conta é exatamente a antiga: `faturamento × pct`.
    base_comissionavel = faturamento * regras.fator_base_comissao()
    comissao = dinheiro(base_comissionavel * comissao_pct)
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
        base_comissionavel=dinheiro(base_comissionavel), comissao_pct=comissao_pct,
        icms_base_comissao_pct=regras.comissao_base_icms_pct,
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
    denom = 1 - taxa_fixa - regras.comissao_efetiva_sobre_receita(comissao_pct)
    preco_preciso = (f / denom) if denom > 0 else ZERO

    r = calcular_por_preco(custo, qtd, preco_preciso, regras, preco_base)
    r.preco_preciso = preco_preciso
    r.margem_alvo = margem_alvo
    return r


# ---------------------------------------------------------------------------
# Política comercial de 21/09/2026 — B2B = menor centavo economicamente válido
# ---------------------------------------------------------------------------
#: Quantos centavos abaixo do preço preciso a busca começa. A margem sobre o preço comercial
#: não é estritamente monótona centavo a centavo — impostos e comissão são arredondados
#: separadamente —, então partir de "um pouco abaixo" e subir é o que garante achar o
#: PRIMEIRO centavo válido, e não um centavo válido qualquer.
_FOLGA_BUSCA_B2B = Decimal("0.05")
#: Limite de passos da busca. O preço preciso já é a solução contínua; se em mil centavos a
#: margem não fechar, o denominador está degenerado e é erro, não preço.
_PASSOS_MAXIMOS_B2B = 1000


class B2BIndeterminado(ValueError):
    """Não existe preço em centavos que cumpra a margem-alvo — denominador não positivo."""


def preco_b2b(custo, margem_alvo, regras: TaxRuleSet, preco_base=None) -> ResultadoPrecificacao:
    """O preço B2B recomendado da política de 21/09/2026: o **menor preço em centavos** cuja
    margem realizada (recomposta sobre o preço comercial, com todos os componentes
    quantizados) é **≥ margem-alvo**.

    Não é "resolver preciso, arredondar HALF_UP e subir 1 centavo": como impostos, comissão
    e custo são arredondados separadamente, a margem pode oscilar alguns milésimos entre
    centavos vizinhos, e o centavo imediatamente acima do preciso pode falhar enquanto um
    abaixo passa. A busca é determinística:

    1. resolve o preço preciso da forma fechada (`calcular_por_margem`);
    2. parte de alguns centavos ABAIXO dele e desce até um centavo que **não** cumpre o alvo
       (ou até R$ 0,01);
    3. sobe de centavo em centavo até o primeiro que cumpre.

    O resultado é sempre um par (`candidato − 0,01` falha, `candidato` cumpre) — é a
    propriedade que os testes provam. O preço devolvido é unitário (quantidade 1): o B2B é a
    referência da linha, e o total da linha continua sendo `preço × quantidade`.
    """
    custo = D0(custo)
    margem_alvo = D0(margem_alvo)
    if custo <= 0:
        return _vazio(ZERO, 1)
    solucao = calcular_por_margem(custo, 1, margem_alvo, regras, preco_base)
    if solucao.preco_preciso is None or solucao.preco_preciso <= 0:
        raise B2BIndeterminado(
            "A margem-alvo somada às cargas do cenário não deixa preço positivo — não existe "
            "B2B para este item neste cenário.")

    def cumpre(p: Decimal) -> bool:
        r = calcular_por_preco(custo, 1, p, regras, preco_base)
        return r.faturamento > ZERO and r.margem_liquida >= margem_alvo

    centavo = Decimal("0.01")
    # piso da busca: alguns centavos abaixo do preciso, nunca abaixo de um centavo
    candidato = (solucao.preco_preciso - _FOLGA_BUSCA_B2B).quantize(centavo, rounding="ROUND_DOWN")
    if candidato < centavo:
        candidato = centavo
    passos = 0
    # desce até falhar — garante que o vizinho de baixo do resultado é inválido
    while candidato > centavo and cumpre(candidato):
        candidato -= centavo
        passos += 1
        if passos > _PASSOS_MAXIMOS_B2B:
            raise B2BIndeterminado("busca do B2B não convergiu para baixo")
    passos = 0
    while not cumpre(candidato):
        candidato += centavo
        passos += 1
        if passos > _PASSOS_MAXIMOS_B2B:
            raise B2BIndeterminado("busca do B2B não convergiu para cima")

    r = calcular_por_preco(custo, 1, candidato, regras, preco_base)
    r.preco_preciso = solucao.preco_preciso
    r.margem_alvo = margem_alvo
    return r


def preco_de_tabela(preco_b2b_unitario, fator) -> Decimal:
    """`dinheiro(fator × B2B)`. A tabela é derivada do B2B vigente — nunca cache."""
    return dinheiro(D0(preco_b2b_unitario) * D0(fator)) or ZERO


def desconto_vs_tabela(preco_negociado, preco_tabela) -> Decimal:
    """`max(0, 1 − negociado ÷ tabela)`, exato em Decimal. Acima da tabela é 0, não negativo."""
    tabela = D0(preco_tabela)
    if tabela <= ZERO:
        return ZERO
    d = D("1") - D0(preco_negociado) / tabela
    return d if d > ZERO else ZERO


def preco_por_desconto(preco_tabela, desconto) -> Decimal:
    """O preço comercial que corresponde a um desconto sobre a tabela, ao centavo.

    Arredonda **para cima**: quem pede "20% de desconto" recebe o menor centavo cujo desconto
    efetivo é ≤ 20%, nunca 20,003%. Arredondar para baixo empurraria o desconto efetivo para a
    faixa seguinte da escada de comissão (20,003% → 7%, e não 8%) por um centavo que a
    vendedora não pediu — e é o desconto EFETIVO, recomputado do preço em centavos, que
    determina a faixa.
    """
    bruto = D0(preco_tabela) * (D("1") - D0(desconto))
    if bruto <= ZERO:
        return ZERO
    # Um desconto derivado de um preço em centavos (`1 − preço ÷ tabela`) carrega o ruído da
    # divisão na 34ª casa; sem esta limpeza, `233,34` voltaria como `233,340…01` e subiria um
    # centavo que ninguém pediu. Nove casas é muito abaixo de qualquer centavo e muito acima
    # do ruído.
    bruto = bruto.quantize(Decimal("1e-9"), rounding="ROUND_HALF_UP")
    return bruto.quantize(Decimal("0.01"), rounding="ROUND_UP")


# ---------------------------------------------------------------------------
# Política comercial (Fase 3A, 16/09/2026) — comissão fixa e comissão máxima para o piso
# ---------------------------------------------------------------------------
def com_comissao_fixa(regras: TaxRuleSet, comissao_pct) -> TaxRuleSet:
    """O mesmo `TaxRuleSet`, com a comissão presa em um percentual, qualquer que seja o markup.

    A política de 16/09/2026 trocou a comissão por faixa de markup por uma comissão de
    **formação** (10% não-Daune, 5% Daune) e uma comissão **negociada** da cotação inteira.
    Nada muda no gross-up: uma tabela de faixa única `[(0, pct)]` faz `comissao_para_markup`
    devolver `pct` para todo markup, e o motor segue idêntico — é assim que a comissão fixa
    entra sem uma segunda fórmula.
    """
    return TaxRuleSet(icms_pct=regras.icms_pct, pis_cofins_pct=regras.pis_cofins_pct,
                      encargo_financeiro_pct=regras.encargo_financeiro_pct,
                      comissao_tabela=[(ZERO, D0(comissao_pct))], origem_uf=regras.origem_uf,
                      frete_cf_unitario=regras.frete_cf_unitario,
                      frete_rv_pct=regras.frete_rv_pct,
                      comissao_base_icms_pct=regras.comissao_base_icms_pct)


def comissao_maxima_para_margem(custo, qtd, preco, margem_piso,
                                regras: TaxRuleSet) -> Optional[Decimal]:
    """A maior comissão (fração da receita) que ainda deixa a linha na margem-piso.

    Sai da **decomposição canônica** da linha, não de uma fórmula paralela: roda
    `calcular_por_preco` com comissão zero — impostos, custo total e frete quantizados
    exatamente como o motor os quantiza — e o lucro que sobra é o que pode ser repartido
    entre comissão e margem:

        lucro₀      = receita − impostos − custo_total − frete_CF − frete_RV     (comissão = 0)
        c_max       = lucro₀ ÷ receita − piso

    Pode ser negativa: o item já está abaixo do piso mesmo sem comissão nenhuma. `None`
    quando não há receita ou custo — sem eles não existe margem para preservar.
    """
    custo_d = D0(custo)
    preco_d = dinheiro(preco) or ZERO
    if custo_d <= ZERO or preco_d <= ZERO or D0(qtd) <= ZERO:
        return None
    sem_comissao = calcular_por_preco(custo_d, qtd, preco_d, com_comissao_fixa(regras, ZERO))
    if sem_comissao.faturamento <= ZERO:
        return None
    return divide(sem_comissao.lucro, sem_comissao.faturamento) - D0(margem_piso)


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
    denom = 1 - regras.rates_variaveis() - regras.comissao_efetiva_sobre_receita(comissao_pct)
    preco_preciso = ((custo + regras.frete_cf_unitario) * (1 + markup) / denom) \
        if denom > 0 else ZERO

    r = calcular_por_preco(custo, qtd, preco_preciso, regras, preco_base)
    r.preco_preciso = preco_preciso
    return r

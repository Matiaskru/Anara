"""Workflow comercial da cotação — estados, exceções, aprovação e emissão.

Este módulo responde três perguntas, e nenhuma delas é opinião da tela:

    esta cotação pode avançar para este estado?
    esta configuração tem exceção comercial que exija aprovação?
    esta configuração é exatamente a que foi aprovada?

## A distinção que sustenta tudo

**Aprovação não aprova uma cotação — aprova uma configuração.** "A cotação 123 está
aprovada" é uma frase perigosa: aprovada com qual desconto, com qual quantidade, para qual
destino fiscal? Por isso toda decisão fica presa a um **fingerprint**, o hash determinístico
do estado material que o aprovador viu. Mudou qualquer campo material, a decisão anterior
morre — não porque alguém a revogou, mas porque ela era sobre outra coisa.

## O que aprovação NÃO faz

Aprovação é decisão **comercial**. Ela não substitui dado econômico ausente. Um item
`A_COTAR` não tem preço; um frete CIF irresolvido não tem valor. Nenhum OWNER pode aprovar
um número que não existe — e é por isso que blocker duro e exceção comercial são conceitos
separados aqui, com funções separadas.

## Autonomia da vendedora — a regra mudou em 16/09/2026

Até a Fase 3A a autonomia de desconto era **zero**: preço abaixo do recomendado exigia
aprovação mesmo com margem boa. A política comercial de 16/09/2026 dá à vendedora um
**piso de margem** por item (`piso_margem_pct`, congelado no item): abaixo do recomendado e
acima do piso é autonomia; abaixo do piso — depois de a comissão da cotação ter caído até o
mínimo — é **exceção comercial** (`MARGEM_ABAIXO_PISO`), aprovável pelo workflow canônico.

Dois casos continuam com a regra antiga, de propósito: o item com **preço travado** (Daune),
cujo negociado só pode ser o recomendado — se estiver abaixo, é exceção; e o item **anterior
à política** (sem piso congelado), que segue sendo avaliado como foi formado.

Nada aqui conhece FastAPI, Jinja nem sessão de banco: recebe os objetos já lidos e devolve
resultado. O que grava é `workflow_service`.
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Sequence

from app.dinheiro import CENTAVO, D, D0, ZERO, dinheiro, divide, para_float

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------
# Os canônicos do workflow. `rascunho` e `enviada` já existiam com exatamente esta
# semântica — reaproveitados em vez de duplicados. `fechada`, `pedido` e `perdida` são
# estados comerciais legados (WON/LOST), fora deste workflow e intocados: dar sentido a
# eles é assunto da Sessão 7.
DRAFT = "rascunho"
PENDING_APPROVAL = "aguardando_aprovacao"
APPROVED = "aprovada"
ISSUED = "emitida"
SENT = "enviada"
CANCELLED = "cancelada"

ESTADOS_DO_WORKFLOW = (DRAFT, PENDING_APPROVAL, APPROVED, ISSUED, SENT, CANCELLED)
#: Legados preservados. Não participam das transições novas nem são reescritos.
ESTADOS_LEGADOS = ("fechada", "pedido", "perdida")

#: Depois destes, a revisão não é mais editável economicamente.
ESTADOS_IMUTAVEIS = (ISSUED, SENT, CANCELLED)

#: Transições permitidas. O que não está aqui é recusado com erro explícito — nunca
#: "corrigido" em silêncio.
TRANSICOES = {
    DRAFT: {PENDING_APPROVAL, ISSUED, CANCELLED},
    PENDING_APPROVAL: {DRAFT, APPROVED, CANCELLED},
    APPROVED: {DRAFT, ISSUED, PENDING_APPROVAL, CANCELLED},
    ISSUED: {SENT, CANCELLED},
    SENT: {CANCELLED},
    CANCELLED: set(),
}


class TransicaoInvalida(RuntimeError):
    """Estado de destino não alcançável a partir do atual."""


class AprovacaoVencida(RuntimeError):
    """A configuração mudou desde que a aprovação foi pedida ou concedida."""


def pode_transicionar(de: str, para: str) -> bool:
    return para in TRANSICOES.get(de, set())


def proximos_estados(atual: str) -> tuple:
    """Os estados que a tela pode oferecer a partir do atual.

    Existe para a interface não precisar decidir isso sozinha. Antes, a tela de detalhe
    listava `StatusCotacao` inteiro como botões — os três legados incluídos — e um clique
    gravava `pedido` direto, sem passar por nenhuma transição. A cotação caía num estado do
    qual `exigir_transicao` recusa sair, e não havia caminho de volta.

    Estado legado não oferece saída porque não existe transição definida a partir dele: o
    que aquele estado significava no sistema antigo não está registrado em lugar nenhum, e
    inventar a semântica agora reescreveria história.
    """
    if atual in ESTADOS_LEGADOS:
        return ()
    return tuple(e for e in ESTADOS_DO_WORKFLOW
                 if e in TRANSICOES.get(atual, set()) and e not in ESTADOS_LEGADOS)


def exigir_transicao(de: str, para: str):
    if de in ESTADOS_LEGADOS:
        raise TransicaoInvalida(
            f"A cotação está no estado legado '{de}', anterior ao workflow. Não é possível "
            "movê-la por aqui sem decisão explícita sobre o que esse estado significava.")
    if not pode_transicionar(de, para):
        raise TransicaoInvalida(f"Transição '{de}' → '{para}' não é permitida.")


# ---------------------------------------------------------------------------
# Blockers duros × exceções comerciais
# ---------------------------------------------------------------------------
#: Status de custo que impedem formar preço. Aprovação não os remove.
CUSTO_BLOQUEIA = {"A_COTAR", "REVIEW_REQUIRED"}
#: Política 21/09/2026: produto sem regra de margem não forma preço — bloqueio, não 15%.
SEM_REGRA_DE_MARGEM = "SEM_REGRA_DE_MARGEM"
#: Status de frete que impedem emitir quando o frete é CIF.
FRETE_BLOQUEIA = {"FRETE_A_COTAR", "FRETE_REVIEW_REQUIRED", "FRETE_ICMS_REVIEW_REQUIRED"}
#: Frete CIF informado manualmente e confirmado pelo usuário (21/09/2026): fonte válida para a
#: cotação, fora do motor automático — nunca entra em `FRETE_BLOQUEIA`.
FRETE_MANUAL_CONFIRMADO = "FRETE_MANUAL_CONFIRMADO"

# Motivos estruturados de exceção comercial (§20). Texto livre nunca é a fonte da semântica.
PRECO_ABAIXO = "PRECO_ABAIXO_RECOMENDADO"
MARGEM_ABAIXO = "MARGEM_ABAIXO_ALVO"          # item anterior à política: alvo é a régua
MARGEM_ABAIXO_PISO = "MARGEM_ABAIXO_PISO"     # política de 16/09/2026: piso é a régua
PRECO_ABAIXO_B2B = "PRECO_ABAIXO_B2B"         # política de 21/09/2026: o B2B é o piso
PREMISSA_VELHA = "PREMISSA_DESATUALIZADA_MANTIDA"
OUTRA_EXCECAO = "OUTRA_EXCECAO_COMERCIAL"

#: Desvio máximo de UMA quantização comercial. `dinheiro()` usa `ROUND_HALF_UP` em 2 casas,
#: então nenhuma quantia isolada erra mais que meio centavo.
MEIO_CENTAVO = CENTAVO / 2

#: Quantas quantias o resíduo do lucro absorve **sempre**: `impostos`, `comissao` e
#: `custo_total`. Frete CF e RV entram quando existem — ver `componentes_quantizados_do_item`.
COMPONENTES_SEMPRE_QUANTIZADOS = 3


def deficit_de_lucro_unitario(preco, margem_alvo, margem_real) -> Decimal:
    """Quanto lucro falta, em reais e por unidade, para a linha entregar a margem-alvo.

    Positivo quer dizer que falta; zero ou negativo, que a linha entregou o alvo ou passou
    dele. `Decimal` puro — a comparação é com dinheiro, e dinheiro aqui não é `float`.

    A identidade que sustenta a conta, com `faturamento = preço × quantidade`:

        (faturamento × alvo − lucro) ÷ quantidade  ==  preço × (alvo − margem_real)
    """
    return D0(preco) * (D0(margem_alvo) - D0(margem_real))


def componentes_quantizados_do_item(item) -> int:
    """Quantas quantias quantizadas caem no resíduo do lucro **deste** item.

    Três sempre — impostos, comissão e custo total. Frete fixo e rate variável só existem
    quando o item pertence a um grupo logístico com tarifa resolvida; contar os cinco onde só
    há três afrouxaria a tolerância sem motivo.
    """
    n = COMPONENTES_SEMPRE_QUANTIZADOS
    if D0(getattr(item, "frete_cf_unitario", None)) > ZERO:
        n += 1
    if D0(getattr(item, "frete_rv_pct", None)) > ZERO:
        n += 1
    return n


def tolerancia_de_arredondamento(quantidade, componentes_quantizados: int) -> Decimal:
    """O maior déficit de lucro por unidade que o **próprio arredondamento** pode produzir.

    Não é número escolhido: é cota superior derivada do waterfall de
    `pricing_engine.calcular_por_preco`, que é onde as quantizações acontecem.

    ## As quantias que erram

    Com `dinheiro()` a 2 casas e `ROUND_HALF_UP`, cada quantia erra no máximo meio centavo.
    Chamando `δ` a diferença entre o valor quantizado e o exato:

        preço        P  = P* + δp              quantizado por UNIDADE
        faturamento  F  = P·q + δF             δF = 0 quando q é inteiro
        impostos     I  = F·t + δI
        comissão     C  = F·c + δC
        custo total  T  = custo·q + δT         o CNET não é quantizado, então δT existe
        frete CF     K  = cf·q + δK            só quando há frete fixo
        frete RV     R  = F·r + δR             só quando há rate variável

    O lucro é **resíduo**: `L = F − I − C − T − K − R`. Substituindo:

        L = F·(1 − t − c − r) − custo·q − cf·q − Σδ        Σδ = δI + δC + δT + δK + δR

    ## O que o preço exato garante

    `calcular_por_margem` resolve o preço exato `P*` de modo que a margem seja exatamente a
    alvo. Com `F* = P*·q` e `k = 1 − t − c − r − m`, isso significa:

        (custo + cf)·q = F*·k

    ## O déficit, então, é só resíduo

        déficit_linha = F·m − L = (F* − F)·k + Σδ = −(δp·q + δF)·k + Σδ

    Dividindo por `q` e tomando os módulos máximos, com `k ≤ 1` como cota superior:

        déficit_unitário ≤ 0,005·k + 0,005·k/q + n·0,005/q
                         ≤ 0,005 · (1 + (1 + n)/q)

    É esta a fórmula. Duas leituras importantes:

    * **cai com a quantidade.** Os resíduos de `Σδ` são por LINHA, então diluem: uma linha de
      100 peças tolera ~R$ 0,0053 por unidade, não R$ 0,035. Uma tolerância constante teria de
      adotar o pior caso (q = 1) e perdoaria 100 × mais dinheiro numa linha grande;
    * **`k ≤ 1` é folga deliberada.** O `k` real fica entre 0,55 e 0,70, então o limite é
      conservador por construção — sem precisar que o workflow conheça as alíquotas.

    ## Verificação

    55.000 combinações economicamente válidas (custo, quantidade, margem-alvo, ICMS, encargo,
    frete CF e RV, com o PIS/COFINS derivado de cada ICMS): **zero violações**, folga mínima de
    R$ 0,0015. Pior déficit real observado: R$ 0,0174, em q = 1 com os cinco componentes.
    """
    q = D0(quantidade)
    if q <= ZERO:
        q = D("1")          # sem quantidade não há diluição: vale o pior caso
    n = D(str(int(componentes_quantizados)))
    return MEIO_CENTAVO * (D("1") + (D("1") + n) / q)


@dataclass
class Blocker:
    """Impedimento **duro**: falta dado econômico. Nenhuma alçada o dispensa."""
    codigo: str
    escopo: str
    detalhe: str

    def como_dict(self) -> dict:
        return {"codigo": self.codigo, "escopo": self.escopo, "detalhe": self.detalhe}


@dataclass
class Excecao:
    """Exceção **comercial**: o número existe, alguém precisa autorizar."""
    motivo: str
    escopo: str
    detalhe: str
    preco_recomendado: Optional[str] = None
    preco_negociado: Optional[str] = None
    diferenca: Optional[str] = None
    diferenca_pct: Optional[str] = None
    margem_alvo: Optional[str] = None
    margem_real: Optional[str] = None
    margem_piso: Optional[str] = None

    def como_dict(self) -> dict:
        return {"motivo": self.motivo, "escopo": self.escopo, "detalhe": self.detalhe,
                "preco_recomendado": self.preco_recomendado,
                "preco_negociado": self.preco_negociado, "diferenca": self.diferenca,
                "diferenca_pct": self.diferenca_pct, "margem_alvo": self.margem_alvo,
                "margem_real": self.margem_real, "margem_piso": self.margem_piso}


def blockers_do_item(item) -> List[Blocker]:
    """O que impede este item de virar documento final."""
    achados = []
    rotulo = item.nome_produto or f"item #{item.id}"

    if (item.status_fiscal or "") == "REVIEW_REQUIRED":
        achados.append(Blocker("FISCAL_REVIEW_REQUIRED", rotulo,
                               item.motivo_fiscal or "Cenário fiscal não resolvido."))
    if (item.status_pagamento or "") == "REVIEW_REQUIRED":
        achados.append(Blocker("PAGAMENTO_REVIEW_REQUIRED", rotulo,
                               item.motivo_pagamento or
                               "Condição de pagamento não resolvida."))
    status_custo = (getattr(item, "status_custo_item", None) or "").upper()
    if status_custo in CUSTO_BLOQUEIA:
        achados.append(Blocker(
            f"CUSTO_{status_custo}", rotulo,
            "A_COTAR não tem preço formado e REVIEW_REQUIRED tem premissa quebrada. "
            "Aprovação comercial não cria o número que falta."))
    # Política 21/09: `_preencher_item` marca o item cujo produto nenhuma regra alcança.
    # A marca é explícita (não "margem nula"), para não confundir com item legado montado
    # antes de existir regra de margem.
    if getattr(item, "margem_regra", None) == SEM_REGRA_DE_MARGEM:
        achados.append(Blocker(
            SEM_REGRA_DE_MARGEM, rotulo,
            "Nenhuma regra de margem cadastrada alcança este produto. Sem regra o sistema "
            "não forma preço — cadastre a regra do escopo no painel de administração."))
    if not item.preco_negociado or D0(item.preco_negociado) <= 0:
        achados.append(Blocker("SEM_PRECO", rotulo,
                               "O item não tem preço comercial formado."))
    return achados


def blockers_da_cotacao(cotacao, itens: Sequence, frete: Optional[dict] = None
                        ) -> List[Blocker]:
    """Todos os impedimentos duros da cotação. Lista vazia = pode emitir."""
    achados: List[Blocker] = []
    if not itens:
        achados.append(Blocker("SEM_ITENS", "cotação", "A cotação não tem item nenhum."))
    for it in itens:
        achados.extend(blockers_do_item(it))

    # Frete: só bloqueia quando o frete é da Anara. FOB não forma frete e não bloqueia.
    if (getattr(cotacao, "freight_type", "") or "").upper() == "CIF" and frete:
        status = (frete.get("status") or "").upper()
        if status in FRETE_BLOQUEIA:
            achados.append(Blocker(
                status, "frete CIF",
                frete.get("motivo") or
                "O frete é responsabilidade da Anara e não está resolvido. Aprovação "
                "comercial não transforma frete desconhecido em zero."))
        for motivo in frete.get("motivos") or []:
            achados.append(Blocker("FRETE_GRUPO", "frete CIF", motivo))
    return achados


# ---------------------------------------------------------------------------
# Exceções comerciais
# ---------------------------------------------------------------------------
def item_tem_politica(item) -> bool:
    """O item foi formado pela política de 16/09/2026 (tem piso congelado)?"""
    return (getattr(item, "politica_comercial", None) is not None
            and getattr(item, "piso_margem_pct", None) is not None
            and not item_da_politica_2026_09_21(item))


def item_da_politica_2026_09_21(item) -> bool:
    """O item foi formado pela política de 21/09/2026 (B2B como piso de autonomia)?"""
    from app.politica_comercial import ROTULO_2026_09_21
    return getattr(item, "politica_comercial", None) == ROTULO_2026_09_21


def excecoes_do_item(item) -> List[Excecao]:
    """Onde este item foge da política comercial.

    Três réguas, escolhidas pelo que o item congelou — nunca pelo que vale hoje:

    * **preço travado** (Daune) ou **item anterior à política**: preço abaixo do recomendado
      é exceção mesmo com margem boa — a autonomia é zero;
    * **item da política de 16/09/2026**: abaixo do recomendado é autonomia; o que exige
      aprovação é a **margem realizada abaixo do piso**, medida com a comissão que a
      cotação já reduziu até o mínimo (`comercial_service` recalcula o item antes);
    * **margem real abaixo da régua** (alvo no legado, piso na política) é exceção ainda
      que o preço não tenha caído — pode ter subido o custo, mudado o imposto ou entrado
      frete.
    """
    achados = []
    rotulo = item.nome_produto or f"item #{item.id}"
    # O recomendado é o do CENÁRIO desta cotação, não o preço-base do catálogo: o base foi
    # formado com outro destino fiscal e outra condição de pagamento. Sem essa distinção,
    # uma venda interestadual normal pareceria desconto.
    recomendado = dinheiro(item.preco_recomendado) if item.preco_recomendado else None
    negociado = dinheiro(item.preco_negociado) if item.preco_negociado else None

    if item_da_politica_2026_09_21(item):
        # Política de 21/09/2026: o B2B recomendado É o piso de autonomia. Abaixo dele, a
        # proposta exige aprovação — qualquer que seja a margem que ainda sobre. Acima ou
        # igual, está dentro da autonomia; a margem-alvo já está garantida pela construção
        # do B2B (menor centavo com margem ≥ alvo), e a comissão do item vem da faixa do
        # desconto sobre a tabela, não de piso nenhum.
        if recomendado and negociado and negociado < recomendado:
            diferenca = negociado - recomendado
            tabela = dinheiro(getattr(item, "preco_tabela", None) or 0)
            achados.append(Excecao(
                motivo=PRECO_ABAIXO_B2B, escopo=rotulo,
                detalhe=(f"Proposta de R$ {negociado} abaixo do preço B2B recomendado de "
                         f"R$ {recomendado}"
                         + (f" (tabela R$ {tabela})" if tabela and tabela > ZERO else "")
                         + ". O B2B é o piso de autonomia da vendedora: abaixo dele a "
                         "proposta precisa de aprovação."),
                preco_recomendado=str(recomendado), preco_negociado=str(negociado),
                diferenca=str(diferenca),
                diferenca_pct=str(divide(diferenca, recomendado))))
        return achados

    politica = item_tem_politica(item)
    travado = bool(getattr(item, "preco_travado", False))

    if recomendado and negociado and negociado < recomendado and (travado or not politica):
        diferenca = negociado - recomendado
        achados.append(Excecao(
            motivo=PRECO_ABAIXO, escopo=rotulo,
            detalhe=(f"Negociado R$ {negociado} contra R$ {recomendado} de recomendado. "
                     + ("O preço deste produto é travado pela política comercial: não há "
                        "autonomia de desconto."
                        if travado else
                        "Preço abaixo do recomendado exige aprovação mesmo quando a margem "
                        "continua saudável.")),
            preco_recomendado=str(recomendado), preco_negociado=str(negociado),
            diferenca=str(diferenca),
            diferenca_pct=str(divide(diferenca, recomendado))))

    alvo = D(item.margem_padrao_pct)
    piso = D(getattr(item, "piso_margem_pct", None)) if politica else None
    regua = piso if piso is not None else alvo
    real = D(item.margem_liquida)
    if regua is not None and real is not None and item.custo_unitario:
        preco = dinheiro(item.preco_negociado) or ZERO
        deficit = deficit_de_lucro_unitario(preco, regua, real)
        tolerancia = tolerancia_de_arredondamento(
            getattr(item, "quantidade", 1), componentes_quantizados_do_item(item))
        # Sem preço não há receita contra a qual medir déficit — e um item nesse estado está
        # bloqueado por outro motivo, não aprovado por omissão. Mantém-se a comparação estrita.
        material = deficit > tolerancia if preco > ZERO else real < regua
        if material:
            if piso is not None:
                achados.append(Excecao(
                    motivo=MARGEM_ABAIXO_PISO, escopo=rotulo,
                    detalhe=(f"Margem realizada de {real * 100:.2f}% abaixo do piso de "
                             f"autonomia de {piso * 100:.2f}% (alvo {alvo * 100:.2f}%), já "
                             "com a comissão da cotação reduzida ao mínimo"
                             + (f" — R$ {dinheiro(deficit)} de lucro a menos por unidade."
                                if preco > ZERO else ".")),
                    margem_alvo=str(alvo) if alvo is not None else None,
                    margem_real=str(real), margem_piso=str(piso)))
            else:
                achados.append(Excecao(
                    motivo=MARGEM_ABAIXO, escopo=rotulo,
                    detalhe=(f"Margem real de {real * 100:.2f}% contra alvo de "
                             f"{alvo * 100:.2f}%"
                             + (f" — R$ {dinheiro(deficit)} de lucro a menos por unidade."
                                if preco > ZERO else ".")),
                    margem_alvo=str(alvo), margem_real=str(real)))
    return achados


def excecoes_da_cotacao(cotacao, itens: Sequence,
                        premissas_desatualizadas: bool = False) -> List[Excecao]:
    """Exceções por ITEM, mais as da cotação inteira.

    A detecção é **por item** de propósito. Um desconto no item A compensado por um aumento
    no item B deixaria o total acima do recomendado e esconderia o desconto — que continua
    sendo uma decisão que alguém precisa tomar conscientemente.
    """
    achados: List[Excecao] = []
    for it in itens:
        achados.extend(excecoes_do_item(it))
    if premissas_desatualizadas:
        achados.append(Excecao(
            motivo=PREMISSA_VELHA, escopo="cotação",
            detalhe=("A cotação usa premissas anteriores às vigentes e o usuário optou por "
                     "mantê-las. Emitir assim é uma decisão comercial, não um acidente.")))
    return achados


def resumo_comercial(itens: Sequence) -> dict:
    """Totais do documento, para a tela do aprovador. Nada aqui recalcula economia.

    A comissão estimada (Fase 3A) é a **soma do que cada item já gravou** em
    `comissao_valor`; a taxa efetiva divide essa soma pela receita dos itens comissionáveis
    — os que têm custo e comissão gravada. Item sem custo não entra em nenhum dos dois.
    Nunca média de percentuais.
    """
    recomendado = ZERO
    negociado = ZERO
    tabela = ZERO
    comissao = ZERO
    receita_comissionavel = ZERO
    base_comissionavel = ZERO
    for it in itens:
        qtd = D0(it.quantidade)
        if it.preco_recomendado:
            recomendado += dinheiro(D0(it.preco_recomendado) * qtd)
        if getattr(it, "preco_tabela", None):
            tabela += dinheiro(D0(it.preco_tabela) * qtd)
        negociado += D0(it.faturamento)
        if getattr(it, "comissao_valor", None) is not None and D0(it.custo_unitario) > ZERO:
            comissao += D0(it.comissao_valor)
            receita_comissionavel += D0(it.faturamento)
            # Σ base: a base líquida gravada (política 21/09) ou a receita (anteriores).
            base = getattr(it, "base_comissionavel", None)
            base_comissionavel += D0(base) if base is not None else D0(it.faturamento)
    diferenca = negociado - recomendado
    return {"total_recomendado": para_float(recomendado),
            "total_negociado": para_float(negociado),
            "total_tabela": para_float(tabela),
            "diferenca": para_float(diferenca),
            "diferenca_pct": para_float(divide(diferenca, recomendado)),
            "comissao_estimada_valor": para_float(comissao),
            # taxa efetiva = Σ comissão ÷ Σ base comissionável — nunca reaplicada a item algum
            "comissao_estimada_pct_efetiva": para_float(divide(comissao, base_comissionavel)),
            "receita_comissionavel": para_float(receita_comissionavel),
            "base_comissionavel": para_float(base_comissionavel)}


# ---------------------------------------------------------------------------
# Fingerprint — a identidade da configuração aprovada
# ---------------------------------------------------------------------------
#: Os campos do ITEM que entram no fingerprint. Mudar qualquer um muda a decisão que o
#: aprovador tomaria, então mudar qualquer um invalida a aprovação.
CAMPOS_MATERIAIS_ITEM = (
    "produto_id", "quantidade", "preco_base", "preco_recomendado", "preco_negociado",
    "faturamento",
    "custo_unitario", "margem_padrao_pct", "margem_liquida", "comissao_pct",
    "icms_pct", "difal_pct", "encargo_pct", "custo_referencia_id",
    "condicao_pagamento_id", "aliquota_interestadual_id", "premissas_pinadas",
    # Fase 3A: a política que formou o item é material — piso, comissão de formação, preço
    # travado e a versão da política. Mudou a política, mudou a decisão que se tomaria.
    "piso_margem_pct", "comissao_formacao_pct", "preco_travado", "politica_comercial",
)
#: Política 21/09/2026: tabela, desconto, faixa e base da comissão são materiais — a aprovação
#: foi dada sobre ESTE desconto e ESTA comissão. Entram no fingerprint **só quando
#: preenchidos**: item anterior à política tem tudo nulo, e incluir nulos mudaria o
#: fingerprint de toda cotação já emitida/aprovada sem que nada material tivesse mudado.
CAMPOS_MATERIAIS_ITEM_2026_09_21 = (
    "preco_tabela", "desconto_vs_tabela_pct", "comissao_faixa_pct", "icms_base_comissao_pct",
    "base_comissionavel",
    # 22/09/2026: a base comercial que formou o B2B e a proteção usada são materiais — mudou a
    # referência de preço, mudou a decisão. Só entram quando preenchidas (item antigo intacto).
    "base_comercial_precificacao", "protecao_comercial_pct",
)
#: Os campos da COTAÇÃO. `observacoes` e notas internas ficam de fora de propósito: são
#: descritivas, não mudam economia nem contexto fiscal, e invalidar aprovação por causa
#: delas seria burocracia sem conteúdo (§24).
CAMPOS_MATERIAIS_COTACAO = (
    "cliente_id", "estado_destino", "uf_origem_fiscal", "contribuinte_icms", "finalidade",
    "condicao_pagamento", "freight_type", "freight_valor", "revisao",
)


def _canonico(valor):
    """Representação estável de um valor para o hash.

    Números viram `Decimal` normalizado — `100`, `100.0` e `"100.00"` produzem a mesma
    string, então reformatar não invalida aprovação. `None` é distinto de `0`.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        d = D(valor)
        return str(d.normalize()) if d is not None else None
    return str(valor)


def fingerprint(cotacao, itens: Sequence) -> str:
    """Hash determinístico do estado **material** da cotação.

    Não entra timestamp, nem contador de visualização, nem nada que mude sozinho: o hash
    precisa ser o mesmo quando nada de relevante mudou, senão a aprovação morreria a cada
    página aberta.
    """
    def item_material(it):
        base = {c: _canonico(getattr(it, c, None)) for c in CAMPOS_MATERIAIS_ITEM}
        base["id"] = it.id
        for c in CAMPOS_MATERIAIS_ITEM_2026_09_21:
            v = getattr(it, c, None)
            if v is not None:
                base[c] = _canonico(v)
        return base

    corpo = {
        "cotacao": {c: _canonico(getattr(cotacao, c, None))
                    for c in CAMPOS_MATERIAIS_COTACAO},
        "itens": sorted([item_material(it) for it in itens], key=lambda x: (x.get("id") or 0)),
    }
    # Frete manual confirmado é material (a aprovação inclui o total com ele) — e só entra
    # quando verdadeiro, pelo mesmo motivo acima.
    if getattr(cotacao, "freight_manual_confirmado", False):
        corpo["cotacao"]["freight_manual_confirmado"] = True
    # Sinal (21/09/2026) é material — muda o encargo efetivo e, com ele, preço, comissão e
    # totais. Entra só quando há sinal: cotação anterior (0) mantém o hash que aprovou.
    sinal = getattr(cotacao, "percentual_sinal", 0) or 0
    if D0(sinal) > 0:
        corpo["cotacao"]["percentual_sinal"] = _canonico(sinal)
    bruto = json.dumps(corpo, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Prontidão
# ---------------------------------------------------------------------------
@dataclass
class Prontidao:
    """`pode_emitir` é **derivado**, nunca um campo que o navegador manda."""
    pode_emitir: bool
    blockers: List[Blocker] = field(default_factory=list)
    excecoes: List[Excecao] = field(default_factory=list)
    precisa_aprovacao: bool = False
    aprovacao_valida: bool = False
    motivos: List[str] = field(default_factory=list)
    fingerprint: Optional[str] = None

    def como_dict(self) -> dict:
        return {"pode_emitir": self.pode_emitir,
                "blockers": [b.como_dict() for b in self.blockers],
                "excecoes": [e.como_dict() for e in self.excecoes],
                "precisa_aprovacao": self.precisa_aprovacao,
                "aprovacao_valida": self.aprovacao_valida,
                "motivos": list(self.motivos), "fingerprint": self.fingerprint}


def avaliar(cotacao, itens: Sequence, *, frete: Optional[dict] = None,
            premissas_desatualizadas: bool = False,
            aprovacao_vigente=None) -> Prontidao:
    """A cotação pode virar documento final agora?

    `aprovacao_vigente` é a decisão APROVADA cujo fingerprint bate com o estado atual —
    quem resolve isso é `workflow_service`, porque depende do banco.
    """
    fp = fingerprint(cotacao, itens)
    blockers = blockers_da_cotacao(cotacao, itens, frete)
    excecoes = excecoes_da_cotacao(cotacao, itens, premissas_desatualizadas)
    precisa = bool(excecoes)
    valida = bool(aprovacao_vigente is not None
                  and getattr(aprovacao_vigente, "fingerprint", None) == fp
                  and getattr(aprovacao_vigente, "status", None) == "APROVADA")

    motivos = []
    if blockers:
        motivos.append(f"{len(blockers)} impedimento(s) econômico(s) não resolvido(s). "
                       "Aprovação comercial não os dispensa.")
    if precisa and not valida:
        motivos.append(f"{len(excecoes)} exceção(ões) comercial(is) aguardando aprovação.")

    estado = getattr(cotacao, "status", DRAFT)
    if estado in ESTADOS_IMUTAVEIS:
        motivos.append(f"A cotação está em '{estado}' e não pode ser emitida de novo.")

    return Prontidao(
        pode_emitir=(not blockers and (not precisa or valida)
                     and estado not in ESTADOS_IMUTAVEIS),
        blockers=blockers, excecoes=excecoes, precisa_aprovacao=precisa,
        aprovacao_valida=valida, motivos=motivos, fingerprint=fp)


# ---------------------------------------------------------------------------
# Compromisso firme — a antessala do pedido (Sessão 7)
# ---------------------------------------------------------------------------
@dataclass
class Compromisso:
    pode: bool
    impedimentos: List[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {"pode": self.pode, "impedimentos": list(self.impedimentos)}


def validar_compromisso_firme(cotacao, itens: Sequence, *, frete: Optional[dict] = None,
                              aprovacao_vigente=None,
                              custos_reconfirmados: Optional[set] = None) -> Compromisso:
    """Esta cotação poderia virar pedido/PO/WON?

    **Não cria pedido nem WON** — isso é da Sessão 7. Existe para que a regra fique escrita
    em um lugar só desde já, e para que a diferença entre "posso propor" e "posso me
    comprometer" não seja descoberta na hora errada.

    O caso central: uma proposta com custo **ESTIMADO** pode sair, com PDF e tudo. O que ela
    não pode é virar compromisso firme antes de alguém confirmar o custo — porque o número
    veio de proxy, e assumir obrigação sobre proxy é o caminho para vender no prejuízo.

    `custos_reconfirmados` traz os itens cuja referência de custo **já foi reconfirmada
    depois da emissão**. A distinção é sutil e importa: o preço do documento fica congelado
    para sempre — é o que o cliente tem em mãos —, mas "posso me comprometer **hoje**?" é
    pergunta sobre o presente. Sem essa porta, um item emitido em REVALIDAR ficaria
    bloqueado eternamente, porque o item é imutável e seu status nunca mudaria. Quem resolve
    isso é `workflow_service`, que tem banco; aqui só se consome a resposta.
    """
    custos_reconfirmados = custos_reconfirmados or set()
    impedimentos = []
    prontidao = avaliar(cotacao, itens, frete=frete, aprovacao_vigente=aprovacao_vigente)

    for b in prontidao.blockers:
        impedimentos.append(f"{b.escopo}: {b.detalhe}")
    if prontidao.precisa_aprovacao and not prontidao.aprovacao_valida:
        impedimentos.append("Há exceção comercial sem aprovação vigente.")

    if getattr(cotacao, "status", None) not in (ISSUED, SENT):
        impedimentos.append(
            "A cotação ainda não foi emitida. Compromisso firme exige documento emitido.")

    for it in itens:
        rotulo = it.nome_produto or f"item #{it.id}"
        if it.id in custos_reconfirmados:
            # A referência foi reconfirmada depois da emissão. O preço não muda; o
            # impedimento sai, porque ele era sobre a confiança no número, não sobre o
            # número.
            continue
        if getattr(it, "confirmation_pending", False):
            impedimentos.append(
                f"{rotulo}: custo ESTIMADO ainda não confirmado. A proposta pode sair; o "
                "compromisso firme, não — o número veio de proxy.")
        if (getattr(it, "status_custo_item", "") or "").upper() == "REVALIDAR":
            impedimentos.append(
                f"{rotulo}: custo em REVALIDAR. A referência é direta, mas envelheceu — "
                "reconfirmar antes de assumir compromisso.")

    return Compromisso(pode=not impedimentos, impedimentos=impedimentos)

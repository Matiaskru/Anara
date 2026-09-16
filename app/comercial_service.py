"""Negociação comercial — o preview canônico da cotação, para a vendedora e para o admin.

Um serviço só responde às perguntas que a tela reativa vai fazer a cada tecla:

    com estes preços negociados, quanto fica cada linha, o subtotal e o total?
    qual é a comissão estimada da cotação — uma só, em R$ e taxa efetiva?
    a vendedora está dentro da autonomia, ou isto exige aprovação?

Nada aqui inventa economia: o custo, o fiscal, o encargo e o frete de cada item vêm do
`pricing_service`, a decomposição do preço vem do `pricing_engine`, a regra de comissão e de
piso vem de `politica_comercial` (puro), e a exceção que exige aprovação é decidida pela
**mesma função** que o workflow usa (`workflow.excecoes_do_item`) — o preview não pode
prometer o que a emissão recusa.

## O que este módulo grava

`aplicar_negociacao` grava os preços propostos e **recalcula a cotação inteira**, porque a
comissão é da cotação: baixar o preço do item A muda a comissão aplicada ao item B, e por
isso lucro e margem de B mudam também. `recalcular_comissao` faz o mesmo sem trocar preço —
é o que o router chama depois de adicionar, editar ou remover um item.

Item **anterior à política** (sem `politica_comercial`) não é tocado: ele foi formado com
comissão por faixa de markup e continua assim até alguém o reprecificar explicitamente
(`/cotacoes/{id}/premissas/atualizar`).
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlmodel import Session

from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais, sem_confidenciais
from app.dinheiro import D, D0, ZERO, dinheiro, divide, para_float
from app.models import Cotacao, CotacaoItem, Produto, TipoFrete, Usuario
from app.politica_comercial import (  # noqa: F401  (MOTIVO_* reexportados para o router)
    DENTRO_DA_AUTONOMIA, MOTIVO_SEM_CUSTO, MOTIVO_TRAVADO, REQUER_APROVACAO, ComissaoDaCotacao,
    LinhaNegociada, PoliticaInconsistente, avaliar_comissao, classificar, taxa_efetiva,
)
from app.pricing_engine import (
    ResultadoPrecificacao, calcular_por_preco, com_comissao_fixa, comissao_maxima_para_margem,
)


class PrecoTravado(HTTPException):
    """Tentativa de alterar o unitário de um item com preço travado. Recusa explícita, 409."""

    def __init__(self, item: CotacaoItem, recomendado, proposto):
        super().__init__(
            status_code=409,
            detail=(f"O preço de '{item.nome_produto}' é travado pela política comercial de "
                    f"16/09/2026: só pode ser R$ {dinheiro(recomendado)} (recomendado). "
                    f"R$ {dinheiro(proposto)} foi recusado. Mudar a política Daune exige "
                    "nova política versionada, não override na cotação."))


# ---------------------------------------------------------------------------
# Política do item — o que o item congelou
# ---------------------------------------------------------------------------
def comissao_de_formacao_do_item(item: CotacaoItem):
    """O que `regras_da_cotacao` deve receber para recalcular ESTE item.

    Item da política: a comissão de formação congelada nele. Item anterior: `None`, que
    significa "tabela de faixas" — nunca a política de hoje, que ele não pinou.
    """
    if getattr(item, "politica_comercial", None) is not None:
        return item.comissao_formacao_pct
    return None


def regras_do_item(session: Session, cotacao: Cotacao, item: CotacaoItem,
                   produto: Optional[Produto]):
    return ps.regras_da_cotacao(session, cotacao, produto,
                                comissao_formacao_pct=comissao_de_formacao_do_item(item))


# ---------------------------------------------------------------------------
# A avaliação
# ---------------------------------------------------------------------------
@dataclass
class ItemAvaliado:
    item: CotacaoItem
    linha: LinhaNegociada
    regras: object                                   # TaxRuleSet ou None (cenário irresolvido)
    resultado: Optional[ResultadoPrecificacao]       # no preço negociado, comissão aplicada
    no_recomendado: Optional[ResultadoPrecificacao]  # no preço recomendado, comissão de formação
    excecoes: List[wf.Excecao] = field(default_factory=list)
    viola_piso: bool = False
    deficit_unitario: Optional[Decimal] = None
    tolerancia: Optional[Decimal] = None


@dataclass
class Avaliacao:
    cotacao: Cotacao
    itens: List[ItemAvaliado]
    comissao: Optional[ComissaoDaCotacao]
    subtotal_recomendado: Decimal
    subtotal_negociado: Decimal
    desconto_pct: Optional[Decimal]
    frete_tipo: Optional[str]
    frete_valor: Optional[Decimal]
    frete_no_total: bool
    total_proposta: Decimal
    comissao_travada_valor: Decimal
    comissao_variavel_valor: Decimal
    comissao_total_valor: Decimal
    receita_comissionavel: Decimal
    taxa_efetiva: Optional[Decimal]
    requer_aprovacao: bool
    # --- diagnóstico (admin) ---
    absorvido_por_comissao: Decimal = ZERO
    absorvido_por_margem: Decimal = ZERO
    absorvido_por_impostos_e_frete: Decimal = ZERO
    lucro_total: Decimal = ZERO
    custo_total: Decimal = ZERO

    @property
    def autonomia_status(self) -> str:
        return REQUER_APROVACAO if self.requer_aprovacao else DENTRO_DA_AUTONOMIA


def _sombra(item: CotacaoItem, linha: LinhaNegociada, res: Optional[ResultadoPrecificacao]):
    """O item como ficaria — o objeto que `workflow.excecoes_do_item` lê.

    Existe para que o preview e a emissão avaliem a mesma coisa pela mesma função, sem
    gravar o item antes de a vendedora decidir.
    """
    return SimpleNamespace(
        id=item.id, nome_produto=item.nome_produto, quantidade=linha.quantidade,
        preco_recomendado=para_float(linha.preco_recomendado),
        preco_negociado=para_float(linha.preco_negociado),
        custo_unitario=item.custo_unitario,
        margem_padrao_pct=item.margem_padrao_pct,
        margem_liquida=(para_float(res.margem_liquida) if res is not None
                        else item.margem_liquida),
        piso_margem_pct=item.piso_margem_pct, politica_comercial=item.politica_comercial,
        preco_travado=item.preco_travado,
        frete_cf_unitario=item.frete_cf_unitario, frete_rv_pct=item.frete_rv_pct)


def avaliar_negociacao(session: Session, cotacao: Cotacao,
                       itens: Optional[Sequence[CotacaoItem]] = None,
                       propostas: Optional[Dict[int, object]] = None) -> Avaliacao:
    """A cotação com os preços propostos (ou os gravados), avaliada de ponta a ponta.

    `propostas` é `{item_id: preço unitário}`. Item ausente fica com o preço gravado. Preço
    proposto para item travado que não seja o recomendado é recusado com `PrecoTravado`.
    **Não grava nada.**
    """
    itens = list(itens if itens is not None else ws.itens_de(session, cotacao.id))
    propostas = {int(k): v for k, v in (propostas or {}).items()}
    vigente = ps.politica_comercial_vigente(session)
    if vigente is None and any(getattr(it, "politica_comercial", None) for it in itens):
        raise PoliticaInconsistente(
            "Há item formado pela política comercial de 16/09/2026, mas as premissas "
            "comissao_base_pct / comissao_min_pct não estão cadastradas. Não se assume 10%/5%.")

    avaliados: List[ItemAvaliado] = []
    produtos: Dict[int, Optional[Produto]] = {}
    for it in itens:
        produto = None
        if it.produto_id:
            produto = produtos.get(it.produto_id)
            if produto is None and it.produto_id not in produtos:
                produto = session.get(Produto, it.produto_id)
                produtos[it.produto_id] = produto
        recomendado = D(it.preco_recomendado) if it.preco_recomendado else None
        proposto = propostas.get(it.id)
        negociado = dinheiro(proposto) if proposto is not None else (dinheiro(it.preco_negociado)
                                                                     or ZERO)
        if it.preco_travado and proposto is not None and recomendado is not None \
                and negociado != dinheiro(recomendado):
            raise PrecoTravado(it, recomendado, negociado)

        linha = classificar(LinhaNegociada(
            item_id=it.id, produto_id=it.produto_id, nome=it.nome_produto,
            quantidade=D0(it.quantidade), preco_recomendado=recomendado,
            preco_negociado=negociado, custo_unitario=D0(it.custo_unitario),
            politica=getattr(it, "politica_comercial", None),
            preco_travado=bool(it.preco_travado), piso_pct=D(it.piso_margem_pct),
            margem_alvo_pct=D(it.margem_padrao_pct),
            comissao_formacao_pct=D(it.comissao_formacao_pct)))
        regras, _ctx = regras_do_item(session, cotacao, it, produto)
        if linha.elegivel_variavel and regras is not None and linha.piso_pct is not None:
            linha.comissao_maxima_piso_pct = comissao_maxima_para_margem(
                linha.custo_unitario, linha.quantidade, linha.preco_negociado,
                linha.piso_pct, regras)
        avaliados.append(ItemAvaliado(item=it, linha=linha, regras=regras,
                                      resultado=None, no_recomendado=None))

    comissao = None
    if vigente is not None:
        comissao = avaliar_comissao([a.linha for a in avaliados],
                                    vigente.comissao_base_pct, vigente.comissao_min_pct)

    # Cada item no preço negociado, com a comissão que a cotação decidiu — e, para o
    # diagnóstico, no preço recomendado com a comissão de formação.
    for a in avaliados:
        linha, it = a.linha, a.item
        if a.regras is None or linha.custo_unitario <= ZERO or linha.preco_negociado <= ZERO:
            a.resultado = (calcular_por_preco(linha.custo_unitario, linha.quantidade,
                                              linha.preco_negociado, a.regras)
                           if a.regras is not None and linha.preco_negociado > ZERO else None)
            continue
        if linha.comissao_aplicada_pct is not None:
            regras_aplicadas = com_comissao_fixa(a.regras, linha.comissao_aplicada_pct)
        else:
            regras_aplicadas = a.regras                 # item anterior: faixa por markup
        a.resultado = calcular_por_preco(linha.custo_unitario, linha.quantidade,
                                         linha.preco_negociado, regras_aplicadas, it.preco_base)
        if linha.preco_recomendado:
            a.no_recomendado = calcular_por_preco(linha.custo_unitario, linha.quantidade,
                                                  linha.preco_recomendado, a.regras,
                                                  it.preco_base)
        a.excecoes = wf.excecoes_do_item(_sombra(it, linha, a.resultado))
        if linha.piso_pct is not None and linha.politica is not None and not linha.preco_travado:
            a.deficit_unitario = wf.deficit_de_lucro_unitario(
                linha.preco_negociado, linha.piso_pct, a.resultado.margem_liquida)
            a.tolerancia = wf.tolerancia_de_arredondamento(
                linha.quantidade, wf.componentes_quantizados_do_item(it))
            a.viola_piso = a.deficit_unitario > a.tolerancia

    return _consolidar(cotacao, avaliados, comissao)


def _consolidar(cotacao: Cotacao, avaliados: List[ItemAvaliado],
                comissao: Optional[ComissaoDaCotacao]) -> Avaliacao:
    sub_rec = sum((a.linha.faturamento_recomendado for a in avaliados
                   if a.linha.preco_recomendado), ZERO)
    neg_com_rec = sum((a.linha.faturamento for a in avaliados if a.linha.preco_recomendado), ZERO)
    sub_neg = sum((a.linha.faturamento for a in avaliados), ZERO)
    desconto = divide(sub_rec - neg_com_rec, sub_rec) if sub_rec > ZERO else None
    if desconto is not None and desconto < ZERO:
        desconto = ZERO                                  # acima da tabela não é desconto

    # Frete: só entra no total quando é da Anara e tem valor informado. FOB é do cliente;
    # CIF sem valor está a cotar — o total não finge que ele é zero.
    tipo = (cotacao.freight_type or "").upper() or None
    valor_frete = D(cotacao.freight_valor)
    frete_no_total = (tipo == TipoFrete.cif.value and valor_frete is not None
                      and valor_frete > ZERO)
    total = sub_neg + (dinheiro(valor_frete) if frete_no_total else ZERO)

    com_travada = ZERO
    com_variavel = ZERO
    receita_comissionavel = ZERO
    lucro_total = ZERO
    custo_total = ZERO
    abs_comissao = ZERO
    abs_margem = ZERO
    abs_impostos = ZERO
    requer = False
    for a in avaliados:
        res = a.resultado
        if res is None:
            continue
        lucro_total += res.lucro
        custo_total += res.custo_total
        if a.linha.comissao_aplicada_pct is not None and a.linha.custo_unitario > ZERO:
            receita_comissionavel += res.faturamento
            if a.linha.preco_travado:
                com_travada += res.comissao
            else:
                com_variavel += res.comissao
        if a.no_recomendado is not None and a.linha.elegivel_variavel:
            abs_comissao += a.no_recomendado.comissao - res.comissao
            abs_margem += a.no_recomendado.lucro - res.lucro
            abs_impostos += ((a.no_recomendado.impostos + a.no_recomendado.frete_rv)
                             - (res.impostos + res.frete_rv))
        if a.excecoes:
            requer = True
    com_total = com_travada + com_variavel
    return Avaliacao(
        cotacao=cotacao, itens=avaliados, comissao=comissao,
        subtotal_recomendado=sub_rec, subtotal_negociado=sub_neg, desconto_pct=desconto,
        frete_tipo=tipo, frete_valor=valor_frete, frete_no_total=frete_no_total,
        total_proposta=total, comissao_travada_valor=com_travada,
        comissao_variavel_valor=com_variavel, comissao_total_valor=com_total,
        receita_comissionavel=receita_comissionavel,
        taxa_efetiva=taxa_efetiva(com_total, receita_comissionavel),
        requer_aprovacao=requer, absorvido_por_comissao=abs_comissao,
        absorvido_por_margem=abs_margem, absorvido_por_impostos_e_frete=abs_impostos,
        lucro_total=lucro_total, custo_total=custo_total)


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------
def _gravar_resultado(it: CotacaoItem, a: ItemAvaliado):
    """Fronteira de saída: `Decimal` → `float`, quantias já quantizadas."""
    res = a.resultado
    if res is None:
        return
    it.preco_negociado = para_float(res.preco_negociado)
    it.faturamento = para_float(res.faturamento)
    it.custo_total = para_float(res.custo_total)
    it.impostos = para_float(res.impostos)
    it.comissao_valor = para_float(res.comissao)
    it.lucro = para_float(res.lucro)
    it.margem_liquida = para_float(res.margem_liquida)
    it.markup_implicito = para_float(res.markup_implicito)
    it.diferenca_pct_vs_base = para_float(res.diferenca_pct_vs_base)
    if a.linha.comissao_aplicada_pct is not None:
        it.comissao_pct = para_float(a.linha.comissao_aplicada_pct)


def recalcular_comissao(session: Session, cotacao: Cotacao,
                        itens: Optional[Sequence[CotacaoItem]] = None) -> Avaliacao:
    """Reaplica a comissão da cotação a cada item da política, sem trocar preço.

    Chamada depois de qualquer mutação de item: a comissão variável é função de TODOS os
    itens, então adicionar, editar ou remover um muda a economia dos outros. Item anterior
    à política não é tocado.
    """
    itens = list(itens if itens is not None else ws.itens_de(session, cotacao.id))
    av = avaliar_negociacao(session, cotacao, itens)
    for a in av.itens:
        if a.linha.politica is None or a.resultado is None:
            continue
        _gravar_resultado(a.item, a)
        session.add(a.item)
    session.flush()
    return av


def aplicar_negociacao(session: Session, cotacao: Cotacao, propostas: Dict[int, object], *,
                       ator: Optional[Usuario] = None) -> Avaliacao:
    """Grava os preços propostos e recalcula a cotação inteira. Aprovação anterior cai.

    Preço travado é recusado antes de qualquer gravação. Preço unitário é quantia comercial:
    entra por `dinheiro()`, e o mesmo centavo vai para subtotal, comissão, margem, lucro,
    aprovação e snapshot — não existe preço "visual" diferente do econômico.
    """
    ws.exigir_editavel(cotacao, "negociar preço")
    itens = ws.itens_de(session, cotacao.id)
    av = avaliar_negociacao(session, cotacao, itens, propostas)     # valida o travado
    por_id = {it.id: it for it in itens}
    for a in av.itens:
        it = por_id[a.item.id]
        if a.item.id in {int(k) for k in propostas}:
            it.modo_edicao = "preco"
            it.valor_editado = para_float(a.linha.preco_negociado)
        if a.resultado is not None and (a.linha.politica is not None
                                        or a.item.id in {int(k) for k in propostas}):
            _gravar_resultado(it, a)
            produto = session.get(Produto, it.produto_id) if it.produto_id else None
            if produto is not None and a.item.id in {int(k) for k in propostas}:
                it.memoria_json = ps.memoria_json(ps.memoria_do_preco(
                    session, produto, cotacao, preco_negociado=it.preco_negociado,
                    quantidade=it.quantidade,
                    comissao_formacao_pct=comissao_de_formacao_do_item(it)))
        session.add(it)
    session.flush()
    ws.invalidar_aprovacoes_obsoletas(session, cotacao, ator=ator, motivo="preço negociado")
    return av


# ---------------------------------------------------------------------------
# Payloads — lista de permissão, nunca "tira depois"
# ---------------------------------------------------------------------------
def _item_vendedora(a: ItemAvaliado) -> dict:
    l = a.linha
    return {
        "item_id": a.item.id, "produto_id": l.produto_id, "nome_produto": l.nome,
        "quantidade": para_float(l.quantidade),
        "preco_recomendado": para_float(dinheiro(l.preco_recomendado))
        if l.preco_recomendado is not None else None,
        "preco_negociado": para_float(l.preco_negociado),
        "total_linha": para_float(l.faturamento),
        # desconto da linha em relação ao recomendado — só para a tela mostrar; acima do
        # recomendado não é desconto (fica 0)
        "desconto_linha_pct": (para_float(max(ZERO, D("1") - divide(l.preco_negociado,
                                                                        l.preco_recomendado)))
                               if l.preco_recomendado else None),
        "editavel": l.editavel, "motivo_nao_editavel": l.motivo_nao_editavel,
        "preco_travado": l.preco_travado,
    }


def payload_vendedora(av: Avaliacao) -> dict:
    """O que a vendedora vê — e **só** isto. Construído campo a campo.

    Comissão estimada e taxa efetiva passaram a ser visíveis à vendedora em 16/09/2026, por
    decisão deliberada. Custo, margem, piso, lucro, comissão interna por item e a mecânica
    de proteção continuam invisíveis — não estão aqui, e a rede de segurança confere.
    """
    corpo = {
        "itens": [_item_vendedora(a) for a in av.itens],
        "subtotal_recomendado": para_float(av.subtotal_recomendado),
        "subtotal_negociado": para_float(av.subtotal_negociado),
        "desconto_pct": para_float(av.desconto_pct),
        "frete": {"tipo": av.frete_tipo, "valor": para_float(av.frete_valor),
                  "incluido_no_total": av.frete_no_total},
        "total_proposta": para_float(av.total_proposta),
        "comissao_estimada_pct_efetiva": para_float(av.taxa_efetiva),
        "comissao_estimada_valor": para_float(av.comissao_total_valor),
        "autonomia_status": av.autonomia_status,
        "requer_aprovacao": av.requer_aprovacao,
    }
    corpo = sem_confidenciais(corpo)
    vazamentos = encontrar_confidenciais(corpo)
    assert not vazamentos, f"payload comercial com campo confidencial: {vazamentos}"
    return corpo


def payload_admin(av: Avaliacao) -> dict:
    """Tudo que a vendedora vê, mais a economia — para OWNER/ADMIN e para a UI futura."""
    corpo = payload_vendedora(av)
    com = av.comissao
    itens_econ = []
    for a in av.itens:
        l, res = a.linha, a.resultado
        itens_econ.append({
            "item_id": a.item.id,
            "custo_unitario": para_float(l.custo_unitario),
            "custo_total": para_float(res.custo_total) if res else None,
            "margem_alvo_pct": para_float(l.margem_alvo_pct),
            "piso_margem_pct": para_float(l.piso_pct),
            "margem_realizada_pct": para_float(res.margem_liquida) if res else None,
            "lucro": para_float(res.lucro) if res else None,
            "impostos": para_float(res.impostos) if res else None,
            "comissao_formacao_pct": para_float(l.comissao_formacao_pct),
            "comissao_aplicada_pct": para_float(l.comissao_aplicada_pct),
            "comissao_valor": para_float(res.comissao) if res else None,
            "comissao_max_piso_pct": para_float(l.comissao_maxima_piso_pct),
            "elegivel_variavel": l.elegivel_variavel,
            "preco_travado": l.preco_travado,
            "politica_comercial": l.politica,
            "viola_piso": a.viola_piso,
            "deficit_unitario": para_float(a.deficit_unitario),
            "tolerancia_unitaria": para_float(a.tolerancia),
            "excecoes": [e.como_dict() for e in a.excecoes],
        })
    corpo["economia"] = {
        "comissao_base_pct": para_float(com.base_pct) if com else None,
        "comissao_min_pct": para_float(com.minima_pct) if com else None,
        "recomendado_variavel": para_float(com.recomendado_variavel) if com else None,
        "negociado_variavel": para_float(com.negociado_variavel) if com else None,
        "desconto_ratio_comissao": para_float(com.desconto_ratio) if com else None,
        "comissao_proporcional_pct": para_float(com.proporcional_pct) if com else None,
        "comissao_max_piso_pct": para_float(com.maxima_para_o_piso_pct) if com else None,
        "comissao_variavel_pct": para_float(com.variavel_pct) if com else None,
        "limitada_pelo_piso": com.limitada_pelo_piso if com else None,
        "limitada_por": com.limitada_por if com else None,
        "comissao_travada_valor": para_float(av.comissao_travada_valor),
        "comissao_variavel_valor": para_float(av.comissao_variavel_valor),
        "receita_comissionavel": para_float(av.receita_comissionavel),
        "absorvido_por_comissao": para_float(av.absorvido_por_comissao),
        "absorvido_por_margem": para_float(av.absorvido_por_margem),
        "absorvido_por_impostos_e_frete": para_float(av.absorvido_por_impostos_e_frete),
        "lucro_total": para_float(av.lucro_total),
        "custo_total": para_float(av.custo_total),
        "margem_agregada_pct": para_float(divide(av.lucro_total, av.subtotal_negociado)),
        "itens": itens_econ,
    }
    return corpo


# ---------------------------------------------------------------------------
# Detecção: rascunho formado antes da política
# ---------------------------------------------------------------------------
def itens_anteriores_a_politica(session: Session, itens: Sequence[CotacaoItem]) -> list:
    """Itens sem política congelada cujo produto hoje resolve para uma regra COM política.

    Só detecta. Reprecificar é `/cotacoes/{id}/premissas/atualizar`, ato explícito.
    """
    achados = []
    for it in itens:
        if getattr(it, "politica_comercial", None) is not None or not it.produto_id:
            continue
        produto = session.get(Produto, it.produto_id)
        if produto is None:
            continue
        atual = ps.margem_padrao(session, produto)
        if atual.tem_politica:
            achados.append({"item_id": it.id, "produto_id": it.produto_id,
                            "nome": it.nome_produto, "politica_vigente": atual.politica,
                            "margem_no_item": para_float(D(it.margem_padrao_pct)),
                            "margem_vigente": para_float(atual.margem_pct)})
    return achados

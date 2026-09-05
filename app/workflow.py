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

## Preço abaixo do recomendado é exceção, mesmo com margem boa

A regra é do projeto e é deliberada: a autonomia de desconto do vendedor é **zero**. Se a
margem continuar saudável, ótimo — a aprovação existe para que a decisão de abrir mão de
receita seja de quem tem alçada, não para verificar se sobrou lucro.

Nada aqui conhece FastAPI, Jinja nem sessão de banco: recebe os objetos já lidos e devolve
resultado. O que grava é `workflow_service`.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from app.dinheiro import D, D0, ZERO, dinheiro, divide, para_float

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
#: Status de frete que impedem emitir quando o frete é CIF.
FRETE_BLOQUEIA = {"FRETE_A_COTAR", "FRETE_REVIEW_REQUIRED", "FRETE_ICMS_REVIEW_REQUIRED"}

# Motivos estruturados de exceção comercial (§20). Texto livre nunca é a fonte da semântica.
PRECO_ABAIXO = "PRECO_ABAIXO_RECOMENDADO"
MARGEM_ABAIXO = "MARGEM_ABAIXO_ALVO"
PREMISSA_VELHA = "PREMISSA_DESATUALIZADA_MANTIDA"
OUTRA_EXCECAO = "OUTRA_EXCECAO_COMERCIAL"


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

    def como_dict(self) -> dict:
        return {"motivo": self.motivo, "escopo": self.escopo, "detalhe": self.detalhe,
                "preco_recomendado": self.preco_recomendado,
                "preco_negociado": self.preco_negociado, "diferenca": self.diferenca,
                "diferenca_pct": self.diferenca_pct, "margem_alvo": self.margem_alvo,
                "margem_real": self.margem_real}


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
def excecoes_do_item(item) -> List[Excecao]:
    """Onde este item foge da política comercial.

    Duas regras independentes, e a primeira é a que mais surpreende:

    * **preço abaixo do recomendado é exceção mesmo com margem boa.** A autonomia de
      desconto do vendedor é zero — abrir mão de receita é decisão de quem tem alçada, não
      consequência de o lucro ter sobrado;
    * **margem real abaixo da alvo é exceção**, ainda que o preço não tenha caído (pode ter
      subido o custo, mudado o imposto ou entrado frete).
    """
    achados = []
    rotulo = item.nome_produto or f"item #{item.id}"
    # O recomendado é o do CENÁRIO desta cotação, não o preço-base do catálogo: o base foi
    # formado com outro destino fiscal e outra condição de pagamento. Sem essa distinção,
    # uma venda interestadual normal pareceria desconto.
    recomendado = dinheiro(item.preco_recomendado) if item.preco_recomendado else None
    negociado = dinheiro(item.preco_negociado) if item.preco_negociado else None

    if recomendado and negociado and negociado < recomendado:
        diferenca = negociado - recomendado
        achados.append(Excecao(
            motivo=PRECO_ABAIXO, escopo=rotulo,
            detalhe=(f"Negociado R$ {negociado} contra R$ {recomendado} de recomendado. "
                     "Preço abaixo do recomendado exige aprovação mesmo quando a margem "
                     "continua saudável."),
            preco_recomendado=str(recomendado), preco_negociado=str(negociado),
            diferenca=str(diferenca),
            diferenca_pct=str(divide(diferenca, recomendado))))

    alvo = D(item.margem_padrao_pct)
    real = D(item.margem_liquida)
    if alvo is not None and real is not None and item.custo_unitario:
        # tolerância de meio ponto-base: o centavo comercial move a margem, e isso não é
        # exceção comercial — é o arredondamento da Sessão 3B.
        if real < alvo - D("0.00005"):
            achados.append(Excecao(
                motivo=MARGEM_ABAIXO, escopo=rotulo,
                detalhe=(f"Margem real de {real * 100:.2f}% contra alvo de "
                         f"{alvo * 100:.2f}%."),
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
    """Totais do documento, para a tela do aprovador. Nada aqui recalcula economia."""
    recomendado = ZERO
    negociado = ZERO
    for it in itens:
        qtd = D0(it.quantidade)
        if it.preco_recomendado:
            recomendado += dinheiro(D0(it.preco_recomendado) * qtd)
        negociado += D0(it.faturamento)
    diferenca = negociado - recomendado
    return {"total_recomendado": para_float(recomendado),
            "total_negociado": para_float(negociado),
            "diferenca": para_float(diferenca),
            "diferenca_pct": para_float(divide(diferenca, recomendado))}


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
    corpo = {
        "cotacao": {c: _canonico(getattr(cotacao, c, None))
                    for c in CAMPOS_MATERIAIS_COTACAO},
        "itens": sorted(
            [{c: _canonico(getattr(it, c, None)) for c in CAMPOS_MATERIAIS_ITEM}
             | {"id": it.id}
             for it in itens],
            key=lambda x: (x.get("id") or 0)),
    }
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
                              aprovacao_vigente=None) -> Compromisso:
    """Esta cotação poderia virar pedido/PO/WON?

    **Não cria pedido nem WON** — isso é da Sessão 7. Existe para que a regra fique escrita
    em um lugar só desde já, e para que a diferença entre "posso propor" e "posso me
    comprometer" não seja descoberta na hora errada.

    O caso central: uma proposta com custo **ESTIMADO** pode sair, com PDF e tudo. O que ela
    não pode é virar compromisso firme antes de alguém confirmar o custo — porque o número
    veio de proxy, e assumir obrigação sobre proxy é o caminho para vender no prejuízo.
    """
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
        if getattr(it, "confirmation_pending", False):
            impedimentos.append(
                f"{rotulo}: custo ESTIMADO ainda não confirmado. A proposta pode sair; o "
                "compromisso firme, não — o número veio de proxy.")
        if (getattr(it, "status_custo_item", "") or "").upper() == "REVALIDAR":
            impedimentos.append(
                f"{rotulo}: custo em REVALIDAR. A referência é direta, mas envelheceu — "
                "reconfirmar antes de assumir compromisso.")

    return Compromisso(pode=not impedimentos, impedimentos=impedimentos)

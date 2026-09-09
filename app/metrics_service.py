"""Definições métricas — **um lugar só**.

O motivo de este módulo existir é específico: quando cada tela calcula a sua própria
conversão, o dashboard diz 42%, o relatório diz 39%, e ninguém sabe qual está certo. Aqui a
definição é escrita uma vez, e dashboard, CSV e testes consomem a mesma função.

## Persistir fato, derivar métrica

Nada aqui é gravado. Conversão, aging, ticket médio, valor de pipeline e tempo em etapa são
**derivados** dos fatos que as Sessões 6 e 7 já registram. Uma coluna `conversao` ficaria
errada no dia em que alguém fechasse um negócio sem passar pela tela que a atualiza.

## Estado atual × evento histórico

A distinção que mais confunde relatório comercial: uma oportunidade pode ter sido perdida,
reaberta e estar aberta agora.

* **estado atual** — ela está `ABERTA`. Não conta como perdida em nenhuma métrica de estado;
* **evento histórico** — a perda aconteceu, está em `AuditLog` e continua auditável.

"Quantas oportunidades estão perdidas" e "quantos eventos de perda ocorreram" são perguntas
diferentes, e misturá-las produz números que ninguém consegue reconciliar. O relatório
gerencial padrão usa **estado atual**; a auditoria de eventos é outra consulta.

## O que NÃO existe aqui

Forecast ponderado por probabilidade. Nunca definimos que `PROSPECCAO` vale 10% e
`NEGOCIACAO` 70% — inventar esses pesos produziria um número de aparência sofisticada e sem
fundamento. O pipeline é mostrado pelo **valor bruto aberto**.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence

from sqlmodel import Session, select

from app import crm_service as crm
from app import workflow as wf
from app import workflow_service as ws
from app.dinheiro import D, D0, ZERO, dinheiro, divide, para_float, soma
from app.models import (
    AprovacaoCotacao, AtividadeComercial, Cliente, Cotacao, CotacaoItem, CustoReferencia,
    Oportunidade, OportunidadeEtapaHistorico, Produto, StatusCotacao, StatusOportunidade,
    Usuario,
)

ABERTA = StatusOportunidade.aberta.value
GANHA = StatusOportunidade.ganha.value
PERDIDA = StatusOportunidade.perdida.value


# ---------------------------------------------------------------------------
# Período
# ---------------------------------------------------------------------------
@dataclass
class Periodo:
    """Janela de análise. `None` nos dois lados significa "todo o período"."""
    inicio: Optional[date] = None
    fim: Optional[date] = None
    rotulo: str = "Todo o período"

    def contem(self, quando) -> bool:
        if quando is None:
            return False
        dia = quando.date() if isinstance(quando, datetime) else quando
        if self.inicio and dia < self.inicio:
            return False
        if self.fim and dia > self.fim:
            return False
        return True

    def como_dict(self) -> dict:
        return {"inicio": self.inicio, "fim": self.fim, "rotulo": self.rotulo}


def periodo_de(atalho: str = "", inicio: Optional[str] = None,
               fim: Optional[str] = None) -> Periodo:
    """Resolve o período a partir dos atalhos da tela ou de datas explícitas.

    Sem fuso horário: o sistema é local e de uma empresa só. Inventar conversão de timezone
    aqui criaria uma classe de bug difícil de ver — relatório que muda de número conforme a
    hora — em troca de nada.
    """
    hoje = date.today()
    if inicio or fim:
        return Periodo(
            inicio=date.fromisoformat(inicio) if inicio else None,
            fim=date.fromisoformat(fim) if fim else None,
            rotulo="Período personalizado")
    if atalho == "mes":
        return Periodo(hoje.replace(day=1), hoje, "Este mês")
    if atalho == "30d":
        return Periodo(hoje - timedelta(days=30), hoje, "Últimos 30 dias")
    if atalho == "ano":
        return Periodo(hoje.replace(month=1, day=1), hoje, "Este ano")
    return Periodo(rotulo="Todo o período")


# ---------------------------------------------------------------------------
# Valor cotado atual — a regra determinística do §11
# ---------------------------------------------------------------------------
def proposta_relevante(session: Session, oportunidade_id: int) -> Optional[Cotacao]:
    """A cotação que representa comercialmente a oportunidade **agora**.

    A regra, em ordem, e ela é fechada de propósito:

    1. só cotações vinculadas a esta oportunidade;
    2. **revisões canceladas saem** — uma revisão cancelada não representa nada;
    3. as revisões de uma mesma proposta formam uma genealogia (`cotacao_origem_id`);
       dentro dela vale a de **maior revisão**. R1 e R2 **nunca são somadas**: são a mesma
       proposta em dois momentos, e somá-las dobraria o negócio;
    4. havendo propostas paralelas — genealogias diferentes na mesma oportunidade —, vale a
       de atualização mais recente: emissão, senão criação, e o `id` desempata.

    O empate por `id` existe para que a resposta **nunca** dependa da ordem em que o banco
    devolveu as linhas.
    """
    vivas = [c for c in crm.cotacoes_de(session, oportunidade_id)
             if c.status != StatusCotacao.cancelada.value]
    if not vivas:
        return None

    genealogias = {}
    for c in vivas:
        raiz = c.cotacao_origem_id or c.id
        atual = genealogias.get(raiz)
        if atual is None or (c.revisao or 1, c.id or 0) > (atual.revisao or 1, atual.id or 0):
            genealogias[raiz] = c

    def recencia(c: Cotacao):
        return (c.issued_em or c.criado_em or datetime.min, c.id or 0)

    return max(genealogias.values(), key=recencia)


def valor_cotado_atual(session: Session, oportunidade_id: int) -> Optional[float]:
    """Total comercial da proposta relevante. `None` quando não há cotação com valor."""
    cot = proposta_relevante(session, oportunidade_id)
    if cot is None:
        return None
    itens = ws.itens_de(session, cot.id)
    if not itens:
        return None
    return wf.resumo_comercial(itens)["total_negociado"]


def valor_de_pipeline(session: Session, op: Oportunidade) -> Optional[float]:
    """O que esta oportunidade aberta vale hoje.

    Cotação atual quando existe; senão o estimado; senão **`None`** — e `None` não é zero.
    Um negócio sem valor informado não é um negócio de R$ 0,00, e tratá-lo assim faria o
    pipeline parecer menor do que é.
    """
    cotado = valor_cotado_atual(session, op.id)
    if cotado is not None:
        return cotado
    return op.valor_estimado


# ---------------------------------------------------------------------------
# Painel comercial
# ---------------------------------------------------------------------------
@dataclass
class PainelComercial:
    periodo: Periodo
    abertas: int = 0
    valor_pipeline: Optional[float] = None
    abertas_sem_valor: int = 0
    ganhas: int = 0
    valor_ganho: Optional[float] = None
    perdidas: int = 0
    conversao: Optional[float] = None
    ticket_medio: Optional[float] = None
    atividades_atrasadas: int = 0
    sem_proxima_atividade: int = 0
    aprovacoes_pendentes: int = 0
    por_etapa: List[dict] = field(default_factory=list)
    por_origem: List[dict] = field(default_factory=list)
    por_responsavel: List[dict] = field(default_factory=list)
    motivos_perda: List[dict] = field(default_factory=list)
    vazio: bool = False

    def como_dict(self) -> dict:
        return {
            "periodo": self.periodo.como_dict(), "abertas": self.abertas,
            "valor_pipeline": self.valor_pipeline,
            "abertas_sem_valor": self.abertas_sem_valor,
            "ganhas": self.ganhas, "valor_ganho": self.valor_ganho,
            "perdidas": self.perdidas, "conversao": self.conversao,
            "ticket_medio": self.ticket_medio,
            "atividades_atrasadas": self.atividades_atrasadas,
            "sem_proxima_atividade": self.sem_proxima_atividade,
            "aprovacoes_pendentes": self.aprovacoes_pendentes,
            "por_etapa": self.por_etapa, "por_origem": self.por_origem,
            "por_responsavel": self.por_responsavel,
            "motivos_perda": self.motivos_perda, "vazio": self.vazio,
        }


def _fechada_no_periodo(op: Oportunidade, periodo: Periodo) -> bool:
    """Encerrou dentro da janela, **pelo desfecho atual**.

    Uma oportunidade reaberta não conta como encerrada: o desfecho dela hoje é "aberta", e
    é o estado atual que responde "como está a operação".
    """
    if op.status == GANHA:
        return periodo.contem(op.won_em)
    if op.status == PERDIDA:
        return periodo.contem(op.lost_em)
    return False


def painel_comercial(session: Session, periodo: Periodo, *,
                     responsavel_id: Optional[int] = None) -> PainelComercial:
    """O painel inteiro, com todas as métricas derivadas dos mesmos fatos."""
    todas = session.exec(select(Oportunidade)).all()
    if responsavel_id is not None:
        todas = [o for o in todas if o.responsavel_id == responsavel_id]

    abertas = [o for o in todas if o.status == ABERTA]
    ganhas = [o for o in todas if o.status == GANHA and _fechada_no_periodo(o, periodo)]
    perdidas = [o for o in todas if o.status == PERDIDA and _fechada_no_periodo(o, periodo)]

    valores = [(o, valor_de_pipeline(session, o)) for o in abertas]
    com_valor = [v for _o, v in valores if v is not None]
    valor_ganho = soma(o.valor_fechado for o in ganhas if o.valor_fechado is not None)

    painel = PainelComercial(
        periodo=periodo,
        abertas=len(abertas),
        valor_pipeline=para_float(soma(com_valor)) if com_valor else None,
        abertas_sem_valor=len([1 for _o, v in valores if v is None]),
        ganhas=len(ganhas),
        valor_ganho=para_float(valor_ganho) if ganhas else None,
        perdidas=len(perdidas),
        conversao=taxa_de_conversao(len(ganhas), len(perdidas)),
        ticket_medio=(para_float(divide(valor_ganho, D(len(ganhas)))) if ganhas else None),
        vazio=not todas,
    )

    agora = datetime.utcnow()
    atividades = crm.atividades_de(session, responsavel_id=responsavel_id, pendentes=True,
                                   limite=1000)
    painel.atividades_atrasadas = len([a for a in atividades if crm.esta_atrasada(a, agora)])
    painel.sem_proxima_atividade = len(
        [o for o in abertas if crm.proxima_atividade(session, o.id) is None])
    painel.aprovacoes_pendentes = len(session.exec(
        select(AprovacaoCotacao).where(AprovacaoCotacao.status == "PENDENTE")).all())

    painel.por_etapa = por_etapa(session, abertas)
    painel.por_origem = por_origem(session, todas, periodo)
    painel.por_responsavel = por_responsavel(session, todas, periodo)
    painel.motivos_perda = por_motivo_de_perda(perdidas)
    return painel


def taxa_de_conversao(ganhas: int, perdidas: int) -> Optional[float]:
    """`ganhas ÷ (ganhas + perdidas)` — **só negócios encerrados**.

    Oportunidade aberta não entra no denominador: ela ainda pode virar qualquer coisa, e
    incluí-la faria a conversão cair só porque o funil cresceu.

    Sem nenhum negócio encerrado, o resultado é `None` — não `0%`. Dizer "0% de conversão"
    quando nada foi decidido é afirmar um fracasso que não aconteceu.
    """
    encerradas = ganhas + perdidas
    if encerradas == 0:
        return None
    return para_float(divide(D(ganhas), D(encerradas)))


def por_etapa(session: Session, abertas: Sequence[Oportunidade]) -> List[dict]:
    """Quantidade, valor e aging por etapa — **só oportunidades abertas**."""
    agora = datetime.utcnow()
    saida = []
    for etapa in crm.ETAPAS:
        grupo = [o for o in abertas if o.etapa == etapa]
        valores = [v for v in (valor_de_pipeline(session, o) for o in grupo)
                   if v is not None]
        idades = [(agora - o.criado_em).days for o in grupo if o.criado_em]
        saida.append({
            "etapa": etapa, "quantidade": len(grupo),
            "valor": para_float(soma(valores)) if valores else None,
            "aging_medio_dias": (round(sum(idades) / len(idades), 1) if idades else None),
        })
    return saida


def por_origem(session: Session, todas: Sequence[Oportunidade],
               periodo: Periodo) -> List[dict]:
    """Desempenho por canal de entrada. `OUTRO` não explode em uma categoria por comentário."""
    origens = sorted({(o.origem or "NAO_INFORMADA") for o in todas})
    saida = []
    for origem in origens:
        grupo = [o for o in todas if (o.origem or "NAO_INFORMADA") == origem]
        g = [o for o in grupo if o.status == GANHA and _fechada_no_periodo(o, periodo)]
        p = [o for o in grupo if o.status == PERDIDA and _fechada_no_periodo(o, periodo)]
        valor = soma(o.valor_fechado for o in g if o.valor_fechado is not None)
        saida.append({
            "origem": origem, "total": len(grupo), "ganhas": len(g), "perdidas": len(p),
            "valor_ganho": para_float(valor) if g else None,
            "conversao": taxa_de_conversao(len(g), len(p)),
        })
    return sorted(saida, key=lambda x: -x["total"])


def por_responsavel(session: Session, todas: Sequence[Oportunidade],
                    periodo: Periodo) -> List[dict]:
    """Panorama por pessoa. **Não é ranking**: é para enxergar carga e pendência."""
    agora = datetime.utcnow()
    usuarios = {u.id: u for u in session.exec(select(Usuario)).all()}
    ids = sorted({o.responsavel_id for o in todas}, key=lambda x: (x is None, x))
    saida = []
    for uid in ids:
        grupo = [o for o in todas if o.responsavel_id == uid]
        abertas = [o for o in grupo if o.status == ABERTA]
        g = [o for o in grupo if o.status == GANHA and _fechada_no_periodo(o, periodo)]
        p = [o for o in grupo if o.status == PERDIDA and _fechada_no_periodo(o, periodo)]
        valores = [v for v in (valor_de_pipeline(session, o) for o in abertas)
                   if v is not None]
        pendentes = crm.atividades_de(session, responsavel_id=uid, pendentes=True,
                                      limite=1000)
        saida.append({
            "responsavel_id": uid,
            "responsavel": getattr(usuarios.get(uid), "nome", None) or "Sem responsável",
            "abertas": len(abertas),
            "pipeline": para_float(soma(valores)) if valores else None,
            "ganhas": len(g),
            "valor_ganho": para_float(soma(o.valor_fechado for o in g
                                           if o.valor_fechado is not None)) if g else None,
            "perdidas": len(p),
            "conversao": taxa_de_conversao(len(g), len(p)),
            "atividades_atrasadas": len([a for a in pendentes
                                         if crm.esta_atrasada(a, agora)]),
        })
    return sorted(saida, key=lambda x: -x["abertas"])


def por_motivo_de_perda(perdidas: Sequence[Oportunidade]) -> List[dict]:
    """Motivos das perdas **atuais** — categoria estruturada, uma linha por negócio.

    Usa o desfecho atual, então um negócio perdido, reaberto e perdido de novo aparece uma
    vez, com o motivo que vale hoje. Contar cada evento de perda faria o mesmo negócio
    inflar o relatório.
    """
    contagem = {}
    for o in perdidas:
        motivo = o.motivo_perda or "NAO_INFORMADO"
        contagem[motivo] = contagem.get(motivo, 0) + 1
    total = sum(contagem.values())
    return sorted(
        [{"motivo": m, "quantidade": n,
          "participacao": para_float(divide(D(n), D(total))) if total else None}
         for m, n in contagem.items()],
        key=lambda x: -x["quantidade"])


# ---------------------------------------------------------------------------
# Tempo em etapa
# ---------------------------------------------------------------------------
def tempo_em_etapas(session: Session, oportunidade_id: int) -> dict:
    """Quanto tempo o negócio passou em cada etapa, somando as visitas.

    Uma oportunidade pode voltar para uma etapa anterior. Aqui as visitas são **somadas** —
    "quanto tempo este negócio passou em NEGOCIAÇÃO no total" é a pergunta que o comercial
    faz. Medir cada visita separadamente é mais preciso e menos útil; se um dia for
    necessário, o histórico append-only permite reconstruir.

    A etapa atual é contada até agora, e vem marcada como `em_curso` para não parecer um
    período encerrado.
    """
    historico = crm.historico_de_etapas(session, oportunidade_id)
    if not historico:
        return {"por_etapa": {}, "atual": None, "aging_etapa_dias": None}

    agora = datetime.utcnow()
    acumulado = {}
    for i, h in enumerate(historico):
        fim = historico[i + 1].ocorrido_em if i + 1 < len(historico) else agora
        dias = (fim - h.ocorrido_em).total_seconds() / 86400
        acumulado[h.etapa_nova] = round(acumulado.get(h.etapa_nova, 0.0) + dias, 2)

    ultimo = historico[-1]
    return {
        "por_etapa": acumulado,
        "atual": ultimo.etapa_nova,
        "aging_etapa_dias": round((agora - ultimo.ocorrido_em).total_seconds() / 86400, 2),
        "em_curso": ultimo.etapa_nova,
    }


def aging(op: Oportunidade, agora: Optional[datetime] = None) -> Optional[float]:
    """Idade do negócio **aberto**, em dias. Não é "tempo de fechamento" — não fechou."""
    if op.criado_em is None:
        return None
    return round(((agora or datetime.utcnow()) - op.criado_em).total_seconds() / 86400, 1)


def tempo_ate_fechamento(op: Oportunidade) -> Optional[float]:
    """Dias entre a criação e o desfecho atual. `updated_at` não serve: ele muda por
    qualquer edição, inclusive por trocar uma observação."""
    fim = op.won_em if op.status == GANHA else (op.lost_em if op.status == PERDIDA else None)
    if fim is None or op.criado_em is None:
        return None
    return round((fim - op.criado_em).total_seconds() / 86400, 1)


# ---------------------------------------------------------------------------
# Documentos: cotações e aprovações
# ---------------------------------------------------------------------------
def painel_de_cotacoes(session: Session, periodo: Periodo) -> dict:
    """Relatório de **documentos**. Aqui cada revisão conta separadamente — de propósito.

    É a diferença entre "quantos negócios temos" (oportunidade) e "quantos documentos
    produzimos" (cotação). Nomear errado faz R1 e R2 parecerem dois negócios.
    """
    cotacoes = [c for c in session.exec(select(Cotacao)).all()
                if periodo.contem(c.criado_em)]
    por_status = {}
    for c in cotacoes:
        por_status[c.status] = por_status.get(c.status, 0) + 1

    total = ZERO
    for c in cotacoes:
        itens = ws.itens_de(session, c.id)
        if itens:
            total += D0(wf.resumo_comercial(itens)["total_negociado"])
    return {
        "documentos": len(cotacoes),
        "revisoes": len([c for c in cotacoes if (c.revisao or 1) > 1]),
        "por_status": por_status,
        "valor_comercial": para_float(total) if cotacoes else None,
    }


def painel_de_aprovacoes(session: Session, periodo: Periodo) -> dict:
    """Pendentes agora e decisões do período. **Sem SLA** — nunca definimos um."""
    todas = session.exec(select(AprovacaoCotacao)).all()
    pendentes = [a for a in todas if a.status == "PENDENTE"]
    decididas = [a for a in todas if a.decidido_em and periodo.contem(a.decidido_em)]
    aprovadas = [a for a in decididas if a.status == "APROVADA"]
    rejeitadas = [a for a in decididas if a.status == "REJEITADA"]

    horas = [(a.decidido_em - a.criado_em).total_seconds() / 3600
             for a in decididas if a.criado_em and a.decidido_em]
    return {
        "pendentes": len(pendentes),
        "aprovadas": len(aprovadas), "rejeitadas": len(rejeitadas),
        "horas_ate_decisao_media": round(sum(horas) / len(horas), 1) if horas else None,
        "mais_antigo_pendente_dias": (
            round(min((datetime.utcnow() - a.criado_em).total_seconds() / 86400
                      for a in pendentes if a.criado_em), 1) if pendentes else None),
    }


# ---------------------------------------------------------------------------
# Indicadores econômicos — restritos
# ---------------------------------------------------------------------------
def painel_economico(session: Session, periodo: Periodo) -> dict:
    """Indicadores internos. **Só para quem pode ver economia** — a rota é quem barra.

    A margem agregada é `Σ lucro ÷ Σ receita`, **nunca** a média dos percentuais por item:
    um item de R$ 100 com 10% e um de R$ 900 com 20% dão 19% agregados, e 15% na média
    simples — que é um número que não corresponde a dinheiro nenhum.

    Item sem dado econômico válido fica **fora** do agregado. Incluí-lo como margem zero
    afirmaria um prejuízo que ninguém apurou.
    """
    itens = [i for i in session.exec(select(CotacaoItem)).all()
             if i.faturamento and i.custo_unitario]
    receita = soma(i.faturamento for i in itens)
    lucro = soma(i.lucro for i in itens)

    ganhas = [o for o in session.exec(select(Oportunidade)).all()
              if o.status == GANHA and _fechada_no_periodo(o, periodo)]

    return {
        "itens_considerados": len(itens),
        "itens_sem_dado_economico": len(session.exec(select(CotacaoItem)).all()) - len(itens),
        "receita_cotada": para_float(receita) if itens else None,
        "lucro_cotado": para_float(lucro) if itens else None,
        "margem_agregada": para_float(divide(lucro, receita)) if receita else None,
        "valor_ganho": para_float(soma(o.valor_fechado for o in ganhas
                                       if o.valor_fechado is not None)) if ganhas else None,
        "por_fornecedor": _margem_por_fornecedor(session, itens),
    }


def _margem_por_fornecedor(session: Session, itens: Sequence[CotacaoItem]) -> List[dict]:
    from app.models import Fornecedor

    nomes = {f.id: f.nome for f in session.exec(select(Fornecedor)).all()}
    grupos = {}
    for i in itens:
        grupos.setdefault(i.fornecedor_id, []).append(i)
    saida = []
    for fid, grupo in grupos.items():
        receita = soma(i.faturamento for i in grupo)
        lucro = soma(i.lucro for i in grupo)
        saida.append({
            "fornecedor": nomes.get(fid) or "Não informado",
            "itens": len(grupo),
            "receita": para_float(receita),
            "lucro": para_float(lucro),
            "margem": para_float(divide(lucro, receita)) if receita else None,
        })
    return sorted(saida, key=lambda x: -(x["receita"] or 0))


# ---------------------------------------------------------------------------
# Saúde operacional
# ---------------------------------------------------------------------------
def saude_operacional(session: Session) -> dict:
    """O que está travado e o que só precisa de atenção — **e essa diferença importa**.

    `blockers` impedem emitir documento: falta dado econômico, e nenhuma alçada os dispensa.
    `avisos` são coisas para acompanhar — proposta estimada, custo envelhecido, negócio sem
    próximo passo. Misturar os dois produziria "23 problemas" sem dizer quais impedem
    trabalhar hoje.

    Cada contagem diz também **o que** está sendo contado: SKU, referência ou cotação.
    """
    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    referencias = session.exec(select(CustoReferencia)).all()
    vigentes = [r for r in referencias if r.vigente]
    por_status = {}
    for r in vigentes:
        chave = r.status_custo or "SEM_STATUS"
        por_status[chave] = por_status.get(chave, 0) + 1

    itens = session.exec(select(CotacaoItem)).all()
    cotacoes = {c.id: c for c in session.exec(select(Cotacao)).all()}
    editaveis = [c for c in cotacoes.values()
                 if c.status not in (StatusCotacao.emitida.value,
                                     StatusCotacao.enviada.value,
                                     StatusCotacao.cancelada.value)]

    def cotacoes_com(condicao) -> List[dict]:
        alvo = {}
        for i in itens:
            c = cotacoes.get(i.cotacao_id)
            if c is None or c not in editaveis:
                continue
            if condicao(i):
                alvo.setdefault(c.id, {"cotacao_id": c.id, "numero": c.numero,
                                       "status": c.status, "itens": 0})
                alvo[c.id]["itens"] += 1
        return list(alvo.values())

    blockers = {
        "cotacoes_com_a_cotar": cotacoes_com(
            lambda i: (i.status_custo_item or "").upper() == "A_COTAR"),
        "cotacoes_com_review_required": cotacoes_com(
            lambda i: (i.status_custo_item or "").upper() == "REVIEW_REQUIRED"
            or (i.status_fiscal or "") == "REVIEW_REQUIRED"),
        "cotacoes_com_pagamento_irresolvido": cotacoes_com(
            lambda i: (i.status_pagamento or "") == "REVIEW_REQUIRED"),
    }
    avisos = {
        "cotacoes_com_estimado_pendente": cotacoes_com(
            lambda i: bool(i.confirmation_pending)),
        "cotacoes_com_revalidar": cotacoes_com(
            lambda i: (i.status_custo_item or "").upper() == "REVALIDAR"),
    }

    agora = datetime.utcnow()
    abertas = session.exec(select(Oportunidade)
                           .where(Oportunidade.status == ABERTA)).all()
    pendentes = crm.atividades_de(session, pendentes=True, limite=1000)

    return {
        "catalogo": {
            "skus_ativos": len(produtos),
            "skus_sem_custo": len([p for p in produtos if not p.custo_unitario]),
            "referencias_versionadas": len(referencias),
            "referencias_vigentes_por_status": por_status,
        },
        "blockers": blockers,
        "total_blockers": sum(len(v) for v in blockers.values()),
        "avisos": avisos,
        "total_avisos": sum(len(v) for v in avisos.values()),
        "comercial": {
            "aprovacoes_pendentes": len(session.exec(
                select(AprovacaoCotacao)
                .where(AprovacaoCotacao.status == "PENDENTE")).all()),
            "oportunidades_sem_atividade": len(
                [o for o in abertas if crm.proxima_atividade(session, o.id) is None]),
            "atividades_atrasadas": len([a for a in pendentes
                                         if crm.esta_atrasada(a, agora)]),
        },
        "pendencias_conhecidas": PENDENCIAS_CONHECIDAS,
    }


#: Pendências reais que o sistema **não** resolve e não finge resolver. Mostrar que elas
#: existem é o oposto de transformá-las em zero.
PENDENCIAS_CONHECIDAS = [
    {"id": "C-NEW-01", "assunto": "Frete", "titulo": "ICMS da prestação de frete",
     "situacao": "Três evidências não reconciliadas. Bloqueia frete CIF."},
    {"id": "C-NEW-06", "assunto": "Frete", "titulo": "Fiel depositário",
     "situacao": "0,5% da NF existe na tabela; quando incide, não."},
    {"id": "C-NEW-02", "assunto": "Frete", "titulo": "GRIS",
     "situacao": "Aplicabilidade não confirmada."},
    {"id": "C-NEW-08", "assunto": "Frete", "titulo": "Base do pedágio",
     "situacao": "Peso real ou taxado — não declarado."},
    {"id": "C-NEW-03", "assunto": "Frete", "titulo": "Passo Fundo-RS",
     "situacao": "Região sem tarifa — frete a cotar."},
    {"id": "C-NEW-07", "assunto": "Frete", "titulo": "Validade da tabela TRANSAL",
     "situacao": "Vence em 31/12/2026. Vencida sem substituta, bloqueia."},
    {"id": "Q-L", "assunto": "Frete", "titulo": "Origem logística de Daune e Decor",
     "situacao": "Ambas embarcam de São Paulo, e não há tabela de frete com essa origem."},
    {"id": "C-NEW-04", "assunto": "Catálogo", "titulo": "Volume por SKU",
     "situacao": "Cadastro ausente. Só importa no CIF, quando peso real e taxado divergem."},
    {"id": "B-17", "assunto": "Custos", "titulo": "Duas fontes Daune com bases diferentes",
     "situacao": "Razão constante de 1,5123 entre os documentos. Confirmar com a Daune."},
    {"id": "B-16", "assunto": "Catálogo", "titulo": "Gramatura não estruturada",
     "situacao": "28 de 31 SKUs de Duvet Insert sem `gsm` — a gramatura vive no nome."},
    {"id": "B-13", "assunto": "Sistema", "titulo": "Esquema declarado ≠ esquema real",
     "situacao": "21 divergências entre `models.py` e o banco. Consequência do ALTER TABLE do SQLite."},
    {"id": "B-19", "assunto": "Sistema", "titulo": "Scripts em massa",
     "situacao": "Dois quebram com cenário fiscal bloqueado. Nenhum efeito no uso normal."},
    {"id": "B-20", "assunto": "Sistema", "titulo": "GET /logout",
     "situacao": "Muda estado via GET. Baixo risco: a pessoa loga de novo."},
    {"id": "B-22", "assunto": "Sistema", "titulo": "Backup sem poda",
     "situacao": "`scripts/backup_banco.py` não aplica o limite. Não bloqueia nada."},
]

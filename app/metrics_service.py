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
from app import rotulos
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
    if atalho == "trimestre":
        inicio_tri = hoje.replace(month=((hoje.month - 1) // 3) * 3 + 1, day=1)
        return Periodo(inicio_tri, hoje, "Este trimestre")
    if atalho == "ano":
        return Periodo(hoje.replace(month=1, day=1), hoje, "Este ano")
    if atalho == "12m":
        # doze meses fechados + o corrente: do dia 1 de onze meses atrás até hoje
        ano, mes = hoje.year, hoje.month - 11
        while mes <= 0:
            mes += 12
            ano -= 1
        return Periodo(date(ano, mes, 1), hoje, "Últimos 12 meses")
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


# ---------------------------------------------------------------------------
# Fase 3B — Cliente 360 e painel de vendas (para o Dashboard Admin da Fase 3C)
# ---------------------------------------------------------------------------
def _vencedora_itens(session: Session, op: Oportunidade) -> List[CotacaoItem]:
    """Os itens da proposta vencedora — a única cotação que representa uma venda GANHA.

    R1 e R2 nunca são somadas: só a `cotacao_vencedora_id` conta.
    """
    if not op.cotacao_vencedora_id:
        return []
    return ws.itens_de(session, op.cotacao_vencedora_id)


def cliente_360(session: Session, cliente_id: int) -> dict:
    """Os números comerciais DERIVADOS da ficha do cliente. Nada aqui é gravado.

        TOTAL COMPRADO   Σ valor_fechado das vendas GANHAS      (cotação enviada NÃO é compra)
        QTD DE VENDAS    número de vendas GANHAS
        TICKET MÉDIO     total comprado ÷ quantidade de vendas ganhas
        ÚLTIMA COMPRA    maior won_em entre as ganhas
        EM ANDAMENTO     vendas ABERTAS (quantidade e valor de pipeline)
        EM ABERTO        Σ valor_fechado das ganhas ainda não pagas
        ATRASADO         Σ valor_fechado das ganhas marcadas ATRASADO
        PAGO             Σ valor_fechado das ganhas PAGO
    """
    from app import pos_venda_service as pv

    todas = session.exec(select(Oportunidade)
                         .where(Oportunidade.cliente_id == cliente_id)).all()
    ganhas = [o for o in todas if o.status == GANHA]
    abertas = [o for o in todas if o.status == ABERTA]
    total = soma(o.valor_fechado for o in ganhas if o.valor_fechado is not None)
    pipeline = [v for v in (valor_de_pipeline(session, o) for o in abertas) if v is not None]
    em_aberto = [o for o in ganhas if o.status_pos_venda in pv.EM_ABERTO]
    atrasadas = [o for o in ganhas if o.status_pos_venda == pv.ATRASADO]
    pagas = [o for o in ganhas if o.status_pos_venda == pv.PAGO]
    faturadas = [o for o in ganhas if o.faturado_em]
    ultima = max((o.won_em for o in ganhas if o.won_em), default=None)
    return {
        "total_comprado": para_float(total) if ganhas else None,
        "quantidade_vendas": len(ganhas),
        "ticket_medio": para_float(divide(total, D(len(ganhas)))) if ganhas else None,
        "ultima_compra": ultima,
        "vendas_em_andamento": len(abertas),
        "valor_em_andamento": para_float(soma(pipeline)) if pipeline else None,
        "valor_em_aberto": para_float(soma(o.valor_fechado for o in em_aberto)) if em_aberto else None,
        "quantidade_em_aberto": len(em_aberto),
        "valor_atrasado": para_float(soma(o.valor_fechado for o in atrasadas)) if atrasadas else None,
        "quantidade_atrasada": len(atrasadas),
        "valor_faturado": para_float(soma(o.valor_fechado for o in faturadas)) if faturadas else None,
        "valor_pago": para_float(soma(o.valor_fechado for o in pagas)) if pagas else None,
        "perdidas": len([o for o in todas if o.status == PERDIDA]),
    }


def _agrupar(linhas, chave):
    grupos = {}
    for rotulo, valor in linhas:
        grupos.setdefault(rotulo, ZERO)
        grupos[rotulo] += D0(valor)
    return sorted([{chave: k, "valor": para_float(v)} for k, v in grupos.items()],
                  key=lambda x: -(x["valor"] or 0))


def painel_vendas(session: Session, periodo: Periodo) -> dict:
    """As métricas que o Dashboard Admin (Fase 3C) vai mostrar — definições canônicas.

    * **vendido / faturado / pago são três coisas**: `valor_fechado` das GANHAS no período
      (por `won_em`), das que têm `faturado_em` no período, das PAGAS por `pago_em`;
    * **lucro e margem** vêm dos itens da **cotação vencedora** de cada venda ganha —
      margem agregada = Σ lucro ÷ Σ receita, nunca média de percentuais;
    * **desconto médio** é ponderado por valor: 1 − Σ negociado ÷ Σ recomendado, sobre os
      itens da vencedora que têm recomendado;
    * **comissão estimada** = Σ `comissao_valor` da vencedora (estimativa de pricing);
    * revisões de cotação **não** contam como vendas; itens sem dado econômico ficam fora
      do agregado.

    Lucro, margem, comissão e desconto são **economia**: quem consome decide se mostra.
    """
    todas = session.exec(select(Oportunidade)).all()
    ganhas = [o for o in todas if o.status == GANHA and periodo.contem(o.won_em)]
    perdidas = [o for o in todas if o.status == PERDIDA and periodo.contem(o.lost_em)]
    abertas = [o for o in todas if o.status == ABERTA]
    faturadas = [o for o in todas if o.status == GANHA and o.faturado_em
                 and periodo.contem(datetime.combine(o.faturado_em, datetime.min.time()))]
    pagas = [o for o in todas if o.status == GANHA and o.pago_em and periodo.contem(o.pago_em)]
    from app import pos_venda_service as pv
    aguardando_entrega = [o for o in todas if o.status_pos_venda == pv.AGUARDANDO_ENTREGA]
    aguardando_pagamento = [o for o in todas if o.status_pos_venda == pv.AGUARDANDO_PAGAMENTO]
    atrasadas = [o for o in todas if o.status_pos_venda == pv.ATRASADO]

    vendido = soma(o.valor_fechado for o in ganhas if o.valor_fechado is not None)
    receita = lucro = comissao = rec = neg = ZERO
    por_vendedor, por_cliente, por_fornecedor, por_familia = [], [], [], []
    usuarios = {u.id: u.nome for u in session.exec(select(Usuario)).all()}
    clientes = {c.id: c.nome for c in session.exec(select(Cliente)).all()}
    produtos = {p.id: p for p in session.exec(select(Produto)).all()}
    for o in ganhas:
        por_vendedor.append((usuarios.get(o.responsavel_id) or "Sem responsável", o.valor_fechado))
        por_cliente.append((clientes.get(o.cliente_id) or "—", o.valor_fechado))
        for it in _vencedora_itens(session, o):
            if not (it.faturamento and it.custo_unitario):
                continue
            receita += D0(it.faturamento)
            lucro += D0(it.lucro)
            comissao += D0(it.comissao_valor)
            if it.preco_recomendado:
                rec += dinheiro(D0(it.preco_recomendado) * D0(it.quantidade))
                neg += D0(it.faturamento)
            por_fornecedor.append((it.fornecedor_nome or "—", it.faturamento))
            familia = getattr(produtos.get(it.produto_id), "familia", None) or "—"
            por_familia.append((familia, it.faturamento))

    tempos = [t for t in (tempo_ate_fechamento(o) for o in ganhas) if t is not None]
    idades = [a for a in (aging(o) for o in abertas) if a is not None]
    em_negociacao = [v for v in (valor_de_pipeline(session, o) for o in abertas
                                 if o.etapa == crm.NEGOCIACAO) if v is not None]
    return {
        "periodo": periodo.como_dict(),
        "valor_vendido": para_float(vendido) if ganhas else None,
        "valor_faturado": para_float(soma(o.valor_fechado for o in faturadas)) if faturadas else None,
        "valor_pago": para_float(soma(o.valor_fechado for o in pagas)) if pagas else None,
        "lucro_das_vendas": para_float(lucro) if receita else None,
        "margem_agregada": para_float(divide(lucro, receita)) if receita else None,
        "numero_vendas": len(ganhas),
        "ticket_medio": para_float(divide(vendido, D(len(ganhas)))) if ganhas else None,
        "taxa_conversao": taxa_de_conversao(len(ganhas), len(perdidas)),
        "desconto_medio_ponderado": para_float(D("1") - divide(neg, rec)) if rec else None,
        "comissao_estimada": para_float(comissao) if receita else None,
        "vendas_abertas": len(abertas),
        "valor_em_negociacao": para_float(soma(em_negociacao)) if em_negociacao else None,
        "por_vendedor": _agrupar(por_vendedor, "vendedor"),
        "por_cliente": _agrupar(por_cliente, "cliente"),
        "por_fornecedor": _agrupar(por_fornecedor, "fornecedor"),
        "por_familia": _agrupar(por_familia, "familia"),
        "tempo_medio_ate_fechamento_dias": (round(sum(tempos) / len(tempos), 1) if tempos else None),
        "aging_medio_abertas_dias": (round(sum(idades) / len(idades), 1) if idades else None),
        "aguardando_entrega": len(aguardando_entrega),
        "valor_aguardando_pagamento": (para_float(soma(o.valor_fechado for o in aguardando_pagamento))
                                       if aguardando_pagamento else None),
        "valor_atrasado": para_float(soma(o.valor_fechado for o in atrasadas)) if atrasadas else None,
        "quantidade_atrasada": len(atrasadas),
    }


#: O que do painel de vendas é economia interna — a rota corta para quem não vê.
CAMPOS_ECONOMICOS_DO_PAINEL = ("lucro_das_vendas", "margem_agregada", "comissao_estimada",
                               "desconto_medio_ponderado")


def painel_vendas_comercial(session: Session, periodo: Periodo) -> dict:
    painel = painel_vendas(session, periodo)
    return {k: v for k, v in painel.items() if k not in CAMPOS_ECONOMICOS_DO_PAINEL}


def clientes_resumo(session: Session) -> dict:
    """`{cliente_id: resumo}` para a lista de Clientes (Fase 3C) — uma passada só.

    Mesmas definições de `cliente_360`, agrupadas por cliente sem uma consulta por linha.
    `status_financeiro` é derivado do pós-venda das vendas ganhas: "Atrasado" só quando a
    alçada financeira marcou; "Em aberto" quando há valor vendido ainda não pago; "Em dia"
    quando tudo o que foi vendido está pago; `None` sem venda.
    """
    from app import pos_venda_service as pv

    grupos = {}
    for o in session.exec(select(Oportunidade)).all():
        grupos.setdefault(o.cliente_id, []).append(o)
    saida = {}
    for cliente_id, todas in grupos.items():
        ganhas = [o for o in todas if o.status == GANHA]
        total = soma(o.valor_fechado for o in ganhas if o.valor_fechado is not None)
        em_aberto = [o for o in ganhas if o.status_pos_venda in pv.EM_ABERTO]
        atrasadas = [o for o in ganhas if o.status_pos_venda == pv.ATRASADO]
        if atrasadas:
            status = "ATRASADO"
        elif em_aberto:
            status = "EM_ABERTO"
        elif ganhas:
            status = "EM_DIA"
        else:
            status = None
        saida[cliente_id] = {
            "total_comprado": para_float(total) if ganhas else None,
            "quantidade_vendas": len(ganhas),
            "ultima_compra": max((o.won_em for o in ganhas if o.won_em), default=None),
            "valor_em_aberto": para_float(soma(o.valor_fechado for o in em_aberto)) if em_aberto else None,
            "valor_atrasado": para_float(soma(o.valor_fechado for o in atrasadas)) if atrasadas else None,
            "vendas_em_andamento": len([o for o in todas if o.status == ABERTA]),
            "status_financeiro": status,
        }
    return saida


# ---------------------------------------------------------------------------
# Fase 3C — Dashboard OWNER/ADMIN: filtros, série mensal e análises secundárias
# ---------------------------------------------------------------------------
# Tudo aqui é DERIVADO das mesmas definições de `painel_vendas`: vendido = `valor_fechado`
# das GANHAS por `won_em`; lucro/margem/desconto/comissão = itens da cotação vencedora;
# revisões nunca somam; item sem dado econômico fica fora do agregado. Filtro de vendedora e
# de cliente corta a VENDA; filtro de fornecedor e de família corta os ITENS da vencedora (e
# a venda entra se algum item sobrar). Nada é inventado para preencher gráfico.
@dataclass
class FiltrosDashboard:
    responsavel_id: Optional[int] = None
    cliente_id: Optional[int] = None
    fornecedor_id: Optional[int] = None
    familia: Optional[str] = None

    @property
    def por_item(self) -> bool:
        return self.fornecedor_id is not None or bool(self.familia)


def _vendas_ganhas(session: Session, filtros: Optional[FiltrosDashboard] = None):
    """`[(op, itens_da_vencedora_filtrados)]` para TODAS as ganhas — o período corta depois."""
    filtros = filtros or FiltrosDashboard()
    produtos = {p.id: p for p in session.exec(select(Produto)).all()}
    saida = []
    for o in session.exec(select(Oportunidade)).all():
        if o.status != GANHA:
            continue
        if filtros.responsavel_id is not None and o.responsavel_id != filtros.responsavel_id:
            continue
        if filtros.cliente_id is not None and o.cliente_id != filtros.cliente_id:
            continue
        itens = _vencedora_itens(session, o)
        if filtros.por_item:
            itens = [it for it in itens
                     if (filtros.fornecedor_id is None or it.fornecedor_id == filtros.fornecedor_id)
                     and (not filtros.familia
                          or (getattr(produtos.get(it.produto_id), "familia", None) or "—") == filtros.familia)]
            if not itens:
                continue
        saida.append((o, itens))
    return saida


def _economia_dos_itens(itens: Sequence[CotacaoItem]) -> dict:
    """Receita, lucro, comissão e desconto ponderado de um conjunto de itens (Decimal)."""
    receita = lucro = comissao = rec = neg = ZERO
    for it in itens:
        if not (it.faturamento and it.custo_unitario):
            continue
        receita += D0(it.faturamento)
        lucro += D0(it.lucro)
        comissao += D0(it.comissao_valor)
        if it.preco_recomendado:
            rec += dinheiro(D0(it.preco_recomendado) * D0(it.quantidade))
            neg += D0(it.faturamento)
    return {"receita": receita, "lucro": lucro, "comissao": comissao, "rec": rec, "neg": neg}


def _meses_ate(fim: date, quantidade: int) -> List[tuple]:
    """`[(ano, mes)]` dos `quantidade` meses que terminam no mês de `fim`, em ordem."""
    ano, mes = fim.year, fim.month
    meses = []
    for _ in range(quantidade):
        meses.append((ano, mes))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return list(reversed(meses))


ROTULO_MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def serie_mensal(session: Session, *, meses: int = 12, fim: Optional[date] = None,
                 filtros: Optional[FiltrosDashboard] = None) -> dict:
    """Vendido, lucro, margem, nº de vendas, faturado e pago por mês — os últimos `meses`.

    `ano_anterior` traz a mesma série deslocada 12 meses **só se houver dado**; sem venda
    naquele período o campo fica `None` e a tela não desenha comparação nenhuma.
    """
    fim = fim or date.today()
    janela = _meses_ate(fim, meses)
    ganhas = _vendas_ganhas(session, filtros)
    todas = {o.id: o for o, _ in ganhas}

    def bucket(chave):
        return {"vendido": ZERO, "lucro": ZERO, "receita": ZERO, "n": 0, "faturado": ZERO, "pago": ZERO}

    por_mes = {m: bucket(m) for m in janela}
    anterior = {(a - 1, m): bucket(m) for a, m in janela}
    for o, itens in ganhas:
        eco = _economia_dos_itens(itens)
        valor = D0(o.valor_fechado) if not (filtros and filtros.por_item) else eco["receita"]
        if o.won_em:
            chave = (o.won_em.year, o.won_em.month)
            for alvo in (por_mes, anterior):
                if chave in alvo:
                    b = alvo[chave]
                    b["vendido"] += valor
                    b["lucro"] += eco["lucro"]
                    b["receita"] += eco["receita"]
                    b["n"] += 1
        if o.faturado_em and (o.faturado_em.year, o.faturado_em.month) in por_mes:
            por_mes[(o.faturado_em.year, o.faturado_em.month)]["faturado"] += valor
        if o.pago_em and (o.pago_em.year, o.pago_em.month) in por_mes:
            por_mes[(o.pago_em.year, o.pago_em.month)]["pago"] += valor

    def linha(chave, b):
        return {
            "ano": chave[0], "mes": chave[1],
            "rotulo": f"{ROTULO_MES[chave[1] - 1]}/{str(chave[0])[2:]}",
            "vendido": para_float(b["vendido"]) if b["n"] else None,
            "lucro": para_float(b["lucro"]) if b["receita"] else None,
            "margem": para_float(divide(b["lucro"], b["receita"])) if b["receita"] else None,
            "vendas": b["n"],
            "faturado": para_float(b["faturado"]) if b["faturado"] else None,
            "pago": para_float(b["pago"]) if b["pago"] else None,
        }

    serie = [linha(m, por_mes[m]) for m in janela]
    ant = [linha((a - 1, m), anterior[(a - 1, m)]) for a, m in janela]
    tem_anterior = any(x["vendas"] for x in ant)
    return {"meses": serie, "ano_anterior": ant if tem_anterior else None,
            "total_vendido": para_float(soma(por_mes[m]["vendido"] for m in janela)),
            "total_lucro": para_float(soma(por_mes[m]["lucro"] for m in janela))}


def dashboard_admin(session: Session, periodo: Periodo,
                    filtros: Optional[FiltrosDashboard] = None) -> dict:
    """Tudo que o Dashboard OWNER/ADMIN mostra, num dicionário só, com as mesmas regras."""
    filtros = filtros or FiltrosDashboard()
    from app import pos_venda_service as pv

    todas_ops = session.exec(select(Oportunidade)).all()
    usuarios = {u.id: u.nome for u in session.exec(select(Usuario)).all()}
    clientes = {c.id: c.nome for c in session.exec(select(Cliente)).all()}
    produtos = {p.id: p for p in session.exec(select(Produto)).all()}

    def passa_venda(o):
        if filtros.responsavel_id is not None and o.responsavel_id != filtros.responsavel_id:
            return False
        if filtros.cliente_id is not None and o.cliente_id != filtros.cliente_id:
            return False
        return True

    ganhas_periodo = [(o, its) for o, its in _vendas_ganhas(session, filtros)
                      if periodo.contem(o.won_em)]
    perdidas = [o for o in todas_ops if o.status == PERDIDA and passa_venda(o)
                and periodo.contem(o.lost_em)]
    abertas = [o for o in todas_ops if o.status == ABERTA and passa_venda(o)]
    # ganhas de qualquer período (para o financeiro: faturado/pago no período e a receber)
    ganhas_todas = _vendas_ganhas(session, filtros)

    def valor_de(o, itens):
        return D0(o.valor_fechado) if not filtros.por_item else _economia_dos_itens(itens)["receita"]

    vendido = soma(valor_de(o, its) for o, its in ganhas_periodo)
    eco = _economia_dos_itens([it for _o, its in ganhas_periodo for it in its])

    faturado = soma(valor_de(o, its) for o, its in ganhas_todas
                    if o.faturado_em and periodo.contem(datetime.combine(o.faturado_em, datetime.min.time())))
    pago = soma(valor_de(o, its) for o, its in ganhas_todas if o.pago_em and periodo.contem(o.pago_em))
    a_receber_lista = [(o, its) for o, its in ganhas_todas if o.status_pos_venda in pv.EM_ABERTO]
    atrasadas = [(o, its) for o, its in ganhas_todas if o.status_pos_venda == pv.ATRASADO]
    ag_entrega = [(o, its) for o, its in ganhas_todas if o.status_pos_venda == pv.AGUARDANDO_ENTREGA]
    ag_pag = [(o, its) for o, its in ganhas_todas if o.status_pos_venda == pv.AGUARDANDO_PAGAMENTO]

    pipeline = [(o, valor_de_pipeline(session, o)) for o in abertas]
    pipeline_com_valor = [v for _o, v in pipeline if v is not None]

    # --- por vendedora ---------------------------------------------------
    por_vendedora = {}
    for o, its in ganhas_periodo:
        g = por_vendedora.setdefault(o.responsavel_id, {"vendas": 0, "vendido": ZERO, "itens": []})
        g["vendas"] += 1
        g["vendido"] += valor_de(o, its)
        g["itens"].extend(its)
    performance = []
    for uid, g in por_vendedora.items():
        e = _economia_dos_itens(g["itens"])
        performance.append({
            "responsavel_id": uid, "vendedora": usuarios.get(uid) or "Sem responsável",
            "vendido": para_float(g["vendido"]), "vendas": g["vendas"],
            "ticket": para_float(divide(g["vendido"], D(g["vendas"]))),
            "margem": para_float(divide(e["lucro"], e["receita"])) if e["receita"] else None,
            "desconto_medio": para_float(D("1") - divide(e["neg"], e["rec"])) if e["rec"] else None,
            "comissao_estimada": para_float(e["comissao"]) if e["receita"] else None,
        })
    performance.sort(key=lambda x: -(x["vendido"] or 0))

    # --- por cliente: top, concentração e rentabilidade --------------------
    por_cliente = {}
    for o, its in ganhas_periodo:
        g = por_cliente.setdefault(o.cliente_id, {"vendas": 0, "vendido": ZERO, "itens": []})
        g["vendas"] += 1
        g["vendido"] += valor_de(o, its)
        g["itens"].extend(its)
    clientes_lista = []
    for cid, g in por_cliente.items():
        e = _economia_dos_itens(g["itens"])
        clientes_lista.append({
            "cliente_id": cid, "cliente": clientes.get(cid) or "—",
            "vendido": para_float(g["vendido"]), "vendas": g["vendas"],
            "lucro": para_float(e["lucro"]) if e["receita"] else None,
            "margem": para_float(divide(e["lucro"], e["receita"])) if e["receita"] else None,
            "participacao": para_float(divide(g["vendido"], vendido)) if vendido else None,
        })
    clientes_lista.sort(key=lambda x: -(x["vendido"] or 0))
    top5 = clientes_lista[:5]
    concentracao_top5 = (para_float(divide(soma(D0(c["vendido"]) for c in top5), vendido))
                         if vendido else None)

    # --- mix fornecedor / família (itens das vencedoras) -------------------
    mix_forn, mix_fam = {}, {}
    for _o, its in ganhas_periodo:
        for it in its:
            mix_forn[it.fornecedor_nome or "—"] = mix_forn.get(it.fornecedor_nome or "—", ZERO) + D0(it.faturamento)
            fam = getattr(produtos.get(it.produto_id), "familia", None) or "—"
            mix_fam[fam] = mix_fam.get(fam, ZERO) + D0(it.faturamento)
    total_itens = soma(mix_forn.values())

    def mix(d):
        return sorted([{"nome": k, "valor": para_float(v),
                        "participacao": para_float(divide(v, total_itens)) if total_itens else None}
                       for k, v in d.items()], key=lambda x: -(x["valor"] or 0))

    # --- impacto dos descontos: faixas de desconto × margem ---------------
    faixas = [("Sem desconto", ZERO, ZERO), ("Até 5%", ZERO, D("0.05")),
              ("5% a 10%", D("0.05"), D("0.10")), ("Acima de 10%", D("0.10"), None)]
    impacto = {f[0]: {"vendas": 0, "vendido": ZERO, "itens": []} for f in faixas}
    margem_x_desconto = []
    for o, its in ganhas_periodo:
        e = _economia_dos_itens(its)
        desconto = (D("1") - divide(e["neg"], e["rec"])) if e["rec"] else None
        margem = divide(e["lucro"], e["receita"]) if e["receita"] else None
        if desconto is not None and desconto < ZERO:
            desconto = ZERO
        margem_x_desconto.append({
            "venda_id": o.id, "titulo": o.titulo, "cliente": clientes.get(o.cliente_id) or "—",
            "vendido": para_float(valor_de(o, its)),
            "desconto": para_float(desconto), "margem": para_float(margem)})
        if desconto is None:
            continue
        for nome, de, ate in faixas:
            if (de == ZERO and ate == ZERO and desconto == ZERO) or \
               (ate is not None and not (de == ZERO and ate == ZERO) and de < desconto <= ate) or \
               (ate is None and desconto > de):
                impacto[nome]["vendas"] += 1
                impacto[nome]["vendido"] += valor_de(o, its)
                impacto[nome]["itens"].extend(its)
                break
    impacto_lista = []
    for nome, g in impacto.items():
        e = _economia_dos_itens(g["itens"])
        impacto_lista.append({"faixa": nome, "vendas": g["vendas"],
                              "vendido": para_float(g["vendido"]) if g["vendas"] else None,
                              "margem": para_float(divide(e["lucro"], e["receita"])) if e["receita"] else None})

    # --- funil simples e aging das abertas ------------------------------
    funil = []
    for etapa in crm.ETAPAS:
        grupo = [(o, v) for o, v in pipeline if o.etapa == etapa]
        valores = [v for _o, v in grupo if v is not None]
        funil.append({"etapa": etapa, "rotulo": rotulos.etapa_venda(etapa),
                      "quantidade": len(grupo), "valor": para_float(soma(valores)) if valores else None})
    agora = datetime.utcnow()
    faixas_aging = [("Até 7 dias", 0, 7), ("8 a 30 dias", 8, 30), ("31 a 60 dias", 31, 60), ("Mais de 60 dias", 61, None)]
    aging_lista = []
    for nome, de, ate in faixas_aging:
        grupo = []
        for o, v in pipeline:
            idade = aging(o, agora)
            if idade is None:
                continue
            if idade >= de and (ate is None or idade <= ate):
                grupo.append(v)
        valores = [v for v in grupo if v is not None]
        aging_lista.append({"faixa": nome, "quantidade": len(grupo),
                            "valor": para_float(soma(valores)) if valores else None})

    return {
        "periodo": periodo.como_dict(),
        "filtros": {"responsavel_id": filtros.responsavel_id, "cliente_id": filtros.cliente_id,
                    "fornecedor_id": filtros.fornecedor_id, "familia": filtros.familia},
        # principais
        "valor_vendido": para_float(vendido) if ganhas_periodo else None,
        "lucro": para_float(eco["lucro"]) if eco["receita"] else None,
        "margem_agregada": para_float(divide(eco["lucro"], eco["receita"])) if eco["receita"] else None,
        "vendas_fechadas": len(ganhas_periodo),
        # secundários
        "ticket_medio": para_float(divide(vendido, D(len(ganhas_periodo)))) if ganhas_periodo else None,
        "conversao": taxa_de_conversao(len(ganhas_periodo), len(perdidas)),
        "desconto_medio": para_float(D("1") - divide(eco["neg"], eco["rec"])) if eco["rec"] else None,
        "pipeline_aberto": para_float(soma(pipeline_com_valor)) if pipeline_com_valor else None,
        "vendas_abertas": len(abertas),
        "perdidas": len(perdidas),
        # financeiro — três fatos, três números
        "valor_faturado": para_float(faturado) if faturado else None,
        "valor_pago": para_float(pago) if pago else None,
        "a_receber": para_float(soma(valor_de(o, its) for o, its in a_receber_lista)) if a_receber_lista else None,
        "a_receber_quantidade": len(a_receber_lista),
        "atrasado": para_float(soma(valor_de(o, its) for o, its in atrasadas)) if atrasadas else None,
        "atrasado_quantidade": len(atrasadas),
        # análises
        "performance_vendedoras": performance,
        "top_clientes": top5,
        "concentracao_top5": concentracao_top5,
        "rentabilidade_clientes": clientes_lista,
        "mix_fornecedor": mix(mix_forn),
        "mix_familia": mix(mix_fam),
        "impacto_descontos": impacto_lista,
        "margem_x_desconto": sorted(margem_x_desconto, key=lambda x: -(x["vendido"] or 0)),
        "funil": funil,
        "aging": aging_lista,
        "motivos_perda": por_motivo_de_perda(perdidas),
        "pos_venda": {
            "aguardando_entrega": {"quantidade": len(ag_entrega),
                                   "valor": para_float(soma(valor_de(o, its) for o, its in ag_entrega)) if ag_entrega else None},
            "aguardando_pagamento": {"quantidade": len(ag_pag),
                                     "valor": para_float(soma(valor_de(o, its) for o, its in ag_pag)) if ag_pag else None},
            "atrasado": {"quantidade": len(atrasadas),
                         "valor": para_float(soma(valor_de(o, its) for o, its in atrasadas)) if atrasadas else None},
        },
        "comissao_estimada": para_float(eco["comissao"]) if eco["receita"] else None,
        "vazio": not todas_ops,
    }

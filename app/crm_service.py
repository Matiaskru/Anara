"""CRM — organizações, oportunidades, atividades e pipeline.

O que este módulo **não** faz: economia. Ele consome a cotação já aprovada nas Sessões 1 a 6
e não recalcula preço nenhum. Quando precisa de um valor comercial, lê o que a cotação já
formou.

## Três conceitos que não são o mesmo

**Cliente** é a organização — e também o prospect. Criar `Lead → Prospect → Conta` seria
burocracia: o que muda entre eles é *quanto se sabe*, não *o que são*.

**Oportunidade** é o negócio. **Cotação** é a proposta econômica. Uma oportunidade tem zero,
uma ou várias cotações, e as revisões de uma proposta continuam sendo o mesmo negócio —
contar cada revisão como item do funil faria o pipeline medir papel em vez de negócio.

**Atividade** é a próxima ação combinada. "Atrasada" não é campo: é `due_em < agora` e não
concluída, derivado na leitura. Um campo booleano só seria verdadeiro enquanto alguém
lembrasse de atualizá-lo.

## Persistir fato, derivar métrica

`valor_estimado` é palpite manual de antes da cotação; o **valor cotado** é derivado da
cotação mais recente e por isso não tem coluna; `valor_fechado` é snapshot do momento do
ganho. Persistir o valor cotado criaria uma segunda verdade que envelheceria em silêncio.
"""
from datetime import date, datetime
from typing import List, Optional, Sequence

from fastapi import HTTPException
from sqlmodel import Session, select

from app import admin_service as adm
from app import workflow_service as ws
from app.dinheiro import D, para_float
from app.models import (
    ETAPAS_LEGADAS, AtividadeComercial, AtualizacaoComercial, Cliente, Contato, Cotacao,
    EtapaOportunidade, MotivoPerda, Oportunidade, OportunidadeEtapaHistorico, StatusCotacao,
    StatusOportunidade, StatusPosVenda, Usuario,
)

#: As três etapas de uma VENDA aberta (Fase 3B). Vendido/Perdido são `status`.
ETAPAS = [e.value for e in EtapaOportunidade]
STATUS = [s.value for s in StatusOportunidade]
RASCUNHO = EtapaOportunidade.rascunho.value
ENVIADO = EtapaOportunidade.enviado.value
NEGOCIACAO = EtapaOportunidade.negociacao.value
#: Motivos de perda oferecidos na tela (o enum ainda lê o legado `FORA_DE_ESCOPO`).
MOTIVOS_PERDA = [m.value for m in MotivoPerda if m != MotivoPerda.fora_de_escopo]

#: Estados da cotação que valem como proposta apresentada ao cliente.
COTACAO_APRESENTAVEL = (StatusCotacao.emitida.value, StatusCotacao.enviada.value)


class DadoInvalido(HTTPException):
    """Erro de cadastro/fluxo, com mensagem legível — nunca stack trace."""

    def __init__(self, detalhe: str, status: int = 400):
        super().__init__(status_code=status, detail=detalhe)


# ---------------------------------------------------------------------------
# Cliente / prospect
# ---------------------------------------------------------------------------
def _limpar_cnpj(valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    so_digitos = "".join(c for c in valor if c.isdigit())
    return so_digitos or None


def cliente_por_cnpj(session: Session, cnpj: Optional[str]) -> Optional[Cliente]:
    alvo = _limpar_cnpj(cnpj)
    if not alvo:
        return None
    for c in session.exec(select(Cliente)).all():
        if _limpar_cnpj(c.cnpj_cpf) == alvo:
            return c
    return None


def criar_cliente(session: Session, *, ator: Usuario, nome: str, **campos) -> Cliente:
    """Cadastra organização ou prospect. **CNPJ não é obrigatório.**

    Exigir CNPJ para anotar uma empresa que ligou hoje travaria o CRM logo na porta. O que
    continua exigindo dado fiscal completo é **emitir cotação** — e essa validação não foi
    afrouxada em nada.

    Se o CNPJ vier e já existir, recusa com o cliente que já está lá. Nome parecido **não**
    bloqueia: "Hotel Praia" e "Hotel Praia Ltda" podem ser dois clientes de verdade, e um
    merge automático destruiria cadastro.
    """
    if not (nome or "").strip():
        raise DadoInvalido("O nome da organização é obrigatório.")
    duplicado = cliente_por_cnpj(session, campos.get("cnpj_cpf"))
    if duplicado is not None:
        raise DadoInvalido(
            f"Cliente já cadastrado com este CNPJ: {duplicado.nome} (#{duplicado.id}).",
            status=409)

    permitidos = ("cnpj_cpf", "cidade_uf", "telefone", "email", "contato_nome",
                  "departamento", "finalidade", "nome_fantasia", "segmento", "site",
                  "origem", "observacoes_comerciais")
    cliente = Cliente(nome=nome.strip(),
                      **{k: v for k, v in campos.items() if k in permitidos and v})
    session.add(cliente)
    session.flush()
    adm.registrar(session, ator=ator, acao="CREATE_CLIENT", entidade="Cliente",
                  entidade_id=cliente.id, escopo=cliente.nome, origem="crm")
    return cliente


def clientes_parecidos(session: Session, nome: str, limite: int = 5) -> List[Cliente]:
    """Sugestão de possíveis duplicatas — para **avisar**, nunca para bloquear."""
    alvo = (nome or "").strip().lower()
    if len(alvo) < 4:
        return []
    achados = [c for c in session.exec(select(Cliente)).all()
               if alvo in (c.nome or "").lower() or (c.nome or "").lower() in alvo]
    return achados[:limite]


def dados_fiscais_faltando(cliente: Cliente) -> List[str]:
    """O que ainda falta para este cliente poder receber uma cotação emitida.

    Existe para a UX explicar a pendência **antes** de o vendedor montar a proposta inteira e
    esbarrar num erro obscuro na hora de emitir.
    """
    faltando = []
    if not (cliente.cidade_uf or "").strip():
        faltando.append("cidade/UF — define o destino fiscal da operação")
    if not (cliente.finalidade or "").strip():
        faltando.append("finalidade da operação (revenda, uso e consumo…)")
    return faltando


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------
def criar_contato(session: Session, *, ator: Usuario, cliente_id: int, nome: str,
                  principal: bool = False, **campos) -> Contato:
    cliente = session.get(Cliente, cliente_id)
    if cliente is None:
        raise DadoInvalido("Cliente não encontrado.", status=404)
    if not (nome or "").strip():
        raise DadoInvalido("O nome do contato é obrigatório.")
    if principal:
        # Só um principal por cliente — os demais continuam, apenas deixam de ser o principal.
        for outro in contatos_de(session, cliente_id):
            if outro.principal:
                outro.principal = False
                session.add(outro)
    contato = Contato(cliente_id=cliente_id, nome=nome.strip(), principal=principal,
                      criado_por=ator.email,
                      **{k: v for k, v in campos.items()
                         if k in ("cargo", "email", "telefone", "observacao") and v})
    session.add(contato)
    session.flush()
    return contato


def contatos_de(session: Session, cliente_id: int, apenas_ativos: bool = False):
    linhas = session.exec(select(Contato)
                          .where(Contato.cliente_id == cliente_id)
                          .order_by(Contato.principal.desc(), Contato.nome)).all()
    return [c for c in linhas if c.ativo] if apenas_ativos else linhas


def contato_principal(session: Session, cliente_id: int) -> Optional[Contato]:
    ativos = contatos_de(session, cliente_id, apenas_ativos=True)
    return next((c for c in ativos if c.principal), ativos[0] if ativos else None)


# ---------------------------------------------------------------------------
# Oportunidades
# ---------------------------------------------------------------------------
def criar_oportunidade(session: Session, *, ator: Usuario, cliente_id: int, titulo: str,
                       responsavel_id: Optional[int] = None,
                       etapa: str = EtapaOportunidade.rascunho.value,
                       origem: Optional[str] = None, origem_detalhe: Optional[str] = None,
                       valor_estimado=None, data_prevista_fechamento: Optional[date] = None,
                       descricao: Optional[str] = None) -> Oportunidade:
    cliente = session.get(Cliente, cliente_id)
    if cliente is None:
        raise DadoInvalido("Cliente não encontrado.", status=404)
    if not (titulo or "").strip():
        raise DadoInvalido("A oportunidade precisa de um título.")
    if etapa not in ETAPAS:
        raise DadoInvalido(f"Etapa '{etapa}' não existe. Válidas: {', '.join(ETAPAS)}."
                           + (" Etapa do funil anterior não é aceita em venda nova."
                              if etapa in ETAPAS_LEGADAS else ""))
    responsavel = _validar_responsavel(session, responsavel_id)

    op = Oportunidade(
        cliente_id=cliente_id, titulo=titulo.strip(), descricao=descricao or None,
        responsavel_id=responsavel.id if responsavel else None, etapa=etapa,
        status=StatusOportunidade.aberta.value, origem=origem or None,
        origem_detalhe=origem_detalhe or None,
        valor_estimado=para_float(D(valor_estimado)) if valor_estimado not in (None, "") else None,
        data_prevista_fechamento=data_prevista_fechamento, criado_por=ator.email)
    session.add(op)
    session.flush()
    session.add(OportunidadeEtapaHistorico(
        oportunidade_id=op.id, etapa_anterior=None, etapa_nova=etapa,
        ator_id=ator.id, ator_email=ator.email, observacao="criação"))
    adm.registrar(session, ator=ator, acao="CREATE_OPPORTUNITY", entidade="Oportunidade",
                  entidade_id=op.id, escopo=f"{cliente.nome} · {op.titulo}",
                  depois=etapa, origem="crm")
    return op


def _validar_responsavel(session: Session, responsavel_id: Optional[int]
                         ) -> Optional[Usuario]:
    if not responsavel_id:
        return None
    u = session.get(Usuario, responsavel_id)
    if u is None:
        raise DadoInvalido("Responsável não encontrado.", status=404)
    if not u.ativo:
        # Oportunidade antiga de alguém desativado continua existindo; o que não se faz é
        # entregar negócio novo a quem não entra mais no sistema.
        raise DadoInvalido("Não é possível atribuir uma oportunidade a um usuário inativo.")
    return u


def mudar_etapa(session: Session, op: Oportunidade, nova: str, *, ator: Usuario,
                observacao: Optional[str] = None) -> Oportunidade:
    """Move o negócio no funil. **Aceita avançar, voltar e pular** — e registra tudo.

    Diferente do workflow da cotação, aqui não há máquina de estados rígida: uma qualificação
    pode virar negociação no mesmo telefonema, e um negócio esfria e volta. Travar isso
    inventaria uma sequência comercial que não corresponde a como se vende.
    """
    if nova not in ETAPAS:
        raise DadoInvalido(f"Etapa '{nova}' não existe. Válidas: {', '.join(ETAPAS)}."
                           + (" Etapa do funil anterior não é aceita." if nova in ETAPAS_LEGADAS
                              else ""))
    if op.status != StatusOportunidade.aberta.value:
        raise DadoInvalido(
            f"A oportunidade está {op.status} — reabra antes de mover no funil.", status=409)
    if nova == op.etapa:
        return op

    anterior = op.etapa
    op.etapa = nova
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    session.add(OportunidadeEtapaHistorico(
        oportunidade_id=op.id, etapa_anterior=anterior, etapa_nova=nova,
        ator_id=ator.id, ator_email=ator.email, observacao=observacao))
    adm.registrar(session, ator=ator, acao="CHANGE_STAGE", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=anterior, depois=nova,
                  motivo=observacao, origem="crm")
    return op


def atribuir(session: Session, op: Oportunidade, responsavel_id: Optional[int], *,
             ator: Usuario) -> Oportunidade:
    responsavel = _validar_responsavel(session, responsavel_id)
    antes = op.responsavel_id
    op.responsavel_id = responsavel.id if responsavel else None
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    adm.registrar(session, ator=ator, acao="ASSIGN_OWNER", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=str(antes),
                  depois=str(op.responsavel_id), origem="crm")
    return op


def historico_de_etapas(session: Session, oportunidade_id: int):
    return session.exec(select(OportunidadeEtapaHistorico)
                        .where(OportunidadeEtapaHistorico.oportunidade_id == oportunidade_id)
                        .order_by(OportunidadeEtapaHistorico.ocorrido_em,
                                  OportunidadeEtapaHistorico.id)).all()


# ---------------------------------------------------------------------------
# Oportunidade ↔ cotação
# ---------------------------------------------------------------------------
def cotacoes_de(session: Session, oportunidade_id: int) -> List[Cotacao]:
    return session.exec(select(Cotacao)
                        .where(Cotacao.oportunidade_id == oportunidade_id)
                        .order_by(Cotacao.criado_em, Cotacao.id)).all()


def vincular_cotacao(session: Session, op: Oportunidade, cotacao: Cotacao, *,
                     ator: Usuario) -> Cotacao:
    """Liga uma cotação à oportunidade — conferindo que é o **mesmo cliente**.

    Sem essa checagem, trocar o ID no formulário juntaria a proposta do Hotel A ao negócio
    do Hotel B. O resultado não seria um erro visível: seria um funil que parece certo e
    aponta para o cliente errado.
    """
    if cotacao.cliente_id != op.cliente_id:
        raise DadoInvalido(
            "Esta cotação é de outro cliente. Vincular negócios de clientes diferentes "
            "deixaria a genealogia comercial inconsistente.", status=409)
    if cotacao.oportunidade_id and cotacao.oportunidade_id != op.id:
        raise DadoInvalido("Esta cotação já pertence a outra oportunidade.", status=409)
    cotacao.oportunidade_id = op.id
    session.add(cotacao)
    adm.registrar(session, ator=ator, acao="LINK_QUOTE", entidade="Cotacao",
                  entidade_id=cotacao.id, escopo=op.titulo,
                  depois=f"oportunidade #{op.id}", origem="crm")
    return cotacao


def cotacao_mais_recente(session: Session, oportunidade_id: int) -> Optional[Cotacao]:
    """A cotação/revisão comercial mais relevante — regra determinística.

    Revisão mais alta, entre as não canceladas. **Não** é "a vencedora": enquanto o negócio
    não fecha, nenhuma proposta é a escolhida, e chamá-la assim adiantaria uma decisão que é
    do cliente.
    """
    candidatas = [c for c in cotacoes_de(session, oportunidade_id)
                  if c.status != StatusCotacao.cancelada.value]
    if not candidatas:
        return None
    return sorted(candidatas, key=lambda c: (c.revisao or 1, c.id or 0))[-1]


def valor_cotado(session: Session, oportunidade_id: int) -> Optional[float]:
    """Valor comercial da cotação mais recente. **Derivado**, nunca persistido.

    Persistir isto criaria uma segunda verdade que envelheceria em silêncio na primeira
    revisão que ninguém lembrasse de propagar.
    """
    cot = cotacao_mais_recente(session, oportunidade_id)
    if cot is None:
        return None
    itens = ws.itens_de(session, cot.id)
    if not itens:
        return None
    from app import workflow as wf
    return wf.resumo_comercial(itens)["total_negociado"]


# ---------------------------------------------------------------------------
# Fechamento
# ---------------------------------------------------------------------------
def marcar_ganha(session: Session, op: Oportunidade, cotacao_id: int, *,
                 ator: Usuario) -> Oportunidade:
    """Fecha o negócio — mas só se a proposta puder virar compromisso firme.

    Aqui a Sessão 6 é consultada de verdade: `validar_compromisso_firme` bloqueia custo
    `ESTIMADO` não confirmado, `REVALIDAR` não reconfirmado, `A_COTAR`, frete CIF
    irresolvido e exceção sem aprovação. Marcar ganho por cima disso seria registrar como
    fechado um negócio cujo preço ninguém pode sustentar.

    **Não cria pedido nem PO.** GANHA é resultado comercial no CRM, e nada mais.
    """
    if op.status == StatusOportunidade.ganha.value:
        return op                                   # idempotente
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is None:
        raise DadoInvalido("Cotação não encontrada.", status=404)
    if cotacao.oportunidade_id != op.id:
        raise DadoInvalido("Esta cotação não pertence a esta oportunidade.", status=409)
    if cotacao.status not in COTACAO_APRESENTAVEL:
        raise DadoInvalido(
            f"A cotação está em '{cotacao.status}'. Só uma proposta emitida ou enviada pode "
            "ser a vencedora — antes disso não há documento que o cliente tenha aceitado.",
            status=409)

    compromisso = ws.validar_compromisso_firme(session, cotacao)
    if not compromisso.pode:
        raise DadoInvalido(
            "Esta proposta ainda não pode virar compromisso firme: "
            + " ".join(compromisso.impedimentos), status=409)

    from app import workflow as wf
    total = wf.resumo_comercial(ws.itens_de(session, cotacao.id))["total_negociado"]

    op.status = StatusOportunidade.ganha.value
    op.won_em = datetime.utcnow()
    op.won_por = ator.email
    op.cotacao_vencedora_id = cotacao.id
    op.cotacao_vencedora_fingerprint = cotacao.fingerprint
    # Snapshot: se a tabela de preços mudar amanhã, o valor fechado não muda.
    op.valor_fechado = total
    op.atualizado_em = op.won_em
    # Pós-venda (Fase 3B): vendido → aguardando entrega. Só existe a partir daqui.
    op.status_pos_venda = StatusPosVenda.aguardando_entrega.value
    session.add(op)
    adm.registrar(session, ator=ator, acao="MARK_WON", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo,
                  depois=f"GANHA · cotação {cotacao.numero or cotacao.id} r{cotacao.revisao}",
                  origem="crm",
                  detalhe={"cotacao_id": cotacao.id, "valor_fechado": total,
                           "fingerprint": cotacao.fingerprint})
    return op


def marcar_perdida(session: Session, op: Oportunidade, *, ator: Usuario, motivo: str,
                   comentario: Optional[str] = None) -> Oportunidade:
    """Fecha como perdida. **Motivo estruturado é obrigatório.**

    Sem motivo padronizado, a Sessão 8 não conseguiria responder "por que perdemos" — e
    texto livre não agrega.
    """
    if motivo not in {m.value for m in MotivoPerda}:
        raise DadoInvalido(
            "Informe um motivo de perda válido: "
            + ", ".join(m.value for m in MotivoPerda))
    if op.status == StatusOportunidade.perdida.value:
        return op
    op.status = StatusOportunidade.perdida.value
    op.lost_em = datetime.utcnow()
    op.lost_por = ator.email
    op.motivo_perda = motivo
    op.comentario_perda = (comentario or "").strip() or None
    op.atualizado_em = op.lost_em
    session.add(op)
    adm.registrar(session, ator=ator, acao="MARK_LOST", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, depois=f"PERDIDA · {motivo}",
                  motivo=op.comentario_perda, origem="crm")
    return op


def reabrir(session: Session, op: Oportunidade, *, ator: Usuario, etapa: Optional[str] = None,
            motivo: Optional[str] = None) -> Oportunidade:
    """Reabre uma oportunidade perdida. Ato **explícito**, e o evento anterior fica.

    Ganha é terminal nesta versão: corrigir um fechamento errado é ação administrativa
    própria, não edição casual de quem passou pela tela.
    """
    if op.status == StatusOportunidade.aberta.value:
        return op
    if op.status == StatusOportunidade.ganha.value:
        raise DadoInvalido(
            "Oportunidade ganha não se reabre por aqui. Corrigir um fechamento é ação "
            "administrativa própria — e ainda não existe.", status=409)
    # Sem etapa pedida, volta para a **última etapa aberta antes da perda** — `op.etapa` não
    # é apagada na perda, então é determinística. Etapa do funil anterior não é reoferecida:
    # cai em RASCUNHO, e o histórico diz que caiu.
    destino = etapa or (op.etapa if op.etapa in ETAPAS else RASCUNHO)
    if destino not in ETAPAS:
        raise DadoInvalido(f"Etapa '{destino}' não existe. Válidas: {', '.join(ETAPAS)}.")

    op.status = StatusOportunidade.aberta.value
    op.atualizado_em = datetime.utcnow()
    anterior = op.etapa
    op.etapa = destino
    # O registro da perda NÃO é apagado: lost_em, lost_por e motivo continuam, porque a
    # oportunidade realmente foi perdida um dia, e isso é história.
    session.add(op)
    session.add(OportunidadeEtapaHistorico(
        oportunidade_id=op.id, etapa_anterior=anterior, etapa_nova=destino,
        ator_id=ator.id, ator_email=ator.email,
        observacao=f"reabertura: {motivo}" if motivo else "reabertura"))
    adm.registrar(session, ator=ator, acao="REOPEN_OPPORTUNITY", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes="PERDIDA", depois="ABERTA",
                  motivo=motivo, origem="crm")
    return op


# ---------------------------------------------------------------------------
# Atualização comercial (Fase 3B) — append-only
# ---------------------------------------------------------------------------
def registrar_atualizacao(session: Session, op: Oportunidade, *, ator: Usuario, texto: str,
                          proxima_atividade: Optional[dict] = None) -> AtualizacaoComercial:
    """"Registrar atualização": o que aconteceu, dito por quem viu. Nunca editada.

    `proxima_atividade`, opcional, cria a atividade junto (`{"titulo", "tipo", "due_em"}`)
    — a mesma `AtividadeComercial` de sempre, não uma tabela nova.
    """
    if not (texto or "").strip():
        raise DadoInvalido("A atualização precisa de texto.")
    nota = AtualizacaoComercial(oportunidade_id=op.id, autor_id=ator.id, autor_email=ator.email,
                                texto=texto.strip())
    session.add(nota)
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    session.flush()
    adm.registrar(session, ator=ator, acao="COMMERCIAL_UPDATE", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, depois=texto.strip()[:200], origem="crm",
                  detalhe={"atualizacao_id": nota.id})
    if proxima_atividade and (proxima_atividade.get("titulo") or "").strip():
        criar_atividade(session, ator=ator, titulo=proxima_atividade["titulo"],
                        oportunidade_id=op.id, cliente_id=op.cliente_id,
                        tipo=proxima_atividade.get("tipo") or "FOLLOW_UP",
                        due_em=proxima_atividade.get("due_em"))
    return nota


def atualizacoes_de(session: Session, oportunidade_id: int) -> List[AtualizacaoComercial]:
    return session.exec(select(AtualizacaoComercial)
                        .where(AtualizacaoComercial.oportunidade_id == oportunidade_id)
                        .order_by(AtualizacaoComercial.criado_em.desc(),
                                  AtualizacaoComercial.id.desc())).all()


# ---------------------------------------------------------------------------
# Avanço automático — só o óbvio (Fase 3B, §7)
# ---------------------------------------------------------------------------
def avancar_por_envio(session: Session, cotacao: Cotacao, *, ator: Optional[Usuario],
                      evento: str) -> Optional[Oportunidade]:
    """Cotação emitida/enviada: venda em RASCUNHO vai para ENVIADO. Só isso.

    Venda já em NEGOCIACAO **não volta** para ENVIADO por causa de uma revisão nova;
    NEGOCIACAO, VENDIDO e PERDIDO nunca são marcados sozinhos. O avanço fica no histórico
    com o ator identificado como automático.
    """
    if not cotacao.oportunidade_id:
        return None
    op = session.get(Oportunidade, cotacao.oportunidade_id)
    if op is None or op.status != StatusOportunidade.aberta.value or op.etapa != RASCUNHO:
        return op
    anterior = op.etapa
    op.etapa = ENVIADO
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    session.add(OportunidadeEtapaHistorico(
        oportunidade_id=op.id, etapa_anterior=anterior, etapa_nova=ENVIADO,
        ator_id=getattr(ator, "id", None),
        ator_email=f"sistema (automático · {getattr(ator, 'email', None) or '—'})",
        observacao=f"AUTOMÁTICO: cotação {cotacao.numero or cotacao.id} r{cotacao.revisao} "
                   f"{evento}"))
    adm.registrar(session, ator=ator, acao="CHANGE_STAGE", entidade="Oportunidade",
                  entidade_id=op.id, escopo=op.titulo, antes=anterior, depois=ENVIADO,
                  motivo=f"automático: cotação {evento}", origem="crm:automatico")
    return op


def cotacoes_com_venda(session: Session) -> dict:
    """`{cotacao_id: Oportunidade}` para a lista de cotações mostrar a venda vinculada."""
    ops = {o.id: o for o in session.exec(select(Oportunidade)).all()}
    return {c.id: ops.get(c.oportunidade_id)
            for c in session.exec(select(Cotacao)).all() if c.oportunidade_id}


def status_comercial(op: Oportunidade) -> str:
    """O rótulo único da tela de Vendas — ver `rotulos.venda`."""
    from app import rotulos
    return rotulos.venda(op.status, op.etapa)


# ---------------------------------------------------------------------------
# Atividades
# ---------------------------------------------------------------------------
def criar_atividade(session: Session, *, ator: Usuario, titulo: str,
                    oportunidade_id: Optional[int] = None,
                    cliente_id: Optional[int] = None, contato_id: Optional[int] = None,
                    responsavel_id: Optional[int] = None, tipo: str = "FOLLOW_UP",
                    due_em: Optional[datetime] = None,
                    observacao: Optional[str] = None) -> AtividadeComercial:
    if not (titulo or "").strip():
        raise DadoInvalido("A atividade precisa de um título.")
    if oportunidade_id and session.get(Oportunidade, oportunidade_id) is None:
        raise DadoInvalido("Oportunidade não encontrada.", status=404)
    atividade = AtividadeComercial(
        oportunidade_id=oportunidade_id, cliente_id=cliente_id, contato_id=contato_id,
        responsavel_id=responsavel_id or ator.id, tipo=tipo, titulo=titulo.strip(),
        observacao=observacao or None, due_em=due_em, criado_por=ator.email)
    session.add(atividade)
    session.flush()
    adm.registrar(session, ator=ator, acao="CREATE_ACTIVITY", entidade="AtividadeComercial",
                  entidade_id=atividade.id, escopo=titulo, origem="crm")
    return atividade


def concluir_atividade(session: Session, atividade: AtividadeComercial, *,
                       ator: Usuario) -> AtividadeComercial:
    """Idempotente. A atividade concluída **permanece** — é o histórico do que foi feito."""
    if atividade.concluida_em:
        return atividade
    atividade.concluida_em = datetime.utcnow()
    atividade.concluida_por = ator.email
    session.add(atividade)
    adm.registrar(session, ator=ator, acao="COMPLETE_ACTIVITY",
                  entidade="AtividadeComercial", entidade_id=atividade.id,
                  escopo=atividade.titulo, origem="crm")
    return atividade


def atividades_de(session: Session, *, oportunidade_id: Optional[int] = None,
                  cliente_id: Optional[int] = None, responsavel_id: Optional[int] = None,
                  pendentes: Optional[bool] = None, limite: int = 200):
    q = select(AtividadeComercial)
    if oportunidade_id is not None:
        q = q.where(AtividadeComercial.oportunidade_id == oportunidade_id)
    if cliente_id is not None:
        q = q.where(AtividadeComercial.cliente_id == cliente_id)
    if responsavel_id is not None:
        q = q.where(AtividadeComercial.responsavel_id == responsavel_id)
    if pendentes is True:
        q = q.where(AtividadeComercial.concluida_em.is_(None))
    elif pendentes is False:
        q = q.where(AtividadeComercial.concluida_em.is_not(None))
    linhas = session.exec(q.limit(limite)).all()
    # `due_em` nulo vai para o fim: atividade sem data não disputa a próxima posição.
    return sorted(linhas, key=lambda a: (a.due_em is None, a.due_em or datetime.max,
                                         a.id or 0))


def proxima_atividade(session: Session, oportunidade_id: int
                      ) -> Optional[AtividadeComercial]:
    """A pendente mais próxima. `None` quando não há — e isso a UI mostra explicitamente."""
    pendentes = atividades_de(session, oportunidade_id=oportunidade_id, pendentes=True)
    return pendentes[0] if pendentes else None


def esta_atrasada(atividade: AtividadeComercial, agora: Optional[datetime] = None) -> bool:
    """Derivado na leitura — não há coluna nem job. Ver o docstring de `AtividadeComercial`."""
    if atividade is None or atividade.concluida_em or not atividade.due_em:
        return False
    return atividade.due_em < (agora or datetime.utcnow())


# ---------------------------------------------------------------------------
# Consultas do pipeline
# ---------------------------------------------------------------------------
def listar_oportunidades(session: Session, *, etapa: Optional[str] = None,
                         status: Optional[str] = None,
                         responsavel_id: Optional[int] = None,
                         cliente_id: Optional[int] = None, origem: Optional[str] = None,
                         busca: Optional[str] = None,
                         sem_proxima_atividade: bool = False,
                         limite: int = 200, offset: int = 0) -> List[Oportunidade]:
    q = select(Oportunidade)
    if etapa:
        q = q.where(Oportunidade.etapa == etapa)
    if status:
        q = q.where(Oportunidade.status == status)
    if responsavel_id is not None:
        q = q.where(Oportunidade.responsavel_id == responsavel_id)
    if cliente_id is not None:
        q = q.where(Oportunidade.cliente_id == cliente_id)
    if origem:
        q = q.where(Oportunidade.origem == origem)
    linhas = session.exec(q.order_by(Oportunidade.criado_em.desc())
                          .limit(limite).offset(offset)).all()

    if busca:
        alvo = busca.strip().lower()
        nomes = {c.id: (c.nome or "").lower()
                 for c in session.exec(select(Cliente)).all()}
        linhas = [o for o in linhas
                  if alvo in (o.titulo or "").lower()
                  or alvo in nomes.get(o.cliente_id, "")]
    if sem_proxima_atividade:
        linhas = [o for o in linhas if proxima_atividade(session, o.id) is None]
    return linhas


def cartao(session: Session, op: Oportunidade) -> dict:
    """O que o pipeline mostra de uma oportunidade.

    Leva **valor comercial** — preço e total já fazem parte do dia a dia do vendedor. Não
    leva custo, margem, lucro nem markup: a confidencialidade da Sessão 4 não muda porque o
    dado passou a aparecer num card.
    """
    cliente = session.get(Cliente, op.cliente_id)
    responsavel = session.get(Usuario, op.responsavel_id) if op.responsavel_id else None
    prox = proxima_atividade(session, op.id)
    cot = cotacao_mais_recente(session, op.id)
    return {
        "id": op.id, "titulo": op.titulo, "etapa": op.etapa, "status": op.status,
        "cliente": getattr(cliente, "nome", None), "cliente_id": op.cliente_id,
        "responsavel": getattr(responsavel, "nome", None),
        "responsavel_id": op.responsavel_id,
        "valor_estimado": op.valor_estimado,
        "valor_cotado": valor_cotado(session, op.id),
        "valor_fechado": op.valor_fechado,
        "previsao": op.data_prevista_fechamento,
        "proxima_atividade": (
            {"id": prox.id, "titulo": prox.titulo, "due_em": prox.due_em,
             "atrasada": esta_atrasada(prox)} if prox else None),
        "cotacao": ({"id": cot.id, "numero": cot.numero, "revisao": cot.revisao,
                     "status": cot.status} if cot else None),
        "origem": op.origem,
        # Fase 3B — a lista de Vendas
        "status_comercial": status_comercial(op),
        "atualizado_em": op.atualizado_em or op.criado_em,
        "status_pos_venda": op.status_pos_venda,
        "valor_atual": (op.valor_fechado if op.status == StatusOportunidade.ganha.value
                        else (valor_cotado(session, op.id) or op.valor_estimado)),
    }


def board(session: Session, **filtros) -> dict:
    """Oportunidades abertas agrupadas por etapa."""
    filtros.setdefault("status", StatusOportunidade.aberta.value)
    abertas = listar_oportunidades(session, **filtros)
    colunas = {e: [] for e in ETAPAS}
    for op in abertas:
        colunas.setdefault(op.etapa, []).append(cartao(session, op))
    return colunas


def timeline(session: Session, op: Oportunidade) -> List[dict]:
    """A história do negócio, em ordem — composta das fontes que já existem.

    Não há event-sourcing próprio: criação, etapas, atividades, cotações e fechamento já
    estão gravados cada um no seu lugar, e duplicá-los num log paralelo criaria duas
    versões da mesma história.
    """
    eventos = [{"quando": op.criado_em, "tipo": "criacao",
                "texto": f"Venda criada por {op.criado_por or '—'}"}]
    from app import rotulos
    for h in historico_de_etapas(session, op.id):
        if h.etapa_anterior:
            reabertura = (h.observacao or "").startswith("reabertura")
            automatico = (h.ator_email or "").startswith("sistema (automático")
            observacao = (h.observacao or "")
            if automatico:
                # "AUTOMÁTICO: cotação X r1 emitida" → "automático, ao emitir a cotação X"
                observacao = observacao.replace("AUTOMÁTICO: ", "").strip()
                ator = f"automático — {observacao}" if observacao else "automático"
                observacao = ""
            else:
                ator = h.ator_email or "—"
            eventos.append({"quando": h.ocorrido_em,
                            "tipo": "reabertura" if reabertura else "etapa",
                            "texto": (f"{'Venda reaberta: ' if reabertura else ''}"
                                      f"{rotulos.etapa_venda(h.etapa_anterior)} → "
                                      f"{rotulos.etapa_venda(h.etapa_nova)} "
                                      f"({ator})"
                                      + (f" — {observacao}" if observacao and not reabertura
                                         else ""))})
    for n in atualizacoes_de(session, op.id):
        eventos.append({"quando": n.criado_em, "tipo": "atualizacao",
                        "texto": f"{n.texto} ({n.autor_email or '—'})"})
    for a in atividades_de(session, oportunidade_id=op.id):
        # rótulo humano do tipo ("Follow-up", "Ligação"), nunca o código do enum
        eventos.append({"quando": a.criado_em, "tipo": "atividade",
                        "texto": f"{rotulos.atividade(a.tipo)}: {a.titulo}"
                                 + (f" — para {a.due_em:%d/%m %H:%M}" if a.due_em else "")})
        if a.concluida_em:
            eventos.append({"quando": a.concluida_em, "tipo": "atividade",
                            "texto": f"Concluída: {a.titulo} ({a.concluida_por or '—'})"})
    from app.models import AprovacaoCotacao
    for c in cotacoes_de(session, op.id):
        eventos.append({"quando": c.criado_em, "tipo": "cotacao",
                        "texto": (f"Revisão {c.revisao} da cotação {c.numero or c.id} criada"
                                  if (c.revisao or 1) > 1
                                  else f"Cotação {c.numero or c.id} criada")})
        for a in session.exec(select(AprovacaoCotacao)
                              .where(AprovacaoCotacao.cotacao_id == c.id)).all():
            if a.decidido_em:
                eventos.append({"quando": a.decidido_em, "tipo": "aprovacao",
                                "texto": (f"Exceção comercial "
                                          f"{'aprovada' if a.status == 'APROVADA' else ('rejeitada' if a.status == 'REJEITADA' else a.status.lower())} "
                                          f"na cotação {c.numero or c.id} r{c.revisao} "
                                          f"({a.aprovador_email or '—'})")})
        if c.issued_em:
            eventos.append({"quando": c.issued_em, "tipo": "cotacao",
                            "texto": f"Cotação {c.numero or c.id} r{c.revisao} emitida"})
        if c.sent_em:
            eventos.append({"quando": c.sent_em, "tipo": "cotacao",
                            "texto": f"Cotação {c.numero or c.id} r{c.revisao} enviada"})
    if op.won_em:
        eventos.append({"quando": op.won_em, "tipo": "ganho",
                        "texto": f"Venda fechada — marcada como vendida por {op.won_por or '—'}"})
    if op.lost_em:
        eventos.append({"quando": op.lost_em, "tipo": "perda",
                        "texto": f"Venda perdida ({rotulos.motivo_perda(op.motivo_perda)}) "
                                 f"por {op.lost_por or '—'}"})
    # pós-venda (Fase 3B): os fatos moram nas colunas; a timeline só os lê
    if op.entregue_em:
        eventos.append({"quando": op.entregue_em, "tipo": "entrega", "texto": "Entrega registrada"})
    if op.faturado_em:
        eventos.append({"quando": datetime.combine(op.faturado_em, datetime.min.time()),
                        "tipo": "faturamento",
                        "texto": f"Faturamento registrado"
                                 + (f" — documento {op.numero_documento_fiscal}"
                                    if op.numero_documento_fiscal else "")})
    if op.pago_em:
        eventos.append({"quando": op.pago_em, "tipo": "pagamento", "texto": "Pagamento registrado"})
    return sorted([e for e in eventos if e["quando"]], key=lambda e: e["quando"])

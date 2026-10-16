"""CRM de relacionamento (15/10/2026): contatos, visitas, acompanhamentos e perfil de produtos.

Constrói sobre o CRM da Fase 3B, sem duplicar: o contato é o `Contato` que já existia (agenda do
cliente) e o acompanhamento é a `AtividadeComercial` (follow-up) — aqui só se acrescenta o vínculo
com a visita, o reagendamento e a regra de quem pode agir. Tabelas novas só onde não havia nada:
`VisitaCliente` (+ participantes) e `ClienteProdutoUtilizado`.

Regras:
* **Quem vê o quê não muda** (Sessão 4): todo usuário comercial vê todos os clientes; nada aqui
  esconde cliente de vendedora. O que este módulo restringe é **agir sobre registro de outra
  pessoa**: editar visita só o autor (ou OWNER/ADMIN); concluir, reagendar e editar acompanhamento
  só o responsável, quem criou ou OWNER/ADMIN. A recusa é do servidor.
* **Nada é apagado.** Contato e produto utilizado são inativados; visita é retirada do histórico
  com `removida_em`; acompanhamento concluído permanece.
* **Produto utilizado ≠ cotado ≠ vendido.** Nada aqui infere uso a partir de visita ou cotação, nem
  cria SKU, nem toca em produto, custo ou preço.
* Datas comerciais no calendário de São Paulo (`hoje_sp`). O prazo do acompanhamento é gravado às
  23:59 do dia previsto, para não ficar "atrasado" no próprio dia nas telas que comparam `due_em`
  com o relógio.
* Nenhuma função aqui devolve número econômico.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from sqlmodel import Session, select

from app import admin_service as adm
from app import crm_service as crm
from app import rotulos
from app.crm_service import DadoInvalido
from app.models import (
    ASSUNTOS_VISITA, PAPEIS_ADMINISTRATIVOS, AtividadeComercial, Cliente, ClienteProdutoUtilizado,
    Contato, Produto, Usuario, VisitaCliente, VisitaParticipante,
)

ORIGEM = "crm-relacionamento"
ROTULO_ASSUNTO = dict(ASSUNTOS_VISITA)

#: Composições aceitas no perfil de produtos — o vocabulário da busca. "Outra" leva o detalhe
#: nas observações. Os rótulos são o próprio valor gravado (legível no banco e no filtro).
COMPOSICOES = ("100% algodão", "90% algodão / 10% poliéster", "70% algodão / 30% poliéster",
               "60% algodão / 40% poliéster", "50% algodão / 50% poliéster", "100% poliéster", "Outra")
#: Tamanho comercial da cama/peça; a medida exata vai em `medidas` (texto livre, ex.: 2,80x2,90).
TAMANHOS = ("Solteiro", "Solteiro King", "Casal", "Queen", "King", "Super King", "Outro")
#: composição do catálogo (cotton_pct) → vocabulário acima; só correspondência exata
_COMPOSICAO_POR_ALGODAO = {1.0: COMPOSICOES[0], 0.9: COMPOSICOES[1], 0.7: COMPOSICOES[2],
                           0.6: COMPOSICOES[3], 0.5: COMPOSICOES[4], 0.0: COMPOSICOES[5]}

try:   # o Brasil não tem horário de verão desde 2019: sem a base de fusos, UTC−3 é o mesmo relógio
    from zoneinfo import ZoneInfo
    FUSO_SP = ZoneInfo("America/Sao_Paulo")
except Exception:   # pragma: no cover — imagem sem tzdata
    FUSO_SP = timezone(timedelta(hours=-3))


def hoje_sp(agora: Optional[datetime] = None) -> date:
    """O dia comercial em São Paulo. `agora` (UTC, sem fuso) é para teste."""
    base = (agora.replace(tzinfo=timezone.utc) if agora else datetime.now(timezone.utc))
    return base.astimezone(FUSO_SP).date()


def prazo_do_dia(dia: date) -> datetime:
    return datetime.combine(dia, time(23, 59))


# ---------------------------------------------------------------------------
# Permissões de ação (a visibilidade continua a da Sessão 4)
# ---------------------------------------------------------------------------
def eh_admin(usuario: Optional[Usuario]) -> bool:
    return bool(usuario) and getattr(usuario, "papel", None) in PAPEIS_ADMINISTRATIVOS


def pode_editar_visita(usuario: Usuario, visita: VisitaCliente) -> bool:
    return eh_admin(usuario) or (visita.criado_por_id is not None and visita.criado_por_id == usuario.id)


def pode_operar_acompanhamento(usuario: Usuario, atividade: AtividadeComercial) -> bool:
    return (eh_admin(usuario) or atividade.responsavel_id == usuario.id
            or (atividade.criado_por and atividade.criado_por == usuario.email))


def _exigir(condicao: bool, mensagem: str, status: int = 403):
    if not condicao:
        raise DadoInvalido(mensagem, status=status)


def _cliente(session: Session, cliente_id: int) -> Cliente:
    cliente = session.get(Cliente, cliente_id)
    if cliente is None:
        raise DadoInvalido("Cliente não encontrado.", status=404)
    return cliente


def _texto(valor) -> Optional[str]:
    valor = (valor or "").strip() if isinstance(valor, str) else valor
    return valor or None


def _inteiro(valor, campo: str) -> Optional[int]:
    if valor in (None, ""):
        return None
    try:
        n = int(str(valor).strip())
    except ValueError:
        raise DadoInvalido(f"{campo}: informe um número inteiro.")
    if n <= 0:
        raise DadoInvalido(f"{campo}: precisa ser maior que zero.")
    return n


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------
CAMPOS_CONTATO = ("nome", "cargo", "email", "telefone", "whatsapp", "observacao")


def criar_contato(session: Session, *, ator: Usuario, cliente_id: int, nome: str,
                  principal: bool = False, **campos) -> Contato:
    """O cadastro da Fase 3B (`crm.criar_contato`) + WhatsApp."""
    contato = crm.criar_contato(session, ator=ator, cliente_id=cliente_id, nome=nome, principal=principal,
                                **{k: v for k, v in campos.items() if k != "whatsapp"})
    if _texto(campos.get("whatsapp")):
        contato.whatsapp = _texto(campos["whatsapp"])
        session.add(contato)
    adm.registrar(session, ator=ator, acao="CREATE_CONTACT", entidade="Contato", entidade_id=contato.id,
                  escopo=f"cliente {cliente_id}: {contato.nome}", origem=ORIGEM)
    return contato


def editar_contato(session: Session, *, ator: Usuario, contato: Contato, principal: Optional[bool] = None,
                   **campos) -> Contato:
    """Corrige os dados do contato. O cliente do contato **não** muda: contato de outro cliente é
    outro contato (o histórico de visitas aponta para a pessoa naquela empresa)."""
    if "nome" in campos:
        _exigir(bool(_texto(campos["nome"])), "O nome do contato é obrigatório.", 400)
    antes = {k: getattr(contato, k) for k in CAMPOS_CONTATO}
    for k in CAMPOS_CONTATO:
        if k in campos:
            setattr(contato, k, _texto(campos[k]))
    if principal is not None and principal != contato.principal:
        if principal:
            for outro in crm.contatos_de(session, contato.cliente_id):
                if outro.id != contato.id and outro.principal:
                    outro.principal = False
                    session.add(outro)
        contato.principal = principal
    contato.atualizado_em, contato.atualizado_por = datetime.utcnow(), ator.email
    session.add(contato)
    depois = {k: getattr(contato, k) for k in CAMPOS_CONTATO}
    adm.registrar(session, ator=ator, acao="UPDATE_CONTACT", entidade="Contato", entidade_id=contato.id,
                  escopo=contato.nome, antes=str({k: v for k, v in antes.items() if antes[k] != depois[k]}),
                  depois=str({k: v for k, v in depois.items() if antes[k] != depois[k]}), origem=ORIGEM)
    return contato


def definir_contato_ativo(session: Session, *, ator: Usuario, contato: Contato, ativo: bool) -> Contato:
    """Inativar não apaga: o contato some das seleções novas e continua nas visitas antigas."""
    if contato.ativo == ativo:
        return contato
    contato.ativo = ativo
    if not ativo and contato.principal:
        contato.principal = False
    contato.atualizado_em, contato.atualizado_por = datetime.utcnow(), ator.email
    session.add(contato)
    adm.registrar(session, ator=ator, acao="REACTIVATE_CONTACT" if ativo else "DEACTIVATE_CONTACT",
                  entidade="Contato", entidade_id=contato.id, escopo=contato.nome, origem=ORIGEM)
    return contato


# ---------------------------------------------------------------------------
# Visitas
# ---------------------------------------------------------------------------
CAMPOS_VISITA_TEXTO = ("necessidades", "produtos_discutidos", "oportunidades", "proximos_passos",
                       "observacoes", "participantes_externos")


@dataclass
class NovoAcompanhamento:
    descricao: str
    data_prevista: Optional[date] = None
    responsavel_id: Optional[int] = None


def _validar_participantes(session: Session, cliente_id: int, ids: Iterable[int]) -> List[int]:
    ids = sorted({int(i) for i in ids if str(i).strip()})
    for cid in ids:
        contato = session.get(Contato, cid)
        _exigir(contato is not None and contato.cliente_id == cliente_id,
                "Participante não é contato deste cliente.", 400)
    return ids


def criar_visita(session: Session, *, ator: Usuario, cliente_id: int, data_visita: Optional[date],
                 relatorio: str, assunto: Optional[str] = None, participantes: Iterable[int] = (),
                 acompanhamentos: Iterable[NovoAcompanhamento] = (), **campos) -> VisitaCliente:
    """Cliente → Nova visita → relatório → próximos passos → salvar. O relatório e a data são os
    únicos obrigatórios; a vendedora responsável é quem registra."""
    _cliente(session, cliente_id)
    _exigir(data_visita is not None, "Informe a data da visita.", 400)
    _exigir(bool(_texto(relatorio)), "O relatório da visita é obrigatório.", 400)
    _exigir(assunto in (None, "") or assunto in ROTULO_ASSUNTO, "Assunto inválido.", 400)
    ids = _validar_participantes(session, cliente_id, participantes)
    visita = VisitaCliente(cliente_id=cliente_id, data_visita=data_visita, responsavel_id=ator.id,
                           assunto=assunto or None, relatorio=relatorio.strip(),
                           criado_por_id=ator.id, criado_por=ator.email,
                           **{k: _texto(campos.get(k)) for k in CAMPOS_VISITA_TEXTO})
    session.add(visita)
    session.flush()
    for cid in ids:
        session.add(VisitaParticipante(visita_id=visita.id, contato_id=cid))
    for a in acompanhamentos:
        if _texto(a.descricao):
            criar_acompanhamento(session, ator=ator, cliente_id=cliente_id, descricao=a.descricao,
                                 data_prevista=a.data_prevista, responsavel_id=a.responsavel_id,
                                 visita_id=visita.id)
    adm.registrar(session, ator=ator, acao="CREATE_VISIT", entidade="VisitaCliente", entidade_id=visita.id,
                  escopo=f"cliente {cliente_id} · {data_visita.isoformat()}", origem=ORIGEM)
    return visita


def editar_visita(session: Session, *, ator: Usuario, visita: VisitaCliente, data_visita: Optional[date],
                  relatorio: str, assunto: Optional[str] = None, participantes: Iterable[int] = (),
                  **campos) -> VisitaCliente:
    """Correção do relatório. A autoria original (`criado_por_*`) e o responsável não mudam."""
    _exigir(visita.removida_em is None, "Esta visita foi retirada do histórico.", 409)
    _exigir(pode_editar_visita(ator, visita), "Só quem registrou a visita (ou a administração) pode editá-la.")
    _exigir(data_visita is not None, "Informe a data da visita.", 400)
    _exigir(bool(_texto(relatorio)), "O relatório da visita é obrigatório.", 400)
    _exigir(assunto in (None, "") or assunto in ROTULO_ASSUNTO, "Assunto inválido.", 400)
    ids = _validar_participantes(session, visita.cliente_id, participantes)
    campos_alterados = []
    novos = {"data_visita": data_visita, "relatorio": relatorio.strip(), "assunto": assunto or None,
             **{k: _texto(campos.get(k)) for k in CAMPOS_VISITA_TEXTO}}
    for k, v in novos.items():
        if getattr(visita, k) != v:
            campos_alterados.append(k)
            setattr(visita, k, v)
    atuais = {p.contato_id: p for p in session.exec(
        select(VisitaParticipante).where(VisitaParticipante.visita_id == visita.id)).all()}
    if set(atuais) != set(ids):
        campos_alterados.append("participantes")
        for cid, p in atuais.items():
            if cid not in ids:
                session.delete(p)
        for cid in ids:
            if cid not in atuais:
                session.add(VisitaParticipante(visita_id=visita.id, contato_id=cid))
    if campos_alterados:
        visita.atualizado_em, visita.atualizado_por = datetime.utcnow(), ator.email
        session.add(visita)
        adm.registrar(session, ator=ator, acao="UPDATE_VISIT", entidade="VisitaCliente", entidade_id=visita.id,
                      escopo=f"cliente {visita.cliente_id}", depois=", ".join(campos_alterados), origem=ORIGEM)
    return visita


def remover_visita(session: Session, *, ator: Usuario, visita: VisitaCliente, motivo: str) -> VisitaCliente:
    """Exclusão lógica: a visita sai do histórico normal, o registro fica (com quem e por quê)."""
    _exigir(pode_editar_visita(ator, visita), "Só quem registrou a visita (ou a administração) pode retirá-la.")
    _exigir(bool(_texto(motivo)), "Informe o motivo.", 400)
    if visita.removida_em is None:
        visita.removida_em, visita.removida_por = datetime.utcnow(), ator.email
        session.add(visita)
        adm.registrar(session, ator=ator, acao="REMOVE_VISIT", entidade="VisitaCliente", entidade_id=visita.id,
                      escopo=f"cliente {visita.cliente_id}", motivo=motivo.strip(), origem=ORIGEM)
    return visita


@dataclass
class LinhaVisita:
    visita: VisitaCliente
    responsavel: Optional[str]
    autor: Optional[str]
    assunto: Optional[str]
    participantes: List[Contato]
    acompanhamentos: List[AtividadeComercial] = field(default_factory=list)

    @property
    def trecho(self) -> str:
        texto = " ".join(self.visita.relatorio.split())
        return texto if len(texto) <= 220 else texto[:217].rstrip() + "…"

    @property
    def proximo_passo(self) -> Optional[str]:
        pend = [a for a in self.acompanhamentos if not a.concluida_em]
        if pend:
            return pend[0].titulo
        return self.visita.proximos_passos


def _nomes_usuarios(session: Session) -> Dict[int, str]:
    return {u.id: u.nome for u in session.exec(select(Usuario)).all()}


def linha_visita(session: Session, visita: VisitaCliente, nomes: Optional[Dict[int, str]] = None) -> LinhaVisita:
    nomes = nomes if nomes is not None else _nomes_usuarios(session)
    ids = [p.contato_id for p in session.exec(
        select(VisitaParticipante).where(VisitaParticipante.visita_id == visita.id)).all()]
    participantes = [session.get(Contato, i) for i in ids]
    acomp = session.exec(select(AtividadeComercial).where(AtividadeComercial.visita_id == visita.id)
                         .order_by(AtividadeComercial.id)).all()
    return LinhaVisita(visita=visita, responsavel=nomes.get(visita.responsavel_id),
                       autor=nomes.get(visita.criado_por_id) or visita.criado_por,
                       assunto=ROTULO_ASSUNTO.get(visita.assunto),
                       participantes=sorted([p for p in participantes if p], key=lambda c: c.nome),
                       acompanhamentos=list(acomp))


def visitas_do_cliente(session: Session, cliente_id: int) -> List[LinhaVisita]:
    """Histórico de visitas, mais recente primeiro (mesma data: a registrada por último antes)."""
    visitas = session.exec(select(VisitaCliente).where(VisitaCliente.cliente_id == cliente_id,
                                                       VisitaCliente.removida_em.is_(None))).all()
    visitas = sorted(visitas, key=lambda v: (v.data_visita, v.criado_em, v.id), reverse=True)
    nomes = _nomes_usuarios(session)
    return [linha_visita(session, v, nomes) for v in visitas]


# ---------------------------------------------------------------------------
# Acompanhamentos (AtividadeComercial)
# ---------------------------------------------------------------------------
def criar_acompanhamento(session: Session, *, ator: Usuario, cliente_id: int, descricao: str,
                         data_prevista: Optional[date] = None, responsavel_id: Optional[int] = None,
                         visita_id: Optional[int] = None) -> AtividadeComercial:
    """Follow-up do cliente — da visita (`visita_id`) ou direto da ficha (sem visita)."""
    _cliente(session, cliente_id)
    if visita_id is not None:
        visita = session.get(VisitaCliente, visita_id)
        _exigir(visita is not None and visita.cliente_id == cliente_id,
                "A visita não é deste cliente.", 400)
    if responsavel_id is not None:
        resp = session.get(Usuario, responsavel_id)
        _exigir(resp is not None and resp.ativo, "Responsável inválido.", 400)
    atividade = crm.criar_atividade(session, ator=ator, titulo=descricao, cliente_id=cliente_id,
                                    responsavel_id=responsavel_id or ator.id, tipo="FOLLOW_UP",
                                    due_em=prazo_do_dia(data_prevista) if data_prevista else None)
    atividade.visita_id = visita_id
    session.add(atividade)
    return atividade


def _exigir_acompanhamento(ator: Usuario, atividade: Optional[AtividadeComercial]) -> AtividadeComercial:
    _exigir(atividade is not None, "Acompanhamento não encontrado.", 404)
    _exigir(pode_operar_acompanhamento(ator, atividade),
            "Este acompanhamento é de outra pessoa: só o responsável, quem criou ou a administração podem alterá-lo.")
    return atividade


def concluir_acompanhamento(session: Session, *, ator: Usuario,
                            atividade: Optional[AtividadeComercial]) -> AtividadeComercial:
    atividade = _exigir_acompanhamento(ator, atividade)
    return crm.concluir_atividade(session, atividade, ator=ator)


def reagendar_acompanhamento(session: Session, *, ator: Usuario, atividade: Optional[AtividadeComercial],
                             data_prevista: Optional[date]) -> AtividadeComercial:
    atividade = _exigir_acompanhamento(ator, atividade)
    _exigir(atividade.concluida_em is None, "Acompanhamento concluído não se reagenda.", 409)
    _exigir(data_prevista is not None, "Informe a nova data.", 400)
    antes = atividade.due_em.date().isoformat() if atividade.due_em else "sem data"
    atividade.due_em = prazo_do_dia(data_prevista)
    atividade.atualizado_em = datetime.utcnow()
    session.add(atividade)
    adm.registrar(session, ator=ator, acao="RESCHEDULE_ACTIVITY", entidade="AtividadeComercial",
                  entidade_id=atividade.id, escopo=atividade.titulo, antes=antes,
                  depois=data_prevista.isoformat(), origem=ORIGEM)
    return atividade


def editar_acompanhamento(session: Session, *, ator: Usuario, atividade: Optional[AtividadeComercial],
                          descricao: str) -> AtividadeComercial:
    atividade = _exigir_acompanhamento(ator, atividade)
    _exigir(bool(_texto(descricao)), "A descrição é obrigatória.", 400)
    if atividade.titulo != descricao.strip():
        antes = atividade.titulo
        atividade.titulo = descricao.strip()
        atividade.atualizado_em = datetime.utcnow()
        session.add(atividade)
        adm.registrar(session, ator=ator, acao="UPDATE_ACTIVITY", entidade="AtividadeComercial",
                      entidade_id=atividade.id, escopo=f"cliente {atividade.cliente_id}", antes=antes,
                      depois=atividade.titulo, origem=ORIGEM)
    return atividade


def data_prevista(atividade: AtividadeComercial) -> Optional[date]:
    return atividade.due_em.date() if atividade.due_em else None


@dataclass
class Painel:
    atrasados: List[AtividadeComercial]
    hoje: List[AtividadeComercial]
    proximos: List[AtividadeComercial]
    sem_data: List[AtividadeComercial]
    concluidos: List[AtividadeComercial]


def painel_acompanhamentos(session: Session, *, responsavel_id: Optional[int] = None,
                           cliente_id: Optional[int] = None, agora: Optional[datetime] = None,
                           limite_concluidos: int = 30) -> Painel:
    """Atrasados · hoje · próximos · sem data, pelo dia comercial de São Paulo; concluídos à parte."""
    hoje = hoje_sp(agora)
    pend = crm.atividades_de(session, responsavel_id=responsavel_id, cliente_id=cliente_id,
                             pendentes=True, limite=2000)
    conc = crm.atividades_de(session, responsavel_id=responsavel_id, cliente_id=cliente_id,
                             pendentes=False, limite=2000)
    conc = sorted(conc, key=lambda a: a.concluida_em or datetime.min, reverse=True)[:limite_concluidos]
    return Painel(atrasados=[a for a in pend if a.due_em and data_prevista(a) < hoje],
                  hoje=[a for a in pend if a.due_em and data_prevista(a) == hoje],
                  proximos=[a for a in pend if a.due_em and data_prevista(a) > hoje],
                  sem_data=[a for a in pend if not a.due_em], concluidos=conc)


# ---------------------------------------------------------------------------
# Perfil de produtos utilizados
# ---------------------------------------------------------------------------
CAMPOS_PRODUTO_UTILIZADO = ("familia", "descricao", "thread_count", "composicao", "tamanho", "medidas", "gsm",
                            "volume_aproximado", "frequencia_reposicao", "fornecedor_atual", "observacoes")


def _normalizar_produto_utilizado(session: Session, dados: dict) -> dict:
    """Valida o vocabulário e, havendo produto do catálogo, completa o que o catálogo SABE e o
    usuário deixou em branco (família, fios, g/m², medida, composição exata). Nada é inventado."""
    out = {k: _texto(dados.get(k)) for k in CAMPOS_PRODUTO_UTILIZADO}
    out["thread_count"] = _inteiro(dados.get("thread_count"), "Fios")
    out["gsm"] = _inteiro(dados.get("gsm"), "Gramatura (g/m²)")
    produto_id = _inteiro(dados.get("produto_id"), "Produto")
    if out["familia"] is not None:
        _exigir(out["familia"] in rotulos.FAMILIA, "Família inválida.", 400)
    if out["composicao"] is not None:
        _exigir(out["composicao"] in COMPOSICOES, "Composição inválida.", 400)
    if out["tamanho"] is not None:
        _exigir(out["tamanho"] in TAMANHOS, "Tamanho inválido.", 400)
    if produto_id is not None:
        p = session.get(Produto, produto_id)
        _exigir(p is not None, "Produto do catálogo não encontrado.", 400)
        out["familia"] = out["familia"] or p.familia
        out["thread_count"] = out["thread_count"] or (int(p.thread_count) if p.thread_count else None)
        out["gsm"] = out["gsm"] or (int(p.gsm) if p.gsm else None)
        if not out["medidas"] and p.largura_cm and p.comprimento_cm:
            out["medidas"] = f"{int(p.largura_cm)}x{int(p.comprimento_cm)}"
        if not out["composicao"] and p.cotton_pct is not None:
            out["composicao"] = _COMPOSICAO_POR_ALGODAO.get(round(float(p.cotton_pct), 2))
        out["descricao"] = out["descricao"] or p.nome
    out["produto_id"] = produto_id
    _exigir(bool(out["familia"] or out["descricao"] or produto_id),
            "Identifique o produto: família, descrição ou produto do catálogo.", 400)
    return out


def criar_produto_utilizado(session: Session, *, ator: Usuario, cliente_id: int, **dados) -> ClienteProdutoUtilizado:
    _cliente(session, cliente_id)
    campos = _normalizar_produto_utilizado(session, dados)
    registro = ClienteProdutoUtilizado(cliente_id=cliente_id, criado_por=ator.email, **campos)
    session.add(registro)
    session.flush()
    adm.registrar(session, ator=ator, acao="CREATE_CLIENT_PRODUCT", entidade="ClienteProdutoUtilizado",
                  entidade_id=registro.id, escopo=f"cliente {cliente_id}: {registro.descricao or registro.familia}",
                  origem=ORIGEM)
    return registro


def editar_produto_utilizado(session: Session, *, ator: Usuario, registro: ClienteProdutoUtilizado,
                             **dados) -> ClienteProdutoUtilizado:
    campos = _normalizar_produto_utilizado(session, dados)
    alterados = [k for k, v in campos.items() if getattr(registro, k) != v]
    for k in alterados:
        setattr(registro, k, campos[k])
    if alterados:
        registro.atualizado_em, registro.atualizado_por = datetime.utcnow(), ator.email
        session.add(registro)
        adm.registrar(session, ator=ator, acao="UPDATE_CLIENT_PRODUCT", entidade="ClienteProdutoUtilizado",
                      entidade_id=registro.id, escopo=f"cliente {registro.cliente_id}",
                      depois=", ".join(alterados), origem=ORIGEM)
    return registro


def definir_produto_utilizado_ativo(session: Session, *, ator: Usuario, registro: ClienteProdutoUtilizado,
                                    ativo: bool) -> ClienteProdutoUtilizado:
    if registro.ativo != ativo:
        registro.ativo = ativo
        registro.atualizado_em, registro.atualizado_por = datetime.utcnow(), ator.email
        session.add(registro)
        adm.registrar(session, ator=ator, acao="REACTIVATE_CLIENT_PRODUCT" if ativo else "DEACTIVATE_CLIENT_PRODUCT",
                      entidade="ClienteProdutoUtilizado", entidade_id=registro.id,
                      escopo=f"cliente {registro.cliente_id}", origem=ORIGEM)
    return registro


def produtos_utilizados(session: Session, cliente_id: int) -> List[ClienteProdutoUtilizado]:
    linhas = session.exec(select(ClienteProdutoUtilizado)
                          .where(ClienteProdutoUtilizado.cliente_id == cliente_id)).all()
    return sorted(linhas, key=lambda r: (not r.ativo, rotulos.familia(r.familia) if r.familia else "~",
                                         (r.descricao or "").lower(), r.id))


def especificacao(registro: ClienteProdutoUtilizado) -> str:
    """O registro em uma linha legível — só atributos de produto, nenhum número econômico."""
    partes = [rotulos.familia(registro.familia) if registro.familia else None, registro.descricao,
              f"{registro.thread_count} fios" if registro.thread_count else None, registro.composicao,
              registro.tamanho, registro.medidas, f"{registro.gsm} g/m²" if registro.gsm else None]
    vistos, saida = set(), []
    for p in partes:
        if p and p.lower() not in vistos:
            vistos.add(p.lower())
            saida.append(p)
    return " · ".join(saida)


# ---------------------------------------------------------------------------
# Busca: clientes por produto utilizado
# ---------------------------------------------------------------------------
@dataclass
class ResultadoBusca:
    cliente: Cliente
    especificacoes: List[ClienteProdutoUtilizado]
    registrado_por: List[str]


def buscar_clientes_por_produto(session: Session, *, familia: Optional[str] = None,
                                produto_id: Optional[int] = None, thread_count: Optional[int] = None,
                                composicao: Optional[str] = None, tamanho: Optional[str] = None,
                                gsm: Optional[int] = None, incluir_arquivados: bool = False) -> List[ResultadoBusca]:
    """Filtro estruturado (igualdade) sobre o perfil de produtos UTILIZADOS. Um cliente aparece uma
    vez, com todas as especificações que casaram. Sem filtro nenhum, não devolve nada (não é lista
    de todos os clientes)."""
    if not any(v not in (None, "") for v in (familia, produto_id, thread_count, composicao, tamanho, gsm)):
        return []
    q = select(ClienteProdutoUtilizado).where(ClienteProdutoUtilizado.ativo == True)   # noqa: E712
    if familia:
        q = q.where(ClienteProdutoUtilizado.familia == familia)
    if produto_id:
        q = q.where(ClienteProdutoUtilizado.produto_id == produto_id)
    if thread_count:
        q = q.where(ClienteProdutoUtilizado.thread_count == thread_count)
    if composicao:
        q = q.where(ClienteProdutoUtilizado.composicao == composicao)
    if tamanho:
        q = q.where(ClienteProdutoUtilizado.tamanho == tamanho)
    if gsm:
        q = q.where(ClienteProdutoUtilizado.gsm == gsm)
    por_cliente: Dict[int, List[ClienteProdutoUtilizado]] = {}
    for r in session.exec(q).all():
        por_cliente.setdefault(r.cliente_id, []).append(r)
    nomes = {u.email: u.nome for u in session.exec(select(Usuario)).all()}
    saida = []
    for cid, regs in por_cliente.items():
        cliente = session.get(Cliente, cid)
        if cliente is None or (not cliente.ativo and not incluir_arquivados):
            continue
        quem = sorted({nomes.get(r.atualizado_por or r.criado_por, r.atualizado_por or r.criado_por)
                       for r in regs if (r.atualizado_por or r.criado_por)})
        saida.append(ResultadoBusca(cliente=cliente, especificacoes=regs, registrado_por=quem))
    return sorted(saida, key=lambda x: x.cliente.nome.lower())


# ---------------------------------------------------------------------------
# Visão geral do cliente
# ---------------------------------------------------------------------------
def resumo_relacionamento(session: Session, cliente_id: int, agora: Optional[datetime] = None) -> dict:
    """Última visita e próximo acompanhamento — para a visão geral do Cliente 360."""
    visitas = visitas_do_cliente(session, cliente_id)
    pend = crm.atividades_de(session, cliente_id=cliente_id, pendentes=True)
    hoje = hoje_sp(agora)
    return {"ultima_visita": visitas[0] if visitas else None,
            "proximo_acompanhamento": next((a for a in pend if a.due_em), pend[0] if pend else None),
            "acompanhamentos_atrasados": sum(1 for a in pend if a.due_em and data_prevista(a) < hoje),
            "hoje": hoje}


# ---------------------------------------------------------------------------
# Amostras do cliente (só leitura — o controle continua em app/amostras.py)
# ---------------------------------------------------------------------------
def amostras_do_cliente(session: Session, cliente_id: int) -> List[dict]:
    """As movimentações de amostra que citam o cliente, mais recentes primeiro."""
    from app import amostras as am
    from app.models import AmostraMovimentacao, AmostraProduto
    movs = session.exec(select(AmostraMovimentacao).where(AmostraMovimentacao.cliente_id == cliente_id)).all()
    saida = []
    for m in sorted(movs, key=lambda x: (x.data, x.criado_em), reverse=True):
        amostra = session.get(AmostraProduto, m.amostra_produto_id)
        produto = session.get(Produto, amostra.produto_id) if amostra and amostra.produto_id else None
        nome, espec = am.identificacao(amostra, produto) if amostra else ("Amostra", "")
        saida.append({"data": m.data, "tipo": am.ROTULO_TIPO.get(m.tipo, m.tipo), "quantidade": m.quantidade,
                      "nome": nome, "especificacao": espec, "motivo": m.motivo, "quem": m.usuario_nome,
                      "amostra_id": m.amostra_produto_id})
    return saida

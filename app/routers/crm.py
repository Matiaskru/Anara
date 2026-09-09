"""Rotas do CRM — home comercial, pipeline, oportunidades, contatos e atividades.

A linguagem aqui é comercial, e isso é deliberado: o vendedor vê cliente, oportunidade,
cotação, preço, total e próxima atividade. Não vê CNET, fingerprint, `premissa_id` nem
versão de referência — a complexidade das Sessões 1 a 6 continua existindo e continua
invisível para quem não precisa dela.

`responsavel` é responsabilidade operacional, **não** controle de acesso: quem vê o quê
segue a política da Sessão 4. O filtro "minhas oportunidades" existe para organizar o dia,
não para esconder negócio de colega.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import crm_service as crm
from app import workflow_service as ws
from app.db import get_session
from app.models import (
    AtividadeComercial, Cliente, Contato, Cotacao, EtapaOportunidade, MotivoPerda,
    Oportunidade, OrigemOportunidade, StatusOportunidade, TipoAtividade, Usuario,
)
from app.permissoes import exigir_autenticado, usuario_da_request, ve_economia
from app.templating import templates

router = APIRouter()


def _data(valor: Optional[str]) -> Optional[date]:
    if not (valor or "").strip():
        return None
    try:
        return date.fromisoformat(valor.strip())
    except ValueError:
        raise crm.DadoInvalido(f"Data inválida: '{valor}'. Use AAAA-MM-DD.")


def _datahora(valor: Optional[str]) -> Optional[datetime]:
    if not (valor or "").strip():
        return None
    texto = valor.strip().replace(" ", "T")
    for formato in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    raise crm.DadoInvalido(f"Data/hora inválida: '{valor}'.")


def _op(session: Session, oportunidade_id: int) -> Oportunidade:
    op = session.get(Oportunidade, oportunidade_id)
    if op is None:
        raise crm.DadoInvalido("Oportunidade não encontrada.", status=404)
    return op


def _contexto_comum(session: Session, request: Request) -> dict:
    return {
        "etapas": crm.ETAPAS, "status_possiveis": crm.STATUS,
        "origens": [o.value for o in OrigemOportunidade],
        "motivos_perda": [m.value for m in MotivoPerda],
        "tipos_atividade": [t.value for t in TipoAtividade],
        "usuarios": session.exec(select(Usuario).where(Usuario.ativo == True)).all(),  # noqa: E712
        "eu": usuario_da_request(request),
    }


# ---------------------------------------------------------------------------
# Home comercial
# ---------------------------------------------------------------------------
@router.get("/comercial", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)):
    """O que precisa de atenção hoje. Sem gráfico, sem análise — isso é da Sessão 8."""
    eu = exigir_autenticado(request)
    minhas = crm.listar_oportunidades(session, status=StatusOportunidade.aberta.value,
                                      responsavel_id=eu.id)
    sem_atividade = [o for o in minhas if crm.proxima_atividade(session, o.id) is None]
    pendentes = crm.atividades_de(session, responsavel_id=eu.id, pendentes=True)
    agora = datetime.utcnow()
    atrasadas = [a for a in pendentes if crm.esta_atrasada(a, agora)]
    hoje = [a for a in pendentes
            if a.due_em and not crm.esta_atrasada(a, agora)
            and a.due_em.date() == agora.date()]

    # A fila é de quem **decide**. Ver economia não é ter alçada — mostrar pedidos a quem
    # não pode aprová-los oferece uma tarefa que a pessoa não consegue concluir.
    from app.permissoes import aprova_cotacoes
    aprovacoes = ws.fila_de_aprovacao(session) if aprova_cotacoes(request) else []
    return templates.TemplateResponse(request, "crm_home.html", {
        "active": "comercial",
        "minhas": [crm.cartao(session, o) for o in minhas[:20]],
        "sem_atividade": [crm.cartao(session, o) for o in sem_atividade[:10]],
        "atrasadas": atrasadas[:15], "hoje": hoje[:15],
        "aprovacoes": aprovacoes[:10],
        **_contexto_comum(session, request),
    })


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
@router.get("/pipeline", response_class=HTMLResponse)
def pipeline(request: Request, responsavel: str = "", origem: str = "", busca: str = "",
             session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    responsavel_id = eu.id if responsavel == "eu" else (
        int(responsavel) if responsavel.isdigit() else None)
    colunas = crm.board(session, responsavel_id=responsavel_id, origem=origem or None,
                        busca=busca or None)
    return templates.TemplateResponse(request, "crm_pipeline.html", {
        "active": "pipeline", "colunas": colunas, "responsavel": responsavel,
        "origem": origem, "busca": busca,
        "totais": {e: sum(1 for _ in c) for e, c in colunas.items()},
        **_contexto_comum(session, request),
    })


@router.get("/oportunidades", response_class=HTMLResponse)
def listar(request: Request, etapa: str = "", status: str = "", responsavel: str = "",
           cliente_id: str = "", origem: str = "", busca: str = "",
           sem_atividade: str = "", pagina: int = 1,
           session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    por_pagina = 50
    responsavel_id = eu.id if responsavel == "eu" else (
        int(responsavel) if responsavel.isdigit() else None)
    linhas = crm.listar_oportunidades(
        session, etapa=etapa or None, status=status or None,
        responsavel_id=responsavel_id,
        cliente_id=int(cliente_id) if cliente_id.isdigit() else None,
        origem=origem or None, busca=busca or None,
        sem_proxima_atividade=(sem_atividade == "sim"),
        limite=por_pagina + 1, offset=(max(pagina, 1) - 1) * por_pagina)
    tem_proxima = len(linhas) > por_pagina
    return templates.TemplateResponse(request, "crm_lista.html", {
        "active": "oportunidades",
        "oportunidades": [crm.cartao(session, o) for o in linhas[:por_pagina]],
        "filtros": {"etapa": etapa, "status": status, "responsavel": responsavel,
                    "cliente_id": cliente_id, "origem": origem, "busca": busca,
                    "sem_atividade": sem_atividade},
        "pagina": max(pagina, 1), "tem_proxima": tem_proxima,
        "clientes": session.exec(select(Cliente).order_by(Cliente.nome)).all(),
        **_contexto_comum(session, request),
    })


# ---------------------------------------------------------------------------
# Oportunidade 360
# ---------------------------------------------------------------------------
@router.get("/oportunidades/{oportunidade_id}", response_class=HTMLResponse)
def detalhe(request: Request, oportunidade_id: int,
            session: Session = Depends(get_session)):
    exigir_autenticado(request)
    op = _op(session, oportunidade_id)
    cliente = session.get(Cliente, op.cliente_id)
    cotacoes = crm.cotacoes_de(session, op.id)
    return templates.TemplateResponse(request, "crm_oportunidade.html", {
        "active": "oportunidades", "op": op, "cliente": cliente,
        "cartao": crm.cartao(session, op),
        "contatos": crm.contatos_de(session, op.cliente_id),
        "cotacoes": cotacoes,
        "cotacao_atual": crm.cotacao_mais_recente(session, op.id),
        "atividades": crm.atividades_de(session, oportunidade_id=op.id),
        "proxima": crm.proxima_atividade(session, op.id),
        "timeline": crm.timeline(session, op),
        "responsavel": session.get(Usuario, op.responsavel_id) if op.responsavel_id else None,
        "faltando_fiscal": crm.dados_fiscais_faltando(cliente) if cliente else [],
        "atrasada": crm.esta_atrasada,
        **_contexto_comum(session, request),
    })


@router.post("/oportunidades")
def criar(request: Request, cliente_id: int = Form(...), titulo: str = Form(...),
          responsavel_id: str = Form(""), etapa: str = Form("PROSPECCAO"),
          origem: str = Form(""), origem_detalhe: str = Form(""),
          valor_estimado: str = Form(""), previsao: str = Form(""),
          descricao: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.criar_oportunidade(
        session, ator=ator, cliente_id=cliente_id, titulo=titulo,
        responsavel_id=int(responsavel_id) if responsavel_id.isdigit() else ator.id,
        etapa=etapa, origem=origem or None, origem_detalhe=origem_detalhe or None,
        valor_estimado=valor_estimado or None,
        data_prevista_fechamento=_data(previsao), descricao=descricao or None)
    session.commit()
    return RedirectResponse(url=f"/oportunidades/{op.id}", status_code=303)


@router.post("/oportunidades/{oportunidade_id}/etapa")
def mover(request: Request, oportunidade_id: int, etapa: str = Form(...),
          observacao: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.mudar_etapa(session, _op(session, oportunidade_id), etapa, ator=ator,
                         observacao=observacao or None)
    session.commit()
    return JSONResponse({"etapa": op.etapa, "mensagem": f"Movida para {op.etapa}."})


@router.post("/oportunidades/{oportunidade_id}/responsavel")
def responsavel(request: Request, oportunidade_id: int, responsavel_id: str = Form(""),
                session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.atribuir(session, _op(session, oportunidade_id),
                      int(responsavel_id) if responsavel_id.isdigit() else None, ator=ator)
    session.commit()
    return JSONResponse({"responsavel_id": op.responsavel_id, "mensagem": "Responsável atualizado."})


@router.post("/oportunidades/{oportunidade_id}/ganha")
def ganhar(request: Request, oportunidade_id: int, cotacao_id: int = Form(...),
           session: Session = Depends(get_session)):
    """Fecha o negócio — passando pelo `validar_compromisso_firme` da Sessão 6."""
    ator = exigir_autenticado(request)
    op = crm.marcar_ganha(session, _op(session, oportunidade_id), cotacao_id, ator=ator)
    session.commit()
    return JSONResponse({"status": op.status, "valor_fechado": op.valor_fechado,
                         "cotacao_vencedora_id": op.cotacao_vencedora_id,
                         "mensagem": "Negócio marcado como ganho."})


@router.post("/oportunidades/{oportunidade_id}/perdida")
def perder(request: Request, oportunidade_id: int, motivo: str = Form(...),
           comentario: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.marcar_perdida(session, _op(session, oportunidade_id), ator=ator,
                            motivo=motivo, comentario=comentario)
    session.commit()
    return JSONResponse({"status": op.status, "motivo": op.motivo_perda,
                         "mensagem": "Negócio marcado como perdido."})


@router.post("/oportunidades/{oportunidade_id}/reabrir")
def reabrir(request: Request, oportunidade_id: int, etapa: str = Form(""),
            motivo: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.reabrir(session, _op(session, oportunidade_id), ator=ator,
                     etapa=etapa or None, motivo=motivo or None)
    session.commit()
    return JSONResponse({"status": op.status, "etapa": op.etapa,
                         "mensagem": "Oportunidade reaberta."})


@router.post("/oportunidades/{oportunidade_id}/cotacao")
def nova_cotacao(request: Request, oportunidade_id: int,
                 session: Session = Depends(get_session)):
    """Cria a cotação já com o cliente e o vínculo — o vendedor não recadastra nada."""
    from app.routers.cotacoes import proximo_numero

    ator = exigir_autenticado(request)
    op = _op(session, oportunidade_id)
    cliente = session.get(Cliente, op.cliente_id)
    contato = crm.contato_principal(session, op.cliente_id)

    cot = Cotacao(cliente_id=op.cliente_id, oportunidade_id=op.id,
                  numero=proximo_numero(session), vendedor=ator.nome,
                  estado_destino=getattr(cliente, "cidade_uf", None),
                  finalidade=getattr(cliente, "finalidade", None),
                  contato_nome=getattr(contato, "nome", None),
                  departamento_contato=getattr(contato, "cargo", None))
    session.add(cot)
    session.commit()
    session.refresh(cot)
    return RedirectResponse(url=f"/cotacoes/{cot.id}", status_code=303)


@router.post("/oportunidades/{oportunidade_id}/vincular")
def vincular(request: Request, oportunidade_id: int, cotacao_id: int = Form(...),
             session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = _op(session, oportunidade_id)
    cot = session.get(Cotacao, cotacao_id)
    if cot is None:
        raise crm.DadoInvalido("Cotação não encontrada.", status=404)
    crm.vincular_cotacao(session, op, cot, ator=ator)
    session.commit()
    return JSONResponse({"ok": True, "mensagem": "Cotação vinculada."})


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------
@router.post("/clientes/{cliente_id}/contatos")
def criar_contato(request: Request, cliente_id: int, nome: str = Form(...),
                  cargo: str = Form(""), email: str = Form(""), telefone: str = Form(""),
                  principal: str = Form(""), observacao: str = Form(""),
                  session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    crm.criar_contato(session, ator=ator, cliente_id=cliente_id, nome=nome, cargo=cargo,
                      email=email, telefone=telefone, observacao=observacao,
                      principal=(principal in ("sim", "on", "true", "1")))
    session.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


# ---------------------------------------------------------------------------
# Atividades
# ---------------------------------------------------------------------------
@router.post("/atividades")
def criar_atividade(request: Request, titulo: str = Form(...),
                    oportunidade_id: str = Form(""), cliente_id: str = Form(""),
                    contato_id: str = Form(""), tipo: str = Form("FOLLOW_UP"),
                    due_em: str = Form(""), observacao: str = Form(""),
                    responsavel_id: str = Form(""),
                    session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    atividade = crm.criar_atividade(
        session, ator=ator, titulo=titulo,
        oportunidade_id=int(oportunidade_id) if oportunidade_id.isdigit() else None,
        cliente_id=int(cliente_id) if cliente_id.isdigit() else None,
        contato_id=int(contato_id) if contato_id.isdigit() else None,
        responsavel_id=int(responsavel_id) if responsavel_id.isdigit() else None,
        tipo=tipo, due_em=_datahora(due_em), observacao=observacao or None)
    session.commit()
    return JSONResponse({"id": atividade.id, "mensagem": "Atividade criada."})


@router.post("/atividades/{atividade_id}/concluir")
def concluir(request: Request, atividade_id: int,
             session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    atividade = session.get(AtividadeComercial, atividade_id)
    if atividade is None:
        raise crm.DadoInvalido("Atividade não encontrada.", status=404)
    crm.concluir_atividade(session, atividade, ator=ator)
    session.commit()
    return JSONResponse({"concluida_em": str(atividade.concluida_em),
                         "mensagem": "Atividade concluída."})

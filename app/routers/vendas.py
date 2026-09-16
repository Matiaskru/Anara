"""Vendas — a superfície diária da equipe comercial (Fase 3B).

`Venda` é o nome de interface da `Oportunidade`: um projeto/negociação com um cliente, que
pode ter várias revisões de cotação. Um status comercial só por linha — Rascunho, Enviado,
Negociação, Vendido, Perdido —, trocado inline e gravado na hora, com histórico.

O que continua sendo verdade da Sessão 7: `responsavel` organiza, não esconde; economia
(custo, margem, lucro, piso) nunca entra aqui — a lista mostra preço e total comerciais.
Quem decide o que a vendedora vê é `crm.cartao` e `pos_venda.resumo`, nunca o template.
"""
from datetime import date, datetime
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import crm_service as crm
from app import pos_venda_service as pv
from app import workflow_service as ws
from app.db import get_session
from app.models import (
    Cliente, Cotacao, Oportunidade, StatusOportunidade, TipoAtividade, Usuario,
)
from app.permissoes import exigir_autenticado, usuario_da_request, ve_economia
from app.templating import templates

router = APIRouter()

#: Filtro da lista: o status comercial único → (status, etapa).
FILTRO_STATUS = {
    "rascunho": (StatusOportunidade.aberta.value, crm.RASCUNHO),
    "enviado": (StatusOportunidade.aberta.value, crm.ENVIADO),
    "negociacao": (StatusOportunidade.aberta.value, crm.NEGOCIACAO),
    "vendido": (StatusOportunidade.ganha.value, None),
    "perdido": (StatusOportunidade.perdida.value, None),
    "abertas": (StatusOportunidade.aberta.value, None),
}


def _venda(session: Session, venda_id: int) -> Oportunidade:
    op = session.get(Oportunidade, venda_id)
    if op is None:
        raise crm.DadoInvalido("Venda não encontrada.", status=404)
    return op


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


def _contexto(session: Session, request: Request) -> dict:
    return {
        "etapas": crm.ETAPAS, "motivos_perda": crm.MOTIVOS_PERDA,
        "tipos_atividade": [t.value for t in TipoAtividade],
        "status_pos_venda": pv.STATUS,
        "usuarios": session.exec(select(Usuario).where(Usuario.ativo == True)).all(),  # noqa: E712
        "eu": usuario_da_request(request),
        "financeiro": ve_economia(request),
    }


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
#: Rótulos dos filtros de status da lista — a tela nunca mostra o enum.
ROTULO_FILTRO_STATUS = {
    "": "Todas", "abertas": "Abertas", "rascunho": "Rascunho", "enviado": "Enviado",
    "negociacao": "Negociação", "vendido": "Vendido", "perdido": "Perdido",
}

#: Atalhos de período da lista (Fase 3C). Filtram pela última atualização da venda.
PERIODOS_LISTA = [("", "Qualquer data"), ("mes", "Este mês"), ("trimestre", "Este trimestre"),
                  ("ano", "Este ano"), ("12m", "Últimos 12 meses")]


def listar_vendas(session: Session, *, status: str = "", responsavel_id: Optional[int] = None,
                  cliente_id: Optional[int] = None, busca: str = "", periodo: str = "") -> list:
    st, etapa = FILTRO_STATUS.get(status, (None, None))
    linhas = crm.listar_oportunidades(session, status=st, etapa=etapa,
                                      responsavel_id=responsavel_id, cliente_id=cliente_id,
                                      busca=busca or None, limite=500)
    if periodo:
        from app import metrics_service as mx
        janela = mx.periodo_de(periodo)
        linhas = [o for o in linhas if janela.contem(o.atualizado_em or o.criado_em)]
    cartoes = [crm.cartao(session, o) for o in linhas]
    # a mais recentemente tocada primeiro — é assim que se opera uma lista de trabalho
    return sorted(cartoes, key=lambda c: c["atualizado_em"] or datetime.min, reverse=True)


def quadro(vendas: list) -> list:
    """As três colunas do pipeline ativo — só vendas abertas, com contagem e total.

    Vendido e Perdido ficam fora: o quadro é do que ainda está em jogo.
    """
    from app.dinheiro import D0, ZERO, para_float
    colunas = []
    for etapa in crm.ETAPAS:
        cartoes = [v for v in vendas if v["status"] == StatusOportunidade.aberta.value
                   and v["etapa"] == etapa]
        com_valor = [c["valor_atual"] for c in cartoes if c["valor_atual"] is not None]
        colunas.append({
            "etapa": etapa, "rotulo": crm.status_comercial(SimpleNamespace(
                status=StatusOportunidade.aberta.value, etapa=etapa)),
            "quantidade": len(cartoes),
            "total": para_float(sum((D0(v) for v in com_valor), ZERO)) if com_valor else None,
            "cartoes": cartoes,
        })
    return colunas


@router.get("/vendas", response_class=HTMLResponse)
def lista(request: Request, status: str = "", responsavel: str = "", cliente_id: str = "",
          busca: str = "", periodo: str = "", vista: str = "lista",
          session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    responsavel_id = eu.id if responsavel == "eu" else (
        int(responsavel) if responsavel.isdigit() else None)
    vendas = listar_vendas(session, status=status, responsavel_id=responsavel_id,
                           cliente_id=int(cliente_id) if cliente_id.isdigit() else None,
                           busca=busca, periodo=periodo)
    vista = "quadro" if vista == "quadro" else "lista"
    from urllib.parse import urlencode
    filtros_ativos = {k: v for k, v in (("status", status), ("responsavel", responsavel),
                                        ("cliente_id", cliente_id), ("busca", busca),
                                        ("periodo", periodo)) if v}
    return templates.TemplateResponse(request, "vendas_list.html", {
        "active": "vendas", "vendas": vendas, "vista": vista,
        "qs_filtros": ("&" + urlencode(filtros_ativos)) if filtros_ativos else "",
        "colunas": quadro(vendas) if vista == "quadro" else [],
        "filtros": {"status": status, "responsavel": responsavel, "cliente_id": cliente_id,
                    "busca": busca, "periodo": periodo},
        "opcoes_status": [(k, ROTULO_FILTRO_STATUS.get(k, k)) for k in ("",) + tuple(FILTRO_STATUS)],
        "periodos": PERIODOS_LISTA,
        "clientes": session.exec(select(Cliente).where(Cliente.ativo == True)  # noqa: E712
                                 .order_by(Cliente.nome)).all(),
        **_contexto(session, request),
    })


@router.post("/vendas")
def criar(request: Request, cliente_id: int = Form(...), titulo: str = Form(...),
          responsavel_id: str = Form(""), descricao: str = Form(""),
          session: Session = Depends(get_session)):
    """Nova venda: cliente + nome do projeto. Nasce em RASCUNHO, com quem criou como responsável."""
    ator = exigir_autenticado(request)
    padrao = ator.id if session.get(Usuario, ator.id) is not None else None
    op = crm.criar_oportunidade(
        session, ator=ator, cliente_id=cliente_id, titulo=titulo,
        responsavel_id=int(responsavel_id) if responsavel_id.isdigit() else padrao,
        descricao=descricao or None)
    session.commit()
    return RedirectResponse(url=f"/vendas/{op.id}", status_code=303)


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------
@router.get("/vendas/{venda_id}", response_class=HTMLResponse)
def detalhe(request: Request, venda_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    op = _venda(session, venda_id)
    cliente = session.get(Cliente, op.cliente_id)
    cotacoes = crm.cotacoes_de(session, op.id)
    return templates.TemplateResponse(request, "venda_detail.html", {
        "active": "vendas", "op": op, "cliente": cliente,
        "cartao": crm.cartao(session, op),
        "cotacoes": cotacoes,
        "cotacao_atual": crm.cotacao_mais_recente(session, op.id),
        "atividades": crm.atividades_de(session, oportunidade_id=op.id),
        "proxima": crm.proxima_atividade(session, op.id),
        "atualizacoes": crm.atualizacoes_de(session, op.id),
        "timeline": crm.timeline(session, op),
        "pos_venda": pv.resumo(op),
        "responsavel": session.get(Usuario, op.responsavel_id) if op.responsavel_id else None,
        "faltando_fiscal": crm.dados_fiscais_faltando(cliente) if cliente else [],
        "atrasada": crm.esta_atrasada,
        **_contexto(session, request),
    })


# ---------------------------------------------------------------------------
# Status comercial — inline, grava na hora
# ---------------------------------------------------------------------------
@router.post("/vendas/{venda_id}/status")
def mudar_status(request: Request, venda_id: int, etapa: str = Form(...),
                 observacao: str = Form(""), session: Session = Depends(get_session)):
    """Rascunho ↔ Enviado ↔ Negociação, nos dois sentidos. Vendido/Perdido são ações próprias."""
    ator = exigir_autenticado(request)
    op = crm.mudar_etapa(session, _venda(session, venda_id), etapa, ator=ator,
                         observacao=observacao or None)
    session.commit()
    return JSONResponse({"etapa": op.etapa, "status": op.status,
                         "status_comercial": crm.status_comercial(op),
                         "mensagem": f"Venda em {crm.status_comercial(op)}."})


@router.post("/vendas/{venda_id}/vendido")
def vendido(request: Request, venda_id: int, cotacao_id: int = Form(...),
            session: Session = Depends(get_session)):
    """Marcar vendido = GANHA, pela infraestrutura existente (`validar_compromisso_firme`)."""
    ator = exigir_autenticado(request)
    op = crm.marcar_ganha(session, _venda(session, venda_id), cotacao_id, ator=ator)
    session.commit()
    return JSONResponse({"status": op.status, "status_comercial": crm.status_comercial(op),
                         "valor_fechado": op.valor_fechado,
                         "cotacao_vencedora_id": op.cotacao_vencedora_id,
                         "status_pos_venda": op.status_pos_venda,
                         "mensagem": "Venda marcada como vendida — aguardando entrega."})


@router.post("/vendas/{venda_id}/perdido")
def perdido(request: Request, venda_id: int, motivo: str = Form(...), nota: str = Form(""),
            session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.marcar_perdida(session, _venda(session, venda_id), ator=ator, motivo=motivo,
                            comentario=nota)
    session.commit()
    return JSONResponse({"status": op.status, "status_comercial": crm.status_comercial(op),
                         "motivo": op.motivo_perda, "mensagem": "Venda marcada como perdida."})


@router.post("/vendas/{venda_id}/reabrir")
def reabrir(request: Request, venda_id: int, motivo: str = Form(""),
            session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.reabrir(session, _venda(session, venda_id), ator=ator, motivo=motivo or None)
    session.commit()
    return JSONResponse({"status": op.status, "etapa": op.etapa,
                         "status_comercial": crm.status_comercial(op),
                         "mensagem": f"Venda reaberta em {crm.status_comercial(op)}."})


@router.post("/vendas/{venda_id}/responsavel")
def responsavel(request: Request, venda_id: int, responsavel_id: str = Form(""),
                session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = crm.atribuir(session, _venda(session, venda_id),
                      int(responsavel_id) if responsavel_id.isdigit() else None, ator=ator)
    session.commit()
    return JSONResponse({"responsavel_id": op.responsavel_id, "mensagem": "Responsável atualizado."})


# ---------------------------------------------------------------------------
# Atualização comercial (+ próxima atividade opcional)
# ---------------------------------------------------------------------------
@router.post("/vendas/{venda_id}/atualizacoes")
def registrar_atualizacao(request: Request, venda_id: int, texto: str = Form(...),
                          atividade_titulo: str = Form(""), atividade_tipo: str = Form("FOLLOW_UP"),
                          atividade_due_em: str = Form(""),
                          session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = _venda(session, venda_id)
    proxima = ({"titulo": atividade_titulo, "tipo": atividade_tipo,
                "due_em": _datahora(atividade_due_em)} if atividade_titulo.strip() else None)
    nota = crm.registrar_atualizacao(session, op, ator=ator, texto=texto,
                                     proxima_atividade=proxima)
    session.commit()
    return JSONResponse({"id": nota.id, "mensagem": "Atualização registrada."})


# ---------------------------------------------------------------------------
# Nova cotação dentro da venda
# ---------------------------------------------------------------------------
@router.post("/vendas/{venda_id}/cotacao")
def nova_cotacao(request: Request, venda_id: int, session: Session = Depends(get_session)):
    """Cria a cotação já vinculada — o mesmo caminho canônico de `/cotacoes`."""
    from app.routers.cotacoes import criar_cotacao_da_venda

    ator = exigir_autenticado(request)
    op = _venda(session, venda_id)
    cot = criar_cotacao_da_venda(session, request, op, ator=ator)
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{cot.id}", status_code=303)


# ---------------------------------------------------------------------------
# Pós-venda
# ---------------------------------------------------------------------------
def _pv(op: Oportunidade, mensagem: str) -> JSONResponse:
    r = pv.resumo(op)
    return JSONResponse({**{k: (str(v) if isinstance(v, (date, datetime)) else v)
                            for k, v in r.items()}, "mensagem": mensagem})


@router.post("/vendas/{venda_id}/pos-venda/entrega-prevista")
def entrega_prevista(request: Request, venda_id: int, prevista_em: str = Form(""),
                     session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.definir_entrega_prevista(session, _venda(session, venda_id), ator=ator,
                                     prevista_em=_data(prevista_em))
    session.commit()
    return _pv(op, "Entrega prevista atualizada.")


@router.post("/vendas/{venda_id}/pos-venda/entrega")
def entrega(request: Request, venda_id: int, entregue_em: str = Form(""),
            session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.registrar_entrega(session, _venda(session, venda_id), ator=ator,
                              entregue_em=_datahora(entregue_em))
    session.commit()
    return _pv(op, "Entrega registrada.")


@router.post("/vendas/{venda_id}/pos-venda/observacao")
def observacao(request: Request, venda_id: int, texto: str = Form(""),
               session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.registrar_observacao(session, _venda(session, venda_id), ator=ator, texto=texto)
    session.commit()
    return _pv(op, "Observação registrada.")


@router.post("/vendas/{venda_id}/pos-venda/faturamento")
def faturamento(request: Request, venda_id: int, faturado_em: str = Form(""),
                numero_documento_fiscal: str = Form(""),
                session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.registrar_faturamento(session, _venda(session, venda_id), ator=ator,
                                  faturado_em=_data(faturado_em),
                                  numero_documento_fiscal=numero_documento_fiscal)
    session.commit()
    return _pv(op, "Faturamento registrado.")


@router.post("/vendas/{venda_id}/pos-venda/pagamento-previsto")
def pagamento_previsto(request: Request, venda_id: int, previsto_em: str = Form(""),
                       session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.definir_pagamento_previsto(session, _venda(session, venda_id), ator=ator,
                                       previsto_em=_data(previsto_em))
    session.commit()
    return _pv(op, "Pagamento previsto atualizado.")


@router.post("/vendas/{venda_id}/pos-venda/pago")
def pago(request: Request, venda_id: int, pago_em: str = Form(""),
         session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.marcar_pago(session, _venda(session, venda_id), ator=ator,
                        pago_em=_datahora(pago_em))
    session.commit()
    return _pv(op, "Venda marcada como paga.")


@router.post("/vendas/{venda_id}/pos-venda/atrasado")
def atrasado(request: Request, venda_id: int, observacao: str = Form(""),
             session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.marcar_atrasado(session, _venda(session, venda_id), ator=ator,
                            observacao=observacao or None)
    session.commit()
    return _pv(op, "Venda marcada como atrasada.")


@router.post("/vendas/{venda_id}/pos-venda/corrigir")
def corrigir(request: Request, venda_id: int, status: str = Form(...), motivo: str = Form(""),
             session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    op = pv.corrigir_status(session, _venda(session, venda_id), ator=ator, novo=status,
                            motivo=motivo)
    session.commit()
    return _pv(op, "Status financeiro corrigido.")


# ---------------------------------------------------------------------------
# Apoio ao formulário de nova cotação
# ---------------------------------------------------------------------------
@router.get("/clientes/{cliente_id}/vendas.json")
def vendas_do_cliente(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    abertas = crm.listar_oportunidades(session, cliente_id=cliente_id,
                                       status=StatusOportunidade.aberta.value)
    return JSONResponse([{"id": o.id, "titulo": o.titulo,
                          "status_comercial": crm.status_comercial(o)} for o in abertas])


# ---------------------------------------------------------------------------
# Compatibilidade: as URLs antigas do CRM levam à tela de Vendas
# ---------------------------------------------------------------------------
@router.get("/oportunidades")
def lista_antiga(request: Request):
    return RedirectResponse(url="/vendas", status_code=303)

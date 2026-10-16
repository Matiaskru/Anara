"""CRM de relacionamento (15/10/2026): contatos, visitas, acompanhamentos, perfil de produtos e
busca de clientes por produto. Regras em `app/crm_relacionamento.py`.

Todas as rotas exigem login; nenhuma devolve número econômico (custo, margem, preço de compra,
`material_ref`). Formulário clássico (POST → redirect): a tela só mostra o resultado depois que o
servidor gravou — erro vira a página de erro do sistema, nunca um "salvo" falso.
"""
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import crm_relacionamento as rel
from app import crm_service as crm
from app import rotulos
from app import tabela_comercial
from app.crm_service import DadoInvalido
from app.db import get_session
from app.models import (
    ASSUNTOS_VISITA, AtividadeComercial, Cliente, ClienteProdutoUtilizado, Contato, Produto, Usuario,
    VisitaCliente, VisitaParticipante,
)
from app.permissoes import exigir_autenticado
from app.templating import templates

router = APIRouter()

SIM = ("sim", "on", "true", "1")


def _data(valor: Optional[str], campo: str = "Data") -> Optional[date]:
    if not (valor or "").strip():
        return None
    try:
        return date.fromisoformat(valor.strip())
    except ValueError:
        raise DadoInvalido(f"{campo} inválida: '{valor}'. Use AAAA-MM-DD.")


def _int(valor) -> Optional[int]:
    valor = (valor or "").strip() if isinstance(valor, str) else valor
    return int(valor) if valor not in (None, "") and str(valor).isdigit() else None


def _voltar(valor: Optional[str], padrao: str) -> str:
    """Só caminho interno (evita redirecionar para fora do sistema)."""
    v = (valor or "").strip()
    alvo = urlparse(v)
    return v if v.startswith("/") and not v.startswith("//") and not alvo.netloc else padrao


def _usuarios_ativos(session: Session):
    return sorted(session.exec(select(Usuario).where(Usuario.ativo == True)).all(),   # noqa: E712
                  key=lambda u: u.nome.lower())


def _contexto_formularios(session: Session) -> dict:
    """Vocabulários dos formulários. O produto do catálogo aparece pelo NOME (nunca `material_ref`)."""
    produtos = tabela_comercial.so_da_tabela(session.exec(
        select(Produto).where(Produto.ativo == True).order_by(Produto.familia, Produto.nome)).all())   # noqa: E712
    return {"assuntos": ASSUNTOS_VISITA, "composicoes": rel.COMPOSICOES, "tamanhos": rel.TAMANHOS,
            "familias": sorted(rotulos.FAMILIA.items(), key=lambda kv: kv[1]),
            "produtos_catalogo": [{"id": p.id, "nome": p.nome, "familia": p.familia} for p in produtos],
            "usuarios": _usuarios_ativos(session)}


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------
@router.post("/contatos/{contato_id}/editar")
async def editar_contato(request: Request, contato_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    contato = session.get(Contato, contato_id)
    if contato is None:
        raise DadoInvalido("Contato não encontrado.", status=404)
    form = await request.form()
    rel.editar_contato(session, ator=ator, contato=contato, principal=(form.get("principal") in SIM),
                       **{k: form.get(k, "") for k in rel.CAMPOS_CONTATO})
    session.commit()
    return RedirectResponse(url=f"/clientes/{contato.cliente_id}#contatos", status_code=303)


@router.post("/contatos/{contato_id}/ativo")
async def contato_ativo(request: Request, contato_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    contato = session.get(Contato, contato_id)
    if contato is None:
        raise DadoInvalido("Contato não encontrado.", status=404)
    form = await request.form()
    rel.definir_contato_ativo(session, ator=ator, contato=contato, ativo=(form.get("ativo") in SIM))
    session.commit()
    return RedirectResponse(url=f"/clientes/{contato.cliente_id}#contatos", status_code=303)


# ---------------------------------------------------------------------------
# Visitas
# ---------------------------------------------------------------------------
def _cliente_ou_404(session: Session, cliente_id: int) -> Cliente:
    cliente = session.get(Cliente, cliente_id)
    if cliente is None:
        raise DadoInvalido("Cliente não encontrado.", status=404)
    return cliente


def _contatos_para_visita(session: Session, cliente_id: int, ja_participantes=()):
    """Ativos + os inativos que já participaram (a correção de uma visita antiga não os perde)."""
    return [c for c in crm.contatos_de(session, cliente_id) if c.ativo or c.id in set(ja_participantes)]


@router.get("/clientes/{cliente_id}/visitas/nova", response_class=HTMLResponse)
def nova_visita(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    cliente = _cliente_ou_404(session, cliente_id)
    return templates.TemplateResponse(request, "visita_form.html", {
        "active": "clientes", "cliente": cliente, "visita": None, "participantes": [],
        "contatos": _contatos_para_visita(session, cliente_id), "hoje": rel.hoje_sp(), "eu": eu,
        **_contexto_formularios(session)})


def _campos_visita(form) -> dict:
    return {"data_visita": _data(form.get("data_visita"), "Data da visita"),
            "relatorio": form.get("relatorio", ""), "assunto": form.get("assunto") or None,
            "participantes": [i for i in (_int(v) for v in form.getlist("participantes")) if i],
            **{k: form.get(k, "") for k in rel.CAMPOS_VISITA_TEXTO}}


@router.post("/clientes/{cliente_id}/visitas")
async def criar_visita(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    acomp = [rel.NovoAcompanhamento(descricao=d, data_prevista=_data(dt, "Data prevista"),
                                    responsavel_id=_int(r))
             for d, dt, r in zip(form.getlist("acomp_descricao"), form.getlist("acomp_data"),
                                 form.getlist("acomp_responsavel")) if (d or "").strip()]
    visita = rel.criar_visita(session, ator=ator, cliente_id=cliente_id, acompanhamentos=acomp,
                              **_campos_visita(form))
    session.commit()
    return RedirectResponse(url=f"/visitas/{visita.id}", status_code=303)


def _visita_ou_404(session: Session, visita_id: int) -> VisitaCliente:
    visita = session.get(VisitaCliente, visita_id)
    if visita is None:
        raise DadoInvalido("Visita não encontrada.", status=404)
    return visita


@router.get("/visitas/{visita_id}", response_class=HTMLResponse)
def ver_visita(request: Request, visita_id: int, session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    visita = _visita_ou_404(session, visita_id)
    cliente = session.get(Cliente, visita.cliente_id)
    return templates.TemplateResponse(request, "visita_detalhe.html", {
        "active": "clientes", "cliente": cliente, "linha": rel.linha_visita(session, visita),
        "pode_editar": rel.pode_editar_visita(eu, visita), "pode_operar": lambda a: rel.pode_operar_acompanhamento(eu, a),
        "rotulo_assunto": rel.ROTULO_ASSUNTO, "eu": eu, "hoje": rel.hoje_sp(),
        "data_prevista": rel.data_prevista, **_contexto_formularios(session)})


@router.get("/visitas/{visita_id}/editar", response_class=HTMLResponse)
def editar_visita_form(request: Request, visita_id: int, session: Session = Depends(get_session)):
    eu = exigir_autenticado(request)
    visita = _visita_ou_404(session, visita_id)
    if not rel.pode_editar_visita(eu, visita):
        raise DadoInvalido("Só quem registrou a visita (ou a administração) pode editá-la.", status=403)
    ids = [p.contato_id for p in session.exec(
        select(VisitaParticipante).where(VisitaParticipante.visita_id == visita.id)).all()]
    return templates.TemplateResponse(request, "visita_form.html", {
        "active": "clientes", "cliente": session.get(Cliente, visita.cliente_id), "visita": visita,
        "participantes": ids, "contatos": _contatos_para_visita(session, visita.cliente_id, ids),
        "hoje": rel.hoje_sp(), "eu": eu, **_contexto_formularios(session)})


@router.post("/visitas/{visita_id}/editar")
async def editar_visita(request: Request, visita_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    visita = _visita_ou_404(session, visita_id)
    form = await request.form()
    rel.editar_visita(session, ator=ator, visita=visita, **_campos_visita(form))
    session.commit()
    return RedirectResponse(url=f"/visitas/{visita.id}", status_code=303)


@router.post("/visitas/{visita_id}/remover")
async def remover_visita(request: Request, visita_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    visita = _visita_ou_404(session, visita_id)
    form = await request.form()
    rel.remover_visita(session, ator=ator, visita=visita, motivo=form.get("motivo", ""))
    session.commit()
    return RedirectResponse(url=f"/clientes/{visita.cliente_id}#visitas", status_code=303)


# ---------------------------------------------------------------------------
# Acompanhamentos
# ---------------------------------------------------------------------------
@router.get("/acompanhamentos", response_class=HTMLResponse)
def acompanhamentos(request: Request, responsavel: str = "eu", session: Session = Depends(get_session)):
    """Meus acompanhamentos: atrasados, hoje, próximos e sem data; concluídos à parte. Ver os de
    outra pessoa é permitido (o CRM não esconde negócio de colega); agir, não."""
    eu = exigir_autenticado(request)
    alvo = eu.id if responsavel in ("", "eu") else (None if responsavel == "todos" else _int(responsavel))
    painel = rel.painel_acompanhamentos(session, responsavel_id=alvo)
    todas = painel.atrasados + painel.hoje + painel.proximos + painel.sem_data + painel.concluidos
    clientes = {c.id: c for c in session.exec(select(Cliente).where(
        Cliente.id.in_({a.cliente_id for a in todas if a.cliente_id} or {0}))).all()}
    nomes = {u.id: u.nome for u in session.exec(select(Usuario)).all()}
    return templates.TemplateResponse(request, "acompanhamentos.html", {
        "active": "acompanhamentos", "painel": painel, "clientes": clientes, "nomes": nomes,
        "responsavel": responsavel or "eu", "usuarios": _usuarios_ativos(session), "eu": eu,
        "hoje": rel.hoje_sp(), "data_prevista": rel.data_prevista,
        "pode_operar": lambda a: rel.pode_operar_acompanhamento(eu, a)})


@router.post("/clientes/{cliente_id}/acompanhamentos")
async def criar_acompanhamento(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    rel.criar_acompanhamento(session, ator=ator, cliente_id=cliente_id, descricao=form.get("descricao", ""),
                             data_prevista=_data(form.get("data_prevista"), "Data prevista"),
                             responsavel_id=_int(form.get("responsavel_id")),
                             visita_id=_int(form.get("visita_id")))
    session.commit()
    return RedirectResponse(url=_voltar(form.get("voltar"), f"/clientes/{cliente_id}#acompanhamentos"),
                            status_code=303)


@router.post("/acompanhamentos/{atividade_id}/concluir")
async def concluir_acompanhamento(request: Request, atividade_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    rel.concluir_acompanhamento(session, ator=ator, atividade=session.get(AtividadeComercial, atividade_id))
    session.commit()
    return RedirectResponse(url=_voltar(form.get("voltar"), "/acompanhamentos"), status_code=303)


@router.post("/acompanhamentos/{atividade_id}/reagendar")
async def reagendar_acompanhamento(request: Request, atividade_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    rel.reagendar_acompanhamento(session, ator=ator, atividade=session.get(AtividadeComercial, atividade_id),
                                 data_prevista=_data(form.get("data_prevista"), "Nova data"))
    session.commit()
    return RedirectResponse(url=_voltar(form.get("voltar"), "/acompanhamentos"), status_code=303)


@router.post("/acompanhamentos/{atividade_id}/editar")
async def editar_acompanhamento(request: Request, atividade_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    rel.editar_acompanhamento(session, ator=ator, atividade=session.get(AtividadeComercial, atividade_id),
                              descricao=form.get("descricao", ""))
    session.commit()
    return RedirectResponse(url=_voltar(form.get("voltar"), "/acompanhamentos"), status_code=303)


# ---------------------------------------------------------------------------
# Perfil de produtos utilizados
# ---------------------------------------------------------------------------
def _dados_produto(form) -> dict:
    return {k: form.get(k, "") for k in rel.CAMPOS_PRODUTO_UTILIZADO + ("produto_id",)}


@router.get("/clientes/{cliente_id}/produtos-utilizados/novo", response_class=HTMLResponse)
def novo_produto_utilizado(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    return templates.TemplateResponse(request, "produto_utilizado_form.html", {
        "active": "clientes", "cliente": _cliente_ou_404(session, cliente_id), "registro": None,
        **_contexto_formularios(session)})


@router.get("/produtos-utilizados/{registro_id}/editar", response_class=HTMLResponse)
def editar_produto_utilizado_form(request: Request, registro_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    registro = _registro_ou_404(session, registro_id)
    return templates.TemplateResponse(request, "produto_utilizado_form.html", {
        "active": "clientes", "cliente": session.get(Cliente, registro.cliente_id), "registro": registro,
        **_contexto_formularios(session)})


@router.post("/clientes/{cliente_id}/produtos-utilizados")
async def criar_produto_utilizado(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    form = await request.form()
    rel.criar_produto_utilizado(session, ator=ator, cliente_id=cliente_id, **_dados_produto(form))
    session.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}#produtos", status_code=303)


def _registro_ou_404(session: Session, registro_id: int) -> ClienteProdutoUtilizado:
    registro = session.get(ClienteProdutoUtilizado, registro_id)
    if registro is None:
        raise DadoInvalido("Registro não encontrado.", status=404)
    return registro


@router.post("/produtos-utilizados/{registro_id}/editar")
async def editar_produto_utilizado(request: Request, registro_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    registro = _registro_ou_404(session, registro_id)
    form = await request.form()
    rel.editar_produto_utilizado(session, ator=ator, registro=registro, **_dados_produto(form))
    session.commit()
    return RedirectResponse(url=f"/clientes/{registro.cliente_id}#produtos", status_code=303)


@router.post("/produtos-utilizados/{registro_id}/ativo")
async def produto_utilizado_ativo(request: Request, registro_id: int, session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    registro = _registro_ou_404(session, registro_id)
    form = await request.form()
    rel.definir_produto_utilizado_ativo(session, ator=ator, registro=registro, ativo=(form.get("ativo") in SIM))
    session.commit()
    return RedirectResponse(url=f"/clientes/{registro.cliente_id}#produtos", status_code=303)


# ---------------------------------------------------------------------------
# Busca: clientes por produto utilizado
# ---------------------------------------------------------------------------
@router.get("/clientes-por-produto", response_class=HTMLResponse)
def clientes_por_produto(request: Request, familia: str = "", produto_id: str = "", fios: str = "",
                         composicao: str = "", tamanho: str = "", gsm: str = "",
                         session: Session = Depends(get_session)):
    exigir_autenticado(request)
    filtros = {"familia": familia or None, "produto_id": _int(produto_id), "thread_count": _int(fios),
               "composicao": composicao or None, "tamanho": tamanho or None, "gsm": _int(gsm)}
    algum = any(v is not None for v in filtros.values())
    resultados = rel.buscar_clientes_por_produto(session, **filtros) if algum else []
    return templates.TemplateResponse(request, "clientes_por_produto.html", {
        "active": "clientes", "resultados": resultados, "algum_filtro": algum,
        "filtro": {"familia": familia, "produto_id": produto_id, "fios": fios, "composicao": composicao,
                   "tamanho": tamanho, "gsm": gsm},
        "especificacao": rel.especificacao, **_contexto_formularios(session)})

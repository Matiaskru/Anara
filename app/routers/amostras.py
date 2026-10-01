"""Amostras — a aba das vendedoras e o Admin → Amostras (01/10/2026).

Vendedora (qualquer papel autenticado): vê saldo e histórico, registra envio/uso e retorno.
OWNER/ADMIN: além disso, põem produto do catálogo no controle, registram entrada e fazem
ajuste com motivo. Ninguém apaga nem edita movimentação — não existe rota para isso.

Cada rota lê os campos que declara, um a um (nada de `request.form()` direto no ORM), e toda
regra de saldo mora em `app/amostras.py`.
"""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import amostras as am
from app.busca import buscar as buscar_produtos
from app.db import get_session
from app.models import AmostraProduto, Cliente, Fornecedor, Produto
from app.permissoes import administra, exigir_admin, exigir_autenticado
from app.templating import templates

router = APIRouter()


def _clientes(session: Session):
    return session.exec(select(Cliente).where(Cliente.ativo == True)   # noqa: E712
                        .order_by(Cliente.nome)).all()


# ---------------------------------------------------------------------------
# Aba Amostras (vendedoras, ADMIN, OWNER)
# ---------------------------------------------------------------------------
@router.get("/amostras", response_class=HTMLResponse)
def lista(request: Request, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    return templates.TemplateResponse(request, "amostras_lista.html", {
        "active": "amostras", "painel": am.painel(session), "hoje": date.today(),
        "eh_admin": administra(request),
    })


@router.get("/amostras/{amostra_id}", response_class=HTMLResponse)
def detalhe(request: Request, amostra_id: int, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    amostra = session.get(AmostraProduto, amostra_id)
    if amostra is None or not amostra.ativo:
        raise am.AmostraInvalida("Amostra não encontrada.", status=404)
    movs = am.movimentos(session, amostra.id)
    produto = session.get(Produto, amostra.produto_id) if amostra.produto_id else None
    nome, especificacao = am.identificacao(amostra, produto)
    return templates.TemplateResponse(request, "amostra_detalhe.html", {
        "active": "amostras", "amostra": amostra, "produto": produto, "nome": nome,
        "especificacao": especificacao,
        "saldo": am.saldo_de(movs), "fora": am.fora(session, amostra.id, movs),
        "historico": am.historico(session, amostra.id, movs), "clientes": _clientes(session),
        "motivos": am.MOTIVOS_SAIDA, "condicoes": am.CONDICOES_RETORNO, "hoje": date.today(),
        "eh_admin": administra(request),
    })


@router.post("/amostras/{amostra_id}/saida")
def saida(request: Request, amostra_id: int, quantidade: str = Form(...), cliente_id: str = Form(""),
          cliente_texto: str = Form(""), motivo: str = Form(...), observacao: str = Form(""),
          data: str = Form(""), session: Session = Depends(get_session)):
    """Registrar envio / uso. A responsável é quem está logada — não vem do formulário."""
    ator = exigir_autenticado(request)
    am.registrar_saida(session, amostra_id, quantidade=quantidade, cliente_id=cliente_id,
                       cliente_texto=cliente_texto, motivo=motivo, observacao=observacao,
                       data_mov=data, ator=ator)
    session.commit()
    return RedirectResponse(url=f"/amostras/{amostra_id}", status_code=303)


@router.post("/amostras/{amostra_id}/retorno")
def retorno(request: Request, amostra_id: int, quantidade: str = Form(...),
            condicao: str = Form(...), destino: str = Form(...), observacao: str = Form(""),
            data: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_autenticado(request)
    am.registrar_retorno(session, amostra_id, quantidade=quantidade, condicao=condicao,
                         destino=destino, observacao=observacao, data_mov=data, ator=ator)
    session.commit()
    return RedirectResponse(url=f"/amostras/{amostra_id}", status_code=303)


# ---------------------------------------------------------------------------
# Admin → Amostras (OWNER/ADMIN)
# ---------------------------------------------------------------------------
@router.get("/admin/amostras", response_class=HTMLResponse)
def admin_lista(request: Request, q: str = "", session: Session = Depends(get_session)):
    exigir_admin(request)
    resultados = []
    if q.strip():
        ativos = session.exec(select(Produto).where(Produto.ativo == True)).all()   # noqa: E712
        fornecedores = {f.id: f.nome for f in session.exec(select(Fornecedor)).all()}
        ja = {a.produto_id for a in session.exec(select(AmostraProduto)).all()}
        resultados = [{"produto": p, "ja_esta": p.id in ja}
                      for p in buscar_produtos(ativos, q, fornecedores, limite=20)]
    return templates.TemplateResponse(request, "admin_amostras.html", {
        "active": "admin", "painel": am.painel(session), "q": q, "resultados": resultados,
        "hoje": date.today(),
    })


@router.post("/admin/amostras")
def admin_adicionar(request: Request, produto_id: str = Form(...), quantidade_inicial: str = Form("0"),
                    observacao: str = Form(""), session: Session = Depends(get_session)):
    ator = exigir_admin(request)
    am.adicionar_produto(session, produto_id=produto_id, quantidade_inicial=quantidade_inicial,
                         observacao=observacao, ator=ator)
    session.commit()
    return RedirectResponse(url="/admin/amostras", status_code=303)


@router.post("/admin/amostras/avulsa")
def admin_adicionar_avulsa(request: Request, nome: str = Form(...), especificacao: str = Form(""),
                           quantidade_inicial: str = Form("0"), observacao: str = Form(""),
                           session: Session = Depends(get_session)):
    """Amostra de peça que não existe no catálogo: nome e especificação próprios, sem produto."""
    ator = exigir_admin(request)
    am.adicionar_avulsa(session, nome=nome, especificacao=especificacao,
                        quantidade_inicial=quantidade_inicial, observacao=observacao, ator=ator)
    session.commit()
    return RedirectResponse(url="/admin/amostras", status_code=303)


@router.post("/admin/amostras/entrada")
def admin_entrada(request: Request, amostra_id: int = Form(...), quantidade: str = Form(...),
                  observacao: str = Form(""), data: str = Form(""),
                  session: Session = Depends(get_session)):
    ator = exigir_admin(request)
    am.registrar_entrada(session, amostra_id, quantidade=quantidade, observacao=observacao,
                         data_mov=data, ator=ator)
    session.commit()
    return RedirectResponse(url="/admin/amostras", status_code=303)


@router.post("/admin/amostras/ajuste")
def admin_ajuste(request: Request, amostra_id: int = Form(...), disponivel_contado: str = Form(...),
                 motivo: str = Form(...), data: str = Form(""),
                 session: Session = Depends(get_session)):
    ator = exigir_admin(request)
    am.ajustar(session, amostra_id, disponivel_contado=disponivel_contado, motivo=motivo,
               data_mov=data, ator=ator)
    session.commit()
    return RedirectResponse(url="/admin/amostras", status_code=303)

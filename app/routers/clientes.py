from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import arquivamento
from app.db import get_session
from app.models import Cliente, Cotacao, CotacaoItem
from app.templating import templates

router = APIRouter()


@router.get("/clientes", response_class=HTMLResponse)
def listar(request: Request, q: str = "", arquivados: str = "",
           session: Session = Depends(get_session)):
    todos = session.exec(select(Cliente).order_by(Cliente.nome)).all()
    mostrar_arquivados = arquivados == "sim"
    clientes = [c for c in todos if bool(c.ativo) != mostrar_arquivados]
    if q:
        ql = q.lower()
        clientes = [c for c in clientes if ql in (c.nome or "").lower()]
    return templates.TemplateResponse(request, "clientes_list.html", {
        "active": "clientes", "clientes": clientes, "q": q,
        "mostrar_arquivados": mostrar_arquivados,
        "total_arquivados": sum(1 for c in todos if not c.ativo),
    })


@router.post("/clientes/{cliente_id}/arquivar")
def arquivar(cliente_id: int, arquivar_cotacoes: str = Form(""),
             session: Session = Depends(get_session)):
    arquivamento.arquivar_cliente(session, cliente_id, arquivar_cotacoes == "sim")
    return RedirectResponse(url="/clientes", status_code=303)


@router.post("/clientes/{cliente_id}/restaurar")
def restaurar(cliente_id: int, session: Session = Depends(get_session)):
    cliente = session.get(Cliente, cliente_id)
    if cliente:
        cliente.ativo = True
        session.add(cliente)
        session.commit()
    return RedirectResponse(url="/clientes", status_code=303)


@router.post("/clientes")
def criar(request: Request, nome: str = Form(...), cnpj_cpf: str = Form(""),
          cidade_uf: str = Form(""), telefone: str = Form(""), email: str = Form(""),
          voltar_para: str = Form("/clientes"),
          session: Session = Depends(get_session)):
    cliente = Cliente(nome=nome.strip(), cnpj_cpf=cnpj_cpf or None, cidade_uf=cidade_uf or None,
                       telefone=telefone or None, email=email or None)
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    if voltar_para == "nova_cotacao":
        return RedirectResponse(url=f"/cotacoes/nova?cliente_id={cliente.id}", status_code=303)
    return RedirectResponse(url=f"/clientes/{cliente.id}", status_code=303)


@router.get("/clientes/{cliente_id}", response_class=HTMLResponse)
def detalhe(request: Request, cliente_id: int, session: Session = Depends(get_session)):
    cliente = session.get(Cliente, cliente_id)
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)

    cotacoes = session.exec(
        select(Cotacao).where(Cotacao.cliente_id == cliente_id).order_by(Cotacao.criado_em.desc())
    ).all()

    ultimos_precos = {}
    totais = {}
    for c in cotacoes:
        itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == c.id)).all()
        totais[c.id] = sum(it.faturamento for it in itens)
        for it in itens:
            atual = ultimos_precos.get(it.nome_produto)
            if atual is None or c.criado_em > atual["data"]:
                ultimos_precos[it.nome_produto] = {"preco": it.preco_negociado, "data": c.criado_em,
                                                    "especificacao": it.especificacao}

    # --- CRM (Sessão 7): a ficha vira 360 sem virar dashboard ---
    from app import crm_service as crm

    oportunidades = crm.listar_oportunidades(session, cliente_id=cliente_id)
    return templates.TemplateResponse(request, "cliente_detail.html", {
        "active": "clientes", "cliente": cliente, "cotacoes": cotacoes,
        "totais": totais,
        "ultimos_precos": sorted(ultimos_precos.items(), key=lambda x: x[1]["data"], reverse=True),
        "contatos": crm.contatos_de(session, cliente_id),
        "abertas": [crm.cartao(session, o) for o in oportunidades
                    if o.status == "ABERTA"],
        "fechadas": [crm.cartao(session, o) for o in oportunidades
                     if o.status != "ABERTA"],
        "atividades": crm.atividades_de(session, cliente_id=cliente_id, limite=20),
        "faltando_fiscal": crm.dados_fiscais_faltando(cliente),
        "etapas": crm.ETAPAS,
        "origens": [o.value for o in __import__("app.models", fromlist=["x"]).OrigemOportunidade],
    })

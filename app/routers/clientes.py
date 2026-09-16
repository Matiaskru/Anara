from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import arquivamento
from app.db import get_session
from app.models import Cliente, Cotacao, CotacaoItem, Finalidade, Usuario
from app.permissoes import usuario_da_request
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
    from app import metrics_service as mx
    return templates.TemplateResponse(request, "clientes_list.html", {
        "active": "clientes", "clientes": clientes, "q": q,
        "mostrar_arquivados": mostrar_arquivados,
        "total_arquivados": sum(1 for c in todos if not c.ativo),
        "resumo": mx.clientes_resumo(session),
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
    """Cadastro mínimo: nome. CNPJ repetido de cliente ativo é recusado (409)."""
    from app import crm_service as crm
    from app.permissoes import usuario_da_request

    cliente = crm.criar_cliente(session, ator=usuario_da_request(request), nome=nome,
                                cnpj_cpf=cnpj_cpf or None, cidade_uf=cidade_uf or None,
                                telefone=telefone or None, email=email or None)
    session.commit()
    session.refresh(cliente)
    if voltar_para == "nova_cotacao":
        return RedirectResponse(url=f"/cotacoes/nova?cliente_id={cliente.id}", status_code=303)
    return RedirectResponse(url=f"/clientes/{cliente.id}", status_code=303)


@router.post("/clientes/{cliente_id}/editar")
def editar(request: Request, cliente_id: int, nome: str = Form(...), cnpj_cpf: str = Form(""),
           cidade_uf: str = Form(""), finalidade: str = Form(""), telefone: str = Form(""),
           email: str = Form(""), contato_nome: str = Form(""),
           session: Session = Depends(get_session)):
    """Edita o cadastro comercial (Fase 3C). Não toca em cotação nenhuma.

    CNPJ que já pertence a OUTRO cliente ativo é recusado, como na criação. A finalidade
    só aceita os valores do enum — ela decide o cenário fiscal das cotações futuras.
    """
    from app import admin_service as adm
    from app import crm_service as crm
    from app.models import Finalidade
    from app.permissoes import exigir_autenticado

    ator = exigir_autenticado(request)
    cliente = session.get(Cliente, cliente_id)
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    if not (nome or "").strip():
        raise crm.DadoInvalido("O nome do cliente é obrigatório.")
    duplicado = crm.cliente_por_cnpj(session, cnpj_cpf or None)
    if duplicado is not None and duplicado.id != cliente.id and duplicado.ativo:
        raise crm.DadoInvalido(
            f"Este CNPJ já pertence a {duplicado.nome} (#{duplicado.id}).", status=409)
    if finalidade and finalidade not in {f.value for f in Finalidade}:
        raise crm.DadoInvalido("Finalidade fiscal inválida.")

    antes = {"nome": cliente.nome, "cnpj_cpf": cliente.cnpj_cpf, "cidade_uf": cliente.cidade_uf,
             "finalidade": cliente.finalidade}
    cliente.nome = nome.strip()
    cliente.cnpj_cpf = (cnpj_cpf or "").strip() or None
    cliente.cidade_uf = (cidade_uf or "").strip() or None
    cliente.finalidade = finalidade or None
    cliente.telefone = (telefone or "").strip() or None
    cliente.email = (email or "").strip() or None
    cliente.contato_nome = (contato_nome or "").strip() or None
    session.add(cliente)
    adm.registrar(session, ator=ator, acao="UPDATE_CLIENT", entidade="Cliente",
                  entidade_id=cliente.id, escopo=cliente.nome, antes=str(antes),
                  depois=str({"nome": cliente.nome, "cnpj_cpf": cliente.cnpj_cpf,
                              "cidade_uf": cliente.cidade_uf, "finalidade": cliente.finalidade}),
                  origem="crm")
    session.commit()
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

    # --- CRM (Sessão 7 → Fase 3B): a ficha é o Cliente 360 ---
    from app import crm_service as crm
    from app import metrics_service as mx

    oportunidades = crm.listar_oportunidades(session, cliente_id=cliente_id, limite=1000)
    vendas = {o.id: o for o in oportunidades}
    return templates.TemplateResponse(request, "cliente_detail.html", {
        "active": "clientes", "cliente": cliente, "cotacoes": cotacoes,
        "totais": totais, "vendas_por_id": vendas,
        "ultimos_precos": sorted(ultimos_precos.items(), key=lambda x: x[1]["data"], reverse=True),
        "contatos": crm.contatos_de(session, cliente_id),
        "abertas": [crm.cartao(session, o) for o in oportunidades if o.status == "ABERTA"],
        "vendidas": [crm.cartao(session, o) for o in oportunidades if o.status == "GANHA"],
        "perdidas": [crm.cartao(session, o) for o in oportunidades if o.status == "PERDIDA"],
        "atividades": crm.atividades_de(session, cliente_id=cliente_id, limite=20),
        "faltando_fiscal": crm.dados_fiscais_faltando(cliente),
        "comercial": mx.cliente_360(session, cliente_id),
        "etapas": crm.ETAPAS,
        "finalidades": [f.value for f in Finalidade],
        "usuarios": session.exec(select(Usuario).where(Usuario.ativo == True)).all(),  # noqa: E712
        "eu": usuario_da_request(request),
    })

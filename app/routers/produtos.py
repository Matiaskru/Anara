from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from app import pricing_service as ps
from app.busca import buscar as buscar_produtos
from app.db import get_session
from app.models import BaseImportacao, Fornecedor, Produto
from app.templating import templates

router = APIRouter()


@router.get("/produtos", response_class=HTMLResponse)
def listar(request: Request, q: str = "", fornecedor: str = "", metodo: str = "",
           revisao: str = "", session: Session = Depends(get_session)):
    produtos = session.exec(select(Produto).where(Produto.ativo == True)  # noqa: E712
                            .order_by(Produto.categoria, Produto.nome)).all()
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}

    if q:
        produtos = buscar_produtos(produtos, q,
                                   {f.id: f.nome for f in fornecedores.values()},
                                   limite=len(produtos))
    if fornecedor:
        produtos = [p for p in produtos
                    if fornecedores.get(p.fornecedor_id)
                    and fornecedores[p.fornecedor_id].codigo == fornecedor]
    if metodo:
        produtos = [p for p in produtos if (p.cost_method or "") == metodo]
    if revisao == "sim":
        produtos = [p for p in produtos if p.precisa_revisao]
    elif revisao == "sem_custo":
        produtos = [p for p in produtos if not p.custo_unitario]

    ultima_importacao = session.exec(
        select(BaseImportacao).order_by(BaseImportacao.importado_em.desc())).first()

    return templates.TemplateResponse(request, "produtos_list.html", {
        "active": "produtos", "produtos": produtos, "q": q,
        "fornecedores": sorted(fornecedores.values(), key=lambda f: f.nome),
        "fornecedor_por_id": fornecedores, "fornecedor_filtro": fornecedor,
        "metodo_filtro": metodo, "revisao_filtro": revisao,
        "metodos": sorted({p.cost_method for p in session.exec(select(Produto)).all()
                           if p.cost_method}),
        "ultima_importacao": ultima_importacao,
    })


@router.get("/produtos/buscar")
def buscar(q: str = "", fornecedor: str = "", session: Session = Depends(get_session)):
    """Busca da tela de cotação: entende português e inglês, vários termos e acentos."""
    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
    if fornecedor:
        produtos = [p for p in produtos if fornecedores.get(p.fornecedor_id)
                    and fornecedores[p.fornecedor_id].codigo == fornecedor]
    produtos = buscar_produtos(produtos, q, {i: f.nome for i, f in fornecedores.items()},
                               limite=40)
    return JSONResponse([{
        "id": p.id, "nome": p.nome, "especificacao": p.especificacao, "categoria": p.categoria,
        "familia": p.familia, "custo_unitario": p.custo_unitario, "preco_base": p.preco_base,
        "fornecedor": (fornecedores[p.fornecedor_id].nome if p.fornecedor_id in fornecedores
                       else None),
        "cost_method": p.cost_method, "margem_padrao_pct": p.margem_padrao_pct,
        "precisa_revisao": p.precisa_revisao, "revisao_motivo": p.revisao_motivo,
        "sem_custo": not bool(p.custo_unitario),
        "thread_count": p.thread_count, "gsm": p.gsm,
    } for p in produtos])


@router.get("/produtos/{produto_id}/memoria")
def memoria(produto_id: int, session: Session = Depends(get_session)):
    """Memória do preço do catálogo: da especificação (ou do custo do fornecedor) ao preço."""
    produto = session.get(Produto, produto_id)
    if not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    return JSONResponse(ps.memoria_do_preco(session, produto))

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from app import pricing_service as ps
from app.busca import buscar as buscar_produtos
from app.confidencial import produto_comercial
from app.permissoes import exigir_economia, ve_economia
from app.db import get_session
from app.dinheiro import para_float
from app.margin_rules import resolver_margem
from app.models import MargemRegra, BaseImportacao, Fornecedor, Produto
from app.templating import templates

router = APIRouter()


def situacao_comercial(produto: Produto) -> str:
    """O que a vendedora precisa saber do item, sem economia: dá para cotar agora?

    `DISPONIVEL` tem custo e forma preço; `SOB_CONSULTA` não tem base de custo (o preço
    precisa ser cotado com o fornecedor); `REVISAR` tem custo mas o cadastro pede atenção.
    """
    if not produto.custo_unitario or (produto.status_custo or "").upper() == "A_COTAR":
        return "SOB_CONSULTA"
    if produto.precisa_revisao or (produto.status_custo or "").upper() == "REVIEW_REQUIRED":
        return "REVISAR"
    return "DISPONIVEL"


ROTULO_SITUACAO = {"DISPONIVEL": "Disponível", "SOB_CONSULTA": "Sob consulta", "REVISAR": "Revisar"}


@router.get("/produtos", response_class=HTMLResponse)
def listar(request: Request, q: str = "", fornecedor: str = "", metodo: str = "",
           revisao: str = "", familia: str = "", situacao: str = "",
           session: Session = Depends(get_session)):
    todos = session.exec(select(Produto).where(Produto.ativo == True)  # noqa: E712
                         .order_by(Produto.categoria, Produto.nome)).all()
    produtos = list(todos)
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}

    if q:
        produtos = buscar_produtos(produtos, q,
                                   {f.id: f.nome for f in fornecedores.values()},
                                   limite=len(produtos))
    if fornecedor:
        produtos = [p for p in produtos
                    if fornecedores.get(p.fornecedor_id)
                    and fornecedores[p.fornecedor_id].codigo == fornecedor]
    if familia:
        produtos = [p for p in produtos if (p.familia or "") == familia]
    if metodo and ve_economia(request):
        produtos = [p for p in produtos if (p.cost_method or "") == metodo]
    if situacao:
        produtos = [p for p in produtos if situacao_comercial(p) == situacao]
    # compatibilidade com os filtros anteriores da tela
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
        "familia_filtro": familia, "situacao_filtro": situacao,
        "familias": sorted({p.familia for p in todos if p.familia}),
        "situacoes": list(ROTULO_SITUACAO.items()),
        "situacao_de": situacao_comercial, "rotulo_situacao": ROTULO_SITUACAO,
        "metodos": sorted({p.cost_method for p in todos if p.cost_method}),
        "ultima_importacao": ultima_importacao,
        "total_catalogo": len(todos),
    })


@router.get("/produtos/buscar")
def buscar(request: Request, q: str = "", fornecedor: str = "",
           session: Session = Depends(get_session)):
    """Busca da tela de cotação: entende português e inglês, vários termos e acentos.

    O vendedor precisa desta busca para montar cotação — então ela **não** é negada a ele; o
    que muda é o que ela devolve. `custo_unitario`, `margem_padrao_pct` e `cost_method` saem
    do payload de quem não vê economia. `sem_custo` continua, porque é informação operacional
    (o item não forma margem) e não revela valor nenhum.
    """
    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
    if fornecedor:
        produtos = [p for p in produtos if fornecedores.get(p.fornecedor_id)
                    and fornecedores[p.fornecedor_id].codigo == fornecedor]
    produtos = buscar_produtos(produtos, q, {i: f.nome for i, f in fornecedores.items()},
                               limite=40)
    # A margem que a tela oferece como default é a da regra VIGENTE, resolvida agora — não a
    # coluna-cache `Produto.margem_padrao_pct`, que envelhece quando a política muda (a de
    # 16/09/2026 mudou todas). `preco_travado` é operacional, como `sem_custo`: diz que a
    # linha não aceita outro unitário, sem revelar número nenhum.
    regras = session.exec(select(MargemRegra)).all()
    politicas = {p.id: resolver_margem(regras, fornecedor_id=p.fornecedor_id,
                                       familia=p.familia, thread_count=p.thread_count,
                                       sku_key=p.sku_key) for p in produtos}
    completo = [{
        "id": p.id, "nome": p.nome, "especificacao": p.especificacao, "categoria": p.categoria,
        "familia": p.familia, "custo_unitario": p.custo_unitario, "preco_base": p.preco_base,
        "fornecedor": (fornecedores[p.fornecedor_id].nome if p.fornecedor_id in fornecedores
                       else None),
        "cost_method": p.cost_method,
        "margem_padrao_pct": para_float(politicas[p.id].margem_pct),
        "preco_travado": bool(politicas[p.id].preco_travado),
        "precisa_revisao": p.precisa_revisao, "revisao_motivo": p.revisao_motivo,
        "sem_custo": not bool(p.custo_unitario),
        "thread_count": p.thread_count, "gsm": p.gsm,
    } for p in produtos]
    if ve_economia(request):
        return JSONResponse(completo)
    return JSONResponse([produto_comercial(x) for x in completo])


@router.get("/produtos/{produto_id}/memoria")
def memoria(request: Request, produto_id: int, session: Session = Depends(get_session)):
    """Memória do preço do catálogo: da especificação (ou do custo do fornecedor) ao preço.

    Negado ao vendedor, não filtrado: a memória **é** o motor econômico — EXW, CMT, consumo,
    nacionalização, alíquotas e margem. Devolvê-la "sem os números" não sobraria nada útil,
    e sobraria a chance de esquecer um campo.
    """
    exigir_economia(request)
    produto = session.get(Produto, produto_id)
    if not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    return JSONResponse(ps.memoria_do_preco(session, produto))

"""Calculadora de custo KTC — tela avulsa e uso dentro da cotação."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import calculadora as calc
from app import pricing_service as ps
from app.db import get_session
from app.models import Cotacao, CotacaoItem, Fornecedor, Produto
from app.routers.cotacoes import _aplicar_resultado, _calcular, _preencher_item, montar_regras
from app.templating import templates

router = APIRouter()


@router.get("/calculadora", response_class=HTMLResponse)
def pagina(request: Request, cotacao_id: int = 0, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id) if cotacao_id else None
    cenario = cotacao or ps.cenario_padrao_catalogo(session)
    _regras, contexto = ps.regras_da_cotacao(session, cenario)
    return templates.TemplateResponse(request, "calculadora.html", {
        "active": "calculadora", "opcoes": calc.opcoes(session), "cotacao": cotacao,
        "contexto_fiscal": contexto, "cenario": cenario,
    })


def _parametros(form) -> dict:
    def numero(chave, padrao=None):
        valor = (form.get(chave) or "").strip()
        if not valor:
            return padrao
        try:
            return float(valor.replace(",", "."))
        except ValueError:
            return padrao

    margem = numero("margem_pct")
    return {
        "familia": form.get("familia") or "",
        "largura_cm": numero("largura_cm"),
        "comprimento_cm": numero("comprimento_cm"),
        "material_id": int(numero("material_id") or 0) or None,
        "gsm": int(numero("gsm") or 0) or None,
        "plain_or_stripe": form.get("plain_or_stripe") or "plain",
        "quantidade": numero("quantidade", 1) or 1,
        "outros_custos_usd": numero("outros_custos_usd", 0.0) or 0.0,
        "margem_override": (margem / 100 if margem and margem > 1 else margem),
        "acabamento": form.get("acabamento") or None,
    }


@router.post("/calculadora/calcular")
async def calcular(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    dados = _parametros(form)
    cotacao_id = form.get("cotacao_id")
    cotacao = session.get(Cotacao, int(cotacao_id)) if cotacao_id else None
    return JSONResponse(calc.calcular(session, cotacao=cotacao, **dados))


@router.post("/calculadora/salvar")
async def salvar(request: Request, session: Session = Depends(get_session)):
    """Grava no catálogo e, se veio de uma cotação, já adiciona o item nela."""
    form = await request.form()
    dados = _parametros(form)
    calculavel = form.get("calculavel", "sim") == "sim"
    produto = calc.salvar_no_catalogo(
        session, familia=dados["familia"], largura_cm=dados["largura_cm"],
        comprimento_cm=dados["comprimento_cm"], material_id=dados["material_id"],
        gsm=dados["gsm"], plain_or_stripe=dados["plain_or_stripe"],
        acabamento=dados["acabamento"], calculavel=calculavel,
        observacao=form.get("observacao") or None)

    cotacao_id = form.get("cotacao_id")
    if not cotacao_id:
        return JSONResponse({"produto_id": produto.id, "nome": produto.nome,
                             "sem_custo": not bool(produto.custo_unitario)})

    cotacao = session.get(Cotacao, int(cotacao_id))
    regras, _regra, _ctx = montar_regras(cotacao, session)
    margem = ps.margem_padrao(session, produto)
    quantidade = dados["quantidade"] or 1
    custo = produto.custo_unitario or 0.0

    if custo > 0:
        modo, valor = "margem", (dados["margem_override"] or margem.margem_pct)
        resultado = _calcular(modo, custo, quantidade, valor, regras, produto.preco_base)
    else:
        modo, valor = "preco", (produto.preco_base or 0.0)
        resultado = _calcular(modo, 0.0, quantidade, valor, regras, produto.preco_base)

    ordem = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.cotacao_id == cotacao.id)).all()
    item = CotacaoItem(
        cotacao_id=cotacao.id, produto_id=produto.id, ordem=len(ordem),
        nome_produto=produto.nome, especificacao=produto.especificacao,
        categoria=produto.categoria, quantidade=quantidade, custo_unitario=custo,
        preco_base=produto.preco_base or 0.0, modo_edicao=modo, valor_editado=valor)
    _preencher_item(session, item, produto, margem)
    _aplicar_resultado(item, resultado, regras)
    item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
        session, produto, cotacao, preco_negociado=resultado.preco_negociado,
        quantidade=quantidade))
    session.add(item)
    session.commit()
    session.refresh(item)
    return JSONResponse({"produto_id": produto.id, "nome": produto.nome, "item_id": item.id,
                         "cotacao_id": cotacao.id,
                         "sem_custo": not bool(produto.custo_unitario)})

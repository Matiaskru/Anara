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
from app.permissoes import exigir_admin

router = APIRouter()


@router.get("/calculadora", response_class=HTMLResponse)
def pagina(request: Request, cotacao_id: int = 0, session: Session = Depends(get_session)):
    exigir_admin(request)
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
        # fronha (§18): construção. Fora do que o motor aprova, ele mesmo recusa.
        "abas": int(numero("abas", 0) or 0),
        "flap_cm": numero("flap_cm"),
        "festone": (form.get("festone") or "") in ("sim", "on", "1", "true"),
    }


@router.post("/calculadora/calcular")
async def calcular(request: Request, session: Session = Depends(get_session)):
    exigir_admin(request)
    form = await request.form()
    dados = _parametros(form)
    cotacao_id = form.get("cotacao_id")
    cotacao = session.get(Cotacao, int(cotacao_id)) if cotacao_id else None
    return JSONResponse(calc.calcular(session, cotacao=cotacao, **dados))


@router.post("/calculadora/salvar")
async def salvar(request: Request, session: Session = Depends(get_session)):
    """Grava no catálogo e, se veio de uma cotação, já adiciona o item nela."""
    exigir_admin(request)
    form = await request.form()
    dados = _parametros(form)
    calculavel = form.get("calculavel", "sim") == "sim"
    produto = calc.salvar_no_catalogo(
        session, familia=dados["familia"], largura_cm=dados["largura_cm"],
        comprimento_cm=dados["comprimento_cm"], material_id=dados["material_id"],
        gsm=dados["gsm"], plain_or_stripe=dados["plain_or_stripe"],
        acabamento=dados["acabamento"], calculavel=calculavel,
        observacao=form.get("observacao") or None, abas=dados["abas"],
        flap_cm=dados["flap_cm"], festone=dados["festone"])

    cotacao_id = form.get("cotacao_id")
    if not cotacao_id:
        return JSONResponse({"produto_id": produto.id, "nome": produto.nome,
                             "sem_custo": not bool(produto.custo_unitario)})

    cotacao = session.get(Cotacao, int(cotacao_id))
    if cotacao is None:
        return JSONResponse({"erro": "Cotação não encontrada."}, status_code=404)
    # O item entra pelo MESMO caminho de "adicionar produto" na cotação: cenário fiscal
    # resolvido com o produto (KTC depende dele), preço recomendado, política congelada,
    # comissão da cotação reaplicada e aprovação anterior invalidada. Montar o item aqui
    # à parte deixava o produto calculado entrar com preço zero e sem recomendado.
    from app.routers.cotacoes import adicionar_item
    quantidade = dados["quantidade"] or 1
    if produto.custo_unitario:
        modo, valor = "margem", dados["margem_override"]     # None = margem da regra
    else:
        modo, valor = "preco", (produto.preco_base or 0.0)
    resposta = adicionar_item(request, cotacao.id, produto_id=produto.id, quantidade=quantidade,
                              modo=modo, valor=valor, session=session)
    if getattr(resposta, "status_code", 200) >= 400:
        return resposta
    item = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao.id)
                        .where(CotacaoItem.produto_id == produto.id)
                        .order_by(CotacaoItem.id.desc())).first()
    return JSONResponse({"produto_id": produto.id, "nome": produto.nome, "item_id": item.id,
                         "cotacao_id": cotacao.id,
                         "sem_custo": not bool(produto.custo_unitario)})

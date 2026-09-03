import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import arquivamento
from app import config_service as cfg
from app import pricing_service as ps
from app.db import get_session
from app.models import (
    Cliente, CondicaoPagamento, Cotacao, CotacaoItem, EstadoFiscal, Fornecedor, Produto,
    StatusCotacao, TipoFrete,
)
from app.pdf_bridge import gerar_pdf_para_cotacao
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_markup, calcular_por_preco,
)
from app.templating import templates

router = APIRouter()


def estados(session: Session):
    linhas = session.exec(select(EstadoFiscal).order_by(EstadoFiscal.estado)).all()
    return [e.estado for e in linhas if e.ativo]


def proximo_numero(session: Session) -> str:
    """Numeração sequencial por ano. Nunca renumera cotação existente."""
    ano = datetime.utcnow().year
    prefixo = f"ANARA-{ano}-"
    usados = {c.numero for c in session.exec(select(Cotacao)).all() if c.numero}
    n = 1
    while f"{prefixo}{n:04d}" in usados:
        n += 1
    return f"{prefixo}{n:04d}"


def montar_regras(cotacao: Cotacao, session: Session):
    """TaxRuleSet efetivo da cotação + a regra fiscal textual aplicada."""
    regras, contexto = ps.regras_da_cotacao(session, cotacao)
    return regras, contexto["icms_regra"], contexto


def _calcular(modo: str, custo: float, qtd: float, valor: float,
              regras: TaxRuleSet, preco_base=None):
    """Despacha para o modo escolhido: margem (padrão), preço ou markup."""
    if modo == "margem":
        return calcular_por_margem(custo, qtd, valor, regras, preco_base)
    if modo == "markup":
        return calcular_por_markup(custo, qtd, valor, regras, preco_base)
    return calcular_por_preco(custo, qtd, valor, regras, preco_base)


def _item_para_json(it: CotacaoItem) -> dict:
    return {
        "id": it.id, "produto_id": it.produto_id, "ordem": it.ordem,
        "nome_produto": it.nome_produto, "especificacao": it.especificacao,
        "categoria": it.categoria, "quantidade": it.quantidade,
        "custo_unitario": it.custo_unitario, "preco_base": it.preco_base,
        "preco_negociado": it.preco_negociado, "margem_liquida": it.margem_liquida,
        "faturamento": it.faturamento, "custo_total": it.custo_total, "lucro": it.lucro,
        "diferenca_pct_vs_base": it.diferenca_pct_vs_base,
        "modo_edicao": it.modo_edicao, "valor_editado": it.valor_editado,
        "fornecedor_nome": it.fornecedor_nome, "cost_method": it.cost_method,
        "margem_padrao_pct": it.margem_padrao_pct, "margem_regra": it.margem_regra,
        "comissao_pct": it.comissao_pct, "markup_implicito": it.markup_implicito,
    }


def _totais(itens: list) -> dict:
    faturamento = sum(i.faturamento for i in itens)
    custo = sum(i.custo_total for i in itens)
    lucro = sum(i.lucro for i in itens)
    return {"faturamento": faturamento, "custo_total": custo, "lucro": lucro,
            "margem_liquida": (lucro / faturamento) if faturamento else 0.0,
            "num_itens": len(itens)}


# ---------------------------------------------------------------------------
# Listagem / criação / detalhe
# ---------------------------------------------------------------------------
@router.get("/cotacoes", response_class=HTMLResponse)
def listar(request: Request, status: str = "", cliente_id: str = "", vendedor: str = "",
           categoria: str = "", arquivadas: str = "", session: Session = Depends(get_session)):
    todas = session.exec(select(Cotacao).order_by(Cotacao.criado_em.desc())).all()
    itens_por_cotacao = {}
    for it in session.exec(select(CotacaoItem)).all():
        itens_por_cotacao.setdefault(it.cotacao_id, []).append(it)

    mostrar_arquivadas = arquivadas == "sim"
    cotacoes = [c for c in todas if bool(c.arquivada_em) == mostrar_arquivadas]
    total_arquivadas = sum(1 for c in todas if c.arquivada_em)

    if status:
        cotacoes = [c for c in cotacoes if c.status.value == status]
    if cliente_id:
        cotacoes = [c for c in cotacoes if str(c.cliente_id) == cliente_id]
    if vendedor:
        alvo = vendedor.strip().lower()
        cotacoes = [c for c in cotacoes if alvo in (c.vendedor or "").lower()]
    if categoria:
        cotacoes = [c for c in cotacoes
                    if any((i.categoria or "") == categoria for i in itens_por_cotacao.get(c.id, []))]

    clientes = {c.id: c for c in session.exec(select(Cliente)).all()}
    totais = {c.id: _totais(itens_por_cotacao.get(c.id, [])) for c in cotacoes}
    categorias = sorted({i.categoria for i in session.exec(select(CotacaoItem)).all() if i.categoria})
    vendedores = sorted({c.vendedor for c in session.exec(select(Cotacao)).all() if c.vendedor})

    return templates.TemplateResponse(request, "cotacoes_list.html", {
        "active": "cotacoes", "cotacoes": cotacoes, "clientes": clientes, "totais": totais,
        "status_filtro": status, "cliente_filtro": cliente_id, "vendedor_filtro": vendedor,
        "categoria_filtro": categoria, "categorias": categorias, "vendedores": vendedores,
        "mostrar_arquivadas": mostrar_arquivadas, "total_arquivadas": total_arquivadas,
        "itens_por_cotacao": {k: len(v) for k, v in itens_por_cotacao.items()},
        "sugestoes_teste": {s["id"] for s in arquivamento.candidatas_a_teste(session)},
        "todos_clientes": sorted(clientes.values(), key=lambda c: c.nome),
        "status_opcoes": [s.value for s in StatusCotacao],
    })


@router.get("/cotacoes/nova", response_class=HTMLResponse)
def nova_form(request: Request, cliente_id: int = 0, session: Session = Depends(get_session)):
    clientes = session.exec(select(Cliente).order_by(Cliente.nome)).all()
    return templates.TemplateResponse(request, "cotacao_nova.html", {
        "active": "nova_cotacao", "clientes": clientes,
        "cliente_selecionado": session.get(Cliente, cliente_id) if cliente_id else None,
        "estados_difal": estados(session),
        "condicoes": cfg.condicoes_pagamento(session),
        "tipos_frete": [t.value for t in TipoFrete],
        "validade_padrao": int(cfg.num(session, "validade_dias", 5)),
    })


@router.post("/cotacoes")
def criar(request: Request, cliente_id: int = Form(...), condicao_pagamento: str = Form("30"),
          estado_destino: str = Form(""), estado_origem: str = Form("São Paulo"),
          contribuinte_icms: str = Form("sim"), frete: str = Form(""),
          freight_type: str = Form(TipoFrete.cif.value), prazo_entrega: str = Form(""),
          contato_nome: str = Form(""), departamento_contato: str = Form(""),
          validade_dias: int = Form(0), vendedor: str = Form(""), observacoes: str = Form(""),
          session: Session = Depends(get_session)):
    dias = validade_dias or int(cfg.num(session, "validade_dias", 5))
    agora = datetime.utcnow()
    cotacao = Cotacao(
        criado_em=agora,
        numero=proximo_numero(session), cliente_id=cliente_id, vendedor=vendedor or None,
        condicao_pagamento=condicao_pagamento or "30",
        estado_destino=estado_destino or None, estado_origem=estado_origem or "São Paulo",
        contribuinte_icms=(contribuinte_icms == "sim"), frete=frete or None,
        freight_type=freight_type or TipoFrete.cif.value,
        prazo_entrega=prazo_entrega or None, contato_nome=contato_nome or None,
        departamento_contato=departamento_contato or None,
        observacoes=observacoes or None, validade_dias=dias,
        validade_em=agora + timedelta(days=dias),
        termos_texto=cfg.txt(session, "termos_padrao"),
    )
    _gravar_snapshot_fiscal(session, cotacao)
    session.add(cotacao)
    session.commit()
    session.refresh(cotacao)
    return RedirectResponse(url=f"/cotacoes/{cotacao.id}", status_code=303)


def _gravar_snapshot_fiscal(session: Session, cotacao: Cotacao):
    regras, contexto = ps.regras_da_cotacao(session, cotacao)
    cotacao.icms_aplicado = contexto["icms_pct"]
    cotacao.icms_regra = contexto["icms_regra"]
    cotacao.pis_cofins_pct = contexto["pis_cofins_pct"]
    cotacao.encargo_financeiro_pct = contexto["encargo_pct"]
    return regras, contexto


@router.get("/cotacoes/{cotacao_id}", response_class=HTMLResponse)
def detalhe(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    cliente = session.get(Cliente, cotacao.cliente_id)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)
                         .order_by(CotacaoItem.ordem)).all()
    _regras, regra_icms, contexto = montar_regras(cotacao, session)
    return templates.TemplateResponse(request, "cotacao_detail.html", {
        "active": "cotacoes", "cotacao": cotacao, "cliente": cliente, "itens": itens,
        "totais": _totais(itens), "status_opcoes": [s.value for s in StatusCotacao],
        "estados_difal": estados(session), "regra_icms_atual": regra_icms,
        "contexto_fiscal": contexto, "condicoes": cfg.condicoes_pagamento(session),
        "tipos_frete": [t.value for t in TipoFrete],
        "avisos_exclusao": arquivamento.motivos_para_pensar_duas_vezes(cotacao, len(itens)),
    })


@router.post("/cotacoes/{cotacao_id}/atualizar")
def atualizar_cabecalho(cotacao_id: int, condicao_pagamento: str = Form("30"),
                        estado_destino: str = Form(""), estado_origem: str = Form("São Paulo"),
                        contribuinte_icms: str = Form("sim"), frete: str = Form(""),
                        freight_type: str = Form(TipoFrete.cif.value),
                        freight_valor: str = Form(""), prazo_entrega: str = Form(""),
                        contato_nome: str = Form(""), departamento_contato: str = Form(""),
                        validade_dias: int = Form(0), vendedor: str = Form(""),
                        observacoes: str = Form(""), termos_texto: str = Form(""),
                        session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)

    novo_contribuinte = (contribuinte_icms == "sim")
    mudou_precificacao = (cotacao.condicao_pagamento != condicao_pagamento
                          or cotacao.estado_destino != (estado_destino or None)
                          or cotacao.estado_origem != estado_origem
                          or cotacao.contribuinte_icms != novo_contribuinte)

    cotacao.condicao_pagamento = condicao_pagamento or "30"
    cotacao.estado_destino = estado_destino or None
    cotacao.estado_origem = estado_origem or "São Paulo"
    cotacao.contribuinte_icms = novo_contribuinte
    cotacao.frete = frete or None
    cotacao.freight_type = freight_type or TipoFrete.cif.value
    cotacao.freight_valor = float(freight_valor) if freight_valor else None
    cotacao.prazo_entrega = prazo_entrega or None
    cotacao.contato_nome = contato_nome or None
    cotacao.departamento_contato = departamento_contato or None
    cotacao.vendedor = vendedor or None
    cotacao.observacoes = observacoes or None
    cotacao.termos_texto = termos_texto or cotacao.termos_texto
    if validade_dias:
        cotacao.validade_dias = validade_dias
        base = cotacao.criado_em or datetime.utcnow()
        cotacao.validade_em = base + timedelta(days=validade_dias)
    _gravar_snapshot_fiscal(session, cotacao)
    session.add(cotacao)
    session.commit()

    if mudou_precificacao:
        _recalcular_todos_itens(cotacao, session)

    return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)


def _recalcular_todos_itens(cotacao: Cotacao, session: Session):
    regras, _regra, _ctx = montar_regras(cotacao, session)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao.id)).all()
    for it in itens:
        res = _calcular(it.modo_edicao, it.custo_unitario, it.quantidade, it.valor_editado,
                        regras, it.preco_base)
        _aplicar_resultado(it, res, regras)
        session.add(it)
    session.commit()


def _aplicar_resultado(it: CotacaoItem, res, regras: TaxRuleSet = None):
    it.preco_negociado = res.preco_negociado
    it.margem_liquida = res.margem_liquida
    it.faturamento = res.faturamento
    it.custo_total = res.custo_total
    it.lucro = res.lucro
    it.diferenca_pct_vs_base = res.diferenca_pct_vs_base
    it.impostos = res.impostos
    it.comissao_valor = res.comissao
    it.markup_implicito = res.markup_implicito
    if regras:
        it.comissao_pct = regras.comissao_para_markup(res.markup_implicito)


# ---------------------------------------------------------------------------
# Cálculo ao vivo
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/calc")
def calc(cotacao_id: int, produto_id: int = Form(...), quantidade: float = Form(...),
         modo: str = Form(...), valor: float = Form(...),
         session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    produto = session.get(Produto, produto_id)
    if not cotacao or not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    regras, _regra, _ctx = montar_regras(cotacao, session)
    margem = ps.margem_padrao(session, produto)
    if not produto.custo_unitario:
        return JSONResponse({
            "sem_custo": True, "preco_base": produto.preco_base,
            "margem_padrao_pct": margem.margem_pct, "margem_regra": margem.regra,
            "aviso": ("Produto sem custo cadastrado. Dá para cotar pelo preço, mas margem e "
                      "lucro não podem ser calculados até o custo entrar."),
            "preco_negociado": valor if modo == "preco" else (produto.preco_base or 0),
            "faturamento": (valor if modo == "preco" else (produto.preco_base or 0)) * quantidade,
            "custo_total": 0, "lucro": 0, "margem_liquida": 0,
            "diferenca_pct_vs_base": None,
        })

    res = _calcular(modo, produto.custo_unitario, quantidade, valor, regras, produto.preco_base)
    return JSONResponse({
        "preco_negociado": res.preco_negociado, "faturamento": res.faturamento,
        "custo_total": res.custo_total, "lucro": res.lucro, "margem_liquida": res.margem_liquida,
        "diferenca_pct_vs_base": res.diferenca_pct_vs_base, "preco_base": produto.preco_base,
        "markup_implicito": res.markup_implicito,
        "comissao_pct": regras.comissao_para_markup(res.markup_implicito),
        "margem_padrao_pct": margem.margem_pct, "margem_regra": margem.regra,
        "sem_custo": False,
    })


# ---------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------
def _preencher_item(session: Session, item: CotacaoItem, produto: Produto, margem):
    fornecedor = session.get(Fornecedor, produto.fornecedor_id) if produto.fornecedor_id else None
    item.fornecedor_id = produto.fornecedor_id
    item.fornecedor_nome = fornecedor.nome if fornecedor else None
    item.cost_method = produto.cost_method
    item.margem_padrao_pct = margem.margem_pct
    item.margem_regra = margem.regra


@router.post("/cotacoes/{cotacao_id}/itens")
def adicionar_item(cotacao_id: int, produto_id: int = Form(...), quantidade: float = Form(...),
                   modo: str = Form("margem"), valor: float = Form(None),
                   session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    produto = session.get(Produto, produto_id)
    if not cotacao or not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    regras, _regra, _ctx = montar_regras(cotacao, session)
    margem = ps.margem_padrao(session, produto)
    if valor is None:
        valor = margem.margem_pct if modo == "margem" else (produto.preco_base or 0)

    custo = produto.custo_unitario or 0.0
    if custo <= 0:
        # sem custo: cota pelo preço, margem fica em branco (não se inventa margem)
        preco = valor if modo == "preco" else (produto.preco_base or 0)
        res = _calcular("preco", 0.0, quantidade, preco, regras, produto.preco_base)
        modo, valor = "preco", preco
    else:
        res = _calcular(modo, custo, quantidade, valor, regras, produto.preco_base)

    ordem_atual = session.exec(select(CotacaoItem)
                               .where(CotacaoItem.cotacao_id == cotacao_id)).all()
    item = CotacaoItem(
        cotacao_id=cotacao_id, produto_id=produto.id, ordem=len(ordem_atual),
        nome_produto=produto.nome, especificacao=produto.especificacao,
        categoria=produto.categoria, quantidade=quantidade, custo_unitario=custo,
        preco_base=produto.preco_base or 0.0, modo_edicao=modo, valor_editado=valor,
    )
    _preencher_item(session, item, produto, margem)
    _aplicar_resultado(item, res, regras)
    item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
        session, produto, cotacao, preco_negociado=res.preco_negociado, quantidade=quantidade))
    session.add(item)
    session.commit()
    session.refresh(item)
    return JSONResponse(_item_para_json(item))


@router.put("/cotacoes/{cotacao_id}/itens/{item_id}")
async def editar_item(cotacao_id: int, item_id: int, request: Request,
                      session: Session = Depends(get_session)):
    form = await request.form()
    quantidade = float(form.get("quantidade"))
    modo = form.get("modo", "preco")
    valor = float(form.get("valor"))

    cotacao = session.get(Cotacao, cotacao_id)
    item = session.get(CotacaoItem, item_id)
    if not cotacao or not item or item.cotacao_id != cotacao_id:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    regras, _regra, _ctx = montar_regras(cotacao, session)
    res = _calcular(modo, item.custo_unitario, quantidade, valor, regras, item.preco_base)

    item.quantidade = quantidade
    item.modo_edicao = modo
    item.valor_editado = valor
    _aplicar_resultado(item, res, regras)
    produto = session.get(Produto, item.produto_id) if item.produto_id else None
    if produto:
        item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
            session, produto, cotacao, preco_negociado=res.preco_negociado,
            quantidade=quantidade))
    session.add(item)
    session.commit()
    session.refresh(item)
    return JSONResponse(_item_para_json(item))


@router.get("/cotacoes/{cotacao_id}/itens/{item_id}/memoria")
def memoria_item(cotacao_id: int, item_id: int, session: Session = Depends(get_session)):
    """Memória do preço congelada no item — como aquele preço foi formado."""
    item = session.get(CotacaoItem, item_id)
    if not item or item.cotacao_id != cotacao_id:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    if item.memoria_json:
        return JSONResponse(json.loads(item.memoria_json))
    produto = session.get(Produto, item.produto_id) if item.produto_id else None
    cotacao = session.get(Cotacao, cotacao_id)
    if not produto:
        return JSONResponse({"erro": "item sem produto vinculado"}, status_code=404)
    return JSONResponse(ps.memoria_do_preco(session, produto, cotacao,
                                            preco_negociado=item.preco_negociado,
                                            quantidade=item.quantidade))


@router.delete("/cotacoes/{cotacao_id}/itens/{item_id}")
def remover_item(cotacao_id: int, item_id: int, session: Session = Depends(get_session)):
    item = session.get(CotacaoItem, item_id)
    if item and item.cotacao_id == cotacao_id:
        session.delete(item)
        session.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Status / aceite / duplicar / PDF
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/status")
def mudar_status(cotacao_id: int, status: str = Form(...),
                 session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao:
        cotacao.status = StatusCotacao(status)
        if cotacao.status in (StatusCotacao.enviada, StatusCotacao.fechada) and not cotacao.emitida_em:
            cotacao.emitida_em = datetime.utcnow()
            if not cotacao.termos_texto:
                cotacao.termos_texto = cfg.txt(session, "termos_padrao")
        session.add(cotacao)
        session.commit()
    return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)


@router.post("/cotacoes/{cotacao_id}/aceite")
def registrar_aceite(cotacao_id: int, aceite_responsavel: str = Form(""),
                     aceite_cargo: str = Form(""), aceite_departamento: str = Form(""),
                     local_entrega: str = Form(""), endereco_entrega: str = Form(""),
                     observacoes_pedido: str = Form(""), virar_pedido: str = Form(""),
                     session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    cotacao.aceite_responsavel = aceite_responsavel or None
    cotacao.aceite_cargo = aceite_cargo or None
    cotacao.aceite_departamento = aceite_departamento or None
    cotacao.local_entrega = local_entrega or None
    cotacao.endereco_entrega = endereco_entrega or None
    cotacao.observacoes_pedido = observacoes_pedido or None
    if aceite_responsavel and not cotacao.aceite_em:
        cotacao.aceite_em = datetime.utcnow()
    if virar_pedido == "sim":
        cotacao.status = StatusCotacao.pedido
    session.add(cotacao)
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)


@router.post("/cotacoes/{cotacao_id}/duplicar")
def duplicar(cotacao_id: int, session: Session = Depends(get_session)):
    original = session.get(Cotacao, cotacao_id)
    if not original:
        return RedirectResponse(url="/cotacoes", status_code=303)

    dias = original.validade_dias or int(cfg.num(session, "validade_dias", 5))
    agora = datetime.utcnow()
    nova = Cotacao(
        criado_em=agora,
        numero=proximo_numero(session), cliente_id=original.cliente_id,
        vendedor=original.vendedor, condicao_pagamento=original.condicao_pagamento,
        frete=original.frete, freight_type=original.freight_type,
        prazo_entrega=original.prazo_entrega, contato_nome=original.contato_nome,
        departamento_contato=original.departamento_contato,
        estado_destino=original.estado_destino, estado_origem=original.estado_origem,
        contribuinte_icms=original.contribuinte_icms, observacoes=original.observacoes,
        validade_dias=dias, validade_em=agora + timedelta(days=dias),
        termos_texto=cfg.txt(session, "termos_padrao"),
    )
    _gravar_snapshot_fiscal(session, nova)
    session.add(nova)
    session.commit()
    session.refresh(nova)

    regras, _regra, _ctx = montar_regras(nova, session)
    itens_originais = session.exec(select(CotacaoItem)
                                   .where(CotacaoItem.cotacao_id == cotacao_id)
                                   .order_by(CotacaoItem.ordem)).all()
    for it in itens_originais:
        produto_atual = session.get(Produto, it.produto_id) if it.produto_id else None
        preco_base_atual = produto_atual.preco_base if produto_atual else it.preco_base
        custo_atual = (produto_atual.custo_unitario if produto_atual else it.custo_unitario) or 0.0

        res = calcular_por_preco(custo_atual, it.quantidade, it.preco_negociado, regras,
                                 preco_base_atual)
        novo_item = CotacaoItem(
            cotacao_id=nova.id, produto_id=it.produto_id, ordem=it.ordem,
            nome_produto=it.nome_produto, especificacao=it.especificacao,
            categoria=it.categoria, quantidade=it.quantidade, custo_unitario=custo_atual,
            preco_base=preco_base_atual or 0.0, modo_edicao="preco",
            valor_editado=it.preco_negociado,
        )
        if produto_atual:
            _preencher_item(session, novo_item, produto_atual,
                            ps.margem_padrao(session, produto_atual))
        _aplicar_resultado(novo_item, res, regras)
        session.add(novo_item)
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{nova.id}", status_code=303)


@router.get("/cotacoes/{cotacao_id}/pdf")
def gerar_pdf(cotacao_id: int, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    cliente = session.get(Cliente, cotacao.cliente_id)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)
                         .order_by(CotacaoItem.ordem)).all()

    if not cotacao.termos_texto:
        cotacao.termos_texto = cfg.txt(session, "termos_padrao")
    if not cotacao.emitida_em:
        cotacao.emitida_em = datetime.utcnow()

    out_path = gerar_pdf_para_cotacao(cotacao, cliente, itens)
    cotacao.pdf_gerado_em = datetime.utcnow()
    session.add(cotacao)
    session.commit()

    nome = f"Cotação Anara {cotacao.numero or cotacao.id} - {cliente.nome if cliente else 'Cliente'}.pdf"
    return FileResponse(out_path, media_type="application/pdf", filename=nome)


# ---------------------------------------------------------------------------
# Arquivar e apagar
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/arquivar")
def arquivar(cotacao_id: int, motivo: str = Form(""), session: Session = Depends(get_session)):
    """Tira da lista e dos totais, sem destruir nada."""
    arquivamento.arquivar(session, cotacao_id, motivo or None)
    return RedirectResponse(url="/cotacoes", status_code=303)


@router.post("/cotacoes/{cotacao_id}/restaurar")
def restaurar(cotacao_id: int, session: Session = Depends(get_session)):
    arquivamento.restaurar(session, cotacao_id)
    return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)


@router.post("/cotacoes/{cotacao_id}/apagar")
def apagar(cotacao_id: int, confirmar: str = Form(""), session: Session = Depends(get_session)):
    """Apaga de vez. Só funciona no que já está arquivado, e copia o banco antes."""
    if confirmar != "sim":
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}?erro=confirmacao", status_code=303)
    resultado = arquivamento.apagar(session, cotacao_id)
    if not resultado.get("ok"):
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}?erro=arquivar_antes", status_code=303)
    return RedirectResponse(url="/cotacoes?apagada=" + (resultado.get("numero") or ""),
                            status_code=303)


@router.post("/cotacoes/lote")
async def acao_em_lote(request: Request, session: Session = Depends(get_session)):
    """Arquivar ou apagar várias de uma vez — é o caso de limpar teste acumulado."""
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    acao = form.get("acao")
    if not ids:
        return RedirectResponse(url="/cotacoes", status_code=303)

    if acao == "arquivar":
        n = arquivamento.arquivar_em_lote(session, ids, form.get("motivo") or "Limpeza de testes")
        return RedirectResponse(url=f"/cotacoes?arquivadas_agora={n}", status_code=303)
    if acao == "restaurar":
        for cotacao_id in ids:
            arquivamento.restaurar(session, cotacao_id)
        return RedirectResponse(url="/cotacoes?arquivadas=sim", status_code=303)
    if acao == "apagar":
        resultado = arquivamento.apagar_em_lote(session, ids)
        return RedirectResponse(
            url=f"/cotacoes?arquivadas=sim&apagadas={resultado['apagadas']}", status_code=303)
    return RedirectResponse(url="/cotacoes", status_code=303)

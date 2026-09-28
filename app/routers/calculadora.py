"""Calculadora — produto personalizado, na tela avulsa e dentro da cotação.

## Quem entra (22/09/2026)

A vendedora é quem monta a cotação, e cotação boa precisa de produto que ainda não está no
catálogo. Por isso a calculadora é de **todo papel autenticado** (`opera_cotacao`), e não de
administrador: até 22/09/2026 ela exigia `exigir_admin`, o que obrigava a vendedora a pedir a
alguém para calcular uma fronha com aba diferente — bloqueio errado para a operação.

O que muda por papel **não é o direito de calcular; é o que a resposta carrega de volta**:

* OWNER/ADMIN recebem a memória do preço inteira (custo, EXW, nacionalização, base comercial,
  proteção, margem, lucro) — exatamente como antes;
* VENDEDOR_INTERNO/VENDEDOR_COMISSIONADO recebem o **resultado comercial**
  (`calculadora.resultado_comercial`): descrição, B2B, tabela, preço proposto, desconto, a
  comissão dela, total e a situação em linguagem comercial. Nada de custo é montado na
  resposta — não há campo escondido no HTML para o devtools achar.

Dois inputs também são economia e são **ignorados** para quem não a vê: a margem forçada
(`margem_pct`) e os outros custos por peça (`outros_custos_usd`). Não é só questão de sigilo:
a margem forçada é a alavanca que formaria um B2B abaixo do piso da política.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import calculadora as calc
from app import pricing_service as ps
from app.db import get_session
from app.models import Cotacao, CotacaoItem, Fornecedor, Produto
from app import config_service as cfg
from app.routers.cotacoes import (_aplicar_resultado, _calcular, _preencher_item, estados,
                                  montar_regras)
from app.templating import templates
from app.permissoes import exigir_autenticado, ve_economia

router = APIRouter()


@router.get("/calculadora", response_class=HTMLResponse)
def pagina(request: Request, cotacao_id: int = 0, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    economia = ve_economia(request)
    cotacao = session.get(Cotacao, cotacao_id) if cotacao_id else None
    cenario = cotacao or ps.cenario_padrao_catalogo(session)
    # O contexto fiscal só é montado para quem vê economia: a decomposição (ICMS, DIFAL,
    # PIS/COFINS, encargo) é interna, e não se manda ao navegador o que a tela não mostra.
    _regras, contexto = ps.regras_da_cotacao(session, cenario) if economia else (None, None)
    return templates.TemplateResponse(request, "calculadora.html", {
        "active": "calculadora", "opcoes": calc.opcoes(session, economia=economia), "cotacao": cotacao,
        "contexto_fiscal": contexto, "cenario": cenario, "economia": economia,
        # Aberta pela aba (sem cotação), o cenário é ESCOLHIDO na tela — ver `_cenario_do_form`.
        "estados_difal": estados(session), "condicoes": cfg.condicoes_pagamento(session),
    })


def _cenario_do_form(session: Session, form) -> Cotacao:
    """O cenário da simulação, montado do formulário — **transitório, nunca gravado**.

    Sem cotação, a calculadora precisa de destino, contribuinte e condição para formar preço:
    são eles que decidem ICMS, DIFAL, FCP e encargo, e o mesmo SKU varia quase 50% entre
    cenários. Até 25/09/2026 ela usava o padrão do catálogo em silêncio, o que era aceitável
    enquanto se chegava nela por dentro de uma cotação — e deixou de ser quando ela ganhou
    aba própria.

    O objeto NÃO entra na sessão: é parâmetro de cálculo, não documento. Cotação de verdade
    nasce em `/cotacoes`, dentro de uma venda, com cliente e numeração.
    """
    padrao = ps.cenario_padrao_catalogo(session)
    destino = (form.get("estado_destino") or "").strip() or padrao.estado_destino
    condicao = (form.get("condicao_pagamento") or "").strip() or padrao.condicao_pagamento
    # `contribuinte` é o nome que a tela envia; `contribuinte_icms` é aceito para quem postar
    # com o nome do atributo (o formulário da cotação usa esse).
    contribuinte = form.get("contribuinte", form.get("contribuinte_icms"))
    return Cotacao(
        cliente_id=padrao.cliente_id, estado_origem=padrao.estado_origem,
        uf_origem_fiscal=padrao.uf_origem_fiscal, finalidade=padrao.finalidade,
        estado_destino=destino, condicao_pagamento=condicao,
        contribuinte_icms=(contribuinte == "sim") if contribuinte is not None
        else padrao.contribuinte_icms,
        freight_type=padrao.freight_type, percentual_sinal=padrao.percentual_sinal or 0.0)


def _parametros(form, economia: bool = True) -> dict:
    def numero(chave, padrao=None):
        valor = (form.get(chave) or "").strip()
        if not valor:
            return padrao
        try:
            return float(valor.replace(",", "."))
        except ValueError:
            return padrao

    # Margem forçada e outros custos são alavancas ECONÔMICAS: quem não vê economia não as
    # envia (a tela nem as mostra) e, se enviar, o servidor as ignora — a política forma o
    # preço, não o formulário.
    margem = numero("margem_pct") if economia else None
    outros = (numero("outros_custos_usd", 0.0) or 0.0) if economia else 0.0
    return {
        "familia": form.get("familia") or "",
        "largura_cm": numero("largura_cm"),
        "comprimento_cm": numero("comprimento_cm"),
        "material_id": int(numero("material_id") or 0) or None,
        "gsm": int(numero("gsm") or 0) or None,
        "plain_or_stripe": form.get("plain_or_stripe") or "plain",
        "quantidade": numero("quantidade", 1) or 1,
        "outros_custos_usd": outros,
        "margem_override": (margem / 100 if margem and margem > 1 else margem),
        "acabamento": form.get("acabamento") or None,
        # fronha (§18): construção. Fora do que o motor aprova, ele mesmo recusa.
        "abas": int(numero("abas", 0) or 0),
        "flap_cm": numero("flap_cm"),
        "festone": (form.get("festone") or "") in ("sim", "on", "1", "true"),
        "composicao_toalha": form.get("composicao_toalha") or None,
    }


@router.post("/calculadora/calcular")
async def calcular(request: Request, session: Session = Depends(get_session)):
    exigir_autenticado(request)
    economia = ve_economia(request)
    form = await request.form()
    dados = _parametros(form, economia=economia)
    cotacao_id = form.get("cotacao_id")
    cotacao = (session.get(Cotacao, int(cotacao_id)) if cotacao_id
               else _cenario_do_form(session, form))
    # A conta é a mesma para todo mundo — o mesmo `pricing_service`/`pricing_engine`. O corte
    # é na fronteira da resposta, e é construção por lista de permissão, não remoção.
    memoria = calc.calcular(session, cotacao=cotacao, **dados)
    return JSONResponse(memoria if economia else calc.resultado_comercial(memoria))


@router.post("/calculadora/consultar")
async def consultar(request: Request, session: Session = Depends(get_session)):
    """Preço de um produto do catálogo no cenário pedido — consulta, não cotação.

    Mesmo corte de sempre: OWNER/ADMIN recebem a memória inteira, a vendedora recebe o
    resultado comercial. E, como em toda a calculadora, **nada é gravado**.
    """
    exigir_autenticado(request)
    economia = ve_economia(request)
    form = await request.form()
    produto = session.get(Produto, int(form.get("produto_id") or 0))
    if produto is None:
        return JSONResponse({"erro": "Produto não encontrado."}, status_code=404)
    cotacao_id = form.get("cotacao_id")
    cotacao = (session.get(Cotacao, int(cotacao_id)) if cotacao_id
               else _cenario_do_form(session, form))
    bruto = (form.get("desconto_pct") or "").strip().replace(",", ".")
    try:
        desconto = float(bruto) / 100 if bruto else None
    except ValueError:
        desconto = None
    if desconto is not None and not (0 <= desconto < 1):
        return JSONResponse({"erro": "Desconto precisa ficar entre 0% e 99%."}, status_code=400)
    memoria = calc.consultar_catalogo(session, produto, cotacao=cotacao, desconto_pct=desconto)
    return JSONResponse(memoria if economia else calc.resultado_comercial(memoria))


@router.post("/calculadora/salvar")
async def salvar(request: Request, session: Session = Depends(get_session)):
    """Grava no catálogo e, se veio de uma cotação, já adiciona o item nela."""
    exigir_autenticado(request)
    economia = ve_economia(request)
    form = await request.form()
    dados = _parametros(form, economia=economia)
    calculavel = form.get("calculavel", "sim") == "sim"
    try:
        produto = calc.salvar_no_catalogo(
            session, familia=dados["familia"], largura_cm=dados["largura_cm"],
            comprimento_cm=dados["comprimento_cm"], material_id=dados["material_id"],
            gsm=dados["gsm"], plain_or_stripe=dados["plain_or_stripe"],
            acabamento=dados["acabamento"], calculavel=calculavel,
            observacao=form.get("observacao") or None, abas=dados["abas"],
            flap_cm=dados["flap_cm"], festone=dados["festone"],
            composicao_toalha=dados["composicao_toalha"])
    except calc.EntradaInvalida as e:
        return JSONResponse({"erro": str(e)}, status_code=400)

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
    # A decisão "tem custo para formar preço?" usa o custo VIVO (premissas vigentes), o
    # mesmo que `adicionar_item` vai resolver — nunca a coluna cache do produto.
    custo_vivo, _memoria = ps.custo_para_precificar(session, produto)
    if custo_vivo:
        modo, valor = "margem", dados["margem_override"]     # None = margem da regra
    else:
        modo, valor = "preco", 0.0                   # sem custo: sem preço até alguém digitar
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

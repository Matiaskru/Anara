import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import admin_service as adm
from app import arquivamento
from app import config_service as cfg
from app import pricing_service as ps
from app.confidencial import item_comercial, sem_confidenciais, totais_comerciais
from app.db import get_session
from app.permissoes import (
    exigir_autenticado, exigir_economia, usuario_da_request, ve_economia,
)
from app import workflow as wf
from app import workflow_service as ws
from app.dinheiro import D0, ZERO, dinheiro, divide, para_float, soma
from app.models import (
    Cliente, CondicaoPagamento, Cotacao, CotacaoItem, EstadoFiscal, Fornecedor, Produto,
    StatusCotacao, TipoFrete,
)
from app.pdf_bridge import gerar_pdf_para_cotacao
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_markup, calcular_por_preco,
    icms_excluido_da_base, pis_cofins_efetivo,
)
from app.templating import pagina_de_erro, templates
from app import rotulos

router = APIRouter()


def estados(session: Session):
    linhas = session.exec(select(EstadoFiscal).order_by(EstadoFiscal.estado)).all()
    return [e.estado for e in linhas if e.ativo]


def _valor_status(cotacao) -> str:
    """O status como string, venha ele como enum ou como texto do banco."""
    s = getattr(cotacao, "status", "")
    return s.value if hasattr(s, "value") else str(s or "")


def _origem_fiscal_da_tela(session: Session, cotacao: Cotacao) -> dict:
    """A UF de origem FISCAL da operação, e de onde ela veio.

    **Não é a origem logística.** A NF sai de um lugar fiscal que a rota do caminhão não
    determina: Itajaí-SC ser o ponto de entrada da mercadoria importada não prova a origem
    fiscal da venda. A tela nomeia as duas coisas por extenso justamente porque um campo
    chamado só "Origem" fazia quem preenchia não saber qual das duas estava respondendo.
    """
    uf, fonte = ps.uf_origem_fiscal(session, cotacao)
    return {"uf": uf, "fonte": fonte, "definida_na_cotacao": bool(cotacao.uf_origem_fiscal)}


def _origens_logisticas(session: Session, cotacao: Cotacao, itens) -> list:
    """De onde cada grupo de itens embarca — uma linha por origem, sem achatar.

    Uma cotação pode ter itens de fornecedores que saem de lugares diferentes, e o frete de
    cada embarque é calculado separado. Resumir isso numa "origem da cotação" inventaria
    uma carga que não existe.
    """
    if not itens:
        return []
    from app import frete_service as fs

    achados = []
    for grupo in fs.agrupar_itens(session, cotacao, itens):
        fornecedor = (session.get(Fornecedor, grupo["fornecedor_id"])
                      if grupo["fornecedor_id"] else None)
        achados.append({
            "cidade": grupo.get("origem_cidade"),
            "uf": grupo.get("origem_uf"),
            "fornecedor": fornecedor.nome if fornecedor else None,
            "itens": len(grupo.get("itens") or []),
            "conhecida": bool(grupo.get("origem_cidade") and grupo.get("origem_uf")),
        })
    return achados


def proximo_numero(session: Session) -> str:
    """Numeração sequencial por ano. Nunca renumera cotação existente."""
    ano = datetime.utcnow().year
    prefixo = f"ANARA-{ano}-"
    usados = {c.numero for c in session.exec(select(Cotacao)).all() if c.numero}
    n = 1
    while f"{prefixo}{n:04d}" in usados:
        n += 1
    return f"{prefixo}{n:04d}"


def montar_regras(cotacao: Cotacao, session: Session, produto=None):
    """TaxRuleSet efetivo **do item** + a regra fiscal textual aplicada.

    `regras` volta None quando o fiscal ou a condição de pagamento não se resolveram — nesse
    caso não existe preço confiável a formar, e o chamador grava o bloqueio no item.
    """
    regras, contexto = ps.regras_da_cotacao(session, cotacao, produto)
    return regras, contexto["icms_regra"], contexto


def bloqueios_fiscais(itens) -> list:
    """Itens cujo cenário não se resolveu. Lista vazia = documento pode ser emitido."""
    motivos = []
    for it in itens:
        if it.status_fiscal == "REVIEW_REQUIRED":
            motivos.append(f"Item '{it.nome_produto}': {it.motivo_fiscal}")
        if it.status_pagamento == "REVIEW_REQUIRED":
            motivos.append(f"Item '{it.nome_produto}': {it.motivo_pagamento}")
    return motivos


def _resultado_bloqueado(qtd: float, custo: float):
    """Resultado neutro para item cujo cenário não se resolve. Não inventa preço."""
    from app.pricing_engine import ResultadoPrecificacao
    return ResultadoPrecificacao(
        preco_negociado=ZERO, quantidade=D0(qtd), faturamento=ZERO,
        custo_total=dinheiro(D0(custo) * D0(qtd)), impostos=ZERO, comissao=ZERO,
        lucro=ZERO, margem_liquida=ZERO, markup_implicito=ZERO, diferenca_pct_vs_base=None)


def _calcular(modo: str, custo: float, qtd: float, valor: float,
              regras: TaxRuleSet, preco_base=None):
    """Despacha para o modo escolhido: margem (padrão), preço ou markup.

    `regras is None` significa cenário irresolvido: devolve resultado zerado em vez de um preço
    que pareceria confiável.
    """
    if regras is None:
        return _resultado_bloqueado(qtd, custo)
    if modo == "margem":
        return calcular_por_margem(custo, qtd, valor, regras, preco_base)
    if modo == "markup":
        return calcular_por_markup(custo, qtd, valor, regras, preco_base)
    return calcular_por_preco(custo, qtd, valor, regras, preco_base)


def _item_para_json(it: CotacaoItem, pode_ver_economia: bool = True) -> dict:
    """Item para o JavaScript da tela.

    O default é o payload interno porque quase todo chamador aqui é admin; quem serve
    vendedor passa `pode_ver_economia=False` e recebe a versão comercial. Deixar o corte
    explícito no chamador é de propósito: um endpoint novo que esquecer o parâmetro aparece
    no teste de payload, em vez de silenciosamente herdar a versão permissiva de um default
    escondido.
    """
    completo = {
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
    return completo if pode_ver_economia else item_comercial(completo)


def _totais(itens: list, pode_ver_economia: bool = True) -> dict:
    """Total da cotação = soma exata das linhas.

    A soma é em `Decimal`: somar 45 floats de 2 casas acumula erro binário e o total da
    cotação deixa de bater com a soma que o cliente confere no PDF.

    Para quem não vê economia sobram faturamento e número de itens — o que ele vai cobrar.
    Custo, lucro e margem do documento inteiro saem do dicionário, não da tela.
    """
    faturamento = soma(i.faturamento for i in itens)
    custo = soma(i.custo_total for i in itens)
    lucro = soma(i.lucro for i in itens)
    completo = {"faturamento": para_float(faturamento), "custo_total": para_float(custo),
                "lucro": para_float(lucro),
                "margem_liquida": para_float(divide(lucro, faturamento) or ZERO),
                "num_itens": len(itens)}
    return completo if pode_ver_economia else totais_comerciais(completo)


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
    economia = ve_economia(request)
    totais = {c.id: _totais(itens_por_cotacao.get(c.id, []), economia) for c in cotacoes}
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
        # Aqui é FILTRO, e por isso a lista é completa: os estados herdados precisam ser
        # filtráveis para que as cotações antigas continuem encontráveis. Na tela de
        # detalhe, onde `status_opcoes` vira botão de ação, a lista é outra.
        "status_opcoes": [s.value for s in StatusCotacao],
    })


@router.get("/cotacoes/nova", response_class=HTMLResponse)
def nova_form(request: Request, cliente_id: int = 0, oportunidade_id: int = 0,
              session: Session = Depends(get_session)):
    """Formulário mínimo. Quando vem de uma oportunidade, já chega com o cliente dela."""
    from app import crm_service as crm
    from app.models import Oportunidade

    oportunidade = session.get(Oportunidade, oportunidade_id) if oportunidade_id else None
    if oportunidade is not None and not cliente_id:
        cliente_id = oportunidade.cliente_id

    clientes = session.exec(select(Cliente).order_by(Cliente.nome)).all()
    return templates.TemplateResponse(request, "cotacao_nova.html", {
        "active": "nova_cotacao", "clientes": clientes,
        "cliente_selecionado": session.get(Cliente, cliente_id) if cliente_id else None,
        "contatos": crm.contatos_de(session, cliente_id, apenas_ativos=True) if cliente_id else [],
        "oportunidade": oportunidade,
        "estados_difal": estados(session),
        "condicoes": cfg.condicoes_pagamento(session),
        "tipos_frete": [t.value for t in TipoFrete],
    })


@router.get("/clientes/{cliente_id}/contatos.json")
def contatos_do_cliente(request: Request, cliente_id: int,
                        session: Session = Depends(get_session)):
    """Contatos de um cliente, para o formulário trocar a lista sem recarregar a página."""
    from app import crm_service as crm

    return JSONResponse([{"id": c.id, "nome": c.nome, "cargo": c.cargo or ""}
                         for c in crm.contatos_de(session, cliente_id, apenas_ativos=True)])


def _herdar_da_operacao(session: Session, request: Request, cliente_id: int,
                        oportunidade_id=None, contato_id=None) -> dict:
    """O que a cotação puxa de quem já sabe — em vez de pedir de novo.

    Contato, cargo e departamento moram em `Contato`; o responsável mora na oportunidade ou
    é quem está logado. Pedir tudo isso de novo no formulário fazia a mesma informação ser
    digitada em três lugares, e as três versões divergirem com o tempo.

    **Campo sem origem fica vazio.** Nada de "A definir" nem de nome inventado: um valor
    fictício num documento comercial é pior que um espaço em branco, porque parece dado.
    """
    from app import crm_service as crm
    from app.models import Contato, Oportunidade

    herdado = {"vendedor": None, "contato_nome": None, "departamento_contato": None}

    contato = None
    if contato_id:
        contato = session.get(Contato, contato_id)
        if contato is not None and contato.cliente_id != cliente_id:
            contato = None          # contato de outro cliente não entra
    if contato is None and cliente_id:
        contato = crm.contato_principal(session, cliente_id)
    if contato is not None:
        herdado["contato_nome"] = contato.nome or None
        herdado["departamento_contato"] = getattr(contato, "cargo", None) or None

    responsavel = None
    if oportunidade_id:
        op = session.get(Oportunidade, oportunidade_id)
        if op is not None and op.responsavel_id:
            from app.models import Usuario
            responsavel = session.get(Usuario, op.responsavel_id)
    if responsavel is None:
        responsavel = usuario_da_request(request)
    if responsavel is not None:
        herdado["vendedor"] = responsavel.nome or None

    return herdado


@router.post("/cotacoes")
def criar(request: Request, cliente_id: int = Form(...), condicao_pagamento: str = Form("30"),
          estado_destino: str = Form(""), contribuinte_icms: str = Form("sim"),
          freight_type: str = Form(TipoFrete.cif.value),
          contato_id: str = Form(""), oportunidade_id: str = Form(""),
          session: Session = Depends(get_session)):
    """Cria a cotação com o **mínimo** e deriva o resto.

    O formulário pedia dezoito campos, entre eles vendedor, contato, departamento, prazo de
    entrega, validade, texto do frete e observações — todos preenchíveis depois, e vários já
    conhecidos por quem cadastrou o cliente ou abriu a oportunidade.

    `estado_origem` saiu de vez: ele é **origem logística**, não fiscal, e o formulário o
    fixava em "São Paulo" enquanto o modelo trazia "Santa Catarina". Quem preenchia "Origem
    da venda" não tinha como saber qual das duas coisas estava respondendo. O motor fiscal
    nunca usou esse campo — ele resolve por `uf_origem_fiscal`, que agora é editável no
    lugar certo, com o nome certo.
    """
    dias = int(cfg.num(session, "validade_dias", 5))
    agora = datetime.utcnow()
    herdado = _herdar_da_operacao(
        session, request, cliente_id,
        oportunidade_id=int(oportunidade_id) if oportunidade_id.isdigit() else None,
        contato_id=int(contato_id) if contato_id.isdigit() else None)

    cotacao = Cotacao(
        criado_em=agora,
        numero=proximo_numero(session), cliente_id=cliente_id,
        vendedor=herdado["vendedor"],
        oportunidade_id=int(oportunidade_id) if oportunidade_id.isdigit() else None,
        condicao_pagamento=condicao_pagamento or "30",
        estado_destino=estado_destino or None,
        contribuinte_icms=(contribuinte_icms == "sim"),
        freight_type=freight_type or TipoFrete.cif.value,
        contato_nome=herdado["contato_nome"],
        departamento_contato=herdado["departamento_contato"],
        validade_dias=dias,
        validade_em=agora + timedelta(days=dias),
        termos_texto=cfg.txt(session, "termos_padrao"),
    )
    _gravar_snapshot_fiscal(session, cotacao)
    session.add(cotacao)
    session.commit()
    session.refresh(cotacao)
    return RedirectResponse(url=f"/cotacoes/{cotacao.id}", status_code=303)


def _gravar_snapshot_fiscal(session: Session, cotacao: Cotacao):
    """Snapshot no nível da cotação — o que é comum a todos os itens.

    Desde a Onda 1 o ICMS **não** é da cotação: é de cada item. Os campos `icms_aplicado` e
    `icms_regra` continuam existindo por compatibilidade com o histórico e passam a guardar o
    cenário **sem item** (útil para exibir o cabeçalho); quando os itens divergem entre si, o
    campo registra isso em vez de fingir uma alíquota única.
    """
    _regras, contexto = ps.regras_da_cotacao(session, cotacao)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao.id)).all()
    alíquotas = {it.icms_pct for it in itens if it.icms_pct is not None}
    if len(alíquotas) == 1:
        cotacao.icms_aplicado = alíquotas.pop()
        cotacao.icms_regra = next((it.icms_regra for it in itens if it.icms_regra), None)
    elif len(alíquotas) > 1:
        cotacao.icms_aplicado = None
        cotacao.icms_regra = (f"Cotação mista: {len(alíquotas)} alíquotas diferentes entre os "
                              "itens. O ICMS é por item — ver a memória de cada linha.")
    else:
        cotacao.icms_aplicado = contexto.get("icms_pct")
        cotacao.icms_regra = contexto.get("icms_regra") or contexto.get("motivo_fiscal")
    # PIS/COFINS acompanha o ICMS: desde 09/09/2026 o efetivo é `nominal × (1 − ICMS − FCP)`,
    # então ele é **por item** exatamente na medida em que o ICMS é. Este campo de cabeçalho só
    # pode dizer a verdade quando os itens concordam — e o FCP tem de concordar junto, porque
    # `RegraFcp` é cadastrada por produto/NCM/família e dois itens do mesmo destino podem
    # divergir nela. Cotação mista fica com `None`, como o ICMS: um escalar aqui seria a taxa de
    # um item vendida como se fosse a de todos.
    fcps = {it.fcp_pct for it in itens if it.icms_pct is not None}
    cotacao.pis_cofins_pct = None
    if cotacao.icms_aplicado is not None and len(fcps) <= 1:
        fcp = fcps.pop() if fcps else contexto.get("fcp_pct")
        if fcp is not None:
            cotacao.pis_cofins_pct = para_float(pis_cofins_efetivo(
                contexto["pis_cofins_nominal_pct"],
                icms_excluido_da_base(cotacao.icms_aplicado, fcp)))
    cotacao.encargo_financeiro_pct = contexto["encargo_pct"]
    return _regras, contexto


def acoes_do_workflow(cotacao, prontidao) -> list:
    """As ações que fazem sentido AGORA, cada uma apontando para o seu dono canônico.

    Existe porque a tela oferecia `wf.proximos_estados()` como botões que postavam o estado
    desejado numa rota genérica — e era esse o bypass do C-NEW-09. O estado deixou de ser
    escolha: é consequência de uma ação, e cada ação sabe o que precisa conferir.

    A lista é derivada da `Prontidao`, então a tela não decide nada. "Emitir" só aparece
    quando `pode_emitir` é verdadeiro; enquanto houver blocker ou exceção pendente, o que
    aparece é o motivo.
    """
    estado = _valor_status(cotacao)
    if estado in wf.ESTADOS_LEGADOS:
        return []

    acoes = []
    imutavel = estado in wf.ESTADOS_IMUTAVEIS
    if not imutavel:
        if prontidao.precisa_aprovacao and not prontidao.aprovacao_valida \
                and estado == wf.DRAFT and not prontidao.blockers:
            acoes.append({"rota": f"/cotacoes/{cotacao.id}/aprovacao/solicitar",
                          "rotulo": "Pedir aprovação", "estilo": "btn-ghost",
                          # `solicitar_aprovacao` exige justificativa: quem decide precisa
                          # saber por quê. O campo é do botão, senão o clique morre em 400.
                          "justificativa": True,
                          "ajuda": "Registra o pedido com o fingerprint desta configuração."})
        if prontidao.pode_emitir:
            acoes.append({"rota": f"/cotacoes/{cotacao.id}/emitir",
                          "rotulo": "Emitir", "estilo": "btn-primary",
                          "ajuda": "Congela o documento. Depois disso, só por revisão."})
        if estado in (wf.PENDING_APPROVAL, wf.APPROVED):
            acoes.append({"rota": f"/cotacoes/{cotacao.id}/status", "estado": wf.DRAFT,
                          "rotulo": "Reabrir para edição", "estilo": "btn-ghost",
                          "ajuda": "Volta ao rascunho para mexer nos valores."})
        acoes.append({"rota": f"/cotacoes/{cotacao.id}/cancelar", "motivo": True,
                      "rotulo": "Cancelar", "estilo": "btn-danger",
                      "ajuda": "Nada é apagado — a cotação fica registrada como cancelada."})
    if estado == wf.ISSUED:
        acoes.append({"rota": f"/cotacoes/{cotacao.id}/enviar",
                      "rotulo": "Marcar como enviada", "estilo": "btn-primary",
                      "ajuda": "Registra que a proposta foi ao cliente."})
    if imutavel and estado != wf.CANCELLED:
        acoes.append({"rota": f"/cotacoes/{cotacao.id}/revisao",
                      "rotulo": "Criar revisão", "estilo": "btn-ghost",
                      "ajuda": "A emitida continua íntegra; a revisão nasce em rascunho."})
    return acoes


@router.get("/cotacoes/{cotacao_id}", response_class=HTMLResponse)
def detalhe(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    cliente = session.get(Cliente, cotacao.cliente_id)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)
                         .order_by(CotacaoItem.ordem)).all()
    _regras, regra_icms, contexto = montar_regras(cotacao, session)
    # A mesma avaliação que a emissão faz — a tela não pode prometer o que a emissão recusa.
    prontidao = ws.avaliar(session, cotacao, frete=ws.frete_para_avaliar(session, cotacao))
    return templates.TemplateResponse(request, "cotacao_detail.html", {
        "active": "cotacoes", "cotacao": cotacao, "cliente": cliente, "itens": itens,
        "totais": _totais(itens, ve_economia(request)),
        "prontidao": prontidao,
        "acoes_workflow": acoes_do_workflow(cotacao, prontidao),
        "estados_difal": estados(session), "regra_icms_atual": regra_icms,
        "contexto_fiscal": contexto, "condicoes": cfg.condicoes_pagamento(session),
        "tipos_frete": [t.value for t in TipoFrete],
        # Premissa mais nova que a desta cotação. Só detecta; a tela oferece a escolha.
        "premissas_novas": (adm.premissas_desatualizadas(session, cotacao, itens)
                            if _valor_status(cotacao) not in wf.ESTADOS_IMUTAVEIS else None),
        "origem_fiscal": _origem_fiscal_da_tela(session, cotacao),
        "origens_logisticas": _origens_logisticas(session, cotacao, itens),
        "avisos_exclusao": arquivamento.motivos_para_pensar_duas_vezes(cotacao, len(itens)),
    })


@router.post("/cotacoes/{cotacao_id}/atualizar")
def atualizar_cabecalho(cotacao_id: int, condicao_pagamento: str = Form("30"),
                        estado_destino: str = Form(""), estado_origem: str = Form(""),
                        contribuinte_icms: str = Form("sim"), frete: str = Form(""),
                        freight_type: str = Form(TipoFrete.cif.value),
                        freight_valor: str = Form(""), prazo_entrega: str = Form(""),
                        contato_nome: str = Form(""), departamento_contato: str = Form(""),
                        validade_dias: int = Form(0), vendedor: str = Form(""),
                        observacoes: str = Form(""), termos_texto: str = Form(""),
                        session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "alterar o cabeçalho")
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)

    novo_contribuinte = (contribuinte_icms == "sim")
    # `estado_origem` saiu da comparação junto com o campo: ele é origem **logística**, o
    # motor fiscal não o consulta, e mantê-lo aqui fazia todo salvamento parecer mudança de
    # cenário — o formulário não o envia, então a comparação era sempre contra vazio.
    mudou_precificacao = (cotacao.condicao_pagamento != condicao_pagamento
                          or cotacao.estado_destino != (estado_destino or None)
                          or cotacao.contribuinte_icms != novo_contribuinte
                          or (cotacao.freight_type or "") != (freight_type or ""))

    cotacao.condicao_pagamento = condicao_pagamento or "30"
    cotacao.estado_destino = estado_destino or None
    # O formulário não envia mais `estado_origem` — ele era um campo genérico ("Origem da
    # venda") que ninguém sabia responder, e cujo default aqui era "São Paulo" enquanto o
    # modelo trazia "Santa Catarina". Manter o default na rota faria cada salvamento
    # reescrever silenciosamente o campo. O que não vem, não muda.
    if estado_origem:
        cotacao.estado_origem = estado_origem
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
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}?cenario=atualizado",
                                status_code=303)

    return RedirectResponse(url=f"/cotacoes/{cotacao_id}?salvo=1", status_code=303)


@router.post("/cotacoes/{cotacao_id}/premissas/atualizar")
def atualizar_premissas(request: Request, cotacao_id: int,
                        session: Session = Depends(get_session)):
    """Traz o rascunho para as premissas de hoje — **por ação explícita**.

    O contrário desta rota é o comportamento padrão: um rascunho aberto amanhã continua com
    os números de ontem. Trocar sozinho mudaria o preço debaixo de quem já negociou, e é por
    isso que a detecção só detecta.

    Aqui o custo é re-resolvido pelo mesmo caminho de um item novo, o preço é reformado, e a
    memória e os pinos passam a apontar para as versões vigentes. A aprovação anterior cai
    junto, quando existir: ela era sobre a configuração de antes.
    """
    exigir_autenticado(request)
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    try:
        ws.exigir_editavel(cotacao, "atualizar as premissas")
    except Exception:
        return pagina_de_erro(
            request, titulo="Esta cotação não pode mais ser alterada",
            motivos=["Cotações emitidas ficam congeladas. Para propor com os valores de "
                     "hoje, crie uma revisão."],
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    itens = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.cotacao_id == cotacao.id)).all()
    for it in itens:
        produto = session.get(Produto, it.produto_id) if it.produto_id else None
        if produto is None:
            continue
        custo, memoria_custo = ps.custo_para_precificar(session, produto)
        it.custo_unitario = custo or 0.0
        margem = ps.margem_padrao(session, produto)
        regras, _regra, ctx = montar_regras(cotacao, session, produto)
        res = _calcular(it.modo_edicao, it.custo_unitario, it.quantidade, it.valor_editado,
                        regras, it.preco_base)
        _preencher_item(session, it, produto, margem, memoria_custo)
        _aplicar_resultado(it, res, regras, ctx)
        it.memoria_json = ps.memoria_json(ps.memoria_do_preco(
            session, produto, cotacao, preco_negociado=res.preco_negociado,
            quantidade=it.quantidade))
        session.add(it)

    cotacao.premissas_mantidas_aprovadas = False
    session.add(cotacao)
    session.commit()
    ws.invalidar_aprovacoes_obsoletas(session, cotacao,
                                      ator=exigir_autenticado(request),
                                      motivo="premissas atualizadas pelo usuário")
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{cotacao_id}?premissas=atualizadas",
                            status_code=303)


def _recalcular_todos_itens(cotacao: Cotacao, session: Session):
    """Recalcula item a item: cada um resolve o próprio cenário fiscal.

    Todo recálculo pode mudar o fingerprint — e aprovação vale para uma configuração, não
    para uma cotação. Por isso a invalidação vem junto, aqui, e não como algo que a tela
    precise lembrar de fazer.
    """
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao.id)).all()
    for it in itens:
        produto = session.get(Produto, it.produto_id) if it.produto_id else None
        regras, _regra, ctx = montar_regras(cotacao, session, produto)
        res = _calcular(it.modo_edicao, it.custo_unitario, it.quantidade, it.valor_editado,
                        regras, it.preco_base)
        _aplicar_resultado(it, res, regras, ctx)
        session.add(it)
    session.flush()
    ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="recálculo dos itens")
    session.commit()


def _aplicar_resultado(it: CotacaoItem, res, regras: TaxRuleSet = None, contexto: dict = None):
    """Grava o resultado no item.

    **Fronteira de saída do núcleo econômico**: as colunas do banco são REAL, e é aqui que o
    `Decimal` vira `float`. Os valores monetários já estão quantizados em 2 casas, então a
    conversão é exata — e a leitura de volta, via `D()`, devolve o mesmo centavo.
    """
    # O preço que o motor recomendaria para ESTE cenário, na margem-alvo do item. É a
    # referência contra a qual o desconto é medido — e ela **não** é o `preco_base` do
    # catálogo, que foi formado com outro destino fiscal e outra condição de pagamento.
    # Confundir os dois faria toda venda interestadual parecer desconto, e o aprovador seria
    # chamado para autorizar uma exceção inexistente.
    if regras is not None and it.custo_unitario and it.margem_padrao_pct is not None:
        it.preco_recomendado = para_float(
            calcular_por_margem(it.custo_unitario, 1, it.margem_padrao_pct,
                                regras).preco_negociado)
    it.preco_negociado = para_float(res.preco_negociado)
    it.margem_liquida = para_float(res.margem_liquida)
    it.faturamento = para_float(res.faturamento)
    it.custo_total = para_float(res.custo_total)
    it.lucro = para_float(res.lucro)
    it.diferenca_pct_vs_base = para_float(res.diferenca_pct_vs_base)
    it.impostos = para_float(res.impostos)
    it.comissao_valor = para_float(res.comissao)
    it.markup_implicito = para_float(res.markup_implicito)
    if regras:
        it.comissao_pct = para_float(regras.comissao_para_markup(res.markup_implicito))
    if contexto:
        _gravar_fiscal_no_item(it, contexto, res)


def _gravar_fiscal_no_item(it: CotacaoItem, contexto: dict, res=None):
    """Congela no item o cenário fiscal que formou o preço dele.

    Isto é o coração da Onda 1: o item — não a cotação — carrega origem, destino, natureza da
    mercadoria, finalidade, consumidor final derivado, alíquota, DIFAL e quem o recolhe. Uma
    cotação com KTC, Daune e Decor guarda três combinações diferentes.
    """
    it.origem_fiscal = contexto.get("origem_fiscal")
    it.uf_origem_fiscal = contexto.get("uf_origem_fiscal")
    it.uf_destino_fiscal = contexto.get("uf_destino_fiscal")
    it.finalidade = contexto.get("finalidade")
    it.consumidor_final = contexto.get("consumidor_final")
    it.icms_pct = contexto.get("icms_pct")
    it.aliquota_interestadual = contexto.get("aliquota_interestadual")
    it.aliquota_interna_destino = contexto.get("aliquota_interna_destino")
    it.fcp_pct = contexto.get("fcp_pct")
    it.icms_regra = contexto.get("icms_regra")
    it.icms_fonte = contexto.get("icms_fonte")
    it.difal_pct = contexto.get("difal_pct")
    it.difal_responsavel = contexto.get("difal_responsavel")
    # O DIFAL só vira dinheiro na conta da Anara quando o remetente é quem recolhe. Quando é do
    # destinatário, fica registrado com valor nulo — aparece na memória, não na margem.
    if contexto.get("difal_entra_na_margem") and res is not None and contexto.get("difal_pct"):
        it.difal_valor = para_float(dinheiro(D0(res.faturamento) * D0(contexto["difal_pct"])))
    else:
        it.difal_valor = None
    it.status_fiscal = contexto.get("status_fiscal")
    it.motivo_fiscal = contexto.get("motivo_fiscal")
    it.status_pagamento = contexto.get("status_pagamento")
    it.motivo_pagamento = contexto.get("motivo_pagamento")
    it.encargo_pct = contexto.get("encargo_pct")
    # --- pinning: a IDENTIDADE das premissas, não só o valor delas ---
    # Sem isto, "qual versão formou este preço" seria uma pergunta ao resolvedor de hoje, e
    # uma versão cadastrada depois com vigência retroativa mudaria a resposta.
    it.condicao_pagamento_id = contexto.get("condicao_pagamento_id")
    it.aliquota_interestadual_id = contexto.get("aliquota_interestadual_id")
    pinos = contexto.get("premissas_pinadas")
    if pinos:
        it.premissas_pinadas = json.dumps(pinos, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cálculo ao vivo
# ---------------------------------------------------------------------------
@router.post("/cotacoes/{cotacao_id}/calc")
def calc(request: Request, cotacao_id: int, produto_id: int = Form(...),
         quantidade: float = Form(...), modo: str = Form(...), valor: float = Form(...),
         session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    produto = session.get(Produto, produto_id)
    if not cotacao or not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    regras, _regra, ctx = montar_regras(cotacao, session, produto)
    margem = ps.margem_padrao(session, produto)
    # Mesmo custo que `adicionar_item` vai gravar: a prévia da tela e o item salvo não podem
    # divergir, senão o preço muda ao clicar em "adicionar".
    custo_vivo = ps.custo_para_precificar(session, produto)[0]
    if not custo_vivo:
        # Sem custo não há margem, mas o preço exibido continua sendo quantia comercial.
        preco = dinheiro(valor if modo == "preco" else (produto.preco_base or 0))
        sem_custo = {
            "sem_custo": True, "preco_base": produto.preco_base,
            "margem_padrao_pct": para_float(margem.margem_pct), "margem_regra": margem.regra,
            "aviso": ("Produto sem custo cadastrado. Dá para cotar pelo preço, mas margem e "
                      "lucro não podem ser calculados até o custo entrar."),
            "preco_negociado": para_float(preco),
            "faturamento": para_float(dinheiro(preco * D0(quantidade))),
            "custo_total": 0, "lucro": 0, "margem_liquida": 0,
            "diferenca_pct_vs_base": None,
        }
        return JSONResponse(sem_custo if ve_economia(request)
                            else sem_confidenciais(sem_custo))

    res = _calcular(modo, custo_vivo, quantidade, valor, regras, produto.preco_base)
    # Fronteira da API: `como_dict()` já entrega tudo em float, com o dinheiro em centavos.
    # Serializar Decimal aqui quebraria o JSON (ou, com `default=str`, mandaria dinheiro como
    # string para o JavaScript da tela).
    comercial = res.como_dict()
    payload = {
        "preco_negociado": comercial["preco_negociado"], "faturamento": comercial["faturamento"],
        "custo_total": comercial["custo_total"], "lucro": comercial["lucro"],
        "margem_liquida": comercial["margem_liquida"],
        "diferenca_pct_vs_base": comercial["diferenca_pct_vs_base"],
        "preco_base": produto.preco_base,
        "markup_implicito": comercial["markup_implicito"],
        "preco_preciso": comercial["preco_preciso"],
        "margem_alvo": comercial["margem_alvo"],
        "comissao_pct": para_float(regras.comissao_para_markup(res.markup_implicito)),
        "margem_padrao_pct": para_float(margem.margem_pct), "margem_regra": margem.regra,
        "sem_custo": False,
    }
    # O vendedor precisa do preço e do total para negociar; o resto do payload é o motor.
    # `sem_confidenciais` corta por nome de campo, então um campo novo que alguém adicione
    # aqui já nasce cortado se o nome estiver na política.
    return JSONResponse(payload if ve_economia(request) else sem_confidenciais(payload))


# ---------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------
def _preencher_item(session: Session, item: CotacaoItem, produto: Produto, margem,
                    memoria_custo: dict = None):
    from app import custo_service as cs

    fornecedor = session.get(Fornecedor, produto.fornecedor_id) if produto.fornecedor_id else None
    item.fornecedor_id = produto.fornecedor_id
    item.fornecedor_nome = fornecedor.nome if fornecedor else None
    item.cost_method = produto.cost_method
    item.margem_padrao_pct = margem.margem_pct
    item.margem_regra = margem.regra
    item.margem_regra_id = margem.regra_id
    # A versão de custo que está valendo AGORA fica presa ao item. Depois disto, a
    # genealogia deste preço não depende mais de nenhum lookup vivo.
    vigente = cs.referencia_vigente(session, produto.id)
    if vigente is not None:
        item.custo_referencia_id = vigente.id
        item.custo_referencia_versao = vigente.versao
        # Como o custo foi obtido viaja junto com quanto ele é: A_COTAR não forma preço,
        # ESTIMADO forma proposta mas não compromisso firme.
        item.status_custo_item = vigente.status_custo
        item.confirmation_pending = bool(vigente.confirmation_pending)
    else:
        # Sem referência versionada, o status vem de **como o custo foi resolvido agora** —
        # não de `Produto.custo_confianca`, que fala o vocabulário do método (`CALCULATED`,
        # `QUOTED`, `MANUAL`) e não o dos portões do workflow. Copiar aquele valor para cá
        # produzia um status que `CUSTO_BLOQUEIA` não reconhecia: 210 SKUs ativos passavam
        # por todos os gates sem bloquear nem avisar.
        item.status_custo_item = ps.status_canonico_do_custo(
            item.custo_unitario, memoria_custo)
        item.confirmation_pending = False


@router.post("/cotacoes/{cotacao_id}/itens")
def adicionar_item(request: Request, cotacao_id: int, produto_id: int = Form(...),
                   quantidade: float = Form(...), modo: str = Form("margem"),
                   valor: float = Form(None), session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "adicionar item")
    produto = session.get(Produto, produto_id)
    if not cotacao or not produto:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    regras, _regra, ctx = montar_regras(cotacao, session, produto)
    margem = ps.margem_padrao(session, produto)
    if valor is None:
        valor = margem.margem_pct if modo == "margem" else (produto.preco_base or 0)

    # O custo do item novo é resolvido AGORA, pelas premissas vigentes — não lido da coluna
    # `Produto.custo_unitario`, que é gravada quando o custo foi calculado pela última vez e
    # não acompanha uma troca de câmbio.
    custo, memoria_custo = ps.custo_para_precificar(session, produto)
    custo = custo or 0.0
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
    _preencher_item(session, item, produto, margem, memoria_custo)
    _aplicar_resultado(item, res, regras, ctx)
    item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
        session, produto, cotacao, preco_negociado=res.preco_negociado, quantidade=quantidade))
    session.add(item)
    session.commit()
    session.refresh(item)
    return JSONResponse(_item_para_json(item, ve_economia(request)))


@router.put("/cotacoes/{cotacao_id}/itens/{item_id}")
async def editar_item(cotacao_id: int, item_id: int, request: Request,
                      session: Session = Depends(get_session)):
    form = await request.form()
    quantidade = float(form.get("quantidade"))
    modo = form.get("modo", "preco")
    valor = float(form.get("valor"))

    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "editar item")
    item = session.get(CotacaoItem, item_id)
    if not cotacao or not item or item.cotacao_id != cotacao_id:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    produto = session.get(Produto, item.produto_id) if item.produto_id else None
    regras, _regra, ctx = montar_regras(cotacao, session, produto)
    res = _calcular(modo, item.custo_unitario, quantidade, valor, regras, item.preco_base)

    item.quantidade = quantidade
    item.modo_edicao = modo
    item.valor_editado = valor
    _aplicar_resultado(item, res, regras, ctx)
    if produto:
        item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
            session, produto, cotacao, preco_negociado=res.preco_negociado,
            quantidade=quantidade))
    session.add(item)
    session.commit()
    session.refresh(item)
    return JSONResponse(_item_para_json(item, ve_economia(request)))


@router.get("/cotacoes/{cotacao_id}/itens/{item_id}/memoria")
def memoria_item(request: Request, cotacao_id: int, item_id: int,
                 session: Session = Depends(get_session)):
    """Memória do preço congelada no item — como aquele preço foi formado.

    Negado ao vendedor. Esta é a superfície mais sensível do sistema: traz EXW, CMT, consumo
    de tecido, nacionalização, I.I., CNET, alíquotas e margem de uma vez só. E é a que mais
    convida ao acesso por URL direta, porque o `item_id` está no HTML da tela.
    """
    exigir_economia(request)
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
def remover_item(request: Request, cotacao_id: int, item_id: int,
                 session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "remover item")
    item = session.get(CotacaoItem, item_id)
    if item and item.cotacao_id == cotacao_id:
        session.delete(item)
        session.flush()
        if cotacao is not None:
            ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="item removido")
        session.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Status / aceite / duplicar / PDF
# ---------------------------------------------------------------------------
#: Cada estado do workflow tem **um** dono canônico, e nenhum deles é esta rota. O valor é a
#: ação que o usuário precisa usar — a recusa nomeia o caminho certo em vez de só dizer não.
#:
#: **C-NEW-09.** Esta rota validava a transição e nada mais. Como `TRANSICOES` permite
#: `rascunho → aguardando_aprovacao → aprovada → emitida → enviada`, um vendedor
#: comissionado percorria a cadeia inteira por aqui: sem `can_approve_quotes`, sem registro
#: em `AprovacaoCotacao`, sem conferência de fingerprint, sem `ws.avaliar()` — e sem
#: `SnapshotEmissao`, de modo que a cotação ficava "emitida" sem o documento congelado
#: existir. O PDF saía **final**, sem marca d'água, com item de R$ 0,00 dentro.
#:
#: O estado não é mais alcançável por aqui: a rota concede apenas o que **retira**
#: privilégio, que é voltar para rascunho.
DONOS_CANONICOS_DO_ESTADO = {
    "aguardando_aprovacao": ("Pedir aprovação", "solicita o pedido e registra o fingerprint "
                             "do estado que o aprovador vai ver"),
    "aprovada": ("Aprovar", "exige alçada de aprovação e grava a decisão com o fingerprint "
                 "da configuração aprovada"),
    "emitida": ("Emitir", "revalida blockers e exceções agora e congela o documento num "
                "snapshot"),
    "enviada": ("Marcar como enviada", "só depois de emitida"),
    "cancelada": ("Cancelar", "registra o motivo do cancelamento"),
}


@router.post("/cotacoes/{cotacao_id}/status")
def mudar_status(request: Request, cotacao_id: int, status: str = Form(...),
                 session: Session = Depends(get_session)):
    """Reabre a cotação para edição. **Não concede estado privilegiado** — ver C-NEW-09.

    Sobrou desta rota exatamente uma transição: voltar para `rascunho`. Ela retira
    privilégio em vez de conceder, então não precisa de alçada nem de trilha própria — a
    decisão que existia continua registrada em `AprovacaoCotacao`, e volta a valer apenas se
    o fingerprint bater de novo.

    Todo o resto tem dono canônico em `workflow_service`, e é para lá que a recusa aponta.
    Duplicar aqui qualquer pedaço daquelas regras seria recriar o bypass com outro nome.
    """
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)

    if status in DONOS_CANONICOS_DO_ESTADO:
        acao, porque = DONOS_CANONICOS_DO_ESTADO[status]
        return pagina_de_erro(
            request, titulo="Esta ação não muda a situação diretamente",
            introducao=f"Para deixar a cotação como {rotulos.cotacao(status)}, use:",
            motivos=[f"{acao} — {porque}."],
            ajuda=("A situação de uma cotação é consequência de uma decisão registrada, não "
                   "um campo que se escolhe. Cada ação confere o que precisa ser conferido e "
                   "deixa a trilha de quem decidiu o quê."),
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação",
            status_code=403)

    atual = cotacao.status.value if hasattr(cotacao.status, "value") else str(cotacao.status)
    try:
        wf.exigir_transicao(atual, status)
    except wf.TransicaoInvalida:
        return pagina_de_erro(
            request, titulo="Não foi possível mudar a situação",
            introducao="Esta cotação está em:",
            motivos=[f"{rotulos.cotacao(atual)} — e daqui não é possível ir para "
                     f"{rotulos.cotacao(status)}."],
            ajuda=("As situações seguem uma ordem: rascunho, aprovação, emissão e envio. "
                   "Cotações antigas, importadas do sistema anterior, ficam onde estão."),
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    cotacao.status = StatusCotacao(status)
    session.add(cotacao)
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{cotacao_id}", status_code=303)


@router.post("/cotacoes/{cotacao_id}/aceite")
def registrar_aceite(cotacao_id: int, aceite_responsavel: str = Form(""),
                     aceite_cargo: str = Form(""), aceite_departamento: str = Form(""),
                     local_entrega: str = Form(""), endereco_entrega: str = Form(""),
                     observacoes_pedido: str = Form(""),
                     session: Session = Depends(get_session)):
    """Registra o aceite do cliente. **Não muda a situação da cotação.**

    Havia aqui um `virar_pedido` que gravava `StatusCotacao.pedido` direto — um estado
    herdado do sistema anterior, de onde o workflow não define nenhuma saída. O botão que o
    acionava ficava ao lado de "Salvar aceite" e foi o primeiro que o usuário clicou.

    Registrar que o cliente aceitou é informação do documento; mover a cotação é decisão do
    workflow, e passa por `/emitir` e `/enviar`. O resultado comercial do negócio — ganho ou
    perdido — pertence à oportunidade, não a um atalho de status aqui.
    """
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

    # A duplicação usa as premissas ATUAIS — e agora resolve o fiscal por item, como uma
    # cotação nova faria. A cotação original não é tocada.
    itens_originais = session.exec(select(CotacaoItem)
                                   .where(CotacaoItem.cotacao_id == cotacao_id)
                                   .order_by(CotacaoItem.ordem)).all()
    for it in itens_originais:
        produto_atual = session.get(Produto, it.produto_id) if it.produto_id else None
        regras, _regra, ctx = montar_regras(nova, session, produto_atual)
        preco_base_atual = produto_atual.preco_base if produto_atual else it.preco_base
        # Duplicar é criar item novo: o custo é reconferido contra as premissas de hoje,
        # como em qualquer precificação nova. O preço negociado é que se mantém.
        custo_atual, memoria_custo = (ps.custo_para_precificar(session, produto_atual)
                                      if produto_atual else (it.custo_unitario, None))
        custo_atual = custo_atual or 0.0

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
                            ps.margem_padrao(session, produto_atual), memoria_custo)
        _aplicar_resultado(novo_item, res, regras, ctx)
        session.add(novo_item)
    session.commit()
    return RedirectResponse(url=f"/cotacoes/{nova.id}", status_code=303)


@router.get("/cotacoes/{cotacao_id}/pdf")
def gerar_pdf(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    cliente = session.get(Cliente, cotacao.cliente_id)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)
                         .order_by(CotacaoItem.ordem)).all()

    # Onda 1: item com cenário fiscal ou condição financeira irresolvida não sai em PDF final.
    # O rascunho continua salvo e editável; o que não acontece é o documento comercial sair com
    # um número que ninguém consegue justificar.
    # O usuário chega aqui por um clique, não por `fetch`. A recusa precisa ser uma página
    # que diga o que fazer — não um objeto JSON na barra de endereços, que foi o que ele viu
    # ao tentar gerar a primeira proposta.
    bloqueios = bloqueios_fiscais(itens)
    if bloqueios:
        return pagina_de_erro(
            request, titulo="Não foi possível gerar o PDF",
            introducao="Antes de continuar, resolva:",
            motivos=bloqueios,
            ajuda=("O rascunho continua salvo e editável. O documento comercial só sai "
                   "quando todos os itens têm cenário fiscal e condição de pagamento "
                   "resolvidos."),
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    # Sessão 6: preview e documento final são a mesma folha para quem recebe. Enquanto a
    # cotação não estiver emitida, o PDF sai marcado — inclusive (e principalmente) quando
    # há aprovação pendente. Emitir de verdade é a rota `/emitir`.
    prontidao = ws.avaliar(session, cotacao)
    rascunho = cotacao.status not in (StatusCotacao.emitida.value,
                                      StatusCotacao.enviada.value)

    if not cotacao.termos_texto:
        cotacao.termos_texto = cfg.txt(session, "termos_padrao")
    if not rascunho and not cotacao.emitida_em:
        cotacao.emitida_em = datetime.utcnow()

    out_path = gerar_pdf_para_cotacao(cotacao, cliente, itens, rascunho=rascunho)
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

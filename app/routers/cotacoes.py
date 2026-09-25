import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import admin_service as adm
from app import arquivamento
from app import comercial_service as com
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
    Cliente, CondicaoPagamento, Cotacao, CotacaoItem, EstadoFiscal, Fornecedor, Oportunidade,
    Produto, SnapshotEmissao, StatusCotacao, TipoFrete,
)
from app.payment_terms import SinalInvalido, percentual_sinal_do_formulario
from app.pdf_bridge import gerar_pdf_para_cotacao
from app.politica_comercial import MODO_DESCONTO, ROTULO_2026_09_21
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_markup, calcular_por_preco,
    desconto_vs_tabela, icms_excluido_da_base, pis_cofins_efetivo, preco_b2b, preco_de_tabela,
    preco_por_desconto,
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


def montar_regras(cotacao: Cotacao, session: Session, produto=None, item=None):
    """TaxRuleSet efetivo **do item** + a regra fiscal textual aplicada.

    `regras` volta None quando o fiscal ou a condição de pagamento não se resolveram — nesse
    caso não existe preço confiável a formar, e o chamador grava o bloqueio no item.

    Com `item`, a comissão de formação é a que o item **congelou** (Fase 3A): recalcular um
    rascunho não o migra de política em silêncio. Sem item, é a política vigente do produto.
    """
    if item is not None:
        regras, contexto = ps.regras_da_cotacao(
            session, cotacao, produto,
            comissao_formacao_pct=com.comissao_de_formacao_do_item(item),
            politica=com.politica_do_item(item))
    else:
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


def _e_politica_nova(contexto: dict) -> bool:
    return bool(contexto) and contexto.get("politica_comercial") == ROTULO_2026_09_21


def _b2b_e_tabela(custo, margem_alvo, regras: TaxRuleSet, contexto: dict, preco_base=None):
    """(B2B unitário, tabela) da política de 21/09 para este custo neste cenário.

    O B2B é o menor centavo com margem ≥ alvo (`pricing_engine.preco_b2b`); a tabela é
    `fator × B2B`, com o fator lido da premissa pinada no contexto — nunca de cache.
    """
    b2b = preco_b2b(custo, margem_alvo, regras, preco_base)
    fator = contexto.get("fator_tabela") if contexto else None
    if fator is None:
        raise HTTPException(status_code=409,
                            detail="Premissa fator_tabela não cadastrada — a política de "
                                   "21/09/2026 não forma tabela sem ela.")
    return b2b, preco_de_tabela(b2b.preco_negociado, fator)


def _calcular(modo: str, custo: float, qtd: float, valor: float,
              regras: TaxRuleSet, preco_base=None, *, contexto: dict = None,
              margem_alvo=None, base_comercial=None):
    """Despacha para o modo escolhido: margem (padrão), preço, markup ou desconto.

    `regras is None` significa cenário irresolvido: devolve resultado zerado em vez de um preço
    que pareceria confiável. Margem-alvo ausente (produto sem regra) idem — não se inventa.

    Política de 21/09/2026 (`contexto["politica_comercial"]`):

    * `margem`   → o preço é o **B2B** (menor centavo com margem ≥ alvo), não a forma fechada;
    * `desconto` → `valor` é o desconto sobre a TABELA (fração): B2B e tabela são refeitos
      para o cenário atual e o preço é derivado deles. É a alavanca que sobrevive a uma
      mudança de cenário — o preço absoluto muda, o desconto negociado não (CR-01).

    Economia real × formação comercial (22/09/2026): `custo` é o CUSTO REAL (KTC: I.I. 0%) e
    forma lucro/margem realizada; `base_comercial` (referência comercial do SKU) forma o preço
    — B2B, tabela, recomendado. Sem base (fornecedor nacional, item anterior) as duas coincidem.
    """
    if regras is None:
        return _resultado_bloqueado(qtd, custo)
    base = base_comercial if base_comercial else custo

    def economia_real(r):
        # preço formado sobre a base comercial; economia (lucro, margem) sobre o custo real
        if base == custo:
            return r
        real = calcular_por_preco(custo, qtd, r.preco_negociado, regras, preco_base)
        real.preco_preciso = getattr(r, "preco_preciso", None)
        return real

    if modo in ("margem", MODO_DESCONTO) and _e_politica_nova(contexto):
        # margem: `valor` é a alavanca (margem-alvo da regra, ou um override explícito de
        # quem pode editar margem); desconto: a margem-alvo do item forma o B2B e a tabela.
        alvo = (valor if valor is not None else margem_alvo) if modo == "margem" else margem_alvo
        if alvo is None or not custo or D0(custo) <= 0:
            return _resultado_bloqueado(qtd, custo)
        b2b, tabela = _b2b_e_tabela(base, alvo, regras, contexto, preco_base)
        preco = b2b.preco_negociado if modo == "margem" else preco_por_desconto(tabela, valor)
        r = calcular_por_preco(custo, qtd, preco, regras, preco_base)
        r.preco_preciso = b2b.preco_preciso
        r.margem_alvo = D0(alvo)
        return r
    if modo == "margem":
        if valor is None:
            return _resultado_bloqueado(qtd, custo)
        return economia_real(calcular_por_margem(base, qtd, valor, regras, preco_base))
    if modo == "markup":
        return economia_real(calcular_por_markup(base, qtd, valor, regras, preco_base))
    if modo == MODO_DESCONTO:
        # Desconto sobre tabela só existe na política de 21/09. Fora dela (item que mudou de
        # política antes de ser reprecificado) a alavanca volta à margem-alvo — nunca a um
        # preço inventado.
        if margem_alvo is None:
            return _resultado_bloqueado(qtd, custo)
        return economia_real(calcular_por_margem(base, qtd, margem_alvo, regras, preco_base))
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
        "base_comercial_precificacao": it.base_comercial_precificacao,
        "protecao_comercial_pct": it.protecao_comercial_pct,
        "preco_b2b_economico": it.preco_b2b_economico,
        "preco_negociado": it.preco_negociado, "margem_liquida": it.margem_liquida,
        "faturamento": it.faturamento, "custo_total": it.custo_total, "lucro": it.lucro,
        "diferenca_pct_vs_base": it.diferenca_pct_vs_base,
        "modo_edicao": it.modo_edicao, "valor_editado": it.valor_editado,
        "fornecedor_nome": it.fornecedor_nome, "cost_method": it.cost_method,
        "margem_padrao_pct": it.margem_padrao_pct, "margem_regra": it.margem_regra,
        "comissao_pct": it.comissao_pct, "markup_implicito": it.markup_implicito,
        # Fase 3A — o recomendado é referência comercial; se a linha aceita outro unitário
        # é operacional. Piso, comissão de formação e política são economia.
        "preco_recomendado": it.preco_recomendado,
        "preco_travado": bool(it.preco_travado),
        "editavel": not bool(it.preco_travado),
        "motivo_nao_editavel": (com.MOTIVO_TRAVADO if it.preco_travado else None),
        "total_linha": it.faturamento,
        "piso_margem_pct": it.piso_margem_pct,
        "comissao_formacao_pct": it.comissao_formacao_pct,
        "politica_comercial": it.politica_comercial,
        # política 21/09/2026 — comercial: tabela, B2B, desconto; econômico: base e faixa
        "politica_nova": it.politica_comercial == ROTULO_2026_09_21,
        "preco_tabela": it.preco_tabela,
        "preco_b2b": it.preco_recomendado if it.politica_comercial == ROTULO_2026_09_21 else None,
        "desconto_vs_tabela_pct": it.desconto_vs_tabela_pct,
        "modo_negociacao": it.modo_negociacao,
        "comissao_faixa_pct": it.comissao_faixa_pct,
        "base_comissionavel": it.base_comissionavel,
        "icms_base_comissao_pct": it.icms_base_comissao_pct,
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
    resumo = wf.resumo_comercial(itens)
    completo = {"faturamento": para_float(faturamento), "custo_total": para_float(custo),
                "lucro": para_float(lucro),
                "margem_liquida": para_float(divide(lucro, faturamento) or ZERO),
                "num_itens": len(itens),
                # Fase 3A: a comissão estimada da cotação é da vendedora também.
                "comissao_estimada_valor": resumo["comissao_estimada_valor"],
                "comissao_estimada_pct_efetiva": resumo["comissao_estimada_pct_efetiva"]}
    return completo if pode_ver_economia else totais_comerciais(completo)


# ---------------------------------------------------------------------------
# Listagem / criação / detalhe
# ---------------------------------------------------------------------------
#: Atalhos de período da lista de cotações (Fase 3C) — pela data de criação.
PERIODOS_LISTA = [("", "Qualquer data"), ("mes", "Este mês"), ("trimestre", "Este trimestre"),
                  ("ano", "Este ano"), ("12m", "Últimos 12 meses")]


@router.get("/cotacoes", response_class=HTMLResponse)
def listar(request: Request, status: str = "", cliente_id: str = "", vendedor: str = "",
           categoria: str = "", arquivadas: str = "", periodo: str = "", busca: str = "",
           session: Session = Depends(get_session)):
    todas = session.exec(select(Cotacao).order_by(Cotacao.criado_em.desc())).all()
    itens_por_cotacao = {}
    for it in session.exec(select(CotacaoItem)).all():
        itens_por_cotacao.setdefault(it.cotacao_id, []).append(it)

    mostrar_arquivadas = arquivadas == "sim"
    cotacoes = [c for c in todas if bool(c.arquivada_em) == mostrar_arquivadas]
    total_arquivadas = sum(1 for c in todas if c.arquivada_em)

    if status:
        cotacoes = [c for c in cotacoes if _valor_status(c) == status]
    if cliente_id:
        cotacoes = [c for c in cotacoes if str(c.cliente_id) == cliente_id]
    if vendedor:
        alvo = vendedor.strip().lower()
        cotacoes = [c for c in cotacoes if alvo in (c.vendedor or "").lower()]
    if categoria:
        cotacoes = [c for c in cotacoes
                    if any((i.categoria or "") == categoria for i in itens_por_cotacao.get(c.id, []))]
    if periodo:
        from app import metrics_service as mx
        janela = mx.periodo_de(periodo)
        cotacoes = [c for c in cotacoes if janela.contem(c.criado_em)]

    clientes = {c.id: c for c in session.exec(select(Cliente)).all()}
    if busca:
        alvo = busca.strip().lower()
        cotacoes = [c for c in cotacoes
                    if alvo in (c.numero or "").lower()
                    or alvo in (getattr(clientes.get(c.cliente_id), "nome", "") or "").lower()]
    economia = ve_economia(request)
    totais = {c.id: _totais(itens_por_cotacao.get(c.id, []), economia) for c in cotacoes}
    # Fase 3B: a venda de cada cotação. Cotação sem venda é legado — e a tela diz isso.
    from app import crm_service as crm
    vendas = crm.cotacoes_com_venda(session)
    categorias = sorted({i.categoria for i in session.exec(select(CotacaoItem)).all() if i.categoria})
    vendedores = sorted({c.vendedor for c in session.exec(select(Cotacao)).all() if c.vendedor})

    return templates.TemplateResponse(request, "cotacoes_list.html", {
        "active": "cotacoes", "cotacoes": cotacoes, "clientes": clientes, "totais": totais,
        "status_filtro": status, "cliente_filtro": cliente_id, "vendedor_filtro": vendedor,
        "categoria_filtro": categoria, "categorias": categorias, "vendedores": vendedores,
        "periodo_filtro": periodo, "busca": busca, "periodos": PERIODOS_LISTA,
        "mostrar_arquivadas": mostrar_arquivadas, "total_arquivadas": total_arquivadas,
        "itens_por_cotacao": {k: len(v) for k, v in itens_por_cotacao.items()},
        "todos_clientes": sorted(clientes.values(), key=lambda c: c.nome),
        "vendas": vendas,
        # Aqui é FILTRO, e por isso a lista é completa: os estados herdados precisam ser
        # filtráveis para que as cotações antigas continuem encontráveis. Na tela de
        # detalhe, onde `status_opcoes` vira botão de ação, a lista é outra.
        "status_opcoes": [(s.value, rotulos.cotacao(s.value)) for s in StatusCotacao],
    })


@router.get("/cotacoes/nova", response_class=HTMLResponse)
def nova_form(request: Request, cliente_id: int = 0, oportunidade_id: int = 0,
              session: Session = Depends(get_session)):
    """Formulário mínimo. Quando vem de uma oportunidade, já chega com o cliente dela."""
    from app import crm_service as crm
    from app.models import Oportunidade, StatusOportunidade

    oportunidade = session.get(Oportunidade, oportunidade_id) if oportunidade_id else None
    if oportunidade is not None and not cliente_id:
        cliente_id = oportunidade.cliente_id

    clientes = session.exec(select(Cliente).order_by(Cliente.nome)).all()
    vendas_abertas = (crm.listar_oportunidades(session, cliente_id=cliente_id,
                                               status=StatusOportunidade.aberta.value)
                      if cliente_id else [])
    return templates.TemplateResponse(request, "cotacao_nova.html", {
        "active": "nova_cotacao", "clientes": clientes,
        "cliente_selecionado": session.get(Cliente, cliente_id) if cliente_id else None,
        "contatos": crm.contatos_de(session, cliente_id, apenas_ativos=True) if cliente_id else [],
        "oportunidade": oportunidade,
        "vendas_abertas": [{"id": o.id, "titulo": o.titulo,
                            "status_comercial": crm.status_comercial(o)} for o in vendas_abertas],
        "estados_difal": estados(session),
        "condicoes": cfg.condicoes_pagamento(session),
        "tipos_frete": [t.value for t in TipoFrete],
        # O encargo de cada condição é mecânica do preço: só quem vê economia o enxerga
        # ao lado do rótulo (auditoria de confidencialidade de 21/09/2026).
        "economia": ve_economia(request),
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


class CotacaoSemVenda(HTTPException):
    """Fase 3B: nenhuma cotação comercial nova nasce solta."""

    def __init__(self):
        super().__init__(status_code=400, detail=(
            "Toda cotação nova pertence a uma venda. Escolha uma venda aberta do cliente ou "
            "informe o nome do projeto para criar uma."))


def exigir_venda(session: Session, *, ator, cliente_id: int, oportunidade_id=None,
                 nova_venda: str = ""):
    """A venda a que a cotação nova pertence — existente ou criada agora com o mínimo.

    `nova_venda` é o nome do projeto ("Renovação enxoval 2026"). Venda existente de outro
    cliente é recusada: cotação do Hotel A não entra no negócio do Hotel B.
    """
    from app import crm_service as crm
    from app.models import Oportunidade

    if oportunidade_id:
        op = session.get(Oportunidade, int(oportunidade_id))
        if op is None:
            raise HTTPException(status_code=404, detail="Venda não encontrada.")
        if op.cliente_id != cliente_id:
            raise HTTPException(status_code=409, detail=(
                "Esta venda é de outro cliente. Cotação do cliente A não entra na venda do "
                "cliente B."))
        return op
    if (nova_venda or "").strip():
        if ator is None:
            raise HTTPException(status_code=401, detail="Autenticação necessária.")
        from app.models import Usuario
        # responsável = quem está criando, quando é usuário do banco (teste com ator falso, não)
        responsavel = getattr(ator, "id", None)
        if responsavel is not None and session.get(Usuario, responsavel) is None:
            responsavel = None
        return crm.criar_oportunidade(session, ator=ator, cliente_id=cliente_id,
                                      titulo=nova_venda.strip(), responsavel_id=responsavel)
    raise CotacaoSemVenda()


def criar_cotacao_da_venda(session: Session, request: Request, op, *, ator, **campos) -> Cotacao:
    """O caminho canônico de criação: cliente e vínculo vêm da venda; o resto é herdado."""
    from app.models import Oportunidade  # noqa: F401
    cliente = session.get(Cliente, op.cliente_id)
    dias = int(cfg.num(session, "validade_dias", 5))
    agora = datetime.utcnow()
    herdado = _herdar_da_operacao(session, request, op.cliente_id, oportunidade_id=op.id,
                                  contato_id=campos.get("contato_id"))
    cotacao = Cotacao(
        criado_em=agora, numero=proximo_numero(session), cliente_id=op.cliente_id,
        vendedor=herdado["vendedor"], oportunidade_id=op.id,
        condicao_pagamento=campos.get("condicao_pagamento") or "30",
        percentual_sinal=para_float(campos.get("percentual_sinal") or 0) or 0.0,
        estado_destino=campos.get("estado_destino") or getattr(cliente, "cidade_uf", None),
        contribuinte_icms=campos.get("contribuinte_icms", True),
        finalidade=getattr(cliente, "finalidade", None),
        freight_type=campos.get("freight_type") or TipoFrete.cif.value,
        contato_nome=herdado["contato_nome"],
        departamento_contato=herdado["departamento_contato"],
        validade_dias=dias, validade_em=agora + timedelta(days=dias),
        termos_texto=cfg.txt(session, "termos_padrao"))
    _gravar_snapshot_fiscal(session, cotacao)
    session.add(cotacao)
    session.flush()
    op.atualizado_em = agora
    session.add(op)
    return cotacao


@router.post("/cotacoes")
def criar(request: Request, cliente_id: int = Form(...), condicao_pagamento: str = Form("30"),
          estado_destino: str = Form(""), contribuinte_icms: str = Form("sim"),
          freight_type: str = Form(TipoFrete.cif.value),
          contato_id: str = Form(""), oportunidade_id: str = Form(""),
          nova_venda: str = Form(""),
          session: Session = Depends(get_session)):
    """Cria a cotação com o **mínimo** e deriva o resto — **dentro de uma venda** (Fase 3B).

    O formulário pedia dezoito campos, entre eles vendedor, contato, departamento, prazo de
    entrega, validade, texto do frete e observações — todos preenchíveis depois, e vários já
    conhecidos por quem cadastrou o cliente ou abriu a oportunidade.

    `estado_origem` saiu de vez: ele é **origem logística**, não fiscal, e o formulário o
    fixava em "São Paulo" enquanto o modelo trazia "Santa Catarina". Quem preenchia "Origem
    da venda" não tinha como saber qual das duas coisas estava respondendo. O motor fiscal
    nunca usou esse campo — ele resolve por `uf_origem_fiscal`, que agora é editável no
    lugar certo, com o nome certo.
    """
    ator = usuario_da_request(request)
    op = exigir_venda(session, ator=ator, cliente_id=cliente_id,
                      oportunidade_id=int(oportunidade_id) if oportunidade_id.isdigit() else None,
                      nova_venda=nova_venda)
    cotacao = criar_cotacao_da_venda(
        session, request, op, ator=ator,
        contato_id=int(contato_id) if contato_id.isdigit() else None,
        condicao_pagamento=condicao_pagamento, estado_destino=estado_destino or None,
        contribuinte_icms=(contribuinte_icms == "sim"), freight_type=freight_type)
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


def _negociacao_inicial(session: Session, cotacao: Cotacao, itens, economia: bool):
    """O payload do painel de negociação para a primeira pintura da tela.

    Cotação anterior à política sem as premissas de comissão cadastradas não derruba a
    tela: o painel fica vazio e a proposta continua legível.
    """
    if not itens:
        return None
    try:
        av = com.avaliar_negociacao(session, cotacao, itens)
    except Exception:                                   # noqa: BLE001
        return None
    return com.payload_admin(av) if economia else com.payload_vendedora(av)


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


def _sinal_para_campo(cotacao) -> str:
    """Fração guardada → percentual do formulário, sem zeros inúteis ("30", "33,5" → "33.5")."""
    fracao = D0(getattr(cotacao, "percentual_sinal", 0) or 0)
    if fracao <= 0:
        return ""
    texto = format((fracao * 100).normalize(), "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


@router.get("/cotacoes/{cotacao_id}", response_class=HTMLResponse)
def detalhe(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)
    # Renderizar a cotação resolve o custo de CADA item, e cada custo relê as mesmas
    # tabelinhas de configuração: 1.291 consultas numa cotação de 25 itens (medido em
    # 23/09/2026). Com o RTT de um Postgres gerenciado isso é segundo(s) com a conexão fora
    # do pool — e foi o que esgotou as 5+5 em produção. Tela de leitura pura: nada aqui grava.
    with ps.cache_de_leitura(session):
        return _detalhe(request, cotacao, session)


def _detalhe(request: Request, cotacao: Cotacao, session: Session):
    cotacao_id = cotacao.id
    cliente = session.get(Cliente, cotacao.cliente_id)
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)
                         .order_by(CotacaoItem.ordem)).all()
    _regras, regra_icms, contexto = montar_regras(cotacao, session)
    # A mesma avaliação que a emissão faz — a tela não pode prometer o que a emissão recusa.
    prontidao = ws.avaliar(session, cotacao, frete=ws.frete_para_avaliar(session, cotacao))
    economia = ve_economia(request)
    return templates.TemplateResponse(request, "cotacao_detail.html", {
        "active": "cotacoes", "cotacao": cotacao, "cliente": cliente, "itens": itens,
        "totais": _totais(itens, economia),
        "prontidao": prontidao,
        "acoes_workflow": acoes_do_workflow(cotacao, prontidao),
        # Fase 3C — o painel de negociação nasce já preenchido pelo mesmo payload que o
        # JavaScript recebe do preview: a vendedora vê o comercial; OWNER/ADMIN, a economia.
        "negociacao": _negociacao_inicial(session, cotacao, itens, economia),
        "venda": (session.get(Oportunidade, cotacao.oportunidade_id)
                  if cotacao.oportunidade_id else None),
        "editavel": (_valor_status(cotacao) not in wf.ESTADOS_IMUTAVEIS
                     and _valor_status(cotacao) not in wf.ESTADOS_LEGADOS),
        "revisoes": ws.revisoes_de(session, cotacao),
        "economia": economia,
        "estados_difal": estados(session), "regra_icms_atual": regra_icms,
        "contexto_fiscal": contexto, "condicoes": cfg.condicoes_pagamento(session),
        # Sinal / entrada (21/09/2026): percentual para o campo e o texto comercial da
        # condição composta — só isso vai à tela; encargo efetivo e fórmula ficam no motor.
        "sinal_pct": _sinal_para_campo(cotacao),
        "condicao_texto": cfg.condicao_textual(session, cotacao),
        "tipos_frete": [t.value for t in TipoFrete],
        # Premissa mais nova que a desta cotação. Só detecta; a tela oferece a escolha.
        "premissas_novas": (adm.premissas_desatualizadas(session, cotacao, itens)
                            if _valor_status(cotacao) not in wf.ESTADOS_IMUTAVEIS else None),
        "origem_fiscal": _origem_fiscal_da_tela(session, cotacao),
        "origens_logisticas": _origens_logisticas(session, cotacao, itens),
        "avisos_exclusao": arquivamento.motivos_para_pensar_duas_vezes(cotacao, len(itens)),
    })


@router.get("/cotacoes/{cotacao_id}/painel", response_class=HTMLResponse)
def painel_situacao(request: Request, cotacao_id: int, session: Session = Depends(get_session)):
    """O bloco de situação/ações, em HTML, para a tela trocar depois de uma negociação.

    A prontidão é reavaliada aqui, no servidor — a tela nunca decide sozinha se pode
    emitir ou se precisa de aprovação.
    """
    exigir_autenticado(request)
    cotacao = session.get(Cotacao, cotacao_id)
    if not cotacao:
        raise HTTPException(status_code=404, detail="Cotação não encontrada.")
    with ps.cache_de_leitura(session):      # ver a nota em `detalhe`
        prontidao = ws.avaliar(session, cotacao, frete=ws.frete_para_avaliar(session, cotacao))
    return templates.TemplateResponse(request, "_cotacao_situacao.html", {
        "cotacao": cotacao, "prontidao": prontidao,
        "acoes_workflow": acoes_do_workflow(cotacao, prontidao),
        "economia": ve_economia(request),
    })


@router.post("/cotacoes/{cotacao_id}/atualizar")
def atualizar_cabecalho(cotacao_id: int, condicao_pagamento: str = Form("30"),
                        estado_destino: str = Form(""), estado_origem: str = Form(""),
                        contribuinte_icms: str = Form("sim"), frete: str = Form(""),
                        freight_type: str = Form(TipoFrete.cif.value),
                        freight_valor: str = Form(""), prazo_entrega: str = Form(""),
                        contato_nome: str = Form(""), departamento_contato: str = Form(""),
                        validade_dias: int = Form(0), vendedor: str = Form(""),
                        observacoes: str = Form(""), observacao_cliente: str = Form(""),
                        termos_texto: str = Form(""), local_entrega: str = Form(""),
                        freight_manual_confirmado: str = Form(""),
                        freight_manual_obs: str = Form(""),
                        possui_sinal: str = Form(""),
                        percentual_sinal: str = Form(""),
                        request: Request = None,
                        session: Session = Depends(get_session)):
    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "alterar o cabeçalho")
    if not cotacao:
        return RedirectResponse(url="/cotacoes", status_code=303)

    # Sinal / entrada (21/09/2026): o formulário manda percentual (0–100); o modelo guarda
    # fração. Checkbox desmarcada = sem sinal, seja o que for que o campo numérico contenha.
    # Valor inválido (negativo, > 100, texto) é RECUSADO inteiro — nada do cabeçalho muda.
    try:
        sinal_novo = (para_float(percentual_sinal_do_formulario(percentual_sinal))
                      if possui_sinal in ("sim", "on", "1", "true") else 0.0)
    except SinalInvalido as exc:
        if request is None:
            raise HTTPException(status_code=400, detail=str(exc))
        return pagina_de_erro(
            request, titulo="Sinal inválido", status_code=400,
            motivos=[str(exc), "Informe um percentual entre 0 e 100."],
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    novo_contribuinte = (contribuinte_icms == "sim")
    # `estado_origem` saiu da comparação junto com o campo: ele é origem **logística**, o
    # motor fiscal não o consulta, e mantê-lo aqui fazia todo salvamento parecer mudança de
    # cenário — o formulário não o envia, então a comparação era sempre contra vazio.
    # Sinal e condição do saldo são MATERIAIS: mudam o encargo efetivo, logo B2B, tabela,
    # preço, comissão, margem e totais — o recálculo abaixo preserva o desconto negociado.
    mudou_precificacao = (cotacao.condicao_pagamento != condicao_pagamento
                          or D0(cotacao.percentual_sinal or 0) != D0(sinal_novo)
                          or cotacao.estado_destino != (estado_destino or None)
                          or cotacao.contribuinte_icms != novo_contribuinte
                          or (cotacao.freight_type or "") != (freight_type or ""))

    cotacao.condicao_pagamento = condicao_pagamento or "30"
    cotacao.percentual_sinal = sinal_novo
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
    valor_frete_novo = float(freight_valor) if freight_valor else None
    # Frete CIF manual confirmado (21/09/2026): só vale com tipo CIF e valor positivo. Mudar
    # o valor ou a confirmação é mudança material (entra no total e no fingerprint).
    confirmado_novo = (cotacao.freight_type == TipoFrete.cif.value
                       and freight_manual_confirmado in ("sim", "on", "1", "true")
                       and (valor_frete_novo or 0) > 0)
    mudou_frete = ((cotacao.freight_valor or 0) != (valor_frete_novo or 0)
                   or bool(cotacao.freight_manual_confirmado) != confirmado_novo)
    if confirmado_novo and (not cotacao.freight_manual_confirmado
                            or (cotacao.freight_valor or 0) != (valor_frete_novo or 0)):
        ator = usuario_da_request(request) if request is not None else None
        cotacao.freight_manual_por = getattr(ator, "email", None) or "sistema"
        cotacao.freight_manual_em = datetime.utcnow()
    if not confirmado_novo:
        cotacao.freight_manual_por = None
        cotacao.freight_manual_em = None
    cotacao.freight_manual_confirmado = confirmado_novo
    cotacao.freight_manual_obs = (freight_manual_obs or None) if confirmado_novo else None
    cotacao.freight_valor = valor_frete_novo
    cotacao.prazo_entrega = prazo_entrega or None
    cotacao.contato_nome = contato_nome or None
    cotacao.departamento_contato = departamento_contato or None
    cotacao.vendedor = vendedor or None
    cotacao.observacoes = observacoes or None            # interna — nunca vai ao PDF
    cotacao.observacao_cliente = observacao_cliente or None
    if local_entrega:
        cotacao.local_entrega = local_entrega
    cotacao.termos_texto = termos_texto or cotacao.termos_texto
    if validade_dias:
        cotacao.validade_dias = validade_dias
        base = cotacao.criado_em or datetime.utcnow()
        cotacao.validade_em = base + timedelta(days=validade_dias)
    _gravar_snapshot_fiscal(session, cotacao)
    session.add(cotacao)
    session.commit()

    # O formulário não é a única coisa que muda o cenário: finalidade do cliente, origem
    # fiscal, alíquota cadastrada. Se o cenário que forma preço HOJE não é o que está
    # congelado nos itens, salvar o cabeçalho reforma os preços — nunca deixa um item
    # afirmando um ICMS que a cotação não tem mais.
    if mudou_precificacao or cenario_dos_itens_divergiu(session, cotacao):
        _recalcular_todos_itens(cotacao, session)
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}?cenario=atualizado",
                                status_code=303)
    if mudou_frete:
        # O frete não muda o preço unitário, mas muda o total aprovado: a decisão anterior cai.
        ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="frete alterado")
        session.commit()
        return RedirectResponse(url=f"/cotacoes/{cotacao_id}?frete=atualizado", status_code=303)

    return RedirectResponse(url=f"/cotacoes/{cotacao_id}?salvo=1", status_code=303)


cenario_dos_itens_divergiu = com.cenario_dos_itens_divergiu


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
        mudou_de_politica = (it.politica_comercial != margem.politica)
        if it.modo_edicao == MODO_DESCONTO and not mudou_de_politica and _e_politica_nova(ctx):
            # Política nova → política nova: o desconto negociado é a intenção comercial e
            # é reaplicado sobre a tabela refeita. O preço absoluto muda com as premissas.
            pass
        elif it.custo_unitario > 0:
            # Trazer o rascunho para as premissas de hoje inclui a política de hoje: a
            # alavanca volta ao alvo vigente — B2B na política nova, margem-alvo nas outras.
            # Manter o alvo antigo (18%) enquanto o recomendado passa a 20% fabricava um
            # "desconto" que ninguém deu (cotação 0021 na auditoria de 17/09/2026); e um
            # preço negociado sob a política antiga não é um desconto sobre uma tabela que
            # não existia — a conversão de política é explícita e começa do B2B.
            it.modo_edicao = "margem"
            it.valor_editado = para_float(margem.margem_pct)
            it.modo_negociacao = None
            it.desconto_editado_pct = None
        _preencher_item(session, it, produto, margem, memoria_custo)
        res = _calcular(it.modo_edicao, it.custo_unitario, it.quantidade, it.valor_editado,
                        regras, it.preco_base, contexto=ctx, margem_alvo=margem.margem_pct,
                        base_comercial=_base_de_preco(it))
        _aplicar_resultado(it, res, regras, ctx)
        it.memoria_json = ps.memoria_json(ps.memoria_do_preco(
            session, produto, cotacao, preco_negociado=res.preco_negociado,
            quantidade=it.quantidade,
            comissao_formacao_pct=com.comissao_de_formacao_do_item(it),
            politica=com.politica_do_item(it)))
        session.add(it)

    session.flush()
    # Reprecificar é o ato explícito que traz o rascunho para a política vigente — e a
    # comissão da cotação é reaplicada sobre os itens já com a política nova.
    com.recalcular_comissao(session, cotacao)
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
    """Mudou o cenário (destino, contribuinte, condição, frete): **todo preço é reformado**.

    Cada item volta ao preço que o motor forma para o cenário NOVO, na margem-alvo do item
    (`modo_edicao = "margem"`). Um preço negociado era uma decisão sobre OUTRO cenário — mantê-lo
    exibiria como válido um número formado com outro ICMS, outro DIFAL ou outro encargo. Foi o
    que a auditoria de 17/09/2026 reproduziu: editar a quantidade congelava o item em modo
    "preço", e a troca de destino SP→RJ não contribuinte deixava R$ 69,88 onde o motor formava
    R$ 76,38. Quem quiser desconto renegocia sobre o preço do cenário certo.

    Preço travado (Daune) e item sem custo (cota pelo preço, já bloqueado) não são tocados
    além do recálculo normal. Toda mudança invalida a aprovação anterior — ela era sobre
    outra configuração.
    """
    itens = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao.id)).all()
    for it in itens:
        produto = session.get(Produto, it.produto_id) if it.produto_id else None
        if it.custo_unitario and it.custo_unitario > 0 and it.margem_padrao_pct is not None:
            if it.politica_comercial == ROTULO_2026_09_21 and it.modo_edicao == MODO_DESCONTO:
                # Política de 21/09: a alavanca persistida é o DESCONTO sobre a tabela. B2B e
                # tabela são refeitos para o cenário novo e o mesmo desconto é reaplicado —
                # o preço absoluto muda automaticamente, a intenção comercial fica. Se o
                # desconto levar abaixo do novo B2B, vira exceção (REQUER_APROVACAO), não é
                # ajustado em silêncio.
                pass
            else:
                it.modo_edicao = "margem"
                it.valor_editado = it.margem_padrao_pct
        regras, _regra, ctx = montar_regras(cotacao, session, produto, item=it)
        res = _calcular(it.modo_edicao, it.custo_unitario, it.quantidade, it.valor_editado,
                        regras, it.preco_base, contexto=ctx, margem_alvo=it.margem_padrao_pct,
                        base_comercial=_base_de_preco(it))
        _aplicar_resultado(it, res, regras, ctx)
        if produto is not None and regras is not None:
            it.memoria_json = ps.memoria_json(ps.memoria_do_preco(
                session, produto, cotacao, preco_negociado=res.preco_negociado,
                quantidade=it.quantidade,
                comissao_formacao_pct=com.comissao_de_formacao_do_item(it),
                politica=com.politica_do_item(it)))
        session.add(it)
    session.flush()
    # A comissão é da cotação: reaplicada a todos os itens da política de uma vez.
    com.recalcular_comissao(session, cotacao, itens)
    ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="cenário alterado — preços reformados")
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
        base = _base_de_preco(it)          # referência comercial; custo real quando não há pino
        if it.politica_comercial == ROTULO_2026_09_21:
            # Política 21/09: o recomendado É o B2B (menor centavo com margem ≥ alvo) e a
            # tabela é derivada dele — ambos do cenário desta cotação, nunca de cache.
            b2b, tabela = _b2b_e_tabela(base, it.margem_padrao_pct, regras,
                                        contexto or {}, it.preco_base)
            it.preco_recomendado = para_float(b2b.preco_negociado)
            it.preco_tabela = para_float(tabela)
            it.desconto_vs_tabela_pct = para_float(
                desconto_vs_tabela(res.preco_negociado, tabela))
            # B2B que o custo REAL daria — diagnóstico interno; o piso de autonomia é o comercial
            it.preco_b2b_economico = (para_float(preco_b2b(it.custo_unitario, it.margem_padrao_pct,
                                                           regras, it.preco_base).preco_negociado)
                                      if base != it.custo_unitario else it.preco_recomendado)
        else:
            it.preco_recomendado = para_float(
                calcular_por_margem(base, 1, it.margem_padrao_pct, regras).preco_negociado)
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
    # Sinal (21/09/2026): o que formou o encargo efetivo — fração à vista e encargo do saldo.
    it.percentual_sinal = contexto.get("percentual_sinal")
    it.encargo_saldo_pct = contexto.get("encargo_saldo_pct")
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
    custo_vivo, base_vivo, _memoria_vivo = ps.bases_de_preco(session, produto)
    if not custo_vivo:
        # Sem custo não há margem, mas o preço exibido continua sendo quantia comercial —
        # e só existe se alguém o digitou (o preço-base do catálogo não é referência).
        preco = dinheiro(valor if modo == "preco" else 0)
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

    if not margem.tem_regra:
        bloqueado = {"sem_custo": False, "sem_regra_de_margem": True, "preco_negociado": 0,
                     "faturamento": 0, "aviso": margem.regra, "preco_base": None}
        return JSONResponse(bloqueado if ve_economia(request) else sem_confidenciais(bloqueado))
    if margem.preco_travado:
        # Preço travado (Daune): a prévia mostra o único preço possível, qualquer que seja a
        # alavanca pedida — e diz que é travado. Tentar gravar outro é recusado ao adicionar.
        modo, valor = "margem", para_float(margem.margem_pct)
    if modo == "margem" and not (valor and valor > 0):
        # A tela da vendedora não recebe a margem (é confidencial) e manda 0: a prévia é
        # sempre no alvo da regra — nunca um preço a 0% de margem.
        valor = para_float(margem.margem_pct)
    res = _calcular(modo, custo_vivo, quantidade, valor, regras, produto.preco_base,
                    contexto=ctx, margem_alvo=margem.margem_pct, base_comercial=base_vivo)
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
        "preco_travado": bool(margem.preco_travado),
        "aviso": (com.MOTIVO_TRAVADO if margem.preco_travado else None),
    }
    if _e_politica_nova(ctx) and regras is not None and custo_vivo:
        # B2B/tabela da prévia sobre a BASE COMERCIAL — o mesmo número que o item salvo terá
        b2b, tabela = _b2b_e_tabela(base_vivo or custo_vivo, margem.margem_pct, regras, ctx, produto.preco_base)
        payload.update({"politica_nova": True, "preco_b2b": para_float(b2b.preco_negociado),
                        "preco_tabela": para_float(tabela),
                        "desconto_vs_tabela_pct": para_float(
                            desconto_vs_tabela(res.preco_negociado, tabela))})
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
    item.margem_padrao_pct = para_float(margem.margem_pct) if margem.tem_regra else None
    # Sem regra: a marca `SEM_REGRA_DE_MARGEM` é o que o workflow lê para bloquear o item
    # (o motivo humano vai no blocker). Nunca um 15% escondido.
    item.margem_regra = margem.regra if margem.tem_regra else wf.SEM_REGRA_DE_MARGEM
    item.margem_regra_id = margem.regra_id
    # A política comercial que formou este item fica congelada nele (Fase 3A): piso, comissão
    # de formação, preço travado e qual política era. Regra sem política deixa tudo nulo, e o
    # item é avaliado com a semântica anterior.
    item.piso_margem_pct = para_float(margem.piso_pct)
    item.comissao_formacao_pct = para_float(margem.comissao_formacao_pct)
    item.preco_travado = bool(margem.preco_travado)
    item.politica_comercial = margem.politica
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
    # Economia real × formação comercial (22/09/2026): o item pina a base que forma o B2B
    # (referência comercial, em R$) e a proteção usada; `custo_unitario` é o custo real.
    memoria_custo = memoria_custo or {}
    base = memoria_custo.get("base_comercial_brl")
    item.base_comercial_precificacao = para_float(base) if base else None
    item.protecao_comercial_pct = (memoria_custo.get("referencia_comercial") or {}).get("protecao_pct")


def _base_de_preco(it: CotacaoItem):
    """A base que forma B2B/tabela/recomendado do item: a referência comercial pinada; item
    anterior a 22/09 (sem pino) usa o próprio custo, como sempre fez."""
    base = getattr(it, "base_comercial_precificacao", None)
    return base if base else it.custo_unitario


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
    custo, base_comercial, memoria_custo = ps.bases_de_preco(session, produto)
    custo = custo or 0.0
    if custo <= 0:
        # sem custo: cota pelo preço que a pessoa DIGITAR, margem fica em branco (não se
        # inventa margem). O `preco_base` do catálogo não serve de default: é um cache
        # formado noutro cenário e, na Daune, o preço de venda legado (+96% sobre o
        # recomendado) — um número fabricado apareceria como "seu preço" (NAC-05).
        preco = valor if modo == "preco" else 0.0
        res = _calcular("preco", 0.0, quantidade, preco, regras, produto.preco_base)
        modo, valor = "preco", preco
    elif not margem.tem_regra:
        # Política 21/09: sem regra de margem não se forma preço. O item entra sem preço,
        # bloqueado (`SEM_REGRA_DE_MARGEM`), e o Admin vê o motivo — nada de 15% escondido.
        res = _resultado_bloqueado(quantidade, custo)
        modo, valor = "margem", None
    else:
        # Política 21/09: o item nasce no B2B da margem pedida (a da regra, salvo override
        # explícito de quem pode editar margem) — desconto de 50% sobre a tabela, de
        # propósito; a alavanca fica em "margem" até a vendedora negociar.
        res = _calcular(modo, custo, quantidade, valor, regras, produto.preco_base,
                        contexto=ctx, margem_alvo=margem.margem_pct, base_comercial=base_comercial)
        if margem.preco_travado and regras is not None:
            # Daune: o único unitário aceito é o recomendado da política. Qualquer alavanca
            # que produza outro preço é recusada — explicitamente, para todos os papéis.
            recomendado = dinheiro(calcular_por_margem(base_comercial or custo, 1, margem.margem_pct,
                                                       regras).preco_negociado)
            if dinheiro(res.preco_negociado) != recomendado:
                raise HTTPException(
                    status_code=409,
                    detail=(f"O preço de '{produto.nome}' é travado pela política comercial "
                            f"de 16/09/2026: só pode ser R$ {recomendado} (recomendado). "
                            f"R$ {dinheiro(res.preco_negociado)} foi recusado."))

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
        session, produto, cotacao, preco_negociado=res.preco_negociado, quantidade=quantidade,
        comissao_formacao_pct=com.comissao_de_formacao_do_item(item),
        politica=com.politica_do_item(item)))
    session.add(item)
    session.flush()
    # A comissão é recalculada: da cotação (política 16/09) ou item a item (21/09).
    com.recalcular_comissao(session, cotacao)
    ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="item adicionado")
    session.commit()
    session.refresh(item)
    return JSONResponse(_item_para_json(item, ve_economia(request)))


@router.put("/cotacoes/{cotacao_id}/itens/{item_id}")
async def editar_item(cotacao_id: int, item_id: int, request: Request,
                      session: Session = Depends(get_session)):
    form = await request.form()
    try:
        quantidade = float(str(form.get("quantidade") or "").replace(",", "."))
    except ValueError:
        quantidade = 0.0
    if not quantidade > 0:
        return JSONResponse({"erro": "A quantidade precisa ser maior que zero."}, status_code=400)

    cotacao = session.get(Cotacao, cotacao_id)
    if cotacao is not None:
        ws.exigir_editavel(cotacao, "editar item")
    item = session.get(CotacaoItem, item_id)
    if not cotacao or not item or item.cotacao_id != cotacao_id:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)

    # Sem `modo` no formulário é edição SÓ de quantidade: o item mantém a alavanca que tem
    # (margem-alvo ou preço negociado). Até 17/09/2026 a tela mandava `modo=preco` com o
    # preço corrente, e isso convertia silenciosamente um item de margem em preço fixo —
    # que depois não acompanhava a troca de cenário, e que virava R$ 0,00 permanente quando
    # o cenário estava bloqueado na hora da edição.
    modo = (form.get("modo") or "").strip() or None
    if modo is None or modo == "quantidade":
        modo, valor = item.modo_edicao or "margem", item.valor_editado
        if valor is None:
            modo, valor = "margem", item.margem_padrao_pct
    else:
        try:
            valor = float(str(form.get("valor") or "").replace(",", "."))
        except ValueError:
            valor = 0.0
        if modo == "preco" and not valor > 0:
            return JSONResponse({"erro": "O preço unitário precisa ser maior que zero."},
                                status_code=400)
        if modo in ("margem", "markup") and valor is not None and valor < 0:
            return JSONResponse({"erro": "Margem/markup não podem ser negativos."}, status_code=400)

    produto = session.get(Produto, item.produto_id) if item.produto_id else None
    regras, _regra, ctx = montar_regras(cotacao, session, produto, item=item)
    if modo == "preco" and item.politica_comercial == ROTULO_2026_09_21 and item.preco_tabela \
            and item.custo_unitario and regras is not None:
        # Política 21/09: um preço digitado vira DESCONTO sobre a tabela — é a alavanca que
        # se persiste, para que uma mudança de cenário rederive o preço em vez de congelá-lo.
        modo, valor = MODO_DESCONTO, para_float(desconto_vs_tabela(dinheiro(valor), item.preco_tabela))
        item.modo_negociacao = "preco"
        item.desconto_editado_pct = valor
    res = _calcular(modo, item.custo_unitario, quantidade, valor, regras, item.preco_base,
                    contexto=ctx, margem_alvo=item.margem_padrao_pct,
                    base_comercial=_base_de_preco(item))
    if item.preco_travado and regras is not None and item.custo_unitario \
            and item.margem_padrao_pct is not None:
        # Daune: quantidade muda; o unitário, não. Vendedora e admin recebem a mesma recusa.
        recomendado = dinheiro(calcular_por_margem(_base_de_preco(item), 1,
                                                   item.margem_padrao_pct,
                                                   regras).preco_negociado)
        if dinheiro(res.preco_negociado) != recomendado:
            raise com.PrecoTravado(item, recomendado, res.preco_negociado)

    item.quantidade = quantidade
    item.modo_edicao = modo
    item.valor_editado = valor
    _aplicar_resultado(item, res, regras, ctx)
    if produto:
        item.memoria_json = ps.memoria_json(ps.memoria_do_preco(
            session, produto, cotacao, preco_negociado=res.preco_negociado,
            quantidade=quantidade,
            comissao_formacao_pct=com.comissao_de_formacao_do_item(item),
            politica=com.politica_do_item(item)))
    session.add(item)
    session.flush()
    com.recalcular_comissao(session, cotacao)
    ws.invalidar_aprovacoes_obsoletas(session, cotacao, motivo="item editado")
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
            com.recalcular_comissao(session, cotacao)
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
        percentual_sinal=original.percentual_sinal or 0.0,
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
        custo_atual, base_atual, memoria_custo = (ps.bases_de_preco(session, produto_atual)
                                                  if produto_atual
                                                  else (it.custo_unitario, _base_de_preco(it), None))
        custo_atual = custo_atual or 0.0
        base_atual = base_atual or custo_atual

        margem_atual = ps.margem_padrao(session, produto_atual) if produto_atual else None
        modo, valor = "preco", it.preco_negociado
        if (margem_atual is not None and margem_atual.tem_regra and custo_atual > 0
                and _e_politica_nova(ctx) and regras is not None and it.preco_negociado):
            # Política 21/09: a cópia não carrega um preço absoluto de outro cenário como
            # alavanca — carrega o desconto equivalente sobre a tabela de HOJE (mesmo preço
            # inicial, alavanca coerente). Nada de híbrido preço-fixo dentro da política nova.
            _b2b, tabela = _b2b_e_tabela(base_atual, margem_atual.margem_pct, regras, ctx,
                                         preco_base_atual)
            modo, valor = MODO_DESCONTO, para_float(desconto_vs_tabela(it.preco_negociado, tabela))
        res = _calcular(modo, custo_atual, it.quantidade, valor, regras, preco_base_atual,
                        contexto=ctx,
                        margem_alvo=(margem_atual.margem_pct if margem_atual else None),
                        base_comercial=base_atual)
        novo_item = CotacaoItem(
            cotacao_id=nova.id, produto_id=it.produto_id, ordem=it.ordem,
            nome_produto=it.nome_produto, especificacao=it.especificacao,
            categoria=it.categoria, quantidade=it.quantidade, custo_unitario=custo_atual,
            preco_base=preco_base_atual or 0.0, modo_edicao=modo,
            valor_editado=valor,
            modo_negociacao=("preco" if modo == MODO_DESCONTO else None),
            desconto_editado_pct=(valor if modo == MODO_DESCONTO else None),
        )
        if produto_atual:
            _preencher_item(session, novo_item, produto_atual, margem_atual, memoria_custo)
        else:
            novo_item.base_comercial_precificacao = it.base_comercial_precificacao
            novo_item.protecao_comercial_pct = it.protecao_comercial_pct
        _aplicar_resultado(novo_item, res, regras, ctx)
        session.add(novo_item)
    session.flush()
    com.recalcular_comissao(session, nova)
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
    # 17/09/2026: o rascunho não sai com item formado num cenário que já não é o da cotação.
    # A prévia é ferramenta de trabalho e pode sair incompleta (linha sem preço, marcada);
    # o que ela não pode é mostrar um preço formado com outro ICMS como se fosse deste
    # cenário — isso não é rascunho, é dado errado.
    divergentes = com.cenario_dos_itens_divergiu(session, cotacao, itens)
    if divergentes:
        bloqueios.append(
            f"{len(divergentes)} item(ns) foram formados com um cenário fiscal diferente do "
            "atual da cotação. Clique em \"Atualizar cenário e recalcular\" antes de gerar o PDF.")
    if bloqueios:
        return pagina_de_erro(
            request, titulo="Não foi possível gerar o PDF",
            introducao="Antes de continuar, resolva:",
            motivos=bloqueios,
            ajuda=("O rascunho continua salvo e editável. O documento comercial só sai "
                   "quando todos os itens têm cenário fiscal, condição de pagamento e preço "
                   "resolvidos para o cenário atual."),
            voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    # Sessão 6: preview e documento final são a mesma folha para quem recebe. Enquanto a
    # cotação não estiver emitida, o PDF sai marcado — inclusive (e principalmente) quando
    # há aprovação pendente. Emitir de verdade é a rota `/emitir`.
    rascunho = cotacao.status not in (StatusCotacao.emitida.value,
                                      StatusCotacao.enviada.value)

    # **C-NEW-10.** O PDF final não sai por confiar no campo `status`. Sair sem marca d'água
    # é afirmar que existe um documento emitido, e documento emitido é o `SnapshotEmissao`:
    # `ws.emitir()` revalida blockers e exceções e congela a revisão. Se o snapshot não
    # existe, a emissão não aconteceu — o campo chegou ali por outro caminho, que foi
    # exatamente o C-NEW-09 (cotação "emitida" com item de R$ 0,00 e PDF final).
    #
    # É defesa em profundidade: com a rota de status fechada, `emitida` só se alcança pela
    # emissão canônica. Esta conferência é o que impede o próximo atalho de virar documento.
    if not rascunho:
        emissao = session.exec(
            select(SnapshotEmissao)
            .where(SnapshotEmissao.cotacao_id == cotacao.id)
            .where(SnapshotEmissao.revisao == cotacao.revisao)).first()
        if emissao is None:
            return pagina_de_erro(
                request, titulo="Não foi possível gerar o PDF final",
                introducao=f"A cotação está como {rotulos.cotacao(_valor_status(cotacao))}, "
                           "mas não existe emissão registrada para esta revisão:",
                motivos=["Sem o registro da emissão não há documento congelado — e o PDF "
                         "final afirma que ele existe."],
                ajuda=("Use a ação Emitir na cotação. Ela revalida os impedimentos, confere "
                       "as aprovações e congela o documento antes de ele virar proposta."),
                voltar=f"/cotacoes/{cotacao_id}", rotulo_voltar="Voltar para a cotação")

    if not cotacao.termos_texto:
        cotacao.termos_texto = cfg.txt(session, "termos_padrao")
    if not rascunho and not cotacao.emitida_em:
        cotacao.emitida_em = datetime.utcnow()

    # Fase 3C: o final nasce do snapshot congelado na emissão; o rascunho, da cotação viva.
    # A condição de pagamento vai por extenso ("30 dias"), nunca pelo código ("30").
    # Com sinal, o texto é a composição ("30% de sinal + 70% em 30/60/90 dias"); o PDF final
    # prefere o texto congelado no snapshot, quando ele existe (`pdf_bridge`).
    out_path = gerar_pdf_para_cotacao(
        cotacao, cliente, itens, rascunho=rascunho,
        snapshot=None if rascunho else emissao,
        condicao_label=cfg.condicao_textual(session, cotacao))
    cotacao.pdf_gerado_em = datetime.utcnow()
    session.add(cotacao)
    session.commit()

    nome = (f"Proposta Anara {cotacao.numero or cotacao.id}"
            f"{' R' + str(cotacao.revisao) if (cotacao.revisao or 1) > 1 else ''}"
            f"{' - RASCUNHO' if rascunho else ''} - {cliente.nome if cliente else 'Cliente'}.pdf")
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

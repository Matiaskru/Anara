from fastapi.templating import Jinja2Templates

from app.dinheiro import D, dinheiro

templates = Jinja2Templates(directory="app/templates")


def fmt_brl(v):
    """Quantia na tela. Passa pela MESMA régua do motor — não pela do `format`.

    `f"{v:,.2f}"` arredonda o binário: 2,675 vira "2,67", porque o float 2,675 é
    2,67499999999999982236431605997495353221893310546875. A política do sistema é
    `ROUND_HALF_UP` sobre o decimal, e ela vale também aqui — senão a tela contradiz o preço
    que o motor formou. Aceita `Decimal` e `float`.
    """
    if v in (None, ""):
        return "—"
    try:
        v = dinheiro(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{v:,.2f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {s}"


def fmt_pct(v, casas=1):
    """Percentual com **vírgula** decimal. `0,0759` vira "7,59%", não "7.59%".

    A vírgula não é preciosismo: a tela inteira usa vírgula para dinheiro, e um percentual
    com ponto no meio dela lê como número estrangeiro — ou, pior, como milhar.
    """
    if v in (None, ""):
        return "—"
    try:
        v = D(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v * 100:.{casas}f}".replace(".", ",") + "%"


def fmt_data(v):
    if not v:
        return "—"
    try:
        return v.strftime("%d/%m/%Y")
    except AttributeError:
        return str(v)


def fmt_num(v):
    if v in (None, ""):
        return "—"
    try:
        v = D(v)
    except (TypeError, ValueError):
        return str(v)
    if v == int(v):
        return f"{int(v):,}".replace(",", ".")
    s = f"{dinheiro(v):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return s


def fmt_datahora(v):
    if not v:
        return "—"
    try:
        return v.strftime("%d/%m/%Y %H:%M")
    except AttributeError:
        return str(v)


def fmt_data_curta(v):
    """`16/09` para listas densas; o ano só quando não é o corrente."""
    if not v:
        return "—"
    try:
        from datetime import date as _date
        if v.year == _date.today().year:
            return v.strftime("%d/%m")
        return v.strftime("%d/%m/%y")
    except AttributeError:
        return str(v)


templates.env.filters["brl"] = fmt_brl
templates.env.filters["pct"] = fmt_pct
templates.env.filters["data"] = fmt_data
templates.env.filters["datahora"] = fmt_datahora
templates.env.filters["data_curta"] = fmt_data_curta
templates.env.filters["num"] = fmt_num


# ---------------------------------------------------------------------------
# Classes visuais dos status (Fase 3C) — a cor mora aqui, não em cada template
# ---------------------------------------------------------------------------
_PILL_VENDA = {"RASCUNHO": "pill-rascunho", "ENVIADO": "pill-enviado",
               "NEGOCIACAO": "pill-negociacao"}
_PILL_COTACAO = {"rascunho": "pill-rascunho", "aguardando_aprovacao": "pill-aguardando",
                 "aprovada": "pill-aprovada", "emitida": "pill-emitida", "enviada": "pill-enviada",
                 "cancelada": "pill-cancelada", "fechada": "pill-legado", "pedido": "pill-legado",
                 "perdida": "pill-legado"}
_PILL_FINANCEIRO = {"EM_DIA": "pill-pago", "EM_ABERTO": "pill-negociacao", "ATRASADO": "pill-atrasado"}
_PILL_POS_VENDA = {"AGUARDANDO_ENTREGA": "pill-enviado", "AGUARDANDO_PAGAMENTO": "pill-negociacao",
                   "PAGO": "pill-pago", "ATRASADO": "pill-atrasado"}


def pill_venda(status, etapa=None) -> str:
    if hasattr(status, "value"):
        status = status.value
    if status == "GANHA":
        return "pill-vendido"
    if status == "PERDIDA":
        return "pill-perdido"
    return _PILL_VENDA.get(etapa or "", "pill-neutro")


def pill_cotacao(status) -> str:
    if hasattr(status, "value"):
        status = status.value
    return _PILL_COTACAO.get(str(status or ""), "pill-neutro")


def pill_pos_venda(status) -> str:
    return _PILL_POS_VENDA.get(str(status or ""), "pill-neutro")


templates.env.globals["pill_venda"] = pill_venda
templates.env.globals["pill_cotacao"] = pill_cotacao
def pill_financeiro(status) -> str:
    return _PILL_FINANCEIRO.get(str(status or ""), "pill-neutro")


def custo_pendente(status) -> bool:
    """O custo do item impede a proposta (sob consulta / revisão)? Para a tela marcar a linha
    sem escrever o código do status no template."""
    return str(status or "").upper() in ("A_COTAR", "REVIEW_REQUIRED")


#: Versão dos arquivos estáticos. Entra como `?v=` nas URLs de CSS/JS para o navegador não
#: reaproveitar um cache da fase anterior (o JavaScript antigo quebrava a tela nova).
VERSAO_ESTATICA = "3c5"
templates.env.globals["versao_estatica"] = VERSAO_ESTATICA
templates.env.globals["custo_pendente"] = custo_pendente
templates.env.globals["pill_pos_venda"] = pill_pos_venda
templates.env.globals["pill_financeiro"] = pill_financeiro


# ---------------------------------------------------------------------------
# Papel do usuário dentro do template (Sessão 4)
# ---------------------------------------------------------------------------
# Estes globais existem para o template **não montar** o bloco confidencial — não para
# escondê-lo com CSS. A diferença é a que importa: o que não é montado não chega ao
# navegador, e o que não chega não aparece no "ver código-fonte".
#
# Continua valendo que a autoridade é o backend: um template que esquecesse o `if` não
# abriria brecha em endpoint JSON, porque lá o corte é feito em `app.confidencial`.
def _ve_economia(request) -> bool:
    from app.permissoes import ve_economia
    return ve_economia(request)


def _administra(request) -> bool:
    from app.permissoes import administra
    return administra(request)


def _usuario(request):
    from app.permissoes import usuario_da_request
    return usuario_da_request(request)


def _aprova_cotacoes(request) -> bool:
    from app.permissoes import aprova_cotacoes
    return aprova_cotacoes(request)


templates.env.globals["ve_economia"] = _ve_economia
templates.env.globals["administra"] = _administra
templates.env.globals["aprova_cotacoes"] = _aprova_cotacoes
templates.env.globals["usuario_atual"] = _usuario


# ---------------------------------------------------------------------------
# Rótulos humanos dentro do template
# ---------------------------------------------------------------------------
# Os códigos internos continuam no banco, na trilha e na memória do preço. O que passa por
# aqui é a camada de leitura: o template chama `status|rotulo_cotacao` e recebe "Aguardando
# aprovação", não `aguardando_aprovacao`.
def _registrar_rotulos():
    from app import rotulos
    templates.env.filters["rotulo_custo"] = rotulos.custo
    templates.env.filters["rotulo_fiscal"] = rotulos.fiscal
    templates.env.filters["rotulo_pagamento"] = rotulos.pagamento
    templates.env.filters["rotulo_frete"] = rotulos.frete
    templates.env.filters["rotulo_cotacao"] = rotulos.cotacao
    templates.env.filters["rotulo_acao"] = rotulos.acao
    templates.env.filters["rotulo_blocker"] = rotulos.blocker
    templates.env.filters["rotulo_blocker_comercial"] = rotulos.blocker_comercial
    templates.env.filters["rotulo_excecao"] = rotulos.excecao
    templates.env.filters["rotulo_etapa"] = rotulos.etapa
    templates.env.filters["rotulo_oportunidade"] = rotulos.oportunidade
    templates.env.filters["rotulo_pos_venda"] = rotulos.pos_venda
    templates.env.globals["rotulo_venda"] = rotulos.venda
    templates.env.filters["rotulo_motivo"] = rotulos.motivo_perda
    templates.env.filters["rotulo_origem"] = rotulos.origem
    templates.env.filters["rotulo_atividade"] = rotulos.atividade
    templates.env.filters["rotulo_papel"] = rotulos.papel
    templates.env.filters["explicacao_custo"] = rotulos.explicacao_custo
    templates.env.globals["rotulo_evento"] = rotulos.evento_timeline
    templates.env.filters["rotulo_financeiro"] = rotulos.status_financeiro
    templates.env.filters["rotulo_familia"] = rotulos.familia


_registrar_rotulos()


# ---------------------------------------------------------------------------
# Erro como página, não como JSON
# ---------------------------------------------------------------------------
def pagina_de_erro(request, *, titulo: str, motivos=None, voltar: str = "/",
                   rotulo_voltar: str = "Voltar", introducao: str = "",
                   ajuda: str = "", acoes=None, status_code: int = 409):
    """Recusa de uma ação iniciada por clique, em HTML.

    Um clique de usuário não pode terminar num objeto JSON na barra de endereços. Isso
    acontecia ao gerar PDF de cotação bloqueada: a resposta era um 409 com `{"erro": ...}`,
    tecnicamente correta e completamente inútil para quem só queria a proposta.

    Os endpoints de API continuam devolvendo JSON — quem chama por `fetch` precisa de dado,
    não de página. A diferença é a origem da chamada, não o tipo do erro.

    `motivos` aceita string ou dicionário `{escopo, texto}`; o dicionário permite dizer a
    qual item a pendência pertence, que é a metade da informação que faltava.
    """
    return templates.TemplateResponse(
        request, "erro_acao.html",
        {"titulo": titulo, "motivos": motivos or [], "voltar": voltar,
         "rotulo_voltar": rotulo_voltar, "introducao": introducao, "ajuda": ajuda,
         "acoes": acoes or []},
        status_code=status_code)

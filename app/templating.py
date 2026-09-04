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
    if v in (None, ""):
        return "—"
    try:
        v = D(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v * 100:.{casas}f}%"


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


templates.env.filters["brl"] = fmt_brl
templates.env.filters["pct"] = fmt_pct
templates.env.filters["data"] = fmt_data
templates.env.filters["num"] = fmt_num


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


templates.env.globals["ve_economia"] = _ve_economia
templates.env.globals["administra"] = _administra
templates.env.globals["usuario_atual"] = _usuario

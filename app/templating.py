from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def fmt_brl(v):
    if v in (None, ""):
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{v:,.2f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {s}"


def fmt_pct(v, casas=1):
    if v in (None, ""):
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v*100:.{casas}f}%"


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
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v == int(v):
        return f"{int(v):,}".replace(",", ".")
    s = f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return s


templates.env.filters["brl"] = fmt_brl
templates.env.filters["pct"] = fmt_pct
templates.env.filters["data"] = fmt_data
templates.env.filters["num"] = fmt_num

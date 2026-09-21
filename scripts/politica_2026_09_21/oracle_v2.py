"""Oracle independente da política de 21/09/2026 — Decimal puro, sem importar o motor.

Reimplementa, do zero, o que o motor precisa reproduzir: decomposição da linha com comissão
sobre a base líquida de ICMS, B2B como primeiro centavo válido, tabela, escada de comissão,
fiscal por UF (base única). Se o motor e este arquivo concordarem em dezenas de milhares de
cenários, a chance de os dois errarem igual é pequena — é o papel de um oracle.
"""
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal, getcontext

getcontext().prec = 34
C = Decimal("0.01")


def q(v):
    return v.quantize(C, rounding=ROUND_HALF_UP)


def pis_cofins(nominal, icms, fcp):
    return nominal * (1 - max(icms - fcp, Decimal(0)))


def encargo_efetivo(sinal, encargo_saldo):
    """Sinal / entrada (21/09/2026): o sinal é pago à vista sem encargo; a condição do saldo
    incide só sobre a parte financiada. `encargo_saldo` None = condição sem taxa (bloqueia),
    salvo com sinal de 100%, em que o saldo é irrelevante e o encargo é zero."""
    sinal = Decimal(str(sinal))
    if sinal >= 1:
        return Decimal(0)
    if encargo_saldo is None:
        return None
    return (1 - sinal) * Decimal(str(encargo_saldo))


def linha(cnet, preco, qtd, icms, pc, enc, taxa_comissao, icms_ded):
    fat = q(preco * qtd)
    custo = q(cnet * qtd)
    impostos = q(fat * (icms + pc + enc))
    base = fat * (1 - icms_ded)
    com = q(base * taxa_comissao)
    lucro = fat - impostos - com - custo
    return {"fat": fat, "custo": custo, "impostos": impostos, "base": q(base), "comissao": com,
            "lucro": lucro, "margem": (lucro / fat) if fat else Decimal(0)}


def b2b(cnet, margem, icms, pc, enc, icms_ded, comissao=Decimal("0.05")):
    """Primeiro centavo com margem ≥ alvo: forma fechada, desce até falhar, sobe até cumprir."""
    denom = 1 - icms - pc - enc - comissao * (1 - icms_ded) - margem
    if denom <= 0:
        return None
    preciso = cnet / denom
    cand = (preciso - Decimal("0.05")).quantize(C, rounding="ROUND_DOWN")
    cand = max(cand, C)

    def ok(p):
        return linha(cnet, p, 1, icms, pc, enc, comissao, icms_ded)["margem"] >= margem
    guard = 0
    while cand > C and ok(cand):
        cand -= C; guard += 1
        if guard > 2000:
            return None
    guard = 0
    while not ok(cand):
        cand += C; guard += 1
        if guard > 2000:
            return None
    return cand


def tabela(b2b_preco, fator=Decimal(2)):
    return q(b2b_preco * fator)


def desconto(preco, tab):
    d = 1 - preco / tab
    return d if d > 0 else Decimal(0)


def preco_por_desconto(tab, d):
    bruto = (tab * (1 - d)).quantize(Decimal("1e-9"), rounding=ROUND_HALF_UP)
    return bruto.quantize(C, rounding=ROUND_UP)


def faixa(d):
    if d <= 0:
        return Decimal("0.10")
    for lim, taxa in ((Decimal("0.10"), Decimal("0.09")), (Decimal("0.20"), Decimal("0.08")),
                      (Decimal("0.30"), Decimal("0.07")), (Decimal("0.40"), Decimal("0.06"))):
        if d <= lim:
            return taxa
    return Decimal("0.05")


# fiscal — base única, origem SP
INTER_IMPORTADA = Decimal("0.04")
UF_12 = {"MG", "PR", "RJ", "RS", "SC"}
BASE = {"AC": "0.19", "AL": "0.205", "AP": "0.18", "AM": "0.20", "BA": "0.205", "CE": "0.20", "DF": "0.20",
        "ES": "0.17", "GO": "0.19", "MA": "0.23", "MT": "0.17", "MS": "0.17", "MG": "0.18", "PA": "0.19",
        "PB": "0.20", "PR": "0.195", "PE": "0.205", "PI": "0.225", "RJ": "0.20", "RN": "0.20", "RS": "0.17",
        "RO": "0.195", "RR": "0.20", "SC": "0.17", "SP": "0.18", "SE": "0.19", "TO": "0.20"}
FCP = {"AL": "0.01", "RJ": "0.02", "SE": "0.01"}
CF = {"USO_CONSUMO", "ATIVO_IMOBILIZADO"}


def fiscal(uf, natureza, contribuinte, finalidade, familia_no_escopo=True):
    base = Decimal(BASE[uf])
    fcp = Decimal(FCP.get(uf, "0"))
    if uf == "SP":
        return {"icms": Decimal("0.18"), "fcp": Decimal(0), "difal": None, "resp": "NAO_APLICAVEL",
                "icms_ded": Decimal("0.18")}
    inter = INTER_IMPORTADA if natureza == "IMPORTADA" else (Decimal("0.12") if uf in UF_12 else Decimal("0.07"))
    if contribuinte and finalidade not in CF:
        return {"icms": inter, "fcp": Decimal(0), "difal": None, "resp": "NAO_APLICAVEL", "icms_ded": inter}
    if contribuinte:
        return {"icms": inter, "fcp": Decimal(0), "difal": base - inter, "resp": "DESTINATARIO", "icms_ded": inter}
    if not familia_no_escopo:
        return {"bloqueado": True}
    return {"icms": inter + (base - inter) + fcp, "fcp": fcp, "difal": base - inter, "resp": "REMETENTE",
            "icms_ded": inter + (base - inter)}

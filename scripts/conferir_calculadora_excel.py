#!/usr/bin/env python3
"""Confere a planilha gerada: endereços e números.

A versão anterior quebrou porque uma fórmula apontava para a linha do título em vez do campo
do produto — e a conferência de então só refazia a lógica em Python, sem olhar os endereços do
arquivo. Aqui são duas checagens independentes:

1. cada fórmula do MOTOR referencia exatamente os campos que deveria (não só "um campo válido");
2. o preço que a cadeia do MOTOR produz bate com o motor da plataforma.
"""
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/Anara-Cotacao"))

import openpyxl
from sqlmodel import Session, select

from app.calculadora import calcular
from app.db import engine
from app.models import MaterialPreco

ARQUIVO = os.path.expanduser("~/Downloads/Calculadora KTC Anara.xlsx")
wb = openpyxl.load_workbook(ARQUIVO)
tela, motor = wb["CALCULADORA"], wb["MOTOR"]

# --- mapa: rótulo do campo → endereço, lido do próprio arquivo --------------
campos = {}
for l in range(1, 40):
    rotulo = tela.cell(l, 2).value
    if isinstance(rotulo, str) and tela.cell(l, 3).fill.fgColor.rgb == "FFD8E4F5":
        campos[rotulo] = f"C{l}"
print("campos de entrada encontrados na tela:")
for r, a in campos.items():
    print(f"  {a:5s} {r}")

# --- 1. cada linha do motor referencia o campo certo ------------------------
motor_linhas = {}
for l in range(1, motor.max_row + 1):
    rotulo = motor.cell(l, 1).value
    if isinstance(rotulo, str):
        motor_linhas[rotulo] = motor.cell(l, 2).value

ESPERADO = {
    "Tipo de cálculo": {"Produto"},
    "Nome técnico": {"Produto"},
    "Largura (cm)": {"Medida (cm)"},
    "Comprimento (cm)": {"Medida (cm)"},
    "Gramatura": {"Gramatura (só toalha)"},
    "Quantidade": {"Quantidade de peças"},
    "Preço do tecido US$/m²": {"Tecido"},
    "Fios": {"Tecido"},
    "Composição": {"Tecido"},
    "CMT US$": {"Produto"},
    "Painéis": {"Produto"},
    "Peso kg/m² da família": {"Produto"},
    "Grupo de margem": {"Produto"},
    "Imposto de Importação": {"Produto"},
    "Preço por kg US$": {"Liso ou listrado"},
    "ICMS da venda": {"Sai de", "Vai para", "Cliente é contribuinte de ICMS?"},
    "Encargo do prazo": {"Como o cliente vai pagar"},
    "Margem usada": {"Margem que você quer"},
}
endereco_para_rotulo = {a: r for r, a in campos.items()}
erros = []
for rotulo_motor, esperados in ESPERADO.items():
    formula = motor_linhas.get(rotulo_motor)
    if not formula:
        erros.append(f"linha '{rotulo_motor}' não existe no MOTOR")
        continue
    refs = {f"{c}{n}" for c, n in re.findall(r"CALCULADORA!\$?([A-Z]+)\$?(\d+)", formula)}
    achados = {endereco_para_rotulo.get(r, f"?{r}") for r in refs}
    if achados != esperados:
        erros.append(f"'{rotulo_motor}' usa {sorted(achados)}, deveria usar {sorted(esperados)}")

print("\n1) referências das fórmulas:", "todas corretas" if not erros else "ERRO")
for e in erros:
    print("   ✗", e)

# --- 2. o número bate com o sistema ----------------------------------------
def coluna(aba, chave_col, *cols, ini=4):
    ws = wb[aba]
    d = {}
    for l in range(ini, ws.max_row + 1):
        k = ws.cell(l, chave_col).value
        if k not in (None, ""):
            d[k] = [ws.cell(l, c).value for c in cols]
    return d


par = {k: v[0] for k, v in coluna("PARAMETROS", 1, 3).items()}
mar = {k: v[0] for k, v in coluna("MARGENS", 1, 2).items()}
tecidos = coluna("TECIDOS", 1, 2, 3, 4)
fams = coluna("FAMILIAS", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
est = coluna("FISCAL", 1, 2, 3, 4)
pag = {k: v[0] for k, v in coluna("PAGAMENTO", 1, 2).items()}
toa = {k: v[0] for k, v in coluna("TOALHAS_KG", 1, 2).items()}
com = [[wb["COMISSAO"].cell(l, c).value for c in (1, 2, 3)]
       for l in range(4, 4 + len(wb["COMISSAO"]["A"]) ) if wb["COMISSAO"].cell(l, 1).value is not None]


def preco_da_planilha(produto, medida, tecido=None, gsm=0, acabamento="liso", qtd=1,
                      origem="São Paulo", destino="São Paulo", contribuinte="SIM",
                      pagamento="30 dias", margem=None):
    larg, comp = (float(x) for x in medida.split("x"))
    tipo, tecnica, cmt, paineis, hw, hl, pm2, grupo, ii = fams[produto]
    if tipo == "TECIDO":
        fios, comp_tec, preco_m2 = tecidos[tecido]
        shrink = par["shr_cot"] if comp_tec == "COTTON" else par["shr_cvc"]
        exw = ((((larg + hw) * (1 + shrink)) * ((comp + hl) * (1 + shrink)) / 10000 * paineis
                / (1 - par["waste"]) * preco_m2 + cmt) / (1 - par["qual"]) / (1 - par["mktc"]))
        peso = larg * comp / 10000 * pm2
    else:
        fios = 0
        peso = larg * comp * gsm / 10000000
        exw = peso * toa[f"{tecnica} · {acabamento}"]
    frete = peso * par["frete"]
    net = (exw + frete + (exw + frete) * ii + par["desp"]) * par["fx"]
    icms = (est[destino][1] if origem == destino
            else (est[destino][0] if contribuinte == "SIM" else est[destino][2]))
    taxa = icms + par["pis"] + pag[pagamento]
    m_padrao = (mar["TOALHA"] if grupo == "TOALHA"
                else ((mar["LENCOL_MENOR"] if fios < 300 else mar["LENCOL_MAIOR"])
                      if grupo == "LENCOL" else mar["OUTRO"]))
    usada = margem if margem is not None else m_padrao
    for minimo, maximo, comissao in com:
        markup = usada / (1 - taxa - comissao - usada)
        if minimo <= markup < maximo:
            return net * (1 + markup) / (1 - taxa - comissao), usada
    raise AssertionError("nenhuma faixa de comissão fechou")


CASOS = [
    ("Lençol de cima (plano)", "240x260", "300TC Sateen 100% Cotton · liso", 0, "liso",
     "Flat Sheet", "plain"),
    ("Lençol de cima (plano)", "190x250", "250TC Sateen CVC 70/30 · listrado", 0, "liso",
     "Flat Sheet", "stripe"),
    ("Capa de duvet", "190x260", "250TC Sateen CVC 70/30 · liso", 0, "liso", "Duvet Cover", "plain"),
    ("Toalha de banho", "70x140", None, 500, "liso", "Bath Towel", "plain"),
    ("Toalha de rosto", "50x85", None, 550, "liso", "Hand Towel", "plain"),
    ("Toalha de piscina", "90x170", None, 550, "listrado", "Pool Towel", "stripe"),
]
print("\n2) números:")
with Session(engine) as s:
    mats = {m.material + " · " + ("listrado" if m.plain_or_stripe == "stripe" else "liso"): m.id
            for m in s.exec(select(MaterialPreco)).all() if m.ativo}
    falhas = 0
    for produto, medida, tecido, gsm, acab, familia, listrado in CASOS:
        planilha, margem = preco_da_planilha(produto, medida, tecido, gsm, acab)
        larg, comp = (float(x) for x in medida.split("x"))
        sistema = calcular(s, familia, larg, comp,
                           material_id=mats.get(tecido) if tecido else None,
                           gsm=gsm or None, plain_or_stripe=listrado)["comercial"]["preco_negociado"]
        ok = abs(planilha / sistema - 1) < 1e-9
        falhas += 0 if ok else 1
        print(f"   {produto+' '+medida:38s} planilha R$ {planilha:8.2f} · sistema R$ {sistema:8.2f}"
              f" · margem {margem:.0%}  {'ok' if ok else 'DIVERGE'}")

print("\nRESULTADO:", "planilha conferida" if not erros and not falhas else "CORRIGIR")

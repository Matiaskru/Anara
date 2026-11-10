"""Gera a Tabela de preços do sistema a partir da Tabela comercial publicada (xlsx) — linhas E preços.

Política de 10/11/2026 (Matias): **o preço da tabela fica parado**; câmbio e custo mexem na margem, não no
preço. Por isso a página não recalcula nada: mostra o B2B que a planilha publicou, por código, estado,
contribuinte e prazo (a tabela cheia é 2 × B2B e a escada sai de `preco_por_desconto`, como na planilha —
o script confere as duas coisas em todas as células e recusa se alguma não bater).

Itens novos (pedido do Matias em 10/11: "até o tapete também" e "incluo os 600 fios na tabela? pode"), da
cotação KTC ANARA20102026 (`scripts/ktc_20_10_2026_11_10/itens.json`), com preço fixo formado a 5,19
(`precos_ktc_20_10.py`):
  - 600 fios 100% algodão, lençóis e capas: subseção no topo de "TECIDO 100% ALGODÃO ACABAMENTO CLASSIC"
    (antes da 400 fios — a planilha vai do maior número de fios para o menor);
  - 600 fios 100% algodão, fronhas: subseção no fim de "FRONHAS CLASSIC";
  - tapetes de banheiro: seção no fim.

Saídas:
  - `app/tabela_comercial_layout.py`  — as linhas, na ordem da planilha (seções, textos, "sob consulta");
  - `app/tabela_comercial_precos.json` — B2B fixo por código × "UF|SIM/NÃO" × prazo.

Uso: python3 scripts/tabela_precos_2026_11_10/gerar_layout.py "<Tabela comercial ... REV5.xlsx>" \
         scripts/ktc_20_10_2026_11_10/itens.json scripts/tabela_precos_2026_11_10/precos_ktc_20_10_5_19.json
"""
import json
import os
import pprint
import sys

import openpyxl

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from app.dinheiro import D  # noqa: E402
from app.pricing_engine import preco_por_desconto  # noqa: E402
from app.tabela_comercial import VERSAO  # noqa: E402

COR_SECAO = "FF30354F"          # faixa azul-marinho da planilha (nível 1); a creme é o nível 2
SOB_CONSULTA = "Preço sob consulta"
#: ordem dos blocos de prazo na aba BASE da planilha = códigos de condição do sistema
PRAZOS = ["À VISTA", "30", "30/60", "30/60/90", "30/60/90/120", "30/60/90/120/150"]
DESCONTOS = ("0.10", "0.20", "0.30", "0.40")
SECAO_600 = "600 fios 100% algodão"
SECAO_TAPETES = "TAPETES DE BANHEIRO"


def ler_linhas(wb):
    ws = wb["TABELA"]
    linhas = []
    for r in range(13, ws.max_row + 1):
        a, b, c, d, e = (ws.cell(r, k).value for k in range(1, 6))
        if a is None and b is None:
            continue
        if a and not b and not c:
            cor = ws.cell(r, 1).fill.fgColor.rgb if ws.cell(r, 1).fill else None
            linhas.append({"tipo": "secao", "nivel": 1 if cor == COR_SECAO else 2, "texto": str(a).strip()})
            continue
        sob = isinstance(e, str) and e.startswith(SOB_CONSULTA)
        linhas.append({"tipo": "item", "codigo": str(a).strip(), "descricao": (b or "").strip(),
                       "medida": (str(c).strip() if c is not None else ""),
                       "especificacao": (str(d).strip() if d is not None else ""), "sob_consulta": sob})
    return linhas


def ler_precos(wb):
    """B2B fixo por código e "UF|SIM/NÃO"; confere tabela = 2 × B2B e a escada em cada bloco."""
    precos, falhas = {}, []
    for r in wb["BASE"].iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        codigo, uf, contrib = r[1], r[2], r[3]
        b2bs = []
        for k in range(len(PRAZOS)):
            bloco = r[4 + 6 * k:10 + 6 * k]
            if not all(isinstance(x, (int, float)) for x in bloco):
                b2bs.append(None)                      # "consultar"
                continue
            tabela, b2b = D(bloco[0]), D(bloco[5])
            escada = [round(float(preco_por_desconto(tabela, D(d))), 2) for d in DESCONTOS]
            if float(tabela) != float(b2b * 2) or escada != [round(float(x), 2) for x in bloco[1:5]]:
                falhas.append((r[0], PRAZOS[k]))
            b2bs.append(float(b2b))
        precos.setdefault(codigo, {})[f"{uf}|{contrib}"] = b2bs
    if falhas:
        raise SystemExit(f"recusado: tabela/escada não derivam do B2B em {falhas[:5]} ({len(falhas)} blocos)")
    return precos


def main():
    wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
    linhas, precos = ler_linhas(wb), ler_precos(wb)
    itens = [x for x in linhas if x["tipo"] == "item"]
    codigos = [x["codigo"] for x in itens]
    repetidos = sorted({c for c in codigos if codigos.count(c) > 1})
    sem_preco = [x["codigo"] for x in itens if not x["sob_consulta"] and x["codigo"] not in precos]
    if repetidos or sem_preco:
        raise SystemExit(f"recusado: repetidos={repetidos} com preço e sem BASE={sem_preco}")

    novos = json.load(open(sys.argv[2], encoding="utf-8"))["novos"]
    fixos = json.load(open(sys.argv[3], encoding="utf-8"))
    if fixos["prazos"] != PRAZOS:
        raise SystemExit(f"recusado: prazos dos itens novos {fixos['prazos']} ≠ {PRAZOS}")
    preco_novo = {x["codigo"]: x["precos"] for x in fixos["itens"]}
    if sorted(preco_novo) != sorted(x["codigo"] for x in novos):
        raise SystemExit("recusado: itens novos e preços fixos não são os mesmos códigos")

    def item(n):
        if n["codigo"] in codigos:
            raise SystemExit(f"recusado: código {n['codigo']} já está na tabela")
        precos[n["codigo"]] = preco_novo[n["codigo"]]
        return {"tipo": "item", "codigo": n["codigo"], "descricao": n["descricao"], "medida": n["medida"],
                "especificacao": n["especificacao"], "sob_consulta": False}

    def posicao(texto, nivel):
        achadas = [i for i, x in enumerate(linhas) if x["tipo"] == "secao" and x["texto"] == texto
                   and x["nivel"] == nivel]
        if len(achadas) != 1:
            raise SystemExit(f"recusado: seção {texto!r} (nível {nivel}) aparece {len(achadas)} vezes")
        return achadas[0]

    grupo = {g: [item(n) for n in novos if n["grupo"] == g] for g in ("lencol600", "fronha600", "tapete")}
    # fronhas 600: subseção no fim de FRONHAS CLASSIC (antes da próxima seção de nível 1)
    i = posicao("FRONHAS CLASSIC", 1) + 1
    while i < len(linhas) and not (linhas[i]["tipo"] == "secao" and linhas[i]["nivel"] == 1):
        i += 1
    linhas[i:i] = [{"tipo": "secao", "nivel": 2, "texto": SECAO_600}] + grupo["fronha600"]
    # lençóis e capas 600: subseção logo abaixo do título TECIDO 100% ALGODÃO, antes da 400 fios
    i = posicao("TECIDO 100% ALGODÃO ACABAMENTO CLASSIC", 1) + 1
    if linhas[i] != {"tipo": "secao", "nivel": 2, "texto": "400 fios 100% algodão"}:
        raise SystemExit(f"recusado: esperava a subseção 400 fios logo abaixo de TECIDO 100% ALGODÃO, veio {linhas[i]}")
    linhas[i:i] = [{"tipo": "secao", "nivel": 2, "texto": SECAO_600}] + grupo["lencol600"]
    linhas.append({"tipo": "secao", "nivel": 1, "texto": SECAO_TAPETES})
    linhas.extend(grupo["tapete"])

    n = sum(1 for x in linhas if x["tipo"] == "item")
    sob = sum(1 for x in linhas if x["tipo"] == "item" and x["sob_consulta"])
    destino = os.path.join(RAIZ, "app", "tabela_comercial_layout.py")
    with open(destino, "w", encoding="utf-8") as f:
        f.write('"""Linhas da Tabela de preços, na ordem da planilha das vendedoras.\n\n'
                "Gerado por `scripts/tabela_precos_2026_11_10/gerar_layout.py` a partir do xlsx da tabela\n"
                "publicada + os itens da cotação KTC 20/10 (600 fios e tapetes). **Não editar à mão.** Os\n"
                'preços fixos estão em `app/tabela_comercial_precos.json`.\n"""\n\n')
        f.write(f"VERSAO = {VERSAO!r}\n\n")
        f.write(f"#: {n} produtos ({sob} sob consulta) e {sum(1 for x in linhas if x['tipo'] == 'secao')} "
                "cabeçalhos de seção\n")
        f.write("LINHAS = " + pprint.pformat(linhas, width=110, sort_dicts=False) + "\n")
    json.dump({"versao": VERSAO, "prazos": PRAZOS, "precos": precos},
              open(os.path.join(RAIZ, "app", "tabela_comercial_precos.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"{destino}: {n} produtos, {sob} sob consulta; preços fixos de {len(precos)} códigos")


if __name__ == "__main__":
    main()

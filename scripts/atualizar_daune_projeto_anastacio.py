"""Reconcilia o tarifário "Projeto Anastacio.xlsx" da Daune com o catálogo — e só aplica o que
casa de forma inequívoca.

    python3 scripts/atualizar_daune_projeto_anastacio.py            # só o relatório
    python3 scripts/atualizar_daune_projeto_anastacio.py --aplicar  # registra as versões

## A fonte

Duas abas. `Nova Cotação 05.08.26` é a mais nova para os itens que ela **contém**;
`Cotação 24.06.26` é histórica. Ausência na aba nova não quer dizer descontinuado: a
referência vigente anterior fica como está.

## Como se casa um produto

Por campos estruturados, nunca por nome: `familia`, `gsm`, `largura_cm`, `comprimento_cm`, e a
composição por **igualdade exata** de um token normalizado (minúsculas, sem acento, singular de
"plumas"). A normalização é a única concessão, e ela não aproxima nada que importe: pluma de
ganso não vira poliéster, 180 não vira 250, e 1,93×2,03 não vira 2,03×2,03.

Um SKU que exista só com **medida** coincidente não é match. Foi a armadilha registrada no
AUDIT §2.2.1 — a mesma medida com outra gramatura é outro produto.

## O que cada classificação significa

    EXACT_SAME         referência direta vigente com o MESMO bruto — nada a fazer
    EXACT_NEW          SKU sem referência direta ganha uma — fecha A_COTAR
    PRICE_CHANGED      mesmo SKU, referência direta vigente com bruto DIFERENTE
    NO_MATCH           nenhum SKU tem essa combinação de atributos
    AMBIGUOUS          mais de um SKU candidato, ou atributo que a fonte não dá
    CONFLICT           a fonte contradiz informação estrutural que o catálogo já tem

Só EXACT_NEW e PRICE_CHANGED são aplicáveis. AMBIGUOUS e CONFLICT ficam intactos e vão para o
relatório — a escolha é humana.

## Precisão

O bruto entra **exatamente** como está na célula. `registrar_daune` converte por `D()` (via
repr, sem herdar erro binário) e o CNET sai da fórmula fechada `bruto − 12% − 9,25% sobre a
base líquida` — a mesma que reproduz 249,37 → 199,146882. Nenhum valor é arredondado porque
"parece estranho": outlier é sinalizado, não corrigido.
"""
import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import custo_service as cs  # noqa: E402
from app.db import engine  # noqa: E402
from app.dinheiro import D  # noqa: E402
from app.models import Fornecedor, Produto, StatusCusto  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOME_CANONICO = "Projeto Anastacio.xlsx"
DESTINO_REFERENCIA = os.path.join(RAIZ, "referencia", NOME_CANONICO)
ABA_NOVA = "Nova Cotação 05.08.26"
ABA_ANTIGA = "Cotação 24.06.26"
#: A data que o próprio documento traz — o nome da aba. Não é validade: é a data da cotação.
DATA_DA_FONTE = date(2026, 8, 5)
DATA_DE_RECEBIMENTO = date(2026, 9, 13)
FONTE = f"{NOME_CANONICO} · {ABA_NOVA}"
NOTAS = ("Atualização de tarifário Daune / Projeto Anastacio — lista atualizada pelo "
         "fornecedor, incluindo preços que haviam sido solicitados por estarem faltando. "
         f"Cotação do fornecedor datada de {DATA_DA_FONTE:%d/%m/%Y} (nome da aba); "
         f"recebida em {DATA_DE_RECEBIMENTO:%d/%m/%Y}. Validade não informada na fonte.")
SAIDA = os.path.join(RAIZ, "relatorios", "reconciliacao_daune_projeto_anastacio.json")

APLICAVEIS = {"EXACT_NEW", "PRICE_CHANGED"}


# ---------------------------------------------------------------------------
# Normalização — a única aproximação permitida, e ela é declarada aqui
# ---------------------------------------------------------------------------
def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def composicao_canonica(texto: str) -> str:
    """`'180GSM 100% pluma de ganso'` → `'100% pluma de ganso'`; `'plumas'` → `'pluma'`.

    Igualdade exata depois disto. Não há distância de edição, não há "parecido com".
    """
    t = _sem_acento((texto or "").lower())
    t = re.sub(r"\b\d+\s*gsm\b", " ", t)           # a gramatura vai para o próprio campo
    t = t.replace("plumas", "pluma").replace("penas", "pena")
    t = t.replace(" e ", " ")                        # "90% pena e 10% plumas"
    return re.sub(r"\s+", " ", t).strip()


def gramatura(texto: str):
    m = re.search(r"(\d+)\s*gsm", (texto or "").lower())
    return int(m.group(1)) if m else None


def dimensoes_cm(texto: str):
    """`'1,90x2,60'` → (190, 260); `'50x70'` → (50, 70). Metros viram centímetros exatos."""
    t = (texto or "").lower().replace(" ", "")
    m = re.match(r"^(\d+(?:,\d+)?)x(\d+(?:,\d+)?)$", t)
    if not m:
        return None
    partes = []
    for p in m.groups():
        if "," in p:
            partes.append(int(round(float(p.replace(",", ".")) * 100)))
        else:
            partes.append(int(p))
    return tuple(partes)


def familia_canonica(produto_fonte: str):
    p = _sem_acento((produto_fonte or "").lower())
    if "edredom" in p:
        return "Duvet Insert"
    if "capa protetora" in p:
        return "Pillow Protector"
    if "protetor de colchao" in p:
        return "Mattress Protector"
    if "pillow top" in p:
        return "Mattress Topper"
    if "travesseiro" in p:
        return "Pillow"
    return None


# ---------------------------------------------------------------------------
# Leitura da fonte — os valores saem exatamente como estão na célula
# ---------------------------------------------------------------------------
def ler_fonte(caminho: str) -> dict:
    wb = openpyxl.load_workbook(caminho, data_only=True)
    abas = {}
    for ws in wb.worksheets:
        linhas = []
        for row in ws.iter_rows(min_row=2):
            a, b, c, d = (row[i].value if i < len(row) else None for i in range(4))
            if a in (None, "PRODUTO") or d is None:
                continue
            linhas.append({
                "aba": ws.title, "linha": row[0].row,
                "produto": str(a).strip(), "especificacao": str(b).strip(),
                "dimensao": str(c).strip(),
                "preco_raw": d, "preco": str(D(d)),        # texto exato, sem float no meio
                "familia": familia_canonica(str(a)),
                "composicao": composicao_canonica(str(b)),
                "gsm": gramatura(str(b)),
                "dims": dimensoes_cm(str(c)),
                "construcao": (_sem_acento(str(b).lower()).strip()
                               if "matelassado" in str(b).lower() else None),
            })
        abas[ws.title] = linhas
    return abas


# ---------------------------------------------------------------------------
# Catálogo Daune, com os campos que se comparam
# ---------------------------------------------------------------------------
def _composicao_do_sku(p: Produto) -> str:
    """A composição só existe na string (B-16). Ela é o que sobra da especificação depois de
    tirar a medida e a gramatura — e é comparada por igualdade exata, normalizada."""
    espec = p.especificacao or ""
    partes = [x.strip() for x in espec.split("·")]
    partes = [x for x in partes if not re.match(r"^\d+x\d+$", x)]
    partes = [x for x in partes if not re.match(r"^\d+\s*g$", x)]
    return composicao_canonica(" ".join(partes))


def catalogo_daune(session: Session):
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
    skus = session.exec(select(Produto).where(Produto.fornecedor_id == forn.id,
                                              Produto.ativo == True)).all()  # noqa: E712
    saida = []
    for p in skus:
        ref = cs.referencia_vigente(session, p.id)
        saida.append({
            "produto": p, "familia": p.familia, "gsm": int(p.gsm) if p.gsm else None,
            "dims": ((int(p.largura_cm), int(p.comprimento_cm))
                     if p.largura_cm and p.comprimento_cm else None),
            "composicao": _composicao_do_sku(p),
            "ref": ref,
            "direta": bool(ref and ref.metodo_custo == "DAUNE_DIRECT"
                           and ref.status_custo == StatusCusto.confirmado.value),
        })
    return saida


# ---------------------------------------------------------------------------
# Reconciliação
# ---------------------------------------------------------------------------
def classificar(linha: dict, catalogo: list) -> dict:
    """Uma linha da fonte contra o catálogo inteiro. Devolve a classificação e o porquê."""
    r = dict(linha)
    r.pop("preco_raw", None)

    if linha["familia"] is None or linha["dims"] is None:
        return {**r, "classe": "AMBIGUOUS", "sku": None,
                "motivo": "família ou dimensão não reconhecida na fonte"}

    mesma_familia = [s for s in catalogo if s["familia"] == linha["familia"]]
    mesma_dim = [s for s in mesma_familia if s["dims"] == linha["dims"]]

    # Protetor de colchão: a fonte traz CONSTRUÇÃO (matelassado com alça / com slip) que o
    # catálogo não tem — ele guarda "Manta 120 grs impermeavel". É outra grandeza estrutural.
    if linha["familia"] == "Mattress Protector":
        return {**r, "classe": "CONFLICT", "sku": None,
                "motivo": ("a fonte descreve construção 'matelassado com alça/slip' e o "
                           "catálogo 'Manta 120 grs impermeavel'; e as medidas não coincidem "
                           "(catálogo 100/140/160/200×200, fonte 100/160/180/193×203/200). "
                           "Coincidência de preço não é match de produto")}

    # Pillow Top: os preços são os mesmos do catálogo, mas a fonte declara outras medidas
    # (1,03×2,03 onde o catálogo tem 100×200). Não se funde 193×203 com 200×200.
    if linha["familia"] == "Mattress Topper":
        return {**r, "classe": "CONFLICT", "sku": None,
                "motivo": ("a fonte declara medidas 1,03/1,63/1,83/1,93/2,03 × 2,03 e o "
                           "catálogo tem 100/140/180/200 × 200 com os mesmos preços. "
                           "Medida é atributo material: não se mapeia por preço")}

    candidatos = [s for s in mesma_dim
                  if s["composicao"] == linha["composicao"]
                  and (s["gsm"] == linha["gsm"] or (s["gsm"] is None and linha["gsm"] is None))]

    if not candidatos:
        return {**r, "classe": "NO_MATCH", "sku": None,
                "motivo": "nenhum SKU com esta família, composição, gramatura e medida"}
    if len(candidatos) > 1:
        return {**r, "classe": "AMBIGUOUS", "sku": None,
                "motivo": f"{len(candidatos)} SKUs com os mesmos atributos: "
                          + ", ".join(str(c["produto"].id) for c in candidatos)}

    alvo = candidatos[0]
    p, ref = alvo["produto"], alvo["ref"]
    base = {**r, "sku": p.id, "sku_key": p.sku_key,
            "status_anterior": ref.status_custo if ref else "A_COTAR (sem referência)",
            "bruto_anterior": ref.valor_bruto if ref else None,
            "cnet_anterior": ref.cnet_brl if ref else None,
            "fonte_anterior": ref.documento if ref else None}
    if alvo["direta"]:
        if D(ref.valor_bruto) == D(linha["preco"]):
            return {**base, "classe": "EXACT_SAME",
                    "motivo": f"referência direta vigente com o mesmo bruto ({ref.documento})"}
        return {**base, "classe": "PRICE_CHANGED",
                "motivo": f"bruto vigente {ref.valor_bruto} → {linha['preco']}"}
    return {**base, "classe": "EXACT_NEW",
            "motivo": "SKU sem referência direta: a fonte fecha o A_COTAR"}


def conflitos_estruturais(catalogo: list, linhas_novas: list) -> list:
    """Onde a fonte contradiz um rótulo que o catálogo já carrega.

    O caso concreto: o catálogo tem edredons de poliéster **280 g** (SKUs 340–348), rotulados
    a partir de informação verbal/imagem de 03/09/2026 (AUDIT §2.2.1). A fonte do fornecedor
    traz, nas mesmas medidas e com os mesmos brutos, a linha **180GSM**. Mesmo preço, dois
    rótulos de gramatura. Não se escolhe aqui — registra-se.
    """
    achados = []
    novas_por_chave = {(l["familia"], l["composicao"], l["dims"]): l for l in linhas_novas}
    for s in catalogo:
        p = s["produto"]
        if s["gsm"] is None or s["ref"] is None or not s["direta"]:
            continue
        for l in linhas_novas:
            if (l["familia"] == s["familia"] and l["composicao"] == s["composicao"]
                    and l["dims"] == s["dims"] and l["gsm"] != s["gsm"]
                    and D(l["preco"]) == D(s["ref"].valor_bruto)):
                achados.append({
                    "sku": p.id, "sku_key": p.sku_key, "gsm_catalogo": s["gsm"],
                    "gsm_fonte": l["gsm"], "bruto": l["preco"],
                    "fonte_catalogo": s["ref"].documento, "linha_fonte": l["linha"],
                    "motivo": (f"o catálogo rotula este SKU como {s['gsm']} g e a fonte do "
                               f"fornecedor traz o mesmo bruto R$ {l['preco']} na mesma medida "
                               f"como {l['gsm']}GSM. Mesmo preço, gramaturas diferentes: "
                               "um dos rótulos está errado, e decidir qual é decisão humana"),
                })
    return achados


def reconciliar(caminho: str, aplicar: bool = False) -> dict:
    abas = ler_fonte(caminho)
    novas = abas.get(ABA_NOVA, [])
    with Session(engine) as s:
        catalogo = catalogo_daune(s)
        linhas = [classificar(l, catalogo) for l in novas]
        conflitos = conflitos_estruturais(catalogo, novas)

        por_classe = {}
        for l in linhas:
            por_classe[l["classe"]] = por_classe.get(l["classe"], 0) + 1

        aplicadas = []
        if aplicar:
            for l in linhas:
                if l["classe"] not in APLICAVEIS:
                    continue
                produto = s.get(Produto, l["sku"])
                ref = cs.registrar_daune(
                    s, produto, l["preco"], fonte=FONTE, documento=NOME_CANONICO,
                    data_ref=DATA_DA_FONTE, status=StatusCusto.confirmado.value,
                    origem_registro="atualizacao-daune-projeto-anastacio",
                    notas=f"{NOTAS} Linha {l['linha']} da aba '{ABA_NOVA}'.")
                produto.status_custo = StatusCusto.confirmado.value
                produto.precisa_revisao = False
                produto.revisao_motivo = None
                s.add(produto)
                aplicadas.append({"sku": produto.id, "sku_key": produto.sku_key,
                                  "versao": ref.versao, "bruto": ref.valor_bruto,
                                  "cnet": ref.cnet_brl, "classe": l["classe"]})
            s.commit()
            os.makedirs(os.path.dirname(DESTINO_REFERENCIA), exist_ok=True)
            if os.path.abspath(caminho) != os.path.abspath(DESTINO_REFERENCIA):
                shutil.copy2(caminho, DESTINO_REFERENCIA)

    return {
        "gerado_em": date.today().isoformat(), "fonte": caminho, "abas": list(abas),
        "linhas_na_aba_nova": len(novas), "linhas_na_aba_antiga": len(abas.get(ABA_ANTIGA, [])),
        "por_classe": por_classe, "aplicadas": aplicadas, "conflitos_estruturais": conflitos,
        "linhas": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in l.items()}
                   for l in linhas],
    }


def _tabela(r: dict):
    print("=" * 118)
    print(f"RECONCILIAÇÃO — {NOME_CANONICO} · aba '{ABA_NOVA}' · {r['linhas_na_aba_nova']} linhas")
    print("=" * 118)
    print(f"{'lin':>3} {'classe':<14} {'sku':>4}  {'família':<18} {'composição':<26} "
          f"{'gsm':>4} {'medida':>8} {'bruto':>10}  motivo")
    for l in r["linhas"]:
        dims = f"{l['dims'][0]}x{l['dims'][1]}" if l["dims"] else "?"
        print(f"{l['linha']:>3} {l['classe']:<14} {str(l['sku'] or '—'):>4}  "
              f"{(l['familia'] or '?')[:18]:<18} {l['composicao'][:26]:<26} "
              f"{str(l['gsm'] or '—'):>4} {dims:>8} {l['preco']:>10}  {l['motivo'][:60]}")
    print()
    print("POR CLASSE:", json.dumps(r["por_classe"], ensure_ascii=False))
    if r["conflitos_estruturais"]:
        print()
        print(f"CONFLITOS ESTRUTURAIS NO CATÁLOGO ({len(r['conflitos_estruturais'])}):")
        for c in r["conflitos_estruturais"]:
            print(f"  SKU {c['sku']} · catálogo {c['gsm_catalogo']} g × fonte {c['gsm_fonte']}GSM "
                  f"· R$ {c['bruto']} · {c['sku_key'][:60]}")
    if r["aplicadas"]:
        print()
        print(f"APLICADAS ({len(r['aplicadas'])}):")
        for a in r["aplicadas"]:
            print(f"  SKU {a['sku']} v{a['versao']} · bruto {a['bruto']} → CNET {a['cnet']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("fonte", nargs="?", default=DESTINO_REFERENCIA)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--saida", default=SAIDA)
    a = p.parse_args()

    r = reconciliar(a.fonte, aplicar=a.aplicar)
    _tabela(r)
    os.makedirs(os.path.dirname(a.saida), exist_ok=True)
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nrelatório: {a.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

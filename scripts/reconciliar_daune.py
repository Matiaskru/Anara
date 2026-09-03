#!/usr/bin/env python3
"""Reconciliação Daune SKU a SKU — o relatório que vem ANTES de qualquer migração.

Por que existe: o custo dos SKUs Daune hoje veio de um documento
(`tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx`) **diferente** do que o plano manda
adotar (`Linha Hotelaria - Daune - 12.08.26.xlsx`). Migrar isso não é aplicar um fator sobre o
custo persistido — é trocar a fonte e casar SKU a SKU entre duas planilhas de origens distintas.

Duas regras que o script respeita à risca:

* **nunca `custo_atual × 0,7986`.** A fórmula Daune parte do preço BRUTO da fonte
  correspondente. Aplicar o fator sobre um custo já persistido produz um número sem
  significado, porque esse custo pode ter vindo de outro documento;
* **casamento por campos estruturados**, nunca por nome. Fornecedor, família, composição,
  gramatura e dimensão. Coincidir a medida não é casar.

O script **não escreve nada** por padrão: gera o relatório. A migração só acontece com
`--aplicar`, e só sobre os matches classificados como seguros.

Uso:
    python3 scripts/reconciliar_daune.py                 # só o relatório
    python3 scripts/reconciliar_daune.py --aplicar       # migra os matches seguros
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.custo_service import (  # noqa: E402
    cnet_nacional, referencia_vigente, registrar_daune,
)
from app.db import engine  # noqa: E402
from app.models import Fornecedor, Produto, StatusCusto  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANILHA = os.path.join(RAIZ, "referencia", "Linha Hotelaria - Daune - 12.08.26.xlsx")
SAIDA = os.path.join(RAIZ, "relatorios", "reconciliacao_daune.json")

ABA_NOVA = "Preços Daune 12.08.26"
ABA_ANTIGA = "Preços Daune 27.07.26"


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn").lower()


def extrair_dimensao(texto: str):
    """(largura, comprimento) a partir de '190x260', '190 x 260', '190X260 cm'."""
    m = re.search(r"(\d{2,3})\s*[xX×]\s*(\d{2,3})", texto or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def extrair_gramatura(texto: str):
    """Gramatura em g. Só aceita o padrão explícito — não adivinha por faixa de preço."""
    m = re.search(r"(\d{2,4})\s*(?:g|gr|gramas?)\b", _sem_acento(texto))
    return int(m.group(1)) if m else None


def extrair_composicao(texto: str):
    t = _sem_acento(texto)
    if "poliester" in t or "polyester" in t:
        return "POLIESTER"
    if "pluma" in t or "penas" in t or "ganso" in t:
        return "PLUMA"
    if "algodao" in t or "cotton" in t:
        return "ALGODAO"
    return None


def _preco(valor):
    """Preço da célula. As duas abas não usam o mesmo tipo: a de 27.07 traz número, a de 12.08
    traz texto ("R$\xa0 427,50 "). Ler só números perdia a aba inteira."""
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 1 else None
    if not isinstance(valor, str):
        return None
    limpo = (valor.replace("\xa0", " ").replace("R$", "").strip()
             .replace(".", "").replace(",", "."))
    try:
        n = float(limpo)
    except ValueError:
        return None
    return n if n > 1 else None


def ler_planilha(caminho: str = PLANILHA) -> list:
    """Itens das duas abas, com os campos estruturados que o casamento exige.

    O layout é o mesmo nas duas: ITEM · ESPECIFICAÇÃO · DIMENSÃO · PREÇO. A descrição técnica
    é a coluna ESPECIFICAÇÃO — a coluna ITEM é o nome da família, e entra só como contexto.
    """
    wb = openpyxl.load_workbook(caminho, data_only=True)
    itens = []
    for aba, rotulo in ((ABA_NOVA, "12.08.26"), (ABA_ANTIGA, "27.07.26")):
        if aba not in wb.sheetnames:
            continue
        for linha in wb[aba].iter_rows(values_only=True):
            celulas = list(linha) + [None] * 4
            item, espec, dimensao, preco_bruto = celulas[0], celulas[1], celulas[2], celulas[3]
            gross = _preco(preco_bruto)
            if gross is None or not isinstance(espec, str):
                continue
            texto = " ".join(str(x) for x in (item, espec, dimensao) if isinstance(x, str))
            larg, comp = extrair_dimensao(str(dimensao or "") or texto)
            itens.append({
                "aba": rotulo, "descricao": texto.strip()[:120],
                "gross": gross, "largura": larg, "comprimento": comp,
                "gramatura": extrair_gramatura(texto),
                "composicao": extrair_composicao(texto),
            })
    return itens


# Palavras que descrevem a FAMÍLIA, não o produto — saem da assinatura técnica.
RUIDO = {"daune", "edredom", "edredons", "insert", "inserts", "travesseiro", "travesseiros",
         "protetor", "protetores", "capa", "capas", "manta", "mantas", "de", "do", "da", "e",
         "com", "para", "cm", "tamanho", "linha", "hotelaria", "solicitados", "tamanhos"}

# Famílias em que a GRAMATURA é discriminante: a mesma medida existe em 180 g, 250 g e 280 g,
# e confundir uma com a outra troca o preço do produto. Em travesseiro a gramatura não existe;
# quem discrimina é a composição.
FAMILIAS_COM_GRAMATURA = {"duvet insert", "edredom"}


def assinatura_tecnica(texto: str) -> frozenset:
    """Conjunto normalizado de termos técnicos: composição, percentuais, construção.

    Não é casamento por nome: a comparação exige **igualdade exata** deste conjunto, e o ruído
    de família sai antes. Onde a assinatura não distingue (dois itens da fonte com a mesma
    assinatura e a mesma medida), o SKU fica sem match em vez de receber um dos dois.
    """
    t = _sem_acento(texto)
    t = re.sub(r"\d{2,3}\s*[x×]\s*\d{2,3}", " ", t)        # tira a dimensão
    t = re.sub(r"[^a-z0-9%]+", " ", t)
    fichas = {f for f in t.split() if f and f not in RUIDO and not f.isdigit()}
    percentuais = set(re.findall(r"\d{1,3}%", t))
    return frozenset(fichas | percentuais)


def casar(produto: Produto, itens: list) -> tuple:
    """Match técnico por campos estruturados. Devolve `(item, motivo)`.

    Os discriminantes dependem da família, e é isso que torna o casamento correto em vez de
    apenas restritivo:

    * **sempre** dimensão — sem ela não há match;
    * **gramatura** onde ela discrimina (edredons: 180 g, 250 g e 280 g convivem na mesma
      medida). Sem gramatura estruturada nesses casos, não casa;
    * **assinatura técnica** — composição e construção normalizadas, com igualdade exata.

    Medida sozinha nunca casa. Assinatura ambígua nunca casa.
    """
    nome = f"{produto.nome} {produto.especificacao or ''}"
    p_larg = int(produto.largura_cm) if produto.largura_cm else None
    p_comp = int(produto.comprimento_cm) if produto.comprimento_cm else None
    if not (p_larg and p_comp):
        p_larg, p_comp = extrair_dimensao(nome)
    familia = (produto.familia or "").strip().lower()
    p_gram = produto.gsm or extrair_gramatura(nome)
    p_assin = assinatura_tecnica(nome)

    if not (p_larg and p_comp):
        return None, "SKU sem dimensão — casamento por nome é proibido"

    mesma_medida = [i for i in itens if i["largura"] == p_larg and i["comprimento"] == p_comp]
    if not mesma_medida:
        return None, f"nenhum item da fonte tem a medida {p_larg}x{p_comp}"

    if familia in FAMILIAS_COM_GRAMATURA:
        if p_gram is None:
            return None, (f"{len(mesma_medida)} item(ns) em {p_larg}x{p_comp}, mas a gramatura "
                          "do SKU não está estruturada e nesta família ela discrimina o produto "
                          "(ver B-16). Medida sozinha não casa")
        por_gramatura = [i for i in mesma_medida if i["gramatura"] == p_gram]
        if not por_gramatura:
            achadas = sorted({i["gramatura"] for i in mesma_medida if i["gramatura"]})
            return None, (f"medida {p_larg}x{p_comp} existe na fonte em gramatura "
                          f"{achadas or 'não declarada'}, e o SKU é {p_gram} g")
        mesma_medida = por_gramatura

    exatos = [i for i in mesma_medida if assinatura_tecnica(i["descricao"]) == p_assin]
    if not exatos:
        return None, (f"medida {p_larg}x{p_comp} existe na fonte, mas nenhuma assinatura técnica "
                      "(composição/construção) coincide exatamente")

    novos = [i for i in exatos if i["aba"] == "12.08.26"]
    candidatos = novos or exatos
    if len({i["gross"] for i in candidatos}) > 1:
        return None, (f"{len(candidatos)} itens da fonte casam com este SKU e têm preços "
                      "diferentes — ambiguidade não se resolve por escolha automática")
    escolhido = candidatos[0]
    criterio = "medida + assinatura técnica"
    if familia in FAMILIAS_COM_GRAMATURA:
        criterio = "medida + gramatura + assinatura técnica"
    return escolhido, f"{criterio} (fonte {escolhido['aba']})"


def reconciliar(aplicar: bool = False) -> dict:
    itens = ler_planilha()
    linhas = []
    with Session(engine) as s:
        daune = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
        produtos = s.exec(select(Produto).where(Produto.fornecedor_id == daune.id)
                          .order_by(Produto.sku_key)).all()
        for p in produtos:
            item, motivo = casar(p, itens)
            registro = {
                "produto_id": p.id, "sku": p.sku_key, "nome": p.nome, "familia": p.familia,
                "construcao": p.construcao, "composicao": extrair_composicao(p.nome),
                "gramatura": p.gsm or extrair_gramatura(f"{p.nome} {p.especificacao or ''}"),
                "medida": (f"{p.largura_cm:g}x{p.comprimento_cm:g}"
                           if p.largura_cm and p.comprimento_cm else None),
                "custo_atual": p.custo_unitario, "metodo_atual": p.cost_method,
                "fonte_atual": p.custo_ref_documento,
                "status_legado": p.custo_confianca,
                "match": motivo,
            }
            if item is None:
                # Um SKU que já tem versão vigente com fonte não é rebaixado por não casar com
                # esta planilha: ele tem procedência própria. Só quem nunca teve referência
                # versionada é que vira A_COTAR ou REVALIDAR.
                vigente = referencia_vigente(s, p.id)
                if vigente is not None:
                    proposto = vigente.status_custo
                    justificativa = (
                        f"Já tem referência versionada (v{vigente.versao}, {proposto}) com fonte "
                        f"própria: {(vigente.origem_registro or '')[:70]}. Não casa com esta "
                        f"planilha, e não é rebaixado por isso. {motivo}")
                else:
                    proposto = (StatusCusto.a_cotar.value if not p.custo_unitario
                                else StatusCusto.revalidar.value)
                    justificativa = ("Sem correspondência técnica segura na fonte. "
                                     + ("Sem custo atual: A_COTAR. " if not p.custo_unitario
                                        else "Custo atual sem procedência versionada passa a "
                                             "exigir reconfirmação: REVALIDAR. ")
                                     + motivo)
                registro.update(
                    nova_fonte=None, gross=None, cnet_novo=None, diferenca=None,
                    diferenca_pct=None, formula=None,
                    status_proposto=proposto, justificativa=justificativa)
            else:
                conta = cnet_nacional(item["gross"])
                dif = (conta.cnet - p.custo_unitario) if p.custo_unitario else None
                registro.update(
                    nova_fonte=f"Linha Hotelaria - Daune - {item['aba']}.xlsx · "
                               f"{item['descricao'][:60]}",
                    gross=item["gross"], cnet_novo=conta.cnet,
                    icms_credito=conta.icms_credito, base_pis_cofins=conta.base_pis_cofins,
                    pis_cofins_credito=conta.pis_cofins_credito,
                    formula=conta.como_dict()["formula"],
                    diferenca=dif,
                    diferenca_pct=(conta.cnet / p.custo_unitario - 1) if p.custo_unitario else None,
                    status_proposto=StatusCusto.confirmado.value,
                    justificativa=("Match técnico exato com a fonte mais nova; CNET recalculado "
                                   "a partir do preço BRUTO, não do custo persistido."))
            linhas.append(registro)

        migrados = []
        if aplicar:
            for r in linhas:
                if r["status_proposto"] != StatusCusto.confirmado.value or not r.get("gross"):
                    continue
                produto = s.get(Produto, r["produto_id"])
                ref = registrar_daune(
                    s, produto, r["gross"],
                    fonte=r["nova_fonte"],
                    documento="Linha Hotelaria - Daune - 12.08.26.xlsx",
                    data_ref=date(2026, 8, 12),
                    notas="Migrado pela reconciliação da Sessão 2")
                produto.status_custo = StatusCusto.confirmado.value
                s.add(produto)
                migrados.append({"sku": r["sku"], "versao": ref.versao, "cnet": ref.cnet_brl})
            s.commit()

    por_status = {}
    for r in linhas:
        por_status[r["status_proposto"]] = por_status.get(r["status_proposto"], 0) + 1
    return {"gerado_em": date.today().isoformat(), "itens_na_fonte": len(itens),
            "skus_daune": len(linhas), "por_status_proposto": por_status,
            "migrados": migrados if aplicar else [], "linhas": linhas}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true",
                   help="migra os matches seguros; sem isto, só gera o relatório")
    p.add_argument("--saida", default=SAIDA)
    a = p.parse_args()

    r = reconciliar(aplicar=a.aplicar)
    with open(a.saida, "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)

    print(f"relatório em {a.saida}")
    print(f"  itens lidos na fonte: {r['itens_na_fonte']}")
    print(f"  SKUs Daune no catálogo: {r['skus_daune']}")
    print("  status proposto:")
    for st, n in sorted(r["por_status_proposto"].items(), key=lambda x: -x[1]):
        print(f"    {st:18s} {n:3d}")
    if a.aplicar:
        print(f"  MIGRADOS: {len(r['migrados'])}")
    else:
        print("  (nada foi escrito — use --aplicar depois de revisar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Casamento de produto do catálogo com item de documento de preço.

Nunca por nome: compara campos estruturados (família, dimensões, fios, GSM, composição,
liso/listrado, tamanho, construção). O resultado diz **por que** casou ou por que não casou —
e, em caso de dúvida, devolve REVIEW_REQUIRED em vez de atualizar sozinho.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

EXATO = "EXATO"
COM_DIVERGENCIA = "COM_DIVERGENCIA"
SEM_MATCH = "SEM_MATCH"


@dataclass
class Match:
    status: str
    item: Optional[dict] = None
    divergencias: List[str] = field(default_factory=list)

    @property
    def casou(self) -> bool:
        return self.status in (EXATO, COM_DIVERGENCIA)


def _mesmo_numero(a, b, tolerancia=0.001) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tolerancia


def _tamanho_do_nome(nome: str) -> Optional[str]:
    texto = f" {(nome or '').upper()} "
    for tamanho in ("XXL", "XL", "L", "M", "S"):
        if f" {tamanho} " in texto or texto.strip().endswith(f" {tamanho}"):
            return tamanho
    return None


# Vocabulário que **muda o preço** dentro da mesma família/medida/gramatura. A checagem é
# simétrica: o termo tem de estar nos dois lados ou em nenhum. "terry", cor e afins ficam de
# fora de propósito — são descritivos e não distinguem preço.
VOCABULARIO_DISCRIMINANTE = [
    "waffle", "velour", "kimono", "shawl", "oxford", "housewife", "open bag", "snap",
    "zipper", "ziper", "bordado", "embroider", "flange", "elastico", "elastic",
    "coral fleece", "microfibra", "chenille", "pu",
]


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _tem_termo(texto: str, termo: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])", texto) is not None


def _lados_declarados(texto: str) -> Optional[int]:
    """Quantos lados de oxford o texto declara (2/3/4). None = não declara."""
    m = re.search(r"(\d)\s*(?:lados?|sides?)", texto)
    return int(m.group(1)) if m else None


def _construcao_bate(texto_produto: str, construcao_item: Optional[str]) -> List[str]:
    """Devolve a lista de diferenças discriminantes. Vazia = pode casar."""
    produto = _normalizar(texto_produto)
    item = _normalizar(construcao_item or "")
    problemas = []

    for termo in VOCABULARIO_DISCRIMINANTE:
        no_produto = _tem_termo(produto, termo)
        no_item = _tem_termo(item, termo)
        if no_produto != no_item:
            onde = "só no catálogo" if no_produto else "só no documento"
            problemas.append(f"'{termo}' aparece {onde}")

    lados_item = _lados_declarados(item)
    if lados_item is not None and _lados_declarados(produto) != lados_item:
        problemas.append(f"documento especifica oxford de {lados_item} lados; o catálogo não diz "
                         "quantos lados")
    return problemas


def casar(produto, item: dict, texto_produto: str = "") -> Match:
    """Compara um produto do catálogo com um item de documento."""
    divergencias = []

    if (produto.familia or "").strip().lower() != (item["familia"] or "").strip().lower():
        return Match(SEM_MATCH)

    # dimensões: quando o item tem medida, o produto precisa bater
    if item["largura_cm"] is not None:
        if not (_mesmo_numero(produto.largura_cm, item["largura_cm"])
                and _mesmo_numero(produto.comprimento_cm, item["comprimento_cm"])):
            return Match(SEM_MATCH)

    # fios / GSM: chave técnica principal
    if item["thread_count"] is not None:
        if produto.thread_count is None or int(produto.thread_count) != int(item["thread_count"]):
            return Match(SEM_MATCH)
    if item["gsm"] is not None:
        if produto.gsm is None or int(produto.gsm) != int(item["gsm"]):
            return Match(SEM_MATCH)

    # tamanho (roupão, chinelo)
    if item["tamanho"]:
        tamanho_produto = _tamanho_do_nome(texto_produto or produto.nome)
        if tamanho_produto != item["tamanho"]:
            return Match(SEM_MATCH)

    # construção e acabamento: qualquer diferença discriminante impede o casamento.
    # É aqui que uma fronha bordada deixa de casar com uma fronha lisa, e que uma fronha
    # "com aba" não vira uma de oxford de 3 lados só porque a medida bate.
    texto_completo = f"{texto_produto or produto.nome} {produto.acabamento or ''}"
    problemas = _construcao_bate(texto_completo, item["construcao"])
    if problemas:
        return Match(SEM_MATCH, item, problemas)

    # daqui pra baixo é divergência, não impedimento
    if item["cotton_pct"] is not None and produto.cotton_pct is not None:
        if not _mesmo_numero(produto.cotton_pct, item["cotton_pct"], 0.005):
            divergencias.append(
                f"composição: catálogo {produto.cotton_pct:.0%} algodão × documento "
                f"{item['cotton_pct']:.0%}")
    if (produto.plain_or_stripe or "plain") != (item["plain_or_stripe"] or "plain"):
        divergencias.append(
            f"liso/listrado: catálogo {produto.plain_or_stripe} × documento {item['plain_or_stripe']}")

    return Match(EXATO if not divergencias else COM_DIVERGENCIA, item, divergencias)


def melhor_match(produto, documentos_itens: List[dict], texto_produto: str = "") -> Match:
    """Percorre os itens já ordenados por prioridade de documento e devolve o primeiro que casa."""
    melhor = Match(SEM_MATCH)
    for item in documentos_itens:
        m = casar(produto, item, texto_produto)
        if m.status == EXATO:
            return m
        if m.status == COM_DIVERGENCIA and not melhor.casou:
            melhor = m
    return melhor

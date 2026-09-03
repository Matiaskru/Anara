"""Busca de produto — entende português e inglês, vários termos e acentos.

O catálogo nasceu bilíngue: cada cotação da KTC chegou num idioma, e metade dos SKUs ficou em
inglês. Mesmo com o nome de exibição já normalizado para português, o vendedor pode digitar
`sheet`, `duvet`, `towel` — e o nome original continua guardado. Então a busca:

* ignora acento e maiúscula (`lencol` acha `Lençol`);
* trata cada palavra digitada como um filtro que precisa bater em algum lugar do produto,
  em qualquer ordem: `lencol 250 listrado` funciona, `listrado lencol 250` também;
* traduz os dois sentidos por um dicionário de sinônimos: `sheet` acha lençol, `fronha` acha
  pillow case, `chão` acha bath mat;
* ordena pelo que bate melhor — quem casa no começo de uma palavra vem antes de quem casa no
  meio, e produto com custo cadastrado vem antes do que ainda falta cotar.

Módulo puro: recebe a lista de produtos e devolve a lista ordenada.
"""
import re
import unicodedata
from typing import List, Optional, Sequence

# Cada grupo é um conjunto de termos equivalentes — vale nos dois sentidos.
# Grupos de termos equivalentes, nos dois sentidos. Expressões de duas ou três palavras vêm
# primeiro de propósito: quem digita "bath towel" quer toalha de banho, não toalha de piso —
# e é a expressão inteira, não a palavra "bath" solta, que carrega essa informação.
SINONIMOS = [
    # famílias
    {"lencol de cima", "top sheet", "flat sheet", "lencol plano"},
    {"lencol de baixo", "bottom sheet", "fitted sheet", "lencol com elastico"},
    {"lencol", "sheet", "sheets"},
    {"fronha", "pillow case", "pillowcase"},
    {"fronha decorativa", "sham", "euro sham"},
    # "duvet" sozinho fica fora do grupo de propósito: é ambíguo em português (capa ou
    # edredom), então quem digita só "duvet" vê os dois, e quem digita "capa duvet" vê só capa
    {"capa duvet", "duvet cover", "capa de duvet"},
    {"edredom", "duvet insert", "insert"},
    {"toalha de banho", "bath towel", "banhao"},
    {"toalha de rosto", "hand towel", "face towel"},
    {"toalha de piso", "bath mat", "chao", "tapete"},
    {"toalha de piscina", "pool towel", "beach towel", "toalha de praia"},
    {"toalha de lavabo", "wash cloth", "washcloth"},
    {"toalha", "towel"},
    {"roupao", "bathrobe", "robe"},
    {"chinelo", "pantufa", "slipper", "slippers"},
    {"peseira", "bed runner"},
    {"travesseiro", "pillow"},
    {"protetor de colchao", "mattress protector"},
    {"topper de colchao", "mattress topper", "pillow top", "pillowtop"},
    {"protetor de fronha", "pillow protector", "capa protetora"},
    {"capa de almofada", "cushion cover", "almofada", "cushion"},
    # atributos
    {"algodao", "cotton"},
    {"poliester", "polyester"},
    {"listrado", "stripe", "listrada", "riscado"},
    {"liso", "plain"},
    {"acetinado", "sateen", "cetim"},
    {"percal", "percale"},
    {"hospitalar", "healthcare", "hospital"},
    {"hotel", "hotelaria"},
    {"bordado", "embroidery", "embroidered"},
    {"gola xale", "shawl"},
    {"kimono", "quimono"},
    {"pena", "penas", "feather"},
    {"pluma", "plumas", "down"},
    {"fibra", "fibras", "fiber"},
    {"microfibra", "microfiber"},
    {"banho", "bath"},
    {"piscina", "pool", "spa"},
    {"ktc", "kazareen", "egito"},
]


def normalizar(texto: Optional[str]) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


_INDICE_SINONIMOS = {}
for _grupo in SINONIMOS:
    _normalizados = {normalizar(t) for t in _grupo}
    for _termo in _normalizados:
        _INDICE_SINONIMOS.setdefault(_termo, set()).update(_normalizados)


# expressões de duas ou três palavras que o dicionário conhece ("bath mat", "pillow case")
_EXPRESSOES = sorted((c for c in _INDICE_SINONIMOS if " " in c),
                     key=lambda c: -len(c.split()))


def expandir(termo: str) -> set:
    """Devolve o termo e tudo que significa a mesma coisa nos dois idiomas.

    A expansão é por chave inteira: `bath` puxa `banho`, mas não puxa `bath mat`. Se puxasse,
    procurar "bath towel" traria toalha de piso junto, porque `bath` é palavra de `bath mat`.
    """
    termo = normalizar(termo)
    equivalentes = set(_INDICE_SINONIMOS.get(termo, set()))
    equivalentes.add(termo)
    return {e for e in equivalentes if e}


def tokenizar(consulta: str) -> list:
    """Quebra a consulta em termos, juntando as expressões que o dicionário conhece.

    Palavra solta de uma letra é descartada: não filtra nada e faz "bath towel d" casar com
    qualquer nome que tenha "de".
    """
    palavras = [p for p in normalizar(consulta).split() if p]
    termos, i = [], 0
    while i < len(palavras):
        casou = False
        for expressao in _EXPRESSOES:
            partes = expressao.split()
            if palavras[i:i + len(partes)] == partes:
                termos.append(expressao)
                i += len(partes)
                casou = True
                break
        if not casou:
            palavra = palavras[i]
            if len(palavra) > 1 or palavra.isdigit():
                termos.append(palavra)
            i += 1
    return termos


def texto_do_produto(produto, fornecedor_nome: Optional[str] = None) -> str:
    """Tudo que vale procurar dentro de um produto, inclusive o nome antigo em inglês."""
    partes = [produto.nome, produto.nome_original, produto.especificacao, produto.categoria,
              produto.familia, produto.subcategoria, fornecedor_nome, produto.material_ref,
              produto.plain_or_stripe, produto.construcao, produto.acabamento, produto.cor,
              produto.sku_key]
    if produto.thread_count:
        partes.append(f"{int(produto.thread_count)} fios {int(produto.thread_count)}tc")
    if produto.gsm:
        partes.append(f"{int(produto.gsm)} gsm {int(produto.gsm)} g")
    if produto.largura_cm and produto.comprimento_cm:
        largura, comprimento = int(produto.largura_cm), int(produto.comprimento_cm)
        partes.append(f"{largura}x{comprimento} {largura} {comprimento}")
    return normalizar(" ".join(p for p in partes if p))


def _pontuar(indice: str, termos_expandidos: set) -> Optional[int]:
    """None = não bate. Senão, quanto melhor bate."""
    pontos = 0
    for equivalentes in termos_expandidos:
        melhor = 0
        for termo in equivalentes:
            if not termo:
                continue
            # casa no começo de uma palavra: "lenc" acha "lençol", mas "chão" não acha
            # "colchão". Termo curto exige palavra inteira, senão "mat" acharia "mattress".
            limite = r"\b" if len(termo) <= 3 else ""
            if re.search(rf"(?<![a-z0-9]){re.escape(termo)}{limite}", indice):
                melhor = max(melhor, 3 if f" {termo}" in f" {indice}" else 2)
        if melhor == 0:
            return None                          # todo termo digitado precisa bater
        pontos += melhor
    return pontos


def buscar(produtos: Sequence, consulta: str, fornecedores: Optional[dict] = None,
           limite: int = 40) -> List:
    """Filtra e ordena. Consulta vazia devolve o começo do catálogo em ordem alfabética."""
    fornecedores = fornecedores or {}
    if not (consulta or "").strip():
        return sorted(produtos, key=lambda p: (p.categoria or "", p.nome))[:limite]

    termos = tokenizar(consulta)
    if not termos:
        return sorted(produtos, key=lambda p: (p.categoria or "", p.nome))[:limite]
    expandidos = [expandir(t) for t in termos]

    achados = []
    for produto in produtos:
        indice = texto_do_produto(produto, (fornecedores.get(produto.fornecedor_id) or None))
        pontos = _pontuar(indice, expandidos)
        if pontos is None:
            continue
        # produto pronto para cotar com margem vem antes do que ainda falta cotar
        tem_custo = 1 if produto.custo_unitario else 0
        achados.append((-pontos, -tem_custo, produto.nome, produto))

    achados.sort(key=lambda x: (x[0], x[1], x[2]))
    return [a[3] for a in achados[:limite]]

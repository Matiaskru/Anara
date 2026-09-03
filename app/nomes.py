"""Nome de exibição do produto, montado a partir dos campos estruturados.

O catálogo cresceu por importação de cotações diferentes, e cada uma trouxe o nome no idioma
que a KTC usou naquele documento. O resultado eram três problemas juntos:

* metade dos nomes em português e metade em inglês, dentro da mesma família;
* 199 SKUs com nome repetido — oito "Lençol com elástico 100x200x30" diferentes, em que o que
  separava um do outro (fios, composição, listrado) só aparecia na especificação;
* duas taxonomias paralelas, categoria em português e família em inglês.

Aqui o nome é **gerado**, sempre em português e sempre com o que diferencia o SKU embutido:

    Lençol com elástico 100x200x30 · 250 fios · CVC 70/30 · listrado
    Toalha de banho 70x135 · 330 g/m² · 90/10
    Capa duvet 190x260 · 300 fios · 100% algodão · open bag

Esse nome também é o que vai para o PDF do cliente. O nome antigo continua guardado em
`nome_original` e a busca continua olhando para ele.
"""
import re
from typing import Optional

# categoria do catálogo → como a peça se chama numa proposta
ROTULO_POR_CATEGORIA = {
    "lençol plano": "Lençol plano",
    "lençol plano (hotel)": "Lençol plano hotel",
    "lençol hospitalar": "Lençol hospitalar",
    "lençol hospitalar (maca)": "Lençol de maca",
    "lençol com elástico": "Lençol com elástico",
    "capa duvet": "Capa duvet",
    "fronha com aba": "Fronha com aba",
    "fronha sem aba": "Fronha sem aba",
    "fronha (hotel)": "Fronha hotel",
    "fronha hospitalar": "Fronha hospitalar",
    "euro sham": "Fronha decorativa euro",
    "pillow sham": "Fronha decorativa",
    "protetor de fronha": "Protetor de fronha",
    "toalha banho": "Toalha de banho",
    "toalha rosto": "Toalha de rosto",
    "toalha piso": "Toalha de piso",
    "toalha piscina": "Toalha de piscina",
    "toalha lavabo": "Toalha de lavabo",
    "roupão": "Roupão",
    "chinelos": "Chinelo",
    "topper colchão": "Topper de colchão",
    "protetor colchão": "Protetor de colchão",
    "edredom / insert": "Edredom",
    "bed runner": "Peseira",
    "travesseiros": "Travesseiro",
    "capa de almofada": "Capa de almofada",
}

# variantes de modelo que valem a pena manter no nome (não estão em campo estruturado)
VARIANTES = [
    ("waffle velour", "waffle velour"), ("waffle pique", "waffle piquet"),
    ("waffle", "waffle"), ("velour", "velour"), ("french rib", "canelado"),
    ("terry", "atoalhado"), ("atoalhado", "atoalhado"),
    ("microfiber fleece", "microfibra fleece"),
    ("fleece", "fleece"), ("spa", "spa"), ("kimono", "kimono"), ("piquet", "piquet"),
    ("pique", "piquet"), ("coral", "coral"),
]

# acabamentos e construções que mudam o produto e precisam aparecer
EXTRAS = [
    ("listrado", "listrado"), ("stripe", "listrado"), ("open bag", "open bag"),
    ("oxford", "oxford"), ("bordado", "bordado"), ("shawl", "gola xale"),
    ("gola xale", "gola xale"), ("taupe", "taupe"), ("navy", "azul marinho"),
    ("navy blue", "azul marinho"), ("verde", "verde"), ("microfibra", "microfibra"),
    ("impermeável", "impermeável"), ("tricô", "tricô"), ("egípcio", "egípcio"),
    ("open bag style", "open bag"),
]

TAMANHOS = ("PP", "P", "M", "G", "GG", "S", "L", "XL", "XXL")


def _normalizar(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _rotulo_peca(produto) -> str:
    categoria = (produto.categoria or "").strip().lower()
    if categoria in ROTULO_POR_CATEGORIA:
        return ROTULO_POR_CATEGORIA[categoria]
    if produto.categoria:
        return produto.categoria.strip().capitalize()
    return (produto.familia or "Produto").strip()


def _medida(produto) -> Optional[str]:
    if produto.largura_cm and produto.comprimento_cm:
        largura, comprimento = int(produto.largura_cm), int(produto.comprimento_cm)
        base = f"{largura}x{comprimento}"
        # altura do colchão e aba da fronha vivem na descrição original
        texto = f"{produto.nome_original or produto.nome} {produto.especificacao or ''}"
        m = re.search(rf"{largura}\s*[xX]\s*{comprimento}\s*[xX]\s*(\d{{1,3}})", texto)
        if m:
            return f"{base}x{m.group(1)}"
        m = re.search(rf"{largura}\s*[xX]\s*{comprimento}\s*\+\s*(\d{{1,2}})", texto)
        if m:
            return f"{base}+{m.group(1)}"
        return base
    return None


def _tamanho(produto) -> Optional[str]:
    """Roupão e chinelo são por tamanho, não por medida — e o tamanho tanto pode estar no nome
    quanto na especificação."""
    texto = f"{produto.nome_original or produto.nome or ''} {produto.especificacao or ''}".upper()
    for tamanho in TAMANHOS:
        if re.search(rf"(?<![A-Z0-9]){tamanho}(?![A-Z0-9])", texto):
            return tamanho
    return None


def _variante(produto) -> Optional[str]:
    texto = _normalizar(f"{produto.nome_original or produto.nome} {produto.especificacao or ''}")
    achadas = []
    for chave, rotulo in VARIANTES:
        if _normalizar(chave) in texto and rotulo not in achadas:
            achadas.append(rotulo)
    # tira o que já está contido em outro rótulo ("waffle" dentro de "waffle piquet")
    achadas = [a for a in achadas
               if not any(a != outro and a in outro for outro in achadas)]
    return " ".join(achadas[:2]) if achadas else None


def _enchimento(produto) -> Optional[str]:
    """Para travesseiro, edredom e pillow top o que diferencia é o enchimento, que não cabe em
    fios nem em gramatura. Vem da descrição do fornecedor, limpa do tamanho."""
    if produto.thread_count or produto.gsm or produto.cotton_pct is not None:
        return None
    especificacao = produto.especificacao or ""
    partes = [t.strip() for t in especificacao.split("·") if t.strip()]
    # a primeira parte costuma ser a medida
    candidatas = [t for t in partes if not re.fullmatch(r"[\d,\.]+\s*[xX]\s*[\d,\.]+", t)]
    if not candidatas:
        return None
    # gramatura do enchimento e composição são duas partes distintas ("180 g · 100% plumas")
    texto = " · ".join(re.sub(r"\s+", " ", t).strip() for t in candidatas[:2])
    return texto[:56] if texto else None


def _variacao(produto) -> Optional[str]:
    """Mantém marcadores do tipo 'variação KTC 2' ou 'opção 3', que separam SKUs iguais."""
    texto = produto.nome_original or produto.nome or ""
    m = re.search(r"(?:varia[çc][aã]o|variante|op[çc][aã]o)\s*(?:KTC\s*)?(\d+)", texto, re.I)
    return f"variação {m.group(1)}" if m else None


def _tecnico(produto) -> Optional[str]:
    if produto.thread_count:
        return f"{int(produto.thread_count)} fios"
    if produto.gsm:
        return f"{int(produto.gsm)} g/m²"
    return None


def _composicao(produto) -> Optional[str]:
    algodao, poliester = produto.cotton_pct, produto.poliester_pct
    if algodao is None:
        return None
    if algodao >= 0.999:
        return "100% algodão"
    if algodao <= 0.001:
        return "100% poliéster"
    return f"{int(round(algodao * 100))}/{int(round((poliester or 1 - algodao) * 100))}"


def _extras(produto) -> list:
    texto = _normalizar(f"{produto.nome_original or produto.nome} {produto.especificacao or ''} "
                        f"{produto.acabamento or ''} {produto.construcao or ''} {produto.cor or ''}")
    achados = []
    for chave, rotulo in EXTRAS:
        if _normalizar(chave) in texto and rotulo not in achados:
            achados.append(rotulo)
    if produto.plain_or_stripe == "stripe" and "listrado" not in achados:
        achados.append("listrado")
    return achados[:3]


def nome_canonico(produto) -> str:
    """Monta o nome de exibição. Cai no nome original se não houver campo estruturado nenhum."""
    partes_principais = [_rotulo_peca(produto)]

    variante = _variante(produto)
    if variante:
        partes_principais.append(variante)

    medida = _medida(produto)
    if medida:
        partes_principais.append(medida)
    else:
        tamanho = _tamanho(produto)
        if tamanho:
            partes_principais.append(tamanho)

    detalhes = [d for d in (_tecnico(produto), _composicao(produto), _enchimento(produto)) if d]
    ja_dito = " ".join(_normalizar(d) for d in detalhes)
    detalhes += [e for e in _extras(produto) if _normalizar(e) not in ja_dito]
    variacao = _variacao(produto)
    if variacao:
        detalhes.append(variacao)

    nome = " ".join(partes_principais)
    if detalhes:
        nome += " · " + " · ".join(detalhes)

    # sem nada estruturado, o nome antigo é melhor do que um rótulo genérico
    if not medida and not detalhes and not variante and not _tamanho(produto):
        return produto.nome_original or produto.nome
    return nome

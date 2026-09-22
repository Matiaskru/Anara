"""Peso logístico recuperado de um SKU análogo da própria base da ANARA (22/09/2026).

## O problema

Roupão é **Direct Quote**: a KTC cota o EXW da peça, o motor industrial não tem fórmula para
ele (`calcular_exw` devolve "familia não calculável", e isso está certo). Só que nacionalizar
exige **peso** — é ele que rateia o frete internacional por unidade (US$/kg × kg). Sem peso, o
frete entraria como zero e o custo sairia subestimado; por isso o motor declara `peso` em
`premissas_faltantes` e o SKU vai a `REVIEW_REQUIRED`.

O efeito colateral: sete roupões com EXW **cotado, datado e documentado** ficaram
comercialmente inutilizáveis esperando alguém digitar um peso — enquanto a própria base da
ANARA já carregava o peso logístico daquele mesmo modelo, no SKU de catálogo equivalente.

## O que este módulo faz — e o que ele NÃO faz

Ele **encontra o peso que a ANARA já usava** para o mesmo modelo e o empresta ao SKU sem peso,
com procedência declarada. É recuperação de premissa operacional, não cálculo.

Ele **não** forma EXW, não deriva preço, não cria fórmula de peso por GSM, área ou composição,
e não converte entre construções (Terry/Waffle/Velour/Fleece). Peso aqui é entrada de
nacionalização — nada além disso.

## A regra do casamento (conservadora por desenho)

Um SKU sem peso recebe o peso de um SKU **da mesma família** apenas quando todos batem:

* **tamanho** igual (`L` com `L`; `Unisize` não é `L`, `M` não é `L`, `2XL` não é `XL`);
* **gramatura** igual — ou ausente nos dois (roupão de fleece e de jacquard não declaram GSM);
* **composição** compatível (algodão/poliéster dentro de 1 p.p.);
* e, quando há mais de um candidato, **todos precisam convergir** no mesmo peso (≤ 1%).
  Candidatos que discordam significam que o modelo importa e o casamento não está provado.

Não bateu? O SKU continua `REVIEW_REQUIRED` e o OWNER resolve na tela, com fonte. Extrapolar
de `L` para `M` ou de `XL` para `2XL` seria inventar premissa logística — exatamente o que o
`REVIEW_REQUIRED` existe para impedir.

O peso emprestado é sempre **ESTIMADO**, mesmo quando a origem é um peso `REAL KTC`: real é o
peso *daquela* peça medida, não o desta. Quem herda herda uma estimativa.
"""
from dataclasses import dataclass
from typing import List, Optional

#: Tolerância na convergência entre candidatos — 1% cobre a diferença de arredondamento entre
#: registros históricos (1,45 × 0,99) sem deixar passar modelos de peso realmente diferente.
TOLERANCIA_CONVERGENCIA = 0.01

#: Tolerância na composição: 1 ponto percentual. Cadastros antigos gravam 0,85 onde o novo
#: grava 0,8500000001; o que não pode passar é algodão virar poliéster.
TOLERANCIA_COMPOSICAO = 0.01


@dataclass
class PesoHerdado:
    """O peso encontrado e **de onde** ele veio — sem procedência não entra."""
    peso_kg: float
    sku_origem: str
    tipo_origem: Optional[str]          # peso_tipo do SKU de origem ("REAL KTC" | "ESTIMADO")
    fonte_origem: Optional[str]         # peso_fonte do SKU de origem
    candidatos: List[str]

    @property
    def fonte(self) -> str:
        """A frase que vai para a memória do preço, a tela e a trilha."""
        origem = f"peso {(self.tipo_origem or 'registrado').lower()} de «{self.sku_origem}»"
        detalhe = f" ({self.fonte_origem})" if self.fonte_origem else ""
        return (f"Peso logístico estimado por analogia: {origem}{detalhe} — mesma família, "
                f"tamanho, gramatura e composição. Confirmar com a KTC antes do pedido.")

    def como_dict(self) -> dict:
        return {"peso_kg": self.peso_kg, "tipo": "ESTIMADO", "fonte": self.fonte,
                "origem": "ANALOGIA_HISTORICA", "sku_origem": self.sku_origem,
                "tipo_origem": self.tipo_origem, "fonte_origem": self.fonte_origem,
                "candidatos": list(self.candidatos)}


def _tamanho(produto) -> Optional[str]:
    """O tamanho como dado estruturado, normalizado para comparação.

    Vem de `subcategoria` (é onde o importador da cotação KTC grava `L`, `XL`, `Unisize`) e,
    nos SKUs de catálogo antigos, do começo da especificação (`L · 420 GSM · …`). O **nome**
    nunca é consultado: "Bathrobe G" pode ser tamanho G, mas pode ser outra coisa, e casar
    peso por rótulo em português é o tipo de palpite que este módulo existe para não dar.
    """
    bruto = (getattr(produto, "subcategoria", None) or "").strip()
    if not bruto or bruto.lower() in ("roupão", "roupao"):
        espec = (getattr(produto, "especificacao", None) or "").split("·")[0].strip()
        bruto = espec
    bruto = bruto.upper().replace(" ", "")
    return bruto or None


def _composicao(produto) -> tuple:
    return (getattr(produto, "cotton_pct", None), getattr(produto, "poliester_pct", None))


def _compativel(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= TOLERANCIA_COMPOSICAO


def casa(produto, candidato) -> bool:
    """O candidato serve de origem para o peso deste produto?"""
    if candidato.id == getattr(produto, "id", None):
        return False
    if not candidato.peso_kg or candidato.peso_kg <= 0:
        return False
    if (candidato.familia or "").strip().lower() != (produto.familia or "").strip().lower():
        return False
    t_alvo, t_cand = _tamanho(produto), _tamanho(candidato)
    if not t_alvo or t_alvo != t_cand:
        return False
    gsm_alvo = float(produto.gsm) if produto.gsm else None
    gsm_cand = float(candidato.gsm) if candidato.gsm else None
    if (gsm_alvo is None) != (gsm_cand is None):
        return False
    if gsm_alvo is not None and abs(gsm_alvo - gsm_cand) > 0.5:
        return False
    ca, pa = _composicao(produto)
    cb, pb = _composicao(candidato)
    return _compativel(ca, cb) and _compativel(pa, pb)


def herdar(produto, candidatos: List) -> Optional[PesoHerdado]:
    """O peso logístico deste SKU, vindo da base da ANARA — ou `None` se não está provado.

    Exige convergência: dois candidatos que discordam do peso significam que a construção
    muda o peso e o casamento por família/tamanho/GSM/composição não é suficiente.
    """
    if getattr(produto, "peso_kg", None):
        return None                       # peso próprio manda — este módulo nem é consultado
    achados = [c for c in candidatos if casa(produto, c)]
    if not achados:
        return None
    pesos = [float(c.peso_kg) for c in achados]
    menor, maior = min(pesos), max(pesos)
    if menor <= 0 or (maior - menor) / menor > TOLERANCIA_CONVERGENCIA:
        return None                       # candidatos discordam: não se escolhe um por gosto
    # o de peso REAL primeiro; empate resolvido pelo sku_key, para a escolha ser reproduzível
    achados.sort(key=lambda c: (c.peso_tipo != "REAL KTC", c.sku_key or ""))
    melhor = achados[0]
    return PesoHerdado(peso_kg=float(melhor.peso_kg), sku_origem=melhor.sku_key or f"id={melhor.id}",
                       tipo_origem=melhor.peso_tipo, fonte_origem=melhor.peso_fonte,
                       candidatos=[c.sku_key or f"id={c.id}" for c in achados])

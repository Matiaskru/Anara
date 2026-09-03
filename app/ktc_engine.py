"""Motor industrial KTC — da especificação técnica ao EXW calculado, passo a passo.

Módulo **puro**: não conhece HTML, request, banco nem tela. Recebe medidas e parâmetros,
devolve um objeto com o EXW e o waterfall completo, etapa por etapa, para a memória do preço.

A cadeia é a que a KTC demonstrou no arquivo `KTC_Pricing_Master_Simple.xlsx`
(aba "KTC Actual Pricing Model", colunas E..L):

    hemming → shrinkage → consumo → waste → tecido → CMT → qualidade → margem KTC → EXW

Três pontos em que é fácil errar, e que aqui seguem a KTC ao pé da letra:

* **waste** entra como `consumo / (1 - waste)`, não como `consumo × (1 + waste)`;
* **quality allowance** (o "II 1%" da planilha da KTC) é `custo / (1 - allowance)`. Não tem
  relação nenhuma com Imposto de Importação — aqui é perda de segunda qualidade;
* **margem KTC** é margem sobre o preço final: `EXW = custo / (1 - margem)`, não `custo × 1,15`.
  E não se confunde com a margem de lucro da Anara, que é outro conceito, aplicado depois.

Nada de regra de produção inventada: família sem parâmetro cadastrado devolve
`REVIEW_REQUIRED`, e o produto continua cotável pelo último preço KTC válido.
"""
from dataclasses import dataclass, field
from typing import List, Optional

# status do cálculo industrial
CALCULATED = "CALCULATED"
CALCULATED_PARTIAL = "CALCULATED_PARTIAL"   # base industrial ok, mas há acabamento sem custo conhecido
REVIEW_REQUIRED = "REVIEW_REQUIRED"         # falta parâmetro essencial — não dá para calcular


@dataclass
class Etapa:
    """Uma linha do waterfall, com a conta escrita por extenso."""
    ordem: int
    nome: str
    formula: str
    valor: Optional[float]
    unidade: str = ""

    def como_dict(self) -> dict:
        return {"ordem": self.ordem, "nome": self.nome, "formula": self.formula,
                "valor": self.valor, "unidade": self.unidade}


@dataclass
class ParametrosKTC:
    """Parâmetros do cálculo. Todos vêm de tabela versionada — nenhum default escondido."""
    material_price_usd_m2: Optional[float] = None
    cmt_usd: Optional[float] = None
    shrinkage: Optional[float] = None
    waste: Optional[float] = None
    quality_allowance: Optional[float] = None
    ktc_margin: Optional[float] = None
    hem_width_total_cm: Optional[float] = None
    hem_length_total_cm: Optional[float] = None
    paineis: int = 1
    other_costs_usd: float = 0.0
    material_ref: Optional[str] = None
    cmt_ref: Optional[str] = None
    # preço por kg, só para toalhas
    price_usd_kg: Optional[float] = None
    acabamentos_sem_custo: List[str] = field(default_factory=list)


@dataclass
class ResultadoKTC:
    exw_usd: Optional[float]
    status: str
    etapas: List[Etapa] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)
    faltando: List[str] = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {"exw_usd": self.exw_usd, "status": self.status,
                "etapas": [e.como_dict() for e in self.etapas],
                "avisos": list(self.avisos), "faltando": list(self.faltando),
                "detalhes": dict(self.detalhes)}


def _falta(p: ParametrosKTC, campos: List[str]) -> List[str]:
    return [c for c in campos if getattr(p, c, None) is None]


def shrinkage_por_composicao(cotton_pct: Optional[float]) -> str:
    """Escopo do parâmetro de encolhimento: 100% algodão encolhe mais que poly/cotton."""
    if cotton_pct is not None and float(cotton_pct) >= 0.999:
        return "COTTON"
    return "CVC"


# ---------------------------------------------------------------------------
# Tecido plano — lençóis, fronhas, capas duvet
# ---------------------------------------------------------------------------
def calcular_tecido_plano(largura_cm: float, comprimento_cm: float,
                          p: ParametrosKTC) -> ResultadoKTC:
    """Waterfall de tecido plano. `paineis` = 1 para lençol, 2 para capa duvet (duas faces)."""
    obrigatorios = ["material_price_usd_m2", "cmt_usd", "shrinkage", "waste",
                    "quality_allowance", "ktc_margin", "hem_width_total_cm", "hem_length_total_cm"]
    faltando = _falta(p, obrigatorios)
    if not largura_cm or not comprimento_cm:
        faltando.append("dimensoes")
    if faltando:
        return ResultadoKTC(None, REVIEW_REQUIRED, faltando=faltando,
                            avisos=["Sem parâmetro suficiente para o cálculo industrial — "
                                    "usar o último preço KTC válido."])

    etapas: List[Etapa] = []
    n = 0

    def passo(nome, formula, valor, unidade=""):
        nonlocal n
        n += 1
        etapas.append(Etapa(n, nome, formula, valor, unidade))
        return valor

    largura_hem = passo("Largura com bainha",
                        f"{largura_cm:g} + {p.hem_width_total_cm:g}",
                        largura_cm + p.hem_width_total_cm, "cm")
    comprimento_hem = passo("Comprimento com bainha",
                            f"{comprimento_cm:g} + {p.hem_length_total_cm:g}",
                            comprimento_cm + p.hem_length_total_cm, "cm")
    largura_shr = passo("Largura após encolhimento",
                        f"{largura_hem:g} × (1 + {p.shrinkage:g})",
                        largura_hem * (1 + p.shrinkage), "cm")
    comprimento_shr = passo("Comprimento após encolhimento",
                            f"{comprimento_hem:g} × (1 + {p.shrinkage:g})",
                            comprimento_hem * (1 + p.shrinkage), "cm")
    area_painel = passo("Área de um painel",
                        f"{largura_shr:.4f} × {comprimento_shr:.4f} ÷ 10.000",
                        largura_shr * comprimento_shr / 10000, "m²")
    consumo_bruto = passo("Consumo bruto",
                          f"{area_painel:.6f} × {p.paineis} painel(is)",
                          area_painel * p.paineis, "m²")
    consumo = passo("Consumo com waste",
                    f"{consumo_bruto:.6f} ÷ (1 − {p.waste:g})",
                    consumo_bruto / (1 - p.waste), "m²")
    custo_tecido = passo("Custo do tecido",
                         f"{consumo:.6f} m² × US$ {p.material_price_usd_m2:g}/m²",
                         consumo * p.material_price_usd_m2, "USD")
    custo_producao = passo("Custo de produção",
                           f"{custo_tecido:.6f} + CMT {p.cmt_usd:g} + outros {p.other_costs_usd:g}",
                           custo_tecido + p.cmt_usd + (p.other_costs_usd or 0.0), "USD")
    custo_qualidade = passo("Após perda de 2ª qualidade",
                            f"{custo_producao:.6f} ÷ (1 − {p.quality_allowance:g})",
                            custo_producao / (1 - p.quality_allowance), "USD")
    exw = passo("EXW KTC calculado",
                f"{custo_qualidade:.6f} ÷ (1 − margem KTC {p.ktc_margin:g})",
                custo_qualidade / (1 - p.ktc_margin), "USD")

    status = CALCULATED_PARTIAL if p.acabamentos_sem_custo else CALCULATED
    avisos = []
    if p.acabamentos_sem_custo:
        avisos.append("Base industrial calculada, mas há acabamento sem custo conhecido: "
                      + ", ".join(p.acabamentos_sem_custo)
                      + ". Cadastrar em 'outros custos' antes de tratar o EXW como final.")

    return ResultadoKTC(exw, status, etapas, avisos,
                        detalhes={"consumo_m2": consumo, "area_painel_m2": area_painel,
                                  "paineis": p.paineis, "material_ref": p.material_ref,
                                  "cmt_ref": p.cmt_ref})


def calcular_flat_sheet(largura_cm: float, comprimento_cm: float, p: ParametrosKTC) -> ResultadoKTC:
    """Lençol: painel único."""
    p.paineis = 1
    return calcular_tecido_plano(largura_cm, comprimento_cm, p)


def calcular_duvet_cover(largura_cm: float, comprimento_cm: float, p: ParametrosKTC) -> ResultadoKTC:
    """Capa duvet open bag, sem acabamento especial: duas faces do mesmo tecido."""
    p.paineis = p.paineis or 2
    if p.paineis < 2:
        p.paineis = 2
    return calcular_tecido_plano(largura_cm, comprimento_cm, p)


# ---------------------------------------------------------------------------
# Toalhas — custo por peso
# ---------------------------------------------------------------------------
def peso_toalha_kg(largura_cm: float, comprimento_cm: float, gsm: float) -> float:
    """Peso teórico da toalha: W × L × GSM ÷ 10.000.000 (fórmula da própria KTC)."""
    return largura_cm * comprimento_cm * gsm / 10_000_000


def calcular_toalha(largura_cm: float, comprimento_cm: float, gsm: float,
                    p: ParametrosKTC) -> ResultadoKTC:
    """Custo de toalha por peso.

    A planilha da KTC calcula `peso × preço/kg` e para por aí — não há CMT, perda de segunda
    qualidade nem margem declarados para terry. Aqui esses três entram só se estiverem
    cadastrados para a construção; sem cadastro, ficam fora (e não são inventados).
    """
    if not (largura_cm and comprimento_cm and gsm):
        return ResultadoKTC(None, REVIEW_REQUIRED, faltando=["dimensoes_ou_gsm"],
                            avisos=["Toalha sem largura/comprimento/GSM estruturados."])
    if p.price_usd_kg is None:
        return ResultadoKTC(None, REVIEW_REQUIRED, faltando=["price_usd_kg"],
                            avisos=["Não há preço por kg cadastrado para essa construção de toalha. "
                                    "Usar o último preço KTC válido ou marcar para revisão — "
                                    "os preços por kg variam por construção."])

    etapas: List[Etapa] = []
    n = 0

    def passo(nome, formula, valor, unidade=""):
        nonlocal n
        n += 1
        etapas.append(Etapa(n, nome, formula, valor, unidade))
        return valor

    peso = passo("Peso da peça",
                 f"{largura_cm:g} × {comprimento_cm:g} × {gsm:g} ÷ 10.000.000",
                 peso_toalha_kg(largura_cm, comprimento_cm, gsm), "kg")
    custo = passo("Custo do fio/tecido",
                  f"{peso:.5f} kg × US$ {p.price_usd_kg:g}/kg", peso * p.price_usd_kg, "USD")

    if p.cmt_usd:
        custo = passo("Custo de produção", f"{custo:.6f} + CMT {p.cmt_usd:g}", custo + p.cmt_usd, "USD")
    if p.other_costs_usd:
        custo = passo("Outros custos", f"{custo:.6f} + {p.other_costs_usd:g}",
                      custo + p.other_costs_usd, "USD")
    if p.quality_allowance:
        custo = passo("Após perda de 2ª qualidade",
                      f"{custo:.6f} ÷ (1 − {p.quality_allowance:g})",
                      custo / (1 - p.quality_allowance), "USD")
    if p.ktc_margin:
        custo = passo("EXW KTC calculado",
                      f"{custo:.6f} ÷ (1 − margem KTC {p.ktc_margin:g})",
                      custo / (1 - p.ktc_margin), "USD")

    avisos = []
    if not (p.cmt_usd or p.quality_allowance or p.ktc_margin):
        avisos.append("Cálculo por peso: peso × preço/kg, como na planilha da KTC. A taxa por kg "
                      "cadastrada já é EXW final — CMT, perda de 2ª qualidade e margem KTC não "
                      "entram por cima, senão o custo subiria umas duas vezes pelo mesmo motivo.")
    return ResultadoKTC(custo, CALCULATED, etapas, avisos,
                        detalhes={"peso_kg": peso, "gsm": gsm})

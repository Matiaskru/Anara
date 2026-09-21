"""Nacionalização KTC — do EXW em US$ ao CUSTO NET em R$.

Módulo puro, com o mesmo waterfall auditável do motor industrial. A lógica é a que já estava
na planilha e na plataforma; o que muda é que as premissas agora chegam de fora (tabela
versionada) e o caminho fica registrado etapa por etapa.

    frete unitário   = peso_kg × frete_usd_kg
    base do I.I.     = EXW + frete
    I.I.             = base × alíquota
    NET USD          = EXW + frete + I.I. + outras despesas
    NET BRL          = NET USD × câmbio

Só vale para fornecedor importado (KTC/Egito). Fornecedor nacional não passa por aqui.

## Economia real × formação comercial (22/09/2026)

Desde 22/09/2026 o **I.I. econômico da KTC/Egito é 0%**: `nacionalizar(..., ii_pct=0)` é o
CUSTO NET REAL, o único que entra em lucro, margem realizada, dashboard e relatórios. A antiga
alíquota preferencial (3,5%; 1,62% em travesseiros/protetores) deixou de ser custo e passou a
existir apenas como **proteção comercial de precificação** — `referencia_comercial()` — que
reproduz o mesmo waterfall com a proteção no lugar do imposto, para que B2B, tabela e preco_base
fiquem exatamente onde estavam. A proteção NÃO é tributo, custo nem despesa; é política de preço.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from app.dinheiro import D, ZERO, para_float
from app.ktc_engine import Etapa


@dataclass
class PremissasNacionalizacao:
    frete_usd_kg: Decimal
    outras_desp_usd_un: Decimal
    fx_usd_brl: Decimal
    fonte: Optional[str] = None

    def __post_init__(self):
        """Fronteira: a premissa versionada chega como float do banco e vira Decimal aqui.

        O câmbio é o caso que mais importa. `D(5.11)` é `Decimal("5.11")` — e não
        `5.1100000000000000976996261670137755572795867919921875`, que é o que
        `Decimal(5.11)` produziria e o que multiplicaria o custo NET de 289 SKUs.
        """
        self.frete_usd_kg = D(self.frete_usd_kg, ZERO)
        self.outras_desp_usd_un = D(self.outras_desp_usd_un, ZERO)
        self.fx_usd_brl = D(self.fx_usd_brl, ZERO)


@dataclass
class ResultadoNacionalizacao:
    net_brl: Optional[Decimal]
    net_usd: Optional[Decimal] = None
    frete_usd: Optional[Decimal] = None
    ii_usd: Optional[Decimal] = None
    etapas: List[Etapa] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {"net_brl": para_float(self.net_brl), "net_usd": para_float(self.net_usd),
                "frete_usd": para_float(self.frete_usd), "ii_usd": para_float(self.ii_usd),
                "etapas": [e.como_dict() for e in self.etapas],
                "avisos": list(self.avisos)}


def nacionalizar(exw_usd, peso_kg, ii_pct,
                 premissas: PremissasNacionalizacao) -> ResultadoNacionalizacao:
    avisos = []
    exw_usd = D(exw_usd)
    peso_kg = D(peso_kg)
    ii_pct = D(ii_pct)
    if exw_usd is None:
        return ResultadoNacionalizacao(None, avisos=["Sem EXW: não dá para nacionalizar."])
    if not exw_usd.is_finite() or exw_usd <= 0:
        # EXW zero ou negativo nunca é preço de fábrica. Nacionalizá-lo devolvia "outras
        # despesas × câmbio" (R$ 1,29) como se fosse custo — e esse custo formava preço.
        return ResultadoNacionalizacao(
            None, avisos=[f"EXW inválido ({exw_usd}): não dá para nacionalizar."])
    if peso_kg is not None and (not peso_kg.is_finite() or peso_kg < 0):
        return ResultadoNacionalizacao(None, avisos=[f"Peso inválido ({peso_kg} kg)."])
    if peso_kg is None:
        avisos.append("Produto sem peso — frete internacional considerado zero. Confirmar peso com a KTC.")
        peso_kg = ZERO
    if ii_pct is None:
        avisos.append("Sem alíquota de Imposto de Importação confiável para esse NCM/família — "
                      "considerado zero no cálculo. Validar antes de usar comercialmente.")
        ii_pct = ZERO

    etapas: List[Etapa] = []
    n = 0

    def passo(nome, formula, valor, unidade=""):
        nonlocal n
        n += 1
        etapas.append(Etapa(n, nome, formula, valor, unidade))
        return valor

    passo("EXW KTC", "preço de fábrica", exw_usd, "USD")
    frete = passo("Frete internacional",
                  f"{peso_kg:g} kg × US$ {premissas.frete_usd_kg:g}/kg",
                  peso_kg * premissas.frete_usd_kg, "USD")
    base_ii = passo("Base do Imposto de Importação",
                    f"{exw_usd:.4f} + {frete:.4f}", exw_usd + frete, "USD")
    ii = passo("Imposto de Importação",
               f"{base_ii:.4f} × {ii_pct:g}", base_ii * ii_pct, "USD")
    net_usd = passo("Custo NET",
                    f"{exw_usd:.4f} + {frete:.4f} + {ii:.4f} + {premissas.outras_desp_usd_un:g}",
                    exw_usd + frete + ii + premissas.outras_desp_usd_un, "USD")
    net_brl = passo("Custo NET em reais",
                    f"{net_usd:.6f} × câmbio {premissas.fx_usd_brl:g}",
                    net_usd * premissas.fx_usd_brl, "BRL")

    return ResultadoNacionalizacao(net_brl, net_usd, frete, ii, etapas, avisos)


# ---------------------------------------------------------------------------
# Referência comercial de precificação — NÃO é custo (22/09/2026)
# ---------------------------------------------------------------------------
@dataclass
class ReferenciaComercial:
    """Base sobre a qual a política comercial forma o B2B de um SKU importado.

    Mesma aritmética da nacionalização, com a PROTEÇÃO COMERCIAL onde antes entrava o I.I.
    Por isso o B2B formado sobre ela é idêntico ao que se formava até 22/09/2026 — e por isso
    ela nunca entra em lucro, margem realizada ou custo: é referência de preço, não economia.
    """
    brl: Optional[Decimal]
    usd: Optional[Decimal] = None
    frete_usd: Optional[Decimal] = None
    protecao_pct: Optional[Decimal] = None
    protecao_usd: Optional[Decimal] = None
    etapas: List[Etapa] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {"brl": para_float(self.brl), "usd": para_float(self.usd),
                "frete_usd": para_float(self.frete_usd),
                "protecao_pct": para_float(self.protecao_pct),
                "protecao_usd": para_float(self.protecao_usd),
                "natureza": "formação comercial de preço — não é custo, tributo nem despesa",
                "etapas": [e.como_dict() for e in self.etapas]}


def referencia_comercial(exw_usd, peso_kg, protecao_pct,
                         premissas: PremissasNacionalizacao) -> ReferenciaComercial:
    """(EXW + frete) × (1 + proteção) + outras despesas, em US$ e R$.

    `protecao_pct` é a alíquota preferencial que o SKU usava como I.I. até 22/09/2026, pinada
    no produto ou lida da regra da família — só como fator de preço. Peso ausente = frete zero
    (o aviso fica na nacionalização, que é quem decide o status do custo).
    """
    exw_usd, peso_kg, protecao_pct = D(exw_usd), D(peso_kg), D(protecao_pct)
    if exw_usd is None or not exw_usd.is_finite() or exw_usd <= 0 or protecao_pct is None:
        return ReferenciaComercial(None)
    peso_kg = peso_kg if (peso_kg is not None and peso_kg.is_finite() and peso_kg >= 0) else ZERO
    etapas: List[Etapa] = []

    def passo(n, nome, formula, valor, unidade=""):
        etapas.append(Etapa(n, nome, formula, valor, unidade))
        return valor

    passo(1, "EXW KTC", "preço de fábrica", exw_usd, "USD")
    frete = passo(2, "Frete internacional", f"{peso_kg:g} kg × US$ {premissas.frete_usd_kg:g}/kg",
                  peso_kg * premissas.frete_usd_kg, "USD")
    base = passo(3, "Base da proteção comercial", f"{exw_usd:.4f} + {frete:.4f}", exw_usd + frete, "USD")
    protecao = passo(4, "Proteção comercial de precificação (não é imposto)",
                     f"{base:.4f} × {protecao_pct:g}", base * protecao_pct, "USD")
    usd = passo(5, "Referência comercial",
                f"{exw_usd:.4f} + {frete:.4f} + {protecao:.4f} + {premissas.outras_desp_usd_un:g}",
                exw_usd + frete + protecao + premissas.outras_desp_usd_un, "USD")
    brl = passo(6, "Referência comercial em reais", f"{usd:.6f} × câmbio {premissas.fx_usd_brl:g}",
                usd * premissas.fx_usd_brl, "BRL")
    return ReferenciaComercial(brl, usd, frete, protecao_pct, protecao, etapas)

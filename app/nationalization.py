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

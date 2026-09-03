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
from typing import List, Optional

from app.ktc_engine import Etapa


@dataclass
class PremissasNacionalizacao:
    frete_usd_kg: float
    outras_desp_usd_un: float
    fx_usd_brl: float
    fonte: Optional[str] = None


@dataclass
class ResultadoNacionalizacao:
    net_brl: Optional[float]
    net_usd: Optional[float] = None
    frete_usd: Optional[float] = None
    ii_usd: Optional[float] = None
    etapas: List[Etapa] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {"net_brl": self.net_brl, "net_usd": self.net_usd, "frete_usd": self.frete_usd,
                "ii_usd": self.ii_usd, "etapas": [e.como_dict() for e in self.etapas],
                "avisos": list(self.avisos)}


def nacionalizar(exw_usd: float, peso_kg: Optional[float], ii_pct: Optional[float],
                 premissas: PremissasNacionalizacao) -> ResultadoNacionalizacao:
    avisos = []
    if exw_usd is None:
        return ResultadoNacionalizacao(None, avisos=["Sem EXW: não dá para nacionalizar."])
    if peso_kg is None:
        avisos.append("Produto sem peso — frete internacional considerado zero. Confirmar peso com a KTC.")
        peso_kg = 0.0
    if ii_pct is None:
        avisos.append("Sem alíquota de Imposto de Importação confiável para esse NCM/família — "
                      "considerado zero no cálculo. Validar antes de usar comercialmente.")
        ii_pct = 0.0

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

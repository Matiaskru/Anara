"""Condição de pagamento e encargo financeiro — uma regra, um lugar.

Reescrito na Onda 1. O encargo de cada condição é uma linha de tabela versionada, e **só isso**.

O que saiu daqui, e por quê: a versão anterior tinha uma "rede" que contava as barras da string
("30/60/90" → 3 parcelas → 4,8%) para condições não cadastradas, e devolvia 1,6% quando o código
vinha vazio. Isso é interpolação — produz um encargo plausível para uma condição que ninguém
aprovou, e o número seguia para o preço. A regra do projeto é explícita: **não interpolar, não
aproximar, não contar barras, não inferir parcela**. Condição que não está cadastrada exige
premissa versionada ou override autorizado; sem isso, o cálculo é bloqueado.

Condições canônicas (todas cadastradas em `CondicaoPagamento`):

    30 DD 1,6% · 30/60 3,2% · 30/60/90 4,8% · 30/60/90/120 6,4% · 30/60/90/120/150 8,0%
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.dinheiro import D, ZERO

OK = "OK"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class EncargoResolvido:
    pct: Decimal
    confirmado: bool
    label: str
    origem: str                 # "tabela" | "override" | "bloqueado"
    status: str = OK
    aviso: Optional[str] = None
    motivo: Optional[str] = None

    @property
    def bloqueado(self) -> bool:
        return self.status == REVIEW_REQUIRED


def _bloqueio(label: str, motivo: str) -> EncargoResolvido:
    return EncargoResolvido(pct=ZERO, confirmado=False, label=label, origem="bloqueado",
                            status=REVIEW_REQUIRED, aviso=motivo, motivo=motivo)


def resolver_encargo(condicoes: Sequence, codigo: str,
                     override_pct: Optional[float] = None,
                     override_motivo: Optional[str] = None) -> EncargoResolvido:
    """Encargo financeiro da condição. Só devolve número quando há premissa ou override.

    `override_pct` existe para o caso autorizado — uma condição negociada fora da tabela. Quem
    usa precisa registrar o motivo; sem motivo, o override é recusado.
    """
    codigo = (codigo or "").strip()

    if override_pct is not None:
        if not override_motivo:
            return _bloqueio(codigo or "(sem condição)",
                             "Override de encargo financeiro exige motivo registrado.")
        return EncargoResolvido(D(override_pct), True, codigo or "(override)", "override",
                                aviso=f"Encargo por override autorizado: {override_motivo}")

    if not codigo:
        return _bloqueio("(sem condição)",
                         "Condição de pagamento não informada. O encargo financeiro não é "
                         "presumido — escolha uma condição cadastrada.")

    for c in condicoes:
        if (c.codigo or "").strip().lower() != codigo.lower():
            continue
        if c.encargo_pct is None:
            return _bloqueio(
                c.label,
                f"A condição '{c.label}' está cadastrada mas não tem encargo financeiro "
                "confirmado. Cadastrar a taxa no painel antes de usar comercialmente.")
        return EncargoResolvido(D(c.encargo_pct), bool(c.encargo_confirmado), c.label,
                                "tabela")

    return _bloqueio(
        codigo,
        f"Condição de pagamento '{codigo}' não está cadastrada. O encargo não é estimado "
        "por contagem de parcelas — cadastre a condição com sua taxa, ou use um override "
        "autorizado.")

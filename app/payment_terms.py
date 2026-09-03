"""Condição de pagamento e encargo financeiro — uma regra, um lugar.

Antes o encargo era calculado contando as barras da string ("30/60/90") dentro do motor de
preço. Agora cada condição é uma linha de tabela com seu encargo, o que permite cadastrar
condições que não seguem a régua de 1,6% por parcela (sinal + parcelas, cartão) sem inventar
taxa nenhuma: condição sem taxa confirmada é sinalizada, não estimada.

A contagem de barras continua existindo só como rede para condições antigas que ainda não
foram cadastradas — e quando ela é usada, o chamador recebe um aviso.
"""
from dataclasses import dataclass
from typing import Optional, Sequence

ENCARGO_POR_PARCELA = 0.016


@dataclass
class EncargoResolvido:
    pct: float
    confirmado: bool
    label: str
    origem: str            # "tabela" | "legado" | "padrao"
    aviso: Optional[str] = None


def resolver_encargo(condicoes: Sequence, codigo: str,
                     base_pct: float = ENCARGO_POR_PARCELA) -> EncargoResolvido:
    codigo = (codigo or "").strip()
    for c in condicoes:
        if (c.codigo or "").strip().lower() == codigo.lower():
            if c.encargo_pct is None:
                return EncargoResolvido(
                    0.0, False, c.label, "tabela",
                    f"A condição '{c.label}' ainda não tem encargo financeiro confirmado. "
                    "O preço está sendo formado sem encargo — cadastrar a taxa no painel antes "
                    "de usar comercialmente.")
            return EncargoResolvido(float(c.encargo_pct), bool(c.encargo_confirmado),
                                    c.label, "tabela")

    if not codigo:
        return EncargoResolvido(base_pct, True, "30 dias", "padrao")

    parcelas_extra = codigo.count("/")
    pct = base_pct + parcelas_extra * ENCARGO_POR_PARCELA
    return EncargoResolvido(
        pct, False, codigo, "legado",
        f"Condição '{codigo}' não está cadastrada; encargo calculado pela régua antiga "
        f"({base_pct:.1%} + {ENCARGO_POR_PARCELA:.1%} por parcela extra). Cadastrar no painel.")

"""Peso do produto — define o frete internacional, então erra caro.

Ordem de prioridade:

1. **peso real informado pela KTC** — nunca é substituído por estimativa;
2. **peso por m² da família**, calibrado com os pesos reais que a KTC declarou na PI de
   23/08/2026. É mais fiel que a conta de tecido puro porque o peso que a KTC informa é o de
   embarque: inclui bainha, os dois painéis da capa duvet e a embalagem;
3. **estimado por dimensão × GSM** — `área m² × GSM ÷ 1000`, com o GSM vindo da gramatura
   cadastrada, da tabela fios→GSM ou do GSM padrão da família. Funciona bem em toalha, onde o
   GSM é o dado técnico real;
4. **peso técnico da família** — último recurso, para produto sem dimensão (chinelo).

Função pura: recebe as tabelas já lidas do banco.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PesoResolvido:
    peso_kg: Optional[float]
    tipo: Optional[str]          # "REAL KTC" | "ESTIMADO" | "TÉCNICO"
    fonte: Optional[str]
    gsm_efetivo: Optional[float] = None


def resolver_peso(peso_real: Optional[float], largura_cm: Optional[float],
                  comprimento_cm: Optional[float], gsm: Optional[float],
                  thread_count: Optional[int], familia: Optional[str],
                  gsm_por_tc: dict, gsm_por_familia: dict,
                  peso_por_familia: dict,
                  peso_kg_m2_familia: Optional[dict] = None) -> PesoResolvido:
    if peso_real:
        return PesoResolvido(float(peso_real), "REAL KTC", "Peso informado pela KTC")

    peso_kg_m2_familia = peso_kg_m2_familia or {}
    if largura_cm and comprimento_cm and familia in peso_kg_m2_familia:
        area = (largura_cm * comprimento_cm) / 10000
        return PesoResolvido(area * peso_kg_m2_familia[familia], "ESTIMADO",
                             f"Estimado: área × peso/m² da família {familia} "
                             "(calibrado com os pesos reais da PI 23/08/2026)")

    if largura_cm and comprimento_cm:
        gsm_efetivo = gsm
        origem_gsm = "GSM do produto"
        if gsm_efetivo is None and thread_count is not None:
            gsm_efetivo = gsm_por_tc.get(str(int(thread_count)))
            origem_gsm = f"tabela fios→GSM ({int(thread_count)} fios)"
        if gsm_efetivo is None and familia:
            gsm_efetivo = gsm_por_familia.get(familia)
            origem_gsm = f"GSM padrão da família {familia}"
        if gsm_efetivo:
            area = (largura_cm * comprimento_cm) / 10000
            return PesoResolvido(area * gsm_efetivo / 1000, "ESTIMADO",
                                 f"Estimado: área × GSM ({origem_gsm})", gsm_efetivo)

    if familia and familia in peso_por_familia:
        return PesoResolvido(peso_por_familia[familia], "TÉCNICO",
                             f"Peso técnico padrão da família {familia}")

    return PesoResolvido(None, None, None)

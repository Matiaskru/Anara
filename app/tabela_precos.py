"""Tabela de preços das vendedoras, dentro do sistema (10/11/2026).

As linhas são EXATAMENTE as da Tabela comercial publicada (`app/tabela_comercial_layout.py`, gerado do xlsx):
mesmas seções, mesma ordem, mesmos textos, mesmos itens "sob consulta" — mais os itens da cotação KTC 20/10
que o Matias mandou incluir (tapetes de banheiro e 600 fios 100% algodão).

**O preço é FIXO** (decisão do Matias, 10/11/2026: "a regra agora é manter o preço, não a margem"): o B2B
publicado na planilha, por código, estado, contribuinte e prazo (`app/tabela_comercial_precos.json`). Câmbio
e custo mudam a margem, não esta página. Tabela cheia = 2 × B2B; a escada −10…−40% sai de
`preco_por_desconto`, como na planilha (o gerador conferiu as duas regras em todas as células).

Só preço comercial: nenhum custo, margem ou memória entra aqui.
"""
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from app.dinheiro import D, para_float
from app.pricing_engine import preco_por_desconto
from app.tabela_comercial_layout import LINHAS, VERSAO

DESCONTOS = ("0.10", "0.20", "0.30", "0.40")
_ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabela_comercial_precos.json")
with open(_ARQUIVO, encoding="utf-8") as _f:
    _DADOS = json.load(_f)
PRAZOS: List[str] = _DADOS["prazos"]
PRECOS: dict = _DADOS["precos"]
ESTADOS: List[str] = sorted({chave.split("|")[0] for por_cenario in PRECOS.values() for chave in por_cenario})


@dataclass
class LinhaTabela:
    tipo: str                                  # "secao" | "item"
    texto: str = ""                            # seção
    nivel: int = 1
    codigo: str = ""
    descricao: str = ""
    medida: str = ""
    especificacao: str = ""
    situacao: str = "PRECO"                    # PRECO | SOB_CONSULTA | SEM_PRECO
    tabela: Optional[float] = None
    escada: List[Optional[float]] = field(default_factory=list)   # −10, −20, −30, −40
    b2b: Optional[float] = None


def preco_b2b(codigo: str, estado: str, contribuinte: bool, condicao: str) -> Optional[float]:
    """B2B fixo publicado, ou None (sob consulta / cenário sem preço na tabela)."""
    if condicao not in PRAZOS:
        return None
    linha = PRECOS.get(codigo, {}).get(f"{estado}|{'SIM' if contribuinte else 'NÃO'}")
    return linha[PRAZOS.index(condicao)] if linha else None


def montar(estado: str, contribuinte: bool, condicao: str) -> List[LinhaTabela]:
    linhas = []
    for item in LINHAS:
        if item["tipo"] == "secao":
            linhas.append(LinhaTabela(tipo="secao", texto=item["texto"], nivel=item["nivel"]))
            continue
        linha = LinhaTabela(tipo="item", codigo=item["codigo"], descricao=item["descricao"],
                            medida=item["medida"], especificacao=item["especificacao"])
        b2b = None if item["sob_consulta"] else preco_b2b(item["codigo"], estado, contribuinte, condicao)
        if item["sob_consulta"]:
            linha.situacao = "SOB_CONSULTA"
        elif b2b is None:
            linha.situacao = "SEM_PRECO"
        else:
            tabela = D(b2b) * 2
            linha.b2b = para_float(D(b2b))
            linha.tabela = para_float(tabela)
            linha.escada = [para_float(preco_por_desconto(tabela, D(d))) for d in DESCONTOS]
        linhas.append(linha)
    return linhas


def versao() -> str:
    return VERSAO

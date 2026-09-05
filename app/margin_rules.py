"""Margem líquida-alvo padrão — resolvida por tabela, nunca por `if` espalhado no código.

A margem aqui é sempre **margem líquida**: o que sobra depois de custo, ICMS, PIS/COFINS,
encargo financeiro e comissão. Não é markup.

Precedência (a de menor `prioridade` ganha; empate desempata pela regra mais específica):

1. override explícito feito na cotação — tratado na cotação, não aqui;
2. regra por SKU;
3. regra por fornecedor (Daune e Decor Tricot ficam em 14% mesmo que a família pareça KTC);
4. regra por família (com faixa de thread count, quando fizer sentido);
5. regra geral.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.dinheiro import D, para_float


@dataclass
class MargemResolvida:
    margem_pct: Decimal
    regra: str
    regra_id: Optional[int] = None
    origem: str = "tabela"

    def como_dict(self) -> dict:
        return {"margem_pct": para_float(self.margem_pct), "regra": self.regra,
                "regra_id": self.regra_id, "origem": self.origem}


MARGEM_ULTIMO_RECURSO = Decimal("0.15")


def _vigente_em(regra, ref) -> bool:
    """A regra está valendo nesta data?

    `MargemRegra` sempre teve `valid_from`/`valid_to`, mas o resolvedor os ignorava — só
    olhava `ativo`. Consequência: uma regra cadastrada para valer no ano que vem passava a
    valer no instante em que era salva. Corrigido na Sessão 5; regra sem datas continua
    valendo sempre, como as herdadas.
    """
    if ref is None:
        return True
    inicio = getattr(regra, "valid_from", None)
    fim = getattr(regra, "valid_to", None)
    if inicio is not None and inicio > ref:
        return False
    if fim is not None and fim <= ref:
        return False
    return True


def _bate(regra, fornecedor_id, familia, thread_count, sku_key, ref=None) -> bool:
    if not getattr(regra, "ativo", True):
        return False
    if not _vigente_em(regra, ref):
        return False
    if regra.sku_key and (sku_key or "") != regra.sku_key:
        return False
    if regra.fornecedor_id is not None and regra.fornecedor_id != fornecedor_id:
        return False
    if regra.familia:
        if not familia or regra.familia.strip().lower() != familia.strip().lower():
            return False
    if regra.min_thread_count is not None:
        if thread_count is None or thread_count < regra.min_thread_count:
            return False
    if regra.max_thread_count is not None:
        if thread_count is None or thread_count >= regra.max_thread_count:
            return False
    return True


def _especificidade(regra) -> int:
    """Quanto mais campos a regra fixa, mais específica ela é."""
    pontos = 0
    if regra.sku_key:
        pontos += 8
    if regra.fornecedor_id is not None:
        pontos += 4
    if regra.familia:
        pontos += 2
    if regra.min_thread_count is not None or regra.max_thread_count is not None:
        pontos += 1
    return pontos


def resolver_margem(regras: Sequence, fornecedor_id: Optional[int] = None,
                    familia: Optional[str] = None, thread_count: Optional[int] = None,
                    sku_key: Optional[str] = None,
                    override_pct: Optional[float] = None, ref=None) -> MargemResolvida:
    if override_pct is not None:
        return MargemResolvida(D(override_pct), "Margem definida manualmente nesta cotação",
                               origem="override")

    candidatas = [r for r in regras
                  if _bate(r, fornecedor_id, familia, thread_count, sku_key, ref)]
    if not candidatas:
        return MargemResolvida(MARGEM_ULTIMO_RECURSO,
                               "Nenhuma regra de margem cadastrada bateu — usando 15% como último "
                               "recurso. Cadastrar a regra no painel.", origem="fallback")

    candidatas.sort(key=lambda r: (getattr(r, "prioridade", 100), -_especificidade(r), r.id or 0))
    escolhida = candidatas[0]
    return MargemResolvida(D(escolhida.margem_pct), escolhida.nome, escolhida.id)

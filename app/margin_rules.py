"""Margem líquida-alvo padrão — resolvida por tabela, nunca por `if` espalhado no código.

A margem aqui é sempre **margem líquida**: o que sobra depois de custo, ICMS, PIS/COFINS,
encargo financeiro e comissão. Não é markup.

Desde 16/09/2026 (Fase 3A) a regra resolvida carrega a **política comercial** do escopo
junto com a margem: piso de autonomia da vendedora, comissão de formação do recomendado e
preço travado. Regra sem esses campos é anterior à política e continua valendo com a
semântica antiga para quem a pinou.

**A resolução é por data.** `ref` default é hoje: uma regra encerrada ontem não forma preço
hoje, e uma cadastrada para o ano que vem não forma preço agora. Passar `ref=None`
explicitamente ignora a vigência — é para inspeção, não para precificar.

Precedência (a de menor `prioridade` ganha; empate desempata pela regra mais específica):

1. override explícito feito na cotação — tratado na cotação, não aqui;
2. regra por SKU;
3. regra por fornecedor (Daune e Decor Tricot ficam em 14% mesmo que a família pareça KTC);
4. regra por família (com faixa de thread count, quando fizer sentido);
5. regra geral.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from app.dinheiro import D, para_float

#: Sentinela: "sem filtro de vigência". Diferente de `None`, que agora significa "hoje".
SEM_VIGENCIA = object()


@dataclass
class MargemResolvida:
    margem_pct: Optional[Decimal]          # None = NENHUMA regra bateu (política 21/09: bloqueia)
    regra: str
    regra_id: Optional[int] = None
    origem: str = "tabela"
    # --- política comercial do escopo (Fase 3A); None = regra anterior à política ---
    piso_pct: Optional[Decimal] = None
    comissao_formacao_pct: Optional[Decimal] = None
    preco_travado: bool = False
    politica: Optional[str] = None
    margem_anterior_pct: Optional[Decimal] = None

    @property
    def tem_politica(self) -> bool:
        return self.politica is not None

    @property
    def tem_regra(self) -> bool:
        """Existe regra cadastrada para este produto? Sem regra não se forma preço."""
        return self.margem_pct is not None and self.origem != "sem_regra"

    def como_dict(self) -> dict:
        return {"margem_pct": para_float(self.margem_pct), "regra": self.regra,
                "regra_id": self.regra_id, "origem": self.origem,
                "piso_pct": para_float(self.piso_pct),
                "comissao_formacao_pct": para_float(self.comissao_formacao_pct),
                "preco_travado": bool(self.preco_travado), "politica": self.politica,
                "margem_anterior_pct": para_float(self.margem_anterior_pct)}


#: Até 21/09/2026 um produto sem regra recebia 15% "de último recurso". A política de
#: 21/09/2026 proíbe default silencioso: sem regra, `resolver_margem` devolve
#: `margem_pct=None` (`origem="sem_regra"`), e quem forma preço bloqueia o item e o expõe
#: ao Admin. A constante fica só como registro histórico do que existia.
MARGEM_ULTIMO_RECURSO_LEGADA = Decimal("0.15")
SEM_REGRA = "sem_regra"
MOTIVO_SEM_REGRA = ("Nenhuma regra de margem cadastrada alcança este produto. O sistema não "
                    "assume margem: cadastre a regra do escopo (fornecedor, família ou SKU) "
                    "no painel de administração.")


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
    """A regra vigente em `ref` (default: hoje) para este produto.

    **C-NEW-13.** Até 16/09/2026 `pricing_service.margem_padrao` chamava isto sem `ref`, e
    `ref=None` era "ignore a vigência": uma regra encerrada pelo painel de administração
    continuava formando preço, e a nova só ganhava se tivesse prioridade menor. Agora `None`
    resolve por hoje; quem quer a lista sem filtro de data passa `SEM_VIGENCIA`.
    """
    if override_pct is not None:
        return MargemResolvida(D(override_pct), "Margem definida manualmente nesta cotação",
                               origem="override")
    if ref is None:
        ref = date.today()
    elif ref is SEM_VIGENCIA:
        ref = None

    candidatas = [r for r in regras
                  if _bate(r, fornecedor_id, familia, thread_count, sku_key, ref)]
    if not candidatas:
        return MargemResolvida(None, MOTIVO_SEM_REGRA, origem=SEM_REGRA)

    # Empate de prioridade e especificidade: ganha a vigência mais recente, e só então o id
    # menor. Sem isso, a regra nova de um escopo (mesma prioridade, mesma especificidade)
    # perderia para a antiga no dia da virada, em que as duas ainda respondem.
    candidatas.sort(key=lambda r: (getattr(r, "prioridade", 100), -_especificidade(r),
                                   -(getattr(r, "valid_from", None) or date.min).toordinal(),
                                   r.id or 0))
    escolhida = candidatas[0]
    return MargemResolvida(
        D(escolhida.margem_pct), escolhida.nome, escolhida.id,
        piso_pct=D(getattr(escolhida, "piso_pct", None)),
        comissao_formacao_pct=D(getattr(escolhida, "comissao_formacao_pct", None)),
        preco_travado=bool(getattr(escolhida, "preco_travado", False)),
        politica=getattr(escolhida, "politica", None),
        margem_anterior_pct=D(getattr(escolhida, "margem_anterior_pct", None)))

"""Quem é o histórico que os testes de guardião protegem.

Vários testes leem o banco de **produção** para provar que o legado não foi falsificado:
que as cotações herdadas não ganharam aprovação, snapshot, oportunidade nem revisão que
nunca tiveram. A pergunta é legítima e continua valendo.

O que estava errado era a âncora. Eles afirmavam `len(cotacoes) == 18` — a contagem
**total** da tabela. Isso funcionou enquanto ninguém usava o sistema. No dia em que o
Matias emitiu a `ANARA-2026-0019`, os quatro testes ficaram vermelhos sem que nada do
histórico tivesse mudado, e passariam a ficar vermelhos a cada cotação nova. Guardião que
dispara todo dia por motivo conhecido para de ser lido — e aí deixa de guardar exatamente
quando alguém precisa dele.

A âncora certa é a **identidade** do conjunto herdado, não o tamanho da tabela. E o conjunto
herdado já tem uma definição canônica e imutável: `relatorios/baseline_fase0.json`, gerado
antes das migrations da Fase 0 e que, por regra do projeto, **não se regenera**. É de lá que
estes ids saem — não de uma constante digitada aqui, que precisaria ser mantida à mão e
poderia divergir do baseline sem ninguém notar.

Uso:

    import legado
    historicas = legado.somente_cotacoes(prod.exec(select(Cotacao)).all())

Cotação criada depois da Fase 0 é simplesmente ignorada por estes testes: ela não é assunto
deles. O que ela pode ou não fazer é coberto pelas suítes de workflow, CRM e métricas, que
rodam em banco temporário.
"""
import json
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(RAIZ, "relatorios", "baseline_fase0.json")


def _carregar():
    if not os.path.exists(BASELINE):
        return None
    with open(BASELINE) as f:
        return json.load(f)


_BASE = _carregar()

#: Ids das cotações que existiam antes da Fase 0 — as únicas sobre as quais o guardião fala.
COTACOES = frozenset(c["id"] for c in (_BASE or {}).get("cotacoes", []))

#: Ids dos itens dessas cotações.
ITENS = frozenset(i["id"] for i in (_BASE or {}).get("itens", []))

sem_baseline = pytest.mark.skipif(
    not COTACOES,
    reason="baseline da Fase 0 ausente — sem ele não há conjunto legado a proteger")


def somente_cotacoes(linhas):
    """As cotações herdadas, entre todas as que o banco tiver hoje."""
    return [c for c in linhas if c.id in COTACOES]


def somente_por_cotacao(linhas):
    """Linhas de tabelas que apontam para cotação (`AprovacaoCotacao`, `SnapshotEmissao`,
    `CotacaoItem`), restritas às cotações herdadas."""
    return [x for x in linhas if getattr(x, "cotacao_id", None) in COTACOES]

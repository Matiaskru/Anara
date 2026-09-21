"""A memória do preço é representação externa — e externa quer dizer JSON, em número.

`memoria_do_preco()` alimenta três coisas: a resposta JSON da calculadora, o
`memoria_json` gravado no item e o baseline de regressão. Nas três, o valor precisa ser
`float`: `Decimal` não é serializável, e deixar `json.dumps(default=str)` resolver
transformaria número em texto e mudaria o formato do que já está gravado.

O bloco `fiscal` escapava dessa regra. `contexto["fiscal"]` passava por `como_dict()`, mas
`icms_pct`, `aliquota_interna_destino`, `fcp_pct` e `encargo_pct` vinham do nível de cima,
espalhados crus para dentro do dicionário.

Duas consequências, e a segunda passou meses sem aparecer:

* `/calculadora/calcular` quebrava com *"Object of type Decimal is not JSON serializable"*
  sempre que o cenário fiscal **resolvia** — que é o caso do cenário padrão do catálogo. A
  calculadora estava inteiramente fora do ar;
* um item novo com cenário fiscal resolvido gravaria `"0.18"` (texto) onde as cotações
  históricas têm `0.18` (número). Ninguém tinha esbarrado nisso porque os itens recentes
  do banco tinham o fiscal bloqueado, e aí os campos saíam `null`.

O defeito é anterior ao product cleanup — reproduz em `6600f68`.
"""
import json
from decimal import Decimal

import pytest
from sqlmodel import select

from app import calculadora as calc
from app import pricing_service as ps
from app.models import MaterialPreco


def _decimais(valor, caminho=""):
    """Todos os caminhos onde ainda existe um `Decimal`."""
    if isinstance(valor, Decimal):
        yield caminho or "(raiz)"
    elif isinstance(valor, dict):
        for chave, item in valor.items():
            yield from _decimais(item, f"{caminho}.{chave}" if caminho else str(chave))
    elif isinstance(valor, (list, tuple)):
        for i, item in enumerate(valor):
            yield from _decimais(item, f"{caminho}[{i}]")


@pytest.fixture
def material(session):
    m = session.exec(select(MaterialPreco).where(MaterialPreco.ativo == True)).first()  # noqa: E712
    if m is None:
        pytest.skip("sem material cadastrado neste banco de teste")
    return m


def test_calculadora_devolve_json_serializavel(session, material):
    memoria = calc.calcular(session, familia="Flat Sheet", largura_cm=160,
                            comprimento_cm=310, material_id=material.id,
                            plain_or_stripe="plain", quantidade=10)

    sobraram = list(_decimais(memoria))
    assert sobraram == [], f"Decimal escapou para a memória em: {sobraram}"
    json.dumps(memoria)          # é isto que a rota faz, e é onde quebrava


def test_bloco_fiscal_sai_em_numero_nao_em_texto(session, material):
    """`0.18`, não `"0.18"` — o formato tem de bater com o das cotações históricas."""
    memoria = calc.calcular(session, familia="Flat Sheet", largura_cm=160,
                            comprimento_cm=310, material_id=material.id,
                            plain_or_stripe="plain", quantidade=10)
    fiscal = memoria["fiscal"]

    for campo in ("icms_pct", "aliquota_interna_destino", "encargo_pct"):
        if fiscal.get(campo) is None:
            continue
        assert isinstance(fiscal[campo], (int, float)), (
            f"fiscal.{campo} saiu como {type(fiscal[campo]).__name__}")

    # e a ida e volta pelo JSON preserva o tipo
    relido = json.loads(json.dumps(fiscal))
    if relido.get("icms_pct") is not None:
        assert isinstance(relido["icms_pct"], (int, float))


def test_memoria_do_item_tambem_e_serializavel(session, material):
    """O mesmo dicionário vai para `memoria_json` — com `default=str`, que mascara o erro."""
    memoria = calc.calcular(session, familia="Flat Sheet", largura_cm=160,
                            comprimento_cm=310, material_id=material.id,
                            plain_or_stripe="plain", quantidade=10)

    texto = ps.memoria_json(memoria)
    relido = json.loads(texto)
    fiscal = relido.get("fiscal") or {}
    for campo, valor in fiscal.items():
        assert not isinstance(valor, str) or campo in (
            "icms_regra", "icms_fonte", "status_fiscal", "motivo_fiscal",
            "status_pagamento", "motivo_pagamento", "encargo_label", "encargo_aviso",
            "comissao_base",
            "encargo_saldo_label", "condicao_pagamento_texto",   # sinal (21/09/2026) — texto mesmo
            "uf_origem_fiscal", "uf_destino_fiscal", "finalidade", "origem_fiscal",
            "difal_responsavel", "motivo_bloqueio", "pis_cofins_nota",
            "politica_comercial",       # rótulo da política de 16/09/2026 — é texto mesmo
        ), f"fiscal.{campo} virou texto: {valor!r}"


def test_calculadora_forma_preco_quando_ha_material(session, material):
    """Prova de vida: com material escolhido, sai CNET e sai preço."""
    memoria = calc.calcular(session, familia="Flat Sheet", largura_cm=160,
                            comprimento_cm=310, material_id=material.id,
                            plain_or_stripe="plain", quantidade=10)

    assert memoria["calculavel"] is True
    assert memoria["custo"]["net_brl"] > 0
    assert memoria["comercial"]["preco_negociado"] > memoria["custo"]["net_brl"]


def test_familia_sem_formula_recusa_em_portugues(session):
    """Família sem regra demonstrada não é calculada — e diz por quê, sem código interno."""
    memoria = calc.calcular(session, familia="Slipper", largura_cm=None,
                            comprimento_cm=None)
    assert memoria["calculavel"] is False
    assert "motivo" in memoria
    json.dumps(memoria)

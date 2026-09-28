"""A calculadora aberta pela aba escolhe o cenário da venda (28/09/2026).

Até a calculadora ganhar porta própria no menu, chegava-se nela por dentro de uma cotação, e o
cenário vinha de lá — destino, contribuinte e condição de pagamento, que decidem ICMS, DIFAL,
FCP e encargo. Com a aba, o caminho mais fácil passou a ser o que **não** tinha cenário: ela
usava o padrão do catálogo (São Paulo, não contribuinte, 30 dias) e avisava disso em cinza no
rodapé. Quem lê o número não lê o rodapé, e o mesmo SKU varia quase 50% entre cenários — o
BR-001 vai de R$ 193 a R$ 286. Um preço dito ao cliente a partir da simulação errada é um
preço que a cotação depois não confirma.

Agora o cenário é escolhido na tela quando não há cotação. Três coisas que estes testes fixam:

1. o cenário do formulário **chega ao motor** e muda o preço (1–3);
2. ele é **transitório**: simular não cria cotação nem item no banco (4);
3. dentro de uma cotação, quem manda continua sendo ela — a tela nem oferece os campos (5–6).
"""
import pytest
from sqlmodel import select

from app.models import Cotacao, CotacaoItem
from conftest import RequestFalsa, _novo_usuario


def _form(**kw):
    """O corpo que o JS envia: o formulário inteiro, incluindo o cenário."""
    base = {"familia": "Flat Sheet", "largura_cm": "180", "comprimento_cm": "280",
            "quantidade": "1", "plain_or_stripe": "plain"}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def _material_300(session):
    from app import config_service as cfg
    return next(m for m in cfg.materiais(session) if m.thread_count == 300)


def _preco(session, **cenario):
    """O preço que a calculadora devolve para um cenário — pelo caminho real da rota."""
    from app.routers.calculadora import _cenario_do_form
    from app import calculadora as calc

    form = _form(material_id=_material_300(session).id, **cenario)
    cot = _cenario_do_form(session, form)
    memoria = calc.calcular(session, cotacao=cot, familia="Flat Sheet", largura_cm=180,
                            comprimento_cm=280, material_id=_material_300(session).id,
                            quantidade=1)
    return (memoria.get("b2b") or {}).get("preco_b2b"), cot


# ===========================================================================
# 1–3. O cenário escolhido chega ao motor
# ===========================================================================
def test_1_o_formulario_define_destino_condicao_e_contribuinte(session):
    from app.routers.calculadora import _cenario_do_form

    cot = _cenario_do_form(session, _form(estado_destino="Rio de Janeiro",
                                          condicao_pagamento="30/60/90",
                                          contribuinte="sim"))
    assert cot.estado_destino == "Rio de Janeiro"
    assert cot.condicao_pagamento == "30/60/90"
    assert cot.contribuinte_icms is True


def test_2_sem_escolha_cai_no_padrao_do_catalogo(session):
    """O padrão continua existindo — o que mudou é ele deixar de ser invisível."""
    from app import pricing_service as ps
    from app.routers.calculadora import _cenario_do_form

    padrao = ps.cenario_padrao_catalogo(session)
    cot = _cenario_do_form(session, {})
    assert cot.estado_destino == padrao.estado_destino
    assert cot.condicao_pagamento == padrao.condicao_pagamento
    assert cot.contribuinte_icms == padrao.contribuinte_icms


def test_3_mudar_o_cenario_muda_o_preco(session):
    """Se o cenário não chegasse ao motor, os três preços seriam iguais — e o campo, decorativo."""
    sp, _ = _preco(session, estado_destino="São Paulo", contribuinte="nao",
                   condicao_pagamento="30")
    rj, _ = _preco(session, estado_destino="Rio de Janeiro", contribuinte="nao",
                   condicao_pagamento="30")
    contrib, _ = _preco(session, estado_destino="Minas Gerais", contribuinte="sim",
                        condicao_pagamento="30")
    assert sp and rj and contrib
    assert rj != sp, "destino não alterou o preço"
    assert contrib != sp, "contribuinte não alterou o preço"


# ===========================================================================
# 4. O cenário é parâmetro de cálculo, não documento
# ===========================================================================
def test_4_simular_nao_cria_cotacao_nem_item_no_banco(session):
    """Cotação de verdade nasce em `/cotacoes`, dentro de uma venda, com cliente e número."""
    antes_cot = len(session.exec(select(Cotacao)).all())
    antes_item = len(session.exec(select(CotacaoItem)).all())

    for uf in ("São Paulo", "Rio de Janeiro", "Bahia"):
        preco, cenario = _preco(session, estado_destino=uf, contribuinte="nao")
        assert preco and cenario.id is None       # transitório: nunca recebeu id
    session.flush()

    assert len(session.exec(select(Cotacao)).all()) == antes_cot
    assert len(session.exec(select(CotacaoItem)).all()) == antes_item


# ===========================================================================
# 5–6. Dentro de uma cotação, quem manda é ela
# ===========================================================================
def test_5_a_tela_so_oferece_cenario_quando_nao_ha_cotacao():
    from app.templating import templates

    fonte = open(templates.env.loader.searchpath[0] + "/calculadora.html", encoding="utf-8").read()
    bloco = fonte.split('id="bloco-cenario"')[0]
    assert "{% if not cotacao %}" in bloco, "o bloco de cenário precisa ser condicional"
    for campo in ("estado_destino", "condicao_pagamento", "contribuinte"):
        assert f'name="{campo}"' in fonte
    # o nome do atributo fiscal não pode aparecer na tela da vendedora — ver o comentário
    # no template e `test_f_html_da_calculadora_da_vendedora_nao_traz_economia`
    assert 'name="contribuinte_icms"' not in fonte


def test_6_com_cotacao_o_cenario_dela_prevalece(session):
    """`calcular` só monta cenário do formulário quando NÃO veio `cotacao_id`."""
    import inspect

    from app.routers import calculadora as rota

    fonte = inspect.getsource(rota.calcular)
    assert "_cenario_do_form" in fonte
    assert "session.get(Cotacao, int(cotacao_id)) if cotacao_id" in fonte


def test_7_lencol_de_baixo_aparece_no_grupo_cama():
    """Errinho de passagem: a família nova caía em 'Outros' por não estar na lista de Cama."""
    from app.templating import templates

    fonte = open(templates.env.loader.searchpath[0] + "/calculadora.html", encoding="utf-8").read()
    cama = fonte.split("{% set cama =")[1].split("%}")[0]
    assert "'Bottom Sheet'" in cama
    assert "'Top Sheet'" in cama and "'Flat Sheet'" in cama

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


# ===========================================================================
# 8–14. Consulta de preço do catálogo — a pergunta de reunião
# ===========================================================================
#: "Quanto sai este lençol para o Rio, a prazo?" antes exigia abrir uma cotação inteira
#: (venda, cliente, item) só para ler um número e jogar fora.
@pytest.fixture
def produto_catalogo(session):
    """Um SKU de catálogo que forma preço — é o que a consulta responde."""
    from app.models import Fornecedor
    from tests.crisis.conftest import produto_ktc_cotado

    fornecedores = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    return produto_ktc_cotado(session, fornecedores, familia="Flat Sheet", thread_count=300,
                              largura_cm=180, comprimento_cm=280, exw_usd=9.8, peso_kg=0.8)


def _consulta(session, produto, desconto=None, **cenario):
    from app import calculadora as calc
    from app.routers.calculadora import _cenario_do_form
    cot = _cenario_do_form(session, _form(**cenario)) if cenario else None
    return calc.resultado_comercial(
        calc.consultar_catalogo(session, produto, cotacao=cot, desconto_pct=desconto))


def test_8_sem_desconto_o_preco_e_a_tabela(session, produto_catalogo):
    """Sem isto a consulta abria no B2B e a vendedora falaria o piso sem ter dado desconto."""
    p = produto_catalogo
    r = _consulta(session, p)
    assert r["preco_proposto"] == r["preco_tabela"]
    assert r["preco_proposto"] > r["preco_b2b"]
    assert r["desconto_vs_tabela_pct"] == pytest.approx(0, abs=1e-9)


def test_9_o_desconto_desce_o_preco_e_a_comissao(session, produto_catalogo):
    """A escada da política: quanto maior o desconto, menor a comissão dela."""
    p = produto_catalogo
    cheio = _consulta(session, p)
    meio = _consulta(session, p, desconto=0.25)
    assert meio["preco_proposto"] < cheio["preco_proposto"]
    assert meio["comissao_estimada_pct"] < cheio["comissao_estimada_pct"]
    assert meio["preco_tabela"] == cheio["preco_tabela"]      # a tabela não se move


def test_10_desconto_abaixo_do_b2b_e_sinalizado(session, produto_catalogo):
    """O B2B é o piso da autonomia: abaixo dele a proposta precisa de aprovação."""
    p = produto_catalogo
    fundo = _consulta(session, p, desconto=0.80)
    assert fundo["preco_proposto"] < fundo["preco_b2b"]


def test_11_o_cenario_muda_o_preco_da_consulta(session, produto_catalogo):
    p = produto_catalogo
    sp = _consulta(session, p, estado_destino="São Paulo", contribuinte="nao")
    rj = _consulta(session, p, estado_destino="Rio de Janeiro", contribuinte="nao")
    assert sp["preco_tabela"] and rj["preco_tabela"]
    assert rj["preco_tabela"] != sp["preco_tabela"]


def test_12_consultar_nao_grava_nada(session, produto_catalogo):
    """É o motor respondendo uma pergunta — não um documento nascendo."""
    from app.models import Cotacao, CotacaoItem, Produto

    antes = (len(session.exec(select(Cotacao)).all()),
             len(session.exec(select(CotacaoItem)).all()),
             len(session.exec(select(Produto)).all()))
    p = produto_catalogo
    for desconto in (None, 0.1, 0.4):
        for uf in ("São Paulo", "Bahia"):
            _consulta(session, p, desconto=desconto, estado_destino=uf, contribuinte="nao")
    session.flush()
    depois = (len(session.exec(select(Cotacao)).all()),
              len(session.exec(select(CotacaoItem)).all()),
              len(session.exec(select(Produto)).all()))
    assert depois == antes


def test_13_a_vendedora_nao_recebe_economia_na_consulta(session, produto_catalogo):
    """Mesmo corte da calculadora: lista de permissão, não remoção."""
    from app import calculadora as calc
    from app.confidencial import encontrar_confidenciais

    p = produto_catalogo
    corpo = calc.resultado_comercial(calc.consultar_catalogo(session, p, desconto_pct=0.2))
    assert encontrar_confidenciais(corpo) == []
    assert set(corpo) <= set(calc.CAMPOS_RESULTADO_COMERCIAL)
    texto = str(corpo).lower()
    for termo in ("custo", "cnet", "exw", "margem", "lucro", "markup", "proteç"):
        assert termo not in texto, termo


def test_14_o_desconto_usa_a_alavanca_canonica_da_politica(session, produto_catalogo):
    """Se a conta divergisse da cotação, ela falaria um número que o sistema não confirma."""
    from app.dinheiro import D
    from app.pricing_engine import preco_por_desconto

    p = produto_catalogo
    r = _consulta(session, p, desconto=0.18)
    esperado = preco_por_desconto(D(r["preco_tabela"]), D("0.18"))
    assert r["preco_proposto"] == pytest.approx(float(esperado), abs=0.005)


def test_15_o_resultado_da_busca_nao_interpola_texto_dentro_do_onclick():
    """O bug que chegou como "não dá para editar o desconto" (28/09/2026).

    O nome do produto ia dentro do `onclick`, num atributo delimitado por aspas duplas. Nome
    de produto tem aspas e acento: o atributo terminava na primeira aspa interna, o `onclick`
    virava lixo e clicar no resultado não fazia nada. Sem produto escolhido, mexer no desconto
    também não fazia nada — e o sintoma que chegou não mencionava a busca.

    A regra que fica: identificador em `data-*`, handler recebendo `this`. Texto de produto
    nunca é interpolado dentro de atributo de evento.
    """
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "js",
                           "calculadora.js"), encoding="utf-8").read()
    bloco = js.split("function buscarParaConsulta")[1].split("function escolherParaConsulta")[0]
    assert 'onclick="escolherParaConsulta(this)"' in bloco
    assert "JSON.stringify" not in bloco, "nome de produto voltou para dentro do onclick"
    assert "data-id=" in bloco and "data-nome=" in bloco


def test_16_o_desconto_recalcula_enquanto_digita():
    """`onchange` só dispara ao sair do campo — numa reunião ninguém clica fora para ver."""
    import os
    html = open(os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                             "calculadora.html"), encoding="utf-8").read()
    campo = html.split('id="consulta-desconto"')[1].split(">")[0]
    assert "oninput=" in campo and "onchange=" not in campo

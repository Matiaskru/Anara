"""Blocos 9 e 10 — origem fiscal × logística, e alterações pendentes de recálculo.

## Bloco 9

`estado_origem` é **origem logística**; a UF de origem **fiscal** vem de outra cadeia
(`uf_origem_fiscal` → fornecedor → premissa versionada). O motor fiscal nunca usou
`estado_origem`, e mesmo assim o formulário o mostrava como "Origem da venda" e o fixava
em "São Paulo", enquanto o modelo trazia "Santa Catarina". Quem preenchia não tinha como
saber qual das duas coisas estava respondendo — e a rota reescrevia o campo em todo
salvamento, porque o default estava na assinatura.

## Bloco 10

Trocar destino, condição de pagamento, contribuinte ou tipo de frete muda o cenário
econômico. Enquanto o servidor não recalcula, os preços na tela pertencem ao cenário
anterior. O aviso e o bloqueio das ações de saída existem para que os dois não pareçam a
mesma coisa.
"""
import itertools
import os
import re

import pytest
from sqlmodel import select

from app import pricing_service as ps
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, Produto, TipoFornecedor
from conftest import RequestFalsa, _novo_usuario
from test_cotacao_e2e import chamar

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEQ = itertools.count(1)



def _item_minimo(cotacao, produto, ordem=0):
    """Item com todas as colunas NOT NULL preenchidas — zerado, mas válido."""
    return CotacaoItem(
        cotacao_id=cotacao.id, produto_id=produto.id, ordem=ordem,
        nome_produto=produto.nome, quantidade=1.0, custo_unitario=10.0,
        preco_base=0.0, preco_negociado=0.0, margem_liquida=0.0,
        faturamento=0.0, custo_total=0.0, lucro=0.0,
        modo_edicao="preco", valor_editado=0.0, confirmation_pending=False)


def _texto(caminho: str) -> str:
    return open(os.path.join(RAIZ, caminho), encoding="utf-8").read()


# ---------------------------------------------------------------------------
# 1-3 — a "Origem" genérica sumiu, e o default não decide mais nada
# ---------------------------------------------------------------------------
def test_nova_cotacao_nao_tem_campo_origem_generico():
    html = _texto("app/templates/cotacao_nova.html")
    assert 'name="estado_origem"' not in html
    assert "Origem da venda" not in html


def test_detalhe_nao_tem_campo_origem_generico():
    html = _texto("app/templates/cotacao_detail.html")
    assert 'name="estado_origem"' not in html
    assert "Origem da venda" not in html


def test_criar_nao_injeta_sao_paulo_silenciosamente():
    """A rota de criação não aceita mais `estado_origem` — nem com default."""
    import inspect
    from app.routers.cotacoes import criar

    assert "estado_origem" not in inspect.signature(criar).parameters


def test_atualizar_nao_reescreve_origem_quando_o_form_nao_manda(session):
    """Sem o campo no formulário, um salvamento não pode trocar o valor guardado."""
    from app.routers.cotacoes import atualizar_cabecalho

    cliente = Cliente(nome=f"Hotel origem {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  estado_origem="Santa Catarina", estado_destino="São Paulo",
                  numero=f"ORIG-{cliente.id}")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    chamar(atualizar_cabecalho, cotacao_id=cot.id, condicao_pagamento="30",
           estado_destino="São Paulo", session=session)
    session.refresh(cot)
    assert cot.estado_origem == "Santa Catarina", "o salvamento reescreveu a origem"


def test_default_do_model_nao_decide_o_fiscal(session):
    """`estado_origem` não participa da resolução fiscal, qualquer que seja seu valor."""
    cliente = Cliente(nome=f"Hotel fiscal {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)

    resultados = []
    for origem in ("Santa Catarina", "São Paulo", None):
        cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                      estado_origem=origem, estado_destino="Rio de Janeiro",
                      numero=f"FISC-{next(_SEQ)}")
        session.add(cot)
        session.commit()
        session.refresh(cot)
        resultados.append(ps.uf_origem_fiscal(session, cot)[0])

    assert len(set(resultados)) == 1, (
        f"a origem logística mudou o fiscal: {resultados}")


# ---------------------------------------------------------------------------
# 4-6 — cada origem vem da sua cadeia
# ---------------------------------------------------------------------------
def test_origem_fiscal_vem_da_cadeia_fiscal(session):
    cliente = Cliente(nome=f"Hotel cadeia {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  uf_origem_fiscal="RJ", estado_origem="Santa Catarina",
                  numero=f"CAD-{next(_SEQ)}")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    uf, fonte = ps.uf_origem_fiscal(session, cot)
    assert uf == "RJ"
    assert "cotação" in fonte.lower()


def test_ktc_itajai_e_logistica_nao_fiscal(session):
    """Itajaí-SC é ponto de entrada da mercadoria. Não prova a origem fiscal da venda."""
    from app import frete_service as fs

    ktc = session.exec(select(Fornecedor)
                       .where(Fornecedor.tipo == TipoFornecedor.importado_ktc)).first()
    if ktc is None or not ktc.origem_logistica_cidade:
        pytest.skip("KTC sem origem logística neste banco de teste")

    produto = session.exec(select(Produto)
                           .where(Produto.fornecedor_id == ktc.id)).first()
    if produto is None:
        pytest.skip("sem produto KTC")

    cidade, uf, _ = fs.origem_logistica_do_produto(session, produto)
    assert (cidade, uf) == (ktc.origem_logistica_cidade, ktc.origem_logistica_uf)
    # e nada disso alimenta a origem fiscal
    assert ktc.origem_logistica_uf != (ktc.uf_origem_fiscal or "")


def test_fornecedor_sem_origem_logistica_fica_explicito(session):
    """Daune e Decor não têm origem cadastrada — a tela mostra 'não cadastrada'."""
    from app.routers.cotacoes import _origens_logisticas

    nacional = Fornecedor(codigo=f"NAC-{next(_SEQ)}", nome="Nacional sem origem",
                          tipo=TipoFornecedor.nacional, pais="Brasil", moeda_custo="BRL")
    session.add(nacional)
    session.commit()
    session.refresh(nacional)
    produto = Produto(sku_key=f"SEM-ORIG-{next(_SEQ)}", nome="Peseira",
                      fornecedor_id=nacional.id, ativo=True, custo_unitario=10.0)
    cliente = Cliente(nome=f"Hotel sem origem {next(_SEQ)}")
    session.add(produto)
    session.add(cliente)
    session.commit()
    session.refresh(produto)
    session.refresh(cliente)

    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  numero=f"SEMORIG-{next(_SEQ)}")
    session.add(cot)
    session.commit()
    session.refresh(cot)
    item = _item_minimo(cot, produto)
    session.add(item)
    session.commit()

    origens = _origens_logisticas(session, cot, [item])
    assert len(origens) == 1
    assert origens[0]["conhecida"] is False


def test_multiplos_grupos_nao_sao_achatados(session):
    """Fornecedores que embarcam de lugares diferentes viram linhas diferentes."""
    from app.routers.cotacoes import _origens_logisticas

    a = Fornecedor(codigo=f"FA-{next(_SEQ)}", nome="Fornecedor A",
                   tipo=TipoFornecedor.nacional, pais="Brasil", moeda_custo="BRL",
                   origem_logistica_cidade="Blumenau", origem_logistica_uf="SC")
    b = Fornecedor(codigo=f"FB-{next(_SEQ)}", nome="Fornecedor B",
                   tipo=TipoFornecedor.nacional, pais="Brasil", moeda_custo="BRL",
                   origem_logistica_cidade="São Paulo", origem_logistica_uf="SP")
    cliente = Cliente(nome=f"Hotel multi {next(_SEQ)}")
    session.add_all([a, b, cliente])
    session.commit()
    session.refresh(a)
    session.refresh(b)
    session.refresh(cliente)

    pa = Produto(sku_key=f"PA-{next(_SEQ)}", nome="Item A", fornecedor_id=a.id,
                 ativo=True, custo_unitario=10.0)
    pb = Produto(sku_key=f"PB-{next(_SEQ)}", nome="Item B", fornecedor_id=b.id,
                 ativo=True, custo_unitario=10.0)
    session.add_all([pa, pb])
    session.commit()
    session.refresh(pa)
    session.refresh(pb)

    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  numero=f"MULTI-{next(_SEQ)}")
    session.add(cot)
    session.commit()
    session.refresh(cot)
    itens = [_item_minimo(cot, p, n) for n, p in enumerate((pa, pb))]
    session.add_all(itens)
    session.commit()

    origens = _origens_logisticas(session, cot, itens)
    assert len(origens) == 2, "duas origens viraram uma só"
    assert {o["uf"] for o in origens} == {"SC", "SP"}


# ---------------------------------------------------------------------------
# Bloco 10 — alterações pendentes
# ---------------------------------------------------------------------------
def test_campos_materiais_estao_marcados_no_formulario():
    """Os campos que exigem recálculo carregam `data-material` — é o que o aviso vigia."""
    html = _texto("app/templates/cotacao_detail.html")
    bloco = html.split('action="/cotacoes/{{ cotacao.id }}/atualizar"')[1].split("</form>")[0]
    for campo in ("estado_destino", "contribuinte_icms", "freight_type",
                  "condicao_pagamento"):
        achado = re.search(rf'name="{campo}"[^>]*data-material', bloco) \
            or re.search(rf'data-material[^>]*name="{campo}"', bloco)
        assert achado, f"'{campo}' muda o preço e não está marcado como material"


def test_aviso_de_pendencia_existe_e_comeca_escondido():
    html = _texto("app/templates/cotacao_detail.html")
    assert 'id="pendente"' in html
    assert "Há alterações ainda não aplicadas ao cálculo." in html
    bloco = html.split('id="pendente"')[1][:200]
    assert "hidden" in bloco, "o aviso não pode aparecer antes de haver alteração"


def test_botao_diz_o_que_faz():
    html = _texto("app/templates/cotacao_detail.html")
    assert "Atualizar cenário e recalcular" in html
    assert "Salvar dados da cotação" not in html, "o rótulo antigo não dizia que recalculava"


def test_pdf_fica_inerte_enquanto_ha_pendencia():
    """Gerar PDF com alteração pendente sairia com números do cenário anterior."""
    html = _texto("app/templates/cotacao_detail.html")
    assert "body.cenario-pendente" in html
    assert "pointer-events:none" in html


def test_estado_pendente_e_separado_do_aviso_de_premissa_global():
    """§8 — 'você mudou e não aplicou' não é 'o mundo mudou'."""
    html = _texto("app/templates/cotacao_detail.html")
    assert "Há alterações ainda não aplicadas ao cálculo." in html
    assert "Existem premissas mais recentes disponíveis" in html
    assert html.index("Existem premissas mais recentes") < html.index("id=\"pendente\"")


def test_recalculo_confirma_com_mensagem_humana(session):
    """Mudança material redireciona com o aviso de cenário atualizado."""
    from app.routers.cotacoes import atualizar_cabecalho

    cliente = Cliente(nome=f"Hotel recalc {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  estado_destino="São Paulo", numero=f"RECALC-{next(_SEQ)}")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    resposta = chamar(atualizar_cabecalho, cotacao_id=cot.id,
                      condicao_pagamento="30", estado_destino="Rio de Janeiro",
                      session=session)
    assert "cenario=atualizado" in resposta.headers["location"]


def test_salvar_sem_mudanca_material_nao_diz_que_recalculou(session):
    from app.routers.cotacoes import atualizar_cabecalho

    cliente = Cliente(nome=f"Hotel sem mudanca {next(_SEQ)}")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    cot = Cotacao(cliente_id=cliente.id, condicao_pagamento="30",
                  estado_destino="São Paulo", numero=f"NOCHG-{next(_SEQ)}")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    resposta = chamar(atualizar_cabecalho, cotacao_id=cot.id,
                      condicao_pagamento="30", estado_destino="São Paulo",
                      observacoes="só um comentário", session=session)
    assert "salvo=1" in resposta.headers["location"]
    assert "cenario=atualizado" not in resposta.headers["location"]

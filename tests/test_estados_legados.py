"""P0 — a interface moderna não cria estados legados.

`fechada`, `pedido` e `perdida` são estados herdados do sistema anterior. Eles existem no
banco, em cotações antigas, e precisam continuar legíveis: apagá-los reescreveria história.

O que não pode existir é caminho **novo** para dentro deles. Havia três, e todos foram usados
no primeiro dia de uso real:

* `POST /cotacoes/{id}/aceite` com `virar_pedido=sim` gravava `pedido` direto;
* `POST /cotacoes/{id}/status` aceitava qualquer valor de `StatusCotacao`, sem consultar o
  workflow;
* a tela de detalhe renderizava `StatusCotacao` inteiro como botões, os três legados junto.

O resultado era uma cotação num estado do qual `exigir_transicao` recusa sair — sem
aprovação, sem emissão, sem snapshot e sem volta. Estes testes existem para que isso não
volte por outra porta.
"""
import pytest
import legado
from sqlmodel import select

from app import workflow as wf
from app.models import Cotacao, StatusCotacao


LEGADOS = ("fechada", "pedido", "perdida")


# ---------------------------------------------------------------------------
# O workflow, sozinho
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("legado", LEGADOS)
def test_nenhuma_transicao_leva_a_estado_legado(legado):
    """Nenhum estado do workflow tem um legado como destino."""
    for origem, destinos in wf.TRANSICOES.items():
        assert legado not in destinos, f"'{origem}' → '{legado}' não deveria existir"


@pytest.mark.parametrize("legado", LEGADOS)
def test_pode_transicionar_recusa_destino_legado(legado):
    for origem in wf.ESTADOS_DO_WORKFLOW:
        assert not wf.pode_transicionar(origem, legado)


@pytest.mark.parametrize("legado", LEGADOS)
def test_exigir_transicao_recusa_sair_de_estado_legado(legado):
    """Ler o histórico é permitido; movê-lo, não."""
    with pytest.raises(wf.TransicaoInvalida):
        wf.exigir_transicao(legado, wf.DRAFT)


def test_proximos_estados_nunca_oferece_legado():
    for origem in wf.ESTADOS_DO_WORKFLOW:
        oferecidos = wf.proximos_estados(origem)
        assert not set(oferecidos) & set(LEGADOS)
        # e tudo que é oferecido é de fato alcançável
        for destino in oferecidos:
            assert wf.pode_transicionar(origem, destino)


@pytest.mark.parametrize("legado", LEGADOS)
def test_estado_legado_nao_oferece_saida(legado):
    """A tela não mostra botão nenhum — não há semântica registrada para inventar uma."""
    assert wf.proximos_estados(legado) == ()


def test_rascunho_oferece_o_caminho_moderno():
    oferecidos = wf.proximos_estados(wf.DRAFT)
    assert wf.PENDING_APPROVAL in oferecidos
    assert wf.ISSUED in oferecidos
    assert wf.CANCELLED in oferecidos


# ---------------------------------------------------------------------------
# As rotas
# ---------------------------------------------------------------------------
def test_rota_de_aceite_nao_aceita_mais_virar_pedido():
    """O parâmetro sumiu da assinatura, não foi só escondido no template."""
    import inspect
    from app.routers import cotacoes

    assinatura = inspect.signature(cotacoes.registrar_aceite)
    assert "virar_pedido" not in assinatura.parameters


def test_nenhuma_rota_grava_estado_legado():
    """Varredura do código dos routers atrás de escrita direta de estado legado."""
    import os
    import re

    raiz = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "routers")
    padrao = re.compile(r"status\s*=\s*StatusCotacao\.(fechada|perdida|pedido)\b")
    achados = []
    for nome in os.listdir(raiz):
        if not nome.endswith(".py"):
            continue
        caminho = os.path.join(raiz, nome)
        with open(caminho, encoding="utf-8") as fh:
            for n, linha in enumerate(fh, 1):
                if padrao.search(linha):
                    achados.append(f"{nome}:{n}: {linha.strip()}")
    assert achados == [], "rota grava estado legado diretamente:\n" + "\n".join(achados)


def test_template_de_detalhe_nao_tem_botao_de_virar_pedido():
    import os

    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app", "templates", "cotacao_detail.html")
    html = open(caminho, encoding="utf-8").read()
    assert "virar_pedido" not in html
    assert "Salvar e virar pedido" not in html


# ---------------------------------------------------------------------------
# O histórico continua legível
# ---------------------------------------------------------------------------
def test_estados_legados_continuam_existindo_no_enum():
    """Não foram apagados: cotação antiga precisa continuar carregando do banco."""
    valores = {s.value for s in StatusCotacao}
    for legado in LEGADOS:
        assert legado in valores


def test_estados_legados_tem_rotulo_humano():
    from app import rotulos
    for legado in LEGADOS:
        rotulo = rotulos.cotacao(legado)
        assert rotulo != legado, f"'{legado}' aparece cru na tela"
        assert "antigo" in rotulo.lower()


def test_cotacao_em_estado_legado_carrega_e_e_legivel(session):
    """Uma cotação herdada continua abrindo, com rótulo em português e sem ações."""
    from app.models import Cliente

    cliente = Cliente(nome="Hotel Histórico")
    session.add(cliente)
    session.commit()
    session.refresh(cliente)

    antiga = Cotacao(numero="ANARA-2025-0001", cliente_id=cliente.id,
                     status=StatusCotacao.pedido)
    session.add(antiga)
    session.commit()
    session.refresh(antiga)

    from app import rotulos
    lida = session.get(Cotacao, antiga.id)
    assert lida.status.value == "pedido"
    assert rotulos.cotacao(lida.status) == "Pedido (registro antigo)"
    assert wf.proximos_estados(lida.status.value) == ()


# ---------------------------------------------------------------------------
# A cotação 19, no banco operacional
# ---------------------------------------------------------------------------
def test_cotacao_19_continua_preservada():
    """Arquivada, não apagada — e com os 3 itens e o status originais intactos.

    Ela é a evidência do bypass: foi criada pelo uso real, caiu em `pedido` por causa do
    botão, e foi arquivada em vez de removida justamente para o caso não se perder.
    """
    import os
    from sqlalchemy import create_engine
    from sqlmodel import Session as S
    from app.models import CotacaoItem

    caminho = legado.caminho_banco_real()
    if not os.path.exists(caminho):
        pytest.skip("banco operacional ausente")

    eng = create_engine(f"sqlite:///file:{caminho}?mode=ro&uri=true")
    with S(eng) as prod:
        c = prod.exec(select(Cotacao).where(Cotacao.numero == "ANARA-2026-0019")).first()
        if c is None:
            pytest.skip("ANARA-2026-0019 não está neste banco")
        assert c.status.value == "pedido", "o estado original é a evidência; não normalizar"
        assert c.arquivada_em is not None, "deveria estar arquivada"
        itens = prod.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == c.id)).all()
        assert len(itens) == 3
        assert c.observacoes_pedido == "teste teste teste"

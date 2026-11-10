"""Contato da cotação em branco (10/11/2026).

A cotação guarda o contato quando nasce. Quem cadastrou o contato do hotel depois — ou criou a
revisão de uma cotação que já nasceu sem ele — ficava com o campo vazio, e a proposta saía com
"—" no lugar do nome (Comandatuba R1–R3, Einstein R1). O contato principal do cliente no CRM
passa a completar o campo VAZIO na revisão, na tela, no PDF do rascunho e na emissão. O que a
vendedora digitou nunca é trocado, e cliente sem contato continua sem contato.
"""
from itertools import count

import pytest

from app import crm_relacionamento as rel
from app import crm_service as crm
from app import workflow_service as ws
from app.models import Cliente, Cotacao, StatusCotacao
from conftest import RequestFalsa, _novo_usuario
from tests.crisis.conftest import add_item, chamar, fornecedores, nova_cotacao, produto_nacional  # noqa: F401

_SEQ = count(1)


@pytest.fixture
def ator():
    # Ator padrão da suíte (id fixo): gravar usuário novo aqui deslocaria os ids que outros
    # módulos criam explicitamente.
    return _novo_usuario("OWNER")


@pytest.fixture
def cliente(session):
    c = Cliente(nome=f"Hotel Contato {next(_SEQ)}", cidade_uf="São Paulo", finalidade="REVENDA")
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def _contato(session, ator, cliente, nome, **kw):
    c = rel.criar_contato(session, ator=ator, cliente_id=cliente.id, nome=nome, **kw)
    session.commit()
    return c


def test_sem_contato_no_cadastro_nada_e_inventado(session, cliente):
    cot = nova_cotacao(session, cliente_id=cliente.id)
    assert crm.contato_sugerido(session, cliente.id) == (None, None)
    assert ws.completar_contato(session, cot) is False
    assert cot.contato_nome is None and cot.departamento_contato is None


def test_completa_com_o_principal_e_nunca_troca_o_digitado(session, ator, cliente):
    cot = nova_cotacao(session, cliente_id=cliente.id)
    _contato(session, ator, cliente, "Mariana Rodrigues", cargo="Compras")
    _contato(session, ator, cliente, "Nara Favaretto", cargo="Governanta", principal=True)
    assert ws.completar_contato(session, cot) is True
    assert (cot.contato_nome, cot.departamento_contato) == ("Nara Favaretto", "Governanta")

    digitado = nova_cotacao(session, cliente_id=cliente.id, contato_nome="Victor Castro",
                            departamento_contato="Diretoria")
    assert ws.completar_contato(session, digitado) is False
    assert (digitado.contato_nome, digitado.departamento_contato) == ("Victor Castro", "Diretoria")


def test_revisao_de_cotacao_sem_contato_puxa_o_contato_do_cliente(session, ator, cliente):
    cot = nova_cotacao(session, cliente_id=cliente.id, status=StatusCotacao.emitida.value)
    _contato(session, ator, cliente, "Nara Favaretto", cargo="Governanta")   # cadastrado depois
    rev = ws.criar_revisao(session, cot, ator=ator)
    session.commit()
    assert (rev.contato_nome, rev.departamento_contato) == ("Nara Favaretto", "Governanta")
    session.refresh(cot)
    assert cot.contato_nome is None          # a emitida fica como saiu


def test_revisao_mantem_o_contato_que_ja_estava(session, ator, cliente):
    cot = nova_cotacao(session, cliente_id=cliente.id, status=StatusCotacao.emitida.value,
                       contato_nome="Victor Castro")
    _contato(session, ator, cliente, "Nara Favaretto", principal=True)
    rev = ws.criar_revisao(session, cot, ator=ator)
    session.commit()
    assert rev.contato_nome == "Victor Castro"


def test_tela_do_rascunho_mostra_o_contato_do_cliente(session, ator, cliente, fornecedores):
    from app.routers.cotacoes import detalhe
    cot = nova_cotacao(session, cliente_id=cliente.id)
    add_item(session, cot, produto_nacional(session, fornecedores), ator=ator)
    _contato(session, ator, cliente, "Nara Favaretto", cargo="Governanta")
    r = chamar(detalhe, RequestFalsa(ator), cotacao_id=cot.id, session=session)
    html = bytes(r.body).decode()
    assert 'name="contato_nome" value="Nara Favaretto"' in html
    assert 'name="departamento_contato" value="Governanta"' in html
    session.refresh(cot)
    assert cot.contato_nome is None          # abrir a tela não grava nada


def test_pdf_do_rascunho_e_emissao_gravam_o_contato(session, ator, cliente, fornecedores):
    from app.routers.cotacoes import gerar_pdf
    cot = nova_cotacao(session, cliente_id=cliente.id)
    add_item(session, cot, produto_nacional(session, fornecedores), ator=ator)
    _contato(session, ator, cliente, "Nara Favaretto", cargo="Governanta")

    r = chamar(gerar_pdf, RequestFalsa(ator), cotacao_id=cot.id, session=session)
    assert getattr(r, "media_type", "") == "application/pdf"
    session.refresh(cot)
    assert cot.contato_nome == "Nara Favaretto"

    outra = nova_cotacao(session, cliente_id=cliente.id)
    add_item(session, outra, produto_nacional(session, fornecedores), ator=ator)
    c = session.get(Cotacao, outra.id)
    ws.emitir(session, c, ator=ator)
    session.commit()
    session.refresh(c)
    assert c.status == StatusCotacao.emitida.value
    assert (c.contato_nome, c.departamento_contato) == ("Nara Favaretto", "Governanta")

"""Destino da cotação a partir do "Cidade/UF" do cliente (12/11/2026).

Produção, 12/11: os clientes da Ortobom e do Grupo Wish tinham Cidade/UF = "São Paulo/sP" e "São Paulo/SP".
A cotação herdava o texto cru, que não casava com nenhum estado → destino "Definir depois" → "UF de destino
não informada" → nenhum item com preço (ANARA-2026-0062 e 0063), e as vendedoras pararam.
Agora o estado é reconhecido no texto (nome, sigla, cidade + sigla); o que não dá para saber com certeza
continua None — nada é chutado.
"""
from itertools import count

import pytest
from sqlmodel import select

from app import crm_service as crm
from app.fiscal_rules import nome_do_estado, normalizar_uf
from app.models import Cotacao, EstadoFiscal, Usuario
from conftest import RequestFalsa
from tests.crisis.conftest import add_item, chamar, nova_cotacao, produto_nacional, salvar_cabecalho

_SEQ = count(1)


@pytest.fixture
def estados(session):
    return session.exec(select(EstadoFiscal)).all()


@pytest.fixture
def dona(session):
    # um usuário por módulo (ids fixos de outros testes: ver test_cancelar_acompanhamento_2026_10_19)
    u = session.exec(select(Usuario).where(Usuario.email == "destino-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="destino-owner@anara.test", nome="Owner Destino", senha_hash="h", papel="OWNER",
                    ativo=True, can_approve_quotes=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def fornecedores(session):
    from app.models import Fornecedor
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.mark.parametrize("texto,estado,uf", [
    ("São Paulo/SP", "São Paulo", "SP"), ("São Paulo/sP", "São Paulo", "SP"),
    ("São Paulo", "São Paulo", "SP"), ("sao paulo", "São Paulo", "SP"), ("  SÃO   PAULO ", "São Paulo", "SP"),
    ("SP", "São Paulo", "SP"), ("sp", "São Paulo", "SP"),
    ("Campo Grande - MS", "Mato Grosso do Sul", "MS"), ("Campo Grande – MS", "Mato Grosso do Sul", "MS"),
    ("Recife (PE)", "Pernambuco", "PE"), ("Curitiba, PR", "Paraná", "PR"), ("Vila Velha ES", "Espírito Santo", "ES"),
    ("Rio de Janeiro", "Rio de Janeiro", "RJ"), ("Rio de Janeiro/RJ", "Rio de Janeiro", "RJ"),
    ("Brasília/DF", "Distrito Federal", "DF"), ("Mato Grosso do Sul", "Mato Grosso do Sul", "MS"),
])
def test_estado_reconhecido_no_texto_do_cadastro(estados, texto, estado, uf):
    assert nome_do_estado(estados, texto) == estado
    assert normalizar_uf(estados, texto) == uf


@pytest.mark.parametrize("texto", [
    None, "", "   ", "Campinas",              # cidade sem sigla: não se adivinha o estado
    "XX", "Cidade/ZZ",                        # sigla que não existe
    "São Paulo/RJ",                           # contraditório: nome de um estado com a sigla de outro
    "Santos sp",                              # sigla só por espaço tem de vir em maiúsculas
    "Embu-Guaçu",                             # hífen do nome da cidade não é separador de UF
])
def test_sem_certeza_nao_chuta(estados, texto):
    assert nome_do_estado(estados, texto) is None and normalizar_uf(estados, texto) is None


def test_cotacao_nova_herda_o_estado_e_forma_preco(session, dona, fornecedores):
    from app.routers.cotacoes import criar
    cliente = crm.criar_cliente(session, ator=dona, nome=f"Hotel Destino {next(_SEQ)}", cidade_uf="São Paulo/SP",
                                finalidade="REVENDA")
    session.commit()
    r = chamar(criar, RequestFalsa(dona), cliente_id=cliente.id, nova_venda="Enxoval 2027", session=session)
    cot = session.get(Cotacao, int(r.headers["location"].rsplit("/", 1)[1]))
    assert cot.estado_destino == "São Paulo"
    item = add_item(session, cot, produto_nacional(session, fornecedores), ator=dona)
    assert item.preco_recomendado and item.preco_recomendado > 0           # B2B formado


def test_cotacao_pela_venda_tambem_herda_o_estado(session, dona):
    from app.routers import crm as rcrm
    from app.routers.cotacoes import criar
    cliente = crm.criar_cliente(session, ator=dona, nome=f"Hotel Destino {next(_SEQ)}", cidade_uf="Campo Grande - MS")
    session.commit()
    r = chamar(criar, RequestFalsa(dona), cliente_id=cliente.id, nova_venda="Enxoval MS", session=session)
    op_id = session.get(Cotacao, int(r.headers["location"].rsplit("/", 1)[1])).oportunidade_id
    r2 = chamar(rcrm.nova_cotacao, RequestFalsa(dona), oportunidade_id=op_id, session=session)
    assert session.get(Cotacao, int(r2.headers["location"].rsplit("/", 1)[1])).estado_destino == "Mato Grosso do Sul"


def test_cidade_sem_estado_nao_herda_nada_e_o_cadastro_avisa(session, dona):
    from app.routers.cotacoes import criar
    cliente = crm.criar_cliente(session, ator=dona, nome=f"Hotel Destino {next(_SEQ)}", cidade_uf="Campinas",
                                finalidade="REVENDA")
    session.commit()
    r = chamar(criar, RequestFalsa(dona), cliente_id=cliente.id, nova_venda="Enxoval", session=session)
    assert session.get(Cotacao, int(r.headers["location"].rsplit("/", 1)[1])).estado_destino is None
    assert any("não indica o estado" in f for f in crm.dados_fiscais_faltando(cliente, session))
    ok = crm.criar_cliente(session, ator=dona, nome=f"Hotel Destino {next(_SEQ)}", cidade_uf="São Paulo/SP",
                           finalidade="REVENDA")
    assert crm.dados_fiscais_faltando(ok, session) == []


def test_cotacao_antiga_com_o_texto_cru_forma_preco_e_a_tela_marca_o_estado(session, dona, fornecedores):
    """As cotações 0062/0063 gravaram "São Paulo/sP": o motor agora resolve, e a tela marca São Paulo —
    "Atualizar cenário e recalcular" grava o nome do estado e refaz os preços."""
    from app.routers.cotacoes import detalhe
    cot = nova_cotacao(session, estado_destino="São Paulo/sP")
    item = add_item(session, cot, produto_nacional(session, fornecedores), ator=dona)
    assert item.preco_recomendado and item.preco_recomendado > 0
    html = bytes(chamar(detalhe, RequestFalsa(dona), cotacao_id=cot.id, session=session).body).decode()
    assert '<option value="São Paulo" selected>' in html
    salvar_cabecalho(session, cot, estado_destino="São Paulo")
    session.expire_all()
    assert session.get(Cotacao, cot.id).estado_destino == "São Paulo"


def test_item_que_entrou_sem_preco_ganha_preco_ao_atualizar_o_cenario(session, dona, fornecedores):
    """O caso exato de 0062/0063: o item entrou sem preço (destino não reconhecido) e o destino gravado é o
    texto do cadastro. Depois da correção, a tela marca São Paulo e "Atualizar cenário e recalcular" forma o
    preço — sem mexer em banco."""
    from app import workflow_service as ws
    cot = nova_cotacao(session, estado_destino="Campinas")              # não reconhecível: item sem preço
    item = add_item(session, cot, produto_nacional(session, fornecedores), ator=dona)
    assert not item.preco_recomendado
    cot = session.get(Cotacao, cot.id)
    cot.estado_destino = "São Paulo/sP"                                 # o que 0062 tem gravado
    session.add(cot)
    session.commit()
    salvar_cabecalho(session, cot, estado_destino="São Paulo")          # o seletor já vem marcado assim
    session.expire_all()
    depois = ws.itens_de(session, cot.id)[0]
    assert depois.preco_recomendado and depois.preco_recomendado > 0

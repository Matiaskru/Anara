"""Endereço da empresa no cadastro do cliente e no relatório de visita (10/11/2026, migration 0033).

Pedido do Matias: "falta o endereço da empresa no relatório de visita". O cadastro só tinha cidade/UF
(destino fiscal). `cliente.endereco` é texto livre, informativo: entra no cadastro (novo e editar), na
ficha do cliente e no relatório da visita. Não mexe em cotação nem em cálculo.
"""
import os
from datetime import date
from itertools import count

import pytest
from sqlmodel import select

from app import crm_relacionamento as rel
from app import crm_service as crm
from app.models import AuditLog, Cliente, Usuario
from app.routers import clientes as rc
from app.routers import relacionamento as rr
from conftest import RequestFalsa
from tests.crisis.conftest import chamar

_SEQ = count(1)
ENDERECO = "Av. Atlântica, 1702 — Copacabana — CEP 22021-001"


def _usuario(session, papel):
    # um usuário por papel no módulo (ids fixos de outros testes: ver test_cancelar_acompanhamento_2026_10_19)
    email = f"endereco-{papel.lower()}@anara.test"
    u = session.exec(select(Usuario).where(Usuario.email == email)).first()
    if u is None:
        u = Usuario(email=email, nome=f"{papel.title()} Endereço", senha_hash="h", papel=papel, ativo=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def vend(session):
    return _usuario(session, "VENDEDOR_INTERNO")


def _html(resposta):
    return bytes(resposta.body).decode()


def test_migration_0033_e_a_cabeca_e_so_acrescenta_a_coluna():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    texto = open(os.path.join(raiz, "alembic", "versions", "0033_endereco_cliente.py"), encoding="utf-8").read()
    assert 'revision = "0033"' in texto and 'down_revision = "0032"' in texto
    assert 'op.add_column("cliente", sa.Column("endereco", sa.String(), nullable=True))' in texto
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config(os.path.join(raiz, "alembic.ini"))).get_heads() == ["0033"]


def test_novo_cliente_grava_o_endereco(session, vend):
    nome = f"Hotel Endereço {next(_SEQ)}"
    resp = chamar(rc.criar, RequestFalsa(vend), nome=nome, cnpj_cpf="", cidade_uf="Rio de Janeiro",
                  telefone="", email="", endereco=f"  {ENDERECO}  ", voltar_para="/clientes", session=session)
    cliente = session.exec(select(Cliente).where(Cliente.nome == nome)).one()
    assert resp.status_code == 303 and cliente.endereco == ENDERECO
    pagina = _html(chamar(rc.detalhe, RequestFalsa(vend), cliente_id=cliente.id, session=session))
    assert ENDERECO in pagina and 'name="endereco"' in pagina


def test_editar_cadastro_troca_e_apaga_o_endereco_com_auditoria(session, vend):
    cliente = crm.criar_cliente(session, ator=vend, nome=f"Hotel Endereço {next(_SEQ)}", cidade_uf="São Paulo")
    session.commit()
    base = dict(nome=cliente.nome, cnpj_cpf="", cidade_uf="São Paulo", finalidade="", telefone="", email="",
                contato_nome="")
    chamar(rc.editar, RequestFalsa(vend), cliente_id=cliente.id, endereco=ENDERECO, session=session, **base)
    session.refresh(cliente)
    assert cliente.endereco == ENDERECO
    log = session.exec(select(AuditLog).where(AuditLog.acao == "UPDATE_CLIENT",
                                              AuditLog.entidade_id == cliente.id)).all()[-1]
    assert ENDERECO in log.versao_nova and "'endereco': None" in log.versao_anterior
    chamar(rc.editar, RequestFalsa(vend), cliente_id=cliente.id, endereco="   ", session=session, **base)
    session.refresh(cliente)
    assert cliente.endereco is None                     # campo vazio limpa, não grava espaço


def test_relatorio_de_visita_mostra_o_endereco_da_empresa(session, vend):
    cliente = crm.criar_cliente(session, ator=vend, nome=f"Hotel Endereço {next(_SEQ)}", cidade_uf="Rio de Janeiro",
                                endereco=ENDERECO)
    session.commit()
    v = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 11, 10),
                         relatorio="Apresentamos a linha 600 fios.")
    session.commit()
    pagina = _html(chamar(rr.ver_visita, RequestFalsa(vend), visita_id=v.id, session=session))
    assert "<h3>Empresa</h3>" in pagina and ENDERECO in pagina and "Rio de Janeiro" in pagina
    assert "Endereço não cadastrado" not in pagina


def test_relatorio_de_visita_sem_endereco_avisa_onde_preencher(session, vend):
    cliente = crm.criar_cliente(session, ator=vend, nome=f"Hotel Endereço {next(_SEQ)}", cidade_uf="Bahia")
    session.commit()
    v = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 11, 10), relatorio="Visita.")
    session.commit()
    pagina = _html(chamar(rr.ver_visita, RequestFalsa(vend), visita_id=v.id, session=session))
    assert "Endereço não cadastrado" in pagina and f'href="/clientes/{cliente.id}"' in pagina

"""Cancelar acompanhamento (19/10/2026, migration 0032).

Cancelado = encerrado SEM ter sido feito: sai de toda lista de pendentes e de todo contador de
atrasados, não conta como concluído, não some do histórico. Pode cancelar quem pode concluir.
"""
from datetime import date, datetime
from itertools import count

import pytest
from sqlmodel import select
from starlette.datastructures import FormData

from app import crm_relacionamento as rel
from app import crm_service as crm
from app.crm_service import DadoInvalido
from app.models import AtividadeComercial, AuditLog, Usuario
from conftest import RequestFalsa
from tests.crisis.conftest import RequestComForm, chamar

_SEQ = count(1)


def _usuario(session, papel):
    # Um usuário por papel no módulo inteiro (o banco da suíte é um só): criar um por teste
    # empurrava o autoincremento para os ids fixos que outros testes gravam (77/78 em test_workflow_sessao6).
    email = f"cancelar-{papel.lower()}@anara.test"
    u = session.exec(select(Usuario).where(Usuario.email == email)).first()
    if u is None:
        u = Usuario(email=email, nome=f"{papel.title()} Cancelamento", senha_hash="h", papel=papel, ativo=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def dona(session):
    return _usuario(session, "OWNER")


@pytest.fixture
def vend(session):
    return _usuario(session, "VENDEDOR_INTERNO")


@pytest.fixture
def outra(session):
    return _usuario(session, "VENDEDOR_COMISSIONADO")


@pytest.fixture
def cliente(session, vend):
    c = crm.criar_cliente(session, ator=vend, nome=f"Hotel Cancelamento {next(_SEQ)}")
    session.commit()
    return c


def _acomp(session, ator, cliente, descricao="Ligar para a governanta", d=date(2020, 1, 1)):
    a = rel.criar_acompanhamento(session, ator=ator, cliente_id=cliente.id, descricao=descricao, data_prevista=d)
    session.commit()
    return a


def html(resposta):
    return bytes(resposta.body).decode()


def test_cancelar_tira_de_pendentes_e_de_atrasados_sem_contar_como_concluido(session, vend, cliente):
    a = _acomp(session, vend, cliente)                        # vencido em 2020: está atrasado
    assert crm.esta_atrasada(a) and a.id in [x.id for x in crm.atividades_de(session, cliente_id=cliente.id, pendentes=True)]
    rel.cancelar_acompanhamento(session, ator=vend, atividade=a, motivo="criado por engano")
    session.commit()
    a = session.get(AtividadeComercial, a.id)
    assert a.cancelada_em and a.cancelada_por == vend.email and a.motivo_cancelamento == "criado por engano"
    assert a.concluida_em is None                              # cancelado não é feito
    assert not crm.esta_atrasada(a)
    assert a.id not in [x.id for x in crm.atividades_de(session, cliente_id=cliente.id, pendentes=True)]
    assert a.id in [x.id for x in crm.atividades_de(session, cliente_id=cliente.id, pendentes=False)]
    p = rel.painel_acompanhamentos(session, cliente_id=cliente.id)
    assert a.id not in [x.id for x in p.atrasados + p.hoje + p.proximos + p.sem_data]
    assert [x.id for x in p.concluidos] == [a.id]              # aparece em "concluídos e cancelados"
    assert rel.resumo_relacionamento(session, cliente.id)["proximo_acompanhamento"] is None
    log = session.exec(select(AuditLog).where(AuditLog.acao == "CANCEL_ACTIVITY", AuditLog.entidade_id == a.id)).one()
    assert log.motivo == "criado por engano"


def test_cancelado_some_dos_contadores_do_dashboard(session, vend, cliente):
    from app import metrics_service as ms
    tudo = ms.Periodo()
    antes = ms.painel_comercial(session, tudo, responsavel_id=vend.id).atividades_atrasadas
    a = _acomp(session, vend, cliente)
    assert ms.painel_comercial(session, tudo, responsavel_id=vend.id).atividades_atrasadas == antes + 1
    rel.cancelar_acompanhamento(session, ator=vend, atividade=a)
    session.commit()
    assert ms.painel_comercial(session, tudo, responsavel_id=vend.id).atividades_atrasadas == antes


def test_so_responsavel_criador_ou_admin_cancela(session, vend, outra, dona, cliente):
    a = _acomp(session, vend, cliente)
    with pytest.raises(DadoInvalido) as e:
        rel.cancelar_acompanhamento(session, ator=outra, atividade=a)
    assert e.value.status_code == 403 and session.get(AtividadeComercial, a.id).cancelada_em is None
    rel.cancelar_acompanhamento(session, ator=dona, atividade=a)
    session.commit()
    assert session.get(AtividadeComercial, a.id).cancelada_por == dona.email


def test_concluido_nao_cancela_e_cancelado_nao_conclui_reagenda_nem_edita(session, vend, cliente):
    feito = _acomp(session, vend, cliente, "Feito")
    rel.concluir_acompanhamento(session, ator=vend, atividade=feito)
    session.commit()
    with pytest.raises(DadoInvalido) as e:
        rel.cancelar_acompanhamento(session, ator=vend, atividade=feito)
    assert e.value.status_code == 409 and feito.cancelada_em is None

    c = _acomp(session, vend, cliente, "Cancelado")
    rel.cancelar_acompanhamento(session, ator=vend, atividade=c)
    session.commit()
    for acao in (lambda: rel.concluir_acompanhamento(session, ator=vend, atividade=c),
                 lambda: rel.reagendar_acompanhamento(session, ator=vend, atividade=c, data_prevista=date(2027, 1, 1)),
                 lambda: rel.editar_acompanhamento(session, ator=vend, atividade=c, descricao="outro texto")):
        with pytest.raises(DadoInvalido) as e:
            acao()
        assert e.value.status_code == 409
    c = session.get(AtividadeComercial, c.id)
    assert c.concluida_em is None and c.titulo == "Cancelado"
    # cancelar de novo é inofensivo (duplo clique)
    rel.cancelar_acompanhamento(session, ator=vend, atividade=c)


def test_rota_antiga_de_concluir_recusa_cancelado(session, vend, cliente):
    from app.routers.crm import concluir
    a = _acomp(session, vend, cliente)
    rel.cancelar_acompanhamento(session, ator=vend, atividade=a)
    session.commit()
    with pytest.raises(DadoInvalido) as e:
        chamar(concluir, RequestFalsa(vend), atividade_id=a.id, session=session)
    assert e.value.status_code == 409 and session.get(AtividadeComercial, a.id).concluida_em is None


def test_rota_cancelar_redireciona_e_a_pagina_mostra_cancelado(session, vend, cliente):
    from app.routers import relacionamento as r
    a = _acomp(session, vend, cliente, "Tarefa cancelável zebra")
    pagina = html(chamar(r.acompanhamentos, RequestFalsa(vend), responsavel="eu", session=session))
    assert f'/acompanhamentos/{a.id}/cancelar' in pagina and "Confirmar cancelamento" in pagina
    form = FormData([("voltar", f"/clientes/{cliente.id}#acompanhamentos"), ("motivo", "cliente fechou")])
    resp = chamar(r.cancelar_acompanhamento, RequestComForm(vend, form), atividade_id=a.id, session=session)
    assert resp.status_code == 303 and resp.headers["location"] == f"/clientes/{cliente.id}#acompanhamentos"
    pagina = html(chamar(r.acompanhamentos, RequestFalsa(vend), responsavel="eu", session=session))
    assert "cancelado em" in pagina and "cliente fechou" in pagina
    assert f'/acompanhamentos/{a.id}/cancelar' not in pagina   # sem botão no que já foi cancelado


def test_rota_cancelar_de_outra_pessoa_e_403(session, vend, outra, cliente):
    from app.routers import relacionamento as r
    a = _acomp(session, vend, cliente)
    with pytest.raises(DadoInvalido) as e:
        chamar(r.cancelar_acompanhamento, RequestComForm(outra, FormData([])), atividade_id=a.id, session=session)
    assert e.value.status_code == 403 and session.get(AtividadeComercial, a.id).cancelada_em is None


def test_timeline_e_csv_dizem_cancelada(session, vend, cliente):
    from app.routers.relatorios_comerciais import csv_atividades
    op = crm.criar_oportunidade(session, ator=vend, cliente_id=cliente.id, titulo=f"Venda {next(_SEQ)}")
    a = crm.criar_atividade(session, ator=vend, titulo="Mandar proposta revisada", oportunidade_id=op.id,
                            cliente_id=cliente.id, responsavel_id=vend.id, due_em=datetime(2020, 1, 1))
    session.commit()
    rel.cancelar_acompanhamento(session, ator=vend, atividade=a)
    session.commit()
    assert any(e["texto"].startswith("Cancelada: Mandar proposta revisada") for e in crm.timeline(session, op))
    assert crm.proxima_atividade(session, op.id) is None
    corpo = bytes(chamar(csv_atividades, RequestFalsa(vend), responsavel="", session=session).body).decode()
    cab = corpo.splitlines()[0].lstrip("﻿")
    assert cab.split(";" if ";" in cab else ",")[-1] == "cancelada"
    linha = next(l for l in corpo.splitlines() if "Mandar proposta revisada" in l)
    assert linha.endswith("sim") and ",nao," in linha.replace(";", ",")      # concluída = não, atrasada = não

"""CRM de relacionamento (15/10/2026): contatos, visitas, acompanhamentos, perfil de produtos,
busca de clientes por produto e Cliente 360.

Banco temporário da suíte. Usuários persistidos (as regras de "quem pode agir" comparam ids).
"""
from datetime import date, datetime, timedelta
from itertools import count

import pytest
from sqlmodel import select
from starlette.datastructures import FormData

from app import crm_relacionamento as rel
from app import crm_service as crm
from app.crm_service import DadoInvalido
from app.models import (
    AtividadeComercial, AuditLog, Cliente, ClienteProdutoUtilizado, Contato, Produto, Usuario, VisitaCliente,
    VisitaParticipante,
)
from conftest import RequestFalsa
from tests.crisis.conftest import RequestComForm, chamar

_SEQ = count(1)


def _usuario(session, papel):
    n = next(_SEQ)
    u = Usuario(email=f"crm-rel-{papel.lower()}-{n}@anara.test", nome=f"{papel.title()} {n}", senha_hash="h",
                papel=papel, ativo=True)
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
    c = crm.criar_cliente(session, ator=vend, nome=f"Hotel Relacionamento {next(_SEQ)}", cidade_uf="São Paulo")
    session.commit()
    return c


def _contato(session, ator, cliente, nome="Maria", **kw):
    c = rel.criar_contato(session, ator=ator, cliente_id=cliente.id, nome=nome, **kw)
    session.commit()
    return c


def html(resposta):
    return bytes(resposta.body).decode()


def _auditoria(session, acao, entidade_id):
    return session.exec(select(AuditLog).where(AuditLog.acao == acao, AuditLog.entidade_id == entidade_id)).all()


# ===========================================================================
# Contatos
# ===========================================================================
def test_contatos_multiplos_com_whatsapp_principal_unico_e_auditoria(session, vend, cliente):
    maria = _contato(session, vend, cliente, "Maria", cargo="Governanta", whatsapp="11 99999-0000", principal=True)
    joao = _contato(session, vend, cliente, "João", cargo="Compras", principal=True)
    session.refresh(maria)
    assert maria.whatsapp == "11 99999-0000" and maria.cliente_id == cliente.id
    assert joao.principal and not maria.principal          # um principal por cliente
    assert [c.nome for c in crm.contatos_de(session, cliente.id)] == ["João", "Maria"]
    assert _auditoria(session, "CREATE_CONTACT", maria.id)


def test_editar_contato_registra_quem_e_quando_e_nao_troca_de_cliente(session, vend, cliente):
    maria = _contato(session, vend, cliente)
    rel.editar_contato(session, ator=vend, contato=maria, nome="Maria Souza", cargo="Governanta executiva",
                       telefone="11 3333-0000", whatsapp="11 98888-0000", email="maria@hotel.test", observacao="")
    session.commit()
    session.refresh(maria)
    assert (maria.nome, maria.cargo, maria.whatsapp, maria.cliente_id) == (
        "Maria Souza", "Governanta executiva", "11 98888-0000", cliente.id)
    assert maria.atualizado_por == vend.email and maria.atualizado_em
    assert _auditoria(session, "UPDATE_CONTACT", maria.id)
    with pytest.raises(DadoInvalido):
        rel.editar_contato(session, ator=vend, contato=maria, nome="  ")


def test_inativar_tira_das_selecoes_mas_preserva_historico_e_reativa(session, vend, cliente):
    from app.routers.cotacoes import contatos_do_cliente
    from app.routers.relacionamento import _contatos_para_visita
    maria = _contato(session, vend, cliente, principal=True)
    visita = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 1),
                              relatorio="Conversa inicial", participantes=[maria.id])
    session.commit()
    rel.definir_contato_ativo(session, ator=vend, contato=maria, ativo=False)
    session.commit()
    assert not maria.ativo and not maria.principal
    nomes_json = [c["nome"] for c in __import__("json").loads(bytes(
        chamar(contatos_do_cliente, RequestFalsa(vend), cliente_id=cliente.id, session=session).body))]
    assert "Maria" not in nomes_json                               # nova cotação não oferece
    assert maria not in _contatos_para_visita(session, cliente.id)  # nova visita não oferece
    assert maria in rel.linha_visita(session, visita).participantes  # a visita antiga continua com ela
    assert maria in _contatos_para_visita(session, cliente.id, [maria.id])  # correção da antiga, sim
    rel.definir_contato_ativo(session, ator=vend, contato=maria, ativo=True)
    session.commit()
    assert maria.ativo and _auditoria(session, "REACTIVATE_CONTACT", maria.id)


def test_rota_de_contato_cria_com_whatsapp_e_edita(session, vend, cliente):
    from app.routers import crm as rcrm
    from app.routers import relacionamento as r
    chamar(rcrm.criar_contato, RequestFalsa(vend), cliente_id=cliente.id, nome="Ana", cargo="Diretora",
           email="", telefone="", whatsapp="11 97777-0000", principal="", observacao="", session=session)
    ana = session.exec(select(Contato).where(Contato.cliente_id == cliente.id, Contato.nome == "Ana")).one()
    assert ana.whatsapp == "11 97777-0000"
    form = FormData([("nome", "Ana Lima"), ("cargo", "Diretora de Operações"), ("whatsapp", "11 97777-0000"),
                     ("principal", "sim")])
    chamar(r.editar_contato, RequestComForm(vend, form), contato_id=ana.id, session=session)
    session.refresh(ana)
    assert ana.nome == "Ana Lima" and ana.principal


# ===========================================================================
# Visitas
# ===========================================================================
def test_visita_com_participantes_externos_e_acompanhamentos(session, vend, cliente):
    maria, joao = _contato(session, vend, cliente, "Maria"), _contato(session, vend, cliente, "João")
    v = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 10),
                         relatorio="Apresentei a linha 300 fios.\nGostaram da fronha 4 abas.",
                         assunto="APRESENTACAO", participantes=[maria.id, joao.id],
                         participantes_externos="Carla (consultora)", produtos_discutidos="Lençol 300 fios",
                         acompanhamentos=[rel.NovoAcompanhamento("Enviar proposta", date(2026, 10, 17)),
                                          rel.NovoAcompanhamento("  ")])
    session.commit()
    linha = rel.linha_visita(session, v)
    assert {p.nome for p in linha.participantes} == {"Maria", "João"}
    assert v.participantes_externos == "Carla (consultora)" and v.responsavel_id == vend.id
    assert v.criado_por_id == vend.id and linha.assunto == "Apresentação de produtos"
    assert [a.titulo for a in linha.acompanhamentos] == ["Enviar proposta"]   # linha vazia não vira pendência
    a = linha.acompanhamentos[0]
    assert a.visita_id == v.id and a.cliente_id == cliente.id and a.due_em == datetime(2026, 10, 17, 23, 59)
    assert linha.proximo_passo == "Enviar proposta"
    assert _auditoria(session, "CREATE_VISIT", v.id)
    # participante externo não vira contato
    assert not session.exec(select(Contato).where(Contato.nome.like("Carla%"))).all()


@pytest.mark.parametrize("kw,msg", [
    (dict(relatorio="   "), "relatório"), (dict(data_visita=None), "data"), (dict(assunto="FESTA"), "Assunto"),
])
def test_visita_recusa_sem_relatorio_sem_data_e_assunto_invalido(session, vend, cliente, kw, msg):
    base = dict(data_visita=date(2026, 10, 1), relatorio="ok")
    base.update(kw)
    with pytest.raises(DadoInvalido) as e:
        rel.criar_visita(session, ator=vend, cliente_id=cliente.id, **base)
    assert msg.lower() in str(e.value.detail).lower()


def test_participante_de_outro_cliente_e_recusado(session, vend, cliente):
    outro = crm.criar_cliente(session, ator=vend, nome=f"Outro Hotel {next(_SEQ)}")
    estranho = _contato(session, vend, outro, "Pedro")
    with pytest.raises(DadoInvalido):
        rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 1),
                         relatorio="x", participantes=[estranho.id])


def test_historico_do_cliente_em_ordem_decrescente_sem_visitas_retiradas(session, vend, cliente):
    for d in (date(2026, 9, 1), date(2026, 10, 5), date(2026, 8, 20)):
        rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=d, relatorio=f"visita {d}")
    session.commit()
    datas = [l.visita.data_visita for l in rel.visitas_do_cliente(session, cliente.id)]
    assert datas == [date(2026, 10, 5), date(2026, 9, 1), date(2026, 8, 20)]
    retirada = rel.visitas_do_cliente(session, cliente.id)[1].visita
    rel.remover_visita(session, ator=vend, visita=retirada, motivo="registrada no cliente errado")
    session.commit()
    assert retirada.id not in [l.visita.id for l in rel.visitas_do_cliente(session, cliente.id)]
    assert session.get(VisitaCliente, retirada.id).removida_em      # não foi apagada
    assert _auditoria(session, "REMOVE_VISIT", retirada.id)


def test_editar_visita_autor_pode_outra_vendedora_nao_admin_pode_e_autoria_fica(session, vend, outra, dona, cliente):
    maria = _contato(session, vend, cliente)
    v = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 1), relatorio="v1")
    session.commit()
    with pytest.raises(DadoInvalido) as e:
        rel.editar_visita(session, ator=outra, visita=v, data_visita=date(2026, 10, 1), relatorio="invasão")
    assert e.value.status_code == 403
    rel.editar_visita(session, ator=vend, visita=v, data_visita=date(2026, 10, 2), relatorio="v2",
                      participantes=[maria.id])
    rel.editar_visita(session, ator=dona, visita=v, data_visita=date(2026, 10, 2), relatorio="v3",
                      participantes=[maria.id])
    session.commit()
    session.refresh(v)
    assert v.relatorio == "v3" and v.data_visita == date(2026, 10, 2)
    assert (v.criado_por_id, v.criado_por, v.responsavel_id) == (vend.id, vend.email, vend.id)   # autoria intacta
    assert v.atualizado_por == dona.email and v.atualizado_em
    assert len(_auditoria(session, "UPDATE_VISIT", v.id)) == 2


def test_rota_de_visita_grava_e_redireciona_para_o_relatorio(session, vend, cliente):
    from app.routers import relacionamento as r
    maria = _contato(session, vend, cliente)
    form = FormData([("data_visita", "2026-10-14"), ("assunto", "NEGOCIACAO"), ("relatorio", "Negociamos o volume."),
                     ("participantes", str(maria.id)), ("participantes_externos", ""),
                     ("acomp_descricao", "Ligar para o João"), ("acomp_data", "2026-10-20"),
                     ("acomp_responsavel", str(vend.id)), ("acomp_descricao", ""), ("acomp_data", ""),
                     ("acomp_responsavel", str(vend.id))])
    resp = chamar(r.criar_visita, RequestComForm(vend, form), cliente_id=cliente.id, session=session)
    v = session.exec(select(VisitaCliente).where(VisitaCliente.cliente_id == cliente.id)).one()
    assert resp.status_code == 303 and resp.headers["location"] == f"/visitas/{v.id}"
    assert session.exec(select(VisitaParticipante).where(VisitaParticipante.visita_id == v.id)).one().contato_id == maria.id
    assert [a.titulo for a in session.exec(select(AtividadeComercial).where(AtividadeComercial.visita_id == v.id))] \
        == ["Ligar para o João"]
    pagina = html(chamar(r.ver_visita, RequestFalsa(vend), visita_id=v.id, session=session))
    assert "Negociamos o volume." in pagina and "Maria" in pagina and "Ligar para o João" in pagina


def test_formulario_de_edicao_e_negado_a_quem_nao_e_autor(session, vend, outra, cliente):
    from app.routers import relacionamento as r
    v = rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 1), relatorio="x")
    session.commit()
    with pytest.raises(DadoInvalido) as e:
        chamar(r.editar_visita_form, RequestFalsa(outra), visita_id=v.id, session=session)
    assert e.value.status_code == 403
    assert "Salvar correção" in html(chamar(r.editar_visita_form, RequestFalsa(vend), visita_id=v.id, session=session))


# ===========================================================================
# Acompanhamentos
# ===========================================================================
def test_hoje_e_o_dia_de_sao_paulo():
    assert rel.hoje_sp(datetime(2026, 10, 16, 2, 0)) == date(2026, 10, 15)    # 23h de 15/10 em SP
    assert rel.hoje_sp(datetime(2026, 10, 16, 3, 0)) == date(2026, 10, 16)


def test_painel_separa_atrasados_hoje_proximos_sem_data_e_concluidos(session, vend, outra, cliente):
    agora = datetime(2026, 10, 15, 15, 0)       # 12h em SP
    criar = lambda d, quem=vend: rel.criar_acompanhamento(session, ator=quem, cliente_id=cliente.id,
                                                          descricao=f"ação {d}", data_prevista=d)
    atrasado, hoje, futuro = criar(date(2026, 10, 14)), criar(date(2026, 10, 15)), criar(date(2026, 10, 20))
    sem_data = rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="sem data")
    de_outra = criar(date(2026, 10, 1), outra)
    feito = criar(date(2026, 10, 10))
    rel.concluir_acompanhamento(session, ator=vend, atividade=feito)
    session.commit()
    p = rel.painel_acompanhamentos(session, responsavel_id=vend.id, agora=agora)
    assert [a.id for a in p.atrasados] == [atrasado.id] and [a.id for a in p.hoje] == [hoje.id]
    assert [a.id for a in p.proximos] == [futuro.id] and [a.id for a in p.sem_data] == [sem_data.id]
    assert [a.id for a in p.concluidos] == [feito.id]
    assert de_outra.id not in [a.id for a in p.atrasados + p.concluidos]   # é da outra vendedora


def test_acompanhamento_direto_no_cliente_e_de_visita_de_outro_cliente_recusado(session, vend, cliente):
    a = rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="Enviar catálogo",
                                 data_prevista=date(2026, 11, 3))
    assert a.visita_id is None and a.responsavel_id == vend.id and a.tipo == "FOLLOW_UP"
    outro = crm.criar_cliente(session, ator=vend, nome=f"Outro {next(_SEQ)}")
    v = rel.criar_visita(session, ator=vend, cliente_id=outro.id, data_visita=date(2026, 10, 1), relatorio="x")
    with pytest.raises(DadoInvalido):
        rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="x", visita_id=v.id)


def test_concluir_reagendar_editar_so_responsavel_criador_ou_admin(session, vend, outra, dona, cliente):
    a = rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="Enviar amostra",
                                 data_prevista=date(2026, 10, 20))
    session.commit()
    for acao in (lambda: rel.concluir_acompanhamento(session, ator=outra, atividade=a),
                 lambda: rel.reagendar_acompanhamento(session, ator=outra, atividade=a, data_prevista=date(2026, 11, 1)),
                 lambda: rel.editar_acompanhamento(session, ator=outra, atividade=a, descricao="x")):
        with pytest.raises(DadoInvalido) as e:
            acao()
        assert e.value.status_code == 403
    rel.reagendar_acompanhamento(session, ator=vend, atividade=a, data_prevista=date(2026, 10, 27))
    rel.editar_acompanhamento(session, ator=dona, atividade=a, descricao="Enviar amostra da fronha 400")
    session.commit()
    assert a.due_em == datetime(2026, 10, 27, 23, 59) and a.titulo == "Enviar amostra da fronha 400"
    assert _auditoria(session, "RESCHEDULE_ACTIVITY", a.id)[0].versao_anterior == "2026-10-20"
    rel.concluir_acompanhamento(session, ator=vend, atividade=a)
    session.commit()
    assert a.concluida_em and a.concluida_por == vend.email
    with pytest.raises(DadoInvalido) as e:
        rel.reagendar_acompanhamento(session, ator=vend, atividade=a, data_prevista=date(2026, 12, 1))
    assert e.value.status_code == 409
    assert session.get(AtividadeComercial, a.id) is not None       # concluído permanece


def test_rota_antiga_de_concluir_atividade_tambem_respeita_o_dono(session, vend, outra, cliente):
    from app.routers.crm import concluir
    a = rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="x")
    session.commit()
    with pytest.raises(DadoInvalido) as e:
        chamar(concluir, RequestFalsa(outra), atividade_id=a.id, session=session)
    assert e.value.status_code == 403 and session.get(AtividadeComercial, a.id).concluida_em is None
    chamar(concluir, RequestFalsa(vend), atividade_id=a.id, session=session)
    assert session.get(AtividadeComercial, a.id).concluida_em


def test_pagina_meus_acompanhamentos(session, vend, outra, cliente):
    from app.routers.relacionamento import acompanhamentos
    rel.criar_acompanhamento(session, ator=vend, cliente_id=cliente.id, descricao="Minha tarefa zebra",
                             data_prevista=date(2020, 1, 1))
    rel.criar_acompanhamento(session, ator=outra, cliente_id=cliente.id, descricao="Tarefa da colega zebra",
                             data_prevista=date(2020, 1, 1))
    session.commit()
    minha = html(chamar(acompanhamentos, RequestFalsa(vend), responsavel="eu", session=session))
    assert "Minha tarefa zebra" in minha and "Tarefa da colega zebra" not in minha and "atrasado" in minha
    todas = html(chamar(acompanhamentos, RequestFalsa(vend), responsavel="todos", session=session))
    assert "Tarefa da colega zebra" in todas and "de outra pessoa" in todas    # vê, mas não age


# ===========================================================================
# Perfil de produtos utilizados
# ===========================================================================
def test_produto_utilizado_sem_sku_e_parcial(session, vend, cliente):
    r = rel.criar_produto_utilizado(session, ator=vend, cliente_id=cliente.id, familia="Bath Towel",
                                    descricao="Toalha de banho do concorrente", gsm="650", thread_count="")
    session.commit()
    assert (r.produto_id, r.gsm, r.thread_count, r.composicao) == (None, 650, None, None)
    assert _auditoria(session, "CREATE_CLIENT_PRODUCT", r.id)


@pytest.mark.parametrize("dados", [dict(), dict(familia="Navio"), dict(descricao="x", composicao="80/20"),
                                   dict(descricao="x", tamanho="Gigante"), dict(descricao="x", gsm="-3")])
def test_produto_utilizado_recusa_vazio_e_fora_do_vocabulario(session, vend, cliente, dados):
    with pytest.raises(DadoInvalido):
        rel.criar_produto_utilizado(session, ator=vend, cliente_id=cliente.id, **dados)


def test_produto_do_catalogo_completa_so_o_que_falta_e_nao_mexe_no_produto(session, vend, cliente):
    p = Produto(sku_key=f"CRM-REL-{next(_SEQ)}", nome="Lençol de baixo King 280x290 · 300 fios", familia="Bottom Sheet",
                thread_count=300, largura_cm=280, comprimento_cm=290, cotton_pct=1.0, poliester_pct=0.0, ativo=True)
    session.add(p)
    session.commit()
    session.refresh(p)
    antes = p.model_dump()
    assert antes["sku_key"]
    r = rel.criar_produto_utilizado(session, ator=vend, cliente_id=cliente.id, produto_id=str(p.id), tamanho="King",
                                    thread_count="400")
    session.commit()
    assert (r.familia, r.thread_count, r.medidas, r.composicao, r.tamanho, r.descricao) == (
        "Bottom Sheet", 400, "280x290", "100% algodão", "King", p.nome)   # o informado (400) vence
    session.refresh(p)
    assert p.model_dump() == antes


def test_editar_inativar_e_reativar_produto_utilizado(session, vend, cliente):
    r = rel.criar_produto_utilizado(session, ator=vend, cliente_id=cliente.id, descricao="Fronha", thread_count="250")
    session.commit()
    rel.editar_produto_utilizado(session, ator=vend, registro=r, descricao="Fronha 50x70", thread_count="250",
                                 composicao="70% algodão / 30% poliéster")
    rel.definir_produto_utilizado_ativo(session, ator=vend, registro=r, ativo=False)
    session.commit()
    assert r.composicao == "70% algodão / 30% poliéster" and not r.ativo and r.atualizado_por == vend.email
    assert r in rel.produtos_utilizados(session, cliente.id)        # continua na ficha, como inativo
    rel.definir_produto_utilizado_ativo(session, ator=vend, registro=r, ativo=True)
    session.commit()
    assert r.ativo and _auditoria(session, "REACTIVATE_CLIENT_PRODUCT", r.id)


# ===========================================================================
# Busca de clientes por produto
# ===========================================================================
@pytest.fixture
def base_busca(session, vend):
    def novo(nome, *registros, ativo=True):
        c = crm.criar_cliente(session, ator=vend, nome=f"{nome} {next(_SEQ)}")
        c.ativo = ativo
        for dados in registros:
            rel.criar_produto_utilizado(session, ator=vend, cliente_id=c.id, **dados)
        session.commit()
        return c
    lencol300 = dict(familia="Bottom Sheet", thread_count="300", composicao="100% algodão", tamanho="King")
    a = novo("Alfa Busca", lencol300, dict(familia="Top Sheet", thread_count="300", composicao="100% algodão"))
    b = novo("Beta Busca", dict(familia="Bottom Sheet", thread_count="250", composicao="70% algodão / 30% poliéster",
                                tamanho="Queen"), dict(familia="Bath Towel", gsm="650"))
    c = novo("Gama Busca", dict(familia="Bath Towel", gsm="650"))
    arquivado = novo("Delta Busca", dict(familia="Bath Towel", gsm="650"), ativo=False)
    inativo = novo("Epsilon Busca", dict(familia="Bath Towel", gsm="650"))
    rel.definir_produto_utilizado_ativo(session, ator=vend, registro=rel.produtos_utilizados(session, inativo.id)[0],
                                        ativo=False)
    session.commit()
    return a, b, c, arquivado, inativo


def _nomes(resultados):
    """Só os clientes desta base (o banco da suíte é compartilhado entre os testes)."""
    return {r.cliente.nome.rsplit(" ", 1)[0] for r in resultados if " Busca " in r.cliente.nome}


def test_busca_por_cada_filtro_e_combinados(session, base_busca):
    buscar = lambda **f: _nomes(rel.buscar_clientes_por_produto(session, **f))
    assert buscar(thread_count=300) == {"Alfa Busca"}
    assert buscar(composicao="70% algodão / 30% poliéster") == {"Beta Busca"}
    assert buscar(tamanho="King") == {"Alfa Busca"}
    assert buscar(gsm=650) == {"Beta Busca", "Gama Busca"}        # arquivado e registro inativo ficam de fora
    assert buscar(familia="Bottom Sheet") == {"Alfa Busca", "Beta Busca"}
    assert buscar(familia="Bottom Sheet", thread_count=300, composicao="100% algodão") == {"Alfa Busca"}
    assert buscar(familia="Bottom Sheet", tamanho="Queen", thread_count=300) == set()
    assert rel.buscar_clientes_por_produto(session) == []          # sem filtro não lista todo mundo


def test_busca_nao_duplica_cliente_e_mostra_todas_as_especificacoes(session, base_busca):
    alfa = [r for r in rel.buscar_clientes_por_produto(session, thread_count=300) if r.cliente.id == base_busca[0].id]
    assert len(alfa) == 1 and len(alfa[0].especificacoes) == 2


def test_tela_de_busca_para_vendedora_sem_economia(session, vend, base_busca):
    from app.routers.relacionamento import clientes_por_produto
    pagina = html(chamar(clientes_por_produto, RequestFalsa(vend), familia="", produto_id="", fios="300",
                         composicao="", tamanho="", gsm="", session=session))
    assert "Alfa Busca" in pagina and "Beta Busca" not in pagina
    for termo in ("Custo", "CNET", "EXW", "Margem", "margem", "lucro", "Lucro", "markup"):
        assert termo not in pagina
    vazio = html(chamar(clientes_por_produto, RequestFalsa(vend), session=session))
    assert "Escolha pelo menos um filtro" in vazio


def test_rotas_novas_exigem_login(session, cliente):
    from fastapi import HTTPException
    from app.routers import relacionamento as r
    for rota, kw in ((r.acompanhamentos, {"responsavel": "eu"}), (r.clientes_por_produto, {}),
                     (r.nova_visita, {"cliente_id": cliente.id})):
        with pytest.raises(HTTPException) as e:
            chamar(rota, RequestFalsa(None), session=session, **kw)
        assert e.value.status_code == 401


# ===========================================================================
# Cliente 360 e integração
# ===========================================================================
def test_ficha_do_cliente_integra_as_secoes_sem_economia(session, vend, dona, cliente):
    from app.routers.clientes import detalhe
    maria = _contato(session, vend, cliente, "Maria Zebra", principal=True)
    rel.criar_visita(session, ator=vend, cliente_id=cliente.id, data_visita=date(2026, 10, 9),
                     relatorio="Relatório zebra da visita", participantes=[maria.id],
                     acompanhamentos=[rel.NovoAcompanhamento("Pendência zebra", date(2026, 10, 30))])
    p = Produto(sku_key=f"CRM-REL-{next(_SEQ)}", nome="Produto zebra", familia="Pillow Case", ativo=True,
                material_ref="MATERIAL-SECRETO-ZEBRA", custo_unitario=12.34)
    session.add(p)
    session.commit()
    rel.criar_produto_utilizado(session, ator=vend, cliente_id=cliente.id, descricao="Fronha zebra", produto_id=str(p.id))
    session.commit()
    for quem in (vend, dona):
        pagina = html(chamar(detalhe, RequestFalsa(quem), cliente_id=cliente.id, session=session))
        for texto in ("Relatório zebra da visita", "Pendência zebra", "Maria Zebra", "Fronha zebra",
                      "Produtos utilizados", "Acompanhamentos", "Amostras", "Registrar visita"):
            assert texto in pagina, texto
        for proibido in ("MATERIAL-SECRETO-ZEBRA", "12,34", "12.34", "CNET", "EXW", "Margem padrão"):
            assert proibido not in pagina, proibido


def test_ficha_lista_amostras_enviadas_ao_cliente(session, vend, cliente):
    from app import amostras as am
    from app.routers.clientes import detalhe
    amostra = am.adicionar_avulsa(session, nome="Amostra zebra", especificacao="fronha 50x70", quantidade_inicial=3,
                                  observacao=None, ator=vend)
    session.commit()
    am.registrar_saida(session, amostra.id, quantidade=1, cliente_id=cliente.id, cliente_texto=None,
                       motivo="Apresentação / reunião", observacao=None, data_mov=date(2026, 10, 2), ator=vend)
    session.commit()
    movs = rel.amostras_do_cliente(session, cliente.id)
    assert [(m["nome"], m["tipo"], m["quantidade"]) for m in movs] == [("Amostra zebra", "Saída", 1)]
    assert "Amostra zebra" in html(chamar(detalhe, RequestFalsa(vend), cliente_id=cliente.id, session=session))


def test_cliente_sem_nada_mostra_estados_vazios(session, vend, cliente):
    from app.routers.clientes import detalhe
    pagina = html(chamar(detalhe, RequestFalsa(vend), cliente_id=cliente.id, session=session))
    for texto in ("Nenhuma visita registrada", "Nenhum acompanhamento pendente", "Nenhum produto cadastrado",
                  "Nenhuma amostra enviada", "Nenhum contato cadastrado"):
        assert texto in pagina, texto


def test_pagina_do_produto_utilizado_inclui_e_edita_sem_economia(session, vend, cliente):
    from app.routers import relacionamento as r
    p = Produto(sku_key=f"CRM-REL-{next(_SEQ)}", nome="Toalha zebra do catálogo", familia="Bath Towel", ativo=True,
                material_ref="MATERIAL-SECRETO-ZEBRA-2", custo_unitario=45.67)
    session.add(p)
    session.commit()
    nova = html(chamar(r.novo_produto_utilizado, RequestFalsa(vend), cliente_id=cliente.id, session=session))
    assert "Família" in nova and "Composição" in nova and "Adicionar" in nova
    form = FormData([("familia", "Bath Towel"), ("descricao", "Toalha do hotel"), ("gsm", "650"), ("produto_id", "")])
    chamar(r.criar_produto_utilizado, RequestComForm(vend, form), cliente_id=cliente.id, session=session)
    reg = rel.produtos_utilizados(session, cliente.id)[0]
    editar = html(chamar(r.editar_produto_utilizado_form, RequestFalsa(vend), registro_id=reg.id, session=session))
    assert 'value="650"' in editar and "Toalha do hotel" in editar
    for proibido in ("MATERIAL-SECRETO-ZEBRA-2", "45,67", "45.67", "CNET", "EXW", "Margem", "margem", "lucro"):
        assert proibido not in nova and proibido not in editar, proibido

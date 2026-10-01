"""Controle de amostras (01/10/2026): saldo derivado das movimentações, papéis e histórico.

As rotas são chamadas direto, como o resto da suíte, com a `Request` da pessoa simulada —
então cada teste passa pela mesma barreira de papel que a tela.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import amostras as am
from app.models import AmostraProduto, Cliente, Produto
from app.permissoes import PrecisaLogin
from app.routers import amostras as rotas
from conftest import RequestFalsa


def _produto(session, nome="Toalha de banho (teste amostra)"):
    p = Produto(sku_key=f"TESTE-AMOSTRA-{uuid.uuid4().hex[:8]}", nome=nome,
                especificacao="100x180 · 650 GSM", ativo=True)
    session.add(p)
    session.commit()
    return p


def _amostra(session, owner, quantidade=0):
    """Admin põe um produto novo no controle (pela rota) e devolve a amostra."""
    p = _produto(session)
    r = rotas.admin_adicionar(RequestFalsa(owner), produto_id=str(p.id),
                              quantidade_inicial=str(quantidade), observacao="", session=session)
    assert r.status_code == 303
    return session.exec(select(AmostraProduto).where(AmostraProduto.produto_id == p.id)).one()


def _entrada(session, ator, amostra, qtd):
    rotas.admin_entrada(RequestFalsa(ator), amostra_id=amostra.id, quantidade=str(qtd),
                        observacao="chegaram", data="", session=session)


def _saida(session, ator, amostra, qtd, cliente_id="", cliente_texto="Hotel X",
           motivo="Enviado para avaliação"):
    return rotas.saida(RequestFalsa(ator), amostra.id, quantidade=str(qtd), cliente_id=cliente_id,
                       cliente_texto=cliente_texto, motivo=motivo, observacao="", data="",
                       session=session)


def _retorno(session, ator, amostra, qtd, condicao, destino="t:hotel x", observacao=""):
    return rotas.retorno(RequestFalsa(ator), amostra.id, quantidade=str(qtd), condicao=condicao,
                         destino=destino, observacao=observacao, data="", session=session)


def _saldo(session, amostra):
    s = am.saldo(session, amostra.id)
    return s["disponivel"], s["circulacao"]


# ---------------------------------------------------------------------------
# 1–7: o ciclo — tem amostra → enviou → voltou ou não voltou
# ---------------------------------------------------------------------------
def test_01_admin_adiciona_produto_as_amostras(session, owner):
    amostra = _amostra(session, owner)
    assert amostra.ativo and amostra.criado_por_id == owner.id
    assert _saldo(session, amostra) == (0, 0)


def test_02_entrada_de_5_deixa_5_disponiveis(session, owner):
    amostra = _amostra(session, owner)
    _entrada(session, owner, amostra, 5)
    assert _saldo(session, amostra) == (5, 0)


def test_03_vendedora_envia_2_fica_3_disponiveis_e_2_em_circulacao(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=5)
    assert _saida(session, vendedor_interno, amostra, 2).status_code == 303
    assert _saldo(session, amostra) == (3, 2)
    m = am.movimentos(session, amostra.id)[-1]
    # a responsável vem do usuário logado, não do formulário
    assert (m.tipo, m.quantidade, m.usuario_id, m.usuario_nome) == ("SAIDA", 2, vendedor_interno.id,
                                                                    vendedor_interno.nome)


def test_04_nao_permite_enviar_4_quando_so_ha_3(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=5)
    _saida(session, vendedor_interno, amostra, 2)
    antes = len(am.movimentos(session, amostra.id))
    with pytest.raises(HTTPException) as erro:
        _saida(session, vendedor_interno, amostra, 4)
    assert erro.value.status_code == 409 and "3" in erro.value.detail
    session.rollback()
    assert _saldo(session, amostra) == (3, 2)
    assert len(am.movimentos(session, amostra.id)) == antes


def test_05_retorno_de_1_volta_para_o_disponivel(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=5)
    _saida(session, vendedor_interno, amostra, 2)
    _retorno(session, vendedor_interno, amostra, 1, "RETORNO")
    assert _saldo(session, amostra) == (4, 1)


def test_06_consumida_sai_de_circulacao_sem_voltar_ao_disponivel(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=5)
    _saida(session, vendedor_interno, amostra, 2)
    _retorno(session, vendedor_interno, amostra, 1, "RETORNO")
    _retorno(session, vendedor_interno, amostra, 1, "CONSUMIDA", observacao="Ficou com o cliente")
    assert _saldo(session, amostra) == (4, 0)
    # danificada e perdida também não voltam; nada mais em circulação para baixar
    with pytest.raises(HTTPException):
        _retorno(session, vendedor_interno, amostra, 1, "PERDIDA")
    session.rollback()


def test_07_historico_tem_todas_as_movimentacoes_em_ordem(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=5)
    _saida(session, vendedor_interno, amostra, 2)
    _retorno(session, vendedor_interno, amostra, 1, "RETORNO")
    _retorno(session, vendedor_interno, amostra, 1, "CONSUMIDA", observacao="Ficou com o cliente")
    h = am.historico(session, amostra.id)
    assert [(l["tipo"], l["quantidade"]) for l in h] == [
        ("Entrada", "+5"), ("Saída", "−2"), ("Retorno", "+1"), ("Consumida", "1")]
    assert h[1]["uso"] == "Hotel X · Enviado para avaliação"
    assert h[1]["usuario"] == vendedor_interno.nome
    assert "Retornou normalmente" in h[2]["observacao"] and "Ficou com o cliente" in h[3]["observacao"]
    # e a tela mostra o mesmo histórico para a vendedora
    pagina = rotas.detalhe(RequestFalsa(vendedor_interno), amostra.id, session=session).body.decode()
    for texto in ("Histórico", "Hotel X", "Consumida", "Registrar envio / uso", "Registrar retorno"):
        assert texto in pagina


# ---------------------------------------------------------------------------
# 8–9: ajuste administrativo
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("papel_fixture", ["vendedor_interno", "vendedor_comissionado"])
def test_08_vendedora_nao_faz_ajuste_nem_entrada_nem_cadastro(session, owner, request, papel_fixture):
    vendedora = request.getfixturevalue(papel_fixture)
    amostra = _amostra(session, owner, quantidade=5)
    pedido = RequestFalsa(vendedora)
    for chamada in (
        lambda: rotas.admin_ajuste(pedido, amostra_id=amostra.id, disponivel_contado="4",
                                   motivo="contagem", data="", session=session),
        lambda: rotas.admin_entrada(pedido, amostra_id=amostra.id, quantidade="3", observacao="",
                                    data="", session=session),
        lambda: rotas.admin_adicionar(pedido, produto_id=str(_produto(session).id),
                                      quantidade_inicial="1", observacao="", session=session),
        lambda: rotas.admin_lista(pedido, q="", session=session),
    ):
        with pytest.raises(HTTPException) as erro:
            chamada()
        assert erro.value.status_code == 403
    assert _saldo(session, amostra) == (5, 0)
    # sem sessão, nem a aba abre
    with pytest.raises(PrecisaLogin):
        rotas.lista(RequestFalsa(None), session=session)


def test_09_admin_ajusta_com_motivo_obrigatorio_sem_apagar_nada(session, owner, admin):
    amostra = _amostra(session, owner, quantidade=5)
    with pytest.raises(HTTPException) as erro:
        rotas.admin_ajuste(RequestFalsa(admin), amostra_id=amostra.id, disponivel_contado="4",
                           motivo="  ", data="", session=session)
    assert erro.value.status_code == 400
    session.rollback()
    rotas.admin_ajuste(RequestFalsa(admin), amostra_id=amostra.id, disponivel_contado="4",
                       motivo="Contagem física encontrou 4", data="", session=session)
    assert _saldo(session, amostra) == (4, 0)
    movs = am.movimentos(session, amostra.id)
    assert [(m.tipo, m.quantidade) for m in movs] == [("ENTRADA", 5), ("AJUSTE", -1)]
    ajuste = movs[-1]
    assert ajuste.motivo == "Contagem física encontrou 4" and ajuste.usuario_id == admin.id


# ---------------------------------------------------------------------------
# 10–12: cadastro único e destinatário
# ---------------------------------------------------------------------------
def test_10_produto_nao_entra_duas_vezes(session, owner):
    amostra = _amostra(session, owner)
    with pytest.raises(HTTPException) as erro:
        rotas.admin_adicionar(RequestFalsa(owner), produto_id=str(amostra.produto_id),
                              quantidade_inicial="3", observacao="", session=session)
    assert erro.value.status_code == 409
    session.rollback()
    assert len(session.exec(select(AmostraProduto)
                            .where(AmostraProduto.produto_id == amostra.produto_id)).all()) == 1


def test_11_cliente_existente_fica_associado(session, owner, vendedor_comissionado):
    cliente = Cliente(nome=f"Hotel Cadastrado {uuid.uuid4().hex[:6]}", ativo=True)
    session.add(cliente)
    session.commit()
    amostra = _amostra(session, owner, quantidade=3)
    _saida(session, vendedor_comissionado, amostra, 2, cliente_id=str(cliente.id), cliente_texto="")
    m = am.movimentos(session, amostra.id)[-1]
    assert m.cliente_id == cliente.id and m.cliente_texto is None
    fora = am.fora(session, amostra.id)
    assert fora == [{"chave": f"c{cliente.id}", "rotulo": cliente.nome, "quantidade": 2, "desde": m.data}]
    # o retorno sai da conta DESTE cliente e não devolve mais do que está com ele
    with pytest.raises(HTTPException):
        _retorno(session, vendedor_comissionado, amostra, 3, "RETORNO", destino=f"c{cliente.id}")
    session.rollback()
    _retorno(session, vendedor_comissionado, amostra, 2, "RETORNO", destino=f"c{cliente.id}")
    assert _saldo(session, amostra) == (3, 0)
    assert am.movimentos(session, amostra.id)[-1].cliente_id == cliente.id


def test_12_texto_livre_funciona_sem_cliente_cadastrado(session, owner, vendedor_interno):
    amostra = _amostra(session, owner, quantidade=2)
    total_clientes = len(session.exec(select(Cliente)).all())
    _saida(session, vendedor_interno, amostra, 1, cliente_texto="Feira Hospitality SP",
           motivo="Feira / evento")
    m = am.movimentos(session, amostra.id)[-1]
    assert m.cliente_id is None and m.cliente_texto == "Feira Hospitality SP"
    assert len(session.exec(select(Cliente)).all()) == total_clientes   # não criou cliente
    # sem cliente e sem texto, não dá para saber com quem está: recusa
    with pytest.raises(HTTPException) as erro:
        _saida(session, vendedor_interno, amostra, 1, cliente_texto="  ")
    assert erro.value.status_code == 400
    session.rollback()


# ---------------------------------------------------------------------------
# telas e navegação
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("papel_fixture", ["vendedor_interno", "vendedor_comissionado", "admin", "owner"])
def test_aba_amostras_abre_para_todos_os_papeis(session, owner, request, papel_fixture):
    pessoa = request.getfixturevalue(papel_fixture)
    amostra = _amostra(session, owner, quantidade=4)
    _saida(session, owner, amostra, 1, cliente_texto="Hotel Lista")
    pagina = rotas.lista(RequestFalsa(pessoa), session=session).body.decode()
    for texto in ("Disponíveis", "Em circulação", "Produtos com amostra", "Ver / movimentar",
                  "Hotel Lista · hoje", 'href="/amostras"'):
        assert texto in pagina
    assert ('href="/admin/amostras"' in pagina) == (papel_fixture in ("admin", "owner"))


def test_admin_amostras_busca_no_catalogo_e_marca_quem_ja_esta(session, owner):
    amostra = _amostra(session, owner)
    produto = session.get(Produto, amostra.produto_id)
    pagina = rotas.admin_lista(RequestFalsa(owner), q=produto.nome, session=session).body.decode()
    assert "Já está no controle de amostras." in pagina
    assert "Registrar entrada" in pagina and "Ajuste administrativo" in pagina


def test_nao_existe_rota_para_apagar_ou_editar_movimentacao():
    """O módulo inteiro são estas nove rotas: nenhuma apaga, edita ou sobrescreve saldo."""
    from app.main import app
    caminhos = {(r.path, m) for r in rotas.router.routes for m in r.methods}
    assert caminhos == {
        ("/amostras", "GET"), ("/amostras/{amostra_id}", "GET"),
        ("/amostras/{amostra_id}/saida", "POST"), ("/amostras/{amostra_id}/retorno", "POST"),
        ("/admin/amostras", "GET"), ("/admin/amostras", "POST"),
        ("/admin/amostras/entrada", "POST"), ("/admin/amostras/ajuste", "POST"),
        ("/admin/amostras/avulsa", "POST"),
    }
    # e o app serve o módulo
    assert app.url_path_for("admin_ajuste") == "/admin/amostras/ajuste"
    assert app.url_path_for("retorno", amostra_id=1) == "/amostras/1/retorno"


# ---------------------------------------------------------------------------
# amostra avulsa: peça que não existe no catálogo (01/10/2026)
# ---------------------------------------------------------------------------
def _avulsa(session, ator, nome, especificacao="", quantidade=0):
    r = rotas.admin_adicionar_avulsa(RequestFalsa(ator), nome=nome, especificacao=especificacao,
                                     quantidade_inicial=str(quantidade), observacao="", session=session)
    assert r.status_code == 303
    return session.exec(select(AmostraProduto).where(AmostraProduto.nome == nome)).one()


def test_13_avulsa_entra_sem_produto_e_movimenta_como_as_outras(session, owner, vendedor_interno):
    nome = f"Travesseiro macio 50x70 {uuid.uuid4().hex[:6]}"
    amostra = _avulsa(session, owner, nome, "233 fios percal · enchimento 800 g", quantidade=3)
    assert amostra.produto_id is None and _saldo(session, amostra) == (3, 0)
    linha = next(l for l in am.painel(session)["linhas"] if l["id"] == amostra.id)
    assert (linha["nome"], linha["especificacao"], linha["sku"]) == (
        nome, "233 fios percal · enchimento 800 g", "")
    _saida(session, vendedor_interno, amostra, 1, cliente_texto="Hotel Avulsa")
    assert _saldo(session, amostra) == (2, 1)
    pagina = rotas.detalhe(RequestFalsa(vendedor_interno), amostra.id, session=session).body.decode()
    assert nome in pagina and "fora do catálogo" in pagina
    lista = rotas.lista(RequestFalsa(vendedor_interno), session=session).body.decode()
    assert nome in lista


def test_14_avulsa_exige_nome_e_nao_duplica(session, owner):
    nome = f"Protetor de travesseiro 52x72 {uuid.uuid4().hex[:6]}"
    _avulsa(session, owner, nome, "233 fios percal · com zíper", quantidade=3)
    with pytest.raises(HTTPException) as erro:
        rotas.admin_adicionar_avulsa(RequestFalsa(owner), nome=nome.upper(),
                                     especificacao="233 FIOS PERCAL · COM ZÍPER",
                                     quantidade_inicial="1", observacao="", session=session)
    assert erro.value.status_code == 409
    session.rollback()
    with pytest.raises(HTTPException) as erro:
        rotas.admin_adicionar_avulsa(RequestFalsa(owner), nome="  ", especificacao="x",
                                     quantidade_inicial="1", observacao="", session=session)
    assert erro.value.status_code == 400
    session.rollback()


@pytest.mark.parametrize("papel_fixture", ["vendedor_interno", "vendedor_comissionado"])
def test_15_vendedora_nao_cria_avulsa(session, request, papel_fixture):
    with pytest.raises(HTTPException) as erro:
        rotas.admin_adicionar_avulsa(RequestFalsa(request.getfixturevalue(papel_fixture)),
                                     nome="Qualquer", especificacao="", quantidade_inicial="1",
                                     observacao="", session=session)
    assert erro.value.status_code == 403

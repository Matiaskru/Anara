"""Alçada de aprovação é permissão própria — não vem junto com acesso econômico.

A Sessão 6 separou três coisas que é tentador tratar como uma só:

* **ver** a economia (`ve_economia`) — custo, margem, lucro;
* **versionar** a premissa econômica (`can_manage_economics`) — mexer no câmbio, no custo;
* **decidir** sobre exceção comercial (`can_approve_quotes`) — autorizar o desconto.

Manter o cadastro do câmbio não autoriza abrir mão de receita numa venda. Estes testes
existem porque a fronteira é fácil de apagar sem querer: o menu chegou a oferecer a fila de
aprovações a quem via economia, o que sugeria alçada a quem não tem nenhuma.

A autorização de verdade está em `workflow_service._exigir_alcada`, no servidor. O critério
de menu é consequência dela, não substituto — e a fila continua legível em modo consulta
para os papéis econômicos, porque mostra preço e margem e há quem precise acompanhar sem
decidir.
"""
import pytest
from fastapi import HTTPException

from app.models import Papel
from app.permissoes import aprova_cotacoes, gerencia_economia, ve_economia
from conftest import RequestFalsa, _novo_usuario


def _usuario(papel, **flags):
    u = _novo_usuario(papel)
    for chave, valor in flags.items():
        setattr(u, chave, valor)
    return u


# ---------------------------------------------------------------------------
# Quem tem alçada
# ---------------------------------------------------------------------------
def test_owner_tem_alcada():
    u = _usuario("OWNER")
    assert u.aprova_cotacoes is True
    assert aprova_cotacoes(RequestFalsa(u)) is True


def test_owner_tem_alcada_mesmo_com_a_flag_desligada():
    """OWNER nunca fica sem alçada — senão um sistema com um só dono trava."""
    u = _usuario("OWNER", can_approve_quotes=False)
    assert u.aprova_cotacoes is True


def test_admin_com_a_flag_tem_alcada():
    u = _usuario("ADMIN", can_approve_quotes=True)
    assert u.aprova_cotacoes is True
    assert aprova_cotacoes(RequestFalsa(u)) is True


def test_admin_sem_a_flag_nao_tem_alcada():
    """O caso central: vê economia, e ainda assim não aprova."""
    u = _usuario("ADMIN", can_approve_quotes=False)
    req = RequestFalsa(u)
    assert u.aprova_cotacoes is False
    assert aprova_cotacoes(req) is False
    assert ve_economia(req) is True, "acesso econômico não deveria ter sido afetado"


def test_gerir_economia_nao_concede_alcada():
    """`can_manage_economics` versiona premissa. Não aprova desconto."""
    u = _usuario("ADMIN", can_manage_economics=True, can_approve_quotes=False)
    req = RequestFalsa(u)
    assert gerencia_economia(req) is True
    assert aprova_cotacoes(req) is False


def test_gerir_usuarios_nao_concede_alcada():
    u = _usuario("ADMIN", can_manage_users=True, can_approve_quotes=False)
    assert u.gerencia_usuarios is True
    assert u.aprova_cotacoes is False


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_vendedor_nao_tem_alcada(papel):
    u = _usuario(papel)
    req = RequestFalsa(u)
    assert u.aprova_cotacoes is False
    assert aprova_cotacoes(req) is False
    assert ve_economia(req) is False


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_flag_ligada_em_vendedor_nao_concede_alcada(papel):
    """A flag é granular **dentro** de ADMIN. Não promove vendedor."""
    u = _usuario(papel, can_approve_quotes=True)
    assert u.aprova_cotacoes is False
    assert aprova_cotacoes(RequestFalsa(u)) is False


def test_usuario_desativado_perde_alcada():
    u = _usuario("OWNER", ativo=False)
    assert u.aprova_cotacoes is False
    assert aprova_cotacoes(RequestFalsa(u)) is False


def test_sem_sessao_nao_tem_alcada():
    assert aprova_cotacoes(RequestFalsa(None)) is False


# ---------------------------------------------------------------------------
# A guarda do servidor
# ---------------------------------------------------------------------------
def test_guarda_do_servidor_recusa_quem_nao_tem_alcada():
    from app.workflow_service import _exigir_alcada

    for u in (_usuario("ADMIN", can_approve_quotes=False),
              _usuario("VENDEDOR_INTERNO"),
              _usuario("VENDEDOR_COMISSIONADO"),
              _usuario("OWNER", ativo=False)):
        with pytest.raises(HTTPException) as erro:
            _exigir_alcada(u)
        assert erro.value.status_code == 403


def test_guarda_do_servidor_aceita_quem_tem():
    from app.workflow_service import _exigir_alcada

    _exigir_alcada(_usuario("OWNER"))
    _exigir_alcada(_usuario("ADMIN", can_approve_quotes=True))


def test_decidir_chama_a_guarda_antes_de_qualquer_coisa():
    """A checagem é a primeira linha — não depois de já ter mexido no pedido."""
    import ast
    import inspect
    import textwrap
    from app import workflow_service as ws

    arvore = ast.parse(textwrap.dedent(inspect.getsource(ws.decidir)))
    corpo = arvore.body[0].body
    # a docstring é a primeira expressão; o que interessa é o primeiro comando de verdade
    if isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant):
        corpo = corpo[1:]
    primeiro = ast.unparse(corpo[0])
    assert primeiro == "_exigir_alcada(ator)", (
        f"a alçada deveria ser conferida antes de tudo; primeiro comando: {primeiro}")


# ---------------------------------------------------------------------------
# O vendedor continua conseguindo pedir
# ---------------------------------------------------------------------------
def test_rota_de_solicitar_exige_apenas_autenticacao():
    """Pedir aprovação é do vendedor. Se exigisse alçada, a cotação dele travaria."""
    import inspect
    from app.routers import workflow as rota

    corpo = inspect.getsource(rota.solicitar)
    assert "exigir_autenticado(request)" in corpo
    assert "exigir_economia" not in corpo
    assert "_exigir_alcada" not in corpo


def test_solicitar_aprovacao_nao_exige_alcada():
    import inspect
    from app import workflow_service as ws

    corpo = inspect.getsource(ws.solicitar_aprovacao)
    assert "_exigir_alcada" not in corpo, "o vendedor precisa conseguir pedir"


# ---------------------------------------------------------------------------
# O menu
# ---------------------------------------------------------------------------
def test_menu_usa_alcada_e_nao_acesso_economico():
    import os

    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app", "templates", "base.html")
    html = open(caminho, encoding="utf-8").read()
    assert '<a href="/aprovacoes"' in html, "o item de menu sumiu"
    guarda = html.split('<a href="/aprovacoes"')[0].strip().splitlines()[-1]
    assert "aprova_cotacoes(request)" in guarda, f"guarda errada: {guarda}"
    assert "ve_economia(request)" not in guarda

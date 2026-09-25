"""A matriz de acesso completa — servidor e interface, os cinco perfis.

Não substitui `test_seguranca_rbac.py`, que cobre o corte de payload campo a campo. Aqui a
pergunta é outra e é a do produto: **cada perfil consegue fazer o seu trabalho, e só o seu?**

Os cinco perfis, porque três deles compartilham o papel ADMIN e diferem só nas flags:

* OWNER — pode tudo, e nenhuma flag lhe tira nada;
* ADMIN econômico sem alçada — administra premissa, **não** aprova desconto;
* ADMIN aprovador — aprova;
* VENDEDOR_INTERNO e VENDEDOR_COMISSIONADO — vendem, pedem aprovação, não veem economia.
"""
import pytest

from app.permissoes import (
    administra, aprova_cotacoes, gerencia_economia, gerencia_usuarios, ve_economia,
)
from conftest import RequestFalsa, _novo_usuario


def _perfil(papel, **flags):
    u = _novo_usuario(papel)
    for chave, valor in flags.items():
        setattr(u, chave, valor)
    return u


PERFIS = {
    "owner": lambda: _perfil("OWNER"),
    "admin_economico": lambda: _perfil("ADMIN", can_manage_economics=True,
                                       can_approve_quotes=False),
    "admin_aprovador": lambda: _perfil("ADMIN", can_approve_quotes=True),
    "vendedor_interno": lambda: _perfil("VENDEDOR_INTERNO"),
    "vendedor_comissionado": lambda: _perfil("VENDEDOR_COMISSIONADO"),
}

#: A matriz, escrita uma vez. Cada célula é uma afirmação sobre o produto, não sobre o código.
MATRIZ = {
    #                        econ   admin  gere_econ  alçada  usuários
    "owner":                 (True,  True,  True,      True,   True),
    "admin_economico":       (True,  True,  True,      False,  False),
    "admin_aprovador":       (True,  True,  True,      True,   False),
    "vendedor_interno":      (False, False, False,     False,  False),
    "vendedor_comissionado": (False, False, False,     False,  False),
}


@pytest.mark.parametrize("perfil", sorted(PERFIS))
def test_matriz_de_permissoes(perfil):
    req = RequestFalsa(PERFIS[perfil]())
    econ, adm, gere, alcada, users = MATRIZ[perfil]

    assert ve_economia(req) is econ, f"{perfil}: acesso econômico"
    assert administra(req) is adm, f"{perfil}: administração"
    assert gerencia_economia(req) is gere, f"{perfil}: versionar premissa"
    assert aprova_cotacoes(req) is alcada, f"{perfil}: alçada de aprovação"
    assert gerencia_usuarios(req) is users, f"{perfil}: gestão de usuários"


@pytest.mark.parametrize("perfil", ["vendedor_interno", "vendedor_comissionado"])
def test_vendedor_nao_ve_economia_em_lugar_nenhum(perfil):
    """Inclusive o comissionado: a comissão é calculada no servidor e não lhe é exibida."""
    req = RequestFalsa(PERFIS[perfil]())
    assert ve_economia(req) is False
    assert administra(req) is False


def test_admin_economico_administra_mas_nao_aprova():
    """O caso que mais confunde, e o que a Sessão 6 separou de propósito."""
    req = RequestFalsa(PERFIS["admin_economico"]())
    assert gerencia_economia(req) is True
    assert aprova_cotacoes(req) is False


def test_gerir_usuarios_nao_concede_economia():
    req = RequestFalsa(_perfil("ADMIN", can_manage_users=True,
                               can_manage_economics=False, can_approve_quotes=False))
    assert gerencia_usuarios(req) is True
    assert gerencia_economia(req) is False
    assert aprova_cotacoes(req) is False


@pytest.mark.parametrize("perfil", sorted(PERFIS))
def test_desativado_perde_tudo(perfil):
    u = PERFIS[perfil]()
    u.ativo = False
    req = RequestFalsa(u)
    assert not any((ve_economia(req), administra(req), gerencia_economia(req),
                    aprova_cotacoes(req), gerencia_usuarios(req)))


def test_sem_sessao_nao_tem_nada():
    req = RequestFalsa(None)
    assert not any((ve_economia(req), administra(req), gerencia_economia(req),
                    aprova_cotacoes(req), gerencia_usuarios(req)))


# ---------------------------------------------------------------------------
# O que a interface oferece a cada um
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("perfil,vê_admin,vê_aprovacoes", [
    ("owner", True, True),
    ("admin_economico", True, False),
    ("admin_aprovador", True, True),
    ("vendedor_interno", False, False),
    ("vendedor_comissionado", False, False),
])
def test_menu_oferece_o_que_a_pessoa_pode_usar(perfil, vê_admin, vê_aprovacoes):
    """Anunciar uma tela que a pessoa não pode operar é oferecer trabalho impossível."""
    from app.templating import templates

    html = templates.env.get_template("base.html").render(
        request=RequestFalsa(PERFIS[perfil]()), active="")
    assert ('href="/admin"' in html) is vê_admin, f"{perfil}: item Admin"
    assert ('href="/aprovacoes"' in html) is vê_aprovacoes, f"{perfil}: item Aprovações"


@pytest.mark.parametrize("perfil", sorted(PERFIS))
def test_menu_nunca_mostra_ferramenta_tecnica(perfil):
    from app.templating import templates

    html = templates.env.get_template("base.html").render(
        request=RequestFalsa(PERFIS[perfil]()), active="")
    nav = html.split("<nav>")[1].split("</nav>")[0]
    # `/calculadora` saiu desta lista em 25/09/2026. Ela era ferramenta técnica quando só o
    # administrador a usava; desde 22/09 é rota de OPERAÇÃO — a vendedora monta ali o produto
    # que não está no catálogo para conseguir cotar, e recebe a resposta sem economia
    # (`CAMPOS_RESULTADO_COMERCIAL`). Esconder do menu obrigava a abrir uma cotação antes de
    # simular. As demais continuam técnicas: importar, saúde, configurações e trilha não são
    # tarefa de quem vende.
    for tecnica in ("/importar", "/saude", "/configuracoes",
                    "/admin/trilha", "/admin/usuarios"):
        assert f'href="{tecnica}"' not in nav


# ---------------------------------------------------------------------------
# Confidencialidade — o corte é do servidor
# ---------------------------------------------------------------------------
def test_payload_comercial_nao_leva_economia():
    from app.confidencial import encontrar_confidenciais, item_comercial

    completo = {
        "id": 1, "nome_produto": "Lençol", "quantidade": 10,
        "preco_negociado": 197.36, "faturamento": 1973.60,
        "custo_unitario": 59.61, "lucro": 300.0, "margem_liquida": 0.18,
        "markup_implicito": 0.4, "memoria_json": "{}",
    }
    comercial = item_comercial(completo)
    assert encontrar_confidenciais(comercial) == []
    assert "preco_negociado" in comercial and "custo_unitario" not in comercial


def test_lista_e_de_permissao_nao_de_bloqueio():
    """Campo novo não vaza por esquecimento: o que não está declarado não passa."""
    from app.confidencial import item_comercial

    com_campo_novo = {"id": 1, "nome_produto": "X", "custo_medio_ponderado": 42.0}
    assert "custo_medio_ponderado" not in item_comercial(com_campo_novo)

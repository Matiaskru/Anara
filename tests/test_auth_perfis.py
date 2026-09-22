"""Hardening de acesso por perfil, login e recuperação de senha (17/09/2026).

Os 23 pontos do enunciado, na ordem: destino por papel, bloqueio de rotas administrativas e
econômicas para a vendedora, zero economia em HTML e JSON, comissão própria visível, admin
com economia, "cara de teste" fora da interface, login sem seletor de papel, esqueci-senha
genérico, token (válido, vencido, usado, em hash), senha antiga × nova, gestão de usuários e
AuditLog sem segredo.
"""
import inspect
import json
import re
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import auth
from app import crm_service as crm
from app import mail
from app import recuperacao_senha as rs
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.models import AuditLog, Cliente, CostMethod, Fornecedor, Papel, PasswordResetToken, Produto, Usuario
from conftest import RequestFalsa, _novo_usuario

_SEQ = iter(range(1, 100_000))
ECONOMIA = ("margem", "lucro", "custo", "cnet", "exw", "markup", "piso", "commission_max",
            "comissao_max", "memória do preço", "economia", "premissa")


def chamar(funcao, request, **kwargs):
    args = {}
    for nome, p in inspect.signature(funcao).parameters.items():
        if nome == "request":
            args[nome] = request
            continue
        if nome in kwargs:
            args[nome] = kwargs[nome]
            continue
        padrao = p.default
        v = getattr(padrao, "default", padrao)
        args[nome] = None if (v is inspect.Parameter.empty or repr(v) == "PydanticUndefined") else v
    return funcao(**args)


def corpo(r):
    return json.loads(bytes(r.body).decode())


def html(r):
    return bytes(r.body).decode()


def visivel(pagina: str) -> str:
    """Texto que a pessoa lê: sem `value=`/`name=`/`href=` de formulário e sem scripts."""
    sem_scripts = re.sub(r"<script\b.*?</script>", " ", pagina, flags=re.S | re.I)
    return re.sub(r'(value|name|href|action|id|class|data-[\w-]+)="[^"]*"', " ", sem_scripts).lower()


@pytest.fixture
def owner(session):
    u = session.exec(select(Usuario).where(Usuario.email == "auth-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="auth-owner@anara.test", nome="Dona Auth", papel="OWNER",
                    senha_hash=auth.hash_senha("senha-da-dona-2026"), can_approve_quotes=True)
        session.add(u); session.commit(); session.refresh(u)
    return u


@pytest.fixture
def vendedora(session):
    u = session.exec(select(Usuario).where(Usuario.email == "auth-vend@anara.test")).first()
    if u is None:
        u = Usuario(email="auth-vend@anara.test", nome="Vendedora Auth", papel="VENDEDOR_INTERNO",
                    senha_hash=auth.hash_senha("senha-da-vend-2026"))
        session.add(u); session.commit(); session.refresh(u)
    return u


@pytest.fixture
def mail_memoria(monkeypatch):
    monkeypatch.setenv("ANARA_MAIL_BACKEND", "memoria")
    mail.ENVIADAS.clear()
    yield mail.ENVIADAS
    mail.ENVIADAS.clear()


def cenario(session, owner, vendedora):
    forn = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    cliente = crm.criar_cliente(session, ator=owner, nome=f"Hotel Auth {next(_SEQ)}",
                                cidade_uf="São Paulo", finalidade="REVENDA")
    session.commit()
    op = crm.criar_oportunidade(session, ator=vendedora, cliente_id=cliente.id,
                                titulo=f"Projeto Auth {next(_SEQ)}", responsavel_id=vendedora.id)
    session.commit()
    sku = f"AUTH-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Peseira {sku}", custo_unitario=377.11, preco_base=600.0,
                fornecedor_id=forn["DECOR_TRICOT"].id, familia="Bed Runner",
                cost_method=CostMethod.national_supplier.value)
    session.add(p); session.commit(); session.refresh(p)
    from app.routers.cotacoes import adicionar_item, criar_cotacao_da_venda
    cot = criar_cotacao_da_venda(session, RequestFalsa(vendedora), op, ator=vendedora,
                                 estado_destino="São Paulo", contribuinte_icms=True,
                                 freight_type="FOB", condicao_pagamento="30")
    cot.uf_origem_fiscal = "SP"; cot.finalidade = "REVENDA"
    session.add(cot); session.commit(); session.refresh(cot)
    chamar(adicionar_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
           produto_id=p.id, quantidade=10.0, modo="margem", valor=None, session=session)
    session.commit()
    return cliente, op, cot, p


# ===========================================================================
# 1–4. Destino por papel e bloqueio de rotas
# ===========================================================================
def test_01_seller_login_cai_em_vendas(session, vendedora):
    from app.routers.login import login_submit
    r = chamar(login_submit, RequestFalsa(None), email=vendedora.email, senha="senha-da-vend-2026",
               next="/", session=session)
    assert r.status_code == 303 and r.headers["location"] == "/vendas"
    assert auth.COOKIE_NAME in r.headers.get("set-cookie", "")


def test_02_owner_login_cai_no_dashboard(session, owner):
    from app.routers.login import landing, login_submit
    r = chamar(login_submit, RequestFalsa(None), email=owner.email, senha="senha-da-dona-2026",
               next="/", session=session)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    assert landing(_novo_usuario("ADMIN")) == "/dashboard"
    assert landing(_novo_usuario("VENDEDOR_COMISSIONADO")) == "/vendas"


def test_03_seller_nao_acessa_dashboard(session, vendedora):
    from app.routers.dashboard import dashboard, raiz
    r = chamar(dashboard, RequestFalsa(vendedora), session=session)
    assert r.status_code == 303 and r.headers["location"] == "/vendas"
    r = chamar(raiz, RequestFalsa(vendedora), session=session)
    assert r.status_code == 303 and r.headers["location"] == "/vendas"
    r = chamar(raiz, RequestFalsa(_novo_usuario("OWNER")), session=session)
    assert r.headers["location"] == "/dashboard"


def test_04_seller_nao_acessa_admin_nem_endpoints_economicos(session, vendedora):
    from app.routers import admin, configuracoes, importar, relatorios_comerciais, usuarios, workflow
    from app.routers.cotacoes import memoria_item
    from app.routers.produtos import memoria
    # A calculadora saiu desta lista em 22/09/2026: a vendedora monta a cotação e precisa de
    # produto personalizado. O corte dela é o CONTEÚDO da resposta (resultado comercial), e
    # está em `tests/test_calculadora_vendedora_2026_09_22.py`.
    negados = [
        (admin.hub, {}), (admin.premissas if hasattr(admin, "premissas") else admin.hub, {}),
        (configuracoes.painel, {}),
        (relatorios_comerciais.economico, {}), (relatorios_comerciais.saude, {}),
        (relatorios_comerciais.health_detalhe, {}),
        (workflow.fila, {}), (usuarios.listar, {}),
        (memoria, {"produto_id": 1}), (memoria_item, {"cotacao_id": 1, "item_id": 1}),
    ]
    for rota, kw in negados:
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(vendedora), session=session, **kw)
        assert erro.value.status_code == 403, rota.__name__
    # importar: GET pode ser página; o corte é o mesmo
    for nome in ("pagina", "form", "tela"):
        rota = getattr(importar, nome, None)
        if rota:
            with pytest.raises(HTTPException):
                chamar(rota, RequestFalsa(vendedora), session=session)
            break


# ===========================================================================
# 5–9. Economia: zero para a vendedora, completa para o admin
# ===========================================================================
def test_05_06_07_seller_nao_recebe_economia_em_html_nem_json(session, owner, vendedora):
    from app.routers import clientes, cotacoes, negociacao, produtos, vendas, workflow
    from app.routers.relatorios_comerciais import relatorios
    cliente, op, cot, produto = cenario(session, owner, vendedora)
    telas = [
        (vendas.lista, {}), (vendas.lista, {"vista": "quadro"}), (vendas.detalhe, {"venda_id": op.id}),
        (clientes.listar, {}), (clientes.detalhe, {"cliente_id": cliente.id}),
        (cotacoes.listar, {}), (cotacoes.detalhe, {"cotacao_id": cot.id}),
        (cotacoes.painel_situacao, {"cotacao_id": cot.id}),
        # Produtos filtrado ao item deste cenário: outras suítes cadastram SKUs com nomes
        # como "SKU sem EXW", que são dado de teste, não economia vazada
        (produtos.listar, {"q": produto.nome}), (relatorios, {}),
    ]
    for rota, kw in telas:
        pagina = html(chamar(rota, RequestFalsa(vendedora), session=session, **kw))
        texto = visivel(pagina)
        for termo in ECONOMIA:
            assert termo not in texto, f"{rota.__name__}: vendedora leu '{termo}'"
        assert "377.11" not in pagina and "377,11" not in pagina, rota.__name__

    jsons = [
        (negociacao.negociacao_atual, {"cotacao_id": cot.id}),
        (workflow.situacao, {"cotacao_id": cot.id}),
        (produtos.buscar, {"q": "peseira"}), (produtos.facetas, {}),
        (vendas.vendas_do_cliente, {"cliente_id": cliente.id}),
    ]
    for rota, kw in jsons:
        dados = corpo(chamar(rota, RequestFalsa(vendedora), session=session, **kw))
        assert encontrar_confidenciais(dados) == [], rota.__name__
        # `sem_custo` é flag operacional da lista de permissão ("o item não forma preço"),
        # não um valor; sai da varredura por substring
        texto = json.dumps(dados, ensure_ascii=False).lower().replace("sem_custo", "")
        for termo in ("margem", "lucro", "custo", "cnet", "exw", "markup", "piso", "377.11"):
            assert termo not in texto, f"{rota.__name__}: JSON levou '{termo}'"
    # /calc (prévia de item) para a vendedora
    r = corpo(chamar(cotacoes.calc, RequestFalsa(vendedora), cotacao_id=cot.id,
                     produto_id=produto.id, quantidade=2.0, modo="margem", valor=0.0, session=session))
    assert encontrar_confidenciais(r) == [] and "377.11" not in json.dumps(r)


def test_08_seller_ve_a_propria_comissao_estimada(session, owner, vendedora):
    from app.routers import cotacoes, negociacao
    _c, _op, cot, _p = cenario(session, owner, vendedora)
    dados = corpo(chamar(negociacao.negociacao_atual, RequestFalsa(vendedora), cotacao_id=cot.id,
                         session=session))
    assert dados["comissao_estimada_valor"] > 0 and dados["comissao_estimada_pct_efetiva"] > 0
    assert dados["autonomia_status"] in ("DENTRO_DA_AUTONOMIA", "REQUER_APROVACAO")
    pagina = html(chamar(cotacoes.detalhe, RequestFalsa(vendedora), cotacao_id=cot.id, session=session))
    assert "Sua comissão estimada" in pagina and "Dentro da autonomia" in pagina


def test_09_owner_e_admin_continuam_vendo_economia(session, owner, vendedora):
    from app.routers import cotacoes, negociacao
    _c, _op, cot, _p = cenario(session, owner, vendedora)
    for papel in ("OWNER", "ADMIN"):
        dados = corpo(chamar(negociacao.negociacao_atual, RequestFalsa(_novo_usuario(papel)),
                             cotacao_id=cot.id, session=session))
        assert "economia" in dados and dados["economia"]["custo_total"] > 0
        pagina = html(chamar(cotacoes.detalhe, RequestFalsa(_novo_usuario(papel)), cotacao_id=cot.id,
                             session=session))
        assert "Economia da proposta" in pagina and ("377,11" in pagina or "377.11" in pagina)


# ===========================================================================
# 10–11. Interface limpa
# ===========================================================================
def test_10_cara_de_teste_nao_aparece_em_cotacoes(session, owner, vendedora):
    from app.routers.cotacoes import listar
    cenario(session, owner, vendedora)
    for papel in (vendedora, owner):
        pagina = html(chamar(listar, RequestFalsa(papel), session=session)).lower()
        for termo in ("cara de teste", "provável teste", "marcar como teste", "marcartestes", "data-teste"):
            assert termo not in pagina, termo


def test_11_login_nao_tem_seletor_de_papel():
    import os
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pagina = open(os.path.join(raiz, "app", "templates", "login.html"), encoding="utf-8").read().lower()
    for proibido in ("papel", "perfil", "administrador", "vendedor", "seller", "admin", "ambiente",
                     "porta", "banco", "debug", "<select"):
        assert proibido not in pagina, proibido
    assert "esqueci minha senha" in pagina and 'href="/esqueci-senha"' in pagina


# ===========================================================================
# 12–19. Esqueci minha senha e token
# ===========================================================================
def test_12_13_esqueci_senha_responde_igual_para_email_existente_e_inexistente(session, vendedora, mail_memoria):
    from app.routers.login import esqueci_senha_submit
    a = chamar(esqueci_senha_submit, RequestFalsa(None), email=vendedora.email, session=session)
    b = chamar(esqueci_senha_submit, RequestFalsa(None), email="ninguem@anara.test", session=session)
    assert a.status_code == b.status_code == 200
    assert rs.RESPOSTA_GENERICA in html(a) and rs.RESPOSTA_GENERICA in html(b)
    assert "não existe" not in html(b).lower() and "não encontrado" not in html(b).lower()
    # só a conta existente gerou e-mail; o token nunca aparece na página
    assert len(mail_memoria) == 1 and mail_memoria[0]["para"] == vendedora.email
    token = mail_memoria[0]["corpo"].split("token=")[1].split()[0]
    assert token not in html(a)


def test_14_18_19_token_valido_troca_a_senha_e_a_antiga_para_de_valer(session, vendedora, mail_memoria):
    from app.routers.login import login_submit, redefinir_senha_form, redefinir_senha_submit
    token = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    session.commit()
    assert html(chamar(redefinir_senha_form, RequestFalsa(None), token=token, session=session)).count("Nova senha") >= 1
    r = chamar(redefinir_senha_submit, RequestFalsa(None), token=token, senha="senha-nova-2026",
               confirmar="senha-nova-2026", session=session)
    assert r.status_code == 200 and "Senha redefinida" in html(r)
    session.refresh(vendedora)
    assert auth.senha_confere("senha-nova-2026", vendedora.senha_hash)
    assert not auth.senha_confere("senha-da-vend-2026", vendedora.senha_hash)
    antiga = chamar(login_submit, RequestFalsa(None), email=vendedora.email, senha="senha-da-vend-2026",
                    next="/", session=session)
    assert antiga.status_code == 401
    nova = chamar(login_submit, RequestFalsa(None), email=vendedora.email, senha="senha-nova-2026",
                  next="/", session=session)
    assert nova.status_code == 303 and nova.headers["location"] == "/vendas"
    # restaura para os outros testes
    vendedora.senha_hash = auth.hash_senha("senha-da-vend-2026")
    session.add(vendedora); session.commit()


def test_15_token_expirado_falha(session, vendedora):
    from app.routers.login import redefinir_senha_submit
    passado = datetime.utcnow() - timedelta(hours=2)
    token = rs.gerar(session, vendedora, rs.RESET, criado_por="teste", agora=passado)
    session.commit()
    assert rs.validar(session, token) is None
    r = chamar(redefinir_senha_submit, RequestFalsa(None), token=token, senha="qualquer-coisa-123",
               confirmar="qualquer-coisa-123", session=session)
    assert r.status_code == 400 and "não é válido ou já expirou" in html(r)


def test_16_token_usado_falha_e_o_anterior_cai_quando_nasce_outro(session, vendedora):
    token = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    session.commit()
    rs.consumir(session, token, "senha-usada-uma-vez-2026")
    session.commit()
    with pytest.raises(rs.TokenInvalido):
        rs.consumir(session, token, "outra-senha-2026")
    primeiro = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    segundo = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    session.commit()
    assert rs.validar(session, primeiro) is None and rs.validar(session, segundo) is not None
    vendedora.senha_hash = auth.hash_senha("senha-da-vend-2026")
    session.add(vendedora); session.commit()


def test_17_token_fica_em_hash_no_banco(session, vendedora):
    token = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    session.commit()
    registros = session.exec(select(PasswordResetToken)
                             .where(PasswordResetToken.usuario_id == vendedora.id)).all()
    assert registros and all(token not in (r.token_hash or "") for r in registros)
    assert all(len(r.token_hash) == 64 for r in registros)         # sha256 hex
    assert rs.validar(session, token) is not None
    assert rs.validar(session, token + "x") is None


# ===========================================================================
# 20–22. Usuários
# ===========================================================================
def test_20_seller_nao_administra_usuarios(session, vendedora):
    from app.routers.usuarios import criar, iniciar_redefinicao, listar
    for rota, kw in ((listar, {}), (criar, {"nome": "X", "email": "x@anara.test", "papel": "VENDEDOR_INTERNO", "senha": ""}),
                     (iniciar_redefinicao, {"usuario_id": vendedora.id})):
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(vendedora), session=session, **kw)
        assert erro.value.status_code == 403


def test_21_admin_cria_sem_senha_com_link_de_primeiro_acesso_e_desativa(session, owner, mail_memoria):
    from app.routers.login import login_submit
    from app.routers.usuarios import criar, iniciar_redefinicao, mudar_situacao
    email = f"nova-{next(_SEQ)}@anara.test"
    r = chamar(criar, RequestFalsa(owner), nome="Nova Vendedora", email=email,
               papel=Papel.vendedor_comissionado.value, senha="", session=session)
    assert r.status_code == 303 and "ok=criado_link" in r.headers["location"]
    nova = session.exec(select(Usuario).where(Usuario.email == email)).first()
    assert nova and nova.papel == "VENDEDOR_COMISSIONADO" and nova.ativo
    assert len(mail_memoria) == 1 and "defina sua senha" in mail_memoria[0]["assunto"].lower()
    token = mail_memoria[0]["corpo"].split("token=")[1].split()[0]
    # antes de definir a senha, nada entra
    assert chamar(login_submit, RequestFalsa(None), email=email, senha="", next="/", session=session).status_code == 401
    rs.consumir(session, token, "minha-primeira-senha")
    session.commit()
    assert chamar(login_submit, RequestFalsa(None), email=email, senha="minha-primeira-senha",
                  next="/", session=session).headers["location"] == "/vendas"
    # o gestor pede redefinição: e-mail sai, sem senha passar por ele
    r = chamar(iniciar_redefinicao, RequestFalsa(owner), usuario_id=nova.id, session=session)
    assert r.status_code == 303 and len(mail_memoria) == 2
    # desativar derruba a sessão e o login
    r = chamar(mudar_situacao, RequestFalsa(owner), usuario_id=nova.id, ativo="nao", session=session)
    assert r.status_code == 303
    session.refresh(nova)
    assert nova.ativo is False
    assert chamar(login_submit, RequestFalsa(None), email=email, senha="minha-primeira-senha",
                  next="/", session=session).status_code == 401


def test_22_auditlog_nao_guarda_senha_nem_token(session, vendedora, mail_memoria):
    token = rs.gerar(session, vendedora, rs.RESET, criado_por="teste")
    rs.consumir(session, token, "segredo-que-nao-pode-vazar-2026")
    session.commit()
    linhas = session.exec(select(AuditLog).where(AuditLog.entidade == "Usuario")).all()
    assert any(l.acao == "PASSWORD_RESET" for l in linhas)
    despejo = " ".join(f"{l.versao_anterior} {l.versao_nova} {l.motivo} {l.detalhe} {l.escopo}" for l in linhas)
    assert token not in despejo and "segredo-que-nao-pode-vazar" not in despejo
    assert rs._hash(token) not in despejo
    vendedora.senha_hash = auth.hash_senha("senha-da-vend-2026")
    session.add(vendedora); session.commit()


# ===========================================================================
# 23. Regressão: a política comercial continua a mesma debaixo do hardening
# ===========================================================================
def test_23_sessao_cookie_e_producao(monkeypatch):
    from fastapi.responses import Response
    r = auth.aplicar_cookie(Response(), 1, 1)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert ("secure" in cookie) is auth.PRODUCAO
    # produção sem SMTP: recuperação por e-mail fica declaradamente não operacional
    monkeypatch.delenv("ANARA_MAIL_HOST", raising=False)
    monkeypatch.delenv("ANARA_MAIL_BACKEND", raising=False)
    monkeypatch.setattr(auth, "PRODUCAO", True)
    assert mail.backend() == "indisponivel" and mail.situacao()["operacional"] is False
    with pytest.raises(mail.MailNaoConfigurado):
        mail.enviar("x@anara.test", "a", "b")
    monkeypatch.setattr(auth, "PRODUCAO", False)
    assert mail.backend() == "dev"

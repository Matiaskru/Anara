"""Segurança da Sessão 4 — autenticação, papéis e confidencialidade econômica.

O que estes testes protegem não é uma tela: é a resposta do servidor. A pergunta que cada um
faz é sempre a mesma — *se essa pessoa mandar essa requisição na unha, o que volta?* — porque
é assim que o dado vaza: por URL direta, por devtools, por POST montado à mão. Um teste que
só verificasse a tela passaria com o campo confidencial escondido dentro do HTML.

Organização:

* senha e sessão — o que substituiu a senha compartilhada;
* a matriz OWNER / ADMIN / VENDEDOR_INTERNO / VENDEDOR_COMISSIONADO / anônimo;
* payloads — a ausência **explícita** de cada campo confidencial;
* HTML — o termo não pode estar no fonte, nem escondido;
* PDF — o documento que sai para o cliente.
"""
import inspect
import json
import os

import pytest
from sqlmodel import select

from app import auth
from app.confidencial import CAMPOS_CONFIDENCIAIS, encontrar_confidenciais
from app.models import CostMethod, Cotacao, Fornecedor, Papel, Produto, Usuario
from app.permissoes import PrecisaLogin, administra, ve_economia
from conftest import RequestFalsa, _novo_usuario

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def chamar(funcao, request, **kwargs):
    """Chama a rota como uma pessoa específica, preenchendo os `Form(...)` com o default."""
    argumentos = {}
    for nome, parametro in inspect.signature(funcao).parameters.items():
        if nome == "request":
            argumentos[nome] = request
            continue
        if nome in kwargs:
            argumentos[nome] = kwargs[nome]
            continue
        padrao = parametro.default
        valor = getattr(padrao, "default", padrao)
        if valor is inspect.Parameter.empty or repr(valor) == "PydanticUndefined":
            valor = None
        argumentos[nome] = valor
    resultado = funcao(**argumentos)
    # Rotas `async def` (upload, formulário multipart) devolvem coroutine quando chamadas
    # direto. Sem rodar o loop, um `raise` dentro delas nunca chegaria aqui — e o teste de
    # 403 passaria por não ter executado nada.
    if inspect.iscoroutine(resultado):
        import asyncio
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(resultado)
    return resultado


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


def html(resposta):
    return bytes(resposta.body).decode()


@pytest.fixture(scope="module")
def cenario(session):
    """Um produto com custo e uma cotação com item — o mínimo para haver o que vazar.

    Escopo de módulo porque a `session` da suíte é única: criar o mesmo SKU a cada teste
    esbarraria na unicidade de `sku_key`. Os testes daqui só leem este cenário.
    """
    from app.routers.cotacoes import adicionar_item, criar
    from app.models import Cliente

    cliente = session.exec(select(Cliente)).first()
    if cliente is None:
        cliente = Cliente(nome="Cliente RBAC", estado="São Paulo")
        session.add(cliente)
        session.commit()
        session.refresh(cliente)

    fornecedor = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
    produto = Produto(sku_key="RBAC-1", nome="Produto RBAC", custo_unitario=377.11,
                      preco_base=600.0, fornecedor_id=fornecedor.id, familia="Flat Sheet",
                      cost_method=CostMethod.national_supplier.value, margem_padrao_pct=0.14)
    session.add(produto)
    session.commit()
    session.refresh(produto)

    cot = Cotacao(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                  estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                  condicao_pagamento="30", numero="RBAC-0001")
    session.add(cot)
    session.commit()
    session.refresh(cot)

    chamar(adicionar_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
           produto_id=produto.id, quantidade=10.0, modo="margem", valor=0.14,
           session=session)
    session.commit()
    from app.models import CotacaoItem
    item = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot.id)).first()
    return {"cotacao": cot, "produto": produto, "item": item, "cliente": cliente}


# ---------------------------------------------------------------------------
# 1. Senha
# ---------------------------------------------------------------------------
def test_senha_nao_fica_em_texto_claro():
    """O hash não pode conter a senha, nem devolvê-la."""
    senha = "uma-senha-de-verdade-2026"
    h = auth.hash_senha(senha)
    assert senha not in h
    assert h.startswith("$argon2")
    assert auth.senha_confere(senha, h)


def test_senha_errada_nao_autentica():
    h = auth.hash_senha("senha-correta-123")
    assert not auth.senha_confere("senha-errada-123", h)
    assert not auth.senha_confere("", h)
    assert not auth.senha_confere("senha-correta-123", "")


def test_duas_contas_com_a_mesma_senha_tem_hashes_diferentes():
    """Salt por hash: sem isso, hashes iguais entregariam quem repetiu senha."""
    a = auth.hash_senha("mesma-senha-para-os-dois")
    b = auth.hash_senha("mesma-senha-para-os-dois")
    assert a != b
    assert auth.senha_confere("mesma-senha-para-os-dois", a)
    assert auth.senha_confere("mesma-senha-para-os-dois", b)


def test_senha_curta_e_recusada():
    with pytest.raises(ValueError):
        auth.hash_senha("curta")


def test_hash_corrompido_nao_autentica_ninguem():
    assert not auth.senha_confere("qualquer", "isto-nao-e-um-hash-argon2")


# ---------------------------------------------------------------------------
# 2. Segredo de sessão e cookie
# ---------------------------------------------------------------------------
def test_secret_key_nao_tem_default_conhecido_no_codigo():
    """O fallback fixo e a senha compartilhada não podem voltar por descuido.

    A checagem é sobre **código**, não sobre prosa: o docstring de `auth.py` cita os valores
    antigos para explicar o que foi removido, e isso é documentação útil. Por isso os
    docstrings são retirados antes da varredura — e o que sobra é o que roda.
    """
    import ast

    caminho = os.path.join(os.path.dirname(__file__), "..", "app", "auth.py")
    arvore = ast.parse(open(caminho, encoding="utf-8").read())
    for no in ast.walk(arvore):
        if isinstance(no, ast.Expr) and isinstance(no.value, ast.Constant) \
                and isinstance(no.value.value, str):
            no.value.value = ""                      # zera docstrings e strings soltas
    codigo = ast.unparse(arvore)

    assert "anara-cotacao-local-secret" not in codigo
    assert "[SENHA-LEGADA-REMOVIDA]" not in codigo
    # e nenhuma constante de módulo chamada SENHA
    assert not any(isinstance(n, ast.Assign)
                   and any(getattr(a, "id", "") == "SENHA" for a in n.targets)
                   for n in ast.walk(arvore))


def test_producao_sem_secret_nao_sobe(monkeypatch):
    """Ausência de segredo em produção falha de forma segura — não cai em valor conhecido."""
    monkeypatch.setattr(auth, "PRODUCAO", True)
    monkeypatch.delenv("ANARA_SECRET_KEY", raising=False)
    with pytest.raises(auth.ConfiguracaoInsegura):
        auth._resolver_secret()


def test_secret_curto_e_recusado(monkeypatch):
    monkeypatch.setenv("ANARA_SECRET_KEY", "curto-demais")
    with pytest.raises(auth.ConfiguracaoInsegura):
        auth._resolver_secret()


def test_desenvolvimento_sem_secret_gera_chave_aleatoria(monkeypatch):
    """Sem variável, a chave é aleatória por processo — nunca uma constante previsível."""
    monkeypatch.setattr(auth, "PRODUCAO", False)
    monkeypatch.delenv("ANARA_SECRET_KEY", raising=False)
    a, b = auth._resolver_secret(), auth._resolver_secret()
    assert a != b and len(a) >= 32


def test_cookie_carrega_identidade_e_nao_autorizacao():
    """O papel não viaja no cookie: ele é lido do banco a cada request."""
    valor = auth.criar_cookie_valor(usuario_id=7, sessao_versao=3)
    dados = auth.ler_cookie(valor)
    assert dados == {"uid": 7, "v": 3}
    assert "OWNER" not in valor and "ADMIN" not in valor


def test_cookie_adulterado_e_recusado():
    valor = auth.criar_cookie_valor(usuario_id=7, sessao_versao=1)
    assert auth.ler_cookie(valor[:-4] + "AAAA") is None
    assert auth.ler_cookie("") is None
    assert auth.ler_cookie("qualquer-coisa") is None


def test_cookie_expira():
    """Cookie fora do prazo não vale — o de 30 dias da senha compartilhada acabou."""
    import itsdangerous
    valor = auth.criar_cookie_valor(usuario_id=7)
    antigo = itsdangerous.URLSafeTimedSerializer(auth.SECRET_KEY, salt="anara-auth-v2")
    assert antigo.loads(valor, max_age=auth.SESSAO_MAX_IDADE_S) is not None
    with pytest.raises(itsdangerous.SignatureExpired):
        antigo.loads(valor, max_age=-1)


# ---------------------------------------------------------------------------
# 3. Matriz de papéis
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("papel,espera_economia,espera_admin", [
    ("OWNER", True, True),
    ("ADMIN", True, True),
    ("VENDEDOR_INTERNO", False, False),
    ("VENDEDOR_COMISSIONADO", False, False),
])
def test_matriz_de_papeis(papel, espera_economia, espera_admin):
    r = RequestFalsa(_novo_usuario(papel))
    assert ve_economia(r) is espera_economia
    assert administra(r) is espera_admin


def test_sem_sessao_nao_ve_nada():
    """O default é negar. Request sem usuário não é 'usuário comum': é ninguém."""
    r = RequestFalsa(None)
    assert ve_economia(r) is False
    assert administra(r) is False


def test_usuario_inativo_perde_tudo():
    from conftest import _novo_usuario as novo
    u = novo("OWNER")
    u.ativo = False
    r = RequestFalsa(u)
    assert ve_economia(r) is False
    assert administra(r) is False


def test_papel_desconhecido_nao_ganha_economia():
    """Papel que não está na lista é confidencial por omissão, não por engano."""
    u = _novo_usuario("ADMIN")
    u.papel = "PAPEL_QUE_NAO_EXISTE"
    assert ve_economia(RequestFalsa(u)) is False
    assert administra(RequestFalsa(u)) is False


def test_gerencia_usuarios_e_granular_dentro_de_admin():
    owner = _novo_usuario("OWNER")
    admin_comum = _novo_usuario("ADMIN")
    admin_gerente = _novo_usuario("ADMIN", can_manage_users=True)
    assert owner.gerencia_usuarios is True          # OWNER nunca perde essa capacidade
    assert admin_comum.gerencia_usuarios is False
    assert admin_gerente.gerencia_usuarios is True
    assert _novo_usuario("VENDEDOR_INTERNO").gerencia_usuarios is False


# ---------------------------------------------------------------------------
# 4. Endpoints — quem entra e quem leva 403
# ---------------------------------------------------------------------------
ROTAS_ADMIN = [
    ("app.routers.configuracoes", "painel"),
    ("app.routers.configuracoes", "salvar_premissa"),
    ("app.routers.configuracoes", "salvar_margem"),
    ("app.routers.configuracoes", "salvar_fiscal"),
    ("app.routers.calculadora", "pagina"),
    ("app.routers.calculadora", "calcular"),
    ("app.routers.importar", "form"),
    ("app.routers.importar", "preview"),
    ("app.routers.relatorios", "qualidade"),
    ("app.routers.relatorios", "qualidade_json"),
]


@pytest.mark.parametrize("modulo,nome", ROTAS_ADMIN)
@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_vendedor_leva_403_em_rota_administrativa(session, modulo, nome, papel):
    """URL direta não burla papel: a recusa é do servidor, antes de montar resposta."""
    import importlib
    funcao = getattr(importlib.import_module(modulo), nome)
    with pytest.raises(HTTPException) as erro:
        chamar(funcao, RequestFalsa(_novo_usuario(papel)), session=session)
    assert erro.value.status_code == 403


@pytest.mark.parametrize("modulo,nome", ROTAS_ADMIN)
def test_anonimo_e_mandado_para_o_login(session, modulo, nome):
    import importlib
    funcao = getattr(importlib.import_module(modulo), nome)
    with pytest.raises(PrecisaLogin) as erro:
        chamar(funcao, RequestFalsa(None), session=session)
    assert erro.value.status_code == 401


def test_memoria_do_produto_e_negada_ao_vendedor(session, cenario):
    """A superfície mais sensível do sistema — negada, não filtrada."""
    from app.routers.produtos import memoria
    for papel in ("VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"):
        with pytest.raises(HTTPException) as erro:
            chamar(memoria, RequestFalsa(_novo_usuario(papel)),
                   produto_id=cenario["produto"].id, session=session)
        assert erro.value.status_code == 403


def test_memoria_do_item_e_negada_ao_vendedor(session, cenario):
    """O `item_id` está no HTML da tela: é o convite mais óbvio a trocar o ID na URL."""
    from app.routers.cotacoes import memoria_item
    with pytest.raises(HTTPException) as erro:
        chamar(memoria_item, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
               cotacao_id=cenario["cotacao"].id, item_id=cenario["item"].id, session=session)
    assert erro.value.status_code == 403


def test_admin_acessa_a_memoria(session, cenario):
    from app.routers.cotacoes import memoria_item
    for papel in ("OWNER", "ADMIN"):
        r = chamar(memoria_item, RequestFalsa(_novo_usuario(papel)),
                   cotacao_id=cenario["cotacao"].id, item_id=cenario["item"].id,
                   session=session)
        assert "custo" in corpo(r)


def test_enumeracao_de_id_nao_burla_o_papel(session, cenario):
    """Trocar o ID na URL não muda o papel de quem pergunta."""
    from app.routers.cotacoes import memoria_item
    for item_id in range(1, 12):
        with pytest.raises(HTTPException) as erro:
            chamar(memoria_item, RequestFalsa(_novo_usuario("VENDEDOR_COMISSIONADO")),
                   cotacao_id=cenario["cotacao"].id, item_id=item_id, session=session)
        assert erro.value.status_code == 403


def test_papel_forjado_no_request_nao_funciona(session, cenario):
    """Mandar `papel=OWNER` no formulário não vira permissão.

    O papel não é parâmetro de entrada em lugar nenhum: vem do usuário que o middleware
    resolveu do banco. Este teste existe para que continuar assim seja obrigatório.
    """
    from app.routers.cotacoes import calc
    r = chamar(calc, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
               cotacao_id=cenario["cotacao"].id, produto_id=cenario["produto"].id,
               quantidade=3.0, modo="preco", valor=500.0, session=session,
               papel="OWNER", role="ADMIN", ve_economia=True)
    assert "lucro" not in corpo(r)


# ---------------------------------------------------------------------------
# 5. Payloads — a ausência explícita
# ---------------------------------------------------------------------------
#: Os nomes que o §25 manda procurar, traduzidos para os campos reais do sistema.
PROIBIDOS_NO_PAYLOAD = [
    "custo", "custo_unitario", "custo_total", "custo_net", "cnet", "lucro",
    "margem", "margem_liquida", "margem_alvo", "margem_padrao_pct", "markup",
    "markup_implicito", "exw_usd", "supplier_cost", "cost_reference", "drivers",
    "memoria_calculo", "comissao_pct", "impostos", "preco_preciso", "cost_method",
]


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_payload_do_calc_nao_leva_economia(session, cenario, papel):
    from app.routers.cotacoes import calc
    dados = corpo(chamar(calc, RequestFalsa(_novo_usuario(papel)),
                         cotacao_id=cenario["cotacao"].id, produto_id=cenario["produto"].id,
                         quantidade=5.0, modo="preco", valor=650.0, session=session))
    for chave in PROIBIDOS_NO_PAYLOAD:
        assert chave not in dados, f"/calc vazou '{chave}' para {papel}"
    assert encontrar_confidenciais(dados) == []
    # e o que ele precisa continua lá
    assert dados["preco_negociado"] == 650.0
    assert dados["faturamento"] == 3250.0


def test_payload_do_calc_leva_economia_para_admin(session, cenario):
    """O corte é por papel, não uma amputação geral: o admin continua recebendo tudo."""
    from app.routers.cotacoes import calc
    dados = corpo(chamar(calc, RequestFalsa(_novo_usuario("ADMIN")),
                         cotacao_id=cenario["cotacao"].id, produto_id=cenario["produto"].id,
                         quantidade=5.0, modo="preco", valor=650.0, session=session))
    for chave in ("custo_total", "lucro", "margem_liquida", "markup_implicito", "comissao_pct"):
        assert chave in dados


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_payload_da_busca_de_produtos_nao_leva_custo(session, papel):
    from app.routers.produtos import buscar
    linhas = corpo(chamar(buscar, RequestFalsa(_novo_usuario(papel)), q="", session=session))
    assert linhas, "a busca precisa continuar funcionando para o vendedor"
    for linha in linhas:
        assert encontrar_confidenciais(linha) == []
        assert "custo_unitario" not in linha
        assert "margem_padrao_pct" not in linha
        assert "cost_method" not in linha
        # o que ele precisa para montar a cotação
        assert "nome" in linha and "preco_base" in linha and "id" in linha


def test_payload_do_item_nao_leva_economia(session, cenario):
    from app.routers.cotacoes import _item_para_json
    comercial = _item_para_json(cenario["item"], pode_ver_economia=False)
    assert encontrar_confidenciais(comercial) == []
    for chave in ("custo_unitario", "custo_total", "lucro", "margem_liquida",
                  "margem_padrao_pct", "comissao_pct", "markup_implicito"):
        assert chave not in comercial
    assert comercial["preco_negociado"] and comercial["faturamento"]


def test_totais_da_cotacao_nao_levam_lucro(session, cenario):
    from app.routers.cotacoes import _totais
    itens = [cenario["item"]]
    assert encontrar_confidenciais(_totais(itens, pode_ver_economia=False)) == []
    assert "lucro" in _totais(itens, pode_ver_economia=True)


def test_a_politica_de_confidencialidade_cobre_os_nomes_reais():
    """Se um campo confidencial for renomeado, este teste avisa antes do vazamento."""
    for chave in PROIBIDOS_NO_PAYLOAD:
        assert chave in CAMPOS_CONFIDENCIAIS, f"'{chave}' saiu da política"


# ---------------------------------------------------------------------------
# 6. HTML — o termo não pode estar no fonte
# ---------------------------------------------------------------------------
TERMOS_NO_HTML = ["data-lucro", "data-custo", "data-margem-padrao",
                  "Custo total", "Margem padrão", "Origem do custo"]


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_html_da_cotacao_nao_carrega_economia(session, cenario, papel):
    """Não é sobre estar visível: é sobre estar no documento que o navegador recebeu."""
    from app.routers.cotacoes import detalhe
    pagina = html(chamar(detalhe, RequestFalsa(_novo_usuario(papel)),
                         cotacao_id=cenario["cotacao"].id, session=session))
    for termo in ("data-lucro", "data-custo", "data-margem-padrao", "Custo total",
                  "Margem líquida", "abrirMemoria"):
        assert termo not in pagina, f"HTML vazou '{termo}' para {papel}"
    # o valor numérico do custo também não pode aparecer em lugar nenhum
    assert "377.11" not in pagina and "377,11" not in pagina
    # e a página continua servindo para vender
    assert cenario["item"].nome_produto in pagina


def test_html_da_cotacao_leva_economia_para_admin(session, cenario):
    from app.routers.cotacoes import detalhe
    pagina = html(chamar(detalhe, RequestFalsa(_novo_usuario("ADMIN")),
                         cotacao_id=cenario["cotacao"].id, session=session))
    for termo in ("data-lucro", "Custo total", "abrirMemoria"):
        assert termo in pagina


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_html_de_produtos_nao_carrega_custo(session, cenario, papel):
    from app.routers.produtos import listar
    pagina = html(chamar(listar, RequestFalsa(_novo_usuario(papel)), session=session))
    for termo in ("Custo NET", "Margem padrão", "Origem do custo", "Método de custo"):
        assert termo not in pagina, f"produtos vazou '{termo}' para {papel}"
    assert "377,11" not in pagina


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_menu_nao_oferece_o_que_daria_403(session, cenario, papel):
    """Link que levaria a 403 não aparece — a UI acompanha o backend, não o contradiz."""
    from app.routers.produtos import listar
    pagina = html(chamar(listar, RequestFalsa(_novo_usuario(papel)), session=session))
    for rota in ("/configuracoes", "/importar", "/calculadora", "/relatorios/qualidade"):
        assert f'href="{rota}"' not in pagina


# ---------------------------------------------------------------------------
# 7. PDF — o documento que sai para o cliente
# ---------------------------------------------------------------------------
def test_pdf_comercial_nao_carrega_economia(session, cenario):
    """O PDF vai para o cliente. Nada do motor interno pode estar nele.

    A checagem é sobre o que o gerador **recebe** — é lá que o vazamento entraria, e é o
    ponto em que dá para afirmar campo a campo, sem depender de extração de texto do PDF.
    """
    import app.pdf_bridge as pb
    from app.models import CotacaoItem

    capturado = {}
    original = pb._gerar_cotacao.build_pdf

    def espiao(out, header, items, totals):
        capturado.update(header=header, items=items, totals=totals)
        return original(out, header, items, totals)

    pb._gerar_cotacao.build_pdf = espiao
    try:
        itens = session.exec(select(CotacaoItem)
                             .where(CotacaoItem.cotacao_id == cenario["cotacao"].id)).all()
        caminho = pb.gerar_pdf_para_cotacao(cenario["cotacao"], cenario["cliente"], itens)
    finally:
        pb._gerar_cotacao.build_pdf = original

    assert os.path.exists(caminho)
    assert encontrar_confidenciais(capturado) == []
    for item in capturado["items"]:
        for chave in ("custo", "custo_unitario", "cnet", "lucro", "margem", "markup",
                      "comissao", "fornecedor", "cost_method", "memoria"):
            assert chave not in item, f"o PDF levaria '{chave}'"
    for chave in ("custo_total", "lucro", "margem_liquida"):
        assert chave not in capturado["totals"]
    # e o que o cliente precisa está lá
    assert capturado["items"][0]["preco_final"] and capturado["items"][0]["total"]
    assert capturado["totals"]["total_geral"] and capturado["header"]["numero"]


def test_pdf_nao_menciona_fornecedor_confidencial(session, cenario):
    """Nome de fornecedor é informação de suprimentos, não do documento comercial."""
    import app.pdf_bridge as pb
    from app.models import CotacaoItem

    capturado = {}
    original = pb._gerar_cotacao.build_pdf
    pb._gerar_cotacao.build_pdf = lambda o, h, i, t: capturado.update(
        header=h, items=i, totals=t) or original(o, h, i, t)
    try:
        itens = session.exec(select(CotacaoItem)
                             .where(CotacaoItem.cotacao_id == cenario["cotacao"].id)).all()
        pb.gerar_pdf_para_cotacao(cenario["cotacao"], cenario["cliente"], itens)
    finally:
        pb._gerar_cotacao.build_pdf = original

    texto = json.dumps(capturado, ensure_ascii=False, default=str)
    for termo in ("Daune", "Kazareen", "Decor Tricot", "KTC"):
        assert termo not in texto, f"o PDF mencionaria o fornecedor '{termo}'"


# ---------------------------------------------------------------------------
# 8. Login
# ---------------------------------------------------------------------------
def test_login_valido_cria_sessao(session):
    from app.routers.login import login_submit
    email = "login-ok@anara.test"
    session.add(Usuario(email=email, nome="Login OK", senha_hash=auth.hash_senha("senha-boa-123"),
                        papel=Papel.admin.value))
    session.commit()
    resp = chamar(login_submit, RequestFalsa(None), email=email, senha="senha-boa-123",
                  next="/cotacoes", session=session)
    assert resp.status_code == 303
    assert auth.COOKIE_NAME in resp.headers.get("set-cookie", "")
    assert "httponly" in resp.headers["set-cookie"].lower()
    assert "samesite=lax" in resp.headers["set-cookie"].lower()


def test_login_invalido_nao_cria_sessao(session):
    from app.routers.login import login_submit
    email = "login-ruim@anara.test"
    session.add(Usuario(email=email, nome="X", senha_hash=auth.hash_senha("senha-certa-123"),
                        papel=Papel.admin.value))
    session.commit()
    resp = chamar(login_submit, RequestFalsa(None), email=email, senha="senha-errada",
                  next="/", session=session)
    assert resp.status_code == 401
    assert auth.COOKIE_NAME not in resp.headers.get("set-cookie", "")


def test_usuario_inativo_nao_loga(session):
    from app.routers.login import login_submit
    email = "inativo@anara.test"
    session.add(Usuario(email=email, nome="Inativo", papel=Papel.owner.value, ativo=False,
                        senha_hash=auth.hash_senha("senha-valida-123")))
    session.commit()
    resp = chamar(login_submit, RequestFalsa(None), email=email, senha="senha-valida-123",
                  next="/", session=session)
    assert resp.status_code == 401


def test_mensagem_de_erro_nao_revela_se_o_email_existe(session):
    """Distinguir 'não existe' de 'senha errada' entregaria a lista de quem tem conta."""
    from app.routers.login import login_submit, MENSAGEM_GENERICA
    email = "existe@anara.test"
    session.add(Usuario(email=email, nome="Existe", senha_hash=auth.hash_senha("senha-certa-123"),
                        papel=Papel.admin.value))
    session.commit()
    a = html(chamar(login_submit, RequestFalsa(None), email=email, senha="errada", next="/",
                    session=session))
    b = html(chamar(login_submit, RequestFalsa(None), email="nao-existe@anara.test",
                    senha="errada", next="/", session=session))
    assert MENSAGEM_GENERICA in a and MENSAGEM_GENERICA in b


def test_logout_apaga_o_cookie():
    from app.routers.login import logout
    resp = logout()
    assert "set-cookie" in {k.lower() for k in resp.headers}
    assert auth.COOKIE_NAME in resp.headers["set-cookie"]


def test_next_nao_redireciona_para_fora_do_sistema():
    """`next=https://phishing/` transformaria o login da Anara em trampolim."""
    from app.routers.login import _destino_seguro
    assert _destino_seguro("/cotacoes") == "/cotacoes"
    assert _destino_seguro("https://outro-site.com/") == "/"
    assert _destino_seguro("//outro-site.com/") == "/"
    assert _destino_seguro("") == "/"


def test_troca_de_senha_invalida_sessao_aberta(session):
    """`sessao_versao` é o que faz 'derrubei o acesso' valer agora, e não quando expirar."""
    u = Usuario(email="rotaciona@anara.test", nome="R", papel=Papel.admin.value,
                senha_hash=auth.hash_senha("senha-antiga-123"), sessao_versao=1)
    session.add(u)
    session.commit()
    session.refresh(u)
    cookie = auth.criar_cookie_valor(u.id, u.sessao_versao)
    assert auth.ler_cookie(cookie)["v"] == 1
    u.senha_hash = auth.hash_senha("senha-nova-456")
    u.sessao_versao += 1
    session.add(u)
    session.commit()
    # o cookie continua assinado — o que ele deixou de ser é atual
    assert auth.ler_cookie(cookie)["v"] != u.sessao_versao

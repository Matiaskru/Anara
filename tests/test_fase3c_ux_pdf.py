"""Fase 3C — redesign da plataforma comercial, Dashboard OWNER/ADMIN e PDF cliente.

Três blocos, na ordem do enunciado: a UX da vendedora (nada de economia interna em tela
nenhuma), o Dashboard e as telas do administrador, e o PDF da proposta — allowlist,
rascunho × final, paginação, soma ao centavo e snapshot como fonte do documento emitido.
"""
import inspect
import json
import os
import re
from datetime import date, datetime, timedelta

import pdfplumber
import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import comercial_service as com
from app import crm_service as crm
from app import metrics_service as mx
from app import pos_venda_service as pv
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, dinheiro
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Oportunidade, Produto,
    SnapshotEmissao, Usuario,
)
from conftest import RequestFalsa, _novo_usuario

_SEQ = iter(range(1, 100_000))

#: O que a vendedora nunca lê numa tela comercial. Minúsculas: a varredura é sobre o HTML
#: renderizado, sem distinguir caixa.
ECONOMIA_NA_TELA = ("custo", "cnet", "exw", "lucro", "piso", "markup", "margem",
                    "memória do preço", "abrirmemoria", "data-lucro", "economia da proposta")


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
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
        args[nome] = None if (v is inspect.Parameter.empty
                              or repr(v) == "PydanticUndefined") else v
    return funcao(**args)


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


def html(resposta):
    return bytes(resposta.body).decode()


def texto_pdf(caminho):
    with pdfplumber.open(caminho) as pdf:
        paginas = [p.extract_text() or "" for p in pdf.pages]
    return paginas


@pytest.fixture
def owner(session):
    u = session.exec(select(Usuario).where(Usuario.email == "f3c-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="f3c-owner@anara.test", nome="Dona 3C", senha_hash="h", papel="OWNER",
                    can_approve_quotes=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def vendedora(session):
    u = session.exec(select(Usuario).where(Usuario.email == "f3c-vend@anara.test")).first()
    if u is None:
        u = Usuario(email="f3c-vend@anara.test", nome="Vendedora 3C", senha_hash="h",
                    papel="VENDEDOR_INTERNO")
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def novo_cliente(session, ator, **kw):
    dados = dict(nome=f"Hotel 3C {next(_SEQ)}", cidade_uf="São Paulo", finalidade="REVENDA",
                 cnpj_cpf=f"33.333.333/{next(_SEQ):04d}-33")
    dados.update(kw)
    c = crm.criar_cliente(session, ator=ator, **dados)
    session.commit()
    return c


def nova_venda(session, ator, cliente, **kw):
    dados = dict(cliente_id=cliente.id, titulo=f"Projeto 3C {next(_SEQ)}", responsavel_id=ator.id)
    dados.update(kw)
    op = crm.criar_oportunidade(session, ator=ator, **dados)
    session.commit()
    return op


def produto(session, fornecedores, codigo="DECOR_TRICOT", custo=100.0, familia="Bed Runner",
            nome=None):
    sku = f"F3C-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=nome or f"Peseira {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedores[codigo].id, familia=familia,
                especificacao="0,60x1,90 · tricô",
                cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_na_venda(session, op, ator, produtos=(), quantidade=5.0, **campos):
    from app.routers.cotacoes import adicionar_item, criar_cotacao_da_venda
    dados = dict(estado_destino="São Paulo", contribuinte_icms=True, freight_type="FOB",
                 condicao_pagamento="30")
    dados.update(campos)
    cot = criar_cotacao_da_venda(session, RequestFalsa(ator), op, ator=ator, **dados)
    cot.uf_origem_fiscal = "SP"
    cot.finalidade = "REVENDA"
    session.add(cot)
    session.commit()
    session.refresh(cot)
    for p in produtos:
        chamar(adicionar_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
               produto_id=p.id, quantidade=quantidade, modo="margem", valor=None,
               session=session)
        session.commit()
    return cot


def emitir(session, cot, ator):
    snap = ws.emitir(session, cot, ator=ator)
    session.commit()
    return snap


def vender(session, op, cot, ator):
    crm.marcar_ganha(session, op, cot.id, ator=ator)
    session.commit()
    session.refresh(op)
    return op


# ===========================================================================
# A. Shell, login e menus
# ===========================================================================
def test_01_menu_da_vendedora_e_do_admin(session):
    from app.templating import templates
    pagina = templates.env.get_template("base.html").render(
        request=RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")), active="")
    nav = pagina.split("<nav>")[1].split("</nav>")[0]
    destinos = re.findall(r'href="(/[^"]*)"', nav)
    assert destinos == ["/vendas", "/clientes", "/cotacoes", "/produtos"]
    assert "Olá" not in pagina and "Bom dia" not in pagina

    pagina = templates.env.get_template("base.html").render(
        request=RequestFalsa(_novo_usuario("OWNER")), active="")
    nav = pagina.split("<nav>")[1].split("</nav>")[0]
    destinos = re.findall(r'href="(/[^"]*)"', nav)
    assert destinos == ["/dashboard", "/vendas", "/clientes", "/cotacoes", "/produtos", "/aprovacoes", "/admin"]


def test_02_login_limpo_sem_texto_tecnico():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pagina = open(os.path.join(raiz, "app", "templates", "login.html"), encoding="utf-8").read()
    for esperado in ("ANARA", 'name="email"', 'name="senha"', "Entrar"):
        assert esperado in pagina
    for proibido in ("argon", "cookie", "Sessão", "hash", "tagline"):
        assert proibido not in pagina


# ===========================================================================
# B. Vendas — lista, status inline, quadro
# ===========================================================================
def test_03_lista_de_vendas_com_status_inline_e_sem_economia(session, vendedora, owner):
    from app.routers.vendas import lista
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    pagina = html(chamar(lista, RequestFalsa(vendedora), session=session)).lower()
    assert f'data-status-venda="{op.id}"' in pagina         # status editável na linha
    assert "rascunho" in pagina and "ABERTA".lower() not in pagina.replace("aberta", "")
    for termo in ECONOMIA_NA_TELA:
        assert termo not in pagina, f"lista de vendas mostrou '{termo}'"
    # enums internos não aparecem como texto
    for enum in ("GANHA", "PERDIDA", "EtapaOportunidade", "NEGOCIACAO<"):
        assert enum not in html(chamar(lista, RequestFalsa(vendedora), session=session))


def test_04_status_inline_muda_e_devolve_rotulo_humano(session, vendedora, owner):
    from app.routers.vendas import mudar_status
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    r = corpo(chamar(mudar_status, RequestFalsa(vendedora), venda_id=op.id, etapa="NEGOCIACAO",
                     session=session))
    assert r["status_comercial"] == "Negociação" and r["etapa"] == "NEGOCIACAO"
    r = corpo(chamar(mudar_status, RequestFalsa(vendedora), venda_id=op.id, etapa="RASCUNHO",
                     session=session))
    assert r["status_comercial"] == "Rascunho"


def test_05_quadro_mostra_so_as_tres_etapas_abertas(session, vendedora, owner, fornecedores):
    from app.routers.vendas import lista, quadro
    cliente = novo_cliente(session, owner)
    aberta = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, aberta, vendedora, [produto(session, fornecedores)])
    ganha = nova_venda(session, vendedora, cliente)
    cot_g = cotacao_na_venda(session, ganha, vendedora, [produto(session, fornecedores)])
    emitir(session, cot_g, owner)
    vender(session, ganha, cot_g, owner)

    pagina = html(chamar(lista, RequestFalsa(vendedora), vista="quadro", session=session))
    assert 'class="kanban"' in pagina
    assert f'data-id="{aberta.id}"' in pagina and f'data-id="{ganha.id}"' not in pagina
    colunas = quadro([crm.cartao(session, o) for o in (aberta, ganha)])
    assert [c["etapa"] for c in colunas] == ["RASCUNHO", "ENVIADO", "NEGOCIACAO"]
    assert colunas[0]["quantidade"] == 1 and colunas[0]["total"] == pytest.approx(
        ws.itens_de(session, cot.id)[0].faturamento)


# ===========================================================================
# C. Venda individual, atualização, timeline, pós-venda
# ===========================================================================
def test_06_detalhe_da_venda_e_timeline_sem_economia_nem_codigos(session, vendedora, owner,
                                                                  fornecedores):
    from app.routers.vendas import detalhe, registrar_atualizacao
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    emitir(session, cot, owner)                        # avanço automático → Enviado
    r = corpo(chamar(registrar_atualizacao, RequestFalsa(vendedora), venda_id=op.id,
                     texto="cliente pediu revisão", atividade_titulo="ligar amanhã",
                     atividade_tipo="LIGACAO", atividade_due_em="2026-09-20T10:00",
                     session=session))
    assert r["mensagem"] == "Atualização registrada."

    pagina = html(chamar(detalhe, RequestFalsa(vendedora), venda_id=op.id, session=session))
    assert "cliente pediu revisão" in pagina and "Ligação: ligar amanhã" in pagina
    assert 'class="timeline"' in pagina and "Registrar atualização" in pagina
    baixo = pagina.lower()
    for termo in ECONOMIA_NA_TELA:
        assert termo not in baixo, f"venda mostrou '{termo}'"
    # códigos só como `value=` de formulário (dado que volta ao servidor), nunca como texto
    for tecnico in ("AUTOMÁTICO:", ">FOLLOW_UP<", "fingerprint", "AuditLog", ">LIGACAO<"):
        assert tecnico not in pagina
    # o avanço automático aparece em português, sem prefixo de código
    assert "Rascunho → Enviado" in pagina and "automático" in pagina


def test_07_pos_venda_visual_e_permissoes(session, vendedora, owner, fornecedores):
    from app.routers.vendas import detalhe, entrega
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    emitir(session, cot, owner)
    vender(session, op, cot, owner)

    vend = html(chamar(detalhe, RequestFalsa(vendedora), venda_id=op.id, session=session))
    assert 'class="stepper"' in vend and "Aguardando entrega" in vend
    assert "Registrar entrega" in vend and "Marcar pago" not in vend      # financeiro é alçada
    assert "inadimplente" not in vend.lower()
    adm = html(chamar(detalhe, RequestFalsa(owner), venda_id=op.id, session=session))
    assert "Marcar pago" in adm and "Registrar faturamento" not in adm.replace("Documento fiscal", "")

    r = corpo(chamar(entrega, RequestFalsa(vendedora), venda_id=op.id, session=session))
    assert r["status_pos_venda"] == "AGUARDANDO_PAGAMENTO"
    with pytest.raises(HTTPException) as erro:
        from app.routers.vendas import pago
        chamar(pago, RequestFalsa(vendedora), venda_id=op.id, session=session)
    assert erro.value.status_code == 403


# ===========================================================================
# D. Clientes e Cliente 360
# ===========================================================================
def test_08_lista_de_clientes_com_resumo_e_status_financeiro(session, vendedora, owner,
                                                             fornecedores):
    from app.routers.clientes import listar
    cliente = novo_cliente(session, owner, nome=f"Resort Financeiro {next(_SEQ)}")
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    emitir(session, cot, owner)
    vender(session, op, cot, owner)
    resumo = mx.clientes_resumo(session)[cliente.id]
    assert resumo["status_financeiro"] == "EM_ABERTO" and resumo["quantidade_vendas"] == 1
    assert resumo["total_comprado"] == pytest.approx(op.valor_fechado)
    pv.marcar_atrasado(session, op, ator=owner)
    session.commit()
    assert mx.clientes_resumo(session)[cliente.id]["status_financeiro"] == "ATRASADO"
    pv.marcar_pago(session, op, ator=owner)
    session.commit()
    assert mx.clientes_resumo(session)[cliente.id]["status_financeiro"] == "EM_DIA"

    pagina = html(chamar(listar, RequestFalsa(vendedora), q=cliente.nome, session=session))
    assert "Em dia" in pagina and "Total comprado" in pagina


def test_09_cliente_360_abas_kpis_e_edicao(session, vendedora, owner, fornecedores):
    from app.routers.clientes import detalhe, editar
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    emitir(session, cot, owner)
    vender(session, op, cot, owner)
    # revisão da vencedora NÃO conta como segunda compra
    rev = ws.criar_revisao(session, cot, ator=owner)
    session.commit()

    pagina = html(chamar(detalhe, RequestFalsa(vendedora), cliente_id=cliente.id, session=session))
    for aba in ('data-tab="visao"', 'data-tab="vendas"', 'data-tab="cotacoes"', 'data-tab="contatos"'):
        assert aba in pagina
    for kpi in ("Total comprado", "Nº vendas", "Ticket médio", "Última compra", "Em aberto", "Atrasado"):
        assert kpi in pagina
    assert mx.cliente_360(session, cliente.id)["quantidade_vendas"] == 1
    assert (rev.numero or "") in pagina                  # histórico completo, inclusive a revisão
    for termo in ECONOMIA_NA_TELA:
        assert termo not in pagina.lower(), f"cliente 360 mostrou '{termo}'"

    r = chamar(editar, RequestFalsa(vendedora), cliente_id=cliente.id, nome="Nome Editado",
               cnpj_cpf=cliente.cnpj_cpf, cidade_uf="Rio de Janeiro", finalidade="USO_CONSUMO",
               telefone="", email="", contato_nome="Ana", session=session)
    assert r.status_code == 303
    session.refresh(cliente)
    assert cliente.nome == "Nome Editado" and cliente.cidade_uf == "Rio de Janeiro"
    with pytest.raises(HTTPException):
        chamar(editar, RequestFalsa(vendedora), cliente_id=cliente.id, nome="x", finalidade="BANANA",
               session=session)


# ===========================================================================
# E. Cotações — lista, tela, negociação reativa, painel
# ===========================================================================
def test_10_lista_de_cotacoes_filtros_e_rotulos(session, vendedora, owner, fornecedores):
    from app.routers.cotacoes import listar
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    pagina = html(chamar(listar, RequestFalsa(vendedora), busca=cot.numero, session=session))
    assert cot.numero in pagina and op.titulo in pagina and "R1" in pagina
    assert ">aguardando_aprovacao<" not in pagina         # filtro mostra rótulo, não enum
    assert "Aguardando aprovação" in pagina
    for termo in ("custo", "margem", "lucro"):
        assert termo not in pagina.lower()
    pagina = html(chamar(listar, RequestFalsa(vendedora), busca=cot.numero, periodo="ano",
                         status="rascunho", session=session))
    assert cot.numero in pagina
    pagina = html(chamar(listar, RequestFalsa(vendedora), busca=cot.numero, status="emitida",
                         session=session))
    assert f'data-href="/cotacoes/{cot.id}"' not in pagina      # filtrada (o campo de busca a repete)


def test_11_tela_da_cotacao_vendedora_x_admin(session, vendedora, owner, fornecedores):
    from app.routers.cotacoes import detalhe
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    decor = produto(session, fornecedores)
    daune = produto(session, fornecedores, codigo="DAUNE", familia="Pillow", nome="Travesseiro 3C")
    cot = cotacao_na_venda(session, op, vendedora, [decor, daune])
    itens = {it.produto_id: it for it in ws.itens_de(session, cot.id)}

    pagina = html(chamar(detalhe, RequestFalsa(vendedora), cotacao_id=cot.id, session=session))
    assert f'data-item-id="{itens[decor.id].id}"' in pagina
    assert 'data-preco-input' in pagina                                    # Decor: editável
    assert 'data-travado="1"' in pagina and "fixo" in pagina               # Daune: preço fixo
    assert "Sua comissão estimada" in pagina and "Dentro da autonomia" in pagina
    assert "NEGOCIACAO_INICIAL" in pagina and '"comissao_estimada_valor"' in pagina
    baixo = pagina.lower()
    for termo in ECONOMIA_NA_TELA:
        assert termo not in baixo, f"cotação da vendedora mostrou '{termo}'"
    assert "377,11" not in pagina and "MARGEM_ABAIXO_PISO" not in pagina
    # o motivo do preço fixo não explica margem nem comissão Daune
    assert "12%" not in pagina and "5%" not in pagina.replace("2,5%", "")

    adm = html(chamar(detalhe, RequestFalsa(owner), cotacao_id=cot.id, session=session))
    assert "Economia da proposta" in adm and "data-lucro" in adm and "abrirMemoria" in adm
    assert "<details" in adm.split("Economia da proposta")[0][-400:]      # fechada por padrão


def test_12_negociacao_reativa_preview_aplicar_e_quantidade(session, vendedora, owner,
                                                             fornecedores):
    from app.routers.cotacoes import editar_item, painel_situacao
    from app.routers.negociacao import aplicar, negociacao_atual, preview
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    decor = produto(session, fornecedores)
    cot = cotacao_na_venda(session, op, vendedora, [decor], quantidade=10)
    it = ws.itens_de(session, cot.id)[0]
    rec = dinheiro(it.preco_recomendado)
    antes = corpo(chamar(negociacao_atual, RequestFalsa(vendedora), cotacao_id=cot.id,
                         session=session))
    assert antes["autonomia_status"] == "DENTRO_DA_AUTONOMIA"

    # preview: 4% de desconto — total, desconto e comissão mudam; nada é gravado
    proposto = dinheiro(rec * D("0.96"))

    class Corpo:
        def __init__(self, dados):
            self._dados = dados

        async def json(self):
            return self._dados

    req = RequestFalsa(vendedora)
    req.json = Corpo({"itens": [{"item_id": it.id, "preco_negociado": str(proposto)}]}).json
    import asyncio
    prev = corpo(asyncio.run(preview(req, cot.id, session)))
    linha = prev["itens"][0]
    assert linha["preco_negociado"] == pytest.approx(float(proposto))
    assert linha["desconto_linha_pct"] == pytest.approx(0.04, abs=1e-4)
    assert prev["total_proposta"] < antes["total_proposta"]
    assert prev["comissao_estimada_pct_efetiva"] < antes["comissao_estimada_pct_efetiva"]
    assert encontrar_confidenciais(prev) == []
    session.refresh(it)
    assert dinheiro(it.preco_negociado) == rec                             # não gravou

    # aplicar grava e o painel de situação continua dentro da autonomia
    req2 = RequestFalsa(vendedora)
    req2.json = Corpo({"itens": [{"item_id": it.id, "preco_negociado": str(proposto)}]}).json
    corpo(asyncio.run(aplicar(req2, cot.id, session)))
    session.refresh(it)
    assert dinheiro(it.preco_negociado) == proposto
    painel = html(chamar(painel_situacao, RequestFalsa(vendedora), cotacao_id=cot.id, session=session))
    assert "precisa de aprovação" not in painel

    # quantidade pela edição do item (vendedora), preço mantido
    class Form:
        def __init__(self, d):
            self.d = d

        def get(self, k, default=None):
            return self.d.get(k, default)

    req3 = RequestFalsa(vendedora)

    async def form():
        return Form({"quantidade": "20", "modo": "preco", "valor": str(proposto)})
    req3.form = form
    r = corpo(asyncio.run(editar_item(cot.id, it.id, req3, session)))
    assert r["quantidade"] == 20 and r["faturamento"] == pytest.approx(float(proposto * 20))
    assert encontrar_confidenciais(r) == []


def test_13_daune_bloqueada_e_aprovacao_aparece_quando_necessaria(session, vendedora, owner,
                                                                    fornecedores):
    from app.routers.cotacoes import painel_situacao
    from app.routers.negociacao import aplicar
    import asyncio
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    daune = produto(session, fornecedores, codigo="DAUNE", familia="Pillow")
    decor = produto(session, fornecedores)
    cot = cotacao_na_venda(session, op, vendedora, [daune, decor])
    itens = {it.produto_id: it for it in ws.itens_de(session, cot.id)}

    class Corpo:
        def __init__(self, dados):
            self._dados = dados

        async def json(self):
            return self._dados

    req = RequestFalsa(vendedora)
    req.json = Corpo({"itens": [{"item_id": itens[daune.id].id,
                                 "preco_negociado": str(dinheiro(itens[daune.id].preco_recomendado) - 1)}]}).json
    with pytest.raises(HTTPException) as erro:
        asyncio.run(aplicar(req, cot.id, session))
    assert erro.value.status_code == 409
    assert "MARGEM" not in erro.value.detail and "travado" in erro.value.detail

    # Decor 20% abaixo do recomendado: exceção comercial → o painel pede aprovação
    req2 = RequestFalsa(vendedora)
    req2.json = Corpo({"itens": [{"item_id": itens[decor.id].id,
                                  "preco_negociado": str(dinheiro(D(itens[decor.id].preco_recomendado) * D("0.8")))}]}).json
    r = corpo(asyncio.run(aplicar(req2, cot.id, session)))
    assert r["requer_aprovacao"] is True and r["autonomia_status"] == "REQUER_APROVACAO"
    painel = html(chamar(painel_situacao, RequestFalsa(vendedora), cotacao_id=cot.id, session=session))
    assert "Este preço precisa de aprovação antes da emissão" in painel
    assert "Pedir aprovação" in painel and "MARGEM_ABAIXO_PISO" not in painel
    assert "piso" not in painel.lower() and "margem" not in painel.lower()


# ===========================================================================
# F. Dashboard OWNER/ADMIN e Produtos
# ===========================================================================
def test_14_dashboard_admin_kpis_filtros_e_serie(session, vendedora, owner, fornecedores):
    from app.routers.dashboard import dashboard
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    emitir(session, cot, owner)
    vender(session, op, cot, owner)

    r = chamar(dashboard, RequestFalsa(owner), periodo="12m", session=session)
    assert r.status_code == 200
    pagina = html(r)
    for kpi in ("Valor vendido", "Lucro", "Margem agregada", "Vendas fechadas", "Ticket médio",
                "Conversão", "Desconto médio", "Pipeline aberto", "Faturado", "Pago", "A receber",
                "Atrasado", "Vendas e lucro por mês", "Performance por vendedora", "Top clientes",
                "Impacto dos descontos", "Motivos de perda", "Pós-venda"):
        assert kpi in pagina, kpi
    assert "SERIE_MENSAL" in pagina and "cdn." not in pagina
    assert "Olá" not in pagina

    # vendedora não entra; o filtro por vendedora corta a venda
    r = chamar(dashboard, RequestFalsa(vendedora), session=session)
    assert r.status_code == 303 and r.headers["location"] == "/vendas"
    janela = mx.periodo_de("12m")
    tudo = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id))
    so_dela = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id,
                                                                     responsavel_id=vendedora.id))
    ninguem = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id,
                                                                     responsavel_id=owner.id))
    assert tudo["valor_vendido"] == so_dela["valor_vendido"] == pytest.approx(op.valor_fechado)
    assert ninguem["valor_vendido"] is None and ninguem["vendas_fechadas"] == 0
    assert tudo["margem_agregada"] is not None and tudo["lucro"] is not None
    assert tudo["valor_faturado"] is None and tudo["a_receber"] == pytest.approx(op.valor_fechado)
    assert tudo["performance_vendedoras"][0]["vendedora"] == vendedora.nome
    assert tudo["concentracao_top5"] == pytest.approx(1.0)
    # série mensal: o mês da venda soma o vendido; sem dado do ano anterior não há comparação
    serie = mx.serie_mensal(session, filtros=mx.FiltrosDashboard(cliente_id=cliente.id))
    mes = next(m for m in serie["meses"] if m["vendas"])
    assert mes["vendido"] == pytest.approx(op.valor_fechado) and mes["margem"] is not None
    assert serie["ano_anterior"] is None
    # filtro por família: item da família fica; outra família zera
    fam = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id, familia="Bed Runner"))
    outra = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id, familia="Pillow"))
    assert fam["vendas_fechadas"] == 1 and outra["vendas_fechadas"] == 0


def test_15_produtos_catalogo_comercial_sem_economia(session, vendedora, fornecedores):
    from app.routers.produtos import listar, situacao_comercial
    p = produto(session, fornecedores, familia="Familia3C")
    pagina = html(chamar(listar, RequestFalsa(vendedora), familia="Familia3C", session=session))
    assert p.nome in pagina and "Disponível" in pagina
    for termo in ("Custo NET", "Margem padrão", "Método", "Origem do custo", "Memória"):
        assert termo not in pagina
    sem = produto(session, fornecedores, custo=0.0, familia="Familia3C")
    assert situacao_comercial(sem) == "SOB_CONSULTA"
    pagina = html(chamar(listar, RequestFalsa(vendedora), familia="Familia3C", situacao="SOB_CONSULTA",
                         session=session))
    assert sem.nome in pagina and p.nome not in pagina
    adm = html(chamar(listar, RequestFalsa(_novo_usuario("ADMIN")), familia="Familia3C", session=session))
    assert "Custo NET" in adm and "Memória" in adm


# ===========================================================================
# G. PDF cliente
# ===========================================================================
def _gerar(session, cot, rascunho, snapshot=None):
    from app.pdf_bridge import gerar_pdf_para_cotacao
    return gerar_pdf_para_cotacao(cot, session.get(Cliente, cot.cliente_id),
                                  ws.itens_de(session, cot.id), rascunho=rascunho,
                                  snapshot=snapshot, condicao_label="30 dias")


def _itens_do_texto(paginas):
    """`[(qtd, unitário, total)]` lidos das linhas numeradas do PDF."""
    achados = []
    for pagina in paginas:
        for linha in pagina.splitlines():
            # a linha numérica pode vir colada ao fim de uma linha do nome do produto
            m = re.search(r"(\d[\d\.]*)\s+R\$ ([\d\.]+,\d{2})\s+R\$ ([\d\.]+,\d{2})$", linha.strip())
            if m:
                achados.append(m.groups())
    return achados


def _brl_para_decimal(txt):
    return D(txt.replace(".", "").replace(",", "."))


def test_16_pdf_rascunho_marcado_e_final_limpo(session, vendedora, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)])
    cot.observacao_cliente = "Entrega em duas remessas, conforme combinado."
    cot.observacoes = "INTERNA: cliente sensível a preço, segurar desconto"
    session.add(cot)
    session.commit()
    rasc = "\n".join(texto_pdf(_gerar(session, cot, rascunho=True)))
    assert "RASCUNHO — NÃO ENVIAR AO CLIENTE" in rasc
    assert "Entrega em duas remessas" in rasc
    assert "INTERNA" not in rasc and "segurar desconto" not in rasc     # observação interna nunca

    snap = emitir(session, cot, owner)
    final = "\n".join(texto_pdf(_gerar(session, cot, rascunho=False, snapshot=snap)))
    assert "RASCUNHO" not in final
    assert "PROPOSTA COMERCIAL" in final and cot.numero in final and "TOTAL DA PROPOSTA" in final
    assert "TERMOS E CONDIÇÕES" in final and "CONDIÇÕES COMERCIAIS" in final
    assert "30 dias" in final and "Por conta do cliente" in final
    assert "Página 1 de 1" in final


@pytest.mark.parametrize("quantidade_itens", [1, 10, 34])
def test_17_pdf_paginacao_e_soma_ao_centavo(session, vendedora, owner, fornecedores, quantidade_itens):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    produtos = [produto(session, fornecedores, custo=100.0 + i * 3.37,
                        nome=f"Peseira {i + 1} · modelo com descrição bastante longa para testar "
                             f"a quebra de linha na especificação do item {i + 1}")
                for i in range(quantidade_itens)]
    cot = cotacao_na_venda(session, op, vendedora, produtos, quantidade=7)
    paginas = texto_pdf(_gerar(session, cot, rascunho=True))
    if quantidade_itens >= 30:
        assert len(paginas) >= 2, "30+ itens têm de paginar"
    for i, pagina in enumerate(paginas, start=1):
        assert "PROPOSTA COMERCIAL" in pagina                      # cabeçalho em toda página
        assert f"Página {i} de {len(paginas)}" in pagina           # rodapé numerado
        assert "RASCUNHO — NÃO ENVIAR AO CLIENTE" in pagina         # marca em toda página
        if "R$" in pagina and re.search(r"^\d+\s+\d", pagina, re.M):
            assert "PRODUTO / ESPECIFICAÇÃO" in pagina              # cabeçalho da tabela repete
    linhas = _itens_do_texto(paginas)
    assert len(linhas) == quantidade_itens, "toda linha de item precisa sair legível e inteira"
    itens = ws.itens_de(session, cot.id)
    soma_linhas = sum((_brl_para_decimal(t) for _q, _u, t in linhas), D("0"))
    soma_banco = sum((dinheiro(it.faturamento) for it in itens), D("0"))
    assert soma_linhas == soma_banco
    for (q, u, t) in linhas:
        assert dinheiro(_brl_para_decimal(u) * D(q.replace(".", ""))) == _brl_para_decimal(t)
    texto = "\n".join(paginas)
    subtotal = re.search(r"Subtotal dos produtos\s+R\$ ([\d\.]+,\d{2})", texto)
    total = re.search(r"TOTAL DA PROPOSTA\s+R\$ ([\d\.]+,\d{2})", texto)
    assert subtotal and total, "fechamento precisa estar inteiro e legível"
    assert _brl_para_decimal(subtotal.group(1)) == soma_banco
    assert _brl_para_decimal(total.group(1)) == soma_banco                # FOB: sem frete
    # o fechamento nunca fica sozinho numa página sem o cabeçalho da tabela
    pagina_total = next(p for p in paginas if "TOTAL DA PROPOSTA" in p)
    assert "PRODUTO / ESPECIFICAÇÃO" in pagina_total


def test_18_pdf_nunca_leva_dado_interno(session, vendedora, owner, fornecedores):
    from app import pdf_bridge as pb
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    daune = produto(session, fornecedores, codigo="DAUNE", familia="Pillow", nome="Travesseiro 3C")
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores), daune])
    capturado = {}
    original = pb._gerar_cotacao.build_pdf

    def espiao(out, header, items, totals):
        capturado.update(header=header, items=items, totals=totals)
        return original(out, header, items, totals)
    pb._gerar_cotacao.build_pdf = espiao
    try:
        caminho = _gerar(session, cot, rascunho=True)
    finally:
        pb._gerar_cotacao.build_pdf = original
    assert set(capturado["header"]) <= set(pb.CAMPOS_HEADER)
    assert all(set(i) <= set(pb.CAMPOS_ITEM) for i in capturado["items"])
    assert set(capturado["totals"]) <= set(pb.CAMPOS_TOTAIS)
    assert encontrar_confidenciais(capturado) == []
    texto = "\n".join(texto_pdf(caminho))
    proibidos = ["CNET", "EXW", "custo", "Custo", "CUSTO", "margem", "Margem", "MARGEM", "piso",
                 "lucro", "Lucro", "markup", "Markup", "comissão", "Comissão", "COMISSÃO",
                 "fingerprint", "approval", "AuditLog", "política comercial", "seller",
                 "A_COTAR", "REVIEW_REQUIRED", "REVALIDAR", "SEM_PRECO", "C-NEW",
                 "TRACEABLE_LEGACY", "MARGEM_ABAIXO_PISO", "seller_publishable",
                 "recomendado", "Recomendado", "tabela", "economizou", "desconto", "Desconto",
                 "Daune", "Decor Tricot", "Kazareen", "KTC", "ICMS", "PIS", "COFINS", "US$"]
    achados = [p for p in proibidos if p in texto]
    assert not achados, f"PDF expôs: {achados}"
    # e a ponte recusa se algo interno chegar perto do papel
    cot.observacao_cliente = "Item pendente: A_COTAR com o fornecedor"
    with pytest.raises(pb.PdfInseguro):
        pb.montar_documento(cot, cliente, ws.itens_de(session, cot.id), rascunho=True)
    cot.observacao_cliente = None


def test_19_pdf_final_usa_o_snapshot_e_nao_muda_depois(session, vendedora, owner, fornecedores):
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)], quantidade=3)
    snap = emitir(session, cot, owner)
    it = ws.itens_de(session, cot.id)[0]
    preco_emitido = dinheiro(it.preco_negociado)
    total_emitido = dinheiro(it.faturamento)
    # alteração posterior direta no banco (fora do workflow, que recusaria)
    it.preco_negociado = float(preco_emitido) + 100
    it.faturamento = float(total_emitido) + 300
    session.add(it)
    session.commit()
    texto = "\n".join(texto_pdf(_gerar(session, cot, rascunho=False, snapshot=snap)))
    from app.pdf_proposta import brl
    assert brl(preco_emitido) in texto and brl(total_emitido) in texto
    assert brl(D(preco_emitido) + 100) not in texto
    # a rota também exige o snapshot para o final
    from app.routers.cotacoes import gerar_pdf
    r = chamar(gerar_pdf, RequestFalsa(owner), cotacao_id=cot.id, session=session)
    assert getattr(r, "media_type", "") == "application/pdf"
    # restaura o item para não sujar outros testes
    it.preco_negociado = float(preco_emitido)
    it.faturamento = float(total_emitido)
    session.add(it)
    session.commit()


def test_20_pdf_frete_por_extenso(session, vendedora, owner, fornecedores):
    from app import pdf_bridge as pb
    cliente = novo_cliente(session, owner)
    op = nova_venda(session, vendedora, cliente)
    cot = cotacao_na_venda(session, op, vendedora, [produto(session, fornecedores)], quantidade=2)
    itens = ws.itens_de(session, cot.id)
    subtotal = dinheiro(itens[0].faturamento)

    cot.freight_type = "FOB"
    h, _i, t = pb.montar_documento(cot, cliente, itens, rascunho=True)
    assert h["frete"] == "Por conta do cliente" and t["total_geral"] == pytest.approx(float(subtotal))
    cot.freight_type = "A_COMBINAR"
    h, _i, t = pb.montar_documento(cot, cliente, itens, rascunho=True)
    assert h["frete"] == "A combinar" and t["frete"] is None
    cot.freight_type = "CIF"
    cot.freight_valor = 150.0
    h, _i, t = pb.montar_documento(cot, cliente, itens, rascunho=True)
    assert h["frete"] == "Frete nacional: R$ 150,00"
    assert t["frete"] == 150.0 and t["total_geral"] == pytest.approx(float(subtotal + D("150")))
    cot.freight_valor = None
    h, _i, t = pb.montar_documento(cot, cliente, itens, rascunho=True)
    assert "A_COTAR" not in h["frete"] and h["frete"] == "Frete nacional: a definir"
    cot.freight_type = "FOB"
    session.add(cot)
    session.commit()

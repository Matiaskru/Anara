"""Apoio dos testes de regressão da auditoria de crise (17/09/2026).

Tudo em banco temporário (a `session` do conftest da raiz). Os cenários montam cotação,
itens e produtos próprios — nunca dependem do banco real.
"""
import asyncio
import inspect
import json
import os
import sys

import pytest
from sqlmodel import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import RequestFalsa, _novo_usuario  # noqa: E402

_SEQ = iter(range(1, 100_000))


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
    resultado = funcao(**args)
    if inspect.iscoroutine(resultado):
        resultado = asyncio.run(resultado)
    return resultado


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


class RequestComForm(RequestFalsa):
    """Request cujo `await request.form()` devolve o dicionário dado."""

    def __init__(self, usuario, form, **kw):
        super().__init__(usuario, **kw)
        self._form = form

    async def form(self):
        return self._form


@pytest.fixture
def admin():
    return _novo_usuario("ADMIN")


@pytest.fixture
def owner():
    u = _novo_usuario("OWNER")
    u.can_approve_quotes = True
    return u


@pytest.fixture
def fornecedores(session):
    from app.models import Fornecedor
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def produto_nacional(session, fornecedores, codigo="DECOR_TRICOT", custo=100.0,
                     familia="Bed Runner", **kw):
    from app.models import CostMethod, Produto
    sku = f"CRISIS-{next(_SEQ)}"
    dados = dict(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                 fornecedor_id=fornecedores[codigo].id, familia=familia,
                 cost_method=CostMethod.national_supplier.value)
    dados.update(kw)
    p = Produto(**dados)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def produto_ktc_cotado(session, fornecedores, exw_usd=10.0, peso_kg=0.8, data=None,
                       familia="Flat Sheet", thread_count=300, **kw):
    """SKU KTC com cotação direta (`exw_cotado_usd`), como os 81 KTC_QUOTED do catálogo."""
    from datetime import date
    from app.models import CostMethod, Produto
    sku = f"CRISIS-KTC-{next(_SEQ)}"
    dados = dict(sku_key=sku, nome=f"Lençol {sku}", fornecedor_id=fornecedores["KTC"].id,
                 familia=familia, thread_count=thread_count, cotton_pct=1.0,
                 largura_cm=190, comprimento_cm=250, peso_kg=peso_kg, peso_tipo="REAL KTC",
                 exw_cotado_usd=exw_usd, exw_cotado_data=data or date.today(),
                 exw_cotado_fonte="teste", cost_method=CostMethod.ktc_quoted.value,
                 ncm="6302.31.00", preco_base=150.0)
    dados.update(kw)
    p = Produto(**dados)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def nova_cotacao(session, **kw):
    from app.models import Cliente, Cotacao
    cliente = session.exec(select(Cliente)).first()
    if cliente is None:
        cliente = Cliente(nome="Cliente crise", cidade_uf="São Paulo", finalidade="REVENDA")
        session.add(cliente)
        session.commit()
        session.refresh(cliente)
    dados = dict(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                 estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                 condicao_pagamento="30", numero=f"CRISIS-{next(_SEQ):05d}", status="rascunho",
                 freight_type="FOB")
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def add_item(session, cot, produto, quantidade=10.0, ator=None):
    from app import workflow_service as ws
    from app.routers.cotacoes import adicionar_item
    chamar(adicionar_item, RequestFalsa(ator or _novo_usuario("ADMIN")), cotacao_id=cot.id,
           produto_id=produto.id, quantidade=quantidade, modo="margem", valor=None,
           session=session)
    session.commit()
    return ws.itens_de(session, cot.id)[-1]


def salvar_cabecalho(session, cot, **muda):
    """POST /cotacoes/{id}/atualizar com o formulário completo, como a tela envia."""
    from app.models import Cotacao
    from app.routers.cotacoes import atualizar_cabecalho
    session.expire_all()
    cot = session.get(Cotacao, cot.id)
    form = dict(condicao_pagamento=cot.condicao_pagamento, estado_destino=cot.estado_destino or "",
                contribuinte_icms="sim" if cot.contribuinte_icms else "nao",
                freight_type=cot.freight_type or "CIF",
                freight_valor=str(cot.freight_valor) if cot.freight_valor else "",
                validade_dias=0, vendedor=cot.vendedor or "", frete=cot.frete or "",
                prazo_entrega="", contato_nome="", departamento_contato="", observacoes="",
                observacao_cliente="", termos_texto="", local_entrega="", estado_origem="")
    form.update(muda)
    r = chamar(atualizar_cabecalho, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
               session=session, **form)
    session.commit()
    session.expire_all()
    return r.headers.get("location", "")


def editar_quantidade(session, cot, item, quantidade, ator=None, **extra):
    from app.routers.cotacoes import editar_item
    form = {"quantidade": str(quantidade)}
    form.update({k: str(v) for k, v in extra.items()})
    r = asyncio.run(editar_item(cot.id, item.id, RequestComForm(ator or _novo_usuario("ADMIN"), form),
                                session))
    session.commit()
    session.expire_all()
    return r


def recomendado_para(session, cot, item):
    """O preço que o motor forma para o cenário ATUAL da cotação, na margem-alvo do item."""
    from app.models import Cotacao, Produto
    from app.pricing_engine import calcular_por_margem
    from app.routers.cotacoes import montar_regras
    session.expire_all()
    cot = session.get(Cotacao, cot.id)
    produto = session.get(Produto, item.produto_id)
    regras, _r, ctx = montar_regras(cot, session, produto, item=item)
    if regras is None:
        return None, ctx
    return calcular_por_margem(item.custo_unitario, 1, item.margem_padrao_pct, regras).preco_negociado, ctx

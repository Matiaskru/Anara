"""Negociação sob a política comercial de 16/09/2026 — de ponta a ponta, com banco.

Cobre o que a decisão exige provar (Fase 3A, §18–§21): Daune travada com comissão fixa;
Decor 12/10 com comissão 10→5; comissão global única do bloco variável, ponderada por valor,
presa pelo piso e nunca abaixo de 5% sozinha; exceção aprovável quando o piso fura com 5%;
payload da vendedora sem economia; rotas; invalidação de aprovação; detecção de rascunho
anterior à política.
"""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import admin_service as adm
from app import comercial_service as com
from app import politica_comercial as pol
from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, ZERO, dinheiro
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Produto, StatusCotacao,
    TipoFornecedor,
)
from conftest import RequestFalsa, _novo_usuario
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402

_SEQ = iter(range(1, 100_000))


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def chamar(funcao, request, **kwargs):
    import inspect
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


class RequestJSON(RequestFalsa):
    """Request com corpo JSON, para as rotas de negociação."""

    def __init__(self, usuario, corpo_json):
        super().__init__(usuario)
        self._json = corpo_json

    async def json(self):
        return self._json


class FormFalso:
    def __init__(self, dados):
        self._dados = dados

    def get(self, chave, padrao=None):
        return self._dados.get(chave, padrao)


class RequestForm(RequestFalsa):
    def __init__(self, usuario, dados):
        super().__init__(usuario)
        self._dados = dados

    async def form(self):
        return FormFalso(self._dados)


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def cliente(session):
    c = session.exec(select(Cliente)).first()
    if c is None:
        c = Cliente(nome="Cliente negociação", estado="São Paulo")
        session.add(c)
        session.commit()
        session.refresh(c)
    return c


def produto_nacional(session, fornecedor, custo=100.0, familia="Pillow"):
    sku = f"NEG-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia=familia,
                cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def produto_ktc(session, ktc, exw=10.0, familia="Flat Sheet", tc=300):
    sku = f"NEG-KTC-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Lençol {sku}", familia=familia, thread_count=tc,
                fornecedor_id=ktc.id, cost_method=CostMethod.ktc_quoted.value,
                exw_cotado_usd=exw, peso_kg=1.0, preco_base=100.0, ativo=True)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def nova_cotacao(session, cliente, **kw):
    dados = dict(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                 estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                 condicao_pagamento="30", numero=f"NEG-{next(_SEQ):05d}",
                 status=StatusCotacao.rascunho.value, freight_type="FOB")
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def add_item(session, cot, produto, quantidade=10.0, ator=None, modo="margem", valor=None):
    from app.routers.cotacoes import adicionar_item
    r = chamar(adicionar_item, RequestFalsa(ator or _novo_usuario("ADMIN")),
               cotacao_id=cot.id, produto_id=produto.id, quantidade=quantidade,
               modo=modo, valor=valor, session=session)
    session.commit()
    return session.get(CotacaoItem, corpo(r)["id"])


def negociar(session, cot, propostas, ator=None):
    av = com.aplicar_negociacao(session, cot, propostas, ator=ator or _novo_usuario("ADMIN"))
    session.commit()
    for it in ws.itens_de(session, cot.id):
        session.refresh(it)
    return av


def rec(item):
    return dinheiro(item.preco_recomendado)


def com_desconto(item, pct):
    return dinheiro(rec(item) * (D("1") - D(pct)))


# ===========================================================================
# §18 — DAUNE
# ===========================================================================
def test_daune_forma_a_12_com_comissao_5_e_negociado_igual_ao_recomendado(session, fornecedores,
                                                                          cliente):
    p = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    assert it.margem_padrao_pct == aprox(0.12) and it.piso_margem_pct == aprox(0.12)
    assert it.comissao_formacao_pct == aprox(0.05) and it.comissao_pct == aprox(0.05)
    assert it.preco_travado is True and it.politica_comercial == pol.ROTULO
    assert dinheiro(it.preco_negociado) == rec(it)
    assert it.margem_liquida == aprox(0.12, abs=MARGEM_DO_CENTAVO)
    av = com.avaliar_negociacao(session, cot)
    assert av.itens[0].linha.editavel is False
    assert av.itens[0].linha.motivo_nao_editavel == pol.MOTIVO_TRAVADO
    assert av.requer_aprovacao is False


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO", "ADMIN", "OWNER"])
def test_ninguem_altera_o_preco_daune_pela_edicao_do_item(session, fornecedores, cliente, papel):
    from app.routers.cotacoes import editar_item
    p = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    menor = str(rec(it) - D("1.00"))
    with pytest.raises(HTTPException) as erro:
        asyncio.run(editar_item(cot.id, it.id, RequestForm(_novo_usuario(papel),
                                {"quantidade": "10", "modo": "preco", "valor": menor}),
                                session=session))
    assert erro.value.status_code == 409 and "travado" in erro.value.detail
    session.refresh(it)
    assert dinheiro(it.preco_negociado) == rec(it)
    # quantidade pode mudar, com o unitário travado
    r = asyncio.run(editar_item(cot.id, it.id, RequestForm(_novo_usuario(papel),
                                {"quantidade": "12", "modo": "preco", "valor": str(rec(it))}),
                                session=session))
    assert corpo(r)["quantidade"] == 12.0
    # e a alavanca de margem/markup também não passa
    with pytest.raises(HTTPException):
        asyncio.run(editar_item(cot.id, it.id, RequestForm(_novo_usuario(papel),
                                {"quantidade": "12", "modo": "margem", "valor": "0.20"}),
                                session=session))


def test_daune_nao_entra_pela_rota_de_negociacao_nem_ao_adicionar_com_outro_preco(
        session, fornecedores, cliente):
    p = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    with pytest.raises(HTTPException) as erro:
        com.aplicar_negociacao(session, cot, {it.id: rec(it) - D("0.01")},
                               ator=_novo_usuario("OWNER"))
    assert erro.value.status_code == 409
    # adicionar com preço explícito diferente do recomendado: recusado
    with pytest.raises(HTTPException) as erro:
        add_item(session, cot, p, modo="preco", valor=float(rec(it)) + 10)
    assert erro.value.status_code == 409
    # adicionar com a alavanca padrão (política): aceito, no recomendado
    outro = add_item(session, cot, p)
    assert dinheiro(outro.preco_negociado) == rec(outro)


def test_mixed_quote_nao_altera_a_comissao_daune_nem_a_inclui_no_desconto(session, fornecedores,
                                                                             cliente):
    d = produto_nacional(session, fornecedores["DAUNE"])
    k = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    id_ = add_item(session, cot, d)
    ik = add_item(session, cot, k)
    comissao_daune_antes = D(id_.comissao_pct)
    av = negociar(session, cot, {ik.id: com_desconto(ik, "0.05")})
    session.refresh(id_)
    assert D(id_.comissao_pct) == comissao_daune_antes == D("0.05")
    linha_daune = next(a for a in av.itens if a.item.id == id_.id)
    assert linha_daune.linha.elegivel_variavel is False
    assert av.comissao.recomendado_variavel == dinheiro(rec(ik) * D("10"))
    assert av.comissao.desconto_ratio == aprox(D("0.05"), abs=D("0.0001"))


def test_daune_cnet_golden_nao_mudou():
    from app import custo_service as cs
    conta = cs.cnet_nacional(249.37)
    assert conta.icms_credito == aprox(D("29.9244"), abs=D("1e-6"))
    assert conta.base_pis_cofins == aprox(D("219.4456"), abs=D("1e-6"))
    assert conta.pis_cofins_credito == aprox(D("20.298718"), abs=D("1e-6"))
    assert conta.cnet == aprox(D("199.146882"), abs=D("1e-6"))


def test_gates_c_new_11_e_12_do_handoff_continuam_fechados():
    """Os 37 SKUs fechados no seller gate do handoff de 13/09 não são reabertos por esta política."""
    import importlib.util
    import os
    caminho = os.path.expanduser(
        "~/ANARA_PRICING_ENGINE_HANDOFF_2026-09-13/engine/seller_gate.py")
    if not os.path.exists(caminho):
        pytest.skip("pacote de handoff não está nesta máquina")
    spec = importlib.util.spec_from_file_location("seller_gate_handoff", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(range(340, 349)) <= set(mod.NAO_PUBLICAVEIS)
    assert set(range(254, 266)) <= set(mod.NAO_PUBLICAVEIS)
    assert set(range(274, 290)) <= set(mod.NAO_PUBLICAVEIS)


# ===========================================================================
# §19 — DECOR
# ===========================================================================
def test_decor_12_10_editavel_e_comissao_cai_com_desconto(session, fornecedores, cliente):
    p = produto_nacional(session, fornecedores["DECOR_TRICOT"], familia="Bed Runner")
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    assert it.margem_padrao_pct == aprox(0.12) and it.piso_margem_pct == aprox(0.10)
    assert it.comissao_pct == aprox(0.10) and it.preco_travado is False
    av = negociar(session, cot, {it.id: com_desconto(it, "0.03")})
    assert av.comissao.proporcional_pct == aprox(D("0.097"), abs=D("1e-4"))
    assert D(it.comissao_pct) < D("0.10")
    assert av.requer_aprovacao is False


def test_decor_margem_pode_cair_ate_10_dentro_da_autonomia(session, fornecedores, cliente):
    """8% de desconto: a comissão cai além da proporcional para segurar o piso de 10%."""
    p = produto_nacional(session, fornecedores["DECOR_TRICOT"], familia="Bed Runner")
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, "0.08")})
    a = av.itens[0]
    assert av.comissao.limitada_pelo_piso is True
    assert D(av.comissao.variavel_pct) >= D("0.05")
    assert a.resultado.margem_liquida == aprox(D("0.10"), abs=MARGEM_DO_CENTAVO)
    assert a.viola_piso is False and av.requer_aprovacao is False
    assert ws.avaliar(session, cot).precisa_aprovacao is False


def test_decor_abaixo_do_piso_com_5_por_cento_exige_aprovacao_e_nao_bloqueia(session,
                                                                              fornecedores,
                                                                              cliente):
    p = produto_nacional(session, fornecedores["DECOR_TRICOT"], familia="Bed Runner")
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, "0.15")})
    assert D(av.comissao.variavel_pct) == D("0.05")
    assert av.itens[0].viola_piso is True and av.requer_aprovacao is True
    assert av.autonomia_status == pol.REQUER_APROVACAO
    prontidao = ws.avaliar(session, cot)
    assert prontidao.precisa_aprovacao is True and not prontidao.blockers
    assert {e.motivo for e in prontidao.excecoes} == {wf.MARGEM_ABAIXO_PISO}
    assert wf.PRECO_ABAIXO not in {e.motivo for e in prontidao.excecoes}
    # aprovável pelo workflow canônico
    pedido = ws.solicitar_aprovacao(session, cot, ator=_novo_usuario("VENDEDOR_INTERNO"),
                                    justificativa="cliente estratégico")
    ws.decidir(session, cot, pedido.id, ator=_novo_usuario("OWNER"), aprovar=True)
    session.commit()
    assert ws.avaliar(session, cot).pode_emitir is True


# ===========================================================================
# §21 — comissão global, casos A–J
# ===========================================================================
def test_caso_A_sem_desconto_10(session, fornecedores, cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = com.avaliar_negociacao(session, cot)
    assert av.comissao.variavel_pct == D("0.10")
    assert D(it.comissao_pct) == D("0.10")
    assert av.taxa_efetiva == aprox(D("0.10"), abs=D("1e-4"))
    assert it.margem_liquida == aprox(0.20, abs=MARGEM_DO_CENTAVO)   # lençol ≥ 300TC


def test_caso_B_18_por_cento_de_desconto_da_8_2_antes_do_piso(session, fornecedores, cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, "0.18")})
    assert av.comissao.proporcional_pct == aprox(D("0.082"), abs=D("1e-4"))


@pytest.mark.parametrize("desconto", ["0.50", "0.60"])
def test_caso_C_D_desconto_pesado_proporcional_para_em_5(session, fornecedores, cliente, desconto):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, desconto)})
    assert av.comissao.proporcional_pct == aprox(D("0.05"), abs=D("1e-4"))
    assert av.comissao.variavel_pct == aprox(D("0.05"), abs=D("1e-4"))


def test_caso_E_desconto_ponderado_por_valor(session, fornecedores, cliente):
    grande = produto_ktc(session, fornecedores["KTC"], exw=100.0)     # ~10× o valor
    pequeno = produto_ktc(session, fornecedores["KTC"], exw=10.0)
    cot = nova_cotacao(session, cliente)
    ig = add_item(session, cot, grande)
    ip = add_item(session, cot, pequeno)
    av = negociar(session, cot, {ig.id: com_desconto(ig, "0.20")})     # 20% só no grande
    r = dinheiro(rec(ig) * 10) + dinheiro(rec(ip) * 10)
    n = dinheiro(com_desconto(ig, "0.20") * 10) + dinheiro(rec(ip) * 10)
    assert av.comissao.desconto_ratio == aprox(D("1") - n / r, abs=D("1e-9"))
    assert av.comissao.desconto_ratio > D("0.10"), "a média simples diria 10%"


def test_caso_F_ktc_e_decor_recebem_uma_unica_comissao_variavel(session, fornecedores, cliente):
    k = produto_ktc(session, fornecedores["KTC"])
    d = produto_nacional(session, fornecedores["DECOR_TRICOT"], familia="Bed Runner")
    cot = nova_cotacao(session, cliente)
    ik = add_item(session, cot, k)
    idc = add_item(session, cot, d)
    av = negociar(session, cot, {ik.id: com_desconto(ik, "0.04")})
    session.refresh(ik); session.refresh(idc)
    # a coluna é REAL: 16 dígitos do float contra 34 do Decimal — a diferença é só de escrita
    assert D(ik.comissao_pct) == D(idc.comissao_pct)
    assert D(ik.comissao_pct) == aprox(av.comissao.variavel_pct, abs=D("1e-12"))
    assert D("0.05") <= D(ik.comissao_pct) < D("0.10")


def test_caso_G_ktc_decor_e_daune_uma_comissao_total_para_a_vendedora(session, fornecedores,
                                                                       cliente):
    k = produto_ktc(session, fornecedores["KTC"])
    d = produto_nacional(session, fornecedores["DECOR_TRICOT"], familia="Bed Runner")
    da = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    ik, idc, ida = add_item(session, cot, k), add_item(session, cot, d), add_item(session, cot, da)
    av = negociar(session, cot, {ik.id: com_desconto(ik, "0.04")})
    for it in (ik, idc, ida):
        session.refresh(it)
    assert D(ida.comissao_pct) == D("0.05")
    assert D(ik.comissao_pct) == D(idc.comissao_pct)
    assert D(ik.comissao_pct) == aprox(av.comissao.variavel_pct, abs=D("1e-12"))
    # uma comissão só: soma dos R$ de cada item, taxa = total ÷ receita comissionável
    total = D(ik.comissao_valor) + D(idc.comissao_valor) + D(ida.comissao_valor)
    receita = D(ik.faturamento) + D(idc.faturamento) + D(ida.faturamento)
    assert av.comissao_total_valor == total
    assert av.taxa_efetiva == total / receita
    assert av.comissao_travada_valor == D(ida.comissao_valor)
    vend = com.payload_vendedora(av)
    assert vend["comissao_estimada_valor"] == float(total)
    assert vend["comissao_estimada_pct_efetiva"] == float(total / receita)
    assert "economia" not in vend and encontrar_confidenciais(vend) == []
    # e o resumo do workflow (aprovação/snapshot) diz o mesmo
    resumo = wf.resumo_comercial(ws.itens_de(session, cot.id))
    assert D(resumo["comissao_estimada_valor"]) == total


def test_caso_H_item_rentavel_nao_esconde_violacao_de_piso(session, fornecedores, cliente):
    a = produto_ktc(session, fornecedores["KTC"])
    b = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    ia, ib = add_item(session, cot, a), add_item(session, cot, b)
    av = negociar(session, cot, {ia.id: com_desconto(ia, "0.30"),
                                 ib.id: dinheiro(rec(ib) * D("1.5"))})
    # o subtotal está ACIMA do recomendado e o desconto ponderado é pequeno…
    assert av.subtotal_negociado > av.subtotal_recomendado
    # …e mesmo assim o item A fura o piso e a cotação exige aprovação
    la = next(x for x in av.itens if x.item.id == ia.id)
    lb = next(x for x in av.itens if x.item.id == ib.id)
    assert la.viola_piso is True and lb.viola_piso is False
    assert av.requer_aprovacao is True
    assert av.comissao.variavel_pct == D("0.05")


def test_caso_I_piso_segura_a_comissao_abaixo_da_proporcional(session, fornecedores, cliente):
    """10% de desconto num lençol 20/17: proporcional 9%, mas o piso só admite ~8,2%."""
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, "0.10")})
    assert av.comissao.proporcional_pct == aprox(D("0.09"), abs=D("1e-4"))
    assert av.comissao.limitada_pelo_piso is True
    assert D("0.05") < av.comissao.variavel_pct < D("0.09")
    assert av.comissao.limitada_por == it.nome_produto
    assert av.itens[0].resultado.margem_liquida == aprox(D("0.17"), abs=MARGEM_DO_CENTAVO)
    assert av.itens[0].viola_piso is False and av.requer_aprovacao is False


def test_caso_J_com_5_por_cento_o_piso_ainda_fura_requer_aprovacao(session, fornecedores, cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    av = negociar(session, cot, {it.id: com_desconto(it, "0.20")})
    assert av.comissao.variavel_pct == D("0.05")
    assert av.itens[0].viola_piso is True and av.requer_aprovacao is True
    assert ws.avaliar(session, cot).precisa_aprovacao is True


# ===========================================================================
# Consistência: item ↔ cotação ↔ workflow ↔ snapshot
# ===========================================================================
def test_adicionar_item_com_desconto_recalcula_a_comissao_do_outro(session, fornecedores, cliente):
    a = produto_ktc(session, fornecedores["KTC"])
    b = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    ia = add_item(session, cot, a)
    assert D(ia.comissao_pct) == D("0.10")
    ib = add_item(session, cot, b)
    negociar(session, cot, {ib.id: com_desconto(ib, "0.06")})
    session.refresh(ia)
    assert D(ia.comissao_pct) < D("0.10"), "a comissão é da cotação: A acompanha B"
    # e a identidade da linha continua fechando ao centavo
    assert D(ia.faturamento) == (D(ia.custo_total) + D(ia.impostos) + D(ia.comissao_valor)
                                 + D(ia.lucro))
    # remover B devolve A a 10%
    from app.routers.cotacoes import remover_item
    chamar(remover_item, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
           item_id=ib.id, session=session)
    session.refresh(ia)
    assert D(ia.comissao_pct) == D("0.10")


def test_preco_mostrado_e_o_preco_economico(session, fornecedores, cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p, quantidade=7.0)
    av = negociar(session, cot, {it.id: D("123.456")})       # entra como quantia comercial
    assert dinheiro(it.preco_negociado) == D("123.46")
    assert D(it.faturamento) == dinheiro(D("123.46") * 7)
    vend = com.payload_vendedora(av)
    assert vend["itens"][0]["preco_negociado"] == 123.46
    assert vend["itens"][0]["total_linha"] == float(dinheiro(D("123.46") * 7))
    assert vend["subtotal_negociado"] == float(D(it.faturamento))


def test_negociar_invalida_aprovacao_e_a_negociacao_entra_no_fingerprint(session, fornecedores,
                                                                          cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    negociar(session, cot, {it.id: com_desconto(it, "0.20")})
    pedido = ws.solicitar_aprovacao(session, cot, ator=_novo_usuario("VENDEDOR_INTERNO"),
                                    justificativa="volume")
    ws.decidir(session, cot, pedido.id, ator=_novo_usuario("OWNER"), aprovar=True)
    session.commit()
    assert ws.avaliar(session, cot).aprovacao_valida is True
    negociar(session, cot, {it.id: com_desconto(it, "0.22")})
    session.refresh(pedido)
    assert pedido.status == ws.INVALIDADA
    assert ws.avaliar(session, cot).aprovacao_valida is False


def test_snapshot_de_emissao_carrega_a_comissao_estimada(session, fornecedores, cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    snapshot = ws.emitir(session, cot, ator=_novo_usuario("OWNER"))
    session.commit()
    totais = json.loads(snapshot.totais_json)
    assert totais["comissao_estimada_valor"] == it.comissao_valor
    assert totais["comissao_estimada_pct_efetiva"] == aprox(0.10, abs=1e-4)


# ===========================================================================
# Payloads e rotas — a vendedora vê a comissão dela, e nada da economia
# ===========================================================================
CONFIDENCIAIS_DA_VENDEDORA = (
    "custo", "custo_unitario", "cnet", "exw_usd", "lucro", "margem_liquida", "margem_alvo",
    "margem_padrao_pct", "piso_margem_pct", "margem_realizada_pct", "comissao_pct",
    "comissao_formacao_pct", "comissao_max_piso_pct", "comissao_variavel_pct",
    "comissao_base_pct", "comissao_min_pct", "economia", "memoria", "impostos",
    "absorvido_por_margem", "absorvido_por_comissao", "viola_piso",
)


def _chaves(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _chaves(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _chaves(v, acc)
    return acc


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_rotas_de_negociacao_para_a_vendedora(session, fornecedores, cliente, papel):
    from app.routers.negociacao import aplicar, negociacao_atual, preview
    k = produto_ktc(session, fornecedores["KTC"])
    da = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    ik, ida = add_item(session, cot, k), add_item(session, cot, da)
    vend = _novo_usuario(papel)

    atual = corpo(chamar(negociacao_atual, RequestFalsa(vend), cotacao_id=cot.id,
                         session=session))
    assert set(atual) == {"itens", "subtotal_recomendado", "subtotal_negociado", "desconto_pct",
                          "frete", "total_proposta", "comissao_estimada_pct_efetiva",
                          "comissao_estimada_valor", "autonomia_status", "requer_aprovacao"}
    assert set(atual["itens"][0]) == {"item_id", "produto_id", "nome_produto", "quantidade",
                                      "preco_recomendado", "preco_negociado", "total_linha",
                                      "editavel", "motivo_nao_editavel"}
    assert not (_chaves(atual) & set(CONFIDENCIAIS_DA_VENDEDORA))
    assert encontrar_confidenciais(atual) == []
    assert atual["comissao_estimada_valor"] > 0 and atual["autonomia_status"] == pol.DENTRO_DA_AUTONOMIA
    daune = next(i for i in atual["itens"] if i["item_id"] == ida.id)
    assert daune["editavel"] is False and daune["motivo_nao_editavel"]

    # preview não grava
    proposta = {"itens": [{"item_id": ik.id, "preco_negociado": str(com_desconto(ik, "0.20"))}]}
    prev = corpo(asyncio.run(preview(RequestJSON(vend, proposta), cot.id, session=session)))
    assert prev["autonomia_status"] == pol.REQUER_APROVACAO and prev["requer_aprovacao"] is True
    assert prev["comissao_estimada_valor"] < atual["comissao_estimada_valor"]
    session.refresh(ik)
    assert dinheiro(ik.preco_negociado) == rec(ik)

    # preço Daune pela rota: 409
    with pytest.raises(HTTPException) as erro:
        asyncio.run(preview(RequestJSON(vend, {"itens": [
            {"item_id": ida.id, "preco_negociado": str(rec(ida) - D("1"))}]}),
            cot.id, session=session))
    assert erro.value.status_code == 409

    # aplicar grava
    r = corpo(asyncio.run(aplicar(RequestJSON(vend, proposta), cot.id, session=session)))
    session.refresh(ik)
    assert dinheiro(ik.preco_negociado) == com_desconto(ik, "0.20")
    assert r["requer_aprovacao"] is True and encontrar_confidenciais(r) == []
    assert ws.avaliar(session, cot).precisa_aprovacao is True

    # corpo inválido: 400
    with pytest.raises(HTTPException) as erro:
        asyncio.run(preview(RequestJSON(vend, {"itens": [{"item_id": ik.id,
                                                          "preco_negociado": "-1"}]}),
                            cot.id, session=session))
    assert erro.value.status_code == 400


def test_rota_de_negociacao_para_admin_traz_a_economia(session, fornecedores, cliente):
    from app.routers.negociacao import negociacao_atual
    k = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    ik = add_item(session, cot, k)
    negociar(session, cot, {ik.id: com_desconto(ik, "0.10")})
    adm_ = corpo(chamar(negociacao_atual, RequestFalsa(_novo_usuario("ADMIN")),
                        cotacao_id=cot.id, session=session))
    econ = adm_["economia"]
    for chave in ("comissao_base_pct", "comissao_min_pct", "desconto_ratio_comissao",
                  "comissao_proporcional_pct", "comissao_max_piso_pct", "comissao_variavel_pct",
                  "limitada_pelo_piso", "limitada_por", "absorvido_por_comissao",
                  "absorvido_por_margem", "absorvido_por_impostos_e_frete", "lucro_total",
                  "custo_total", "margem_agregada_pct"):
        assert chave in econ
    item = econ["itens"][0]
    for chave in ("custo_unitario", "margem_alvo_pct", "piso_margem_pct", "margem_realizada_pct",
                  "lucro", "comissao_aplicada_pct", "comissao_max_piso_pct", "viola_piso"):
        assert chave in item
    # o desconto se reparte entre comissão, margem e impostos — e a soma é o desconto
    desconto = D(adm_["subtotal_recomendado"]) - D(adm_["subtotal_negociado"])
    partes = (D(econ["absorvido_por_comissao"]) + D(econ["absorvido_por_margem"])
              + D(econ["absorvido_por_impostos_e_frete"]))
    assert partes == aprox(desconto, abs=MEIO_CENTAVO * 4)


def test_item_da_tela_para_a_vendedora_traz_recomendado_e_editavel_sem_economia(
        session, fornecedores, cliente):
    from app.routers.cotacoes import _item_para_json, _totais
    da = produto_nacional(session, fornecedores["DAUNE"])
    cot = nova_cotacao(session, cliente)
    ida = add_item(session, cot, da)
    vend = _item_para_json(ida, pode_ver_economia=False)
    assert vend["preco_recomendado"] and vend["editavel"] is False and vend["preco_travado"]
    assert encontrar_confidenciais(vend) == []
    for chave in ("piso_margem_pct", "comissao_pct", "comissao_formacao_pct",
                  "politica_comercial", "margem_padrao_pct", "lucro"):
        assert chave not in vend
    totais = _totais([ida], pode_ver_economia=False)
    assert "comissao_estimada_valor" in totais and "lucro" not in totais


# ===========================================================================
# Histórico — item anterior à política continua como foi formado, e é apontado
# ===========================================================================
def test_item_anterior_a_politica_mantem_regua_antiga_e_e_detectado(session, fornecedores,
                                                                      cliente):
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    it = add_item(session, cot, p)
    preco_antes = it.preco_negociado
    # simula um item formado antes de 16/09: sem política congelada, comissão por faixa
    it.politica_comercial = None
    it.piso_margem_pct = None
    it.comissao_formacao_pct = None
    it.preco_travado = False
    session.add(it)
    session.commit()

    novidades = adm.premissas_desatualizadas(session, cot, [it])
    assert novidades["desatualizado"] is True
    assert novidades["politica_anterior"] and "16/09/2026" in novidades["texto"]

    # recálculo do cabeçalho NÃO migra o item: continua sem política
    av = com.recalcular_comissao(session, cot)
    session.commit()
    session.refresh(it)
    assert it.politica_comercial is None and it.preco_negociado == preco_antes
    assert av.itens[0].linha.elegivel_variavel is False
    # e o workflow o avalia com a régua anterior: abaixo do recomendado é exceção de preço
    it.preco_negociado = float(rec(it) - D("1"))
    session.add(it)
    session.commit()
    assert wf.PRECO_ABAIXO in {e.motivo for e in wf.excecoes_do_item(it)}

    # a reprecificação explícita traz o item para a política
    from app.routers.cotacoes import atualizar_premissas
    chamar(atualizar_premissas, RequestFalsa(_novo_usuario("ADMIN")), cotacao_id=cot.id,
           session=session)
    session.refresh(it)
    assert it.politica_comercial == pol.ROTULO and it.piso_margem_pct == aprox(0.17)


def test_politica_ausente_no_banco_nao_vira_10_e_5(session, fornecedores, cliente):
    """Item da política sem as premissas de comissão: erro explícito, não fallback."""
    from app import config_service as cfg
    from app.models import Premissa
    p = produto_ktc(session, fornecedores["KTC"])
    cot = nova_cotacao(session, cliente)
    add_item(session, cot, p)
    base = cfg.premissa(session, pol.CHAVE_COMISSAO_BASE)
    base.ativo = False
    session.add(base)
    session.commit()
    try:
        with pytest.raises(pol.PoliticaInconsistente):
            com.avaliar_negociacao(session, cot)
    finally:
        base.ativo = True
        session.add(base)
        session.commit()


def test_admin_que_versiona_a_margem_nao_perde_a_politica_do_escopo(session, fornecedores):
    """Regra nova pelo painel herda piso, comissão e travamento do escopo encerrado."""
    from app.models import MargemRegra
    ator = _novo_usuario("OWNER")
    antes = {r.id: r.valid_to for r in session.exec(select(MargemRegra)).all()}
    decor = fornecedores["DECOR_TRICOT"]
    prop = adm.preview_margem(session, margem_pct="0.13", nome="Decor 13% (teste)",
                              fornecedor_id=decor.id, prioridade=20, fonte="teste")
    nova = adm.aplicar_margem(session, prop, ator=ator, margem_pct="0.13",
                              nome="Decor 13% (teste)", fornecedor_id=decor.id,
                              prioridade=20, fonte="teste")
    session.commit()
    try:
        assert nova.piso_pct == aprox(0.10) and nova.comissao_formacao_pct == aprox(0.10)
        assert nova.preco_travado is False and nova.politica == pol.ROTULO
        assert nova.margem_anterior_pct == aprox(0.12)
        produto = produto_nacional(session, decor, familia="Bed Runner")
        r = ps.margem_padrao(session, produto)
        assert r.margem_pct == aprox(0.13) and r.piso_pct == aprox(0.10)
    finally:
        for r in session.exec(select(MargemRegra)).all():
            if r.id not in antes:
                session.delete(r)
            elif r.valid_to != antes[r.id]:
                r.valid_to = antes[r.id]
                session.add(r)
        session.commit()

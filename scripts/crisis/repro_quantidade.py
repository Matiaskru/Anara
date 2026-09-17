#!/usr/bin/env python3
"""Reprodução: editar a QUANTIDADE pela tela converte o item para modo 'preco' (JS manda
modo=preco&valor=preço atual); depois disso, mudar o cenário não reforma o preço."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import RequestFalsa, chamar, preparar, usuario_admin
session = preparar("repro_qtd")
from sqlmodel import select
from app import pricing_service as ps
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, Produto
from app.routers.cotacoes import adicionar_item, atualizar_cabecalho, editar_item, montar_regras
from app.pricing_engine import calcular_por_margem
from app.dinheiro import D, dinheiro
ADMIN = usuario_admin(); REQ = RequestFalsa(ADMIN)

class ReqForm(RequestFalsa):
    def __init__(self, usuario, form):
        super().__init__(usuario); self._form = form
    async def form(self):
        return self._form

forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
p = next(p for p in session.exec(select(Produto).where(Produto.fornecedor_id == forn.id, Produto.ativo == True)).all()
         if (lambda c: c[0] and ps.status_canonico_do_custo(*c) == "CONFIRMADO")(ps.custo_para_precificar(session, p)))
cliente = session.exec(select(Cliente)).first()
cot = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA", condicao_pagamento="30", status="rascunho", freight_type="FOB", numero="QTD-1")
session.add(cot); session.commit(); session.refresh(cot)
chamar(adicionar_item, REQ, cotacao_id=cot.id, produto_id=p.id, quantidade=10.0, modo="margem", valor=None, session=session); session.commit()
it = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot.id)).first()
print(f"adicionado: modo={it.modo_edicao} valor_editado={it.valor_editado} preco={it.preco_negociado} rec={it.preco_recomendado}")
# a tela edita a quantidade: PUT modo=preco&valor=<preço atual>
r = asyncio.run(editar_item(cot.id, it.id, ReqForm(ADMIN, {"quantidade": "25", "modo": "preco", "valor": str(it.preco_negociado)}), session))
session.commit(); session.expire_all(); it = session.get(CotacaoItem, it.id)
print(f"após editar quantidade: modo={it.modo_edicao} valor_editado={it.valor_editado} preco={it.preco_negociado} qtd={it.quantidade}")
# muda o cenário: SP → RJ não contribuinte
form = dict(condicao_pagamento="30", estado_destino="Rio de Janeiro", contribuinte_icms="nao", freight_type="FOB", freight_valor="", validade_dias=0, vendedor="", frete="", prazo_entrega="", contato_nome="", departamento_contato="", observacoes="", observacao_cliente="", termos_texto="", local_entrega="", estado_origem="")
chamar(atualizar_cabecalho, REQ, cotacao_id=cot.id, session=session, **form); session.commit(); session.expire_all()
it = session.get(CotacaoItem, it.id); c = session.get(Cotacao, cot.id)
regras, _r, ctx = montar_regras(c, session, p, item=it)
esperado = calcular_por_margem(it.custo_unitario, 1, it.margem_padrao_pct, regras).preco_negociado
print(f"após SP→RJ não contribuinte: modo={it.modo_edicao} preco={it.preco_negociado} rec={it.preco_recomendado} icms={it.icms_pct} | preço que o motor forma para o cenário: {esperado}")
stale = dinheiro(D(it.preco_negociado)) != dinheiro(esperado)
print("PRECO_STALE_APOS_EDITAR_QUANTIDADE:", stale, f"(diferença R$ {dinheiro(esperado) - dinheiro(D(it.preco_negociado))}/un × {it.quantidade})")
# e o caso do preço 0: cenário bloqueado → editar quantidade → cenário resolvido
cot2 = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="Bahia", contribuinte_icms=False, finalidade="USO_CONSUMO", condicao_pagamento="30", status="rascunho", freight_type="FOB", numero="QTD-2")
session.add(cot2); session.commit(); session.refresh(cot2)
chamar(adicionar_item, REQ, cotacao_id=cot2.id, produto_id=p.id, quantidade=10.0, modo="margem", valor=None, session=session); session.commit()
it2 = session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot2.id)).first()
print(f"\ncenário bloqueado (BA não contribuinte): preco={it2.preco_negociado} status_fiscal={it2.status_fiscal}")
asyncio.run(editar_item(cot2.id, it2.id, ReqForm(ADMIN, {"quantidade": "12", "modo": "preco", "valor": "0"}), session)); session.commit()
form2 = dict(form, estado_destino="São Paulo", contribuinte_icms="sim")
chamar(atualizar_cabecalho, REQ, cotacao_id=cot2.id, session=session, **form2); session.commit(); session.expire_all()
it2 = session.get(CotacaoItem, it2.id)
print(f"após editar quantidade e resolver o cenário (SP contribuinte): modo={it2.modo_edicao} valor_editado={it2.valor_editado} preco={it2.preco_negociado} status_fiscal={it2.status_fiscal}")
print("PRECO_ZERO_PERMANENTE:", (it2.preco_negociado or 0) == 0)
sys.exit(1 if stale or (it2.preco_negociado or 0) == 0 else 0)

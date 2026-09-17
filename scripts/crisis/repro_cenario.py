#!/usr/bin/env python3
"""§3 — Reprecificação ao mudar o cenário da cotação. Reprodução do bug observado.

Cria uma cotação com KTC + Daune + Decor (custos CONFIRMADOS), registra os preços, altera
UMA variável por vez pela rota real `POST /cotacoes/{id}/atualizar` (ou pelo campo do modelo
quando a tela não expõe a variável) e compara cada item com o que o motor forma para o cenário
NOVO. Também testa item negociado (modo preço) e produto personalizado.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, RequestFalsa, chamar, preparar, usuario_admin  # noqa: E402

session = preparar("repro_cenario")

from decimal import Decimal  # noqa: E402

from sqlmodel import select  # noqa: E402

from app import pricing_service as ps  # noqa: E402
from app import comercial_service as com  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, Produto  # noqa: E402
from app.pricing_engine import calcular_por_margem, calcular_por_preco  # noqa: E402
from app.routers.cotacoes import adicionar_item, atualizar_cabecalho, editar_item, montar_regras  # noqa: E402
from app.routers import workflow as wfr  # noqa: E402
from app import workflow_service as ws  # noqa: E402

ADMIN = usuario_admin()
REQ = RequestFalsa(ADMIN)
achados = []


def achado(codigo, gravidade, detalhe, **dados):
    achados.append({"codigo": codigo, "gravidade": gravidade, "detalhe": detalhe, **dados})
    print(f"  [{gravidade}] {codigo}: {detalhe}")


def produto_confirmado(codigo_fornecedor, familia=None):
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == codigo_fornecedor)).first()
    q = select(Produto).where(Produto.fornecedor_id == forn.id, Produto.ativo == True)  # noqa: E712
    for p in session.exec(q).all():
        if familia and p.familia != familia:
            continue
        custo, mem = ps.custo_para_precificar(session, p)
        if custo and ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO":
            return p
    return None


def esperado(cot, it, produto):
    """O que o motor forma para o cenário ATUAL da cotação, para este item."""
    regras, _r, ctx = montar_regras(cot, session, produto, item=it)
    if regras is None:
        return None, ctx
    if it.modo_edicao == "preco":
        res = calcular_por_preco(it.custo_unitario, it.quantidade, it.valor_editado, regras, it.preco_base)
    else:
        res = calcular_por_margem(it.custo_unitario, it.quantidade, it.valor_editado, regras, it.preco_base)
    rec = calcular_por_margem(it.custo_unitario, 1, it.margem_padrao_pct, regras).preco_negociado
    return {"preco": res.preco_negociado, "margem": res.margem_liquida, "recomendado": rec,
            "icms": ctx["icms_pct"], "encargo": ctx["encargo_pct"], "status_fiscal": ctx["status_fiscal"]}, ctx


def itens(cot):
    return session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot.id).order_by(CotacaoItem.ordem)).all()


def foto(cot):
    session.expire_all()
    out = {}
    for it in itens(cot):
        out[it.id] = {"preco": D(it.preco_negociado), "margem": D(it.margem_liquida),
                      "recomendado": D(it.preco_recomendado), "icms": it.icms_pct,
                      "encargo": it.encargo_pct, "destino": it.uf_destino_fiscal,
                      "fin": it.finalidade, "cf": it.consumidor_final, "status_fiscal": it.status_fiscal,
                      "difal": it.difal_pct, "modo": it.modo_edicao}
    return out


def conferir(rotulo, cot, produtos):
    session.expire_all()
    cot = session.get(Cotacao, cot.id)
    ok = True
    for it in itens(cot):
        esp, ctx = esperado(cot, it, produtos[it.produto_id])
        if esp is None:
            bloqueado_no_item = "REVIEW_REQUIRED" in (it.status_fiscal or "", it.status_pagamento or "")
            if not bloqueado_no_item or (it.preco_negociado or 0) > 0:
                achado("PRECO_STALE_CENARIO_BLOQUEADO", "P0",
                       f"{rotulo}: item {it.nome_produto} — cenário irresolvido ({ctx.get('motivo_bloqueio')}) mas item mostra preço {it.preco_negociado} status {it.status_fiscal}")
                ok = False
            continue
        dif = {}
        if dinheiro(D(it.preco_negociado)) != dinheiro(esp["preco"]):
            dif["preco"] = (it.preco_negociado, str(esp["preco"]))
        if dinheiro(D(it.preco_recomendado or 0)) != dinheiro(esp["recomendado"]):
            dif["recomendado"] = (it.preco_recomendado, str(esp["recomendado"]))
        if abs(D(it.margem_liquida or 0) - esp["margem"]) > Decimal("0.00005"):
            dif["margem"] = (it.margem_liquida, str(esp["margem"]))
        if D(it.icms_pct or 0) != D(esp["icms"] or 0):
            dif["icms"] = (it.icms_pct, esp["icms"])
        if D(it.encargo_pct or 0) != D(esp["encargo"] or 0):
            dif["encargo"] = (it.encargo_pct, esp["encargo"])
        if it.uf_destino_fiscal != ctx["uf_destino_fiscal"]:
            dif["uf_destino"] = (it.uf_destino_fiscal, ctx["uf_destino_fiscal"])
        if (it.finalidade or "") != (ctx["finalidade"] or ""):
            dif["finalidade"] = (it.finalidade, ctx["finalidade"])
        if dif:
            ok = False
            achado("PRECO_STALE", "P0", f"{rotulo}: item {it.nome_produto} ({it.modo_edicao}) não reflete o cenário: {dif}", cotacao=cot.id, item=it.id)
    if ok:
        print(f"  ok  {rotulo}: {len(itens(cot))} itens reprecificados conforme o motor")
    return ok


def cabecalho(cot, **muda):
    """POST /cotacoes/{id}/atualizar com o formulário como a tela envia (todos os campos)."""
    session.expire_all()
    cot = session.get(Cotacao, cot.id)
    form = dict(condicao_pagamento=cot.condicao_pagamento, estado_destino=cot.estado_destino or "",
                contribuinte_icms="sim" if cot.contribuinte_icms else "nao",
                freight_type=cot.freight_type or "CIF",
                freight_valor=str(cot.freight_valor) if cot.freight_valor else "",
                validade_dias=cot.validade_dias or 0, vendedor=cot.vendedor or "",
                frete=cot.frete or "", prazo_entrega=cot.prazo_entrega or "",
                contato_nome=cot.contato_nome or "", departamento_contato=cot.departamento_contato or "",
                observacoes=cot.observacoes or "", observacao_cliente=cot.observacao_cliente or "",
                termos_texto=cot.termos_texto or "", local_entrega=cot.local_entrega or "", estado_origem="")
    form.update(muda)
    r = chamar(atualizar_cabecalho, REQ, cotacao_id=cot.id, session=session, **form)
    session.commit()
    return r.headers.get("location")


def nova_cotacao(**kw):
    cliente = session.exec(select(Cliente)).first()
    dados = dict(cliente_id=cliente.id, estado_origem="São Paulo", uf_origem_fiscal="SP",
                 estado_destino="SP", contribuinte_icms=True, finalidade="REVENDA",
                 condicao_pagamento="30", numero=f"CRISIS-{os.getpid()}-{len(achados)}",
                 status="rascunho", freight_type="FOB")
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c); session.commit(); session.refresh(c)
    return c


def add(cot, produto, qtd=10.0):
    r = chamar(adicionar_item, REQ, cotacao_id=cot.id, produto_id=produto.id, quantidade=qtd,
               modo="margem", valor=None, session=session)
    session.commit()
    return json.loads(bytes(r.body))


print("== produtos escolhidos ==")
ktc = produto_confirmado("KTC", "Flat Sheet") or produto_confirmado("KTC")
daune = produto_confirmado("DAUNE")
decor = produto_confirmado("DECOR_TRICOT")
for p in (ktc, daune, decor):
    print("  ", p.id if p else None, p.sku_key if p else None, p.familia if p else None, p.cost_method if p else None)
if not decor:
    print("  ! Decor sem custo CONFIRMADO — escolhendo qualquer Decor com custo")
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).first()
    for p in session.exec(select(Produto).where(Produto.fornecedor_id == forn.id)).all():
        if ps.custo_para_precificar(session, p)[0]:
            decor = p; break
    print("   →", decor.id, decor.sku_key, decor.status_custo, ps.status_canonico_do_custo(*ps.custo_para_precificar(session, decor)))
produtos = {p.id: p for p in (ktc, daune, decor) if p}

# ---------------------------------------------------------------------------
print("\n== A. cotação SP→SP contribuinte REVENDA 30 dias, 3 itens em modo margem ==")
cot = nova_cotacao()
for p in produtos.values():
    add(cot, p)
base = foto(cot)
for k, v in base.items():
    print("   item", k, {a: str(b) for a, b in v.items()})
conferir("estado inicial", cot, produtos)

transicoes = [
    ("destino SP→RJ", dict(estado_destino="RJ")),
    ("destino RJ→SP", dict(estado_destino="SP")),
    ("destino SP→MG", dict(estado_destino="MG")),
    ("contribuinte SIM→NÃO", dict(contribuinte_icms="nao")),
    ("contribuinte NÃO→SIM", dict(contribuinte_icms="sim")),
    ("pagamento 30 → 30/60/90/120/150", dict(condicao_pagamento="30/60/90/120/150")),
    ("pagamento 30/60/90/120/150 → À VISTA", dict(condicao_pagamento="À VISTA")),
    ("pagamento À VISTA → 30", dict(condicao_pagamento="30")),
    ("frete FOB→CIF", dict(freight_type="CIF")),
    ("frete CIF valor 500", dict(freight_type="CIF", freight_valor="500")),
    ("frete CIF→A_COMBINAR", dict(freight_type="A_COMBINAR")),
    ("pagamento CARTAO (encargo não confirmado)", dict(condicao_pagamento="CARTAO")),
    ("pagamento volta 30", dict(condicao_pagamento="30")),
    ("destino SP→'' (definir depois)", dict(estado_destino="")),
    ("destino ''→SP", dict(estado_destino="SP")),
]
for rotulo, muda in transicoes:
    antes = foto(cot)
    loc = cabecalho(cot, **muda)
    depois = foto(cot)
    mudou = any(antes[k]["preco"] != depois[k]["preco"] or antes[k]["recomendado"] != depois[k]["recomendado"] for k in antes)
    print(f"\n-- {rotulo}  → {loc}  (itens mudaram: {mudou})")
    conferir(rotulo, cot, produtos)

# ---------------------------------------------------------------------------
print("\n== B. variáveis que a tela NÃO expõe: finalidade e origem fiscal (mudança no modelo) ==")
for rotulo, campo, valor in [("finalidade REVENDA→USO_CONSUMO", "finalidade", "USO_CONSUMO"),
                             ("finalidade USO_CONSUMO→ATIVO_IMOBILIZADO", "finalidade", "ATIVO_IMOBILIZADO"),
                             ("finalidade →REVENDA", "finalidade", "REVENDA"),
                             ("origem fiscal SP→SC", "uf_origem_fiscal", "SC"),
                             ("origem fiscal SC→SP", "uf_origem_fiscal", "SP")]:
    c = session.get(Cotacao, cot.id); setattr(c, campo, valor); session.add(c); session.commit()
    # o que a tela faria em seguida: salvar o cabeçalho sem mudar nada
    loc = cabecalho(cot)
    print(f"\n-- {rotulo} (+ salvar cabeçalho sem mudança) → {loc}")
    conferir(rotulo, cot, produtos)

print("\n== B2. cliente muda a finalidade (cotação sem finalidade própria) ==")
cot2 = nova_cotacao(finalidade=None, estado_destino="MG", contribuinte_icms=False)
cliente = session.get(Cliente, cot2.cliente_id)
fin_antes = cliente.finalidade
cliente.finalidade = "REVENDA"; session.add(cliente); session.commit()
add(cot2, ktc)
f1 = foto(cot2)
cliente.finalidade = "USO_CONSUMO"; session.add(cliente); session.commit()
loc = cabecalho(cot2)
f2 = foto(cot2)
print("   item antes:", {k: str(v) for k, v in list(f1.values())[0].items()})
print("   item depois:", {k: str(v) for k, v in list(f2.values())[0].items()})
conferir("cliente REVENDA→USO_CONSUMO, cotação herda", cot2, {ktc.id: ktc})
cliente.finalidade = fin_antes; session.add(cliente); session.commit()

# ---------------------------------------------------------------------------
print("\n== C. item NEGOCIADO (modo preço) e depois mudança de cenário ==")
cot3 = nova_cotacao()
for p in produtos.values():
    add(cot3, p)
its = itens(cot3)
alvo = [i for i in its if not i.preco_travado][0]
novo_preco = float(dinheiro(D(alvo.preco_negociado) * Decimal("0.97")))
r = wfr  # noqa
av = com.aplicar_negociacao(session, session.get(Cotacao, cot3.id), {alvo.id: novo_preco}, ator=ADMIN)
session.commit()
print(f"   negociado item {alvo.nome_produto}: {alvo.preco_negociado} → {novo_preco}; requer aprovação: {av.requer_aprovacao}")
antes = foto(cot3)
cabecalho(cot3, estado_destino="RJ", contribuinte_icms="nao")
depois = foto(cot3)
print("   negociado antes:", {k: str(v) for k, v in antes[alvo.id].items()})
print("   negociado depois:", {k: str(v) for k, v in depois[alvo.id].items()})
conferir("negociado + SP→RJ não contribuinte", cot3, produtos)
session.expire_all()
c3 = session.get(Cotacao, cot3.id)
av2 = com.avaliar_negociacao(session, c3)
print(f"   após cenário: desconto {av2.desconto_pct}, requer aprovação {av2.requer_aprovacao}, exceções: {[ (a.item.nome_produto, [e.motivo for e in a.excecoes]) for a in av2.itens if a.excecoes]}")
if depois[alvo.id]["preco"] == antes[alvo.id]["preco"] and depois[alvo.id]["recomendado"] == antes[alvo.id]["recomendado"]:
    achado("NEGOCIADO_NAO_REAVALIADO", "P0", "item negociado manteve preço E recomendado após mudança de cenário", cotacao=cot3.id)

# ---------------------------------------------------------------------------
print("\n== D. produto personalizado (calculadora → cotação) e mudança de cenário ==")
from app.routers.calculadora import salvar as calc_salvar  # noqa: E402
cot4 = nova_cotacao()
try:
    r = chamar(calc_salvar, RequestFalsa(ADMIN, accept="text/html"), cotacao_id=cot4.id, session=session,
               familia="Flat Sheet", largura_cm=190, comprimento_cm=250, thread_count=300,
               cotton_pct=100, quantidade=10, weave="Sateen")
    session.commit()
    its4 = itens(cot4)
    print("   itens após calculadora:", [(i.nome_produto, i.preco_negociado, i.status_custo_item, i.cost_method) for i in its4])
    if its4:
        pp = {i.produto_id: session.get(Produto, i.produto_id) for i in its4}
        conferir("personalizado inicial", cot4, pp)
        cabecalho(cot4, estado_destino="BA", contribuinte_icms="nao")
        conferir("personalizado + SP→BA não contribuinte", cot4, pp)
        cabecalho(cot4, condicao_pagamento="30/60/90")
        conferir("personalizado + 30/60/90", cot4, pp)
except Exception as e:  # noqa: BLE001
    import traceback; traceback.print_exc()
    achado("PERSONALIZADO_ERRO", "P1", f"calculadora.salvar falhou: {type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
print("\n== RESUMO ==")
print(f"achados: {len(achados)}")
os.makedirs(AUDIT, exist_ok=True)
with open(os.path.join(AUDIT, "repro_cenario_achados.json"), "w", encoding="utf-8") as f:
    json.dump(achados, f, ensure_ascii=False, indent=2, default=str)
sys.exit(1 if achados else 0)

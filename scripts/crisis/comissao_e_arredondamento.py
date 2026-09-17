#!/usr/bin/env python3
"""§15 comissão + §16 arredondamento — oracle independente contra o serviço canônico.

Cotação com KTC + KTC + Decor + Daune; descontos 0..60% aplicados a um, a vários e a todos os
itens variáveis; quantidades 1..1000 com preços de fração crítica. Tudo comparado centavo a
centavo com `comercial_service.aplicar_negociacao` e com o que fica gravado nos itens.
"""
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal, getcontext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.crisis.ambiente import AUDIT, RequestFalsa, chamar, preparar, usuario_admin  # noqa: E402

session = preparar("comissao")
getcontext().prec = 34
from sqlmodel import select  # noqa: E402

from app import comercial_service as com  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app import workflow_service as ws  # noqa: E402
from app.dinheiro import D, dinheiro  # noqa: E402
from app.models import Cliente, Cotacao, CotacaoItem, Fornecedor, Produto  # noqa: E402
from app.routers.cotacoes import adicionar_item  # noqa: E402

ADMIN = usuario_admin(); REQ = RequestFalsa(ADMIN)
C = lambda x: Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
achados = []
BASE_COM, MIN_COM = Decimal("0.10"), Decimal("0.05")


def achado(codigo, gravidade, detalhe):
    achados.append({"codigo": codigo, "gravidade": gravidade, "detalhe": detalhe}); print(f"  [{gravidade}] {codigo}: {detalhe}")


def confirmado(codigo, familia=None, n=1):
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == codigo)).first()
    out = []
    for p in session.exec(select(Produto).where(Produto.fornecedor_id == forn.id, Produto.ativo == True)).all():  # noqa: E712
        if familia and p.familia != familia:
            continue
        custo, mem = ps.custo_para_precificar(session, p)
        if custo and ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO":
            out.append(p)
            if len(out) == n:
                break
    return out


def nova(**kw):
    cliente = session.exec(select(Cliente)).first()
    d = dict(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="SP", contribuinte_icms=False,
             finalidade="USO_CONSUMO", condicao_pagamento="30", status="rascunho", freight_type="FOB", numero="COM-x")
    d.update(kw); c = Cotacao(**d); session.add(c); session.commit(); session.refresh(c); return c


def add(cot, p, qtd):
    chamar(adicionar_item, REQ, cotacao_id=cot.id, produto_id=p.id, quantidade=float(qtd), modo="margem", valor=None, session=session)
    session.commit()


def itens(cot):
    session.expire_all()
    return session.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cot.id).order_by(CotacaoItem.id)).all()


def taxas(cot, it):
    """icms, pis/cofins efetivo, encargo — lidos do runtime (já provados pela matriz)."""
    p = session.get(Produto, it.produto_id)
    regras, ctx = ps.regras_da_cotacao(session, cot, p)
    return regras.icms_pct, regras.pis_cofins_pct, regras.encargo_financeiro_pct


def oracle_cotacao(cot, precos):
    """Comissão e margens da cotação inteira, do zero. `precos` = {item_id: unitário}."""
    its = itens(cot)
    linhas = {}
    for it in its:
        icms, pc, enc = taxas(cot, it)
        q = D(it.quantidade); custo = D(it.custo_unitario); piso = D(it.piso_margem_pct); alvo = D(it.margem_padrao_pct)
        cform = D(it.comissao_formacao_pct)
        rec = (custo / (1 - icms - pc - enc - cform - alvo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        preco = C(precos.get(it.id, it.preco_negociado))
        if it.preco_travado:
            preco = rec
        receita = C(preco * q)
        impostos = C(receita * (icms + pc + enc))
        custo_total = C(custo * q)
        lucro0 = receita - impostos - custo_total
        cmax = (lucro0 / receita - piso) if receita > 0 else None
        linhas[it.id] = dict(it=it, q=q, custo=custo, piso=piso, alvo=alvo, cform=cform, rec=rec, preco=preco,
                             receita=receita, rec_receita=C(rec * q), impostos=impostos, custo_total=custo_total,
                             cmax=cmax, variavel=(not it.preco_travado and custo > 0 and rec > 0), icms=icms, pc=pc, enc=enc)
    var = [l for l in linhas.values() if l["variavel"]]
    R = sum(l["rec_receita"] for l in var); N = sum(l["receita"] for l in var)
    desconto = max(Decimal(0), 1 - N / R) if R > 0 else Decimal(0)
    prop = max(MIN_COM, min(BASE_COM, BASE_COM * (1 - desconto)))
    maximas = [l["cmax"] for l in var if l["cmax"] is not None]
    variavel = max(MIN_COM, min([prop] + maximas))
    for l in linhas.values():
        l["com_pct"] = l["cform"] if l["it"].preco_travado else (variavel if l["variavel"] else None)
        if l["com_pct"] is None:
            continue
        l["comissao"] = C(l["receita"] * l["com_pct"])
        l["lucro"] = l["receita"] - l["impostos"] - l["comissao"] - l["custo_total"]
        l["margem"] = l["lucro"] / l["receita"] if l["receita"] else None
        # tolerância canônica: 3 componentes quantizados × meio centavo por unidade
        l["viola_piso"] = (not l["it"].preco_travado) and l["margem"] is not None and (l["piso"] - l["margem"]) * l["preco"] > Decimal("0.015") * 1  # por unidade
    return dict(linhas=linhas, desconto=desconto, prop=prop, variavel=variavel, subtotal=sum(l["receita"] for l in linhas.values()))


def comparar(rotulo, cot, precos):
    session.expire_all(); c = session.get(Cotacao, cot.id)
    try:
        av = com.aplicar_negociacao(session, c, precos, ator=ADMIN); session.commit()
    except com.PrecoTravado as e:
        return "travado"
    orc = oracle_cotacao(c, precos)
    ok = True
    # comissão variável da cotação
    if av.comissao is not None and av.comissao.variavel_pct != orc["variavel"]:
        ok = False; achado("COMISSAO_VARIAVEL_DIVERGE", "P0", f"{rotulo}: motor {av.comissao.variavel_pct} × oracle {orc['variavel']} (desc motor {av.comissao.desconto_ratio} × {orc['desconto']})")
    # `av.desconto_pct` é o desconto da proposta inteira (inclui Daune travado) — é o que a
    # tela mostra; o desconto que forma a comissão é o do bloco variável (`comissao.desconto_ratio`).
    if av.comissao is not None and abs(D(av.comissao.desconto_ratio) - orc["desconto"]) > Decimal("1e-12"):
        ok = False; achado("DESCONTO_DIVERGE", "P1", f"{rotulo}: {av.comissao.desconto_ratio} × {orc['desconto']}")
    if av.subtotal_negociado != orc["subtotal"]:
        ok = False; achado("SUBTOTAL_DIVERGE", "P0", f"{rotulo}: {av.subtotal_negociado} × {orc['subtotal']}")
    for a in av.itens:
        l = orc["linhas"][a.item.id]; it = session.get(CotacaoItem, a.item.id)
        res = a.resultado
        if res is None or l.get("com_pct") is None:
            continue
        for campo, motor, esperado in (("preco", D(it.preco_negociado), l["preco"]), ("receita", D(it.faturamento), l["receita"]),
                                       ("impostos", D(it.impostos), l["impostos"]), ("comissao", D(it.comissao_valor), l["comissao"]),
                                       ("lucro", D(it.lucro), l["lucro"]), ("custo_total", D(it.custo_total), l["custo_total"])):
            if motor != esperado:
                ok = False; achado(f"ITEM_{campo.upper()}_DIVERGE", "P0", f"{rotulo} item {it.nome_produto}: gravado {motor} × oracle {esperado}")
        if abs(D(it.margem_liquida) - l["margem"]) > Decimal("1e-9"):
            ok = False; achado("ITEM_MARGEM_DIVERGE", "P0", f"{rotulo} item {it.nome_produto}: {it.margem_liquida} × {l['margem']}")
        if abs(D(it.comissao_pct) - l["com_pct"]) > Decimal("1e-12"):      # coluna REAL: 17 dígitos
            ok = False; achado("ITEM_COMISSAO_PCT_DIVERGE", "P0", f"{rotulo} item {it.nome_produto}: {it.comissao_pct} × {l['com_pct']}")
        if bool(a.viola_piso) != bool(l["viola_piso"]):
            ok = False; achado("VIOLA_PISO_DIVERGE", "P1", f"{rotulo} item {it.nome_produto}: motor {a.viola_piso} × oracle {l['viola_piso']} (margem {l['margem']} piso {l['piso']})")
        if a.linha.preco_recomendado is not None and D(a.linha.preco_recomendado) != l["rec"]:
            ok = False; achado("RECOMENDADO_DIVERGE", "P0", f"{rotulo} item {it.nome_produto}: {a.linha.preco_recomendado} × {l['rec']}")
    # a soma gravada = Σ linhas exatas
    tot = sum(D(i.faturamento) for i in itens(c))
    if tot != av.subtotal_negociado:
        ok = False; achado("SOMA_LINHAS_DIVERGE", "P0", f"{rotulo}: Σ itens {tot} × subtotal {av.subtotal_negociado}")
    print(("  ok  " if ok else "  !!  ") + f"{rotulo}: desconto {orc['desconto']:.4%} · comissão var {orc['variavel']:.4%} · requer aprovação {av.requer_aprovacao} · subtotal {av.subtotal_negociado}")
    return av


print("== produtos ==")
ktc = confirmado("KTC", "Flat Sheet", 2); decor = confirmado("DECOR_TRICOT"); daune = confirmado("DAUNE")
if not decor:
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == "DECOR_TRICOT")).first()
    decor = [p for p in session.exec(select(Produto).where(Produto.fornecedor_id == forn.id)).all() if ps.custo_para_precificar(session, p)[0]][:1]
prods = ktc + decor + daune
print("  ", [(p.id, p.familia, p.sku_key[:30]) for p in prods])

print("\n== §15 matriz de desconto: um item / vários / todos os variáveis (Daune junto) ==")
DESCONTOS = [0, 1, 5, 10, 15, 20, 30, 40, 50, 60]
for cenario in ("um", "varios", "todos"):
    cot = nova(numero=f"COM-{cenario}")
    for p, q in zip(prods, (10, 7, 3, 2)):
        add(cot, p, q)
    its = itens(cot)
    variaveis = [i for i in its if not i.preco_travado]
    alvo = [variaveis[0]] if cenario == "um" else (variaveis[:2] if cenario == "varios" else variaveis)
    for d in DESCONTOS:
        precos = {i.id: float(C(D(i.preco_recomendado) * (1 - Decimal(d) / 100))) for i in alvo}
        comparar(f"{cenario} · desconto {d}%", cot, precos)
    # acima do recomendado
    precos = {i.id: float(C(D(i.preco_recomendado) * Decimal("1.15"))) for i in alvo}
    comparar(f"{cenario} · +15% acima", cot, precos)
    # Daune com outro preço → recusa
    travados = [i for i in its if i.preco_travado]
    if travados:
        r = comparar(f"{cenario} · Daune −1%", cot, {travados[0].id: float(C(D(travados[0].preco_recomendado) * Decimal("0.99")))})
        print("   Daune com outro preço:", "recusado (409)" if r == "travado" else "ACEITO — P0")
        if r != "travado":
            achado("DAUNE_PRECO_ALTERADO_ACEITO", "P0", "preço travado aceitou outro unitário")

print("\n== §16 arredondamento: quantidades × preços de fração crítica ==")
cot = nova(numero="ARR-1")
p = ktc[0]
for q in (1, 2, 3, 7, 10, 99, 100, 500, 1000):
    add(cot, p, q)
its = itens(cot)
precos = {}
for it, unit in zip(its, ("10.005", "0.015", "99.995", "33.335", "1.115", "2.675", "1.005", "0.125", "7.777")):
    precos[it.id] = float(unit)
session.expire_all(); c = session.get(Cotacao, cot.id)
av = com.aplicar_negociacao(session, c, precos, ator=ADMIN); session.commit()
soma = Decimal(0); ok = True
for it in itens(cot):
    unit = C(Decimal(str(precos[it.id])))            # "10.005" → 10.01 (ROUND_HALF_UP)
    linha = C(unit * D(it.quantidade))
    soma += linha
    if D(it.preco_negociado) != unit:
        ok = False; achado("UNITARIO_ARREDONDADO_ERRADO", "P0", f"qtd {it.quantidade}: digitado {precos[it.id]} gravado {it.preco_negociado} esperado {unit}")
    if D(it.faturamento) != linha:
        ok = False; achado("LINHA_NAO_E_UNIT_X_QTD", "P0", f"qtd {it.quantidade}: {it.faturamento} ≠ {unit}×{it.quantidade}={linha}")
    print(f"   qtd {int(it.quantidade):5d} × {unit} = {linha}  (gravado {it.faturamento})")
if av.subtotal_negociado != soma:
    ok = False; achado("SUBTOTAL_NAO_E_SOMA", "P0", f"{av.subtotal_negociado} ≠ {soma}")
c.freight_type = "CIF"; c.freight_valor = 123.45; session.add(c); session.commit()
av2 = com.avaliar_negociacao(session, session.get(Cotacao, cot.id))
if av2.total_proposta != soma + Decimal("123.45"):
    ok = False; achado("TOTAL_COM_FRETE", "P0", f"{av2.total_proposta} ≠ {soma}+123.45")
print(f"   subtotal {av.subtotal_negociado} = Σ linhas {soma}; total com frete {av2.total_proposta} → {'ok' if ok else 'FALHA'}")

print("\n== RESUMO ==", len(achados), "achados")
json.dump(achados, open(os.path.join(AUDIT, "comissao_arredondamento_achados.json"), "w"), ensure_ascii=False, indent=2, default=str)
sys.exit(1 if achados else 0)

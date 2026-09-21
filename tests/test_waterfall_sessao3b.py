"""Regressão do waterfall — §35 da Sessão 3B.

O que esta suíte prova, para cada combinação representativa de fornecedor e operação:

    receita
      − CNET
      − tributos suportados pela Anara
      − encargo financeiro
      − comissão
      − frete (CF rateado + RV recomposto)
      − outros custos atribuíveis
    = lucro_da_operação

e, fechando o ciclo:

    lucro_da_operação ÷ receita = margem_real

**Não é o mesmo teste da reconciliação da linha.** Aquele confere a identidade interna do
resultado; este recompõe o waterfall **de fora**, a partir das alíquotas que o motor fiscal
resolveu e da tabela de comissão vigente, e confere que o número bate. É a diferença entre
"o objeto é consistente consigo mesmo" e "o objeto é consistente com as regras do negócio".

Nenhuma decisão econômica anterior é reaberta: as alíquotas vêm do motor fiscal da Sessão 1,
o custo Daune do modelo da Sessão 2 e o CF/RV da arquitetura da Sessão 3A. O que muda aqui é
só a precisão.

Cobertura exigida: KTC SP · KTC interestadual · Daune SP · Daune interestadual · cenário com
CF logístico · cenário com RV logístico · preço comercial arredondado · preço negociado.
"""
import pytest
from sqlmodel import select

from app import pricing_service as ps
from app.dinheiro import D, D0, ZERO, dinheiro, divide
from app.models import CostMethod, Cotacao, Fornecedor, Produto
from app.pricing_engine import TaxRuleSet, calcular_por_margem, calcular_por_preco
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def produto_de(session, fornecedor, sku, custo=100.0, **kw):
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia="Flat Sheet",
                cost_method=kw.pop("cost_method", CostMethod.national_supplier.value), **kw)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_para(destino="Minas Gerais", contribuinte=True, finalidade="REVENDA",
                 condicao="30"):
    return Cotacao(cliente_id=0, estado_origem="Santa Catarina", uf_origem_fiscal="SP",
                   estado_destino=destino, contribuinte_icms=contribuinte,
                   finalidade=finalidade, condicao_pagamento=condicao)


# ---------------------------------------------------------------------------
def recompor(resultado, regras: TaxRuleSet, custo_unitario, quantidade):
    """Recompõe o waterfall a partir das REGRAS, não dos campos do resultado.

    Devolve `(lucro_recomposto, componentes)`. Cada componente é calculado aqui do zero,
    com a mesma política de quantização — se o motor tiver mudado a ordem das operações ou
    esquecido de recompor algo sobre o preço final, os dois números divergem.
    """
    receita = dinheiro(D0(resultado.preco_negociado) * D0(quantidade))
    cnet = dinheiro(D0(custo_unitario) * D0(quantidade))
    icms_e_pis = dinheiro(receita * (regras.icms_pct + regras.pis_cofins_pct))
    financeiro = dinheiro(receita * regras.encargo_financeiro_pct)
    comissao_pct = regras.comissao_para_markup(resultado.markup_implicito)
    # Base da comissão conforme a política do TaxRuleSet: bruta (políticas anteriores,
    # `comissao_base_icms_pct = 0`) ou líquida do ICMS suportado pela Anara (21/09/2026).
    # Recomposta aqui do zero, a partir das regras — não copiada do resultado.
    base_comissionavel = receita * (D("1") - regras.comissao_base_icms_pct)
    comissao = dinheiro(base_comissionavel * comissao_pct)
    frete_cf = dinheiro(regras.frete_cf_unitario * D0(quantidade))
    frete_rv = dinheiro(receita * regras.frete_rv_pct)

    # O motor soma ICMS e PIS/COFINS num componente só (`impostos`) e o encargo financeiro
    # junto, porque os três dividem a mesma base. Aqui eles são separados de propósito: se um
    # deles for esquecido, a soma não fecha.
    tributos_mais_financeiro = dinheiro(receita * regras.taxa_fixa())
    lucro = receita - tributos_mais_financeiro - comissao - cnet - frete_cf - frete_rv

    return lucro, {
        "receita": receita, "cnet": cnet, "icms_pis_cofins": icms_e_pis,
        "financeiro": financeiro, "comissao": comissao, "comissao_pct": comissao_pct,
        "frete_cf": frete_cf, "frete_rv": frete_rv,
        "tributos_mais_financeiro": tributos_mais_financeiro,
    }


def conferir_waterfall(resultado, regras, custo_unitario, quantidade, rotulo):
    """As três provas do §35, aplicadas a um resultado qualquer."""
    lucro, c = recompor(resultado, regras, custo_unitario, quantidade)

    # 1. o waterfall recomposto de fora bate com o lucro do motor
    assert lucro == resultado.lucro, (
        f"{rotulo}: waterfall recomposto {lucro} ≠ lucro do motor {resultado.lucro}")

    # 2. a identidade da linha fecha ao centavo, sem resíduo
    assert resultado.reconcilia(), f"{rotulo}: a linha não reconcilia"
    assert (c["receita"] == c["cnet"] + c["tributos_mais_financeiro"] + c["comissao"]
            + c["frete_cf"] + c["frete_rv"] + lucro), f"{rotulo}: a soma não fecha"

    # 3. a margem exibida é a do dinheiro que entra
    assert resultado.margem_liquida == divide(lucro, c["receita"]), \
        f"{rotulo}: margem exibida ≠ lucro ÷ receita"

    # e o ICMS + PIS/COFINS + financeiro separados somam o componente agregado, a menos do
    # centavo de cada quantização
    assert c["icms_pis_cofins"] + c["financeiro"] == \
        aprox(c["tributos_mais_financeiro"], abs="0.02"), f"{rotulo}: tributos não batem"
    return c


# ---------------------------------------------------------------------------
# Os oito cenários exigidos
# ---------------------------------------------------------------------------
CENARIOS = [
    # (rótulo, código do fornecedor, destino, contribuinte, finalidade)
    ("KTC SP", "KTC", "São Paulo", True, "REVENDA"),
    ("KTC interestadual", "KTC", "Minas Gerais", True, "REVENDA"),
    ("Daune SP", "DAUNE", "São Paulo", True, "REVENDA"),
    ("Daune interestadual", "DAUNE", "Minas Gerais", True, "REVENDA"),
    ("Decor interestadual", "DECOR_TRICOT", "Bahia", True, "REVENDA"),
    # Não contribuinte: o remetente recolhe DIFAL e FCP, e os dois entram no waterfall.
    # RJ é o destino com `icms_interno_base` cadastrado, então é onde esse caminho resolve —
    # 4% de interestadual + 16% de DIFAL + 2% de FECP = 22% sobre a receita.
    ("KTC não contribuinte RJ", "KTC", "Rio de Janeiro", False, "USO_CONSUMO"),
    ("Daune consumidor final SP", "DAUNE", "São Paulo", False, "USO_CONSUMO"),
]


@pytest.mark.parametrize("rotulo,codigo,destino,contribuinte,finalidade", CENARIOS)
def test_waterfall_por_fornecedor_e_operacao(session, fornecedores, rotulo, codigo, destino,
                                             contribuinte, finalidade):
    """O waterfall fecha para cada combinação de fornecedor × operação."""
    produto = produto_de(session, fornecedores[codigo], f"WF-{codigo}-{destino}-{contribuinte}",
                         custo=377.11)
    cot = cotacao_para(destino=destino, contribuinte=contribuinte, finalidade=finalidade)
    regras, contexto = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None, f"{rotulo}: cenário não resolveu — {contexto.get('motivo_bloqueio')}"

    margem = ps.margem_padrao(session, produto)
    r = calcular_por_margem(produto.custo_unitario, 7, margem.margem_pct, regras)

    c = conferir_waterfall(r, regras, produto.custo_unitario, 7, rotulo)

    # a margem-alvo é entregue a menos do centavo comercial
    assert r.margem_alvo == margem.margem_pct
    assert r.margem_liquida == aprox(margem.margem_pct, abs=MARGEM_DO_CENTAVO)
    assert abs(r.ajuste_arredondamento) <= MEIO_CENTAVO
    # e o ICMS que entrou no waterfall é o que o motor fiscal resolveu, sem atalho
    assert regras.icms_pct == D(str(contexto["icms_pct"]))
    assert c["receita"] == dinheiro(r.preco_negociado * D(7))


@pytest.mark.parametrize("rotulo,codigo,destino,contribuinte,finalidade", CENARIOS)
def test_waterfall_com_preco_negociado(session, fornecedores, rotulo, codigo, destino,
                                       contribuinte, finalidade):
    """Preço negociado é a receita real: todo o waterfall é recomposto sobre ele."""
    produto = produto_de(session, fornecedores[codigo], f"WFN-{codigo}-{destino}-{contribuinte}",
                         custo=377.11)
    cot = cotacao_para(destino=destino, contribuinte=contribuinte, finalidade=finalidade)
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None

    recomendado = calcular_por_margem(produto.custo_unitario, 3,
                                      ps.margem_padrao(session, produto).margem_pct, regras)
    negociado = calcular_por_preco(produto.custo_unitario, 3, "599.90", regras,
                                   preco_base=recomendado.preco_negociado)

    assert negociado.preco_negociado == D("599.90")
    assert negociado.faturamento == D("1799.70")          # 599,90 × 3, ao centavo
    conferir_waterfall(negociado, regras, produto.custo_unitario, 3, f"{rotulo} negociado")
    # a margem do negociado é a do preço negociado, não a do recomendado
    assert negociado.margem_liquida != recomendado.margem_liquida


def test_waterfall_com_cf_logistico(session, fornecedores):
    """CF no numerador: o frete fixo do embarque entra na formação e sai no waterfall."""
    produto = produto_de(session, fornecedores["KTC"], "WF-CF", custo=1000.0)
    cot = cotacao_para(destino="Minas Gerais")
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None
    regras.frete_cf_unitario = D("325.80")

    r = calcular_por_margem(produto.custo_unitario, 4, D("0.14"), regras)
    c = conferir_waterfall(r, regras, produto.custo_unitario, 4, "CF logístico")

    assert c["frete_cf"] == D("1303.20")                  # 325,80 × 4
    assert r.frete_cf == c["frete_cf"]
    assert r.frete_rv == ZERO
    # sem o CF o preço seria menor: o frete realmente entrou na conta
    sem_cf = calcular_por_margem(produto.custo_unitario, 4, D("0.14"),
                                 TaxRuleSet(icms_pct=regras.icms_pct,
                                            pis_cofins_pct=regras.pis_cofins_pct,
                                            encargo_financeiro_pct=regras.encargo_financeiro_pct,
                                            comissao_tabela=regras.comissao_tabela))
    assert r.preco_negociado > sem_cf.preco_negociado


def test_waterfall_com_rv_logistico(session, fornecedores):
    """RV no denominador: vira reais só depois do preço, e a margem-alvo ainda fecha."""
    produto = produto_de(session, fornecedores["DAUNE"], "WF-RV", custo=1000.0)
    cot = cotacao_para(destino="Minas Gerais")
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None
    regras.frete_rv_pct = D("0.003")                       # ADV + GRIS hipotéticos

    r = calcular_por_margem(produto.custo_unitario, 5, D("0.14"), regras)
    c = conferir_waterfall(r, regras, produto.custo_unitario, 5, "RV logístico")

    # o RV é percentual da RECEITA cobrada — não de um preço preliminar
    assert r.frete_rv == dinheiro(r.faturamento * D("0.003"))
    assert c["frete_rv"] == r.frete_rv
    assert r.margem_liquida == aprox("0.14", abs=MARGEM_DO_CENTAVO)


def test_waterfall_com_cf_e_rv_juntos(session, fornecedores):
    """Os dois ao mesmo tempo — numerador e denominador na mesma conta."""
    produto = produto_de(session, fornecedores["KTC"], "WF-CFRV", custo=1000.0)
    cot = cotacao_para(destino="Bahia")
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None
    regras.frete_cf_unitario = D("87.45")
    regras.frete_rv_pct = D("0.003")

    r = calcular_por_margem(produto.custo_unitario, 9, D("0.14"), regras)
    conferir_waterfall(r, regras, produto.custo_unitario, 9, "CF + RV")
    assert r.margem_liquida == aprox("0.14", abs=MARGEM_DO_CENTAVO)


def test_waterfall_do_preco_comercial_arredondado(session, fornecedores):
    """O preço arredondado é o que manda: recalcular a partir dele não move nada."""
    produto = produto_de(session, fornecedores["DAUNE"], "WF-ARRED", custo=377.11)
    cot = cotacao_para(destino="São Paulo")
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    assert regras is not None

    r = calcular_por_margem(produto.custo_unitario, 3, D("0.14"), regras)
    volta = calcular_por_preco(produto.custo_unitario, 3, r.preco_negociado, regras)

    assert volta.preco_negociado == r.preco_negociado
    assert volta.faturamento == r.faturamento
    assert volta.lucro == r.lucro
    assert volta.margem_liquida == r.margem_liquida
    conferir_waterfall(volta, regras, produto.custo_unitario, 3, "preço comercial")


def test_waterfall_da_cotacao_mista_soma_exatamente(session, fornecedores):
    """Três fornecedores, três alíquotas, um total: a soma das linhas é o total da cotação."""
    from app.dinheiro import soma

    cot = cotacao_para(destino="Minas Gerais")
    linhas = []
    for codigo, custo, qtd in (("KTC", 377.11, 7), ("DAUNE", 129.90, 13),
                               ("DECOR_TRICOT", 58.25, 101)):
        produto = produto_de(session, fornecedores[codigo], f"WF-MIX-{codigo}", custo=custo)
        regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
        assert regras is not None
        margem = ps.margem_padrao(session, produto)
        r = calcular_por_margem(custo, qtd, margem.margem_pct, regras)
        conferir_waterfall(r, regras, custo, qtd, f"mista/{codigo}")
        linhas.append(r)

    faturamento = soma(r.faturamento for r in linhas)
    lucro = soma(r.lucro for r in linhas)
    custo_total = soma(r.custo_total for r in linhas)
    impostos = soma(r.impostos for r in linhas)
    comissao = soma(r.comissao for r in linhas)

    # o total da cotação é a soma exata das linhas — sem sobra de centavo
    assert faturamento == custo_total + impostos + comissao + lucro
    assert divide(lucro, faturamento) == aprox("0.14", abs="0.02")   # média das três margens

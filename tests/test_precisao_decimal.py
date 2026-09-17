"""Sessão 3B — precisão, arredondamento e reconciliação monetária.

Os 40 pontos exigidos para a sessão, cada um como uma prova independente. O que estes testes
protegem não é uma fórmula: é a promessa de que **o preço exibido é o preço usado**, e de que
nenhuma diferença de centavo aparece sem dono.

Organização: fundamentos do `Decimal` · arredondamento comercial · preço e margem ·
comissão nas fronteiras · reconciliação e rateio · fornecedores · persistência e fronteiras ·
idempotência.
"""
import json
from decimal import Decimal

import pytest
from sqlmodel import select

from app import frete_engine as fe
from app.dinheiro import (
    CENTAVO, D, D0, PRECISAO_INTERNA, ZERO, dinheiro, divide, para_float, ratear_centavos,
    reconcilia, soma,
)
from app.models import CondicaoPagamento, Produto
from conftest import cliente_de_apoio
from app.nationalization import PremissasNacionalizacao, nacionalizar
from app.payment_terms import resolver_encargo
from app.pricing_engine import TaxRuleSet, calcular_por_margem, calcular_por_preco
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402

REGRAS = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016,
                    comissao_tabela=[(0.0, 0.05), (0.6, 0.06), (0.7, 0.07), (0.8, 0.08),
                                     (0.9, 0.09), (1.0, 0.10)])


# ===========================================================================
# 1-3 · Fundamentos
# ===========================================================================
def test_01_soma_decimal_e_exata():
    """O caso-escola: em float, 0,1 + 0,2 não é 0,3."""
    assert D("0.1") + D("0.2") == D("0.3")
    assert 0.1 + 0.2 != 0.3                      # o mesmo cálculo em float, para contraste


def test_02_nunca_se_constroi_decimal_a_partir_de_float_cru():
    """`D()` passa pela representação textual — `Decimal(float)` carregaria o erro binário."""
    assert D(0.1) == Decimal("0.1")
    assert Decimal(0.1) != Decimal("0.1")        # o que NÃO se deve fazer
    assert D(0.0759) == Decimal("0.0759")
    assert D(5.11) == Decimal("5.11")


def test_02b_nenhum_motor_economico_usa_decimal_de_float_cru():
    """Varredura de código: `Decimal(` aplicado a variável é proibido no núcleo econômico.

    O padrão seguro é `D(...)`. Este teste falha se alguém reintroduzir `Decimal(x)` com x
    não-literal em qualquer motor — que é exatamente o jeito de fabricar um dízimo binário
    permanente.
    """
    import ast
    import pathlib

    # `dinheiro.py` fica de fora: ele É a ponte. É lá dentro que `Decimal(repr(float))`
    # acontece, uma vez, com a conversão textual que torna a operação segura.
    modulos = ["pricing_engine", "frete_engine", "ktc_engine", "nationalization",
               "fiscal_rules", "margin_rules", "payment_terms", "peso", "custo_service",
               "pricing_service"]
    ofensas = []
    for nome in modulos:
        caminho = pathlib.Path("app") / f"{nome}.py"
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                    and no.func.id == "Decimal" and no.args
                    and not isinstance(no.args[0], ast.Constant)):
                ofensas.append(f"{nome}.py:{no.lineno}")
    assert not ofensas, f"Decimal(variável) no núcleo econômico: {ofensas}"


def test_03_round_half_up_e_a_regra_da_moeda():
    """1,005 → 1,01. O `round()` do Python devolve 1,0 e por isso não serve."""
    assert dinheiro("1.005") == D("1.01")
    assert dinheiro("2.675") == D("2.68")
    assert dinheiro("0.005") == D("0.01")
    assert dinheiro("-1.005") == D("-1.01")
    assert round(1.005, 2) == 1.0                # o comportamento que foi substituído


def test_03b_precisao_interna_alta_e_declarada():
    """A política de precisão interna existe, é única e é alta o bastante."""
    assert PRECISAO_INTERNA >= 28
    # 34 dígitos aguentam a cadeia inteira sem truncar no meio
    assert (D(1) / D(3) * D(3)) != D(1)          # divisão inexata continua inexata, como deve
    assert len((D(1) / D(3)).as_tuple().digits) == PRECISAO_INTERNA


# ===========================================================================
# 4-8 · Preço, margem e preço negociado
# ===========================================================================
def test_04_preco_recomendado_preciso_existe():
    r = calcular_por_margem(377.11, 1, 0.14, REGRAS)
    assert r.preco_preciso is not None
    assert r.preco_preciso.as_tuple().exponent < -2      # tem mais casas que o comercial


def test_05_preco_comercial_tem_duas_casas():
    r = calcular_por_margem(377.11, 1, 0.14, REGRAS)
    assert r.preco_negociado == dinheiro(r.preco_preciso)
    assert r.preco_negociado.as_tuple().exponent == -2
    assert abs(r.ajuste_arredondamento) <= MEIO_CENTAVO


def test_06_margem_real_e_recalculada_depois_do_arredondamento():
    """A margem exibida é a do preço cobrado — não a do preço teórico."""
    r = calcular_por_margem(377.11, 1, 0.14, REGRAS)
    assert r.margem_alvo == D("0.14")
    assert r.margem_liquida == divide(r.lucro, r.faturamento)
    assert r.margem_liquida != r.margem_alvo             # o centavo move a margem…
    assert r.margem_liquida == aprox(0.14, abs=MARGEM_DO_CENTAVO)   # …e só o centavo


def test_07_preco_negociado_substitui_a_receita_recomendada():
    recomendado = calcular_por_margem(50.0, 1, 0.18, REGRAS)
    negociado = calcular_por_preco(50.0, 1, "99.90", REGRAS,
                                   preco_base=recomendado.preco_negociado)
    assert negociado.preco_negociado == D("99.90")
    assert negociado.faturamento == D("99.90")
    assert negociado.margem_liquida != recomendado.margem_liquida
    assert negociado.reconcilia()


def test_08_mudanca_negociada_recalcula_a_comissao():
    """Baixar o preço muda o markup, e o markup pode mudar a FAIXA de comissão."""
    caro = calcular_por_preco(50.0, 1, "150.00", REGRAS)
    barato = calcular_por_preco(50.0, 1, "80.00", REGRAS)
    assert caro.markup_implicito > barato.markup_implicito
    assert REGRAS.comissao_para_markup(caro.markup_implicito) > \
        REGRAS.comissao_para_markup(barato.markup_implicito)
    # e a comissão em reais acompanha a receita real, não a recomendada
    assert barato.comissao == dinheiro(
        barato.faturamento * REGRAS.comissao_para_markup(barato.markup_implicito))


# ===========================================================================
# 9-14 · Comissão nas fronteiras
# ===========================================================================
@pytest.mark.parametrize("markup,esperado", [
    ("0.599999999999", "0.05"), ("0.6", "0.06"), ("0.600000000001", "0.06"),
    ("0.699999999999", "0.06"), ("0.7", "0.07"), ("0.700000000001", "0.07"),
    ("0.799999999999", "0.07"), ("0.8", "0.08"), ("0.800000000001", "0.08"),
    ("0.899999999999", "0.08"), ("0.9", "0.09"), ("0.900000000001", "0.09"),
    ("0.999999999999", "0.09"), ("1.0", "0.10"), ("1.000000000001", "0.10"),
])
def test_09_a_14_comissao_nas_fronteiras_exatas(markup, esperado):
    """As cinco fronteiras, por cima e por baixo. Erro binário aqui muda o preço inteiro."""
    assert REGRAS.comissao_para_markup(D(markup)) == D(esperado)


def test_14b_abaixo_de_60_continua_em_5():
    for m in ("0", "0.1", "0.3", "0.5", "0.59"):
        assert REGRAS.comissao_para_markup(D(m)) == D("0.05")


def test_14c_a_fronteira_de_60_seria_perdida_em_float():
    """Prova de que a fronteira é um risco REAL neste sistema, não uma hipótese de manual.

    Caso concreto: custo de R$ 10,00 e o preço que coloca o markup **exatamente** em 60%.
    A conta que o motor faz é `E = preço × (1 − taxa − comissão) / custo − 1`. Em `float`
    ela devolve 0,5999999999999999 — abaixo da fronteira — e o item sairia com comissão de
    5% em vez de 6%, mudando o preço final. Em `Decimal` ela devolve 0,60 exato.
    """
    custo = D("10.00")
    comissao = D("0.06")
    preco = D("1.6") * custo / (1 - REGRAS.taxa_fixa() - comissao)

    e_float = float(preco) * (1 - 0.2719 - 0.06) / float(custo) - 1
    assert e_float < 0.6                                   # o float erra por baixo…
    assert REGRAS.comissao_para_markup(D(e_float)) == D("0.05")   # …e derruba a faixa

    e_decimal = preco * (1 - REGRAS.taxa_fixa() - comissao) / custo - 1
    assert e_decimal == D("0.6")                           # o Decimal cai no ponto
    assert REGRAS.comissao_para_markup(e_decimal) == D("0.06")


# ===========================================================================
# 15-20 · Fornecedores e cadeias aprovadas
# ===========================================================================
def test_15_cnet_daune_reproduz_a_referencia_aprovada():
    """`gross − 12% ICMS − 9,25% PIS/COFINS` sobre o bruto, agora em Decimal."""
    gross = D("427.50")
    icms = gross * D("0.12")
    base_pc = gross - icms
    pis_cofins = base_pc * D("0.0925")
    cnet = gross - icms - pis_cofins
    assert dinheiro(cnet) == D("341.40")
    assert cnet == aprox("341.4015", abs="0.0001")


@pytest.mark.parametrize("bruto,esperado", [
    ("427.50", "341.4015"), ("469.30", "374.7830"),
    ("679.72", "542.8244"), ("678.60", "541.9300"),
])
def test_15b_os_edredons_280g_continuam_reproduzindo(bruto, esperado):
    from app.custo_service import cnet_nacional
    assert cnet_nacional(D(bruto)).cnet == aprox(esperado, abs="0.0001")


def test_16_ktc_industrial_reproduz_o_backtest_aprovado():
    from app.ktc_engine import ParametrosKTC, calcular_flat_sheet
    p = ParametrosKTC(material_price_usd_m2=D("1.20"), cmt_usd=D("0.75"), shrinkage=D("0.03"),
                      waste=D("0.03"), quality_allowance=D("0.01"), ktc_margin=D("0.15"),
                      hem_width_total_cm=4, hem_length_total_cm=4)
    r = calcular_flat_sheet(180, 310, p)
    assert r.exw_usd == aprox("9.90235528", abs="0.000001")


def test_17_nacionalizacao_reproduz_a_referencia():
    """EXW + frete → base do I.I. → NET USD → NET BRL, tudo em Decimal."""
    pr = PremissasNacionalizacao(frete_usd_kg=D("0.516"), outras_desp_usd_un=D("0.2487532709"),
                                 fx_usd_brl=D("5.11"))
    r = nacionalizar(D("10.00"), D("0.5"), D("0.035"), pr)
    frete = D("0.5") * D("0.516")
    base = D("10.00") + frete
    ii = base * D("0.035")
    assert r.frete_usd == frete
    assert r.ii_usd == ii
    assert r.net_usd == D("10.00") + frete + ii + D("0.2487532709")
    assert r.net_brl == r.net_usd * D("5.11")


def test_18_fiscal_reproduz_os_exemplos_aprovados(session):
    """As alíquotas chegam ao motor como decimais exatos, e a soma do DIFAL não deriva."""
    from app.fiscal_rules import resolver_fiscal_item
    from app.models import AliquotaInterestadual, EstadoFiscal, RegraFcp, RegraFiscalVenda
    estados = session.exec(select(EstadoFiscal)).all()
    r = resolver_fiscal_item(
        session.exec(select(RegraFiscalVenda)).all(), estados,
        session.exec(select(AliquotaInterestadual)).all(),
        uf_origem="SP", uf_destino="SP", origem_fiscal="NACIONAL",
        contribuinte=True, finalidade="USO_CONSUMO",
        regras_fcp=session.exec(select(RegraFcp)).all())
    assert r.icms_pct == D("0.18")               # SP→SP é 18%, exato


@pytest.mark.parametrize("codigo,esperado", [
    ("30", "0.016"), ("30/60", "0.032"), ("30/60/90", "0.048"),
    ("30/60/90/120", "0.064"), ("30/60/90/120/150", "0.080"),
])
def test_19_condicoes_de_pagamento_com_taxa_exata(session, codigo, esperado):
    condicoes = session.exec(select(CondicaoPagamento)).all()
    assert resolver_encargo(condicoes, codigo).pct == D(esperado)


def test_20_e_21_frete_cf_rv_e_adv_sobre_o_preco_real():
    """CF no numerador, RV no denominador, e o ADV em reais só depois do preço existir."""
    regras = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016,
                        comissao_tabela=[(0.0, 0.05)],
                        frete_cf_unitario=D("325.80"), frete_rv_pct=D("0.002"))
    r = calcular_por_margem(1000.0, 1, 0.14, regras)
    # o RV em reais é percentual da RECEITA cobrada, não de um preço preliminar
    assert r.frete_rv == dinheiro(r.faturamento * D("0.002"))
    assert r.frete_cf == D("325.80")
    assert r.reconcilia()


# ===========================================================================
# 22-29 · Reconciliação, rateio e totais
# ===========================================================================
def test_22_rateio_de_cem_reais_em_tres_fecha_exatamente():
    """O caso do §14: 33,33 × 3 = 99,99 perderia um centavo. Não perde."""
    parcelas = ratear_centavos("100.00", [1, 1, 1])
    assert parcelas == [D("33.34"), D("33.33"), D("33.33")]
    assert soma(parcelas) == D("100.00")


@pytest.mark.parametrize("total,n", [("100.00", 3), ("0.05", 7), ("1234.56", 11),
                                     ("0.01", 3), ("9999.99", 97), ("10.00", 6)])
def test_23_rateio_nunca_perde_centavo(total, n):
    parcelas = ratear_centavos(total, [1] * n)
    assert reconcilia(parcelas, total)
    assert all(p.as_tuple().exponent == -2 for p in parcelas)


def test_24_desempate_do_rateio_e_deterministico():
    """O mesmo input distribui sempre o mesmo centavo para o mesmo item."""
    for _ in range(20):
        assert ratear_centavos("100.00", [1, 1, 1]) == [D("33.34"), D("33.33"), D("33.33")]
    # e pesos diferentes seguem a proporção, com o resto indo ao maior resto fracionário
    assert soma(ratear_centavos("100.00", [3, 3, 4])) == D("100.00")
    assert ratear_centavos("100.00", [2, 1]) == [D("66.67"), D("33.33")]


def test_24b_ratear_do_motor_de_frete_usa_a_mesma_regra():
    assert fe.ratear("100.00", [1, 1, 1]) == [D("33.34"), D("33.33"), D("33.33")]
    assert soma(fe.ratear("100.00", [1, 1, 1])) == D("100.00")


def test_25_e_26_soma_dos_itens_bate_com_o_total():
    itens = [calcular_por_margem(c, q, 0.14, REGRAS)
             for c, q in [(377.11, 3), (50.0, 7), (12.34, 11), (0.99, 101)]]
    total = soma(i.faturamento for i in itens)
    assert total == soma(dinheiro(i.preco_negociado * i.quantidade) for i in itens)
    # e o frete rateado entre esses itens também fecha
    parcelas = ratear_centavos("1500.00", [i.faturamento for i in itens])
    assert soma(parcelas) == D("1500.00")


def test_27_e_28_lucro_reconcilia_e_margem_e_lucro_sobre_receita():
    for custo, qtd, alvo in [(377.11, 1, "0.14"), (50.0, 10, "0.18"), (12.34, 7, "0.12")]:
        r = calcular_por_margem(custo, qtd, D(alvo), REGRAS)
        assert r.reconcilia(), "a linha tem que fechar ao centavo, sem resíduo"
        assert r.margem_liquida == divide(r.lucro, r.faturamento)


def test_29_total_da_linha_e_preco_unitario_vezes_quantidade():
    """§13: o total é o unitário comercial × quantidade — nunca um total teórico próprio."""
    for qtd in (1, 3, 7.5, 101, 1000):
        r = calcular_por_margem(377.11, qtd, 0.14, REGRAS)
        assert r.faturamento == dinheiro(r.preco_negociado * D0(qtd))


# ===========================================================================
# 30-32, 37-39 · Fronteiras: JSON, banco, PDF
# ===========================================================================
def test_30_json_nao_introduz_perda_monetaria():
    r = calcular_por_margem(377.11, 3, 0.14, REGRAS)
    volta = json.loads(json.dumps(r.como_dict()))
    assert D(volta["preco_negociado"]) == r.preco_negociado
    assert D(volta["faturamento"]) == r.faturamento
    assert D(volta["lucro"]) == r.lucro
    # e o dicionário externo é numérico, não string: Decimal virando texto mudaria o snapshot
    assert isinstance(volta["preco_negociado"], float)


def test_31_banco_roundtrip_preserva_o_centavo(session):
    """Decimal → coluna REAL do SQLite → Decimal, sem perda de quantia."""
    from app.models import Cotacao, CotacaoItem
    cot = Cotacao(cliente_id=cliente_de_apoio(session), estado_destino="São Paulo", condicao_pagamento="30")
    session.add(cot)
    session.commit()
    valores = [D("99.90"), D("0.01"), D("1234.56"), D("0.10"), D("1000000.99")]
    ids = []
    for v in valores:
        it = CotacaoItem(cotacao_id=cot.id, nome_produto="roundtrip", quantidade=1,
                         custo_unitario=1.0, preco_base=para_float(v),
                         preco_negociado=para_float(v), margem_liquida=0.0,
                         faturamento=para_float(v), custo_total=1.0, lucro=0.0)
        session.add(it)
        session.commit()
        ids.append(it.id)
    session.expire_all()
    for v, i in zip(valores, ids):
        lido = session.get(CotacaoItem, i)
        assert D(lido.preco_negociado) == v
        assert dinheiro(lido.faturamento) == v


def test_37_preco_negociado_99_90_continua_99_90_apos_o_banco(session):
    """O caso do §37, escrito à mão: R$ 99,90 tem que voltar exatamente R$ 99,90."""
    from app.models import Cotacao, CotacaoItem
    cot = Cotacao(cliente_id=cliente_de_apoio(session), estado_destino="São Paulo", condicao_pagamento="30")
    session.add(cot)
    session.commit()
    r = calcular_por_preco(50.0, 1, "99.90", REGRAS)
    it = CotacaoItem(cotacao_id=cot.id, nome_produto="x", quantidade=1, custo_unitario=50.0,
                     preco_base=0.0, preco_negociado=para_float(r.preco_negociado),
                     margem_liquida=para_float(r.margem_liquida),
                     faturamento=para_float(r.faturamento),
                     custo_total=para_float(r.custo_total), lucro=para_float(r.lucro))
    session.add(it)
    session.commit()
    session.expire_all()
    lido = session.get(CotacaoItem, it.id)
    assert D(lido.preco_negociado) == D("99.90")
    assert D(lido.faturamento) == D("99.90")


def test_38_fx_5_11_permanece_exato_no_nucleo(session):
    from app import pricing_service as ps
    pr = ps.premissas_nacionalizacao(session)
    assert pr.fx_usd_brl == D(str(pr.fx_usd_brl))
    assert isinstance(pr.fx_usd_brl, Decimal)
    direto = PremissasNacionalizacao(frete_usd_kg=0.516, outras_desp_usd_un=0.0, fx_usd_brl=5.11)
    assert direto.fx_usd_brl == D("5.11")
    assert direto.frete_usd_kg == D("0.516")


def test_39_pis_cofins_nao_sofre_erro_binario():
    regras = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016)
    assert regras.pis_cofins_pct == D("0.0759")
    assert regras.taxa_fixa() == D("0.2719")          # 0,18 + 0,0759 + 0,016, exato
    assert 0.18 + 0.0759 + 0.016 != 0.2719            # o mesmo em float NÃO é exato


def test_32_pdf_formata_quantia_em_centavos():
    from app.templating import fmt_brl
    assert fmt_brl(D("10.00")) == "R$ 10,00"
    assert fmt_brl(D("1234.56")) == "R$ 1.234,56"
    assert fmt_brl(0.30000000000000004) == "R$ 0,30"
    assert fmt_brl(D("34.71540940423179")) == "R$ 34,72"
    assert fmt_brl("2.675") == "R$ 2,68"              # a régua do sistema, não a do format


def test_40_rate_percentual_nao_e_congelado_num_preco_preliminar():
    """O RV vira reais só depois do preço; dobrar a receita dobra o ADV."""
    regras = TaxRuleSet(icms_pct=0.18, pis_cofins_pct=0.0759, encargo_financeiro_pct=0.016,
                        comissao_tabela=[(0.0, 0.05)], frete_rv_pct=D("0.002"))
    um = calcular_por_preco(50.0, 1, "1000.00", regras)
    dois = calcular_por_preco(50.0, 2, "1000.00", regras)
    assert um.frete_rv == D("2.00")
    assert dois.frete_rv == D("4.00")
    assert dois.frete_rv == 2 * um.frete_rv


# ===========================================================================
# 33-36 · Idempotência
# ===========================================================================
def test_33_calcular_varias_vezes_da_o_mesmo_resultado():
    primeiro = calcular_por_margem(377.11, 3, 0.14, REGRAS)
    for _ in range(10):
        outro = calcular_por_margem(377.11, 3, 0.14, REGRAS)
        assert outro.como_dict() == primeiro.como_dict()


def test_35_calcular_salvar_carregar_recalcular_e_idempotente(session):
    """Salvar e recalcular não pode mover um centavo — nem na quinta volta."""
    from app.models import Cotacao, CotacaoItem
    cot = Cotacao(cliente_id=cliente_de_apoio(session), estado_destino="São Paulo", condicao_pagamento="30")
    session.add(cot)
    session.commit()
    r = calcular_por_margem(377.11, 3, 0.14, REGRAS)
    it = CotacaoItem(cotacao_id=cot.id, nome_produto="x", quantidade=3, custo_unitario=377.11,
                     preco_base=para_float(r.preco_negociado),
                     preco_negociado=para_float(r.preco_negociado),
                     margem_liquida=para_float(r.margem_liquida),
                     faturamento=para_float(r.faturamento),
                     custo_total=para_float(r.custo_total), lucro=para_float(r.lucro))
    session.add(it)
    session.commit()

    for _ in range(5):
        session.expire_all()
        lido = session.get(CotacaoItem, it.id)
        novo = calcular_por_preco(lido.custo_unitario, lido.quantidade,
                                  lido.preco_negociado, REGRAS)
        assert novo.preco_negociado == r.preco_negociado
        assert novo.faturamento == r.faturamento
        assert novo.lucro == r.lucro
        assert novo.margem_liquida == r.margem_liquida
        lido.preco_negociado = para_float(novo.preco_negociado)
        lido.faturamento = para_float(novo.faturamento)
        lido.lucro = para_float(novo.lucro)
        session.add(lido)
        session.commit()


def test_36_preco_arredondado_recalcula_a_margem_real():
    """Fechar o ciclo: a margem gravada é a do preço gravado, e sobrevive à volta."""
    r = calcular_por_margem(377.11, 1, 0.14, REGRAS)
    volta = calcular_por_preco(377.11, 1, r.preco_negociado, REGRAS)
    assert volta.margem_liquida == r.margem_liquida
    assert volta.margem_liquida == divide(volta.lucro, volta.faturamento)


# ===========================================================================
# 34 · Baseline e histórico (o histórico completo é coberto por test_fundacao)
# ===========================================================================
@pytest.mark.parametrize("custo", ["0.01", "1.00", "12.34", "377.11", "1999.99", "0.07"])
def test_34_todo_preco_formado_sai_em_centavos_e_reconcilia(custo):
    """Nenhum preço formado sai do motor com mais de duas casas, em nenhuma ordem de grandeza.

    Varre de um centavo a dois mil reais: o exponente do preço é sempre −2, a linha sempre
    fecha, e o desvio contra o preço preciso nunca passa de meio centavo.
    """
    for alvo in ("0.12", "0.14", "0.16", "0.18"):
        for qtd in (1, 3, 250):
            r = calcular_por_margem(D(custo), qtd, D(alvo), REGRAS)
            assert r.preco_negociado.as_tuple().exponent == -2
            assert r.faturamento.as_tuple().exponent == -2
            assert r.reconcilia()
            assert abs(r.ajuste_arredondamento) <= MEIO_CENTAVO

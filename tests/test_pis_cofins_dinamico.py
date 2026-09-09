"""PIS/COFINS da venda: nominal 9,25% menos o ICMS excluído da base.

O sistema usava `pis_cofins_pct = 0,0759` como constante global. Em 09/09/2026 a contabilidade
da Indústria Química Anastacio (Brendo Simão) confirmou, com a planilha
"Fator Cálculo Exclusão ICMS .xlsx", que **o percentual muda em função do ICMS**: o ICMS é
excluído da base de PIS/COFINS, então

    efetivo = 9,25% × (1 − ICMS da operação)

Os 7,59% eram só a aproximação do cenário de ICMS 18% (7,585%). Aplicá-los a toda venda
interestadual — onde a alíquota cai para 12%, 7% ou 4% — subestimava o encargo em até 1,29
ponto percentual, e isso entra direto no denominador do gross-up: preço menor, margem real
menor do que a apurada.

O que esta suíte cobra, em quatro camadas:

1. **a fórmula**, na função pura, incluindo alíquotas que não estão em tabela nenhuma —
   se alguém trocar isto por um lookup de quatro linhas, os testes de ICMS 20% e 22% quebram;
2. **o caminho real**, da cotação ao `TaxRuleSet`, com as regras fiscais já cadastradas —
   nenhuma alíquota foi inventada para caber no teste;
3. **a carga TOTAL**, e não a interestadual: no não contribuinte o DIFAL e o FCP que a Anara
   recolhe já estão dentro do `icms_pct`, e é ele que sai da base;
4. **o que não pode mudar**: o CNET da Daune, que é crédito de COMPRA, e as cotações antigas,
   que continuam valendo 7,59% porque foi com 7,59% que elas foram formadas.
"""
import itertools
import json
from decimal import Decimal

import pytest
from sqlmodel import select

from app import config_service as cfg
from app import pricing_service as ps
from app.custo_service import cnet_nacional
from app.dinheiro import D
from app.models import CostMethod, Cotacao, Fornecedor, Produto
from app.pricing_engine import (
    TaxRuleSet, calcular_por_margem, calcular_por_preco, pis_cofins_efetivo,
)

from decimais import aprox  # noqa: E402

NOMINAL = D("0.0925")


# ===========================================================================
# 1. A fórmula — função pura
# ===========================================================================
@pytest.mark.parametrize("icms,esperado", [
    ("0.18", "0.07585"),      # intraestadual SP
    ("0.12", "0.0814"),       # interestadual nacional para MG, PR, RJ, SC
    ("0.07", "0.086025"),     # interestadual nacional para a Bahia
    ("0.04", "0.0888"),       # interestadual de mercadoria importada
    ("0", "0.0925"),          # sem ICMS a excluir, o efetivo é o nominal
])
def test_golden_do_efetivo(icms, esperado):
    """Os quatro goldens da contabilidade, mais o caso-limite de ICMS zero.

    Igualdade **exata** em `Decimal`, não aproximada: a alíquota não é dinheiro e não se
    quantiza no meio da cadeia. `9,25% × 0,93` é `0,086025`, com os seis decimais.
    """
    assert pis_cofins_efetivo(NOMINAL, D(icms)) == Decimal(esperado)


@pytest.mark.parametrize("icms,esperado", [("0.20", "0.074"), ("0.22", "0.07215"),
                                           ("0.205", "0.0735375"), ("0.17", "0.076775")])
def test_e_formula_nao_tabela(icms, esperado):
    """Alíquotas fora do golden resolvem igual — inclusive as que nem são inteiras.

    Este teste existe para reprovar uma implementação por lookup: uma tabela com as quatro
    linhas do e-mail da contabilidade passaria nos testes acima e falharia aqui, e falharia em
    produção no dia em que uma UF mudasse de alíquota.
    """
    assert pis_cofins_efetivo(NOMINAL, D(icms)) == Decimal(esperado)


def test_7_59_deixou_de_ser_a_resposta_de_18_por_cento():
    """7,585% ≠ 7,59%. A diferença é pequena em SP e é o erro inteiro fora dele."""
    assert pis_cofins_efetivo(NOMINAL, D("0.18")) != D("0.0759")
    assert pis_cofins_efetivo(NOMINAL, D("0.18")) == D("0.07585")


def test_quanto_o_7_59_fixo_subestimava():
    """A diferença cresce à medida que o ICMS cai — e é sempre a favor do erro."""
    esperado = {"0.18": "-0.00005", "0.12": "0.0055", "0.07": "0.010125", "0.04": "0.0129"}
    for icms, diferenca in esperado.items():
        assert pis_cofins_efetivo(NOMINAL, D(icms)) - D("0.0759") == Decimal(diferenca)


def test_nao_quantiza_a_aliquota():
    """`0,086025` tem seis decimais e continua com os seis. Arredondar aqui é o erro antigo."""
    r = pis_cofins_efetivo(NOMINAL, D("0.07"))
    assert r == Decimal("0.086025")
    assert r != Decimal("0.0860") and r != Decimal("0.09")


def test_decimal_de_ponta_a_ponta_sem_erro_binario():
    """Nada de `Decimal(float)`: a ponte é `D()`, pela representação textual."""
    assert pis_cofins_efetivo(0.0925, 0.18) == Decimal("0.07585")
    assert isinstance(pis_cofins_efetivo(NOMINAL, D("0.18")), Decimal)


def test_icms_nao_resolvido_recusa_em_vez_de_assumir_zero():
    """Sem ICMS não há efetivo. Devolver o nominal seria assumir ICMS zero — fallback silencioso.

    É a diferença entre "não sei" e "é 9,25%": a segunda resposta forma preço, e forma errado.
    """
    with pytest.raises(ValueError, match="ICMS"):
        pis_cofins_efetivo(NOMINAL, None)
    with pytest.raises(ValueError, match="nominal"):
        pis_cofins_efetivo(None, D("0.18"))


# ===========================================================================
# 2. O caminho real — cotação → fiscal → TaxRuleSet
# ===========================================================================
@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


#: A sessão de teste é compartilhada por toda a suíte e `Produto.sku_key` é único — um rótulo
#: fixo colide com o de outro arquivo e derruba tudo que vier depois, porque o flush falho deixa
#: a sessão em rollback pendente.
_SEQ = itertools.count(1)


def produto_de(session, fornecedor, rotulo, custo=100.0):
    sku = f"PCD-{rotulo}-{next(_SEQ)}"
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia="Flat Sheet",
                cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def cotacao_para(destino, contribuinte=True, finalidade="REVENDA"):
    return Cotacao(cliente_id=0, estado_origem="São Paulo", uf_origem_fiscal="SP",
                   estado_destino=destino, contribuinte_icms=contribuinte,
                   finalidade=finalidade, condicao_pagamento="30")


#: (cenário, destino, contribuinte, finalidade, fornecedor, ICMS esperado, efetivo esperado).
#: Todas as alíquotas saem do cadastro real semeado — nenhuma foi criada para o teste.
CENARIOS = [
    ("A · SP→SP intraestadual", "São Paulo", True, "REVENDA", "DAUNE", "0.18", "0.07585"),
    ("B · SP→MG importada", "Minas Gerais", True, "REVENDA", "KTC", "0.04", "0.0888"),
    ("C · SP→BA nacional", "Bahia", True, "REVENDA", "DAUNE", "0.07", "0.086025"),
    ("D · SP→MG nacional", "Minas Gerais", True, "REVENDA", "DAUNE", "0.12", "0.0814"),
]


@pytest.mark.parametrize("rotulo,destino,contrib,finalidade,forn,icms,efetivo", CENARIOS)
def test_caminho_completo_por_cenario(session, fornecedores, rotulo, destino, contrib,
                                      finalidade, forn, icms, efetivo):
    """Cotação → `fiscal_do_item` → `regras_da_cotacao` → `TaxRuleSet.pis_cofins_pct`."""
    produto = produto_de(session, fornecedores[forn], f"PC-{rotulo[0]}")
    regras, ctx = ps.regras_da_cotacao(session, cotacao_para(destino, contrib, finalidade),
                                       produto)

    assert regras is not None, f"{rotulo}: o cenário fiscal tinha de resolver"
    assert regras.icms_pct == D(icms), rotulo
    assert regras.pis_cofins_pct == D(efetivo), rotulo
    # e o contexto deixa a conta aberta, não só o resultado
    assert ctx["pis_cofins_nominal_pct"] == aprox(0.0925)
    assert ctx["pis_cofins_icms_excluido_pct"] == D(icms)
    assert ctx["pis_cofins_pct"] == D(efetivo)


@pytest.mark.parametrize("rotulo,destino,contrib,finalidade,forn,icms,efetivo", CENARIOS)
def test_o_efetivo_chega_ao_denominador(session, fornecedores, rotulo, destino, contrib,
                                        finalidade, forn, icms, efetivo):
    """Não basta estar no `TaxRuleSet`: tem de mudar o preço."""
    produto = produto_de(session, fornecedores[forn], f"DEN-{rotulo[0]}")
    regras, _ = ps.regras_da_cotacao(session, cotacao_para(destino, contrib, finalidade),
                                     produto)
    assert regras.taxa_fixa() == D(icms) + D(efetivo) + regras.encargo_financeiro_pct

    velho = TaxRuleSet(icms_pct=regras.icms_pct, pis_cofins_pct=D("0.0759"),
                       encargo_financeiro_pct=regras.encargo_financeiro_pct,
                       comissao_tabela=regras.comissao_tabela)
    novo = calcular_por_margem(100.0, 1, 0.14, regras).preco_negociado
    antigo = calcular_por_margem(100.0, 1, 0.14, velho).preco_negociado
    if D(efetivo) > D("0.0759"):
        assert novo > antigo, f"{rotulo}: encargo maior tem de elevar o preço da margem-alvo"
    else:
        assert novo <= antigo


# ---------------------------------------------------------------------------
# E e F — não contribuinte: DIFAL e FCP entram na exclusão
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("forn,interestadual,difal", [("DAUNE", "0.12", "0.08"),
                                                      ("KTC", "0.04", "0.16")])
def test_E_F_nao_contribuinte_exclui_a_carga_total(session, fornecedores, forn,
                                                   interestadual, difal):
    """SP→RJ, não contribuinte: 12%+8%+2% e 4%+16%+2% dão a MESMA carga de 22%.

    É o teste que separa "usar o ICMS da operação" de "usar a alíquota interestadual". Se o
    PIS/COFINS saísse da interestadual, a Daune daria 8,14% e a KTC 8,88% — dois números
    diferentes para duas operações que suportam exatamente a mesma carga de ICMS.

    O DIFAL aqui é do REMETENTE: sai do bolso da Anara, entra no `icms_pct` e, portanto, sai
    da base de PIS/COFINS junto com o resto.
    """
    produto = produto_de(session, fornecedores[forn], f"NC-{forn}")
    cot = cotacao_para("Rio de Janeiro", contribuinte=False, finalidade="USO_CONSUMO")
    regras, ctx = ps.regras_da_cotacao(session, cot, produto)

    assert ctx["aliquota_interestadual"] == D(interestadual)
    assert ctx["difal_pct"] == D(difal)
    assert ctx["fcp_pct"] == D("0.02")
    assert ctx["difal_responsavel"] == "REMETENTE"
    assert regras.icms_pct == D("0.22"), "4+16+2 = 12+8+2 = 22"

    # a carga total, e nada além dela
    assert regras.pis_cofins_pct == D("0.07215")
    assert regras.pis_cofins_pct != D(interestadual and "0.0814" or ""), "não é só a interestadual"
    assert regras.pis_cofins_pct != pis_cofins_efetivo(NOMINAL, D(interestadual))


def test_F_o_fcp_entra_uma_vez_so(session, fornecedores):
    """O FECP do RJ já está dentro do `icms_pct`. Somá-lo de novo daria 24% e 7,03%.

    A coluna `aliquota_interna` do RJ vale 22% — com o FECP embutido —, e a base é 20%. O
    motor fiscal já resolveu isso na Onda 1; aqui só se prova que o PIS/COFINS **consome** o
    resultado em vez de recompor `interestadual + DIFAL + FCP` por conta própria.
    """
    produto = produto_de(session, fornecedores["DAUNE"], "FCP-1")
    cot = cotacao_para("Rio de Janeiro", contribuinte=False, finalidade="USO_CONSUMO")
    regras, ctx = ps.regras_da_cotacao(session, cot, produto)

    assert regras.pis_cofins_pct == pis_cofins_efetivo(NOMINAL, D("0.22"))
    assert regras.pis_cofins_pct != pis_cofins_efetivo(NOMINAL, D("0.24")), "FCP em dobro"
    # e a soma das partes bate com o total que foi excluído
    assert (ctx["aliquota_interestadual"] + ctx["difal_pct"] + ctx["fcp_pct"]
            == ctx["pis_cofins_icms_excluido_pct"])


def test_carga_final_nao_participa(session, fornecedores):
    """A coluna legada de MG vale 17,07% e não pode aparecer na exclusão."""
    from app.models import EstadoFiscal
    mg = session.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "MG")).first()
    produto = produto_de(session, fornecedores["DAUNE"], "CF-1")
    regras, _ = ps.regras_da_cotacao(session, cotacao_para("Minas Gerais"), produto)

    assert mg.carga_final == aprox(0.1707), "a coluna continua lá, para o histórico"
    assert regras.pis_cofins_pct == D("0.0814")
    assert regras.pis_cofins_pct != pis_cofins_efetivo(NOMINAL, D(str(mg.carga_final)))


# ===========================================================================
# 3. Cotação mista — o efetivo é POR ITEM
# ===========================================================================
def test_cotacao_mista_tem_um_pis_cofins_por_item(session, fornecedores):
    """Mesmo documento, mesmo destino: KTC a 8,88% e Daune a 8,14%.

    Como o ICMS é por item desde a Onda 1 e o PIS/COFINS agora deriva dele, o PIS/COFINS
    também é por item. Um escalar de cabeçalho aqui seria a taxa de uma linha vendida como se
    fosse a de todas.
    """
    ktc = produto_de(session, fornecedores["KTC"], "MIX-KTC", custo=100.0)
    daune = produto_de(session, fornecedores["DAUNE"], "MIX-DAUNE", custo=100.0)
    cot = cotacao_para("Minas Gerais", contribuinte=True)

    r_ktc, ctx_ktc = ps.regras_da_cotacao(session, cot, ktc)
    r_daune, ctx_daune = ps.regras_da_cotacao(session, cot, daune)

    assert (r_ktc.icms_pct, r_ktc.pis_cofins_pct) == (D("0.04"), D("0.0888"))
    assert (r_daune.icms_pct, r_daune.pis_cofins_pct) == (D("0.12"), D("0.0814"))
    assert r_ktc.pis_cofins_pct != r_daune.pis_cofins_pct
    # o nominal é o mesmo para os dois — o que difere é o ICMS de cada um
    assert ctx_ktc["pis_cofins_nominal_pct"] == ctx_daune["pis_cofins_nominal_pct"]

    # e a carga total de cada item continua sendo ICMS + PIS/COFINS + encargo
    assert r_ktc.taxa_fixa() == D("0.04") + D("0.0888") + r_ktc.encargo_financeiro_pct
    assert r_daune.taxa_fixa() == D("0.12") + D("0.0814") + r_daune.encargo_financeiro_pct


def test_memoria_de_cada_item_mostra_a_conta_daquele_item(session, fornecedores):
    """A memória do preço é o que responde "de onde veio este número" — por linha."""
    ktc = produto_de(session, fornecedores["KTC"], "MEM-KTC", custo=100.0)
    daune = produto_de(session, fornecedores["DAUNE"], "MEM-DAUNE", custo=100.0)
    cot = cotacao_para("Minas Gerais", contribuinte=True)

    m_ktc = ps.memoria_do_preco(session, ktc, cot)["fiscal"]
    m_daune = ps.memoria_do_preco(session, daune, cot)["fiscal"]

    assert m_ktc["pis_cofins_icms_excluido_pct"] == aprox(0.04)
    assert m_ktc["pis_cofins_pct"] == aprox(0.0888)
    assert m_daune["pis_cofins_icms_excluido_pct"] == aprox(0.12)
    assert m_daune["pis_cofins_pct"] == aprox(0.0814)
    # continua serializável em número, como o resto da memória
    for bloco in (m_ktc, m_daune):
        for campo in ("pis_cofins_nominal_pct", "pis_cofins_icms_excluido_pct", "pis_cofins_pct"):
            assert isinstance(bloco[campo], (int, float)), campo
    json.dumps(m_ktc)


def test_precos_dos_dois_itens_somam_sem_taxa_de_cabecalho(session, fornecedores):
    """O total da cotação é a soma das linhas, cada uma com o seu próprio encargo."""
    ktc = produto_de(session, fornecedores["KTC"], "SOMA-KTC", custo=100.0)
    daune = produto_de(session, fornecedores["DAUNE"], "SOMA-DAUNE", custo=100.0)
    cot = cotacao_para("Minas Gerais", contribuinte=True)

    r_ktc, _ = ps.regras_da_cotacao(session, cot, ktc)
    r_daune, _ = ps.regras_da_cotacao(session, cot, daune)
    a = calcular_por_margem(100.0, 2, 0.14, r_ktc)
    b = calcular_por_margem(100.0, 3, 0.14, r_daune)

    assert a.preco_negociado != b.preco_negociado
    assert a.faturamento + b.faturamento == (a.preco_negociado * 2 + b.preco_negociado * 3)


# ===========================================================================
# 4. O que NÃO pode mudar
# ===========================================================================
def test_regressao_cnet_daune_249_37():
    """Crédito de COMPRA. A correção é da VENDA e não encosta aqui.

    Golden fechado com o fornecedor e com a contabilidade: ICMS de entrada 12% (redução de
    base em SP) e crédito de PIS/COFINS de 9,25% sobre a aquisição líquida do ICMS.
    """
    c = cnet_nacional(D("249.37"))
    assert c.gross == D("249.37")
    assert c.icms_credito == D("29.9244")
    assert c.base_pis_cofins == D("219.4456")
    assert c.pis_cofins_credito == D("20.298718")
    assert c.cnet == D("199.146882")
    assert c.fator == aprox(0.7986, abs=D("1e-6"))


def test_o_credito_de_compra_usa_o_nominal_cheio_e_nao_o_efetivo_da_venda():
    """9,25% na entrada é 9,25% inteiro — a exclusão do ICMS já aconteceu na base."""
    c = cnet_nacional(D("249.37"))
    assert c.pis_cofins_credito == c.base_pis_cofins * D("0.0925")
    assert c.pis_cofins_credito != c.base_pis_cofins * pis_cofins_efetivo(NOMINAL, D("0.12"))


def test_a_premissa_legada_continua_existindo_e_legivel(session):
    """`pis_cofins_pct` = 0,0759 fica no banco: cotação antiga foi formada com ela."""
    legada = cfg.premissa(session, "pis_cofins_pct")
    assert legada is not None and D(legada.valor_num) == D("0.0759")
    assert legada.valid_to is None, "continua vigente como registro; só não alimenta cálculo"


def test_a_premissa_legada_nao_alimenta_mais_preco_nenhum(session, fornecedores):
    """A prova por contradição: mexer nos 7,59% não move preço nenhum.

    Enquanto ela fosse lida, trocar 0,0759 por 0,50 mudaria o preço de todo item do sistema.
    Depois da correção, o número pode ser qualquer coisa — ninguém pergunta a ele.
    """
    produto = produto_de(session, fornecedores["DAUNE"], "LEG-1")
    cot = cotacao_para("Minas Gerais")
    antes, _ = ps.regras_da_cotacao(session, cot, produto)
    preco_antes = calcular_por_margem(100.0, 1, 0.14, antes).preco_negociado

    cfg.definir(session, "pis_cofins_pct", valor_num=0.50, fonte="sabotagem do teste")
    session.commit()
    try:
        depois, _ = ps.regras_da_cotacao(session, cot, produto)
        assert depois.pis_cofins_pct == D("0.0814"), "o efetivo veio do nominal, não da legada"
        assert calcular_por_margem(100.0, 1, 0.14, depois).preco_negociado == preco_antes
    finally:
        cfg.definir(session, "pis_cofins_pct", valor_num=0.0759, fonte="restauro do teste")
        session.commit()


def test_o_nominal_e_que_move_o_preco(session, fornecedores):
    """O contraponto do teste acima: mexer no nominal move, e move na direção certa."""
    produto = produto_de(session, fornecedores["DAUNE"], "NOM-1")
    cot = cotacao_para("Minas Gerais")
    antes, _ = ps.regras_da_cotacao(session, cot, produto)
    preco_antes = calcular_por_margem(100.0, 1, 0.14, antes).preco_negociado

    cfg.definir(session, "pis_cofins_nominal_pct", valor_num=0.10, fonte="teste")
    session.commit()
    try:
        depois, _ = ps.regras_da_cotacao(session, cot, produto)
        assert depois.pis_cofins_pct == D("0.10") * (D("1") - D("0.12"))
        assert calcular_por_margem(100.0, 1, 0.14, depois).preco_negociado > preco_antes
    finally:
        cfg.definir(session, "pis_cofins_nominal_pct", valor_num=0.0925, fonte="restauro")
        session.commit()


# ---------------------------------------------------------------------------
# Histórico: cotação antiga não é reprecificada
# ---------------------------------------------------------------------------
def test_snapshot_historico_de_7_59_nao_e_recalculado(session, fornecedores):
    """Um item formado sob a metodologia antiga continua dizendo 7,59% depois da correção.

    A garantia não é um `if` no código: é o `memoria_json` do item, que é **snapshot** e não
    é relido do resolvedor. O teste monta a linha como ela existia — memória com 7,59% e pino
    na premissa legada — e prova que trocar a premissa vigente não a reescreve.
    """
    from app.models import CotacaoItem

    historico = CotacaoItem(
        cotacao_id=0, produto_id=None, nome_produto="Item de 2026 · metodologia antiga",
        quantidade=1, custo_unitario=100.0, preco_base=185.82, preco_negociado=185.82,
        margem_liquida=0.14, faturamento=185.82, custo_total=100.0, lucro=26.01,
        icms_pct=0.18, encargo_pct=0.016, status_fiscal="OK",
        memoria_json=json.dumps({"fiscal": {"icms_pct": 0.18, "pis_cofins_pct": 0.0759,
                                            "encargo_pct": 0.016}}),
        premissas_pinadas=json.dumps({"pis_cofins_pct": {"premissa_id": 6, "valor": 0.0759,
                                                         "valid_from": "2026-08-28"}}))
    session.add(historico)
    session.commit()
    session.refresh(historico)
    congelado = (historico.memoria_json, historico.premissas_pinadas,
                 historico.preco_negociado, historico.icms_pct)

    # a premissa nominal muda hoje — o passado não se mexe
    cfg.definir(session, "pis_cofins_nominal_pct", valor_num=0.0925,
                fonte="evidência de 09/09/2026")
    session.commit()
    session.refresh(historico)

    assert (historico.memoria_json, historico.premissas_pinadas,
            historico.preco_negociado, historico.icms_pct) == congelado
    fiscal = json.loads(historico.memoria_json)["fiscal"]
    assert fiscal["pis_cofins_pct"] == 0.0759, "a linha antiga continua dizendo 7,59%"
    pino = json.loads(historico.premissas_pinadas)["pis_cofins_pct"]
    assert pino["valor"] == 0.0759 and pino["premissa_id"] == 6
    session.delete(historico)
    session.commit()


def test_item_novo_pina_o_nominal_e_o_antigo_continua_interpretavel(session):
    """A genealogia nova aponta para `pis_cofins_nominal_pct`; a antiga, para a legada."""
    pinos = ps.pinar_premissas(session)
    assert "pis_cofins_nominal_pct" in pinos
    assert D(pinos["pis_cofins_nominal_pct"]["valor"]) == D("0.0925")
    assert "pis_cofins_pct" not in pinos, "a legada não entra em genealogia nova"
    # mas continua consultável para quem precisa ler um pino antigo
    assert cfg.premissa(session, "pis_cofins_pct") is not None


def test_a_genealogia_permite_reconstruir_a_conta(session, fornecedores):
    """Nominal pinado + ICMS do snapshot fiscal do item = o efetivo, sem coluna nova.

    É o motivo de não existir `CotacaoItem.pis_cofins_pct`: os dois insumos já estão
    congelados na linha, e o terceiro é uma multiplicação.
    """
    produto = produto_de(session, fornecedores["DAUNE"], "GEN-1")
    _regras, ctx = ps.regras_da_cotacao(session, cotacao_para("Bahia"), produto)

    nominal_pinado = D(ctx["premissas_pinadas"]["pis_cofins_nominal_pct"]["valor"])
    icms_do_item = ctx["icms_pct"]
    assert pis_cofins_efetivo(nominal_pinado, icms_do_item) == ctx["pis_cofins_pct"]
    assert ctx["pis_cofins_pct"] == D("0.086025")


# ===========================================================================
# 5. Efeito colateral tratado — a tolerância da margem no workflow
# ===========================================================================
# A correção mudou o preço, e o preço mudou onde o resíduo do arredondamento cai. Isso expôs
# uma tolerância mal calibrada em `workflow.excecoes_do_item`: 5×10⁻⁵ era **menor** que meio
# centavo dividido pela receita em qualquer item abaixo de R$ 100, então o próprio
# arredondamento comercial passou a pedir aprovação administrativa. O comentário da regra
# sempre disse que o centavo não é exceção comercial; o número é que não cumpria.
def test_o_centavo_nao_vira_excecao_comercial():
    """Desvio da ordem do centavo não é desconto. Desvio de regra continua sendo."""
    from app.workflow import MARGEM_ABAIXO, TOLERANCIA_MARGEM_DO_CENTAVO, excecoes_do_item
    from app.models import CotacaoItem

    def excecoes(margem_real):
        item = CotacaoItem(cotacao_id=0, ordem=0, nome_produto="X", quantidade=1,
                           custo_unitario=50.0, preco_base=92.91, preco_negociado=92.91,
                           preco_recomendado=92.91, margem_liquida=margem_real,
                           margem_padrao_pct=0.14, faturamento=92.91, custo_total=50.0,
                           lucro=13.0, modo_edicao="preco", valor_editado=0.0)
        return [e.motivo for e in excecoes_do_item(item)]

    # o caso real que a correção produziu: R$ 92,91 com alvo de 14% entrega 13,99204%
    assert MARGEM_ABAIXO not in excecoes(0.1399203530298138)
    # e o pior desvio de arredondamento medido no motor, −1,2×10⁻⁴, também não
    assert MARGEM_ABAIXO not in excecoes(0.14 - 0.00012)
    # mas meio ponto percentual abaixo do alvo é desconto, e continua exigindo aprovação
    assert MARGEM_ABAIXO in excecoes(0.135)
    assert MARGEM_ABAIXO in excecoes(0.14 - float(TOLERANCIA_MARGEM_DO_CENTAVO) * 2)


def test_a_tolerancia_ainda_reprova_mudanca_de_regra():
    """A folga é do centavo, não de política: trocar alíquota move mil vezes mais que isso."""
    from app.workflow import TOLERANCIA_MARGEM_DO_CENTAVO

    regras_18 = TaxRuleSet(icms_pct=D("0.18"), pis_cofins_pct=D("0.07585"),
                           encargo_financeiro_pct=D("0.016"),
                           comissao_tabela=[(D("0"), D("0.05"))])
    regras_12 = TaxRuleSet(icms_pct=D("0.12"), pis_cofins_pct=D("0.0814"),
                           encargo_financeiro_pct=D("0.016"),
                           comissao_tabela=[(D("0"), D("0.05"))])
    preco = calcular_por_margem(100.0, 1, 0.14, regras_18).preco_negociado
    # o mesmo preço sob outra regra fiscal entrega outra margem — e a diferença é enorme
    # perto da tolerância do centavo
    outra = calcular_por_preco(100.0, 1, preco, regras_12).margem_liquida
    assert abs(outra - D("0.14")) > TOLERANCIA_MARGEM_DO_CENTAVO * 20

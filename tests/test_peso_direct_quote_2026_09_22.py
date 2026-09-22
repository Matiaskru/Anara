"""Peso logístico de produto Direct Quote — e a arquitetura que não pode escorregar (22/09/2026).

Sete roupões KTC com EXW **cotado, datado e documentado** ficavam `REVIEW_REQUIRED` por um
motivo só: falta de peso. Peso não forma EXW — ele rateia o frete internacional por unidade
(US$/kg × kg). Sem peso o frete entraria como zero e o custo sairia subestimado, então o
bloqueio estava certo; o que faltava era **reaproveitar o peso que a própria ANARA já usava**
para o mesmo modelo, em vez de exigir que o OWNER digitasse SKU a SKU.

Estes testes fixam as três coisas que não podem se perder:

1. **Direct Quote continua Direct Quote** (1–3). Roupão, lençol com elástico, protetor e
   topper de colchão, travesseiro, edredom, chinelo e passadeira **não** têm fórmula
   industrial. Recuperar peso não pode virar porta dos fundos para o `ktc_engine` inventar
   EXW — e há teste que falha se alguma dessas famílias passar a ter EXW calculado.
2. **O peso herdado é conservador** (4–9): mesma família, tamanho, gramatura e composição, com
   convergência entre candidatos. Tamanho diferente não herda. Peso próprio sempre manda.
3. **ESTIMADO cota e emite, mas não compromete** (10–13) — é o estado que já existia no
   sistema, e o portão de compromisso firme continua de pé.
"""
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import select

from app import peso_historico as ph
from app import pricing_service as ps
from app.models import CostMethod, Fornecedor, Produto, StatusCusto
from tests.crisis.conftest import add_item, nova_cotacao, produto_ktc_cotado

#: As famílias que a KTC cota peça a peça. Nenhuma tem fórmula industrial — e a lista existe
#: aqui, no teste, de propósito: se alguém tornar uma delas calculável, o teste 1 falha e a
#: decisão passa a ser explícita, não um efeito colateral.
FAMILIAS_DIRECT_QUOTE = [
    "Fitted Sheet", "Bathrobe", "Mattress Protector", "Mattress Topper",
    "Pillow Protector", "Pillow", "Duvet Insert", "Slipper", "Bed Runner",
]
FAMILIAS_CALCULAVEIS = ["Flat Sheet", "Top Sheet", "Duvet Cover", "Bath Towel", "Hand Towel"]


@pytest.fixture
def gestor_peso(session):
    """OWNER persistido — as ações de governança são auditadas e o AuditLog pede ator real."""
    from app.models import Usuario
    u = session.exec(select(Usuario).where(Usuario.email == "gestor.peso@anara.test")).first()
    if u is None:
        u = Usuario(email="gestor.peso@anara.test", nome="Gestor", senha_hash="x", papel="OWNER",
                    ativo=True, can_manage_economics=True, sessao_versao=1)
        session.add(u); session.commit(); session.refresh(u)
    return u


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


def _roupao(session, fornecedores, *, tamanho, gsm, cotton=1.0, poli=0.0, peso=None,
            peso_tipo=None, peso_fonte=None, exw=24.0, familia="Bathrobe", **kw):
    """Um roupão como os do catálogo: sem medida, sem fórmula, com EXW cotado."""
    return produto_ktc_cotado(
        session, fornecedores, familia=familia, thread_count=None, exw_usd=exw,
        largura_cm=None, comprimento_cm=None, gsm=gsm, subcategoria=tamanho,
        cotton_pct=cotton, poliester_pct=poli, peso_kg=peso, peso_tipo=peso_tipo,
        peso_fonte=peso_fonte, data=date(2026, 7, 29),
        exw_cotado_fonte="KTC Samples Quotation 29/07/2026", **kw)


# ===========================================================================
# 1–3. Arquitetura: Direct Quote nunca ganha EXW do motor industrial
# ===========================================================================
@pytest.mark.parametrize("familia", FAMILIAS_DIRECT_QUOTE)
def test_1_direct_quote_nao_e_calculavel_pelo_motor(session, fornecedores, familia):
    """`calcular_exw` recusa a família — e a recusa é declarada, não silenciosa."""
    p = _roupao(session, fornecedores, tamanho="L", gsm=420, familia=familia)
    resultado = ps.calcular_exw(session, p)
    assert resultado.exw_usd is None, f"{familia} passou a ter EXW calculado pelo motor"
    assert "familia não calculável" in resultado.faltando


@pytest.mark.parametrize("familia", FAMILIAS_DIRECT_QUOTE)
def test_2_direct_quote_usa_o_exw_cotado_e_nao_o_sintetico(session, fornecedores, familia):
    """O EXW que forma o custo é o da cotação — US$ 24,00 entra US$ 24,00."""
    p = _roupao(session, fornecedores, tamanho="L", gsm=420, familia=familia,
                exw=24.0, peso=1.0, peso_tipo="REAL KTC")
    _, memoria = ps.custo_para_precificar(session, p)
    assert memoria["exw_usd"] == pytest.approx(24.0)
    assert "cotado pela KTC" in (memoria.get("exw_origem") or "")


def test_3_familia_calculavel_continua_calculavel(session, fornecedores):
    """O outro lado da régua: quem tem fórmula não pode tê-la perdido."""
    p = produto_ktc_cotado(session, fornecedores, familia="Flat Sheet", thread_count=300,
                           largura_cm=190, comprimento_cm=250)
    resultado = ps.calcular_exw(session, p)
    assert "familia não calculável" not in (resultado.faltando or [])


# ===========================================================================
# 4–9. A regra do peso herdado
# ===========================================================================
def test_4_herda_do_mesmo_modelo_e_marca_a_procedencia(session, fornecedores):
    historico = _roupao(session, fornecedores, tamanho="LL", gsm=None, cotton=0.0, poli=1.0,
                        peso=0.66, peso_tipo="ESTIMADO", peso_fonte="Estimado: área × GSM")
    novo = _roupao(session, fornecedores, tamanho="LL", gsm=None, cotton=0.0, poli=1.0)
    herdado = ps.peso_herdado_do_historico(session, novo)
    assert herdado is not None and herdado.peso_kg == pytest.approx(0.66)
    assert herdado.sku_origem == historico.sku_key
    d = herdado.como_dict()
    assert d["tipo"] == "ESTIMADO" and d["origem"] == "ANALOGIA_HISTORICA"
    assert "Confirmar com a KTC antes do pedido" in d["fonte"]


def test_5_peso_proprio_sempre_prevalece(session, fornecedores):
    """Peso REAL do SKU nunca é substituído por estimativa — nem se houver análogo."""
    _roupao(session, fornecedores, tamanho="RR", gsm=420, peso=1.45, peso_tipo="ESTIMADO")
    proprio = _roupao(session, fornecedores, tamanho="RR", gsm=420, peso=1.60, peso_tipo="REAL KTC")
    assert ps.peso_herdado_do_historico(session, proprio) is None
    _, memoria = ps.custo_para_precificar(session, proprio)
    assert memoria["nacionalizacao"]["etapas"][1]["formula"].startswith("1.6")
    assert not memoria.get("peso_por_analogia")
    assert ps.status_do_produto(session, proprio) == StatusCusto.confirmado.value


def test_6_tamanho_diferente_nao_herda(session, fornecedores):
    """Extrapolar de L para M seria inventar premissa logística."""
    _roupao(session, fornecedores, tamanho="TG", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    outro = _roupao(session, fornecedores, tamanho="TM", gsm=420)
    assert ps.peso_herdado_do_historico(session, outro) is None


def test_7_gramatura_ou_composicao_diferente_nao_herda(session, fornecedores):
    _roupao(session, fornecedores, tamanho="GG", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    assert ps.peso_herdado_do_historico(
        session, _roupao(session, fornecedores, tamanho="GG", gsm=240)) is None
    assert ps.peso_herdado_do_historico(
        session, _roupao(session, fornecedores, tamanho="GG", gsm=420,
                         cotton=0.0, poli=1.0)) is None


def test_8_candidatos_que_discordam_nao_herdam(session, fornecedores):
    """Dois análogos com pesos diferentes = o modelo importa; não se escolhe um por gosto."""
    _roupao(session, fornecedores, tamanho="DD", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    _roupao(session, fornecedores, tamanho="DD", gsm=420, peso=1.75, peso_tipo="REAL KTC")
    assert ps.peso_herdado_do_historico(
        session, _roupao(session, fornecedores, tamanho="DD", gsm=420)) is None


def test_9_sem_analogo_continua_review_required(session, fornecedores):
    """Sem peso e sem base defensável, o bloqueio permanece — é o ponto do REVIEW."""
    orfao = _roupao(session, fornecedores, tamanho="ZZ", gsm=999)
    custo, memoria = ps.custo_para_precificar(session, orfao)
    assert memoria["premissas_faltantes"] == ["peso"]
    assert ps.status_do_produto(session, orfao, custo, memoria) == StatusCusto.review_required.value


# ===========================================================================
# 10–13. O que ESTIMADO libera — e o que ele continua barrando
# ===========================================================================
def test_10_peso_herdado_forma_custo_e_vira_estimado(session, fornecedores):
    _roupao(session, fornecedores, tamanho="EE", gsm=None, cotton=0.0, poli=1.0,
            peso=0.66, peso_tipo="ESTIMADO", peso_fonte="Estimado: área × GSM")
    novo = _roupao(session, fornecedores, tamanho="EE", gsm=None, cotton=0.0, poli=1.0)
    custo, memoria = ps.custo_para_precificar(session, novo)
    assert not memoria.get("premissas_faltantes")
    assert custo and custo > 0
    assert memoria["peso_por_analogia"] is True
    assert ps.status_do_produto(session, novo, custo, memoria) == StatusCusto.estimado.value


def test_11_o_peso_entra_no_frete_e_so_nele(session, fornecedores):
    """A prova matemática: o peso mexe no frete internacional, não no EXW."""
    _roupao(session, fornecedores, tamanho="FF", gsm=None, cotton=0.0, poli=1.0, peso=0.66,
            peso_tipo="ESTIMADO")
    novo = _roupao(session, fornecedores, tamanho="FF", gsm=None, cotton=0.0, poli=1.0, exw=24.0)
    _, memoria = ps.custo_para_precificar(session, novo)
    premissas = ps.premissas_nacionalizacao(session)
    frete = memoria["nacionalizacao"]["frete_usd"]
    assert frete == pytest.approx(0.66 * float(premissas.frete_usd_kg), rel=1e-6)
    assert memoria["exw_usd"] == pytest.approx(24.0)      # o EXW não se mexe
    assert memoria["nacionalizacao"]["ii_usd"] == pytest.approx(0.0)   # I.I. econômico segue 0


def test_12_estimado_forma_b2b_e_tabela(session, fornecedores):
    _roupao(session, fornecedores, tamanho="HH", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    novo = _roupao(session, fornecedores, tamanho="HH", gsm=420)
    memoria = ps.memoria_do_preco(session, novo)
    b2b = memoria.get("b2b") or {}
    assert b2b.get("preco_b2b") and b2b["preco_b2b"] > 0
    assert b2b["preco_tabela"] == pytest.approx(b2b["preco_b2b"] * b2b["fator_tabela"], rel=1e-9)
    assert b2b["protecao_comercial_pct"] == pytest.approx(0.035)


def test_13_estimado_propoe_mas_a_trava_de_po_e_o_confirmation_pending(session, fornecedores):
    """O que ESTIMADO libera e o que ele barra — como o sistema **já** era, sem trava nova.

    `CUSTO_BLOQUEIA` (o portão da proposta/PDF) não inclui ESTIMADO: a proposta sai. O portão
    do compromisso firme é outro, e ele responde a `confirmation_pending` no item, não ao
    rótulo do status. Nada aqui foi acrescentado a nenhum dos dois.
    """
    from app import workflow as wf
    assert StatusCusto.estimado.value not in wf.CUSTO_BLOQUEIA
    assert StatusCusto.a_cotar.value in wf.CUSTO_BLOQUEIA
    assert StatusCusto.review_required.value in wf.CUSTO_BLOQUEIA

    # o portão, exercitado com objetos reais: item ESTIMADO pendente de confirmação
    _roupao(session, fornecedores, tamanho="PO", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    novo = _roupao(session, fornecedores, tamanho="PO", gsm=420)
    cot = nova_cotacao(session)
    item = add_item(session, cot, novo, quantidade=10)
    assert item.status_custo_item == StatusCusto.estimado.value
    item.confirmation_pending = True
    cot.status = wf.ISSUED
    session.add(item); session.add(cot); session.commit()

    compromisso = wf.validar_compromisso_firme(cot, [item])
    assert not compromisso.pode
    assert any("ESTIMADO" in i for i in compromisso.impedimentos)


def test_14_estimado_gera_pdf_de_proposta(session, fornecedores):
    """O objetivo operacional da correção: o roupão volta a virar proposta impressa."""
    from app.models import Cliente
    from app.pdf_bridge import gerar_pdf_para_cotacao
    from app import workflow as wf

    _roupao(session, fornecedores, tamanho="PDF", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    novo = _roupao(session, fornecedores, tamanho="PDF", gsm=420)
    cot = nova_cotacao(session)
    item = add_item(session, cot, novo, quantidade=10)
    assert item.status_custo_item == StatusCusto.estimado.value
    # o portão da proposta não barra ESTIMADO — é o ponto
    assert item.status_custo_item not in wf.CUSTO_BLOQUEIA

    cliente = session.get(Cliente, cot.cliente_id)
    caminho = gerar_pdf_para_cotacao(cot, cliente, [item])
    assert caminho and Path(caminho).exists() and Path(caminho).stat().st_size > 1000


# ===========================================================================
# 15–16. Confirmar referência: restrição administrativa, não trava comercial
# ===========================================================================
def test_15_nao_confirma_referencia_com_peso_herdado(session, fornecedores, gestor_peso):
    """Promover a CONFIRMADO apagaria o aviso de peso estimado — e a evidência não existe.

    A recusa é **só** administrativa: o SKU continua cotável e emitindo proposta (teste 16).
    """
    from app import governanca_produtos as gov

    _roupao(session, fornecedores, tamanho="CF", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    novo = _roupao(session, fornecedores, tamanho="CF", gsm=420)
    with pytest.raises(gov.AcaoInvalida) as erro:
        gov.confirmar_referencia(session, novo, fonte="conferi", motivo="tentativa", ator=gestor_peso)
    assert "peso logístico estimado por analogia" in str(erro.value).lower()
    assert "peso próprio" in str(erro.value)
    # e o status vivo continua ESTIMADO, sem referência gravada
    assert ps.status_do_produto(session, novo) == StatusCusto.estimado.value


def test_16_com_peso_proprio_a_confirmacao_volta_a_ser_permitida(session, fornecedores, gestor_peso):
    """O caminho de saída: registrar o peso do SKU destrava a confirmação."""
    from app import custo_service as cs
    from app import governanca_produtos as gov

    _roupao(session, fornecedores, tamanho="CG", gsm=420, peso=1.45, peso_tipo="REAL KTC")
    novo = _roupao(session, fornecedores, tamanho="CG", gsm=420)
    gov.registrar_peso(session, novo, peso_kg=1.48, tipo="REAL KTC",
                       fonte="Peso informado pela KTC em 22/09/2026",
                       documento="KTC Samples Quotation 29/07/2026", data_ref=date(2026, 9, 22),
                       motivo="peso da peça informado pela fábrica", ator=gestor_peso)
    session.commit()
    ref = gov.confirmar_referencia(session, novo, fonte="cotação KTC conferida",
                                   motivo="peso próprio registrado", ator=gestor_peso)
    assert ref is not None
    assert cs.referencia_vigente(session, novo.id).status_custo == StatusCusto.confirmado.value

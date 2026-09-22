"""Governança de produtos e custos + coerência catálogo × motor (22/09/2026).

Três defeitos operacionais do go-live, provados aqui:

1. **BR-001** — roupão do catálogo, com EXW cotado, datado e documentado — entrava na cotação
   como "Revisão necessária" enquanto o catálogo dizia "Disponível". Duas telas, duas fontes:
   a coluna-cache `Produto.status_custo` × o status canônico do motor. E o motivo real do
   REVIEW era legítimo: **sem peso**, o frete internacional entraria como zero.
2. **Sem caminho de volta** — não havia onde registrar peso, cotação ou custo sem SQL.
3. Toalha personalizada saindo com "200 fios" (ver `test_toalha_gsm_2026_09_22.py`).

O que estes testes garantem: o catálogo passa a responder o que a cotação vai responder; o
admin resolve a pendência **fornecendo evidência** (nunca apagando blocker); e produto sem
custo continua bloqueando.
"""
import json
from datetime import date

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import custo_service as cs
from app import governanca_produtos as gov
from app import pricing_service as ps
from app import workflow_service as ws
from app.models import CostMethod, CotacaoItem, CustoReferencia, Fornecedor, Produto, Usuario
from app.routers.produtos import situacao_comercial
from conftest import RequestFalsa, _novo_usuario
from decimais import aprox
from tests.crisis.conftest import add_item, chamar, nova_cotacao, produto_ktc_cotado, produto_nacional


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def gestor(session):
    """OWNER persistido — as ações de governança são auditadas e o AuditLog pede ator real."""
    u = session.exec(select(Usuario).where(Usuario.email == "gestor.gov@anara.test")).first()
    if u is None:
        u = Usuario(email="gestor.gov@anara.test", nome="Gestor", senha_hash="x", papel="OWNER",
                    ativo=True, can_manage_economics=True, sessao_versao=1)
        session.add(u); session.commit(); session.refresh(u)
    return u


def _roupao(session, fornecedores, **kw):
    """Um SKU como o BR-001: EXW cotado, datado, documentado — e sem peso nem medida."""
    dados = dict(familia="Bathrobe", thread_count=None, exw_usd=24.0, peso_kg=None,
                 largura_cm=None, comprimento_cm=None, gsm=None,
                 exw_cotado_fonte="KTC Samples Quotation 29/07/2026 · BR-001 · amostra 34",
                 exw_cotado_data=date(2026, 7, 29), preco_base=237.98, custo_unitario=125.85)
    dados.update(kw)
    return produto_ktc_cotado(session, fornecedores, **dados)


# ===========================================================================
# 1–2. BR-001: a referência resolve; o REVIEW que sobra tem motivo declarado
# ===========================================================================
def test_1_br001_resolve_a_evidencia_que_tem(session, fornecedores):
    p = _roupao(session, fornecedores)
    custo, mem = ps.custo_para_precificar(session, p)
    # o EXW é encontrado (não é CATALOGO_SEM_EXW) e o custo sai do motor
    assert mem["net_fonte"] == ps.CUSTO_DERIVADO_AGORA
    assert mem["exw_usd"] == aprox(24.0) and "29/07/2026" in mem["exw_origem"]
    assert custo and custo > 0
    # o que falta é o PESO — e é por isso, e só por isso, que está em revisão
    assert mem["premissas_faltantes"] == ["peso"]
    assert ps.status_canonico_do_custo(custo, mem) == "REVIEW_REQUIRED"
    linha = gov.diagnosticar(session, p)
    assert linha.pendencias and "peso" in linha.pendencias[0].lower()
    assert linha.pode_confirmar is False


def test_2_catalogo_e_cotacao_dao_a_mesma_resposta(session, fornecedores):
    """A incoerência do go-live: catálogo "Disponível", cotação "Revisão necessária"."""
    p = _roupao(session, fornecedores)
    # o cache do produto (coluna e custo_unitario gravados) dizia DISPONIVEL…
    assert situacao_comercial(p) == "DISPONIVEL"
    # …e o motor, que é quem a cotação consulta, diz REVISAR — agora a tela pergunta a ele
    assert situacao_comercial(p, session) == "REVISAR"
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert it.status_custo_item == "REVIEW_REQUIRED"
    assert situacao_comercial(session.get(Produto, p.id), session) == "REVISAR"


def test_3_com_o_peso_registrado_o_pdf_sai(session, fornecedores, gestor):
    from app.pdf_bridge import montar_documento
    from app.models import Cliente, Cotacao
    p = _roupao(session, fornecedores)
    gov.registrar_peso(session, p, peso_kg="1.85", tipo="REAL KTC",
                       fonte="e-mail KTC 22/09/2026", documento="KTC e-mail 22/09",
                       data_ref=date(2026, 9, 22), motivo="peso confirmado pela fábrica",
                       ator=gestor)
    session.commit()
    assert gov.diagnosticar(session, p).status == "CONFIRMADO"
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all()
    it = session.get(CotacaoItem, it.id)
    assert it.status_custo_item == "CONFIRMADO" and it.preco_negociado > 0
    c = session.get(Cotacao, cot.id)
    pront = ws.avaliar(session, c)
    assert not [b for b in pront.blockers if "CUSTO" in b.codigo], [b.codigo for b in pront.blockers]
    header, itens, _t = montar_documento(c, session.get(Cliente, c.cliente_id),
                                         ws.itens_de(session, c.id), rascunho=True)
    assert itens and itens[0]["preco_final"] == aprox(it.preco_negociado)


def test_4_produto_realmente_sem_custo_continua_a_cotar(session, fornecedores, gestor):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=None, peso_kg=None)
    p.exw_cotado_usd = None; p.preco_ktc_usd = None; p.custo_unitario = None
    session.add(p); session.commit()
    linha = gov.diagnosticar(session, p)
    assert linha.status == "A_COTAR" and linha.situacao == "SOB_CONSULTA"
    assert any("Registre a cotação" in x for x in linha.pendencias)
    # confirmar não inventa custo
    with pytest.raises(gov.AcaoInvalida):
        gov.confirmar_referencia(session, p, fonte="x", motivo="y", ator=gestor)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all()
    assert session.get(CotacaoItem, it.id).status_custo_item == "A_COTAR"
    assert not ws.avaliar(session, session.get(app_cotacao(), cot.id)).pode_emitir


def app_cotacao():
    from app.models import Cotacao
    return Cotacao


# ===========================================================================
# 5–7. Ações do admin: registram evidência, versionam e não destroem nada
# ===========================================================================
def test_5_admin_registra_exw_e_produto_passa_a_confirmado(session, fornecedores, gestor):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=None, peso_kg=0.9)
    p.exw_cotado_usd = None; p.preco_ktc_usd = None; p.custo_unitario = None
    session.add(p); session.commit()
    assert gov.diagnosticar(session, p).status == "A_COTAR"
    r = gov.registrar_exw_ktc(session, p, exw_usd="12.50", data_ref=date(2026, 9, 22),
                              documento="KTC Quotation 22/09/2026", fonte="KTC comercial",
                              motivo="cotação nova recebida", ator=gestor)
    session.commit()
    assert r["status_vivo"] == "CONFIRMADO" and r["cnet"] > 0
    session.refresh(p)
    assert p.exw_cotado_usd == aprox(12.5) and str(p.exw_cotado_data) == "2026-09-22"
    # o CNET não foi digitado: saiu do motor de nacionalização
    custo, mem = ps.custo_para_precificar(session, p)
    assert r["cnet"] == aprox(custo) and mem["ii_pct"] == 0
    ref = cs.referencia_vigente(session, p.id)
    assert ref.status_custo == "CONFIRMADO" and ref.valor_bruto == aprox(12.5) and ref.moeda == "USD"
    assert gov.diagnosticar(session, p).situacao == "DISPONIVEL"


def test_5b_custo_nacional_pelo_bruto_usa_a_regra_de_creditos(session, fornecedores, gestor):
    p = produto_nacional(session, fornecedores, codigo="DECOR_TRICOT", custo=0.0)
    p.custo_unitario = None
    session.add(p); session.commit()
    r = gov.registrar_custo_nacional(session, p, valor="100", base="bruto",
                                     data_ref=date(2026, 9, 22), documento="Orçamento Decor",
                                     fonte="Decor Tricot", motivo="custo de compra recebido",
                                     ator=gestor)
    session.commit()
    # Decor: só crédito de PIS/COFINS (9,25%) → 90,75
    assert r["cnet"] == aprox(90.75, abs=0.01) and r["status_vivo"] == "CONFIRMADO"
    with pytest.raises(gov.AcaoInvalida):      # KTC não entra por aqui
        gov.registrar_custo_nacional(session, produto_ktc_cotado(session, fornecedores),
                                     valor="10", base="net", data_ref=date(2026, 9, 22),
                                     documento="x", fonte="y", motivo="z", ator=gestor)


def test_6_confirmar_referencia_existente_sem_redigitar(session, fornecedores, gestor):
    p = _roupao(session, fornecedores, peso_kg=1.9)
    linha = gov.diagnosticar(session, p)
    assert linha.pode_confirmar and linha.status in ("CONFIRMADO", "REVALIDAR")
    r = gov.confirmar_referencia(session, p, fonte="reconferido com a PI de 23/08/2026",
                                 motivo="cliente vai fechar esta semana", ator=gestor)
    session.commit()
    ref = cs.referencia_vigente(session, p.id)
    assert ref.versao == r["versao"] and ref.status_custo == "CONFIRMADO"
    assert ref.cnet_brl == aprox(linha.custo)          # não redigitou o número
    assert json.loads(ref.memoria_calculo).get("reconfirmacao") is True
    session.refresh(p)
    assert p.precisa_revisao is False


def test_7_nova_versao_nao_destroi_a_anterior_nem_toca_em_emitida(session, fornecedores, gestor):
    from app.models import Cotacao
    p = _roupao(session, fornecedores, peso_kg=1.9)
    owner = _novo_usuario("OWNER"); owner.can_approve_quotes = True
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    snap = ws.emitir(session, session.get(Cotacao, cot.id), ator=owner)
    session.commit()
    emitido = json.loads(snap.itens_json)[0]
    v1 = gov.registrar_exw_ktc(session, p, exw_usd="30.00", data_ref=date(2026, 9, 22),
                               documento="KTC Quotation 22/09", fonte="KTC", motivo="reajuste",
                               ator=gestor)
    session.commit()
    v2 = gov.marcar_status(session, p, status="REVALIDAR", motivo="conferir com a fábrica",
                           fonte="decisão do administrativo", ator=gestor)
    session.commit()
    versoes = cs.versoes(session, p.id)
    assert len(versoes) >= 2 and v2["versao"] > v1["versao"]
    assert all(v.valid_to is not None or v.vigente for v in versoes)   # nenhuma sumiu
    assert cs.referencia_vigente(session, p.id).status_custo == "REVALIDAR"
    # o documento emitido continua exatamente o mesmo
    session.expire_all()
    assert json.loads(snap.itens_json)[0] == emitido
    it_atual = session.get(CotacaoItem, it.id)
    assert it_atual.preco_negociado == aprox(emitido["preco_negociado"])


def test_7b_a_cotar_limpa_o_preco_e_bloqueia_de_novo(session, fornecedores, gestor):
    p = _roupao(session, fornecedores, peso_kg=1.9)
    assert gov.diagnosticar(session, p).status == "CONFIRMADO"
    gov.marcar_status(session, p, status="A_COTAR", motivo="fábrica retirou o modelo",
                      fonte="e-mail KTC", ator=gestor)
    session.commit()
    session.refresh(p)
    assert p.custo_unitario is None and p.preco_base is None
    assert gov.diagnosticar(session, p).status == "A_COTAR"
    assert cs.referencia_vigente(session, p.id).status_custo == "A_COTAR"


def test_motivo_e_fonte_sao_obrigatorios(session, fornecedores, gestor):
    p = _roupao(session, fornecedores)
    for chamada in (
        lambda: gov.registrar_peso(session, p, peso_kg="1.5", tipo="REAL KTC", fonte="",
                                   documento=None, data_ref=None, motivo="m", ator=gestor),
        lambda: gov.registrar_peso(session, p, peso_kg="1.5", tipo="REAL KTC", fonte="f",
                                   documento=None, data_ref=None, motivo="", ator=gestor),
        lambda: gov.registrar_peso(session, p, peso_kg="-1", tipo="REAL KTC", fonte="f",
                                   documento=None, data_ref=None, motivo="m", ator=gestor),
        lambda: gov.registrar_exw_ktc(session, p, exw_usd="10", data_ref=date(2026, 9, 22),
                                      documento="", fonte="f", motivo="m", ator=gestor),
        lambda: gov.marcar_status(session, p, status="CONFIRMADO", motivo="m", fonte="f", ator=gestor),
    ):
        with pytest.raises(gov.AcaoInvalida):
            chamada()


# ===========================================================================
# 9–10. Confidencialidade: isto é OWNER/ADMIN
# ===========================================================================
@pytest.mark.parametrize("papel", ("VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"))
def test_9_10_vendedora_nao_acessa_a_governanca_de_custo(session, fornecedores, papel):
    from app.routers.admin import (gov_confirmar, gov_cotacao_ktc, gov_custo_nacional, gov_peso,
                                   gov_status, produto_governanca, produtos_governanca)
    p = _roupao(session, fornecedores)
    vend = _novo_usuario(papel)
    for rota, kw in ((produtos_governanca, {}), (produto_governanca, {"produto_id": p.id}),
                     (gov_peso, {"produto_id": p.id, "peso_kg": "1", "fonte": "x", "motivo": "y"}),
                     (gov_cotacao_ktc, {"produto_id": p.id, "exw_usd": "1", "data_ref": "2026-09-22",
                                        "documento": "d", "fonte": "f", "motivo": "m"}),
                     (gov_custo_nacional, {"produto_id": p.id, "valor": "1", "data_ref": "2026-09-22",
                                           "fonte": "f", "motivo": "m"}),
                     (gov_confirmar, {"produto_id": p.id, "fonte": "f", "motivo": "m"}),
                     (gov_status, {"produto_id": p.id, "status": "A_COTAR", "motivo": "m"})):
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(vend), session=session, **kw)
        assert erro.value.status_code == 403, rota.__name__
    # e o ADMIN sem gestão econômica lê, mas não altera
    adm_sem = _novo_usuario("ADMIN"); adm_sem.can_manage_economics = False
    assert chamar(produtos_governanca, RequestFalsa(adm_sem), session=session).status_code == 200
    with pytest.raises(HTTPException) as erro:
        chamar(gov_peso, RequestFalsa(adm_sem), produto_id=p.id, peso_kg="1", fonte="x",
               motivo="y", session=session)
    assert erro.value.status_code == 403


def test_tela_de_governanca_mostra_o_diagnostico(session, fornecedores):
    from app.routers.admin import produtos_governanca
    p = _roupao(session, fornecedores)
    r = chamar(produtos_governanca, RequestFalsa(_novo_usuario("OWNER")), q=p.sku_key, session=session)
    html = bytes(r.body).decode()
    assert r.status_code == 200 and p.sku_key in html
    assert "Revisão necessária" in html and "Falta o peso da peça" in html
    assert "US$ 24.00" in html and "29/07/2026" in html.replace("2026-07-29", "29/07/2026")

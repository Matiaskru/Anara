"""I.I. econômico KTC = 0% · proteção comercial de precificação (22/09/2026).

Duas grandezas que o sistema separa e este arquivo prova separadas:

* **CNET REAL** — EXW + frete + outras despesas (I.I. 0%) × câmbio: forma lucro, margem
  realizada, dashboard e relatórios;
* **REFERÊNCIA COMERCIAL** — o mesmo waterfall com a PROTEÇÃO COMERCIAL do SKU (a alíquota
  preferencial que formava o custo até então) no lugar do imposto: forma B2B, tabela e
  preco_base — exatamente onde estavam. Não é custo, tributo nem despesa.
"""
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from app import comercial_service as com
from app import dados_2026_09_21 as dados
from app import metrics_service as mx
from app import pricing_service as ps
from app import workflow as wf
from app import workflow_service as ws
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D, ZERO, dinheiro
from app.models import Cotacao, CotacaoItem, Fornecedor, NcmRegra, ParametroKTC, Produto
from app.nationalization import nacionalizar, referencia_comercial
from app.pricing_engine import calcular_por_margem, calcular_por_preco, preco_b2b, preco_de_tabela
from conftest import RequestFalsa, _novo_usuario
from decimais import MARGEM_DO_CENTAVO, aprox
from tests.crisis.conftest import (add_item, chamar, editar_quantidade, nova_cotacao, produto_ktc_cotado,
                                   salvar_cabecalho)

X = Decimal


@pytest.fixture
def fornecedores(session):
    return {f.codigo: f for f in session.exec(select(Fornecedor)).all()}


@pytest.fixture
def admin():
    u = _novo_usuario("ADMIN"); u.can_approve_quotes = True; return u


@pytest.fixture
def owner():
    u = _novo_usuario("OWNER"); u.can_approve_quotes = True; return u


def _cen(**kw):
    base = dict(cliente_id=1, uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=False,
                finalidade="USO_CONSUMO", condicao_pagamento="30", freight_type="FOB", percentual_sinal=0.0)
    base.update(kw)
    return Cotacao(**base)


def _custo_antigo(session, p, mem):
    """O CNET que o SKU tinha ATÉ 22/09/2026: mesma nacionalização, com a proteção como I.I."""
    prem = ps.premissas_nacionalizacao(session)
    prot = D(mem["referencia_comercial"]["protecao_pct"])
    return nacionalizar(D(mem["exw_usd"]), D(mem["nacionalizacao"]["peso_kg"] if "peso_kg" in mem["nacionalizacao"] else p.peso_kg),
                        prot, prem).net_brl


# ===========================================================================
# 1. I.I. econômico = 0 em todo caminho KTC; a regra de NCM vigente diz o mesmo
# ===========================================================================
def test_ii_economico_ktc_e_zero_e_ncm_vigente_concorda(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=10.0, peso_kg=0.8)
    custo, mem = ps.custo_para_precificar(session, p)
    assert mem["ii_pct"] == 0 and mem["nacionalizacao"]["ii_usd"] == 0
    assert mem["ii_regra"] == ps.II_ECONOMICO_KTC_REGRA
    assert "ii" not in (mem.get("premissas_faltantes") or [])
    prem = ps.premissas_nacionalizacao(session)
    esperado = (D("10.0") + D("0.8") * prem.frete_usd_kg + prem.outras_desp_usd_un) * prem.fx_usd_brl
    assert D(custo) == aprox(esperado, abs=1e-9)
    # toda regra de NCM vigente hoje carrega I.I. 0%
    vigentes = [r for r in session.exec(select(NcmRegra)).all() if r.ativo and ps._vigente_hoje(r)]
    assert vigentes and all((r.ii_preferencial or 0) == 0 for r in vigentes)


def test_nenhuma_aliquota_positiva_de_ii_no_custo_de_nenhuma_familia(session, fornecedores):
    for familia in ("Flat Sheet", "Top Sheet", "Pillow Case", "Duvet Cover", "Bath Towel", "Bathrobe",
                    "Mattress Protector", "Blanket", "Duvet Insert", "Face Towel"):
        p = produto_ktc_cotado(session, fornecedores, familia=familia, thread_count=(300 if "Sheet" in familia else None))
        custo, mem = ps.custo_para_precificar(session, p)
        assert mem["nacionalizacao"]["ii_usd"] == 0 and mem["ii_pct"] == 0, familia


# ===========================================================================
# 2. Proteção comercial: por SKU (pino) e por família — a alíquota legada de cada um
# ===========================================================================
@pytest.mark.parametrize("familia, tc, esperado", [
    ("Flat Sheet", 250, "0.035"), ("Flat Sheet", 300, "0.035"), ("Flat Sheet", 400, "0.035"),
    ("Bath Towel", None, "0.035"), ("Bathrobe", None, "0.035"), ("Pillow Case", 300, "0.035"),
    ("Duvet Cover", 300, "0.035"), ("Mattress Protector", None, "0.0162"), ("Mattress Topper", None, "0.0162"),
    ("Blanket", None, "0"), ("Duvet Insert", None, "0"), ("Face Towel", None, "0"),
])
def test_protecao_comercial_por_familia_e_a_aliquota_legada(session, fornecedores, familia, tc, esperado):
    p = produto_ktc_cotado(session, fornecedores, familia=familia, thread_count=tc)
    pct, fonte = ps.protecao_comercial_do_produto(session, p)
    assert pct == X(esperado) and familia in fonte


def test_pino_do_sku_vence_a_regra_da_familia(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=10.0, peso_kg=0.8)     # Flat Sheet: família 3,5%
    p.protecao_comercial_pct = 0.0162
    p.protecao_comercial_fonte = "pino de teste"
    session.add(p); session.commit()
    pct, fonte = ps.protecao_comercial_do_produto(session, p)
    assert pct == X("0.0162") and fonte == "pino de teste"
    custo, mem = ps.custo_para_precificar(session, p)
    prem = ps.premissas_nacionalizacao(session)
    ref = referencia_comercial(D("10.0"), D("0.8"), X("0.0162"), prem)
    assert D(mem["base_comercial_brl"]) == aprox(ref.brl, abs=1e-9)
    assert D(custo) < D(mem["base_comercial_brl"])


def test_familia_sem_regra_fica_em_revisao_e_nao_recebe_protecao_inventada(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, familia="Família Sem Regra", thread_count=None)
    custo, mem = ps.custo_para_precificar(session, p)
    assert custo and mem["base_comercial_brl"] is None
    assert ps.PROTECAO_COMERCIAL_FALTANTE in mem["premissas_faltantes"]
    assert ps.status_canonico_do_custo(custo, mem) == "REVIEW_REQUIRED"
    c, base, _m = ps.bases_de_preco(session, p)
    assert base == c


# ===========================================================================
# 3. Paridade: B2B comercial = B2B do custo antigo (exato); economia real melhor
# ===========================================================================
@pytest.mark.parametrize("familia, tc, margem", [
    ("Flat Sheet", 250, "0.20"), ("Flat Sheet", 300, "0.22"), ("Flat Sheet", 400, "0.23"),
    ("Bath Towel", None, "0.16"), ("Bathrobe", None, "0.14"), ("Pillow Case", 300, "0.19"),
    ("Duvet Cover", 300, "0.19"), ("Mattress Protector", None, "0.19"),
])
@pytest.mark.parametrize("cenario", [
    dict(), dict(condicao_pagamento="30/60"), dict(condicao_pagamento="30/60/90"),
    dict(estado_destino="Minas Gerais", contribuinte_icms=True, finalidade="REVENDA"),
    dict(estado_destino="Minas Gerais"), dict(estado_destino="Rio de Janeiro"),
    dict(condicao_pagamento="30/60/90", percentual_sinal=0.30), dict(condicao_pagamento="30/60/90", percentual_sinal=0.50),
    dict(condicao_pagamento="30/60/90", percentual_sinal=1.0),
])
def test_b2b_comercial_identico_ao_antigo_e_margem_realizada_maior(session, fornecedores, familia, tc, margem, cenario):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1, familia=familia, thread_count=tc)
    custo, base, mem = ps.bases_de_preco(session, p)
    m = ps.margem_padrao(session, p)
    assert m.margem_pct == aprox(X(margem))
    regras, ctx = ps.regras_da_cotacao(session, _cen(**cenario), p)
    antigo = _custo_antigo(session, p, mem)
    assert D(base) == aprox(antigo, abs=1e-9) and D(custo) < antigo
    b2b_antes = preco_b2b(antigo, m.margem_pct, regras)              # o que o sistema formava até 22/09
    b2b_com = preco_b2b(base, m.margem_pct, regras)
    b2b_eco = preco_b2b(custo, m.margem_pct, regras)
    assert b2b_com.preco_negociado == b2b_antes.preco_negociado        # nem R$ 0,01
    assert preco_de_tabela(b2b_com.preco_negociado, 2) == preco_de_tabela(b2b_antes.preco_negociado, 2)
    assert b2b_eco.preco_negociado <= b2b_com.preco_negociado
    # mesmo preço, custo real menor → lucro e margem realizada maiores que os de antes
    antes = calcular_por_preco(antigo, 1, b2b_com.preco_negociado, regras)
    depois = calcular_por_preco(custo, 1, b2b_com.preco_negociado, regras)
    assert depois.lucro > antes.lucro and depois.margem_liquida > antes.margem_liquida
    assert depois.margem_liquida >= m.margem_pct
    assert depois.faturamento == antes.faturamento and depois.comissao == antes.comissao and depois.impostos == antes.impostos


def test_familia_com_protecao_zero_tem_economia_identica_a_de_antes(session, fornecedores):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=20.86, peso_kg=4.65, familia="Blanket", thread_count=None)
    custo, base, mem = ps.bases_de_preco(session, p)
    assert base == custo and mem["referencia_comercial"]["protecao_pct"] == 0
    regras, _ = ps.regras_da_cotacao(session, _cen(), p)
    m = ps.margem_padrao(session, p)
    assert preco_b2b(base, m.margem_pct, regras).preco_negociado == preco_b2b(custo, m.margem_pct, regras).preco_negociado


# ===========================================================================
# 4. Item de cotação: custo real, base comercial, B2B comercial, tabela, desconto, comissão
# ===========================================================================
def test_item_nasce_com_custo_real_e_preco_sobre_a_base_comercial(session, fornecedores, admin):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    custo, base, mem = ps.bases_de_preco(session, p)
    assert it.custo_unitario == aprox(custo) and it.base_comercial_precificacao == aprox(base)
    assert it.protecao_comercial_pct == aprox(0.035)
    regras, ctx = ps.regras_da_cotacao(session, session.get(Cotacao, cot.id), p)
    m = ps.margem_padrao(session, p)
    assert D(it.preco_recomendado) == preco_b2b(base, m.margem_pct, regras).preco_negociado
    assert D(it.preco_b2b_economico) == preco_b2b(custo, m.margem_pct, regras).preco_negociado
    assert it.preco_b2b_economico < it.preco_recomendado
    assert D(it.preco_tabela) == preco_de_tabela(D(it.preco_recomendado), 2)
    assert it.desconto_vs_tabela_pct == aprox(0.5, abs=1e-3) and it.comissao_faixa_pct == aprox(0.05)
    # lucro e margem do item são os do custo REAL no preço comercial
    real = calcular_por_preco(custo, it.quantidade, D(it.preco_negociado), regras)
    assert it.lucro == aprox(float(real.lucro)) and it.margem_liquida == aprox(float(real.margem_liquida))
    assert D(it.margem_liquida) > m.margem_pct
    # memória: as duas grandezas, nomeadas
    memo = json.loads(it.memoria_json)
    assert memo["custo"]["ii_pct"] == 0 and memo["custo"]["base_comercial_brl"] == aprox(base)
    assert memo["b2b"]["preco_b2b_economico"] < memo["b2b"]["preco_b2b"]
    assert "não é custo" in memo["custo"]["referencia_comercial"]["natureza"]


def test_desconto_comissao_e_repricing_usam_a_base_comercial(session, fornecedores, admin):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30/60/90")
    it = add_item(session, cot, p)
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.20"}}, ator=admin)
    session.commit(); session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert it.comissao_faixa_pct == aprox(0.08) and it.desconto_vs_tabela_pct == aprox(0.20, abs=1e-3)
    custo_antes, base_antes = it.custo_unitario, it.base_comercial_precificacao
    # gatilhos de repricing: UF, contribuinte, pagamento, sinal, quantidade — a base comercial segue
    for muda in (dict(estado_destino="Minas Gerais"), dict(contribuinte_icms="sim"), dict(condicao_pagamento="30/60"),
                 dict(possui_sinal="sim", percentual_sinal="30")):
        salvar_cabecalho(session, cot, **muda)
        session.expire_all(); it = session.get(CotacaoItem, it.id)
        regras, ctx = ps.regras_da_cotacao(session, session.get(Cotacao, cot.id), p)
        assert D(it.preco_recomendado) == preco_b2b(base_antes, it.margem_padrao_pct, regras).preco_negociado
        assert it.custo_unitario == custo_antes and it.base_comercial_precificacao == base_antes
        assert it.desconto_editado_pct == aprox(0.20) and it.comissao_faixa_pct == aprox(0.08)
        assert D(it.margem_liquida) >= D(it.margem_padrao_pct)
    editar_quantidade(session, cot, it, 37)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert it.quantidade == 37 and it.base_comercial_precificacao == base_antes


def test_atualizar_premissas_revisao_e_duplicacao_preservam_preco_e_melhoram_economia(session, fornecedores, admin, owner):
    from app.routers.cotacoes import atualizar_premissas, duplicar
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    # simula um rascunho anterior a 22/09: custo com I.I. embutido e sem pino de base comercial
    custo_real, base, mem = ps.bases_de_preco(session, p)
    it.custo_unitario = base; it.base_comercial_precificacao = None; it.protecao_comercial_pct = None
    session.add(it); session.commit()
    preco_antes, rec_antes, tab_antes = it.preco_negociado, it.preco_recomendado, it.preco_tabela
    chamar(atualizar_premissas, RequestFalsa(admin), cotacao_id=cot.id, session=session)
    session.commit(); session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert (it.preco_negociado, it.preco_recomendado, it.preco_tabela) == (preco_antes, rec_antes, tab_antes)
    assert it.custo_unitario == aprox(custo_real) and it.base_comercial_precificacao == aprox(base)
    assert D(it.margem_liquida) > D(it.margem_padrao_pct)
    # revisão herda os pinos; duplicata reprecifica com as mesmas bases → mesmos preços
    c = session.get(Cotacao, cot.id)
    ws.emitir(session, c, ator=owner); session.commit()
    rev = ws.criar_revisao(session, c, ator=owner); session.commit()
    it_rev = ws.itens_de(session, rev.id)[0]
    assert it_rev.base_comercial_precificacao == aprox(base) and it_rev.preco_negociado == preco_antes
    r = chamar(duplicar, RequestFalsa(admin), cotacao_id=cot.id, session=session); session.commit()
    nova = ws.itens_de(session, int(r.headers["location"].rsplit("/", 1)[-1]))[0]
    assert nova.preco_negociado == preco_antes and nova.base_comercial_precificacao == aprox(base)
    assert nova.custo_unitario == aprox(custo_real)


# ===========================================================================
# 5. Piso comercial × margem econômica; fingerprint; snapshot
# ===========================================================================
def test_preco_entre_b2b_economico_e_comercial_cumpre_margem_mas_requer_aprovacao(session, fornecedores, admin):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert it.preco_b2b_economico < it.preco_recomendado
    meio = dinheiro((D(it.preco_b2b_economico) + D(it.preco_recomendado)) / 2)
    av = com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"preco": str(meio)}}, ator=admin)
    session.commit(); session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert D(it.margem_liquida) >= D(it.margem_padrao_pct) - MARGEM_DO_CENTAVO      # economicamente ok
    assert av.requer_aprovacao and any(e.motivo == "PRECO_ABAIXO_B2B" for a in av.itens for e in a.excecoes)
    assert ws.avaliar(session, session.get(Cotacao, cot.id)).precisa_aprovacao          # piso é comercial


def test_fingerprint_muda_com_a_base_comercial_e_aprovacao_cai(session, fornecedores, admin, owner):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    com.aplicar_negociacao(session, session.get(Cotacao, cot.id), {it.id: {"desconto": "0.55"}}, ator=admin)
    session.commit()
    c = session.get(Cotacao, cot.id)
    pedido = ws.solicitar_aprovacao(session, c, ator=admin, justificativa="x"); session.commit()
    ws.decidir(session, c, pedido.id, ator=owner, aprovar=True, comentario="ok"); session.commit()
    assert ws.avaliar(session, c).aprovacao_valida
    fp = wf.fingerprint(c, ws.itens_de(session, c.id))
    # a referência comercial do SKU muda (pino) e o rascunho é reprecificado explicitamente
    p.protecao_comercial_pct = 0.0; session.add(p); session.commit()
    from app.routers.cotacoes import atualizar_premissas
    chamar(atualizar_premissas, RequestFalsa(admin), cotacao_id=cot.id, session=session); session.commit()
    session.expire_all(); c = session.get(Cotacao, cot.id)
    assert wf.fingerprint(c, ws.itens_de(session, c.id)) != fp
    assert ws.avaliar(session, c).aprovacao_valida is False
    assert "base_comercial_precificacao" in wf.CAMPOS_MATERIAIS_ITEM_2026_09_21


def test_snapshot_novo_registra_economia_real_e_formacao_comercial(session, fornecedores, owner):
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    snap = ws.emitir(session, session.get(Cotacao, cot.id), ator=owner); session.commit()
    linha = json.loads(snap.itens_json)[0]
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    for campo in ("custo_unitario", "lucro", "margem_liquida", "margem_padrao_pct", "preco_recomendado", "preco_tabela",
                  "desconto_vs_tabela_pct", "comissao_pct", "comissao_valor", "base_comercial_precificacao",
                  "protecao_comercial_pct", "preco_b2b_economico"):
        assert linha[campo] == aprox(getattr(it, campo)), campo
    fiscal = json.loads(snap.fiscal_json)
    assert fiscal["ii_economico_ktc_pct"] == 0 and fiscal["protecao_comercial_versao"] == "2026-09-22"
    memo = json.loads(snap.memoria_json)[0]
    memo = json.loads(memo) if isinstance(memo, str) else memo
    assert memo["custo"]["ii_pct"] == 0


# ===========================================================================
# 6. Dashboard: mesma venda, mesmo vendido/ticket/comissão — lucro e margem maiores
# ===========================================================================
def test_dashboard_usa_o_custo_real_no_lucro_sem_mudar_a_receita(session, fornecedores, admin, owner):
    from app import crm_service as crm
    from app.routers.cotacoes import atualizar_premissas, criar_cotacao_da_venda
    from app.models import Usuario
    # usuário persistido próprio deste teste (outros módulos já podem ter gravado o id 1)
    dona = session.exec(select(Usuario).where(Usuario.email == "dona.ii.zero@anara.test")).first()
    if dona is None:
        dona = Usuario(email="dona.ii.zero@anara.test", nome="Dona II zero", senha_hash="x", papel="OWNER",
                       ativo=True, can_approve_quotes=True, sessao_versao=1)
        session.add(dona); session.commit(); session.refresh(dona)
    owner = dona
    cliente = crm.criar_cliente(session, ator=owner, nome="Hotel II Zero", cidade_uf="São Paulo", finalidade="REVENDA",
                                cnpj_cpf="77.777.777/0001-77"); session.commit()
    op = crm.criar_oportunidade(session, ator=owner, cliente_id=cliente.id, titulo="Venda II zero", responsavel_id=owner.id)
    session.commit()
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = criar_cotacao_da_venda(session, RequestFalsa(owner), op, ator=owner, estado_destino="São Paulo",
                                 contribuinte_icms=False, freight_type="FOB", condicao_pagamento="30")
    cot.uf_origem_fiscal = "SP"; session.add(cot); session.commit()
    it = add_item(session, cot, p, quantidade=10)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    custo_real, base, _m = ps.bases_de_preco(session, p)
    # "ANTES": a economia que a venda teria com o custo antigo (I.I. embutido), mesmo preço
    regras, _ctx = ps.regras_da_cotacao(session, session.get(Cotacao, cot.id), p)
    antes = calcular_por_preco(D(base), 10, D(it.preco_negociado), regras)
    it.custo_unitario = base; it.lucro = float(antes.lucro); it.margem_liquida = float(antes.margem_liquida)
    it.custo_total = float(antes.custo_total); it.base_comercial_precificacao = None; it.protecao_comercial_pct = None
    session.add(it); session.commit()
    ws.emitir(session, session.get(Cotacao, cot.id), ator=owner); session.commit()
    crm.marcar_ganha(session, op, cot.id, ator=owner); session.commit()
    janela = mx.periodo_de("12m")
    kpi_antes = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id))
    pv_antes = mx.painel_vendas(session, janela)
    # "DEPOIS": a mesma venda reprecificada com o custo real (revisão da cotação, mesmo preço)
    c = session.get(Cotacao, cot.id)
    rev = ws.criar_revisao(session, c, ator=owner); session.commit()
    chamar(atualizar_premissas, RequestFalsa(admin), cotacao_id=rev.id, session=session); session.commit()
    ws.emitir(session, session.get(Cotacao, rev.id), ator=owner); session.commit()
    op.cotacao_vencedora_id = rev.id; session.add(op); session.commit()   # a venda passa a apontar para a revisão
    session.expire_all()
    it_rev = ws.itens_de(session, rev.id)[0]
    assert it_rev.preco_negociado == it.preco_negociado and it_rev.custo_unitario == aprox(custo_real)
    kpi_depois = mx.dashboard_admin(session, janela, mx.FiltrosDashboard(cliente_id=cliente.id))
    assert kpi_depois["valor_vendido"] == kpi_antes["valor_vendido"]
    assert kpi_depois["vendas_fechadas"] == kpi_antes["vendas_fechadas"]
    assert kpi_depois["ticket_medio"] == kpi_antes["ticket_medio"]
    assert kpi_depois["comissao_estimada"] == aprox(kpi_antes["comissao_estimada"])
    assert kpi_depois["desconto_medio"] == aprox(kpi_antes["desconto_medio"], abs=1e-9) if "desconto_medio" in kpi_depois else True
    assert kpi_depois["lucro"] > kpi_antes["lucro"] and kpi_depois["margem_agregada"] > kpi_antes["margem_agregada"]
    serie = mx.serie_mensal(session, meses=1, filtros=mx.FiltrosDashboard(cliente_id=cliente.id))
    assert serie["total_lucro"] == aprox(kpi_depois["lucro"])


# ===========================================================================
# 7. Calculadora × catálogo × cotação concordam; sem arbitragem no customizado
# ===========================================================================
def test_calculadora_catalogo_e_cotacao_concordam_no_b2b_comercial(session, fornecedores, admin):
    from app import calculadora as calc
    from app.models import MaterialPreco
    m = session.exec(select(MaterialPreco).where(MaterialPreco.material == "300TC Sateen 100% Cotton")
                     .where(MaterialPreco.plain_or_stripe == "plain")).first()
    r = calc.calcular(session, "Flat Sheet", 240, 260, material_id=m.id)
    assert r["custo"]["ii_pct"] == 0 and r["custo"]["base_comercial_brl"] > r["custo"]["net_brl"]
    salvo = calc.salvar_no_catalogo(session, "Flat Sheet", 240, 260, material_id=m.id)
    assert salvo.preco_base == aprox(r["b2b"]["preco_b2b"])
    assert salvo.custo_unitario == aprox(r["custo"]["net_brl"])                 # custo real no catálogo
    cot = nova_cotacao(session, condicao_pagamento="30")           # cenário padrão do catálogo
    it = add_item(session, cot, salvo)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert it.preco_recomendado == aprox(r["b2b"]["preco_b2b"]) and it.custo_unitario == aprox(r["custo"]["net_brl"])
    # a mesma especificação recalculada "como customizado" dá o mesmo B2B do SKU salvo
    r2 = calc.calcular(session, "Flat Sheet", 240, 260, material_id=m.id)
    assert r2["b2b"]["preco_b2b"] == r["b2b"]["preco_b2b"] == aprox(salvo.preco_base)


# ===========================================================================
# 8. Confidencialidade: a vendedora não vê custo real, base comercial, proteção nem B2B econômico
# ===========================================================================
def test_vendedora_nao_ve_economia_real_nem_formacao_comercial(session, fornecedores, admin):
    from app.routers.cotacoes import _item_para_json, calc
    from app.routers.negociacao import negociacao_atual
    vend = _novo_usuario("VENDEDOR_COMISSIONADO")
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    it = add_item(session, cot, p)
    corpo = json.loads(bytes(negociacao_atual(RequestFalsa(vend), cot.id, session).body))
    assert encontrar_confidenciais(corpo) == []
    texto = json.dumps(corpo).lower()
    for t in ("custo", "base_comercial", "protecao", "b2b_economico", "ii_", "lucro", "margem"):
        assert t not in texto, t
    item_json = _item_para_json(session.get(CotacaoItem, it.id), pode_ver_economia=False)
    assert not ({"custo_unitario", "base_comercial_precificacao", "protecao_comercial_pct", "preco_b2b_economico"} & set(item_json))
    assert item_json["preco_b2b"] == aprox(session.get(CotacaoItem, it.id).preco_recomendado)
    r = chamar(calc, RequestFalsa(vend), cotacao_id=cot.id, produto_id=p.id, quantidade=10.0, modo="margem", valor=0, session=session)
    previa = json.loads(bytes(r.body))
    assert encontrar_confidenciais(previa) == [] and "base_comercial" not in json.dumps(previa)
    adm_json = json.loads(bytes(negociacao_atual(RequestFalsa(admin), cot.id, session).body))
    eco = adm_json["economia"]["itens"][0]
    assert eco["base_comercial_precificacao"] > eco["custo_unitario"] and eco["preco_b2b_economico"] < corpo["itens"][0]["preco_b2b"]


# ===========================================================================
# 9. Script de dados: pino por SKU, regra por família, NCM versionada — idempotente
# ===========================================================================
def test_ii_zero_pina_aliquota_legada_versiona_ncm_e_e_idempotente(session, fornecedores, owner):
    hoje = date.today()
    legada = NcmRegra(familia="Família Legada Teste", ncm="6302.99.99", ii_original=0.35, reducao_preferencial=0.90,
                      ii_preferencial=0.035, prioridade=50, valid_from=date(2026, 1, 1), ativo=True, confiavel=True)
    session.add(legada); session.commit()
    p = produto_ktc_cotado(session, fornecedores, familia="Família Legada Teste", thread_count=None, exw_usd=8.0, peso_kg=0.5)
    q = produto_ktc_cotado(session, fornecedores, familia="Mattress Topper", thread_count=None, exw_usd=8.0, peso_kg=0.5)
    r = dados.aplicar_ii_zero(session, owner); session.commit()
    assert r["pinados"] >= 2 and r["ncm_encerradas"] >= 1 and r["ncm_criadas"] >= 1
    session.refresh(p); session.refresh(q); session.refresh(legada)
    assert p.protecao_comercial_pct == aprox(0.035) and "até 22/09/2026" in p.protecao_comercial_fonte
    assert "não é tributo" in p.protecao_comercial_fonte
    assert q.protecao_comercial_pct == aprox(0.0162)
    assert legada.valid_to == date(2026, 9, 22) and legada.ii_preferencial == aprox(0.035)      # histórico intacto
    sucessora = [x for x in session.exec(select(NcmRegra).where(NcmRegra.familia == "Família Legada Teste")).all()
                 if x.valid_from == date(2026, 9, 22)]
    assert len(sucessora) == 1 and sucessora[0].ii_preferencial == 0.0
    assert ps.regra_ncm(session, p).ii_preferencial == 0.0
    fam = session.exec(select(ParametroKTC).where(ParametroKTC.chave == "protecao_comercial_pct")
                       .where(ParametroKTC.escopo == "Família Legada Teste")).first()
    assert fam is not None and fam.valor == aprox(0.035)
    # 2ª aplicação: nada a fazer
    r2 = dados.aplicar_ii_zero(session, owner); session.commit()
    assert (r2["pinados"], r2["familias"], r2["ncm_encerradas"], r2["ncm_criadas"]) == (0, 0, 0, 0)
    # e o produto pinado precifica pela mesma referência de antes
    custo, base, mem = ps.bases_de_preco(session, p)
    prem = ps.premissas_nacionalizacao(session)
    assert D(base) == aprox(nacionalizar(D("8.0"), D("0.5"), X("0.035"), prem).net_brl, abs=1e-9)
    assert D(custo) == aprox(nacionalizar(D("8.0"), D("0.5"), ZERO, prem).net_brl, abs=1e-9)


# ===========================================================================
# 10. BL-001 / BL-002 / BL-003 e políticas anteriores
# ===========================================================================
def test_blankets_da_cotacao_de_29_07(session, fornecedores, owner):
    dados.aplicar_ktc(session, owner); dados.aplicar_bl001(session, owner); dados.aplicar_ii_zero(session, owner)
    session.commit()
    bl = {}
    for p in session.exec(select(Produto)).all():
        for cod in ("BL-001", "BL-002", "BL-003"):
            if f"· {cod} ·" in (p.exw_cotado_fonte or ""):
                bl[cod] = p
    regras, ctx = ps.regras_da_cotacao(session, _cen(), bl["BL-001"])
    for cod, p in bl.items():
        custo, base, mem = ps.bases_de_preco(session, p)
        assert p.protecao_comercial_pct == 0.0 and base == custo and mem["ii_pct"] == 0, cod
        assert "ii" not in (mem.get("premissas_faltantes") or [])
    custo, base, mem = ps.bases_de_preco(session, bl["BL-001"])
    m = ps.margem_padrao(session, bl["BL-001"])
    b2b = preco_b2b(base, m.margem_pct, regras)
    assert ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO" and m.margem_pct == aprox(0.19)
    assert mem["nacionalizacao"]["frete_usd"] == aprox(2.40 * 0.516) and mem["exw_usd"] == aprox(10.71)
    assert b2b.margem_liquida >= X("0.19") and calcular_por_preco(custo, 1, b2b.preco_negociado - X("0.01"), regras).margem_liquida < X("0.19")


def test_politica_anterior_forma_recomendado_na_base_e_economia_no_custo_real(session, fornecedores):
    from app.routers.cotacoes import _calcular
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    custo, base, _m = ps.bases_de_preco(session, p)
    regras, _ = ps.regras_da_cotacao(session, _cen(), p, comissao_formacao_pct=0.05, politica="POLITICA_COMERCIAL_2026-09-16")
    r = _calcular("margem", custo, 3, 0.20, regras, contexto={"politica_comercial": "POLITICA_COMERCIAL_2026-09-16"},
                  margem_alvo=0.20, base_comercial=base)
    esperado = calcular_por_margem(base, 3, 0.20, regras)
    assert r.preco_negociado == esperado.preco_negociado
    assert r.lucro == calcular_por_preco(custo, 3, esperado.preco_negociado, regras).lucro > esperado.lucro


def test_previa_do_calc_e_o_item_salvo_tem_o_mesmo_b2b_comercial(session, fornecedores, admin):
    from app.routers.cotacoes import calc
    p = produto_ktc_cotado(session, fornecedores, exw_usd=12.5, peso_kg=1.1)
    cot = nova_cotacao(session, condicao_pagamento="30")
    r = chamar(calc, RequestFalsa(admin), cotacao_id=cot.id, produto_id=p.id, quantidade=10.0, modo="margem", valor=0, session=session)
    previa = json.loads(bytes(r.body))
    it = add_item(session, cot, p)
    session.expire_all(); it = session.get(CotacaoItem, it.id)
    assert previa["preco_b2b"] == aprox(it.preco_recomendado) and previa["preco_tabela"] == aprox(it.preco_tabela)
    assert previa["preco_negociado"] == aprox(it.preco_negociado)
    assert previa["preco_b2b"] > preco_b2b(D(it.custo_unitario), D(it.margem_padrao_pct),
                                           ps.regras_da_cotacao(session, session.get(Cotacao, cot.id), p)[0]).preco_negociado

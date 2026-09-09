"""Sessão 5 — administração de premissas, versionamento e impacto controlado.

A pergunta que esta suíte responde é sempre a mesma, e é a do §1 do escopo:

    o admin mexeu no SKU X. O que aconteceu com o SKU Y, com a família, com a
    cotação de ontem e com o rascunho de anteontem?

A resposta correta é **nada**. Cada teste aqui existe para que continuar sendo nada seja
obrigatório, e não sorte.

Os testes usam SKUs próprios, criados na hora, e nunca tocam nos 348 do catálogo real.
"""
import json
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import admin_service as adm
from app import config_service as cfg
from app import custo_service as cs
from app import pricing_service as ps
from app.dinheiro import D
from app.models import (
    AuditLog, CostMethod, Cotacao, CotacaoItem, Fornecedor, MargemRegra, Premissa, Produto,
    StatusCusto,
)
from app.pricing_engine import calcular_por_margem
from conftest import RequestFalsa, _novo_usuario

HOJE = date.today()
AMANHA = HOJE + timedelta(days=1)
ANO_QUE_VEM = HOJE + timedelta(days=365)


@pytest.fixture
def ator():
    return _novo_usuario("OWNER")


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def margens_isoladas(session):
    """Desfaz as regras de margem criadas pelo teste.

    A `session` da suíte é única e as margens canônicas (Daune 14%, KTC por família) são
    verificadas em `test_margens.py`. Uma regra de teste que sobrevivesse mudaria a margem
    resolvida lá — e o teste que quebraria seria o de outra pessoa, sem relação com o que
    este arquivo estava provando.
    """
    antes = {r.id for r in session.exec(select(MargemRegra)).all()}
    yield
    for r in session.exec(select(MargemRegra)).all():
        if r.id not in antes:
            session.delete(r)
    # e reabre a vigência das regras que o teste encerrou
    for r in session.exec(select(MargemRegra)).all():
        if r.id in antes and r.valid_to == HOJE:
            r.valid_to = None
            session.add(r)
    session.commit()


def novo_produto(session, fornecedor, sku, custo=100.0, familia="Flat Sheet"):
    p = Produto(sku_key=sku, nome=f"Produto {sku}", custo_unitario=custo, preco_base=200.0,
                fornecedor_id=fornecedor.id, familia=familia,
                cost_method=CostMethod.national_supplier.value)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def versionar(session, produto, cnet, *, fonte, status=StatusCusto.confirmado.value,
              documento=None, quando=None, ator=None):
    """Atalho preview→apply, que é o único caminho de escrita que o admin tem."""
    prop = adm.preview_custo_sku(session, produto.id, cnet_brl=cnet, status=status,
                                 fonte=fonte, documento=documento,
                                 vigente_a_partir_de=quando)
    return adm.aplicar_custo_sku(session, produto.id, prop,
                                 ator=ator or _novo_usuario("OWNER"), cnet_brl=cnet,
                                 status=status, fonte=fonte, documento=documento,
                                 vigente_a_partir_de=quando)


def preco_de(session, produto):
    """Preço recomendado que uma cotação NOVA formaria para este produto, agora."""
    cot = ps.cenario_padrao_catalogo(session)
    regras, _ctx = ps.regras_da_cotacao(session, cot, produto)
    margem = ps.margem_padrao(session, produto)
    return calcular_por_margem(cs.referencia_vigente(session, produto.id).cnet_brl,
                               1, margem.margem_pct, regras).preco_negociado


# ===========================================================================
# P0 — §45: atualizar um SKU não toca em nenhum outro
# ===========================================================================
def test_p0_update_de_sku_e_isolado(session, daune, ator):
    """X vai de 100 para 110. Y continua 120. É o teste central da sessão."""
    x = novo_produto(session, daune, "ISO-X", custo=100.0)
    y = novo_produto(session, daune, "ISO-Y", custo=120.0)
    versionar(session, x, "100.00", fonte="tabela inicial", ator=ator)
    versionar(session, y, "120.00", fonte="tabela inicial", ator=ator)
    session.commit()

    # retrato de Y antes — tudo o que não pode mudar
    y_antes = [(r.id, r.versao, r.cnet_brl, r.valid_from, r.valid_to, r.vigente)
               for r in cs.versoes(session, y.id)]
    y_custo_antes = y.custo_unitario
    outros_antes = {p.id: p.custo_unitario for p in session.exec(select(Produto)).all()
                    if p.id not in (x.id, y.id)}

    # --- a ação: só o X ---
    nova = versionar(session, x, "110.00", fonte="cotação nova de 05/09", ator=ator)
    session.commit()

    assert nova.versao == 2
    assert D(cs.referencia_vigente(session, x.id).cnet_brl) == D("110.00")
    # V1 do X continua consultável — não foi apagada nem editada
    v1 = [r for r in cs.versoes(session, x.id) if r.versao == 1][0]
    assert D(v1.cnet_brl) == D("100.00")
    assert v1.valid_to == HOJE

    # --- Y: nada, nem uma linha ---
    assert [(r.id, r.versao, r.cnet_brl, r.valid_from, r.valid_to, r.vigente)
            for r in cs.versoes(session, y.id)] == y_antes
    session.refresh(y)
    assert y.custo_unitario == y_custo_antes
    assert D(cs.referencia_vigente(session, y.id).cnet_brl) == D("120.00")

    # --- e nenhum outro SKU do catálogo ---
    outros_depois = {p.id: p.custo_unitario for p in session.exec(select(Produto)).all()
                     if p.id not in (x.id, y.id)}
    assert outros_depois == outros_antes

    # --- a trilha registra o X, e só o X ---
    linhas = adm.trilha(session, entidade="CustoReferencia", limite=10)
    assert linhas[0].escopo == "SKU ISO-X"
    assert linhas[0].resultado == "OK"
    assert linhas[0].ator_email == ator.email


def test_p0_nova_cotacao_usa_v2_e_a_antiga_continua_com_v1(session, daune, ator):
    """O ponto do §1: a cotação de ontem não muda; a de hoje usa o custo novo."""
    x = novo_produto(session, daune, "TEMPO-X", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", quando=HOJE - timedelta(days=30), ator=ator)
    session.commit()
    preco_com_v1 = preco_de(session, x)

    # a cotação "antiga": um item emitido com o custo da época
    item_antigo = CotacaoItem(cotacao_id=0, produto_id=x.id, nome_produto=x.nome,
                              quantidade=1, custo_unitario=100.0, preco_base=200.0,
                              preco_negociado=float(preco_com_v1), margem_liquida=0.14,
                              faturamento=float(preco_com_v1), custo_total=100.0, lucro=0.0)
    session.add(item_antigo)
    session.commit()
    congelado = (item_antigo.custo_unitario, item_antigo.preco_negociado)

    versionar(session, x, "110.00", fonte="cotação nova", ator=ator)
    session.commit()

    assert preco_de(session, x) > preco_com_v1          # cotação NOVA usa V2
    session.refresh(item_antigo)
    assert (item_antigo.custo_unitario, item_antigo.preco_negociado) == congelado
    # e a versão que formou aquele preço continua recuperável pela data
    assert D(cs.referencia_em(session, x.id, HOJE - timedelta(days=1)).cnet_brl) == D("100.00")


def test_reimportar_a_mesma_referencia_e_no_op(session, daune, ator):
    """§17: rodar duas vezes a mesma fonte não pode encher o histórico de versões."""
    x = novo_produto(session, daune, "NOOP-1", custo=100.0)
    versionar(session, x, "100.00", fonte="tabela A", documento="tabela-A.xlsx", ator=ator)
    session.commit()
    antes = len(cs.versoes(session, x.id))

    prop = adm.preview_custo_sku(session, x.id, cnet_brl="100.00",
                                 status=StatusCusto.confirmado.value, fonte="tabela A",
                                 documento="tabela-A.xlsx")
    assert prop.linhas[0].situacao == adm.NO_OP
    assert not prop.pode_aplicar

    resultado = adm.aplicar_custo_sku(session, x.id, prop, ator=ator, cnet_brl="100.00",
                                      status=StatusCusto.confirmado.value, fonte="tabela A",
                                      documento="tabela-A.xlsx")
    session.commit()
    assert resultado is None
    assert len(cs.versoes(session, x.id)) == antes
    assert adm.trilha(session, entidade="CustoReferencia", limite=1)[0].resultado == adm.NO_OP


def test_formatacao_diferente_nao_cria_versao(session, daune, ator):
    """§18: "110" e "110.00" são o mesmo número. Decimal resolve isso, não string."""
    x = novo_produto(session, daune, "FMT-1", custo=110.0)
    versionar(session, x, "110.00", fonte="f", documento="d", ator=ator)
    session.commit()
    prop = adm.preview_custo_sku(session, x.id, cnet_brl="110", status="CONFIRMADO",
                                 fonte="f", documento="d")
    assert prop.linhas[0].situacao == adm.NO_OP


# ===========================================================================
# P0 — §46: premissa global, com vigência futura
# ===========================================================================
def test_p0_premissa_global_com_vigencia_futura(session, ator):
    """FX novo agendado para o ano que vem não pode mudar o preço de hoje."""
    # O alcance do câmbio é o conjunto de produtos importados; sem nenhum no banco de teste
    # o número seria zero por ausência de dado, não por acerto. Um produto KTC garante que a
    # asserção de escopo esteja medindo algo.
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    novo_produto(session, ktc, "FX-ALCANCE", custo=50.0)
    cfg.definir(session, "fx_usd_brl", valor_num=5.11, fonte="baseline do teste")
    session.commit()
    assert D(cfg.num(session, "fx_usd_brl")) == D("5.11")

    prop = adm.preview_premissa(session, "fx_usd_brl", valor_num="5.20",
                                fonte="contrato de câmbio", vigente_a_partir_de=ANO_QUE_VEM)
    assert prop.pode_aplicar
    assert prop.skus_afetados > 0                       # o alcance é mostrado
    assert any("Vigência futura" in a for a in prop.avisos)

    adm.aplicar_premissa(session, "fx_usd_brl", prop, ator=ator, valor_num=5.20,
                         fonte="contrato de câmbio", vigente_a_partir_de=ANO_QUE_VEM)
    session.commit()

    # hoje: ainda 5,11
    assert D(cfg.num(session, "fx_usd_brl")) == D("5.11")
    # na vigência: 5,20
    assert D(cfg.num(session, "fx_usd_brl", ref=ANO_QUE_VEM)) == D("5.20")
    # e a versão antiga continua respondendo pelo passado
    assert D(cfg.num(session, "fx_usd_brl", ref=HOJE - timedelta(days=1))) == D("5.11")


def test_premissa_mostra_o_alcance_antes_de_aplicar(session):
    """§12: o admin não pode aplicar globalmente algo que pensava ser específico."""
    prop = adm.preview_premissa(session, "pis_cofins_pct", valor_num="0.0759",
                                fonte="conferência")
    escopo = adm.escopo_da_premissa(session, "pis_cofins_pct")
    assert escopo["skus"] > 0
    assert "TODOS" in escopo["texto"]
    assert prop.skus_afetados == escopo["skus"]


def test_premissa_com_valor_igual_e_no_op(session, ator):
    cfg.definir(session, "frete_int_usd_kg", valor_num=0.516, fonte="baseline")
    session.commit()
    prop = adm.preview_premissa(session, "frete_int_usd_kg", valor_num="0.516", fonte="x")
    assert prop.linhas[0].situacao == adm.NO_OP
    assert adm.aplicar_premissa(session, "frete_int_usd_kg", prop, ator=ator,
                                valor_num=0.516, fonte="x") is None


# ===========================================================================
# P0 — §47: margem por família, sem contaminar outras
# ===========================================================================
def test_p0_margem_de_fornecedor_nao_contamina_os_outros(session, ator, margens_isoladas):
    """Regra nova para um fornecedor não muda a margem de KTC nem de Decor."""
    fornecedores = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    ktc = novo_produto(session, fornecedores["KTC"], "MARG-KTC", familia="Bath Towel")
    daune_p = novo_produto(session, fornecedores["DAUNE"], "MARG-DAUNE")
    decor_p = novo_produto(session, fornecedores["DECOR_TRICOT"], "MARG-DECOR")
    session.commit()

    antes = {p.sku_key: ps.margem_padrao(session, p).margem_pct
             for p in (ktc, daune_p, decor_p)}

    prop = adm.preview_margem(session, margem_pct="0.15", nome="Daune 15% (teste)",
                              fornecedor_id=fornecedores["DAUNE"].id, prioridade=5,
                              fonte="decisão comercial de teste")
    assert prop.escopo.startswith("FORNECEDOR")
    assert prop.skus_afetados >= 1
    adm.aplicar_margem(session, prop, ator=ator, margem_pct="0.15",
                       nome="Daune 15% (teste)", fornecedor_id=fornecedores["DAUNE"].id,
                       prioridade=5, fonte="decisão comercial de teste")
    session.commit()

    depois = {p.sku_key: ps.margem_padrao(session, p).margem_pct
              for p in (ktc, daune_p, decor_p)}
    assert D(depois["MARG-DAUNE"]) == D("0.15")
    assert depois["MARG-KTC"] == antes["MARG-KTC"]
    assert depois["MARG-DECOR"] == antes["MARG-DECOR"]


def test_margem_futura_so_vale_depois_da_data(session, ator, margens_isoladas):
    """`MargemRegra` sempre teve vigência; até a Sessão 5 o resolvedor a ignorava."""
    from app.margin_rules import resolver_margem

    fornecedores = {f.codigo: f for f in session.exec(select(Fornecedor)).all()}
    p = novo_produto(session, fornecedores["DECOR_TRICOT"], "MARG-FUT")
    session.commit()
    hoje_pct = ps.margem_padrao(session, p).margem_pct

    prop = adm.preview_margem(session, margem_pct="0.19", nome="Decor 19% em 2027",
                              fornecedor_id=fornecedores["DECOR_TRICOT"].id, prioridade=1,
                              fonte="planejamento", vigente_a_partir_de=ANO_QUE_VEM)
    adm.aplicar_margem(session, prop, ator=ator, margem_pct="0.19",
                       nome="Decor 19% em 2027",
                       fornecedor_id=fornecedores["DECOR_TRICOT"].id, prioridade=1,
                       fonte="planejamento", vigente_a_partir_de=ANO_QUE_VEM)
    session.commit()

    regras = session.exec(select(MargemRegra)).all()
    agora = resolver_margem(regras, fornecedor_id=p.fornecedor_id, familia=p.familia,
                            sku_key=p.sku_key, ref=HOJE)
    depois = resolver_margem(regras, fornecedor_id=p.fornecedor_id, familia=p.familia,
                             sku_key=p.sku_key, ref=ANO_QUE_VEM)
    assert D(agora.margem_pct) == D(hoje_pct)
    assert D(depois.margem_pct) == D("0.19")


def test_regra_sem_escopo_avisa_que_alcanca_tudo(session):
    """§30: o admin precisa ver o nível em que está criando a regra."""
    prop = adm.preview_margem(session, margem_pct="0.20", nome="geral",
                              fonte="teste de escopo")
    assert prop.escopo.startswith("GERAL")
    assert any("catálogo inteiro" in a for a in prop.avisos)


# ===========================================================================
# P0 — §49: concorrência
# ===========================================================================
def test_p0_conflito_entre_dois_admins(session, daune, ator):
    """A faz preview; B aplica antes; o apply de A é recusado — não vira V3 silenciosa."""
    x = novo_produto(session, daune, "CONC-1", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()

    prop_a = adm.preview_custo_sku(session, x.id, cnet_brl="110.00", status="CONFIRMADO",
                                   fonte="admin A")
    # B chega primeiro
    versionar(session, x, "105.00", fonte="admin B", ator=_novo_usuario("ADMIN"))
    session.commit()

    with pytest.raises(adm.ConflitoDeVersao):
        adm.aplicar_custo_sku(session, x.id, prop_a, ator=ator, cnet_brl="110.00",
                              status="CONFIRMADO", fonte="admin A")
    session.commit()

    # o valor de B permanece, e o conflito ficou registrado
    assert D(cs.referencia_vigente(session, x.id).cnet_brl) == D("105.00")
    assert len(cs.versoes(session, x.id)) == 2
    assert any(t.resultado == adm.CONFLITO
               for t in adm.trilha(session, entidade="CustoReferencia", limite=5))


def test_token_do_preview_nao_transporta_valor(session, daune):
    """§42: o token prova o estado observado; ele não carrega o número."""
    x = novo_produto(session, daune, "TOKEN-1", custo=100.0)
    session.commit()
    p1 = adm.preview_custo_sku(session, x.id, cnet_brl="110.00", status="CONFIRMADO",
                               fonte="f")
    p2 = adm.preview_custo_sku(session, x.id, cnet_brl="999.00", status="CONFIRMADO",
                               fonte="f")
    # mesmo estado observado → mesmo token, ainda que o valor proposto seja outro
    assert p1.token == p2.token
    assert "110" not in p1.token and "999" not in p2.token


# ===========================================================================
# P0 — §51: delete
# ===========================================================================
def test_p0_nao_apaga_versao_usada_em_cotacao(session, daune, ator):
    x = novo_produto(session, daune, "DEL-1", custo=100.0)
    v1 = versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.add(CotacaoItem(cotacao_id=0, produto_id=x.id, nome_produto=x.nome, quantidade=1,
                            custo_unitario=100.0, preco_base=200.0, preco_negociado=200.0,
                            margem_liquida=0.14, faturamento=200.0, custo_total=100.0,
                            lucro=10.0))
    session.commit()

    assert adm.referencia_esta_em_uso(session, v1) is True
    with pytest.raises(adm.DadoInvalido):
        adm.apagar_referencia(session, v1, ator=ator)
    session.commit()
    assert session.get(type(v1), v1.id) is not None
    assert any(t.resultado == "RECUSADO"
               for t in adm.trilha(session, entidade="CustoReferencia", limite=5))


def test_encerrar_vigencia_preserva_a_versao(session, daune, ator):
    """Encerrar não é apagar — e exige motivo."""
    x = novo_produto(session, daune, "DEL-2", custo=100.0)
    v1 = versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()
    with pytest.raises(adm.DadoInvalido):
        adm.encerrar_vigencia(session, v1, ator=ator, motivo="")
    adm.encerrar_vigencia(session, v1, ator=ator, motivo="fornecedor descontinuou")
    session.commit()
    assert session.get(type(v1), v1.id) is not None
    assert v1.valid_to == HOJE


def test_rollback_administrativo_cria_v3(session, daune, ator):
    """§25: voltar ao valor antigo é criar V3, não apagar V2."""
    x = novo_produto(session, daune, "ROLL-1", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    versionar(session, x, "110.00", fonte="V2 — reajuste", ator=ator)
    session.commit()
    versionar(session, x, "100.00", fonte="V3 — reajuste cancelado pelo fornecedor",
              ator=ator)
    session.commit()

    versoes = cs.versoes(session, x.id)
    assert [v.versao for v in versoes] == [1, 2, 3]
    assert D(versoes[-1].cnet_brl) == D("100.00")
    assert D(cs.referencia_vigente(session, x.id).cnet_brl) == D("100.00")


# ===========================================================================
# P0 — §52: rascunho não atualiza sozinho
# ===========================================================================
def test_p0_rascunho_nao_atualiza_automaticamente(session, daune, ator):
    """O rascunho de ontem continua com o custo de ontem. Detectar ≠ recalcular."""
    x = novo_produto(session, daune, "DRAFT-1", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()

    cot = Cotacao(cliente_id=0, estado_origem="São Paulo", uf_origem_fiscal="SP",
                  estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                  condicao_pagamento="30", numero="DRAFT-5", status="rascunho")
    session.add(cot)
    session.commit()
    session.refresh(cot)
    item = CotacaoItem(cotacao_id=cot.id, produto_id=x.id, nome_produto=x.nome, quantidade=2,
                       custo_unitario=100.0, preco_base=200.0, preco_negociado=200.0,
                       margem_liquida=0.14, faturamento=400.0, custo_total=200.0, lucro=20.0)
    session.add(item)
    session.commit()
    congelado = (item.custo_unitario, item.preco_negociado, item.faturamento)

    versionar(session, x, "130.00", fonte="tabela nova", ator=ator)
    session.commit()

    # abrir o rascunho não muda nada
    session.refresh(item)
    assert (item.custo_unitario, item.preco_negociado, item.faturamento) == congelado

    # mas o sistema SABE que há premissa mais nova
    estado = adm.premissas_desatualizadas(session, cot, [item])
    assert estado["desatualizado"] is True
    assert estado["itens"][0]["custo_no_item"] == 100.0
    assert estado["itens"][0]["custo_vigente"] == 130.0
    assert "Nada foi alterado" in estado["texto"]


def test_rascunho_em_dia_nao_e_marcado(session, daune, ator):
    x = novo_produto(session, daune, "DRAFT-2", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()
    item = CotacaoItem(cotacao_id=0, produto_id=x.id, nome_produto=x.nome, quantidade=1,
                       custo_unitario=100.0, preco_base=200.0, preco_negociado=200.0,
                       margem_liquida=0.14, faturamento=200.0, custo_total=100.0, lucro=10.0)
    session.add(item)
    session.commit()
    assert adm.premissas_desatualizadas(session, None, [item])["desatualizado"] is False


# ===========================================================================
# Validação — servidor, não HTML
# ===========================================================================
def test_fonte_e_obrigatoria(session, daune):
    x = novo_produto(session, daune, "VAL-1")
    session.commit()
    with pytest.raises(adm.DadoInvalido):
        adm.preview_custo_sku(session, x.id, cnet_brl="100", status="CONFIRMADO", fonte="")


def test_valor_nao_numerico_e_recusado(session, daune):
    x = novo_produto(session, daune, "VAL-2")
    session.commit()
    with pytest.raises(adm.DadoInvalido):
        adm.preview_custo_sku(session, x.id, cnet_brl="banana", status="CONFIRMADO",
                              fonte="f")


def test_status_fora_dos_cinco_canonicos_e_recusado(session, daune):
    x = novo_produto(session, daune, "VAL-3")
    session.commit()
    with pytest.raises(adm.DadoInvalido):
        adm.preview_custo_sku(session, x.id, cnet_brl="100", status="MAIS_OU_MENOS",
                              fonte="f")


def test_percentual_implausivel_e_recusado(session):
    """"ICMS = banana" não entra, e "margem = 500%" também não."""
    with pytest.raises(adm.DadoInvalido):
        adm.valida_percentual("5", "margem")           # 500%
    with pytest.raises(adm.DadoInvalido):
        adm.valida_percentual("-0.1", "margem")
    assert adm.valida_percentual("0.14", "margem") == D("0.14")


def test_vigencia_invertida_e_recusada():
    with pytest.raises(adm.DadoInvalido):
        adm.valida_vigencia(date(2027, 1, 1), date(2026, 1, 1))


def test_entrada_do_admin_vira_decimal_sem_passar_por_float(session, daune, ator):
    """§40: string do formulário → Decimal. Nunca `Decimal(float)`."""
    x = novo_produto(session, daune, "DEC-1")
    versionar(session, x, "0.1", fonte="f", ator=ator)
    session.commit()
    versionar(session, x, "0.3", fonte="g", ator=ator)
    session.commit()
    assert D(cs.referencia_vigente(session, x.id).cnet_brl) == D("0.3")


def test_produto_inexistente_da_erro_legivel(session):
    prop = adm.preview_custo_sku(session, 999999, cnet_brl="10", status="CONFIRMADO",
                                 fonte="f")
    assert prop.linhas[0].situacao == adm.NAO_ENCONTRADO
    assert not prop.pode_aplicar


def test_variacao_grande_gera_aviso(session, daune, ator):
    """Salto de 30%+ costuma ser erro de unidade — o preview avisa, não bloqueia."""
    x = novo_produto(session, daune, "AVISO-1", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()
    prop = adm.preview_custo_sku(session, x.id, cnet_brl="500.00", status="CONFIRMADO",
                                 fonte="suspeita")
    assert prop.pode_aplicar
    assert any("30%" in a for a in prop.avisos)


# ===========================================================================
# Trilha
# ===========================================================================
def test_trilha_registra_ator_papel_e_motivo(session, daune, ator):
    x = novo_produto(session, daune, "LOG-1", custo=100.0)
    prop = adm.preview_custo_sku(session, x.id, cnet_brl="150.00", status="CONFIRMADO",
                                 fonte="e-mail do fornecedor", motivo="reajuste anual")
    adm.aplicar_custo_sku(session, x.id, prop, ator=ator, cnet_brl="150.00",
                          status="CONFIRMADO", fonte="e-mail do fornecedor",
                          motivo="reajuste anual")
    session.commit()
    t = adm.trilha(session, entidade="CustoReferencia", limite=1)[0]
    assert t.ator_email == ator.email and t.ator_papel == "OWNER"
    assert t.motivo == "reajuste anual"
    assert t.escopo == "SKU LOG-1"
    assert t.versao_anterior and t.versao_nova


def test_trilha_nao_guarda_segredo(session, daune, ator):
    """§19: a trilha guarda o econômico e o porquê — nunca credencial."""
    x = novo_produto(session, daune, "LOG-2", custo=100.0)
    versionar(session, x, "111.00", fonte="f", ator=ator)
    session.commit()
    for t in adm.trilha(session, limite=50):
        texto = " ".join(str(v) for v in (t.detalhe, t.motivo, t.versao_nova,
                                          t.versao_anterior) if v)
        for proibido in ("senha", "senha_hash", "argon2", "cookie", "secret", "token="):
            assert proibido not in texto.lower()


# ===========================================================================
# Simulação de impacto
# ===========================================================================
def test_simulacao_nao_toca_em_nada(session, daune, ator):
    """§29: a simulação mostra o efeito comercial e não grava uma linha sequer."""
    x = novo_produto(session, daune, "SIM-1", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()
    versoes_antes = len(cs.versoes(session, x.id))
    custo_antes = x.custo_unitario

    r = adm.simular_impacto_custo(session, x.id, "130.00")
    session.commit()

    assert r["custo_atual"] == 100.0 and r["custo_novo"] == 130.0
    assert r["preco_novo"] > r["preco_atual"]
    assert "nenhuma cotação" in r["observacao"].lower()
    session.refresh(x)
    assert x.custo_unitario == custo_antes
    assert len(cs.versoes(session, x.id)) == versoes_antes


# ===========================================================================
# P0 — §48: importação com dry run
# ===========================================================================
#: A `session` da suíte é única e `sku_key` é único no banco. Cada teste de importação
#: precisa do próprio conjunto de SKUs, senão o segundo teste esbarra nos SKUs do primeiro.
_LOTE = iter(range(1, 999))


#: A base é criada com a MESMA fonte e o MESMO documento do arquivo que será importado.
#: Isso importa desde a correção da Sessão 5: preço igual vindo de documento diferente é
#: **reconfirmação**, não no-op. Para o cenário do §48 ter dois NO_CHANGE de verdade, as
#: duas linhas inalteradas precisam carregar a mesma evidência — que é o caso real de
#: reimportar a mesma planilha.
FONTE_DO_LOTE = "tabela do fornecedor 09/2026"
DOC_DO_LOTE = "setembro.xlsx"


@pytest.fixture
def catalogo_de_importacao(session, daune, ator):
    """Oito SKUs com custo vigente, com prefixo próprio deste teste."""
    prefixo = f"IMP{next(_LOTE)}"
    skus = {}
    for i in range(1, 9):
        p = novo_produto(session, daune, f"{prefixo}-{i}", custo=100.0 + i)
        versionar(session, p, f"{100 + i}.00", fonte=FONTE_DO_LOTE, documento=DOC_DO_LOTE,
                  ator=ator)
        skus[p.sku_key] = p
    session.commit()
    skus["_prefixo"] = prefixo
    return skus


def _arquivo_de_dez_linhas(prefixo):
    """6 mudam · 2 idênticas · 1 ambígua · 1 inexistente."""
    linhas = [{"sku_key": f"{prefixo}-{i}", "cnet_brl": f"{200 + i}.00"} for i in range(1, 7)]
    linhas += [{"sku_key": f"{prefixo}-7", "cnet_brl": "107.00"},
               {"sku_key": f"{prefixo}-8", "cnet_brl": "108.00"}]
    linhas += [{"familia": "Flat Sheet", "fornecedor_id": None, "cnet_brl": "300.00"}]
    linhas += [{"sku_key": "NAO-EXISTE-NO-CATALOGO", "cnet_brl": "400.00"}]
    return linhas


def test_p0_dry_run_nao_escreve_nada(session, catalogo_de_importacao, ator):
    """§14: o dry run lê, valida, casa, mostra o diff — e não grava."""
    prefixo = catalogo_de_importacao["_prefixo"]
    produtos = {k: v for k, v in catalogo_de_importacao.items() if k != "_prefixo"}
    antes = {sku: len(cs.versoes(session, p.id)) for sku, p in produtos.items()}
    custos_antes = {sku: p.custo_unitario for sku, p in produtos.items()}
    trilha_antes = len(adm.trilha(session, limite=500))

    prop = adm.preview_importacao(session, _arquivo_de_dez_linhas(prefixo),
                                  fonte=FONTE_DO_LOTE, documento=DOC_DO_LOTE)
    session.commit()

    assert prop.resumo.get(adm.MUDANCA) == 6
    assert prop.resumo.get(adm.NO_OP) == 2
    assert prop.resumo.get(adm.REVIEW_REQUIRED) == 1
    assert prop.resumo.get(adm.NAO_ENCONTRADO) == 1
    assert sum(prop.resumo.values()) == 10

    # nada foi escrito
    for sku, p in produtos.items():
        session.refresh(p)
        assert len(cs.versoes(session, p.id)) == antes[sku]
        assert p.custo_unitario == custos_antes[sku]
    assert len(adm.trilha(session, limite=500)) == trilha_antes


def test_p0_apply_versiona_somente_as_seis_mudancas(session, catalogo_de_importacao, ator):
    prefixo = catalogo_de_importacao["_prefixo"]
    linhas = _arquivo_de_dez_linhas(prefixo)
    prop = adm.preview_importacao(session, linhas, fonte=FONTE_DO_LOTE,
                                  documento=DOC_DO_LOTE)
    resultado = adm.aplicar_importacao(session, linhas, prop, ator=ator,
                                       fonte=FONTE_DO_LOTE, documento=DOC_DO_LOTE,
                                       motivo="reajuste de setembro")
    session.commit()

    assert resultado["aplicadas"] == 6
    assert resultado["ignoradas"] == 4
    # as 6 que mudaram ganharam V2
    for i in range(1, 7):
        p = catalogo_de_importacao[f"{prefixo}-{i}"]
        assert len(cs.versoes(session, p.id)) == 2
        assert D(cs.referencia_vigente(session, p.id).cnet_brl) == D(f"{200 + i}.00")
    # as 2 idênticas continuam com uma versão só
    for i in (7, 8):
        p = catalogo_de_importacao[f"{prefixo}-{i}"]
        assert len(cs.versoes(session, p.id)) == 1
    # e o lote inteiro tem uma correlação na trilha
    lote = [t for t in adm.trilha(session, limite=50) if t.acao == "IMPORTAR_LOTE"]
    assert lote and lote[0].correlacao == resultado["correlacao"]


def test_importar_a_mesma_planilha_duas_vezes_e_idempotente(session, catalogo_de_importacao,
                                                            ator):
    """§17: a segunda passada não cria V3."""
    prefixo = catalogo_de_importacao["_prefixo"]
    linhas = _arquivo_de_dez_linhas(prefixo)
    p1 = adm.preview_importacao(session, linhas, fonte=FONTE_DO_LOTE,
                                documento=DOC_DO_LOTE)
    adm.aplicar_importacao(session, linhas, p1, ator=ator, fonte=FONTE_DO_LOTE,
                           documento=DOC_DO_LOTE)
    session.commit()

    p2 = adm.preview_importacao(session, linhas, fonte=FONTE_DO_LOTE,
                                documento=DOC_DO_LOTE)
    assert p2.resumo.get(adm.MUDANCA, 0) == 0
    assert p2.resumo.get(adm.NO_OP) == 8
    r2 = adm.aplicar_importacao(session, linhas, p2, ator=ator, fonte=FONTE_DO_LOTE,
                                documento=DOC_DO_LOTE)
    session.commit()
    assert r2["aplicadas"] == 0
    for i in range(1, 7):
        assert len(cs.versoes(session, catalogo_de_importacao[f"{prefixo}-{i}"].id)) == 2


def test_matching_ambiguo_vira_review_required(session, daune, ator):
    """§16: dois candidatos não viram 'o mais parecido'."""
    novo_produto(session, daune, "AMB-1", familia="Duvet Cover")
    novo_produto(session, daune, "AMB-2", familia="Duvet Cover")
    session.commit()
    prop = adm.preview_importacao(
        session, [{"familia": "Duvet Cover", "fornecedor_id": daune.id, "gsm": None,
                   "thread_count": None, "cnet_brl": "50.00"}],
        fonte="planilha sem SKU")
    assert prop.linhas[0].situacao in (adm.REVIEW_REQUIRED, adm.NAO_ENCONTRADO)
    assert not prop.pode_aplicar


def test_linha_com_valor_invalido_nao_derruba_o_lote(session, catalogo_de_importacao, ator):
    """Uma linha ruim vira REVIEW_REQUIRED; as boas seguem."""
    prefixo = catalogo_de_importacao["_prefixo"]
    linhas = [{"sku_key": f"{prefixo}-1", "cnet_brl": "201.00"},
              {"sku_key": f"{prefixo}-2", "cnet_brl": "abacaxi"}]
    prop = adm.preview_importacao(session, linhas, fonte="t", documento="mista.xlsx")
    assert prop.resumo.get(adm.MUDANCA) == 1
    assert prop.resumo.get(adm.REVIEW_REQUIRED) == 1
    r = adm.aplicar_importacao(session, linhas, prop, ator=ator, fonte="t",
                               documento="mista.xlsx")
    session.commit()
    assert r["aplicadas"] == 1
    assert len(cs.versoes(session, catalogo_de_importacao[f"{prefixo}-2"].id)) == 1


# ===========================================================================
# P0 — §50: segurança da área administrativa
# ===========================================================================
def _chamar(funcao, request, **kwargs):
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


ROTAS_DE_ESCRITA = ["preview_custo", "aplicar_custo", "preview_premissa", "aplicar_premissa",
                    "preview_margem", "aplicar_margem"]


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_p0_vendedor_nao_altera_premissa(session, daune, papel, rota):
    """403 no servidor. Esconder o botão não é autorização."""
    from fastapi import HTTPException
    import app.routers.admin as r

    with pytest.raises(HTTPException) as erro:
        _chamar(getattr(r, rota), RequestFalsa(_novo_usuario(papel)), produto_id=1,
                cnet="10", status="CONFIRMADO", fonte="x", chave="fx_usd_brl", valor="5.2",
                margem="0.15", nome="n", token="t", session=session)
    assert erro.value.status_code == 403


@pytest.mark.parametrize("rota", ROTAS_DE_ESCRITA)
def test_p0_anonimo_nao_altera_premissa(session, rota):
    from app.permissoes import PrecisaLogin
    import app.routers.admin as r

    with pytest.raises(PrecisaLogin):
        _chamar(getattr(r, rota), RequestFalsa(None), produto_id=1, cnet="10",
                status="CONFIRMADO", fonte="x", chave="fx_usd_brl", valor="5.2",
                margem="0.15", nome="n", token="t", session=session)


def test_p0_admin_sem_permissao_economica_e_barrado(session, daune):
    """§21: `can_manage_users` não vira autorização econômica, e a flag é separada."""
    from fastapi import HTTPException
    import app.routers.admin as r
    from app.models import Usuario

    so_usuarios = Usuario(id=90, email="rh@anara.test", nome="RH", senha_hash="h",
                          papel="ADMIN", can_manage_users=True,
                          can_manage_economics=False)
    assert so_usuarios.gerencia_usuarios is True
    assert so_usuarios.gerencia_economia is False
    with pytest.raises(HTTPException) as erro:
        _chamar(r.preview_custo, RequestFalsa(so_usuarios), produto_id=1, cnet="10",
                status="CONFIRMADO", fonte="x", session=session)
    assert erro.value.status_code == 403


def test_owner_e_admin_autorizado_conseguem(session, daune, ator):
    import json as _json
    import app.routers.admin as r

    x = novo_produto(session, daune, "RBAC-ADM-1", custo=100.0)
    session.commit()
    for u in (_novo_usuario("OWNER"), _novo_usuario("ADMIN")):
        resp = _chamar(r.preview_custo, RequestFalsa(u), produto_id=x.id, cnet="150",
                       status="CONFIRMADO", fonte="fonte válida", session=session)
        assert _json.loads(bytes(resp.body).decode())["pode_aplicar"] is True


def test_papel_forjado_no_formulario_nao_autoriza(session, daune):
    """Mandar papel=OWNER no POST não muda quem você é."""
    from fastapi import HTTPException
    import app.routers.admin as r

    with pytest.raises(HTTPException) as erro:
        _chamar(r.aplicar_custo, RequestFalsa(_novo_usuario("VENDEDOR_INTERNO")),
                produto_id=1, cnet="10", status="CONFIRMADO", fonte="x", token="t",
                papel="OWNER", role="ADMIN", can_manage_economics=True, session=session)
    assert erro.value.status_code == 403


def test_premissa_fora_da_lista_editavel_e_recusada(session, ator):
    """Formulário genérico sobre `chave` deixaria criar 'icms = banana'. Lista fechada."""
    import json as _json
    import app.routers.admin as r

    resp = _chamar(r.preview_premissa, RequestFalsa(ator), chave="chave_inventada",
                   valor="1", fonte="x", session=session)
    assert resp.status_code == 403
    assert "não é editável" in _json.loads(bytes(resp.body).decode())["erro"]


def test_erro_administrativo_nao_devolve_stack_trace(session, ator):
    """§44: mensagem clara, nunca traceback nem caminho de arquivo."""
    import json as _json
    import app.routers.admin as r

    resp = _chamar(r.preview_custo, RequestFalsa(ator), produto_id=1, cnet="abacaxi",
                   status="CONFIRMADO", fonte="x", session=session)
    corpo = _json.loads(bytes(resp.body).decode())
    assert resp.status_code == 400
    assert "Traceback" not in corpo["erro"]
    assert "/Users/" not in corpo["erro"]
    assert "não é um número válido" in corpo["erro"]


# ===========================================================================
# CORREÇÃO 1 — NO_CHANGE real × reconfirmação com evidência nova
# ===========================================================================
def test_mesmo_preco_com_fonte_nova_e_reconfirmacao_nao_no_op(session, daune, ator):
    """O preço não muda, mas alguém reconferiu. Perder isso apagaria a evidência.

    É a diferença entre "nada aconteceu" e "o fornecedor confirmou em 05/09 que o preço
    continua 100". A segunda frase é informação econômica, e some se o sistema devolver
    NO_CHANGE.
    """
    x = novo_produto(session, daune, "RECONF-1", custo=100.0)
    versionar(session, x, "100.00", fonte="tabela fornecedor 01/08", documento="A",
              quando=HOJE - timedelta(days=35), ator=ator)
    session.commit()
    v1 = cs.referencia_vigente(session, x.id)

    prop = adm.preview_custo_sku(session, x.id, cnet_brl="100.00", status="CONFIRMADO",
                                 fonte="nova tabela oficial 05/09", documento="B")
    assert prop.linhas[0].situacao == adm.RECONFIRMACAO
    assert prop.pode_aplicar
    assert prop.linhas[0].detalhe["variacao_pct"] == 0.0
    assert any("Reconfirmação" in a for a in prop.avisos)

    nova = adm.aplicar_custo_sku(session, x.id, prop, ator=ator, cnet_brl="100.00",
                                 status="CONFIRMADO", fonte="nova tabela oficial 05/09",
                                 documento="B")
    session.commit()

    # a evidência nova ficou persistida, e o preço não mudou
    assert nova is not None and nova.versao == 2
    assert D(nova.cnet_brl) == D("100.00")
    assert nova.documento == "B"
    assert "nova tabela oficial 05/09" in nova.origem_registro
    # V1 continua auditável, com a evidência original
    session.refresh(v1)
    assert v1.documento == "A" and D(v1.cnet_brl) == D("100.00")
    # e a trilha diz que foi reconfirmação, não mudança de preço
    t = adm.trilha(session, entidade="CustoReferencia", limite=1)[0]
    assert t.acao == "RECONFIRMAR"
    assert json.loads(t.detalhe)["valor_alterado"] is False


def test_reimportar_a_fonte_b_depois_da_reconfirmacao_e_no_op(session, daune, ator):
    """A segunda passada da MESMA fonte B é idempotente — aí sim NO_CHANGE."""
    x = novo_produto(session, daune, "RECONF-2", custo=100.0)
    versionar(session, x, "100.00", fonte="fonte A", documento="A",
              quando=HOJE - timedelta(days=35), ator=ator)
    session.commit()
    versionar(session, x, "100.00", fonte="fonte B", documento="B", ator=ator)
    session.commit()
    assert len(cs.versoes(session, x.id)) == 2

    prop = adm.preview_custo_sku(session, x.id, cnet_brl="100.00", status="CONFIRMADO",
                                 fonte="fonte B", documento="B")
    assert prop.linhas[0].situacao == adm.NO_OP
    assert not prop.pode_aplicar
    assert adm.aplicar_custo_sku(session, x.id, prop, ator=ator, cnet_brl="100.00",
                                 status="CONFIRMADO", fonte="fonte B",
                                 documento="B") is None
    session.commit()
    assert len(cs.versoes(session, x.id)) == 2


def test_diferenca_so_de_representacao_continua_no_op(session, daune, ator):
    """"R$ 100,00" e "100.00" são o mesmo número; " Fonte A " e "fonte a", a mesma fonte."""
    x = novo_produto(session, daune, "RECONF-3", custo=100.0)
    versionar(session, x, "100.00", fonte="Tabela A", documento="A", ator=ator)
    session.commit()
    prop = adm.preview_custo_sku(session, x.id, cnet_brl="100", status="CONFIRMADO",
                                 fonte="  tabela a  ", documento="A")
    assert prop.linhas[0].situacao == adm.NO_OP


def test_importacao_distingue_reconfirmacao_de_no_op(session, daune, ator):
    """No lote também: mesma evidência é no-op; evidência nova é reconfirmação."""
    x = novo_produto(session, daune, "RECONF-LOTE", custo=100.0)
    versionar(session, x, "100.00", fonte="planilha agosto", documento="agosto.xlsx",
              quando=HOJE - timedelta(days=35), ator=ator)
    session.commit()

    linhas = [{"sku_key": "RECONF-LOTE", "cnet_brl": "100.00"}]
    reconf = adm.preview_importacao(session, linhas, fonte="planilha setembro",
                                    documento="setembro.xlsx")
    assert reconf.resumo.get(adm.RECONFIRMACAO) == 1
    adm.aplicar_importacao(session, linhas, reconf, ator=ator, fonte="planilha setembro",
                           documento="setembro.xlsx")
    session.commit()
    assert len(cs.versoes(session, x.id)) == 2

    de_novo = adm.preview_importacao(session, linhas, fonte="planilha setembro",
                                     documento="setembro.xlsx")
    assert de_novo.resumo.get(adm.NO_OP) == 1
    assert de_novo.resumo.get(adm.RECONFIRMACAO, 0) == 0


# ===========================================================================
# CORREÇÃO 2 — pinning: a cotação fica presa à versão EXATA
# ===========================================================================
def _montar_item(session, cotacao, produto, ator_req):
    from app.routers.admin import _erro  # noqa: F401  (garante import do módulo)
    from app.routers.cotacoes import adicionar_item
    _chamar(adicionar_item, ator_req, cotacao_id=cotacao.id, produto_id=produto.id,
            quantidade=3.0, modo="margem", valor=0.14, session=session)
    session.commit()
    return session.exec(select(CotacaoItem)
                        .where(CotacaoItem.cotacao_id == cotacao.id)).all()[-1]


def _nova_cotacao(session, numero):
    c = Cotacao(cliente_id=0, estado_origem="São Paulo", uf_origem_fiscal="SP",
                estado_destino="São Paulo", contribuinte_icms=True, finalidade="REVENDA",
                condicao_pagamento="30", numero=numero, status="rascunho")
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def test_item_pina_a_versao_exata_de_custo(session, daune, ator):
    """O item guarda o ID da versão — não só o valor dela."""
    x = novo_produto(session, daune, "PIN-1", custo=100.0)
    v1 = versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()

    cot = _nova_cotacao(session, "PIN-0001")
    item = _montar_item(session, cot, x, RequestFalsa(_novo_usuario("ADMIN")))

    assert item.custo_referencia_id == v1.id
    assert item.custo_referencia_versao == 1
    assert item.margem_regra_id is not None or item.margem_padrao_pct is not None
    assert item.condicao_pagamento_id is not None
    pinos = json.loads(item.premissas_pinadas)
    # A premissa que forma o preço novo é a NOMINAL; o efetivo é derivado dela com o `icms_pct`
    # do próprio item, que o snapshot fiscal da linha já congela.
    assert "pis_cofins_nominal_pct" in pinos and pinos["pis_cofins_nominal_pct"]["premissa_id"]
    assert "pis_cofins_pct" not in pinos, "a legada não prende mais genealogia nenhuma"


def test_p0_versao_retroativa_nao_reescreve_a_genealogia(session, daune, ator):
    """O teste crítico: V2 com vigência retroativa não rouba a autoria da cotação A.

    O resolvedor por data passa a dizer que V2 valeria naquele dia — e é isso que torna o
    pinning necessário. O item continua apontando para V1, que é a versão que realmente
    formou aquele preço.
    """
    x = novo_produto(session, daune, "PIN-RETRO", custo=100.0)
    ontem = HOJE - timedelta(days=10)
    v1 = versionar(session, x, "100.00", fonte="V1 — tabela de agosto", quando=ontem,
                   ator=ator)
    session.commit()

    cot = _nova_cotacao(session, "PIN-0002")
    item = _montar_item(session, cot, x, RequestFalsa(_novo_usuario("ADMIN")))
    congelado = (item.custo_referencia_id, item.custo_referencia_versao,
                 item.custo_unitario, item.preco_negociado, item.faturamento)
    assert item.custo_referencia_id == v1.id

    # --- o admin cadastra V2 com vigência RETROATIVA, cobrindo a data da cotação ---
    v2 = versionar(session, x, "175.00", fonte="V2 retroativa", quando=ontem, ator=ator)
    session.commit()

    # o resolvedor por data agora aponta para V2 naquela data...
    resolvido = cs.referencia_em(session, x.id, ontem)
    assert resolvido is not None and resolvido.id == v2.id
    # ...mas a cotação continua identificando V1, e o preço não mudou
    session.refresh(item)
    assert (item.custo_referencia_id, item.custo_referencia_versao,
            item.custo_unitario, item.preco_negociado, item.faturamento) == congelado
    assert item.custo_referencia_id == v1.id
    # e V1 continua consultável, com a evidência original
    session.refresh(v1)
    assert D(v1.cnet_brl) == D("100.00")
    assert "V1 — tabela de agosto" in v1.origem_registro


def test_pinning_sobrevive_a_mudanca_de_premissa_global(session, daune, ator):
    """FX novo não reescreve qual versão de premissa formou a cotação de ontem."""
    x = novo_produto(session, daune, "PIN-FX", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    cfg.definir(session, "pis_cofins_nominal_pct", valor_num=0.0925, fonte="baseline")
    session.commit()

    cot = _nova_cotacao(session, "PIN-0003")
    item = _montar_item(session, cot, x, RequestFalsa(_novo_usuario("ADMIN")))
    pinado = json.loads(item.premissas_pinadas)["pis_cofins_nominal_pct"]

    prop = adm.preview_premissa(session, "pis_cofins_nominal_pct", valor_num="0.08",
                                fonte="mudança de regime")
    adm.aplicar_premissa(session, "pis_cofins_nominal_pct", prop, ator=ator, valor_num=0.08,
                         fonte="mudança de regime")
    session.commit()

    # a premissa vigente mudou, o pino do item não
    assert D(cfg.num(session, "pis_cofins_nominal_pct")) == D("0.08")
    session.refresh(item)
    assert json.loads(item.premissas_pinadas)["pis_cofins_nominal_pct"] == pinado
    assert D(pinado["valor"]) == D("0.0925")
    # restaura para não contaminar as demais suítes
    cfg.definir(session, "pis_cofins_nominal_pct", valor_num=0.0925, fonte="restauro do teste")
    session.commit()


# ===========================================================================
# Future-dated da condição de pagamento
# ===========================================================================
def test_condicao_de_pagamento_futura_so_vale_depois_da_data(session):
    """`resolver_encargo` resolve por data — fixture isolada, taxas canônicas intactas."""
    from app.models import CondicaoPagamento
    from app.payment_terms import resolver_encargo

    v1 = CondicaoPagamento(codigo="TESTE-FUT", label="Fixture 30 DD", encargo_pct=0.016,
                           encargo_confirmado=True, versao=1, valid_from=None)
    session.add(v1)
    session.commit()
    session.refresh(v1)
    v2 = CondicaoPagamento(codigo="TESTE-FUT", label="Fixture 30 DD (2027)",
                           encargo_pct=0.021, encargo_confirmado=True, versao=2,
                           valid_from=ANO_QUE_VEM, substitui_id=v1.id)
    v1.valid_to = ANO_QUE_VEM
    session.add_all([v1, v2])
    session.commit()

    condicoes = session.exec(select(CondicaoPagamento)).all()
    assert D(resolver_encargo(condicoes, "TESTE-FUT", ref=HOJE).pct) == D("0.016")
    assert D(resolver_encargo(condicoes, "TESTE-FUT", ref=ANO_QUE_VEM).pct) == D("0.021")

    # as canônicas continuam intactas
    assert D(resolver_encargo(condicoes, "30", ref=HOJE).pct) == D("0.016")
    assert D(resolver_encargo(condicoes, "30/60/90", ref=HOJE).pct) == D("0.048")

    session.delete(v2)
    v1.valid_to = None
    session.add(v1)
    session.commit()
    session.delete(session.get(CondicaoPagamento, v1.id))
    session.commit()


def test_item_pina_a_condicao_e_o_pino_nao_muda_com_versao_nova(session, daune, ator):
    """O item guarda o ID da condição usada; uma versão nova não reescreve esse pino."""
    from app.models import CondicaoPagamento

    x = novo_produto(session, daune, "PIN-COND", custo=100.0)
    versionar(session, x, "100.00", fonte="V1", ator=ator)
    session.commit()
    cot = _nova_cotacao(session, "PIN-0004")
    item = _montar_item(session, cot, x, RequestFalsa(_novo_usuario("ADMIN")))
    pinada = item.condicao_pagamento_id
    assert pinada is not None

    original = session.get(CondicaoPagamento, pinada)
    nova = CondicaoPagamento(codigo=original.codigo, label=original.label + " v2",
                             encargo_pct=0.025, encargo_confirmado=True, versao=2,
                             valid_from=HOJE, substitui_id=original.id)
    original.valid_to = HOJE
    session.add_all([original, nova])
    session.commit()

    session.refresh(item)
    assert item.condicao_pagamento_id == pinada          # o pino não se move
    assert D(item.encargo_pct) == D("0.016")             # nem o valor congelado

    session.delete(nova)
    original.valid_to = None
    session.add(original)
    session.commit()

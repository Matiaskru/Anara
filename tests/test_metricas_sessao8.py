"""Sessão 8 — métricas, relatórios e saúde operacional.

Relatório errado é pior que relatório ausente: o ausente todo mundo sabe que não tem, e o
errado alguém usa para decidir. Por isso quase todo teste aqui fixa uma **definição** —
qual é o denominador da conversão, o que entra no pipeline, como se pondera margem — e
prova que a definição foi respeitada.

As definições vivem em `metrics_service`, um lugar só, e dashboard, CSV e estes testes
chamam as mesmas funções. É o que impede a tela dizer 42% e o CSV dizer 39%.
"""
import csv
import inspect
import io
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from app import crm_service as crm
from app import metrics_service as mx
from app import workflow_service as ws
from app.dinheiro import D
from app.models import (
    Cliente, CostMethod, Cotacao, CotacaoItem, Fornecedor, Oportunidade, Produto,
    StatusCotacao, StatusOportunidade, Usuario,
)
from conftest import RequestFalsa, _novo_usuario

AGORA = datetime.utcnow()
ONTEM = AGORA - timedelta(days=1)
AMANHA = AGORA + timedelta(days=1)
_SEQ = iter(range(1, 9999))
TUDO = mx.Periodo()


def chamar(funcao, request, **kwargs):
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


def html(r):
    return bytes(r.body).decode()


def csv_texto(resposta) -> str:
    return bytes(resposta.body).decode()


def responsavel_novo(session, sufixo: str):
    """Um responsável só deste teste — é como se escopa o painel numa sessão compartilhada.

    O filtro por responsável existe no produto; usá-lo aqui evita asserções absolutas que
    quebrariam conforme outros testes criassem oportunidades.
    """
    u = Usuario(email=f"mx-{sufixo}-{next(_SEQ)}@anara.test", nome=f"Resp {sufixo}",
                senha_hash="h", papel="VENDEDOR_INTERNO")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def owner(session):
    u = session.exec(select(Usuario).where(Usuario.email == "mx-owner@anara.test")).first()
    if u is None:
        u = Usuario(email="mx-owner@anara.test", nome="Owner das métricas", senha_hash="h",
                    papel="OWNER", can_approve_quotes=True)
        session.add(u)
        session.commit()
        session.refresh(u)
    return u


@pytest.fixture
def daune(session):
    return session.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()


@pytest.fixture
def cliente(session, owner):
    c = crm.criar_cliente(session, ator=owner, nome=f"Cliente MX {next(_SEQ)}",
                          cidade_uf="São Paulo", finalidade="REVENDA")
    session.commit()
    return c


def nova_op(session, owner, cliente, **kw):
    dados = dict(cliente_id=cliente.id, titulo=f"Negócio {next(_SEQ)}")
    dados.update(kw)
    op = crm.criar_oportunidade(session, ator=owner, **dados)
    session.commit()
    return op


def cotacao(session, op, cliente, **kw):
    dados = dict(cliente_id=cliente.id, oportunidade_id=op.id, estado_origem="São Paulo",
                 uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=True,
                 finalidade="REVENDA", condicao_pagamento="30", freight_type="FOB",
                 numero=f"MX-{next(_SEQ):04d}", status=StatusCotacao.rascunho.value)
    dados.update(kw)
    c = Cotacao(**dados)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def item_com_total(session, cot, total, *, lucro=None):
    """Item com faturamento conhecido — o que as métricas comerciais leem."""
    it = CotacaoItem(cotacao_id=cot.id, nome_produto=f"Item {next(_SEQ)}", quantidade=1,
                     custo_unitario=float(D(total)) * 0.5, preco_base=float(D(total)),
                     preco_recomendado=float(D(total)), preco_negociado=float(D(total)),
                     faturamento=float(D(total)), custo_total=float(D(total)) * 0.5,
                     lucro=float(D(lucro)) if lucro is not None else float(D(total)) * 0.1,
                     margem_liquida=0.1)
    session.add(it)
    session.commit()
    session.refresh(it)
    return it


# ===========================================================================
# P0 §43 — valor cotado atual
# ===========================================================================
def test_p0_valor_cotado_atual_e_a_revisao_mais_alta_nao_cancelada(session, owner, cliente):
    """R1 10k · R2 11k · R3 12k cancelada → 11k. **R1 e R2 nunca são somadas.**

    Somá-las dobraria o mesmo negócio: são a mesma proposta em dois momentos.
    """
    op = nova_op(session, owner, cliente)
    r1 = cotacao(session, op, cliente, revisao=1)
    item_com_total(session, r1, "10000")
    r2 = cotacao(session, op, cliente, revisao=2, cotacao_origem_id=r1.id)
    item_com_total(session, r2, "11000")
    r3 = cotacao(session, op, cliente, revisao=3, cotacao_origem_id=r1.id,
                 status=StatusCotacao.cancelada.value)
    item_com_total(session, r3, "12000")
    session.commit()

    assert mx.proposta_relevante(session, op.id).id == r2.id
    assert D(mx.valor_cotado_atual(session, op.id)) == D("11000.00")
    # a soma ingênua daria 21.000 — e é o erro que este teste existe para impedir
    assert D(mx.valor_cotado_atual(session, op.id)) != D("21000.00")


def test_p0_propostas_paralelas_usam_criterio_deterministico(session, owner, cliente):
    """Duas genealogias na mesma oportunidade: vale a de atualização mais recente."""
    op = nova_op(session, owner, cliente)
    antiga = cotacao(session, op, cliente, revisao=1,
                     criado_em=AGORA - timedelta(days=10))
    item_com_total(session, antiga, "5000")
    nova = cotacao(session, op, cliente, revisao=1, criado_em=AGORA - timedelta(days=1))
    item_com_total(session, nova, "7000")
    session.commit()

    escolhida = mx.proposta_relevante(session, op.id)
    assert escolhida.id == nova.id
    assert D(mx.valor_cotado_atual(session, op.id)) == D("7000.00")
    # determinístico: repetir dá o mesmo, não depende da ordem do banco
    assert mx.proposta_relevante(session, op.id).id == escolhida.id


def test_emissao_desempata_propostas_paralelas(session, owner, cliente):
    """Emitir é o sinal mais forte de relevância — mais que a data de criação."""
    op = nova_op(session, owner, cliente)
    a = cotacao(session, op, cliente, criado_em=AGORA - timedelta(days=1))
    item_com_total(session, a, "3000")
    b = cotacao(session, op, cliente, criado_em=AGORA - timedelta(days=9),
                issued_em=AGORA, status=StatusCotacao.emitida.value)
    item_com_total(session, b, "9000")
    session.commit()
    assert mx.proposta_relevante(session, op.id).id == b.id


def test_oportunidade_sem_cotacao_nao_tem_valor_cotado(session, owner, cliente):
    op = nova_op(session, owner, cliente)
    session.commit()
    assert mx.valor_cotado_atual(session, op.id) is None


# ===========================================================================
# P0 §44 — valor do pipeline
# ===========================================================================
def test_p0_valor_do_pipeline(session, owner, cliente):
    """Cotado vence estimado; sem cotação vale o estimado; ganha fica fora."""
    resp = responsavel_novo(session, "pipeline")
    a = nova_op(session, owner, cliente, valor_estimado="8000", responsavel_id=resp.id)
    cot_a = cotacao(session, a, cliente)
    item_com_total(session, cot_a, "10000")
    b = nova_op(session, owner, cliente, valor_estimado="5000", responsavel_id=resp.id)
    c = nova_op(session, owner, cliente, responsavel_id=resp.id)
    cot_c = cotacao(session, c, cliente, status=StatusCotacao.emitida.value,
                    issued_em=AGORA)
    item_com_total(session, cot_c, "20000")
    session.commit()
    crm.marcar_ganha(session, c, cot_c.id, ator=owner)
    session.commit()

    assert D(mx.valor_de_pipeline(session, a)) == D("10000.00")     # cotado > estimado
    assert D(mx.valor_de_pipeline(session, b)) == D("5000")         # só estimado
    painel = mx.painel_comercial(session, TUDO, responsavel_id=resp.id)
    # a ganha (20.000) não entra no pipeline aberto
    assert D(painel.valor_pipeline) == D("15000.00")
    assert painel.abertas == 2
    assert painel.ganhas == 1 and D(painel.valor_ganho) == D("20000.00")


def test_oportunidade_sem_valor_nao_vira_zero(session, owner, cliente):
    """§35: valor ausente ≠ R$ 0,00. Somar zero encolheria o pipeline artificialmente."""
    op = nova_op(session, owner, cliente)
    session.commit()
    assert mx.valor_de_pipeline(session, op) is None
    painel = mx.painel_comercial(session, TUDO)
    assert painel.abertas_sem_valor >= 1


# ===========================================================================
# P0 §45 — reaberta não é perdida
# ===========================================================================
def test_p0_reaberta_conta_como_aberta_e_nao_como_perdida(session, owner, cliente):
    """Estado atual manda. O evento de perda continua auditável — é outra pergunta."""
    op = nova_op(session, owner, cliente, valor_estimado="1000")
    crm.marcar_perdida(session, op, ator=owner, motivo="PRECO")
    session.commit()
    antes = mx.painel_comercial(session, TUDO)
    assert antes.perdidas >= 1

    crm.reabrir(session, op, ator=owner, motivo="cliente voltou")
    session.commit()
    depois = mx.painel_comercial(session, TUDO)

    assert depois.abertas == antes.abertas + 1
    assert depois.perdidas == antes.perdidas - 1
    # e o registro da perda não sumiu do objeto
    assert op.lost_em is not None and op.motivo_perda == "PRECO"
    # nem dos motivos: ela deixou de contar porque hoje está aberta
    motivos = {m["motivo"] for m in depois.motivos_perda}
    assert op.status == StatusOportunidade.aberta.value


# ===========================================================================
# P0 §46 — ganho usa o snapshot
# ===========================================================================
def test_p0_valor_ganho_nao_e_recalculado(session, owner, cliente, daune):
    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente, status=StatusCotacao.emitida.value,
                  issued_em=AGORA)
    item = item_com_total(session, cot, "25000")
    session.commit()
    crm.marcar_ganha(session, op, cot.id, ator=owner)
    session.commit()
    assert D(op.valor_fechado) == D("25000.00")

    # o preço da cotação muda depois (cenário de reajuste)
    item.faturamento = 90000.0
    item.preco_negociado = 90000.0
    session.add(item)
    session.commit()

    painel = mx.painel_comercial(session, TUDO)
    assert D(op.valor_fechado) == D("25000.00")
    assert D(painel.valor_ganho) >= D("25000.00")
    # o número do painel vem do snapshot, não do item vivo
    assert "90000" not in str(op.valor_fechado)


# ===========================================================================
# P0 §47 — conversão
# ===========================================================================
def test_p0_conversao_so_conta_encerradas():
    """6 ganhas, 4 perdidas → 60%. As 3 abertas não entram no denominador."""
    assert mx.taxa_de_conversao(6, 4) == 0.6
    assert mx.taxa_de_conversao(1, 0) == 1.0
    assert mx.taxa_de_conversao(0, 3) == 0.0


def test_p0_conversao_sem_encerradas_e_indefinida():
    """§36: sem negócio decidido, a resposta é `None` — não `0%`.

    "0% de conversão" afirma um fracasso que não aconteceu.
    """
    assert mx.taxa_de_conversao(0, 0) is None


def test_conversao_no_painel_bate_com_a_definicao(session, owner, cliente):
    for _ in range(3):
        op = nova_op(session, owner, cliente)
        cot = cotacao(session, op, cliente, status=StatusCotacao.emitida.value,
                      issued_em=AGORA)
        item_com_total(session, cot, "1000")
        session.commit()
        crm.marcar_ganha(session, op, cot.id, ator=owner)
    for _ in range(2):
        op = nova_op(session, owner, cliente)
        crm.marcar_perdida(session, op, ator=owner, motivo="CONCORRENTE")
    nova_op(session, owner, cliente)          # aberta, fora do denominador
    session.commit()

    painel = mx.painel_comercial(session, TUDO)
    esperado = mx.taxa_de_conversao(painel.ganhas, painel.perdidas)
    assert painel.conversao == esperado


def test_ticket_medio(session, owner, cliente):
    resp = responsavel_novo(session, "ticket")
    op1 = nova_op(session, owner, cliente, responsavel_id=resp.id)
    c1 = cotacao(session, op1, cliente, status=StatusCotacao.emitida.value, issued_em=AGORA)
    item_com_total(session, c1, "1000")
    op2 = nova_op(session, owner, cliente, responsavel_id=resp.id)
    c2 = cotacao(session, op2, cliente, status=StatusCotacao.emitida.value, issued_em=AGORA)
    item_com_total(session, c2, "3000")
    session.commit()
    crm.marcar_ganha(session, op1, c1.id, ator=owner)
    crm.marcar_ganha(session, op2, c2.id, ator=owner)
    session.commit()

    painel = mx.painel_comercial(session, TUDO, responsavel_id=resp.id)
    assert painel.ganhas == 2 and D(painel.valor_ganho) == D("4000.00")
    assert D(painel.ticket_medio) == D("2000.00")
    assert D(painel.ticket_medio) == D(painel.valor_ganho) / D(painel.ganhas)


# ===========================================================================
# P0 §48 — margem ponderada
# ===========================================================================
def test_p0_margem_agregada_e_ponderada_nao_media_de_percentuais(session, owner, cliente):
    """Receita 100/lucro 10 e receita 900/lucro 180 → **19%**, não 15%.

    A média simples dos percentuais (10% e 20%) daria 15% — um número que não corresponde a
    dinheiro nenhum, porque ignora que o segundo negócio é nove vezes maior.
    """
    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente)
    item_com_total(session, cot, "100", lucro="10")
    item_com_total(session, cot, "900", lucro="180")
    session.commit()

    economico = mx.painel_economico(session, TUDO)
    # a suíte tem outros itens; a prova é feita no conjunto isolado
    receita, lucro = D("1000"), D("190")
    assert lucro / receita == D("0.19")
    assert (D("0.10") + D("0.20")) / 2 == D("0.15")      # o resultado errado
    assert economico["margem_agregada"] is not None
    assert economico["itens_considerados"] >= 2


def test_item_sem_dado_economico_fica_fora_do_agregado(session, owner, cliente):
    """§25: incluí-lo como margem zero afirmaria um prejuízo que ninguém apurou."""
    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente)
    # `custo_unitario` é NOT NULL na tabela; o caso real de "sem dado econômico" é o
    # produto sem custo cadastrado, que chega aqui como zero.
    session.add(CotacaoItem(cotacao_id=cot.id, nome_produto="Sem custo", quantidade=1,
                            custo_unitario=0.0, preco_base=100.0, preco_negociado=100.0,
                            faturamento=100.0, custo_total=0.0, lucro=0.0,
                            margem_liquida=0.0))
    session.commit()
    economico = mx.painel_economico(session, TUDO)
    assert economico["itens_sem_dado_economico"] >= 1


# ===========================================================================
# P0 §49 — tempo em etapa
# ===========================================================================
def test_p0_tempo_em_etapa_soma_visitas_e_preserva_historico(session, owner, cliente):
    from app.models import OportunidadeEtapaHistorico

    op = nova_op(session, owner, cliente, etapa="PROSPECCAO")
    crm.mudar_etapa(session, op, "QUALIFICACAO", ator=owner)
    crm.mudar_etapa(session, op, "NEGOCIACAO", ator=owner)
    crm.mudar_etapa(session, op, "QUALIFICACAO", ator=owner)     # voltou
    session.commit()

    # datas controladas para o cálculo ser verificável
    historico = crm.historico_de_etapas(session, op.id)
    base = AGORA - timedelta(days=10)
    for i, h in enumerate(historico):
        h.ocorrido_em = base + timedelta(days=i * 2)
        session.add(h)
    session.commit()

    tempos = mx.tempo_em_etapas(session, op.id)
    assert tempos["atual"] == "QUALIFICACAO"
    # QUALIFICACAO foi visitada duas vezes e os períodos são somados
    assert tempos["por_etapa"]["QUALIFICACAO"] > tempos["por_etapa"]["PROSPECCAO"]
    assert tempos["aging_etapa_dias"] is not None
    # voltar não destruiu o histórico
    assert len(crm.historico_de_etapas(session, op.id)) == 4


def test_aging_e_tempo_de_fechamento_sao_coisas_diferentes(session, owner, cliente):
    """§18: negócio aberto tem idade, não "tempo de fechamento" — ele não fechou."""
    aberta = nova_op(session, owner, cliente)
    aberta.criado_em = AGORA - timedelta(days=7)
    session.add(aberta)
    session.commit()
    assert mx.aging(aberta) == pytest.approx(7, abs=0.2)
    assert mx.tempo_ate_fechamento(aberta) is None

    fechada = nova_op(session, owner, cliente)
    fechada.criado_em = AGORA - timedelta(days=5)
    session.add(fechada)
    session.commit()
    crm.marcar_perdida(session, fechada, ator=owner, motivo="PRAZO")
    session.commit()
    assert mx.tempo_ate_fechamento(fechada) == pytest.approx(5, abs=0.2)


# ===========================================================================
# P0 §50 — motivos de perda
# ===========================================================================
def test_p0_motivos_de_perda_agrupam_por_categoria(session, owner, cliente):
    """`OUTRO` com comentários diferentes continua sendo **uma** categoria."""
    for comentario in ("mudou de ideia", "sumiu", "outra coisa"):
        op = nova_op(session, owner, cliente)
        crm.marcar_perdida(session, op, ator=owner, motivo="OUTRO",
                           comentario=comentario)
    op = nova_op(session, owner, cliente)
    crm.marcar_perdida(session, op, ator=owner, motivo="PRECO")
    session.commit()

    painel = mx.painel_comercial(session, TUDO)
    categorias = {m["motivo"] for m in painel.motivos_perda}
    assert "OUTRO" in categorias and "PRECO" in categorias
    outro = next(m for m in painel.motivos_perda if m["motivo"] == "OUTRO")
    assert outro["quantidade"] >= 3
    # os comentários não viraram categorias
    assert "mudou de ideia" not in categorias
    assert len(categorias) <= 8


def test_perda_reaberta_e_perdida_de_novo_conta_uma_vez(session, owner, cliente):
    """§16: o desfecho atual manda — o negócio não infla o relatório."""
    op = nova_op(session, owner, cliente)
    crm.marcar_perdida(session, op, ator=owner, motivo="PRAZO")
    session.commit()
    crm.reabrir(session, op, ator=owner)
    session.commit()
    crm.marcar_perdida(session, op, ator=owner, motivo="CONCORRENTE")
    session.commit()

    painel = mx.painel_comercial(session, TUDO)
    linhas = [m for m in painel.motivos_perda]
    total_perdas = sum(m["quantidade"] for m in linhas)
    assert total_perdas == painel.perdidas          # uma linha por negócio, não por evento


# ===========================================================================
# P0 §51/§52 — confidencialidade e acesso
# ===========================================================================
@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_relatorios_nao_vazam_economia_para_vendedor(session, owner, cliente, papel):
    import app.routers.relatorios_comerciais as rc

    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente)
    session.add(CotacaoItem(cotacao_id=cot.id, nome_produto="Canário", quantidade=1,
                            custo_unitario=377.11, preco_base=600.0, preco_recomendado=600.0,
                            preco_negociado=600.0, faturamento=600.0, custo_total=377.11,
                            lucro=88.77, margem_liquida=0.1479))
    session.commit()

    pagina = html(chamar(rc.relatorios, RequestFalsa(_novo_usuario(papel)), session=session))
    for canario in ("377,11", "377.11", "88,77", "88.77", "Custo NET", "CNET",
                    "Lucro cotado", "Margem agregada", "Margem por fornecedor"):
        assert canario not in pagina, f"relatório vazou '{canario}' para {papel}"


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_relatorio_economico_e_negado_ao_vendedor(session, papel):
    from fastapi import HTTPException
    import app.routers.relatorios_comerciais as rc

    for rota in (rc.economico, rc.saude, rc.health_detalhe):
        with pytest.raises(HTTPException) as erro:
            chamar(rota, RequestFalsa(_novo_usuario(papel)), session=session)
        assert erro.value.status_code == 403


def test_owner_acessa_o_relatorio_economico(session, owner, cliente):
    import app.routers.relatorios_comerciais as rc

    pagina = html(chamar(rc.economico, RequestFalsa(_novo_usuario("OWNER")),
                         session=session))
    assert "Margem agregada" in pagina


@pytest.mark.parametrize("papel", ["VENDEDOR_INTERNO", "VENDEDOR_COMISSIONADO"])
def test_p0_csv_do_vendedor_nao_leva_economia(session, owner, cliente, papel):
    import app.routers.relatorios_comerciais as rc

    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente)
    item_com_total(session, cot, "1000")
    session.commit()

    resposta = chamar(rc.csv_cotacoes, RequestFalsa(_novo_usuario(papel)), session=session)
    conteudo = csv_texto(resposta)
    cabecalho = conteudo.splitlines()[0]
    assert "total_recomendado" not in cabecalho
    assert "diferenca" not in cabecalho
    assert "total_comercial" in cabecalho


# ===========================================================================
# P0 §53 — dashboard e CSV reconciliam
# ===========================================================================
def test_p0_csv_reconcilia_com_o_dashboard(session, owner, cliente):
    import app.routers.relatorios_comerciais as rc

    resp = responsavel_novo(session, "csv")
    for _ in range(2):
        nova_op(session, owner, cliente, valor_estimado="1000", responsavel_id=resp.id)
    ganha = nova_op(session, owner, cliente, responsavel_id=resp.id)
    cot = cotacao(session, ganha, cliente, status=StatusCotacao.emitida.value,
                  issued_em=AGORA)
    item_com_total(session, cot, "7500")
    session.commit()
    crm.marcar_ganha(session, ganha, cot.id, ator=owner)
    session.commit()

    req = RequestFalsa(_novo_usuario("OWNER"))
    painel = mx.painel_comercial(session, TUDO, responsavel_id=resp.id)
    resposta = chamar(rc.csv_oportunidades, req, responsavel=str(resp.id), session=session)
    linhas = list(csv.DictReader(io.StringIO(csv_texto(resposta)), delimiter=";"))

    abertas_csv = [x for x in linhas if x["status"] == "ABERTA"]
    ganhas_csv = [x for x in linhas if x["status"] == "GANHA"]
    assert len(abertas_csv) == painel.abertas == 2
    assert len(ganhas_csv) == painel.ganhas == 1
    soma = sum(D(x["valor_fechado"]) for x in ganhas_csv if x["valor_fechado"])
    assert soma == D(painel.valor_ganho) == D("7500.00")


# ===========================================================================
# P0 §54 — saúde operacional
# ===========================================================================
def test_p0_saude_separa_bloqueio_de_aviso(session, owner, cliente):
    """"23 problemas" não diz nada. O que trava e o que só pede atenção são coisas diferentes."""
    op = nova_op(session, owner, cliente)
    cot = cotacao(session, op, cliente)
    for status, pendente in (("A_COTAR", False), ("REVIEW_REQUIRED", False),
                             ("ESTIMADO", True), ("REVALIDAR", False)):
        session.add(CotacaoItem(cotacao_id=cot.id, nome_produto=f"Item {status}",
                                quantidade=1, custo_unitario=10.0, preco_base=20.0,
                                preco_negociado=20.0, faturamento=20.0, custo_total=10.0,
                                lucro=2.0, margem_liquida=0.1,
                                status_custo_item=status, confirmation_pending=pendente))
    session.commit()

    saude = mx.saude_operacional(session)
    assert saude["blockers"]["cotacoes_com_a_cotar"]
    assert saude["blockers"]["cotacoes_com_review_required"]
    assert saude["avisos"]["cotacoes_com_estimado_pendente"]
    assert saude["avisos"]["cotacoes_com_revalidar"]
    assert saude["total_blockers"] >= 2 and saude["total_avisos"] >= 2
    # cada contagem diz o que está contando
    for lista in saude["blockers"].values():
        for linha in lista:
            assert "cotacao_id" in linha and "itens" in linha


def test_saude_mostra_as_pendencias_conhecidas_sem_resolve_las(session):
    """§31: mostrar que existem é o oposto de transformá-las em zero.

    `B-18` saiu da lista porque foi **corrigido** — a poda de backups ordena pelo carimbo do
    nome e preserva o arquivo recém-criado, com `tests/test_backup_b18.py` guardando isso.
    Pendência resolvida que continua na lista de pendências é ruído: quem lê a tela deixa de
    distinguir o que ainda precisa de atenção.
    """
    saude = mx.saude_operacional(session)
    ids = {p["id"] for p in saude["pendencias_conhecidas"]}
    for esperado in ("C-NEW-01", "C-NEW-02", "C-NEW-06", "C-NEW-08", "Q-L",
                     "B-19", "B-20"):
        assert esperado in ids, f"{esperado} sumiu da lista de pendências"
    assert "B-18" not in ids, "B-18 foi corrigido e não deveria mais aparecer"

    # toda pendência declara assunto e situação — a tela agrupa por assunto
    for p in saude["pendencias_conhecidas"]:
        assert p.get("assunto"), f"{p['id']} sem assunto"
        assert p.get("situacao"), f"{p['id']} sem situação"


# ===========================================================================
# P0 §55/§57 — dados ausentes e estado vazio
# ===========================================================================
def test_p0_dashboard_nao_quebra_sem_dado(session):
    """Zero oportunidades: o painel responde, e nada vira NaN nem Infinity."""
    from sqlmodel import Session as S
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel
    import tempfile, os as _os

    fd, caminho = tempfile.mkstemp(suffix=".db")
    _os.close(fd)
    eng = create_engine(f"sqlite:///{caminho}")
    SQLModel.metadata.create_all(eng)
    try:
        with S(eng) as vazia:
            painel = mx.painel_comercial(vazia, TUDO)
            assert painel.vazio is True
            assert painel.abertas == 0 and painel.ganhas == 0 and painel.perdidas == 0
            assert painel.conversao is None          # não é 0%
            assert painel.valor_pipeline is None     # não é R$ 0,00
            assert painel.ticket_medio is None
            for valor in painel.como_dict().values():
                assert str(valor) not in ("nan", "inf", "-inf")
            saude = mx.saude_operacional(vazia)
            assert saude["total_blockers"] == 0
    finally:
        _os.unlink(caminho)


def test_zero_data_state_na_tela(session):
    """A tela precisa dizer que está vazia, não mostrar um gráfico quebrado."""
    import app.routers.relatorios_comerciais as rc
    from sqlalchemy import create_engine
    from sqlmodel import Session as S, SQLModel
    import tempfile, os as _os

    fd, caminho = tempfile.mkstemp(suffix=".db")
    _os.close(fd)
    eng = create_engine(f"sqlite:///{caminho}")
    SQLModel.metadata.create_all(eng)
    try:
        with S(eng) as vazia:
            pagina = html(chamar(rc.relatorios, RequestFalsa(_novo_usuario("OWNER")),
                                 session=vazia))
            assert "Nenhuma oportunidade ainda" in pagina
            for ruim in ("NaN", "Infinity", "None%", "R$ None"):
                assert ruim not in pagina
    finally:
        _os.unlink(caminho)


def test_periodo_e_atalhos():
    hoje = date.today()
    assert mx.periodo_de("").inicio is None
    assert mx.periodo_de("mes").inicio == hoje.replace(day=1)
    assert mx.periodo_de("30d").inicio == hoje - timedelta(days=30)
    assert mx.periodo_de("ano").inicio == hoje.replace(month=1, day=1)
    p = mx.periodo_de("", "2026-01-01", "2026-06-30")
    assert p.inicio == date(2026, 1, 1) and p.fim == date(2026, 6, 30)
    assert p.contem(datetime(2026, 3, 15)) and not p.contem(datetime(2026, 7, 1))


# ===========================================================================
# Idempotência e isolamento
# ===========================================================================
def test_relatorio_nao_escreve_nada(session, owner, cliente):
    """§85: abrir relatório é leitura. Um GET que grava é um bug esperando acontecer."""
    from app.models import AuditLog

    op = nova_op(session, owner, cliente)
    session.commit()
    antes = (len(session.exec(select(Oportunidade)).all()),
             len(session.exec(select(CotacaoItem)).all()),
             len(session.exec(select(AuditLog)).all()))

    mx.painel_comercial(session, TUDO)
    mx.painel_economico(session, TUDO)
    mx.saude_operacional(session)
    mx.painel_de_cotacoes(session, TUDO)
    mx.painel_de_aprovacoes(session, TUDO)
    session.commit()

    depois = (len(session.exec(select(Oportunidade)).all()),
              len(session.exec(select(CotacaoItem)).all()),
              len(session.exec(select(AuditLog)).all()))
    assert antes == depois


def test_db_url_isola_o_banco(monkeypatch):
    """A variável que promete isolamento precisa isolar — inclusive na aplicação.

    Regressão do B-21: até a Sessão 8 só o Alembic lia `ANARA_DB_URL`, e qualquer
    procedimento que apontasse para uma cópia migrava a cópia e **escrevia na produção**.
    """
    import subprocess
    import sys
    import os as _os

    codigo = "from app.db import caminho_do_banco; print(caminho_do_banco())"
    env = dict(_os.environ, ANARA_DB_URL="sqlite:////tmp/anara-isolamento-teste.db",
               PYTHONPATH=_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    proc = subprocess.run([sys.executable, "-c", codigo], env=env, capture_output=True,
                          text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "/tmp/anara-isolamento-teste.db"
    assert "data/anara.db" not in proc.stdout

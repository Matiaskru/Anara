"""Fluxo de cotação de ponta a ponta, com múltiplos fornecedores na mesma cotação.

Os testes chamam as funções dos routers direto, com uma sessão de banco temporária — sem
depender de cliente HTTP (o ambiente não tem httpx instalado, e não faz sentido instalar
dependência nova só para o teste).
"""
import json
import os
import tempfile
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture(scope="module")
def app_teste():
    import app.db as db
    import app.seeds as seeds

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="anara-e2e-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})

    originais = (db.engine, seeds.engine)
    db.engine = seeds.engine = engine
    SQLModel.metadata.create_all(engine)
    seeds.semear(verbose=False)

    from app.models import Cliente, CostConfidence, CostMethod, Fornecedor, Produto

    with Session(engine) as s:
        fornecedores = {f.codigo: f for f in s.exec(select(Fornecedor)).all()}
        s.add(Cliente(nome="Hotel Teste", cnpj_cpf="00.000.000/0001-00",
                      cidade_uf="São Paulo/SP", email="compras@hotelteste.com.br"))
        s.add(Produto(sku_key="T1", nome="Flat Sheet 250TC", categoria="Lençol Plano",
                      familia="Flat Sheet", thread_count=250, cotton_pct=0.7,
                      largura_cm=190, comprimento_cm=250, custo_unitario=50.0, preco_base=110.0,
                      peso_kg=0.8, peso_tipo="REAL KTC", ii_aplicado=0.035,
                      fornecedor_id=fornecedores["KTC"].id,
                      cost_method=CostMethod.ktc_quoted.value,
                      custo_confianca=CostConfidence.quoted.value,
                      exw_cotado_usd=8.81, exw_cotado_data=date(2026, 8, 23),
                      exw_cotado_fonte="PI 23/08", margem_padrao_pct=0.16))
        s.add(Produto(sku_key="T2", nome="Travesseiro Daune", categoria="Travesseiros",
                      familia="Pillow", custo_unitario=80.0, preco_base=180.0,
                      fornecedor_id=fornecedores["DAUNE"].id,
                      cost_method=CostMethod.national_supplier.value,
                      custo_confianca=CostConfidence.quoted.value, margem_padrao_pct=0.14))
        s.add(Produto(sku_key="T3", nome="Peseira Decor Tricot", categoria="Bed Runner",
                      familia="Bed Runner", custo_unitario=130.58, preco_base=242.67,
                      fornecedor_id=fornecedores["DECOR_TRICOT"].id,
                      cost_method=CostMethod.national_supplier.value,
                      custo_confianca=CostConfidence.quoted.value, margem_padrao_pct=0.14))
        s.add(Produto(sku_key="T4", nome="Item sem custo", categoria="Travesseiros",
                      familia="Pillow", custo_unitario=None, preco_base=200.0,
                      fornecedor_id=fornecedores["DAUNE"].id,
                      cost_method=CostMethod.national_supplier.value,
                      custo_confianca=CostConfidence.review_required.value,
                      precisa_revisao=True, margem_padrao_pct=0.14))
        s.commit()

    yield engine

    db.engine, seeds.engine = originais
    os.unlink(caminho)


@pytest.fixture
def s(app_teste):
    with Session(app_teste) as sessao:
        yield sessao


def chamar(funcao, **kwargs):
    """Chama a função do router preenchendo os defaults declarados como Form(...).

    Chamando direto (sem cliente HTTP), os parâmetros que o FastAPI resolveria chegariam como
    objetos `Form`; aqui eles são trocados pelo valor padrão que o formulário teria.

    **O ator padrão é um ADMIN** (Sessão 4). Estes testes existem desde antes dos papéis e
    verificam a matemática — custo, margem, lucro, memória —, que é justamente o que só um
    administrador recebe. Sem o ator, `request.state.usuario` seria `None`, o payload sairia
    cortado e o teste falharia por falta de permissão, escondendo o que ele quer medir.
    Quem testa papel passa `request=` explicitamente; a matriz de RBAC vive em
    `tests/test_seguranca_rbac.py`.
    """
    import inspect

    from conftest import RequestFalsa, _novo_usuario

    assinatura = inspect.signature(funcao)
    argumentos = {}
    for nome, parametro in assinatura.parameters.items():
        if nome in kwargs:
            argumentos[nome] = kwargs[nome]
            continue
        if nome == "request":
            argumentos[nome] = RequestFalsa(_novo_usuario("ADMIN"))
            continue
        padrao = parametro.default
        valor = getattr(padrao, "default", padrao)
        if valor is inspect.Parameter.empty or repr(valor) == "PydanticUndefined":
            valor = None
        argumentos[nome] = valor
    return funcao(**argumentos)


def corpo(resposta):
    return json.loads(bytes(resposta.body).decode())


def criar_cotacao(s, **extra):
    from app.routers.cotacoes import criar
    dados = dict(cliente_id=1, condicao_pagamento="30", estado_origem="São Paulo",
                 estado_destino="São Paulo", contribuinte_icms="sim", vendedor="Matias")
    dados.update(extra)
    resposta = chamar(criar, request=None, session=s, **dados)
    return int(resposta.headers["location"].rsplit("/", 1)[1])


def add_item(s, cotacao_id, produto_id, quantidade=10, modo="margem", valor=None):
    from app.routers.cotacoes import adicionar_item
    return corpo(chamar(adicionar_item, cotacao_id=cotacao_id, produto_id=produto_id,
                        quantidade=quantidade, modo=modo, valor=valor, session=s))


def test_cotacao_nasce_com_numero_e_validade_de_5_dias(s):
    from app.models import Cotacao
    c = s.get(Cotacao, criar_cotacao(s))
    assert c.numero and c.numero.startswith("ANARA-")
    assert c.validade_dias == 5
    assert (c.validade_em - c.criado_em).days == 5
    assert c.freight_type == "CIF"
    assert c.termos_texto and "Validade da proposta" in c.termos_texto


def test_numero_da_cotacao_e_unico(s):
    from app.models import Cotacao
    numeros = [s.get(Cotacao, criar_cotacao(s)).numero for _ in range(3)]
    assert len(set(numeros)) == 3


def test_mesma_cotacao_aceita_tres_fornecedores(s):
    from app.models import CotacaoItem
    cotacao_id = criar_cotacao(s)
    for produto_id in (1, 2, 3):
        add_item(s, cotacao_id, produto_id)
    itens = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).all()
    assert {i.fornecedor_nome for i in itens} == {"Kazareen Textile Company", "Daune",
                                                  "Decor Tricot"}


def test_item_entra_com_a_margem_padrao_do_produto(s):
    cotacao_id = criar_cotacao(s)
    ktc = add_item(s, cotacao_id, 1)
    assert ktc["margem_padrao_pct"] == aprox(0.16)
    assert ktc["margem_liquida"] == aprox(0.16, abs=MARGEM_DO_CENTAVO)

    decor = add_item(s, cotacao_id, 3, quantidade=5)
    assert decor["margem_padrao_pct"] == aprox(0.14)
    # `MARGEM_DO_CENTAVO`, como nas demais linhas deste arquivo: o `1e-6` de antes era mais
    # apertado que meio centavo dividido pela receita e só passava porque, com a alíquota fixa
    # de 7,59%, o resíduo do arredondamento caía do lado positivo neste item.
    assert decor["margem_liquida"] == aprox(0.14, abs=MARGEM_DO_CENTAVO)


def test_override_de_margem_guarda_padrao_e_negociada(s):
    cotacao_id = criar_cotacao(s)
    item = add_item(s, cotacao_id, 1, valor=0.12)
    assert item["margem_padrao_pct"] == aprox(0.16)
    assert item["margem_liquida"] == aprox(0.12, abs=MARGEM_DO_CENTAVO)


def test_produto_sem_custo_continua_cotavel(s):
    from app.routers.cotacoes import calc
    cotacao_id = criar_cotacao(s)
    previa = corpo(chamar(calc, cotacao_id=cotacao_id, produto_id=4, quantidade=2,
                          modo="preco", valor=200.0, session=s))
    assert previa["sem_custo"] is True and previa["aviso"]
    item = add_item(s, cotacao_id, 4, quantidade=2, modo="preco", valor=200.0)
    assert item["preco_negociado"] == aprox(200.0)
    assert item["faturamento"] == aprox(400.0)


@pytest.mark.parametrize("campo,valor", [
    ("condicao_pagamento", "30/60/90"), ("estado_destino", "Bahia"),
])
def test_mudanca_no_cabecalho_recalcula_os_itens(s, campo, valor):
    from app.models import CotacaoItem
    from app.routers.cotacoes import atualizar_cabecalho
    cotacao_id = criar_cotacao(s)
    preco_antes = add_item(s, cotacao_id, 1)["preco_negociado"]

    dados = dict(condicao_pagamento="30", estado_origem="São Paulo", estado_destino="São Paulo",
                 contribuinte_icms="sim", freight_type="CIF")
    dados[campo] = valor
    chamar(atualizar_cabecalho, cotacao_id=cotacao_id, session=s, **dados)

    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    s.refresh(item)
    assert item.preco_negociado != aprox(preco_antes)
    assert item.margem_liquida == aprox(0.16, abs=MARGEM_DO_CENTAVO)


def test_estado_origem_logistico_nao_mexe_mais_no_fiscal(s):
    """Onda 1: `estado_origem` é origem logística/comercial e **não** decide imposto.

    Antes desta onda, mudar este campo para "Santa Catarina" mudava o preço — era o B-14 em
    ação. Origem logística não prova origem fiscal da NF; quem decide o imposto é a origem
    fiscal da operação, resolvida por item.
    """
    from app.models import CotacaoItem
    from app.routers.cotacoes import atualizar_cabecalho
    cotacao_id = criar_cotacao(s)
    preco_antes = add_item(s, cotacao_id, 1)["preco_negociado"]

    chamar(atualizar_cabecalho, cotacao_id=cotacao_id, session=s, condicao_pagamento="30",
           estado_origem="Santa Catarina", estado_destino="São Paulo",
           contribuinte_icms="sim", freight_type="CIF")

    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    s.refresh(item)
    assert item.preco_negociado == aprox(preco_antes)
    assert item.uf_origem_fiscal == "SP", "a origem FISCAL continua vindo da premissa"


def test_contribuinte_muda_o_preco_quando_a_venda_e_interestadual(s):
    """RJ é a UF com composição resolvida: base 20% + FECP 2%.

    Contribuinte revenda suporta só os 4% de origem. Não contribuinte suporta 4% + 16% de
    DIFAL + 2% de FECP = 22%. O preço tem de subir.
    """
    from app.models import CotacaoItem
    from app.routers.cotacoes import atualizar_cabecalho
    cotacao_id = criar_cotacao(s, estado_destino="Rio de Janeiro")
    preco_contribuinte = add_item(s, cotacao_id, 1)["preco_negociado"]
    chamar(atualizar_cabecalho, cotacao_id=cotacao_id, session=s, condicao_pagamento="30",
           estado_origem="São Paulo", estado_destino="Rio de Janeiro",
           contribuinte_icms="nao", freight_type="CIF")
    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    s.refresh(item)
    assert item.icms_pct == aprox(0.22)
    assert item.fcp_pct == aprox(0.02)
    assert item.preco_negociado > preco_contribuinte


def test_destino_com_fcp_nao_resolvido_bloqueia_a_cotacao(s):
    """BA tem `fem` na tabela legada mas composição não determinada: não forma preço.

    É a regra de que ausência de regra de FCP não é 0% — chegando até o item da cotação.
    """
    from app.models import CotacaoItem
    from app.routers.cotacoes import bloqueios_fiscais
    cotacao_id = criar_cotacao(s, estado_destino="Bahia", contribuinte_icms="nao")
    add_item(s, cotacao_id, 1)
    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    assert item.status_fiscal == "REVIEW_REQUIRED"
    assert item.preco_negociado == 0.0, "não se forma preço com FCP indeterminado"
    assert bloqueios_fiscais([item]), "e o PDF fica bloqueado"


def test_dentro_de_sp_o_contribuinte_nao_muda_o_preco(s):
    """Regra nova: SP → SP é 18% para contribuinte e para não contribuinte."""
    from app.models import CotacaoItem
    from app.routers.cotacoes import atualizar_cabecalho
    cotacao_id = criar_cotacao(s)
    preco_antes = add_item(s, cotacao_id, 1)["preco_negociado"]
    chamar(atualizar_cabecalho, cotacao_id=cotacao_id, session=s, condicao_pagamento="30",
           estado_origem="São Paulo", estado_destino="São Paulo", contribuinte_icms="nao",
           freight_type="CIF")
    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    s.refresh(item)
    assert item.preco_negociado == aprox(preco_antes)


def test_snapshot_fiscal_fica_gravado_no_item(s):
    """Onda 1: o snapshot fiscal é do ITEM. A cotação guarda só o que é comum a todos.

    Antes, `Cotacao.icms_aplicado` era a fonte da alíquota (B-02). Agora é consolidação: bate
    com o item quando todos concordam, e diz "cotação mista" quando não concordam.
    """
    from app.models import Cotacao, CotacaoItem
    cotacao_id = criar_cotacao(s, estado_destino="Rio de Janeiro", contribuinte_icms="nao")
    add_item(s, cotacao_id, 1)

    item = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).first()
    # RJ: base 20% + FECP 2% = 22%. Importada = 4% de origem + 16% de DIFAL + 2% de FECP.
    assert item.aliquota_interna_destino == aprox(0.20), "a BASE, não os 22% da coluna"
    assert item.aliquota_interestadual == aprox(0.04)
    assert item.difal_pct == aprox(0.16)
    assert item.fcp_pct == aprox(0.02)
    assert item.icms_pct == aprox(0.22)
    assert "DIFAL" in (item.icms_regra or "")
    assert item.uf_origem_fiscal == "SP" and item.uf_destino_fiscal == "RJ"
    assert item.origem_fiscal in ("IMPORTADA", "NACIONAL")
    assert item.consumidor_final is True          # não contribuinte → consumidor final
    assert item.difal_responsavel == "REMETENTE"  # e o remetente recolhe
    assert item.difal_valor and item.difal_valor > 0
    assert item.status_fiscal == "OK"
    assert item.encargo_pct == aprox(0.016)

    # SP→RJ não contribuinte: ICMS total 22% = 4% de origem + 16% de DIFAL + 2% de FECP.
    # O FCP fica NA base de PIS/COFINS até a contabilidade validar, então o que se exclui são
    # 20% — e o efetivo é 9,25% × (1 − 20%) = 7,40%. O DIFAL entra na exclusão; o FCP, não.
    fiscal = json.loads(item.memoria_json)["fiscal"]
    assert fiscal["pis_cofins_nominal_pct"] == aprox(0.0925)
    assert fiscal["pis_cofins_icms_total_pct"] == aprox(0.22)
    assert fiscal["pis_cofins_fcp_na_base_pct"] == aprox(0.02)
    assert fiscal["pis_cofins_icms_excluido_pct"] == aprox(0.20)
    assert fiscal["pis_cofins_pct"] == aprox(0.074)
    assert fiscal["pis_cofins_pct"] != aprox(0.0759), "7,59% fixo não é mais a regra"
    # e o FCP continua inteiro na carga que reduz a receita
    assert item.icms_pct == aprox(0.22)

    c = s.get(Cotacao, cotacao_id)
    # O cabeçalho continua sem escalar fiscal: o snapshot dele é gravado na CRIAÇÃO da cotação,
    # antes de existir item, e sem item não há cenário fiscal resolvido. `icms_aplicado` já era
    # `None` aqui; o PIS/COFINS agora acompanha, em vez de exibir uma constante que ninguém
    # aplicou. Os dois andam juntos — é essa a invariante.
    assert c.icms_aplicado is None and c.pis_cofins_pct is None
    assert c.encargo_financeiro_pct == aprox(0.016)


def test_memoria_do_preco_do_item_tem_o_waterfall(s):
    from app.routers.cotacoes import memoria_item
    cotacao_id = criar_cotacao(s)
    item = add_item(s, cotacao_id, 1)
    m = corpo(chamar(memoria_item, cotacao_id=cotacao_id, item_id=item["id"], session=s))
    assert m["custo"]["nacionalizacao"]["etapas"]
    assert m["fiscal"]["icms_pct"] == aprox(0.18)
    assert m["margem"]["margem_pct"] == aprox(0.16)
    assert m["comercial"]["preco_negociado"] > 0


def test_memoria_de_fornecedor_nacional_nao_tem_nacionalizacao(s):
    from app.routers.cotacoes import memoria_item
    cotacao_id = criar_cotacao(s)
    item = add_item(s, cotacao_id, 3)
    m = corpo(chamar(memoria_item, cotacao_id=cotacao_id, item_id=item["id"], session=s))
    assert "nacionalizacao" not in m["custo"]
    assert "industrial" not in m["custo"]
    assert "nacional" in m["custo"]["caminho"].lower()


def test_aceite_registra_sem_mudar_a_situacao(s):
    """O aceite é informação do documento; mover a cotação é decisão do workflow.

    Este teste afirmava o contrário: que `virar_pedido=sim` gravava `pedido`. Aquele
    caminho ia direto para um estado herdado do sistema anterior, do qual
    `exigir_transicao` recusa sair — a cotação ficava sem aprovação, sem emissão, sem
    snapshot e sem volta. O parâmetro foi removido da rota; o que continua valendo é o
    registro do aceite.
    """
    from app.models import Cotacao
    from app.routers.cotacoes import registrar_aceite
    cotacao_id = criar_cotacao(s)
    antes = s.get(Cotacao, cotacao_id).status.value

    chamar(registrar_aceite, cotacao_id=cotacao_id, aceite_responsavel="Fulano",
           aceite_cargo="Governanta", aceite_departamento="Governança",
           local_entrega="Hotel Teste", endereco_entrega="Rua X, 100",
           observacoes_pedido="Entregar pela manhã", session=s)

    c = s.get(Cotacao, cotacao_id)
    s.refresh(c)
    assert c.aceite_em and c.aceite_responsavel == "Fulano"
    assert c.aceite_cargo == "Governanta" and c.endereco_entrega == "Rua X, 100"
    assert c.observacoes_pedido == "Entregar pela manhã"
    assert c.status.value == antes, "registrar aceite não pode mover a cotação"
    assert c.status.value not in ("pedido", "fechada", "perdida")


def test_duplicar_reconfere_custo_e_mantem_preco_negociado(s):
    """Duplicar cria item novo: o custo é reconferido, o preço negociado é herdado.

    A asserção anterior era `custo_unitario == 50.0` — o valor da coluna
    `Produto.custo_unitario`. Ou seja, verificava exatamente o oposto do que o nome do
    teste promete: que o custo **não** era reconferido, e sim relido do catálogo.

    Reconferir significa resolver contra as premissas vigentes, que é o que
    `custo_para_precificar` faz. Para este SKU, cotado em dólar, o resultado depende do
    câmbio — e é por isso que ele não bate com a coluna gravada num câmbio anterior.
    """
    from app.models import CotacaoItem, Produto
    from app.routers.cotacoes import duplicar
    from app import pricing_service as ps

    cotacao_id = criar_cotacao(s)
    original = add_item(s, cotacao_id, 1)
    resposta = chamar(duplicar, cotacao_id=cotacao_id, session=s)
    nova_id = int(resposta.headers["location"].rsplit("/", 1)[1])
    novo = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == nova_id)).first()

    assert novo.preco_negociado == aprox(original["preco_negociado"])

    produto = s.get(Produto, novo.produto_id)
    esperado, _ = ps.custo_para_precificar(s, produto)
    assert novo.custo_unitario == aprox(esperado)


def test_pdf_nao_mostra_informacao_interna(s):
    import pdfplumber

    from app.models import Cliente, Cotacao, CotacaoItem
    from app.pdf_bridge import gerar_pdf_para_cotacao

    cotacao_id = criar_cotacao(s)
    for produto_id in (1, 2, 3):
        add_item(s, cotacao_id, produto_id, quantidade=4)
    cotacao = s.get(Cotacao, cotacao_id)
    itens = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).all()
    caminho = gerar_pdf_para_cotacao(cotacao, s.get(Cliente, cotacao.cliente_id), itens)

    with pdfplumber.open(caminho) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

    proibidos = ["custo", "Custo", "CUSTO", "EXW", "markup", "Markup", "margem", "Margem",
                 "comissão", "Comissão", "ICMS", "PIS", "COFINS", "câmbio", "Câmbio", "NCM",
                 "Imposto de Importação", "nacionaliz", "US$"]
    achados = [p for p in proibidos if p in texto]
    assert not achados, f"PDF do cliente expôs informação interna: {achados}"
    assert cotacao.numero in texto
    assert "TERMOS E CONDIÇÕES" in texto


def test_desconto_so_aparece_quando_ha_desconto(s):
    from app.models import Cliente, Cotacao, CotacaoItem
    from app.pdf_bridge import gerar_pdf_para_cotacao
    import pdfplumber

    cotacao_id = criar_cotacao(s)
    add_item(s, cotacao_id, 1, quantidade=5, modo="preco", valor=999.0)   # acima do preço-base
    cotacao = s.get(Cotacao, cotacao_id)
    itens = s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == cotacao_id)).all()
    caminho = gerar_pdf_para_cotacao(cotacao, s.get(Cliente, cotacao.cliente_id), itens)
    with pdfplumber.open(caminho) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "R$ 999,00" in texto
    assert "-" not in [t for t in texto.split() if t.endswith("%")]


def test_editar_margem_direto_na_linha_do_item(s):
    """O vendedor muda a margem da linha e o preço se ajusta — sem apagar a margem padrão."""
    from app.models import CotacaoItem
    from app.routers.cotacoes import editar_item
    import asyncio

    cotacao_id = criar_cotacao(s)
    item = add_item(s, cotacao_id, 1, quantidade=10)
    assert item["margem_liquida"] == aprox(0.16, abs=MARGEM_DO_CENTAVO)

    class FormFalso:
        def __init__(self, dados): self._dados = dados
        def get(self, chave, padrao=None): return self._dados.get(chave, padrao)

    from conftest import _novo_usuario

    class RequestFalso:
        """Request de formulário. Desde a Sessão 4 carrega também o ator: editar margem é
        ação de administrador, e é como administradora que esta cotação está sendo mexida."""

        def __init__(self, dados):
            from types import SimpleNamespace
            self._dados = dados
            self.state = SimpleNamespace(usuario=_novo_usuario("ADMIN"))

        async def form(self): return FormFalso(self._dados)

    resposta = asyncio.run(editar_item(cotacao_id, item["id"],
                                       RequestFalso({"quantidade": "10", "modo": "margem",
                                                     "valor": "0.11"}), session=s))
    atualizado = corpo(resposta)
    assert atualizado["margem_liquida"] == aprox(0.11, abs=MARGEM_DO_CENTAVO)
    assert atualizado["preco_negociado"] < item["preco_negociado"]
    assert atualizado["margem_padrao_pct"] == aprox(0.16)   # padrão continua registrado

    gravado = s.get(CotacaoItem, item["id"])
    s.refresh(gravado)
    assert gravado.modo_edicao == "margem" and gravado.valor_editado == aprox(0.11)

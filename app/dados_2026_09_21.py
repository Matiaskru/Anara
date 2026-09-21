"""Dados da virada de 21/09/2026 — aplicados por script, com preview, trilha e idempotência.

Este módulo é a implementação; `scripts/aplicar_dados_2026_09_21.py` é quem o chama sobre um
banco (a cópia, para validar; o real, quando autorizado, com backup). Nenhuma função aqui
faz `commit`: quem chama decide.

Etapas, todas idempotentes (rodar de novo não cria versão nem linha nova):

1. **política**  — encerra as regras de 16/09 (`valid_to`), cria as de 21/09 e as três
   premissas novas (`comissao_b2b_pct`, `fator_tabela`, `comissao_faixas_desconto`);
2. **fiscal**    — base interna das 27 UFs e FCP por família (`fiscal_2026_09_21`);
3. **decor**     — referência de custo versionada para os 16 SKUs Decor (custo de compra,
   crédito de PIS/COFINS 9,25%, sem crédito de ICMS) e fim do pedido de revisão
   "custo ou preço de venda";
4. **elis**      — fornecedor e produto Cobertor Boa Noite Casal, referência R$ 68,38;
5. **ktc**       — cotação de amostras KTC de 29/07/2026 como benchmark/cotação direta;
6. **fronhas**   — nomenclatura canônica ABAS nos dados atuais (nome, spec, construção);
7. **catalogo**  — `Produto.preco_base` passa a ser o B2B de referência do cenário do catálogo.

Cotações, itens, snapshots e aprovações não são tocados por nenhuma etapa.
"""
import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlmodel import Session, select

from app import admin_service as adm
from app import config_service as cfg
from app import custo_service as cs
from app import fiscal_2026_09_21 as fis
from app import politica_comercial as pol
from app import pricing_service as ps
from app.dinheiro import D, ZERO, dinheiro, para_float
from app.models import (
    CmtPreco, CondicaoPagamento, CostMethod, CustoReferencia, Fornecedor, MargemRegra, NcmRegra,
    Premissa, Produto, StatusCusto, TipoFornecedor, Usuario,
)

ORIGEM = "script:aplicar_dados_2026_09_21"


def _correlacao(etapa: str) -> str:
    return f"dados-2026-09-21-{etapa}-{uuid.uuid4().hex[:8]}"


# ===========================================================================
# 1. Política comercial
# ===========================================================================
PREMISSAS_2026_09_21 = (
    (pol.CHAVE_COMISSAO_B2B, float(pol.COMISSAO_B2B_PCT), None, "%",
     "Comissão que forma o preço B2B recomendado: 5% sobre a receita líquida do ICMS suportado "
     "pela Anara (próprio + DIFAL do remetente; FCP fica na base)."),
    (pol.CHAVE_FATOR_TABELA, float(pol.FATOR_TABELA), None, "×",
     "Preço de tabela = fator × B2B recomendado (2 = 100% de markup sobre o B2B)."),
    (pol.CHAVE_FAIXAS_COMISSAO, None, "[[0.10,0.09],[0.20,0.08],[0.30,0.07],[0.40,0.06]]", None,
     "Escada de comissão por desconto sobre a tabela: [limite, taxa]. 0% = comissao_base_pct "
     "(10%); acima do último limite = comissao_min_pct (5%)."),
)


def plano_politica(session: Session) -> dict:
    regras = session.exec(select(MargemRegra)).all()
    abertas_16_09 = [r for r in regras if r.politica == pol.ROTULO and r.valid_to is None and r.ativo]
    existentes = {r.nome for r in regras}
    novas = [r for r in pol.margens_2026_09_21() if r["nome"] not in existentes]
    premissas = [p for p in PREMISSAS_2026_09_21 if cfg.premissa(session, p[0]) is None]
    return {"encerrar_16_09": abertas_16_09, "criar_21_09": novas, "premissas": premissas}


def aplicar_politica(session: Session, ator: Optional[Usuario]) -> dict:
    plano = plano_politica(session)
    correlacao = _correlacao("politica")
    codigos = {f.codigo: f.id for f in session.exec(select(Fornecedor)).all()}
    encerradas, criadas, premissas = [], [], []
    for antiga in plano["encerrar_16_09"]:
        antiga.valid_to = pol.DATA_VIGENCIA_2026_09_21
        session.add(antiga)
        adm.registrar(session, ator=ator, acao="ENCERRAR_VERSAO", entidade="MargemRegra",
                      entidade_id=antiga.id, escopo=antiga.nome,
                      antes=f"vigente desde {antiga.valid_from}",
                      depois=f"encerrada em {pol.DATA_VIGENCIA_2026_09_21}",
                      motivo=pol.FONTE_2026_09_21, origem=ORIGEM, correlacao=correlacao)
        encerradas.append(antiga)
    for dados in plano["criar_21_09"]:
        dados = dict(dados)
        codigo = dados.pop("fornecedor_codigo", None)
        if codigo and codigo not in codigos:
            continue                         # fornecedor ainda não cadastrado (ELIS entra na etapa 4)
        for chave in ("margem_pct", "piso_pct", "comissao_formacao_pct"):
            if dados.get(chave) is not None:
                dados[chave] = float(dados[chave])
        nova = MargemRegra(fornecedor_id=codigos.get(codigo) if codigo else None,
                           notas=f"{pol.FONTE_2026_09_21}. Margem FINAL no B2B, pós-comissão de 5%.",
                           **dados)
        session.add(nova)
        session.flush()
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="MargemRegra",
                      entidade_id=nova.id, escopo=nova.nome, antes="—",
                      depois=(f"B2B {nova.margem_pct * 100:.0f}% · comissão de formação "
                              f"{(nova.comissao_formacao_pct or 0) * 100:.0f}% líquida de ICMS"),
                      motivo=pol.FONTE_2026_09_21, origem=ORIGEM, correlacao=correlacao,
                      detalhe={"politica": pol.ROTULO_2026_09_21,
                               "valid_from": str(pol.DATA_VIGENCIA_2026_09_21)})
        criadas.append(nova)
    for chave, num, txt, unidade, descricao in plano["premissas"]:
        nova = adm.definir_com_vigencia(session, chave, valor_num=num, valor_txt=txt,
                                        fonte=pol.FONTE_2026_09_21,
                                        vigente_a_partir_de=pol.DATA_VIGENCIA_2026_09_21)
        nova.descricao, nova.unidade = descricao, unidade
        session.add(nova)
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="Premissa",
                      entidade_id=nova.id, escopo=f"premissa '{chave}'", antes="não cadastrada",
                      depois=str(num if num is not None else txt), motivo=pol.FONTE_2026_09_21,
                      origem=ORIGEM, correlacao=correlacao)
        premissas.append(nova)
    session.flush()
    return {"correlacao": correlacao, "encerradas": len(encerradas), "criadas": len(criadas),
            "premissas": len(premissas)}


# ===========================================================================
# 2. Fiscal
# ===========================================================================
def aplicar_fiscal(session: Session, ator: Optional[Usuario]) -> dict:
    escritos = fis.aplicar_matriz(session)
    if escritos:
        adm.registrar(session, ator=ator, acao="CADASTRAR_MATRIZ_FISCAL", entidade="EstadoFiscal",
                      escopo="27 UFs · base interna sem FCP · FCP por família do escopo",
                      depois=f"{escritos} linhas/colunas", motivo=fis.FONTE_MATRIZ, origem=ORIGEM,
                      correlacao=_correlacao("fiscal"))
    return {"escritos": escritos}


# ===========================================================================
# 3. Decor Tricot — custo de compra, crédito de PIS/COFINS, sem crédito de ICMS
# ===========================================================================
FONTE_DECOR = "ORÇAMENTO ANARA - 240826 (Decor Tricot)"
DATA_DECOR = date(2026, 8, 24)


def plano_decor(session: Session) -> List[dict]:
    decor = session.exec(select(Fornecedor).where(Fornecedor.codigo == pol.CODIGO_DECOR)).first()
    if decor is None:
        return []
    linhas = []
    for p in session.exec(select(Produto).where(Produto.fornecedor_id == decor.id)).all():
        bruto = p.custo_ref_valor if p.custo_ref_valor is not None else p.custo_unitario
        vigente = cs.referencia_vigente(session, p.id)
        if vigente is not None and vigente.metodo_custo == CostMethod.decor_direct.value:
            continue
        if not bruto or bruto <= 0:
            linhas.append({"produto": p, "bruto": None, "cnet": None, "acao": "manter A_COTAR"})
            continue
        conta = cs.cnet_nacional(bruto, icms_pct=cs.DECOR_ICMS_CREDITO,
                                 pis_cofins_pct=cs.DECOR_PIS_COFINS_CREDITO)
        linhas.append({"produto": p, "bruto": D(bruto), "cnet": conta.cnet,
                       "acao": "registrar DECOR_DIRECT v1 (CONFIRMADO)"})
    return linhas


def aplicar_decor(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("decor")
    registradas = 0
    for linha in plano_decor(session):
        p = linha["produto"]
        if linha["bruto"] is None:
            continue
        antes = p.custo_unitario
        ref = cs.registrar_decor(session, p, float(linha["bruto"]), fonte=FONTE_DECOR,
                                 documento=p.custo_ref_documento or FONTE_DECOR,
                                 data_ref=p.custo_ref_data or DATA_DECOR,
                                 notas=("Decisão 21/09/2026: valor do orçamento é CUSTO DE COMPRA; "
                                        "crédito de PIS/COFINS 9,25%, sem crédito de ICMS."))
        # a dúvida "custo ou preço de venda" está encerrada — outros motivos de revisão ficariam
        if p.precisa_revisao and "CUSTO DE COMPRA" in (p.revisao_motivo or "").upper():
            p.precisa_revisao = False
            p.revisao_motivo = None
        session.add(p)
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="CustoReferencia",
                      entidade_id=ref.id, escopo=f"SKU {p.sku_key}",
                      antes=f"custo_unitario {antes} (bruto, sem crédito)",
                      depois=f"CNET {para_float(linha['cnet'])} = bruto {para_float(linha['bruto'])} − 9,25% PIS/COFINS",
                      motivo="Decisão 21/09/2026 — Decor: custo de compra; crédito PIS/COFINS; sem ICMS",
                      origem=ORIGEM, correlacao=correlacao,
                      detalhe={"versao": ref.versao, "fonte": FONTE_DECOR})
        registradas += 1
    session.flush()
    return {"correlacao": correlacao, "registradas": registradas}


# ===========================================================================
# 4. ELIS — Cobertor Boa Noite Casal
# ===========================================================================
ELIS = dict(
    sku_key="ELIS · Blanket · 180x220 · Boa Noite Casal · xadrez preto/branco/cinza · 600 g/m²",
    nome="Cobertor Boa Noite Casal 180x220 · xadrez preto, branco e cinza",
    especificacao=("180 × 220 cm · xadrez preto com branco e cinza · 80% poliéster, 15% algodão, "
                   "3% poliamida, 2% acrílico · 600 g/m²"),
    categoria="Cobertor", familia="Blanket", largura_cm=180.0, comprimento_cm=220.0, gsm=600,
    cotton_pct=0.15, poliester_pct=0.80, cor="preto com branco e cinza", plain_or_stripe="plain",
    construcao="xadrez", custo_bruto=68.38,
    fonte="Cotação ELIS Guaratinguetá — preço NET, CIF Barueri",
    documento="Cotação ELIS — Cobertor Boa Noite Casal (recebida em 21/09/2026)",
    data_ref=date(2026, 9, 21),
    condicoes=("Pagamento antecipado ao fornecedor (condição de COMPRA, não do cliente). Pedido "
               "mínimo 500 peças. Produção aproximada de 30 a 40 dias úteis, sujeita a alteração. "
               "Preço NET, entregue em Barueri (CIF Barueri)."),
)


def aplicar_elis(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("elis")
    forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == pol.CODIGO_ELIS)).first()
    criado_forn = False
    if forn is None:
        forn = Fornecedor(codigo=pol.CODIGO_ELIS, nome="ELIS (Guaratinguetá)",
                          tipo=TipoFornecedor.nacional, pais="Brasil", moeda_custo="BRL",
                          cost_method_padrao=CostMethod.national_supplier,
                          uf_origem_fiscal="SP", origem_logistica_cidade="Guaratinguetá",
                          origem_logistica_uf="SP",
                          observacoes="Cobertores. Fábrica em Guaratinguetá/SP, faturado em São "
                                      "Paulo. " + ELIS["condicoes"])
        session.add(forn)
        session.flush()
        criado_forn = True
    # regra de margem do fornecedor (14% no B2B), caso a etapa de política tenha rodado antes
    nome_regra = next(r["nome"] for r in pol.margens_2026_09_21()
                      if r.get("fornecedor_codigo") == pol.CODIGO_ELIS)
    if session.exec(select(MargemRegra).where(MargemRegra.nome == nome_regra)).first() is None:
        dados = next(dict(r) for r in pol.margens_2026_09_21() if r["nome"] == nome_regra)
        dados.pop("fornecedor_codigo")
        for chave in ("margem_pct", "piso_pct", "comissao_formacao_pct"):
            if dados.get(chave) is not None:
                dados[chave] = float(dados[chave])
        session.add(MargemRegra(fornecedor_id=forn.id,
                                notas=f"{pol.FONTE_2026_09_21}. Cobertores ELIS: 14% no B2B.", **dados))
    produto = session.exec(select(Produto).where(Produto.sku_key == ELIS["sku_key"])).first()
    criado_prod = False
    if produto is None:
        produto = Produto(
            sku_key=ELIS["sku_key"], nome=ELIS["nome"], nome_original="Cobertor Boa Noite Casal",
            especificacao=ELIS["especificacao"], categoria=ELIS["categoria"], familia=ELIS["familia"],
            fornecedor_id=forn.id, cost_method=CostMethod.national_supplier.value,
            largura_cm=ELIS["largura_cm"], comprimento_cm=ELIS["comprimento_cm"], gsm=ELIS["gsm"],
            cotton_pct=ELIS["cotton_pct"], poliester_pct=ELIS["poliester_pct"], cor=ELIS["cor"],
            plain_or_stripe=ELIS["plain_or_stripe"], construcao=ELIS["construcao"],
            custo_ref_tipo="SUPPLIER_COST", custo_ref_nota=ELIS["condicoes"], ativo=True)
        session.add(produto)
        session.flush()
        criado_prod = True
    if cs.referencia_vigente(session, produto.id) is None:
        ref = cs.registrar_referencia(
            session, produto, cnet_brl=ELIS["custo_bruto"],
            metodo=CostMethod.national_supplier.value, status=StatusCusto.confirmado.value,
            fonte=ELIS["fonte"], documento=ELIS["documento"], data_ref=ELIS["data_ref"],
            valor_bruto=ELIS["custo_bruto"], origem_registro=ORIGEM,
            memoria={"preco_informado": ELIS["custo_bruto"], "descricao_fonte": "Preço NET - CIF Barueri",
                     "credito_fiscal_entrada": "não aplicado — sem evidência específica deste fornecedor",
                     "moq": 500, "prazo_producao": "30 a 40 dias úteis (sujeito a alteração)",
                     "condicao_compra": "pagamento antecipado (do fornecedor; não é condição do cliente)",
                     "origem_fiscal": "SP", "mercadoria": "NACIONAL"},
            notas=ELIS["condicoes"])
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="CustoReferencia",
                      entidade_id=ref.id, escopo=f"SKU {produto.sku_key}", antes="—",
                      depois="CNET R$ 68,38 (custo líquido direto de aquisição)",
                      motivo="Cadastro ELIS 21/09/2026", origem=ORIGEM, correlacao=correlacao)
    session.flush()
    return {"correlacao": correlacao, "fornecedor_criado": criado_forn, "produto_criado": criado_prod,
            "produto_id": produto.id}


# ===========================================================================
# 5. KTC — Samples Quotation 29/07/2026 (benchmark / cotação direta)
# ===========================================================================
DATA_KTC_SAMPLES = date(2026, 7, 29)
DOCUMENTO_KTC_SAMPLES = "Samples Quotation KTC 29/07/2026 (EXW, USD, Egito, cliente ANARA)"
FONTE_KTC_SAMPLES = "KTC Samples Quotation 29/07/2026"

#: (n, família, descrição da fonte, largura, comprimento, tc, gsm, algodão, poliéster,
#:  liso/listrado, tamanho, código KTC, EXW USD, construção/observação)
KTC_SAMPLES_2026_07_29 = [
    (1, "Flat Sheet", "233TC Percale 100% Cotton plain white", 260, 280, 233, None, 1.0, 0.0, "plain", None, "KTC-006", 11.97, None),
    (2, "Flat Sheet", "250TC Sateen 70% Cotton 30% Polyester", 260, 280, 250, None, 0.70, 0.30, "plain", None, "KTC-011", 12.53, None),
    (3, "Flat Sheet", "250TC Sateen stripe 8mm 70/30", 200, 220, 250, None, 0.70, 0.30, "stripe", None, "KTC-012", 8.22, "listra 8 mm"),
    (4, "Flat Sheet", "Elis Germany 250TC Sateen 80/20", 180, 320, 250, None, 0.80, 0.20, "plain", None, "KTC-011", 10.50, "referência Elis Germany"),
    (5, "Flat Sheet", "300TC Sateen 100% Cotton plain", 260, 280, 300, None, 1.0, 0.0, "plain", None, "KTC-001", 14.02, None),
    (6, "Flat Sheet", "300TC Sateen 50/50 plain", 260, 280, 300, None, 0.50, 0.50, "plain", None, "KTC-002", 12.53, None),
    (7, "Flat Sheet", "300TC Sateen stripe 4mm 100% Cotton", 260, 280, 300, None, 1.0, 0.0, "stripe", None, "KTC-023", 14.53, "listra 4 mm"),
    (8, "Flat Sheet", "300TC Sateen stripe 2cm 50/50", 220, 220, 300, None, 0.50, 0.50, "stripe", None, "KTC-022", 8.96, "listra 2 cm"),
    (9, "Flat Sheet", "300TC Sateen stripe 1cm 70/30", 220, 220, 300, None, 0.70, 0.30, "stripe", None, "KTC-024", 8.96, "listra 1 cm"),
    (10, "Flat Sheet", "400TC Sateen 100% Cotton plain", 229, 305, 400, None, 1.0, 0.0, "plain", None, None, 14.47, None),
    (11, "Flat Sheet", "600TC Sateen 100% Cotton plain", 260, 280, 600, None, 1.0, 0.0, "plain", None, "KTC-016", 23.23, None),
    (12, "Blanket", "100% Polyester plain", 220, 230, None, None, 0.0, 1.0, "plain", None, "BL-001", 10.71, None),
    (13, "Blanket", "100% Polyester plain 4.650Gm", 210, 230, None, None, 0.0, 1.0, "plain", None, "BL-003", 20.86, "peso da peça 4,650 kg"),
    (14, "Blanket", "100% Cotton Basket weave 400Gsm", 244, 274, None, 400, 1.0, 0.0, "plain", None, "BL-002", 28.96, "basket weave"),
    (15, "Pool Towel", "Stripe 100% Cotton Terry 700Gsm", 100, 200, None, 700, 1.0, 0.0, "stripe", None, "PT.006", 19.60, None),
    (16, "Pool Towel", "Stripe 100% Cotton Terry 700Gsm", 100, 180, None, 700, 1.0, 0.0, "stripe", None, "PT.007", 17.64, None),
    (17, "Pool Towel", "Stripe 100% Cotton Terry 650Gsm", 90, 165, None, 650, 1.0, 0.0, "stripe", None, "PT.009", 13.51, None),
    (18, "Bath Towel", "100% Cotton Terry 650Gsm plain", 100, 180, None, 650, 1.0, 0.0, "plain", None, "BT-004", 9.95, None),
    (19, "Bath Towel", "100% Cotton Terry 543Gsm with border", 100, 150, None, 543, 1.0, 0.0, "plain", None, "BT-002", 6.95, "com barra"),
    (20, "Bath Towel", "100% Cotton Terry 600Gsm plain", 70, 140, None, 600, 1.0, 0.0, "plain", None, "BT-003", 5.00, None),
    (21, "Face Towel", "100% Cotton Terry 650Gsm with border", 33, 33, None, 650, 1.0, 0.0, "plain", None, "FT.004", 0.64, "com barra"),
    (22, "Face Towel", "91% Cotton 9% Polyester Terry 600Gsm plain", 33, 33, None, 600, 0.91, 0.09, "plain", None, "FT.003", 0.59, None),
    (23, "Bath Mat", "100% Cotton Terry 800Gsm 2 lines", 50, 75, None, 800, 1.0, 0.0, "plain", None, "BM.006", 2.70, "2 linhas"),
    (24, "Bath Mat", "100% Cotton Terry 1150Gsm with border", 50, 70, None, 1150, 1.0, 0.0, "plain", None, "BM.011", 3.62, "com barra"),
    (25, "Hand Towel", "Elis Germany 100% Cotton Terry 500Gsm plain", 50, 100, None, 500, 1.0, 0.0, "plain", None, "HT.001", 2.13, "referência Elis Germany"),
    (26, "Bath Towel", "Elis Germany 100% Cotton Terry 500Gsm plain", 70, 140, None, 500, 1.0, 0.0, "plain", None, "BT.001", 4.17, "referência Elis Germany"),
    (27, "Bath Rug", "100% Cotton 2050Gsm Roman Border", 53, 53, None, 2050, 1.0, 0.0, "plain", None, "BRU.001", 9.21, "Roman Border"),
    (28, "Bath Rug", "100% Cotton 2050Gsm Roman Border", 61, 91, None, 2050, 1.0, 0.0, "plain", None, "BRU.001", 18.21, "Roman Border"),
    (29, "Bathrobe", "100% Cotton waffle velour 420Gsm shawl collar", None, None, None, 420, 1.0, 0.0, "plain", "M", "BR-002", 24.00, "waffle velour · gola xale"),
    (30, "Bathrobe", "100% Cotton waffle pique 240Gsm, shawl collar, collar velour", None, None, None, 240, 1.0, 0.0, "plain", "2XL", "BR-019", 20.00, "waffle piquet · gola xale velour"),
    (31, "Bathrobe", "100% Cotton waffle 240Gsm kimono", None, None, None, 240, 1.0, 0.0, "plain", "L", "BR-008", 16.00, "waffle · kimono"),
    (32, "Bathrobe", "100% Cotton jacquard textured sateen black piping kimono", None, None, None, None, 1.0, 0.0, "plain", "L", "BR-003", 24.00, "jacquard · vivo preto · kimono"),
    (33, "Bathrobe", "100% Cotton velour plain 420Gsm shawl collar", None, None, None, 420, 1.0, 0.0, "plain", "L", "BR-002", 24.00, "velour · gola xale"),
    (34, "Bathrobe", "100% Polyester microfiber + polar fleece inner, kimono", None, None, None, None, 0.0, 1.0, "plain", "L", "BR-001", 24.00, "microfibra + fleece · kimono"),
    (35, "Bathrobe", "85% Polyester / 15% Nylon 160gsm + 100% Cotton jacquard inner lining 110gsm, shawl collar", None, None, None, 160, 0.0, 0.85, "plain", "Unisize", "BR-028", 27.00, "85% poliéster / 15% náilon · forro jacquard 100% algodão 110 g · gola xale"),
]


def _mesmo(a, b, tol=0.0) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def casar_produto_ktc(produtos: List[Produto], linha: tuple) -> Optional[Produto]:
    """Casamento por campos estruturados — nunca por nome. Ambíguo → nenhum."""
    (_n, familia, _desc, w, l, tc, gsm, algodao, _poli, listrado, tamanho, _cod, _exw, _obs) = linha
    candidatos = []
    for p in produtos:
        if (p.familia or "").strip().lower() != familia.lower():
            continue
        if w is not None and not (_mesmo(p.largura_cm, w, 0.5) and _mesmo(p.comprimento_cm, l, 0.5)):
            continue
        if w is None and tamanho and (p.subcategoria or "").strip().upper() != tamanho.upper():
            continue
        if tc is not None and not _mesmo(p.thread_count, tc):
            continue
        if gsm is not None and not _mesmo(p.gsm, gsm, 5):
            continue
        if algodao is not None and p.cotton_pct is not None and not _mesmo(p.cotton_pct, algodao, 0.011):
            continue
        if (p.plain_or_stripe or "plain") != listrado:
            continue
        candidatos.append(p)
    if len(candidatos) > 1:
        # desempate por construção/observação declarada (ex.: dois roupões L 100% algodão:
        # "jacquard · kimono" × "velour · gola xale") e por gramatura ausente/presente
        obs = (linha[13] or "").strip().lower()
        finos = [p for p in candidatos if (p.construcao or "").strip().lower() == obs
                 and _mesmo(p.gsm, gsm, 5)]
        candidatos = finos
    return candidatos[0] if len(candidatos) == 1 else None


def plano_ktc(session: Session) -> List[dict]:
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    produtos = session.exec(select(Produto).where(Produto.fornecedor_id == ktc.id)).all()
    saida = []
    for linha in KTC_SAMPLES_2026_07_29:
        p = casar_produto_ktc(produtos, linha)
        ja = (p is not None and p.exw_cotado_fonte and FONTE_KTC_SAMPLES in p.exw_cotado_fonte
              and _mesmo(p.exw_cotado_usd, linha[12], 0.0001))
        saida.append({"linha": linha, "produto": p,
                      "acao": ("já ingerida" if ja else
                               ("atualizar cotação direta" if p is not None else "criar produto"))})
    return saida


def _sku_key_sample(linha: tuple) -> str:
    (n, familia, desc, w, l, tc, gsm, algodao, poli, listrado, tamanho, cod, _exw, _obs) = linha
    medida = f"{w}x{l}" if w else (tamanho or "sem medida")
    comp = f"{int(round((algodao or 0) * 100))}/{int(round((poli or 0) * 100))}"
    fios = f"{tc} fios" if tc else (f"{gsm} g/m²" if gsm else "—")
    return f"KTC · {familia} · {medida} · {fios} · {comp} · {listrado} · {cod or 'sem código'} · amostra {n:02d}"


def aplicar_ktc(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("ktc")
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    # allowance de costing da fronha (planilha "Pillow Case Costing sheet.xlsx": 2%) — escopo
    # por família; NÃO é Imposto de Importação
    from app.models import ParametroKTC, ToalhaPreco
    if session.exec(select(ParametroKTC).where(ParametroKTC.chave == "quality_allowance")
                    .where(ParametroKTC.escopo == "Pillow Case")).first() is None:
        session.add(ParametroKTC(chave="quality_allowance", escopo="Pillow Case", valor=0.02,
                                 fonte="Pillow Case Costing sheet.xlsx (KTC)",
                                 notas="pillowcase_allowance — etapa de 2% da planilha de fronhas da KTC "
                                       "(rotulada '2% II' lá). Não é Imposto de Importação; não reutiliza "
                                       "NcmRegra.ii_pct."))
    # taxa por kg da face towel, derivada desta mesma cotação (0,59 ÷ 0,06534 kg = 9,03/kg)
    if session.exec(select(ToalhaPreco).where(ToalhaPreco.subcategoria == "Face Towel")).first() is None:
        session.add(ToalhaPreco(subcategoria="Face Towel", plain_or_stripe="plain", price_usd_kg=9.00,
                                preco_final=True,
                                fonte=f"Derivado da {FONTE_KTC_SAMPLES} (33x33 600 g: 0,59 ÷ 0,06534 kg = 9,03/kg)",
                                notas="Mesma taxa das peças pequenas de terry; composição não entra."))
    ncm_por_familia = {}
    for regra in session.exec(select(NcmRegra)).all():
        ncm_por_familia.setdefault((regra.familia or "").lower(), regra.ncm)
    # material do motor industrial, quando a KTC já cotou o tecido (tc × composição × liso/listrado)
    from app.models import MaterialPreco
    materiais = [m for m in session.exec(select(MaterialPreco)).all() if m.ativo]

    def material_para(tc, algodao, listrado):
        if tc is None or algodao is None:
            return None
        cand = [m for m in materiais if m.thread_count == tc and m.plain_or_stripe == listrado
                and m.cotton_pct is not None and abs(float(m.cotton_pct) - float(algodao)) < 0.011]
        return cand[0] if len(cand) == 1 else None

    atualizados, criados = 0, 0
    for item in plano_ktc(session):
        if item["acao"] == "já ingerida":
            continue
        (n, familia, desc, w, l, tc, gsm, algodao, poli, listrado, tamanho, cod, exw, obs) = item["linha"]
        fonte = f"{FONTE_KTC_SAMPLES} · {cod or 'sem código'} · amostra {n:02d}"
        p = item["produto"]
        if p is None:
            from app.nomes import nome_canonico
            p = Produto(sku_key=_sku_key_sample(item["linha"]), nome=desc, nome_original=desc,
                        especificacao=(desc + (f" · {obs}" if obs else "")), familia=familia,
                        fornecedor_id=ktc.id, cost_method=CostMethod.ktc_quoted.value,
                        largura_cm=w, comprimento_cm=l, thread_count=tc, gsm=gsm,
                        cotton_pct=algodao, poliester_pct=poli, plain_or_stripe=listrado,
                        subcategoria=tamanho, construcao=obs, ncm=ncm_por_familia.get(familia.lower()),
                        custo_ref_tipo="EXW_QUOTED", custo_ref_cliente="ANARA", ativo=True)
            material = material_para(tc, algodao, listrado) if familia in ("Flat Sheet", "Top Sheet", "Duvet Cover", "Pillow Case") else None
            if material is not None:
                # tecido já cotado pela KTC: o motor industrial é a fonte; a cotação, benchmark
                p.material_ref = material.material
                p.weave = material.weave
                p.cost_method = CostMethod.ktc_calculated.value
            try:
                p.nome = nome_canonico(p)
            except Exception:              # noqa: BLE001 — nome canônico é conveniência
                p.nome = desc
            session.add(p)
            session.flush()
            criados += 1
            antes = "—"
        else:
            antes = f"cotação direta {p.exw_cotado_usd} ({p.exw_cotado_data})"
            atualizados += 1
        p.exw_cotado_usd = exw
        p.exw_cotado_data = DATA_KTC_SAMPLES
        p.exw_cotado_fonte = fonte
        p.exw_cotado_cliente = "ANARA"
        p.custo_ref_documento = DOCUMENTO_KTC_SAMPLES
        p.custo_ref_data = DATA_KTC_SAMPLES
        # peso não informado pela cotação → continua o que houver (real ou estimado); II/NCM
        # pela família — o que faltar, os gates do custo marcam
        session.add(p)
        adm.registrar(session, ator=ator, acao="REGISTRAR_COTACAO_KTC", entidade="Produto",
                      entidade_id=p.id, escopo=f"SKU {p.sku_key}", antes=antes,
                      depois=f"EXW cotado US$ {exw} em {DATA_KTC_SAMPLES} ({cod or 'sem código'})",
                      motivo=DOCUMENTO_KTC_SAMPLES, origem=ORIGEM, correlacao=correlacao,
                      detalhe={"evidencia_data": str(DATA_KTC_SAMPLES), "benchmark": True})
    session.flush()
    return {"correlacao": correlacao, "atualizados": atualizados, "criados": criados}


# ===========================================================================
# 5b. BL-001 — peso logístico ESTIMADO (premissa interna), preço KTC continua CONFIRMADO
# ===========================================================================
BL001_CODIGO = "BL-001"
BL001_EXW_USD = 10.71
BL001_PESO_KG = 2.40
BL001_PESO_TIPO = "ESTIMADO"
BL001_PESO_DATA = date(2026, 9, 21)
BL001_PESO_DOCUMENTO = ("Premissa interna Anara (21/09/2026) — proporção de preço com BL-003 da "
                        f"{FONTE_KTC_SAMPLES}; NÃO informado pela KTC")
BL001_PESO_FONTE = ("PREÇO CONFIRMADO · PESO LOGÍSTICO ESTIMADO — premissa interna Anara "
                    "(21/09/2026): 2,40 kg estimado por proporção com o BL-003 da mesma cotação "
                    "(Blanket 100% poliéster plain 210x230, 4,650 kg, US$ 20,86; BL-001 US$ 10,71 "
                    "≈ 51% do preço), ≈ 474 g/m² em 220x230. Peso NÃO informado pela KTC — "
                    "confirmar na próxima cotação. O EXW US$ 10,71 continua confirmado.")


def _produto_bl001(session: Session) -> Optional[Produto]:
    """O SKU criado pela ingestão da cotação de 29/07 para o código BL-001 (e só ele)."""
    for p in session.exec(select(Produto)).all():
        if f"· {BL001_CODIGO} ·" in (p.exw_cotado_fonte or "") and (p.familia or "") == "Blanket":
            return p
    return None


def plano_bl001(session: Session) -> List[dict]:
    """O que a etapa mudaria no BL-001: só os campos de PESO (kg, tipo, fonte, data, documento).
    EXW/cotação/NCM/II não são tocados. Vazio quando já está como deve ficar (idempotente)."""
    p = _produto_bl001(session)
    if p is None:
        return []
    alvo = {"peso_kg": BL001_PESO_KG, "peso_tipo": BL001_PESO_TIPO, "peso_fonte": BL001_PESO_FONTE,
            "peso_data": BL001_PESO_DATA, "peso_documento": BL001_PESO_DOCUMENTO}
    campos = {k: (getattr(p, k), v) for k, v in alvo.items()
              if (D(getattr(p, k)) if k == "peso_kg" and getattr(p, k) is not None else getattr(p, k))
              != (D(v) if k == "peso_kg" else v)}
    return [{"tipo": "Produto", "objeto": p, "campos": campos}] if campos else []


def aplicar_bl001(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("bl001")
    n = 0
    for a in plano_bl001(session):
        p = a["objeto"]
        if p.exw_cotado_usd is not None and abs(float(p.exw_cotado_usd) - BL001_EXW_USD) > 1e-9:
            # o preço confirmado da cotação é a âncora desta premissa; se mudou, não se
            # aplica um peso proporcional a um preço que não é mais o de 29/07
            raise ValueError(f"BL-001 com EXW {p.exw_cotado_usd} ≠ {BL001_EXW_USD}: revisar a premissa de peso.")
        antes = f"peso {p.peso_kg} ({p.peso_tipo or 'sem tipo'})"
        for campo, (_v, depois) in a["campos"].items():
            setattr(p, campo, depois)
        session.add(p)
        adm.registrar(session, ator=ator, acao="ESTIMAR_PESO_LOGISTICO", entidade="Produto",
                      entidade_id=p.id, escopo=f"SKU {p.sku_key}", antes=antes,
                      depois=f"peso {BL001_PESO_KG} kg ({BL001_PESO_TIPO}) · EXW US$ {BL001_EXW_USD} mantido CONFIRMADO",
                      motivo=BL001_PESO_DOCUMENTO, origem=ORIGEM, correlacao=correlacao,
                      detalhe={"premissa_interna": True, "informado_pela_ktc": False,
                               "base": "BL-003 4,650 kg / US$ 20,86", "gsm_equivalente": 474})
        n += 1
    session.flush()
    return {"correlacao": correlacao, "atualizados": n}


# ===========================================================================
# 5c. I.I. econômico KTC = 0% · proteção comercial de precificação (22/09/2026)
# ===========================================================================
II_ZERO_DESDE = date(2026, 9, 22)
FONTE_II_ZERO = "Decisão Anara de 22/09/2026 — I.I. econômico KTC/Egito = 0%"


def _pct(v) -> str:
    return "0%" if not v else f"{float(v) * 100:g}%"


def _ii_legado_do_produto(session: Session, produto: Produto):
    """(alíquota, origem) de I.I. que formava o custo deste SKU ATÉ 22/09/2026 — lida das linhas
    de NcmRegra anteriores à virada (encerradas ou não), com a mesma precedência do resolvedor
    (família vence NCM), depois `ii_aplicado` do produto; sem nada, 0 (era assim que ele saía)."""
    legadas = [r for r in session.exec(select(NcmRegra)).all()
               if r.ativo and (r.valid_from is None or r.valid_from < II_ZERO_DESDE)]
    familia = (produto.familia or "").strip().lower()
    categoria = (produto.categoria or "").strip().lower()
    por_familia = sorted([r for r in legadas if r.familia and r.familia.strip().lower() in (familia, categoria)],
                         key=lambda r: r.prioridade)
    if por_familia:
        r = por_familia[0]
        return (D(r.ii_preferencial) if r.ii_preferencial is not None else ZERO,
                f"NcmRegra '{r.familia}' ({r.ncm}) até 22/09/2026: {_pct(r.ii_preferencial)}"
                + ("" if r.ii_preferencial is not None else " — sem alíquota confiável, custo formado com 0"))
    if produto.ncm:
        por_ncm = sorted([r for r in legadas if (r.ncm or "").strip() == produto.ncm.strip()], key=lambda r: r.prioridade)
        if por_ncm:
            r = por_ncm[0]
            return (D(r.ii_preferencial) if r.ii_preferencial is not None else ZERO,
                    f"NcmRegra por NCM {r.ncm} até 22/09/2026: {_pct(r.ii_preferencial)}")
    # banco semeado já na vigência nova (sem linha legada): a regra da família carrega a mesma
    # alíquota legada — é dela que a proteção sai
    tabela = ps._tabela_parametro(session, ps.CHAVE_PROTECAO_COMERCIAL)
    if produto.familia and produto.familia in tabela:
        return D(tabela[produto.familia]), f"regra de proteção comercial da família {produto.familia}: {_pct(tabela[produto.familia])}"
    if produto.ii_aplicado is not None:
        return D(produto.ii_aplicado), f"ii_aplicado gravado no produto até 22/09/2026: {_pct(produto.ii_aplicado)}"
    return ZERO, "família sem regra de NCM até 22/09/2026 — custo formado com I.I. 0 (em revisão)"


def plano_ii_zero(session: Session) -> dict:
    """O que a etapa faz: pina a proteção comercial por SKU KTC (a alíquota legada de cada um),
    cria a regra por família (ParametroKTC) e versiona as linhas de NcmRegra para I.I. 0%."""
    from app import seeds as sd
    from app.models import ParametroKTC
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    produtos = (session.exec(select(Produto).where(Produto.fornecedor_id == ktc.id)
                             .where(Produto.ativo == True)).all() if ktc else [])   # noqa: E712
    pinar = []
    for p in produtos:
        if p.protecao_comercial_pct is None:
            pct, origem = _ii_legado_do_produto(session, p)
            pinar.append({"produto": p, "pct": pct, "origem": origem})
    familias = dict(sd.PROTECAO_COMERCIAL_POR_FAMILIA)
    legadas = [r for r in session.exec(select(NcmRegra)).all()
               if r.ativo and (r.valid_from is None or r.valid_from < II_ZERO_DESDE)]
    for p in produtos:
        if p.familia and p.familia not in familias:
            # família fora do mapa semeado: a alíquota legada da sua linha de NCM (se houver);
            # sem linha até 22/09, era precificada com 0
            por_fam = sorted([r for r in legadas if (r.familia or "").strip().lower() == p.familia.strip().lower()],
                             key=lambda r: r.prioridade)
            familias[p.familia] = float(por_fam[0].ii_preferencial or 0) if por_fam else 0.0
    existentes = {(x.chave, x.escopo) for x in session.exec(select(ParametroKTC)).all()}
    criar_familias = [(f, v) for f, v in familias.items() if ("protecao_comercial_pct", f) not in existentes]
    regras = [r for r in session.exec(select(NcmRegra)).all() if r.ativo]
    sucessoras = {(r.familia, r.ncm) for r in regras if r.valid_from == II_ZERO_DESDE}
    encerrar = [r for r in regras if (r.valid_from is None or r.valid_from < II_ZERO_DESDE) and r.valid_to is None]
    criar_ncm = [r for r in encerrar if (r.familia, r.ncm) not in sucessoras]
    return {"pinar": pinar, "criar_familias": criar_familias, "encerrar_ncm": encerrar, "criar_ncm": criar_ncm}


def aplicar_ii_zero(session: Session, ator: Optional[Usuario]) -> dict:
    from app import seeds as sd
    from app.models import ParametroKTC
    correlacao = _correlacao("ii-zero")
    plano = plano_ii_zero(session)
    for a in plano["pinar"]:
        p = a["produto"]
        p.protecao_comercial_pct = para_float(a["pct"])
        p.protecao_comercial_fonte = (f"Proteção comercial de precificação = {_pct(a['pct'])} — {a['origem']}. "
                                      "Preservada em 22/09/2026 só para manter B2B/tabela/preco_base; não é tributo, "
                                      "custo nem despesa (I.I. econômico KTC = 0%).")
        session.add(p)
        adm.registrar(session, ator=ator, acao="PINAR_PROTECAO_COMERCIAL", entidade="Produto",
                      entidade_id=p.id, escopo=f"SKU {p.sku_key}", antes="—",
                      depois=f"protecao_comercial_pct {para_float(a['pct'])}", motivo=a["origem"],
                      origem=ORIGEM, correlacao=correlacao,
                      detalhe={"ii_economico_pct": 0.0, "natureza": "formação comercial de preço"})
    for familia, pct in plano["criar_familias"]:
        session.add(ParametroKTC(chave="protecao_comercial_pct", escopo=familia, valor=float(pct),
                                 valid_from=II_ZERO_DESDE, fonte=sd.FONTE_PROTECAO_COMERCIAL,
                                 notas=f"Alíquota preferencial anterior: {_pct(pct)}."))
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="ParametroKTC", entidade_id=None,
                      escopo=f"protecao_comercial_pct · {familia}", antes="—", depois=str(float(pct)),
                      motivo=FONTE_II_ZERO, origem=ORIGEM, correlacao=correlacao)
    for r in plano["criar_ncm"]:
        session.add(NcmRegra(familia=r.familia, descricao_ncm=r.descricao_ncm, ncm=r.ncm,
                             ii_original=r.ii_original, reducao_preferencial=r.reducao_preferencial,
                             ii_preferencial=0.0, prioridade=r.prioridade, valid_from=II_ZERO_DESDE,
                             ativo=True, confiavel=True, fonte=FONTE_II_ZERO,
                             notas=sd.NOTA_II_ZERO.format(pct=sd._pct_texto(r.ii_preferencial))
                             + (" · " + r.notas if r.notas else "")))
        adm.registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="NcmRegra", entidade_id=None,
                      escopo=f"{r.familia} · {r.ncm}", antes=f"I.I. {_pct(r.ii_preferencial)}", depois="I.I. econômico 0%",
                      motivo=FONTE_II_ZERO, origem=ORIGEM, correlacao=correlacao)
    for r in plano["encerrar_ncm"]:
        r.valid_to = II_ZERO_DESDE
        r.notas = ((r.notas + " · ") if r.notas else "") + "Vigência encerrada em 22/09/2026: alíquota preservada como proteção comercial."
        session.add(r)
        adm.registrar(session, ator=ator, acao="ENCERRAR_VERSAO", entidade="NcmRegra", entidade_id=r.id,
                      escopo=f"{r.familia} · {r.ncm}", antes="vigente", depois="valid_to 2026-09-22",
                      motivo=FONTE_II_ZERO, origem=ORIGEM, correlacao=correlacao)
    session.flush()
    return {"correlacao": correlacao, "pinados": len(plano["pinar"]), "familias": len(plano["criar_familias"]),
            "ncm_encerradas": len(plano["encerrar_ncm"]), "ncm_criadas": len(plano["criar_ncm"])}


# ===========================================================================
# 6. Fronhas — nomenclatura canônica ABAS
# ===========================================================================
_LEGADO = "ox" + "ford"


def _normalizar_texto(texto: Optional[str]) -> Optional[str]:
    if not texto or _LEGADO not in texto.lower():
        return texto
    # "oxford 5cm + aba 20cm" → "4 abas 5 cm + aba 20 cm"; "oxford" isolado → "4 abas"
    novo = re.sub(r"(?i)" + _LEGADO + r"\s*(\d+)\s*cm", r"4 abas \1 cm", texto)
    novo = re.sub(r"(?i)" + _LEGADO, "4 abas", novo)
    return novo


def plano_fronhas(session: Session) -> List[dict]:
    achados = []
    for p in session.exec(select(Produto)).all():
        campos = {}
        for campo in ("nome", "especificacao", "sku_key", "construcao", "acabamento", "nome_original"):
            atual = getattr(p, campo)
            novo = _normalizar_texto(atual)
            if novo != atual:
                campos[campo] = (atual, novo)
        if campos:
            achados.append({"tipo": "Produto", "objeto": p, "campos": campos})
    for c in session.exec(select(CmtPreco)).all():
        if c.construcao and _LEGADO in c.construcao.lower():
            achados.append({"tipo": "CmtPreco", "objeto": c, "campos": {"construcao": (c.construcao, "com abas")}})
    for r in session.exec(select(CustoReferencia)).all():
        if r.sku_key and _LEGADO in r.sku_key.lower():
            achados.append({"tipo": "CustoReferencia", "objeto": r,
                            "campos": {"sku_key": (r.sku_key, _normalizar_texto(r.sku_key))}})
    return achados


def aplicar_fronhas(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("fronhas")
    n = 0
    for a in plano_fronhas(session):
        obj = a["objeto"]
        for campo, (antes, depois) in a["campos"].items():
            setattr(obj, campo, depois)
        session.add(obj)
        adm.registrar(session, ator=ator, acao="NORMALIZAR_NOMENCLATURA", entidade=a["tipo"],
                      entidade_id=getattr(obj, "id", None),
                      escopo=", ".join(a["campos"]), antes=" | ".join(v[0] for v in a["campos"].values()),
                      depois=" | ".join(v[1] for v in a["campos"].values()),
                      motivo="Nomenclatura canônica de fronhas: ABAS (21/09/2026)", origem=ORIGEM,
                      correlacao=correlacao)
        n += 1
    session.flush()
    return {"correlacao": correlacao, "normalizados": n}


# ===========================================================================
# 7. Catálogo — preco_base = B2B de referência (cenário padrão do catálogo)
# ===========================================================================
def recalcular_preco_base_catalogo(session: Session) -> dict:
    """`Produto.preco_base` passa a ser o **B2B de referência** no cenário padrão do catálogo
    (SP→SP, não contribuinte, 30 dias). Cache informativo, recalculável, nunca alavanca.
    Produto sem custo, sem regra ou com cenário bloqueado fica sem preço-base."""
    from app.pricing_engine import preco_b2b
    cenario = ps.cenario_padrao_catalogo(session)
    recalculados, sem_preco, caches = 0, 0, 0
    for p in session.exec(select(Produto).where(Produto.ativo == True)).all():   # noqa: E712
        # 22/09/2026: o preco_base forma-se sobre a BASE COMERCIAL (referência comercial do
        # SKU), não sobre o custo real — é o que mantém o catálogo exatamente onde estava.
        custo, base, _mem = ps.bases_de_preco(session, p)
        # A coluna `Produto.custo_unitario` é CACHE do último cálculo (a tela de catálogo do
        # admin a mostra). Onde o custo é derivado agora das premissas, o cache passa a ser o
        # CNET REAL (I.I. 0%); custo lido do catálogo (sem EXW) ou nacional não é tocado.
        if custo and _mem.get("net_fonte") in (ps.CUSTO_DERIVADO_AGORA, ps.CUSTO_HISTORICO_SEM_EVIDENCIA) \
                and (p.custo_unitario is None or abs(float(p.custo_unitario) - float(custo)) > 1e-9):
            p.custo_unitario = para_float(custo)
            p.custo_net_usd = _mem.get("net_usd")
            session.add(p)
            caches += 1
        margem = ps.margem_padrao(session, p)
        regras, _ctx = ps.regras_da_cotacao(session, cenario, p)
        if not custo or custo <= 0 or not margem.tem_regra or regras is None:
            if p.preco_base is not None:
                p.preco_base = None
                session.add(p)
            sem_preco += 1
            continue
        b2b = preco_b2b(base, margem.margem_pct, regras).preco_negociado
        p.preco_base = para_float(b2b)
        p.margem_padrao_pct = para_float(margem.margem_pct)
        session.add(p)
        recalculados += 1
    session.flush()
    return {"recalculados": recalculados, "sem_preco_base": sem_preco, "custo_cache_atualizado": caches}


# ===========================================================================
# 8. Sinal / entrada — a condição opaca `SINAL30+30/60/90` sai de cena (fica desativada)
# ===========================================================================
CONDICAO_SINAL_LEGADA = "SINAL30+30/60/90"
NOTA_SINAL_LEGADA = ("Desativada em 21/09/2026: sinal passou a ser composição (percentual à "
                     "vista + condição do saldo) — `Cotacao.percentual_sinal` + "
                     "`condicao_pagamento`. Linha mantida por histórico; nunca teve encargo.")


def plano_sinal(session: Session) -> List[CondicaoPagamento]:
    """Linhas `SINAL30+30/60/90` ainda ativas. Nenhuma cotação as usa (conferido no banco real
    em 21/09/2026); desativar tira a opção bloqueada do dropdown e evita o uso opaco."""
    return [c for c in session.exec(select(CondicaoPagamento)
                                    .where(CondicaoPagamento.codigo == CONDICAO_SINAL_LEGADA)).all()
            if c.ativo]


def aplicar_sinal(session: Session, ator: Optional[Usuario]) -> dict:
    correlacao = _correlacao("sinal")
    n = 0
    for c in plano_sinal(session):
        c.ativo = False
        c.notas = ((c.notas + " ") if c.notas else "") + NOTA_SINAL_LEGADA
        session.add(c)
        adm.registrar(session, ator=ator, acao="DESATIVAR", entidade="CondicaoPagamento",
                      entidade_id=c.id, escopo=c.codigo, antes="ativo", depois="inativo",
                      motivo="Sinal é composição, não condição opaca (21/09/2026)",
                      origem=ORIGEM, correlacao=correlacao)
        n += 1
    session.flush()
    return {"correlacao": correlacao, "desativadas": n}


ETAPAS = ("politica", "fiscal", "decor", "elis", "ktc", "bl001", "ii_zero", "fronhas", "catalogo", "sinal")


def aplicar_tudo(session: Session, ator: Optional[Usuario], etapas=ETAPAS) -> Dict[str, dict]:
    resultado = {}
    if "politica" in etapas:
        resultado["politica"] = aplicar_politica(session, ator)
    if "fiscal" in etapas:
        resultado["fiscal"] = aplicar_fiscal(session, ator)
    if "decor" in etapas:
        resultado["decor"] = aplicar_decor(session, ator)
    if "elis" in etapas:
        resultado["elis"] = aplicar_elis(session, ator)
    if "ktc" in etapas:
        resultado["ktc"] = aplicar_ktc(session, ator)
    if "bl001" in etapas:
        resultado["bl001"] = aplicar_bl001(session, ator)
    if "ii_zero" in etapas:
        resultado["ii_zero"] = aplicar_ii_zero(session, ator)
    if "fronhas" in etapas:
        resultado["fronhas"] = aplicar_fronhas(session, ator)
    if "catalogo" in etapas:
        resultado["catalogo"] = recalcular_preco_base_catalogo(session)
    if "sinal" in etapas:
        resultado["sinal"] = aplicar_sinal(session, ator)
    return resultado

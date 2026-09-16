"""Camada que liga o banco aos motores puros.

Os motores (`ktc_engine`, `nationalization`, `pricing_engine`, `fiscal_rules`, `margin_rules`,
`payment_terms`) não conhecem banco nem tela. Este módulo é quem lê as premissas versionadas,
monta os parâmetros e devolve:

* o `TaxRuleSet` efetivo de uma cotação (ICMS do cenário + PIS/COFINS + encargo + comissão);
* a margem líquida-alvo padrão de um produto;
* o **custo NET** de um produto por qualquer um dos caminhos de fornecedor;
* a **memória do preço**: o waterfall inteiro, da especificação técnica ao preço final.
"""
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Tuple

from sqlmodel import Session, select

from app import config_service as cfg
from app.fiscal_rules import (
    consumidor_final_de, linha_estado, normalizar_uf, resolver_fiscal_item,
)
from app.ktc_engine import (
    CALCULATED, REVIEW_REQUIRED, ParametrosKTC, calcular_bottom_sheet, calcular_duvet_cover,
    calcular_flat_sheet, calcular_fronha,
    calcular_toalha, shrinkage_por_composicao,
)
from app.margin_rules import MargemResolvida, resolver_margem
from app.models import (
    AliquotaInterestadual, CondicaoPagamento, CostConfidence, CostMethod, Cotacao, EstadoFiscal,
    Finalidade, Fornecedor, MargemRegra, NcmRegra, OrigemFiscal, Produto, RegraFcp,
    RegraFiscalVenda, StatusCusto, TipoFornecedor,
)
from app.dinheiro import D, D0, para_float
from app.nationalization import PremissasNacionalizacao, nacionalizar
from app.payment_terms import resolver_encargo
from app.peso import PesoResolvido, resolver_peso
from app.pricing_engine import TaxRuleSet, icms_excluido_da_base, pis_cofins_efetivo

# Famílias com fórmula industrial demonstrada e validada pela KTC. A fronha entrou na Sessão 2
# com a geometria do §18; o bottom sheet SEM elástico usa o motor do lençol plano. Lençol com
# elástico, roupão e chinelo continuam fora: sem geometria confirmada, não se inventa fórmula —
# esses vão por KTC_SPECIAL_QUOTED ou pelo último preço KTC válido.
FAMILIAS_CALCULAVEIS_PLANO = {"flat sheet", "top sheet", "bottom sheet"}
FAMILIAS_FRONHA = {"pillow case", "pillowcase", "fronha"}
FAMILIAS_TECIDO_PLANO = FAMILIAS_CALCULAVEIS_PLANO | {"duvet cover"} | FAMILIAS_FRONHA
FAMILIAS_TOALHA = {"bath towel", "hand towel", "face towel", "pool towel", "beach towel",
                   "bath mat", "wash cloth", "towel"}   # terry: custo por peso


# ---------------------------------------------------------------------------
# Cenário fiscal e regras da cotação
# ---------------------------------------------------------------------------
def origem_fiscal_do_produto(session: Session, produto: Optional[Produto]) -> Tuple[Optional[str], str]:
    """Natureza fiscal do item: IMPORTADA ou NACIONAL, e de onde essa conclusão veio.

    Precedência: override do próprio produto → tipo do fornecedor. Fornecedor "OUTRO" ou
    ausente **não** vira default — devolve None, e o fiscal bloqueia.
    """
    if produto is None:
        return None, "produto não informado"
    if produto.origem_fiscal:
        return produto.origem_fiscal.strip().upper(), f"override do SKU {produto.sku_key}"
    fornecedor = session.get(Fornecedor, produto.fornecedor_id) if produto.fornecedor_id else None
    if fornecedor is None:
        return None, "produto sem fornecedor — natureza fiscal indeterminada"
    if fornecedor.tipo == TipoFornecedor.importado_ktc:
        return OrigemFiscal.importada.value, f"tipo do fornecedor {fornecedor.nome} (importado)"
    if fornecedor.tipo == TipoFornecedor.nacional:
        return OrigemFiscal.nacional.value, f"tipo do fornecedor {fornecedor.nome} (nacional)"
    return None, (f"fornecedor {fornecedor.nome} tem tipo '{fornecedor.tipo}', que não define "
                  "natureza fiscal")


def uf_origem_fiscal(session: Session, cotacao: Cotacao,
                     produto: Optional[Produto] = None) -> Tuple[Optional[str], str]:
    """UF de origem FISCAL da operação, e a fonte da conclusão.

    Precedência: cotação → fornecedor do item → premissa padrão versionada. `estado_origem` da
    cotação **não** entra: é origem logística/comercial, e origem logística não prova origem
    fiscal da NF.
    """
    estados = session.exec(select(EstadoFiscal)).all()
    if cotacao is not None and getattr(cotacao, "uf_origem_fiscal", None):
        uf = normalizar_uf(estados, cotacao.uf_origem_fiscal)
        if uf:
            return uf, "definida na cotação"
    if produto is not None and produto.fornecedor_id:
        fornecedor = session.get(Fornecedor, produto.fornecedor_id)
        if fornecedor is not None and getattr(fornecedor, "uf_origem_fiscal", None):
            uf = normalizar_uf(estados, fornecedor.uf_origem_fiscal)
            if uf:
                return uf, f"cadastro do fornecedor {fornecedor.nome}"
    padrao = cfg.txt(session, "fiscal_uf_origem_padrao")
    if padrao:
        uf = normalizar_uf(estados, padrao)
        if uf:
            return uf, "premissa versionada fiscal_uf_origem_padrao (default, não evidência)"
    return None, "não há origem fiscal definida em lugar nenhum"


def finalidade_da_operacao(session: Session, cotacao: Cotacao) -> Tuple[Optional[str], str]:
    """Finalidade: cotação → cliente → premissa padrão. Nunca inventada no código."""
    if cotacao is not None and getattr(cotacao, "finalidade", None):
        return cotacao.finalidade.strip().upper(), "definida na cotação"
    if cotacao is not None and getattr(cotacao, "cliente_id", None):
        from app.models import Cliente
        cliente = session.get(Cliente, cotacao.cliente_id)
        if cliente is not None and getattr(cliente, "finalidade", None):
            return cliente.finalidade.strip().upper(), f"cadastro do cliente {cliente.nome}"
    padrao = cfg.txt(session, "fiscal_finalidade_padrao")
    if padrao:
        return padrao.strip().upper(), "premissa versionada fiscal_finalidade_padrao"
    return None, "não há finalidade definida em lugar nenhum"


def fiscal_do_item(session: Session, cotacao: Cotacao, produto: Optional[Produto] = None):
    """Resolução fiscal completa de um item, com a memória de cada variável."""
    estados = session.exec(select(EstadoFiscal)).all()
    explicitas = session.exec(select(RegraFiscalVenda)).all()
    aliquotas = session.exec(select(AliquotaInterestadual)).all()
    regras_fcp = session.exec(select(RegraFcp)).all()

    uf_origem, fonte_origem = uf_origem_fiscal(session, cotacao, produto)
    uf_destino = normalizar_uf(estados, getattr(cotacao, "estado_destino", None))
    origem_fiscal, fonte_natureza = origem_fiscal_do_produto(session, produto)
    finalidade, fonte_finalidade = finalidade_da_operacao(session, cotacao)

    resultado = resolver_fiscal_item(
        explicitas, estados, aliquotas,
        uf_origem=uf_origem, uf_destino=uf_destino, origem_fiscal=origem_fiscal,
        contribuinte=getattr(cotacao, "contribuinte_icms", None),
        finalidade=finalidade,
        ncm=getattr(produto, "ncm", None),
        produto_id=getattr(produto, "id", None),
        familia=getattr(produto, "familia", None),
        regras_fcp=regras_fcp)
    resultado.avisos.append(f"origem fiscal: {fonte_origem}")
    resultado.avisos.append(f"natureza da mercadoria: {fonte_natureza}")
    resultado.avisos.append(f"finalidade: {fonte_finalidade}")
    return resultado


def _condicao_vigente(condicoes, codigo):
    """A linha de `CondicaoPagamento` que o resolvedor efetivamente escolheu."""
    from app.payment_terms import _vigente_em
    alvo = (codigo or "").strip().lower()
    if not alvo:
        return None
    candidatas = [c for c in condicoes
                  if (c.codigo or "").strip().lower() == alvo
                  and getattr(c, "ativo", True) and _vigente_em(c, date.today())]
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (getattr(c, "valid_from", None) is not None,
                                   getattr(c, "valid_from", None) or date.min,
                                   getattr(c, "versao", 1), c.id or 0))
    return candidatas[-1]


def _id_da_fonte(fonte):
    """`"AliquotaInterestadual#12 + ..."` → `12`. A fonte fiscal já carregava a identidade
    em texto; aqui ela vira coluna, para consulta e para prova."""
    import re
    m = re.search(r"AliquotaInterestadual#(\d+)", fonte or "")
    return int(m.group(1)) if m else None


#: Sentinela para `regras_da_cotacao(comissao_formacao_pct=...)`: "resolva pela política do
#: produto". Diferente de `None`, que é "comissão por faixa de markup" (item anterior à
#: política de 16/09/2026).
PELA_POLITICA_DO_PRODUTO = object()


def regras_da_cotacao(session: Session, cotacao: Cotacao,
                      produto: Optional[Produto] = None, *,
                      comissao_formacao_pct=PELA_POLITICA_DO_PRODUTO
                      ) -> Tuple[Optional[TaxRuleSet], dict]:
    """TaxRuleSet efetivo **do item** + contexto, para exibir e para snapshot.

    Devolve `(None, contexto)` quando o fiscal ou a condição de pagamento não se resolvem: não
    se forma preço com premissa faltando. O contexto sempre diz o motivo.

    ## Comissão (Fase 3A, 16/09/2026)

    A comissão que entra no gross-up é a de **formação** da política do produto — 10% para
    não-Daune, 5% para Daune —, e não mais a faixa por markup. Chega aqui de três jeitos:

    * `PELA_POLITICA_DO_PRODUTO` (default): resolve a regra de margem vigente do produto e usa
      a comissão de formação dela; produto sem política (ou sem produto) cai na tabela de
      faixas, que continua sendo lida para interpretar cotação antiga;
    * um percentual: o que o **item** congelou (`CotacaoItem.comissao_formacao_pct`) — é o
      que o recálculo de um rascunho usa, para não migrar item antigo de política em silêncio;
    * `None`: item anterior à política — tabela de faixas.
    """
    fiscal = fiscal_do_item(session, cotacao, produto)
    condicoes = session.exec(select(CondicaoPagamento)).all()
    encargo = resolver_encargo(condicoes, getattr(cotacao, "condicao_pagamento", None))
    condicao_usada = _condicao_vigente(condicoes, getattr(cotacao, "condicao_pagamento", None))
    # PIS/COFINS: a premissa cadastrada é a NOMINAL. O que entra no denominador é a EFETIVA,
    # derivada por item pela exclusão do ICMS da base. A premissa legada `pis_cofins_pct`
    # (7,59%) NÃO é lida aqui — ela continua no banco para interpretar cotação antiga.
    pis_nominal = cfg.num(session, "pis_cofins_nominal_pct", 0.0925)
    # Duas grandezas, e confundi-las foi o excesso corrigido em seguida à primeira versão
    # desta regra: `fiscal.icms_pct` é a carga TOTAL que reduz a receita (e continua indo
    # inteira para o gross-up); `icms_excluido` é só a parcela que reduz a BASE de
    # PIS/COFINS, hoje sem o FCP, à espera da validação da contabilidade.
    icms_excluido = (icms_excluido_da_base(fiscal.icms_pct, fiscal.fcp_pct)
                     if fiscal.icms_pct is not None else None)
    pis_cofins = (pis_cofins_efetivo(pis_nominal, icms_excluido)
                  if icms_excluido is not None else None)
    comissao = cfg.tabela_comissao(session)
    politica = None
    if comissao_formacao_pct is PELA_POLITICA_DO_PRODUTO:
        if produto is not None:
            politica = margem_padrao(session, produto)
            comissao_formacao_pct = (para_float(politica.comissao_formacao_pct)
                                     if politica.tem_politica else None)
        else:
            comissao_formacao_pct = None
    if comissao_formacao_pct is not None:
        # Faixa única: `comissao_para_markup` devolve este percentual para qualquer markup, e o
        # gross-up segue idêntico — a comissão fixa entra sem uma segunda fórmula.
        comissao = [(0.0, para_float(D(comissao_formacao_pct)))]

    contexto = {
        "fiscal": fiscal,
        "icms_pct": fiscal.icms_pct, "icms_regra": fiscal.regra, "icms_fonte": fiscal.fonte,
        "aliquota_interestadual": fiscal.aliquota_interestadual,
        "aliquota_interna_destino": fiscal.aliquota_interna_destino,
        "fcp_pct": fiscal.fcp_pct,
        "status_fiscal": fiscal.status, "motivo_fiscal": fiscal.motivo,
        "origem_fiscal": fiscal.origem_fiscal, "uf_origem_fiscal": fiscal.uf_origem,
        "uf_destino_fiscal": fiscal.uf_destino, "finalidade": fiscal.finalidade,
        "consumidor_final": fiscal.consumidor_final,
        "difal_pct": fiscal.difal_pct, "difal_responsavel": fiscal.difal_responsavel,
        "difal_entra_na_margem": fiscal.difal_entra_na_margem,
        # A conta aberta na memória: nominal, carga total de ICMS, FCP mantido na base, a
        # parcela efetivamente excluída e o resultado. `pis_cofins_pct` continua sendo o que
        # incide sobre a receita — muda só a forma de chegar nele.
        "pis_cofins_nominal_pct": pis_nominal,
        "pis_cofins_icms_total_pct": fiscal.icms_pct,
        "pis_cofins_fcp_na_base_pct": fiscal.fcp_pct,
        "pis_cofins_icms_excluido_pct": icms_excluido,
        "pis_cofins_pct": pis_cofins,
        "pis_cofins_nota": ("FCP mantido na base de PIS/COFINS até validação específica da "
                            "contabilidade."),
        "encargo_pct": encargo.pct, "encargo_label": encargo.label,
        "encargo_confirmado": encargo.confirmado, "encargo_aviso": encargo.aviso,
        "status_pagamento": encargo.status, "motivo_pagamento": encargo.motivo,
        "comissao_tabela": comissao,
        "comissao_formacao_pct": (para_float(D(comissao_formacao_pct))
                                  if comissao_formacao_pct is not None else None),
        "politica_comercial": politica.politica if politica is not None else None,
        # --- identidades para pinar no item ---
        "condicao_pagamento_id": condicao_usada.id if condicao_usada else None,
        "aliquota_interestadual_id": _id_da_fonte(fiscal.fonte),
        "premissas_pinadas": pinar_premissas(session),
        "bloqueado": fiscal.bloqueado or encargo.bloqueado,
    }
    if contexto["bloqueado"]:
        contexto["motivo_bloqueio"] = fiscal.motivo or encargo.motivo
        return None, contexto

    regras = TaxRuleSet(icms_pct=fiscal.icms_pct, pis_cofins_pct=pis_cofins,
                        encargo_financeiro_pct=encargo.pct, comissao_tabela=comissao,
                        origem_uf=fiscal.uf_origem or "")
    return regras, contexto


def cenario_padrao_catalogo(session: Session) -> Cotacao:
    """Cotação 'virtual' com o cenário padrão usado para formar o preço-base do catálogo.

    A origem fiscal do cenário vem da premissa versionada, não do campo logístico: o preço-base
    do catálogo é formado com a mesma regra fiscal que uma venda real usaria.
    """
    origem = cfg.txt(session, "catalogo_origem", "São Paulo")
    return Cotacao(
        cliente_id=0,
        estado_origem=origem,
        uf_origem_fiscal=origem,
        estado_destino=cfg.txt(session, "catalogo_destino", "São Paulo"),
        contribuinte_icms=bool(cfg.num(session, "catalogo_contribuinte", 0.0)),
        finalidade=cfg.txt(session, "fiscal_finalidade_padrao", Finalidade.uso_consumo.value),
        condicao_pagamento=cfg.txt(session, "catalogo_condicao_pagamento", "30"),
    )


# ---------------------------------------------------------------------------
# Margem padrão
# ---------------------------------------------------------------------------
def margem_padrao(session: Session, produto: Produto,
                  override_pct: Optional[float] = None, ref: Optional[date] = None
                  ) -> MargemResolvida:
    """A regra de margem — e a política comercial — vigente para o produto **na data**.

    `ref` default é hoje. Até 16/09/2026 a chamada não passava data e o resolvedor tratava a
    ausência como "sem filtro de vigência": regra encerrada continuava formando preço
    (C-NEW-13). Uma política versionada por data só funciona se a data for consultada.
    """
    regras = session.exec(select(MargemRegra)).all()
    return resolver_margem(regras, fornecedor_id=produto.fornecedor_id,
                           familia=produto.familia, thread_count=produto.thread_count,
                           sku_key=produto.sku_key, override_pct=override_pct,
                           ref=ref or date.today())


@dataclass
class PoliticaComercialVigente:
    """As premissas globais da política de comissão, lidas do banco — nunca inventadas."""
    comissao_base_pct: Decimal
    comissao_min_pct: Decimal
    base_id: Optional[int]
    min_id: Optional[int]


def politica_comercial_vigente(session: Session, ref: Optional[date] = None
                               ) -> Optional[PoliticaComercialVigente]:
    """`None` enquanto a política não foi semeada/aplicada: o sistema não assume 10%/5%."""
    from app.politica_comercial import CHAVE_COMISSAO_BASE, CHAVE_COMISSAO_MINIMA
    base = cfg.premissa(session, CHAVE_COMISSAO_BASE, ref)
    minimo = cfg.premissa(session, CHAVE_COMISSAO_MINIMA, ref)
    if base is None or minimo is None or base.valor_num is None or minimo.valor_num is None:
        return None
    return PoliticaComercialVigente(D(base.valor_num), D(minimo.valor_num), base.id, minimo.id)


# ---------------------------------------------------------------------------
# Imposto de importação por família/NCM
# ---------------------------------------------------------------------------
def regra_ncm(session: Session, produto: Produto) -> Optional[NcmRegra]:
    regras = [r for r in session.exec(select(NcmRegra)).all() if r.ativo]
    familia = (produto.familia or "").strip().lower()
    categoria = (produto.categoria or "").strip().lower()
    por_familia = [r for r in regras
                   if r.familia and r.familia.strip().lower() in (familia, categoria)]
    if por_familia:
        return sorted(por_familia, key=lambda r: r.prioridade)[0]
    if produto.ncm:
        por_ncm = [r for r in regras if (r.ncm or "").strip() == produto.ncm.strip()]
        if por_ncm:
            return sorted(por_ncm, key=lambda r: r.prioridade)[0]
    return None


# ---------------------------------------------------------------------------
# Motor industrial: parâmetros a partir do cadastro do produto
# ---------------------------------------------------------------------------
def _familia_normalizada(produto: Produto) -> str:
    return (produto.familia or "").strip().lower()


def _num_do_texto(texto, padrao: float) -> float:
    """Primeiro número de um campo de cadastro. Ausente → o padrão do §18, não zero."""
    import re
    m = re.search(r"(\d+(?:[.,]\d+)?)", texto or "")
    return float(m.group(1).replace(",", ".")) if m else padrao


def _abas_do_produto(produto: Produto) -> int:
    """Número de abas da fronha, a partir do cadastro estruturado.

    Construções aprovadas no §18: 0, 2, 3 e 4 abas. Oxford é a de 4. Qualquer outra coisa
    devolve 0 (standard) — e se o cadastro disser um número fora da lista, o motor bloqueia
    em vez de arredondar para o vizinho.
    """
    import re
    texto = f"{produto.construcao or ''} {produto.acabamento or ''}".lower()
    if "oxford" in texto:
        return 4
    m = re.search(r"(\d)\s*abas?", texto)
    return int(m.group(1)) if m else 0


def parametros_ktc_do_produto(session: Session, produto: Produto) -> Tuple[ParametrosKTC, list]:
    """Monta os parâmetros do motor industrial. O que não estiver cadastrado volta como falta."""
    familia = produto.familia or ""
    faltando = []

    escopo_shrink = shrinkage_por_composicao(produto.cotton_pct)
    p = ParametrosKTC(
        shrinkage=cfg.parametro_ktc(session, "shrinkage", escopo_shrink),
        waste=cfg.parametro_ktc(session, "waste"),
        quality_allowance=cfg.parametro_ktc(session, "quality_allowance"),
        ktc_margin=cfg.parametro_ktc(session, "ktc_margin"),
        hem_width_total_cm=cfg.parametro_ktc(session, "hem_width_total_cm", familia),
        hem_length_total_cm=cfg.parametro_ktc(session, "hem_length_total_cm", familia),
        paineis=int(cfg.parametro_ktc(session, "paineis", familia, padrao=1) or 1),
        other_costs_usd=0.0,
    )

    if produto.material_ref:
        material = cfg.material_preco(session, produto.material_ref,
                                      produto.plain_or_stripe or "plain")
        if material:
            p.material_price_usd_m2 = material.price_usd_m2
            p.material_ref = f"{material.material} ({material.plain_or_stripe})"
        else:
            faltando.append(f"preço de material para '{produto.material_ref}'")
    elif _familia_normalizada(produto) in FAMILIAS_TECIDO_PLANO:
        faltando.append("material_ref não cadastrado no produto")

    cmt = cfg.cmt_preco(session, familia, produto.construcao)
    if cmt:
        p.cmt_usd = cmt.cmt_usd
        p.cmt_ref = f"{cmt.familia}{' · ' + cmt.construcao if cmt.construcao else ''}"
    elif _familia_normalizada(produto) in FAMILIAS_TECIDO_PLANO:
        faltando.append(f"CMT para a família '{familia}'")

    if _familia_normalizada(produto) in FAMILIAS_TOALHA:
        toalha = cfg.toalha_preco(session, subcategoria=produto.familia,
                                  composicao=None, gsm=produto.gsm,
                                  yarn_type=produto.yarn_type,
                                  plain_or_stripe=produto.plain_or_stripe or "plain")
        if toalha:
            p.price_usd_kg = toalha.price_usd_kg
            if toalha.preco_final:
                # a taxa por kg já é o EXW: aplicar CMT, perda de 2ª qualidade e margem KTC de
                # novo inflaria o custo em ~19% sem nenhuma razão
                p.cmt_usd = None
                p.quality_allowance = None
                p.ktc_margin = None
                p.cmt_ref = None
        else:
            faltando.append("preço por kg da construção dessa toalha")

    if produto.acabamento:
        p.acabamentos_sem_custo = [produto.acabamento]

    return p, faltando


def calcular_exw(session: Session, produto: Produto):
    """EXW calculado pelo motor industrial, quando a família é calculável."""
    familia = _familia_normalizada(produto)
    p, faltando = parametros_ktc_do_produto(session, produto)

    if familia in FAMILIAS_TOALHA:
        resultado = calcular_toalha(produto.largura_cm, produto.comprimento_cm, produto.gsm, p)
    elif familia == "duvet cover":
        resultado = calcular_duvet_cover(produto.largura_cm, produto.comprimento_cm, p)
    elif familia in FAMILIAS_FRONHA:
        # A construção decide o corte e o CMT. Ela vem do cadastro estruturado — número de
        # abas e flap —, nunca do nome. Sem construção declarada, vale o standard do §18.
        resultado = calcular_fronha(
            produto.largura_cm, produto.comprimento_cm, p,
            flap_cm=_num_do_texto(produto.fechamento, padrao=20.0),
            abas=_abas_do_produto(produto),
            festone="feston" in (produto.acabamento or "").lower(),
            bordado_especial=any(x in (produto.acabamento or "").lower()
                                 for x in ("bordado", "logotipo", "logo")))
    elif familia in FAMILIAS_CALCULAVEIS_PLANO:
        com_elastico = any(x in f"{produto.construcao or ''} {produto.nome}".lower()
                           for x in ("elástico", "elastico", "fitted"))
        resultado = calcular_bottom_sheet(produto.largura_cm, produto.comprimento_cm, p,
                                          com_elastico=com_elastico) \
            if familia == "bottom sheet" \
            else calcular_flat_sheet(produto.largura_cm, produto.comprimento_cm, p)
    else:
        from app.ktc_engine import ResultadoKTC
        return ResultadoKTC(None, REVIEW_REQUIRED, faltando=["familia não calculável"],
                            avisos=[f"Família '{produto.familia or '—'}' não tem fórmula industrial "
                                    "confirmada pela KTC. Continua cotável pelo preço cotado."])
    for f in faltando:
        if f not in resultado.faltando:
            resultado.faltando.append(f)
    return resultado


# ---------------------------------------------------------------------------
# Custo NET por caminho de fornecedor
# ---------------------------------------------------------------------------
#: As premissas versionadas que participam da formação do preço e precisam ficar **pinadas**
#: no item — não só pelo valor, mas pela identidade da versão que produziu aquele valor.
#:
#: `pis_cofins_nominal_pct` substituiu `pis_cofins_pct` aqui em 09/09/2026: o que forma o preço
#: novo é a nominal, e o efetivo é DERIVADO dela com o `icms_pct` do próprio item — que já está
#: congelado no snapshot fiscal da linha. Pinar a legada continuaria prendendo a genealogia a
#: uma premissa que não alimenta mais cálculo nenhum. Itens antigos mantêm o pino que têm.
#: `comissao_base_pct` e `comissao_min_pct` entraram em 16/09/2026 (Fase 3A): são as premissas
#: da comissão variável da cotação. Item anterior à política não as tem pinadas — e é por
#: isso mesmo que `admin_service.premissas_desatualizadas` o reconhece como anterior.
CHAVES_PINADAS = ("fx_usd_brl", "frete_int_usd_kg", "outras_desp_usd_un",
                  "pis_cofins_nominal_pct", "comissao_base_pct", "comissao_min_pct")


def pinar_premissas(session: Session, ref_data=None) -> dict:
    """`{chave: {"premissa_id", "valor", "valid_from"}}` das premissas vigentes agora.

    Guardar o valor já protegia o dinheiro; guardar o **id** protege a genealogia. Sem ele,
    responder "qual versão formou este preço" dependeria de perguntar ao resolvedor o que
    estaria valendo naquela data — e uma versão cadastrada depois, com vigência retroativa,
    mudaria a resposta sem que preço nenhum tivesse mudado.
    """
    pinos = {}
    for chave in CHAVES_PINADAS:
        linha = cfg.premissa(session, chave, ref=ref_data)
        if linha is not None:
            pinos[chave] = {"premissa_id": linha.id, "valor": linha.valor_num,
                            "valid_from": (linha.valid_from.isoformat()
                                           if linha.valid_from else None)}
    return pinos


def premissas_nacionalizacao(session: Session) -> PremissasNacionalizacao:
    return PremissasNacionalizacao(
        frete_usd_kg=cfg.num(session, "frete_int_usd_kg", 0.516),
        outras_desp_usd_un=cfg.num(session, "outras_desp_usd_un", 0.2487532709),
        fx_usd_brl=cfg.num(session, "fx_usd_brl", 5.11),
        fonte="Premissas versionadas (painel de configurações)")


#: De onde saiu o CNET que `custo_net` devolve. Existe porque "o número" não basta: um CNET
#: derivado agora das premissas vigentes e um CNET lido do catálogo são a mesma quantia com
#: significados diferentes, e só o primeiro reage a uma troca de câmbio.
CUSTO_DERIVADO_AGORA = "DERIVADO_DAS_PREMISSAS_VIGENTES"
CUSTO_DO_FORNECEDOR_NACIONAL = "CUSTO_CADASTRADO_DO_FORNECEDOR"
CUSTO_DO_CATALOGO = "CATALOGO_SEM_EXW"


def status_canonico_do_custo(cnet, memoria: dict) -> str:
    """O `StatusCusto` que corresponde a **como** este custo foi resolvido.

    ## Por que traduzir em vez de copiar

    `CotacaoItem.status_custo_item` é lido pelos portões do workflow — `CUSTO_BLOQUEIA`, o
    compromisso firme, o WON e a saúde operacional —, e todos esperam o vocabulário
    canônico: `CONFIRMADO`, `ESTIMADO`, `REVALIDAR`, `A_COTAR`, `REVIEW_REQUIRED`.

    Quando o item não tem `CustoReferencia` versionada, o valor gravado ali vinha de
    `Produto.custo_confianca`, que fala **outra língua**: `CALCULATED`, `QUOTED`, `MANUAL`,
    `LEGACY`. Esses termos descrevem o *método* pelo qual o custo foi obtido, não se ele
    sustenta um compromisso — e nenhum portão os reconhece. O efeito era um bypass
    silencioso: 210 SKUs ativos com `QUOTED` ou `CALCULATED` produziam um status que não
    bloqueava nem avisava. Que os 121 com `REVIEW_REQUIRED` bloqueassem era coincidência de
    string: o mesmo texto existe nos dois vocabulários.

    O método continua registrado, onde sempre esteve — `cost_method`, `custo_confianca` e a
    memória do preço. O que ele deixa de fazer é competir com o status operacional.

    ## A regra

    * custo **derivado das premissas vigentes** ou **cadastrado pelo fornecedor nacional**,
      com valor positivo → `CONFIRMADO`: existe evidência viva e rastreável;
    * custo **lido do catálogo** porque não há EXW para nacionalizar → `REVIEW_REQUIRED`.
      Há um número, mas nada que diga de onde ele veio nem se ainda vale. Não é `A_COTAR`
      (que é a ausência de número), e não é `CONFIRMADO` (que exige evidência);
    * **sem valor nenhum** → `A_COTAR`, qualquer que seja a origem.

    Note que "não há EXW" nunca vira `CONFIRMADO`. Preço direto e rastreável da KTC entra
    por `exw_cotado_usd` e é nacionalizado normalmente — esse caminho é derivado, não
    catálogo. O que cai aqui é dado legado sem evidência, e ele não passa pelos portões.
    """
    if not cnet or D0(cnet) <= 0:
        return StatusCusto.a_cotar.value
    fonte = (memoria or {}).get("net_fonte")
    if fonte == CUSTO_DO_CATALOGO:
        return StatusCusto.review_required.value
    if fonte in (CUSTO_DERIVADO_AGORA, CUSTO_DO_FORNECEDOR_NACIONAL):
        return StatusCusto.confirmado.value
    # Origem não declarada: não se inventa confiança para ela.
    return StatusCusto.review_required.value


def custo_para_precificar(session: Session, produto: Produto):
    """O CNET que deve formar o preço de um item **novo**, com a memória de como se chegou nele.

    ## Por que esta função existe

    `Produto.custo_unitario` é uma coluna persistida, gravada quando o custo foi calculado
    pela última vez. Ela **não** é recalculada quando uma premissa versionada muda — e o
    câmbio é premissa versionada. Em 08/09/2026 o câmbio passou de R$ 5,11 para R$ 5,19 e
    170 dos 241 SKUs KTC com custo ficaram com a coluna defasada.

    O efeito era um item que se contradizia: `custo_unitario` gravado com o custo de 5,11,
    `memoria_json` recalculado com 5,19 e `premissas_pinadas` apontando para a versão 5,19.
    O documento afirmava ter sido formado com um câmbio que não formou o preço dele.

    ## O que muda, e o que não muda

    `custo_net()` já resolvia o custo pelo caminho certo de cada fornecedor. Para fornecedor
    nacional e para SKU KTC sem EXW conhecido ele devolve **exatamente**
    `produto.custo_unitario` — então esses dois casos continuam idênticos. Só muda o SKU KTC
    com EXW em dólar, que é justamente aquele cujo custo depende do câmbio e que, por isso,
    deveria ter mudado desde o começo.

    ## O que NÃO fazer com o retorno

    Quando `net_brl` vem `None`, o custo vivo não pôde ser resolvido. O chamador **não** deve
    cair para `produto.custo_unitario` para conseguir cotar assim mesmo: o status canônico
    (`A_COTAR`, `REVIEW_REQUIRED`) é a resposta certa, e mascarar a falha com um valor antigo
    é o erro que esta função existe para não repetir.
    """
    memoria = custo_net(session, produto)
    return memoria.get("net_brl"), memoria


def custo_net(session: Session, produto: Produto) -> dict:
    """Devolve o CUSTO NET em R$ do produto e como se chegou nele.

    Cada fornecedor tem seu caminho; o motor comercial daqui pra frente é o mesmo para todos.

    **Representação externa.** O dicionário devolvido aqui é memória: vai para JSON, para o
    snapshot do item e para o baseline. Por isso sai em `float`, não em `Decimal` — se saísse
    em Decimal, `json.dumps(default=str)` transformaria dinheiro em string e mudaria o
    formato dos snapshots já emitidos. Os motores calculam em Decimal; a conversão acontece
    uma vez, na saída desta função.
    """
    fornecedor = session.get(Fornecedor, produto.fornecedor_id) if produto.fornecedor_id else None
    metodo = produto.cost_method or (fornecedor.cost_method_padrao.value if fornecedor else None)
    memoria = {"fornecedor": fornecedor.nome if fornecedor else None,
               "cost_method": metodo, "avisos": [], "etapas": []}

    # A versão exata de `CustoReferencia` que está valendo agora. É ela que o item vai
    # pinar — a identidade, não só o número.
    from app import custo_service as _cs
    vigente = _cs.referencia_vigente(session, produto.id) if produto.id else None
    memoria["custo_referencia_id"] = vigente.id if vigente else None
    memoria["custo_referencia_versao"] = vigente.versao if vigente else None

    # --- fornecedor nacional: o custo já está em reais ---
    if fornecedor and fornecedor.tipo == TipoFornecedor.nacional:
        memoria["caminho"] = "Fornecedor nacional — sem motor industrial KTC e sem nacionalização"
        memoria["custo_ref"] = {
            "valor": produto.custo_ref_valor, "moeda": produto.custo_ref_moeda,
            "data": produto.custo_ref_data.isoformat() if produto.custo_ref_data else None,
            "documento": produto.custo_ref_documento, "tipo": produto.custo_ref_tipo,
        }
        if produto.custo_unitario is None:
            memoria["avisos"].append(
                "Produto de fornecedor nacional sem custo NET cadastrado. Continua cotável "
                "pelo preço, mas a margem não pode ser calculada até o custo entrar.")
        memoria["net_brl"] = produto.custo_unitario
        memoria["net_fonte"] = CUSTO_DO_FORNECEDOR_NACIONAL
        return memoria

    # --- KTC ---
    ncm = regra_ncm(session, produto)
    ii = None
    if ncm:
        ii = ncm.ii_preferencial
        memoria["ncm"] = {"ncm": ncm.ncm, "familia": ncm.familia, "ii": para_float(D(ii)),
                          "confiavel": ncm.confiavel, "notas": ncm.notas}
        if not ncm.confiavel:
            memoria["avisos"].append(f"NCM/II da família '{ncm.familia}' marcado para validação: "
                                     f"{ncm.notas or 'sem detalhe'}")
    if ii is None:
        ii = produto.ii_aplicado
        if ii is not None:
            memoria["avisos"].append("Usando o I.I. que já estava gravado no produto — "
                                     "sem linha confiável na tabela de NCM.")

    exw = None
    origem_exw = None
    if metodo == CostMethod.ktc_calculated.value:
        resultado = calcular_exw(session, produto)
        memoria["industrial"] = resultado.como_dict()
        if resultado.exw_usd is not None:
            exw, origem_exw = resultado.exw_usd, "EXW calculado pelo motor industrial"
        else:
            memoria["avisos"].extend(resultado.avisos)

    if exw is None and produto.exw_cotado_usd:
        exw = produto.exw_cotado_usd
        origem_exw = (f"EXW cotado pela KTC em "
                      f"{produto.exw_cotado_data.strftime('%d/%m/%Y') if produto.exw_cotado_data else '—'}"
                      f" ({produto.exw_cotado_fonte or 'fonte não registrada'})")
    if exw is None:
        exw = produto.preco_ktc_usd
        if exw is not None:
            origem_exw = f"Preço KTC histórico do catálogo ({produto.cotacao_origem or 'origem não registrada'})"

    memoria["exw_usd"] = para_float(exw)
    memoria["exw_origem"] = origem_exw
    memoria["exw_calculado_usd"] = produto.exw_calculado_usd
    memoria["exw_cotado_usd"] = produto.exw_cotado_usd

    if exw is None:
        memoria["avisos"].append("Sem EXW conhecido — custo NET não pode ser recalculado; "
                                 "mantido o custo que já estava no catálogo.")
        memoria["net_brl"] = produto.custo_unitario
        memoria["net_fonte"] = CUSTO_DO_CATALOGO
        return memoria

    # produto sem peso gravado (o caso da calculadora, e de SKU novo) tem o peso estimado aqui.
    # Peso real da KTC nunca é tocado — `peso_do_produto` só estima quando não existe.
    peso_kg = produto.peso_kg
    if peso_kg is None:
        estimado = peso_do_produto(session, produto)
        peso_kg = estimado.peso_kg
        if peso_kg:
            memoria["peso"] = {"peso_kg": para_float(peso_kg), "tipo": estimado.tipo,
                               "fonte": estimado.fonte}

    nac = nacionalizar(exw, peso_kg, ii, premissas_nacionalizacao(session))
    memoria["nacionalizacao"] = nac.como_dict()
    memoria["avisos"].extend(nac.avisos)
    memoria["net_brl"] = para_float(nac.net_brl)
    memoria["net_usd"] = para_float(nac.net_usd)
    memoria["net_fonte"] = CUSTO_DERIVADO_AGORA
    memoria["caminho"] = ("Especificação → motor industrial KTC → EXW → nacionalização → NET"
                          if metodo == CostMethod.ktc_calculated.value
                          else "Último preço KTC válido → nacionalização → NET")
    return memoria


# ---------------------------------------------------------------------------
# Peso do produto
# ---------------------------------------------------------------------------
def _tabela_parametro(session: Session, chave: str) -> dict:
    from app.models import ParametroKTC
    linhas = [p for p in session.exec(select(ParametroKTC).where(ParametroKTC.chave == chave)).all()
              if p.ativo and p.escopo]
    return {p.escopo: p.valor for p in linhas}


def peso_do_produto(session: Session, produto: Produto) -> PesoResolvido:
    """Peso real da KTC quando existe; senão estima pela régua da planilha."""
    peso_real = produto.peso_kg if (produto.peso_tipo == "REAL KTC") else None
    if peso_real is None and produto.peso_kg and produto.peso_tipo is None:
        peso_real = produto.peso_kg
    return resolver_peso(
        peso_real, produto.largura_cm, produto.comprimento_cm, produto.gsm,
        produto.thread_count, produto.familia,
        _tabela_parametro(session, "gsm_por_tc"),
        _tabela_parametro(session, "gsm_por_familia"),
        _tabela_parametro(session, "peso_tecnico_familia"),
        _tabela_parametro(session, "peso_kg_m2_familia"))


# ---------------------------------------------------------------------------
# Frescor do preço
# ---------------------------------------------------------------------------
def frescor(session: Session, data_ref: Optional[date]) -> dict:
    if not data_ref:
        return {"status": "UNKNOWN", "dias": None,
                "texto": "Sem data de referência do preço"}
    if isinstance(data_ref, datetime):
        data_ref = data_ref.date()
    dias = (date.today() - data_ref).days
    limite_fresh = int(cfg.num(session, "freshness_fresh_dias", 30))
    limite_aging = int(cfg.num(session, "freshness_aging_dias", 60))
    if dias <= limite_fresh:
        status = "FRESH"
    elif dias <= limite_aging:
        status = "AGING"
    else:
        status = "STALE"
    return {"status": status, "dias": dias,
            "texto": f"{dias} dia(s) desde {data_ref.strftime('%d/%m/%Y')}"}


# ---------------------------------------------------------------------------
# Memória do preço
# ---------------------------------------------------------------------------
def memoria_do_preco(session: Session, produto: Produto, cotacao: Optional[Cotacao] = None,
                     preco_negociado: Optional[float] = None, quantidade: float = 1,
                     margem_override: Optional[float] = None,
                     comissao_formacao_pct=PELA_POLITICA_DO_PRODUTO) -> dict:
    """Waterfall completo: da especificação (ou do custo do fornecedor) ao preço final.

    `comissao_formacao_pct` segue a convenção de `regras_da_cotacao`: o recálculo de um item
    existente passa a comissão que o item congelou, para a memória descrever o preço dele —
    não o que um item novo teria.
    """
    from app.pricing_engine import calcular_por_margem, calcular_por_preco

    cot = cotacao or cenario_padrao_catalogo(session)
    regras, contexto = regras_da_cotacao(session, cot, produto,
                                         comissao_formacao_pct=comissao_formacao_pct)
    custo = custo_net(session, produto)
    margem = margem_padrao(session, produto, margem_override)

    net = custo.get("net_brl") or produto.custo_unitario
    resultado = None
    # `regras` é None quando o fiscal ou a condição de pagamento não se resolveram: nesse caso
    # não se forma preço nenhum. A memória continua sendo devolvida, com o motivo do bloqueio.
    if net and regras is not None:
        if preco_negociado:
            resultado = calcular_por_preco(net, quantidade, preco_negociado, regras,
                                           produto.preco_base)
        else:
            resultado = calcular_por_margem(net, quantidade, margem.margem_pct, regras,
                                            produto.preco_base)

    fornecedor = session.get(Fornecedor, produto.fornecedor_id) if produto.fornecedor_id else None
    return {
        "produto": {"id": produto.id, "nome": produto.nome, "sku_key": produto.sku_key,
                    "especificacao": produto.especificacao, "categoria": produto.categoria,
                    "familia": produto.familia, "thread_count": produto.thread_count,
                    "dimensoes": (f"{produto.largura_cm:g}x{produto.comprimento_cm:g} cm"
                                  if produto.largura_cm and produto.comprimento_cm else None),
                    "gsm": produto.gsm, "material": produto.material_ref,
                    "peso_kg": produto.peso_kg, "peso_tipo": produto.peso_tipo,
                    "peso_fonte": produto.peso_fonte},
        "fornecedor": {"id": fornecedor.id if fornecedor else None,
                       "nome": fornecedor.nome if fornecedor else None,
                       "tipo": fornecedor.tipo.value if fornecedor else None},
        "cost_method": produto.cost_method,
        "confianca": produto.custo_confianca,
        "precisa_revisao": produto.precisa_revisao,
        "revisao_motivo": produto.revisao_motivo,
        "custo": custo,
        "frescor": frescor(session, produto.custo_ref_data),
        # O espalhamento do `contexto` levava `Decimal` cru para dentro da memória — o
        # `contexto["fiscal"]` passava por `como_dict()`, mas `icms_pct`,
        # `aliquota_interna_destino`, `fcp_pct` e `encargo_pct` vinham do nível de cima e
        # escapavam da conversão. Duas consequências, e a segunda é a silenciosa:
        #
        #   * `/calculadora/calcular` devolve este dicionário como JSON e quebrava com
        #     "Object of type Decimal is not JSON serializable" — sempre que o cenário
        #     fiscal resolvia, que é o caso do cenário padrão do catálogo;
        #   * `memoria_json` serializa com `default=str`, então um item novo gravaria
        #     `"0.18"` (texto) onde o histórico tem `0.18` (número).
        "fiscal": {**{k: _para_json(v) for k, v in contexto.items() if k != "fiscal"},
                   "memoria_fiscal": contexto["fiscal"].como_dict()},
        "margem": margem.como_dict(),
        # `como_dict()`, não `asdict()`: a memória é JSON, e o núcleo é Decimal. A conversão
        # para float é explícita e acontece uma vez, na fronteira — nunca por `default=str`,
        # que transformaria dinheiro em string e mudaria o formato do snapshot.
        "comercial": (resultado.como_dict() if resultado else None),
        "cenario": {"origem_logistica": cot.estado_origem,
                    "uf_origem_fiscal": contexto.get("uf_origem_fiscal"),
                    "destino": cot.estado_destino,
                    "uf_destino_fiscal": contexto.get("uf_destino_fiscal"),
                    "contribuinte": cot.contribuinte_icms,
                    "finalidade": contexto.get("finalidade"),
                    "consumidor_final": contexto.get("consumidor_final"),
                    "condicao_pagamento": cot.condicao_pagamento},
        "bloqueado": contexto.get("bloqueado", False),
        "motivo_bloqueio": contexto.get("motivo_bloqueio"),
        "gerado_em": datetime.utcnow().isoformat(),
    }


def _para_json(valor):
    """Fronteira de saída: `Decimal` vira `float`, o resto passa como está.

    A memória do preço é **representação externa** — vai para JSON, para o snapshot do item
    e para o baseline. `Decimal` não é serializável, e deixar `default=str` resolver
    transformaria número em texto e mudaria o formato do que já está gravado.
    """
    from decimal import Decimal

    if isinstance(valor, Decimal):
        return para_float(valor)
    if isinstance(valor, dict):
        return {k: _para_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_para_json(v) for v in valor]
    return valor


def memoria_json(memoria: dict) -> str:
    return json.dumps(memoria, ensure_ascii=False, default=str)

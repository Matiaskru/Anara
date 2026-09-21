"""Carga inicial das tabelas de configuração — idempotente.

Só insere o que ainda não existe; nunca sobrescreve o que o Matias já editou no painel.
Todos os números aqui vêm de documento (KTC Pricing Master, PI/cotações KTC, tabela de DIFAL
da Anara, aba 05_Premissas da planilha) ou de regra comercial explicitamente confirmada.
Nada é chutado: o que não temos fonte fica de fora e o produto vai para revisão.
"""
from datetime import date

from sqlmodel import Session, select

from app.db import engine
from app import politica_comercial as _pol
from app.models import (
    AliquotaInterestadual, RegraFcp,
    CmtPreco, CondicaoPagamento, EstadoFiscal, Fornecedor, MargemRegra, MaterialPreco,
    NcmRegra, ParametroKTC, Premissa, RegraFiscalVenda, ToalhaPreco, TipoFornecedor, CostMethod,
)

FONTE_MASTER = "KTC_Pricing_Master_Simple.xlsx"
FONTE_PLANILHA = "Sistema de preços Anara novo.xlsx · 05_Premissas"
FONTE_DIFAL = "Tabela de DIFAL Anara — coluna Carga Final"

# ---------------------------------------------------------------------------
# Fornecedores
# ---------------------------------------------------------------------------
FORNECEDORES = [
    dict(codigo="KTC", nome="Kazareen Textile Company", tipo=TipoFornecedor.importado_ktc,
         pais="Egito", moeda_custo="USD", cost_method_padrao=CostMethod.ktc_quoted,
         observacoes="Fabricante do Egito. Custo em US$ EXW + nacionalização por Navegantes-SC."),
    dict(codigo="DAUNE", nome="Daune", tipo=TipoFornecedor.nacional, pais="Brasil",
         moeda_custo="BRL", cost_method_padrao=CostMethod.national_supplier,
         observacoes="Fornecedor nacional. Não passa por motor industrial KTC nem por nacionalização."),
    dict(codigo="DECOR_TRICOT", nome="Decor Tricot", tipo=TipoFornecedor.nacional, pais="Brasil",
         moeda_custo="BRL", cost_method_padrao=CostMethod.national_supplier,
         observacoes="Fornecedor nacional (peseiras/itens decorativos em tricô)."),
    # 21/09/2026 — cobertores. Fábrica em Guaratinguetá, faturamento em São Paulo (origem
    # fiscal SP). Preço recebido como "NET — CIF Barueri", com pagamento antecipado (condição
    # de COMPRA do fornecedor, não do cliente). Sem crédito fiscal de entrada documentado.
    dict(codigo=_pol.CODIGO_ELIS, nome="ELIS (Guaratinguetá)", tipo=TipoFornecedor.nacional,
         pais="Brasil", moeda_custo="BRL", cost_method_padrao=CostMethod.national_supplier,
         uf_origem_fiscal="SP", origem_logistica_cidade="Guaratinguetá", origem_logistica_uf="SP",
         observacoes="Cobertores. Fábrica em Guaratinguetá/SP, faturado em São Paulo. Custo "
                     "informado como preço NET CIF Barueri; pagamento antecipado ao fornecedor; "
                     "produção de 30 a 40 dias úteis (sujeito a alteração); pedido mínimo 500 peças."),
]

# ---------------------------------------------------------------------------
# Premissas gerais
# ---------------------------------------------------------------------------
PREMISSAS = [
    dict(chave="fx_usd_brl", valor_num=5.11, unidade="BRL/USD",
         descricao="Câmbio usado na nacionalização", fonte=FONTE_PLANILHA),
    dict(chave="frete_int_usd_kg", valor_num=0.516, unidade="USD/kg",
         descricao="Frete internacional por kg (US$ 645 para 1.250 kg)", fonte=FONTE_PLANILHA),
    dict(chave="frete_int_referencia_usd", valor_num=645.0, unidade="USD",
         descricao="Frete de referência do embarque", fonte=FONTE_PLANILHA),
    dict(chave="frete_int_referencia_kg", valor_num=1250.0, unidade="kg",
         descricao="Peso de referência do embarque", fonte=FONTE_PLANILHA),
    dict(chave="outras_desp_usd_un", valor_num=0.2487532709, unidade="USD/un",
         descricao="Outras despesas de nacionalização por unidade", fonte=FONTE_PLANILHA),
    dict(chave="pis_cofins_nominal_pct", valor_num=0.0925, unidade="%",
         descricao="PIS/COFINS nominal da venda, antes da exclusão do ICMS da base. O "
                   "percentual efetivo sobre a receita é calculado por item: "
                   "nominal x (1 - ICMS da operação).",
         fonte="Brendo Simão — Contabilidade Indústria Química Anastacio — 09/09/2026 — "
               "planilha \"Fator Cálculo Exclusão ICMS .xlsx\"",
         notas="PIS 1,65% + COFINS 7,60%. Não editar como se fosse a alíquota efetiva: "
               "9,25% nunca incide cheio sobre o faturamento."),
    # LEGADO. Era o efetivo fixo da metodologia anterior — na prática, a aproximação do cenário
    # de ICMS 18% (7,585%). Continua semeada e vigente porque cotações antigas a pinaram e
    # precisam continuar interpretáveis; nenhuma precificação nova a lê.
    dict(chave="pis_cofins_pct", valor_num=0.0759, unidade="%",
         descricao="LEGADO — PIS/COFINS efetivo fixo da metodologia anterior (até 09/09/2026). "
                   "Não alimenta precificação nova: ver pis_cofins_nominal_pct.",
         fonte=FONTE_PLANILHA),
    # Política comercial de 16/09/2026 (Fase 3A): a comissão variável da cotação, para os
    # itens não-Daune. Daune tem comissão fixa na própria regra de margem (5%).
    dict(chave=_pol.CHAVE_COMISSAO_BASE, valor_num=float(_pol.COMISSAO_BASE_PCT), unidade="%",
         descricao="Comissão-base da comissão variável da cotação (itens não-Daune): vale com "
                   "desconto zero e cai proporcionalmente ao desconto ponderado por valor.",
         fonte=_pol.FONTE, valid_from=_pol.DATA_VIGENCIA,
         notas="Estimativa de pricing sobre a receita comercial (preço × quantidade). Não é a "
               "comissão pagável, que depende de faturamento e recebimento."),
    dict(chave=_pol.CHAVE_COMISSAO_MINIMA, valor_num=float(_pol.COMISSAO_MINIMA_PCT), unidade="%",
         descricao="Comissão mínima da cotação: a comissão variável nunca cai sozinha abaixo "
                   "disto. Se com ela algum item ficar abaixo do piso, é exceção a aprovar.",
         fonte=_pol.FONTE, valid_from=_pol.DATA_VIGENCIA),
    # Política comercial de 21/09/2026: B2B com 5% de comissão sobre a receita líquida de
    # ICMS, tabela = 2 × B2B, comissão por item pela faixa do desconto sobre a tabela.
    dict(chave=_pol.CHAVE_COMISSAO_B2B, valor_num=float(_pol.COMISSAO_B2B_PCT), unidade="%",
         descricao="Comissão que forma o preço B2B recomendado: 5% sobre a receita líquida do "
                   "ICMS suportado pela Anara (próprio + DIFAL do remetente; FCP fica na base).",
         fonte=_pol.FONTE_2026_09_21, valid_from=_pol.DATA_VIGENCIA_2026_09_21),
    dict(chave=_pol.CHAVE_FATOR_TABELA, valor_num=float(_pol.FATOR_TABELA), unidade="×",
         descricao="Preço de tabela = fator × B2B recomendado (2 = 100% de markup sobre o B2B). "
                   "Derivado sempre do B2B vigente do cenário — nunca de cache.",
         fonte=_pol.FONTE_2026_09_21, valid_from=_pol.DATA_VIGENCIA_2026_09_21),
    dict(chave=_pol.CHAVE_FAIXAS_COMISSAO,
         valor_txt="[[0.10,0.09],[0.20,0.08],[0.30,0.07],[0.40,0.06]]",
         descricao="Escada de comissão por desconto sobre a tabela: [limite do desconto, taxa]. "
                   "Desconto 0% = comissao_base_pct (10%); acima do último limite = "
                   "comissao_min_pct (5%). 10,00% → 9%; 10,01% → 8%; 50% (= B2B) → 5%.",
         fonte=_pol.FONTE_2026_09_21 + " · contrato cl. 6.1.1 · e-mail Jan Krueder 17/06/2026",
         valid_from=_pol.DATA_VIGENCIA_2026_09_21),
    # A premissa `icms_fallback_pct` foi APOSENTADA na Onda 1: cenário fiscal que não se
    # resolve vira REVIEW_REQUIRED, não vira 18%. A linha some da semeadura; bases antigas que
    # já a têm continuam com ela guardada, sem efeito — nenhum código a lê mais.
    dict(chave="fiscal_uf_origem_padrao", valor_txt="SP", unidade="UF",
         descricao="Origem FISCAL padrão da operação quando nem a cotação nem o fornecedor a "
                   "definem. É default configurado, não evidência — a memória do preço registra "
                   "quando a origem veio daqui.",
         fonte="Decisões de 03/09/2026 — cenários nacionais SP→*",
         notas="Origem fiscal não se confunde com origem logística. Se um fornecedor faturar de "
               "outra UF, cadastrar em Fornecedor.uf_origem_fiscal."),
    dict(chave="fiscal_finalidade_padrao", valor_txt="USO_CONSUMO",
         descricao="Finalidade padrão da operação — hotel consome o enxoval, não revende",
         fonte="Decisões de 03/09/2026",
         notas="Editável por cliente/unidade e sobrescrevível na cotação. Consumidor final é "
               "DERIVADO desta finalidade, nunca digitado."),
    dict(chave="validade_dias", valor_num=5.0, unidade="dias",
         descricao="Validade padrão da proposta", fonte="Premissas do site de cotação (ago/2026)"),
    dict(chave="comissao_tabela", valor_txt="[[0.0,0.05],[0.6,0.06],[0.7,0.07],[0.8,0.08],[0.9,0.09],[1.0,0.1]]",
         descricao="Comissão comercial por faixa de markup", fonte=FONTE_PLANILHA),
    dict(chave="indice_algodao", valor_num=100.8, unidade="índice",
         descricao="Indicador de algodão — NÃO altera preço automaticamente",
         fonte=FONTE_MASTER, notas="Indicador de acompanhamento. Só vira preço novo quando a KTC "
                                  "confirmar material novo."),
    dict(chave="indice_petroleo", valor_num=100.0, unidade="índice",
         descricao="Indicador de petróleo — NÃO altera preço automaticamente",
         notas="Indicador de acompanhamento; não entra em nenhuma fórmula de custo."),
    dict(chave="freshness_fresh_dias", valor_num=30.0, unidade="dias",
         descricao="Até quantos dias um preço cotado é considerado FRESH"),
    dict(chave="freshness_aging_dias", valor_num=60.0, unidade="dias",
         descricao="Até quantos dias um preço cotado é considerado AGING (acima disso, STALE)"),
    dict(chave="catalogo_origem", valor_txt="São Paulo",
         descricao="Cenário padrão do catálogo: estado de origem do preço-base"),
    dict(chave="catalogo_destino", valor_txt="São Paulo",
         descricao="Cenário padrão do catálogo: estado de destino do preço-base"),
    dict(chave="catalogo_contribuinte", valor_num=0.0,
         descricao="Cenário padrão do catálogo: 1 = cliente contribuinte, 0 = não contribuinte"),
    dict(chave="catalogo_condicao_pagamento", valor_txt="30",
         descricao="Cenário padrão do catálogo: condição de pagamento do preço-base"),
    dict(chave="termos_padrao", valor_txt=(
        "Validade da proposta: 5 dias corridos a partir da data de emissão.\n"
        "Preços em reais (R$), com impostos inclusos, para as condições comerciais desta proposta.\n"
        "Prazo de entrega conforme indicado nesta proposta, contado a partir da confirmação do pedido.\n"
        "Frete conforme condição indicada nesta proposta.\n"
        "Alterações de quantidade, prazo, destino ou condição de pagamento podem alterar os preços.\n"
        "Pedido confirmado mediante aceite formal desta proposta."),
        descricao="Termos e condições padrão da proposta", fonte="Premissas do site de cotação"),
]

# ---------------------------------------------------------------------------
# Motor industrial KTC — materiais (USD/m²)
# ---------------------------------------------------------------------------
MATERIAIS = [
    # (material, TC, weave, algodão%, poliéster%, plain, stripe)
    ("230TC Percale 100% Cotton", 230, "Percale", 1.0, 0.0, 1.20, None),
    ("200TC Percale Polycotton", 200, "Percale", None, None, 1.10, None),
    ("250TC Sateen CVC 70/30", 250, "Sateen", 0.70, 0.30, 1.25, 1.30),
    ("250TC Sateen 100% Cotton", 250, "Sateen", 1.0, 0.0, 1.30, 1.35),
    ("300TC Sateen CVC 70/30", 300, "Sateen", 0.70, 0.30, 1.35, 1.40),
    ("300TC Sateen 100% Cotton", 300, "Sateen", 1.0, 0.0, 1.40, 1.45),
    ("400TC Sateen 100% Cotton", 400, "Sateen", 1.0, 0.0, 1.50, 1.55),
]

CMTS = [
    ("Flat Sheet", None, 0.75, None),
    ("Top Sheet", None, 0.75, "Mesma construção do Flat Sheet na lista da KTC — a PI chama de "
                              "Top Sheet, a lista de CMT chama de Flat Sheet."),
    ("Fitted Sheet", None, 1.00, None),
    ("Duvet Cover", None, 1.50, None),
    ("Pillow Case", "standard", 0.50, None),
    ("Pillow Case", "com abas", 0.75, "Fronha com abas (2, 3 ou 4). Nomenclatura canônica: ABAS."),
]

# Preço por kg das toalhas.
#
# Os dois primeiros valores são os que a KTC informou na aba "Towels Costing" (fio simples e
# retorcido), genéricos de propósito. Os demais foram **derivados da PI ANARA de 23/08/2026**:
# para cada toalha da PI, `preço ÷ peso teórico (W × L × GSM ÷ 10.000.000)`. O resultado é
# limpo e consistente dentro de cada construção — o que confirma que a KTC precifica terry por
# peso, e explica por que ela avisou que o preço/kg varia por construção:
#
#     Bath Towel (86x150 550g e 90x160 650g)  → 6,03/0,7095 e 7,96/0,9360  = US$ 8,50/kg
#     Hand Towel (50x85 550g e 650g)          → 2,10/0,2338 e 2,49/0,2763  = US$ 9,00/kg
#     Bath Mat   (50x80 750g e 950g)          → 2,70/0,3000 e 3,42/0,3800  = US$ 9,00/kg
#     Pool Towel listrada (90x170 550g)       → 11,78/0,8415               = US$ 14,00/kg
#
# A cotação da HAMAN GLOBAL de 25/08/2026 — outro cliente, outras medidas, outras gramaturas —
# devolve **exatamente a mesma tabela**, o que é uma confirmação independente e forte:
#
#     Bath Towel 70x140 500g   → 4,17/0,4900  = 8,51/kg
#     Hand Towel 50x80 450g    → 1,62/0,1800  = 9,00/kg
#     Bath Mat   50x80 600g    → 2,16/0,2400  = 9,00/kg
#     Wash Cloth 33x33 550g    → 0,54/0,0599  = 9,02/kg
#     Pool Towel 86x172 550g   → 11,39/0,8136 = 14,00/kg
#     Pool Towel 100x160 550g  → 12,32/0,8800 = 14,00/kg
#
# Ainda assim são valores **derivados de documento**, não uma tabela que a KTC mandou. Vale
# confirmar com ela, sobretudo para construções fora das que os dois documentos cobrem.
DERIVADO_PI = "Derivado da PI ANARA 23/08/2026 (preço ÷ peso teórico)"

TOALHAS = [
    dict(subcategoria="Bath Towel", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="plain", price_usd_kg=8.50, fonte=DERIVADO_PI,
         notas="Confere nas duas medidas da PI (86x150 550g e 90x160 650g), em composições "
               "diferentes, e na HAMAN 25/08 (70x140 500g → 8,51/kg)."),
    dict(subcategoria="Hand Towel", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="plain", price_usd_kg=9.00, fonte=DERIVADO_PI,
         notas="Peça menor custa mais por kg — mais CMT por quilo. Confere em 550g e 650g na PI "
               "e em 50x80 450g na HAMAN 25/08."),
    dict(subcategoria="Bath Mat", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="plain", price_usd_kg=9.00, fonte=DERIVADO_PI,
         notas="Confere exatamente em 750g e 950g na PI e em 600g na HAMAN 25/08."),
    dict(subcategoria="Face Towel", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="plain", price_usd_kg=9.00,
         fonte="Derivado da cotação de amostras KTC 29/07/2026 (33x33 600 g: US$ 0,59 ÷ 0,06534 kg "
               "= 9,03/kg; 650 g com barra: 0,64 ÷ 0,07079 = 9,04/kg), na mesma taxa das peças "
               "pequenas de terry (rosto, piso, lavabo)",
         notas="Toalha de rosto pequena (face towel). Composição não entra: 100% e 91/9 têm o "
               "mesmo preço na cotação da KTC."),
    dict(subcategoria="Wash Cloth", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="plain", price_usd_kg=9.00,
         fonte="Derivado da HAMAN 25/08/2026 (0,54 ÷ 0,0599 kg = 9,02/kg), na mesma taxa das "
               "demais peças pequenas de terry da PI ANARA",
         notas="Único terry cujo preço vem de documento de outro cliente. Bate com a taxa de "
               "9,00/kg que a PI ANARA dá para toalha de rosto e de piso."),
    dict(subcategoria="Pool Towel", composicao=None, yarn_type=None, gsm=None,
         plain_or_stripe="stripe", price_usd_kg=14.00, fonte=DERIVADO_PI,
         notas="Toalha de piscina listrada — confere em 3 medidas (PI 90x170; HAMAN 86x172 e "
               "100x160). Bem acima do banho liso: listrado e acabamento pesam. Piscina LISA não "
               "tem preço conhecido em nenhum documento."),
    dict(subcategoria=None, composicao=None, yarn_type="single", gsm=None, price_usd_kg=8.00,
         fonte=FONTE_MASTER + " · Towels Costing",
         notas="Preço por kg de fio simples informado pela KTC. Genérico: confirmar por construção."),
    dict(subcategoria=None, composicao=None, yarn_type="twisted", gsm=None, price_usd_kg=8.50,
         fonte=FONTE_MASTER + " · Towels Costing",
         notas="Preço por kg de fio retorcido informado pela KTC. Genérico: confirmar por construção."),
]

PARAMETROS_KTC = [
    # chave, escopo, valor, nota
    ("shrinkage", "CVC", 0.03, "Encolhimento poly/cotton — informado pela KTC"),
    ("shrinkage", "COTTON", 0.05, "Encolhimento 100% algodão — informado pela KTC"),
    ("waste", None, 0.03, "Perda de tecido; entra como consumo / (1 - waste)"),
    ("quality_allowance", None, 0.01, "Perda de segunda qualidade ('II 1%' na planilha da KTC). "
                                      "Não confundir com Imposto de Importação."),
    # Fronhas: a planilha "Pillow Case Costing sheet.xlsx" da KTC aplica uma etapa de 2%
    # (rotulada '2% II' na planilha — é allowance de costing da KTC, NÃO o Imposto de
    # Importação brasileiro, que mora em NcmRegra). Escopo por família: só a fronha.
    ("quality_allowance", "Pillow Case", 0.02,
     "pillowcase_allowance — etapa de 2% da planilha 'Pillow Case Costing sheet.xlsx' (KTC). "
     "Não é Imposto de Importação; não reutiliza NcmRegra.ii_pct."),
    ("ktc_margin", None, 0.15, "Margem comercial da KTC sobre o preço final (EXW = custo / (1-0,15))"),
    ("hem_width_total_cm", "Flat Sheet", 4.0, "Bainha 2cm de cada lado, conforme exemplo validado pela KTC"),
    ("hem_length_total_cm", "Flat Sheet", 4.0, "Bainha 2cm de cada lado, conforme exemplo validado pela KTC"),
    ("hem_width_total_cm", "Top Sheet", 4.0, "Lençol de cima: mesma bainha do Flat Sheet validado "
                                              "pela KTC (2cm de cada lado)"),
    ("hem_length_total_cm", "Top Sheet", 4.0, "Lençol de cima: mesma bainha do Flat Sheet validado "
                                              "pela KTC (2cm de cada lado)"),
    ("paineis", "Top Sheet", 1.0, "Painel único"),
    ("hem_width_total_cm", "Duvet Cover", 4.0, "Allowance inicial; configurável por construção"),
    ("hem_length_total_cm", "Duvet Cover", 4.0, "Allowance inicial; configurável por construção"),
    ("paineis", "Duvet Cover", 2.0, "Duas faces do mesmo tecido (open bag)"),
    ("paineis", "Flat Sheet", 1.0, "Painel único"),
]

# Estimativa de peso (mesma régua da planilha): fios → GSM, GSM padrão por família e peso
# técnico por família. Só entram quando não há peso real informado pela KTC.
GSM_POR_TC = {"120": 105, "180": 110, "200": 115, "233": 120, "250": 125, "300": 135,
              "400": 150, "500": 165, "800": 200}
GSM_POR_FAMILIA = {"Fitted Sheet": 120, "Pillow Case": 120, "Bath Mat": 750,
                   "Duvet Insert": 250, "Bathrobe": 300, "Mattress Protector": 200,
                   "Mattress Topper": 200, "Pillow Protector": 200}
PESO_TECNICO_FAMILIA = {"Slipper": 0.15}

# Peso de embarque por m², calibrado com os pesos reais que a KTC declarou na PI de 23/08/2026.
# O peso que a KTC informa não é o peso do tecido: inclui bainha, os dois painéis da capa duvet
# e a embalagem. Por isso a conta de tecido puro (área × GSM) subestima o frete em roupa de
# cama — em toalha ela continua valendo, porque lá o GSM é o dado real da peça.
#
#     Top Sheet     6 medidas → 0,150 a 0,168 kg/m² (média 0,1574)
#     Bottom Sheet  6 medidas → 0,152 a 0,160 kg/m² (média 0,1557)
#     Duvet Cover   6 medidas → 0,304 a 0,310 kg/m² (média 0,3070) — dois painéis
#     Pillow Case  12 medidas → 0,571 a 0,667 kg/m² (média 0,6190) — peça pequena, peso mínimo
PESO_KG_M2_FAMILIA = {
    "Top Sheet": 0.1574, "Flat Sheet": 0.1574, "Bottom Sheet": 0.1557, "Fitted Sheet": 0.1557,
    "Duvet Cover": 0.3070, "Pillow Case": 0.6190,
}

# ---------------------------------------------------------------------------
# NCM / Imposto de Importação
# ---------------------------------------------------------------------------
# Desde 22/09/2026 o **I.I. ECONÔMICO da KTC/Egito é 0%** (`pricing_service.II_ECONOMICO_KTC`).
# As alíquotas preferenciais abaixo (3,5%; 1,62%) são as que formavam o custo até então e ficam
# aqui como DOCUMENTAÇÃO e como fonte da PROTEÇÃO COMERCIAL por família — a linha de NcmRegra
# que o seed grava sai com `ii_preferencial = 0` (vigente) e a alíquota anterior vai para
# `ParametroKTC protecao_comercial_pct`, que só forma preço (nunca custo, lucro ou margem).
II_ECONOMICO_KTC_DESDE = date(2026, 9, 22)
NOTA_II_ZERO = ("I.I. econômico KTC/Egito = 0% desde 22/09/2026 (decisão Anara). A alíquota "
                "preferencial anterior ({pct}) só forma preço, como proteção comercial da família "
                "(ParametroKTC protecao_comercial_pct) — não é tributo, custo nem despesa.")
NCMS = [
    dict(familia="Cotton Bedding", descricao_ncm="Bed linen of cotton", ncm="6302.31.00",
         ii_original=0.35, reducao_preferencial=0.90, ii_preferencial=0.035, prioridade=100),
    dict(familia="Bed linen sintético", descricao_ncm="Bed linen of synthetic fibers",
         ncm="6302.32.00", ii_original=0.35, reducao_preferencial=0.90, ii_preferencial=0.035,
         prioridade=100, confiavel=True,
         notas="II preferencial assumido igual ao 6302.31.00 (mesma família têxtil de cama)."),
    dict(familia="Toalha", descricao_ncm="Toilet/kitchen linen, cotton terry", ncm="6302.60.00",
         ii_original=0.35, reducao_preferencial=0.90, ii_preferencial=0.035, prioridade=100),
    dict(familia="Toalha sintética", descricao_ncm="Toilet/kitchen linen, synthetic terry",
         ncm="6302.93.00", ii_original=None, reducao_preferencial=None, ii_preferencial=None,
         prioridade=100, confiavel=False,
         notas="NCM da tabela da Anara, sem linha na tabela preferencial Egito. Validar II."),
    dict(familia="Manta sintética", descricao_ncm="Blankets, synthetic", ncm="6301.40.00",
         ii_original=None, ii_preferencial=None, prioridade=100, confiavel=False,
         notas="Sem linha na tabela preferencial. Validar II."),
    dict(familia="Manta algodão", descricao_ncm="Blankets, cotton", ncm="6301.30.00",
         ii_original=None, ii_preferencial=None, prioridade=100, confiavel=False,
         notas="Sem linha na tabela preferencial. Validar II."),
    dict(familia="Edredom / Insert", descricao_ncm="Bedspreads, duvets and similar",
         ncm="9404.40.00", ii_original=None, ii_preferencial=None, prioridade=100, confiavel=False,
         notas="Fora da tabela preferencial Egito. O sistema vinha usando 7% por fallback — "
               "esse número não tem documento; validar com quem cuida da importação."),
    dict(familia="Travesseiro", descricao_ncm="Pillows, cushions and similar", ncm="9404.90.00",
         ii_original=0.162, reducao_preferencial=0.90, ii_preferencial=0.0162, prioridade=100),
    dict(familia="Chinelos", descricao_ncm="Footwear", ncm="6404.19.00", ii_original=0.35,
         reducao_preferencial=0.90, ii_preferencial=0.035, prioridade=100),
    # Regra de família com prioridade sobre o NCM — vigente e explícita, como o Matias pediu.
    dict(familia="Roupão", descricao_ncm="Roupões de algodão — 6208.91.00", ncm="6208.91.00",
         ii_original=0.35, reducao_preferencial=0.90, ii_preferencial=0.035, prioridade=10,
         confiavel=False,
         notas="II de 3,5% aplicado por regra de família (configuração explícita). O NCM cadastrado "
               "(6309.00.10) é de artigos usados e parece erro de cadastro — marcado para validação, "
               "não corrigido por hipótese. Se a regra de família cair, a alíquota vira 35%."),
]

# Mapa família estruturada → NCM/II, seguindo o que a própria planilha já aplicava a cada
# família (nada aqui é alíquota nova: é a mesma que os SKUs já carregavam, agora explícita
# e versionada, para que produto novo da mesma família não fique sem regra).
NCM_POR_FAMILIA = [
    (["Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet", "Duvet Cover", "Pillow Case",
      "Pillow Protector", "Bed Runner"], "6302.31.00", 0.35, 0.90, 0.035, True,
     "Roupa de cama — tabela preferencial Egito (mesma alíquota que a planilha já aplicava)."),
    (["Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth"], "6302.60.00",
     0.35, 0.90, 0.035, True, "Linha banho/terry — tabela preferencial Egito."),
    (["Slipper"], "6404.19.00", 0.35, 0.90, 0.035, True, "Chinelos — tabela preferencial Egito."),
    (["Mattress Protector", "Mattress Topper", "Pillow"], "9404.90.00", 0.162, 0.90, 0.0162, True,
     "Travesseiros, protetores e toppers — tabela preferencial Egito."),
    (["Duvet Insert"], "9404.40.00", None, None, None, False,
     "Fora da tabela preferencial Egito e sem II confiável. O sistema antigo usava 7% por "
     "fallback, sem documento. Validar antes de cotar."),
    (["Bathrobe"], "6208.91.00", 0.35, 0.90, 0.035, True,
     "II de 3,5% por regra de família (configuração explícita). O NCM cadastrado é de artigos "
     "usados e parece erro de cadastro — marcado para validação, não corrigido por hipótese."),
]


def _pct_texto(v) -> str:
    return "sem alíquota confiável (0%)" if v is None else f"{float(v) * 100:g}%"


def _ncm_vigente(regra: dict) -> dict:
    """A linha de NcmRegra como ela É desde 22/09/2026: mesmo NCM, I.I. econômico 0%."""
    anterior = regra.get("ii_preferencial")
    nota = NOTA_II_ZERO.format(pct=_pct_texto(anterior))
    return {**regra, "ii_preferencial": 0.0, "confiavel": True,
            "valid_from": II_ECONOMICO_KTC_DESDE,
            "notas": (nota + (" · " + regra["notas"] if regra.get("notas") else ""))}


# Proteção comercial de precificação por família (fração): a alíquota preferencial que formava
# o custo de cada família até 22/09/2026 — e 0 onde não havia alíquota confiável (essas
# famílias eram precificadas com 0 e ficavam em revisão; agora ficam com preço e regra
# explícita). Só forma preço. Não é alíquota fiscal; não entra em custo, lucro ou margem.
PROTECAO_COMERCIAL_POR_FAMILIA = {
    **{f: (pref or 0.0) for familias, _ncm, _o, _r, pref, _c, _n in NCM_POR_FAMILIA for f in familias},
    **{r["familia"]: (r.get("ii_preferencial") or 0.0) for r in NCMS},
    # famílias do catálogo KTC sem linha de NCM até 22/09/2026: eram precificadas com 0
    "Blanket": 0.0, "Bath Rug": 0.0, "Face Towel": 0.0,
}
FONTE_PROTECAO_COMERCIAL = ("Alíquota de I.I. preferencial que formava o custo da família até "
                            "22/09/2026, preservada como proteção comercial de precificação "
                            "(decisão Anara de 22/09/2026). Não é tributo, custo nem despesa.")

# ---------------------------------------------------------------------------
# Estados (carga final do DIFAL) — nunca recalculada pelo sistema
# ---------------------------------------------------------------------------
ESTADOS = [
    # estado, uf, interestadual, interna, base simples, base dupla, FEM, carga final
    ("Acre", "AC", 0.04, 0.19, 0.15, 0.1852, 0.0, 0.1852),
    ("Alagoas", "AL", 0.04, 0.20, 0.16, 0.20, 0.0, 0.20),
    ("Amapá", "AP", 0.04, 0.18, 0.14, 0.1707, 0.0, 0.14),
    ("Amazonas", "AM", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Bahia", "BA", 0.04, 0.205, 0.165, 0.2075, 0.02, 0.2275),
    ("Ceará", "CE", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Distrito Federal", "DF", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Espírito Santo", "ES", 0.04, 0.17, 0.13, 0.1566, 0.0, 0.1566),
    ("Goiás", "GO", 0.04, 0.19, 0.15, 0.1852, 0.0, 0.1852),
    ("Maranhão", "MA", 0.04, 0.23, 0.19, 0.2468, 0.0, 0.19),
    ("Mato Grosso", "MT", 0.04, 0.17, 0.13, 0.1566, 0.0, 0.13),
    ("Mato Grosso do Sul", "MS", 0.04, 0.17, 0.13, 0.1566, 0.0, 0.13),
    ("Minas Gerais", "MG", 0.04, 0.18, 0.14, 0.1707, 0.0, 0.1707),
    ("Pará", "PA", 0.04, 0.19, 0.15, 0.1852, 0.0, 0.1852),
    ("Paraíba", "PB", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Paraná", "PR", 0.04, 0.195, 0.155, 0.1925, 0.0, 0.1925),
    ("Pernambuco", "PE", 0.04, 0.205, 0.165, 0.2075, 0.02, 0.2275),
    ("Piauí", "PI", 0.04, 0.225, 0.185, 0.2387, 0.02, 0.2587),
    ("Rio de Janeiro", "RJ", 0.04, 0.22, 0.18, 0.2308, 0.02, 0.20),
    ("Rio Grande do Norte", "RN", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Rio Grande do Sul", "RS", 0.04, 0.17, 0.13, 0.1566, 0.0, 0.1566),
    ("Rondônia", "RO", 0.04, 0.195, 0.155, 0.1925, 0.0, 0.1925),
    ("Roraima", "RR", 0.04, 0.20, 0.16, 0.20, 0.0, 0.16),
    ("Santa Catarina", "SC", 0.04, 0.17, 0.13, 0.1566, 0.0, 0.13),
    ("São Paulo", "SP", 0.04, 0.18, 0.14, 0.1707, 0.0, 0.1707),
    ("Sergipe", "SE", 0.04, 0.19, 0.15, 0.1852, 0.0, 0.1852),
    ("Tocantins", "TO", 0.04, 0.20, 0.16, 0.20, 0.0, 0.20),
]

# Override explícito: venda dentro de SP é 18% para contribuinte e para não contribuinte.
# (Regra comercial atual da Anara — corrige o 4% que a tabela antiga trazia.)
REGRAS_FISCAIS = [
    dict(origem="São Paulo", destino="São Paulo", contribuinte=True, icms_venda=0.18,
         regra="Intraestadual SP — alíquota interna 18%, independente de o cliente ser contribuinte",
         prioridade=10, fonte="Premissa comercial Anara (ago/2026)"),
    dict(origem="São Paulo", destino="São Paulo", contribuinte=False, icms_venda=0.18,
         regra="Intraestadual SP — alíquota interna 18%, independente de o cliente ser contribuinte",
         prioridade=10, fonte="Premissa comercial Anara (ago/2026)"),
]

# ---------------------------------------------------------------------------
# Margens líquidas-alvo padrão
# ---------------------------------------------------------------------------
FAMILIAS_LENCOL = ["Flat Sheet", "Top Sheet", "Bottom Sheet", "Fitted Sheet"]
FAMILIAS_TOALHA = ["Bath Towel", "Hand Towel", "Face Towel", "Pool Towel", "Beach Towel",
                   "Bath Mat", "Wash Cloth", "Towel"]

MARGENS = [
    # fornecedor DAUNE / DECOR_TRICOT ganham de qualquer regra de família KTC
    dict(nome="Daune — padrão", fornecedor_codigo="DAUNE", margem_pct=0.14, prioridade=20,
         notas="14% para qualquer família fornecida pela Daune."),
    dict(nome="Decor Tricot — padrão", fornecedor_codigo="DECOR_TRICOT", margem_pct=0.14,
         prioridade=20, notas="14% para qualquer família fornecida pela Decor Tricot."),
    # KTC por família
    *[dict(nome=f"KTC — {f} (toalha)", fornecedor_codigo="KTC", familia=f, margem_pct=0.12,
           prioridade=40, notas="Linha de banho/terry: 12%.") for f in FAMILIAS_TOALHA],
    dict(nome="KTC — Bathrobe", fornecedor_codigo="KTC", familia="Bathrobe", margem_pct=0.12,
         prioridade=40, notas="Roupões: 12%."),
    # lençóis por faixa de TC
    *[dict(nome=f"KTC — {f} < 300TC", fornecedor_codigo="KTC", familia=f, max_thread_count=300,
           margem_pct=0.16, prioridade=30, notas="Lençóis abaixo de 300 fios: 16%.")
      for f in FAMILIAS_LENCOL],
    *[dict(nome=f"KTC — {f} >= 300TC", fornecedor_codigo="KTC", familia=f, min_thread_count=300,
           margem_pct=0.18, prioridade=30, notas="Lençóis de 300 fios ou mais: 18%.")
      for f in FAMILIAS_LENCOL],
    # resto da KTC
    dict(nome="KTC — demais famílias", fornecedor_codigo="KTC", margem_pct=0.15, prioridade=60,
         notas="Fronhas, capas duvet, inserts, travesseiros, protetores, chinelos e especiais: 15%."),
    # rede de segurança: produto sem fornecedor identificado
    dict(nome="Geral — sem fornecedor definido", margem_pct=0.15, prioridade=900,
         notas="Regra geral, usada só quando não há regra de fornecedor nem de família."),
]

# As 21 regras acima foram ENCERRADAS em 16/09/2026 pela política comercial da Fase 3A — não
# apagadas: os itens que as pinaram continuam sendo lidos por elas. Num banco novo elas nascem
# já encerradas; no banco real, quem as encerra é `scripts/aplicar_politica_comercial_2026_09_16.py`.
for _regra in MARGENS:
    _regra.setdefault("valid_from", date(2026, 8, 28))    # a data em que foram semeadas
    _regra.setdefault("valid_to", _pol.DATA_VIGENCIA)


def _regras_da_politica_2026_09_16(antigas) -> list:
    """Uma regra nova por regra antiga, mesmo escopo e prioridade, derivada — não digitada."""
    novas = []
    for antiga in antigas:
        pol = _pol.regra_da_politica(antiga["margem_pct"], antiga.get("fornecedor_codigo"))
        escopo = {k: v for k, v in antiga.items()
                  if k not in ("nome", "margem_pct", "notas", "valid_to", "valid_from")}
        novas.append(dict(
            escopo, nome=_pol.nome_da_regra_nova(antiga["nome"]),
            margem_pct=float(pol["margem_pct"]), piso_pct=float(pol["piso_pct"]),
            comissao_formacao_pct=float(pol["comissao_formacao_pct"]),
            preco_travado=bool(pol["preco_travado"]),
            margem_anterior_pct=float(pol["margem_anterior_pct"]),
            politica=_pol.ROTULO, fonte=_pol.FONTE, valid_from=_pol.DATA_VIGENCIA,
            notas=(f"{_pol.FONTE}. Sucede '{antiga['nome']}' "
                   f"({antiga['margem_pct'] * 100:.0f}%).")))
    return novas


MARGENS_2026_09_16 = _regras_da_politica_2026_09_16(MARGENS)
# As 21 regras da política de 16/09/2026 foram ENCERRADAS em 21/09/2026 pela política nova — não
# apagadas: os itens que as pinaram continuam sendo lidos por elas. Num banco novo já nascem
# encerradas; no banco real quem as encerra é `scripts/aplicar_politica_comercial_2026_09_21.py`.
for _regra in MARGENS_2026_09_16:
    _regra.setdefault("valid_to", _pol.DATA_VIGENCIA_2026_09_21)


def _regras_da_politica_2026_09_21() -> list:
    """As regras da política de 21/09/2026 — dados de `politica_comercial.margens_2026_09_21`."""
    novas = []
    for r in _pol.margens_2026_09_21():
        dados = dict(r)
        for chave in ("margem_pct", "piso_pct", "comissao_formacao_pct"):
            if dados.get(chave) is not None:
                dados[chave] = float(dados[chave])
        dados.setdefault("notas", f"{_pol.FONTE_2026_09_21}. Margem FINAL no B2B, pós-comissão de 5%.")
        novas.append(dados)
    return novas


MARGENS_2026_09_21 = _regras_da_politica_2026_09_21()

# ---------------------------------------------------------------------------
# Condições de pagamento (encargo financeiro centralizado num lugar só)
# ---------------------------------------------------------------------------
CONDICOES = [
    ("À VISTA", "À vista", 0.0, True, 10, "Sem encargo financeiro."),
    ("30", "30 dias", 0.016, True, 20, "Encargo base de 30 dias."),
    ("30/60", "30/60 dias", 0.032, True, 30, "1,6% por parcela."),
    ("30/60/90", "30/60/90 dias", 0.048, True, 40, "1,6% por parcela."),
    ("30/60/90/120", "30/60/90/120 dias", 0.064, True, 50, "1,6% por parcela."),
    ("30/60/90/120/150", "30/60/90/120/150 dias", 0.080, True, 60, "1,6% por parcela."),
    # `SINAL30+30/60/90` NÃO é mais semeada (21/09/2026): sinal é composição — percentual à
    # vista + condição do saldo — e não uma condição opaca. Em bancos existentes a linha é
    # desativada pelo script de dados (`dados_2026_09_21.aplicar_sinal`), nunca apagada.
    ("CARTAO", "Cartão de crédito", None, False, 80,
     "Condição pedida nas premissas do site. Taxa da operadora ainda não confirmada."),
]


def _existe(session, modelo, **filtros) -> bool:
    stmt = select(modelo)
    for campo, valor in filtros.items():
        stmt = stmt.where(getattr(modelo, campo) == valor)
    return session.exec(stmt).first() is not None


# ---------------------------------------------------------------------------
# Alíquotas interestaduais — tabela de dados, não `if` no código
# ---------------------------------------------------------------------------
# Decisões de 03/09/2026. Origem SP. A mercadoria NACIONAL segue as duas faixas do Senado;
# a IMPORTADA segue os 4% da Res. 13/2012 **enquanto a regra lhe for aplicável** — e é por isso
# que isto é uma tabela: uma exceção por NCM ou por produto entra como linha de prioridade menor,
# sem tocar em uma linha de código.
UFS_FAIXA_7 = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "PA",
               "PB", "PE", "PI", "RN", "RO", "RR", "SE", "TO"]
UFS_FAIXA_12 = ["MG", "PR", "RJ", "RS", "SC"]

FONTE_ALIQUOTAS = "Decisões Anara 03/09/2026 · Res. Senado 22/1989 e 13/2012"


def aliquotas_interestaduais_seed() -> list:
    linhas = []
    for uf in UFS_FAIXA_7:
        linhas.append(dict(uf_origem="SP", uf_destino=uf, origem_fiscal="NACIONAL",
                           aliquota=0.07, prioridade=100, fonte=FONTE_ALIQUOTAS,
                           regra=f"Interestadual SP→{uf}, mercadoria nacional — 7%"))
    for uf in UFS_FAIXA_12:
        linhas.append(dict(uf_origem="SP", uf_destino=uf, origem_fiscal="NACIONAL",
                           aliquota=0.12, prioridade=100, fonte=FONTE_ALIQUOTAS,
                           regra=f"Interestadual SP→{uf}, mercadoria nacional — 12%"))
    for uf in UFS_FAIXA_7 + UFS_FAIXA_12:
        linhas.append(dict(
            uf_origem="SP", uf_destino=uf, origem_fiscal="IMPORTADA", aliquota=0.04,
            prioridade=100, fonte=FONTE_ALIQUOTAS,
            regra=f"Interestadual SP→{uf}, mercadoria importada — 4% (Res. Senado 13/2012)",
            notas="Vale enquanto a regra da mercadoria importada for aplicável ao item. "
                  "Exceção entra como linha por NCM ou por produto, com prioridade menor."))
    return linhas


def semear(verbose: bool = True) -> dict:
    contagem = {}
    with Session(engine) as s:
        # fornecedores
        n = 0
        for f in FORNECEDORES:
            if not _existe(s, Fornecedor, codigo=f["codigo"]):
                s.add(Fornecedor(**f)); n += 1
        contagem["fornecedores"] = n
        s.commit()

        fornecedor_por_codigo = {f.codigo: f.id for f in s.exec(select(Fornecedor)).all()}

        # premissas
        n = 0
        for p in PREMISSAS:
            if not _existe(s, Premissa, chave=p["chave"]):
                s.add(Premissa(**p)); n += 1
        contagem["premissas"] = n

        # alíquotas interestaduais (Onda 1) — idempotente por par UF × natureza
        n = 0
        for a in aliquotas_interestaduais_seed():
            if not _existe(s, AliquotaInterestadual, uf_origem=a["uf_origem"],
                           uf_destino=a["uf_destino"], origem_fiscal=a["origem_fiscal"]):
                s.add(AliquotaInterestadual(**a)); n += 1
        contagem["aliquotas_interestaduais"] = n

        # materiais
        n = 0
        for material, tc, weave, algodao, poli, plain, stripe in MATERIAIS:
            for tipo, preco in (("plain", plain), ("stripe", stripe)):
                if preco is None:
                    continue
                if not _existe(s, MaterialPreco, material=material, plain_or_stripe=tipo):
                    s.add(MaterialPreco(material=material, thread_count=tc, weave=weave,
                                        cotton_pct=algodao, poliester_pct=poli,
                                        plain_or_stripe=tipo, price_usd_m2=preco,
                                        fonte=FONTE_MASTER + " · Materials - CMT Prices",
                                        notas="KTC: 'These prices can vary from time to time'.")); n += 1
        contagem["materiais"] = n

        # CMT
        n = 0
        for familia, construcao, valor, nota in CMTS:
            if not _existe(s, CmtPreco, familia=familia, construcao=construcao):
                s.add(CmtPreco(familia=familia, construcao=construcao, cmt_usd=valor, notas=nota,
                               fonte=FONTE_MASTER + " · Materials - CMT Prices")); n += 1
        contagem["cmt"] = n

        # toalhas
        n = 0
        for t in TOALHAS:
            if not _existe(s, ToalhaPreco, yarn_type=t["yarn_type"], gsm=t["gsm"],
                           subcategoria=t["subcategoria"]):
                s.add(ToalhaPreco(**t)); n += 1
        contagem["toalhas"] = n

        # parâmetros KTC
        n = 0
        for chave, escopo, valor, nota in PARAMETROS_KTC:
            if not _existe(s, ParametroKTC, chave=chave, escopo=escopo):
                s.add(ParametroKTC(chave=chave, escopo=escopo, valor=valor, notas=nota,
                                   fonte=FONTE_MASTER)); n += 1
        contagem["parametros_ktc"] = n

        # tabelas de estimativa de peso
        n = 0
        for tc, gsm in GSM_POR_TC.items():
            if not _existe(s, ParametroKTC, chave="gsm_por_tc", escopo=tc):
                s.add(ParametroKTC(chave="gsm_por_tc", escopo=tc, valor=float(gsm),
                                   fonte=FONTE_PLANILHA + " · TC → GSM estimado",
                                   notas="Só usado quando o produto não tem GSM nem peso real.")); n += 1
        for familia, gsm in GSM_POR_FAMILIA.items():
            if not _existe(s, ParametroKTC, chave="gsm_por_familia", escopo=familia):
                s.add(ParametroKTC(chave="gsm_por_familia", escopo=familia, valor=float(gsm),
                                   fonte=FONTE_PLANILHA + " · GSM default por família")); n += 1
        for familia, peso in PESO_KG_M2_FAMILIA.items():
            if not _existe(s, ParametroKTC, chave="peso_kg_m2_familia", escopo=familia):
                s.add(ParametroKTC(chave="peso_kg_m2_familia", escopo=familia, valor=float(peso),
                                   fonte="Calibrado com os pesos reais da PI ANARA 23/08/2026",
                                   notas="Peso de embarque por m² — inclui bainha, painéis e "
                                         "embalagem. Só entra quando não há peso real da KTC.")); n += 1
        for familia, peso in PESO_TECNICO_FAMILIA.items():
            if not _existe(s, ParametroKTC, chave="peso_tecnico_familia", escopo=familia):
                s.add(ParametroKTC(chave="peso_tecnico_familia", escopo=familia, valor=float(peso),
                                   fonte=FONTE_PLANILHA + " · peso técnico por família")); n += 1
        contagem["tabelas_peso"] = n

        # NCM — gravado já como vigente desde 22/09/2026: I.I. econômico 0%
        n = 0
        for regra in NCMS:
            if not _existe(s, NcmRegra, familia=regra["familia"]):
                s.add(NcmRegra(fonte=FONTE_PLANILHA + " / NCM - ANARA.xlsx", **_ncm_vigente(regra))); n += 1
        contagem["ncm"] = n

        # NCM por família estruturada
        for familias, ncm, original, reducao, preferencial, confiavel, nota in NCM_POR_FAMILIA:
            for familia in familias:
                if not _existe(s, NcmRegra, familia=familia):
                    s.add(NcmRegra(**_ncm_vigente(dict(
                        familia=familia, ncm=ncm, ii_original=original, reducao_preferencial=reducao,
                        ii_preferencial=preferencial, confiavel=confiavel, notas=nota, prioridade=50,
                        fonte="Catálogo Anara + tabela preferencial Egito")))); n += 1
        contagem["ncm"] = n

        # proteção comercial de precificação por família (22/09/2026)
        n = 0
        for familia, pct in PROTECAO_COMERCIAL_POR_FAMILIA.items():
            if not _existe(s, ParametroKTC, chave="protecao_comercial_pct", escopo=familia):
                s.add(ParametroKTC(chave="protecao_comercial_pct", escopo=familia, valor=float(pct),
                                   valid_from=II_ECONOMICO_KTC_DESDE, fonte=FONTE_PROTECAO_COMERCIAL,
                                   notas=f"Alíquota preferencial anterior: {_pct_texto(pct if pct else None)}.")); n += 1
        contagem["protecao_comercial"] = n

        # estados
        n = 0
        for estado, uf, inter, interna, simples, dupla, fem, carga in ESTADOS:
            if not _existe(s, EstadoFiscal, uf=uf):
                s.add(EstadoFiscal(estado=estado, uf=uf, aliquota_interestadual=inter,
                                   aliquota_interna=interna, base_simples=simples,
                                   base_dupla=dupla, fem=fem, carga_final=carga,
                                   fonte=FONTE_DIFAL)); n += 1
        contagem["estados"] = n

        # regras fiscais explícitas
        n = 0
        for r in REGRAS_FISCAIS:
            if not _existe(s, RegraFiscalVenda, origem=r["origem"], destino=r["destino"],
                           contribuinte=r["contribuinte"]):
                s.add(RegraFiscalVenda(**r)); n += 1
        contagem["regras_fiscais"] = n

        # margens — as anteriores (encerradas em 16/09/2026) e as da política vigente
        n = 0
        for regra in MARGENS + MARGENS_2026_09_16 + MARGENS_2026_09_21:
            dados = dict(regra)
            codigo = dados.pop("fornecedor_codigo", None)
            dados["fornecedor_id"] = fornecedor_por_codigo.get(codigo) if codigo else None
            if not _existe(s, MargemRegra, nome=dados["nome"]):
                s.add(MargemRegra(**dados)); n += 1
        contagem["margens"] = n

        # condições de pagamento
        n = 0
        for codigo, label, encargo, confirmado, ordem, nota in CONDICOES:
            if not _existe(s, CondicaoPagamento, codigo=codigo):
                s.add(CondicaoPagamento(codigo=codigo, label=label, encargo_pct=encargo,
                                        encargo_confirmado=confirmado, ordem=ordem, notas=nota)); n += 1
        contagem["condicoes_pagamento"] = n

        # FCP/FECP e semântica da alíquota interna (Onda 1).
        # Só o RJ tem composição fixada pela regra canônica: base 20% + FECP 2% = 22%. As demais
        # UFs ficam sem `icms_interno_base` e sem linha de FCP — o que significa DESCONHECIDO,
        # e bloqueia o cenário em vez de assumir 0%. Generalizar o RJ seria inventar premissa.
        rj = s.exec(select(EstadoFiscal).where(EstadoFiscal.uf == "RJ")).first()
        if rj is not None and rj.icms_interno_base is None:
            rj.icms_interno_base, rj.interna_inclui_fcp = 0.20, True
            s.add(rj)
        if not _existe(s, RegraFcp, uf_destino="RJ"):
            s.add(RegraFcp(uf_destino="RJ", fcp_pct=0.02, situacao="APLICA",
                           regra="FECP do Rio de Janeiro — regra geral",
                           fonte="Regra canônica Anara 03/09/2026 — RJ: ICMS 20% + FECP 2%"))
        contagem["fcp"] = 1
        # Matriz fiscal de 21/09/2026: base interna (sem FCP) das 27 UFs e FCP por UF × família
        # para o escopo de produtos reconciliado. Idempotente; nada da tabela-benchmark
        # (base simples/dupla, FEM, carga final) é alterado.
        from app import fiscal_2026_09_21 as _fis
        contagem["matriz_fiscal_2026_09_21"] = _fis.aplicar_matriz(s)

        # Origem LOGÍSTICA da KTC: Itajaí-SC, que é o ponto de entrada da importação e a
        # origem declarada pela própria tabela TRANSAL. Daune e Decor ficam nulas: não se sabe
        # de onde embarcam, e usar a tabela de Itajaí para elas seria inventar frete.
        ktc = s.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
        if ktc is not None and not ktc.origem_logistica_cidade:
            ktc.origem_logistica_cidade, ktc.origem_logistica_uf = "Itajaí", "SC"
            s.add(ktc)

        s.commit()

    if verbose:
        print("[seeds]", contagem)
    return contagem


if __name__ == "__main__":
    semear()

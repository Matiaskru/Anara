"""Modelo de dados da plataforma Anara.

Evolução ago/2026: o sistema deixou de ser "o catálogo KTC importado do Excel" e passou a ser
um sistema único de precificação e cotação com **fornecedor como entidade central**. Cada
fornecedor pode chegar ao CUSTO NET por um caminho diferente (motor industrial KTC, preço KTC
cotado, custo de fornecedor nacional, manual), mas dali pra frente o motor comercial é o mesmo.

Tudo que é premissa (câmbio, frete, material, CMT, shrinkage, margem KTC, NCM/II, ICMS,
comissão, encargo financeiro, margens-alvo, termos) vive em tabela versionada — nada hardcoded
em fórmula ou em tela — e é fotografado na cotação, pra que histórico continue reproduzível.
"""
import enum
from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class StatusCotacao(str, enum.Enum):
    rascunho = "rascunho"
    enviada = "enviada"
    fechada = "fechada"
    perdida = "perdida"
    pedido = "pedido"


class TipoFornecedor(str, enum.Enum):
    """Como o fornecedor entrega custo pro sistema."""
    importado_ktc = "IMPORTADO_KTC"      # EXW em US$ + nacionalização Egito
    nacional = "NACIONAL"                # custo/preço já em R$, sem nacionalização
    outro = "OUTRO"


class CostMethod(str, enum.Enum):
    """Como o CUSTO NET desse produto é formado."""
    ktc_calculated = "KTC_CALCULATED"      # motor industrial KTC → EXW calculado → nacionalização
    ktc_quoted = "KTC_QUOTED"              # EXW cotado pela KTC → nacionalização
    national_supplier = "NATIONAL_SUPPLIER"  # custo de fornecedor nacional
    manual = "MANUAL"                      # custo digitado à mão
    legacy_excel = "LEGACY_EXCEL"          # veio da planilha antiga, origem não rastreada


class CostConfidence(str, enum.Enum):
    """Quanto se confia no custo — nunca deixar preço antigo passar por atual."""
    calculated = "CALCULATED"
    quoted = "QUOTED"
    estimated = "ESTIMATED"
    manual = "MANUAL"
    legacy = "LEGACY"
    review_required = "REVIEW_REQUIRED"


class OrigemFiscal(str, enum.Enum):
    """Natureza fiscal da mercadoria — decide qual faixa interestadual se aplica."""
    importada = "IMPORTADA"
    nacional = "NACIONAL"


class Finalidade(str, enum.Enum):
    """Finalidade da operação. `consumidor_final` é DERIVADO daqui, nunca um valor do enum."""
    revenda = "REVENDA"
    industrializacao = "INDUSTRIALIZACAO"
    uso_consumo = "USO_CONSUMO"
    ativo_imobilizado = "ATIVO_IMOBILIZADO"


# Consumidor final é derivado: uso/consumo e ativo imobilizado encerram a cadeia.
FINALIDADES_CONSUMIDOR_FINAL = {Finalidade.uso_consumo.value, Finalidade.ativo_imobilizado.value}


class StatusFiscal(str, enum.Enum):
    """Resultado da resolução fiscal do item. Separado do status de confiança do CUSTO."""
    ok = "OK"
    review_required = "REVIEW_REQUIRED"


class ResponsavelDifal(str, enum.Enum):
    nao_aplicavel = "NAO_APLICAVEL"
    remetente = "REMETENTE"          # entra no waterfall da Anara
    destinatario = "DESTINATARIO"    # registrado, NÃO reduz a margem da Anara


class StatusPagamento(str, enum.Enum):
    ok = "OK"
    review_required = "REVIEW_REQUIRED"


class TipoFrete(str, enum.Enum):
    cif = "CIF"
    fob = "FOB"
    a_combinar = "A_COMBINAR"
    outro = "OUTRO"


# ---------------------------------------------------------------------------
# Cadastros comerciais
# ---------------------------------------------------------------------------
class Cliente(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    cnpj_cpf: Optional[str] = None
    cidade_uf: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    contato_nome: Optional[str] = None
    departamento: Optional[str] = None          # Compras, Governança, Operações, ...
    finalidade: Optional[str] = None            # Finalidade — default vem de premissa
    ativo: bool = True                          # arquivar em vez de apagar
    criado_em: datetime = Field(default_factory=datetime.utcnow)


class Fornecedor(SQLModel, table=True):
    """KTC, Daune, Decor Tricot e os que vierem. O `tipo` decide qual caminho de custo se aplica."""
    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str = Field(index=True, unique=True)     # KTC | DAUNE | DECOR_TRICOT
    nome: str
    tipo: TipoFornecedor = Field(default=TipoFornecedor.nacional)
    pais: Optional[str] = None
    moeda_custo: str = "BRL"
    cost_method_padrao: CostMethod = Field(default=CostMethod.national_supplier)
    # UF de onde a NF deste fornecedor efetivamente sai. NULO = desconhecida — cai para a
    # premissa padrão, e a memória registra que veio de default, não de evidência.
    uf_origem_fiscal: Optional[str] = None
    ativo: bool = True
    observacoes: Optional[str] = None


# ---------------------------------------------------------------------------
# Premissas versionadas (nada hardcoded)
# ---------------------------------------------------------------------------
class Premissa(SQLModel, table=True):
    """Premissa numérica ou textual versionada por vigência.

    Ex.: fx_usd_brl, frete_int_usd_kg, outras_desp_usd_un, pis_cofins_pct, validade_dias,
    termos_padrao, indice_algodao, indice_petroleo.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    chave: str = Field(index=True)
    valor_num: Optional[float] = None
    valor_txt: Optional[str] = None
    unidade: Optional[str] = None
    descricao: Optional[str] = None
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    fonte_data: Optional[date] = None
    notas: Optional[str] = None
    criado_em: datetime = Field(default_factory=datetime.utcnow)


class MaterialPreco(SQLModel, table=True):
    """Preço de tecido KTC por m² (aba 'Materials - CMT Prices' do KTC Pricing Master)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    material: str                       # "250TC Sateen CVC 70/30"
    thread_count: Optional[int] = None
    weave: Optional[str] = None         # Percale | Sateen
    cotton_pct: Optional[float] = None
    poliester_pct: Optional[float] = None
    plain_or_stripe: str = "plain"      # plain | stripe
    price_usd_m2: float
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    fonte_data: Optional[date] = None
    notas: Optional[str] = None


class CmtPreco(SQLModel, table=True):
    """CMT (corte/costura/acabamento) por item, em US$."""
    id: Optional[int] = Field(default=None, primary_key=True)
    familia: str                        # Flat Sheet, Fitted Sheet, Duvet Cover, ...
    construcao: Optional[str] = None    # standard | oxford | open bag | ...
    cmt_usd: float
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class ToalhaPreco(SQLModel, table=True):
    """Preço de fio/tecido de toalha por kg (custo por peso — aba 'Towels Costing')."""
    id: Optional[int] = Field(default=None, primary_key=True)
    familia: str = "Towel"
    subcategoria: Optional[str] = None     # Bath Towel, Hand Towel, Bath Mat, Pool Towel...
    composicao: Optional[str] = None       # 100% Cotton Terry, 90/10 ...
    yarn_type: Optional[str] = None        # single | twisted
    gsm: Optional[int] = None
    plain_or_stripe: str = "plain"
    acabamento: Optional[str] = None
    price_usd_kg: float
    # True = o preço por kg já é EXW final (já embute CMT, perda de 2ª qualidade e margem KTC).
    # É o caso dos valores derivados de cotação e também da conta da própria KTC na aba
    # "Towels Costing", que faz Total Price = peso × preço/kg e para por aí.
    # False = é custo de matéria-prima, e aí CMT, qualidade e margem entram por cima.
    preco_final: bool = True
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class ParametroKTC(SQLModel, table=True):
    """Parâmetro do motor industrial KTC, com escopo opcional por família/construção.

    chave: shrinkage | waste | quality_allowance | ktc_margin | hem_width_total_cm |
           hem_length_total_cm | paineis
    escopo: None = default global; senão "familia" ou "familia|construcao" ou "CVC"/"COTTON"
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    chave: str = Field(index=True)
    escopo: Optional[str] = Field(default=None, index=True)
    valor: float
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class NcmRegra(SQLModel, table=True):
    """NCM e Imposto de Importação por família/produto (tabela preferencial Egito)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    familia: str                            # "Roupão", "Cotton Bedding", ...
    descricao_ncm: Optional[str] = None
    ncm: str
    ii_original: Optional[float] = None
    reducao_preferencial: Optional[float] = None
    ii_preferencial: Optional[float] = None
    prioridade: int = 100                   # menor = ganha (regra de família vence NCM genérico)
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    confiavel: bool = True                  # False = alerta "validar com quem cuida da importação"
    fonte: Optional[str] = None
    notas: Optional[str] = None


class EstadoFiscal(SQLModel, table=True):
    """Tabela de estados para o ICMS da venda — espelha a tabela de DIFAL da Anara.

    A coluna que entra no preço é a **Carga Final**; Base Simples/Base Dupla/FEM ficam
    guardadas só como memória de como aquela carga foi apurada. O sistema **não recalcula**
    a carga a partir delas.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    estado: str = Field(index=True)
    uf: str = Field(index=True)
    aliquota_interestadual: float = 0.04
    # `aliquota_interna` é a carga interna do destino **como cadastrada** — e a auditoria de
    # 03/09/2026 mostrou que ela nem sempre significa a mesma coisa: para o RJ ela vale 22%,
    # que é a base de 20% MAIS o FECP de 2%. Por isso a base passou a ter coluna própria.
    aliquota_interna: float = 0.18
    icms_interno_base: Optional[float] = None    # interna SEM FCP. NULO = não determinado
    interna_inclui_fcp: Optional[bool] = None    # NULO = não se sabe o que a coluna significa
    base_simples: Optional[float] = None
    base_dupla: Optional[float] = None
    fem: Optional[float] = None
    carga_final: float = 0.18
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class RegraFiscalVenda(SQLModel, table=True):
    """ICMS da venda por Origem × Destino × Contribuinte. Única fonte da alíquota."""
    id: Optional[int] = Field(default=None, primary_key=True)
    origem: str = Field(index=True)
    destino: str = Field(index=True)
    contribuinte: bool = Field(index=True)
    icms_venda: float
    regra: str
    prioridade: int = 100
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None


class AliquotaInterestadual(SQLModel, table=True):
    """Alíquota interestadual por par de UF × natureza da mercadoria.

    É **tabela de dados versionada**, nunca um `if` no código. Isso é o que permite tratar a
    exceção sem alterar programa: os 4% de mercadoria importada valem **quando a regra aplicável
    à mercadoria importada efetivamente se aplica** — e uma linha de prioridade menor, por NCM ou
    por produto, sobrepõe o par de UF quando houver exceção (conteúdo de importação, lista de
    bens sem similar nacional, decisão do fisco).

    Sem linha que resolva o cenário, o resultado é `REVIEW_REQUIRED`. **Nunca um padrão.**
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    uf_origem: str = Field(index=True)
    uf_destino: str = Field(index=True)
    origem_fiscal: str = Field(index=True)      # OrigemFiscal
    aliquota: float
    ncm: Optional[str] = Field(default=None, index=True)      # exceção por NCM
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")  # exceção por SKU
    prioridade: int = 100                       # menor ganha
    regra: str = ""
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class RegraFcp(SQLModel, table=True):
    """FCP/FEM aplicável a uma operação — **configurado**, nunca inferido pela UF.

    O adicional de Fundo de Combate à Pobreza não incide sobre tudo que entra num estado: a
    incidência depende do produto, e a lista varia por UF e por vigência. Por isso ele não é
    lido de uma coluna por estado — exige linha cadastrada dizendo a que se aplica.

    Sem linha que cubra a operação, o FCP é **zero e a memória registra que nenhuma regra foi
    encontrada**. Não é bloqueio: ausência de regra de FCP não impede formar preço. Vira
    bloqueio só se alguém marcar `exige_confirmacao`, para o caso de uma UF onde a operação
    notoriamente tem FCP e a alíquota ainda não foi levantada.

    A coluna `EstadoFiscal.fem` continua existindo para rastreabilidade da tabela histórica,
    mas **não alimenta o motor**.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    uf_destino: str = Field(index=True)
    ncm: Optional[str] = Field(default=None, index=True)
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")
    familia: Optional[str] = None
    fcp_pct: float = 0.0
    # Três situações, e "sem linha" não é nenhuma delas: ausência de regra é DESCONHECIDO.
    #   APLICA        — incide, com a alíquota de `fcp_pct`
    #   NAO_APLICA    — comprovadamente não incide (fonte obrigatória)
    #   DESCONHECIDO  — pode incidir e ninguém levantou → bloqueia quando muda preço
    situacao: str = "DESCONHECIDO"
    prioridade: int = 100                # menor ganha
    regra: str = ""
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    fonte: Optional[str] = None
    notas: Optional[str] = None


class MargemRegra(SQLModel, table=True):
    """Margem líquida-alvo padrão. Resolvida por prioridade — nunca por `if` espalhado no código."""
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    fornecedor_id: Optional[int] = Field(default=None, foreign_key="fornecedor.id")
    familia: Optional[str] = None
    sku_key: Optional[str] = None
    min_thread_count: Optional[int] = None
    max_thread_count: Optional[int] = None      # exclusivo
    margem_pct: float
    prioridade: int = 100                       # menor = ganha
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True
    notas: Optional[str] = None


class CondicaoPagamento(SQLModel, table=True):
    """Condição de pagamento e seu encargo financeiro — regra centralizada num lugar só."""
    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str = Field(index=True, unique=True)
    label: str
    encargo_pct: Optional[float] = None      # None = taxa ainda não confirmada
    encargo_confirmado: bool = True
    ordem: int = 0
    ativo: bool = True
    notas: Optional[str] = None


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
class BaseImportacao(SQLModel, table=True):
    """Snapshot das premissas vigentes numa importação de planilha (legado, preservado).

    É o modelo anterior às tabelas versionadas: guardava todas as premissas fiscais e
    comerciais em colunas de uma linha só. Continua existindo e continua sendo o que
    torna cada cotação histórica reproduzível — **nunca é apagado nem reescrito**.

    A Fase 0 acrescentou a ele a mesma vigência que as tabelas versionadas já têm
    (`valid_from`/`valid_to`/`ativo`/`fonte`) e a ponte `BasePremissaPonte`, que diz,
    campo a campo, onde cada premissa desta base vive hoje no mundo versionado.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    importado_em: datetime = Field(default_factory=datetime.utcnow)
    nome_arquivo: str
    observacoes: Optional[str] = None
    icms_pct: float
    pis_cofins_pct: float
    encargo_financeiro_pct: float
    comissao_tabela_json: str
    origem_uf: str = "SC"
    icms_por_estado_json: str = "{}"
    cenarios_fiscais_json: str = "[]"
    cambio_usd_brl: Optional[float] = None
    frete_usd_kg: Optional[float] = None
    outras_desp_usd_un: Optional[float] = None

    # --- vigência (Fase 0): metadado, não altera nenhum valor econômico da base ---
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None          # preenchido quando uma base mais nova entrou
    ativo: Optional[bool] = True             # só a base mais recente fica ativa
    fonte: Optional[str] = None


class BasePremissaPonte(SQLModel, table=True):
    """Ponte entre a `BaseImportacao` legada e as premissas versionadas (Fase 0).

    Uma linha por campo de premissa de cada base. Responde, sem tocar em nada:

    * qual era o valor legado daquela base;
    * onde essa premissa vive hoje (tabela e chave do mundo versionado);
    * quanto ela vale hoje, quando é um escalar comparável;
    * se legado e vigente divergem.

    É o que permite migrar premissa nas ondas seguintes sem perder a reprodutibilidade
    das cotações já emitidas: a cotação continua apontando para a base, e a base agora
    aponta para a premissa versionada equivalente.

    A ponte é **descritiva**. Nenhum cálculo lê esta tabela; ela não muda preço nenhum.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    base_importacao_id: int = Field(foreign_key="baseimportacao.id", index=True)
    campo_legado: str = Field(index=True)        # icms_pct, pis_cofins_pct, ...
    valor_legado_num: Optional[float] = None
    valor_legado_txt: Optional[str] = None
    premissa_tabela: Optional[str] = None        # premissa | condicaopagamento | estadofiscal...
    premissa_chave: Optional[str] = None         # chave/coluna dentro dessa tabela
    valor_vigente_num: Optional[float] = None    # o que o mundo versionado resolve hoje
    valor_vigente_txt: Optional[str] = None
    diverge: Optional[bool] = None               # None = não é escalar comparável
    observacao: Optional[str] = None
    origem: str = "FASE_0_PONTE"
    criado_em: datetime = Field(default_factory=datetime.utcnow)


class Produto(SQLModel, table=True):
    """Catálogo único — KTC, Daune, Decor Tricot e futuros, lado a lado."""
    id: Optional[int] = Field(default=None, primary_key=True)
    sku_key: str = Field(index=True, unique=True)
    categoria: Optional[str] = None
    nome: str                                    # nome de exibição (canônico, em português)
    nome_original: Optional[str] = None          # como veio da planilha/cotação, para rastreio
    especificacao: Optional[str] = None
    custo_unitario: Optional[float] = None      # CUSTO NET ANARA em R$ — entrada do motor comercial
    preco_base: Optional[float] = None
    base_importacao_id: Optional[int] = Field(default=None, foreign_key="baseimportacao.id")
    ativo: bool = True

    # --- fornecedor e método de custo ---
    fornecedor_id: Optional[int] = Field(default=None, foreign_key="fornecedor.id", index=True)
    cost_method: Optional[str] = Field(default=None, index=True)      # CostMethod
    custo_confianca: Optional[str] = Field(default=None, index=True)  # CostConfidence
    # Override de natureza fiscal do item. NULO = deriva do tipo do fornecedor.
    origem_fiscal: Optional[str] = Field(default=None, index=True)    # OrigemFiscal
    precisa_revisao: bool = False
    revisao_motivo: Optional[str] = None

    # --- rastreabilidade do custo de referência ---
    custo_ref_valor: Optional[float] = None
    custo_ref_moeda: Optional[str] = None
    custo_ref_data: Optional[date] = None
    custo_ref_documento: Optional[str] = None
    custo_ref_cliente: Optional[str] = None      # cliente do documento (ex.: HAMAN GLOBAL)
    custo_ref_tipo: Optional[str] = None         # EXW_QUOTED | SUPPLIER_COST | SELLING_REF | ...
    custo_ref_nota: Optional[str] = None

    # --- KTC: EXW calculado vs cotado ---
    exw_calculado_usd: Optional[float] = None
    exw_calculado_em: Optional[datetime] = None
    exw_cotado_usd: Optional[float] = None
    exw_cotado_data: Optional[date] = None
    exw_cotado_fonte: Optional[str] = None
    exw_cotado_cliente: Optional[str] = None
    exw_diferenca_usd: Optional[float] = None
    exw_diferenca_pct: Optional[float] = None

    # --- memória de nacionalização (interna) ---
    ncm: Optional[str] = None
    ii_aplicado: Optional[float] = None
    peso_kg: Optional[float] = None
    peso_fonte: Optional[str] = None
    peso_tipo: Optional[str] = None              # "REAL KTC" | "ESTIMADO"
    peso_data: Optional[date] = None
    peso_documento: Optional[str] = None
    preco_ktc_usd: Optional[float] = None
    frete_usd_un: Optional[float] = None
    custo_net_usd: Optional[float] = None
    cotacao_origem: Optional[str] = None

    # --- especificação estruturada (cálculo usa campos, não texto) ---
    familia: Optional[str] = Field(default=None, index=True)
    subcategoria: Optional[str] = None
    thread_count: Optional[int] = None
    weave: Optional[str] = None
    cotton_pct: Optional[float] = None
    poliester_pct: Optional[float] = None
    largura_cm: Optional[float] = None
    comprimento_cm: Optional[float] = None
    gsm: Optional[int] = None
    plain_or_stripe: Optional[str] = None
    yarn_type: Optional[str] = None
    construcao: Optional[str] = None
    fechamento: Optional[str] = None
    acabamento: Optional[str] = None
    cor: Optional[str] = None
    material_ref: Optional[str] = None           # material usado no cálculo industrial

    # --- comercial ---
    margem_padrao_pct: Optional[float] = None    # cache da regra resolvida (informativo)


class CustoReferencia(SQLModel, table=True):
    """Histórico de custos/preços observados por SKU — nada é sobrescrito silenciosamente."""
    id: Optional[int] = Field(default=None, primary_key=True)
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id", index=True)
    sku_key: Optional[str] = Field(default=None, index=True)
    fornecedor_id: Optional[int] = Field(default=None, foreign_key="fornecedor.id")
    tipo: str                                   # EXW_QUOTED | EXW_CALCULATED | SUPPLIER_COST | SELLING_REF
    valor: float
    moeda: str = "USD"
    data_ref: Optional[date] = None
    documento: Optional[str] = None
    cliente_documento: Optional[str] = None
    confianca: Optional[str] = None
    aplicado: bool = False                      # virou o custo vigente do produto?
    notas: Optional[str] = None
    criado_em: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Cotação
# ---------------------------------------------------------------------------
class Cotacao(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    numero: Optional[str] = Field(default=None, index=True)     # ANARA-2026-0001
    cliente_id: int = Field(foreign_key="cliente.id")
    vendedor: Optional[str] = None
    status: StatusCotacao = Field(default=StatusCotacao.rascunho)
    condicao_pagamento: str = Field(default="30")
    estado_destino: Optional[str] = None
    # `estado_origem` é LEGADO e representa origem logística/comercial. **Não é usado no
    # cálculo fiscal** desde a Onda 1 — origem logística não prova origem fiscal da NF.
    estado_origem: str = Field(default="Santa Catarina")
    # Origem fiscal da operação (UF). NULO = resolver pela hierarquia do pricing_service.
    uf_origem_fiscal: Optional[str] = None
    finalidade: Optional[str] = None            # override da finalidade do cliente
    contribuinte_icms: bool = Field(default=True)
    frete: Optional[str] = None
    observacoes: Optional[str] = None
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    validade_em: Optional[datetime] = None
    base_importacao_id: Optional[int] = Field(default=None, foreign_key="baseimportacao.id")
    pdf_gerado_em: Optional[datetime] = None
    # Arquivar tira da lista e dos totais sem destruir nada — é o caminho para cotação de
    # teste. Apagar de vez continua existindo, mas exige arquivar antes.
    arquivada_em: Optional[datetime] = None
    arquivada_motivo: Optional[str] = None

    # --- premissas do site de cotação ---
    validade_dias: int = 5
    prazo_entrega: Optional[str] = None
    contato_nome: Optional[str] = None
    departamento_contato: Optional[str] = None
    freight_type: str = Field(default=TipoFrete.cif.value)
    freight_valor: Optional[float] = None
    freight_incluso: bool = True
    freight_notas: Optional[str] = None
    termos_texto: Optional[str] = None          # snapshot dos termos no momento da emissão
    emitida_em: Optional[datetime] = None

    # --- aceite / pedido ---
    aceite_responsavel: Optional[str] = None
    aceite_cargo: Optional[str] = None
    aceite_departamento: Optional[str] = None
    aceite_em: Optional[datetime] = None
    local_entrega: Optional[str] = None
    endereco_entrega: Optional[str] = None
    observacoes_pedido: Optional[str] = None

    # --- snapshot fiscal/financeiro aplicado ---
    icms_aplicado: Optional[float] = None
    icms_regra: Optional[str] = None
    pis_cofins_pct: Optional[float] = None
    encargo_financeiro_pct: Optional[float] = None


class CotacaoItem(SQLModel, table=True):
    """Fotografia dos valores usados no momento em que o item foi salvo."""
    id: Optional[int] = Field(default=None, primary_key=True)
    cotacao_id: int = Field(foreign_key="cotacao.id")
    produto_id: Optional[int] = Field(default=None, foreign_key="produto.id")
    ordem: int = 0
    nome_produto: str
    especificacao: Optional[str] = None
    categoria: Optional[str] = None
    quantidade: float
    custo_unitario: float
    preco_base: float
    preco_negociado: float
    margem_liquida: float
    faturamento: float
    custo_total: float
    lucro: float
    diferenca_pct_vs_base: Optional[float] = None
    modo_edicao: str = "preco"                  # "preco" | "margem" | "markup"
    valor_editado: float = 0.0

    # --- fornecedor e margem ---
    fornecedor_id: Optional[int] = Field(default=None, foreign_key="fornecedor.id")
    fornecedor_nome: Optional[str] = None
    cost_method: Optional[str] = None
    margem_padrao_pct: Optional[float] = None   # o que a regra sugeriu
    margem_regra: Optional[str] = None          # qual regra resolveu
    comissao_pct: Optional[float] = None
    impostos: Optional[float] = None
    comissao_valor: Optional[float] = None
    markup_implicito: Optional[float] = None
    memoria_json: Optional[str] = None          # memória do preço congelada (snapshot completo)

    # --- snapshot fiscal POR ITEM (Onda 1) ---
    # Uma cotação pode ter KTC, Daune e Decor com três tratamentos fiscais diferentes. O que
    # decide o preço deste item mora aqui, congelado no momento em que o item foi salvo.
    origem_fiscal: Optional[str] = None         # OrigemFiscal — IMPORTADA | NACIONAL
    uf_origem_fiscal: Optional[str] = None
    uf_destino_fiscal: Optional[str] = None
    finalidade: Optional[str] = None            # Finalidade
    consumidor_final: Optional[bool] = None     # DERIVADO da finalidade
    icms_pct: Optional[float] = None            # TOTAL que reduz a receita da Anara
    aliquota_interestadual: Optional[float] = None   # parcela devida à origem
    aliquota_interna_destino: Optional[float] = None
    fcp_pct: Optional[float] = None
    icms_regra: Optional[str] = None
    icms_fonte: Optional[str] = None            # de qual tabela/linha veio a alíquota
    difal_pct: Optional[float] = None           # diferencial apurado, exista ou não ônus Anara
    difal_responsavel: Optional[str] = None     # ResponsavelDifal
    difal_valor: Optional[float] = None         # em R$, quando o remetente recolhe
    status_fiscal: Optional[str] = None         # StatusFiscal — separado da confiança do CUSTO
    motivo_fiscal: Optional[str] = None
    status_pagamento: Optional[str] = None      # StatusPagamento
    motivo_pagamento: Optional[str] = None
    encargo_pct: Optional[float] = None         # encargo financeiro efetivamente aplicado

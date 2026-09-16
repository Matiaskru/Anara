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
    """Estados da cotação.

    Os cinco primeiros são o workflow da Sessão 6. `rascunho` e `enviada` já existiam com
    exatamente esta semântica e foram **reaproveitados**, não duplicados — `enviada` sempre
    significou "documento emitido e mandado ao cliente".

    `fechada`, `pedido` e `perdida` são estados comerciais **legados** (WON/LOST). Continuam
    gravados como estão nas cotações históricas e ficam fora do workflow novo: dar sentido a
    eles é assunto da Sessão 7, e reescrevê-los agora seria fingir que cotações de 2026
    passaram por um fluxo de aprovação que não existia.
    """
    rascunho = "rascunho"                          # DRAFT — editável
    aguardando_aprovacao = "aguardando_aprovacao"  # PENDING_APPROVAL
    aprovada = "aprovada"                          # APPROVED — exceção autorizada
    emitida = "emitida"                            # ISSUED — congelada
    enviada = "enviada"                            # SENT — emitida e enviada
    cancelada = "cancelada"                        # CANCELLED
    # --- legados, preservados ---
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
    ktc_special_quoted = "KTC_SPECIAL_QUOTED"        # EXW cotado direto, cadastrado à mão
    ktc_estimated_from_quotes = "KTC_ESTIMATED_FROM_QUOTES"   # derivado de âncoras, com buffer
    daune_direct = "DAUNE_DIRECT"          # preço bruto Daune → créditos → CNET
    decor_direct = "DECOR_DIRECT"          # idem, Decor Tricot
    national_supplier = "NATIONAL_SUPPLIER"  # custo de fornecedor nacional, origem genérica
    a_cotar_ktc = "A_COTAR_KTC"            # sem base: precisa de cotação da KTC
    a_cotar_nacional = "A_COTAR_NACIONAL"  # sem base: precisa de cotação do fornecedor nacional
    manual = "MANUAL"                      # custo digitado à mão
    legacy_excel = "LEGACY_EXCEL"          # veio da planilha antiga, origem não rastreada


class CostConfidence(str, enum.Enum):
    """Rótulo LEGADO de confiança do custo. Preservado para não reescrever os 339 SKUs.

    O vocabulário canônico é o `StatusCusto` abaixo. Estes valores continuam onde estão até a
    reconciliação SKU a SKU dizer o que cada um vira — `legacy REVIEW_REQUIRED` **não** é o
    `REVIEW_REQUIRED` canônico, e converter por atalho derrubaria 36% do catálogo.
    """
    calculated = "CALCULATED"
    quoted = "QUOTED"
    estimated = "ESTIMATED"
    manual = "MANUAL"
    legacy = "LEGACY"
    review_required = "REVIEW_REQUIRED"


class StatusCusto(str, enum.Enum):
    """Os cinco estados canônicos. A diferença é **de onde veio o número**.

    * `CONFIRMADO`      — referência direta, atual e confiável;
    * `ESTIMADO`        — proxy: curva, análogo forte, interpolação documentada. Vai a proposta
                          com `confirmation_pending`, e não vira PO/WON sem confirmação humana;
    * `REVALIDAR`       — referência **direta** que envelheceu, venceu ou tem anomalia. Continua
                          utilizável, com alerta;
    * `A_COTAR`         — não existe base segura. Não forma preço automático;
    * `REVIEW_REQUIRED` — problema crítico de premissa, rastreabilidade ou configuração.
    """
    confirmado = "CONFIRMADO"
    estimado = "ESTIMADO"
    revalidar = "REVALIDAR"
    a_cotar = "A_COTAR"
    review_required = "REVIEW_REQUIRED"


# Estados que permitem formar preço automaticamente.
STATUS_QUE_PRECIFICAM = {StatusCusto.confirmado.value, StatusCusto.estimado.value,
                         StatusCusto.revalidar.value}


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


class SituacaoComponente(str, enum.Enum):
    """Aplicabilidade de um componente de frete. Ausência de regra NÃO é NAO_APLICA."""
    aplica = "APLICA"
    nao_aplica = "NAO_APLICA"
    desconhecido = "DESCONHECIDO"


class StatusFrete(str, enum.Enum):
    """Resultado da resolução do frete de um grupo logístico."""
    ok = "OK"
    estimado = "FRETE_ESTIMADO"                      # premissa logística explícita e datada
    a_cotar = "FRETE_A_COTAR"                        # fora de cobertura, sem tarifa
    review_required = "FRETE_REVIEW_REQUIRED"        # falta dado material (volume, base, tabela)
    icms_review_required = "FRETE_ICMS_REVIEW_REQUIRED"   # tratamento do ICMS não provado


class TipoComponenteFrete(str, enum.Enum):
    """Como o componente entra na conta — e é isto que decide onde ele entra no waterfall."""
    fixo = "FIXO"                    # R$ por embarque (paletização, agendamento, ...)
    por_peso = "POR_PESO"            # R$ por kg (pedágio)
    percentual_nf = "PERCENTUAL_NF"  # % sobre o valor da NF (ADV, GRIS, fiel depositário)
    percentual_frete = "PERCENTUAL_FRETE"   # % sobre o próprio frete (reentrega, devolução)
    por_hora = "POR_HORA"            # R$/hora excedente (TDE, TDC)


class EtapaOportunidade(str, enum.Enum):
    """Onde a VENDA aberta está — três etapas, desde 16/09/2026 (Fase 3B).

    Na interface a oportunidade chama-se **Venda**, e uma venda aberta está em
    `RASCUNHO`, `ENVIADO` ou `NEGOCIACAO`. Vendido e Perdido não são etapas: são o `status`
    (`GANHA`/`PERDIDA`), eixo separado.

    **Não é máquina de estados rígida** — diferente do workflow da cotação. A vendedora move
    a venda entre as três etapas nos dois sentidos; o que é obrigatório é **registrar**
    toda mudança em `OportunidadeEtapaHistorico`. As etapas da COTAÇÃO (aguardando
    aprovação, emitida, enviada) não entram aqui: são estados do documento, não do negócio.

    Os valores do funil anterior (`PROSPECCAO`, `CONTATO`, `QUALIFICACAO`, `COTACAO`,
    `DECISAO`) continuam aceitos **só para leitura** de registros antigos
    (`ETAPAS_LEGADAS`); nenhum registro novo nasce com eles.
    """
    rascunho = "RASCUNHO"
    enviado = "ENVIADO"
    negociacao = "NEGOCIACAO"


#: Etapas do funil anterior à Fase 3B. Não são oferecidas nem aceitas em registro novo.
ETAPAS_LEGADAS = frozenset({"PROSPECCAO", "CONTATO", "QUALIFICACAO", "COTACAO", "DECISAO"})


class StatusOportunidade(str, enum.Enum):
    """Resultado do negócio — eixo separado da etapa. Na UI: Vendido / Perdido."""
    aberta = "ABERTA"
    ganha = "GANHA"
    perdida = "PERDIDA"


class StatusPosVenda(str, enum.Enum):
    """Acompanhamento depois de VENDIDO, até o pagamento. V1: sem pagamento parcial.

    `ATRASADO` é um status marcado por quem tem alçada financeira — o sistema não chama um
    cliente de inadimplente por conta própria. Nulo em venda aberta ou perdida.
    """
    aguardando_entrega = "AGUARDANDO_ENTREGA"
    aguardando_pagamento = "AGUARDANDO_PAGAMENTO"
    pago = "PAGO"
    atrasado = "ATRASADO"


class OrigemOportunidade(str, enum.Enum):
    inbound = "INBOUND"
    outbound = "OUTBOUND"
    indicacao = "INDICACAO"
    evento = "EVENTO"
    parceria = "PARCERIA"
    carteira = "CARTEIRA"
    outro = "OUTRO"


class MotivoPerda(str, enum.Enum):
    preco = "PRECO"
    prazo = "PRAZO"
    concorrente = "CONCORRENTE"
    sem_retorno = "SEM_RETORNO"
    projeto_cancelado = "PROJETO_CANCELADO"
    produto_especificacao = "PRODUTO_ESPECIFICACAO"    # Fase 3B
    fora_de_escopo = "FORA_DE_ESCOPO"                  # anterior à Fase 3B; só leitura
    outro = "OUTRO"


class TipoAtividade(str, enum.Enum):
    ligacao = "LIGACAO"
    email = "EMAIL"
    reuniao = "REUNIAO"
    follow_up = "FOLLOW_UP"
    outro = "OUTRO"


class Papel(str, enum.Enum):
    """Papéis canônicos. Quem pode ver o motor econômico, e quem não pode.

    A fronteira que importa aqui não é "quem manda mais": é **quem pode ver custo, margem e
    lucro**. OWNER e ADMIN veem; os dois papéis de vendedor não veem — e isso vale na resposta
    da API, não só na tela.
    """
    owner = "OWNER"
    admin = "ADMIN"
    vendedor_interno = "VENDEDOR_INTERNO"
    vendedor_comissionado = "VENDEDOR_COMISSIONADO"


#: Papéis que enxergam o motor econômico interno. Qualquer papel novo é confidencial por
#: omissão: entrar nesta lista é ato deliberado, não consequência de existir.
PAPEIS_ECONOMICOS = frozenset({Papel.owner.value, Papel.admin.value})

#: Papéis com poder administrativo sobre premissas, custos, fiscal e importação.
PAPEIS_ADMINISTRATIVOS = frozenset({Papel.owner.value, Papel.admin.value})


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
    # Origem LOGÍSTICA — de onde a mercadoria embarca. Não se confunde com a origem fiscal:
    # uma é onde a carga sai, a outra é de onde a NF é emitida. NULO = desconhecida, e nesse
    # caso nenhuma tabela de frete resolve o grupo.
    origem_logistica_cidade: Optional[str] = None
    origem_logistica_uf: Optional[str] = None
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


class Transportadora(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str = Field(index=True, unique=True)
    nome: str
    cnpj: Optional[str] = None
    endereco: Optional[str] = None
    ativo: bool = True
    notas: Optional[str] = None


class TabelaFrete(SQLModel, table=True):
    """Uma tabela de frete de uma transportadora, com origem, vigência e semântica declarada.

    A semântica é persistida em vez de assumida: `tarifa_unidade` diz o que o número da faixa
    significa, `faixa_unidade` diz em que grandeza a faixa é medida, `pedagio_base` diz sobre
    qual peso o pedágio incide. Nada disso é deduzido no código.

    **A tabela só resolve o grupo cuja origem logística e transportadora batem com as dela.**
    Não existe "tabela padrão": origem incompatível não cai aqui, vai para `FRETE_A_COTAR`.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    transportadora_id: int = Field(foreign_key="transportadora.id", index=True)
    versao: int = 1
    origem_logistica_cidade: str                 # "Itajaí"
    origem_logistica_uf: str                     # "SC"
    origem_regiao: Optional[str] = None          # rótulo da região de origem na própria tabela
    documento_fonte: str
    data_fonte: Optional[date] = None
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None              # a TRANSAL 2026-02 vence em 31/12/2026
    ativo: bool = True

    # semântica declarada — nunca inferida
    tarifa_unidade: str = "BRL_POR_TONELADA"
    faixa_unidade: str = "KG"
    minimo_unidade: str = "BRL_POR_EMBARQUE"
    fator_cubagem_kg_m3: Optional[float] = None  # 300 na TRANSAL
    pedagio_base: str = "DESCONHECIDO"           # PESO_TAXADO | PESO_REAL | DESCONHECIDO

    # ICMS da prestação: APLICA/NAO_APLICA/DESCONHECIDO, com a alíquota quando conhecida
    icms_situacao: str = "DESCONHECIDO"
    icms_pct: Optional[float] = None
    icms_notas: Optional[str] = None
    notas: Optional[str] = None


class FaixaFrete(SQLModel, table=True):
    """Faixa de peso × região de destino: a tarifa e o frete mínimo daquela região."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tabela_id: int = Field(foreign_key="tabelafrete.id", index=True)
    regiao_destino: str = Field(index=True)
    peso_de: float = 0.0                          # na unidade declarada em `faixa_unidade`
    peso_ate: Optional[float] = None              # None = sem teto
    tarifa: Optional[float] = None                # None = região sem tarifa (Passo Fundo)
    frete_minimo: Optional[float] = None
    prazo: Optional[str] = None
    ativo: bool = True
    notas: Optional[str] = None


class CoberturaFrete(SQLModel, table=True):
    """Cidade atendida → região tarifária. Sem linha, o destino está fora de cobertura."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tabela_id: int = Field(foreign_key="tabelafrete.id", index=True)
    cidade: str = Field(index=True)
    uf: Optional[str] = Field(default=None, index=True)
    regiao_destino: str = Field(index=True)
    unidade: Optional[str] = None                 # matriz/filial que atende
    ativo: bool = True


class ComponenteFrete(SQLModel, table=True):
    """Componente do frete, com tipo, valor e **aplicabilidade explícita**.

    O tipo decide onde o componente entra no waterfall: `PERCENTUAL_NF` é rate variável sobre a
    receita e entra no denominador; `FIXO` e `POR_PESO` são custo fixo do embarque e entram no
    numerador. Tratar tudo como valor fixo calculado antes do preço seria congelar um
    percentual da receita sobre um preço preliminar.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tabela_id: int = Field(foreign_key="tabelafrete.id", index=True)
    codigo: str = Field(index=True)               # ADV | GRIS | PEDAGIO | FIEL_DEPOSITARIO | ...
    nome: str
    tipo: str                                     # TipoComponenteFrete
    valor: Optional[float] = None                 # % em fração, ou R$ conforme o tipo
    unidade: Optional[str] = None
    situacao: str = "DESCONHECIDO"                # SituacaoComponente
    automatico: bool = False                      # entra sem alguém pedir? (ADV sim; TDE não)
    fonte: Optional[str] = None
    regra: Optional[str] = None
    valid_from: date = Field(default_factory=date.today)
    valid_to: Optional[date] = None
    ativo: bool = True


class GrupoLogistico(SQLModel, table=True):
    """Um embarque dentro da cotação: uma origem logística, uma transportadora, um destino.

    Itens de origens diferentes **não** viram uma carga só. Cada grupo resolve o próprio frete,
    e o frete CIF da cotação é a soma dos grupos. A base dos componentes percentuais é o valor
    de mercadoria **do grupo**, não o total da cotação — senão o adicional seria cobrado
    tantas vezes quantos forem os grupos.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    cotacao_id: int = Field(foreign_key="cotacao.id", index=True)
    nome: Optional[str] = None
    origem_cidade: Optional[str] = None
    origem_uf: Optional[str] = None
    fornecedor_id: Optional[int] = Field(default=None, foreign_key="fornecedor.id")
    transportadora_id: Optional[int] = Field(default=None, foreign_key="transportadora.id")
    tabela_id: Optional[int] = Field(default=None, foreign_key="tabelafrete.id")
    destino_cidade: Optional[str] = None
    destino_uf: Optional[str] = None
    regiao_destino: Optional[str] = None

    peso_real_kg: Optional[float] = None
    volume_m3: Optional[float] = None
    peso_cubado_kg: Optional[float] = None
    peso_taxado_kg: Optional[float] = None
    peso_taxado_fonte: Optional[str] = None       # SKU_PACKING | SHIPMENT_VOLUME | ...
    valor_mercadoria: Optional[float] = None      # base dos componentes percentuais DO GRUPO

    frete_peso: Optional[float] = None
    cf_logistico: Optional[float] = None          # soma dos componentes fixos/por peso
    rv_logistico_pct: Optional[float] = None      # soma dos percentuais sobre a NF
    rv_logistico_valor: Optional[float] = None    # recomposto em R$ depois do preço
    frete_total: Optional[float] = None

    status: str = "FRETE_REVIEW_REQUIRED"         # StatusFrete
    motivo: Optional[str] = None
    memoria_json: Optional[str] = None
    criado_em: datetime = Field(default_factory=datetime.utcnow)


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
    # --- Política comercial de 16/09/2026 (Fase 3A) ---
    # A regra deixa de dizer só "qual é a margem-alvo" e passa a dizer a política inteira do
    # escopo: o PISO abaixo do qual a vendedora perde a autonomia, a comissão que FORMA o
    # preço recomendado, e se o preço é travado (Daune). NULO em todos = regra anterior à
    # política, que o sistema continua interpretando com a semântica antiga (comissão por
    # faixa de markup, autonomia de desconto zero) — nunca reescrita.
    piso_pct: Optional[float] = None            # piso de autonomia da vendedora
    comissao_formacao_pct: Optional[float] = None   # comissão usada no gross-up do recomendado
    preco_travado: bool = False                 # vendedora (e admin) não alteram o unitário
    margem_anterior_pct: Optional[float] = None # rastreabilidade: a margem que esta regra sucedeu
    politica: Optional[str] = None              # rótulo da política que criou a regra
    fonte: Optional[str] = None                 # "Decisão comercial ANARA — 16/09/2026"


class CondicaoPagamento(SQLModel, table=True):
    """Condição de pagamento e seu encargo financeiro — regra centralizada num lugar só.

    **Versionada por vigência desde a Sessão 5.** `codigo` deixou de ser único porque a mesma
    condição ("30/60") pode ter mais de uma linha: a que valeu até ontem e a que vale a partir
    de hoje. Quem resolve qual delas usar é a data, não a unicidade — e é isso que permite
    cadastrar hoje um encargo que só entra em vigor no ano que vem.

    O histórico não depende disto: cotação emitida guarda o `encargo_pct` no próprio item.
    A vigência serve para o cálculo NOVO.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str = Field(index=True)
    label: str
    encargo_pct: Optional[float] = None      # None = taxa ainda não confirmada
    encargo_confirmado: bool = True
    ordem: int = 0
    ativo: bool = True
    notas: Optional[str] = None
    # --- vigência (Sessão 5) ---
    valid_from: Optional[date] = None        # NULO nas 8 linhas herdadas = "sempre valeu"
    valid_to: Optional[date] = None
    versao: int = 1
    substitui_id: Optional[int] = Field(default=None, foreign_key="condicaopagamento.id")
    fonte: Optional[str] = None
    criado_em: Optional[datetime] = None
    criado_por: Optional[str] = None


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
    # Cache do status canônico da versão vigente de custo. A fonte da verdade é
    # `CustoReferencia.status_custo`; `custo_confianca` acima é o rótulo LEGADO e não se mistura.
    status_custo: Optional[str] = Field(default=None, index=True)


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

    # --- versionamento (Sessão 2) ---
    # Atualizar o custo de um SKU é criar uma VERSÃO NOVA e fechar a vigência da anterior. A
    # linha antiga fica no banco, auditável, com fonte e data. Duas consequências garantidas
    # pela arquitetura: mexer no SKU X não toca no SKU Y, e cotação emitida não muda, porque ela
    # guarda o próprio snapshot em `CotacaoItem.memoria_json` e não relê a referência.
    #
    # Nas 105 linhas herdadas estas colunas são NULAS: são observações do modelo antigo, e serão
    # classificadas na reconciliação — não convertidas por adivinhação.
    versao: Optional[int] = Field(default=None, index=True)   # 1, 2, 3... por SKU
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None             # preenchido quando uma versão nova entra
    vigente: Optional[bool] = Field(default=None, index=True)
    metodo_custo: Optional[str] = None          # CostMethod
    status_custo: Optional[str] = Field(default=None, index=True)   # StatusCusto
    confirmation_pending: Optional[bool] = None  # ESTIMADO não vira PO/WON até confirmar
    valor_bruto: Optional[float] = None         # preço do fornecedor, antes dos créditos
    cnet_brl: Optional[float] = None            # CUSTO NET resultante desta versão
    memoria_calculo: Optional[str] = None       # JSON: drivers e passos que produziram o CNET
    origem_registro: Optional[str] = None       # quem/o quê criou a versão
    substitui_versao: Optional[int] = None


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
    # `observacoes` é INTERNA (nunca vai ao PDF). O que o cliente lê é `observacao_cliente`
    # (Fase 3C) — separação formal, para que uma anotação da equipe nunca vire texto da
    # proposta por descuido.
    observacoes: Optional[str] = None
    observacao_cliente: Optional[str] = None
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

    # --- workflow comercial (Sessão 6) ---
    # Uma cotação emitida não se edita: cria-se uma revisão. `revisao` e `cotacao_origem_id`
    # guardam a genealogia; a numeração histórica (`numero`) não muda, para que ninguém
    # perca a referência que já mandou ao cliente.
    revisao: int = 1
    cotacao_origem_id: Optional[int] = Field(default=None, foreign_key="cotacao.id",
                                             index=True)
    fingerprint: Optional[str] = None            # do estado material, quando emitida
    issued_em: Optional[datetime] = None
    issued_por: Optional[str] = None
    sent_em: Optional[datetime] = None
    sent_por: Optional[str] = None
    cancelada_em: Optional[datetime] = None
    cancelada_por: Optional[str] = None
    cancelamento_motivo: Optional[str] = None
    premissas_mantidas_aprovadas: bool = False   # optou por manter premissa velha
    # --- CRM (Sessão 7) ---
    # Nulo nas 18 históricas, e assim fica: inventar oportunidade para cotação de 2026 seria
    # criar negócio que nunca existiu no funil.
    oportunidade_id: Optional[int] = Field(default=None, foreign_key="oportunidade.id",
                                           index=True)


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

    # --- frete comercial rateado (Sessão 3A) ---
    grupo_logistico_id: Optional[int] = Field(default=None, foreign_key="grupologistico.id")
    frete_cf_unitario: Optional[float] = None     # parcela fixa do frete rateada ao item
    frete_rv_pct: Optional[float] = None          # rate variável logístico aplicado
    frete_rv_valor: Optional[float] = None        # recomposto sobre o preço final
    frete_total_item: Optional[float] = None
    frete_status: Optional[str] = None
    frete_rateio_criterio: Optional[str] = None

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

    # --- pinning das premissas (Sessão 5 · correção) ---
    # O item já guardava os VALORES que formaram o preço, e isso protege o dinheiro. O que
    # faltava era a IDENTIDADE: qual versão, exatamente, produziu aquele número.
    #
    # Sem estes campos, responder "de onde veio este preço" dependia de perguntar ao
    # resolvedor qual versão estaria valendo naquela data — e uma versão cadastrada depois,
    # com vigência retroativa, mudaria a resposta. O preço continuaria certo e a genealogia,
    # não. Aqui a genealogia fica presa por ID, e nenhuma versão futura a reescreve.
    custo_referencia_id: Optional[int] = Field(default=None,
                                               foreign_key="custoreferencia.id", index=True)
    custo_referencia_versao: Optional[int] = None
    margem_regra_id: Optional[int] = Field(default=None, foreign_key="margemregra.id")
    condicao_pagamento_id: Optional[int] = Field(default=None,
                                                 foreign_key="condicaopagamento.id")
    aliquota_interestadual_id: Optional[int] = None
    premissas_pinadas: Optional[str] = None    # JSON: {chave: {"premissa_id", "valor"}}
    # --- confiança do custo, congelada no item (Sessão 6) ---
    # O workflow precisa saber COMO o custo foi obtido, não só quanto ele é: A_COTAR não
    # forma preço, ESTIMADO forma proposta mas não compromisso firme, e REVALIDAR pede
    # reconfirmação. Ler isso do produto na hora da emissão daria a resposta de hoje para
    # uma pergunta sobre ontem.
    status_custo_item: Optional[str] = None
    confirmation_pending: bool = False
    # O preço que o motor recomenda **para o cenário desta cotação** — não o `preco_base`,
    # que é a referência do catálogo e foi formada com outro destino fiscal, outra condição
    # de pagamento e talvez outro custo. Confundir os dois faria toda cotação interestadual
    # parecer um desconto, e o aprovador seria chamado para autorizar exceção inexistente.
    preco_recomendado: Optional[float] = None
    # --- Política comercial congelada no item (Fase 3A, 16/09/2026) ---
    # O item carrega a política que formou o preço dele, como carrega o fiscal e os pinos:
    # o piso de autonomia, a comissão de formação, se o preço é travado e QUAL política era.
    # NULO = item formado antes da política (ou por regra sem política): o workflow o avalia
    # com a semântica anterior e a detecção de premissa velha o aponta — nunca o reescreve.
    piso_margem_pct: Optional[float] = None
    comissao_formacao_pct: Optional[float] = None
    preco_travado: bool = False
    politica_comercial: Optional[str] = None


# ---------------------------------------------------------------------------
# Usuários e acesso (Sessão 4)
# ---------------------------------------------------------------------------
class Usuario(SQLModel, table=True):
    """Quem entra no sistema, e com qual papel.

    Substitui a senha compartilhada. Três decisões que valem registro:

    * **`senha_hash` nunca guarda a senha.** É um hash argon2id, com salt próprio embutido —
      duas contas com a mesma senha produzem hashes diferentes, e nenhum deles volta a ser a
      senha.
    * **`can_manage_users` é granular dentro de ADMIN** (decisão B do plano): o gerente
      administra usuários, o administrativo não. Não existe um quinto papel para isso.
    * **`sessao_versao` invalida sessão sem apagar o usuário.** Trocar a senha, desativar a
      conta ou forçar logout global incrementa o número, e todo cookie emitido antes deixa de
      valer — sem precisar de tabela de sessões.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    nome: str
    senha_hash: str
    papel: str = Field(default=Papel.vendedor_interno.value, index=True)
    ativo: bool = True
    can_manage_users: bool = False
    can_manage_economics: bool = True
    can_approve_quotes: bool = False
    sessao_versao: int = 1
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    ultimo_login_em: Optional[datetime] = None
    criado_por: Optional[str] = None

    @property
    def ve_economia(self) -> bool:
        """Pode ver custo, CNET, margem, lucro, markup e a memória do preço."""
        return self.ativo and self.papel in PAPEIS_ECONOMICOS

    @property
    def administra(self) -> bool:
        """Pode alterar premissa, custo, fiscal, frete e importar base."""
        return self.ativo and self.papel in PAPEIS_ADMINISTRATIVOS

    @property
    def gerencia_economia(self) -> bool:
        """Pode **versionar** premissa econômica: custo, câmbio, margem, encargo.

        Separada de `administra` de propósito (Sessão 5). Ver o custo e poder trocá-lo são
        coisas diferentes: um administrativo consulta a formação do preço o dia inteiro sem
        precisar da caneta que muda o custo de um SKU. `can_manage_users` **não** concede
        isto — gerir gente não é gerir número.

        OWNER sempre pode; ADMIN, conforme a flag, que vem ligada por não haver hoje um
        segundo administrador de quem separar.
        """
        if not self.ativo:
            return False
        if self.papel == Papel.owner.value:
            return True
        return self.papel == Papel.admin.value and bool(self.can_manage_economics)

    @property
    def aprova_cotacoes(self) -> bool:
        """Pode decidir sobre exceção comercial — desconto, margem, premissa velha.

        **Permissão própria, e isso é deliberado.** Administrar premissa
        (`can_manage_economics`) é manter o cadastro; aprovar cotação é autorizar abrir mão
        de receita numa venda específica. Quem versiona o câmbio não é, por isso, quem
        decide o desconto — e reaproveitar a outra flag apagaria essa diferença.

        Vem desligada: alçada se concede, não se herda. OWNER sempre pode.
        """
        if not self.ativo:
            return False
        if self.papel == Papel.owner.value:
            return True
        return self.papel == Papel.admin.value and bool(self.can_approve_quotes)

    @property
    def gerencia_usuarios(self) -> bool:
        # OWNER nunca perde a capacidade de administrar quem entra — senão um sistema com um
        # único OWNER e a flag desligada ficaria sem ninguém capaz de criar acesso.
        return self.ativo and (self.papel == Papel.owner.value or bool(self.can_manage_users))


class AuditLog(SQLModel, table=True):
    """Trilha de auditoria administrativa — quem mudou o quê, quando e por quê.

    Existe porque versionar o valor responde "qual era o número antes", mas não responde
    "quem decidiu trocar, e com base em quê". As duas perguntas são diferentes, e a segunda é
    a que aparece quando um preço é contestado meses depois.

    **Nunca guarda senha, hash, cookie, segredo nem payload confidencial inteiro.** Guarda o
    valor econômico antes e depois — que é o ponto — e o motivo declarado pelo ator.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    ocorrido_em: datetime = Field(default_factory=datetime.utcnow, index=True)
    ator_id: Optional[int] = Field(default=None, foreign_key="usuario.id", index=True)
    ator_email: Optional[str] = None
    ator_papel: Optional[str] = None
    acao: str = Field(index=True)             # CRIAR_VERSAO | ENCERRAR_VIGENCIA | IMPORTAR | ...
    entidade: str = Field(index=True)         # CustoReferencia | Premissa | MargemRegra | ...
    entidade_id: Optional[int] = Field(default=None, index=True)
    escopo: Optional[str] = None              # "SKU X" | "fornecedor Daune" | "global"
    versao_anterior: Optional[str] = None     # representação textual, não o objeto
    versao_nova: Optional[str] = None
    motivo: Optional[str] = None
    origem: Optional[str] = None              # "admin-ui" | "importacao" | "script"
    resultado: str = "OK"                     # OK | CONFLITO | RECUSADO | NO_OP
    correlacao: Optional[str] = Field(default=None, index=True)   # agrupa um lote/preview
    detalhe: Optional[str] = None             # JSON curto com o diff


class AprovacaoCotacao(SQLModel, table=True):
    """Decisão de aprovação — **append-only**. Nada aqui é editado nem apagado.

    A ideia central: uma aprovação **não aprova uma cotação, aprova uma configuração**.
    "A cotação 123 está aprovada" não responde com qual desconto, com qual quantidade nem
    para qual destino fiscal. Por isso cada decisão carrega o `fingerprint` do estado
    material que o aprovador viu; se qualquer campo material mudar, esta decisão passa a ser
    sobre outra coisa e é marcada `INVALIDADA` — não porque alguém a revogou, mas porque o
    objeto dela deixou de existir.

    Rejeitar **não** apaga nem encerra a cotação: ela volta a ser editável, o vendedor
    ajusta, e um pedido novo nasce com fingerprint novo.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    cotacao_id: int = Field(foreign_key="cotacao.id", index=True)
    revisao: int = 1
    fingerprint: str = Field(index=True)
    status: str = Field(default="PENDENTE", index=True)   # PENDENTE|APROVADA|REJEITADA|INVALIDADA
    motivos: Optional[str] = None                 # JSON: lista de motivos estruturados
    excecoes_json: Optional[str] = None           # o retrato da exceção, como foi vista
    resumo_json: Optional[str] = None             # totais comerciais no momento do pedido

    solicitante_id: Optional[int] = Field(default=None, foreign_key="usuario.id")
    solicitante_email: Optional[str] = None
    justificativa: Optional[str] = None           # obrigatória no pedido de exceção

    aprovador_id: Optional[int] = Field(default=None, foreign_key="usuario.id")
    aprovador_email: Optional[str] = None
    comentario: Optional[str] = None

    criado_em: datetime = Field(default_factory=datetime.utcnow, index=True)
    decidido_em: Optional[datetime] = None
    invalidado_em: Optional[datetime] = None
    invalidacao_motivo: Optional[str] = None


class SnapshotEmissao(SQLModel, table=True):
    """O documento emitido, congelado.

    Existe para que reconstruir uma proposta emitida **não dependa de nenhum lookup vivo**:
    nem do catálogo, nem das premissas, nem da tabela de frete, nem sequer da própria
    cotação. Tudo o que formou aquele documento está aqui, em JSON, no estado em que estava.

    `aprovacao_id` + `aprovacao_fingerprint` identificam a decisão exata usada — não um
    "aprovado = sim", que não diria *o quê* foi aprovado.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    cotacao_id: int = Field(foreign_key="cotacao.id", index=True)
    revisao: int = 1
    numero: Optional[str] = None
    fingerprint: str = Field(index=True)
    emitido_em: datetime = Field(default_factory=datetime.utcnow)
    emitido_por: Optional[str] = None
    aprovacao_id: Optional[int] = Field(default=None, foreign_key="aprovacaocotacao.id")
    aprovacao_fingerprint: Optional[str] = None
    cliente_json: Optional[str] = None
    itens_json: Optional[str] = None
    totais_json: Optional[str] = None
    fiscal_json: Optional[str] = None
    frete_json: Optional[str] = None
    premissas_json: Optional[str] = None
    memoria_json: Optional[str] = None            # interna; nunca vai ao PDF do cliente
    pdf_caminho: Optional[str] = None


# ---------------------------------------------------------------------------
# CRM (Sessão 7)
# ---------------------------------------------------------------------------
class Contato(SQLModel, table=True):
    """Pessoa dentro de uma organização.

    O `contato_nome` que já existia em `Cliente` e em `Cotacao` continua onde está — ele é o
    contato **daquele documento**, congelado. Este modelo é a agenda: várias pessoas por
    cliente, cada uma com seu papel.

    O e-mail **não** é único globalmente: duas fichas podem legitimamente compartilhar um
    endereço (`compras@`), e tornar isso único quebraria cadastro real por causa de uma
    regra que ninguém pediu.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    cliente_id: int = Field(foreign_key="cliente.id", index=True)
    nome: str
    cargo: Optional[str] = None
    email: Optional[str] = None
    telefone: Optional[str] = None
    observacao: Optional[str] = None
    principal: bool = False
    ativo: bool = True
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    criado_por: Optional[str] = None


class Oportunidade(SQLModel, table=True):
    """O negócio sendo perseguido — que **não** é a cotação.

    Uma oportunidade pode ter zero, uma ou várias cotações, e as revisões de uma proposta
    continuam sendo o mesmo negócio. Confundir os dois faria cada revisão virar um item a
    mais no funil, e o pipeline passaria a contar papel em vez de negócio.

    **`responsavel_id` é responsabilidade operacional, não controle de acesso.** Quem vê o
    quê continua sendo a política da Sessão 4; aqui só se registra quem toca o negócio, para
    que exista o filtro "minhas oportunidades" e para que a Sessão 8 consiga medir.

    Três valores, três significados diferentes, e persistir só o que é **fato**:
    `valor_estimado` é palpite manual de antes da cotação; o valor cotado é **derivado** da
    cotação mais recente e por isso não tem coluna; `valor_fechado` é snapshot do ganho.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    cliente_id: int = Field(foreign_key="cliente.id", index=True)
    titulo: str
    descricao: Optional[str] = None
    responsavel_id: Optional[int] = Field(default=None, foreign_key="usuario.id", index=True)
    etapa: str = Field(default=EtapaOportunidade.rascunho.value, index=True)
    status: str = Field(default=StatusOportunidade.aberta.value, index=True)
    origem: Optional[str] = None
    origem_detalhe: Optional[str] = None
    valor_estimado: Optional[float] = None
    data_prevista_fechamento: Optional[date] = Field(default=None, index=True)
    criado_em: datetime = Field(default_factory=datetime.utcnow, index=True)
    atualizado_em: Optional[datetime] = None
    criado_por: Optional[str] = None
    # --- fechamento ---
    won_em: Optional[datetime] = None
    won_por: Optional[str] = None
    cotacao_vencedora_id: Optional[int] = Field(default=None, foreign_key="cotacao.id")
    cotacao_vencedora_fingerprint: Optional[str] = None
    valor_fechado: Optional[float] = None
    lost_em: Optional[datetime] = None
    lost_por: Optional[str] = None
    motivo_perda: Optional[str] = None
    comentario_perda: Optional[str] = None
    # --- pós-venda (Fase 3B) — só existe depois de GANHA; nulo em aberta/perdida ---
    # VENDIDO (`valor_fechado`), FATURADO (`faturado_em` + documento) e PAGO (`pago_em`) são
    # três fatos diferentes e ficam em três lugares. V1 sem pagamento parcial.
    status_pos_venda: Optional[str] = Field(default=None, index=True)
    entrega_prevista_em: Optional[date] = None
    entregue_em: Optional[datetime] = None
    faturado_em: Optional[date] = None
    numero_documento_fiscal: Optional[str] = None
    pagamento_previsto_em: Optional[date] = None
    pago_em: Optional[datetime] = None
    observacao_pos_venda: Optional[str] = None


class AtualizacaoComercial(SQLModel, table=True):
    """"Registrar atualização": uma nota comercial da venda, **append-only** (Fase 3B).

    Não é atividade (que tem data e conclusão) nem edição de campo: é o que aconteceu, dito
    por quem viu. Não se edita nem se apaga — correção é outra atualização.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    autor_id: Optional[int] = Field(default=None, foreign_key="usuario.id")
    autor_email: Optional[str] = None
    texto: str
    criado_em: datetime = Field(default_factory=datetime.utcnow, index=True)


class OportunidadeEtapaHistorico(SQLModel, table=True):
    """Append-only: toda mudança de etapa, com quem e quando.

    A coluna `etapa` diz onde o negócio está; ela não diz há quanto tempo, nem por onde
    passou. Sem este registro, "tempo em etapa", "conversão" e "aging" — que a Sessão 8 vai
    precisar — seriam impossíveis de reconstruir depois.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    etapa_anterior: Optional[str] = None
    etapa_nova: str
    ator_id: Optional[int] = Field(default=None, foreign_key="usuario.id")
    ator_email: Optional[str] = None
    ocorrido_em: datetime = Field(default_factory=datetime.utcnow, index=True)
    observacao: Optional[str] = None


class AtividadeComercial(SQLModel, table=True):
    """Follow-up: a próxima ação combinada.

    "Atrasada" **não** é coluna: é `due_em < agora and concluida_em is None`, derivado na
    leitura. Persistir isso exigiria um job para mantê-lo verdadeiro, e um campo que só é
    verdadeiro enquanto alguém lembra de atualizá-lo é pior que não existir.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: Optional[int] = Field(default=None, foreign_key="oportunidade.id",
                                           index=True)
    cliente_id: Optional[int] = Field(default=None, foreign_key="cliente.id", index=True)
    contato_id: Optional[int] = Field(default=None, foreign_key="contato.id")
    responsavel_id: Optional[int] = Field(default=None, foreign_key="usuario.id", index=True)
    tipo: str = Field(default=TipoAtividade.follow_up.value)
    titulo: str
    observacao: Optional[str] = None
    due_em: Optional[datetime] = Field(default=None, index=True)
    concluida_em: Optional[datetime] = None
    concluida_por: Optional[str] = None
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    criado_por: Optional[str] = None

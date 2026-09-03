# IMPLEMENTATION_PLAN_ANARA

Plano da Fase 2 do SUPER PROMPT v2. Atualizado na **Sessão 0.1 (03/09/2026)** com os estados de
confiança canônicos, o gate de reconciliação do `REVIEW_REQUIRED` legado, a origem fiscal por
operação, o B-15, a re-extração da TRANSAL, a nova fonte Daune 280 g e a divisão da Onda 3.

**Nomenclatura oficial:** **Fase 0 — Fundação** (preparatória) e **Ondas de Implementação 1 a
8**, com a Onda 3 dividida em **3A** e **3B**.

## Estado

- **Fase 0 — Fundação: EXECUTADA e aceita.** Alterou `app/models.py` de forma aditiva, criou 2
  migrations, alterou o esquema do banco, criou 13 testes e dois commits locais. HEAD `165d75e`,
  árvore limpa, sem push.
- **Sessão 0.1: documental.** Nenhum código, banco, migration, teste ou baseline tocado.
- **Ondas 1 a 8: NÃO autorizadas.** Nenhuma foi iniciada.

`relatorios/baseline_fase0.json` é o **BASELINE IMUTÁVEL PRÉ-ONDA 1**. Não se regenera: é a
referência contra a qual cada onda é medida.

Companheiro deste documento: `AUDIT_ANARA_MASTER.md`.

---

## 1. Princípio de sequenciamento

1. **Primeiro o que muda número** — fiscal, custo, frete, arredondamento. Toda validação
   posterior depende de número estável.
2. **Depois o que a operação precisa para não depender de programador** — área administrativa
   atualizável (movida para P1 pela decisão A).
3. **Depois o que protege** — papéis, permissões, aprovação, bloqueios.
4. **Por último o que amplia** — CRM, pipeline, relatórios.

Antes de cada onda: baseline. Depois de cada onda: comparação e relatório do que mudou de número
e por qual regra. Nada é publicado sem esse relatório.

---

## 2. Fase 0 e ondas

```
FASE 0  Fundação: Alembic, baseline imutável, backup, restore, ponte BaseImportacao   ✔ EXECUTADA
Onda 1  P0 fiscal: por item, origem fiscal por operação, nacional 7/12%, finalidade,
        DIFAL, fim dos fallbacks fiscais E de condição de pagamento (B-15),
        gate de reconciliação do REVIEW_REQUIRED legado                        ⚠ risco alto
Onda 2  P0 custo: Daune (incl. linha 280 g), fronha, bottom sheet, métodos,
        estados canônicos de confiança, roupão, KTC special
Onda 3A P0 frete e logística: TRANSAL, cobertura, volume, cubagem, peso taxado,
        grupos logísticos, rateio, CIF no waterfall                            ⚠ bloqueada
Onda 3B P0 precisão: Decimal, política de arredondamento, reconciliação ao centavo
─────── a partir daqui o número está estável ───────
Onda 4  P1 segurança e multiusuário: papéis, 403, Postgres, audit log, concorrência
Onda 5  P1 área administrativa atualizável: upload, diff, SIMULAR → PUBLICAR
Onda 6  P1 aprovação, blockers, status e revisões
Onda 7  P1 CRM e UX: organizações, unidades, contatos, deals, pipeline, atividades
Onda 8  P2 relatórios, saúde do sistema, notificações, preparação IA/Pipedrive
```

**Por que 3A e 3B foram separadas.** A Onda 3 original juntava três mudanças econômicas
independentes: frete no waterfall, rateio por grupo logístico e conversão para `Decimal`. Com as
três publicadas juntas, uma diferença de R$ 0,03 num item não teria autoria — e a rastreabilidade
é exatamente o que a Fase 0 existiu para garantir. Cada uma passa a ter **baseline de entrada
próprio, testes próprios, relatório de diferenças próprio e checkpoint humano próprio**.

---

## FASE 0 — FUNDAÇÃO

| Entrega | Detalhe |
|---|---|
| `AUDIT_ANARA_MASTER.md` | Feito |
| Baseline ampliado | 339 SKUs × 6 cenários fiscais × 5 condições de pagamento + 45 itens + totais por cotação. Hoje: 241 SKUs × 9 cenários |
| Alembic | Esquema atual como revisão inicial; `migrations.py` vira ponte e depois é aposentado |
| Ponte `BaseImportacao` (decisão H) | Migration explícita ligando o modelo legado ao versionado. Bases antigas preservadas, snapshots intocados |
| Backup e restore | Script, procedimento testado, política escrita |

**Arquivos novos:** `alembic/`, `scripts/baseline_regressao_v2.py`, `BACKUP.md`
**Migrations:** 1 (revisão inicial) + 1 (ponte)
**Testes:** 4 (baseline reprodutível, ponte não altera snapshot, restore, migration idempotente)
**Risco:** baixo
**Resultado:** qualquer alteração posterior é mensurável e reversível

> **EXECUTADA e aceita em revisão externa.** Entregou: Git local com commit inicial seguro,
> baseline imutável, Alembic com o esquema atual como revisão inicial (fiel ao banco vivo, com as
> divergências B-13 anotadas), migration-ponte da `BaseImportacao`, script de backup com restore
> ensaiado e 13 testes. Nenhuma cotação, item ou snapshot mudou de número.

---

## ONDA 1 — P0 fiscal ⚠

### 1.1. Fiscal por item (B-02)
`CotacaoItem` ganha `origem_fiscal`, `icms_pct`, `icms_regra`, `difal_pct`, `difal_responsavel`.
A cotação guarda o cenário; o total é a soma dos itens.

### 1.2. Origem fiscal e alíquota interestadual (B-01, B-14, Q-04, Q-11, Q-15)

**A origem fiscal é atributo da operação/item/NF, não do fornecedor.** Precisa ser resolvida por
item, auditável, snapshotada, sobrescrevível com autorização registrada e **separada da origem
logística**. Origem logística Itajaí-SC **não prova** origem fiscal da NF.

`Produto.origem_fiscal` ∈ {IMPORTADA, NACIONAL}, sugerida pelo fornecedor e **sobrescrevível por
item**. Tabela `AliquotaInterestadual(uf_origem, uf_destino, origem_fiscal, aliquota, vigencia,
fonte)` — tabela de dados versionada, **nunca um `if` no código**:

- NACIONAL, SP → AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, PA, PB, PE, PI, RN, RO, RR, SE, TO → **7%**
- NACIONAL, SP → MG, PR, RJ, RS, SC → **12%**
- mesmo estado → alíquota interna do destino (SP: 18%)
- IMPORTADA em operação interestadual → **4% quando a regra legal aplicável à mercadoria
  importada efetivamente se aplicar**

**Os 4% não são constante universal.** A resolução tem de admitir vigência, origem,
produto/NCM, exceção e **override autorizado e rastreado** — com prioridade acima da regra por par
de UF, do mesmo modo que `NcmRegra` já faz com o I.I. Sem isso, um item nacionalizado ou de lista
CAMEX exigiria mudança de código.

**Nada de hardcode de origem:** nem `KTC = SP`, nem `KTC = SC`, nem "SP" como constante eterna de
Daune e Decor. Se a NF real tiver outra origem, vale a origem real.

**Se a origem fiscal não puder ser determinada com confiança: `REVIEW_REQUIRED`.** Nunca default.

**Gate obrigatório antes de qualquer migração massiva de origem:** relatório SKU → fornecedor →
origem atual → fonte → origem proposta → evidência. **Sem evidência, `REVIEW_REQUIRED`.**

### 1.3. Finalidade e DIFAL (B-03, Q-05, Q-08)
`finalidade` ∈ {REVENDA, INDUSTRIALIZACAO, USO_CONSUMO, ATIVO_IMOBILIZADO} em cliente, unidade e
cotação. Default hoteleiro `USO_CONSUMO`. **Consumidor final é derivado** de USO_CONSUMO ou
ATIVO_IMOBILIZADO — não é valor do enum.

| Contribuinte | Finalidade | Alíquota destacada | DIFAL | Entra na margem da Anara? |
|---|---|---|---|---|
| SIM | REVENDA / INDUSTRIALIZACAO | interestadual (4% ou 7/12%) | não se aplica | — |
| SIM | USO_CONSUMO / ATIVO | interestadual | **destinatário recolhe** | **NÃO** |
| NÃO | (derivado consumidor final) | interestadual | **remetente recolhe** | **SIM**, carga final do destino |

Exceções por UF ficam configuráveis e versionadas, mas o default acima existe no código.

### 1.4. Fim do fallback silencioso (B-06)
Cenário irresolvível → `REVIEW_REQUIRED`; custo ausente → `A_COTAR`; **PDF final bloqueado** com
o motivo escrito na tela. Cobre os cinco `return fallback` de `fiscal_rules` (linhas 33, 49, 55,
60 e 90), o I.I. = 0 e o frete internacional = 0 de `nationalization`.

### 1.5. Fim da interpolação de condição de pagamento (B-15)
Condição não cadastrada **não** devolve número: exige condição cadastrada ou **override explícito
autorizado**. Some a régua de contagem de barras dos casos 3 e 4 de `payment_terms`, e some
também o código morto `pricing_engine.encargo_financeiro_efetivo`, que a reimplementa. O teste
`test_condicao_nao_cadastrada_usa_regua_antiga_com_aviso` é reescrito para exigir bloqueio.

### 1.6. Gate de reconciliação do `REVIEW_REQUIRED` legado
**`legacy REVIEW_REQUIRED` ≠ `REVIEW_REQUIRED` canônico.** Antes de o estado virar blocker real de
operação, produzir relatório por SKU: SKU · fornecedor · família · método de custo · confidence
legado · `precisa_revisao` · motivo · custo atual · fonte · data · freshness · rastreabilidade ·
**status canônico recomendado** (CONFIRMADO / ESTIMADO / REVALIDAR / A_COTAR / REVIEW_REQUIRED) ·
justificativa.

Alcance: **121 SKUs** com `REVIEW_REQUIRED` legado e **156** com `precisa_revisao`. Sem o gate,
ativar o blocker torna 36% do catálogo não-cotável de uma vez. **Nenhuma conversão automática.**

**Migrations:** `cotacaoitem` +6 (inclui `origem_fiscal` por item) · `produto` +1 ·
`cliente`/`unidade`/`cotacao` +1 · tabela `aliquota_interestadual` (≈54 linhas seed)
**Arquivos:** `fiscal_rules.py` (reescrito), `payment_terms.py`, `pricing_engine.py` (remoção do
código morto), `pricing_service.py`, `models.py`, `routers/cotacoes.py`, `seeds.py`
**Testes novos:** ~40 — SP→SP; importada interestadual com override; nacional 7% (21 UFs
amostradas) e 12% (5 UFs); matriz contribuinte × 4 finalidades; DIFAL por responsável; cotação
mista com três alíquotas no mesmo documento; cenário irresolvível bloqueia PDF; **origem fiscal
indeterminada vira `REVIEW_REQUIRED`**; **condição de pagamento desconhecida bloqueia em vez de
interpolar**
**Testes reescritos:** 3 (`test_interestadual_contribuinte_e_4`,
`test_cenario_desconhecido_cai_no_fallback_com_aviso`,
`test_condicao_nao_cadastrada_usa_regua_antiga_com_aviso`) + o do baseline, por desenho
**Risco de regressão:** **ALTO**
**Critério de aceite:** matriz completa testada · relatório SKU a SKU do que mudou contra o
baseline imutável, com a regra que explica cada diferença · gate de reconciliação entregue antes
de qualquer blocker ser ativado
**Condição de stop:** terminar e **parar**. Não encadear a Onda 2
**Resultado:** cotação mista fiscalmente correta item a item; venda Daune/Decor interestadual a
contribuinte sai de 4% para 7%/12%, o que **aumenta** o preço desses itens

---

## ONDA 2 — P0 custo

### 2.1. Modelo Daune (B-09) com gate de validação (decisão I)
```
ICMS_credit       = gross × 12%
base_pc           = gross − ICMS_credit
PIS_COFINS_credit = base_pc × 9,25%
custo_NET         = gross − ICMS_credit − PIS_COFINS_credit      (fator ≈ 0,7986)
```
**Escopo real, corrigido na Sessão 0.1:** dos 52 SKUs Daune, **32 têm custo** para converter
(`NATIONAL_SUPPLIER`/`QUOTED`) e **20 não têm** (edredons em `A_COTAR`/`REVIEW_REQUIRED`). A frase
"52 SKUs terão custo reduzido em ~20%" estava errada.

**Complicação:** os 32 custeados vieram de `tabela de preços Daune Anara-Trousseau-Fio a
Fio.xlsx`, documento **diferente** do que será importado. A migração é **troca de fonte com
casamento SKU a SKU entre duas planilhas**, não aplicação de um fator.

**A fórmula parte do preço bruto da fonte correspondente.** Nunca `custo_atual × 0,7986` — o custo
atual pode não corresponder ao bruto da fonte nova.

**Relatório obrigatório antes de qualquer migração**, por SKU: SKU · família · construção ·
medida · custo atual · **preço bruto da fonte** · fonte/data · **match técnico** · crédito ICMS ·
base PIS/COFINS · crédito PIS/COFINS · **CUSTO NET novo** · diferença · **status canônico
proposto** · justificativa.

Item ambíguo **não recebe transformação automática** — vai para `REVIEW_REQUIRED`.

Importar `Linha Hotelaria - Daune - 12.08.26.xlsx`, aba `Preços Daune 12.08.26` (28 itens) como
vigente; a aba `Preços Daune 27.07.26` **do mesmo arquivo** como versão anterior; documento-fonte
e data guardados.

### 2.1.1. Reconciliação Daune — Edredom Poliéster 280 g (NOVA FONTE, Sessão 0.1)

Fonte registrada no audit §2.2.1: **DAUNE · EDREDOM · 100% fibras de poliéster · 280 g**, nove
dimensões com **preço bruto do fornecedor** (R$ 427,50 a R$ 679,72), informada pelo responsável do
projeto em 03/09/2026.

**Os nove valores são preço BRUTO DO FORNECEDOR. Não são preço final Anara.** Cada um percorre a
cadeia: bruto → créditos (ICMS 12% e PIS/COFINS 9,25%) → **CUSTO NET** → fiscal da venda →
condição financeira → comissão pela faixa → frete comercial quando aplicável → demais custos
atribuíveis → **margem-alvo Daune de 14%** → preço recomendado.

Procedimento na execução:

| | Passo |
|---|---|
| **A** | Verificar se o catálogo já tem SKUs 280 g compatíveis. **Levantamento da Sessão 0.1: não tem nenhum** — busca por `280 g`, `280g` e `gsm = 280` devolve zero |
| **B** | Havendo SKU, casar por **fornecedor + família + composição + gramatura + dimensão + construção** — campos estruturados, nunca nome |
| **C** | Não havendo equivalente, **propor criação de SKU 280 g específico** |
| **D** | **Nunca sobrescrever um SKU 180 g ou 250 g só porque a medida coincide.** Três das nove dimensões (190×260, 285×265, 290×260) coincidem com SKUs existentes que são 180 g ou 250 g — é exatamente aí que o erro aconteceria |
| **E** | Registrar fonte, data, preço bruto, composição, gramatura, dimensão, match, status e validade/freshness quando houver |
| **F** | Só então aplicar a fórmula Daune para o CUSTO NET |
| **G** | Só então precificar com a margem Anara de 14% |
| **H** | Preservar a rastreabilidade inteira: fonte → custo bruto → créditos → CNET → pricing → preço final |

**Pré-requisito técnico — B-16:** `gsm` está NULO em 28 dos 31 SKUs de `Duvet Insert`; a gramatura
só existe dentro da string do nome. **Preencher `gsm` a partir da fonte antes de qualquer match** —
sem isso, o casamento por campos estruturados que a regra exige é impossível nesta família.

**Status na Onda 2:** um SKU pode ser `CONFIRMADO` quando houver **match técnico exato e
rastreabilidade suficiente** — candidato a `DAUNE_DIRECT`. Não classificar a família inteira como
confirmada sem verificar o catálogo. Havendo necessidade de derivação, **não** promover a
CONFIRMADO; classificar conforme os cinco estados canônicos.

**O que esta fonte NÃO resolve:** edredom poliéster **180 g e 250 g continuam `A_COTAR`** quando
não houver fonte específica. **Proibido** `180 g = extrapolação de 280 g` e `250 g = extrapolação
de 280 g` sem autorização posterior. A série 280 g serve, no futuro, para consistência entre
tamanhos **da própria linha**, detecção de outliers **da própria linha** e uma curva 280 g **se
explicitamente aprovada** — nunca para outra gramatura.

**Pluma × poliéster permanecem separados.** Não misturar curvas.

**Protetores Daune:** sem match automático entre "Manta 120 grs impermeável" e construções
"matelassado com alça"/"com slip" sem evidência técnica. Sem match seguro: `A_COTAR`.

### 2.2. Fronha calculável (B-08) — regra já validada
§18 como está: W_cut/L_cut por número de abas, CMT 0,50 (standard) e 0,75 (com abas),
festonê +US$ 0,10 antes da 2ª qualidade. Bordado extraordinário → `KTC_SPECIAL_QUOTED`.

### 2.3. Bottom sheet sem elástico (B-08)
Mesma engine do Flat/Top. Fitted/elástico permanece `KTC_SPECIAL_QUOTED` ou `A_COTAR_KTC`.

### 2.4. Métodos de custo (B-12) e os cinco estados canônicos de confiança
Enum do §13 com mapeamento do legado. **Os cinco estados** — CONFIRMADO, ESTIMADO, **REVALIDAR**,
A_COTAR, REVIEW_REQUIRED — implementados com semáforo, conforme o audit §1.0.

- `confirmation_pending` em ESTIMADO (usado pela decisão C na Onda 6);
- **REVALIDAR** liga-se ao `frescor` já existente (FRESH / AGING / STALE / UNKNOWN), que hoje só
  alimenta relatório: referência **direta** que envelheceu, venceu ou tem anomalia continua
  utilizável **com alerta**, e exige reconfirmação antes de compromisso firme;
- **ESTIMADO nunca é promovido a CONFIRMADO em silêncio** — a promoção é ato humano registrado.

Situação de partida: 147 SKUs FRESH · 4 AGING · 67 STALE · 121 sem data de referência.

### 2.5. Cadastro manual `KTC_SPECIAL_QUOTED` (decisão D, C-08)
Formulário administrativo: produto/SKU, descrição/construção, medida, EXW USD, peso, NCM,
fonte/documento, data, validade, observações. Uma **única service API interna** cria a
referência — o formulário chama hoje, a IA poderá chamar no futuro, sempre com aprovação humana.

### 2.5.1. Produtos sem método fechado
**Não inventar engine nem fornecedor para fechar lacuna documental.** Com evidência documental
clara, classificar; sem ela, o item fica OPEN ou `A_COTAR` conforme a natureza. Nenhuma fórmula
fictícia. Aplica-se hoje a **Bed Runner (17 SKUs)** e **Cushion Cover (1 SKU)**, que não tinham
destino declarado até a Sessão 0.1.

### 2.6. Roupões (decisão E, B-10, Q-02)
NCM **6208.91.00** (100% algodão) e **6208.92.00** (sintéticas/artificiais). 6309 proibido.
I.I. 3,5% como override de família versionado, com precedência sobre lookup de NCM e imune a
troca de NCM. Política de estimativa: mesma construção/GSM/style/collar/size ou curva forte;
sem cruzar Terry/Waffle/Velour/Kimono/Shawl; referência recente; **buffer de 5%** nos anchors
aprovados; confirmação KTC antes do PO; sem base forte → `A_COTAR_KTC`.

**Migrations:** `produto` +6 (origem_fiscal já veio na Onda 1, `metodo_custo`, `custo_bruto`,
`credito_icms_pct`, `credito_pis_cofins_pct`, `confirmation_pending`) ·
tabela `referencia_custo_fornecedor`
**Testes novos:** ~36 — fator Daune aplicado ao **bruto da fonte**; 5 backtests de fronha;
festonê; bottom = flat; fitted bloqueado; precedência do I.I. de roupão; troca de NCM não remove
override; buffer de 5%; special quoted manual gera custo NET; ambíguo vai para revisão; **os
cinco estados canônicos, incluindo REVALIDAR**; **280 g não sobrescreve SKU de 180 g ou 250 g com
a mesma medida**; **gsm preenchido antes do match**
**Risco:** ALTO para Daune (**32 SKUs com custo**, não 52), baixo no resto
**Critério de aceite:** relatório de validação Daune aprovado **antes** da migração · nenhum SKU
de outra gramatura tocado pela fonte 280 g · teste do I.I. de roupão criado **antes** da troca de
NCM · rastreabilidade completa da fonte ao preço
**Resultado:** 39 fronhas calculáveis; custo Daune correto nos 32 com custo; roupões com NCM
correto; linha 280 g cadastrada com rastreabilidade

---

## ONDA 3A — P0 frete e logística ⚠ **BLOQUEADA**

> **Não iniciar** antes de C-NEW-01 (ICMS do frete), C-NEW-02 (aplicabilidade do GRIS) e
> C-NEW-06 (fiel depositário) serem resolvidas, e antes de conhecidas as origens logísticas
> efetivas de Daune e Decor.

### 3.1. Tabela TRANSAL (Q-01)
Entidades: `Transportadora` · `TabelaFrete` (origem, vigência, `icms_incluso`, `icms_pct`,
documento-fonte) · `FaixaFrete` (região, faixa de peso, R$/ton, mínimo) ·
`CoberturaFrete` (cidade, UF → região) · `AdicionalFrete` (TDE/TDC R$ 272/h após 2 h, +50% fora
do horário comercial; agendamento, paletização, reentrega).

**Dados a importar — contagem corrigida na Sessão 0.1:** **10 regiões de destino, das quais 9
com tarifa preenchida**; **~238 cidades** em 9 unidades (matriz Morro da Fumaça + 8 filiais).
A contagem anterior ("9 regiões, 8 com tarifa") cadastraria **uma região tarifada a menos**.

Passo Fundo-RS consta **sem tarifa** e **sem cidades atribuídas** → `FRETE_A_COTAR` (C-NEW-03).
Cidade fora da cobertura → `FRETE_A_COTAR`, **sem aproximar por cidade ou região vizinha**.
Origem **Itajaí-SC** — declarada pela própria tabela, editável e versionada por perfil tarifário.
São Paulo capital → filial Guarulhos.

**Adicionais a modelar, cada um com aplicabilidade própria:** TDE e TDC (2 h de limite, R$ 272/h
excedente, +50% fora do horário comercial) · **fiel depositário 0,5% da NF** · paletização
R$ 91,00/pallet PBR · sábados, domingos e feriados 30% do frete com mínimo de R$ 1.431,00 ·
agendamento por veículo (VUC/3-4/Toco R$ 1.000,00 · Truck R$ 1.431,00 · Carreta R$ 2.144,00) ·
reentrega 50% · devolução 100%.

**ICMS (C-NEW-01, REABERTO):** três evidências não se reconciliam — o texto da tabela
(*"ICMS CONFORME LEGISLAÇÃO"*), a informação posterior de que estaria incluso, e o exemplo da
própria tabela que executa gross-up de 12%. **Não escolher.** Os campos `icms_incluso`/`icms_pct`
ficam no modelo; o valor deles é decisão pendente e **bloqueia a implementação definitiva**.

**Vigência (C-NEW-07):** a tabela vale até **31/12/2026**, com cláusulas de revisão por
combustível e por volumetria. A `TabelaFrete` precisa de vigência: **tabela vencida não continua
sendo usada em silêncio** — vencida e sem substituta válida → `FRETE_REVIEW_REQUIRED` ou
`FRETE_A_COTAR`, conforme a modelagem definir.

### 3.2. Motor de frete
```
peso_cubado  = volume_m³ × 300          ← confirmado pela tabela: "MERCADORIA VOLUMOSA 300KG/M3"
peso_taxado  = max(peso_real, peso_cubado)
frete_peso   = max(tarifa_ton × peso_taxado / 1000, frete_minimo)
ADV          = 0,20% × valor da NF      (NF × 0,002)     aplicabilidade: confirmada
GRIS         = 0,10% × valor da NF      (NF × 0,001)     aplicabilidade: OPEN (C-NEW-02)
fiel_dep.    = 0,50% × valor da NF      (NF × 0,005)     aplicabilidade: OPEN (C-NEW-06)
pedagio      = 0,0536 × peso_taxado
frete_total  = frete_peso + componentes aplicáveis + pedágio + adicionais
               (+ tratamento de ICMS ainda indefinido — C-NEW-01)
```

> **Não fixar `RV = 0,30%` nem `RV = 0,80%`.** O rate variável é a soma dos componentes
> percentuais **aplicáveis** sobre a receita, e quais se aplicam é decisão pendente. Modelar cada
> componente como linha com aplicabilidade própria, não como constante somada.

### 3.3. Peso taxado — hierarquia de resolução (decisão de 03/09, C-NEW-04)

Não é preciso preencher volume nos 339 SKUs para o sistema entrar no ar.

| Prioridade | Fonte | Código |
|---|---|---|
| 1 | Volume unitário/embalado confiável no SKU ou na referência logística | `SKU_PACKING` |
| 2 | Volume total informado para o grupo logístico/shipment da cotação | `SHIPMENT_VOLUME` |
| 3 | Peso taxado confirmado por Admin (transportadora/operação já informou) | `CARRIER_CONFIRMED_TAXABLE_WEIGHT` |
| — | Override administrativo pontual | `ADMIN_OVERRIDE` |
| nenhuma | — | **`FRETE_REVIEW_REQUIRED`** |

Campos: `Produto.volume_m3_unitario`, `volume_fonte`, `volume_data`; volume por shipment;
peso taxado confirmado por cotação. **Nunca** assumir que `peso_real > peso_cubado`. Todo
override guarda **usuário, data/hora, valor e observação/fonte**. A fonte usada fica registrada
no item.

Para a vendedora: apenas **"Frete aguardando validação"**.

### 3.4. Grupos logísticos (decisão G, C-10)
`GrupoLogistico` por origem dentro da cotação: KTC → Itajaí-SC; Daune → origem própria;
Decor → origem própria; especiais → origem da referência. Frete calculado **por grupo** e somado.
O vendedor vê um frete total; o admin vê a composição.

### 3.5. CIF no waterfall (B-04)
```
modo MARKUP:  Preço = [CNET × (1 + E) + CF] / [1 − ICMS − PIS/COFINS − Encargo − Comissão(E) − RV]
modo MARGEM:  Preço = [CNET + CF]           / [1 − ICMS − PIS/COFINS − Encargo − Comissão(E) − RV − M]
```
CF = frete-peso + pedágio + adicionais, rateados por item. RV = ADV + GRIS = 0,30% como rate
variável sobre a receita — resolve a circularidade em forma fechada, sem iteração. Rateio pela
participação de peso real ou cubado, conforme o dominante; nunca igual por unidade.
Reconciliação: soma dos rateios = frete total.

### 3.6. Decimal e arredondamento (B-07)
`Decimal` nos motores financeiros; política central; preço comercial em 2 casas;
**lucro, margem e comissão recalculados sobre o preço arredondado**; totais reconciliam ao
centavo com a soma das linhas.

**Migrations:** 6 tabelas de frete · `produto` +3 (volume) · `cotacao` +4 ·
`cotacaoitem` +3 (frete rateado, RV) · `grupo_logistico`
**Testes novos:** ~38 — peso vs cubado; faixas ≤7.000 e >7.000 nas **9 regiões com tarifa**;
mínimo só sobre frete-peso; cada componente percentual com sua aplicabilidade; pedágio; TDE/TDC e
os demais adicionais; SP capital → Guarulhos; **Passo Fundo → `FRETE_A_COTAR`**; cidade fora de
cobertura → `FRETE_A_COTAR`; **tabela vencida não é usada em silêncio**; hierarquia de peso taxado
nas 4 fontes; sem nenhuma fonte → revisão; override registra usuário/data/valor/fonte; rateio soma
exata; múltiplos grupos somam; CIF fecha a margem alvo; FOB não altera
**Risco:** **ALTO** — três questões em aberto sobre componentes que entram na fórmula
**Critério de aceite:** C-NEW-01, C-NEW-02 e C-NEW-06 resolvidas com evidência anexada ·
soma dos rateios idêntica ao frete total · relatório de diferenças contra o baseline
**Condição de stop:** não iniciar com questão de composição em aberto
**Resultado:** CIF deixa de comer margem

---

## ONDA 3B — P0 precisão e arredondamento

Separada da 3A para que as diferenças tenham autoria. Uma mudança que altera **todos** os valores
em ±0,01 não pode ser publicada junto com outra que altera **só** as cotações CIF.

### 3B.1. Decimal e política central (B-07)
`Decimal` nos motores financeiros; política central de arredondamento; preço comercial em 2 casas;
**lucro, margem e comissão recalculados sobre o preço arredondado**; totais reconciliam ao centavo
com a soma das linhas.

**Migrations:** nenhuma estrutural — conversão de tipo e política
**Testes:** os testes de valor escritos em `float` são reescritos com tolerância explícita
**Baseline de entrada próprio.** **Relatório de diferenças próprio.** **Checkpoint humano próprio.**
**Risco:** MÉDIO — alcance total, magnitude mínima
**Critério de aceite:** toda diferença explicada por arredondamento, nenhuma por regra ·
reconciliação centavo a centavo entre linhas e total
**Resultado:** preço exibido = preço usado

---

## ONDA 4 — P1 segurança e multiusuário

| Entrega | Detalhe |
|---|---|
| Usuários e papéis | `Usuario`(email único, hash argon2/bcrypt, papel, ativo, `can_manage_users`). OWNER, ADMIN, VENDEDOR_INTERNO, VENDEDOR_COMISSIONADO |
| Permissão granular (decisão B) | `can_manage_users` dentro de ADMIN: gerente sim, administrativo não. Sem quinta área visual |
| Autorização no backend | Dependency por rota; URL/API sem permissão → **403** |
| Visibilidade | Vendedor não recebe custo/EXW/margem/markup/premissas **na resposta da API**. Comissionado vê só a própria carteira |
| Sessão, CSRF, rate limit | Sessão com expiração, token CSRF, proteção de login |
| PostgreSQL | Driver e migrations compatíveis; SQLite mantido para dev/teste |
| Concorrência | Optimistic locking (`versao`) em cotação, item e publicação de premissa |
| Audit log | `AuditLog(usuario, timestamp, entidade, campo, valor_antigo, valor_novo, motivo, versao)` |

**Migrations:** `usuario`, `audit_log`, `versao` em 3 tabelas
**Testes novos:** ~32
**Risco:** MÉDIO
**Resultado:** o sistema pode sair da máquina local

---

## ONDA 5 — P1 área administrativa atualizável (decisão A) ← era P2

A operação não pode depender de programador para atualizar premissa normal do negócio.

| Entrega | Detalhe |
|---|---|
| Produtos & Custos | Tabela com filtros por fornecedor, família, status, método, freshness, data. Por SKU: referência vigente, fonte, método, status, histórico, cotações em rascunho afetadas |
| Edição e massa | Edição individual, atualização em massa, importação de arquivo |
| Diff antes de publicar | unchanged / increased / decreased / new / missing / suspicious. SKU que sumir de uma importação → **inativo/superseded, nunca apagado** |
| Premissas editáveis | FX, materiais, CMT, shrinkage/waste/2ª qualidade/margem KTC, Daune, Decor, frete internacional e nacional, NCM, I.I., fiscal, PIS/COFINS, margens, comissões, pagamentos, validade, pesos/GSM/volume, freshness |
| Fluxo | **RASCUNHO → SIMULAR IMPACTO → PUBLICAR**, com SKUs afetados, rascunhos impactados, variação média, maiores variações e warnings |
| Publicação | Nova versão, usuário, data, audit trail. **Snapshots antigos intocados** |

**Migrations:** `publicacao_premissa`, `importacao_arquivo`
**Testes novos:** ~22 — simulação não altera dado; publicação cria versão; snapshot antigo
inalterado; SKU sumido vira inativo; diff classifica corretamente; outlier bloqueia publicação
**Risco:** MÉDIO
**Resultado:** o Matias e o administrativo atualizam o negócio sem programador

---

## ONDA 6 — P1 aprovação, blockers, status e revisões

| Entrega | Detalhe |
|---|---|
| Preço recomendado no cenário da cotação | Resolve B-05 |
| Aprovação | Qualquer preço abaixo do recomendado exige aprovação, **mesmo que a margem final continue boa**. Preço acima é livre |
| Invalidação | Alteração posterior invalida a aprovação |
| Central | Cotação, cliente, vendedor, recomendado, solicitado, margem alvo, margem resultante, impacto de lucro, itens causadores, aprovar/rejeitar com carimbo |
| Blockers de PDF | A_COTAR, fiscal irresolvido, CIF sem frete, `FRETE_REVIEW_REQUIRED`. **A_COMBINAR não bloqueia** (§31A) |
| Blocker de PO/WON (decisão C) | ESTIMADO com `confirmation_pending=true` **pode** ir a PDF e ao cliente, mas **não** permite registrar pedido/PO nem marcar WON até um Admin confirmar |
| Status | DRAFT, PENDING_APPROVAL, READY, SENT, SUPERSEDED. BLOCKED e EXPIRED **derivados** |
| Revisões | ANARA-YYYY-NNNN-R1, R2…; cotação principal do deal; histórico preservado |

**Testes novos:** ~26
**Risco:** BAIXO
**Resultado:** desconto deixa de ser decisão isolada do vendedor; estimado não vira pedido firme

---

## ONDA 7 — P1 CRM e UX

`Organizacao`, `Unidade`, `Contato`, `Deal`, `Atividade`. Cotação aponta para **unidade**.
Pipeline Kanban com os 6 estágios do §40; WON/LOST como status de fechamento; LOST com motivo
obrigatório e lista editável. Card com cliente/unidade, nome curto, valor, vendedor, próxima
atividade, cotação atual e alertas; ordenação atrasada → hoje → sem atividade → futura. Rotting
por estágio, editável, alerta que não bloqueia. Cotação em 3 passos com autosave. Vendedor vê
recomendado, negociado, comissão, total, alertas e aprovação — **não** vê custo, margem interna,
markup nem memória fiscal, e **não edita margem alvo nem markup**. Admin abre o drawer "Economia
da operação".

**Testes novos:** ~24 · **Risco:** baixo no cálculo, alto em volume · **Resultado:** vira o
sistema comercial

---

## ONDA 8 — P2

Relatórios (§51), saúde do sistema (§50), notificações internas (§57C), outliers (§38),
interfaces preparadas para IA (§56) e mapeamento para Pipedrive (§57), sem implementá-los.

---

## 3. Caso Nanai (Q-07)

Não é golden master. Depois das Ondas 1 a 3: preservar o arquivo original, recalcular de forma
independente com a Base Mestra vigente, criar **`NANAI_MASTER_V2`** como golden regression
oficial e documentar item a item cada diferença contra o orçamento histórico, com a regra que a
explica.

---

## 4. Impacto esperado nos números

| Mudança | Alcance | Direção | Ordem de grandeza |
|---|---|---|---|
| Custo Daune com créditos | **32 SKUs com custo** (não 52) | **cai** | −20,14% no custo (fator 0,7986) |
| Linha Daune poliéster 280 g | 0 SKUs hoje; até 9 dimensões novas | **novo** | preço bruto R$ 427,50 a R$ 679,72, ainda a converter em CNET e preço |
| ICMS nacional interestadual 7%/12% em vez de 4% | Daune e Decor interestadual a contribuinte | **sobe** | +3 a +8 p.p. |
| DIFAL de contribuinte em uso/consumo deixa de pesar na Anara | vendas a contribuinte | neutro/positivo | corrige atribuição |
| Fronha passa a ser calculada | 39 SKUs | varia | depende do cotado atual |
| CIF na rentabilidade | toda cotação CIF | **sobe** | frete sai da margem |
| ICMS do frete | toda cotação CIF | **INDEFINIDO** | C-NEW-01 reaberto: pode ser 0 ou +13,6% sobre o frete |
| Fiel depositário 0,5% | toda cotação CIF, se aplicável | **INDEFINIDO** | C-NEW-06: muda o rate variável |
| Condição de pagamento desconhecida | hoje interpolada | **corrige** | B-15: passa a bloquear em vez de estimar |
| Decimal | todos | ±0,01 | centavos |

**Não muda:** cotação já emitida. Cláusula 9 do Definition of Done, já garantida hoje.

---

## 5. Volume estimado

| | Antes | Agora |
|---|---|---|
| Estrutura | 7 ondas | **Fase 0 (executada) + 8 ondas, com a 3 dividida em 3A e 3B** |
| Testes novos | ~150 | **~225** |
| Migrations | ~20 | **~29** |
| Arquivos novos | ~25 | **~34** |
| Arquivos alterados | ~35 | **~42** |

Crescimento vindo de: grupos logísticos, volume/cubagem, TDE/TDC, cadastro special quoted,
área administrativa como onda própria e a matriz fiscal de finalidade.

---

## 6. Riscos

| Risco | Prob. | Mudança | Mitigação |
|---|---|---|---|
| Onda 1 muda preço de item nacional interestadual | Alta | = | Emitidas são imutáveis; relatório por SKU antes de publicar |
| Volume ausente na largada | **Média** | **↓ mitigado** | Hierarquia de 4 fontes de peso taxado; só cai em `FRETE_REVIEW_REQUIRED` se nenhuma existir |
| **ICMS do frete (C-NEW-01)** | Alta | **REABERTO** | Três evidências não reconciliadas; a que sustentava o "resolvido" não está no repositório. **Bloqueia a Onda 3A** |
| **Fiel depositário 0,5% (C-NEW-06)** | Média | **NOVO** | Componente percentual sobre a NF fora do plano até agora. **Bloqueia a fórmula do rate variável** |
| **Tabela TRANSAL vence em 31/12/2026 (C-NEW-07)** | Certa | **NOVO** | Exige vigência no modelo; vencida sem substituta → bloqueio, nunca uso silencioso |
| **Seed com uma região a menos** | Alta se o texto antigo for seguido | **NOVO** | São 10 regiões / 9 tarifadas, não 9/8 |
| GRIS (C-NEW-02) | Média | **REABERTO** | O valor 0,10% é fato; a aplicabilidade universal não |
| **Ativar `REVIEW_REQUIRED` canônico sem reconciliar o legado** | Alta | **NOVO** | 121 SKUs viram não-cotáveis de uma vez. Mitigação: gate obrigatório na Onda 1 |
| **Match da fonte 280 g sobrescrever SKU de outra gramatura** | Média | **NOVO** | 3 das 9 dimensões coincidem com SKUs de 180 g/250 g. Mitigação: regra D da §2.1.1 e B-16 |
| **Cobertura só Sul/Sudeste (C-NEW-05)** | Média | **NOVO** | Fora da cobertura → `FRETE_A_COTAR`; venda FOB segue normal |
| Tabela de frete não chegar | Média | **ELIMINADO** | Recebida em 03/09 |
| Daune cair 20% e já ter havido venda no preço antigo | Média | = | Custo cai, margem sobe; efeito positivo, mas comunicar ao comercial |
| Área administrativa atrasar o go-live | Média | **NOVO** | Onda 5 é pré-go-live por decisão A; escopo fechado no §35/§54 |
| CRM inflar prazo | Alta | ↓ | CRM é a Onda 7, depois de tudo que mexe em número |
| Postgres quebrar dado | Baixa | = | Alembic + backup + validação + rollback |
| Decimal quebrar teste antigo | Alta | = | Esperado; testes reescritos com tolerância explícita. **Mitigado pela separação 3A/3B** |
| Misturar mudanças numéricas e perder autoria | Média | **↓ mitigado** | Onda 3 dividida em 3A e 3B, cada uma com baseline, relatório e checkpoint próprios |
| Testes cristalizarem legado | Média | = | Lista de reconciliação na seção 6 do audit |

---

## 7. Definition of Done (§63) — mapa por onda

| # | Critério | Onda |
|---|---|---|
| 1-2 | AUDIT + PLAN | 0 (feito) |
| 3 | P0 resolvidos ou documentados | 1-3 |
| 4 | P1 de produção | 4-7 |
| 5 | Pricing reconcilia | 1-3 |
| 6-8 | Regressão, cobertura, casos reais | todas + Nanai V2 |
| 9 | Histórico intacto | 0 |
| 10-11 | Vendedor sem custo; papéis no backend | 4 |
| 12 | A_COTAR bloqueia PDF | 1, 6 |
| 13 | Aprovação e invalidação | 6 |
| 14 | CIF/FOB na rentabilidade | 3 |
| 15 | Pipeline e atividades | 7 |
| 16 | Audit trail | 4 |
| 17-18 | Local + produção documentada | 4 |
| 19 | Legados eliminados | 1-2 |
| 20 | Suite passa | todas |
| 21 | Markup/comissão sobre Custo NET | 3 |
| 22 | I.I. 3,5% roupão preservado | 2 |
| 23 | Duplicate revalida | 6 |
| 24 | Campos comerciais snapshotted | 6 |
| 25 | Preço exibido = preço usado | 3 |
| 26 | Backup/restore/concorrência | 0, 4 |
| — | Área administrativa atualizável | **5** (novo, por decisão A) |

---

## 8. Bloqueios abertos

**Situação após a Sessão 0.1:**

| Etapa | Bloqueada? | Por quê |
|---|---|---|
| **Onda 1** | **Não** — pode ser autorizada | A origem fiscal deixou de ser pergunta: virou regra (atributo da operação, sem evidência → `REVIEW_REQUIRED`). O escopo incorpora B-14, B-15 e o gate de reconciliação |
| **Onda 2** | Não | A fonte Daune 280 g está registrada com regras de match e proibições. REVALIDAR está definido |
| **Onda 3A** | **SIM** | C-NEW-01 (ICMS do frete), C-NEW-02 (GRIS) e C-NEW-06 (fiel depositário) em aberto — os três alteram a fórmula. Falta também a origem logística efetiva de Daune e Decor |
| **Onda 3B** | Não | Depende só da 3A ter terminado |

C-NEW-04 (volume) segue resolvido conceitualmente pela hierarquia de 4 fontes. C-NEW-05 continua
sendo limite operacional, não bloqueio: cobertura SC, PR, SP e RS; fora disso `FRETE_A_COTAR`.

---

## 9. Estado de autorização

| Etapa | Situação |
|---|---|
| Auditoria (Fase 1 do prompt) | **Concluída** |
| Plano (Fase 2 do prompt) | **Concluído** |
| **Fase 0 — Fundação** | **EXECUTADA e aceita em revisão externa** |
| **Sessão 0.1 — fechamento documental** | **Concluída** — documental, sem código |
| Ondas 1 a 8 | **Não autorizadas.** Nenhuma iniciada |

Regra do projeto: **uma etapa por autorização**. Ao terminar a Fase 0, parar e reportar os 10
itens exigidos antes de qualquer início da Onda 1.

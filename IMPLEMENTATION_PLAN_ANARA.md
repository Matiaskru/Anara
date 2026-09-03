# IMPLEMENTATION_PLAN_ANARA

Plano da Fase 2 do SUPER PROMPT v2. Atualizado em 03/09/2026 com as decisões do usuário, a
tabela da transportadora, as correções A–I e as resoluções de ICMS do frete, GRIS e volume.

**Nomenclatura oficial:** **Fase 0 — Fundação** (preparatória) e **Ondas de Implementação 1 a
8**. São 1 fase + 8 ondas.

**Nenhuma linha de código foi alterada. Nenhuma migration criada. Aguardando autorização.**

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
FASE 0  Fundação: auditoria, Alembic, baseline ampliado, backup, restore, ponte BaseImportacao
Onda 1  P0 fiscal: por item, importado × nacional, finalidade, DIFAL, fim do fallback   ⚠ risco alto
Onda 2  P0 custo: Daune, fronha, bottom sheet, métodos, roupão, KTC special, robes
Onda 3  P0 frete e precisão: TRANSAL, peso taxado hierárquico, grupos logísticos, CIF, Decimal
─────── a partir daqui o número está estável ───────
Onda 4  P1 segurança e multiusuário: papéis, 403, Postgres, audit log, concorrência
Onda 5  P1 área administrativa atualizável: upload, diff, SIMULAR → PUBLICAR              ← movida de P2
Onda 6  P1 aprovação, blockers, status e revisões
Onda 7  P1 CRM e UX: organizações, unidades, contatos, deals, pipeline, atividades
Onda 8  P2 relatórios, saúde do sistema, notificações, preparação IA/Pipedrive
```

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

> **Única etapa autorizada até segunda ordem.** Ao terminar a Fase 0, parar e reportar.

---

## ONDA 1 — P0 fiscal ⚠

### 1.1. Fiscal por item (B-02)
`CotacaoItem` ganha `origem_fiscal`, `icms_pct`, `icms_regra`, `difal_pct`, `difal_responsavel`.
A cotação guarda o cenário; o total é a soma dos itens.

### 1.2. Origem fiscal e alíquota interestadual (B-01, Q-04)
`Produto.origem_fiscal` ∈ {IMPORTADA, NACIONAL}, derivada do fornecedor e sobrescrevível.
Tabela `AliquotaInterestadual(uf_origem, uf_destino, origem_fiscal, aliquota, vigencia, fonte)`:

- IMPORTADA interestadual → **4%**
- NACIONAL, SP → AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, PA, PB, PE, PI, RN, RO, RR, SE, TO → **7%**
- NACIONAL, SP → MG, PR, RJ, RS, SC → **12%**
- mesmo estado → alíquota interna do destino (SP: 18%)

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
o motivo escrito na tela.

**Migrations:** `cotacaoitem` +5 · `produto` +1 · `cliente`/`unidade`/`cotacao` +1 ·
tabela `aliquota_interestadual` (≈54 linhas seed)
**Arquivos:** `fiscal_rules.py` (reescrito), `pricing_service.py`, `models.py`,
`routers/cotacoes.py`, `seeds.py`
**Testes novos:** ~34 — SP→SP; importada interestadual; nacional 7% (21 UFs amostradas) e 12%
(5 UFs); matriz contribuinte × 4 finalidades; DIFAL por responsável; cotação mista com três
alíquotas no mesmo documento; cenário irresolvível bloqueia PDF
**Risco de regressão:** **ALTO**
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
**Antes de aplicar aos 52 SKUs**, gerar relatório com: SKU · custo atual · preço bruto da fonte ·
crédito ICMS · base sem ICMS · crédito PIS/COFINS · custo NET novo · fonte/documento ·
diferença %. Só migra automaticamente o que casar com confiança; ambíguo vai para
`REVIEW_REQUIRED`, **sem transformação por aproximação**.

Importar `Linha Hotelaria - Daune - 12.08.26.xlsx` (28 itens) como vigente; 27.07.26 como versão
anterior; documento-fonte e data guardados.

### 2.2. Fronha calculável (B-08) — regra já validada
§18 como está: W_cut/L_cut por número de abas, CMT 0,50 (standard) e 0,75 (com abas),
festonê +US$ 0,10 antes da 2ª qualidade. Bordado extraordinário → `KTC_SPECIAL_QUOTED`.

### 2.3. Bottom sheet sem elástico (B-08)
Mesma engine do Flat/Top. Fitted/elástico permanece `KTC_SPECIAL_QUOTED` ou `A_COTAR_KTC`.

### 2.4. Métodos de custo (B-12) e status de confiança
Enum do §13 com mapeamento do legado. Status CONFIRMADO / ESTIMADO / A_COTAR com semáforo.
`confirmation_pending` em ESTIMADO (usado pela decisão C na Onda 6).

### 2.5. Cadastro manual `KTC_SPECIAL_QUOTED` (decisão D, C-08)
Formulário administrativo: produto/SKU, descrição/construção, medida, EXW USD, peso, NCM,
fonte/documento, data, validade, observações. Uma **única service API interna** cria a
referência — o formulário chama hoje, a IA poderá chamar no futuro, sempre com aprovação humana.

### 2.6. Roupões (decisão E, B-10, Q-02)
NCM **6208.91.00** (100% algodão) e **6208.92.00** (sintéticas/artificiais). 6309 proibido.
I.I. 3,5% como override de família versionado, com precedência sobre lookup de NCM e imune a
troca de NCM. Política de estimativa: mesma construção/GSM/style/collar/size ou curva forte;
sem cruzar Terry/Waffle/Velour/Kimono/Shawl; referência recente; **buffer de 5%** nos anchors
aprovados; confirmação KTC antes do PO; sem base forte → `A_COTAR_KTC`.

**Migrations:** `produto` +6 (origem_fiscal já veio na Onda 1, `metodo_custo`, `custo_bruto`,
`credito_icms_pct`, `credito_pis_cofins_pct`, `confirmation_pending`) ·
tabela `referencia_custo_fornecedor`
**Testes novos:** ~30 — fator Daune; 5 backtests de fronha; festonê; bottom = flat; fitted
bloqueado; precedência do I.I. de roupão; troca de NCM não remove override; buffer de 5%;
special quoted manual gera custo NET; ambíguo vai para revisão
**Risco:** ALTO para Daune (52 SKUs), baixo no resto
**Resultado:** 39 fronhas calculáveis; custo Daune correto; 28 itens Daune atualizados; roupões
com NCM correto

---

## ONDA 3 — P0 frete e precisão ⚠

### 3.1. Tabela TRANSAL (Q-01)
Entidades: `Transportadora` · `TabelaFrete` (origem, vigência, `icms_incluso`, `icms_pct`,
documento-fonte) · `FaixaFrete` (região, faixa de peso, R$/ton, mínimo) ·
`CoberturaFrete` (cidade, UF → região) · `AdicionalFrete` (TDE/TDC R$ 272/h após 2 h, +50% fora
do horário comercial; agendamento, paletização, reentrega).

Dados a importar: **9 regiões/filiais cadastradas, das quais 8 com tarifa preenchida**; ~200
cidades. Passo Fundo-RS consta **sem tarifa** → região e cidades dependentes ficam
`FRETE_A_COTAR` (C-NEW-03). Origem **Itajaí-SC**, editável e versionada, por perfil tarifário.
São Paulo capital → filial Guarulhos.

**ICMS (C-NEW-01, resolvido):** para a tabela TRANSAL atual `icms_incluso = true`. Sem gross-up,
sem divisão por 0,88. Os campos `icms_incluso`/`icms_pct` ficam no modelo para tabelas futuras.

### 3.2. Motor de frete
```
peso_cubado  = volume_m³ × 300
peso_taxado  = max(peso_real, peso_cubado)
frete_peso   = max(tarifa_ton × peso_taxado / 1000, frete_minimo)
ADV          = 0,20% × valor da NF        (NF × 0,002)
GRIS         = 0,10% × valor da NF        (NF × 0,001)  — os dois se aplicam (C-NEW-02)
pedagio      = 0,0536 × peso_taxado
frete_total  = frete_peso + ADV + GRIS + pedagio + adicionais   (ICMS já incluso na TRANSAL)
```

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

**Migrations:** 5 tabelas de frete · `produto` +3 (volume) · `cotacao` +4 ·
`cotacaoitem` +3 (frete rateado, RV) · `grupo_logistico`
**Testes novos:** ~38 — peso vs cubado; faixas ≤7.000 e >7.000 nas 8 regiões com tarifa; mínimo só sobre
frete-peso; ADV/GRIS; pedágio; TDE/TDC; SP capital → Guarulhos; Passo Fundo bloqueia; cidade fora
de cobertura bloqueia; **hierarquia de peso taxado nas 4 fontes**; sem nenhuma fonte → revisão;
override registra usuário/data/valor/fonte; ICMS não é somado duas vezes; ADV e GRIS somados;
rateio soma exata; múltiplos grupos somam; CIF fecha a margem alvo; FOB não altera; centavos e
reconciliação
**Risco:** MÉDIO — C-NEW-01, C-NEW-02 e C-NEW-04 resolvidos
**Resultado:** CIF deixa de comer margem

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
| Custo Daune com créditos | 52 SKUs | **cai** | −20% no custo |
| ICMS nacional interestadual 7%/12% em vez de 4% | Daune e Decor interestadual a contribuinte | **sobe** | +3 a +8 p.p. |
| DIFAL de contribuinte em uso/consumo deixa de pesar na Anara | vendas a contribuinte | neutro/positivo | corrige atribuição |
| Fronha passa a ser calculada | 39 SKUs | varia | depende do cotado atual |
| CIF na rentabilidade | toda cotação CIF | **sobe** | frete sai da margem |
| ICMS do frete | — | **sem efeito** | `icms_incluso = true`: nenhum gross-up (C-NEW-01 resolvido) |
| Decimal | todos | ±0,01 | centavos |

**Não muda:** cotação já emitida. Cláusula 9 do Definition of Done, já garantida hoje.

---

## 5. Volume estimado

| | Antes | Agora |
|---|---|---|
| Estrutura | 7 ondas | **Fase 0 + 8 ondas** (área administrativa virou onda própria) |
| Testes novos | ~150 | **~210** |
| Migrations | ~20 | **~28** |
| Arquivos novos | ~25 | **~34** |
| Arquivos alterados | ~35 | **~40** |

Crescimento vindo de: grupos logísticos, volume/cubagem, TDE/TDC, cadastro special quoted,
área administrativa como onda própria e a matriz fiscal de finalidade.

---

## 6. Riscos

| Risco | Prob. | Mudança | Mitigação |
|---|---|---|---|
| Onda 1 muda preço de item nacional interestadual | Alta | = | Emitidas são imutáveis; relatório por SKU antes de publicar |
| Volume ausente na largada | **Média** | **↓ mitigado** | Hierarquia de 4 fontes de peso taxado; só cai em `FRETE_REVIEW_REQUIRED` se nenhuma existir |
| ICMS do frete | — | **ELIMINADO** | `icms_incluso = true` confirmado por escrito pela transportadora |
| GRIS | — | **ELIMINADO** | ADV 0,2% e GRIS 0,1%, ambos aplicáveis |
| **Cobertura só Sul/Sudeste (C-NEW-05)** | Média | **NOVO** | Fora da cobertura → `FRETE_A_COTAR`; venda FOB segue normal |
| Tabela de frete não chegar | Média | **ELIMINADO** | Recebida em 03/09 |
| Daune cair 20% e já ter havido venda no preço antigo | Média | = | Custo cai, margem sobe; efeito positivo, mas comunicar ao comercial |
| Área administrativa atrasar o go-live | Média | **NOVO** | Onda 5 é pré-go-live por decisão A; escopo fechado no §35/§54 |
| CRM inflar prazo | Alta | ↓ | CRM é a Onda 7, depois de tudo que mexe em número |
| Postgres quebrar dado | Baixa | = | Alembic + backup + validação + rollback |
| Decimal quebrar teste antigo | Alta | = | Esperado; testes reescritos com tolerância explícita |
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

**Nenhum bloqueio impede a Fase 0, nem as Ondas 1, 2 e 3.**

C-NEW-01 (ICMS do frete), C-NEW-02 (GRIS) e C-NEW-04 (volume) foram resolvidos em 03/09.
Permanece apenas o limite operacional C-NEW-05: a cobertura da TRANSAL é SC, PR, SP e RS, e toda
venda CIF fora dela cai em `FRETE_A_COTAR` — não é bloqueio de implementação, é fato a exibir.

---

## 9. Estado de autorização

| Etapa | Situação |
|---|---|
| Auditoria (Fase 1 do prompt) | **Concluída** |
| Plano (Fase 2 do prompt) | **Concluído** |
| **Fase 0 — Fundação** | **Autorizada, ainda não executada** |
| Ondas 1 a 8 | **Não autorizadas** |

Regra do projeto: **uma etapa por autorização**. Ao terminar a Fase 0, parar e reportar os 10
itens exigidos antes de qualquer início da Onda 1.

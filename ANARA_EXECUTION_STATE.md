# ANARA — ESTADO DE EXECUÇÃO

Handoff entre sessões do Claude Code. Atualize este arquivo ao fim de cada etapa.

Última atualização: **04/09/2026 — fim da Sessão 3A**

---

# Estado atual

**Fase 0, Sessões 0.1, 1, 2 e 3A executadas e aprovadas. Sessão 3B é a próxima.**

| Etapa | Situação | Commit |
|---|---|---|
| Fase 0 — Fundação | aprovada | `165d75e` |
| Sessão 0.1 — fechamento documental | aprovada | junto de `9293c28` |
| **Sessão 1 — P0 fiscal por item, DIFAL, pagamento** | **APROVADA** | `9293c28` · `cd0fbd8` · `1d678ad` · `214f74b` |
| **Sessão 2 — custo por SKU, Daune, fronha, 280 g** | **APROVADA** | `7f09652` · `6b913b1` · `7d06036` |
| **Sessão 3A — frete comercial TRANSAL** | **APROVADA** | `a88eebd` |
| **Sessão 3B — Decimal e arredondamento** | **PRÓXIMA, não autorizada** | — |
| Ondas 4 a 8 | não autorizadas | — |

HEAD `a88eebd` · árvore limpa · Alembic em `0009` · **352 testes passando** · sem remote.

- Fase 1 (auditoria): **concluída** → `AUDIT_ANARA_MASTER.md`
- Fase 2 (plano): **concluída** → `IMPLEMENTATION_PLAN_ANARA.md`
- **Fase 0 — Fundação: EXECUTADA**, revisada externamente e **aceita**
- **Sessão 0.1 — fechamento documental: concluída.** Nenhum código, banco, migration, teste ou
  baseline tocado
- Ondas 1 a 8: **não autorizadas** — nenhuma foi iniciada

> **A Fase 0 alterou código.** `app/models.py` (aditivo), 2 migrations, esquema do banco, 13
> testes novos, 2 commits locais. Não escreva mais "nenhuma linha de código foi alterada" como
> descrição do estado. O que continua verdadeiro: **nenhuma regra de negócio, nenhum motor de
> cálculo e nenhum valor econômico foram alterados.**

O sistema continua rodando como antes: **173 testes originais passando** (mais 13 novos da
Fundação, 186 no total), 339 SKUs, 3 fornecedores, 4 bases de importação, 18 cotações
(2 ativas + 16 arquivadas), 45 itens, plataforma em `http://127.0.0.1:8420`.

Nenhuma cotação, item ou snapshot mudou de número: provado por digest coluna a coluna e
pelo baseline, que foi gerado antes das migrations e continua reproduzindo idêntico.

`relatorios/baseline_fase0.json` é o **BASELINE IMUTÁVEL PRÉ-ONDA 1**: 339 SKUs × 6 cenários
fiscais × 5 condições = 10.170 combinações teóricas, **8.670 preenchidas** (289 SKUs com custo;
50 sem custo não geram célula), mais 18 cotações com totais, **45 itens** com sha256 da memória
congelada, as 4 bases e as premissas vigentes, com digest por tabela e por coluna.
**Não se regenera.**

## Git

O repositório foi inicializado nesta fase e é **local**. Commit inicial
`Estado herdado do sistema Anara (pré-Fase 0)` é o ponto de retorno do código.

> **BLOQUEIO DE PUBLICAÇÃO REMOTA.** `app/auth.py:10-11` tem senha compartilhada em texto
> claro (`SENHA = "[SENHA-LEGADA-REMOVIDA]"`) e `SECRET_KEY` com fallback fixo, e isso está no histórico
> do Git desde o commit inicial. Enquanto essa credencial existir no histórico: **nenhum
> remote, nenhum push, nenhum GitHub**. Some-se a isso que `referencia/` versiona tabelas
> de preço de fornecedor. Liberar exige a Onda 4 (reforma de autenticação) **ou** uma
> sanitização explícita de histórico autorizada em separado.

> Estes números são o retrato de **03/09/2026**. O usuário usa a plataforma entre sessões,
> então contagens de cotações/itens **podem ter mudado legitimamente**. Divergência nessas
> contagens **não é inconsistência** e não deve travar a sessão. O que não pode mudar sem
> explicação é: 339 SKUs, 3 fornecedores, 4 bases e 173 testes passando.

---

# Última fase concluída

**Sessão 0.1 — fechamento documental** (03/09/2026), depois da **Fase 0 — Fundação**, que foi
executada e aceita em revisão externa.

A Sessão 0.1 fechou: os cinco estados de confiança canônicos (com REVALIDAR), o gate de
reconciliação do `REVIEW_REQUIRED` legado, a origem fiscal como atributo da operação, o B-15
(interpolação proibida de condição de pagamento), o B-16 (gramatura não estruturada), a
re-extração da tabela TRANSAL, a reabertura de C-NEW-01 e C-NEW-02, os novos C-NEW-06 e C-NEW-07,
o modelo econômico Daune, a nova fonte Daune 280 g e a divisão da Onda 3 em 3A e 3B.

---

# Alterações realizadas na Fase 0

**Nenhuma regra de negócio, nenhum motor de cálculo, nenhum template.** O único arquivo de
aplicação tocado foi `app/models.py`, e só de forma aditiva.

| Arquivo | O que é |
|---|---|
| `.gitignore` | Novo. Banco, backups, uploads, logs, caches e credenciais fora do Git |
| `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` | Novos. URL vem de `app.db.DB_PATH`, sobreponível por `ANARA_DB_URL` |
| `alembic/versions/0001_esquema_inicial.py` | Novo. Fotografia fiel do banco de 03/09 |
| `alembic/versions/0002_ponte_baseimportacao.py` | Novo. Ponte da decisão H |
| `app/models.py` | **Aditivo**: 4 colunas de vigência em `BaseImportacao` + modelo `BasePremissaPonte` |
| `scripts/fundacao.py` | Novo. Digest por tabela e por coluna, comparação de estados |
| `scripts/backup_banco.py` | Novo. Backup, verificação, restore de ensaio e restore real |
| `scripts/baseline_regressao_v2.py` | Novo. Baseline ampliado, com modo `--verificar` |
| `scripts/conferir_esquema_alembic.py` | Novo. Prova que as migrations reproduzem produção |
| `tests/test_fundacao.py` | Novo. 13 testes |
| `BACKUP.md` | Novo. Política de backup, restore e rollback |
| `relatorios/baseline_fase0.json` | Novo. O baseline (2,1 MB) |
| `relatorios/fase0_estado_banco_antes.json` / `_depois.json` | Novos. Retratos do banco |
| `AUDIT_ANARA_MASTER.md` | **B-13** e **B-14**, descobertos na Fase 0 |
| `ANARA_EXECUTION_STATE.md` | Este arquivo |

---

# Alterações realizadas na Sessão 0.1

**Somente documentação.** Nenhum `.py`, nenhuma migration, nenhum template, nenhum teste, nenhum
seed, nenhuma linha do banco, nenhum byte do baseline.

| Arquivo | O que mudou |
|---|---|
| `AUDIT_ANARA_MASTER.md` | Sequência histórica no cabeçalho · §1.0 estados canônicos · §1.0.1 gate de reconciliação · **B-15** e **B-16** · B-14 reformulado · §2.2 Daune consolidada · **§2.2.1 nova fonte 280 g** · §2.2.2 o que ela não resolve · §2.2.3 produtos sem método · §2.3 TRANSAL re-extraída · C-NEW-01 e C-NEW-02 reabertos · **C-NEW-06** e **C-NEW-07** · Q-09 a Q-15 · §6 testes legados |
| `IMPLEMENTATION_PLAN_ANARA.md` | Estado real da Fase 0 · mapa com **3A/3B** · Onda 1 com origem fiscal, B-15 e gate · Onda 2 com **§2.1.1 Daune 280 g**, cinco estados canônicos e produtos sem método · Onda 3A com contagem corrigida, adicionais e vigência · **Onda 3B** nova · impactos, riscos, volume, bloqueios e autorização |
| `ANARA_EXECUTION_STATE.md` | Este arquivo |
| `CLAUDE.md` | Tabela dos **cinco** estados de confiança · três armadilhas novas: origem fiscal ≠ origem logística, condição de pagamento não se interpola, preço bruto ≠ preço de venda |

# Testes / baseline

- **173 testes originais: todos passando, nenhum alterado.** Nenhum teste legado foi
  "consertado" para acomodar a Fundação
- **13 testes novos** em `tests/test_fundacao.py` — 186 no total
- Baseline ampliado: `relatorios/baseline_fase0.json` — 339 SKUs × 6 cenários fiscais ×
  5 condições de pagamento = 8.670 células (289 SKUs com custo; 50 sem custo não geram
  célula, porque o baseline não inventa margem), mais 18 cotações com totais, 45 itens com
  sha256 da memória congelada, as 4 bases e as premissas vigentes
- Reprodutibilidade conferida: `python3 scripts/baseline_regressao_v2.py --verificar`
- Baseline antigo preservado: `relatorios/baseline_regressao.json` (241 SKUs × 9 cenários)

> O baseline registra o comportamento **atual, com os bugs conhecidos de pé** — B-01 ainda
> devolve 4% para venda interestadual a contribuinte de fornecedor nacional. É de propósito:
> quando a Onda 1 mudar isso, a diferença tem que ser mensurável e explicável.

---

# Decisões vigentes

## Estados de confiança (Sessão 0.1)
- São **cinco**: CONFIRMADO · ESTIMADO · **REVALIDAR** · A_COTAR · REVIEW_REQUIRED
- **REVALIDAR** = referência **direta** que envelheceu, venceu ou tem anomalia. Continua
  utilizável **com alerta**; reconfirmar antes de compromisso firme
- REVALIDAR **≠** ESTIMADO (proxy) · **≠** A_COTAR (sem número) · **≠** REVIEW_REQUIRED (erro)
- **ESTIMADO nunca vira CONFIRMADO em silêncio**
- **`legacy REVIEW_REQUIRED` ≠ `REVIEW_REQUIRED` canônico** — 121 SKUs afetados; conversão exige
  o gate de reconciliação da Onda 1, nunca automação

## Fiscal
- **Origem fiscal é atributo da operação/item/NF**, não do fornecedor. Resolvida por item,
  auditável, snapshotada, sobrescrevível com autorização, **separada da origem logística**.
  Sem evidência → `REVIEW_REQUIRED`. Nunca hardcodar `KTC = SP` nem `KTC = SC`
- **SP → SP = 18%**, contribuinte ou não
- **Importada (KTC) interestadual = 4% quando a regra legal aplicável à mercadoria importada
  efetivamente se aplicar** — não é constante universal. O modelo admite vigência, origem,
  produto/NCM, exceção e override autorizado e rastreado
- **Nacional (Daune/Decor) saindo de SP: 7%** para AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT,
  MS, PA, PB, PE, PI, RN, RO, RR, SE, TO · **12%** para MG, PR, RJ, RS, SC
- Finalidade: `REVENDA`, `INDUSTRIALIZACAO`, `USO_CONSUMO`, `ATIVO_IMOBILIZADO`. Default
  hoteleiro `USO_CONSUMO`. **Consumidor final é derivado**, não é valor do enum
- DIFAL em interestadual para consumidor final: **contribuinte → destinatário recolhe** (não
  desconta da margem da Anara); **não contribuinte → remetente recolhe** (entra no waterfall)
- ICMS é **por item**, não por cotação

## Custo
- Daune: `custo_NET = gross − 12% ICMS − 9,25% PIS/COFINS` (fator ≈ 0,7986), aplicado ao
  **preço bruto da fonte**, nunca ao custo atual. Margem-alvo **14%**. Relatório de validação
  obrigatório antes de migrar; ambíguo vai para `REVIEW_REQUIRED`
- **Escopo real: 32 SKUs Daune com custo** (não 52) + 20 edredons em `A_COTAR`
- **Preço bruto de fornecedor ≠ preço de venda.** Bruto → créditos → CUSTO NET → fiscal,
  financeiro, comissão, frete → margem 14% → preço recomendado
- **Nova fonte registrada (03/09):** Daune · Edredom · 100% poliéster · **280 g** · nove
  dimensões, R$ 427,50 a R$ 679,72 de **preço bruto**. Linha própria: **não** é 180 g, **não** é
  250 g, e **não** se converte uma na outra. Nenhum SKU 280 g existe hoje no catálogo.
  Tratamento na Onda 2 — **não implementada**
- **180 g e 250 g continuam `A_COTAR`** quando não houver fonte específica. Proibido extrapolar
  de 280 g. Pluma e poliéster não compartilham curva
- Fronha: regra do §18 fechada e **validada contra os 5 backtests** (desvio 0,002%)
- Bottom sheet sem elástico usa o motor Flat/Top. Fitted/elástico continua especial ou A_COTAR
- 200TC/233TC/500TC/800TC **não** são calculáveis, mesmo existindo US$/m² na tabela
- Roupões: NCM **6208.91.00** (algodão) e **6208.92.00** (sintéticas). **6309 proibido**.
  I.I. 3,5% é override de família versionado, com precedência e imune a troca de NCM
- Estimativa de roupão: mesma construção/GSM/style/collar/size, buffer de **5%**, confirmação
  KTC antes do PO; sem base forte → `A_COTAR_KTC`
- `KTC_SPECIAL_QUOTED` com cadastro manual é obrigatório na V1

## Frete
- Transportadora **TRANSAL**, origem **Itajaí-SC** (editável e versionada)
- **10 regiões de destino, 9 com tarifa** (contagem corrigida na Sessão 0.1) · ~238 cidades em
  9 unidades. Passo Fundo-RS sem tarifa e sem cidades → `FRETE_A_COTAR`
- **ICMS do frete: EM ABERTO** (C-NEW-01 reaberto). Três evidências não reconciliadas. Não fixar
- **ADV = NF × 0,002** confirmado · **GRIS = NF × 0,001** existe, aplicabilidade **em aberto** ·
  **fiel depositário = NF × 0,005** existe na tabela, aplicabilidade **em aberto**
- **Não fixar `RV`** — o rate variável é a soma dos componentes percentuais **aplicáveis**
- **Validade da tabela: 31/12/2026.** Tabela vencida não é usada em silêncio
- Adicionais: paletização R$ 91/pallet · sábados, domingos e feriados 30% com mínimo R$ 1.431 ·
  agendamento R$ 1.000 / 1.431 / 2.144 por veículo · reentrega 50% · devolução 100%
- Cidade fora da cobertura → `FRETE_A_COTAR`, **sem aproximar por região vizinha**
- Pedágio R$ 0,0536/kg · TDE/TDC R$ 272/h após 2 h, +50% fora do horário comercial
- Frete mínimo substitui **apenas** o componente frete-peso
- SP capital → filial Guarulhos
- Peso taxado por hierarquia: `SKU_PACKING` → `SHIPMENT_VOLUME` →
  `CARRIER_CONFIRMED_TAXABLE_WEIGHT` → `ADMIN_OVERRIDE` → senão `FRETE_REVIEW_REQUIRED`.
  **Nunca** assumir que peso real vence. Override guarda usuário, data/hora, valor e fonte
- Múltiplas origens: frete por **grupo logístico**, somado. Vendedora vê só o total e, quando
  for o caso, "Frete aguardando validação"

## Comercial
- Margens: KTC toalhas e roupões 12% · lençóis <300TC 16% · ≥300TC 18% · demais KTC 15% ·
  Daune 14% · Decor 14%
- Comissão por faixa de markup: <60% 5% · ≥60% 6% · ≥70% 7% · ≥80% 8% · ≥90% 9% · ≥100% 10%
- Pagamento: 30 DD 1,6% · 30/60 3,2% · 30/60/90 4,8% · 30/60/90/120 6,4% · +150 8,0%
- **Condição não cadastrada não se interpola**: não contar barras, não somar 1,6% por parcela,
  não devolver como confirmada. Exige condição cadastrada ou override explícito autorizado.
  O comportamento atual do código é o **B-15**, corrigido na Onda 1
- Preço abaixo do recomendado **sempre** exige aprovação
- ESTIMADO com `confirmation_pending` gera PDF, mas **não** vira pedido nem WON
- Área administrativa atualizável é **P1**, pré-go-live (Onda 5)

---

# Blockers conhecidos

| ID | Situação |
|---|---|
| C-NEW-01 ICMS do frete | **REABERTO na Sessão 0.1** — três evidências não reconciliadas: o texto da tabela ("ICMS CONFORME LEGISLAÇÃO"), a informação posterior de que estaria incluso (**não anexada ao repositório**) e o exemplo da própria tabela que executa gross-up de 12%. **Bloqueia a Onda 3A** |
| C-NEW-02 GRIS | **REABERTO** — o valor 0,10% é fato; a aplicabilidade universal não. O exemplo da tabela omite o GRIS. Não remover, não assumir. **Resolver antes da Onda 3A** |
| C-NEW-06 Fiel depositário | **NOVO** — a tabela cobra **0,5% do valor da NF**. Aplicabilidade não confirmada. **Não fixar `RV = 0,80%` nem qualquer valor.** Bloqueia a fórmula do rate variável |
| C-NEW-07 Validade TRANSAL | **NOVO** — tabela vale até **31/12/2026**, com cláusulas de revisão por combustível e volumetria. Exige vigência no modelo |
| C-NEW-04 Volume/cubagem | **RESOLVIDO conceitualmente** — hierarquia de 4 fontes; não trava go-live. Risco residual: carga operacional de cadastro de volume |
| C-NEW-03 Passo Fundo-RS | Sem tarifa → `FRETE_A_COTAR` para a região |
| C-NEW-05 Cobertura | TRANSAL cobre SC, PR, SP e RS. CIF fora disso → `FRETE_A_COTAR`. Limite operacional, não bug |

| **PUBLICAÇÃO REMOTA** | **BLOQUEADA.** Credencial legada (`app/auth.py:10-11`) no histórico do Git desde o commit inicial. Sem remote e sem push até a Onda 4 ou sanitização explícita de histórico |
| B-13 esquema | Modelos e banco divergem em 21 pontos (índices, FKs, NOT NULL, tipos). Descoberto na Fase 0. Não impede onda nenhuma; exige migration própria em etapa autorizada |
| B-14 origem fiscal | **Regra definida na Sessão 0.1**: origem fiscal é atributo da operação/item/NF, não do fornecedor. O bug agora é "origens inconsistentes e hardcoded no legado, sem resolução canônica por item". Implementação na Onda 1 |
| B-15 condição de pagamento | **NOVO, P0** — condição desconhecida é interpolada por contagem de barras; condição vazia devolve 1,6% como confirmada; e há código morto que reimplementa a régua. Onda 1 |
| B-16 gramatura não estruturada | **NOVO** — `gsm` NULO em 28 dos 31 `Duvet Insert`; a gramatura só existe no nome. Impede o match por campos estruturados exatamente onde a fonte 280 g cai. Onda 2 |

**Onda 1: livre para autorização.** **Onda 2: livre.** **Onda 3A: BLOQUEADA** por C-NEW-01,
C-NEW-02 e C-NEW-06, e pela ausência das origens logísticas de Daune e Decor. **Onda 3B** depende
apenas de a 3A ter terminado.

---

# Fase 0 — o que foi entregue

| # | Entrega | Situação |
|---|---|---|
| 1 | Git local + `.gitignore` + commit inicial seguro | Feito. Sem remote |
| 2 | Baseline ampliado | Feito. `relatorios/baseline_fase0.json`, reprodutível |
| 3 | Alembic, esquema atual como revisão inicial | Feito. `0001`, fiel ao banco vivo |
| 4 | Ponte `BaseImportacao` | Feito. `0002`, aditiva, 40 linhas de ponte |
| 5 | Bases históricas e snapshots preservados | Provado por digest coluna a coluna |
| 6 | Script/procedimento de backup | Feito. `scripts/backup_banco.py` + `BACKUP.md` |
| 7 | Restore testado | Ensaio e restore real exercitados, em cópia |
| 8 | Testes da Fundação | 13 testes, todos passando |

Ponto de rollback do dado, fora da pasta que é podada automaticamente:
`~/Anara-Cotacao-Backups/anara_fase0_pre_20260903-080837.db`

---

# Próxima fase

## SESSÃO 3B — DECIMAL E ARREDONDAMENTO — **NÃO AUTORIZADA**

Escopo: `Decimal` nos motores financeiros, política central de arredondamento, preço comercial
em 2 casas, lucro/margem/comissão recalculados sobre o preço arredondado, reconciliação ao
centavo. Baseline de entrada próprio, relatório de diferenças próprio, checkpoint próprio.

> **As pendências de frete NÃO são escopo da 3B e não devem ser resolvidas nela.** Elas estão
> bloqueadas de forma segura — nenhuma cotação CIF forma frete hoje, e é assim que tem de
> continuar até haver decisão. Resolver qualquer uma delas de passagem, dentro de uma sessão de
> arredondamento, misturaria mudanças numéricas de origens diferentes e destruiria a
> rastreabilidade que o baseline existe para garantir.

### Pendências de frete — congeladas, aguardando decisão humana

| Pendência | Estado | O que falta |
|---|---|---|
| ICMS da prestação (C-NEW-01) | `FRETE_ICMS_REVIEW_REQUIRED` | Decidir entre tarifa com ICMS incluso ou gross-up; a alíquota é cadastrada, nunca hardcoded |
| GRIS (C-NEW-02) | `DESCONHECIDO` | Confirmar se os 0,10% incidem sempre |
| Fiel depositário (C-NEW-06) | `DESCONHECIDO` | Confirmar quando os 0,5% da NF incidem |
| Base do pedágio (C-NEW-08) | `DESCONHECIDO` | Declarar se incide sobre peso real ou taxado |
| Volume por SKU (C-NEW-04) | ausente | Cadastro; sem ele a cubagem bloqueia |
| Origem logística de Daune e Decor | NULA | De onde cada uma embarca |
| Passo Fundo / fora de cobertura (C-NEW-03, C-NEW-05) | `FRETE_A_COTAR` | Limite operacional, não bug |

## ONDA 1 — P0 FISCAL — **CONCLUÍDA na Sessão 1**

Não iniciar sem autorização explícita e nova. Escopo completo em `IMPLEMENTATION_PLAN_ANARA.md`.

O escopo agora inclui, além de B-01, B-02, B-03 e B-06:

- **B-14** — origem fiscal por item/operação, com relatório de evidência antes de qualquer
  migração massiva e `REVIEW_REQUIRED` quando a origem não for determinável;
- **B-15** — fim da interpolação de condição de pagamento e remoção do código morto;
- **Gate de reconciliação** do `REVIEW_REQUIRED` legado, **antes** de ativar qualquer blocker.

Antes de começar, o procedimento do `BACKUP.md`: estado, backup, cópia para
`~/Anara-Cotacao-Backups/` com nome próprio, ensaio de restore. Depois: comparar contra o
**baseline imutável** e explicar cada número que mudou, com a regra que o explica.

---

# O que NÃO fazer

- **Não** iniciar a Onda 1 sem autorização explícita e nova — a Fase 0 ter terminado não
  autoriza nada
- **Não** configurar remote nem dar push enquanto a credencial legada estiver no histórico
- **Não** "consertar" nem regenerar o baseline: ele é a referência imutável pré-Onda 1
- **Não** converter `REVIEW_REQUIRED` legado em canônico automaticamente
- **Não** fixar o rate variável do frete enquanto C-NEW-01, C-NEW-02 e C-NEW-06 estiverem abertas
- **Não** tratar preço bruto de fornecedor como preço de venda
- **Não** aplicar a fonte Daune 280 g a SKU de 180 g ou 250 g, nem que a medida coincida
- **Não** hardcodar origem fiscal de fornecedor nenhum
- **Não** executar mais de uma etapa por autorização
- **Não** apagar `BaseImportacao`, snapshots, cotações ou histórico
- **Não** reescrever componente que está correto por preferência arquitetural
- **Não** inventar premissa fiscal, custo, NCM, I.I. ou regra de produção
- **Não** usar fallback silencioso — falta de dado vira `REVIEW_REQUIRED` ou `A_COTAR`
- **Não** aplicar gross-up de ICMS no frete da TRANSAL
- **Não** assumir que peso real vence o peso cubado
- **Não** usar NCM 6309 para roupão novo
- **Não** deixar vendedor ver custo, margem, markup ou premissas — inclusive na API
- **Não** resolver discrepância em silêncio: abra ID novo no audit

---

# Questões em aberto

| ID | Pergunta | Bloqueia | Quem responde |
|---|---|---|---|
| C-NEW-01 | ICMS do frete: incluso, ou gross-up de 12%? | **Onda 3A** | TRANSAL — anexar a confirmação escrita ao repositório |
| C-NEW-02 | O GRIS de 0,10% incide sempre? | **Onda 3A** | TRANSAL |
| C-NEW-06 | O fiel depositário de 0,5% da NF incide sempre? | **Onda 3A** | TRANSAL |
| Q-L | De onde saem fiscal e logisticamente as mercadorias de **Daune** e **Decor**? A Daune declara "frete CIF São Paulo", o que **não** prova a origem | **Onda 3A** | Você |
| Q-F | O que fazer com venda CIF fora de SC, PR, SP e RS? | Onda 3A | Você |
| Q-G | Quem preenche volume dos SKUs, e em que prazo? | Onda 3A | Você |
| C-NEW-07 | Cotação com validade além de 31/12/2026, quando a tabela vence? | Onda 3A | Você |
| Q-E | Passo Fundo é região ativa? Nenhuma cidade aponta para ela | Onda 3A | TRANSAL |
| Q-O | Ativar `REVIEW_REQUIRED` canônico torna 121 SKUs não-cotáveis. Qual o plano de saneamento? | Onda 1 (gate) | Você |
| Q-M | Bed Runner (17) e Cushion Cover (1): há evidência documental, ou ficam `A_COTAR`? | Onda 2 | Você |
| Q-K | Onde está o "CHECKPOINT de respostas/correções"? Não está no repositório | — | Você |
| Q-P | Versionar `referencia/` e `relatorios/` com preço de fornecedor foi decisão minha. Confirma? | gate de push | Você |

**Encerradas na Sessão 0.1:** existência e definição de REVALIDAR · condição de pagamento
desconhecida · origem fiscal por item/operação · margem Daune 14% · fórmula econômica Daune ·
gramatura 280 g como linha própria · os nove preços como bruto de fornecedor · 4% da KTC como
regra condicional, não constante universal.

# Arquivos que a próxima sessão deve ler

| Ordem | Arquivo | Para quê |
|---|---|---|
| 1 | `ANARA_EXECUTION_STATE.md` | Este. O que está autorizado agora |
| 2 | `CLAUDE.md` | Princípios operacionais e armadilhas de cálculo |
| 3 | `IMPLEMENTATION_PLAN_ANARA.md` | Escopo exato da Fase 0 e das ondas |
| 4 | `AUDIT_ANARA_MASTER.md` | O que está certo, o que está quebrado, e por quê |
| 5 | `referencia/SUPER_PROMPT_ANARA_v2.txt` | Fonte de verdade de negócio (cópia no repo) |
| 6 | `BACKUP.md` | Backup, restore e rollback — o que fazer antes de cada onda |
| 7 | `relatorios/baseline_fase0.json` | Contra o que comparar depois de cada onda |
| — | `README.md` | Como rodar, estrutura de pastas |

Documentos-fonte de dados, todos em `~/Anara-Cotacao/referencia/`:
`Tabela TRANSAL - frete nacional 2026-02.xlsx` ·
`Linha Hotelaria - Daune - 12.08.26.xlsx` · `KTC_Pricing_Master_Simple.xlsx` ·
`Anara Storage PI 23-08-2026.pdf` · `HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf` ·
`Orcamento_Nanai_Muro_Alto_Atualizado_02-09-2026.docx` (caso histórico, não é golden master)

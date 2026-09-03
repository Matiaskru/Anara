# ANARA — ESTADO DE EXECUÇÃO

Handoff entre sessões do Claude Code. Atualize este arquivo ao fim de cada etapa.

Última atualização: **03/09/2026 — fim da Fase 0**

---

# Estado atual

**Fase 0 — Fundação concluída. Nenhuma regra de negócio foi alterada.**

- Fase 1 do SUPER PROMPT v2 (auditoria read-only): **concluída** → `AUDIT_ANARA_MASTER.md`
- Fase 2 (plano): **concluída** → `IMPLEMENTATION_PLAN_ANARA.md`
- Fase 0 — Fundação: **CONCLUÍDA em 03/09/2026**
- Ondas 1 a 8: **não autorizadas** — a Onda 1 **não** foi iniciada

O sistema continua rodando como antes: **173 testes originais passando** (mais 13 novos da
Fundação, 186 no total), 339 SKUs, 3 fornecedores, 4 bases de importação, 18 cotações
(2 ativas + 16 arquivadas), 45 itens, plataforma em `http://127.0.0.1:8420`.

Nenhuma cotação, item ou snapshot mudou de número: provado por digest coluna a coluna e
pelo baseline, que foi gerado antes das migrations e continua reproduzindo idêntico.

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

**Fase 0 — Fundação** (03/09/2026). Entregou: Git local com commit inicial seguro,
baseline ampliado, Alembic com o esquema atual como revisão inicial, migration-ponte da
`BaseImportacao`, script de backup com restore ensaiado e 13 testes de Fundação.

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

## Fiscal
- **SP → SP = 18%**, contribuinte ou não
- **Importada (KTC) interestadual = 4%**
- **Nacional (Daune/Decor) saindo de SP: 7%** para AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT,
  MS, PA, PB, PE, PI, RN, RO, RR, SE, TO · **12%** para MG, PR, RJ, RS, SC
- Finalidade: `REVENDA`, `INDUSTRIALIZACAO`, `USO_CONSUMO`, `ATIVO_IMOBILIZADO`. Default
  hoteleiro `USO_CONSUMO`. **Consumidor final é derivado**, não é valor do enum
- DIFAL em interestadual para consumidor final: **contribuinte → destinatário recolhe** (não
  desconta da margem da Anara); **não contribuinte → remetente recolhe** (entra no waterfall)
- ICMS é **por item**, não por cotação

## Custo
- Daune: `custo_NET = gross − 12% ICMS − 9,25% PIS/COFINS` (fator ≈ 0,7986). Relatório de
  validação obrigatório antes de migrar os 52 SKUs; ambíguo vai para `REVIEW_REQUIRED`
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
- **9 regiões/filiais cadastradas, 8 com tarifa**. Passo Fundo-RS sem tarifa → `FRETE_A_COTAR`
- **`icms_incluso = true`** na tabela atual: **sem gross-up, sem dividir por 0,88**
- **ADV = NF × 0,002 · GRIS = NF × 0,001** — os dois se aplicam
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
- Preço abaixo do recomendado **sempre** exige aprovação
- ESTIMADO com `confirmation_pending` gera PDF, mas **não** vira pedido nem WON
- Área administrativa atualizável é **P1**, pré-go-live (Onda 5)

---

# Blockers conhecidos

| ID | Situação |
|---|---|
| C-NEW-01 ICMS do frete | **RESOLVIDO** — `icms_incluso = true`; o exemplo da planilha que faz gross-up de 12% fica documentado como inconsistência do documento e não prevalece |
| C-NEW-02 GRIS | **RESOLVIDO** — ADV 0,2% e GRIS 0,1%, ambos aplicáveis; o exemplo da planilha que omite GRIS é incompleto |
| C-NEW-04 Volume/cubagem | **RESOLVIDO conceitualmente** — hierarquia de 4 fontes; não trava go-live. Risco residual: carga operacional de cadastro de volume |
| C-NEW-03 Passo Fundo-RS | Sem tarifa → `FRETE_A_COTAR` para a região |
| C-NEW-05 Cobertura | TRANSAL cobre SC, PR, SP e RS. CIF fora disso → `FRETE_A_COTAR`. Limite operacional, não bug |

| **PUBLICAÇÃO REMOTA** | **BLOQUEADA.** Credencial legada (`app/auth.py:10-11`) no histórico do Git desde o commit inicial. Sem remote e sem push até a Onda 4 ou sanitização explícita de histórico |
| B-13 esquema | Modelos e banco divergem em 21 pontos (índices, FKs, NOT NULL, tipos). Descoberto na Fase 0. Não impede onda nenhuma; exige migration própria em etapa autorizada |
| B-14 origem fiscal | Três origens convivem: "SC" nas bases, "Santa Catarina" no default do modelo, "São Paulo" na premissa e nas decisões. Descoberto na Fase 0. **Resolver na Onda 1** |

**Nenhum blocker impede as Ondas 1, 2 e 3.** A Fase 0 está concluída.

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

## ONDA 1 — P0 FISCAL — **NÃO AUTORIZADA**

Não iniciar sem autorização explícita e nova. Escopo em `IMPLEMENTATION_PLAN_ANARA.md`.
Quando for autorizada, precisa resolver também o **B-14** (origem fiscal), descoberto na
Fase 0, junto com B-01 e B-02.

Antes de começar, o procedimento do `BACKUP.md`: estado, backup, cópia do backup para
`~/Anara-Cotacao-Backups/` com nome próprio, ensaio de restore. Depois: comparar contra
`relatorios/baseline_fase0.json` e explicar cada número que mudou, com a regra que o
explica.

---

# O que NÃO fazer

- **Não** iniciar a Onda 1 sem autorização explícita e nova — a Fase 0 ter terminado não
  autoriza nada
- **Não** configurar remote nem dar push enquanto a credencial legada estiver no histórico
- **Não** "consertar" o baseline: ele registra o estado atual, bugs conhecidos incluídos
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

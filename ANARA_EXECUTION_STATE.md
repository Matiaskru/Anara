# ANARA — ESTADO DE EXECUÇÃO

Handoff entre sessões do Claude Code. Atualize este arquivo ao fim de cada etapa.

Última atualização: **03/09/2026**

---

# Estado atual

**Auditoria e planejamento concluídos. Nenhuma linha de código funcional alterada.**

- Fase 1 do SUPER PROMPT v2 (auditoria read-only): **concluída** → `AUDIT_ANARA_MASTER.md`
- Fase 2 (plano): **concluída** → `IMPLEMENTATION_PLAN_ANARA.md`
- Fase 0 — Fundação: **autorizada pelo usuário, NÃO executada nesta sessão**
- Ondas 1 a 8: **não autorizadas**

O sistema continua rodando como antes: 173 testes passando, 339 SKUs, 3 fornecedores,
4 bases de importação, 18 cotações no total (2 ativas + 16 arquivadas), 45 itens de cotação,
plataforma em `http://127.0.0.1:8420`.

> Estes números são o retrato de **03/09/2026**. O usuário usa a plataforma entre sessões,
> então contagens de cotações/itens **podem ter mudado legitimamente**. Divergência nessas
> contagens **não é inconsistência** e não deve travar a sessão. O que não pode mudar sem
> explicação é: 339 SKUs, 3 fornecedores, 4 bases e 173 testes passando.

---

# Última fase concluída

**Fase 2 — Plano**, com duas rodadas de decisões do usuário incorporadas (03/09/2026).

---

# Alterações realizadas

Apenas documentação. **Zero alteração em código, migrations, banco, templates ou testes.**

| Arquivo | O que é |
|---|---|
| `AUDIT_ANARA_MASTER.md` | Novo. Matriz de auditoria: 15 comportamentos corretos, 12 bugs P0, 11 módulos ausentes, questões encerradas, contradições |
| `IMPLEMENTATION_PLAN_ANARA.md` | Novo. Fase 0 + Ondas 1 a 8, com escopo, migrations, testes, risco e resultado esperado |
| `CLAUDE.md` | Reescrito como instruções operacionais curtas. Versão anterior em `data/backups/CLAUDE.md.antes-handoff` |
| `ANARA_EXECUTION_STATE.md` | Este arquivo |
| `PLANO_EXECUCAO_ANARA_v2.md` | Convertido em ponteiro para os dois documentos oficiais |

---

# Testes / baseline

- Suíte atual: **173 testes, todos passando** (estado herdado, não alterado nesta sessão)
- Baseline ampliado da Fase 0: **ainda não gerado** — é o primeiro entregável da Fase 0
- Baseline antigo existente: `relatorios/baseline_regressao.json` (241 SKUs × 9 cenários) e
  `relatorios/regressao.json`

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

**Nenhum blocker impede a Fase 0 nem as Ondas 1, 2 e 3.**

---

# Próxima fase

## FASE 0 — FUNDAÇÃO (autorizada, aguardando início em nova sessão)

Entregar:
1. Baseline ampliado — 339 SKUs × 6 cenários fiscais × 5 condições de pagamento + 45 itens +
   totais por cotação
2. Alembic com o esquema atual como revisão inicial
3. Ponte `BaseImportacao` (migration explícita, sem apagar base antiga nem tocar snapshot)
4. Script de backup
5. Restore testado
6. Testes da Fundação

Ao terminar, **parar** e reportar os 10 itens exigidos:
arquivos criados/alterados · migrations criadas · resultado dos 173 testes originais · resultado
dos testes novos · resultado do baseline ampliado · prova de que nenhuma cotação ou snapshot
histórico mudou · prova de backup e restore · anomalias · git diff ou resumo equivalente ·
confirmação de que a Onda 1 não foi iniciada.

---

# O que NÃO fazer

- **Não** iniciar a Onda 1 automaticamente após a Fase 0
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
| — | `README.md` | Como rodar, estrutura de pastas |

Documentos-fonte de dados, todos em `~/Anara-Cotacao/referencia/`:
`Tabela TRANSAL - frete nacional 2026-02.xlsx` ·
`Linha Hotelaria - Daune - 12.08.26.xlsx` · `KTC_Pricing_Master_Simple.xlsx` ·
`Anara Storage PI 23-08-2026.pdf` · `HAMAN GLOBAL_ANARA Quotation 25-8-2026.pdf` ·
`Orcamento_Nanai_Muro_Alto_Atualizado_02-09-2026.docx` (caso histórico, não é golden master)

# ANARA — ESTADO DE EXECUÇÃO

Handoff entre sessões do Claude Code. Atualize este arquivo ao fim de cada etapa.

Última atualização: **22/09/2026 — governança de produtos e custos no Admin, coerência catálogo × motor (BR-001) e toalha por gramatura; antes: calculadora para as vendedoras e I.I. KTC = 0%**

---

# 22/09/2026 — TRÊS CORREÇÕES OPERACIONAIS DE GO-LIVE (local; sem migration; sem commit novo, sem push)

**1. Catálogo × cotação diziam coisas diferentes.** O status de um SKU vinha de três fontes; o
BR-001 (roupão com EXW cotado, datado e documentado) aparecia "Disponível" no catálogo e
"Revisão necessária" na cotação. Causa raiz do REVIEW: **não é vínculo de custo perdido** — a
evidência é encontrada (`DERIVADO_DAS_PREMISSAS_VIGENTES`, US$ 24,00, 29/07/2026); falta o
**peso**, e sem peso a nacionalização assumiria frete internacional zero. Agora
`pricing_service.status_do_produto` é a única regra (referência vigente → senão motor) e o
catálogo pergunta a ela. Auditoria dos 380 SKUs ativos: **24 incoerentes → 0**; 86 rótulos de
cache corrigidos; 0 referências vigentes ignoradas pelo motor.

**2. Faltava o caminho de volta.** `Admin → Produtos e custos` (`/admin/produtos`) lista o
catálogo com o diagnóstico do motor e resolve na própria tela: peso, cotação KTC (EXW),
custo nacional, confirmar referência, rebaixar para REVALIDAR/A_COTAR — com fonte e motivo
obrigatórios, versionado e auditado. Confirmar é **recusado** enquanto houver premissa faltando.

**3. Toalha personalizada saía "200 fios".** O `<select>` de tecido, escondido para toalha,
continuava sendo enviado pelo `FormData`; `produto_simulado` copiava o `thread_count` do tecido.
Toalha agora ignora `material_id` no servidor: `gsm` persiste, `thread_count` fica NULO.

| O quê | Onde |
|---|---|
| `status_do_produto` (referência vigente → motor), usado por catálogo, item e governança | `app/pricing_service.py`, `app/routers/produtos.py` |
| Serviço de governança: diagnóstico + ações versionadas e auditadas | `app/governanca_produtos.py` |
| Tela e endpoints `/admin/produtos*` | `app/routers/admin.py`, `admin_produtos.html`, `admin_produtos.js` |
| Peso declarado no documento da cotação de 29/07 (etapa `peso_ktc`) | `app/dados_2026_09_21.py`, `scripts/aplicar_dados_2026_09_21.py` |
| Toalha sem fios (semântica no servidor) + campo escondido desabilitado | `app/calculadora.py`, `app/static/js/calculadora.js` |
| Auditoria catálogo × motor, antes/depois | `scripts/governanca_2026_09_22/auditoria_status_custo.py`, `relatorios/governanca_status_custo_2026_09_22.md` |
| Testes: governança (13), toalha (12) | `tests/test_governanca_produtos_2026_09_22.py`, `tests/test_toalha_gsm_2026_09_22.py` |

Resultados: pytest SQLite **1.798/0** e PostgreSQL **1.798/0**; Playwright governança **9/9**,
calculadora **19/19**, geral **42/42**, sinal **120/120**; smoke production-like **79 ok / 0
falhas**; auditoria de confidencialidade **170 superfícies, 0 vazamentos**. Paridade de preço
contra o baseline pós-I.I.-zero: **279 dos 280 SKUs idênticos**; só o **BL-003** mudou
(220,35 → 245,41), porque o peso declarado no documento passou a contar o frete internacional
que antes entrava como zero. Banco local em 0025, `integrity_check` ok, 1 produto alterado pela
etapa `peso_ktc` (#364) e nada mais; backups `data/backups/anara.db.antes-governanca-*` e
`~/Anara-Cotacao-Backups/anara_pre_governanca_20260922-140014.db`.

---

# 22/09/2026 — CALCULADORA / PRODUTO PERSONALIZADO PARA A VENDEDORA (local; sem commit novo, sem push)

**Causa raiz:** `app/routers/calculadora.py` chamava `exigir_admin` nas três rotas. Não era
filtro de conteúdo — era negação de acesso: a vendedora, que é quem monta a cotação, não
conseguia sequer abrir a tela para calcular uma fronha com aba diferente. **Sem migration** (nada
de esquema mudou) e **sem tocar em fórmula, margem, política, fiscal, comissão, sinal ou frete**.

| O quê | Onde |
|---|---|
| Rotas de operação (`exigir_autenticado` + `ve_economia`), contexto fiscal só para economia, opções sem preço de material, inputs econômicos ignorados para a vendedora | `app/routers/calculadora.py` |
| `resultado_comercial()` — lista de permissão `CAMPOS_RESULTADO_COMERCIAL`, comissão pela `comissao_do_item` canônica, pendências operacionais (`PENDENCIA_COMERCIAL`), `opcoes(session, economia=)` | `app/calculadora.py` |
| Situação em linguagem comercial, sem a palavra "custo" (`SITUACAO_COMERCIAL`, `EXPLICACAO_COMERCIAL`) | `app/rotulos.py` |
| Tela por papel: KPIs comerciais × econômicos, memória e `memoria.js` só com economia, ajudas com US$/CMT só com economia, título por papel | `app/templates/calculadora.html`, `app/static/js/calculadora.js` |
| Botão "Produto personalizado" na cotação para todo papel que edita | `app/templates/cotacao_detail.html` |
| RBAC: calculadora sai das listas de "rota administrativa" | `tests/test_auth_perfis.py`, `tests/test_seguranca_rbac.py`, `scripts/smoke_producao.py` |
| Testes A–L + Playwright dos três papéis + auditoria com as superfícies da calculadora | `tests/test_calculadora_vendedora_2026_09_22.py`, `scripts/ii_zero_2026_09_22/e2e_calculadora_playwright.py`, `scripts/politica_2026_09_21/auditoria_confidencialidade.py` |

Resultados: pytest SQLite **1.773/0** e PostgreSQL **1.773/0**; dirigidos da calculadora **18/18**;
Playwright calculadora (OWNER + as duas vendedoras + fluxo completo até pedir aprovação) **19/19**;
Playwright geral **42/42** e de sinal **120/120**; smoke production-like **79 ok / 0 falhas**
(OWNER abre a calculadora; SELLER abre e o HTML não leva economia); auditoria de confidencialidade
**164 superfícies, 0 vazamentos, 0 itens obrigatórios ausentes**; paridade de preço contra o
baseline pós-I.I.-zero **280 SKUs × 9 cenários, 0 diferenças** (CNET idêntico nos 280). Banco real
local **intocado** nesta etapa (sha `fde8289c…`, 388 produtos / 27 cotações / 60 itens / 2 snapshots).

---

# 22/09/2026 — I.I. KTC/Egito = 0% · PREÇOS COMERCIAIS INALTERADOS · LUCRO REAL MAIOR (local; commit sem push; sem deploy)

Alembic **`0025`** (`0025_ii_zero_protecao_comercial`, aditiva/reversível): `produto.protecao_comercial_pct/_fonte`,
`cotacaoitem.base_comercial_precificacao`, `protecao_comercial_pct`, `preco_b2b_economico`. Banco local real
migrado 0024 → 0025 e dados aplicados (etapa `ii_zero`: 309 SKUs pinados — 285 × 3,5%, 14 × 1,62%, 10 × 0 —,
32 regras de família, 29 linhas de NCM encerradas + 29 sucessoras a 0%; catálogo: `preco_base` recalculado
sobre a base comercial (0 alterados) e cache `custo_unitario` = CNET real em 209 SKUs). 2ª/3ª aplicação: 0 escritas.

| O quê | Onde |
|---|---|
| `referencia_comercial()` (mesmo waterfall, proteção no lugar do I.I.; `natureza` declarada) | `app/nationalization.py` |
| `II_ECONOMICO_KTC = 0`; `protecao_comercial_do_produto` (pino → família); `bases_de_preco` → (custo real, base comercial, memória); memória com `ii_pct`, `referencia_comercial`, `base_comercial_brl`, `b2b.preco_b2b_economico`; `regra_ncm` respeita vigência | `app/pricing_service.py` |
| `_calcular(..., base_comercial=)`: preço sobre a base, economia sobre o custo real; `_base_de_preco(item)`; pinos no item; `preco_b2b_economico`; prévia/adicionar/editar/duplicar/premissas/cenário | `app/routers/cotacoes.py` |
| Fingerprint (base/proteção só quando preenchidas); snapshot: custo real, lucro, margem, B2B comercial, tabela, desconto, comissão, base, proteção, B2B econômico, `ii_economico_ktc_pct`, `protecao_comercial_versao` | `app/workflow.py`, `app/workflow_service.py` |
| Calculadora: custo real no catálogo, preco_base = B2B comercial sobre a base (sem arbitragem customizado × SKU) | `app/calculadora.py` |
| Seeds: NCM vigente a 0% + `PROTECAO_COMERCIAL_POR_FAMILIA`; script: etapa `ii_zero` (inventário → pino → família → NCM versionada), cache do CNET real | `app/seeds.py`, `app/dados_2026_09_21.py`, `scripts/aplicar_dados_2026_09_21.py` |
| Confidenciais novos (base comercial, proteção, B2B econômico, I.I., NCM); payload admin com os três campos | `app/confidencial.py`, `app/comercial_service.py` |
| Baseline ANTES/DEPOIS + paridade (`gerar` / `comparar`), relatório `relatorios/paridade_ii_zero_2026_09_22.md` | `scripts/ii_zero_2026_09_22/baseline_ktc.py` |
| Oracle KTC (II 0 + referência comercial), matriz/fuzz (base comercial × custo real), sanity com CNET real / base / B2B econômico | `scripts/crisis/oracle_ktc.py`, `scripts/politica_2026_09_21/`, `scripts/relatorios_2026_09_21.py` |
| Testes: `tests/test_ii_zero_2026_09_22.py` (101) + semântica atualizada em calculadora/custo_familias/crisis/BL-001 | `tests/` |

Resultados (22/09/2026): pytest SQLite **1.761/0**, PostgreSQL 17.9 **1.761/0**; Playwright geral **42/42**, sinal
**120/120** (4 perfis), dashboard OWNER/ADMIN **9/9** (mesma venda: vendido 1.249,00 / ticket / comissão 51,21 iguais;
lucro 274,77 → 294,06; margem 22,0% → 23,5%); smoke production-like **79 ok / 0 falhas**; oracle KTC **124/124** (114
referências comerciais conferidas, dif. 0); **paridade 280 SKUs × 9 cenários = 2.520, 0 diferenças** de B2B/tabela/
preco_base/comissão (CNET caiu em 202, igual em 78 sem I.I. no custo); fuzz **2.000/0**; matriz × oracle com sinais
0/30/100% **272.160 cenários, 0 violações** (25.920 bloqueados = CARTÃO com sinal < 100%); auditoria de confidencialidade
**156 superfícies, 0 vazamentos** (achou e fechou: prévia `/calc` formava o B2B sobre o custo real — corrigido para a
base comercial, com teste). Âncora 300TC: CNET 58,35 → 56,42 · B2B 124,90 / tabela 249,80 iguais · margem realizada
22,00% → 23,55% · lucro unitário 27,48 → 29,41. Banco local: 0025, `integrity_check` ok, cotações 27 / itens 60 /
snapshots 2 / clientes 1 / oportunidades 2 / usuários 1 preservados linha a linha; backups `data/backups/anara.db.pre-ii-zero-0024-para-0025-*`
e `~/Anara-Cotacao-Backups/anara_pre_ii_zero_0024_*.db`.

---

# Patch pré-deploy de 21/09/2026 — SINAL/ENTRADA + auditoria de confidencialidade (local; sem commit, push, deploy; banco real intocado)

Parte do working tree da política de 21/09 (HEAD `3e40a64`, main). Alembic **`0024`**
(`0024_sinal_entrada`, aditiva e reversível): `cotacao.percentual_sinal` (NOT NULL, default 0),
`cotacaoitem.percentual_sinal`, `cotacaoitem.encargo_saldo_pct`. Migration 0023 **inalterada**.
Banco real continua em `0021` (sha256 `4ddd98ae…` antes e depois de tudo).

| O quê | Onde |
|---|---|
| Composição: `validar_percentual_sinal`, `percentual_sinal_do_formulario`, `rotulo_condicao`, `encargo_com_sinal` — `encargo_efetivo = (1 − sinal) × encargo_do_saldo`; sinal 0 devolve o mesmo objeto; 100% → 0 sem bloqueio | `app/payment_terms.py` |
| `regras_da_cotacao`: encargo efetivo no `TaxRuleSet`; contexto com `percentual_sinal`, `encargo_saldo_pct`, `condicao_pagamento_texto`; memória (`cenario`) | `app/pricing_service.py` |
| Cabeçalho: `possui_sinal` + `percentual_sinal` (validação 0–100, 400 sem alterar nada), material → recálculo preservando desconto; pinos do item; `criar`/`duplicar`; PDF com texto composto; `/cotacoes/nova` sem encargo para quem não vê economia | `app/routers/cotacoes.py` |
| Fingerprint: `percentual_sinal` só quando > 0 (hash antigo intacto); divergência de cenário considera o sinal pinado | `app/workflow.py`, `app/comercial_service.py` |
| Snapshot: `fiscal_json` + `percentual_sinal`, `condicao_saldo`, `condicao_pagamento_texto`, `encargo_efetivo_pct`; itens + `percentual_sinal`, `encargo_saldo_pct`; revisão herda o sinal; PDF final lê o texto do snapshot | `app/workflow_service.py`, `app/pdf_bridge.py` |
| `condicao_textual(session, cotacao)` (tela, PDF, aprovação) | `app/config_service.py`, `app/routers/workflow.py`, `aprovacao_detalhe.html` |
| UI: checkbox "Possui sinal / entrada" → percentual + rótulo "Saldo" + texto ao vivo ("30% de sinal + 70% em 30/60/90 dias"); checkbox conta como alteração material; desconto sobre a tabela também na leitura (emitida) | `cotacao_detail.html`, `cotacao.js`, `templating.py` (`p21-2`) |
| Confidenciais novos: `encargo_efetivo_pct`, `encargo_saldo_pct`, `encargo_saldo_label`, `encargo_label`, `encargo_financeiro_pct` | `app/confidencial.py` |
| Dados: etapa `sinal` desativa `SINAL30+30/60/90` (não semeada mais); `esquema_pronto` exige 0024 | `app/dados_2026_09_21.py`, `app/seeds.py`, `scripts/aplicar_dados_2026_09_21.py` |
| Testes: `tests/test_sinal_2026_09_21.py` (85: fórmula 6 sinais × 6 condições, validação, cotação, aprovação, snapshot, PDF, revisão/duplicata, rota 400, memória, vendedora, legado) | `tests/` |
| Validação: e2e de sinal (OWNER/ADMIN/vendedora interna/comissionada), matriz `--sinais`, fuzz com sinal, auditoria de confidencialidade com prova, sanity §11 | `scripts/politica_2026_09_21/`, `scripts/relatorios_2026_09_21.py`, `relatorios/confidencialidade_2026_09_21.md` |

Resultados (21/09/2026, patch): pytest SQLite **1.657/0**, PostgreSQL 17.9 **1.657/0**; Playwright
geral **42/42**, Playwright sinal **120/120** (4 perfis); matriz × oracle com sinais 0/30/50/100%:
**362.880 cenários, 0 divergências/violações** (38.880 bloqueados = exatamente CARTÃO com sinal < 100%; CARTÃO com sinal 100% resolve com encargo 0); fuzz **2.000 casos, 0 falhas** (1.137 com sinal, 529 trocas de cenário, 59 emitidas);
smoke production-like SQLite **78 ok / 0 falhas** (cópia real 0021→0024 e cópia ensaiada) e
PostgreSQL **80 ok / 0 falhas**; política vigente **378/378**; oracle KTC **122/122**; backtest KTC
**0 MOTOR ABAIXO** (REVISAR 2 — KTC-024 mantido em revisão); auditoria de confidencialidade
**156 superfícies × 2 vendedoras, 0 vazamentos, 0 itens obrigatórios ausentes**.

Ensaio de migração em cópia (`anara_ensaio24.db`): 0021 → 0024 (3 upgrades) → preview → aplicar →
aplicar de novo (0 escritas; contagens idênticas: cotações 24 · itens 55 · snapshots 1 · aprovações 0 ·
auditlog 160 · produtos 386 · regras 74 · referências 192 · FCP 676 · condições ativas 7) → smoke.

---

# Política comercial de 21/09/2026 — IMPLEMENTADA LOCALMENTE (sem commit, sem push, sem deploy, banco real intocado)

Baseline: HEAD `3e40a64` (main). Fontes: pacote `~/Downloads/ANARA_HANDOFF_FINAL_2026-09-20/`
(AS-IS 20/09, TO-BE, DIFF, contrato, escopo aprovado) e o prompt consolidado de 21/09, que
superou os pontos abertos (Daune sem trava, comissão por item, DIFAL/FCP na base, equipe fora).

Alembic **`0023`** (`0023_politica_2026_09_21`, aditiva): `cotacaoitem` + `preco_tabela`,
`desconto_vs_tabela_pct`, `modo_negociacao`, `desconto_editado_pct`, `base_comissionavel`,
`icms_base_comissao_pct`, `comissao_faixa_pct`; `cotacao` + `freight_manual_confirmado/por/em/obs`.
**Não aplicada ao banco real** (`alembic current` = 0021). A suíte que lê o banco real (somente
leitura) rodou contra uma cópia migrada via `ANARA_DB_REAL_PARA_TESTES` (`scripts/fundacao.py`,
`tests/legado.caminho_banco_real`).

| O quê | Onde |
|---|---|
| Base da comissão líquida de ICMS (`comissao_base_icms_pct`), B2B = primeiro centavo válido (`preco_b2b`), tabela, desconto, `preco_por_desconto` (ROUND_UP) | `app/pricing_engine.py` |
| Política 21/09: rótulo, premissas (`comissao_b2b_pct`, `fator_tabela`, `comissao_faixas_desconto`), escada, `comissao_do_item`, `versao_da_politica`, margens como dados | `app/politica_comercial.py` |
| Sem fallback de 15%: `MargemResolvida.tem_regra`, `SEM_REGRA` | `app/margin_rules.py` |
| `regras_da_cotacao(..., politica=)`; `politica_comercial_vigente` lê as premissas novas; `CHAVES_PINADAS`; memória com `b2b`; allowance por família; CMT "standard"/"com abas" | `app/pricing_service.py` |
| Avaliação por item (v2) mantendo v1 e pré-política; propostas por preço OU desconto; payloads | `app/comercial_service.py`, `app/routers/negociacao.py` |
| `PRECO_ABAIXO_B2B`, `SEM_REGRA_DE_MARGEM`, `FRETE_MANUAL_CONFIRMADO`, fingerprint (campos novos só quando preenchidos), Σ base | `app/workflow.py`, `app/workflow_service.py` |
| `_calcular` (margem = B2B; desconto), `_aplicar_resultado` (B2B + tabela), repricing preservando desconto, `editar_item` (preço→desconto), `duplicar`, frete manual no cabeçalho, `calc` | `app/routers/cotacoes.py` |
| Matriz fiscal 27 UFs + FCP por família (AL/RJ/SE); seeds/idempotente | `app/fiscal_2026_09_21.py`, `app/seeds.py` |
| Dados 21/09 (política, fiscal, Decor, ELIS, KTC 29/07, ABAS, preco_base) — preview/aplicar | `app/dados_2026_09_21.py`, `scripts/aplicar_dados_2026_09_21.py` |
| Decor: `registrar_decor`, `cnet_nacional` com memória parametrizada | `app/custo_service.py` |
| Fronhas 2% (`quality_allowance` escopo Pillow Case), ABAS em `nomes`/`spec_parser`/calculadora, toalha 90/10 | `app/calculadora.py`, `app/nomes.py`, `app/spec_parser.py`, `app/templates/calculadora.html` |
| Primeiro acesso com perfil autorizado pelo servidor (`conferir_perfil`, 403 ao forjar) | `app/recuperacao_senha.py`, `app/routers/login.py`, `redefinir_senha.html` |
| UI: Tabela · B2B · Preço da proposta · Desconto % (editável) · Sua comissão; frete manual; economia por item; "B2B de referência" no catálogo; `valor-alerta` (sem "margem" na tela comercial) | `cotacao_detail.html`, `cotacao.js`, `anara.css`, `produtos_list.html`, `_cotacao_situacao.html` |
| Confidencialidade: allowlist do item + confidenciais novos | `app/confidencial.py` |
| Testes: `tests/test_politica_2026_09_21.py` (67), legado sob contexto 16/09 (`tests/politica_legada.py`), crisis/e2e/fase3c/margens atualizados | `tests/` |
| Validação: e2e Playwright, matriz × oracle, fuzz, política vigente, relatórios A–E | `scripts/politica_2026_09_21/`, `scripts/relatorios_2026_09_21.py`, `relatorios/*2026_09_21*` |

Resultados (21/09/2026): pytest SQLite **1.572/0**, PostgreSQL 17.9 efêmero **1.572/0**; Playwright
e2e **42/42**; matriz fiscal × oracle **103.680 cenários, 0 divergências/violações**; fuzz **2.000
casos, 0 falhas**; smoke production-like SQLite **78 ok** / Postgres **80 ok**; política vigente
**378/378 produtos ativos com regra**; oracle KTC **122/122**; backtest KTC **0 P0_SUBCUSTO**.

**Pendências para aplicar (manuais):** backup → `alembic upgrade head` → `scripts/aplicar_dados_2026_09_21.py`
(preview, depois `--aplicar`) → `scripts/relatorios_2026_09_21.py` → conferir. Ver "Conflitos e
decisões" em `AUDIT_ANARA_MASTER.md` §12. **Regra fiscal do escopo atual está fechada** (base única
para não contribuinte; FCP 0% em BA/PE/PI para cama/banho sem enquadramento específico; AL/RJ/SE
como estão; sem cobertura comprovada → `REVIEW_REQUIRED`) — não há blocker fiscal externo para o
deploy. Registrados à parte e sem bloquear: KTC-024 em revisão; divergência documental da escada (CF-01).

---

# Auditoria de crise P0 — econômica, fiscal e comercial (17/09/2026) — EXECUTADA

**Deploy comercial estava bloqueado** pelo bug "mudar o cenário não recalcula os itens". Causa
raiz encontrada e corrigida (CR-01/CR-02: editar quantidade congelava o item em preço fixo, e
o preço fixo sobrevivia à troca de cenário). Sete correções de código (CR-01…CR-07), zero P0
aberto; P1 abertos são de dado/fonte/decisão (ver `AUDIT_ANARA_MASTER.md` §11). Sem migration.

Provas depois das correções: pytest SQLite **1.436/0**, PostgreSQL **1.436/0**; matriz fiscal
418.176 cenários sem divergência do oracle nem violação; fuzz 10.000 casos sem falha; 116
goldens; 12/12 transições Playwright; oracle KTC 106/106; backtest KTC 0 subcusto; Daune 52/52;
comissão e arredondamento ao centavo; smoke OWNER/SELLER 78/0. Cotações que podem ter ido ao
cliente: exposição conhecida R$ 0,00 contra a Anara (0022 correta; 0019 +R$ 86,40 a favor).
Artefatos em `~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917/` (`CRISIS_AUDIT_FINAL.md`,
`CRISIS_PRODUCT_REVIEW.md`, CSVs). Scripts `scripts/crisis/`, testes `tests/crisis/`.

Efeito operacional das correções: 87 SKUs passam a REVALIDAR (71 KTC com cotação de mai–jul e 16
Decor) — cotam e emitem, **não fecham venda** até reconfirmação no Admin; venda a não
contribuinte fora de SP/RJ continua bloqueada até cadastrar base interna e FCP (FIS-01).

# Preparação para produção / deploy (17/09/2026) — EXECUTADA, sem publicar

Alembic **`0022`** (`0022_enums_portaveis`: no PostgreSQL converte os três enums nativos em
`VARCHAR(64)`; **no-op no SQLite**). Nada foi publicado: sem remote, sem push, sem conta
externa, banco real intocado (sha256 `5b0c5cc0…` antes e depois).

| Item | Resultado |
|---|---|
| Banco por URL | `ANARA_DB_URL` > `DATABASE_URL` > `data/anara.db` relativo ao repo; `postgres://` normalizado para psycopg 3 |
| Postgres vazio + `alembic upgrade head` | **0001 → 0022 aplicadas** (PostgreSQL 17.9 efêmero). Antes: 0003/0005/0007 quebravam com `ativo = 1` em coluna boolean — corrigido para parâmetro |
| Suíte no Postgres (`ANARA_TEST_DB_URL`) | **1.241 passaram, 1 pulado, 4 falhas** — exatamente as mesmas 4 do SQLite (guardiões do conjunto herdado, D-01), em 1 min 49 s. Antes dos ajustes: FK aplicada derrubava `auditlog.ator_id` sem usuário, `cotacao_id=0`, `cliente_id=0`, `aprovador_id` fictício; `drop_all` não ordenava o ciclo cotacao ↔ oportunidade; um `IntegrityError` condenava a sessão compartilhada |
| Migrador `scripts/migrar_sqlite_para_postgres.py` | cópia do banco real → Postgres de teste: **1.231 linhas, 36 tabelas, 0 divergências, 0 FKs órfãs**; recusa destino ocupado; `--substituir-destino <nome>`; `--so-verificar` idempotente |
| Smoke production-like `scripts/smoke_producao.py` | SQLite: **79 ok / 0 falhas**; `--postgres` (via migrador): **81 ok / 0 falhas** — comando do `Procfile`, `ANARA_ENV=producao`, cookie Secure, OWNER/SELLER, URLs proibidas 403, PDF rascunho/final varridos, log sem segredo |
| Railway | `railway.json` (Railpack, `alembic upgrade head` no pre-deploy, healthcheck `/health`), `Procfile`, `requirements.txt`, `.python-version` |
| Histórico Git | **sanitizado** com `git filter-repo --replace-text`; ver "Git" abaixo |

O que mudou no código: `app/db.py` (URL, `criar_engine`, `url_segura`), `app/migrations.py`
(sem DDL nem backup de arquivo fora do SQLite), `app/models.py` (enums `native_enum=False`),
`app/auth.py` (`chave_e_placeholder`), `app/main.py` (fontes atrás do login, avisos de
produção), `app/routers/importar.py` (uploads dentro do repo), `alembic/env.py`,
migrations 0002/0003/0005/0007 (booleanos por parâmetro), `tests/conftest.py`
(`ANARA_TEST_DB_URL`, atores persistidos, sessão recuperável, `cotacao_de_apoio`),
`scripts/smoke_test.py` (etapa `RASCUNHO`, 303 em emitir/enviar). Novos:
`DEPLOY_PRODUCTION.md`, `scripts/migrar_sqlite_para_postgres.py`, `scripts/smoke_producao.py`,
`tests/test_producao_deploy.py` (22 testes), `railway.json`, `Procfile`, `requirements*.txt`,
`.python-version`, `alembic/versions/0022_enums_portaveis.py`.

**Banco real durante a sessão:** às 11:25:54 a conta OWNER apagou de vez, pela plataforma
local, 17 cotações herdadas já arquivadas (ids 1–16 e 19; `AUDIT_ANARA_MASTER.md` D-01). Backup
automático `data/backups/anara.db.exclusao-lote-20260917-112554` (sha256 idêntico ao baseline
`5b0c5cc0…` desta sessão) e cópia externa. Quatro testes-guardiões do conjunto herdado passaram
a falhar por isso. **D-01 RESOLVIDO no mesmo dia por decisão humana: as 63 linhas (17 cotações
+ 46 itens) foram restauradas do backup, ids preservados, 0 FKs órfãs** — ver D-01 no audit.
Suítes finais após a restauração: SQLite e Postgres **sem falhas**.

**Pendências para publicar (manuais, fora desta sessão):** decidir `referencia/` (recomendado
tirar do repo antes do push), criar GitHub privado e Railway, SMTP real, domínio. Roteiro
em `DEPLOY_PRODUCTION.md`.

# Hardening de acesso por perfil · login · recuperação de senha (17/09/2026) — EXECUTADO

Alembic **`0021`** (`0021_password_reset_token`, aditiva: tabela `passwordresettoken`, só o
hash do token). Backups antes: `data/backups/anara.db.antes-auth-migration-0021-*` e
`~/Anara-Cotacao-Backups/anara_auth_pre_20260917-085347.db`. Nada de pricing, fiscal,
comissão, CRM, PDF ou dados comerciais foi tocado.

| O quê | Onde |
|---|---|
| Destino por papel decidido no backend: vendedora → `/vendas`; OWNER/ADMIN → `/dashboard` (rota canônica; `/` redireciona por papel; a vendedora em `/dashboard` volta para `/vendas`) | `app/routers/login.py` (`landing`), `app/routers/dashboard.py` (`raiz`, `dashboard`), `base.html` |
| Vendedora não recebe economia em HTML nem em JSON: `situacao` devolve `motivo=PRECISA_APROVACAO` (não o código `MARGEM_ABAIXO_PISO`); bloqueios em linguagem comercial (`rotulos.BLOCKER_COMERCIAL`); aviso de premissas sem valores internos; varredura de todas as telas e endpoints da vendedora em teste | `app/routers/workflow.py`, `app/rotulos.py`, `_cotacao_situacao.html`, `cotacao_detail.html`, `tests/test_auth_perfis.py` |
| "Marcar as com cara de teste" saiu da lista de Cotações (a função `arquivamento.candidatas_a_teste` continua interna, só em teste) | `cotacoes_list.html`, `app/routers/cotacoes.py` |
| Login: ANARA · e-mail · senha · Entrar · "Esqueci minha senha"; sem seletor de papel, sem texto técnico | `login.html` |
| **Esqueci minha senha** (`/esqueci-senha`, resposta genérica e mesmo custo de tempo para e-mail inexistente) e **redefinir** (`/redefinir-senha?token=…`): token `secrets.token_urlsafe(32)`, só `sha256` no banco, 30 min, uso único, gerar um novo encerra os anteriores, redefinir incrementa `sessao_versao` e registra `AuditLog` sem senha/token | `app/recuperacao_senha.py`, `app/routers/login.py`, `esqueci_senha.html`, `redefinir_senha.html`, `app/models.py` (`PasswordResetToken`) |
| E-mail por ENV (`ANARA_MAIL_HOST/PORT/USER/PASSWORD/FROM/TLS`); sem SMTP: DEV escreve no log `anara.mail` (nunca na página), `ANARA_MAIL_BACKEND=memoria` para testes; produção sem SMTP avisa no startup e em `/health/detalhe` (`recuperacao_senha_por_email`) | `app/mail.py`, `app/main.py`, `app/routers/relatorios_comerciais.py` |
| Usuários (OWNER/`can_manage_users`): criar sem senha → link de primeiro acesso (48 h, mesma infraestrutura); "Enviar redefinição de senha" por pessoa; senha nunca visível; `CREATE_USER`/`SET_PASSWORD` na trilha | `app/routers/usuarios.py`, `admin_usuarios.html` |
| 18 testes / 23 pontos do enunciado | `tests/test_auth_perfis.py` |

Sessão/cookie revisados: `HttpOnly`, `SameSite=lax`, `Secure` em produção, segredo de ENV,
argon2id, logout — sem mudança necessária. Testes ajustados só na interface: destino do
admin (`/dashboard`) em `test_fase3b`, `test_copy_e_navegacao`, `test_fase3c`.

**Primeiro acesso:** implementado pela mesma infraestrutura do reset (conta criada sem senha
conhecida pelo gestor). `ANARA_BASE_URL` define a origem do link; sem ela, a origem do
pedido (ou `http://127.0.0.1:8420`).

---

# Fase 3C — Redesign da plataforma · Dashboard OWNER/ADMIN · UX seller · PDF cliente (16/09/2026) — EXECUTADA

Alembic **`0020`** (`0020_observacao_cliente`, aditiva: `cotacao.observacao_cliente`).
Backups antes da migration: `data/backups/anara.db.antes-fase3c-migration-0020-*` e
`~/Anara-Cotacao-Backups/anara_fase3c_pre_20260916-171044.db`. Nenhuma regra econômica,
fiscal, de workflow ou de política comercial foi reaberta: o frontend chama os endpoints
canônicos e mostra a resposta.

| O quê | Onde |
|---|---|
| Design system único (sidebar compacta, topbar baixa, tabelas densas, pills, modais, popovers, timeline, stepper, kanban, painel sticky, gráficos SVG sem CDN, responsivo) | `app/static/css/anara.css`, `app/static/js/ui.js`, `base.html`, `login.html` |
| Menu por papel: vendedora Vendas · Clientes · Cotações · Produtos; OWNER/ADMIN + Dashboard, Aprovações, Admin. Relatórios saiu do menu (fica no Admin e no Dashboard). Sem saudação | `base.html`, `app/routers/admin.py` (`AREAS_ADMIN`) |
| **Vendas**: lista em toda a largura com status inline (popover → `POST /vendas/{id}/status`), filtros (busca, status, cliente, responsável, período), `+ Nova venda` em modal, **Quadro** (Rascunho/Enviado/Negociação com contagem/total e drag-and-drop pelo backend) | `app/routers/vendas.py` (`quadro`, `PERIODOS_LISTA`), `vendas_list.html` |
| **Venda individual** 70/30: registro rápido (Enter salva), modal com próxima ação (tipo/data/hora), timeline única em português (sem enum/código), resumo, cotação atual, próxima atividade, pós-venda com stepper (Venda fechada → Entrega → Faturamento → Pagamento); vendedora edita só o que o backend permite; financeiro só OWNER/ADMIN | `venda_detail.html`, `app/crm_service.py` (`timeline` humanizada), `app/rotulos.py` (`EVENTO_TIMELINE`) |
| **Clientes**: lista com total comprado, nº vendas, última compra, em aberto, status financeiro derivado (`metrics_service.clientes_resumo`); **Cliente 360** com KPIs (total, nº, ticket, última compra, em aberto, atrasado), abas (Visão geral/Vendas/Cotações/Contatos), modais de nova venda, contato e edição de cadastro (`POST /clientes/{id}/editar`, CNPJ duplicado 409, finalidade do enum) | `app/routers/clientes.py`, `clientes_list.html`, `cliente_detail.html`, `app/metrics_service.py` |
| **Cotações**: lista (Número, Cliente, Venda, Rev., Valor, Status, Data, Responsável), filtros com rótulos humanos, período, busca; legado = "Legado — sem venda vinculada"; arquivar/restaurar/apagar no rodapé da tabela | `app/routers/cotacoes.py` (`listar`), `cotacoes_list.html` |
| **Cotação** redesenhada: breadcrumb Cliente/Venda, status, revisões, dados comerciais em grid (Destino, Contribuinte, Pagamento, Validade, Frete Anara/cliente/a combinar + valor) e "Mais opções" (prazo, local, contato, departamento, responsável, texto do frete, **observação para o cliente** × **observação interna**, termos); tabela Produto/Qtd./Recomendado/Seu preço/Desconto/Total; Daune = "🔒 fixo"; busca e inclusão de produto no recomendado | `cotacao_detail.html`, `app/static/js/cotacao.js` |
| **Negociação reativa**: preço → `POST /cotacoes/{id}/negociacao/preview` (debounce 300 ms) e grava ao confirmar (`POST /cotacoes/{id}/negociacao`); quantidade → `PUT /cotacoes/{id}/itens/{item}` + releitura; painel sticky (Produtos, Frete, Total, Desconto, Sua comissão estimada, ✓ Dentro da autonomia / ⚠ precisa de aprovação); bloco de situação/ações recarregado por `GET /cotacoes/{id}/painel` (partial `_cotacao_situacao.html`); recusa do servidor → mensagem humana + rollback visual. `desconto_linha_pct` e `preco_travado` entraram no payload da vendedora (comerciais) | `app/routers/cotacoes.py` (`_negociacao_inicial`, `painel_situacao`), `app/comercial_service.py` |
| **Economia da proposta** (OWNER/ADMIN, fechada por padrão): custo, receita, lucro, margem agregada, comissão variável/fixa, limitação pelo piso, absorções, diagnóstico por item — tudo do `payload_admin` | `cotacao_detail.html`, `cotacao.js` |
| **Dashboard OWNER/ADMIN** (`/`): filtros Período (Mês/Trimestre/Ano/12 meses/Personalizado), Vendedora, Cliente, Fornecedor, Família; KPIs principais (vendido, lucro, margem agregada, vendas fechadas), secundários (ticket, conversão, desconto médio, pipeline aberto), financeiros (faturado, pago, a receber, atrasado — três fatos separados); gráfico **Vendas e lucro por mês** (12 meses, barras + linha, tooltip, ano anterior só com dado real); performance por vendedora, top clientes + concentração Top 5, funil e aging, mix fornecedor/família, impacto dos descontos (faixas × margem, por venda), pós-venda, motivos de perda, rentabilidade por cliente | `app/routers/dashboard.py`, `dashboard.html`, `app/static/js/dashboard.js`, `metrics_service.dashboard_admin` / `serie_mensal` / `FiltrosDashboard` / `periodo_de` (+ `trimestre`, `12m`) |
| **Produtos**: catálogo comercial (busca, fornecedor, família, situação Disponível/Sob consulta/Revisar); economia e Memória só para quem vê economia | `app/routers/produtos.py` (`situacao_comercial`), `produtos_list.html` |
| **PDF cliente** — PROPOSTA COMERCIAL ANARA (ReportLab, fontes já licenciadas): cabeçalho com nº/revisão/data/validade, cliente/contato/local, tabela Produto-Especificação/Qtd./Valor unitário/Total com cabeçalho repetido e fechamento (subtotal, frete, TOTAL) nas últimas linhas da tabela, condições comerciais, observação cliente, termos, contato comercial, aceite, "Página X de Y"; rascunho com faixa e marca d'água (imagem) RASCUNHO — NÃO ENVIAR AO CLIENTE; **final nasce do `SnapshotEmissao`**; allowlist `CAMPOS_HEADER/ITEM/TOTAIS` + varredura de códigos (`PdfInseguro`); frete por extenso (nunca `A_COTAR`); sem desconto/recomendado/tabela | `app/pdf_proposta.py`, `app/pdf_bridge.py`, `app/routers/cotacoes.py` (`gerar_pdf`) |
| Ambiente de demonstração numa CÓPIA do banco + inspeção visual Playwright (desktop, celular, sem economia para a vendedora, sem erro de console) | `scripts/demo_3c.py`, `scripts/visual_3c.py` |
| 22 regressões da fase (UX seller, Dashboard, PDF: rascunho/final, 1/10/34 itens, paginação, soma ao centavo, confidencialidade, snapshot, frete) | `tests/test_fase3c_ux_pdf.py` |

Ajustes em testes anteriores, todos de interface (nenhum econômico): menu da 3C
(`test_copy_e_navegacao`), rótulo "Legado — sem venda vinculada" (`test_fase3b`), dois campos
comerciais novos no item da negociação (`test_negociacao_comercial`).

**Banco real:** só o esquema mudou (0020). Nenhum backfill; as 21 cotações históricas
continuam com `oportunidade_id` NULO. `gerar_cotacao.py` (gerador legado do Excel) ficou
intocado e deixou de ser usado pela plataforma.

**Fora desta fase, de propósito:** frete nacional definitivo (C-NEW-01/02/03/05/06/07/08),
cadastro fiscal pendente, offline V3, produção/deploy. OQ-01 continua aberta.

---

# Fase 3B — CRM comercial simples · Cliente 360 · cotações · pós-venda (16/09/2026) — EXECUTADA

Alembic **`0019`** (`0019_crm_pos_venda`, aditiva). **1176 testes** passando. Servidor
religado em 127.0.0.1:8420. Nenhuma alteração econômica (a Fase 3A não foi reaberta).

| O quê | Onde |
|---|---|
| **C-NEW-14 resolvido**: `/configuracoes/margem` versiona pelo `admin_service` (fonte, trilha, herança da política, faixa de fios) | `app/routers/configuracoes.py`, `app/admin_service.py` |
| "Venda" = Oportunidade; 3 etapas (RASCUNHO/ENVIADO/NEGOCIACAO); Vendido/Perdido = status; legado só leitura | `app/models.py`, `app/crm_service.py`, `app/rotulos.py` |
| Tela **Vendas** (lista com status inline + detalhe), rotas de status/vendido/perdido/reabrir/atualização/pós-venda | `app/routers/vendas.py`, `vendas_list.html`, `venda_detail.html` |
| Avanço automático só Rascunho → Enviado ao emitir/enviar cotação | `app/workflow_service.py` → `crm.avancar_por_envio` |
| Cotação nova exige venda (existente ou criada pelo nome); cross-client 409; lista mostra a venda ou "Sem venda vinculada (legado)" | `app/routers/cotacoes.py`, `cotacao_nova.html`, `cotacoes_list.html` |
| Cliente 360 (visão geral com métricas derivadas, vendas, cotações, contatos); criação recusa CNPJ repetido | `app/routers/clientes.py`, `cliente_detail.html`, `metrics_service.cliente_360` |
| `AtualizacaoComercial` append-only (+ próxima atividade); timeline consolidada | `app/models.py`, `app/crm_service.py` |
| Pós-venda V1 (`pos_venda_service`): entrega → aguardando pagamento → pago / atrasado; permissões vendedora × financeiro; AuditLog | `app/pos_venda_service.py` |
| Métricas para a 3C: `painel_vendas` (vendido/faturado/pago, lucro, margem, ticket, conversão, desconto ponderado, comissão, por vendedor/cliente/fornecedor/família, aging, pós-venda) | `app/metrics_service.py` |
| Vendedora entra em `/vendas`; `/` redireciona; menu Vendas · Clientes · Cotações · Produtos | `app/routers/login.py`, `app/routers/dashboard.py`, `base.html` |
| Relatório READ-ONLY de backfill das 21 cotações (não aplicado) | `scripts/relatorio_backfill_cotacoes.py` → `relatorios/fase3b_proposta_backfill_cotacoes.md` |
| 35 regressões da fase | `tests/test_fase3b_comercial.py` |

**Banco real:** cotações, itens, produtos, custos, snapshots e aprovações idênticos; 0
oportunidades criadas; só esquema novo (0019). **Backfill das 21 cotações: NÃO aplicado** —
decisão humana pendente sobre o relatório.

**Agendado para a Fase 3C:** Dashboard Admin + redesign completo da UX + **PDF cliente
final** (proposta profissional com identidade ANARA, allowlist de campos, sem código/status
interno, preço negociado, frete separado, rascunho marcado, snapshot como fonte, paginação
1/10/30+ itens, soma ao centavo). **Frete nacional continua pausado** (política já decidida:
rota exata → automático; sem tabela → manual; sem aproximação; fora de comissão/desconto);
C-NEW-01/02/03/05/06/07/08 seguem abertos. Offline V2 STALE, intocado.

---

# Fase 3A — política comercial canônica (16/09/2026) — EXECUTADA

Decisão comercial de 16/09/2026 implementada no sistema oficial, versionada e auditável.
Alembic **`0018`** (aditiva). **1142 testes** passando. Servidor religado em 127.0.0.1:8420.

| O quê | Onde |
|---|---|
| Política pura (constantes da decisão, derivação das regras, comissão proporcional/ponderada/variável) | `app/politica_comercial.py` |
| Preview/aplicação da negociação, payloads vendedora × admin, detecção de rascunho anterior | `app/comercial_service.py`, `app/routers/negociacao.py` |
| Comissão fixa e máxima para o piso no motor | `app/pricing_engine.py` |
| Regra de margem com piso/comissão/travado, resolução por data (C-NEW-13) | `app/margin_rules.py`, `app/pricing_service.py` |
| Exceção `MARGEM_ABAIXO_PISO`, fingerprint com política, comissão estimada no resumo | `app/workflow.py` |
| Preço Daune travado (409), recálculo da cotação após cada mutação de item | `app/routers/cotacoes.py` |
| Regras novas + premissas de comissão semeadas; script de aplicação com backup e trilha | `app/seeds.py`, `scripts/aplicar_politica_comercial_2026_09_16.py` |
| Relatório de impacto por SKU | `scripts/relatorio_impacto_politica_comercial.py` → `relatorios/impacto_politica_comercial_2026-09-16.md` |
| Testes novos | `tests/test_margens.py` (reescrito), `tests/test_politica_comercial.py`, `tests/test_negociacao_comercial.py` |

Banco real: 21 regras encerradas em 16/09 + 21 sucessoras, 2 premissas, 23 linhas de trilha
(`politica-comercial-2026-09-16-431b8c3a`); cotações, itens, snapshots, produtos e custos
**idênticos** ao backup. Backups: `data/backups/anara.db.antes-fase3a-migration-0018-*`,
`data/backups/anara.db.antes-politica-comercial-2026-09-16-*`,
`~/Anara-Cotacao-Backups/anara_fase3a_pre_*.db`.

Aberto: **C-NEW-14** (`/configuracoes/margem` edita no lugar), **OQ-01** (base contratual da
comissão), offline V2 **stale**. Próxima fase: a UX reativa sobre `/cotacoes/{id}/negociacao` —
**não iniciada, não autorizada**.

---

# Estado atual

**Fase 0, Sessões 0.1, 1, 2 e 3A aprovadas. Sessões 3B a 8 EXECUTADAS, aguardando auditoria.**

**O sistema está pronto para ser aberto e usado localmente** — ver `PILOT_READINESS.md`.

| Etapa | Situação | Commit |
|---|---|---|
| Fase 0 — Fundação | aprovada | `165d75e` |
| Sessão 0.1 — fechamento documental | aprovada | junto de `9293c28` |
| **Sessão 1 — P0 fiscal por item, DIFAL, pagamento** | **APROVADA** | `9293c28` · `cd0fbd8` · `1d678ad` · `214f74b` |
| **Sessão 2 — custo por SKU, Daune, fronha, 280 g** | **APROVADA** | `7f09652` · `6b913b1` · `7d06036` |
| **Sessão 3A — frete comercial TRANSAL** | **APROVADA** | `a88eebd` |
| **Sessão 3B — Decimal e reconciliação monetária** | **EXECUTADA, aguarda auditoria** | `e27e11e` (WIP) + commit final |
| **Sessão 4 — segurança, papéis, confidencialidade** | **EXECUTADA, aguarda auditoria** | commit da Sessão 4 |
| **Sessão 5 — admin, versionamento, impacto controlado** | **EXECUTADA, aguarda auditoria** | commit da Sessão 5 |
| **Sessão 6 — workflow, aprovações, emissão, revisão** | **EXECUTADA, aguarda auditoria** | commit da Sessão 6 |
| **Sessão 7 — CRM, pipeline, atividades, UX** | **EXECUTADA, aguarda auditoria** | commit da Sessão 7 |
| **Sessão 8 — relatórios, saúde, runtime, piloto** | **EXECUTADA, aguarda auditoria** | commit da Sessão 8 |

Alembic em **`0017`** (a Sessão 8 não criou migration) · **715 testes passando** · smoke test em runtime: 60 ok · árvore limpa · sem remote.

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

> **Histórico sanitizado em 17/09/2026.** `app/auth.py` do commit inicial tinha uma senha
> compartilhada em texto claro (constante `SENHA`) e um `SECRET_KEY` com fallback fixo; o
> valor da senha também foi citado neste arquivo e num teste, o que o levou a **todos** os
> commits. `git filter-repo --replace-text` substituiu os dois valores por
> `[SENHA-LEGADA-REMOVIDA]` / `[SECRET-LEGADO-REMOVIDO]` em toda a história. Bundle
> anterior à reescrita: `~/Anara-Cotacao-Backups/anara_git_pre_sanitize_20260917-093018.bundle`.
> Integridade: `git fsck` limpo, árvore de trabalho idêntica (mesmo hash de árvore do HEAD),
> `git log --all -p` sem o valor. **Os hashes de todos os commits mudaram** — os que
> aparecem nas tabelas deste arquivo e dos documentos irmãos são os anteriores; o mapa
> completo está em `~/Anara-Cotacao-Backups/anara_git_commit_map_20260917.txt` e os
> principais abaixo. A única conta real não usa a senha antiga (hash argon2id conferido);
> se ela foi reaproveitada fora da plataforma, rotacionar lá antes do push.
>
> | Commit (antes) | Commit (depois) | O que é |
> |---|---|---|
> | `413d6bd` | `68903cf` | Estado herdado (pré-Fase 0) |
> | `165d75e` | `fcfb793` | Fase 0 — Fundação |
> | `5551a25` | `b85948c` | Sessão 4 — autenticação |
> | `4ef4f8a` | `635b7f6` | Fase 3C — redesign |
> | `ded3b2e` | `80bba80` | Hardening de acesso e senha |
> | `6a85baf` | `96fd799` | Preparação para produção |
>
> **Segunda reescrita (17/09/2026, antes do primeiro push):** `referencia/` removida de todo
> o histórico (`git filter-repo --path referencia --invert-paths`), pasta mantida local e
> ignorada, cópia em `~/Anara-Cotacao-Backups/referencia/`. Os hashes mudaram de novo — mapa em
> `anara_git_commit_map_20260917_referencia.txt`; na tabela acima, a coluna "depois" vale para
> a primeira reescrita. Remote: `https://github.com/Matiaskru/Anara.git`.

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

## Precisão monetária (Sessão 3B)
- **`app/dinheiro.py` é a política única.** Precisão interna de 34 dígitos; `ROUND_HALF_UP`
  como único arredondamento comercial; `D()` como única entrada; `para_float()` como única saída
- **Nunca `Decimal(float)`** — a conversão passa pela representação textual
- **Quantização uma vez só**, quando o preço vira preço; depois **todos** os componentes são
  recompostos sobre o preço arredondado
- **`margem_alvo` e `margem_liquida` são campos separados.** A segunda é a margem do dinheiro
  que entra. Não reportar a teórica como se fosse a real
- **Lucro é resíduo**; a linha reconcilia ao centavo por construção
- **Total da linha = unitário comercial × quantidade**, quantizado
- **Rateio pelo maior resto**, desempate pela ordem canônica: R$ 100,00 ÷ 3 = 33,34 + 33,33 + 33,33
- **CNET não é quantizado** — é custo interno; só o preço comercial vira centavo
- **Sem migration `Numeric`, e o motivo foi medido:** no SQLite, `Numeric(18,2)` vira afinidade
  REAL igual a `Float` e **trunca na leitura** (`34.71540940423179` → `34.72`; `0.0759` → `0.08`).
  Como o histórico guarda preços com a precisão cheia do float, migrar **reescreveria cotações
  emitidas**. As colunas seguem REAL e a ponte é `D()`. **Não reabrir sem refazer a medição**
- Provado: 10.440 células da grade sem uma única diferença inexplicada; histórico com **zero**
  mudanças econômicas; digest do banco idêntico antes e depois

## Acesso e confidencialidade (Sessão 4)
- **Papéis canônicos:** OWNER · ADMIN · VENDEDOR_INTERNO · VENDEDOR_COMISSIONADO. Só OWNER e
  ADMIN veem economia; papel fora da lista é confidencial **por omissão**
- **Senha por usuário, hash argon2id.** A senha compartilhada acabou. Bootstrap por
  `scripts/criar_usuario.py`, senha vinda do ambiente ou digitada sem eco — nunca do código
- **`ANARA_SECRET_KEY` obrigatória em produção**; sem ela o processo não sobe. Em
  desenvolvimento, chave aleatória por processo. **Nunca** um default conhecido
- **Cookie carrega identidade, não autorização** (`id` + `sessao_versao`); o papel vem do banco
  a cada request. `HttpOnly` · `SameSite=lax` · `Secure` em produção · 12 h
- **Endpoint que só existe para expor economia é negado (403)**, não filtrado. Endpoint
  comercial é filtrado — o vendedor continua conseguindo cotar
- **Lista de permissão, não de bloqueio**, em `app/confidencial.py`
- **CSRF:** mitigado por `SameSite=lax` nas 28 rotas mutáveis, que são todas POST/PUT/DELETE.
  Sem token próprio — a arquitetura atual já barra o vetor clássico. Exceção registrada: B-20
- **Publicação remota continua BLOQUEADA.** A credencial saiu do código, mas segue nos commits
  `413d6bd` e `165d75e`
- Provado: 79 testes de segurança · regressão econômica idêntica à da 3B · digest sem nenhuma
  alteração em tabela preexistente

## Administração e versionamento (Sessão 5)
- **`app/admin_service.py` é a camada administrativa.** Atualizar cria versão; nunca
  sobrescreve. A **data** decide a versão vigente
- **Vigência futura passou a funcionar de verdade:** `referencia_vigente`, `resolver_margem` e
  `resolver_encargo` resolvem por data. Antes os três ignoravam `valid_from` — cadastrar para
  2027 valia no ato
- **`preview → aplicar` com token** de estado. Estado mudou desde o preview → `CONFLITO`,
  registrado na trilha, sem criar versão
- **`CondicaoPagamento` ganhou vigência** e `codigo` deixou de ser único — duas versões da
  mesma condição precisam coexistir para agendar troca de encargo
- **Trilha em `AuditLog`**: ator, papel, escopo, antes/depois, motivo, origem, resultado,
  correlação de lote
- **NO_OP × RECONFIRMAÇÃO:** a comparação é pela identidade econômica completa (valor,
  status **e** evidência). Preço igual com fonte nova vira versão — a reconfirmação é
  informação, não ruído. Só diferença de escrita é no-op
- **Pinning:** `CotacaoItem` guarda o **id** da versão de custo, da regra de margem, da
  condição de pagamento, da alíquota e das premissas globais. A genealogia de uma cotação
  não depende de lookup vivo, e versão retroativa não a reescreve
- **Importação com dry run obrigatório.** 6 mudam · 2 no-op · 1 review · 1 inexistente: só as
  6 escrevem. Segunda passada da mesma planilha é no-op
- **Rascunho não atualiza sozinho** — só é marcado como desatualizado
- **`can_manage_economics`** separa ver de alterar. `can_manage_users` não concede economia
- Provado: 56 testes de admin · regressão econômica idêntica à da 3B · nenhuma coluna
  preexistente alterada no digest

## Workflow comercial (Sessão 6)
- Estados: `rascunho` · `aguardando_aprovacao` · `aprovada` · `emitida` · `enviada` ·
  `cancelada`. `fechada`/`pedido`/`perdida` são legados e ficam **fora** do workflow
- **Aprovação vale para um fingerprint**, não para a cotação. Alteração material invalida
- **Preço abaixo do recomendado exige aprovação mesmo com margem boa**; margem abaixo da
  alvo exige de forma independente. Detecção **por item**
- **`preco_recomendado` ≠ `preco_base`** — o primeiro é do cenário da cotação
- **Blocker duro não é aprovável**: `A_COTAR`, `REVIEW_REQUIRED`, frete CIF irresolvido
- **`ESTIMADO`** emite proposta; `validar_compromisso_firme()` bloqueia o pedido
- **Emitido é imutável**; alterar depois cria revisão, com genealogia por
  `cotacao_origem_id` + `revisao`
- **`can_approve_quotes`** é alçada própria — `can_manage_economics` não a concede
- 18 cotações e 45 itens históricos: status, revisão e fingerprint **não** foram inventados

## CRM comercial (Sessão 7)
- **Cliente serve de prospect** — sem cadeia Lead/Prospect/Conta. CNPJ não é exigido para
  cadastrar; **emitir cotação continua exigindo dado fiscal**
- **Oportunidade ≠ cotação**; revisões da mesma proposta são o mesmo negócio
- **`responsavel_id` não é ACL** — "minhas oportunidades" é filtro, não segurança
- **Pipeline aceita avançar, voltar e pular**; `OportunidadeEtapaHistorico` é append-only
- **Valor estimado × cotado × fechado:** o cotado é **derivado**, não persistido
- **GANHA chama `validar_compromisso_firme`** — ESTIMADO/REVALIDAR/A_COTAR/frete bloqueiam
- **PERDIDA exige motivo estruturado**; reabrir não apaga a perda; GANHA é terminal
- **Cross-client recusado no servidor**
- Nenhuma das 18 cotações históricas ganhou oportunidade: `oportunidade_id` fica NULO

## Relatórios e prontidão (Sessão 8)
- **`app/metrics_service.py` é a definição única** das métricas; dashboard, CSV e testes
  usam as mesmas funções
- **Estado atual ≠ evento histórico**: reaberta conta como aberta, não como perdida
- **`valor_cotado_atual`**: maior revisão não cancelada por genealogia; R1 e R2 nunca somadas
- **Conversão** só sobre encerradas; sem encerradas devolve `None`, não `0%`
- **Margem agregada** é `Σ lucro ÷ Σ receita`
- **Saúde operacional** separa bloqueio de aviso e lista as pendências conhecidas sem
  resolvê-las
- **`/health`** público e mínimo; `/health/detalhe` autenticado
- **Smoke test real** (`scripts/smoke_test.py`): sobe o uvicorn, faz login, 23 rotas, dois
  fluxos E2E, PDF, e prova que o banco de produção não foi tocado
- **B-21 corrigido**: `ANARA_DB_URL` passou a isolar a aplicação inteira

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

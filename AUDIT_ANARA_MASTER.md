# AUDIT_ANARA_MASTER

Auditoria exigida pela Fase 1 do SUPER PROMPT v2. Atualizada na **Sessão 0.1 (03/09/2026)** com
os estados de confiança canônicos, a regra de origem fiscal por operação, a re-extração da tabela
TRANSAL e a nova referência Daune.

## Sequência histórica — leia antes de interpretar qualquer coisa aqui

1. **Fase 1 — auditoria** e **Fase 2 — plano**: feitas sem alteração ampla de código.
2. **Fase 0 — Fundação**: autorizada especificamente e **executada**. Alterou `app/models.py` de
   forma aditiva, criou 2 migrations Alembic, alterou o esquema do banco, criou 13 testes e dois
   commits locais.
3. **Sessão 0.1** (esta): documental. Nenhum código, banco, migration, teste ou baseline tocado.

> Não escreva mais "nenhuma linha de código foi alterada" como descrição do estado atual — é
> falso desde a Fase 0. O que continua verdadeiro é que **nenhuma regra de negócio, nenhum motor
> de cálculo e nenhum valor econômico foram alterados**.

**Checkpoint da Fase 0, revisado externamente e aceito:** HEAD `165d75e` · árvore Git limpa ·
nenhum push remoto · 186 testes passando · 173 herdados preservados sem edição · Alembic com o
esquema atual como revisão inicial · ponte para `BaseImportacao` · backup e restore ensaiados ·
alteração aditiva em models · **nenhuma alteração econômica nos snapshots históricos**.

O baseline gerado na Fundação é agora o **BASELINE IMUTÁVEL PRÉ-ONDA 1**
(`relatorios/baseline_fase0.json`). Não se regenera. É contra ele que toda onda é medida.

**Nenhuma Onda 1+ foi iniciada.**

Base auditada: `~/Anara-Cotacao` — 9.637 linhas Python em 61 arquivos (5.726 em 37 módulos de
`app/`), 14 templates, 186 testes passando (173 herdados + 13 da Fundação), 339 SKUs ativos,
3 fornecedores, 1 cliente, 18 cotações, 45 itens de cotação.

---

## 1. Matriz de auditoria

Legenda de prioridade: **P0** matemática/fiscal/dados · **P1** segurança/multiusuário/operação ·
**P2** melhoria.

Nomenclatura oficial do projeto: **Fase 0 — Fundação** (preparatória) e **Ondas de
Implementação 1 a 8**. São 1 fase + 8 ondas.

### 1.0. Estados de confiança canônicos — REGRA APROVADA

São **cinco**. A diferença entre eles é **de onde veio o número**, não o quanto ele parece bom.

| Estado | Origem do número | Pode cotar? | PDF? | PO / pedido / WON? | Condição |
|---|---|---|---|---|---|
| **CONFIRMADO** | referência direta, atual e suficientemente confiável | sim | sim | **sim** | dentro da validade |
| **ESTIMADO** | **proxy**: curva, análogo forte, interpolação documentalmente suportada, outra referência indireta robusta | sim | sim | **não** | `confirmation_pending = true` até confirmação |
| **REVALIDAR** | referência **direta** que era confiável e envelheceu, perdeu freshness, venceu, tem anomalia ou precisa de reconfirmação | sim, **com alerta** | sim | **não** | reconfirmar antes de compromisso firme |
| **A_COTAR** | não existe base segura | **não** gera preço automático | **não** | não | pedir referência ao fornecedor |
| **REVIEW_REQUIRED** | problema crítico de premissa, fiscal, rastreabilidade, configuração ou consistência | bloqueia conclusão | **não** | não | impedir passagem silenciosa |

**As três confusões que precisam ser evitadas:**

- **REVALIDAR ≠ ESTIMADO.** O REVALIDAR tem número **próprio e direto**; o ESTIMADO veio de proxy.
- **REVALIDAR ≠ A_COTAR.** O REVALIDAR já tem número comercialmente utilizável; o A_COTAR não tem
  número nenhum.
- **REVALIDAR ≠ REVIEW_REQUIRED.** Envelhecer não é erro crítico de cálculo. Nem toda anomalia de
  freshness invalida a matemática.

**ESTIMADO não é promovido a CONFIRMADO em silêncio** — a promoção exige ato humano registrado.

**Situação no código (03/09/2026):** o enum `CostConfidence` tem `CALCULATED`, `QUOTED`,
`ESTIMATED`, `MANUAL`, `LEGACY`, `REVIEW_REQUIRED`. **`REVALIDAR` não existe.** O conceito mais
próximo é `pricing_service.frescor` (FRESH ≤30 d · AGING ≤60 d · STALE >60 d · UNKNOWN), hoje
usado apenas em relatório e sem ligação com bloqueio. Distribuição atual: **147 FRESH · 4 AGING ·
67 STALE · 121 sem data de referência**. Implementação na Onda 2.

### 1.0.1. Gate de reconciliação — `legacy REVIEW_REQUIRED` ≠ `REVIEW_REQUIRED` canônico

**REGRA APROVADA.** Hoje há **121 SKUs** com `custo_confianca = REVIEW_REQUIRED` (71
`LEGACY_EXCEL` + 50 `MANUAL`) e **156** com `precisa_revisao = true`. Esses campos carregam o
sentido **histórico** do rótulo, não o canônico definido em 1.0.

**Nenhuma conversão automática.** Antes de `REVIEW_REQUIRED` virar blocker real de operação, é
obrigatório um relatório por SKU com: SKU · fornecedor · família · método de custo · confidence
legado · `precisa_revisao` · motivo · custo atual · fonte · data · freshness · rastreabilidade ·
**status canônico recomendado** (CONFIRMADO / ESTIMADO / REVALIDAR / A_COTAR / REVIEW_REQUIRED) ·
justificativa.

Sem esse gate, ativar o blocker torna **36% do catálogo não-cotável de uma vez**.

### 1.1. Comportamento verificado como CORRETO — preservar

| ID | Regra | Arquivo | Comportamento atual | Correto? | Teste? | Risco | Ação |
|---|---|---|---|---|---|---|---|
| OK-01 | Encargo financeiro subtraído do lucro (P0-01) | `pricing_engine.py:16-18,36-37` | `taxa_fixa = icms+pis_cofins+encargo`; `lucro = fat − impostos − comissão − custo` | Sim | Sim | — | Nenhuma. O P0-01 do prompt levanta suspeita infundada |
| OK-02 | Preço negociado recalcula waterfall (P0-02) | `routers/cotacoes.py::editar_item` | `_calcular` → `_aplicar_resultado`; grava `modo_edicao`/`valor_editado` | Sim | Sim | — | Nenhuma |
| OK-03 | Comissão recalcula por faixa (P0-03) | `_aplicar_resultado` | `comissao_para_markup(markup)` a cada alteração | Sim | Sim | — | Nenhuma |
| OK-04 | Circularidade determinística (§6) | `pricing_engine._resolve_markup_de_*` | Varredura de faixas com forma fechada, sem iteração | Sim | Sim | — | Estender para CF/RV (Onda 3) |
| OK-05 | Snapshot histórico (P0-08) | `CotacaoItem` + `memoria_json` | Regressão anterior: 0 de 43 itens alterados | Sim | Sim | — | Nenhuma |
| OK-06 | Motor KTC flat/top e duvet | `ktc_engine.py` | EXW 180×310 = 9,90235528 (8 casas); duvet 6/6 ≤1,02% | Sim | Sim | — | Nenhuma |
| OK-07 | Nacionalização | `nationalization.py` | EXW 8,81 → NET R$ 50,0491 exato | Sim | Sim | — | Nenhuma |
| OK-08 | Towels por peso (§17) | `ktc_engine.calcular_toalha` | 8,50 / 9,00 / 14,00; `preco_final=True` impede reaplicar CMT/2ª qual./margem | Sim | Sim | — | Nenhuma |
| OK-09 | SP→SP 18% (§27) | `fiscal_rules` + `RegraFiscalVenda` | Regra explícita, prioridade 10 | Sim | Sim | — | Nenhuma |
| OK-10 | Margens oficiais (§7) | `margin_rules` + `MargemRegra` | 12/16/18/15 KTC; 14% Daune e Decor | Sim | Sim | — | Legado 15% e piso 23% não existem no código |
| OK-11 | PDF não expõe interno (§34) | `gerar_cotacao.py` | Teste gera PDF com 3 fornecedores e falha se aparecer custo/EXW/ICMS/margem | Sim | Sim | — | Acrescentar campos novos sem redesenhar |
| OK-12 | Premissas versionadas (§35) | 8 tabelas com `valid_from/valid_to/ativo/fonte` | Alterar fecha versão, não sobrescreve | Sim | Sim | — | Falta UI de publicação (ver C-06) |
| OK-13 | Carga final usada como está (§27) | `fiscal_rules` | Não recalcula por base simples/dupla/FEM | Sim | Sim | — | Nenhuma |
| OK-14 | Duplicação usa base atual (P0-14) | `routers/cotacoes.py::duplicar` | Reconfere custo/preço-base; mantém preço negociado | Parcial | Sim | Baixo | Falta recalcular aprovação (Onda 5) |
| OK-15 | I.I. 3,5% roupão com precedência (P0-12) | `NcmRegra` prioridade 10 | Override de família vence lookup de NCM | Sim | Não | Médio | Criar teste; corrigir o NCM (B-10) |

### 1.2. Bugs P0 confirmados

| ID | Regra violada | Arquivo/linha | Comportamento atual | Risco | Ação |
|---|---|---|---|---|---|
| **B-01** | §27 — interestadual nacional 7%/12% | `fiscal_rules.py:54-57` | `if contribuinte: aliquota = aliquota_interestadual` → 4% para qualquer fornecedor | ALTO | Modelar `origem_fiscal` e tabela por par UF |
| **B-02** | §27/§6 — fiscal por item | `models.Cotacao.icms_aplicado` | ICMS é campo da cotação | ALTO | Mover cálculo fiscal para o item |
| **B-03** | §27 — finalidade | inexistente | Só existe `contribuinte_icms` | ALTO | Adicionar `finalidade` em cliente/unidade/cotação |
| **B-04** | §25/§26 — CIF na rentabilidade | `models.py:416` + router | `freight_valor` não entra no preço nem no lucro | ALTO | CF e RV no waterfall |
| **B-05** | §30 — preço-base comparável | `Produto.preco_base` | Base fixa do catálogo; `diferenca_pct_vs_base` mistura desconto com diferença fiscal | MÉDIO | Recomendado no cenário da própria cotação |
| **B-06** | §27/§23/§58 — fallback proibido | `fiscal_rules.py:63`, `nationalization.py` | ICMS 18% e II=0 avisam mas não bloqueiam | ALTO | `REVIEW_REQUIRED` + PDF bloqueado |
| **B-07** | §57A — precisão monetária | toda a cadeia | `float`; preço exibido 2 casas, margem com float cheio | MÉDIO | `Decimal` + política central |
| **B-08** | §15/§18 — fronha e bottom sheet | `spec_parser.FAMILIAS_CALCULAVEIS` | Marcados "sem fórmula" | MÉDIO | §18 fecha a fronha (validado); bottom sem elástico usa Flat/Top |
| **B-09** | §21 — custo Daune | `scripts/importar_fornecedores_nacionais.py` | Custo entra cheio, sem crédito | ALTO | Fator ≈ 0,7986 |
| **B-10** | §20/§58 — NCM roupão | `seeds.NCM_POR_FAMILIA` | 6309.00.10 (legado proibido) | MÉDIO | 6208.91.00 / 6208.92.00 (ver D-02, fechada) |
| **B-11** | §61 — migrations | `app/migrations.py` | `create_all` + diff de metadata; sem downgrade | MÉDIO | Alembic |
| **B-12** | §13 — métodos de custo | `models.CostMethod` | Enum pobre: falta `KTC_SPECIAL_QUOTED`, `KTC_ESTIMATED_FROM_QUOTES`, `A_COTAR_*`, `DAUNE_DIRECT`, `DECOR_DIRECT` | MÉDIO | Migrar enum com mapeamento do legado |
| **B-13** | §61 — esquema declarado ≠ esquema real | modelos × `data/anara.db` | 21 divergências entre o que `models.py` declara e o que o banco tem: 5 índices, 2 chaves estrangeiras, 10 colunas NOT NULL e 4 tipos booleanos. Consequência direta do B-11 — `ALTER TABLE ADD COLUMN` no SQLite não cria índice, FK nem constraint | MÉDIO | Migration própria, em etapa autorizada. **Descoberto na Fase 0** |
| **B-14** | §27 — origem fiscal | `models.Cotacao.estado_origem` (default), `BaseImportacao.origem_uf`, `premissa.catalogo_origem`, `TaxRuleSet.origem_uf` (default) | **Origens fiscais inconsistentes e hardcoded no legado, e ausência de resolução canônica por item/operação.** Quatro origens convivem — "Santa Catarina", "SC", "São Paulo" e "SC" — nenhuma delas resolvida a partir da operação real. 14 das 18 cotações gravadas com Santa Catarina; `RegraFiscalVenda` só tem regra explícita para SP→SP | ALTO | Onda 1: modelar origem fiscal **por item/operação**, auditável, snapshotada e sobrescrevível com autorização. Sem evidência → `REVIEW_REQUIRED` |
| **B-15** | §22 — condição de pagamento não se interpola | `payment_terms.py:41-47` e `pricing_engine.py:117` | Condição não cadastrada devolve `base + 1,6% × número de barras` na string, com `confirmado=False` e aviso — mas o número **segue para o preço**. Condição vazia devolve 1,6% com `confirmado=True`, **sem aviso**. `pricing_engine.encargo_financeiro_efetivo` é código morto que reimplementa a mesma régua e pode ressuscitá-la | **ALTO** | Onda 1, junto com o fim dos fallbacks silenciosos: exigir condição cadastrada ou override autorizado; remover o código morto; reescrever o teste que protege o comportamento |
| **B-17** | §21 — duas fontes Daune com bases diferentes | `tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx` × `Linha Hotelaria - Daune` | O custo persistido dos 13 SKUs reconciliados é **1,5123× o preço bruto** da fonte Daune — razão praticamente constante (1,51222 a 1,51236) entre produtos sem relação. Uma razão uniforme entre documentos diferentes não é coincidência: ou a planilha Trousseau é outra base (preço de venda, não custo), ou houve uma transformação uniforme no passado | ALTO | **Descoberto na Sessão 2.** Não resolvido: a migração usou o bruto da fonte do fornecedor, que é rastreável. Confirmar com a Daune qual é a base da planilha Trousseau |
| **B-21** | §63 — `ANARA_DB_URL` não isolava a aplicação | `app/db.py` (corrigido nesta sessão) | O `alembic/env.py` lia a variável desde a Fase 0 e o próprio comentário dele a documentava como "sobreponível por `ANARA_DB_URL`". Mas `app/db.py` montava o engine com o caminho fixo. O efeito era silencioso e perverso: apontar a variável para uma cópia fazia as **migrations irem para a cópia** e os **dados irem para a produção**. Nenhum erro, nenhum aviso | **ALTO** | **Descoberto na Sessão 8**, e da pior maneira: o primeiro smoke test desta sessão inseriu 21 linhas de teste no banco histórico — 2 cotações, 2 itens, 2 usuários, 2 oportunidades e mais. As 18 cotações e os 45 itens **históricos não foram alterados** (verificado byte a byte), e o banco foi restaurado do backup de entrada da sessão. **CORRIGIDO**: `app/db.py` passou a resolver `ANARA_DB_URL`, e `scripts/smoke_test.py` ganhou uma prova de isolamento que aborta antes de qualquer escrita, mais uma conferência final do tamanho e mtime do banco de produção |
| **B-20** | §10-11 — logout muda estado via GET | `app/routers/login.py` · `app/templates/base.html` | `GET /logout` encerra a sessão. `SameSite=lax` faz o navegador **enviar** o cookie em navegação de topo, então um link de terceiro clicado pelo usuário derruba a sessão dele. Não vaza dado nem executa ação sobre a cotação: o efeito é ter de logar de novo | **BAIXO** | **Descoberto na Sessão 4.** Não corrigido: a correção é transformar o link do menu em formulário POST com o mesmo visual, e mexer em UX estava fora do escopo desta sessão. Registrado para a sessão de UX ou para a de aprovação |
| **B-19** | §13 — script em massa não trata cenário fiscal bloqueado | `scripts/classificar_base.py:242`, `scripts/importar_fornecedores_nacionais.py:162,208` | Desde a Onda 1, `regras_da_cotacao` devolve `None` quando o fiscal ou a condição de pagamento não se resolvem — é a regra do "sem fallback". Os dois scripts em massa passam esse `None` direto a `calcular_por_margem` e morrem com `AttributeError: 'NoneType' object has no attribute 'rates_variaveis'`. O `importar.py` e o `calculadora.py` já tratam o caso; estes dois ficaram para trás | MÉDIO | **Descoberto na Sessão 3B**, ao varrer os scripts economicamente relevantes. **Reproduzido em `4a0a4a0`, sem nenhuma alteração da 3B — é consequência da Onda 1, não regressão da 3B, e não foi corrigido nela.** Corrigir exige decidir o que um importador em massa faz com SKU de cenário bloqueado (pular, marcar `precisa_revisao`, ou abortar): é decisão de negócio, não de arredondamento. **Mesma família:** `scripts/comparar_regressao.py:20` importa `resolver_icms_estruturado`, que a Onda 1 removeu de `fiscal_rules` — o script morre no import, também em `4a0a4a0` |
| **B-18** | §61 — poda de backup apaga o backup recém-criado | `app/migrations.py:32-41` | `shutil.copy2` **preserva o mtime da origem**, então todas as cópias do mesmo `anara.db` ficam com o **mesmo** mtime. Quando `data/backups/` chega a `MAX_BACKUPS`, a poda ordena por mtime, o empate é desfeito pela ordem arbitrária de `os.listdir`, e o arquivo que acabou de ser criado pode ser o removido. O comentário do próprio código descreve um bug irmão corrigido em 03/09 (ordem alfabética); trocar o nome pelo mtime **moveu** o problema em vez de encerrá-lo, porque `copy2` iguala os mtimes | MÉDIO | **Descoberto na Sessão 3B**, ao encher o diretório com o backup obrigatório da sessão. Reproduzido no código original em `4a0a4a0`, sem nenhuma alteração da 3B — **não é regressão da 3B e não foi corrigido nela**. Correção sugerida: `shutil.copy` (mtime novo) ou ordenar pelo timestamp que já está no nome do arquivo |
| **B-16** | §13/§21 — casamento por campos estruturados | `models.Produto.gsm` | **A gramatura dos edredons não é campo estruturado.** 28 dos 31 SKUs da família `Duvet Insert` têm `gsm` NULO; a gramatura existe apenas dentro da string do nome ("Edredom 190x260 · 180 g · 100% fibras de poliéster"). O casamento por campos estruturados, que a regra exige, é **hoje impossível** para gramatura — exatamente na família onde a nova fonte Daune 280g vai cair | MÉDIO | Onda 2: preencher `gsm` a partir da fonte antes de qualquer match, nunca casar por nome. **Descoberto na Sessão 0.1** |

### 1.2.1. Descobertos na Fase 0 (03/09/2026)

Os dois vieram da própria Fundação: o B-13 apareceu ao gerar a revisão inicial do Alembic,
que comparou modelo e banco pela primeira vez; o B-14 apareceu na ponte da `BaseImportacao`,
que compara campo legado com premissa versionada.

**B-13 — inventário da divergência.** Nada aqui muda número; muda garantia.

| Tipo | Quantidade | Quais |
|---|---|---|
| Índices declarados e ausentes | 5 | `ix_cotacao_numero`, `ix_produto_cost_method`, `ix_produto_custo_confianca`, `ix_produto_familia`, `ix_produto_fornecedor_id` |
| Chaves estrangeiras ausentes | 2 | `produto.fornecedor_id` → `fornecedor.id`, `cotacaoitem.fornecedor_id` → `fornecedor.id` |
| NOT NULL declarado, coluna nullable | 10 | `baseimportacao.icms_por_estado_json`, `baseimportacao.cenarios_fiscais_json`, `cliente.ativo`, `cotacao.estado_origem`, `cotacao.contribuinte_icms`, `cotacao.validade_dias`, `cotacao.freight_type`, `cotacao.freight_incluso`, `produto.precisa_revisao`, `toalhapreco.preco_final` |
| Tipo BOOLEAN declarado, INTEGER no banco | 4 | `cliente.ativo`, `cotacao.freight_incluso`, `produto.precisa_revisao`, `toalhapreco.preco_final` |

Os índices são desempenho; as FKs e os NOT NULL são integridade referencial que hoje não
existe em produção; os tipos são cosméticos no SQLite (afinidade NUMERIC × INTEGER guarda
0/1 igual) e deixam de ser cosméticos no PostgreSQL da Onda 4. A revisão inicial `0001`
fotografa o banco **como ele é**, com as diferenças anotadas no próprio arquivo; corrigir é
etapa própria, não efeito colateral da Fase 0.

**B-15 — a régua proibida, e onde ela mora.**

```
payment_terms.resolver_encargo(condicoes, codigo)
  1. código na tabela, com encargo   → encargo cadastrado                    CORRETO
  2. código na tabela, encargo NULL  → 0,0% + confirmado=False + aviso       CORRETO
  3. código VAZIO                    → 1,6% "padrão", confirmado=True        ← default SILENCIOSO
  4. código NÃO cadastrado           → 1,6% + 1,6% por barra, aviso          ← INTERPOLAÇÃO PROIBIDA
```

Os casos 3 e 4 contrariam a regra aprovada: não interpolar, não aproximar, não contar barras, não
inferir 1,6% por parcela, não devolver como confirmada. Exigir condição cadastrada ou override
explícito autorizado.

**Código morto a eliminar junto:** `pricing_engine.encargo_financeiro_efetivo` (linha 117)
implementa a mesma contagem de barras e não é chamado por ninguém. Enquanto existir, convida à
reintrodução do comportamento.

**Teste que protege o erro:** `test_motor_comercial::test_condicao_nao_cadastrada_usa_regua_antiga_com_aviso`
afirma que `"30/60/90/120/150/180"` devolve 9,6%. Correto quanto ao código, **errado quanto à
regra**. Reescrever na Onda 1.

**Situação atual do cadastro:** as cinco condições canônicas estão corretas (1,6% · 3,2% · 4,8% ·
6,4% · 8,0%), mais `À VISTA` a 0% e duas sem taxa confirmada (`SINAL30+30/60/90`, `CARTAO`), que
já se comportam corretamente. **Não existem** condições históricas de 14, 28, 56 ou 84 dias, nem
na tabela nem nas 18 cotações.

**B-14 — a origem fiscal não é uma só.**

| Onde | Valor | Alcance |
|---|---|---|
| `models.Cotacao.estado_origem` (default) | `"Santa Catarina"` | toda cotação nova que não definir origem |
| `BaseImportacao.origem_uf` | `"SC"` | as 4 bases |
| `premissa.catalogo_origem` | `"São Paulo"` | preço-base do catálogo |
| `pricing_engine.TaxRuleSet.origem_uf` (default) | `"SC"` | motor |
| Decisões de 03/09 | **São Paulo** | SP→SP 18%; nacional saindo de SP 7%/12% |

Hoje: 14 cotações gravadas com Santa Catarina (todas arquivadas) e 4 com São Paulo (as 2
ativas). `RegraFiscalVenda` tem regra explícita só para SP→SP, então cotação que ficar no
default cai na resolução por `EstadoFiscal` em vez da regra decidida.

**REGRA CANÔNICA APROVADA (Sessão 0.1).** Não existe obrigatoriamente uma única "origem fiscal da
Anara". A origem fiscal é **atributo da operação / item / NF**, e precisa ser:

- resolvida **por item**;
- auditável e rastreável até a evidência;
- **snapshotada** na cotação;
- sobrescrevível **com autorização registrada**;
- **separada da origem logística**.

**Nunca tratar origem logística como sinônimo de origem fiscal da NF.** Itajaí-SC ser o ponto de
entrada da importação da KTC **não prova** a origem fiscal da venda.

Cenários nacionais aprovados, quando a operação sair **fiscalmente** de SP:

| Operação | Alíquota no cenário geral |
|---|---|
| SP → SP | interna paulista, hoje **18%** |
| SP → N / NE / CO / ES | **7%** |
| SP → MG, PR, RJ, RS, SC | **12%** |

"SP" **não** vira constante eterna do fornecedor. Se a NF real tiver outra origem, vale a origem
real. Para a KTC vale o mesmo: **não hardcodar `KTC = SP` nem `KTC = SC`**.

**Se a origem fiscal necessária para calcular a operação não puder ser determinada com confiança:
`REVIEW_REQUIRED`.** Nunca um default.

Antes de qualquer migração massiva na Onda 1, exigir relatório: SKU → fornecedor → origem atual →
fonte → origem proposta → evidência. **Sem evidência, `REVIEW_REQUIRED`.**

### 1.3. Módulos exigidos e ausentes

| ID | Módulo | § | Situação | Prioridade |
|---|---|---|---|---|
| C-01 | Usuários, papéis, hash, CSRF, 403 backend | 10, 11 | Senha compartilhada em `auth.py:10` | **P1** |
| C-02 | Aprovação comercial | 9, 52 | Inexistente | **P1** |
| C-03 | CRM: organizações, unidades, contatos, deals, pipeline, atividades | 39-46 | Só `Cliente` simples | **P1** |
| C-04 | Frete nacional | 24-26 | Inexistente | **P0/P1** |
| C-05 | Audit log | 55 | Inexistente | **P1** |
| C-06 | **Área administrativa atualizável** (premissas, custos, upload, diff, RASCUNHO→SIMULAR→PUBLICAR) | 35, 54 | Existe leitura versionada e telas de configuração parciais; **não existe** upload, diff, simulação de impacto nem publicação | **P1** — reclassificado por decisão A |
| C-07 | PostgreSQL, optimistic locking | 62, 57B | SQLite, sem versão de linha | **P1** |
| C-08 | Cadastro manual `KTC_SPECIAL_QUOTED` | 19, decisão D | Inexistente | **P1** |
| C-09 | Volume/cubagem por SKU | decisão F | Inexistente — nenhum campo de volume | **P0** para CIF |
| C-10 | Grupos logísticos por origem | decisão G | Inexistente — origem única implícita | **P0** para CIF |
| C-11 | Saúde do sistema, relatórios, notificações | 50, 51, 57C | Parcial (`/relatorios/qualidade`) | **P2** |

---

## 2. Validações que executei sobre o documento-fonte

### 2.1. Regra de fronha (§18) — CONFIRMADA

Implementei as fórmulas e comparei com os cinco backtests do próprio §18
(50×70, flap 20, 250TC CVC, material US$ 1,25/m²):

| Construção | Corte | Calculado | Alvo §18 | Desvio |
|---|---|---|---|---|
| Standard / 0 abas | 54×165 | 2,0417 | 2,0417 | +0,002% |
| 2 abas | 54×185 | 2,5143 | 2,5143 | −0,001% |
| 3 abas | 59×185 | 2,6646 | 2,6646 | −0,001% |
| 4 abas | 64×185 | 2,8148 | 2,8148 | +0,002% |
| 4 abas + festonê | 64×185 | 2,9337 | 2,9337 | −0,001% |

Consequência: **39 fronhas** deixam de ser "sem fórmula". O valor implícito confirma
250TC Sateen CVC 70/30 plain = US$ 1,25/m².

### 2.2. Daune — modelo econômico aprovado e situação real do catálogo

**Daune é fornecedor nacional.** Não passa por nacionalização KTC.

**Condições de compra aprovadas**, declaradas por escrito pelo fornecedor na aba "Informações" de
`Linha Hotelaria - Daune - 12.08.26.xlsx` (verificado verbatim na Sessão 0.1):

- *"Sim todos os impostos estão inclusos, e o credito de ICMS é 12%"*
- *"Considerei 30 DD após a emissão da nota"*
- *"Sim frete CIF São Paulo"*

O arquivo tem **3 abas**: `Preços Daune 12.08.26` (fonte mais nova, **28 itens**),
`Preços Daune 27.07.26` (versão anterior — **é uma aba do mesmo arquivo**, não um arquivo
separado) e `Informações`.

#### Fórmula aprovada — CUSTO NET ANARA a partir do preço BRUTO do fornecedor

```
ICMS_credit        = gross × 12%
base_pc            = gross − ICMS_credit
PIS_COFINS_credit  = base_pc × 9,25%
CUSTO_NET_ANARA    = gross − ICMS_credit − PIS_COFINS_credit

fator equivalente  ≈ gross × 0,7986      (1 × 0,88 × 0,9075 = 0,798600)
```

#### O preço bruto Daune NÃO é o preço de venda da Anara

Depois do CUSTO NET, o motor comercial ainda aplica: fiscal da venda · PIS/COFINS de saída
vigente · condição financeira · comissão pela faixa de markup · frete comercial da Anara quando
aplicável · demais custos atribuíveis · e a **margem-alvo Daune de 14%**.

```
PREÇO BRUTO FORNECEDOR → créditos recuperáveis → CUSTO NET ANARA
                       → fiscal / financeiro / comissão / frete
                       → margem 14% → PREÇO RECOMENDADO ANARA
```

**Nenhuma referência Daune é copiada para o cliente como preço de venda.**

#### Situação real do catálogo (verificada no banco, Sessão 0.1)

| | SKUs | Método | Confiança | Fonte cadastrada |
|---|---|---|---|---|
| Com custo | **32** | `NATIONAL_SUPPLIER` | `QUOTED` | `tabela de preços Daune Anara-Trousseau-Fio a Fio.xlsx` |
| Sem custo | **20** | `MANUAL` | `REVIEW_REQUIRED` | bloco "TAMANHOS DE EDREDOM SOLICITADOS", tipo `A_COTAR` |
| **Total** | **52** | | | |

Custo médio dos 32 com custo: R$ 685,44 (faixa R$ 34,48 a R$ 2.064,71).

**Correção obrigatória de documentação:** a frase "52 SKUs Daune terão custo reduzido em ~20%"
está errada. Só **32** têm custo para converter. Os 20 restantes são edredons em `A_COTAR` e
continuarão assim até haver fonte.

**Complicação adicional:** os 32 SKUs custeados vieram de um documento **diferente** daquele que
a Onda 2 vai importar. A migração não é aplicar um fator — é **trocar a fonte e casar SKU a SKU
entre duas planilhas de origens distintas**.

### 2.2.1. NOVA FONTE DAUNE — Edredom 100% poliéster 280 g (registrada na Sessão 0.1)

**Informação fornecida diretamente pelo responsável do projeto em 03/09/2026.** Registrada aqui
para tratamento futuro na **Onda 2**. **Não implementada. Nenhum SKU criado. Nenhum preço
calculado.**

**Produto:** DAUNE · EDREDOM · **100% FIBRAS DE POLIÉSTER** · **280 GRAMAS**.

> **280 g é uma linha própria.** Não é 180 g. Não é 250 g. **Não converter silenciosamente uma
> gramatura na outra.**
>
> **13/09/2026 — a fonte escrita do fornecedor contradiz este rótulo.** Ver C-NEW-11.

**Preços BRUTOS DO FORNECEDOR** — nove dimensões:

| Dimensão | Preço bruto Daune |
|---|---:|
| 180×250 | R$ 427,50 |
| 190×260 | R$ 469,30 |
| 220×250 | R$ 495,00 |
| 230×260 | R$ 538,20 |
| 250×250 | R$ 562,50 |
| 260×260 | R$ 608,40 |
| 285×265 | R$ 679,72 |
| 290×245 | R$ 639,45 |
| 290×260 | R$ 678,60 |

Fonte: tabela/imagem fornecida diretamente pelo responsável do projeto em 03/09/2026.

> **ESTES NÃO SÃO PREÇOS FINAIS ANARA.** São **preço bruto do fornecedor** — o início da cadeia
> econômica, não o fim. R$ 469,30 na tabela **não** significa vender por R$ 469,30. Cada um ainda
> precisa passar por créditos → CUSTO NET → fiscal, financeiro, comissão e frete → margem de 14%
> → preço recomendado. Não cadastrar, não documentar e não exibir como preço de venda.

#### O que o catálogo tem hoje, e por que o match não é trivial

Verificado no banco na Sessão 0.1, sem alterar nada:

- A família `Duvet Insert` tem **31 SKUs**: 28 Daune (8 com custo, 20 sem) e 3 KTC.
- Os 20 sem custo são **5 dimensões × 2 composições (plumas de ganso / fibras de poliéster) ×
  2 gramaturas (180 g e 250 g)**.
- **Nenhum SKU do catálogo é 280 g.** Busca por `280 g`, `280g` e `gsm = 280`: **zero
  resultados**.
- Dimensões dos edredons Daune no catálogo: 156×230, 190×260, 220×240, 240×260, 250×260, 260×290,
  270×265, 285×265, 290×260.

Cruzando com as nove dimensões da nova fonte:

| Situação | Dimensões |
|---|---|
| Coincidem com dimensão existente | **190×260 · 285×265 · 290×260** (3) |
| Sem SKU correspondente | 180×250 · 220×250 · 230×260 · 250×250 · 260×260 · 290×245 (6) |
| SKU existe, sem preço 280 g | 250×260 · 270×265 (e as demais dimensões Daune) |

**A armadilha está exatamente nas três dimensões que coincidem:** o SKU existente é 180 g ou
250 g, e o preço novo é de 280 g. **Coincidência de medida não é match.** Sobrescrever seria
atribuir a um edredom de 180 g o preço de um de 280 g.

**Agravante — ver B-16:** `gsm` está **NULO em 28 dos 31** SKUs da família; a gramatura só existe
dentro da string do nome. O casamento por campos estruturados que a regra exige é hoje impossível
para gramatura, justamente nesta família.

### 2.2.2. O que a fonte 280 g NÃO resolve — REGRA APROVADA

**Edredom poliéster 180 g e 250 g continuam `A_COTAR`** quando não houver fonte específica.
A fonte 280 g não os resolve.

**Proibido, sem autorização posterior e explícita:**

- `180 g = extrapolação de 280 g`
- `250 g = extrapolação de 280 g`
- qualquer curva que cruze gramaturas

A série 280 g pode, no futuro, servir para: análise de consistência **entre tamanhos da própria
linha 280 g**; detecção de outliers **da própria linha**; e criação de uma curva 280 g, **se
explicitamente aprovada**. Para outra gramatura, não serve.

**Pluma × poliéster continuam separados.** Não misturar curvas de edredom de pluma de ganso com
edredom de fibra de poliéster — são produtos e cadeias de custo diferentes. As classificações já
auditadas das referências Daune atuais permanecem: CONFIRMADO quando o match direto é
suficientemente confiável; ESTIMADO quando derivado de curva forte; REVALIDAR quando a referência
direta tem problema de freshness ou anomalia; A_COTAR quando falta base segura.

**Protetores Daune.** Não fazer match automático entre *"Manta 120 grs impermeável"* e
construções do catálogo que mencionem *matelassado com alça* ou *matelassado com slip*, sem
evidência de equivalência técnica. Sem match técnico seguro: `A_COTAR`.

### 2.2.3. Produtos sem método fechado — REGRA APROVADA

**Não inventar engine nem fornecedor para fechar lacuna documental.** Havendo evidência
documental clara, classificar corretamente; não havendo, o item fica OPEN ou `A_COTAR` conforme a
natureza. **Nenhuma fórmula fictícia.**

Aplica-se hoje a **Bed Runner (17 SKUs)** e **Cushion Cover (1 SKU)**, que não tinham destino
declarado em nenhum documento até a Sessão 0.1.

### 2.3. Tabela da transportadora — RECEBIDA E LIDA

`Cópia de Tabela Industria Quimica Anastacio SC 2026 02 (3).xlsx`

**Transportadora:** TRANSAL TRANSPORTADORA SALVAN LTDA · CNPJ 00214121000993 ·
Av. Radial Oeste 563-293, Espinheiros, **Itajaí-SC**, CEP 88311740.

**Origem da tabela:** `REGIÃO ITAJAÍ - SC`. O documento da transportadora **já traz Itajaí como
origem** — não é apenas override da Anara. E `ILHOTA` aparece como cidade atendida pela filial
Itajaí, o que reconcilia a menção histórica a Ilhota-SC: Ilhota está dentro da região Itajaí.

**Re-extraída célula a célula na Sessão 0.1.** A extração anterior estava incompleta e com um
erro de contagem — as correções estão em negrito.

**Faixas (R$/tonelada sobre peso taxado) — linhas 10 a 19 da aba `Tabela de Frete`:**

| Região destino | 1 a 7.000 kg | acima de 7.000 kg | Frete mínimo | Prazo |
|---|---|---|---|---|
| Jundiaí-SP | 439 | 395 | 132 | 48 h |
| Guarulhos-SP | 439 | 395 | 132 | 48 h |
| Colombo-PR | 287 | 259 | 86 | 24-48 h |
| Joinville-SC | 281 | 252 | 84 | 24 h |
| Itajaí-SC | 270 | 243 | 81 | 24 h |
| Palhoça-SC | 281 | 252 | 84 | 24 h |
| Morro da Fumaça-SC | 333 | 299 | 99 | 24-48 h |
| Cachoeirinha-RS | 598 | 538 | 179 | 24-48 h |
| Farroupilha-RS | 632 | 569 | 189 | 48 h |
| **Passo Fundo-RS** | **vazio** | **vazio** | **vazio** | — |

**CORRIGIDO NA SESSÃO 0.1.** São **10 regiões de destino, 9 com tarifa preenchida**. A contagem
anterior ("9 regiões, 8 com tarifa") misturava duas coisas: a aba `Cidades Atendidas` tem
**9 unidades** — matriz Morro da Fumaça + 8 filiais (Cachoeirinha, Guarulhos, Jundiaí, Itajaí,
Joinville, Colombo, Palhoça, Farroupilha) — e **Passo Fundo não está entre elas**. Quem semear a
partir da frase antiga cadastra **uma região tarifada a menos**.

Passo Fundo-RS consta sem tarifa, sem mínimo e sem prazo, e **nenhuma cidade da aba de cobertura
aponta para essa região**. Tratamento: `FRETE_A_COTAR`.

ADV **0,20%** · GRIS **0,10%** · Pedágio **R$ 0,0536/kg taxado** — uniformes nas 9 regiões
tarifadas.

**Peso taxado (regra aprovada):** `peso_cubado = volume_m³ × 300` · `peso_taxado = max(peso_real,
peso_cubado)`. O fator 300 é confirmado pela própria tabela: *"MERCADORIA VOLUMOSA 300KG/M3"*.

**Adicionais — bloco integral da célula A21, não extraído antes:**

| Adicional | Valor conforme o documento | Constava? |
|---|---|---|
| TDE — dificuldade de entrega | Limite 2 h; após, **R$ 272,00/hora excedida** em horário comercial; **+50%** fora dele | sim |
| TDC — dificuldade de coleta | Mesma regra | sim |
| **Fiel depositário** | **0,5% do valor da nota fiscal** | **NÃO** |
| Paletização | **R$ 91,00 por pallet PBR** | sem valor |
| Sábados, domingos e feriados | **30% do frete original, mínimo R$ 1.431,00** | **NÃO** |
| Agendamento | Por veículo: VUC / 3-4 / Toco **R$ 1.000,00** · Truck **R$ 1.431,00** · Carreta **R$ 2.144,00** | sem valor |
| Reentrega | **50% do frete** | sem valor |
| Devolução | **100% do frete** | **NÃO** |
| ICMS | *"ICMS CONFORME LEGISLAÇÃO"* | **NÃO** |
| Cubagem | *"MERCADORIA VOLUMOSA 300KG/M3"* | como decisão, não como citação |
| **Validade da tabela** | **31/12/2026** | **NÃO** |
| Cláusulas de reajuste | Revisão conforme política de preços de combustível da Petrobras; reavaliação se a volumetria cair | **NÃO** |
| SASSMAQ | Vigência março/2027 | não relevante |

> **O fiel depositário de 0,5% é FATO — a sua aplicabilidade universal NÃO é.** Não fixar
> `RV = 0,80%` nem somar automaticamente o fiel depositário a ADV/GRIS. A composição do rate
> variável fica **indefinida** até C-NEW-06 ser resolvida.

**Cobertura:** **~238 cidades** em 9 unidades (recontagem da Sessão 0.1; o documento não numera,
a contagem é por separador). Cidade fora da cobertura → `FRETE_A_COTAR`, **sem aproximar por
região vizinha**. **São Paulo capital está na filial Guarulhos**, o que
confirma o §24. Jundiaí cobre Campinas, Sorocaba, Osasco, Barueri; Palhoça cobre Florianópolis;
Colombo cobre Curitiba; Cachoeirinha cobre Porto Alegre.

**TDE/TDC:** limite de 2 horas; após isso **R$ 272,00 por hora excedida** em horário comercial,
**+50%** fora do horário comercial.

---

## 3. Perguntas ENCERRADAS pelas decisões de 03/09

| ID | Questão | Resposta incorporada |
|---|---|---|
| Q-01 | Tabela da transportadora | **Recebida.** TRANSAL, origem Itajaí-SC, **9 regiões/filiais cadastradas, das quais 8 com tarifa preenchida**; Passo Fundo-RS sem tarifa. ~200 cidades, TDE/TDC R$ 272/h. Origem editável, versionada, sobrescrevível por perfil |
| Q-02 | NCM do roupão | **6208.91.00** (100% algodão) e **6208.92.00** (sintéticas/artificiais). 6309 proibido para mercadoria nova. I.I. 3,5% permanece como override de família versionado, imune a troca de NCM |
| Q-15 | KTC importada interestadual = 4% é constante universal? | **NÃO. Encerrada.** A formulação correta é: mercadoria KTC importada em operação interestadual → **4% quando a regra legal aplicável à mercadoria importada efetivamente se aplicar**. O modelo tem de admitir vigência, origem, produto/NCM, exceção e **override autorizado e rastreado**. Nunca uma constante impossível de sobrescrever |
| Q-03 | 200TC/230TC na tabela de materiais | Mantida a precedência do §15: existência de US$/m² **não** habilita cálculo automático |
| Q-09 | REVALIDAR existe? | **SIM — regra aprovada na Sessão 0.1.** Cinco estados canônicos, definidos em 1.0. REVALIDAR é referência direta que envelheceu ou tem anomalia; não é proxy (ESTIMADO), não é ausência de número (A_COTAR) e não é erro de cálculo (REVIEW_REQUIRED) |
| Q-10 | Condição de pagamento desconhecida | **ENCERRADA — não interpolar.** Nem por barras, nem por 1,6% por parcela, nem devolvendo como confirmada. Exige condição cadastrada ou override autorizado. O comportamento atual é o **B-15** |
| Q-11 | Existe uma "origem fiscal da Anara"? | **NÃO. Encerrada.** Origem fiscal é atributo da **operação/item/NF**, resolvida por item, auditável, snapshotada e sobrescrevível com autorização. Separada da origem logística. Sem evidência → `REVIEW_REQUIRED` |
| Q-12 | Margem-alvo Daune | **14%**, confirmada |
| Q-13 | Fórmula econômica Daune | **Encerrada** — ver 2.2. Fator ≈ 0,7986 aplicado ao **bruto da fonte**, nunca ao custo atual |
| Q-14 | Gramatura da nova linha de edredom Daune | **280 g**, linha própria. Os nove valores são **preço bruto do fornecedor**, não preço final Anara |
| Q-04 | UFs por faixa interestadual nacional | **7%:** AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, PA, PB, PE, PI, RN, RO, RR, SE, TO. **12%:** MG, PR, RJ, RS, SC. SP→SP interna (18%). Importada KTC segue 4% |
| Q-05 | Finalidade padrão | `USO_CONSUMO` para hotel, editável por cliente/unidade, sobrescrevível na cotação, snapshotada. Enum: REVENDA, INDUSTRIALIZACAO, USO_CONSUMO, ATIVO_IMOBILIZADO. **Consumidor final é derivado**, não é valor do enum |
| Q-06 | Cubagem e faixas | Confirmados: 300 kg/m³, faixas 1–7.000 e >7.000, tarifas agora conhecidas |
| Q-07 | Caso Nanai | **Não** é golden master. Preservar o arquivo, recalcular após as Ondas 1 a 3, criar `NANAI_MASTER_V2` e documentar cada diferença item a item |
| Q-08 | DIFAL em uso/consumo | **Contribuinte → destinatário recolhe** (não desconta da margem da Anara). **Não contribuinte → remetente recolhe** (entra no waterfall). Regra default no código; exceções por UF configuráveis |

---

## 4. NOVAS contradições encontradas

### C-NEW-01 — ICMS do frete — **REABERTO na Sessão 0.1** (P0, ALTO)

**Estava marcado RESOLVIDO. Volta a OPEN, e bloqueia a implementação definitiva da Onda 3A.**

Motivo da reabertura: existem **três evidências que não se reconciliam**, e a que sustentava o
"resolvido" não está no repositório.

| | Evidência | Diz | Está no repositório? |
|---|---|---|---|
| **A** | Texto da própria tabela | *"ICMS CONFORME LEGISLAÇÃO"* — ambíguo | **sim**, verificado |
| **B** | Informação posterior da transportadora | ICMS já estaria incluso | **não** — existe apenas como afirmação neste documento |
| **C** | Exemplo numérico da própria tabela | executa gross-up de 12% | **sim**, verificado em 13 casas |

Enquanto B não for anexado com data e autoria, não há como uma auditoria independente verificar a
decisão. **Não escolher solução aqui.**

**O exemplo, reconstituído integralmente na Sessão 0.1** (células G1:G5 da aba `Tabela de Frete`):

```
peso taxado    500 kg          ← 26,80 ÷ 0,0536
destino        Cachoeirinha-RS ← 299,00 = 0,5 t × R$ 598/t, faixa 1 a 7.000 kg
valor da NF    R$ 13.350,00    ← 26,70 ÷ 0,002
```

```
frete 299,00 + ADV 26,70 + pedágio 26,80 = 352,50
352,50 ÷ (1 − 12%)                       = 400,5681818182
célula "Frete Total" (G5)                 = 400,5681818181818   ← confere em 10 casas
```

**Dois componentes que a própria tabela define não aparecem no exemplo:**

```
GRIS              0,1% × 13.350 = 13,35    ← ausente
fiel depositário  0,5% × 13.350 = 66,75    ← ausente
```

Isso enfraquece o exemplo como fonte, mas **não o anula** — e não autoriza ninguém a decidir
sozinho. **Questão em aberto, bloqueadora da Onda 3A.**

**O modelo preserva `icms_incluso` e `icms_pct`** por tabela/transportadora, porque tabelas
futuras podem ter tratamento diferente.

### C-NEW-02 — GRIS — **REABERTO na Sessão 0.1** (P1, MÉDIO)

O que é **fato**: a coluna GRIS existe e vale **0,10%** nas 9 regiões tarifadas.

```
ADV  = valor total da NF × 0,002   (0,20%)
GRIS = valor total da NF × 0,001   (0,10%)
```

O que **não** é fato: que o GRIS incida sempre. O exemplo da própria tabela soma ADV e pedágio e
**omite o GRIS**.

Portanto: **não remover o GRIS do modelo** e **não assumir que sempre incide**. A aplicabilidade
fica OPEN e precisa ser resolvida antes da Onda 3A.

### C-NEW-06 — Fiel depositário de 0,5% sobre a NF (P0, ALTO) — **NOVO na Sessão 0.1**

**Fato:** a tabela cobra *"TAXA DE FIEL DEPOSITÁRIO, SERÁ COBRADO 0,5% DO VALOR DA NOTA FISCAL"*.

**Não é fato:** que se aplique a toda carga.

**Por que é P0:** o plano define o rate variável do waterfall como `RV = ADV + GRIS = 0,30%`. Se
o fiel depositário incidir sempre, `RV = 0,80%` — quase o triplo, dentro de uma fórmula de forma
fechada que resolve a circularidade comissão × markup. Não é detalhe de cadastro, é um **termo da
equação**.

**Enquanto não for resolvido: não fixar RV.** O modelo deve tratar cada componente como uma linha
de adicional com aplicabilidade própria, não como uma constante somada.

### C-NEW-07 — Validade da tabela TRANSAL: 31/12/2026 (P1, MÉDIO) — **NOVO na Sessão 0.1**

A tabela declara **validade até 31/12/2026**, mais duas cláusulas de revisão: por política de
preços de combustível da Petrobras e por queda de volumetria.

**Consequência para a modelagem:** a tabela de frete precisa de vigência, como as demais
premissas versionadas. **Uma tabela vencida não pode continuar sendo usada em silêncio.** Vencida
e sem versão válida no lugar → `FRETE_REVIEW_REQUIRED` ou `FRETE_A_COTAR`, conforme a modelagem
da Onda 3A definir.

### C-NEW-08 — Base do pedágio não declarada (P1, MÉDIO) — **NOVO na Sessão 3A**

O cabeçalho da TRANSAL traz a coluna `PEDAGIO` com R$ 0,0536, mas **não diz sobre qual peso ela
incide** — real ou taxado. O exemplo da planilha não distingue: nele os dois valem 500 kg.

Tratamento implementado: a tabela guarda `pedagio_base` (PESO_TAXADO / PESO_REAL /
DESCONHECIDO). Enquanto for DESCONHECIDO, o cálculo segue quando peso real e peso taxado
coincidem — a ambiguidade não muda o número — e **bloqueia** quando eles diferem, que é
exatamente quando ela passa a importar. Proporcional, e sem escolher por conveniência.

### C-NEW-09 — `POST /cotacoes/{id}/status` alcança `aprovada` sem alçada e sem trilha (P0, ALTO) — **RESOLVIDO em 09/09/2026**

**Alcance real: maior que o registrado inicialmente.** Reproduzido em banco temporário, um
VENDEDOR_COMISSIONADO percorria a cadeia inteira pela rota genérica — `aguardando_aprovacao`,
`aprovada`, `emitida` e `enviada`, todas atingidas —, deixando `AprovacaoCotacao` e
`SnapshotEmissao` com zero registros. A cotação ficava "emitida" **sem o documento congelado
existir**, e o PDF saía final, sem marca d'água, com item de R$ 0,00 dentro.

A causa da exposição era a tela: `cotacao_detail.html` renderizava `wf.proximos_estados()`
como botões que postavam o estado desejado nessa rota, e **nenhuma rota canônica era chamada
por aquela tela**. O único caminho que o usuário tinha era o bypass.

**Correção.** `DONOS_CANONICOS_DO_ESTADO` recusa todo destino privilegiado nomeando a ação
certa; da rota genérica sobrou `→ rascunho`, que retira privilégio. A tela passou a chamar as
ações canônicas, com os botões derivados da `Prontidao`, e as rotas de workflow respondem a
form HTML com redirect (JSON continua para `fetch`). Ver `SYSTEM_AS_BUILT.md` §9.5.

**Provado por** `tests/test_workflow_bypass_p0.py`: os cinco estados privilegiados recusados
para os quatro papéis, a cadeia completa passo a passo, vendedor não aprovando a própria
exceção, OWNER precisando da mesma trilha, invalidação pós-alteração intacta, e nenhuma ação
da tela postando estado privilegiado.

**A cotação ANARA-2026-0021 permanece exatamente como estava** — é a evidência do defeito e
não foi corrigida retroativamente.

<details><summary>Registro original da investigação</summary>

Descoberto no uso real do sistema em 09/09/2026: a `ANARA-2026-0021` está com
`status = "aprovada"` e a tabela `aprovacaocotacao` tem **zero linhas**. O log do servidor
mostra dois `POST /cotacoes/21/status` e nenhuma chamada a `/aprovacao/`.

**O caminho.** `cotacoes.py::mudar_status` valida a **transição** com `wf.exigir_transicao` — e
só isso. `TRANSICOES[aguardando_aprovacao]` contém `aprovada`, então
`rascunho → aguardando_aprovacao → aprovada` são dois passos legais na tabela de estados. O que
a rota **não** faz:

| Garantia | Rota `/aprovacao/{id}/aprovar` | Rota `/status` |
|---|---|---|
| `can_approve_quotes` (`_exigir_alcada`) | sim | **não** |
| Registro `AprovacaoCotacao` | sim | **não** |
| Conferência do `fingerprint` visto | sim | **não** |
| Genealogia da decisão | sim | **não** |
| `ws.avaliar()` — exceções e blockers | sim | **não** |

Autenticação está garantida (o `AuthMiddleware` cobre toda rota não pública), então não é buraco
aberto. É buraco de **autorização**: qualquer pessoa autenticada, `VENDEDOR_COMISSIONADO`
inclusive, chega a `aprovada` por aqui. E a Sessão 6 é explícita — *"aprovação aprova uma
CONFIGURAÇÃO, não uma cotação"*; um estado `aprovada` sem fingerprint não diz **o que** foi
aprovado.

Correlato de B-20 (`GET /logout` muda estado via GET): rota que muda estado sem o portão que o
estado pressupõe.

**Consequência prática.** `aprovada` é pré-requisito de `emitir`. Uma cotação pode chegar à
emissão sem que exceção comercial nenhuma tenha sido avaliada — ver C-NEW-10, que é exatamente
esse caso acontecendo.

**Não reabrir por conta própria.** Workflow é fase fechada; corrigir exige autorização explícita.
As opções aparentes, sem escolher nenhuma: exigir `can_approve_quotes` para o destino `aprovada`;
ou remover `aprovada` dos destinos alcançáveis por esta rota, deixando-o só para
`workflow_service.decidir`; ou fazer a rota delegar a `ws.avaliar()` antes de aceitar o destino.

</details>

### C-NEW-10 — Item com preço zero dentro de cotação aprovada (P0, ALTO) — **RESOLVIDO em 09/09/2026**

**A regra já existia.** `wf.blockers_do_item` tem `SEM_PRECO` desde a Sessão 6 e `ws.avaliar()`
sempre devolveu `pode_emitir=False` para esse item. Não faltava regra: faltava alguém
perguntar — e quem não perguntava era a rota do C-NEW-09.

**Correção.** Fechado o bypass, a pergunta passa a ser feita em cada porta. Somou-se defesa em
profundidade: o **PDF final exige `SnapshotEmissao`** da revisão, em vez de confiar no campo
`status`. Sair sem marca d'água afirma que existe documento emitido, e a prova disso é o
snapshot que `ws.emitir()` cria depois de revalidar tudo.

**A fronteira ficou explícita:** rascunho **aceita** o item sem preço — é assim que se monta
uma cotação; nada além de rascunho aceita. Forçar o pedido de aprovação também não resolve,
porque `solicitar_aprovacao` recusa abrir pedido sobre o que não é decisão de alçada. Preço
válido abaixo do recomendado continua sendo exceção normal, com aprovação e trilha.

**Provado por** `tests/test_emissao_item_invalido.py` (casos A a F). A cotação 21 não foi
alterada.

<details><summary>Registro original da investigação</summary>

Item 49 da `ANARA-2026-0021`:

```
Lençol plano 240x250 · 300 fios · 100% algodão   qtd 10
custo_unitario     R$  71,5255526059197     status_custo_item CONFIRMADO
preco_recomendado  R$ 153,44                status_fiscal     OK
preco_negociado    R$   0,00                modo_edicao       preco
faturamento/lucro  R$   0,00                valor_editado     0.0
```

Não é bloqueio de custo nem de fiscal: os dois estão resolvidos. O item entrou, o preço nunca
foi digitado, e `modo_edicao="preco"` com `valor_editado=0` formou uma linha de receita zero. A
cotação foi para `aprovada` e teve **PDF gerado seis vezes** com ele assim.

O `excecoes_do_item` **enxerga** o problema — devolve `MARGEM_ABAIXO_ALVO` para esse item, com
margem realizada 0% contra alvo de 18%. O que falhou não foi a detecção: foi ninguém ter
perguntado. É a consequência direta de C-NEW-09 — a rota que mudou o status não chama
`ws.avaliar()`.

**Duas perguntas em aberto, nenhuma decidida aqui:**

1. item com `preco_negociado = 0` deveria ser **blocker duro** (como `A_COTAR`) em vez de
   exceção comercial? Preço zero não é desconto — é ausência de preço;
2. o PDF deveria sair com uma linha de R$ 0,00? Hoje sai.

</details>

> **Pergunta que continua aberta:** o PDF de **prévia** segue exibindo a linha de R$ 0,00.
> Isso é deliberado — a prévia existe para montar a proposta e precisa mostrar o que ainda
> falta —, mas se a preferência for omitir a linha ou marcá-la, é decisão de produto.

### C-NEW-11 — Edredom de poliéster: rótulo 280 g do catálogo contra 180GSM da fonte escrita (P1, MÉDIO) — **NOVO em 13/09/2026**

**Decisão de fornecedor pendente. Nada foi alterado nos SKUs 340–348.**

O tarifário "Projeto Anastacio.xlsx" (aba `Nova Cotação 05.08.26`) traz a linha
**180GSM 100% fibras de poliéster** com estes brutos: 190×260 = R$ 469,30 · 285×265 = R$ 679,72
· 290×260 = R$ 678,60. São **exatamente** os brutos que o catálogo guarda nos SKUs 341, 346 e
348 sob o rótulo **280 g** — rótulo que veio de informação verbal/imagem de 03/09/2026
(§2.2.1), não de documento do fornecedor. A fonte de 12.08.26 que sustenta esses SKUs também não
declara gramatura para poliéster.

Mesmo preço, mesma medida, dois rótulos de gramatura. Uma das duas coisas é verdade:

1. a Daune tem duas linhas de poliéster (180 g e 280 g) com preço idêntico nessas medidas; ou
2. a informação de 03/09 era a linha de 180 g, e os nove SKUs de 280 g são um rótulo errado.

**O sistema não escolheu.** Os SKUs de 180 g (324, 327, 328) receberam a linha que o
fornecedor rotulou como 180GSM — é o que a evidência escrita diz. Os de 280 g ficaram como
estavam. Se a resposta for (2), os nove SKUs 340–348 precisam ser desativados ou refeitos,
e as seis medidas que só existem neles (180×250, 220×250, 230×260, 250×250, 260×260, 290×245)
passam a ser 180 g.

**Pergunta para a Daune:** existe uma linha de edredom de poliéster de 280 g? Se sim, com os
mesmos preços da de 180 g?

### C-NEW-12 — Protetor de colchão e Pillow Top: a fonte nova reespecifica construção e medidas (P1, MÉDIO) — **NOVO em 13/09/2026**

**Não aplicado. 20 linhas da fonte ficaram como CONFLICT.**

- **Protetor de colchão:** o catálogo (SKUs 254–257) descreve *Manta 120 grs impermeável* nas
  medidas 100/140/160/200 × 200. A fonte descreve *matelassado com alça* e *matelassado com
  slip*, nas medidas 100/160/180/193×203/200. Os preços 157,49 e 171,24 aparecem nos dois, mas
  em medidas **diferentes** (catálogo 140 e 160; fonte 160 e 180). Construção e grade mudaram.
- **Pillow Top:** os oito preços do catálogo (SKUs 258–265) reaparecem idênticos, mas a fonte
  declara 1,03/1,63/1,83/1,93/2,03 × 2,03 onde o catálogo tem 100/140/180/200 × 200. E há uma
  quinta medida por composição (1,93 e 2,03), com o valor de três casas R$ 1.103,203.

Coincidência de preço não é match de produto. Mapear por preço fundiria medidas que a regra
manda manter distintas. **Pergunta para a Daune:** as medidas atuais dos protetores e pillow
tops são as da aba nova? A linha "manta impermeável" continua existindo?

### C-NEW-03 — Região Passo Fundo-RS sem tarifa (P1, BAIXO)

A linha existe na tabela mas sem tarifa, mínimo ou prazo, e **nenhuma cidade da aba de cobertura
aponta para ela** (verificado na Sessão 0.1). **Regra conservadora aprovada:** `FRETE_A_COTAR`
para a região, e `FRETE_A_COTAR` para qualquer cidade fora da cobertura — **sem aproximar por
cidade ou região vizinha** sem regra autorizada (§24).

### C-NEW-04 — Volume/cubagem — **RESOLVIDO conceitualmente em 03/09** (era P0, ALTO)

O catálogo não tem volume em nenhum dos 339 SKUs, e a regra proíbe assumir que o peso real vence.
A decisão de 03/09 **não exige preencher volume nos 339 SKUs para o sistema entrar no ar**: cria
uma hierarquia de resolução do peso taxado.

| Prioridade | Fonte | Código da fonte |
|---|---|---|
| 1 | Volume unitário/embalado confiável no SKU ou na referência logística | `SKU_PACKING` |
| 2 | Volume total informado manualmente para o grupo logístico/shipment da cotação | `SHIPMENT_VOLUME` |
| 3 | Peso taxado informado/confirmado por Admin, quando a transportadora ou a operação já forneceu | `CARRIER_CONFIRMED_TAXABLE_WEIGHT` |
| — | Override administrativo pontual | `ADMIN_OVERRIDE` |
| nenhuma | — | **`FRETE_REVIEW_REQUIRED`** |

Regras que continuam valendo: **nunca** assumir silenciosamente que `peso_real > peso_cubado`; a
fonte usada fica registrada no item/cotação; todo override manual guarda **usuário, data/hora,
valor e observação/fonte**.

Para a vendedora, nada disso aparece: ela vê apenas **"Frete aguardando validação"** quando for o
caso.

Risco residual: enquanto o cadastro de volume não avançar, parte das cotações CIF dependerá de
volume do shipment ou de peso taxado confirmado. Deixa de ser bloqueio de go-live e passa a ser
carga operacional.

### C-NEW-05 — Cobertura da transportadora × alcance comercial (P1, MÉDIO)

A cobertura é SC, PR, SP e RS. As alíquotas interestaduais decididas na Q-04 incluem 21 UFs em
7% (Norte, Nordeste, Centro-Oeste, ES). Ou seja: o sistema saberá calcular o **imposto** para
essas UFs, mas **não terá frete** para elas. Toda venda CIF fora do Sul/Sudeste coberto cai em
`FRETE_A_COTAR`. Não é contradição de regra, é limite operacional que precisa estar visível.

---

## 5. Decisões de 03/09 incorporadas

| Decisão | Efeito no plano |
|---|---|
| **A** — Área administrativa atualizável é P1 | C-06 reclassificado de P2 para **P1**, antes do go-live. Nova Onda 5 dedicada |
| **B** — `can_manage_users` granular | Permissão booleana dentro do papel ADMIN; sem quinta área visual |
| **C** — ESTIMADO pode ir a PDF, mas `confirmation_pending` bloqueia PO/WON | Novo blocker, distinto do bloqueio de PDF |
| **D** — `KTC_SPECIAL_QUOTED` manual obrigatório na V1 | Novo entregável na Onda 2 (C-08) |
| **E** — Política de estimativa de roupões, buffer 5% | Formalizada na Onda 2 |
| **F** — Volume/cubagem obrigatórios | C-09 + hierarquia de 3 prioridades + `FRETE_REVIEW_REQUIRED`; ver C-NEW-04 |
| **G** — Grupos logísticos por origem | C-10; frete por grupo, somado; vendedor vê total |
| **H** — Preservar `BaseImportacao` e snapshots | Migration-ponte explícita, sem apagar base antiga |
| **I** — Relatório Daune antes da migração em massa | Gate obrigatório na Onda 2; item ambíguo vai para `REVIEW_REQUIRED` |

---

## 6. Testes que cristalizam regra legada (P0-09)

Revisão dos 173 testes atuais contra a Base Mestra:

| Teste | Problema | Ação |
|---|---|---|
| `test_fiscal.py::test_interestadual_contribuinte_e_4` | Afirma 4% para qualquer fornecedor | Reescrever: 4% só para importada; nacional 7%/12% |
| `test_fiscal.py` (paramétrico de carga final) | Correto, mas não cobre finalidade | Ampliar com a matriz contribuinte × finalidade |
| `test_calculadora.py::test_familia_sem_formula_nao_inventa` | Inclui `Pillow Case` como não calculável | **Feito em 17/09/2026**: fronha saiu da lista; a calculadora ("Produto personalizado") calcula pelo §18 com abas (0/2/3/4), flap e festonê, e o teste `test_fronha_calcula_pelo_paragrafo_18_com_abas_flap_e_festone` reproduz os backtests |
| `test_motor_comercial.py` | Não cobre CF/RV | Ampliar com frete e ADV/GRIS |
| `test_motor_comercial::test_condicao_nao_cadastrada_usa_regua_antiga_com_aviso` | **Protege a interpolação proibida** (B-15): afirma que `"30/60/90/120/150/180"` devolve 9,6% pela régua legada | **Reescrever na Onda 1** — a condição desconhecida tem de exigir cadastro ou override, não devolver número |
| `test_fiscal::test_cenario_desconhecido_cai_no_fallback_com_aviso` | Protege o fallback de 18% (B-06) | **Substituir na Onda 1** — cenário irresolvível vira `REVIEW_REQUIRED` |
| `test_fundacao::test_baseline_registra_o_estado_atual_com_os_bugs_conhecidos` | Afirma que MG e BA a contribuinte são 4% | **Reescrever junto com a Onda 1** — foi escrito para mudar, por desenho |
| Testes de valor (preço/margem) | Escritos com `float` | Reescrever com tolerância explícita após `Decimal` |
| Toda a suíte | Nenhum teste de papel/permissão/aprovação | Criar (Ondas 4 e 6) |

Nenhum teste atual está *errado de propósito*; eles refletem a base anterior. Todos serão
reconciliados com a Base Mestra na onda correspondente.

**Consequência que precisa estar dita:** a suíte verde **não** prova que o sistema está correto.
Três testes hoje passam justamente porque protegem comportamento que a regra aprovada condena
(B-01, B-06 e B-15). Nenhum teste precisa ser **removido**; nove são reescritos ou ampliados.


---

## 7. Fase 3A — política comercial canônica (16/09/2026)

Sessão que implementou no sistema oficial a decisão comercial de 16/09/2026: margem-alvo,
piso de autonomia da vendedora, comissão de formação, comissão variável da cotação,
comissão fixa e preço travado Daune. Migration **0018** (aditiva). Nenhuma cotação, item,
snapshot ou aprovação alterado — provado por digest tabela a tabela
(`relatorios/fase3a_estado_banco_antes.json` × `_depois.json`): só `margemregra` (21 regras
encerradas em 16/09 + 21 sucessoras), `premissa` (+2) e `auditlog` (+23) mudaram.

### C-NEW-13 — `pricing_service.margem_padrao` ignorava a vigência da regra de margem (P1, MÉDIO) — **RESOLVIDO em 16/09/2026**

`resolver_margem(regras, ..., ref=None)` tratava `ref=None` como "sem filtro de vigência", e
`margem_padrao` nunca passava `ref`. Consequência: uma regra encerrada pelo painel
(`admin_service.aplicar_margem` fecha `valid_to`) **continuava formando preço**, e a regra
nova só vencia se tivesse prioridade menor — o teste `test_margem_futura_so_vale_depois_da_data`
passava porque chamava o resolvedor direto, com `ref`. O CLAUDE.md afirmava "vigência futura
funciona" para `resolver_margem`; era verdade para a função e falso para o serviço.

**Correção:** `ref=None` passa a significar **hoje**; `SEM_VIGENCIA` é a sentinela explícita
para inspeção sem data. Empate de prioridade e especificidade desempata pela vigência mais
recente. Regressão: `tests/test_margens.py::test_margem_padrao_do_servico_resolve_por_hoje`.

### C-NEW-14 — `POST /configuracoes/margem` edita a regra de margem NO LUGAR (P1, MÉDIO) — **RESOLVIDO em 16/09/2026 (Fase 3B)**

**Resolução:** a rota passou a chamar `admin_service.preview_margem → aplicar_margem` — o
mesmo caminho versionado do fluxo administrativo: encerra a regra vigente (`valid_to`),
cria a sucessora com o **mesmo escopo, prioridade e faixa de fios**, herda piso/comissão de
formação/travamento, exige **fonte**, grava `AuditLog`, exige alçada econômica
(`exigir_economia_gerenciavel`). Regra já encerrada não é reeditável. De quebra,
`_mesmo_escopo` passou a considerar `min/max_thread_count`: versionar "Flat Sheet < 300TC"
não encerra mais a regra "≥ 300TC". Regressões:
`tests/test_fase3b_comercial.py::test_01_*`. Registro original abaixo, preservado.

`app/routers/configuracoes.py::salvar_margem` faz `regra.margem_pct = ...; session.commit()`
— reescreve a linha em vez de versionar, sem `AuditLog`, sem fonte, sem vigência. É o
oposto do que `admin_service.aplicar_margem` faz, e desde 16/09/2026 uma edição por ali
também deixaria `piso_pct`/`comissao_formacao_pct` incoerentes com a margem. **Fora do
escopo desta fase** (não é redesign, mas é rota legada da tela de configurações).
Recomendação: apontar a aba "Margens" para `/admin/margem/preview → aplicar` e remover a
rota. Enquanto isso, **não usar** `/configuracoes` para margem.

### OQ-01 — Base contratual da comissão (OPEN_QUESTION, comercial/financeiro)

O motor calcula a comissão como **percentual da receita comercial** (`faturamento` = preço
comercial × quantidade, com impostos dentro) — é a base do gross-up desde a planilha
original, e a Fase 3A a **preservou** para formar preço e para a **comissão estimada**
mostrada à vendedora. A documentação canônica e o SUPER PROMPT não fixam se a comissão
contratual da vendedora incide sobre receita bruta, receita líquida de impostos, valor
recebido ou outra base, nem quando ela é devida (emissão, faturamento, recebimento).

**Não foi inventada reconciliação.** O sistema documenta a comissão como **ESTIMATIVA DE
PRICING** (`resumo_comercial.comissao_estimada_*`, payload da negociação) e não implementa
liquidação. A comissão realizada/pagável é assunto do financeiro e de uma fase própria.
Decisão pendente: diretoria + financeiro.

### Notas da fase

- **Offline V2 = STALE.** O cotador offline V2 (`~/ANARA_COTADOR_WORKSPACE/`, 15/09/2026)
  usa monkeypatch "margem fixa 15% + comissão 10%", que **não é** a política canônica. Não
  distribuir como fonte de preço após 16/09/2026. A próxima versão offline deve ser regerada a
  partir do sistema oficial (handoff novo). Nada no workspace foi tocado.
- **`Produto.margem_padrao_pct` é cache informativo** e envelheceu com a política (349 produtos
  guardam 12–18%/14%). Não foi reescrito (não é premissa; não é usado para formar preço). A
  busca de produtos (`/produtos/buscar`) passou a devolver a margem **resolvida** da regra
  vigente, para a tela não oferecer o cache como default.
- **`preco_base` do catálogo** continua o formado antes da política; é referência de catálogo,
  não forma preço de item, e é recalculado por ação explícita (importação/catálogo).
- Os **17 rascunhos reais** passam a ser apontados por `premissas_desatualizadas` como
  formados com a política anterior: continuam com seus números; reprecificar é o botão
  "atualizar premissas" — ou emitir com premissa antiga, que exige alçada (`PREMISSA_VELHA`).


---

## 8. Fase 3B — CRM comercial simples, Cliente 360 e pós-venda (16/09/2026)

Sem alteração econômica: pricing, fiscal, CNET, PIS/COFINS, política 3A, fingerprint,
snapshot e workflow técnico da cotação intocados (provado por `test_31_…_35`). Migration
**0019** aditiva. Banco real: 0 oportunidades na entrada; **nenhum backfill** — o relatório
read-only `relatorios/fase3b_proposta_backfill_cotacoes.md` classifica as 21 cotações
históricas (ALTA/MEDIA/BAIXA) e a decisão é humana.

- **"Venda" = rótulo de interface de `Oportunidade`**; três etapas abertas
  (RASCUNHO/ENVIADO/NEGOCIACAO); Vendido/Perdido = `status`. Etapas do funil anterior
  ficam `ETAPAS_LEGADAS`, só leitura.
- **Cotação nova exige venda** (service `exigir_venda`); `oportunidade_id` continua NULO nas
  21 históricas — acessíveis em Cotações com "Sem venda vinculada (legado)".
- **Pós-venda V1** sem pagamento parcial; ATRASADO é marcado por alçada financeira.
- **PDF cliente** e **frete nacional**: fora desta fase (PDF agendado para a 3C; frete
  continua pausado — C-NEW-01/02/03/05/06/07/08 abertos).
- **Offline V2** continua STALE, intocado.


## 9. Fase 3C — redesign comercial, Dashboard OWNER/ADMIN e PDF cliente (16/09/2026)

**Escopo cumprido:** shell/login/menus por papel; UX completa da vendedora (Vendas lista +
quadro, status inline, venda individual com timeline e registro rápido, Cliente 360,
cotações e negociação reativa sobre os endpoints canônicos, painel sticky, pós-venda
visual); Dashboard exclusivo OWNER/ADMIN; Produtos como catálogo comercial; PDF cliente
redesenhado e blindado; responsividade; testes funcionais (`tests/test_fase3c_ux_pdf.py`) e
inspeção visual Playwright (`scripts/visual_3c.py`). Migration aditiva `0020`
(`cotacao.observacao_cliente`), com backup antes.

**Nada reaberto:** pricing, fiscal, CNET, PIS/COFINS, workflow, SnapshotEmissao,
approval/fingerprint, gates, política comercial 3A, CRM/pós-venda 3B. O frontend não
duplica fórmula: chama `negociacao/preview`, `negociacao`, `itens`, `painel` e exibe.

**Confidencialidade verificada:** HTML da vendedora (vendas, venda, clientes, cliente 360,
cotações, cotação, produtos) sem custo/CNET/EXW/lucro/piso/markup/margem/memória; payload
JSON da negociação sem confidenciais; PDF com allowlist explícita, varredura de códigos e
teste que extrai o texto e falha se aparecer economia, fornecedor, código operacional,
recomendado/desconto ou observação interna. PDF final prova o snapshot: alteração posterior
do item não muda o documento emitido.

**Testes de interface ajustados (não econômicos):** ordem do menu (3C), rótulo "Legado —
sem venda vinculada", dois campos comerciais novos no item da negociação
(`desconto_linha_pct`, `preco_travado`).

**Continuam abertos, fora da fase:** C-NEW-01/02/03/05/06/07/08 (frete), C-NEW-11/12,
OQ-01, cadastro fiscal pendente, offline V3 (STALE), produção/deploy.

**Limitações declaradas da 3C:** o quadro e o status inline recarregam a página depois de
gravar para trazer a timeline (a gravação em si é sem reload); a cotação no celular rola a
tabela de produtos horizontalmente; comparação com o ano anterior no gráfico só aparece com
dado real (hoje não há); o gerador legado `gerar_cotacao.py` fica no repositório sem uso.

## 10. Preparação para produção (17/09/2026)

### C-NEW-15 — SQLite nunca aplicou chaves estrangeiras (P1, MÉDIO) — **MITIGADO**

`PRAGMA foreign_keys` fica desligado por padrão, então itens com `cotacao_id=0` e
`auditlog.ator_id` sem usuário passavam. No PostgreSQL a FK vale. Dados reais conferidos: 0
órfãos em todas as FKs (`scripts/migrar_sqlite_para_postgres.py`). Testes ajustados
(`cotacao_de_apoio`, atores persistidos). Não se ligou o PRAGMA no SQLite local de propósito:
mudaria o comportamento do banco em uso sem migration.

### C-NEW-16 — Migrations com booleano como inteiro (P0 para Postgres, ALTO) — **RESOLVIDO**

0002, 0003, 0005 e 0007 tinham `ativo = 1`, `confiavel = 0`, `interna_inclui_fcp = 1` em SQL
cru. `alembic upgrade head` num Postgres vazio quebrava em 0003. Agora vão como parâmetro
(`:sim` → `True`); no SQLite o efeito é idêntico. `tests/test_producao_deploy.py` varre.

### C-NEW-17 — Enums nativos congelados no Postgres (P0 para Postgres, ALTO) — **RESOLVIDO**

`0001` declarava `sa.Enum(name=...)`, que no Postgres cria tipo nativo com a lista de
2026-09-01 — `CostMethod` ganhou seis membros e `StatusCotacao` o workflow desde então, sem
migration, porque no SQLite era `VARCHAR`. Migration `0022` (Postgres → `VARCHAR(64)`; SQLite
no-op) e modelos com `native_enum=False`.

### C-NEW-18 — `scripts/smoke_test.py` estava desatualizado (P2, BAIXO) — **RESOLVIDO**

Criava oportunidade com etapa legada `COTACAO` (recusada desde a Fase 3B) e esperava 200
onde `/emitir` e `/enviar` passaram a redirecionar (Fase 3C). Corrigido; `smoke_producao.py`
reaproveita os fluxos.

### C-NEW-19 — Fontes licenciadas servidas publicamente (P2, MÉDIO) — **MITIGADO**

`app/static/fonts/*.ttf` (Didot, Futura) saíam por `/static` sem autenticação. Agora
`/static/fonts/` responde 404 a anônimo; a tela de login usa a pilha de fallback. Publicação
em CDN continua fora de questão.

### OQ-02 — `referencia/` no repositório publicado (OPEN_QUESTION, comercial)

Não é lida em runtime; contém tabelas de preço de fornecedor, PDFs de cotação e orçamento.
Pode ficar fora do repositório de produção com impacto em um script de importação e um
teste (que já pula sem os arquivos). Decisão do dono antes do push — comandos em
`DEPLOY_PRODUCTION.md`.

### D-01 — 17 cotações herdadas apagadas pela plataforma em 17/09/2026 11:25:54 (DADOS, decisão do dono)

Durante a preparação para produção, o `data/server.log` do servidor local (127.0.0.1:8420)
registra `POST /cotacoes/lote` → `GET /cotacoes?arquivadas=sim&apagadas=17`, pela conta
OWNER (a trilha de auditoria mostra a mesma conta ativa das 10:54 às 11:14). Foram apagadas de
vez as cotações **1–16 e 19** (todas arquivadas antes, como a regra exige); o banco passou de
23 para 6 cotações e o `sha256` saiu de `5b0c5cc0…` para `a5af8aee…`. **Nenhum processo da
preparação escreveu no banco real** — os smokes provam o isolamento e não chamam `/lote`.

Backup automático feito pela própria exclusão: `data/backups/anara.db.exclusao-lote-20260917-112554`
(23 cotações), copiado para `~/Anara-Cotacao-Backups/anara_pre_exclusao_lote_owner_20260917-112554.db`.

Consequência: quatro guardiões do conjunto herdado (`tests/legado.py`, baseline Fase 0)
**falham**, também no código anterior (provado com `git stash`):
`test_workflow_sessao6::test_historico_legado_nao_e_falsificado`,
`test_crm_sessao7::test_p0_historico_legado_nao_ganha_oportunidade_ficticia`,
`test_fundacao::test_cotacoes_e_itens_historicos_nao_mudaram` e
`test_fundacao::test_bases_de_importacao_preservadas`. O backup pré-exclusão tem sha256 igual
ao baseline desta sessão (`5b0c5cc0…`): nada mais mudou no banco. Decisão do dono,
não do código: (a) restaurar o backup se a exclusão não foi intencional (`BACKUP.md`), ou
(b) aposentar/atualizar os guardiões do conjunto herdado. Não foi resolvido em silêncio.


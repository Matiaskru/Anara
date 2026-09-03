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
| `test_calculadora.py::test_familia_sem_formula_nao_inventa` | Inclui `Pillow Case` como não calculável | Remover fronha da lista; manter Fitted |
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

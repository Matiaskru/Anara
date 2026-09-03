# AUDIT_ANARA_MASTER

Auditoria read-only exigida pela Fase 1 do SUPER PROMPT v2.
Atualizada em 03/09/2026 com as decisões do usuário, a tabela da transportadora e as
resoluções de ICMS do frete, GRIS e volume/cubagem.

**Nenhuma linha de código foi alterada.**

Base auditada: `~/Anara-Cotacao` — 8.827 linhas Python, 56 módulos, 14 templates, 173 testes
passando, 339 SKUs ativos, 3 fornecedores, 17 cotações, 45 itens de cotação.

---

## 1. Matriz de auditoria

Legenda de prioridade: **P0** matemática/fiscal/dados · **P1** segurança/multiusuário/operação ·
**P2** melhoria.

Nomenclatura oficial do projeto: **Fase 0 — Fundação** (preparatória) e **Ondas de
Implementação 1 a 8**. São 1 fase + 8 ondas.

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
| **B-14** | §27 — origem fiscal | `models.Cotacao.estado_origem`, `BaseImportacao.origem_uf`, `premissa.catalogo_origem` | Três origens fiscais convivem: modelo default "Santa Catarina", bases legadas "SC", premissa de catálogo "São Paulo". 14 das 18 cotações estão gravadas com Santa Catarina, e `RegraFiscalVenda` só tem regra explícita para SP→SP | ALTO | Onda 1, junto com B-01/B-02. **Descoberto na Fase 0** |

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
default cai na resolução por `EstadoFiscal` em vez da regra decidida. Não foi tocado na
Fase 0 — é matéria fiscal, portanto Onda 1.

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

### 2.2. Fonte Daune — CONFIRMADA

`Linha Hotelaria - Daune - 12.08.26.xlsx` existe, 28 itens, com aba "Informações" onde o
fornecedor declara por escrito: *"todos os impostos estão inclusos, e o crédito de ICMS é 12%"*,
*"30 DD após a emissão da nota"*, *"frete CIF São Paulo"*. Fecha a questão do crédito e confirma
o modelo do §21.

### 2.3. Tabela da transportadora — RECEBIDA E LIDA

`Cópia de Tabela Industria Quimica Anastacio SC 2026 02 (3).xlsx`

**Transportadora:** TRANSAL TRANSPORTADORA SALVAN LTDA · CNPJ 00214121000993 ·
Av. Radial Oeste 563-293, Espinheiros, **Itajaí-SC**, CEP 88311740.

**Origem da tabela:** `REGIÃO ITAJAÍ - SC`. O documento da transportadora **já traz Itajaí como
origem** — não é apenas override da Anara. E `ILHOTA` aparece como cidade atendida pela filial
Itajaí, o que reconcilia a menção histórica a Ilhota-SC: Ilhota está dentro da região Itajaí.

**Faixas (R$/tonelada sobre peso taxado):**

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

São **9 regiões/filiais cadastradas, 8 com tarifa preenchida**. Passo Fundo-RS consta sem
tarifa, mínimo ou prazo → as cidades dependentes dessa região ficam `FRETE_A_COTAR`.

ADV 0,20% · GRIS 0,10% · Pedágio R$ 0,0536/kg — uniformes em todas as regiões.

**Cobertura:** ~200 cidades em 9 filiais. **São Paulo capital está na filial Guarulhos**, o que
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
| Q-03 | 200TC/230TC na tabela de materiais | Mantida a precedência do §15: existência de US$/m² **não** habilita cálculo automático |
| Q-04 | UFs por faixa interestadual nacional | **7%:** AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, PA, PB, PE, PI, RN, RO, RR, SE, TO. **12%:** MG, PR, RJ, RS, SC. SP→SP interna (18%). Importada KTC segue 4% |
| Q-05 | Finalidade padrão | `USO_CONSUMO` para hotel, editável por cliente/unidade, sobrescrevível na cotação, snapshotada. Enum: REVENDA, INDUSTRIALIZACAO, USO_CONSUMO, ATIVO_IMOBILIZADO. **Consumidor final é derivado**, não é valor do enum |
| Q-06 | Cubagem e faixas | Confirmados: 300 kg/m³, faixas 1–7.000 e >7.000, tarifas agora conhecidas |
| Q-07 | Caso Nanai | **Não** é golden master. Preservar o arquivo, recalcular após as Ondas 1 a 3, criar `NANAI_MASTER_V2` e documentar cada diferença item a item |
| Q-08 | DIFAL em uso/consumo | **Contribuinte → destinatário recolhe** (não desconta da margem da Anara). **Não contribuinte → remetente recolhe** (entra no waterfall). Regra default no código; exceções por UF configuráveis |

---

## 4. NOVAS contradições encontradas

### C-NEW-01 — ICMS do frete — **RESOLVIDO em 03/09** (era P0, ALTO)

**Regra vigente:** a confirmação escrita e posterior da transportadora — *"ICMS já está incluso
no cadastro das tabelas"* — é a regra operacional. Para a versão atual da tabela TRANSAL:

```
icms_incluso = true
Frete Total = frete-peso final + ADV + GRIS + pedágio + adicionais aplicáveis
```

**Sem** gross-up de 12%. **Sem** divisão por 0,88.

**Inconsistência do documento, registrada e não aplicada:** o exemplo de cálculo nas células
G1:G5 da aba `Tabela de Frete` faz gross-up de 12%:

```
frete 299,00 + ADV 26,70 + pedágio 26,80 = 352,50
352,50 ÷ (1 − 12%)                       = 400,5681818182
célula "Frete Total" (G5)                 = 400,5681818181818   ← confere em 10 casas
```

Esse exemplo **não prevalece** sobre a confirmação escrita posterior. Fica documentado como
inconsistência do documento/exemplo, para que ninguém no futuro "corrija" o motor com base nele.

**O modelo preserva `icms_incluso` e `icms_pct`** por tabela/transportadora, porque tabelas
futuras podem ter tratamento diferente.

### C-NEW-02 — GRIS — **RESOLVIDO em 03/09** (era P1, MÉDIO)

**Regra vigente**, confirmada pela transportadora:

```
ADV  = valor total da NF × 0,002   (0,20%)
GRIS = valor total da NF × 0,001   (0,10%)
```

Os dois se aplicam. O exemplo da planilha, que soma ADV e pedágio mas **omite o GRIS** (seriam
R$ 13,35 sobre a NF de R$ 13.350), fica documentado como exemplo incompleto — não como regra.

### C-NEW-03 — Região Passo Fundo-RS sem tarifa (P1, BAIXO)

A linha existe na tabela mas sem tarifa, mínimo ou prazo. Tratamento proposto:
`FRETE_A_COTAR` para as cidades dessa região, sem aproximar por região vizinha (§24).

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
| Testes de valor (preço/margem) | Escritos com `float` | Reescrever com tolerância explícita após `Decimal` |
| Toda a suíte | Nenhum teste de papel/permissão/aprovação | Criar (Ondas 4 e 6) |

Nenhum teste atual está *errado de propósito*; eles refletem a base anterior. Todos serão
reconciliados com a Base Mestra na onda correspondente.

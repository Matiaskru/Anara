# ANARA — Handoff de sessão · 09/09/2026

Documento de continuidade. Escrito para ser lido **inteiro e sozinho**: uma sessão nova deve
conseguir retomar o trabalho a partir daqui sem depender do histórico de conversa anterior.

Nada foi implementado na sessão que produziu este arquivo depois do último checkpoint — ele é
**registro de estado e de autorização**, não plano de execução.

> **Regra de leitura.** Onde este documento e um documento antigo discordarem sobre **premissa
> econômica**, o `ANARA_PRICING_VALIDATION_MASTER.md` e este handoff prevalecem. Onde qualquer
> documento discordar do **código e do banco** sobre comportamento real, o código e o banco
> prevalecem. Verifique antes de confiar.

---

## A. O que é o projeto

**ANARA** é a plataforma comercial e de precificação da Anara, que vende **enxoval hoteleiro**
(cama, banho e complementos) para hotéis, hospitais e redes.

O sistema faz quatro coisas, nesta ordem de importância:

1. **Forma o preço de venda de forma determinística e auditável** — do custo de fábrica ao
   preço final, com fiscal, financeiro, comissão, frete e margem explicitados.
2. **Governa premissas econômicas com versão e vigência** — trocar o câmbio não reescreve o
   passado, e cotação emitida não muda.
3. **Roda o ciclo comercial** — CRM, oportunidades, pipeline, cotação, aprovação, emissão, PDF.
4. **Responde "de onde veio este número?"** para qualquer valor em qualquer tela.

### Fornecedores

| Fornecedor | Origem | Natureza | Situação de custo |
|---|---|---|---|
| **KTC / Kazareen Textile Company** | Egito | Importado, **EXW** | Motor industrial próprio: tecido, CMT, waste, encolhimento, 2ª qualidade, margem KTC, depois nacionalização |
| **Daune** | Brasil (embarca de **São Paulo**) | Nacional | Preço bruto do fornecedor → créditos de compra → CNET |
| **Decor Tricot** | Brasil (embarca de **São Paulo**) | Nacional | Idem Daune |

### Stack

Python 3.12 · FastAPI · SQLModel/SQLAlchemy · SQLite (Postgres previsto para produção) ·
Jinja2 · ReportLab · openpyxl · pdfplumber · Alembic · pytest · argon2-cffi · itsdangerous.

O núcleo econômico é **`Decimal`**, com política única em `app/dinheiro.py`. Os motores puros
(`ktc_engine`, `nationalization`, `pricing_engine`, `fiscal_rules`, `margin_rules`,
`payment_terms`, `peso`, `frete_engine`) **não importam FastAPI, Jinja nem banco**.

---

## B. Estado técnico atual

### Verificado no fechamento desta sessão

| | |
|---|---|
| **HEAD** | `2c45f31` — "Product cleanup: navegação, entrada de dados, erros e confiabilidade do custo" |
| **Branch** | `main` |
| **Remote** | **nenhum** (`git remote -v` vazio) — e assim deve permanecer |
| **Alembic** | `0017` no banco **e** no código — sincronizados, nenhuma migration pendente |
| **Suíte** | **900 testes, 900 passando**, 62,49 s |
| **Servidor** | no ar em `http://127.0.0.1:8420` — `/health` devolve `{"status":"ok","database":"ok"}` |
| **`.env`** | **não versionado** (`.gitignore:23`) — confirmado |

### `git status` — trabalho não commitado

```
 M app/pricing_service.py
 M relatorios/regressao_sessao3b.json
?? ANARA_PRICING_VALIDATION_MASTER.md
?? tests/test_memoria_serializavel.py
```

O que cada um é:

- **`app/pricing_service.py`** — a correção da calculadora. `Decimal` cru escapava para dentro
  da memória do preço pelo espalhamento do `contexto`: `contexto["fiscal"]` passava por
  `como_dict()`, mas `icms_pct`, `aliquota_interna_destino`, `fcp_pct` e `encargo_pct` vinham do
  nível de cima e escapavam da conversão. Novo helper `_para_json()` na fronteira de saída.
- **`tests/test_memoria_serializavel.py`** — 5 testes de regressão do defeito acima.
- **`ANARA_PRICING_VALIDATION_MASTER.md`** — 1.557 linhas, 72 KB. Documento de metodologia de
  precificação para validação com os sócios. Ainda **não rastreado pelo Git**.
- **`relatorios/regressao_sessao3b.json`** — mudou **uma linha**: `linhas_depois` de 19 para 20.
  É o script de baseline tendo registrado a cotação 20, criada manualmente na tela. **Não é
  mudança de código nem de regra.**

> O defeito da calculadora era **anterior** ao product cleanup — foi reproduzido em `6600f68`
> num worktree isolado antes de ser corrigido. Não é regressão do cleanup.

### Banco de produção (`data/anara.db`)

| Entidade | Quantidade |
|---|---|
| Cotações | 20 (a 20 é rascunho vazio, criada na tela em 09/09) |
| Itens de cotação | 48 |
| Produtos ativos | 340 |
| Usuários | 1 (OWNER) |
| Clientes | 1 |
| Oportunidades · contatos · atividades · aprovações · snapshots | 0 |
| Registros de auditoria | 2 |

**Não há dado de teste no banco de produção.** O smoke E2E que cria clientes, cotações,
oportunidades e aprovações **nunca foi rodado contra `data/anara.db`** — e continua proibido de
rodar contra ele.

**Backups:** 34 arquivos em `data/backups/`, 57 MB. **Nenhum backup real foi removido.**

### Premissas vigentes (`valid_to IS NULL`)

| id | Chave | Valor | Unidade | Vigente desde |
|---|---|---|---|---|
| 21 | `fx_usd_brl` | **5,19** | BRL/USD | 08/09/2026 |
| 2 | `frete_int_usd_kg` | 0,516 | USD/kg | 28/08/2026 |
| 5 | `outras_desp_usd_un` | 0,2487532709 | USD/un | 28/08/2026 |
| 6 | `pis_cofins_pct` | **0,0759** | % | 28/08/2026 |
| 7 | `icms_fallback_pct` | 0,18 | % | 28/08/2026 |
| 8 | `validade_dias` | 5 | dias | 28/08/2026 |
| 12 / 13 | `freshness_fresh_dias` / `freshness_aging_dias` | 30 / 60 | dias | 28/08/2026 |

> **`pis_cofins_pct = 0,0759` é o valor hoje sabidamente ERRADO como regra fixa.** Ver seção K.

---

## C. Documentos canônicos

Todos na raiz do projeto.

| Documento | O que é | Rastreado? |
|---|---|---|
| **`ANARA_PRICING_VALIDATION_MASTER.md`** | Metodologia de precificação ponta a ponta, para validação com os sócios. Números, premissas, fórmulas, evidências e lacunas | **não** (`??`) |
| **`SYSTEM_AS_BUILT.md`** | As-built em 23 seções: o que o sistema **é**, não o que se pretendia. 75 KB | sim (`2c45f31`) |
| **`PRODUCT_CLEANUP_REPORT.md`** | O que o Product Cleanup fechou e por quê | sim |
| **`PILOT_READINESS.md`** | Condições de operação do piloto: o que confiar, o que não confiar, checklist | sim |
| **`CLAUDE.md`** | Instruções operacionais permanentes e armadilhas de cálculo | sim |
| **`AUDIT_ANARA_MASTER.md`** | Matriz de bugs e pendências com IDs (B-xx, C-NEW-xx) | sim |
| **`ANARA_EXECUTION_STATE.md`** | Estado de execução e o que está autorizado | sim |
| `IMPLEMENTATION_PLAN_ANARA.md`, `BACKUP.md`, `SESSION_3B_HANDOFF.md`, `PLANO_EVOLUCAO.md`, `PLANO_EXECUCAO_ANARA_v2.md` | Histórico de planejamento | sim |
| `referencia/SUPER_PROMPT_ANARA_v2.txt` | Fonte de verdade **de negócio** | sim |

### Documentos-fonte externos (fora do repositório)

| Arquivo | O que prova |
|---|---|
| `~/Downloads/Indústria Química Anastacio PI 31-08-2026.pdf` | **Golden master.** Proforma Invoice real da KTC, **Delivery Terms EXW**. Base do backtest do motor |
| `~/Downloads/KTC_Pricing_Master_Simple (2).xlsx` | Planilha industrial da KTC. Abas: *KTC Pricing Model*, *KTC Actual Pricing Model*, **Towels Costing**, *Materials - CMT Prices* |
| `Fator Cálculo Exclusão ICMS .xlsx` (contabilidade Química Anastacio) | Origem da correção de PIS/COFINS da seção K |
| `~/Downloads/Samples Prices.pdf`, `~/Downloads/Orçamento Hospital Albert Einstein Anara-Anastacio julho 26.xlsx` | Localizados, **não** integralmente processados |

---

## D. Hierarquia de verdade

Quando duas fontes discordarem, esta é a ordem — **de cima para baixo**:

| # | Fonte | Vale para |
|---|---|---|
| **1** | **Código e banco** | **Comportamento real.** O que o sistema faz é o que o código faz. Documento nenhum sobrepõe execução |
| 2 | `ANARA_PRICING_VALIDATION_MASTER.md` | Números, premissas e metodologia de precificação |
| 3 | `SYSTEM_AS_BUILT.md` | Arquitetura e estrutura |
| 4 | `PRODUCT_CLEANUP_REPORT.md` | Fronteiras fechadas no cleanup |
| 5 | `PILOT_READINESS.md` | Condições de operação do piloto |
| 6 | Demais documentos | Contexto e histórico |

Duas consequências práticas:

- **Documento antigo em conflito com o Pricing Validation Master sobre premissa econômica NÃO
  está automaticamente certo.** Não assuma que o mais antigo é o mais verdadeiro. O Master é
  posterior e foi construído contra evidência documental.
- **Nem o Master sobrepõe o código.** Se o Master descreve um comportamento e o código faz
  outro, isso é um **achado**, não uma licença para "corrigir" a documentação em silêncio.
  Registre a divergência e pergunte.

---

## E. Fases fechadas — não reabrir por melhoria marginal

Estas fases foram executadas, testadas e aprovadas. **Elas não se reabrem porque algo pode
ficar melhor.**

| Fase | Escopo |
|---|---|
| Fase 0 · Sessão 0.1 | Fundação |
| Sessão 1 | Fiscal por item, DIFAL, condições de pagamento |
| Sessão 2 | Custo versionado por SKU, Daune, fronha, edredom 280 g |
| Sessão 3A | Frete comercial TRANSAL, grupos logísticos, CF/RV no waterfall |
| Sessão 3B | `Decimal`, arredondamento, reconciliação monetária |
| Sessão 4 | Segurança, papéis, confidencialidade |
| Sessão 5 | Administração de premissas, versionamento, preview→apply |
| Sessão 6 | Workflow comercial e aprovação por fingerprint |
| Sessão 7 | CRM, oportunidades, pipeline, atividades |
| Sessão 8 | Relatórios, saúde operacional, runtime, prontidão para o piloto |
| **Product Cleanup — Lotes A, B e C** | UX operacional, navegação, entrada de dados, erros, confiabilidade do custo |
| **Pricing Validation Master** | Documentação da metodologia de precificação |
| **Custos KTC · Daune · frete · margens · comissão** | Economia congelada |

### Os quatro únicos motivos para reabrir qualquer uma delas

1. **Erro que muda dinheiro** — preço, margem, custo ou imposto saem diferentes do correto.
2. **Vulnerabilidade de segurança real**, demonstrável.
3. **Risco de corrupção ou perda de dado.**
4. **Bloqueador operacional real** — alguém não consegue trabalhar.

"Ficaria mais elegante", "o nome poderia ser melhor", "dá para simplificar" **não estão nesta
lista**. Se a única justificativa é qualidade de código, a resposta é não.

---

## F. Roadmap

A ordem é deliberada. Cada etapa depende da anterior estar fechada.

```
1. LOTE C ────────────────────── CONCLUÍDO
2. PRICING VALIDATION MASTER ─── CONCLUÍDO (documento escrito, ainda não commitado)
3. POWERPOINT ────────────────── PRÓXIMO — apresentação da metodologia
4. VALIDAÇÃO COM OS SÓCIOS ───── decisão humana sobre as premissas
5. IMPLEMENTAR SÓ O APROVADO ─── nenhuma mudança econômica sem passar por (4)
6. PILOTO ────────────────────── 3 a 4 semanas de uso real
7. IA ────────────────────────── depois de tudo acima, e nunca no cálculo
```

**A etapa 5 é a que mais importa.** Mudança econômica que não passou pela validação com os
sócios não entra — mesmo que pareça obviamente certa. A exceção já concedida está na seção L.

---

## G. Principais invariantes

Estas regras não se negociam. Quebrar qualquer uma delas é defeito, não escolha de estilo.

### Cálculo

- **O cálculo é determinístico.** Motor puro, sem framework, sem banco, sem UI.
  **IA não participa da matemática, nem agora nem depois.** O motor determinístico é a fonte de
  verdade do preço, permanentemente.
- **Nunca inventar premissa econômica ou fiscal.** Faltou dado, o estado é `A_COTAR` ou
  `REVIEW_REQUIRED` — **nunca** um número plausível. Fallback silencioso é proibido.
- **Waste divide:** `consumo / (1 − waste)`. Nunca `× (1 + waste)`.
- **Margem KTC é sobre o preço final:** `custo / (1 − 0,15)`. Não é `custo × 1,15`.
- O **"II 1%"** da planilha KTC é **perda de 2ª qualidade**, não Imposto de Importação.
- **Taxa por kg de toalha já é EXW final** — não aplicar CMT, 2ª qualidade nem margem por cima.
- **Peso real da KTC nunca** é substituído por estimativa.
- **Margem líquida ≠ markup. Margem KTC ≠ margem Anara. `margem_alvo` ≠ `margem_liquida`.**

### Dinheiro

- **Nunca `Decimal(float)`** — use `D()`, que converte pela representação textual.
- **Precisão interna de 34 dígitos.** Nada é quantizado no meio da cadeia.
- **Dinheiro comercial: 2 casas, `ROUND_HALF_UP`** via `dinheiro()`. **Nunca `round()`.**
- **A quantização acontece uma vez**, quando o preço vira preço; os componentes são recompostos
  sobre o preço já arredondado.
- **Lucro é resíduo.** **Rateio pelo maior resto** (`ratear_centavos`).
- **Custo NET não é quantizado** — é custo interno.
- **As colunas continuam `REAL`, e isso foi medido.** `Numeric(18,2)` no SQLite trunca na
  leitura e reescreveria cotações emitidas. **Não criar migration `Numeric` sem reabrir a
  medição.**

### Fiscal

- **Origem fiscal é atributo da operação/NF, não do fornecedor.** Origem logística ≠ origem
  fiscal.
- **Contribuinte não se infere pelo estado**, e contribuinte ≠ consumidor final.
- **Carga final do DIFAL entra como está** — não recalcular por base simples/dupla/FEM.
- **Condição de pagamento desconhecida não se interpola.**

### Histórico e versionamento

- **Cotação emitida não muda quando a premissa muda.** Snapshot e pinos congelados.
- **Premissa nova fecha a anterior (`valid_to`)** — não sobrescreve. Banco nunca é resetado.
- **A cotação fica presa à versão EXATA** — guardar o valor protege o dinheiro; guardar o **id**
  protege a genealogia.
- **NO_OP ≠ RECONFIRMAÇÃO.** Mesmo preço com fonte nova **é** versão nova.
- **Precificação nova sempre usa a premissa vigente** — via `ps.custo_para_precificar()`.
  `Produto.custo_unitario` é persistido e **não** se recalcula sozinho.

### Acesso

- **A autorização é do backend.** Esconder campo no HTML não é autorização.
- **Vendedor não vê custo, EXW, CNET, margem, lucro, markup, impostos detalhados nem
  premissas** — na **resposta da API**, não só na tela.
- **A lista é de permissão, não de bloqueio** (`CAMPOS_ITEM_COMERCIAL`).
- **`can_approve_quotes` é permissão própria.** `can_manage_economics` **não** autoriza desconto.
- **O PDF comercial não leva custo, CNET, margem, lucro, markup, comissão nem fornecedor.**

### Processo

- **Uma etapa por autorização.** Terminou a etapa autorizada, **pare**.
- Bug corrigido ganha teste de regressão.
- Discrepância nova vira **ID novo** no `AUDIT_ANARA_MASTER.md`. Nunca resolver em silêncio.

---

## H. Estado do pricing

### O motor está certo. O driver de tecido está velho.

O backtest contra a **PI da Indústria Química Anastacio de 31/08/2026** (EXW confirmado no
próprio documento) resolveu, para cada peça, o preço de material **implícito** que reproduziria
o EXW real — mantendo waste, encolhimento, CMT, 2ª qualidade e margem KTC nos valores vigentes.

| Construção | Cadastrado | Implícito | Dispersão |
|---|---|---|---|
| 250TC Sateen CVC 70/30 — lençóis | US$ 1,25/m² | **1,1920** | 1,00% |
| 250TC — Duvet Cover | — | **1,1905** | 0,58% |
| 300TC Sateen 100% Cotton — lençóis | US$ 1,40/m² | **1,3443** | 0,75% |
| 300TC — Duvet Cover | — | **1,3422** | 0,50% |

**Doze peças de geometria diferente convergindo para o mesmo número** é a prova: a estrutura do
motor reproduz a fábrica. O que está desatualizado é o **preço do tecido cadastrado** — cerca de
**4% acima** do implícito, em toda a linha de cama.

Isso confirma a hipótese de ~1,20 e ~1,35 levantada antes do teste.

> **O ajuste do preço do tecido NÃO foi feito.** É mudança econômica e depende da validação com
> os sócios (roadmap, etapa 4→5).

### Toalhas (terry): motor confirmado

`peso_kg = L × C × GSM / 10.000.000` · `EXW = peso × price_usd_kg`.

As **7 taxas** da PI batem **exatamente** com o cadastro. Bath Towel 8,50 aparece tanto em 90/10
quanto em 100% algodão: **a composição não separa a taxa**. Hand Towel 8,98/9,01, Bath Mat 9,00,
Pool stripe 14,00.

**Hand Towel a US$ 9,00/kg está CONFIRMADO pela PI de 31/08/2026** — explicitamente **não** deve
ser marcado REVALIDAR.

**Validação da calculadora, ponta a ponta:** Hand Towel 50×85, 650 g/m², 100% algodão →
EXW **US$ 2,48625**, contra **US$ 2,49** do item 26 da PI. Nacionalizado: CNET **R$ 15,4120**,
preço **R$ 27,62**, margem realizada **12,0203%**.

Decomposição do preço: **CNET 55,8% · impostos 27,2% · comissão 5,0% · lucro 12,0%**.

### Fronha (Pillow Case): NÃO calculável

O motor devolve **o mesmo US$ 1,91** para 2, 3 e 4 flancos, contra **2,17 / 2,31 / 2,46** reais.

- A geometria-base **subestima ~25%** — implícito 1,4968 contra 1,1920 dos lençóis da mesma
  construção.
- O incremento por flanco é de **+6,75% por lado** (dispersão 9,1%).

**Nada disso foi cadastrado.** É hipótese registrada, não premissa. A fronha continua sem
fórmula demonstrada.

### Cobertura de custo

**45 SKUs ativos sem custo**, de 340:

| Fornecedor | SKUs sem custo |
|---|---|
| Kazareen (KTC) | 29 |
| Daune | 15 |
| Decor Tricot | 1 |

**Confiança do custo** (`produto.custo_confianca`, ativos):

| Valor | SKUs |
|---|---|
| `REVIEW_REQUIRED` (legado) | 121 |
| `QUOTED` | 121 |
| `CALCULATED` | 89 |
| nulo | 9 |

`precisa_revisao = 1` em **156** SKUs. KTC tem **270** SKUs ativos.

**110 SKUs de cama da KTC estão sem `material_ref`** (Flat Sheet, Fitted Sheet, Top Sheet, Duvet
Cover, Pillow Case, Bed Runner, Pillow Protector, Mattress Protector). Considerando todos os
fornecedores, são 132 nessas mesmas famílias. Sem `material_ref` não há tecido de onde derivar.

---

## I. Nova evidência — Daune (Marcelo)

Confirmado diretamente com o fornecedor em 09/09/2026:

| Item | Confirmação |
|---|---|
| Regime tributário | **Lucro Presumido** |
| PIS do fornecedor | **0,65%** |
| COFINS do fornecedor | **3%** |
| ICMS em venda interna SP | nominal **18%**, com **redução de base** para **efetivo 12%** |
| Abrangência da redução | aplica-se a **São Paulo** |
| Validade da tabela atual | até **31/12/2026** |
| Reajustes | normalmente em **janeiro** |
| Mudança extraordinária | anunciada com **30 dias** de antecedência |

> **A premissa de ICMS 12% efetivo da Daune em SP está FECHADA pelo usuário. Não reabrir.**

---

## J. Nova evidência — contabilidade da Indústria Química Anastacio (Brendo Simão)

Duas conclusões, de peso muito diferente:

**(A) A lógica do crédito de PIS/COFINS na aquisição está correta.** Nada a mudar.

**(B) O percentual de PIS/COFINS da VENDA varia em função do ICMS.** Textualmente:

> *"o percentual de 7,59% muda em função do ICMS"*

Planilha de apoio enviada: **`Fator Cálculo Exclusão ICMS .xlsx`**.

---

## K. Erro confirmado — PIS/COFINS da venda

### A regra correta

```
PIS/COFINS nominal  = 9,25%
PIS/COFINS efetivo  = 9,25% × (1 − ICMS da operação)
```

O ICMS é excluído da base de PIS/COFINS. O percentual efetivo, portanto, **depende da alíquota
de ICMS da operação** — não é constante.

| ICMS da operação | PIS/COFINS efetivo |
|---|---|
| 18% | **7,585%** |
| 12% | **8,14%** |
| 7% | **8,6025%** |
| 4% | **8,88%** |

### Por que 7,59% está errado

**7,59% é apenas a aproximação para ICMS ≈ 18%.** Aplicá-lo como regra fixa subestima o encargo
em toda operação com ICMS menor que 18% — ou seja, **em toda venda interestadual**, que é onde a
alíquota cai para 12%, 7% ou 4%.

Quanto menor o ICMS, maior o erro: em ICMS 4%, o encargo real é **8,88%** contra os 7,59%
aplicados — **1,29 ponto percentual** a menos de imposto no cálculo do preço.

**Isto é erro econômico material.** Está **autorizado para correção — e somente na próxima
sessão** (ver seção L).

### O CNET da Daune NÃO muda

A correção é do PIS/COFINS **da venda**. O **crédito de aquisição** permanece exatamente como
está.

A fórmula do CNET continua: `bruto − crédito de ICMS − 9,25% de PIS/COFINS sobre a aquisição
líquida de ICMS = CNET`.

Para a Daune com ICMS 12%:

```
fator = 1 − 0,12 − [0,0925 × (1 − 0,12)] = 0,7986
```

**Golden — estes números têm de permanecer idênticos após a correção:**

| Etapa | Valor |
|---|---|
| Bruto | R$ 249,37 |
| Crédito de ICMS (12%) | R$ 29,9244 |
| Base após ICMS | R$ 219,4456 |
| Crédito de PIS/COFINS (9,25%) | R$ 20,298718 |
| **CNET** | **R$ 199,146882** |

Se a implementação da correção alterar qualquer linha desta tabela, **a implementação está
errada** — o escopo é a venda, não a aquisição.

---

## L. O que ESTÁ autorizado na próxima sessão

**Uma única mudança econômica**, e ela é esta:

> **Corrigir o PIS/COFINS da VENDA para `9,25% × (1 − ICMS da operação)`,** substituindo o
> valor fixo de 7,59%.

Com estas condições, todas obrigatórias:

1. **O CNET da Daune não muda.** O golden da seção K é critério de aceitação, não referência.
2. **O crédito de aquisição não muda.**
3. **Baseline antes, comparação depois.** Toda diferença de número tem de ser explicada por esta
   regra e por nenhuma outra.
4. **Teste de regressão** que falhe se o percentual voltar a ser fixo, cobrindo pelo menos os
   quatro cenários de ICMS da tabela.
5. **Cotações emitidas não são reprecificadas.** A regra vale para precificação nova.
6. **Nada além disso.** Terminada a correção, **pare**.

Também autorizado, por ser documentação e verificação de estado:

- Manter este handoff atualizado.
- Rodar a suíte, o baseline e as verificações de leitura da seção O.

---

## M. O que NÃO está autorizado

### Fora de escopo por decisão explícita — nenhuma inferência nova permitida

| Tema | Situação |
|---|---|
| **DIFAL** | Congelado |
| **FCP** | Congelado |
| **Qual parcela do DIFAL entra na exclusão da base de PIS/COFINS** | **Não decidido. Não inferir.** |
| **Efeitos do FCP sobre a base** | **Não decidido. Não inferir.** |
| Outras regras fiscais não validadas | Congeladas |
| **KTC** — custos, tecido, motor, taxas | Congelado |
| **Frete** | Congelado |
| **Margens · comissão · financeiro** | Congelados |
| **Catálogo** | Congelado |
| **Daune além da correção documentada** | Congelado — inclusive o ICMS 12%, **fechado** |
| **Decor Tricot** | Congelado |

> **Se a implementação da correção de PIS/COFINS depender de uma decisão nova sobre DIFAL ou
> FCP: PARE E PERGUNTE. NÃO INVENTE.**

### Proibições operacionais permanentes

- **NÃO fazer push. NÃO criar remote.** Publicação remota **bloqueada** — a senha compartilhada
  saiu do código na Sessão 4 mas continua nos commits **`413d6bd`** e **`165d75e`**, e
  `referencia/` versiona tabela de preço de fornecedor.
- **NÃO reescrever histórico Git.**
- **NÃO versionar `.env`. NÃO colocar segredo no Git. NÃO exibir o segredo inteiro.**
- **NÃO colocar senha real no repositório. NÃO inventar senha. NÃO criar OWNER automaticamente.**
- **NÃO rodar o smoke E2E** que cria clientes, cotações, oportunidades e aprovações **contra
  `data/anara.db`**.
- **NÃO remover os backups reais** de `data/backups/` sem autorização explícita.
- **NUNCA INVENTAR PREMISSA ECONÔMICA.**
- **IA não calcula preço** — nem agora, nem na etapa 7 do roadmap.

### Escopo

- **Nenhuma outra implementação** além da seção L.
- **Não reabrir fase fechada** por melhoria marginal (seção E).
- **Não misturar mudanças.** Correção de PIS/COFINS é um commit; documentação é outro.
- **Commit exige autorização.** Mostre o diff e espere.

---

## N. Pendências genuínas ainda abertas

### Frete — bloqueia CIF

| ID | Pergunta à TRANSAL | Peso |
|---|---|---|
| **C-NEW-01** | O ICMS da prestação já está incluso, ou o valor sofre gross-up de ÷(1−12%)? | **P0** |
| **C-NEW-06** | A taxa de fiel depositário de 0,5% da NF incide sempre? | **P0** |
| C-NEW-02 | O GRIS de 0,10% incide sempre? | P1 |
| C-NEW-08 | O pedágio incide sobre peso real ou taxado? | P1 |

Composição hoje: ADV 0,20% (confirmado) + GRIS 0,10% (aberto) + fiel depositário 0,5% (aberto).
Se o fiel depositário incidir sempre, a taxa variável **salta de 0,30% para 0,80%**.

- **C-NEW-03** — Passo Fundo-RS existe na tabela sem tarifa, mínimo ou prazo.
- **C-NEW-07** — **a tabela TRANSAL vence em 31/12/2026.** Vencida sem substituta, bloqueia.
- **C-NEW-04** — volume por SKU ausente; importa quando peso real e taxado divergem.

### Origem logística de Daune e Decor (Q-L)

Ambas embarcam de **São Paulo**. A única tabela de frete cadastrada tem origem **Itajaí-SC**.
Não existe tarifa saindo de São Paulo → o **CIF dessas duas continua `A_COTAR`**, e o sistema
está **certo** em travar. Destravar exige tabela de frete com origem São Paulo.

### Pricing

- **45 SKUs sem custo** (KTC 29 · Daune 15 · Decor 1).
- **Tecido ~4% caro:** 250TC CVC cadastrado 1,25 contra implícito 1,192; 300TC Cotton cadastrado
  1,40 contra implícito 1,344. **Afeta toda a linha de cama.**
- **Fronha não calculável** — geometria −25%, flancos não modelados.
- **7 Pool Towel plain sem taxa.** **Face Towel sem linha em `ToalhaPreco`.**
- **110 SKUs de cama da KTC sem `material_ref`.**
- **121 SKUs com `REVIEW_REQUIRED` legado** — o rótulo antigo tem sentido diferente do canônico;
  reclassificar exige o gate de reconciliação. **Nunca converter automaticamente.**

### Fiscal

- **MG e BA estão bloqueados para não-contribuinte** — `icms_interno_base` não está cadastrado, e
  o sistema **não adivinha** se a alíquota interna já inclui FCP. **Só o RJ tem linha em
  `RegraFcp`.** Este bloqueio é comportamento correto, não defeito.

### Técnicas (do `AUDIT_ANARA_MASTER.md`)

| ID | O quê | Impacto |
|---|---|---|
| **B-17** | Duas fontes Daune com bases diferentes — razão constante de 1,5123 | Confirmar qual é a base da planilha Trousseau |
| **B-16** | 28 de 31 SKUs de Duvet Insert sem `gsm` | Casamento estruturado impossível nessa família |
| **B-13** | 21 divergências entre `models.py` e o esquema real | Nenhum no uso normal |
| **B-19** | Dois scripts de manutenção quebram com fiscal bloqueado | Nenhum no uso normal |
| **B-20** | `GET /logout` muda estado via GET | Baixo |
| **B-22** | `scripts/backup_banco.py` não aplica retenção | **Não bloqueante** — a poda do `app/migrations.py` funciona |

**B-18 está resolvido** — a poda deixou de apagar o backup recém-criado.

### Publicação

**BLOQUEADA.** Ver seção M.

---

## O. Comandos úteis para validar o ambiente

Todos de **leitura**, exceto onde indicado. Rode-os antes de confiar em qualquer coisa escrita
aqui.

### Estado do repositório

```bash
cd ~/Anara-Cotacao && git rev-parse --short HEAD && git rev-parse --abbrev-ref HEAD && git status --porcelain && git remote -v
```

### Alembic — banco contra código

```bash
cd ~/Anara-Cotacao && sqlite3 -readonly data/anara.db "select version_num from alembic_version;" && ls alembic/versions/ | grep -oE "^[0-9]{4}" | sort | tail -1
```

### Suíte completa

```bash
cd ~/Anara-Cotacao && python3 -m pytest -q 2>&1 | tail -5
```

### Premissas vigentes

```bash
cd ~/Anara-Cotacao && sqlite3 -readonly -header -column data/anara.db "select id, chave, valor_num, unidade, valid_from from premissa where valid_to is null and ativo=1 order by chave;"
```

### Volumetria do banco

```bash
cd ~/Anara-Cotacao && sqlite3 -readonly data/anara.db "select 'cotacoes', count(*) from cotacao union all select 'itens', count(*) from cotacaoitem union all select 'produtos ativos', count(*) from produto where ativo=1 union all select 'usuarios', count(*) from usuario;"
```

### SKUs sem custo, por fornecedor

```bash
cd ~/Anara-Cotacao && sqlite3 -readonly data/anara.db "select coalesce(f.nome,'(sem fornecedor)'), count(*) from produto p left join fornecedor f on f.id=p.fornecedor_id where p.ativo=1 and (p.custo_unitario is null or p.custo_unitario=0) group by 1 order by 2 desc;"
```

### Confiança do custo

```bash
cd ~/Anara-Cotacao && sqlite3 -readonly data/anara.db "select coalesce(custo_confianca,'(null)'), count(*) from produto where ativo=1 group by 1 order by 2 desc;"
```

### Subir o servidor

```bash
cd ~/Anara-Cotacao && python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8420 --reload
```

### Verificar que está no ar

```bash
curl -s http://127.0.0.1:8420/health
```

### Backup antes de qualquer mudança — ESCREVE

```bash
cd ~/Anara-Cotacao && python3 scripts/backup_banco.py backup --motivo antes-da-correcao-piscofins
```

### Baseline de regressão

Procedimento completo em `BACKUP.md`. O baseline imutável é
`relatorios/baseline_fase0.json`; o comparador de sessão é
`scripts/comparar_baseline_sessao3b.py`.

> **Não rode `scripts/smoke_test.py` contra `data/anara.db`.** Ele cria dados. Use
> `ANARA_DB_URL` apontando para uma cópia — a variável isola aplicação, scripts e migrations.

---

## P. Fechamento — estado exato desta sessão

Tudo abaixo foi **verificado diretamente no repositório** em 09/09/2026, não recuperado de
memória de conversa.

| | |
|---|---|
| **HEAD** | `2c45f31` |
| **Mensagem do commit** | "Product cleanup: navegação, entrada de dados, erros e confiabilidade do custo" |
| **Commit anterior** | `6600f68` — "Sessão 8 — relatórios, saúde operacional e prontidão para o piloto" |
| **Branch** | `main` |
| **Remote** | nenhum |
| **Alembic (banco)** | `0017` |
| **Alembic (código)** | `0017` — sincronizado |
| **Migrations criadas nesta sessão** | nenhuma |
| **Testes** | **900 passando**, 0 falhando, 62,49 s |
| **Servidor** | no ar em `127.0.0.1:8420`, `/health` = ok |
| **Backups** | 34 arquivos, 57 MB — nenhum removido |
| **`.env`** | não versionado |

### Trabalho não commitado no fechamento

```
 M app/pricing_service.py             correção do Decimal na memória do preço
 M relatorios/regressao_sessao3b.json apenas o contador 19 → 20 (cotação criada na tela)
?? ANARA_PRICING_VALIDATION_MASTER.md 1.557 linhas, 72 KB
?? tests/test_memoria_serializavel.py 5 testes de regressão
```

**Nada disso foi commitado** — commit depende de autorização.

### O que esta sessão resolveu

| | |
|---|---|
| **Calculadora quebrada** | `TypeError: Object of type Decimal is not JSON serializable`. Defeito **pré-existente**, reproduzido em `6600f68` num worktree isolado. Corrigido com `_para_json()` na fronteira de saída, mais 5 testes |
| **Motor de toalhas** | Auditado contra a PI de 31/08/2026 e contra a planilha industrial da KTC. **A composição não separa a taxa por kg** — 7 de 7 taxas batem exatamente com o cadastro |
| **Motor de cama** | Backtest com 12 peças: estrutura confirmada, **preço do tecido ~4% desatualizado** |
| **Fronha** | Comprovadamente **não calculável** hoje. Hipótese registrada, **não** cadastrada |
| **Proposta de `yarn_type`** | **Rejeitada pela evidência.** Exigir `yarn_type` + `subcategoria` **não casa com nada** no cadastro — as linhas 1–2 têm `subcategoria` nula e as 3–7 têm `yarn_type` nulo. Implementar teria quebrado a calculadora |

### Nada foi implementado depois do último checkpoint

A sessão terminou em modo de documentação e verificação. A correção de PIS/COFINS descrita na
seção K **não foi implementada** — está autorizada apenas para a próxima sessão, nas condições
da seção L.

---

*Handoff escrito em 09/09/2026. Estado verificado no repositório, não na memória da conversa.*

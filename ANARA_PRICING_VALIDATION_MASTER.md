# Anara — Metodologia de Precificação
## Documento mestre para validação com os sócios da Química Anastacio

| | |
|---|---|
| Snapshot | 09/09/2026, 08:15 |
| Versão do sistema | HEAD `2c45f31` · Alembic `0017` |
| Base de dados | `data/anara.db`, lida em modo somente leitura |
| Câmbio vigente | **R$ 5,19 / US$** desde 08/09/2026 |

Este documento descreve **a metodologia que está implementada e rodando hoje**. Todo número
aqui foi extraído do sistema no momento do snapshot — nenhum veio de memória, de conversa ou
de documento anterior. Onde a evidência não bastou, está escrito **NÃO CONFIRMADO**.

O objetivo não é aprovar o sistema. É colocar cada premissa na mesa e perguntar: *isto está
certo?*

---

# 1. Executive summary — como a Anara forma preço

A Anara compra de três fornecedores por caminhos diferentes e vende por um caminho só.

```
        KTC (Egito)                    Daune / Decor (Brasil)
             │                                   │
   especificação técnica                  preço bruto do
   (medidas, fios, gramatura)               fornecedor
             │                                   │
   motor industrial                        créditos de
   tecido + confecção                    ICMS e PIS/COFINS
             │                                   │
          EXW US$                                │
             │                                   │
    nacionalização                               │
  frete + I.I. + despesas                        │
        × câmbio                                 │
             │                                   │
             └──────────► CUSTO NET (R$) ◄───────┘
                               │
                    ┌──────────┴──────────┐
                    │  impostos da venda  │  ICMS · PIS/COFINS · DIFAL · FCP
                    │  condição de pgto   │  encargo financeiro
                    │  comissão           │  por faixa de markup
                    │  frete comercial    │  quando CIF
                    │  margem             │  alvo por fornecedor/família
                    └──────────┬──────────┘
                               │
                     PREÇO RECOMENDADO
```

**A ideia central:** o custo é apurado *cheio*, sem imposto embutido, e o preço é formado por
um **gross-up** — divide-se o custo mais margem por aquilo que a receita vai ter de pagar.
Não é "custo × 1,X". Isso importa porque imposto e comissão incidem sobre a receita, não
sobre o custo: somá-los como se fossem custo subestima o preço necessário.

**Três exemplos reais, do catálogo de hoje:**

| Produto | Custo NET | Margem-alvo | Preço recomendado |
|---|---|---|---|
| Lençol plano hotel 160×310 · 300 fios · algodão (KTC) | R$ 60,55 | 18% | **R$ 121,56** |
| Toalha de banho 100×150 · 450 g/m² · algodão (KTC) | R$ 33,98 | 12% | **R$ 60,89** |
| Travesseiro 50×70 · plumas de ganso (Daune) | R$ 199,15 | 14% | **R$ 370,09** |

Cenário: venda interna São Paulo → São Paulo, cliente não contribuinte, pagamento em 30 dias,
frete não incluso.

---

# 2. Mapa mestre das premissas

Toda premissa vigente que altera preço, direta ou indiretamente.

**Legenda de status:**
🟢 `CONFIRMADO INTERNAMENTE` · 🟡 `VALIDAR COM ANASTACIO` · 🔵 `VALIDAR COM FISCAL` ·
🟠 `VALIDAR COM LOGÍSTICA` · 🔴 `PENDENTE / NÃO CONFIRMADO`

## 2.1 Premissas globais

| Premissa | Valor | Unidade | Desde | Fonte | Onde entra | Afeta | Validar com | Status |
|---|---|---|---|---|---|---|---|---|
| `fx_usd_brl` | **5,19** | R$/US$ | 08/09/2026 | *Email de atualização diária* | multiplica o NET USD | 270 SKUs KTC | Diretoria | 🟡 |
| `frete_int_usd_kg` | **0,516** | US$/kg | 28/08/2026 | *Sistema de preços Anara novo.xlsx · 05_Premissas* | soma ao EXW antes do I.I. | KTC | Logística | 🟡 |
| `frete_int_referencia_usd` | 645 | US$ | 28/08/2026 | idem | origem do 0,516 | — | Logística | 🟡 |
| `frete_int_referencia_kg` | 1.250 | kg | 28/08/2026 | idem | origem do 0,516 | — | Logística | 🟡 |
| `outras_desp_usd_un` | **0,2487532709** | US$/un | 28/08/2026 | idem | soma ao NET USD | KTC | Financeiro | 🟡 |
| `pis_cofins_nominal_pct` | **9,25%** | fração | 09/09/2026 | *Brendo Simão — Contabilidade Química Anastacio, 09/09/2026 · planilha "Fator Cálculo Exclusão ICMS .xlsx"* | base do PIS/COFINS **efetivo**, derivado por item | todos | Fiscal | 🟢 |
| `pis_cofins_pct` | 7,59% | fração | 28/08/2026 | idem | **nenhum** — efetivo fixo da metodologia anterior | — | — | ⬛ legado |
| `comissao_tabela` | 6 faixas, 5%→10% | — | 28/08/2026 | idem | denominador do preço | todos | Diretoria | 🟡 |
| `validade_dias` | 5 | dias | 28/08/2026 | *Premissas do site de cotação* | validade da proposta | — | Comercial | 🟢 |
| `fiscal_uf_origem_padrao` | **SP** | UF | 03/09/2026 | *Decisões de 03/09/2026* | origem fiscal quando não há outra | todos | Fiscal | 🔵 |
| `fiscal_finalidade_padrao` | `USO_CONSUMO` | — | 03/09/2026 | idem | finalidade da operação | todos | Fiscal | 🔵 |
| `icms_fallback_pct` | 18% | fração | 28/08/2026 | *Regra Anara* | **nenhum** | — | — | ⬛ resíduo |

> ⬛ **`icms_fallback_pct` é resíduo histórico e NÃO ALIMENTA O PREÇO.** Foi aposentada na
> Onda 1; a migration `0003` registra que "deixa de ser lida por qualquer código", e a
> varredura do código confirma: nenhuma chamada a `cfg.num(session, "icms_fallback_pct")`.
> Continua cadastrada por rastreabilidade. **Não usar como regra vigente.**

**Premissas de exibição** (não alteram preço): `catalogo_origem`, `catalogo_destino`,
`catalogo_contribuinte`, `catalogo_condicao_pagamento` — definem o cenário do preço-base
mostrado em `/produtos`. `freshness_fresh_dias` (30) e `freshness_aging_dias` (60) classificam
o quanto uma referência de custo envelheceu. `indice_algodao` (100,8) e `indice_petroleo`
(100,0) estão cadastrados e **NÃO CONFIRMADO** se alimentam algum cálculo hoje.

---

# 3. KTC — formação do custo industrial

## 3.1 Parâmetros de produção vigentes

| Parâmetro | Escopo | Valor | Fonte | Validar com | Status |
|---|---|---|---|---|---|
| `waste` | geral | **3%** | `KTC_Pricing_Master_Simple.xlsx` | KTC | 🟡 |
| `shrinkage` | 100% algodão | **5%** | idem | KTC | 🟡 |
| `shrinkage` | CVC (poly/cotton) | **3%** | idem | KTC | 🟡 |
| `quality_allowance` | geral | **1%** | idem | KTC | 🟡 |
| `ktc_margin` | geral | **15%** | idem | Diretoria | 🟡 |
| `hem_width_total_cm` / `hem_length_total_cm` | Flat Sheet, Top Sheet, Duvet Cover | **4,0 cm** cada | idem | KTC | 🟡 |
| `paineis` | Flat Sheet, Top Sheet | 1 | idem | KTC | 🟢 |
| `paineis` | Duvet Cover | 2 | idem | KTC | 🟢 |

## 3.2 As três contas que parecem multiplicação e não são

Este é o ponto que mais gera confusão em revisão, e o código faz **exatamente** o seguinte:

### Waste — divide

```
consumo_com_waste = consumo ÷ (1 − 0,03)
```

**Por quê:** o waste de 3% é a fração do tecido **comprado** que se perde. Se preciso de
5,677 m² de tecido bom e 3% do que compro vira retalho, tenho de comprar `5,677 ÷ 0,97 =
5,853 m²`. Multiplicar por 1,03 daria 5,847 m² — 0,006 m² a menos, e a peça sairia
sistematicamente subcusteada.

### Segunda qualidade — divide

```
custo_com_2a = custo ÷ (1 − 0,01)
```

Mesma lógica: 1% da produção sai como segunda qualidade e não é vendida como primeira. O
custo da peça boa absorve a perda.

> ⚠️ **Atenção ao nome.** Na planilha da KTC esse campo aparece rotulado como **"II 1%"**.
> Não é Imposto de Importação. É perda de segunda qualidade. O Imposto de Importação é outra
> coisa, entra depois, e tem alíquota própria por NCM.

### Margem KTC — divide

```
EXW = custo ÷ (1 − 0,15)
```

A margem de 15% é **sobre o preço final da KTC**, não sobre o custo dela. Um custo de US$ 9,03
com margem de 15% sobre o preço dá EXW de US$ 10,63 — e a margem embutida é
`(10,63 − 9,03) ÷ 10,63 = 15,0%`. Se fosse `× 1,15`, o EXW seria US$ 10,39 e a margem real,
14,6%.

> **`margem KTC ≠ margem Anara`.** A primeira é do fornecedor e já está dentro do EXW. A
> segunda é da Anara e entra bem depois, sobre o CNET.

### Shrinkage — multiplica

```
medida_final = medida_com_bainha × (1 + shrinkage)
```

Aqui é multiplicação mesmo: o tecido encolhe na lavagem, então corta-se **maior** para que a
peça acabada tenha a medida contratada.

## 3.3 GSM e peso — quando a medida não basta

| Uso | Parâmetro | Quando entra |
|---|---|---|
| GSM por título | `gsm_por_tc` | 120→105 · 180→110 · 200→115 · 233→120 · 250→125 · 300→135 · 400→150 · 500→165 · 800→200 |
| GSM por família | `gsm_por_familia` | Bath Mat 750 · Bathrobe 300 · Duvet Insert 250 · Fitted Sheet 120 · Mattress Protector/Topper 200 · Pillow Case 120 · Pillow Protector 200 |
| Peso por m² | `peso_kg_m2_familia` | Flat/Top Sheet 0,1574 · Bottom/Fitted Sheet 0,1557 · Duvet Cover 0,307 · Pillow Case 0,619 |
| Peso técnico | `peso_tecnico_familia` | Slipper 0,15 kg |

Os pesos por m² foram **calibrados com os pesos reais da PI ANARA de 23/08/2026** — é a
premissa com evidência mais forte deste bloco. Os GSM estimados vêm da planilha e são usados
**só quando o produto não tem GSM nem peso real cadastrado**.

> **Peso real da KTC nunca é substituído por estimativa.** A estimativa só entra na ausência.

**Validar com KTC/Logística:** 🟡 os GSM estimados por título ainda não têm confirmação
documental do fornecedor.

---

# 4. Tabela completa de materiais KTC

Todos os materiais vigentes. Preço em dólar por metro quadrado.

| Material | Fios | Armação | Algodão | Poliéster | Acabamento | US$/m² |
|---|---|---|---|---|---|---|
| 200TC Percale Polycotton | 200 | Percale | — | — | plain | **1,10** |
| 230TC Percale 100% Cotton | 230 | Percale | 100% | 0% | plain | **1,20** |
| 250TC Sateen CVC 70/30 | 250 | Sateen | 70% | 30% | plain | **1,25** |
| 250TC Sateen CVC 70/30 | 250 | Sateen | 70% | 30% | stripe | **1,30** |
| 250TC Sateen 100% Cotton | 250 | Sateen | 100% | 0% | plain | **1,30** |
| 250TC Sateen 100% Cotton | 250 | Sateen | 100% | 0% | stripe | **1,35** |
| 300TC Sateen CVC 70/30 | 300 | Sateen | 70% | 30% | plain | **1,35** |
| 300TC Sateen CVC 70/30 | 300 | Sateen | 70% | 30% | stripe | **1,40** |
| 300TC Sateen 100% Cotton | 300 | Sateen | 100% | 0% | plain | **1,40** |
| 300TC Sateen 100% Cotton | 300 | Sateen | 100% | 0% | stripe | **1,45** |
| 400TC Sateen 100% Cotton | 400 | Sateen | 100% | 0% | plain | **1,50** |
| 400TC Sateen 100% Cotton | 400 | Sateen | 100% | 0% | stripe | **1,55** |

**12 combinações**, todas vigentes desde 28/08/2026, fonte
`KTC_Pricing_Master_Simple.xlsx · Materials - CMT Prices`.

**Padrões legíveis:** listrado custa US$ 0,05/m² a mais que liso, em toda a tabela. Algodão
puro custa US$ 0,05/m² a mais que CVC 70/30 no mesmo título. Cada 50 fios acrescentam entre
US$ 0,05 e 0,10/m².

> 🟡 **Para validar:** a própria KTC anotou na fonte que *"These prices can vary from time to
> time"*. Não há data de validade cadastrada. **Não existe material de 500 nem de 800 fios na
> tabela** — e o catálogo tem SKUs nesses títulos, que por isso não formam preço industrial.

## 4.1 Terry — preço por quilo

Toalhas não usam m²: usam peso.

| Subcategoria | Acabamento | US$/kg | Fonte | Status |
|---|---|---|---|---|
| Towel (fio simples) | plain | 8,00 | `KTC_Pricing_Master_Simple.xlsx` | 🟡 genérico |
| Towel (fio retorcido) | plain | 8,50 | idem | 🟡 genérico |
| Bath Towel | plain | **8,50** | Derivado da PI ANARA 23/08/2026 | 🟢 confere em 2 medidas |
| Hand Towel | plain | **9,00** | idem | 🟢 confere em 550g e 650g |
| Bath Mat | plain | **9,00** | idem | 🟢 confere em 750g e 950g |
| Wash Cloth | plain | **9,00** | Derivado da HAMAN 25/08/2026 | 🟡 documento de outro cliente |
| Pool Towel | stripe | **14,00** | Derivado da PI ANARA 23/08/2026 | 🟡 |

> **A taxa por kg já é EXW final.** Não se aplica CMT, segunda qualidade nem margem KTC por
> cima — ela já os contém. É por isso que o waterfall da toalha tem duas linhas e o do lençol
> tem onze.

> 🟡 **Pool Towel a US$ 14,00/kg** é 65% acima da toalha de banho lisa. A nota do próprio
> cadastro diz: *"listrado e acabamento pesam. Piscina lisa não tem preço conhecido."*
> **Pergunta para a KTC:** o listrado justifica esse salto, ou há outro fator?

---

# 5. CMT — custo de confecção KTC

| Família | Construção | US$/peça |
|---|---|---|
| Flat Sheet | — | **0,75** |
| Top Sheet | — | **0,75** |
| Fitted Sheet | — | **1,00** |
| Duvet Cover | — | **1,50** |
| Pillow Case | standard | **0,50** |
| Pillow Case | oxford | **0,75** |

Seis linhas, todas de 28/08/2026, mesma fonte.

## 5.1 Quais famílias usam o motor industrial

| Caminho | Famílias | Como o custo nasce |
|---|---|---|
| **Motor industrial** | Flat Sheet · Top Sheet · Bottom Sheet · Duvet Cover · Pillow Case (fronha, geometria §18) | Especificação → tecido → CMT → EXW |
| **Motor de terry** | Bath/Hand/Face/Pool/Beach Towel · Bath Mat · Wash Cloth | Peso × US$/kg = EXW direto |
| **Fora do motor** | Fitted Sheet (com elástico) · Bathrobe · Slipper | EXW cotado direto pela KTC, ou último preço válido |
| **Sem base** | 45 SKUs ativos | Não formam preço — ver §37 |

> **Por que Fitted Sheet tem CMT cadastrado mas está fora do motor:** o CMT existe, mas a
> **geometria** do lençol com elástico não foi confirmada pela KTC. Sem saber o consumo de
> tecido, não há como calcular. O sistema não inventa a fórmula — usa o preço cotado.

---

# 6. EXW — o ponto de saída da fábrica

EXW é o preço da mercadoria na porta da fábrica no Egito: sem frete, sem imposto, sem
despesa de importação.

O sistema resolve o EXW por **precedência**, na ordem:

1. **Motor industrial** — quando o método é `KTC_CALCULATED` e a família tem fórmula
2. **EXW cotado pela KTC** (`exw_cotado_usd`) — quando existe, com fonte e data
3. **Preço KTC histórico do catálogo** (`preco_ktc_usd`)
4. **Nenhum** → o custo NET não é recalculado, e o item entra como *revisão necessária*

## 6.1 Waterfall industrial completo — exemplo real

**Lençol plano hotel 160×310 · 300 fios · 100% algodão**, do catálogo de hoje:

| # | Etapa | Fórmula | Resultado |
|---|---|---|---|
| 1 | Largura com bainha | 160,0 + 4,0 | 164,0 cm |
| 2 | Comprimento com bainha | 310,0 + 4,0 | 314,0 cm |
| 3 | Largura após encolhimento | 164,0 × (1 + 0,05) | 172,2 cm |
| 4 | Comprimento após encolhimento | 314,0 × (1 + 0,05) | 329,7 cm |
| 5 | Área de um painel | 172,2 × 329,7 ÷ 10.000 | 5,677434 m² |
| 6 | Consumo bruto | 5,677434 × 1 painel | 5,677434 m² |
| 7 | Consumo com waste | 5,677434 **÷** (1 − 0,03) | 5,853025 m² |
| 8 | Custo do tecido | 5,853025 m² × US$ 1,40/m² | US$ 8,194235 |
| 9 | Custo de produção | 8,194235 + CMT 0,75 | US$ 8,944235 |
| 10 | Após perda de 2ª qualidade | 8,944235 **÷** (1 − 0,01) | US$ 9,034580 |
| 11 | **EXW KTC** | 9,034580 **÷** (1 − 0,15) | **US$ 10,628918** |

Repare: o tecido é 77% do EXW. **Câmbio e preço de tecido são as duas alavancas que mais
mexem no preço final de cama.**

---

# 7. Nacionalização — do Egito ao custo em reais

## 7.1 Câmbio

| | |
|---|---|
| Valor vigente | **R$ 5,19 / US$** |
| Versão | `Premissa` id 21 |
| Vigente desde | 08/09/2026 |
| Fonte registrada | *"Email de atualização diária"* |
| Versão anterior | R$ 5,11, de 28/08 a 08/09, fonte *Sistema de preços Anara novo.xlsx* |

**Como entra:** multiplica o NET USD inteiro, no último passo. É o parâmetro de maior
alavancagem do sistema — 1% de câmbio move ~1% do preço final de todo produto importado.

**Política atual de atualização:** **NÃO CONFIRMADO.** O sistema aceita qualquer cadência e
registra a data de cada versão; não há regra automática nem integração de cotação. A fonte da
versão vigente diz "email de atualização diária", mas isso é texto livre digitado por quem
cadastrou, não uma política implementada.

> 🟡 **Pergunta para a diretoria:** qual câmbio deve ser usado — o do dia, uma média, o de
> fechamento do contrato de câmbio, ou um câmbio de trabalho com margem de segurança? A
> resposta muda a política, não o sistema: o versionamento já suporta qualquer uma.

## 7.2 Frete internacional

| | |
|---|---|
| Valor vigente | **US$ 0,516 / kg** |
| Derivação | US$ 645 ÷ 1.250 kg = 0,516 |
| Fonte | *Sistema de preços Anara novo.xlsx · 05_Premissas* |
| Desde | 28/08/2026 |
| Validade | **nenhuma cadastrada** |

Os três valores estão no banco separadamente — referência em dólar, referência em quilos e a
taxa derivada — o que permite reconferir a conta a qualquer momento.

**Como entra:** `frete_unitário = peso_kg × 0,516`, somado ao EXW **antes** do Imposto de
Importação. É por isso que o frete internacional é tributado junto com a mercadoria.

> 🟡 **Pergunta para logística:** US$ 645 por 1.250 kg continua representativo? O embarque de
> referência é de quando? A taxa varia com o volume do embarque?

## 7.3 Outras despesas de importação

| | |
|---|---|
| Valor vigente | **US$ 0,2487532709 / unidade** |
| Fonte | *Sistema de preços Anara novo.xlsx · 05_Premissas* |
| Composição | **NÃO CONFIRMADO** — o sistema guarda o total, não a decomposição |

**Como entra:** soma direta ao NET USD, **por unidade**, depois do I.I.

> 🟡 **Duas perguntas para o financeiro:** (1) o que compõe esses US$ 0,2488 — despachante,
> armazenagem, THC, capatazia, seguro? (2) Ser **por unidade** significa que uma peça de US$ 3
> e uma de US$ 60 carregam a mesma despesa. Isso é intencional, ou deveria ser proporcional ao
> valor ou ao peso? As nove casas decimais sugerem um rateio calculado em algum lugar — vale
> recuperar a conta de origem.

## 7.4 Imposto de Importação

Resolvido por **NCM**, com precedência: regra da tabela de NCM → `ii_aplicado` do produto →
nenhum.

| NCM | Família | I.I. | Confiável | Nota |
|---|---|---|---|---|
| 6302.31.00 | Flat/Top/Bottom/Fitted Sheet, Duvet Cover, Bed Runner, Cotton Bedding | **3,5%** | sim | Tabela preferencial Egito |
| 6302.60.00 | Bath Towel, Bath Mat | **3,5%** | sim | Linha banho/terry |
| 6208.91.00 | Bathrobe | **3,5%** | sim | NCM corrigido na Sessão 2 |
| 6302.32.00 | Bed linen sintético | **3,5%** | sim | Assumido igual ao 6302.31.00 |
| 6404.19.00 | Chinelos | **3,5%** | sim | — |
| **9404.40.00** | **Duvet Insert / Edredom** | **— sem valor** | **não** | 🔴 ver abaixo |

> 🔴 **ACHADO — Duvet Insert sem I.I. confiável.** A nota do próprio cadastro diz:
> *"Fora da tabela preferencial Egito e sem II confiável. O sistema antigo usava 7% por
> fallback, sem documento. Validar antes de cotar."*
>
> São 31 SKUs de Duvet Insert no catálogo, 14 deles sem custo. **Isto precisa de resposta de
> quem cuida da importação antes de qualquer cotação de edredom.**

**Origem Egito:** o I.I. de 3,5% é atribuído a "tabela preferencial Egito". O sistema aplica
a alíquota cadastrada, sem verificar certificado de origem nem acordo comercial —
**NÃO CONFIRMADO** se há requisito documental para essa preferência.

## 7.5 Fórmula completa

```
frete_unitário =  peso_kg × frete_usd_kg
base_do_II     =  EXW + frete_unitário
I.I.           =  base_do_II × alíquota_NCM
NET_USD        =  EXW + frete_unitário + I.I. + outras_despesas
CNET_BRL       =  NET_USD × câmbio
```

**No exemplo do lençol:**

| Componente | Cálculo | Valor |
|---|---|---|
| EXW | do motor industrial | US$ 10,628918 |
| Frete internacional | 0,780704 kg × 0,516 | US$ 0,402843 |
| Base do I.I. | 10,628918 + 0,402843 | US$ 11,031761 |
| I.I. | 11,031761 × 3,5% | US$ 0,386112 |
| Outras despesas | fixo por unidade | US$ 0,248753 |
| **NET USD** | soma | **US$ 11,666626** |
| **CNET BRL** | × 5,19 | **R$ 60,549791** |

> **O CNET não é arredondado.** É custo interno, e arredondá-lo introduziria erro que se
> propaga. Só o preço comercial vira centavo.

---

# 8. Peso e NCM

**Precedência do peso**, em ordem:

1. `Produto.peso_kg` — peso real, quando cadastrado
2. `peso_kg_m2_familia` × área da peça — calibrado com a PI ANARA de 23/08/2026
3. `peso_tecnico_familia` — valor fixo por família (só Slipper)
4. GSM × área — quando há gramatura

**Onde o peso importa:** no frete internacional (US$/kg) e no frete nacional (peso taxado).
Não entra em mais nada.

> 🟡 **Para logística:** os pesos por m² foram calibrados contra uma PI real — é a evidência
> mais forte do bloco. Vale confirmar se a calibração vale para todas as construções ou só
> para as que estavam naquela PI.

---

# 9. Daune — fornecedor nacional

O caminho é completamente diferente: **não há motor industrial nem nacionalização**. O que a
Daune cobra já é preço em reais, com impostos dentro.

## 9.1 A conta

```
CNET = bruto − (bruto × 12%) − ((bruto − bruto×12%) × 9,25%)
```

| Crédito | Alíquota | Base |
|---|---|---|
| ICMS de compra | **12%** | preço bruto |
| PIS/COFINS de compra | **9,25%** | bruto menos o ICMS |

**Exemplo real — Travesseiro 50×70 · 100% plumas de ganso:**

| Etapa | Cálculo | Valor |
|---|---|---|
| Preço bruto Daune | da tabela do fornecedor | R$ 249,37 |
| − Crédito de ICMS 12% | 249,37 × 0,12 | R$ 29,92 |
| = Base PIS/COFINS | | R$ 219,45 |
| − Crédito PIS/COFINS 9,25% | 219,45 × 0,0925 | R$ 20,30 |
| **= CNET** | | **R$ 199,15** |
| Fator efetivo | 199,15 ÷ 249,37 | **0,7986** |

> **O fator 0,7986 é conferência, não fórmula.** O sistema calcula pelos componentes. Aplicar
> o fator sobre um custo já persistido daria um número sem significado, porque esse custo pode
> ter vindo de outro documento.

## 9.2 Crédito de compra ≠ imposto de venda

Esta é a distinção que mais importa nesta seção, e o código a marca explicitamente:

- O **crédito de 12%** é o ICMS que a Anara **recupera** por ter comprado da Daune. Reduz o
  custo.
- O **ICMS da venda** é o que a Anara **paga** ao vender ao hotel. Aumenta o preço.

São impostos diferentes, em operações diferentes, com alíquotas que não têm relação. Confundir
os dois foi um bug registrado (B-09) e corrigido.

## 9.3 Situação

| | |
|---|---|
| Referências vigentes com bruto e CNET | **52**, todas `CONFIRMADO`, método `DAUNE_DIRECT` |
| SKUs ativos sem custo | **1** — o protetor de colchão 100×200 (modelo indeterminado) |
| Margem-alvo | 14% |
| Origem logística cadastrada | **vazia** — ver §22 |

## 9.4 Fonte de 13/09/2026 — "Projeto Anastacio.xlsx"

Atualização de tarifário encaminhada pelo fornecedor, com os preços que haviam sido
solicitados por estarem faltando. Duas abas: `Cotação 24.06.26` (histórica) e
`Nova Cotação 05.08.26` (a mais nova para o que ela contém). Reconciliada por campos
estruturados — família, gramatura, medida — mais composição por igualdade exata de token.

**Fechou a pendência dos edredons.** A fonte anterior não distinguia 180 g de 250 g, e 14 SKUs
ficaram sem referência em vez de receber gramatura inventada. A nova aba traz as duas
gramaturas explícitas, e cada SKU recebeu a **sua** linha:

| Composição | 180 g · bruto | 250 g · bruto |
|---|---|---|
| Pluma 250×260 | R$ 1.137,50 | R$ 1.235,00 |
| Pluma 270×265 | R$ 1.216,35 | R$ 1.359,45 |
| Poliéster 190×260 | R$ 469,30 | R$ 518,70 |
| Poliéster 250×260 | R$ 585,00 | R$ 682,50 |
| Poliéster 270×265 | R$ 643,95 | R$ 751,27 |
| Poliéster 285×265 | R$ 679,72 | R$ 793,01 |
| Poliéster 290×260 | R$ 678,60 | R$ 791,70 |

Os seis edredons de pluma que já tinham referência (190×260, 285×265, 290×260, nas duas
gramaturas) vieram com o **mesmo bruto** da fonte anterior — sem versão nova. O fornecedor
precifica por área a R$/m² constante: poliéster 180 g a R$ 90, 250 g a R$ 105, pluma 250 g a
R$ 190. Única anomalia entre os 14: pluma 180 g 270×265 a R$ 170/m² contra 175 dos vizinhos
— sinalizada, não corrigida.

**O que a fonte NÃO fechou — e por quê:**

- **Protetor de colchão (10 linhas):** a fonte descreve *matelassado com alça / com slip*; o
  catálogo tem *manta 120 g impermeável*. E as medidas não coincidem (catálogo 140×200 e
  160×200; fonte 160×200 e 180×200, com os preços deslocados uma medida). É outra construção
  e outra grade — **CONFLICT**, intacto.
- **Pillow Top (10 linhas):** os preços são os mesmos do catálogo, mas a fonte declara
  1,03/1,63/1,83/1,93/2,03 × 2,03 onde o catálogo tem 100/140/180/200 × 200. Medida é atributo
  material: **CONFLICT**, intacto. Inclui o valor de três casas, R$ 1.103,203, no 2,03×2,03.
- **Os 280 g (SKUs 340–348):** ver AUDIT §2.2.1 e C-NEW-11.

> 🟠 **DECISÃO PENDENTE — 280 g × 180GSM.** O catálogo tem edredons de poliéster rotulados
> **280 g** a partir de informação verbal/imagem de 03/09/2026. A fonte escrita do fornecedor
> traz, nas mesmas três medidas e com **exatamente os mesmos brutos** (R$ 469,30 · 679,72 ·
> 678,60), a linha **180GSM**. Mesmo preço, dois rótulos. Um dos dois está errado, e o sistema
> não escolheu: os 280 g ficaram como estavam, e os 180 g receberam a linha que o fornecedor
> rotulou como 180. **Pergunta para a Daune:** existe uma linha de 280 g de poliéster com o
> mesmo preço da de 180 g, ou a informação de 03/09 era a linha de 180 g?

> 🔵 **VALIDAR COM FISCAL/CONTABILIDADE:** os 12% e os 9,25% estão marcados no código como
> *"créditos de ENTRADA aprovados"*, mas **NÃO CONFIRMADO** por quem aprovou nem quando. A
> recuperação depende do regime tributário da Anara e do que a NF da Daune destaca.
> **Esta é provavelmente a pergunta fiscal de maior impacto do documento** — 12% + 9,25%
> compõem 20,14% do preço bruto de todo item Daune.

> 🔴 **ACHADO — B-17, duas fontes com bases diferentes.** O custo persistido de 13 SKUs
> reconciliados é **1,5123× o preço bruto** da fonte Daune, com razão praticamente constante
> (1,51222 a 1,51236) entre produtos sem relação nenhuma. Razão uniforme entre documentos
> diferentes não é coincidência: ou a planilha *Trousseau-Fio a Fio* é outra base — preço de
> **venda**, não custo — ou houve uma transformação uniforme no passado que ninguém registrou.
> **Pergunta para a Daune:** qual é a base da planilha Trousseau?

---

# 10. Decor Tricot

**Mesma mecânica da Daune**, e isso está no código: método `DECOR_DIRECT`, tipo de fornecedor
`NACIONAL`, e `custo_net()` trata os dois pelo mesmo ramo — custo já em reais, sem
nacionalização.

**As diferenças reais, verificadas:**

| | Daune | Decor Tricot |
|---|---|---|
| SKUs ativos | 53 | 17 |
| Sem custo | 15 | 1 |
| Margem-alvo | 14% | 14% |
| Origem logística | vazia | vazia |
| Referências `CONFIRMADO` | 38 | 0 |

> ⚠️ **NÃO CONFIRMADO:** as constantes de crédito no código chamam-se
> `DAUNE_ICMS_CREDITO` e `DAUNE_PIS_COFINS_CREDITO`, e a função `cnet_nacional()` as usa como
> **default para qualquer fornecedor nacional**. Não há constante própria para a Decor.
>
> Na prática isso significa: **se a Decor Tricot tiver situação tributária diferente da Daune,
> o sistema não sabe disso.** Nenhum SKU Decor tem referência de custo `CONFIRMADO` hoje, então
> o efeito prático é nulo — mas a premissa está lá, esperando o primeiro custo Decor cadastrado.
>
> 🔵 **Pergunta para a contabilidade:** os créditos de ICMS e PIS/COFINS da Decor Tricot são
> os mesmos 12% e 9,25% da Daune?

---

# 11. Margens

## 11.1 Matriz completa vigente

Resolvida por **prioridade** — menor número vence. Todas vigentes desde 28/08/2026, nenhuma
com data de encerramento.

| Prior. | Regra | Escopo | Margem |
|---|---|---|---|
| 20 | Daune — padrão | fornecedor Daune | **14%** |
| 20 | Decor Tricot — padrão | fornecedor Decor | **14%** |
| 30 | KTC — Flat Sheet < 300TC | família + fios < 300 | **16%** |
| 30 | KTC — Top Sheet < 300TC | idem | **16%** |
| 30 | KTC — Bottom Sheet < 300TC | idem | **16%** |
| 30 | KTC — Fitted Sheet < 300TC | idem | **16%** |
| 30 | KTC — Flat Sheet ≥ 300TC | família + fios ≥ 300 | **18%** |
| 30 | KTC — Top Sheet ≥ 300TC | idem | **18%** |
| 30 | KTC — Bottom Sheet ≥ 300TC | idem | **18%** |
| 30 | KTC — Fitted Sheet ≥ 300TC | idem | **18%** |
| 40 | KTC — Bath / Hand / Face / Pool / Beach Towel | família | **12%** |
| 40 | KTC — Bath Mat, Wash Cloth, Towel | família | **12%** |
| 40 | KTC — Bathrobe | família | **12%** |
| 60 | KTC — demais famílias | fornecedor KTC | **15%** |
| 900 | Geral — sem fornecedor definido | tudo | **15%** |

**21 regras.** A lógica comercial é legível: cama de alto padrão (300+ fios) tem a maior
margem; banho e roupão, a menor; nacional fica no meio.

> 🟡 **Perguntas para a diretoria:** (1) A escada 12% / 14% / 15% / 16% / 18% continua
> refletindo a estratégia? (2) O corte em 300 fios é o certo, ou deveria ser em 250? (3) Por
> que banho tem margem menor que cama — concorrência, ou giro?

## 11.2 Margem-alvo ≠ markup

Duas medidas diferentes do mesmo negócio, e confundi-las é o erro clássico:

```
MARGEM  =  lucro ÷ RECEITA      ← quanto sobra de cada real vendido
MARKUP  =  lucro ÷ CUSTO        ← quanto se acrescenta sobre o custo
```

Uma margem de 18% corresponde a um markup de ~22%. O sistema trabalha com **margem-alvo**; o
markup é variável interna, resolvida por busca na tabela de comissão, e existe só porque a
faixa de comissão depende dele.

## 11.3 Margem-alvo ≠ margem realizada

Pedir 18% e cobrar R$ 121,56 entrega **17,9993%**. A diferença é o arredondamento comercial —
o preço vira centavo uma vez, e a margem é recalculada sobre o preço que o cliente paga. O
sistema registra os dois números separadamente e **nunca reporta a margem teórica como se
fosse a realizada**.

---

# 12. Comissão

## 12.1 Tabela vigente

`comissao_tabela = [[0.0, 0.05], [0.6, 0.06], [0.7, 0.07], [0.8, 0.08], [0.9, 0.09], [1.0, 0.10]]`

| Markup do item | Comissão |
|---|---|
| de 0% a menos de 60% | **5%** |
| de 60% a menos de 70% | **6%** |
| de 70% a menos de 80% | **7%** |
| de 80% a menos de 90% | **8%** |
| de 90% a menos de 100% | **9%** |
| 100% ou mais | **10%** |

**Fronteiras inclusivas na base:** um markup de exatamente 60% cai na faixa de 6%, não na de
5%. A comparação é feita em `Decimal` exato — em ponto flutuante, 0,6 pode virar
0,5999999999999999 e derrubar o item para a faixa de baixo, mudando o preço inteiro.

## 12.2 Base de cálculo

A comissão é **percentual da receita**, não do lucro nem do custo. Entra no **denominador** do
gross-up, junto com os impostos.

> ⚠️ **A comissão é escalonada pelo markup, e o markup depende do preço, que depende da
> comissão.** É circular. O sistema resolve testando cada faixa e ficando com a que é
> internamente consistente — o markup resultante tem de pertencer à faixa cuja comissão foi
> usada.

> 🟡 **Perguntas para a diretoria:** (1) Estas faixas valem para todos os vendedores? (2) A
> comissão é sobre a venda toda ou só sobre a parte acima de um mínimo? (3) Com margens-alvo
> entre 12% e 18%, o markup dos itens fica bem abaixo de 60% — na prática **quase todo item cai
> na faixa de 5%**. As faixas superiores existem para quê?

---

# 13. Condições de pagamento

| Código | Rótulo | Encargo | Confirmado | Ordem |
|---|---|---|---|---|
| `À VISTA` | À vista | **0,0%** | sim | 10 |
| `30` | 30 dias | **1,6%** | sim | 20 |
| `30/60` | 30/60 dias | **3,2%** | sim | 30 |
| `30/60/90` | 30/60/90 dias | **4,8%** | sim | 40 |
| `30/60/90/120` | 30/60/90/120 dias | **6,4%** | sim | 50 |
| `30/60/90/120/150` | 30/60/90/120/150 dias | **8,0%** | sim | 60 |
| `SINAL30+30/60/90` | 30% de sinal + 30/60/90 | **sem encargo cadastrado** | não | 70 |
| `CARTAO` | Cartão de crédito | **sem encargo cadastrado** | não | 80 |

**Como entra:** o encargo financeiro vai para o **denominador** do preço, junto com ICMS,
PIS/COFINS, comissão e o rate variável de frete.

**A escada é linear:** 1,6% por parcela mensal. Mas o sistema **não deriva isso**.

> **Condição desconhecida não é interpolada.** O sistema não conta barras, não soma 1,6% por
> parcela e não devolve um encargo plausível. Exige condição cadastrada ou override
> autorizado. As duas condições sem encargo — sinal e cartão — **bloqueiam a formação de
> preço** até alguém cadastrar o número.
>
> Isso é deliberado: derivar "30/60/90/120/150/180 = 9,6%" pareceria certo e estaria errado
> se a escada não for linear além de 150 dias.

> 🟡 **Perguntas para o financeiro:** (1) Os 1,6% ao mês continuam corretos? Correspondem a
> qual custo de capital? (2) Qual é o encargo de `SINAL30+30/60/90` e de `CARTAO` — são as duas
> condições que hoje travam a cotação.

---

# 14. Fiscal — o que o sistema precisa saber

Para resolver a tributação de **um item**, o sistema precisa de seis respostas:

```
1. NATUREZA DA MERCADORIA     importada ou nacional?
        └─ vem do tipo do fornecedor, ou de override no SKU
                              KTC → IMPORTADA · Daune/Decor → NACIONAL

2. UF DE ORIGEM FISCAL        de onde sai a NF?
        └─ cotação → cadastro do fornecedor → premissa padrão (hoje: SP)
           ⚠ NÃO é a origem logística

3. UF DE DESTINO              para onde vai?
        └─ do cabeçalho da cotação. Sem ela, o item BLOQUEIA

4. CONTRIBUINTE?              o cliente é contribuinte de ICMS?
        └─ do cabeçalho da cotação

5. FINALIDADE                 revenda, uso e consumo, industrialização, ativo?
        └─ cotação → cliente → premissa padrão (hoje: USO_CONSUMO)

6. CONSUMIDOR FINAL?          derivado da finalidade
```

Com isso resolve: **alíquota de ICMS · DIFAL · FCP · quem recolhe**.

> **Sem qualquer uma dessas respostas, o item vai para *revisão necessária*.** Não há
> alíquota padrão de segurança.

---

# 15. ICMS

## 15.1 Operação interna (origem = destino)

Aplica-se a **alíquota interna do estado de destino**, direto da tabela.

Exemplos: SP 18% · RJ 22% · SC 17% · MG 18% · BA 20,5% · MT 17% · MA 23%.

**27 estados cadastrados**, todos ativos.

## 15.2 Interestadual

| Natureza | Alíquota | Linhas cadastradas |
|---|---|---|
| **Importada** | **4%** | 26 pares (SP → todos os demais) |
| **Nacional** | **7%** | 21 pares |
| **Nacional** | **12%** | 5 pares: SP→MG, SP→PR, SP→RJ, SP→RS, SP→SC |

A lógica é a da legislação: mercadoria importada tem alíquota interestadual única de 4%;
mercadoria nacional saindo do Sudeste/Sul tem 12% para o próprio Sul/Sudeste e 7% para
Norte, Nordeste e Centro-Oeste.

> **Todas as linhas partem de SP.** Não há alíquota cadastrada para origem diferente de São
> Paulo. Se a origem fiscal mudar, o par não é encontrado e o item **bloqueia com mensagem
> nomeando o par que falta** — não usa um valor aproximado.

## 15.3 Matriz por tipo de operação

| Operação | Cliente | ICMS na venda | DIFAL | Quem recolhe o DIFAL |
|---|---|---|---|---|
| SP → SP | qualquer | 18% interna | não há | — |
| SP → RJ | contribuinte | 4% (KTC) / 12% (nacional) | sim | **destinatário** |
| SP → RJ | não contribuinte | 4% / 12% | sim | **remetente (Anara)** |
| SP → MG | contribuinte | 4% / 12% | sim | destinatário |
| SP → BA | contribuinte | 4% / 7% | sim | destinatário |

## 15.4 O resíduo

> ⬛ **`icms_fallback_pct = 18%` não alimenta o preço.** Aposentada na Onda 1, permanece
> cadastrada por rastreabilidade. Aparece no painel de configurações como premissa crítica —
> **resíduo de interface**, não regra vigente. Não usar.

---

# 16. DIFAL

**Quando existe:** só em operação interestadual. Operação interna não tem DIFAL.

**A regra de responsabilidade (Q-08):**

| Cliente | Quem recolhe | Entra no preço da Anara? |
|---|---|---|
| **Contribuinte** | o destinatário | **Não.** Não reduz a margem da Anara |
| **Não contribuinte** | o remetente (Anara) | **Sim.** Entra no waterfall |

**Consequência comercial, medida:** um cliente **não contribuinte** em venda interestadual
custa significativamente mais caro que um contribuinte, porque a Anara assume o DIFAL. Dentro
de SP a diferença é zero.

> **A carga final entra como está.** O sistema usa `EstadoFiscal.carga_final` do cadastro e
> **não recalcula** por base simples, base dupla ou FEM. Exemplos vigentes: SP 17,07% ·
> RJ 20,00% · MG 17,07% · BA 22,75% · SC 13,00%.

> 🔵 **VALIDAR COM FISCAL — de alto impacto.** As cargas finais foram cadastradas de uma
> fonte que o sistema não registra. Elas decidem quanto a Anara paga de DIFAL em toda venda
> interestadual a não contribuinte. **NÃO CONFIRMADO** de onde vieram nem quando.

---

# 17. FCP

Fundo de Combate à Pobreza — adicional de ICMS em alguns estados.

## 17.1 Como o sistema trata

**Exige regra cadastrada.** Não é lido de uma coluna por estado, e a razão é boa: **o FCP não
incide sobre tudo que entra num estado** — a incidência depende do produto, e a lista varia
por UF e por vigência.

Três situações possíveis:

| Situação | Significado | Efeito |
|---|---|---|
| `APLICA` | incide, com a alíquota cadastrada | soma ao ICMS |
| `NAO_APLICA` | comprovadamente não incide (fonte obrigatória) | zero |
| `DESCONHECIDO` | pode incidir e ninguém levantou | bloqueia se marcado `exige_confirmacao` |

**Sem linha que cubra a operação, o FCP é zero** e a memória registra que nenhuma regra foi
encontrada. Não bloqueia.

## 17.2 O que está cadastrado hoje

| UF | NCM | Família | FCP | Situação | Fonte |
|---|---|---|---|---|---|
| **RJ** | todos | todas | **2%** | `APLICA` | *Regra canônica Anara 03/09/2026 — RJ: ICMS 20% + FECP 2% = 22%* |

**Uma linha. Só o Rio de Janeiro.**

> 🔴 **ACHADO DE MAIOR IMPACTO FISCAL.** A coluna `EstadoFiscal.fem` mostra **BA, PE, PI e RJ
> com 2%**. Só o RJ tem `RegraFcp` cadastrada.
>
> Na prática: uma venda para a **Bahia**, **Pernambuco** ou **Piauí** hoje sai **sem FCP**,
> porque não existe regra. Não é bug — é a política de "não inventar" funcionando. Mas
> significa que essas vendas podem estar **subprecificadas em 2 pontos percentuais**.
>
> O código diz explicitamente que `EstadoFiscal.fem` *"não alimenta o motor"* — existe para
> rastreabilidade da tabela histórica.
>
> 🔵 **Pergunta para o fiscal:** o FCP de BA, PE e PI incide sobre roupa de cama e banho? Em
> que alíquota? Se incidir, três estados precisam de `RegraFcp` cadastrada.

---

# 18. PIS/COFINS da venda

**Corrigido em 09/09/2026.** Até essa data o sistema aplicava **7,59% fixos** a toda venda.
Não é constante: o ICMS é excluído da base de PIS/COFINS, então o percentual efetivo depende
da alíquota de ICMS da operação.

| | |
|---|---|
| Alíquota **nominal** | **9,25%** (PIS 1,65% + COFINS 7,60%) |
| Alíquota **efetiva** | `9,25% × (1 − ICMS da operação)` |
| Desde | 09/09/2026 |
| Fonte | Brendo Simão — Contabilidade da Indústria Química Anastacio, 09/09/2026 · planilha *"Fator Cálculo Exclusão ICMS .xlsx"* |
| Onde entra | o **efetivo** vai ao denominador do gross-up, calculado **por item** |
| Premissa versionada | `pis_cofins_nominal_pct = 0,0925` |

### Golden

| ICMS excluído da base | PIS/COFINS efetivo | Diferença contra os 7,59% antigos |
|---|---|---|
| 18% | **7,585%** | −0,005 p.p. |
| 12% | **8,14%** | **+0,55 p.p.** |
| 7% | **8,6025%** | **+1,0125 p.p.** |
| 4% | **8,88%** | **+1,29 p.p.** |
| 20% (RJ não contribuinte: 22% de carga **menos 2% de FCP**) | **7,40%** | −0,19 p.p. |

### Por que 7,59% estava errado

7,59% é a aproximação do cenário de ICMS 18% — 7,585%. Como regra fixa, subestimava o encargo
em **toda venda interestadual**, que é justamente onde a alíquota cai para 12%, 7% ou 4%.
Quanto menor o ICMS, maior o erro. Em ICMS 4% o encargo real é 8,88% contra os 7,59%
aplicados: **1,29 ponto percentual** de imposto a menos no denominador, e portanto preço
menor do que o necessário para entregar a margem-alvo.

Medido no catálogo real, mantendo o preço antigo com o encargo verdadeiro: um SKU Daune com
margem-alvo de 14% entrega **12,99%** numa venda para a Bahia. A margem some no imposto.

### Qual ICMS é excluído da base

**`ResultadoFiscal.icms_pct` menos o `fcp_pct`.** A carga de ICMS que reduz a receita da Anara,
resolvida por item pelo motor fiscal, **descontado o FCP** — ver a seção seguinte para o
porquê. Consequências:

- na venda a **não contribuinte**, o **DIFAL** recolhido pela remetente está dentro desse
  número e **participa da exclusão**: sai do bolso da Anara e reduz a base como qualquer ICMS.
  SP→RJ soma 22% de carga, exclui 20% e o efetivo cai para 7,40% — ICMS maior significa base
  de PIS/COFINS menor;
- o FCP entra **uma vez só** na carga, e **zero vezes** na exclusão. O motor fiscal já o
  consolidou; recompor `interestadual + DIFAL + FCP` fora dele contaria o FECP do RJ em dobro;
- **`EstadoFiscal.carga_final` NÃO participa.** Ela expressa o diferencial sobre uma base
  anterior à inclusão do ICMS de destino e não é percentual da receita final.

### O que está validado e o que não está

| | Item | Situação |
|---|---|---|
| 🟢 | Alíquota nominal de 9,25% | **VALIDADO** — Brendo Simão, 09/09/2026 |
| 🟢 | O ICMS é excluído da base de PIS/COFINS | **VALIDADO** — mesma fonte |
| 🟢 | O efetivo varia por item, conforme a alíquota da operação | **VALIDADO** — mesma fonte |
| 🟢 | O DIFAL suportado pela Anara é excluído junto | **VALIDADO** — é ICMS, e é ônus dela |
| 🟡 | O **FCP/FECP** reduz ou não a base | **PENDENTE** — não perguntado, não respondido |

**Política operacional enquanto a validação não vem:**

> Enquanto não houver validação específica da contabilidade da Química Anastacio, **o FCP não
> reduz a base de PIS/COFINS**.

Isto é escolha **conservadora e temporária**, não conclusão jurídica. Manter o FCP na base
produz alíquota efetiva **maior** — o sistema reconhece mais imposto, não menos. Se a
contabilidade confirmar depois que o FCP também sai da base, o efetivo cai de 7,40% para
7,215% no RJ e o preço acompanha; o caminho inverso teria emitido proposta com imposto
subestimado, que é o erro que esta correção existe para não repetir.

**Alcance da pendência:** só cenários com FCP cadastrado. Hoje isso é **apenas o RJ**, e
apenas na venda a não contribuinte — nos demais o `fcp_pct` é zero e excluído == carga total.

> 🟡 **PERGUNTA PARA A CONTABILIDADE:** o FCP/FECP também é excluído da base de PIS/COFINS,
> como o ICMS próprio e o DIFAL? Se sim, o efetivo do RJ não contribuinte passa de 7,40% para
> 7,215%.

### Nominal ≠ efetivo, e nenhum dos dois é o crédito de compra

Três números de 9,25% e 7,59% circulam pelo sistema e **não se somam**:

| Número | O que é | Onde vive |
|---|---|---|
| **9,25% nominal da venda** | base do cálculo do efetivo; **nunca** incide cheio sobre o faturamento | `pis_cofins_nominal_pct` |
| **7,585%–8,88% efetivo** | o que de fato reduz a receita, por item | derivado, no denominador |
| **9,25% de crédito de compra** | o que a Anara **recupera** ao comprar da Daune | `DAUNE_PIS_COFINS_CREDITO` |
| 7,59% legado | efetivo fixo da metodologia anterior | `pis_cofins_pct`, só histórico |

O crédito de compra **não mudou** e não tem relação com esta correção — a exclusão do ICMS já
acontece na base dele (`bruto − ICMS`), e a alíquota aplicada ali é a cheia.

---

# 19. Frete nacional — CIF × FOB

| | FOB | CIF |
|---|---|---|
| Quem paga | o cliente | a Anara |
| Entra no preço? | **não** | **sim** |
| Bloqueia se irresolvido? | não | **sim** |

**Por que CIF precisa entrar no preço:** se a Anara paga o frete, ele é custo da operação. Não
incluí-lo significa vender com margem menor do que a calculada — e o erro cresce com a
distância.

**Por que bloqueia:** um frete desconhecido tratado como zero produziria um preço que parece
certo e está errado. O sistema recusa emitir.

`A_COMBINAR` é termo comercial deliberado e **não bloqueia**.

---

# 20. TRANSAL — a tabela de frete cadastrada

## 20.1 A tabela

| | |
|---|---|
| Transportadora | TRANSAL |
| Documento | `Tabela TRANSAL - frete nacional 2026-02.xlsx` |
| Data da fonte | 01/02/2026 |
| **Origem logística** | **Itajaí — SC** |
| **Vigência** | 01/02/2026 → **31/12/2026** |
| Unidade da tarifa | R$ por **tonelada** |
| Unidade da faixa | **kg** |
| Mínimo | R$ por **embarque** |
| Fator de cubagem | **300 kg/m³** |
| Base do pedágio | **DESCONHECIDO** |
| Situação do ICMS | **DESCONHECIDO** |

## 20.2 Regiões e tarifas — tabela completa

| Região | Até 7.000 kg | Acima de 7.000 kg | Mínimo | Prazo | Cidades |
|---|---|---|---|---|---|
| Itajaí — SC | R$ 270/t | R$ 243/t | R$ 81 | 24 h | 22 |
| Joinville — SC | R$ 281/t | R$ 252/t | R$ 84 | 24 h | 13 |
| Palhoça — SC | R$ 281/t | R$ 252/t | R$ 84 | 24 h | 15 |
| Colombo — PR | R$ 287/t | R$ 259/t | R$ 86 | 24–48 h | 14 |
| Morro da Fumaça — SC | R$ 333/t | R$ 299/t | R$ 99 | 24–48 h | 24 |
| Guarulhos — SP | R$ 439/t | R$ 395/t | R$ 132 | 48 h | 24 |
| Jundiaí — SP | R$ 439/t | R$ 395/t | R$ 132 | 48 h | 49 |
| Cachoeirinha — RS | R$ 598/t | R$ 538/t | R$ 179 | 24–48 h | 47 |
| Farroupilha — RS | R$ 632/t | R$ 569/t | R$ 189 | 48 h | 30 |
| **Passo Fundo — RS** | **— sem tarifa** | **—** | **—** | **—** | **0** |

**9 regiões com tarifa · 238 cidades cobertas · 20 faixas de peso.**

> 🟠 **Passo Fundo-RS existe na tabela sem tarifa, mínimo ou prazo**, e nenhuma cidade da
> cobertura aponta para ela. Regra conservadora: frete a cotar para a região e para qualquer
> cidade fora das 238 — **sem aproximar por região vizinha**.

## 20.3 Peso real, cubado e taxado

```
peso_cubado  =  volume_m³ × 300
peso_taxado  =  max(peso_real, peso_cubado)
```

**Por que existe:** um caminhão enche por volume antes de encher por peso. Travesseiro e
edredom ocupam muito e pesam pouco — cobrar por peso real seria transporte de graça.

> 🔴 **O catálogo não tem volume cadastrado em nenhum SKU.** Sem volume, o peso cubado não é
> calculável. O sistema tem uma hierarquia de resolução (volume no SKU → volume informado no
> embarque → peso taxado confirmado pela transportadora), mas hoje nenhum SKU tem a primeira.

---

# 21. Componentes do frete

Onze componentes cadastrados.

| Código | Nome | Tipo | Valor | Situação | Automático | Entra como |
|---|---|---|---|---|---|---|
| `ADV` | Ad valorem | % da NF | **0,20%** | `APLICA` | sim | **RV** |
| `GRIS` | GRIS | % da NF | **0,10%** | 🔴 `DESCONHECIDO` | sim | **RV** |
| `FIEL_DEPOSITARIO` | Taxa de fiel depositário | % da NF | **0,50%** | 🔴 `DESCONHECIDO` | sim | **RV** |
| `PEDAGIO` | Pedágio | R$/kg | **0,0536** | `APLICA` | sim | **CF** |
| `PALETIZACAO` | Paletização | fixo | R$ 91/pallet PBR | `APLICA` | não | CF |
| `TDE` | Dificuldade de entrega | R$/hora | 272,00 | `APLICA` | não | CF |
| `TDC` | Dificuldade de coleta | R$/hora | 272,00 | `APLICA` | não | CF |
| `AGENDAMENTO_TRUCK` | Agendamento — Truck | fixo | R$ 1.431/veículo | `APLICA` | não | CF |
| `REENTREGA` | Reentrega | % do frete | 50% | `APLICA` | não | CF |
| `DEVOLUCAO` | Devolução | % do frete | 100% | `APLICA` | não | CF |
| `FIM_DE_SEMANA` | Entrega em fim de semana/feriado | % do frete | 30% | `APLICA` | não | CF |

**"Automático"** significa que o componente entra em todo cálculo. Os não automáticos entram
só quando a operação os exige — paletização, reentrega, agendamento.

**A taxa variável hoje:**

```
RV = ADV (0,20%) + GRIS (0,10%)  =  0,30%
```

Se o fiel depositário incidir sempre: **RV = 0,80%**. Quase o triplo.

---

# 22. Pendências TRANSAL — as perguntas que travam o CIF

## 🔴 C-NEW-01 — ICMS da prestação de frete

**Pergunta:** o ICMS da prestação já está incluso na tarifa, ou o valor sofre gross-up de
`÷ (1 − 12%)`?

**Por que importa:** 12% sobre todo o frete CIF. É o maior componente isolado da conta.

**O que temos — três evidências que não se reconciliam:**

| | Evidência | Diz | No repositório? |
|---|---|---|---|
| A | Texto da própria tabela | *"ICMS conforme legislação"* — ambíguo | sim |
| B | Informação posterior da transportadora | ICMS já estaria incluso | **não** — só como afirmação |
| C | Exemplo numérico da própria tabela | executa gross-up de 12% | sim, confere em 10 casas |

O exemplo da tabela, reconstituído: frete R$ 299,00 + ADV R$ 26,70 + pedágio R$ 26,80 =
R$ 352,50; `352,50 ÷ 0,88 = 400,5681818…`, e a célula "Frete Total" traz
400,5681818181818.

**Mas o mesmo exemplo omite dois componentes que a tabela define:** GRIS (R$ 13,35) e fiel
depositário (R$ 66,75). Isso enfraquece o exemplo como fonte.

**O que o sistema faz hoje:** `icms_situacao = DESCONHECIDO`, e o frete CIF bloqueia.

**Se não respondermos:** CIF continua indisponível. FOB funciona normalmente.

**Quem valida:** 🟠 TRANSAL, por escrito, com data e autoria.

## 🔴 C-NEW-06 — Fiel depositário

**Pergunta:** a taxa de 0,5% do valor da NF incide em toda carga, ou só em situação
específica?

**Por que importa:** é um **termo da equação**, não um detalhe de cadastro. Muda a taxa
variável de 0,30% para 0,80%, dentro da fórmula fechada que resolve a circularidade
comissão × markup.

**O que temos:** a tabela cobra *"TAXA DE FIEL DEPOSITÁRIO, SERÁ COBRADO 0,5% DO VALOR DA
NOTA FISCAL"*. Não é fato que se aplique a toda carga.

**O que o sistema faz hoje:** `situacao = DESCONHECIDO`.

**Quem valida:** 🟠 TRANSAL.

## 🟠 C-NEW-02 — GRIS

**Pergunta:** o GRIS de 0,10% incide sempre?

**O que é fato:** a coluna existe e vale 0,10% nas 9 regiões tarifadas.
**O que não é fato:** que incida sempre — o exemplo da própria tabela soma ADV e pedágio e
**omite o GRIS**.

## 🟠 C-NEW-08 — Base do pedágio

**Pergunta:** os R$ 0,0536 incidem sobre peso **real** ou **taxado**?

**Por que importa:** só quando os dois divergem — ou seja, exatamente no caso da cubagem, que
é o caso de travesseiro e edredom.

**O que o sistema faz hoje:** `pedagio_base = DESCONHECIDO`. O cálculo **segue** quando peso
real e taxado coincidem, e **bloqueia** quando divergem. Proporcional, sem escolher por
conveniência.

## 🟠 Q-L — Origem logística de Daune e Decor

**Situação:** ambas embarcam de **São Paulo** (informado pela diretoria). A única tabela de
frete cadastrada tem origem **Itajaí-SC**.

**Consequência:** não existe tarifa saindo de São Paulo. O CIF de Daune e Decor fica a cotar.

**O que fazer:** pedir cotação de frete com origem São Paulo, de qualquer transportadora.

**Quem valida:** 🟠 Logística.

## 🟠 C-NEW-07 — Validade da tabela

A tabela **vence em 31/12/2026**, com duas cláusulas de revisão: política de preços de
combustível da Petrobras e queda de volumetria. Vencida sem substituta, o sistema bloqueia em
vez de usar tarifa velha em silêncio.

---

# 23. CF × RV — por que o frete entra em dois lugares

Nem todo componente de frete é um valor fixo, e tratá-los como se fossem produz um preço que
não fecha.

```
CF  →  custo fixo do embarque, em R$        →  NUMERADOR, junto com o custo
       frete-peso, pedágio, paletização

RV  →  percentual sobre o valor da NF       →  DENOMINADOR, junto com os impostos
       ADV, GRIS, fiel depositário
```

**Por que o RV não pode ir para o numerador:** ele é percentual da própria receita que o preço
ainda vai formar. Congelá-lo sobre um preço preliminar faria a margem-alvo não fechar.

**Exemplo simples.** Custo R$ 100, margem-alvo 18%, impostos 25,59%, comissão 5%, CF de
R$ 10, RV de 0,30%:

```
custo efetivo = 100 + 10 = 110
preço = 110 × (1 + markup) ÷ (1 − 0,2559 − 0,05 − 0,003)
```

O CF entra no que se multiplica; o RV, no que se divide. Depois de o preço estar formado, o RV
é recomposto em reais para aparecer no waterfall.

---

# 24. A fórmula final

## 24.1 Modo margem — o usado por padrão

Dada a margem-alvo `m`:

```
E      =  m ÷ (1 − taxas − comissão(E) − m)          markup consistente com a margem
F      =  custo_efetivo × (1 + E)
preço  =  F ÷ (1 − ICMS − PIS/COFINS − encargo − RV − comissão(E))
```

## 24.2 Modo preço — quando o vendedor digita o valor

Dado o preço:

```
E      =  preço × (1 − taxas − comissão) ÷ custo_efetivo − 1
lucro  =  faturamento − impostos − comissão − frete_CF − frete_RV − custo_total
margem =  lucro ÷ faturamento
```

## 24.3 Cada variável

| Variável | O que é | Numerador ou denominador |
|---|---|---|
| **CNET** | custo da mercadoria em reais, sem imposto | numerador |
| **CF** | custo fixo do embarque rateado ao item | numerador |
| **E (markup)** | alavanca interna sobre o custo | numerador |
| **ICMS** | do cenário fiscal do item | denominador |
| **PIS/COFINS** | efetivo do item: `9,25% × (1 − ICMS)` | denominador |
| **encargo** | da condição de pagamento | denominador |
| **RV** | rate variável de frete sobre a NF | denominador |
| **comissão** | da faixa do markup | denominador |
| **margem** | o que sobra | resultado |

**A lógica do denominador:** tudo que é percentual da **receita** entra ali. O preço tem de ser
grande o bastante para pagar todos eles e ainda deixar a margem.

---

# 25. Arredondamento — o centavo é determinístico

| Regra | O que significa |
|---|---|
| Núcleo em `Decimal` | Nada de ponto flutuante binário na conta do dinheiro |
| Precisão interna de 34 dígitos | EXW, câmbio, consumo e taxas trabalham cheios |
| **Uma quantização só** | O preço vira centavo **uma vez**, quando vira preço |
| `ROUND_HALF_UP` | R$ 1,005 → R$ 1,01. O `round()` do Python daria 1,00 |
| Componentes recompostos | Impostos, comissão e frete recalculados **sobre o preço arredondado** |
| Lucro é resíduo | Receita menos os componentes já quantizados |
| Rateio pelo maior resto | R$ 100 entre 3 itens dá 33,34 + 33,33 + 33,33, não 99,99 |
| CNET não é arredondado | É custo interno |

**O que isso garante:** a linha da cotação **fecha ao centavo por construção**. Não há
"aproximadamente". O cliente soma a coluna e bate.

---

# 26. Preço recomendado × preço negociado

| Conceito | O que é |
|---|---|
| **Preço recomendado** | O que o motor forma **para o cenário desta cotação**, na margem-alvo |
| **Preço base** | Referência do catálogo, formada noutro contexto fiscal |
| **Preço negociado** | O que o vendedor decidiu cobrar |
| **Receita real** | preço negociado × quantidade |
| **Margem realizada** | recalculada sobre a receita real |
| **Comissão** | recalculada — a faixa pode mudar |

> **Preço recomendado ≠ preço base.** O recomendado considera destino fiscal, contribuinte e
> condição de pagamento **desta** venda. Medir desconto contra o preço-base faria toda venda
> interestadual parecer exceção.

Negociar preço muda a economia real: baixar 10% não tira 10% do lucro — tira mais, porque
impostos e comissão acompanham a receita enquanto o custo não se mexe.

---

# 27. Aprovação comercial

Duas regras **independentes** disparam aprovação:

1. **Preço negociado abaixo do recomendado** — por item, mesmo com margem saudável. A
   autonomia de desconto do vendedor é zero.
2. **Margem realizada abaixo da margem-alvo** — mesmo sem desconto: pode ter subido o custo,
   mudado o imposto ou entrado frete.

Mais uma da cotação inteira: **manter premissas anteriores às vigentes**.

**Detecção por item.** Desconto no item A compensado por acréscimo no B continua sendo exceção
do A — o total esconderia a decisão.

> **Blocker não é aprovável.** Custo a cotar, revisão necessária e frete CIF irresolvido **não
> passam por alçada nenhuma**. Aprovação é decisão comercial; ela não cria o número que falta.

---

# 28. Status de confiança do custo

| Status | Significa | Cota? | PDF? | Compromisso firme? | Quem age |
|---|---|---|---|---|---|
| **Custo confirmado** | Referência direta, atual e confiável | sim | sim | **sim** | ninguém |
| **Custo estimado** | Formado por comparação — curva, análogo, interpolação | sim | sim | **não** | quem cota com o fornecedor |
| **Custo a revalidar** | Número direto, mas envelheceu ou tem anomalia | sim, com alerta | sim | **não** até reconfirmar | quem reconfirma a referência |
| **Preço sob consulta** | Não existe base de custo | **não** | **não** | não | quem cota com o fornecedor |
| **Revisão necessária** | Premissa, fiscal ou rastreabilidade quebrada | **não** | **não** | não | quem resolve a premissa |

**As três distinções que custam caro se forem confundidas:**

- **Revalidar ≠ Estimado** — o revalidar tem número próprio e direto; o estimado veio de proxy
- **Revalidar ≠ Sob consulta** — o revalidar já tem número utilizável
- **Revalidar ≠ Revisão necessária** — envelhecer não é erro de cálculo

**Estimado nunca vira confirmado em silêncio.**

---

# 29. Exemplos completos

## 29.1 KTC — lençol industrial

**Lençol plano hotel 160×310 · 300 fios · 100% algodão**
Cenário: SP → SP, não contribuinte, 30 dias, sem frete incluso

```
ESPECIFICAÇÃO      160 × 310 cm · 300 fios · 100% algodão · Sateen · plain
                            ↓
BAINHA             164,0 × 314,0 cm                      (+4 cm cada lado)
                            ↓
ENCOLHIMENTO       172,2 × 329,7 cm                      × (1 + 5%)
                            ↓
ÁREA               5,677434 m²                           1 painel
                            ↓
WASTE              5,853025 m²                           ÷ (1 − 3%)
                            ↓
TECIDO             US$ 8,194235                          × US$ 1,40/m²
                            ↓
CMT                US$ 8,944235                          + US$ 0,75
                            ↓
2ª QUALIDADE       US$ 9,034580                          ÷ (1 − 1%)
                            ↓
MARGEM KTC         US$ 10,628918   ← EXW                 ÷ (1 − 15%)
                            ↓
FRETE INTERNAC.    US$ 0,402843                          0,780704 kg × 0,516
                            ↓
I.I.               US$ 0,386112                          3,5% sobre 11,031761
                            ↓
OUTRAS DESPESAS    US$ 0,248753
                            ↓
NET USD            US$ 11,666626
                            ↓
CÂMBIO             R$ 60,549791    ← CNET                × 5,19
                            ↓
ICMS 18% · PIS/COFINS 7,585% · encargo 1,6% · comissão 5%
MARGEM-ALVO 18%    (KTC — Flat Sheet ≥ 300TC)
                            ↓
PREÇO RECOMENDADO  R$ 121,55
```

**Decomposição do preço:**

| Componente | Valor | % da receita |
|---|---|---|
| Custo (CNET) | R$ 60,55 | 49,8% |
| Impostos | R$ 33,05 | 27,2% |
| Comissão | R$ 6,08 | 5,0% |
| **Lucro** | **R$ 21,88** | **18,0%** |
| **Preço** | **R$ 121,56** | 100% |

Margem realizada: **17,9993%** — a diferença para 18% é o centavo comercial.

## 29.2 KTC — toalha

**Toalha de banho 100×150 · 450 g/m² · 100% algodão**

```
ESPECIFICAÇÃO      100 × 150 cm · 450 g/m²
                            ↓
PESO DA PEÇA       0,675 kg              100 × 150 × 450 ÷ 10.000.000
                            ↓
CUSTO DO FIO       US$ 5,7375   ← EXW    0,675 kg × US$ 8,50/kg
                            ↓
FRETE INTERNAC.    US$ 0,348300          0,675 kg × 0,516
                            ↓
I.I.               US$ 0,213003          3,5%
                            ↓
OUTRAS DESPESAS    US$ 0,248753
                            ↓
NET USD            US$ 6,547556
                            ↓
CÂMBIO             R$ 33,981817 ← CNET   × 5,19
                            ↓
MARGEM-ALVO 12%    (KTC — Bath Towel)
                            ↓
PREÇO RECOMENDADO  R$ 60,89
```

> **Duas etapas, não onze.** A taxa por quilo do terry já é EXW final — não passa por CMT,
> segunda qualidade nem margem KTC. É a diferença estrutural entre cama e banho.

| Componente | Valor | % |
|---|---|---|
| Custo | R$ 33,98 | 55,8% |
| Impostos | R$ 16,56 | 27,2% |
| Comissão | R$ 3,04 | 5,0% |
| **Lucro** | **R$ 7,31** | **12,0%** |
| **Preço** | **R$ 60,89** | 100% |

## 29.3 Daune — nacional

**Travesseiro 50×70 · 100% plumas de ganso**

```
PREÇO BRUTO DAUNE  R$ 249,37
                            ↓
CRÉDITO ICMS 12%   − R$ 29,92
                            ↓
BASE PIS/COFINS    R$ 219,45
                            ↓
CRÉDITO 9,25%      − R$ 20,30
                            ↓
CNET               R$ 199,146882          fator 0,7986
                            ↓
MARGEM-ALVO 14%    (Daune — padrão)
                            ↓
PREÇO RECOMENDADO  R$ 370,09
```

**Sem motor industrial, sem nacionalização, sem câmbio.** O caminho tem quatro etapas.

| Componente | Valor | % |
|---|---|---|
| Custo | R$ 199,15 | 53,8% |
| Impostos | R$ 100,63 | 27,2% |
| Comissão | R$ 18,50 | 5,0% |
| **Lucro** | **R$ 51,81** | **14,0%** |
| **Preço** | **R$ 370,09** | 100% |

## 29.4 O padrão que aparece nos três

Impostos = **27,2%** da receita nos três exemplos, e comissão = **5,0%**. Isso não é
coincidência: com ICMS 18% + PIS/COFINS 7,585% + encargo 1,6% no denominador, a carga é a mesma
independentemente do custo. **O que diferencia os três é a margem-alvo.**

> Os exemplos desta seção são intraestaduais, e é por isso que a correção de 09/09/2026 quase
> não os move: em ICMS 18% o efetivo passa de 7,59% para 7,585%, um centavo de preço. Fora de
> SP a história é outra — ver §18.

---

# 30. Análise de sensibilidade

Simulação **em memória**, sem alterar nada. Base: lençol plano 160×310 · 300 fios,
R$ 121,56.

| Cenário | Preço | Δ R$ | Δ % |
|---|---|---|---|
| **BASE** (câmbio 5,19 · margem 18%) | R$ 121,56 | — | — |
| Câmbio **+1%** (5,2419) | R$ 122,78 | +1,22 | **+1,00%** |
| Câmbio **−1%** (5,1381) | R$ 120,35 | −1,21 | **−1,00%** |
| Frete internacional **+10%** | R$ 122,00 | +0,44 | **+0,36%** |
| Margem **+1 p.p.** (19%) | R$ 124,05 | +2,49 | **+2,05%** |
| Margem **−1 p.p.** (17%) | R$ 119,17 | −2,39 | **−1,97%** |

**Leitura:**

- **Câmbio é linear** — 1% de dólar é 1% de preço, para todo produto importado
- **Margem alavanca ~2×** — 1 ponto percentual de margem move ~2% de preço, porque atravessa
  o gross-up
- **Frete internacional é pouco sensível** — 10% de aumento move 0,36%, porque é ~3,5% do NET

> A alavanca mais forte sob controle da Anara é a **margem**. A mais forte fora do controle é
> o **câmbio**.

---

# 31. Matriz de validação — para a reunião

| # | Tema | Premissa atual | Fonte | Impacto | Pergunta | Responsável | Status |
|---|---|---|---|---|---|---|---|
| 1 | Câmbio | R$ 5,19 | email diário | **altíssimo** | Qual câmbio usar: do dia, média, de fechamento, ou de trabalho com colchão? | Diretoria | 🟡 |
| 2 | Frete internacional | US$ 0,516/kg (645÷1.250) | planilha 05_Premissas | médio | US$ 645 por 1.250 kg continua representativo? De quando é o embarque? | Logística | 🟡 |
| 3 | Outras despesas | US$ 0,2488/un | planilha | médio | O que compõe? Por que por unidade e não por valor? | Financeiro | 🟡 |
| 4 | Margem KTC | 15% no EXW | planilha KTC | alto | A KTC confirma 15% sobre o preço final? | Diretoria/KTC | 🟡 |
| 5 | Waste / 2ª qualidade | 3% / 1% | planilha KTC | médio | Continuam válidos? Variam por construção? | KTC | 🟡 |
| 6 | Encolhimento | 5% algodão / 3% CVC | planilha KTC | médio | Confirmados pela KTC? | KTC | 🟡 |
| 7 | Preço do tecido | 12 combinações, US$ 1,10–1,55/m² | planilha KTC | **alto** | A KTC avisou que "podem variar". Qual a validade? | KTC | 🟡 |
| 8 | Tecido 500/800 fios | **não existe na tabela** | — | alto | Vamos vender 500/800 fios? Se sim, precisamos do preço | KTC | 🔴 |
| 9 | Terry Pool Towel | US$ 14,00/kg | derivado da PI | médio | 65% acima do banho liso. O listrado justifica? | KTC | 🟡 |
| 10 | I.I. Duvet Insert | **sem valor confiável** | — | **alto** | Qual o I.I. do NCM 9404.40.00? O antigo 7% não tem documento | Importação | 🔴 |
| 11 | Preferência Egito | 3,5% | tabela preferencial | alto | Há requisito de certificado de origem? | Importação | 🔵 |
| 12 | Créditos Daune | 12% ICMS + 9,25% PIS/COFINS | "aprovados" | **altíssimo** | A contabilidade valida? Somam 20,14% do bruto | Fiscal | 🔵 |
| 13 | Créditos Decor | **mesmos da Daune, por default** | — | médio | A Decor tem a mesma situação tributária? | Fiscal | 🔵 |
| 14 | Base Daune | razão 1,5123 entre duas fontes | B-17 | **alto** | Qual é a base da planilha Trousseau — custo ou venda? | Daune | 🔴 |
| 15 | Margens | 12% a 18% por família | decisão interna | **altíssimo** | A escada continua certa? O corte em 300 fios? | Diretoria | 🟡 |
| 16 | Comissão | 5% a 10% por faixa de markup | planilha | alto | Quase todo item cai em 5%. As faixas superiores servem para quê? | Diretoria | 🟡 |
| 17 | Encargo financeiro | 1,6% por parcela | planilha | alto | Corresponde a qual custo de capital? | Financeiro | 🟡 |
| 18 | Sinal e cartão | **sem encargo cadastrado** | — | médio | Qual o encargo? Hoje bloqueiam a cotação | Financeiro | 🔴 |
| 19 | PIS/COFINS venda | **9,25% nominal**, efetivo `× (1 − ICMS)` | Contabilidade Química Anastacio, 09/09/2026 | — | **RESPONDIDA.** A base exclui o ICMS da operação; 7,59% fixo era erro. Ver §18 | Fiscal | 🟢 |
| 20 | Carga final DIFAL | por estado, do cadastro | fonte não registrada | **altíssimo** | De onde vieram as cargas finais? | Fiscal | 🔵 |
| 21 | FCP | **só RJ cadastrado** | decisão 03/09 | **alto** | BA, PE e PI têm FEM de 2% e nenhuma regra. Incide? | Fiscal | 🔴 |
| 22 | Origem fiscal padrão | SP | decisão 03/09 | alto | Toda NF sai de SP? | Fiscal | 🔵 |
| 23 | Finalidade padrão | uso e consumo | decisão 03/09 | médio | É a finalidade típica dos clientes? | Fiscal/Comercial | 🔵 |
| 24 | ICMS do frete | **desconhecido** | 3 evidências | **alto** | Incluso ou gross-up de 12%? | TRANSAL | 🔴 |
| 25 | Fiel depositário | **desconhecido** | tabela | **alto** | Incide sempre? Muda RV de 0,30% para 0,80% | TRANSAL | 🔴 |
| 26 | GRIS | **desconhecido** | tabela | médio | Incide sempre? O exemplo da tabela o omite | TRANSAL | 🔴 |
| 27 | Base do pedágio | **desconhecido** | tabela | médio | Peso real ou taxado? | TRANSAL | 🔴 |
| 28 | Origem Daune/Decor | São Paulo, sem tabela | — | **alto** | Precisamos de tarifa saindo de SP | Logística | 🔴 |
| 29 | Validade TRANSAL | 31/12/2026 | tabela | alto | Renovar antes do vencimento | Logística | 🟠 |
| 30 | Volume por SKU | **nenhum cadastrado** | — | médio | Sem volume não há peso cubado | Operação | 🔴 |
| 31 | 45 SKUs sem custo | — | — | alto | Vamos cotar esses itens? | Comercial | 🟡 |

---

# 32. Por responsável

## 🟡 Diretoria / sócios
1 câmbio · 4 margem KTC · 15 margens Anara · 16 comissão

## 🟡 Comercial
31 SKUs sem custo · 23 finalidade típica

## 🟡 Financeiro
3 outras despesas · 17 encargo financeiro · 18 sinal e cartão

## 🔵 Fiscal / contabilidade
11 preferência Egito · 12 créditos Daune · 13 créditos Decor · 19 PIS/COFINS ·
20 carga final DIFAL · 21 FCP · 22 origem fiscal · 23 finalidade

## 🟠 Logística
2 frete internacional · 28 origem SP · 29 validade TRANSAL · 30 volume por SKU ·
10 I.I. Duvet Insert

## 🟠 Fornecedores
**KTC:** 5 waste · 6 encolhimento · 7 preço de tecido · 8 tecido 500/800 · 9 pool towel
**Daune:** 14 base da planilha Trousseau
**TRANSAL:** 24 ICMS do frete · 25 fiel depositário · 26 GRIS · 27 base do pedágio

---

# 33. Semáforo executivo

## 🟢 VERDE — confirmado, com fonte forte

- Motor industrial KTC: fórmulas demonstradas pela KTC e reproduzidas em 13 casas decimais
- Pesos por m²: calibrados contra a PI ANARA de 23/08/2026
- Terry Bath Towel, Hand Towel e Bath Mat: derivados de PI real, conferem em duas medidas
- Alíquotas interestaduais 4% / 7% / 12%: correspondem à legislação
- Precisão monetária: `Decimal`, `ROUND_HALF_UP`, reconciliação ao centavo, com prova
- Estrutura de versionamento: premissa antiga nunca some, cotação emitida nunca muda

## 🟡 AMARELO — em uso hoje, precisa validação

- Câmbio: valor certo, **política de atualização não definida**
- Frete internacional e outras despesas de importação
- Todas as margens comerciais
- Tabela de comissão
- Encargo financeiro de 1,6% por parcela
- Preços de tecido (a própria KTC avisou que variam)
- Waste, encolhimento, segunda qualidade, margem KTC
- GSM estimados por título

## 🔴 VERMELHO — faltando ou bloqueado

- **I.I. do Duvet Insert** — sem valor confiável; 31 SKUs
- **FCP de BA, PE e PI** — FEM de 2% na tabela, nenhuma regra cadastrada
- **Créditos fiscais Daune** — 20,14% do bruto, sem validação da contabilidade registrada
- **Base da planilha Trousseau** (B-17) — razão de 1,5123 inexplicada
- **Quatro perguntas TRANSAL** — CIF bloqueado
- **Tabela de frete com origem São Paulo** — não existe
- **Encargo de sinal e cartão** — bloqueiam a cotação
- **Tecido de 500 e 800 fios** — não existe na tabela
- **45 SKUs sem custo**
- **Volume por SKU** — nenhum cadastrado

---

# 34. O que a Anara não inventa

Uma lista curta e deliberada. Em cada um destes casos o sistema **recusa** em vez de produzir
um número plausível:

| Situação | O que o sistema faz | Por que |
|---|---|---|
| Custo ausente | Marca sob consulta e não forma preço | Preço sem custo é chute com aparência de cálculo |
| Cenário fiscal irresolvido | Bloqueia o item | Alíquota errada vira problema fiscal, não comercial |
| Condição de pagamento desconhecida | Exige cadastro | Contar barras e somar 1,6% pareceria certo e poderia estar errado |
| Frete CIF irresolvido | Bloqueia a emissão | Frete desconhecido tratado como zero corrói margem em silêncio |
| Par de UF sem alíquota | Bloqueia nomeando o par | Aproximar por estado vizinho é errar com convicção |
| Cidade fora da cobertura | Frete a cotar | Sem aproximar por região vizinha |
| FCP sem regra | Zero, e registra que não havia regra | Assumir que incide encarece sem base |
| Custo estimado | Cota, mas **não** deixa fechar pedido | Proposta é diferente de compromisso |
| Referência envelhecida | Alerta e exige reconfirmação | Preço de seis meses atrás não é preço de hoje |

**O princípio comercial:** é melhor dizer "não sei" e travar do que emitir uma proposta com um
número que ninguém consegue justificar depois. Uma proposta errada custa mais que uma proposta
atrasada.

---

# 35. Achados para validação

Contradições e resíduos encontrados na leitura. **Nada foi corrigido.**

| # | Achado | Evidência | Impacto |
|---|---|---|---|
| A1 | `icms_fallback_pct = 18%` cadastrado e exposto como premissa crítica na tela de configurações, mas **não lido por nenhum código** | Migration 0003; varredura sem chamadas | Nenhum no preço. Resíduo de interface |
| A2 | `EstadoFiscal.fem` traz 2% para BA, PE, PI e RJ, mas só RJ tem `RegraFcp` | Tabelas `estadofiscal` e `regrafcp` | **Vendas para BA, PE e PI podem estar 2 p.p. subprecificadas** |
| A3 | Créditos Daune usados como default para **qualquer** fornecedor nacional; não há constante Decor | `custo_service.cnet_nacional()` | Nulo hoje (Decor sem custo confirmado); latente |
| A4 | I.I. do Duvet Insert sem valor; o sistema antigo usava 7% "por fallback, sem documento" | `ncmregra`, campo `confiavel = 0` | 31 SKUs sem custeio confiável |
| A5 | Razão constante de 1,5123 entre duas fontes Daune | B-17 | 13 SKUs com base incerta |
| A6 | `indice_algodao` (100,8) e `indice_petroleo` (100,0) cadastrados | `premissa` | **NÃO CONFIRMADO** se alimentam algum cálculo |
| A7 | Tabela de materiais vai até 400 fios; catálogo tem SKUs de 500 e 800 | `materialpreco` × `produto` | 17 SKUs sem preço de tecido |
| A8 | Nenhuma alíquota interestadual com origem ≠ SP | `aliquotainterestadual` | Mudar a origem fiscal bloqueia toda venda interestadual |
| A9 | Cargas finais de DIFAL sem fonte registrada | `estadofiscal.fonte` vazio | Alto: decidem o DIFAL de não contribuinte |
| A10 | Exemplo da TRANSAL omite GRIS e fiel depositário | Reconstituição na Sessão 0.1 | Enfraquece o exemplo como fonte para C-NEW-01 |

---

# 36. Pendências que **não** afetam preço

Registradas para completude, fora da apresentação principal:

| ID | O quê | Afeta preço? |
|---|---|---|
| B-13 | 21 divergências entre `models.py` e o esquema real do banco | **Não** |
| B-16 | `gsm` nulo em 28 de 31 SKUs de Duvet Insert | **Indiretamente** — impede casamento estruturado |
| B-19 | Dois scripts de manutenção quebram com cenário fiscal bloqueado | **Não** |
| B-20 | `GET /logout` muda estado via GET | **Não** |
| B-22 | `scripts/backup_banco.py` não aplica retenção | **Não** |
| C-NEW-04 | Volume por SKU ausente | **Sim, no CIF** — sem volume não há peso cubado |

---

# 37. Os SKUs sem custo — 45 no snapshot, **31 desde 13/09/2026**

| Fornecedor | SKUs | | Família | SKUs |
|---|---|---|---|---|
| Kazareen (KTC) | 29 | | Duvet Insert | 0 — **fechados em 13/09/2026, ver §9.4** |
| Daune | **1** | | Fitted Sheet | 6 |
| Decor Tricot | 1 | | Pool Towel | 6 |
| | | | Bathrobe | 6 |
| | | | Duvet Cover · Pillow Case | 8 |
| | | | Flat Sheet · Mattress Protector | 4 |

**Causas prováveis, por família:**

- **Duvet Insert** — os 14 Daune receberam referência direta em 13/09/2026 (§9.4)
- **Fitted Sheet (6)** — fora do motor industrial, sem EXW cotado
- **Bathrobe (6)** — idem
- **Pool Towel (6)** — tem preço por kg (US$ 14,00), mas **NÃO CONFIRMADO** por que não formam
- **Os de 500 e 800 fios** — sem material na tabela; ver A7

> 🟡 **Pergunta para o comercial:** quais destes 45 realmente vamos vender? A resposta define a
> prioridade das cotações com fornecedor.

---

# SUGESTÃO DE NARRATIVA PARA APRESENTAÇÃO

21 slides. Cada um com uma mensagem só.

| # | Título | Mensagem principal | Conteúdo | Visual sugerido |
|---|---|---|---|---|
| 1 | Como a Anara chega ao preço | Existe um método, e ele é auditável de ponta a ponta | Waterfall macro do §1 | Waterfall horizontal, 9 blocos |
| 2 | Três caminhos, um destino | KTC, Daune e Decor chegam ao CNET por rotas diferentes | Diagrama de convergência do §1 | Dois fluxos convergindo no CNET |
| 3 | O que vamos validar hoje | 31 decisões, agrupadas por responsável | Contagem do §32 | Semáforo 🟢🟡🔴 com números |
| 4 | O custo começa no produto | Especificação técnica vira consumo de tecido | Etapas 1–6 do §6.1 | Desenho do lençol com bainha e encolhimento |
| 5 | Três contas que dividem | Waste, 2ª qualidade e margem KTC dividem, não multiplicam | §3.2 | Comparação lado a lado: ÷ certo vs × errado |
| 6 | O preço do tecido | 12 combinações, US$ 1,10 a 1,55/m² | Tabela do §4 | Matriz fios × acabamento, calor por preço |
| 7 | Do Egito ao Brasil | Frete, imposto e câmbio transformam EXW em CNET | §7.5 | Mapa Egito→Brasil com as 4 somas |
| 8 | O câmbio é a maior alavanca | 1% de dólar é 1% de preço | §30, linhas de câmbio | Gráfico de sensibilidade |
| 9 | Toalha é outro caminho | Terry usa peso, não área — duas etapas, não onze | §29.2 | Comparação lado a lado com o lençol |
| 10 | Daune: crédito não é imposto | Crédito de compra reduz custo; imposto de venda aumenta preço | §9.1 e §9.2 | Duas setas em direções opostas |
| 11 | As margens que praticamos | 12% banho · 14% nacional · 16–18% cama | Tabela do §11.1 | Barras horizontais por família |
| 12 | Margem não é markup | Uma divide pela receita, a outra pelo custo | §11.2 | Duas fórmulas lado a lado |
| 13 | O que o cliente paga, decomposto | Metade é custo, um quarto é imposto | §29.1, tabela final | Pizza ou barra empilhada |
| 14 | O fiscal em seis perguntas | Sem as seis respostas, o item não forma preço | §14 | Fluxograma de decisão |
| 15 | DIFAL: quem paga a conta | Não contribuinte custa mais, e a Anara assume | §16 | Duas colunas, contribuinte × não |
| 16 | FCP: só o Rio está cadastrado | BA, PE e PI têm FEM e nenhuma regra | §17.2 e A2 | Mapa do Brasil com 4 estados marcados |
| 17 | Frete CIF está bloqueado | Quatro perguntas para a TRANSAL travam o CIF | §22 | Quatro cartões de pergunta |
| 18 | Daune e Decor saem de SP | E não temos tarifa saindo de SP | §22, Q-L | Mapa: Itajaí tem tabela, SP não |
| 19 | O que o sistema se recusa a chutar | Travar é mais barato que errar | §34 | Lista com ícone de bloqueio |
| 20 | O semáforo | O que está confirmado, o que precisa de vocês | §33 | Três colunas 🟢🟡🔴 |
| 21 | As decisões de hoje | 31 perguntas, agrupadas por quem responde | §31 e §32 | Tabela por responsável, com prazo |

**Tom sugerido:** não é uma apresentação de aprovação do sistema. É uma sessão de trabalho
para fechar 31 premissas. O sistema já roda — o que falta é confirmar os números que ele usa.

---

# APÊNDICE — Rastreabilidade técnica

Para auditoria depois da reunião. Não faz parte da apresentação.

| Assunto | Tabela / model | Arquivo |
|---|---|---|
| Premissas globais | `Premissa` | `app/config_service.py` |
| Parâmetros KTC | `ParametroKTC` | `app/pricing_service.py::parametros_ktc_do_produto` |
| Materiais | `MaterialPreco` | idem |
| CMT | `CmtPreco` | idem |
| Terry | `ToalhaPreco` | `app/ktc_engine.py::calcular_toalha` |
| Motor industrial | — | `app/ktc_engine.py` |
| Nacionalização | — | `app/nationalization.py::nacionalizar` |
| Custo nacional | — | `app/custo_service.py::cnet_nacional` |
| Resolução de custo | `CustoReferencia` | `app/pricing_service.py::custo_para_precificar` |
| Status canônico | — | `app/pricing_service.py::status_canonico_do_custo` |
| Margens | `MargemRegra` | `app/margin_rules.py::resolver_margem` |
| Comissão | `Premissa.comissao_tabela` | `app/pricing_engine.py::comissao_para_markup` |
| Condições de pagamento | `CondicaoPagamento` | `app/payment_terms.py::resolver_encargo` |
| Fiscal | `EstadoFiscal`, `AliquotaInterestadual`, `RegraFcp`, `RegraFiscalVenda` | `app/fiscal_rules.py::resolver_fiscal_item` |
| NCM e I.I. | `NcmRegra` | `app/pricing_service.py::regra_ncm` |
| Peso | — | `app/peso.py::resolver_peso` |
| Frete | `TabelaFrete`, `FaixaFrete`, `CoberturaFrete`, `ComponenteFrete` | `app/frete_engine.py`, `app/frete_service.py` |
| Formação do preço | — | `app/pricing_engine.py::calcular_por_margem` |
| Precisão monetária | — | `app/dinheiro.py` |
| Aprovação | `AprovacaoCotacao` | `app/workflow.py::excecoes_do_item` |
| Memória do preço | `CotacaoItem.memoria_json` | `app/pricing_service.py::memoria_do_preco` |

**Versões vigentes no snapshot:** câmbio `Premissa` id 21 · frete internacional id 2 · outras
despesas id 5 · PIS/COFINS id 6 · tabela de frete `TabelaFrete` id 1 · FCP `RegraFcp` do RJ.

**Como reproduzir qualquer número deste documento:** abrir a cotação no sistema e clicar na
memória do item. O waterfall completo está lá, etapa por etapa, com fórmula e valor.

# Dados migrados em 21/09/2026 (trilha do script)

Banco: `/private/tmp/claude-501/-Users-matiaskrueder/969ec3b9-0e7d-41bb-a945-a11a0c33f049/scratchpad/anara_pos_ii.db` · linhas de AuditLog com origem `script:aplicar_dados_2026_09_21`: **512**

## CmtPreco · NORMALIZAR_NOMENCLATURA — 1

| Escopo | Antes | Depois |
|---|---|---|
| construcao | oxford | com abas |

## CondicaoPagamento · DESATIVAR — 1

| Escopo | Antes | Depois |
|---|---|---|
| SINAL30+30/60/90 | ativo | inativo |

## CustoReferencia · CRIAR_VERSAO — 17

| Escopo | Antes | Depois |
|---|---|---|
| SKU DECOR_TRICOT · Peseira · RELEVO · 0,60x1,90 | custo_unitario 130.58 (bruto, sem crédito) | CNET 118.50135 = bruto 130.58 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SOFIA · 0,60x1,90 | custo_unitario 128.36 (bruto, sem crédito) | CNET 116.4867 = bruto 128.36 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · AREZZO · 0,60x1,90 | custo_unitario 128.36 (bruto, sem crédito) | CNET 116.4867 = bruto 128.36 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SISSI · 0,60x1,90 | custo_unitario 127.32 (bruto, sem crédito) | CNET 115.5429 = bruto 127.32 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · RELEVO · 0,60x2,40 | custo_unitario 164.95 (bruto, sem crédito) | CNET 149.692125 = bruto 164.95 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SOFIA · 0,60x2,40 | custo_unitario 162.14 (bruto, sem crédito) | CNET 147.14205 = bruto 162.14 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · AREZZO · 0,60x2,40 | custo_unitario 162.14 (bruto, sem crédito) | CNET 147.14205 = bruto 162.14 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SISSI · 0,60x2,40 | custo_unitario 160.83 (bruto, sem crédito) | CNET 145.953225 = bruto 160.83 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · RELEVO · 0,60x2,70 | custo_unitario 185.56 (bruto, sem crédito) | CNET 168.3957 = bruto 185.56 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SOFIA · 0,60x2,70 | custo_unitario 182.41 (bruto, sem crédito) | CNET 165.537075 = bruto 182.41 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · AREZZO · 0,60x2,70 | custo_unitario 182.41 (bruto, sem crédito) | CNET 165.537075 = bruto 182.41 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SISSI · 0,60x2,70 | custo_unitario 180.94 (bruto, sem crédito) | CNET 164.20305 = bruto 180.94 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · RELEVO · 0,60x2,85 | custo_unitario 195.87 (bruto, sem crédito) | CNET 177.752025 = bruto 195.87 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SOFIA · 0,60x2,85 | custo_unitario 192.54 (bruto, sem crédito) | CNET 174.73005 = bruto 192.54 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · AREZZO · 0,60x2,85 | custo_unitario 192.54 (bruto, sem crédito) | CNET 174.73005 = bruto 192.54 − 9,25% PIS/COFINS |
| SKU DECOR_TRICOT · Peseira · SISSI · 0,60x2,85 | custo_unitario 190.99 (bruto, sem crédito) | CNET 173.323425 = bruto 190.99 − 9,25% PIS/COFINS |
| SKU ELIS · Blanket · 180x220 · Boa Noite Casal · xadrez preto/branco/cinza · 600 | — | CNET R$ 68,38 (custo líquido direto de aquisição) |

## EstadoFiscal · CADASTRAR_MATRIZ_FISCAL — 1

| Escopo | Antes | Depois |
|---|---|---|
| 27 UFs · base interna sem FCP · FCP por família do escopo |  | 701 linhas/colunas |

## MargemRegra · CRIAR_VERSAO — 31

| Escopo | Antes | Depois |
|---|---|---|
| Daune — B2B 13% · política 21/09/2026 | — | B2B 13% · comissão de formação 5% líquida de ICMS |
| Decor Tricot — B2B 13% · política 21/09/2026 | — | B2B 13% · comissão de formação 5% líquida de ICMS |
| KTC — Flat Sheet < 300 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Flat Sheet 300–399 fios · política 21/09/2026 | — | B2B 22% · comissão de formação 5% líquida de ICMS |
| KTC — Flat Sheet ≥ 400 fios · política 21/09/2026 | — | B2B 23% · comissão de formação 5% líquida de ICMS |
| KTC — Top Sheet < 300 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Top Sheet 300–399 fios · política 21/09/2026 | — | B2B 22% · comissão de formação 5% líquida de ICMS |
| KTC — Top Sheet ≥ 400 fios · política 21/09/2026 | — | B2B 23% · comissão de formação 5% líquida de ICMS |
| KTC — Bottom Sheet < 300 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Bottom Sheet 300–399 fios · política 21/09/2026 | — | B2B 22% · comissão de formação 5% líquida de ICMS |
| KTC — Bottom Sheet ≥ 400 fios · política 21/09/2026 | — | B2B 23% · comissão de formação 5% líquida de ICMS |
| KTC — Fitted Sheet < 300 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Fitted Sheet 300–399 fios · política 21/09/2026 | — | B2B 22% · comissão de formação 5% líquida de ICMS |
| KTC — Fitted Sheet ≥ 400 fios · política 21/09/2026 | — | B2B 23% · comissão de formação 5% líquida de ICMS |
| KTC — Pillow Case < 400 fios · política 21/09/2026 | — | B2B 19% · comissão de formação 5% líquida de ICMS |
| KTC — Pillow Case ≥ 400 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Pillow Case sem fios estruturados · política 21/09/2026 | — | B2B 19% · comissão de formação 5% líquida de ICMS |
| KTC — Duvet Cover < 400 fios · política 21/09/2026 | — | B2B 19% · comissão de formação 5% líquida de ICMS |
| KTC — Duvet Cover ≥ 400 fios · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — Duvet Cover sem fios estruturados · política 21/09/2026 | — | B2B 19% · comissão de formação 5% líquida de ICMS |
| KTC — Bath Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Hand Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Face Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Pool Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Beach Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Bath Mat (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Wash Cloth (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Towel (toalha) B2B 16% · política 21/09/2026 | — | B2B 16% · comissão de formação 5% líquida de ICMS |
| KTC — Bathrobe (roupão) B2B 14% · política 21/09/2026 | — | B2B 14% · comissão de formação 5% líquida de ICMS |
| KTC — demais famílias ≥ 400 fios B2B 20% · política 21/09/2026 | — | B2B 20% · comissão de formação 5% líquida de ICMS |
| KTC — demais famílias B2B 19% · política 21/09/2026 | — | B2B 19% · comissão de formação 5% líquida de ICMS |

## MargemRegra · ENCERRAR_VERSAO — 21

| Escopo | Antes | Depois |
|---|---|---|
| Daune — padrão · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| Decor Tricot — padrão · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Bath Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Hand Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Face Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Pool Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Beach Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Bath Mat (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Wash Cloth (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Towel (toalha) · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Bathrobe · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Flat Sheet < 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Top Sheet < 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Bottom Sheet < 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Fitted Sheet < 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Flat Sheet >= 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Top Sheet >= 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Bottom Sheet >= 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — Fitted Sheet >= 300TC · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| KTC — demais famílias · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |
| Geral — sem fornecedor definido · política 16/09/2026 | vigente desde 2026-09-16 | encerrada em 2026-09-21 |

## NcmRegra · CRIAR_VERSAO — 29

| Escopo | Antes | Depois |
|---|---|---|
| Cotton Bedding · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Bed linen sintético · 6302.32.00 | I.I. 3.5% | I.I. econômico 0% |
| Toalha · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Toalha sintética · 6302.93.00 | I.I. 0% | I.I. econômico 0% |
| Manta sintética · 6301.40.00 | I.I. 0% | I.I. econômico 0% |
| Manta algodão · 6301.30.00 | I.I. 0% | I.I. econômico 0% |
| Edredom / Insert · 9404.40.00 | I.I. 0% | I.I. econômico 0% |
| Travesseiro · 9404.90.00 | I.I. 1.62% | I.I. econômico 0% |
| Chinelos · 6404.19.00 | I.I. 3.5% | I.I. econômico 0% |
| Roupão · 6208.91.00 | I.I. 3.5% | I.I. econômico 0% |
| Flat Sheet · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Top Sheet · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Bottom Sheet · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Fitted Sheet · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Duvet Cover · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Pillow Case · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Pillow Protector · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Bed Runner · 6302.31.00 | I.I. 3.5% | I.I. econômico 0% |
| Bath Towel · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Hand Towel · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Bath Mat · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Pool Towel · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Wash Cloth · 6302.60.00 | I.I. 3.5% | I.I. econômico 0% |
| Slipper · 6404.19.00 | I.I. 3.5% | I.I. econômico 0% |
| Mattress Protector · 9404.90.00 | I.I. 1.62% | I.I. econômico 0% |
| Mattress Topper · 9404.90.00 | I.I. 1.62% | I.I. econômico 0% |
| Pillow · 9404.90.00 | I.I. 1.62% | I.I. econômico 0% |
| Duvet Insert · 9404.40.00 | I.I. 0% | I.I. econômico 0% |
| Bathrobe · 6208.91.00 | I.I. 3.5% | I.I. econômico 0% |

## NcmRegra · ENCERRAR_VERSAO — 29

| Escopo | Antes | Depois |
|---|---|---|
| Cotton Bedding · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Bed linen sintético · 6302.32.00 | vigente | valid_to 2026-09-22 |
| Toalha · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Toalha sintética · 6302.93.00 | vigente | valid_to 2026-09-22 |
| Manta sintética · 6301.40.00 | vigente | valid_to 2026-09-22 |
| Manta algodão · 6301.30.00 | vigente | valid_to 2026-09-22 |
| Edredom / Insert · 9404.40.00 | vigente | valid_to 2026-09-22 |
| Travesseiro · 9404.90.00 | vigente | valid_to 2026-09-22 |
| Chinelos · 6404.19.00 | vigente | valid_to 2026-09-22 |
| Roupão · 6208.91.00 | vigente | valid_to 2026-09-22 |
| Flat Sheet · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Top Sheet · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Bottom Sheet · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Fitted Sheet · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Duvet Cover · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Pillow Case · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Pillow Protector · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Bed Runner · 6302.31.00 | vigente | valid_to 2026-09-22 |
| Bath Towel · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Hand Towel · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Bath Mat · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Pool Towel · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Wash Cloth · 6302.60.00 | vigente | valid_to 2026-09-22 |
| Slipper · 6404.19.00 | vigente | valid_to 2026-09-22 |
| Mattress Protector · 9404.90.00 | vigente | valid_to 2026-09-22 |
| Mattress Topper · 9404.90.00 | vigente | valid_to 2026-09-22 |
| Pillow · 9404.90.00 | vigente | valid_to 2026-09-22 |
| Duvet Insert · 9404.40.00 | vigente | valid_to 2026-09-22 |
| Bathrobe · 6208.91.00 | vigente | valid_to 2026-09-22 |

## ParametroKTC · CRIAR_VERSAO — 32

| Escopo | Antes | Depois |
|---|---|---|
| protecao_comercial_pct · Flat Sheet | — | 0.035 |
| protecao_comercial_pct · Top Sheet | — | 0.035 |
| protecao_comercial_pct · Bottom Sheet | — | 0.035 |
| protecao_comercial_pct · Fitted Sheet | — | 0.035 |
| protecao_comercial_pct · Duvet Cover | — | 0.035 |
| protecao_comercial_pct · Pillow Case | — | 0.035 |
| protecao_comercial_pct · Pillow Protector | — | 0.035 |
| protecao_comercial_pct · Bed Runner | — | 0.035 |
| protecao_comercial_pct · Bath Towel | — | 0.035 |
| protecao_comercial_pct · Hand Towel | — | 0.035 |
| protecao_comercial_pct · Bath Mat | — | 0.035 |
| protecao_comercial_pct · Pool Towel | — | 0.035 |
| protecao_comercial_pct · Wash Cloth | — | 0.035 |
| protecao_comercial_pct · Slipper | — | 0.035 |
| protecao_comercial_pct · Mattress Protector | — | 0.0162 |
| protecao_comercial_pct · Mattress Topper | — | 0.0162 |
| protecao_comercial_pct · Pillow | — | 0.0162 |
| protecao_comercial_pct · Duvet Insert | — | 0.0 |
| protecao_comercial_pct · Bathrobe | — | 0.035 |
| protecao_comercial_pct · Cotton Bedding | — | 0.035 |
| protecao_comercial_pct · Bed linen sintético | — | 0.035 |
| protecao_comercial_pct · Toalha | — | 0.035 |
| protecao_comercial_pct · Toalha sintética | — | 0.0 |
| protecao_comercial_pct · Manta sintética | — | 0.0 |
| protecao_comercial_pct · Manta algodão | — | 0.0 |
| protecao_comercial_pct · Edredom / Insert | — | 0.0 |
| protecao_comercial_pct · Travesseiro | — | 0.0162 |
| protecao_comercial_pct · Chinelos | — | 0.035 |
| protecao_comercial_pct · Roupão | — | 0.035 |
| protecao_comercial_pct · Blanket | — | 0.0 |
| protecao_comercial_pct · Bath Rug | — | 0.0 |
| protecao_comercial_pct · Face Towel | — | 0.0 |

## Premissa · CRIAR_VERSAO — 3

| Escopo | Antes | Depois |
|---|---|---|
| premissa 'comissao_b2b_pct' | não cadastrada | 0.05 |
| premissa 'fator_tabela' | não cadastrada | 2.0 |
| premissa 'comissao_faixas_desconto' | não cadastrada | [[0.10,0.09],[0.20,0.08],[0.30,0.07],[0.40,0.06]] |

## Produto · ESTIMAR_PESO_LOGISTICO — 1

| Escopo | Antes | Depois |
|---|---|---|
| SKU KTC · Blanket · 220x230 · — · 0/100 · plain · BL-001 · amostra 12 | peso None (sem tipo) | peso 2.4 kg (ESTIMADO) · EXW US$ 10.71 mantido CONFIRMADO |

## Produto · NORMALIZAR_NOMENCLATURA — 2

| Escopo | Antes | Depois |
|---|---|---|
| nome, especificacao, sku_key, construcao | Fronha com aba 50x70+5 · 250 fios · 70/30 · listrado · oxford / 50x70+5 · 250 fi | Fronha com aba 50x70+5 · 250 fios · 70/30 · listrado · 4 abas / 50x70+5 · 250 fios · CVC 7 |
| nome, especificacao, sku_key, construcao | Fronha com aba 50x90+5 · 250 fios · 70/30 · listrado · oxford / 50x90+5 · 250 fi | Fronha com aba 50x90+5 · 250 fios · 70/30 · listrado · 4 abas / 50x90+5 · 250 fios · CVC 7 |

## Produto · PINAR_PROTECAO_COMERCIAL — 309

| Escopo | Antes | Depois |
|---|---|---|
| SKU Bed Sheet Healthcare 180x280  ·  180x280 · 120 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Maca Sheet Healthcare 120x280  ·  120x280 · 120 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Standard Single Bed Sheet 160x310  ·  160x310 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Single Bed Sheet Bordado 160x310  ·  160x310 · 300 fios · 100% algodão · bor | — | protecao_comercial_pct 0.035 |
| SKU Single Sheet Hotel 180x295  ·  180x295 · 180 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Double Sheet Hotel 240x295  ·  240x295 · 180 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Queen Sheet Hotel 280x295  ·  280x295 · 180 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU King Sheet Hotel 325x295  ·  325x295 · 180 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 180x280  ·  180x280 · 233 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 180x280  ·  180x280 · 233 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 180x280  ·  180x280 · 233 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 180x280  ·  180x280 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 180x280  ·  180x280 · 250 fios · CVC 70/30 · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol 180x280  ·  180x280 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 180x280  ·  180x280 · 300 fios · 100% algodão · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol 180x280  ·  180x280 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 180x280  ·  180x280 · 400 fios · CVC 80/20 | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 180x280  ·  180x280 · 400 fios · CVC 80/20 · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol 190x290  ·  190x290 · 233 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 190x290  ·  190x290 · 233 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 190x290  ·  190x290 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 190x290  ·  190x290 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 190x290  ·  190x290 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 240x280  ·  240x280 · 233 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 240x280  ·  240x280 · 233 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 240x280  ·  240x280 · 233 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 240x280  ·  240x280 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 240x280  ·  240x280 · 250 fios · CVC 70/30 · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol 240x280  ·  240x280 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 240x280  ·  240x280 · 300 fios · 100% algodão · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol 240x280  ·  240x280 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 240x280  ·  240x280 · 400 fios · CVC 80/20 | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 240x280  ·  240x280 · 400 fios · CVC 80/20 · listrado | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 260x280  ·  260x280 · 200 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 260x280  ·  260x280 · 233 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 260x280  ·  260x280 · 233 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 260x280  ·  260x280 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 260x280  ·  260x280 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 260x280  ·  260x280 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 260x280  ·  260x280 · 500 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 260x280 — variação KTC 1  ·  260x280 · 800 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 260x280 — variação KTC 2  ·  260x280 · 800 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol plano 260x280 — variação KTC 3  ·  260x280 · 800 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 300x300  ·  300x300 · 233 fios · CVC 50/50 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 300x300  ·  300x300 · 233 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 300x300  ·  300x300 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol 300x300  ·  300x300 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol 300x300  ·  300x300 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 233 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 250 fios · CVC 70/30 · listr | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 300 fios · 100% algodão · li | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 400 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 400 fios · CVC 80/20 | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 100x200x30  ·  100x200x30 · 400 fios · CVC 80/20 · listr | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 160x200x30  ·  160x200x30 · 233 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 160x200x30  ·  160x200x30 · 250 fios · CVC 70/30 | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 160x200x30  ·  160x200x30 · 250 fios · CVC 70/30 · listr | — | protecao_comercial_pct 0.035 |
| SKU Lençol com elástico 160x200x30  ·  160x200x30 · 300 fios · 100% algodão | — | protecao_comercial_pct 0.035 |
| … | … | (+249) |

## Produto · REGISTRAR_COTACAO_KTC — 35

| Escopo | Antes | Depois |
|---|---|---|
| SKU KTC · Flat Sheet · 260x280 · 233 fios · 100/0 · plain · KTC-006 · amostra 01 | — | EXW cotado US$ 11.97 em 2026-07-29 (KTC-006) |
| SKU Lençol 260x280  ·  260x280 · 250 fios · CVC 70/30 | cotação direta None (None) | EXW cotado US$ 12.53 em 2026-07-29 (KTC-011) |
| SKU KTC · Flat Sheet · 200x220 · 250 fios · 70/30 · stripe · KTC-012 · amostra 0 | — | EXW cotado US$ 8.22 em 2026-07-29 (KTC-012) |
| SKU KTC · Flat Sheet · 180x320 · 250 fios · 80/20 · plain · KTC-011 · amostra 04 | — | EXW cotado US$ 10.5 em 2026-07-29 (KTC-011) |
| SKU Lençol 260x280  ·  260x280 · 300 fios · 100% algodão | cotação direta None (None) | EXW cotado US$ 14.02 em 2026-07-29 (KTC-001) |
| SKU KTC · Flat Sheet · 260x280 · 300 fios · 50/50 · plain · KTC-002 · amostra 06 | — | EXW cotado US$ 12.53 em 2026-07-29 (KTC-002) |
| SKU KTC · Flat Sheet · 260x280 · 300 fios · 100/0 · stripe · KTC-023 · amostra 0 | — | EXW cotado US$ 14.53 em 2026-07-29 (KTC-023) |
| SKU KTC · Flat Sheet · 220x220 · 300 fios · 50/50 · stripe · KTC-022 · amostra 0 | — | EXW cotado US$ 8.96 em 2026-07-29 (KTC-022) |
| SKU KTC · Flat Sheet · 220x220 · 300 fios · 70/30 · stripe · KTC-024 · amostra 0 | — | EXW cotado US$ 8.96 em 2026-07-29 (KTC-024) |
| SKU KTC · Flat Sheet · 229x305 · 400 fios · 100/0 · plain · sem código · amostra | — | EXW cotado US$ 14.47 em 2026-07-29 (sem código) |
| SKU KTC · Flat Sheet · 260x280 · 600 fios · 100/0 · plain · KTC-016 · amostra 11 | — | EXW cotado US$ 23.23 em 2026-07-29 (KTC-016) |
| SKU KTC · Blanket · 220x230 · — · 0/100 · plain · BL-001 · amostra 12 | — | EXW cotado US$ 10.71 em 2026-07-29 (BL-001) |
| SKU KTC · Blanket · 210x230 · — · 0/100 · plain · BL-003 · amostra 13 | — | EXW cotado US$ 20.86 em 2026-07-29 (BL-003) |
| SKU KTC · Blanket · 244x274 · 400 g/m² · 100/0 · plain · BL-002 · amostra 14 | — | EXW cotado US$ 28.96 em 2026-07-29 (BL-002) |
| SKU KTC · Pool Towel · 100x200 · 700 g/m² · 100/0 · stripe · PT.006 · amostra 15 | — | EXW cotado US$ 19.6 em 2026-07-29 (PT.006) |
| SKU KTC · Pool Towel · 100x180 · 700 g/m² · 100/0 · stripe · PT.007 · amostra 16 | — | EXW cotado US$ 17.64 em 2026-07-29 (PT.007) |
| SKU KTC · Pool Towel · 90x165 · 650 g/m² · 100/0 · stripe · PT.009 · amostra 17 | — | EXW cotado US$ 13.51 em 2026-07-29 (PT.009) |
| SKU KTC · Bath Towel · 100x180 · 650 g/m² · 100/0 · plain · BT-004 · amostra 18 | — | EXW cotado US$ 9.95 em 2026-07-29 (BT-004) |
| SKU KTC · Bath Towel · 100x150 · 543 g/m² · 100/0 · plain · BT-002 · amostra 19 | — | EXW cotado US$ 6.95 em 2026-07-29 (BT-002) |
| SKU KTC · Bath Towel · 70x140 · 600 g/m² · 100/0 · plain · BT-003 · amostra 20 | — | EXW cotado US$ 5.0 em 2026-07-29 (BT-003) |
| SKU KTC · Face Towel · 33x33 · 650 g/m² · 100/0 · plain · FT.004 · amostra 21 | — | EXW cotado US$ 0.64 em 2026-07-29 (FT.004) |
| SKU KTC · Face Towel · 33x33 · 600 g/m² · 91/9 · plain · FT.003 · amostra 22 | — | EXW cotado US$ 0.59 em 2026-07-29 (FT.003) |
| SKU KTC · Bath Mat · 50x75 · 800 g/m² · 100/0 · plain · BM.006 · amostra 23 | — | EXW cotado US$ 2.7 em 2026-07-29 (BM.006) |
| SKU KTC · Bath Mat · 50x70 · 1150 g/m² · 100/0 · plain · BM.011 · amostra 24 | — | EXW cotado US$ 3.62 em 2026-07-29 (BM.011) |
| SKU KTC · Hand Towel · 50x100 · 500 g/m² · 100/0 · plain · HT.001 · amostra 25 | — | EXW cotado US$ 2.13 em 2026-07-29 (HT.001) |
| SKU KTC · Bath Towel · 70x140 · 500 g/m² · 100/0 · plain · BT.001 · amostra 26 | — | EXW cotado US$ 4.17 em 2026-07-29 (BT.001) |
| SKU KTC · Bath Rug · 53x53 · 2050 g/m² · 100/0 · plain · BRU.001 · amostra 27 | — | EXW cotado US$ 9.21 em 2026-07-29 (BRU.001) |
| SKU KTC · Bath Rug · 61x91 · 2050 g/m² · 100/0 · plain · BRU.001 · amostra 28 | — | EXW cotado US$ 18.21 em 2026-07-29 (BRU.001) |
| SKU KTC · Bathrobe · M · 420 g/m² · 100/0 · plain · BR-002 · amostra 29 | — | EXW cotado US$ 24.0 em 2026-07-29 (BR-002) |
| SKU KTC · Bathrobe · 2XL · 240 g/m² · 100/0 · plain · BR-019 · amostra 30 | — | EXW cotado US$ 20.0 em 2026-07-29 (BR-019) |
| SKU KTC · Bathrobe · L · 240 g/m² · 100/0 · plain · BR-008 · amostra 31 | — | EXW cotado US$ 16.0 em 2026-07-29 (BR-008) |
| SKU KTC · Bathrobe · L · — · 100/0 · plain · BR-003 · amostra 32 | — | EXW cotado US$ 24.0 em 2026-07-29 (BR-003) |
| SKU KTC · Bathrobe · L · 420 g/m² · 100/0 · plain · BR-002 · amostra 33 | — | EXW cotado US$ 24.0 em 2026-07-29 (BR-002) |
| SKU KTC · Bathrobe · L · — · 0/100 · plain · BR-001 · amostra 34 | — | EXW cotado US$ 24.0 em 2026-07-29 (BR-001) |
| SKU KTC · Bathrobe · Unisize · 160 g/m² · 0/85 · plain · BR-028 · amostra 35 | — | EXW cotado US$ 27.0 em 2026-07-29 (BR-028) |

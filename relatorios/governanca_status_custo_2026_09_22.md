# Coerência catálogo × motor e governança de custo — 22/09/2026

Auditoria de TODOS os SKUs ativos, antes e depois das correções. O detalhe por SKU está
em `auditoria_status_custo_ANTES|DEPOIS_2026_09_22.json`.

| medida | antes | depois |
|---|---|---|
| SKUs ativos | 380 | 380 |
| catálogo × motor **incoerentes** | 24 | 0 |
| em REVIEW só por falta de peso | 8 | 7 |
| com evidência direta mas em REVIEW | 8 | 7 |
| referência vigente não usada pelo motor | 0 | 0 |
| sem custo (A_COTAR) | 31 | 31 |

## O que o catálogo dizia × o que a cotação resolvia

Antes (a tela lia a coluna-cache `Produto.status_custo`):

```
{
 "DISPONIVEL → CONFIRMADO": 183,
 "DISPONIVEL → REVALIDAR": 62,
 "DISPONIVEL → REVIEW_REQUIRED": 8,
 "REVISAR → CONFIRMADO": 16,
 "REVISAR → REVALIDAR": 9,
 "REVISAR → REVIEW_REQUIRED": 71,
 "SOB_CONSULTA → A_COTAR": 31
}
```

Depois (a tela pergunta ao motor, a mesma resposta que o item congela):

```
{
 "DISPONIVEL → CONFIRMADO": 200,
 "REVISAR → REVALIDAR": 71,
 "REVISAR → REVIEW_REQUIRED": 78,
 "SOB_CONSULTA → A_COTAR": 31
}
```

## SKUs cujo status mudou (evidência já existente no documento)

* #364 `KTC · Blanket · 210x230 · — · 0/100 · plain · BL-003` — REVIEW_REQUIRED → **CONFIRMADO** (peso declarado na cotação KTC de 29/07/2026)

## Continuam em revisão por falta de peso (sem evidência no documento)

A cotação de 29/07 não declara o peso destes SKUs. O sistema não inventa: o admin
registra o peso em **Admin → Produtos e custos** (informado pela fábrica ou estimado,
com fonte, documento e motivo) e o SKU passa a CONFIRMADO.

* #380 `KTC · Bathrobe · M · 420 g/m² · 100/0 · plain · BR-0` — Bathrobe, EXW US$ 24.0
* #381 `KTC · Bathrobe · 2XL · 240 g/m² · 100/0 · plain · BR` — Bathrobe, EXW US$ 20.0
* #382 `KTC · Bathrobe · L · 240 g/m² · 100/0 · plain · BR-0` — Bathrobe, EXW US$ 16.0
* #383 `KTC · Bathrobe · L · — · 100/0 · plain · BR-003 · am` — Bathrobe, EXW US$ 24.0
* #384 `KTC · Bathrobe · L · 420 g/m² · 100/0 · plain · BR-0` — Bathrobe, EXW US$ 24.0
* #385 `KTC · Bathrobe · L · — · 0/100 · plain · BR-001 · am` — Bathrobe, EXW US$ 24.0
* #386 `KTC · Bathrobe · Unisize · 160 g/m² · 0/85 · plain ·` — Bathrobe, EXW US$ 27.0

## Continuam A_COTAR

31 SKUs sem EXW e sem custo: não há o que vincular. Entram por
**Admin → Produtos e custos → Cotação KTC (EXW)** ou **Custo nacional (R$)**.


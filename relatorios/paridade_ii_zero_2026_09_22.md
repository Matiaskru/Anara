# Paridade comercial × economia real — retirada do I.I. KTC (22/09/2026)

Baseline ANTES: `baseline_ktc_ii_zero_ANTES_2026_09_22.json` · DEPOIS: `baseline_ktc_ii_zero_DEPOIS_2026_09_22.json` (cópias em `relatorios/baseline_ktc_ii_zero_ANTES|DEPOIS_2026_09_22.json`; o ANTES foi gerado com o código anterior ao patch)

* SKUs KTC ativos: 309 · comparados (precificáveis antes): **280** · cenários comparados: **2520**
* Violações de paridade/economia: **0** 
* CNET caiu (I.I. legado > 0 efetivamente no custo): 202 · CNET igual (I.I. 0 ou custo lido do catálogo, que não se decompõe): 78
* fonte do custo × alíquota legada: {'None|0.035': 256, 'None|None': 10, 'None|0.0162': 14}
* SKUs que passaram a precificar depois do patch: []

## Exemplos (SP→SP não contribuinte, 30 dias)

| SKU | I.I. antes | CNET antes → depois | B2B comercial | B2B econômico | tabela | margem no B2B antes → depois | lucro unit. antes → depois |
|---|---|---|---|---|---|---|---|
| Bed Sheet Healthcare 180x280  ·  180x280 · 120 fios · CVC 50 | 3.50% | 31.32 → 30.30 | 64.30 | 62.20 | 128.60 | 20.0000% → 21.5863% | 12.86 → 13.88 |
| Maca Sheet Healthcare 120x280  ·  120x280 · 120 fios · CVC 5 | 3.50% | 22.79 → 22.07 | 46.79 | 45.30 | 93.58 | 20.0043% → 21.5431% | 9.36 → 10.08 |
| Standard Single Bed Sheet 160x310  ·  160x310 · 300 fios · 1 | 3.50% | 60.55 → 58.55 | 129.61 | 125.34 | 259.22 | 22.0045% → 23.5476% | 28.52 → 30.52 |
| Single Bed Sheet Bordado 160x310  ·  160x310 · 300 fios · 10 | 3.50% | 66.84 → 64.62 | 143.09 | 138.34 | 286.18 | 22.0001% → 23.5516% | 31.48 → 33.70 |
| Single Sheet Hotel 180x295  ·  180x295 · 180 fios · CVC 50/5 | 3.50% | 36.59 → 35.40 | 75.12 | 72.68 | 150.24 | 20.0080% → 21.5921% | 15.03 → 16.22 |
| Double Sheet Hotel 240x295  ·  240x295 · 180 fios · CVC 50/5 | 3.50% | 46.82 → 45.28 | 96.10 | 92.95 | 192.20 | 20.0000% → 21.6025% | 19.22 → 20.76 |
| Queen Sheet Hotel 280x295  ·  280x295 · 180 fios · CVC 50/50 | 3.50% | 53.67 → 51.90 | 110.18 | 106.54 | 220.36 | 20.0036% → 21.6101% | 22.04 → 23.81 |
| King Sheet Hotel 325x295  ·  325x295 · 180 fios · CVC 50/50 | 3.50% | 61.39 → 59.36 | 126.03 | 121.85 | 252.06 | 20.0032% → 21.6139% | 25.21 → 27.24 |

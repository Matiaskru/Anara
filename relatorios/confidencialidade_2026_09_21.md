# Auditoria de confidencialidade da vendedora — 21/09/2026 (com prova)

Banco (cópia): `/private/tmp/claude-501/-Users-matiaskrueder/969ec3b9-0e7d-41bb-a945-a11a0c33f049/scratchpad/anara_audit25.db` · cotação 28 (rascunho, 30% de sinal + 30/60/90, desconto 20%) · cotação 29 (emitida) · produto `Single Top Sheet 190x250 300TC  ·  190x250 · 300 fios · 100% algodão`

## Veredito

* Superfícies auditadas: **156** (rotas GET, endpoints JSON/POST da tela, estáticos, atributos `data-*`/hidden, PDFs) × 2 perfis de vendedora.
* Vazamentos (termo proibido ou número confidencial da própria cotação em resposta 200 para vendedora): **0**.
* Itens que a vendedora TEM de ver e não viu: **0** .

## Números confidenciais procurados (todas as formatações)

* `custo_unitario`: 56,42, 56.41882832089079, 56.42, R$ 56,42, R$ 56,42
* `custo_total`: 564,19, 564.1882832089079, 564.19, R$ 564,19, R$ 564,19
* `base_comercial_precificacao`: 58,35, 58.34830128046298, 58.35, R$ 58,35, R$ 58,35
* `preco_b2b_economico`: 125,52, 125.52, R$ 125,52, R$ 125,52
* `lucro`: 775,24, 775.24, R$ 775,24, R$ 775,24
* `margem_liquida`: 0,3733, 0.3732858243451464, 0.3733, 37,3%, 37,33%, 37.33%
* `base_comissionavel`: 1.702,98, 1702.98, R$ 1.702,98, R$ 1.702,98
* `encargo_pct`: 0,0336, 0.0336, 3,36%, 3.36%
* `encargo_saldo_pct`: 0,0480, 0.048, 0.0480, 4,80%, 4.80%
* `icms_base_comissao_pct`: 0,1800, 0.1800, 18,0%, 18,00%, 18.00%

## O que a vendedora vê (valores da cotação, do banco)

| campo | valor |
|---|---|
| preco_tabela | 259.6 |
| preco_b2b | 129.8 |
| preco_negociado | 207.68 |
| desconto_vs_tabela_pct | 0.2 |
| comissao_pct | 0.08 |
| comissao_valor | 136.24 |
| faturamento | 2076.8 |
| encargo_pct | 0.0336 |
| percentual_sinal | 0.3 |

## Superfícies (vendedora)

| perfil | método | URL | status | tipo | bytes | sha256 | vazamentos | estruturais aceitos | números |
|---|---|---|---|---|---|---|---|---|---|
| VENDEDOR_INTERNO | GET | `/` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin/cotacao/28/premissas` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin/premissas` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin/sku/223` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin/trilha` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/admin/usuarios` | 403 | text/html | 2467 | 7d10e718dde8cc91 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/aprovacoes` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/aprovacoes/1` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/calculadora` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/clientes` | 200 | text/html | 6608 | 56f676cba97345eb | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/clientes/2` | 200 | text/html | 11778 | d04ba2f930219043 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/clientes/2/contatos.json` | 200 | application/json | 2 | 4f53cda18c2baa0c | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/clientes/2/vendas.json` | 200 | application/json | 70 | 69af67b5ad22cf0b | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/comercial` | 200 | text/html | 3746 | f4b5c95f7ba359b6 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/configuracoes` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes` | 200 | text/html | 11843 | 9e92a1804b0b6bd1 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/nova` | 200 | text/html | 11401 | 6aa4691108ae44a4 | 0 | 1 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28` | 200 | text/html | 20687 | 9352111279a1a214 | 0 | 6 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/29` | 200 | text/html | 17296 | cfd1b5e8ae3c8fa7 | 0 | 3 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/compromisso` | 200 | application/json | 112 | a07b9cce5f58fb63 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/itens/63/memoria` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/negociacao` | 200 | application/json | 877 | cfd96d875394b51d | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/painel` | 200 | text/html | 729 | 2f26893cf8479efc | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/pdf` | 200 | application/pdf | 402773 | 1c3cd7d43457aabc | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/28/situacao` | 200 | application/json | 251 | 2681bf5892e02086 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/dashboard` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/esqueci-senha` | 200 | text/html | 1084 | 05888ff7c7525ee7 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/health` | 200 | application/json | 31 | 68fd26fd028c249f | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/health/detalhe` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/importar` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/login` | 200 | text/html | 1162 | 7268afc21b1670ab | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/oportunidades` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/oportunidades/3` | 200 | text/html | 9389 | 58e7d728a5046c51 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/pipeline` | 200 | text/html | 8169 | 284960021a7cb151 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/primeiro-acesso` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/produtos` | 200 | text/html | 167744 | 92985a3d0bba4a9f | 0 | 61 | 0 |
| VENDEDOR_INTERNO | GET | `/produtos/buscar` | 200 | application/json | 22479 | ec112b0b01b92e69 | 0 | 61 | 0 |
| VENDEDOR_INTERNO | GET | `/produtos/facetas` | 200 | application/json | 2486 | 6f69d3db54c57225 | 0 | 2 | 0 |
| VENDEDOR_INTERNO | GET | `/produtos/223/memoria` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/redefinir-senha` | 400 | text/html | 907 | ac7e9ff71082e270 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios` | 200 | text/html | 9155 | 67284420284ba523 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/aprovacoes` | 200 | text/html | 3995 | c193386706e74873 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/atividades.csv` | 200 | text/csv | 70 | 890f14f4e8b9bdb0 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/cotacoes` | 200 | text/html | 4636 | e90ffec1ebd23294 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/cotacoes.csv` | 200 | text/csv | 1811 | 0a8714ef6e43120e | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/economico` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/oportunidades.csv` | 200 | text/csv | 508 | d9dcfc968d17ee55 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/qualidade` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/relatorios/qualidade.json` | 403 | text/html | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/saude` | 403 | text/html | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/vendas` | 200 | text/html | 13120 | 2fe038b157656518 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/vendas/3` | 200 | text/html | 14527 | b3dd8419498b0e46 | 0 | 1 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/29/pdf` | 200 | application/pdf | 250769 | 2ae26f485cf6554b | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/29/negociacao` | 200 | application/json | 877 | 7967766fc1a655d5 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/cotacoes/29/situacao` | 200 | application/json | 330 | a755f72ed6aadbce | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/produtos/buscar?q=190x250` | 200 | application/json | 779 | 457bdeb67bf32a29 | 0 | 6 | 0 |
| VENDEDOR_INTERNO | POST | `/cotacoes/28/calc` | 200 | application/json | 251 | 592400873007c1d5 | 0 | 1 | 0 |
| VENDEDOR_INTERNO | POST | `/cotacoes/28/calc(desconto)` | 200 | application/json | 251 | 15d339eeaf9eabdb | 0 | 1 | 0 |
| VENDEDOR_INTERNO | POST | `/cotacoes/28/negociacao/preview` | 200 | application/json | 878 | cb38d1dbc91af417 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | POST | `/cotacoes/28/negociacao/preview(abaixo do B2B)` | 200 | application/json | 870 | 9b2ffcac6d8623dd | 0 | 0 | 0 |
| VENDEDOR_INTERNO | POST | `/cotacoes/28/negociacao` | 200 | application/json | 877 | cfd96d875394b51d | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/calculadora.js` | 200 | text/javascript | 4473 | 611c27ff992a5c5a | 0 | 18 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/cotacao.js` | 200 | text/javascript | 27011 | 453a2563d1c170af | 0 | 54 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/dashboard.js` | 200 | text/javascript | 4033 | 644c8b2fc22ff981 | 0 | 12 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/memoria.js` | 200 | text/javascript | 9999 | 89962d713b20c659 | 0 | 61 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/toast.js` | 200 | text/javascript | 465 | 8f7944f740d8ec38 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/static/js/ui.js` | 200 | text/javascript | 7582 | efb22062f2e912b0 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | GET | `/static/css/anara.css` | 200 | text/css | 41742 | 6f276a019d01a76a | 0 | 7 | 0 |
| VENDEDOR_INTERNO | HTML | `/cotacoes/28 data-* e hidden` | 200 | atributos | 20539 | 9352111279a1a214 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | HTML | `/cotacoes/29 data-* e hidden` | 200 | atributos | 17168 | cfd1b5e8ae3c8fa7 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/cotacoes/28/itens/63/memoria` | 403 | gate | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/produtos/223/memoria` | 403 | gate | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/admin/cotacao/28/premissas` | 403 | gate | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/relatorios/economico` | 403 | gate | 2466 | 7045dfdb0e08b509 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/configuracoes` | 403 | gate | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_INTERNO | 403? | `/admin/premissas` | 403 | gate | 2455 | ce9d5e4f1739b492 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin/cotacao/28/premissas` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin/premissas` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin/sku/223` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin/trilha` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/admin/usuarios` | 403 | text/html | 2483 | c89dca5769eafc70 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/aprovacoes` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/aprovacoes/1` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/calculadora` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/clientes` | 200 | text/html | 6624 | f72469ea4b6f4a45 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/clientes/2` | 200 | text/html | 11794 | 1b6d38759d094f73 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/clientes/2/contatos.json` | 200 | application/json | 2 | 4f53cda18c2baa0c | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/clientes/2/vendas.json` | 200 | application/json | 70 | 69af67b5ad22cf0b | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/comercial` | 200 | text/html | 3762 | 261c69f234b07e07 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/configuracoes` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes` | 200 | text/html | 11859 | 58f44027b59ca8d8 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/nova` | 200 | text/html | 11417 | 3ff18b7f519d8734 | 0 | 1 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28` | 200 | text/html | 20703 | 7195b333f7d6feca | 0 | 6 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/29` | 200 | text/html | 17312 | e6acecc0b067bcb9 | 0 | 3 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/compromisso` | 200 | application/json | 112 | a07b9cce5f58fb63 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/itens/63/memoria` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/negociacao` | 200 | application/json | 877 | cfd96d875394b51d | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/painel` | 200 | text/html | 729 | 2f26893cf8479efc | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/pdf` | 200 | application/pdf | 402773 | fde532c3fc82600e | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/28/situacao` | 200 | application/json | 251 | 2681bf5892e02086 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/dashboard` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/esqueci-senha` | 200 | text/html | 1084 | 05888ff7c7525ee7 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/health` | 200 | application/json | 31 | 68fd26fd028c249f | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/health/detalhe` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/importar` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/login` | 200 | text/html | 1162 | 7268afc21b1670ab | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/oportunidades` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/oportunidades/3` | 200 | text/html | 9405 | 116aab582d56d2c4 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/pipeline` | 200 | text/html | 8185 | b95cfcd1a1d4c39b | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/primeiro-acesso` | 303 |  | 0 | e3b0c44298fc1c14 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/produtos` | 200 | text/html | 167760 | f9e583534d3ae768 | 0 | 61 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/produtos/buscar` | 200 | application/json | 22479 | ec112b0b01b92e69 | 0 | 61 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/produtos/facetas` | 200 | application/json | 2486 | 6f69d3db54c57225 | 0 | 2 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/produtos/223/memoria` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/redefinir-senha` | 400 | text/html | 907 | ac7e9ff71082e270 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios` | 200 | text/html | 9171 | 6a50cf95b91a923b | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/aprovacoes` | 200 | text/html | 4011 | 5ce2ad3221548e85 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/atividades.csv` | 200 | text/csv | 70 | 890f14f4e8b9bdb0 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/cotacoes` | 200 | text/html | 4652 | acda3cbe259c485f | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/cotacoes.csv` | 200 | text/csv | 1811 | 0a8714ef6e43120e | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/economico` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/oportunidades.csv` | 200 | text/csv | 508 | d9dcfc968d17ee55 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/qualidade` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/relatorios/qualidade.json` | 403 | text/html | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/saude` | 403 | text/html | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/vendas` | 200 | text/html | 13136 | 12ca54572c65bf59 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/vendas/3` | 200 | text/html | 14543 | a3dada073c18e1de | 0 | 1 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/29/pdf` | 200 | application/pdf | 250769 | 961d7bc358fb47b3 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/29/negociacao` | 200 | application/json | 877 | 7967766fc1a655d5 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/cotacoes/29/situacao` | 200 | application/json | 330 | a755f72ed6aadbce | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/produtos/buscar?q=190x250` | 200 | application/json | 779 | 457bdeb67bf32a29 | 0 | 6 | 0 |
| VENDEDOR_COMISSIONADO | POST | `/cotacoes/28/calc` | 200 | application/json | 251 | 592400873007c1d5 | 0 | 1 | 0 |
| VENDEDOR_COMISSIONADO | POST | `/cotacoes/28/calc(desconto)` | 200 | application/json | 251 | 15d339eeaf9eabdb | 0 | 1 | 0 |
| VENDEDOR_COMISSIONADO | POST | `/cotacoes/28/negociacao/preview` | 200 | application/json | 878 | cb38d1dbc91af417 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | POST | `/cotacoes/28/negociacao/preview(abaixo do B2B)` | 200 | application/json | 870 | 9b2ffcac6d8623dd | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | POST | `/cotacoes/28/negociacao` | 200 | application/json | 877 | cfd96d875394b51d | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/calculadora.js` | 200 | text/javascript | 4473 | 611c27ff992a5c5a | 0 | 18 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/cotacao.js` | 200 | text/javascript | 27011 | 453a2563d1c170af | 0 | 54 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/dashboard.js` | 200 | text/javascript | 4033 | 644c8b2fc22ff981 | 0 | 12 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/memoria.js` | 200 | text/javascript | 9999 | 89962d713b20c659 | 0 | 61 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/toast.js` | 200 | text/javascript | 465 | 8f7944f740d8ec38 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/js/ui.js` | 200 | text/javascript | 7582 | efb22062f2e912b0 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | GET | `/static/css/anara.css` | 200 | text/css | 41742 | 6f276a019d01a76a | 0 | 7 | 0 |
| VENDEDOR_COMISSIONADO | HTML | `/cotacoes/28 data-* e hidden` | 200 | atributos | 20555 | 7195b333f7d6feca | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | HTML | `/cotacoes/29 data-* e hidden` | 200 | atributos | 17184 | e6acecc0b067bcb9 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/cotacoes/28/itens/63/memoria` | 403 | gate | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/produtos/223/memoria` | 403 | gate | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/admin/cotacao/28/premissas` | 403 | gate | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/relatorios/economico` | 403 | gate | 2482 | a7f1f01f04f9d539 | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/configuracoes` | 403 | gate | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |
| VENDEDOR_COMISSIONADO | 403? | `/admin/premissas` | 403 | gate | 2471 | c2f262daaf67b6aa | 0 | 0 | 0 |

## Ocorrências estruturais aceitas (sem valor confidencial) — justificativa

* `crédito` — cartão de crédito (ex.: `/cotacoes/nova`: … <option value="CARTAO" >               Cartão de crédito             </option> …)
* `memoria` — drawer-memoria (ex.: `/cotacoes/28`: …rMemoria()"></div> <div class="drawer" id="drawer-memoria"><button class="fechar…)
* `memoria` — conteudo-memoria (ex.: `/cotacoes/28`: …ick="fecharMemoria()">×</button><div id="conteudo-memoria"></div></div>        <…)
* `fornecedor` — f-fornecedor (ex.: `/cotacoes/28`: …todas</option></select>             <select id="f-fornecedor" data-filtro aria-l…)
* `fornecedor` — fornecedor: todos (ex.: `/cotacoes/28`: …a-filtro aria-label="Fornecedor"><option value="">Fornecedor: todos</option></se…)
* `piso` — toalha de piso (ex.: `/produtos`: …      <tr>         <td class="primaria">Toalha de piso 45x80 · 500 g/m² · 90/10<…)
* `kazareen` — kazareen textile company (ex.: `/produtos`: …ELIS (Guaratinguetá)</option><option value="KTC" >Kazareen Textile Company</opti…)
* `kazareen` — tag-ktc">kazareen (ex.: `/produtos`: …/span></td>         <td><span class="tag tag-ktc">Kazareen</span></td>         <…)
* `custo` — sem_custo (ex.: `/produtos/buscar`: …1.46,"fornecedor":"Kazareen Textile Company","sem_custo":false,"thread_count":nu…)
* `fornecedor` — fornecedor local (ex.: `/vendas/3`: …><input id="op-nota" placeholder="ex.: fechou com fornecedor local"></div>     <…)
* `fornecedor` — "fornecedor": (ex.: `/produtos/buscar?q=190x250`: …l de cima","tamanho":"190x250","preco_base":null,"fornecedor":"Kazareen Textile …)
* `custo` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/calculadora.js`: …// Calculadora de custo KTC. O formulário muda conforme a família: t…)
* `margem` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/calculadora.js`: …al.preco_negociado);   document.getElementById("r-margem").textContent = pctM(co…)
* `lucro` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/calculadora.js`: …sto || {}).net_brl);   document.getElementById("r-lucro").textContent = brlM(com…)
* `memoria` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/calculadora.js`: … memória do preço da cotação, renderizada pelo // memoria.js.  let ultimoResulta…)
* `memória` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/calculadora.js`: …rece // registrar o pedido. O resultado é a mesma memória do preço da cotação, r…)
* `encargo` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …to comercial acompanha ao vivo.   // Só texto — o encargo efetivo e a fórmula fi…)
* `piso` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …{PRECO_ABAIXO_B2B: "abaixo do B2B", MARGEM_ABAIXO_PISO: "abaixo do piso",       …)
* `icms_base` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …= undefined ? brl(i.base_comissionavel) : "—"}${i.icms_base_comissao_pct ? `<div…)
* `comissao_item` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …ML = (e.itens || []).map(i => {       const c = i.comissao_item || {};       con…)
* `kazareen` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …ome) return "";     const classe = nome.includes("Kazareen") || nome.includes("K…)
* `fornecedor` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/cotacao.js`: …ainel com os filtros do cotador offline: família, fornecedor, tamanho, fios, gra…)
* `exw` — código estático: renderiza só o que o payload da vendedora traz (ex.: `/static/js/memoria.js`: …tml += avisos(custo.industrial.avisos);   }    // EXW comparado   const exw = []…)

## Atributos `data-*` e `input hidden` no HTML da cotação (vendedora)

* data-*: `data-autonomia`, `data-comissao-item`, `data-condicao-texto`, `data-contribuinte`, `data-cotacao`, `data-desconto-input`, `data-economia`, `data-editavel`, `data-filtro`, `data-item-id`, `data-material`, `data-politica`, `data-popover`, `data-preco`, `data-preco-input`, `data-qtd`, `data-qtd-input`, `data-rec`, `data-res`, `data-res-rotulo`, `data-tabela`, `data-tabela-cel`, `data-total`, `data-travado`
* hidden: `contribuinte_icms`

## O que a vendedora TEM de ver

* [VENDEDOR_INTERNO] ok — tabela na tela
* [VENDEDOR_INTERNO] ok — B2B na tela
* [VENDEDOR_INTERNO] ok — preço da proposta na tela
* [VENDEDOR_INTERNO] ok — desconto % na tela
* [VENDEDOR_INTERNO] ok — sua comissão (taxa e valor) na tela
* [VENDEDOR_INTERNO] ok — total na tela
* [VENDEDOR_INTERNO] ok — condição com sinal e saldo na tela
* [VENDEDOR_INTERNO] ok — status na tela
* [VENDEDOR_INTERNO] ok — JSON traz tabela/B2B/proposta/desconto/comissão/total/autonomia
* [VENDEDOR_INTERNO] ok — JSON: valores batem com o banco
* [VENDEDOR_COMISSIONADO] ok — tabela na tela
* [VENDEDOR_COMISSIONADO] ok — B2B na tela
* [VENDEDOR_COMISSIONADO] ok — preço da proposta na tela
* [VENDEDOR_COMISSIONADO] ok — desconto % na tela
* [VENDEDOR_COMISSIONADO] ok — sua comissão (taxa e valor) na tela
* [VENDEDOR_COMISSIONADO] ok — total na tela
* [VENDEDOR_COMISSIONADO] ok — condição com sinal e saldo na tela
* [VENDEDOR_COMISSIONADO] ok — status na tela
* [VENDEDOR_COMISSIONADO] ok — JSON traz tabela/B2B/proposta/desconto/comissão/total/autonomia
* [VENDEDOR_COMISSIONADO] ok — JSON: valores batem com o banco

## Contraste OWNER/ADMIN (veem a economia)

* OWNER: {'economia_no_json': True, 'economia_na_tela': True, 'encargo_no_cenario_fiscal': True, 'memoria_do_item_200': True}
* ADMIN: {'economia_no_json': True, 'economia_na_tela': True, 'encargo_no_cenario_fiscal': True, 'memoria_do_item_200': True}

## Contradição aparente resolvida

`b2b` e `comissao_item` em `CAMPOS_CONFIDENCIAIS` são os nomes dos DICIONÁRIOS INTERNOS (o `b2b` da memória do preço traz custo, margem-alvo e regras; `comissao_item` traz a decomposição com base comissionável e ICMS deduzido). A vendedora recebe os ALIASES seguros `preco_b2b`, `preco_tabela`, `desconto_vs_tabela_pct`, `comissao_estimada_pct`, `comissao_estimada_valor` — presentes em `CAMPOS_ITEM_COMERCIAL`/`payload_vendedora`. `encontrar_confidenciais` casa por nome EXATO de chave, então o alias passa e o dicionário interno não: {'preco_b2b': True, 'comissao_estimada_pct/valor': True, 'desconto_vs_tabela_pct': True, 'preco_tabela': True}.

## Status por perfil

* VENDEDOR_INTERNO: {'303': 4, '403': 18, '200': 46, '400': 1}
* VENDEDOR_COMISSIONADO: {'303': 4, '403': 18, '200': 46, '400': 1}

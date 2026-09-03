# Anara — Sistema único de precificação e cotação

Plataforma local (FastAPI + SQLite) que forma preço e monta cotação para **todos os fornecedores
da Anara no mesmo catálogo**: KTC (importado do Egito), Daune e Decor Tricot (nacionais), com
espaço para os próximos sem mexer na arquitetura.

```
FORNECEDOR → FORMAÇÃO OU LEITURA DO CUSTO → CUSTO NET ANARA →
CENÁRIO FISCAL → CONDIÇÃO FINANCEIRA → COMISSÃO → MARGEM LÍQUIDA → PREÇO → COTAÇÃO
```

Cada fornecedor chega ao custo por um caminho diferente; **do custo NET em diante o motor
comercial é o mesmo para todos**.

| Caminho | Quando se aplica |
|---|---|
| Especificação → motor industrial KTC → EXW calculado → nacionalização → NET | Famílias KTC com fórmula validada: lençol, capa duvet e toalha (custo por peso) |
| Último preço KTC válido → nacionalização → NET | Demais famílias KTC (fronha, roupão, chinelo, protetor…) |
| Custo do fornecedor → NET | Daune, Decor Tricot e outros nacionais — **sem** motor KTC e **sem** nacionalização |

## Como rodar

| O quê | Como |
|---|---|
| Plataforma | ícone **"Plataforma Anara"** na área de trabalho, ou `python3 iniciar_plataforma.py` |
| Endereço | http://127.0.0.1:8420 |
| Banco | `data/anara.db` (SQLite) — migrations e seeds rodam sozinhas no startup |
| Testes | `python3 -m pytest tests/ -q` |
| Backups | automáticos em `data/backups/` antes de cada migration, e diário 20h em `~/Anara-Cotacao-Backups/` |

## Estrutura

```
app/
  main.py              FastAPI + middleware de senha + migrations/seeds no startup
  models.py            tabelas (fornecedor, produto, cotação, premissas versionadas, ...)
  migrations.py        migrations incrementais e idempotentes, com backup do banco
  seeds.py             carga inicial das tabelas de configuração
  config_service.py    leitura das premissas versionadas — o único lugar que sabe onde cada número mora

  ktc_engine.py        MOTOR INDUSTRIAL KTC (puro): consumo → waste → CMT → qualidade → margem → EXW
  nationalization.py   EXW → frete → I.I. → despesas → NET USD → NET BRL (puro)
  pricing_engine.py    MOTOR COMERCIAL (puro): preço, margem, markup, comissão por faixa
  fiscal_rules.py      ICMS por origem × destino × contribuinte (puro)
  margin_rules.py      margem líquida-alvo por fornecedor/família/faixa de fios (puro)
  payment_terms.py     encargo financeiro por condição de pagamento (puro)
  peso.py              peso real > estimado por área × GSM > peso técnico (puro)
  matching.py          casamento produto × documento por especificação estruturada (puro)
  spec_parser.py       leitura da descrição textual antiga → campos estruturados (migração)
  nomes.py             nome de exibição gerado dos campos estruturados (puro)
  busca.py             busca por termos, bilíngue, sem acento (puro)
  fontes_ktc.py        PI 23/08, HAMAN 25/08 e demais documentos transcritos
  pricing_service.py   liga o banco aos motores: regras da cotação, custo NET, memória do preço
  relatorios.py        relatório de qualidade da base e perguntas pendentes para a KTC
  calculadora.py       preço de produto que ainda não está no catálogo, pelos mesmos motores
  arquivamento.py      arquivar e apagar cotação/cliente com backup antes
  routers/             login, dashboard, clientes, produtos, importar, cotações, configurações, relatórios
gerar_cotacao.py       GERADOR DE PDF — layout aprovado, não redesenhar
scripts/               classificação da base, importação de fornecedores nacionais, regressão
tests/                 pytest — motor industrial, nacionalização, fiscal, margens, matching, cotação
referencia/            documentos-fonte (PI, cotações, pricing master, NCM, tabelas de fornecedor)
relatorios/            baseline, regressão e relatório de qualidade
```

Os motores são **puros**: só números e parâmetros, sem banco, sem HTML, sem request. Isso é o que
permite testá-los contra os números que a própria KTC demonstrou.

## Regras que não se negociam

1. **Nada de premissa hardcoded.** Câmbio, frete, despesas, material, CMT, encolhimento, waste,
   perda de 2ª qualidade, margem KTC, NCM/II, ICMS, PIS/COFINS, encargo, comissão, margens-alvo,
   termos e validade vivem em tabela versionada e editável no painel de configurações.
2. **Margem líquida não é markup.** Margem = lucro ÷ faturamento depois de custo, impostos,
   comissão e encargo. O markup é variável interna.
3. **Margem KTC ≠ margem Anara.** A da fábrica entra no EXW (`custo / (1 − 15%)`); a da Anara
   entra depois, no preço de venda.
4. **"II 1%" da planilha da KTC é perda de segunda qualidade**, não Imposto de Importação. No
   sistema chama-se `quality_allowance`.
5. **Waste divide:** `consumo / (1 − waste)`. Nunca `× (1 + waste)`.
6. **Peso real da KTC nunca é substituído** por estimativa.
7. **Carga final do DIFAL entra como está.** O sistema não recalcula por Base Simples/Base Dupla/FEM.
8. **Nada de fallback silencioso.** Preço antigo, documento de outro cliente ou combinação sem
   cadastro geram aviso e `REVIEW_REQUIRED` — nunca um número apresentado como se fosse atual.
9. **O PDF do cliente não mostra informação interna**: custo, EXW, material, CMT, câmbio, frete,
   I.I., NCM, ICMS, PIS/COFINS, comissão, markup ou margem. Existe teste automatizado para isso.
10. **Histórico é intocável.** Alterar premissa hoje abre uma versão nova; cotação emitida
    continua reproduzindo o que usou.

## Cenário fiscal da venda

Três campos independentes: **origem × destino × contribuinte**. Nunca se infere contribuinte
pelo estado.

| Situação | ICMS |
|---|---|
| Dentro de SP (contribuinte ou não) | 18% |
| Mesmo estado, fora de SP | alíquota interna do destino |
| Interestadual, contribuinte | 4% (Res. Senado 13/2012, bem importado) |
| Interestadual, não contribuinte | **carga final** do estado de destino |

## Margens líquidas-alvo padrão

| Fornecedor / família | Margem |
|---|---|
| KTC — toalhas e roupões | 12% |
| KTC — lençóis abaixo de 300 fios | 16% |
| KTC — lençóis de 300 fios para cima | 18% |
| KTC — demais famílias | 15% |
| Daune (qualquer família) | 14% |
| Decor Tricot (qualquer família) | 14% |

Precedência: override na cotação → regra por SKU → **fornecedor** → família → geral. Fornecedor
vence família: lençol 300TC da Daune continua 14%, não 18%.

## Scripts

```bash
python3 -m app.migrations                              # migrations + backfill
python3 -m app.seeds                                   # carga das tabelas de configuração
python3 scripts/classificar_base.py --dry-run          # classifica o catálogo KTC (só relata)
python3 scripts/importar_fornecedores_nacionais.py     # Daune + Decor Tricot
python3 scripts/comparar_regressao.py                  # compara com o baseline
python3 scripts/renomear_catalogo.py                   # regera os nomes de exibição
python3 -m pytest tests/ -q                            # 173 testes
python3 scripts/gerar_relatorio.py                     # relatório de qualidade em markdown
```

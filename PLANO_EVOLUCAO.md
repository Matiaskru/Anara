# Plano de evolução — Sistema único de precificação e cotação Anara

## Diagnóstico do que existe hoje

- FastAPI + SQLModel + SQLite + Jinja2, sem migrations, sem testes, sem camada de configuração.
- `pricing_engine.py` — motor comercial puro, correto e reaproveitável (3 modos, comissão por faixa
  resolvida por busca fechada). **Preservar.**
- `fiscal_rules.py` — resolve ICMS por lista de cenários vinda do snapshot do Excel. A tabela do
  Excel traz `SP→SP contribuinte = 4%`, que agora está **errado** (deve ser 18%).
- `excel_import.py` — lê valores já calculados do Excel e sobrescreve o catálogo. O Excel é hoje a
  única fonte de custo e de premissas; não há fornecedor, nem origem/confiança do custo.
- `models.py` — 5 tabelas. Produto sem fornecedor, sem dados técnicos estruturados, sem histórico
  de custo. Cotação sem número, sem prazo, sem termos, validade 30 dias.
- `gerar_cotacao.py` — PDF aprovado. O rodapé hoje **discrimina ICMS/PIS-COFINS**, o que conflita
  com a regra de não expor informação interna.
- Banco: 241 produtos, 14 cotações, 43 itens, 4 bases de importação, 1 cliente.

## Ordem de execução

1. **Fundação de dados** — `models.py` (novas tabelas + colunas), `migrations.py` (idempotente, com
   backup automático do SQLite), `seeds.py` (fornecedores, materiais, CMT, fios de toalha,
   parâmetros KTC, NCM, regras fiscais corrigidas, regras de margem, condições de pagamento,
   termos, premissas versionadas).
2. **Motores puros** — `ktc_engine.py` (waterfall industrial: flat sheet, duvet cover, toalhas),
   `nationalization.py` (EXW → NET BRL), `margin_rules.py` (resolução determinística de margem),
   `fiscal_rules.py` reescrito sobre a tabela de regras, `payment_terms.py` (encargo centralizado).
3. **Classificação da base** — atribuir fornecedor/cost_method/confiança a todos os SKUs, cruzar
   PI 23/08 > Cotação 10/08 > HAMAN 25/08 > histórico, importar Daune e Decor Tricot.
4. **Cotação** — número único, validade 5 dias, prazo de entrega, departamento, frete CIF, termos
   com snapshot, aceite/pedido, filtros por vendedor e categoria, margem padrão por item.
5. **Memória do preço** e telas de configuração/relatório.
6. **PDF** — aditivo, sem redesenho; remoção da linha que discrimina imposto.
7. **Testes + regressão + relatório de qualidade + documentação.**

## Baseline de regressão

`relatorios/baseline_regressao.json` — 241 SKUs (margem do preço-base, markup, preço a 18%),
43 itens de cotação e 9 cenários fiscais, capturados ANTES de qualquer alteração.

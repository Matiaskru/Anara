# Publicar a tabela REV4 no sistema (dados)

Leva o banco de produção ao estado da tabela comercial **REV4 (13/10/2026)**:
- regras Daune/Decor em 8% com comissão fixa de 5%;
- 85 SKUs novos e 21 desativados;
- calibração KTC;
- liberação de P120–P125 e R203/R204;
- SKU do lençol de baixo King 400 fios.

## Ordem
1. **Backup:** no Railway, Postgres → Backups → criar backup.
2. **Deploy** do commit com este pacote. O pre-deploy roda `alembic upgrade head` (migrations 0028, 0029 e 0030).
3. **Prévia** no Console do serviço `web`. Faz tudo, confere e desfaz:
   ```
   python3 scripts/publicar_rev4_2026_10_15/aplicar.py
   ```
   Tem que terminar em `PRÉVIA OK`, com `0 divergência(s)` na conferência dos 189 códigos.
4. **Aplicar:**
   ```
   python3 scripts/publicar_rev4_2026_10_15/aplicar.py --aplicar
   ```
   Tem que terminar em `APLICADO — preços do sistema = tabela REV4.`

## Garantias
- **Uma transação só.** Se qualquer conferência falhar (esquema, ids, tecidos, QA da calibração, preço de um código diferente da tabela), nada é gravado e o motivo aparece na tela.
- **Idempotente.** Rodar de novo não muda nada, e nada é apagado: SKU superado é desativado, não excluído.
- **Ensaiado em 15/10/2026** numa cópia do banco com as migrations e a inicialização do app aplicadas:
  - prévia: 0 divergência;
  - aplicação: 0 divergência;
  - segunda aplicação: nenhuma mudança;
  - matriz completa (61.236 células): 0 diferença contra a REV4.

## Arquivos
| Arquivo | O que é |
|---|---|
| `aplicar.py` | Orquestrador (prévia/aplicar, conferências, verificação) |
| `passo1_catalogo.py`, `passo2_calibracao.py`, `passo3_rev4.py` | Os três passos, portados dos scripts do gerador sem mudar a lógica |
| `dados_daune.py` | Dados das planilhas Daune 05/08 e 13/10, embutidos (as planilhas não vão para o Git) |
| `pi_23_09.py`, `ktc_07_10.py`, `catalogo_rev2.py`, `ancoras_ktc.py` | Dados KTC (PI 23/09, cotação 07/10, identidade dos SKUs, âncoras da calibração) |
| `ids_esperados_rev2.json` | 48 ids conferidos por `sku_key` antes de gravar |
| `esperado_rev4.json` | Preço B2B esperado (SP · não contribuinte · 30 dias) de cada código da REV4 |

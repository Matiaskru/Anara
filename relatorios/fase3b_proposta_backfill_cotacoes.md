# Fase 3B — proposta de backfill das cotações históricas em vendas (READ-ONLY)

Gerado em 2026-09-16 sobre `/Users/matiaskrueder/Anara-Cotacao/data/anara.db`. **Nada foi aplicado.** 21 cotações sem venda vinculada · confiança ALTA 0 · MEDIA 11 · BAIXA 10.

Critérios: ALTA = revisões da mesma genealogia (`cotacao_origem_id`); MEDIA = mesmo cliente, produto em comum e ≤ 30 dias; BAIXA = o resto (inclui cotações com cara de teste). Uma venda por genealogia; revisões nunca viram vendas separadas.

| id | número | cliente | rev. | geneal. | status | itens | valor | data | relação provável | proposta | confiança |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ANARA-2026-0001 | Anara | r1 | #1 | rascunho (arquivada) | 0 | — | 2026-07-15 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 2 | ANARA-2026-0002 | Anara | r1 | #2 | rascunho (arquivada) | 2 | R$ 31.570,72 | 2026-07-15 | ANARA-2026-0003, ANARA-2026-0004, ANARA-2026-0005, ANARA-2026-0007, ANARA-2026-0009 | mesma venda de ANARA-2026-0003 (1 produto(s) em comum, 0 dias), ANARA-2026-0004 (1 produto(s) em comum, 0 dias), ANARA-2026-0005 (1 produto(s) em comum, 0 dias), ANARA-2026-0007 (1 produto(s) em comum, 2 dias), ANARA-2026-0009 (1 produto(s) em comum, 22 dias) | **MEDIA** |
| 3 | ANARA-2026-0003 | Anara | r1 | #3 | rascunho (arquivada) | 1 | R$ 98,98 | 2026-07-16 | ANARA-2026-0002, ANARA-2026-0004, ANARA-2026-0005, ANARA-2026-0009 | mesma venda de ANARA-2026-0002 (1 produto(s) em comum, 1 dias), ANARA-2026-0004 (1 produto(s) em comum, 0 dias), ANARA-2026-0005 (1 produto(s) em comum, 0 dias), ANARA-2026-0009 (1 produto(s) em comum, 22 dias) | **MEDIA** |
| 4 | ANARA-2026-0004 | Anara | r1 | #4 | rascunho (arquivada) | 3 | R$ 217.047,85 | 2026-07-16 | ANARA-2026-0002, ANARA-2026-0003, ANARA-2026-0005, ANARA-2026-0009 | mesma venda de ANARA-2026-0002 (1 produto(s) em comum, 1 dias), ANARA-2026-0003 (1 produto(s) em comum, 1 dias), ANARA-2026-0005 (1 produto(s) em comum, 0 dias), ANARA-2026-0009 (1 produto(s) em comum, 21 dias) | **MEDIA** |
| 5 | ANARA-2026-0005 | Anara | r1 | #5 | rascunho (arquivada) | 1 | R$ 100,01 | 2026-07-16 | ANARA-2026-0002, ANARA-2026-0003, ANARA-2026-0004, ANARA-2026-0009 | mesma venda de ANARA-2026-0002 (1 produto(s) em comum, 1 dias), ANARA-2026-0003 (1 produto(s) em comum, 1 dias), ANARA-2026-0004 (1 produto(s) em comum, 1 dias), ANARA-2026-0009 (1 produto(s) em comum, 21 dias) | **MEDIA** |
| 6 | ANARA-2026-0006 | Anara | r1 | #6 | rascunho (arquivada) | 0 | — | 2026-07-17 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 7 | ANARA-2026-0007 | Anara | r1 | #7 | rascunho (arquivada) | 2 | R$ 5.087,63 | 2026-07-18 | ANARA-2026-0002 | mesma venda de ANARA-2026-0002 (1 produto(s) em comum, 3 dias) | **MEDIA** |
| 8 | ANARA-2026-0008 | Anara | r1 | #8 | rascunho (arquivada) | 25 | R$ 2.982,91 | 2026-07-20 | ANARA-2026-0014 | mesma venda de ANARA-2026-0014 (1 produto(s) em comum, 27 dias) | **MEDIA** |
| 9 | ANARA-2026-0009 | Anara | r1 | #9 | rascunho (arquivada) | 1 | R$ 134,92 | 2026-08-07 | ANARA-2026-0002, ANARA-2026-0003, ANARA-2026-0004, ANARA-2026-0005 | mesma venda de ANARA-2026-0002 (1 produto(s) em comum, 23 dias), ANARA-2026-0003 (1 produto(s) em comum, 23 dias), ANARA-2026-0004 (1 produto(s) em comum, 22 dias), ANARA-2026-0005 (1 produto(s) em comum, 22 dias) | **MEDIA** |
| 10 | ANARA-2026-0010 | Anara | r1 | #10 | rascunho (arquivada) | 3 | R$ 266,27 | 2026-08-17 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 11 | ANARA-2026-0011 | Anara | r1 | #11 | perdida (arquivada) | 3 | R$ 18.061,78 | 2026-08-17 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 12 | ANARA-2026-0012 | Anara | r1 | #12 | rascunho (arquivada) | 0 | — | 2026-08-17 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 13 | ANARA-2026-0013 | Anara | r1 | #13 | perdida (arquivada) | 0 | — | 2026-08-17 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 14 | ANARA-2026-0014 | Anara | r1 | #14 | rascunho (arquivada) | 2 | R$ 19.166,08 | 2026-08-17 | ANARA-2026-0008 | mesma venda de ANARA-2026-0008 (1 produto(s) em comum, 28 dias) | **MEDIA** |
| 15 | ANARA-2026-0015 | Anara | r1 | #15 | rascunho (arquivada) | 0 | — | 2026-08-31 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 16 | ANARA-2026-0016 | Anara | r1 | #16 | rascunho (arquivada) | 0 | — | 2026-09-01 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 17 | ANARA-2026-0017 | Anara | r1 | #17 | rascunho | 2 | R$ 145,64 | 2026-09-01 | ANARA-2026-0019, ANARA-2026-0021 | mesma venda de ANARA-2026-0019 (1 produto(s) em comum, 4 dias), ANARA-2026-0021 (2 produto(s) em comum, 9 dias) | **MEDIA** |
| 18 | ANARA-2026-0018 | Anara | r1 | #18 | rascunho | 0 | — | 2026-09-01 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 19 | ANARA-2026-0019 | Anara | r1 | #19 | pedido (arquivada) | 3 | R$ 20.803,50 | 2026-09-05 | ANARA-2026-0017, ANARA-2026-0021 | mesma venda de ANARA-2026-0017 (1 produto(s) em comum, 5 dias), ANARA-2026-0021 (1 produto(s) em comum, 4 dias) | **MEDIA** |
| 20 | ANARA-2026-0020 | Anara | r1 | #20 | rascunho | 0 | — | 2026-09-09 | — | cara de teste / arquivada — provavelmente não é venda; arquivar em vez de vincular | **BAIXA** |
| 21 | ANARA-2026-0021 | Anara | r1 | #21 | aprovada | 4 | R$ 4.539,18 | 2026-09-10 | ANARA-2026-0017, ANARA-2026-0019 | mesma venda de ANARA-2026-0017 (2 produto(s) em comum, 10 dias), ANARA-2026-0019 (1 produto(s) em comum, 5 dias) | **MEDIA** |

## Decisão pendente

Aplicar o agrupamento exige decisão humana por linha. O caminho, quando decidido, é `POST /oportunidades/{id}/vincular` (mesmo cliente, nunca cross-client) — sem script de backfill automático.

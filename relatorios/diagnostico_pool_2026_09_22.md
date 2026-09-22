# QueuePool estourado — causa raiz e correção (22/09/2026)

    sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 5 reached,
    connection timed out, timeout 30.00

## 1. Não é vazamento de sessão

A primeira hipótese — sessão aberta e não devolvida — foi **descartada com medição**, não por
leitura de código. `scripts/diagnostico_2026_09_22/reproduzir_pool.py` sobe o uvicorn **no mesmo
processo** (o pool vive no processo do servidor; medir de fora não prova nada) e instrumenta o
engine com os eventos `checkout`/`checkin`:

* `checkedout` volta a **0** depois de **toda** requisição, em todas as rotas;
* volta a 0 também depois de cada rajada concorrente;
* pool ocioso ao fim: `{'checkedout': 0, 'checkedin': 5, 'overflow': 0, 'size': 5}`.

O código confere com a medição: `app/db.get_session` é `with Session(engine) as session: yield
session` — o `with` devolve a conexão inclusive quando a rota levanta exceção. Nenhum
`next(get_session())`, nenhum `Session(engine)` sem `with`. Os testes 1–3 de
`tests/test_pool_conexoes_2026_09_22.py` fixam isso (o teste 3 varre `app/` e falha se o padrão
reaparecer).

## 2. A causa: N+1 sobre as tabelinhas de configuração

Listar o catálogo resolve o custo de **cada** SKU, e resolver o custo de um SKU relê as mesmas
tabelinhas: premissas, `ParametroKTC`, `NcmRegra`, `MargemRegra`, as versões de
`CustoReferencia` e o fornecedor. Perfil da varredura de governança, no banco real (380 SKUs):

| tabela | consultas |
|---|---:|
| custoreferencia | 1.830 |
| premissa | 1.694 |
| parametroktc | 1.634 |
| ncmregra | 618 |
| cmtpreco | 198 |
| materialpreco | 114 |
| toalhapreco | 84 |
| **total** | **6.174 numa requisição** |

Duas agravantes:

* `/admin/produtos` varria o catálogo **duas vezes** — uma para a tabela, outra para os contadores;
* o identity map do SQLAlchemy guarda **referência fraca**: numa varredura que não segura o
  objeto, o coletor descarta o `Fornecedor` e o SKU seguinte o reconsulta (2 consultas por SKU).

Na máquina local cada consulta custa ~0,1 ms e a página "só" demora 2,6 s. Num PostgreSQL
gerenciado, com 1–3 ms de RTT, **as mesmas 13.869 consultas viram ~53 segundos com a conexão
fora do pool**. Com `pool_size=5 + max_overflow=5`, três ou quatro pessoas na mesma tela bastam
para a requisição seguinte esperar os 30 s de `pool_timeout` e estourar. O pool não vazava:
ficava **ocupado**.

**Reprodução literal do erro de produção** (cópia do código anterior à correção, banco real,
`--latencia-ms 2` emulando a rede):

    rajada 12× /admin/produtos → erros=2 · 42.160 ms · 138.728 queries
    sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 5 reached,
    connection timed out, timeout 30.00

## 3. A correção: ler cada tabela uma vez por requisição

`pricing_service.cache_de_leitura(session)` — um `contextvars` de **escopo explícito e curto**,
aberto só em caminho de leitura pura (`governanca_produtos.listar`, catálogo e busca de
`/produtos`). Fora do bloco nada muda: `_memo` cai direto na consulta, como sempre foi. Quem
grava chama `invalidar_cache_de_leitura()` — ler valor velho depois de gravar seria erro
silencioso.

* `config_service._linhas` memoiza a **carga** da tabelinha; a escolha por vigência continua
  fora do memo, porque depende da data pedida.
* `custo_service._versoes_para_leitura` traz as versões de custo **de uma vez** (192 linhas) e
  indexa por produto.
* `pricing_service.fornecedor_do_produto` resolve o fornecedor uma vez por bloco — e segura a
  referência, que é o que o identity map fraco não faz.
* `/admin/produtos` faz **uma** varredura: `gov.listar` uma vez, contadores sobre ela, e
  `gov.filtrar` para a tabela.

**`pool_size` e `max_overflow` continuam 5+5.** Aumentar o pool só adiaria o mesmo estouro: uma
página que segura a conexão por 50 s esgota qualquer pool.

## 4. Depois (mesmo banco, mesma latência de 2 ms/consulta)

| medida | antes | depois |
|---|---:|---:|
| `/admin/produtos` — consultas | 13.869 | **29** |
| `/admin/produtos` — tempo | 53.245 ms | **220 ms** |
| `/produtos` — consultas | 2.865 | **25** |
| varredura de governança (380 SKUs) | 6.174 consultas / 1.433 ms | **27 / 163 ms** |
| rajada 12× `/admin/produtos` | **2 erros 500** · 42.160 ms | **0 erros** · 1.580 ms |
| rajada 40× `/admin/produtos` | — | **0 erros** · 5.294 ms |
| `checkedout` com o servidor ocioso | 0 | 0 |

## 5. Nenhum número mudou

Snapshot completo de precificação (309 SKUs KTC × 9 cenários: B2B, tabela, `preco_base`,
comissão, margem realizada, lucro, status), gerado com o código anterior e com o corrigido
contra o **mesmo banco**: **idêntico, campo a campo**. Cache de leitura é sobre *quantas vezes se
pergunta*, não sobre *qual é a resposta*.

## 6. Testes

* `tests/test_pool_conexoes_2026_09_22.py` (11) — disciplina de sessão, o pool continua 5+5,
  transparência do cache, queda de ordem de grandeza nas consultas, escopo por contexto
  (duas threads = dois blocos), invalidação na escrita.
* Direcionados, verdes: governança (13), calculadora da vendedora (18), toalha/GSM (12),
  I.I. zero, política 21/09, sinal, custo (4 arquivos), crise (191), versionamento/fiscal/
  permissões (295).

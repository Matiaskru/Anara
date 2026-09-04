# SESSÃO 3B — RELATÓRIO DA SESSÃO

> **Escrito primeiro como handoff de pausa, e fechado depois que a sessão foi retomada e
> concluída na mesma conversa.** A Sessão 3B está **executada e aguardando auditoria** — o que
> não é o mesmo que aprovada. O checkpoint WIP `e27e11e` permanece no histórico local; o commit
> final vem depois dele.
>
> O estado corrente do projeto é o de `ANARA_EXECUTION_STATE.md`; este arquivo permanece pelo
> registro das **medições e decisões** (seção G), que são a justificativa de por que não existe
> migration `Numeric`.

Escrito em 04/09/2026, ao pausar; fechado no mesmo dia, ao concluir.

---

## A. Objetivo original

Eliminar a dependência de `float` no núcleo econômico: `Decimal` nos motores, política central
de arredondamento, preço comercial em 2 casas, lucro/margem/comissão recalculados **sobre o
preço arredondado**, rateio sem perda de centavo e reconciliação monetária ao centavo.

Escopo negativo confirmado e respeitado: nada de fiscal, custo, Daune, KTC, TRANSAL, regras de
frete, FCP, DIFAL, Sessões 1/2/3A. As sete pendências de frete continuam congeladas e **não**
foram tocadas.

## B. HEAD de entrada

`4a0a4a0` — *Estado: Sessões 1, 2 e 3A aprovadas; 3B é a próxima*
Alembic `0009` · 352 testes · árvore limpa · sem remote.

## C. Commit WIP

`WIP: checkpoint intermediário Sessão 3B Decimal` — ver `git log -1`.
Local. Sem push. Sem remote. **Não significa aprovação nem conclusão.**

## D. Arquivos alterados

**Novos (núcleo):**

| Arquivo | O que é |
|---|---|
| `app/dinheiro.py` | **A política monetária única.** Conversão segura (`D`), precisão interna, `ROUND_HALF_UP`, rateio por maior resto, reconciliação |
| `tests/test_precisao_decimal.py` | Os 40 pontos exigidos pela sessão, em 69 testes |
| `tests/decimais.py` | `aprox()` — comparação com tolerância explícita, Decimal e float misturados |
| `scripts/scan_float_economico.py` | Scan §31/§32: varredura por AST **e** por chamada real |

**Novos (relatórios):**
`relatorios/baseline_entrada_sessao3b.json` (2,0 MB — baseline de entrada, gerado com o código
**pré-3B**, via `git stash`) · `relatorios/scan_float_sessao3b.json` ·
`relatorios/sessao3b_antes.json` (estado do banco antes da sessão, procedimento do `BACKUP.md`).

**Motores convertidos para Decimal:** `pricing_engine.py` · `frete_engine.py` · `ktc_engine.py` ·
`nationalization.py` · `fiscal_rules.py` · `margin_rules.py` · `payment_terms.py` · `peso.py` ·
`custo_service.py`

**Fronteiras ajustadas:** `pricing_service.py` · `pdf_bridge.py` · `templating.py` ·
`calculadora.py` · `routers/cotacoes.py` · `routers/importar.py` ·
`scripts/baseline_regressao_v2.py`

**Documentação:** `AUDIT_ANARA_MASTER.md` (só o B-18 novo).

**Testes adaptados (13 arquivos):** conversão mecânica de `pytest.approx` → `aprox`, mais a
reescrita semântica dos testes de margem.

## E. O que JÁ ESTÁ IMPLEMENTADO

1. **Política central única** (`app/dinheiro.py`): precisão interna de 34 dígitos declarada;
   `ROUND_HALF_UP` como único arredondamento comercial; `D()` como única porta de entrada
   (nunca `Decimal(float)`); `para_float()` como única saída; `ratear_centavos()` por maior
   resto com desempate determinístico.
2. **Motores em Decimal**, com normalização na fronteira de entrada de cada um.
3. **Preço preciso → preço comercial → recomposição.** `ResultadoPrecificacao` ganhou
   `preco_preciso`, `margem_alvo` e `ajuste_arredondamento`. A margem devolvida é a **real**,
   do preço cobrado.
4. **Lucro por resíduo** — a linha reconcilia ao centavo por construção
   (`ResultadoPrecificacao.reconcilia()`).
5. **Rateio de frete** por maior resto: R$ 100,00 ÷ 3 = 33,34 + 33,33 + 33,33.
6. **Fronteiras fechadas**: banco, JSON da API `/calc`, memória do preço, PDF, filtros Jinja.
7. **Comissão exata nas fronteiras** de 60/70/80/90/100%.
8. **Scan de float** executado, com relatório salvo.

## F. O que faltava na pausa — TUDO FECHADO

Todos os itens abaixo foram concluídos depois da retomada. Ficam registrados com o resultado.

1. **§34 — comparação de baseline. FEITO.** `scripts/comparar_baseline_sessao3b.py`,
   resultado em `relatorios/regressao_sessao3b.json`: **10.440 células**, sendo 5.900
   `ROUNDING_CORRIGIDO` e 4.540 `IGUAL`. **Zero `NAO_EXPLICADA`, zero mudança real de preço.**
   Maior Δ de preço unitário: **R$ 0,005000** — exatamente o limite do meio centavo, nunca
   ultrapassado. 104 SKUs tiveram `custo_net_recalculado` classificado como
   `DECIMAL_REPRESENTATION_ONLY`: Δ máximo **5,7×10⁻¹⁴** absoluto (4,2×10⁻¹⁶ relativo), que é o
   erro do encadeamento em float sendo removido, não mudança econômica.
2. **§35 — regressão do waterfall. FEITO.** `tests/test_waterfall_sessao3b.py`, 19 testes.
   Recompõe o waterfall **de fora**, a partir das alíquotas que o motor fiscal resolveu, para
   KTC SP · KTC interestadual · Daune SP · Daune interestadual · Decor interestadual · KTC não
   contribuinte RJ (DIFAL + FECP = 22%) · Daune consumidor final SP · CF logístico · RV
   logístico · CF+RV juntos · preço comercial arredondado · preço negociado · cotação mista.
3. **§28 — verificação formal do histórico. FEITO.** `relatorios/sessao3b_depois.json` gerado
   e comparado com `_antes.json`: **a única diferença entre os dois é o timestamp da própria
   medição.** Hash do arquivo do banco, integridade, contagens e digest por tabela e por coluna:
   idênticos. No comparador, os campos econômicos do histórico deram **18 cotações × 3 campos =
   54 IGUAL** e **45 itens × 17 campos = 765 IGUAL**, com **zero mudanças econômicas** —
   incluindo o `sha256` da memória de preço congelada em cada item.
4. **§36 — documentação. FEITO.** Seção "Precisão monetária — uma régua só" em `CLAUDE.md`,
   bloco "Precisão monetária (Sessão 3B)" em `ANARA_EXECUTION_STATE.md`, e o docstring de
   `app/dinheiro.py` como referência longa.
5. **Scripts economicamente relevantes. FEITO.** Seis corrigidos na ponte
   (`adicionar_skus_do_catalogo`, `cadastrar_edredom_280g`, `classificar_base`,
   `comparar_regressao`, `importar_fornecedores_nacionais`, `reconciliar_daune`).
   `reconciliar_daune` e `cadastrar_edredom_280g` rodam em simulação sem erro. Dois deles
   permanecem quebrados por causa **anterior** à 3B — ver B-19 na seção H.
6. **API, templates e PDF. FEITO.** Ver seção M.
7. **§42 — checkpoint final.** Entregue na resposta da sessão.

## G. Decisões tomadas

**G1. Nenhuma migration. As colunas continuam `REAL`.** — decisão medida, não presumida.

Teste executado com SQLAlchemy + SQLite:

| gravado | `Numeric(18,2)` devolve | `Float` + `D(str(x))` devolve |
|---|---|---|
| `34.71540940423179` | **`34.72`** | `34.71540940423179` |
| `1234.5678999` | **`1234.57`** | `1234.5678999` |
| `0.0759` | **`0.08`** | `0.0759` |

`typeof()` no SQLite devolve `real` nos três casos: **`Numeric` não cria coluna decimal
nativa**, só trunca na leitura. E o histórico guarda preços com a precisão cheia do float —
há itens de 2026 com `preco_negociado = 34.71540940423179`. Migrar para `Numeric(18,2)`
**reescreveria economicamente cotações emitidas**, o que o projeto proíbe. Por isso o
armazenamento continua REAL e a ponte é `D()`, exata nos dois sentidos.
Consequência: **Alembic permanece em `0009`.**

**G2. Ausência de valor não vira zero.** `D(None)` devolve `None`; só `D0()` assume zero, e
apenas onde ausência realmente significa zero.

**G3. Lucro é resíduo, não conta paralela.** Receita menos todos os componentes já
quantizados. É o que faz a linha fechar ao centavo sem "aproximadamente".

**G4. CF do frete sai do motor em centavos.** R$ 370,2272… não é quantia pagável.
Muda o valor da Sessão 3A em até meio centavo — classe `ROUNDING_CORRIGIDO`, e o teste
`test_gross_up_usa_a_aliquota_cadastrada_e_nao_12_hardcoded` foi atualizado com o motivo escrito.

**G5. CNET **não** é quantizado.** É custo interno; arredondá-lo mudaria o preço de venda de
32 SKUs Daune sem que ninguém tivesse pedido. Só o preço **comercial** vira centavo.

**G6. Memória e relatórios são representação externa, em `float`.** `custo_net()`,
`como_dict()` e o baseline saem em float — se saíssem em Decimal, `json.dumps(default=str)`
transformaria dinheiro em string e mudaria o formato de snapshots já emitidos.

**G7. `markup_implicito` é o markup do preço cobrado**, não do teórico — porque
`calcular_por_margem` arredonda e recompõe. Recompor a fórmula a partir dele devolve o preço
comercial exato.

**G8. Filtros Jinja passaram a usar a régua do sistema.** `f"{v:,.2f}"` arredonda o binário
(2,675 → "2,67"); `fmt_brl` agora usa `dinheiro()`.

## H. Bugs e preexistências descobertos

**B-18 — a poda de backup apaga o backup recém-criado.** Registrado no
`AUDIT_ANARA_MASTER.md`. `shutil.copy2` preserva o mtime da origem, então todas as cópias do
mesmo `anara.db` ficam com o mesmo mtime; ao atingir `MAX_BACKUPS = 30`, a poda ordena por
mtime, o empate cai na ordem arbitrária de `os.listdir` e o arquivo recém-criado pode ser o
removido. **Reproduzido no código original em `4a0a4a0`, sem nenhuma alteração da 3B — não é
regressão desta sessão e não foi corrigido nela.**
Mitigação aplicada: os 27 backups gerados pelas execuções da suíte **desta sessão** foram
**movidos** (não apagados) para `data/backups/sessao3b-artefatos-de-teste/`, fora do alcance da
poda. `data/backups/` é gitignored. O ponto de rollback real está preservado em
`~/Anara-Cotacao-Backups/anara_sessao3b_pre_20260904-102022.db`
(sha256 `cb20953eee82e181bb4f43ca3e472ee0a3399cf55b87e12e746c83bd7b9551af`).

**Bug real de produção corrigido de passagem:** `ParametrosKTC` era normalizado só no
`__post_init__`, mas `pricing_service.parametros_ktc_do_produto` preenche
`material_price_usd_m2` e `price_usd_kg` **depois** de construir o objeto — esses dois campos
ficavam em float dentro do motor industrial. Resolvido com `ParametrosKTC.normalizar()`,
chamado também na entrada de cada cálculo.

**Bug real de produção corrigido de passagem:** o endpoint `POST /cotacoes/{id}/calc` passou a
serializar `Decimal` e quebrava o JSON. Fechado com `ResultadoPrecificacao.como_dict()`.

## I. Testes

| Momento | Total | Situação |
|---|---|---|
| Entrada da sessão (`4a0a4a0`) | **352** | passando |
| **Maior número confirmado passando nesta sessão** | **422** | **suíte completa, `422 passed`** |
| Sanidade no encerramento | **160** | `test_precisao_decimal` + `test_motor_comercial` + `test_motor_industrial_ktc` + `test_nacionalizacao` + `test_frete`, `160 passed in 0.72s` |

**Nenhum teste falhando no momento do checkpoint.** Os 70 testes novos são: 69 em
`tests/test_precisao_decimal.py` + 1 em `tests/test_motor_comercial.py`
(`test_margem_alvo_exata_no_preco_preciso`).

Também executado no encerramento: `python3 -m compileall -q app scripts tests` (rc 0) e import
dos 16 módulos alterados (OK).

> **Atenção ao tempo de execução.** A suíte roda em ~35–45 s em condições normais, mas esta
> máquina teve picos em que a mesma suíte levou 23 e 38 minutos. Isso é carga da máquina, não
> do código — foi medido antes e depois das mudanças. Rode a suíte completa em background.

## J. Migrations e Alembic

**Nenhuma migration criada.** `alembic current` = `alembic heads` = **`0009`**, igual à
entrada. Ver decisão **G1**: migrar as colunas monetárias para `Numeric` reescreveria o
histórico e foi rejeitado com evidência.

## K. Scan de floats

Executado. Relatório em `relatorios/scan_float_sessao3b.json`.

**Varredura textual (AST) em `app/` — 246 ocorrências:**

| Classe | | Nº |
|---|---|---|
| A | removido do núcleo econômico | 16 |
| B | permitido / não econômico | 25 |
| C | ponte inevitável, conversão segura | 205 |
| **D** | **REVIEW_REQUIRED** | **0** |

As 16 da classe A são **todas anotações de tipo** em assinaturas públicas dos motores
(parâmetros que aceitam float vindo do banco e normalizam internamente). **Zero `float()` e
zero `round()` dentro de qualquer motor puro.** Os 3 `round()` de `app/` estão em `nomes.py` e
`spec_parser.py` — formatação de nome e parser de especificação, não monetário. `Decimal(var)`
só existe dentro de `app/dinheiro.py`, que é a própria ponte.

**Varredura dinâmica (tipo real em execução):** 24 pontos econômicos devolvem `Decimal`,
3 fronteiras devolvem `float` corretamente, **0 divergentes**.

**Falta:** nada no scan em si. O que falta é o §35, que é regressão de valor, não de tipo.

## L. Baseline e histórico

**Já verificado:**
- `tests/test_fundacao.py` — **14 passando**, incluindo
  `test_cotacoes_e_itens_historicos_nao_mudaram`, `test_bases_de_importacao_preservadas` e
  `test_baseline_e_reprodutivel`. Isso já é evidência forte de que as 18 cotações, os 45 itens
  e as 4 bases **não mudaram**.
- `relatorios/baseline_fase0.json` **não foi tocado** (segue no commit `165d75e`).
- Baseline de entrada da 3B gerado com o código pré-3B.

**Falta verificar:**
- a comparação célula a célula pré-3B × pós-3B, com classificação (item F1);
- o digest de coluna do banco depois × antes (item F3).

## M. Fronteiras — verificado

**Nenhum vazamento de `Decimal` permanece.** A varredura dinâmica não acha divergência,
`json.dumps` do dicionário externo funciona sem `default=str`, e o roundtrip de banco é testado.

**API `/calc`** — exercitada de verdade, chamando a rota: modo margem devolve `float` em todos
os campos monetários; `json.dumps` do payload passa sem `default=str`; preço negociado 99,90
volta e `D(json) == Decimal("99.90")`; e um valor digitado de 1234,567 com quantidade 2,5 sai
como R$ 1.234,57 unitário e R$ 3.086,43 de total — 3.086,425 arredondado por `ROUND_HALF_UP`.

**PDF** — gerado de verdade, a partir da cotação histórica `ANARA-2026-0002` lida em modo
somente leitura. O banco guarda `preco_negociado = 34.71540940423179`; o documento imprime
**R$ 34,72**. Todos os valores saem no formato `R$ x.xxx,xx`, e o total geral (R$ 31.570,72) é
a soma exata das linhas. `gerar_cotacao.py` **não foi alterado** — o PDF aprovado não se
redesenha.

**Templates** — os filtros foram exercitados com valores hostis: `0.30000000000000004` →
"R$ 0,30", `2.675` → "R$ 2,68" (a régua do sistema, não a do `format`), `100.1` → "R$ 100,10".
Varredura nos 14 templates: as únicas três expressões monetárias sem filtro são atributos
`data-*` de `cotacao_detail.html`, lidos pelo JavaScript da página — as células visíveis
passam por `|brl`.

**Scripts** — os seis economicamente relevantes foram corrigidos na ponte. Dois deles
(`classificar_base`, `importar_fornecedores_nacionais`) e o `comparar_regressao` continuam
quebrando por causa **anterior à 3B**: ver B-19.

## N. Próximos passos, em ordem

1. Ler este arquivo e `git log -1` para confirmar o commit WIP.
2. Rodar a suíte completa **em background** e confirmar 422 passando.
3. Escrever `scripts/comparar_baseline_sessao3b.py` (item F1) e rodá-lo:
   `baseline_entrada_sessao3b.json` × estado atual, com `NAO_EXPLICADA = 0`.
   Salvar em `relatorios/regressao_sessao3b.json`.
4. Rodar também contra o baseline imutável (`comparar_baseline_onda1.py`) para confirmar que as
   diferenças acumuladas continuam sendo as das Sessões 1/2/3A **mais** o arredondamento.
5. Escrever a regressão do waterfall do §35 (item F2).
6. Rodar os scripts de `scripts/` listados em **M** e corrigir as pontes que quebrarem.
7. Abrir a plataforma e conferir telas e um PDF (item M).
8. `backup_banco.py estado --json relatorios/sessao3b_depois.json` e comparar digests (item F3).
9. Documentar a política em `CLAUDE.md` e `ANARA_EXECUTION_STATE.md` (item F4).
10. Produzir o checkpoint final do §42, com os 28 itens.
11. Commit definitivo, substituindo o WIP. **Sem push. Sem remote.**

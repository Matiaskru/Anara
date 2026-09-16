# SYSTEM_AS_BUILT — Anara

**Documentação as-built do sistema como ele existe no código em 08/09/2026.**

| | |
|---|---|
| HEAD | commit da Fase 3C (16/09/2026) — redesign comercial e proposta cliente |
| Alembic | `0020` — `cotacao.observacao_cliente` (aditiva) |
| Suíte | **1200 passando**, 0 falhas (`python3 -m pytest -q`) |
| Código | ~13.000 linhas em `app/`, 41 módulos, 15 routers, 34 templates |
| Banco | SQLite em `data/anara.db`, 33 tabelas |
| Atualizado em | 09/09/2026, ao fim do Product Cleanup |

Este documento descreve **o que o código faz**, não o que foi especificado. Onde uma
afirmação não pôde ser comprovada lendo o código, está escrito **NÃO CONFIRMADO NO CÓDIGO**.

> **Premissas vigentes** (não exemplos históricos): câmbio **R$ 5,19** desde 08/09/2026 ·
> frete internacional US$ 0,516/kg · outras despesas US$ 0,2488/un · PIS/COFINS nominal 9,25%
> (efetivo por item: `9,25% × (1 − ICMS)`).
> A fonte de verdade é sempre Admin → Premissas; esta tabela envelhece.

---

# 1. Visão geral da arquitetura

## 1.1 Stack

| Camada | Tecnologia | Onde |
|---|---|---|
| Web | FastAPI | `app/main.py` |
| Templates | Jinja2 | `app/templating.py`, `app/templates/` |
| ORM | SQLModel sobre SQLAlchemy | `app/models.py` |
| Banco | SQLite | `data/anara.db` |
| Migrations | Alembic | `alembic/versions/`, atual `0017` |
| PDF | ReportLab — proposta comercial da Fase 3C | `app/pdf_bridge.py` (allowlist) → `app/pdf_proposta.py` |
| Planilha | openpyxl | `app/excel_import.py` |
| Autenticação | argon2-cffi + itsdangerous | `app/auth.py` |
| Testes | pytest | `tests/`, `pytest.ini` |

## 1.2 Princípio estrutural

Os **motores são puros**: não importam FastAPI, Jinja nem sessão de banco. Recebem números e
parâmetros, devolvem resultado com a memória do cálculo.

```
motores puros          ktc_engine · nationalization · pricing_engine · fiscal_rules
                       frete_engine · margin_rules · payment_terms · peso · dinheiro
        ↑
camada de serviço      pricing_service · custo_service · frete_service · workflow_service
                       crm_service · admin_service · metrics_service · config_service
        ↑
routers (HTTP)         app/routers/*.py
        ↑
templates              app/templates/*.html
```

`app/pricing_service.py` é o único lugar que liga banco → motores: lê premissas versionadas,
monta parâmetros, chama os motores puros e devolve a memória do preço.

## 1.3 Árvore resumida

```
app/
  main.py               FastAPI, AuthMiddleware, PUBLIC_PATHS, montagem dos routers
  db.py                 engine, ANARA_DB_URL, caminho_do_banco(), get_session()
  auth.py               argon2id, cookie assinado, _resolver_secret()
  permissoes.py         predicados e barreiras de autorização
  confidencial.py       listas de permissão e de campos confidenciais
  models.py             33 tabelas + enums do domínio          (1.325 linhas)
  dinheiro.py           D(), dinheiro(), ratear_centavos(), para_float()

  ktc_engine.py         motor industrial KTC (puro)
  nationalization.py    EXW US$ → CUSTO NET R$ (puro)
  pricing_engine.py     markup/margem → preço, TaxRuleSet (puro)
  fiscal_rules.py       ICMS, interestadual, DIFAL, FCP (puro)
  frete_engine.py       CF/RV do frete comercial (puro)
  margin_rules.py       resolução de margem-alvo por prioridade
  payment_terms.py      encargo financeiro por condição
  peso.py               resolução do peso taxado

  pricing_service.py    banco ↔ motores; memória do preço      (640 linhas)
  custo_service.py      CNET nacional, versões de custo
  frete_service.py      grupos logísticos, tabela TRANSAL
  workflow.py           estados, blockers, exceções, fingerprint (puro)
  workflow_service.py   grava transições, aprovações, snapshots
  crm_service.py        clientes, oportunidades, atividades     (623 linhas)
  metrics_service.py    definições métricas — fonte única       (616 linhas)
  admin_service.py      preview/apply, versionamento, trilha    (958 linhas)
  config_service.py     leitura de premissas versionadas
  arquivamento.py       arquivar/restaurar cotação e cliente
  seeds.py              carga inicial de premissas e tabelas
  migrations.py         backfill legado + backup automático
  pdf_bridge.py         monta header/items/totals → build_pdf()

  routers/              14 módulos HTTP
  templates/            28 templates Jinja2
  static/css/anara.css  design system (tokens --midnight, --copper, --sand…)

alembic/versions/       0001 … 0017
scripts/                23 scripts (bootstrap, backup, importação, smoke, baseline)
tests/                  suíte pytest
```

---

# 2. Mapa completo de telas

Todas as rotas HTML existentes. `AuthMiddleware` exige sessão em **tudo** exceto
`PUBLIC_PATHS = {"/login", "/logout", "/health", "/primeiro-acesso"}` e `/static/*`.
Rota sem sessão → `303` para `/login?next=<path>`.

### Navegação

O menu lateral tem **7 itens**, organizados por tarefa (Fase 3B):

```
Vendas · Clientes · Cotações · Produtos · Relatórios
+ Aprovações   (quem tem alçada — `aprova_cotacoes`, não `ve_economia`)
+ Admin        (papel administrativo)
```

**Vendas** é o nome de interface da Oportunidade. "Meu dia" (`/comercial`) e o quadro por
etapa (`/pipeline`) continuam existindo sem linha de menu; `/oportunidades` redireciona para
`/vendas`. Vendedora entra e cai em **Vendas**; `/` (dashboard) a redireciona para lá.
Nova cotação é ação, não linha de menu. Calculadora,
importação, configurações, auditoria, qualidade da base e saúde moram **dentro do Admin** —
`/admin` é um hub com 13 áreas, e `/configuracoes` deixou de ser um segundo endereço a
memorizar.

## 2.1 Autenticação

### `/login` — Entrar
- **Arquivo:** `app/routers/login.py:46` · template `login.html`
- **Acesso:** público
- **Campos:** e-mail, senha, `next` (hidden)
- **Ações:** POST `/login`
- **Comportamento:** se o banco não tem nenhum usuário, o GET redireciona para
  `/primeiro-acesso` (303)
- **Erro:** mensagem **genérica única** — `"E-mail ou senha inválidos."` — para e-mail
  inexistente, senha errada e conta desativada. O hash é verificado mesmo sem usuário
  (`_HASH_ISCA`), para o tempo de resposta não virar oráculo
- **Status de erro:** 401

### `/primeiro-acesso` — Criar o primeiro OWNER
- **Arquivo:** `app/routers/login.py:105,119` · template `primeiro_acesso.html`
- **Acesso:** público, **e só existe enquanto `_sem_usuarios(session)` é verdadeiro**
- **Campos:** nome, e-mail, senha, confirmar
- **Validações:** nome não vazio; e-mail com `@` e ponto no domínio; senha ≥ 8 caracteres
  (`SENHA_MINIMA`); as duas senhas iguais
- **Ação:** cria `Usuario` com `papel=OWNER`, `can_manage_users=True`,
  `criado_por="primeiro-acesso"`, aplica o cookie e redireciona para `/`
- **Fechamento do portão:** o POST **refaz** a checagem de banco vazio; com qualquer usuário
  existente, ambos os métodos redirecionam para `/login`
- **Observação:** rota adicionada nesta sessão, não commitada

### `/logout`
- **Arquivo:** `app/routers/login.py:77` · sem template
- **Método:** GET (ver pendência **B-20**)
- **Ação:** limpa o cookie, redireciona para `/login`

## 2.2 Home e comercial

### `/` — Dashboard
- **Arquivo:** `app/routers/dashboard.py:15` · template `dashboard.html`
- **Acesso:** OWNER/ADMIN. Vendedora → `303 /vendas` (Fase 3B); o dashboard novo é da 3C

### `/vendas` — Vendas (Fase 3B)
- **Arquivo:** `app/routers/vendas.py` · template `vendas_list.html`
- **Exibe:** cliente, projeto, valor atual, **status comercial único** (Rascunho / Enviado /
  Negociação / Vendido / Perdido), responsável, última atualização, próxima atividade.
  Status trocado inline (`POST /vendas/{id}/status`, grava na hora, histórico append-only)
- **Ações:** POST `/vendas` (cliente + projeto → RASCUNHO, responsável = quem criou)

### `/vendas/{id}` — Venda
- **Template** `venda_detail.html`. Status, responsável, marcar **vendido** (proposta aceita
  → `crm.marcar_ganha` → `validar_compromisso_firme`; pós-venda nasce AGUARDANDO_ENTREGA),
  **perdido** (motivo obrigatório), reabrir (volta à última etapa aberta; legado → Rascunho),
  nova cotação nesta venda, **registrar atualização** (+ próxima atividade opcional),
  cotações/revisões, timeline consolidada, bloco de **pós-venda**
- **Pós-venda:** `POST /vendas/{id}/pos-venda/{entrega-prevista|entrega|observacao}`
  (vendedora) · `/{faturamento|pagamento-previsto|pago|atrasado|corrigir}` (OWNER/ADMIN,
  403 para vendedor, `AuditLog`)
- **Automação:** cotação emitida/enviada → venda em RASCUNHO vai a ENVIADO
  (`crm.avancar_por_envio`, ator "sistema (automático)"); em NEGOCIAÇÃO nada muda; VENDIDO,
  PERDIDO e NEGOCIAÇÃO nunca são marcados sozinhos
- `/clientes/{id}/vendas.json` alimenta o formulário de nova cotação

### `/comercial` — O dia
- **Arquivo:** `app/routers/crm.py:74` · template `crm_home.html`
- **Acesso:** autenticado
- **Exibe:** atividades atrasadas, do dia, oportunidades sem próxima atividade

### `/pipeline` — Quadro por etapa (mantido, fora do menu)
- **Arquivo:** `app/routers/crm.py:102` · template `crm_pipeline.html`
- **Exibe:** três colunas (`RASCUNHO`, `ENVIADO`, `NEGOCIACAO`), cards de `crm_service.cartao()`
- **Confidencialidade:** preço e total comerciais podem aparecer; custo, margem, lucro e
  markup, não

### `/oportunidades` — redireciona para `/vendas` (Fase 3B)

### `/oportunidades/{id}` — Oportunidade 360
- **Arquivo:** `app/routers/crm.py:150` · template `crm_oportunidade.html`
- **Exibe:** dados, histórico de etapas, atividades, cotações vinculadas, timeline
- **Ações:** POST `/oportunidades/{id}/etapa`, `/responsavel`, `/ganha`, `/perdida`,
  `/reabrir`, `/cotacao`, `/vincular`

### `/clientes` — Lista
- **Arquivo:** `app/routers/clientes.py:13` · template `clientes_list.html`
- **Ações:** POST `/clientes` (criar), `/clientes/{id}/arquivar`, `/restaurar`

### `/clientes/{id}` — Cliente 360 (Fase 3B)
- **Arquivo:** `app/routers/clientes.py` · template `cliente_detail.html`
- **Visão geral:** cadastro (razão social, CNPJ, cidade/UF, fiscal, contatos) + números
  **derivados** (`metrics_service.cliente_360`): total comprado = Σ `valor_fechado` das
  vendas GANHAS (cotação enviada não é compra; R1/R2 não somam), quantidade, ticket médio,
  última compra (maior `won_em`), em andamento, em aberto, atrasado, faturado, pago
- **Vendas:** abertas / vendidas / perdidas · **Cotações:** todas do cliente, com revisão e
  venda vinculada ("Sem venda vinculada (legado)") · **Contatos**
- **Ações:** POST `/vendas` (nova venda do cliente), `/clientes/{id}/contatos`;
  `POST /clientes` cria com nome só e recusa CNPJ repetido (409, `crm.criar_cliente`)
- **Bloqueio informativo:** `crm_service.dados_fiscais_faltando()` lista o que falta para
  emitir

## 2.3 Cotação

### `/cotacoes` — Lista (módulo próprio — não fica escondido em Vendas)
- **Arquivo:** `app/routers/cotacoes.py` · template `cotacoes_list.html`
- **Fase 3B:** coluna **Venda** (link) ou "Sem venda vinculada (legado)"; revisão marcada;
  filtros por status incluem os estados legados e arquivadas. Status técnico da cotação
  **não** vira status comercial da venda
- **Nova cotação exige venda** (`exigir_venda`): venda aberta do cliente ou nome de projeto
  para criar uma (`nova_venda`); sem vínculo → 400; venda de outro cliente → 409.
  `Cotacao.oportunidade_id` continua NULO nas 21 históricas (não é NOT NULL no banco)

### `/cotacoes/nova` — Criar
- **Arquivo:** `app/routers/cotacoes.py:180` · template `cotacao_nova.html`
- **Ação:** POST `/cotacoes`
- **Campos: cinco.** Cliente · Contato · Destino · Condição de pagamento · Tipo de frete.
  Eram dezoito.
- **Herdado, não digitado:** contato e cargo vêm de `Contato` (o principal do cliente, ou o
  escolhido); o responsável vem da oportunidade ou do usuário logado; prazo, validade, texto
  do frete, observações e termos ficam para a cotação, em *Mais detalhes*.
- **`estado_origem` saiu.** Era origem **logística** rotulada como "Origem da venda", com
  default `"São Paulo"` na rota contra `"Santa Catarina"` no modelo — o da rota vencia em
  toda criação **e em todo salvamento**. O motor fiscal nunca usou esse campo.

### `/cotacoes/{id}` — Detalhe
- **Arquivo:** `app/routers/cotacoes.py:249` · template `cotacao_detail.html`
- **Ordem dos blocos, por hierarquia de uso:** cabeçalho (cliente · contato · situação ·
  valor total) → **Produtos** → **Resumo comercial** → **Dados da venda** (cenário comercial
  e fiscal) → *Mais detalhes*
- **Botão do cabeçalho:** `Atualizar cenário e recalcular` — o rótulo antigo,
  "Salvar dados da cotação", não dizia que recalculava e ficava enterrado abaixo de dois
  textarea
- **Alterações pendentes:** mexer em destino, condição de pagamento, contribuinte ou tipo de
  frete (campos marcados `data-material`) exibe *"Há alterações ainda não aplicadas ao
  cálculo"* e torna inertes o Gerar PDF e as ações de saída
- **Premissa nova:** quando existe versão mais recente que a desta cotação, a tela avisa e
  oferece **Atualizar e recalcular** ou **Manter premissas desta cotação** — nunca muda sozinha
- **Origem, em *Mais detalhes*:** *Origem fiscal da NF* (com a fonte da conclusão) e
  *Origem logística do embarque* (uma linha por grupo, sem achatar múltiplas origens)
- **Ações POST:** `/atualizar` · `/itens` · `/calc` · `/status` · `/aceite` · `/duplicar` ·
  `/arquivar` · `/restaurar`
- **Ações GET:** `/pdf` · `/situacao` · `/compromisso` · `/itens/{item_id}/memoria`
- **Recálculo:** `atualizar_cabecalho` recalcula todos os itens quando muda condição de
  pagamento, destino, origem ou contribuinte (`cotacoes.py:286-313`)
- **Imutabilidade:** `ws.exigir_editavel(cotacao, ...)` recusa alteração após emissão

### `/cotacoes/{id}/negociacao` · `/negociacao/preview` · `/negociacao` (POST) — Fase 3A
Preview canônico da negociação (`app/routers/negociacao.py` → `comercial_service`). Corpo
JSON `{"itens": [{"item_id", "preco_negociado"}]}`. `preview` não grava; `POST /negociacao`
grava, recalcula a cotação inteira e invalida aprovação. Vendedora recebe
`payload_vendedora` (itens com recomendado/negociado/total/editável, subtotais, desconto,
frete, total, **comissão estimada em R$ e taxa efetiva**, `autonomia_status`,
`requer_aprovacao`); OWNER/ADMIN recebem também `economia` (piso, margem realizada, lucro,
comissão por item, máxima para o piso, quanto o desconto consumiu de comissão/margem/impostos).
Preço de item travado (Daune) diferente do recomendado: **409**.

### `/cotacoes/{id}/itens/{item_id}/memoria` — Memória do preço
- **Arquivo:** `app/routers/cotacoes.py:568`
- **Acesso:** **negado** (403) a quem não vê economia — não filtrado

### `/cotacoes/{id}/pdf`
- **Arquivo:** `app/routers/cotacoes.py:704`
- **Bloqueio:** devolve **página HTML** com o que resolver e o caminho de volta. Devolvia
  `{"erro": "PDF bloqueado", ...}` — um clique de usuário terminando em JSON cru

## 2.4 Aprovação

### `/aprovacoes` — Fila
- **Arquivo:** `app/routers/workflow.py:190` · template `aprovacoes_fila.html`

### `/aprovacoes/{pedido_id}` — Detalhe
- **Arquivo:** `app/routers/workflow.py:201` · template `aprovacao_detalhe.html`
- **Ações:** POST `.../aprovar`, `.../rejeitar`

## 2.5 Catálogo e simulação

### `/produtos`
- **Arquivo:** `app/routers/produtos.py:16` · template `produtos_list.html`
- **API:** GET `/produtos/buscar` (`produtos.py:52`) — payload filtrado por
  `CAMPOS_PRODUTO_COMERCIAL`

### `/calculadora`
- **Arquivo:** `app/routers/calculadora.py:17` · template `calculadora.html`
- **Acesso:** exige economia
- **Ações:** POST `/calculadora/calcular`, `/calculadora/salvar`

### `/importar`
- **Arquivo:** `app/routers/importar.py:21` · templates `importar.html`,
  `importar_preview.html`
- **Fluxo:** POST `/importar/preview` → POST `/importar/confirmar`

## 2.6 Administração

### `/admin` — Hub da administração
- **Arquivo:** `app/routers/admin.py` · template `admin_hub.html`
- **Acesso:** `exigir_admin`
- **13 áreas:** premissas e preços · catálogo e custos · motor industrial KTC · margens ·
  condições de pagamento · fiscal · estimativa de peso · importar planilha · calculadora ·
  usuários · auditoria · saúde do sistema · qualidade da base

### `/admin/premissas` — Premissas e versões
- **Arquivo:** `app/routers/admin.py` · template `admin_painel.html`
- **Acesso:** `exigir_admin`
- **Premissas editáveis** — lista **fechada** (`admin.py:40`):

| Chave | Rótulo | Unidade |
|---|---|---|
| `fx_usd_brl` | Câmbio USD → BRL | R$/US$ |
| `frete_int_usd_kg` | Frete internacional | US$/kg |
| `outras_desp_usd_un` | Outras despesas de importação | US$/un |
| `pis_cofins_nominal_pct` | PIS/COFINS **nominal** da venda — o efetivo é derivado por item | fração |
| `pis_cofins_pct` | LEGADO — efetivo fixo da metodologia anterior; não alimenta preço novo | fração |

- **Ações:** POST `/admin/premissa/preview` → `/admin/premissa/aplicar`;
  `/admin/margem/preview` → `/admin/margem/aplicar`
- **Barreira das ações:** `exigir_economia_gerenciavel`

### `/admin/sku/{produto_id}` — Custo de um SKU
- **Arquivo:** `app/routers/admin.py:94` · template `admin_sku.html`
- **Ações:** POST `.../custo/preview` → `.../custo/aplicar`

### `/admin/usuarios` — Usuários
- **Arquivo:** `app/routers/usuarios.py` · template `admin_usuarios.html`
- **Acesso:** `gerencia_usuarios` — OWNER sempre; ADMIN conforme `can_manage_users`
- **Ações:** criar · trocar papel · ligar/desligar cada permissão granular · ativar/desativar ·
  definir senha
- **Regras que a tela não afrouxa:** flag não promove vendedor; rebaixar papel limpa as flags
  incompatíveis; ninguém desativa o próprio acesso nem o último OWNER ativo; troca de senha e
  desativação incrementam `sessao_versao` e derrubam os cookies abertos
- **Não implementa:** SSO, OAuth, convite por e-mail, 2FA, recuperação automática

### `/admin/trilha` — Auditoria
- **Arquivo:** `app/routers/admin.py:277` · template `admin_trilha.html`
- **Filtro:** por entidade

### `/admin/cotacao/{id}/premissas`
- **Arquivo:** `app/routers/admin.py:290`
- **Devolve:** premissas desatualizadas do rascunho (detecção, sem alteração)

### `/configuracoes` — Sete abas
- **Arquivo:** `app/routers/configuracoes.py:32` · template `configuracoes.html`
- **Acesso:** `exigir_admin`
- **Abas** (`configuracoes.html:15`): `premissas` · `ktc` · `fiscal` · `margens` ·
  `pagamento` · `ncm` · `peso`
- **Ações POST:** `/configuracoes/premissa` · `/material` · `/parametro` · `/cmt` ·
  `/fiscal`
- **Confirmação obrigatória:** toda ação exige `confirmar == "sim"`; sem isso redireciona
  com `?erro=confirmacao` e **não grava**
- **Versionamento:** cada alteração fecha a linha vigente (`valid_to = hoje`, `ativo=False`)
  e insere uma nova, com `fonte="Painel de configurações"` e nota do valor substituído
- **Premissas críticas** (`PREMISSAS_CRITICAS`, `configuracoes.py:28`): `fx_usd_brl`,
  `frete_int_usd_kg`, `outras_desp_usd_un`, `pis_cofins_nominal_pct`, `pis_cofins_pct`,
  `icms_fallback_pct`. As duas últimas são **legadas** e a tela as marca como tal — não são
  taxas que se somam às vigentes

## 2.7 Relatórios e saúde

Relatórios é **um grupo com quatro abas** (`_abas_relatorios.html`): Comercial · Econômico ·
Cotações · Aprovações. Saúde não entra — é diagnóstico técnico e vive no Admin.

### `/relatorios` — Comercial
- **Arquivo:** `app/routers/relatorios_comerciais.py` · template `relatorios.html`
- **Acesso:** autenticado; o conteúdo econômico depende de `ve_economia`

### `/relatorios/cotacoes` — Documentos
- Documentos por situação, revisões e valor comercial. Cada revisão é uma linha.

### `/relatorios/aprovacoes` — Exceções
- Pendentes, aprovadas, rejeitadas, tempo médio até decidir, pedido mais antigo.

### `/relatorios/economico`
- **Arquivo:** `relatorios_comerciais.py:67` · template `relatorio_economico.html`
- **Acesso:** `exigir_economia` — **negado**, não filtrado

### `/saude` — Saúde operacional
- **Arquivo:** `relatorios_comerciais.py:85` · template `saude.html`
- **Acesso:** `exigir_economia`

### `/relatorios/qualidade` e `/relatorios/qualidade.json`
- **Arquivo:** `app/routers/relatorios.py:15,33` · template `relatorio_qualidade.html`

### CSVs
- `/relatorios/oportunidades.csv` · `/relatorios/atividades.csv` · `/relatorios/cotacoes.csv`
- Separador `;`, UTF-8, nome `anara-<tipo>-<AAAAMMDD-HHMM>.csv`
- Coluna econômica só entra para quem vê economia; o **número de linhas é o mesmo** para
  todos os papéis

## 2.8 Sistema

### `/health` — público
Devolve `{"status":"ok","database":"ok"}`. **Não** revela versão de migration, caminho de
arquivo nem contagem de dados.

### `/health/detalhe` — `exigir_economia`
- **Arquivo:** `relatorios_comerciais.py:224`
- Devolve status do banco, versão da migration e contagens de produtos, cotações,
  oportunidades e usuários

### `403.html`
Template de recusa. `401` vira redirect para o login; `403` continua 403
(`app/main.py:70-88`, `permissoes.resposta_de_negacao`).

## 2.9 Tratamento de erro

Um clique de tela **nunca** termina em JSON cru. O handler global (`app/main.py`) renderiza
`erro_acao.html` quando o `Accept` traz `text/html`; chamada por `fetch` continua recebendo
JSON. Vale para `DadoInvalido`, `TransicaoInvalida`, `OperacaoInvalida` e `AprovacaoVencida`,
vindas de qualquer rota.

Os códigos internos passam por `app/rotulos.py` antes de virar texto: `REVIEW_REQUIRED` vira
"Revisão necessária", `A_COTAR` vira "Preço sob consulta", `FRETE_ICMS_REVIEW_REQUIRED` vira
"Frete pendente de validação fiscal". O código continua no banco, no log e na trilha.

---

# 3. Fluxo completo do usuário

## 3.1 Caminho principal

```
CLIENTE            POST /clientes                        crm_service.criar_cliente
  ↓
CONTATO            POST /clientes/{id}/contatos          crm_service.criar_contato
  ↓
OPORTUNIDADE       POST /oportunidades                   crm_service.criar_oportunidade
  ↓
ATIVIDADE          POST /atividades                      crm_service.criar_atividade
  ↓
COTAÇÃO            POST /cotacoes  ou
                   POST /oportunidades/{id}/cotacao      já vinculada
  ↓
ITENS              POST /cotacoes/{id}/itens
  ↓
CÁLCULO            pricing_service.montar_regras + pricing_engine
  ↓
PREÇO RECOMENDADO  gravado em CotacaoItem.preco_recomendado
  ↓
NEGOCIAÇÃO         POST /cotacoes/{id}/calc              preco_negociado
  ↓
APROVAÇÃO          POST /cotacoes/{id}/aprovacao/solicitar   (só se houver exceção)
                   POST .../aprovar | .../rejeitar
  ↓
PDF RASCUNHO       GET /cotacoes/{id}/pdf                marcado "(RASCUNHO — não emitida)"
  ↓
EMISSÃO            POST /cotacoes/{id}/emitir            cria SnapshotEmissao
  ↓
ENVIO              POST /cotacoes/{id}/enviar
  ↓
GANHA / PERDIDA    POST /oportunidades/{id}/ganha | /perdida
  ↓
RELATÓRIOS         /relatorios · /relatorios/economico · /saude
```

## 3.2 Caminhos alternativos e retornos

| Situação | O que o código faz |
|---|---|
| Cotação sem oportunidade | Permitido. `Cotacao.oportunidade_id` é opcional |
| Vincular depois | POST `/oportunidades/{id}/vincular` |
| Cotação de outro cliente | **Recusado pelo servidor** — cross-client não entra |
| Voltar de `aguardando_aprovacao` | Permitido: `PENDING_APPROVAL → DRAFT` |
| Voltar de `aprovada` | Permitido: `APPROVED → DRAFT` e `APPROVED → PENDING_APPROVAL` |
| Alterar depois de emitir | **Recusado.** Cria-se revisão: POST `/cotacoes/{id}/revisao` |
| Reabrir negócio perdido | POST `/oportunidades/{id}/reabrir` — não apaga o evento de perda |
| Reabrir negócio ganho | **Não existe.** GANHA é terminal nesta versão |
| Duplicar cotação | POST `/cotacoes/{id}/duplicar` (`cotacoes.py:650`) |
| Arquivar | POST `/cotacoes/{id}/arquivar`, com restauração |

## 3.3 O que é editável e o que congela

| Estado | Editável? |
|---|---|
| `rascunho` | sim |
| `aguardando_aprovacao` | sim — e qualquer alteração material invalida o pedido |
| `aprovada` | sim — e a alteração invalida a aprovação |
| `emitida` | **não** — `ws.exigir_editavel` recusa |
| `enviada` | **não** |
| `cancelada` | **não** |

Ações que criam revisão: **apenas** POST `/cotacoes/{id}/revisao`, a partir de uma cotação
emitida ou enviada.

---

# 4. Modelo de dados

33 tabelas em `app/models.py`. Abaixo as relevantes ao fluxo.

## 4.1 CRM

### `Cliente` (`models.py:251`)
Organização **e** prospect — não existe entidade separada de lead.
- Campos: `nome`, `cnpj`, `cidade_uf`, `finalidade`, `contribuinte_icms`, dados fiscais,
  `arquivado_em`
- `crm_service.dados_fiscais_faltando()` lista o que impede emitir; **não** impede criar

### `Contato` (`models.py:1223`)
`cliente_id`, `nome`, `cargo`, `email`, `telefone`, `principal`, `ativo`

### `Oportunidade` = **Venda** na interface (`models.py`)
- `cliente_id`, `titulo`, `etapa` (**RASCUNHO / ENVIADO / NEGOCIACAO** — Fase 3B; os
  valores do funil anterior são `ETAPAS_LEGADAS`, só leitura), `status` (ABERTA / GANHA =
  Vendido / PERDIDA = Perdido), `responsavel_id`, `origem`
- `valor_estimado` — **palpite manual**, persistido
- `valor_fechado` — snapshot do ganho (**valor vendido**), persistido
- `cotacao_vencedora_id` — preenchido em GANHA
- `data_prevista_fechamento`, `motivo_perda` (+ `PRODUTO_ESPECIFICACAO`), `criado_em`
- **Pós-venda (0019):** `status_pos_venda` (AGUARDANDO_ENTREGA → AGUARDANDO_PAGAMENTO →
  PAGO; ATRASADO marcado pelo financeiro; nulo em aberta/perdida), `entrega_prevista_em`,
  `entregue_em`, `faturado_em` + `numero_documento_fiscal` (**valor faturado**),
  `pagamento_previsto_em`, `pago_em` (**valor pago**), `observacao_pos_venda`. V1 sem
  pagamento parcial
- **Derivado, sem coluna:** valor cotado, aging, tempo em etapa, tempo até fechamento,
  status comercial (`rotulos.venda`)

### `AtualizacaoComercial` (`models.py`, Fase 3B)
Append-only: `oportunidade_id`, `autor_id`, `autor_email`, `texto`, `criado_em`. Não se
edita nem apaga — correção é outra atualização. Pode criar uma `AtividadeComercial` junto.

### `OportunidadeEtapaHistorico` (`models.py:1288`)
Append-only. `oportunidade_id`, `de`, `para`, `ator_id`, `em`, `nota`

### `AtividadeComercial` (`models.py:1305`)
`tipo`, `titulo`, `responsavel_id`, `oportunidade_id`, `due_em`, `concluida_em`
- **"Atrasada" é derivado** (`crm_service.esta_atrasada`): `due_em < agora` e não concluída

## 4.2 Cotação

### `Cotacao` (`models.py:873`)
- Identidade: `numero` (`ANARA-<ano>-<0000>`), `revisao`, `cotacao_origem_id` (genealogia)
- Cenário: `estado_origem` (default `"Santa Catarina"`), `estado_destino`,
  `uf_origem_fiscal`, `contribuinte_icms`, `finalidade`, `condicao_pagamento`
- Frete: `freight_type`, `freight_valor`, `frete` (texto)
- Workflow: `status`, `fingerprint`, `issued_em`, `sent_em`
- Comercial: `vendedor`, `contato_nome`, `departamento_contato`, `prazo_entrega`,
  `validade_dias`, `validade_em`, `observacoes`, `termos_texto`
- Vínculo: `oportunidade_id`, `cliente_id`
- Arquivo: `arquivada_em`

> **`estado_origem` ≠ `uf_origem_fiscal`.** O primeiro é origem logística/comercial; o
> segundo é a origem fiscal da operação. `pricing_service.uf_origem_fiscal()` **não** usa
> `estado_origem` — origem logística não prova origem fiscal da NF.

### `CotacaoItem` (`models.py:949`)
- Comercial: `nome_produto`, `especificacao`, `quantidade`, `preco_base`,
  `preco_recomendado`, `preco_negociado`, `faturamento`, `modo_edicao`, `valor_editado`
- Econômico: `custo_unitario` (**NOT NULL**), `margem_padrao_pct`, `margem_liquida`,
  `comissao_pct` (a comissão **aplicada** — desde 16/09/2026 é a da cotação, não a da faixa),
  `lucro`, `impostos`, `markup_implicito`
- **Política comercial congelada** (Fase 3A): `piso_margem_pct`, `comissao_formacao_pct`,
  `preco_travado`, `politica_comercial`. NULO = item anterior a 16/09/2026, avaliado com a
  semântica anterior e apontado por `premissas_desatualizadas`
- Fiscal: `icms_pct`, `difal_pct`, `encargo_pct`, `status_fiscal`, `motivo_fiscal`,
  `uf_destino_fiscal`, `finalidade`
- Status: `status_custo_item`, `confirmation_pending`, `status_pagamento`,
  `motivo_pagamento`
- **Pinning de versão** (Sessão 5): `custo_referencia_id`, `custo_referencia_versao`,
  `margem_regra_id`, `condicao_pagamento_id`, `aliquota_interestadual_id`,
  `premissas_pinadas`
- Memória: `memoria_json`

> Guardar o **valor** protege o dinheiro; guardar o **id** protege a genealogia. Sem o id,
> uma versão cadastrada depois com vigência retroativa mudaria a resposta de "qual versão
> formou este preço".

### `AprovacaoCotacao` (`models.py:1155`)
`cotacao_id`, `fingerprint`, `status`, `solicitante_id`, `decisor_id`, `justificativa`,
`comentario`, `motivos_json`, `solicitado_em`, `decidido_em`, `invalidado_em`.
Append-only — decisões não são editadas.

### `SnapshotEmissao` (`models.py:1191`)
Congela o documento emitido e **pina `aprovacao_id` + fingerprint** — não um booleano
"aprovado = sim".

## 4.3 Custo e premissas

### `Produto` (`models.py:752`)
`sku_key`, `nome`, `familia`, `categoria`, `fornecedor_id`, `custo_unitario`, `preco_base`,
`status_custo`, `custo_confianca`, `precisa_revisao`, `revisao_motivo`, `origem_fiscal`,
`ncm`, `thread_count`, `gsm`, `peso_kg`, medidas, `ativo`

### `CustoReferencia` (`models.py:831`)
Versionado. `produto_id`, `versao`, `valor`, `valor_bruto`, `cnet_brl`, `moeda`, `tipo`,
`documento`, `data_ref`, `confianca`, `status_custo`, `metodo_custo`, `memoria_calculo`,
`origem_registro`, `substitui_versao`, `valid_from`, `valid_to`, `vigente`

### `Premissa` (`models.py:289`)
`chave`, `valor_num`, `valor_txt`, `unidade`, `descricao`, `valid_from`, `valid_to`,
`ativo`, `fonte`, `fonte_data`

### `MargemRegra` (`models.py:639`)
Resolvida **por prioridade e por data** (`ref` default = hoje — C-NEW-13), nunca por `if`
espalhado. Escopo: fornecedor, família ou SKU. Desde 16/09/2026 carrega a política do escopo:
`piso_pct`, `comissao_formacao_pct`, `preco_travado`, `margem_anterior_pct`, `politica`,
`fonte`. As 21 regras anteriores estão encerradas (`valid_to = 2026-09-16`) e têm uma
sucessora cada.

### `CondicaoPagamento` (`models.py:656`)
`codigo`, `label`, `encargo_pct`, `confirmado`, vigência.
Condição não cadastrada **não é interpolada** — exige cadastro ou override autorizado.

### `AuditLog` (`models.py:1127`)
Ator, papel, ação, escopo, antes, depois, motivo, origem, resultado, correlação do lote.
**Nunca** senha, hash, cookie ou segredo.

### `Usuario` (`models.py:1047`)
`email` (único, indexado), `nome`, `senha_hash`, `papel`, `ativo`, `sessao_versao`,
`can_manage_users`, `can_manage_economics` (default **True**), `can_approve_quotes`
(default **False**), `criado_em`, `ultimo_login_em`, `criado_por`

## 4.4 Fiscal e frete

`EstadoFiscal` · `RegraFiscalVenda` · `AliquotaInterestadual` · `RegraFcp` · `NcmRegra` ·
`Transportadora` · `TabelaFrete` · `FaixaFrete` · `CoberturaFrete` · `ComponenteFrete` ·
`GrupoLogistico`

## 4.5 Motor KTC

`MaterialPreco` (`price_usd_m2`) · `CmtPreco` (`cmt_usd`) · `ToalhaPreco` ·
`ParametroKTC` (`chave`, `escopo`, `valor`) — todos versionados por `valid_from`/`valid_to`

## 4.6 Legado preservado

`BaseImportacao` · `BasePremissaPonte` — bases de importação históricas, intocadas.

---

# 5. Cotação / motor econômico

## 5.1 Waterfall implementado

```
ESPECIFICAÇÃO TÉCNICA
  ↓  ktc_engine (só KTC)
hemming → shrinkage → consumo → waste → tecido → CMT → 2ª qualidade → margem KTC
  ↓
EXW US$
  ↓  nationalization (só KTC)
frete internacional → base do I.I. → I.I. → outras despesas → × câmbio
  ↓
CUSTO NET (R$)        ← resolvido AGORA por ps.custo_para_precificar(), não lido de coluna
                        (fornecedor nacional entra aqui, via custo_service.cnet_nacional)
  ↓  pricing_engine
markup E → gross-up por (ICMS + PIS/COFINS + encargo financeiro + comissão + RV logístico)
  ↓
PREÇO RECOMENDADO     ← comissão de FORMAÇÃO da política (10% não-Daune, 5% Daune)
  ↓  negociação (Fase 3A: comercial_service)
PREÇO NEGOCIADO → comissão da COTAÇÃO (variável, presa pelo piso) → impostos → frete
                → LUCRO (resíduo) → MARGEM REALIZADA → piso? autonomia : exceção
```

## 5.2 Fórmulas reais

**Motor industrial KTC** (`app/ktc_engine.py`):

```
consumo_com_waste  = consumo / (1 − waste)                 NÃO × (1 + waste)
custo_com_2a_qual  = custo / (1 − quality_allowance)       o "II 1%" da planilha KTC
EXW                = custo / (1 − ktc_margin)              NÃO × (1 + margem)
```

**Nacionalização** (`app/nationalization.py`):

```
frete_unitario = peso_kg × frete_usd_kg
base_ii        = EXW + frete
ii             = base_ii × aliquota
NET_USD        = EXW + frete + ii + outras_desp_usd_un
NET_BRL        = NET_USD × fx_usd_brl
```

**Custo nacional** (`app/custo_service.py:68`):

```
CNET = gross − gross×12% − (gross − gross×12%)×9,25%
```
Constantes em `custo_service.py:43-44`: `DAUNE_ICMS_CREDITO = 0.12`,
`DAUNE_PIS_COFINS_CREDITO = 0.0925`. Fator efetivo 0,7986 — **conferência, não fórmula**.

**Formação do preço** (`app/pricing_engine.py`):

```
F      = custo × (1 + E)
preco  = F / (1 − icms% − pis_cofins% − encargo% − rv_logistico% − comissao%(E))
lucro  = faturamento − impostos − comissao − frete_cf − frete_rv − custo_total
margem = lucro / faturamento
```

`custo_efetivo = custo + regras.frete_cf_unitario` — o CF do frete entra no **numerador**;
o RV entra no **denominador** (`TaxRuleSet.rates_variaveis()`).

**Política comercial de 16/09/2026** (`app/politica_comercial.py`, puro; `app/comercial_service.py`,
com banco; `pricing_engine.com_comissao_fixa` / `comissao_maxima_para_margem`):

```
comissão de formação   faixa única [(0, c)] no TaxRuleSet → o gross-up não muda
desconto ponderado     max(0, 1 − Σneg/Σrec)            bloco variável = não-Daune com custo e recomendado
proporcional           max(5%, 10% × (1 − desconto))
c_max_i                lucro_sem_comissão_i / receita_i − piso_i     (calcular_por_preco com comissão 0)
comissão variável      max(5%, min(proporcional, min_i c_max_i))
item                   calcular_por_preco(custo, qtd, negociado, com_comissao_fixa(regras, variável))
exceção                déficit(preço, piso, margem_realizada) > tolerância_de_arredondamento → MARGEM_ABAIXO_PISO
```

Daune: preço travado (409 ao tentar outro unitário, para qualquer papel) e comissão fixa 5%.
Item anterior à política: comissão por faixa, autonomia zero — não é migrado sem ato explícito.

## 5.3 Os conceitos, separados

| Conceito | O que é | Onde |
|---|---|---|
| **MARKUP (E)** | Alavanca interna sobre o custo. Determina a faixa de comissão | `markup_implicito` |
| **MARGEM** | Lucro ÷ faturamento | `margem_liquida` |
| **Preço recomendado** | O que o motor forma **para o cenário desta cotação** | `preco_recomendado` |
| **Preço base** | Referência do catálogo, formada em outro contexto fiscal | `preco_base` |
| **Preço negociado** | O que se cobra | `preco_negociado` |
| **Receita real** | `preço negociado × quantidade`, quantizado | `faturamento` |
| **Lucro** | **Resíduo** da receita menos os componentes já quantizados | `lucro` |
| **Margem alvo** | A pedida | `margem_alvo` / `margem_padrao_pct` |
| **Margem líquida** | A realizada — com a comissão da cotação | `margem_liquida` |
| **Piso de autonomia** | Abaixo dele a vendedora perde a autonomia (Fase 3A) | `piso_margem_pct` |
| **Comissão de formação** | A que forma o recomendado (10% / 5% Daune) | `comissao_formacao_pct` |
| **Comissão aplicada** | A da cotação, presa pelo piso, ≥ 5% | `comissao_pct` / `comissao_valor` |
| **Comissão estimada** | Σ `comissao_valor` e taxa efetiva — estimativa de pricing, não pagável | `resumo_comercial` |

> `preco_recomendado ≠ preco_base`. Medir desconto contra o base faria toda venda
> interestadual parecer exceção (`workflow.py:207-211`).

> `margem_alvo ≠ margem_liquida`. Pedir 14% e cobrar R$ 377,12 entrega 13,9998%, e é isso
> que a memória registra.

## 5.4 De onde vem o custo de um item novo

`ps.custo_para_precificar(session, produto)` devolve `(cnet, memoria)`, e é ele que os quatro
caminhos de precificação usam — `adicionar_item`, `/calc`, `duplicar` e a calculadora.

`Produto.custo_unitario` **não é fonte autoritativa** de precificação nova: é coluna
persistida, gravada quando o custo foi calculado pela última vez, e não acompanha uma troca
de premissa versionada. Ela continua servindo de referência de catálogo.

Toda resolução declara a fonte, e o status canônico é derivado dela:

| `net_fonte` | Quando | `status_custo_item` |
|---|---|---|
| `DERIVADO_DAS_PREMISSAS_VIGENTES` | KTC com EXW, nacionalizado com o câmbio vigente | `CONFIRMADO` |
| `CUSTO_CADASTRADO_DO_FORNECEDOR` | fornecedor nacional, custo já em reais | `CONFIRMADO` |
| `CATALOGO_SEM_EXW` | KTC sem EXW conhecido — número sem evidência | `REVIEW_REQUIRED` |
| — | sem valor nenhum | `A_COTAR` |

`ps.status_canonico_do_custo()` faz essa tradução. `CostConfidence` (`CALCULATED`, `QUOTED`,
`MANUAL`, `LEGACY`) descreve o **método** e continua em `cost_method`, `custo_confianca` e na
memória — não compete mais com o status operacional lido pelos portões do workflow.

## 5.5 Decimal e arredondamento

Política única em `app/dinheiro.py`:

- **Nunca `Decimal(float)`.** `D()` converte pela representação textual — `D(0.18)` é
  `Decimal("0.18")`
- **Precisão interna de 34 dígitos** (`PRECISAO_INTERNA`); nada é quantizado no meio da cadeia
- **Dinheiro comercial: 2 casas, `ROUND_HALF_UP`** (`dinheiro()`). `round()` do Python faz
  banker's rounding sobre binário e não serve
- **A quantização acontece UMA vez**, e todos os componentes são recompostos sobre o preço
  arredondado
- **Rateio pelo maior resto** (`ratear_centavos`), com desempate pela ordem canônica
- **Custo NET não é quantizado** — é custo interno
- `ResultadoPrecificacao.reconcilia()` prova que
  `faturamento == custo_total + impostos + comissao + frete_cf + frete_rv + lucro`

**Persistência:** as colunas continuam `REAL`. `Numeric(18,2)` no SQLite vira afinidade REAL
e **trunca na leitura**. A ponte é `D()` na entrada e `para_float()` na saída.

---

# 6. Fornecedores e produtos

Três fornecedores cadastrados (`tabela fornecedor`):

| id | código | nome | tipo | moeda | método padrão | origem logística |
|---|---|---|---|---|---|---|
| 1 | `KTC` | Kazareen Textile Company | `IMPORTADO_KTC` | USD | `ktc_quoted` | Itajaí — SC |
| 2 | `DAUNE` | Daune | `NACIONAL` | BRL | `national_supplier` | *(vazio)* |
| 3 | `DECOR_TRICOT` | Decor Tricot | `NACIONAL` | BRL | `national_supplier` | *(vazio)* |

## 6.1 KTC

**Famílias com motor industrial** (`pricing_service.py:41-47`):

| Grupo | Famílias | Função |
|---|---|---|
| Tecido plano | flat sheet, top sheet, bottom sheet | `calcular_flat_sheet`, `calcular_bottom_sheet` |
| Duvet cover | duvet cover | `calcular_duvet_cover` |
| Fronha | pillow case, pillowcase, fronha | `calcular_fronha` (geometria §18) |
| Terry | bath/hand/face/pool/beach towel, bath mat, wash cloth | `calcular_toalha` (custo por peso) |

**Fora do motor industrial, por decisão explícita** (comentário em `pricing_service.py:43`):
lençol com elástico, **roupão** e **chinelo** — sem geometria confirmada. Esses vão por
`KTC_SPECIAL_QUOTED` ou pelo último preço KTC válido.

**Parâmetros vigentes** (`ParametroKTC`):

| chave | escopo | valor | nota |
|---|---|---|---|
| `waste` | — | 0,03 | entra como `consumo / (1 − waste)` |
| `shrinkage` | CVC | 0,03 | poly/cotton |
| `shrinkage` | COTTON | 0,05 | 100% algodão |
| `quality_allowance` | — | 0,01 | perda de 2ª qualidade — **não é I.I.** |
| `ktc_margin` | — | 0,15 | `EXW = custo / (1 − 0,15)` |
| `hem_width_total_cm` / `hem_length_total_cm` | Flat Sheet, Duvet Cover, Top Sheet | 4,0 | 2 cm de cada lado |
| `paineis` | Duvet Cover | 2,0 | open bag |
| `paineis` | Flat Sheet, Top Sheet | 1,0 | painel único |
| `gsm_por_tc` | 120…300 | 105…135 | só quando não há GSM nem peso real |

**Materiais** (`MaterialPreco.price_usd_m2`): 230TC Percale Cotton 1,20 · 200TC Percale
Polycotton 1,10 · 250TC Sateen CVC plain 1,25 · CVC stripe 1,30 · 250TC Sateen Cotton 1,30.
Nota da fonte: *"These prices can vary from time to time"*.

**CMT** (`CmtPreco.cmt_usd`): Flat Sheet 0,75 · Fitted Sheet 1,00 · Duvet Cover 1,50 ·
Pillow Case standard 0,50 · oxford 0,75.

**Nacionalização:** `frete_int_usd_kg = 0,516`, `outras_desp_usd_un = 0,2487532709`,
`fx_usd_brl = 5,11`. Referências: `frete_int_referencia_kg = 1250`,
`frete_int_referencia_usd = 645`.

**Toalhas:** `ToalhaPreco` guarda taxa por kg. A taxa por kg **já é EXW final** — não se
aplica CMT, 2ª qualidade nem margem por cima.

## 6.2 Daune

Caminho `DAUNE_DIRECT`: preço bruto → créditos de compra → CNET (seção 5.2).
38 referências vigentes `CONFIRMADO` com `valor_bruto`, `cnet_brl` e `memoria_calculo`
gravados. 15 SKUs ativos **sem** `custo_unitario`.

## 6.3 Decor Tricot

Caminho `DECOR_DIRECT`, mesma mecânica. 17 SKUs ativos, 1 sem custo.

## 6.4 Estado do catálogo

| Métrica | Valor |
|---|---|
| SKUs ativos | 340 |
| Sem `custo_unitario` | 45 — KTC 29, Daune 15, Decor 1 |
| Referências versionadas | 161 |
| Vigentes `CONFIRMADO` | 38 |
| Vigentes `A_COTAR` | 9 |

Famílias dos 45 sem custo: Duvet Insert 14 · Fitted Sheet 6 · Pool Towel 6 · Bathrobe 6 ·
Duvet Cover 4 · Pillow Case 4 · Flat Sheet 3 · Mattress Protector 1.

---

# 7. Fiscal

Motor em `app/fiscal_rules.py` (413 linhas), puro.

## 7.1 Entradas resolvidas por precedência

| Variável | Precedência | Função |
|---|---|---|
| **Origem fiscal da mercadoria** | override do SKU → tipo do fornecedor | `origem_fiscal_do_produto` |
| **UF de origem fiscal** | cotação → fornecedor do item → `fiscal_uf_origem_padrao` | `uf_origem_fiscal` |
| **UF de destino** | `cotacao.estado_destino`, normalizada | `normalizar_uf` |
| **Finalidade** | cotação → cliente → `fiscal_finalidade_padrao` | `finalidade_da_operacao` |
| **Contribuinte** | `cotacao.contribuinte_icms` | — |

`normalizar_uf` aceita `"SP"`, `"sp"` e `"São Paulo"` e devolve sempre a sigla;
desconhecido → `None`.

> Fornecedor de tipo `OUTRO` ou ausente **não** vira default: devolve `None` e o fiscal
> bloqueia.

## 7.2 Resolução

```
uf_destino ausente  → BLOQUEIO "UF de destino não informada ou desconhecida…"
uf_destino fora da tabela → BLOQUEIO "UF de destino {X} não está na tabela…"
uf_origem == uf_destino   → intraestadual, alíquota interna do estado
uf_origem != uf_destino   → resolver_aliquota_interestadual + DIFAL + FCP
```

**Interestadual** (`resolver_aliquota_interestadual`): busca linha cadastrada em
`AliquotaInterestadual` por origem × destino × natureza. Sem linha → bloqueio com mensagem
que nomeia o par. As alíquotas semeadas são 4% (importada), 7% e 12% (nacional, conforme a
UF de destino) — `app/seeds.py:407-416`.

**DIFAL:** a carga final entra **como está**, do cadastro de `EstadoFiscal.carga_final`.
O código **não** recalcula por base simples, base dupla ou FEM.
Responsabilidade (Q-08): contribuinte → destinatário recolhe (não desconta da margem
Anara); não contribuinte → remetente recolhe (entra no waterfall).

**FCP** (`resolver_fcp` + `RegraFcp`): **exige linha cadastrada** dizendo a que se aplica —
não é lido de uma coluna por estado, porque a incidência depende do produto. Sem linha,
FCP é zero e a memória registra que nenhuma regra foi encontrada; isso **não** bloqueia.
Vira bloqueio só com `exige_confirmacao`. Situações: `APLICA`, `NAO_APLICA`,
`DESCONHECIDO`.

> `EstadoFiscal.fem` existe para rastreabilidade da tabela histórica e, nas palavras do
> próprio código (`models.py:616`), **não alimenta o motor**.

**PIS/COFINS:** premissa versionada `pis_cofins_nominal_pct = 0,0925`, e o que entra no
denominador é o **efetivo**, calculado por item em `pricing_engine.pis_cofins_efetivo()`:

```
icms_excluido = ResultadoFiscal.icms_pct − ResultadoFiscal.fcp_pct     (nunca negativo)
efetivo       = nominal × (1 − icms_excluido)
```

O ICMS é excluído da base de PIS/COFINS, então o efetivo depende da alíquota da operação —
7,585% em ICMS 18%, 8,88% em 4%. Como o ICMS é resolvido por item desde a Onda 1, o
PIS/COFINS também é: uma cotação com KTC e Daune para o mesmo destino carrega duas alíquotas
efetivas diferentes.

**Duas grandezas, e confundi-las foi o excesso corrigido logo depois de `549d407`:**

| Grandeza | O que é | Onde entra |
|---|---|---|
| `ResultadoFiscal.icms_pct` | carga **total** de ICMS + FCP que reduz a receita | denominador do gross-up, inteira |
| `icms_excluido_da_base()` | a parcela que reduz a **base de PIS/COFINS** | só no cálculo do efetivo |

Na venda a não contribuinte o **DIFAL** suportado pela Anara está dentro do `icms_pct` e
**participa da exclusão**. O **FCP não**: o que a contabilidade validou foi a exclusão do
ICMS, e o tratamento do FCP tem discussão própria. Enquanto não houver validação específica,
a política é conservadora — o FCP continua **cobrado integralmente no gross-up** e **mantido
na base** de PIS/COFINS. SP→RJ não contribuinte: carga 22%, excluído 20%, efetivo **7,40%**.

`EstadoFiscal.carga_final` não participa de nenhuma das duas.

A premissa antiga `pis_cofins_pct = 0,0759` continua no banco, vigente e consultável, para
interpretar cotação formada antes de 09/09/2026 — **nenhum caminho de precificação a lê**.
Fonte da correção: Brendo Simão, contabilidade da Indústria Química Anastacio, 09/09/2026,
planilha "Fator Cálculo Exclusão ICMS .xlsx".

**Créditos de entrada:** só no caminho nacional (seção 5.2). Crédito da **compra** — sem
relação com o ICMS da **venda**.

**`icms_fallback_pct = 0,18`** existe como premissa e está em `PREMISSAS_CRITICAS`.
Se e quando esse fallback é efetivamente aplicado na formação de preço:
**NÃO CONFIRMADO NO CÓDIGO** nesta leitura.

## 7.3 Como entra no preço

`pricing_service.montar_regras()` devolve um `TaxRuleSet` com `icms_pct`,
`pis_cofins_pct`, `encargo_financeiro_pct`, `comissao_tabela`, `frete_cf_unitario` e
`frete_rv_pct`. Tudo isso vai para o **denominador** do gross-up, exceto o CF.

O `pis_cofins_pct` que o `TaxRuleSet` recebe é sempre o **efetivo** — a semântica do campo
não mudou em 09/09/2026, só a forma de chegar nele: antes era a premissa lida direto, agora é
`pis_cofins_efetivo(nominal, fiscal.icms_pct)`. O motor de precificação não foi tocado.

---

# 8. Frete

## 8.1 CIF × FOB

`TipoFrete`: `CIF`, `FOB`, `A_COMBINAR`, `OUTRO`.

O frete só bloqueia quando é **CIF** — quando é responsabilidade da Anara
(`workflow.py:174`). FOB não forma frete e não bloqueia. `A_COMBINAR` é termo comercial
deliberado e **não** bloqueia.

## 8.2 CF e RV

`app/frete_engine.py` devolve **dois** números, e essa é a razão de o módulo existir:

```
CF  custo fixo do embarque, em R$   → NUMERADOR do pricing
RV  rate variável sobre a NF, em %  → DENOMINADOR do pricing
```

Tipos de componente: `FIXO`, `POR_PESO`, `PERCENTUAL_NF`, `PERCENTUAL_FRETE`, `POR_HORA`.

## 8.3 Tabela TRANSAL cadastrada

Uma única tabela (`TabelaFrete` id 1):

| Campo | Valor |
|---|---|
| Transportadora | TRANSAL |
| Origem logística | **Itajaí — SC** |
| Documento | `Tabela TRANSAL - frete nacional 2026-02.xlsx` |
| Vigência | 2026-02-01 → **2026-12-31** |
| Tarifa | `BRL_POR_TONELADA` |
| Faixa | `KG` |
| Mínimo | `BRL_POR_EMBARQUE` |
| Fator de cubagem | 300 kg/m³ |
| `pedagio_base` | **`DESCONHECIDO`** |
| `icms_situacao` | **`DESCONHECIDO`** |

Cobertura: **9 regiões, 238 cidades**. Faixas: 10 regiões.

**Onze componentes cadastrados** (`ComponenteFrete`):

| Código | Situação | | Código | Situação |
|---|---|---|---|---|
| `ADV` | `APLICA` | | `REENTREGA` | `APLICA` |
| `GRIS` | **`DESCONHECIDO`** | | `DEVOLUCAO` | `APLICA` |
| `PEDAGIO` | `APLICA` | | `AGENDAMENTO_TRUCK` | `APLICA` |
| `FIEL_DEPOSITARIO` | **`DESCONHECIDO`** | | `FIM_DE_SEMANA` | `APLICA` |
| `PALETIZACAO` | `APLICA` | | | |
| `TDE` / `TDC` | `APLICA` | | | |

## 8.4 Peso taxado e cubagem

`app/peso.py` — hierarquia de resolução, sem assumir que o peso real vence:

| Prioridade | Fonte | Código |
|---|---|---|
| 1 | Volume no SKU ou referência logística | `SKU_PACKING` |
| 2 | Volume total informado para o grupo/embarque | `SHIPMENT_VOLUME` |
| 3 | Peso taxado confirmado por Admin | `CARRIER_CONFIRMED_TAXABLE_WEIGHT` |

Com `pedagio_base = DESCONHECIDO`, o cálculo **segue** quando peso real e peso taxado
coincidem — a ambiguidade não muda o número — e **bloqueia** quando divergem.

## 8.5 Rateio e grupos

`GrupoLogistico` agrupa itens de um embarque. O CF é rateado ao item por
`dinheiro.ratear_centavos()` — maior resto, desempate pela ordem canônica.

## 8.6 Status de frete

| Status | Significado | Bloqueia emissão CIF? |
|---|---|---|
| `OK` | resolvido | não |
| `FRETE_ESTIMADO` | estimado | não |
| `FRETE_A_COTAR` | sem base | **sim** |
| `FRETE_REVIEW_REQUIRED` | premissa quebrada | **sim** |
| `FRETE_ICMS_REVIEW_REQUIRED` | ICMS da prestação irresolvido | **sim** |

`STATUS_BLOQUEIA_EMISSAO = {A_COTAR, REVIEW_REQUIRED, ICMS_REVIEW_REQUIRED}`
(`frete_engine.py:44`).

## 8.7 Pendente

- ICMS da prestação: incluso ou gross-up de `÷(1−12%)` — `icms_situacao = DESCONHECIDO`
- GRIS 0,10%: aplicabilidade — `DESCONHECIDO`
- Fiel depositário 0,5% da NF: aplicabilidade — `DESCONHECIDO`
- Base do pedágio: peso real ou taxado — `pedagio_base = DESCONHECIDO`
- Origem logística de Daune e Decor: campos **vazios** em `fornecedor`
- Região Passo Fundo-RS: sem tarifa, mínimo ou prazo
- **Não existe tabela de frete com origem São Paulo** — logo, CIF de Daune e Decor não tem
  tarifa para consultar

---

# 9. Status e bloqueios

## 9.1 Estados de confiança do custo

| Estado | De onde vem | Cota? | PDF? | Compromisso firme / WON? |
|---|---|---|---|---|
| `CONFIRMADO` | referência direta, atual, confiável | sim | sim | sim |
| `ESTIMADO` | proxy: curva, análogo, interpolação documentada | sim | sim | **não** enquanto `confirmation_pending` |
| `REVALIDAR` | referência **direta** que envelheceu ou tem anomalia | sim, com alerta | sim | **não** antes de reconfirmar |
| `A_COTAR` | não existe base segura | **não** | **não** | não |
| `REVIEW_REQUIRED` | premissa, fiscal, rastreabilidade ou dado quebrado | **não** | **não** | não |

`STATUS_QUE_PRECIFICAM` em `app/models.py`; `CUSTO_BLOQUEIA = {"A_COTAR",
"REVIEW_REQUIRED"}` em `workflow.py:96`.

Três distinções que o código mantém separadas:
`REVALIDAR ≠ ESTIMADO` (direto × proxy) · `REVALIDAR ≠ A_COTAR` (tem número × não tem) ·
`REVALIDAR ≠ REVIEW_REQUIRED` (envelhecer não é erro de cálculo).

**ESTIMADO nunca é promovido a CONFIRMADO em silêncio.**

> **Legado.** Os SKUs marcados `REVIEW_REQUIRED` e `precisa_revisao` na base histórica
> carregam o sentido **antigo** do campo. Reclassificar exige gate de reconciliação; não há
> conversão automática no código.

## 9.2 Estados do workflow da cotação

| Constante | Valor no banco |
|---|---|
| `DRAFT` | `rascunho` |
| `PENDING_APPROVAL` | `aguardando_aprovacao` |
| `APPROVED` | `aprovada` |
| `ISSUED` | `emitida` |
| `SENT` | `enviada` |
| `CANCELLED` | `cancelada` |

**Transições permitidas** (`workflow.py:60-67`):

```
rascunho              → aguardando_aprovacao, emitida, cancelada
aguardando_aprovacao  → rascunho, aprovada, cancelada
aprovada              → rascunho, emitida, aguardando_aprovacao, cancelada
emitida               → enviada, cancelada
enviada               → cancelada
cancelada             → (nenhuma)
```

`ESTADOS_IMUTAVEIS = (emitida, enviada, cancelada)`.

**Estados legados**, preservados e fora do workflow: `fechada`, `pedido`, `perdida`.
`exigir_transicao` recusa mover uma cotação a partir de estado legado — e, como nenhum deles
é destino de transição nenhuma, também são **inalcançáveis**: a interface moderna não cria
estado legado por caminho nenhum.

`wf.proximos_estados(atual)` é o que a tela oferece como botão, com o verbo da ação
("Solicitar aprovação", "Emitir", "Cancelar cotação"). Em estado legado devolve vazio, e a
tela explica que a cotação veio do sistema anterior.

## 9.3 O que cada coisa permite

| Pergunta | Resposta do código |
|---|---|
| Permite montar proposta? | qualquer status exceto os imutáveis |
| Permite PDF? | ausência de blocker duro; rascunho sai **marcado** |
| Bloqueia emissão? | `blockers_da_cotacao()` não vazia |
| Bloqueia compromisso firme? | `validar_compromisso_firme()` |
| Bloqueia WON? | a mesma `validar_compromisso_firme()`, chamada por `crm_service.marcar_ganha` |

**Blockers duros** (`workflow.blockers_do_item` / `blockers_da_cotacao`):
`FISCAL_REVIEW_REQUIRED` · `PAGAMENTO_REVIEW_REQUIRED` · `CUSTO_A_COTAR` ·
`CUSTO_REVIEW_REQUIRED` · `SEM_PRECO` · `SEM_ITENS` · os três de frete CIF · `FRETE_GRUPO`.

## 9.4 Blocker técnico × exceção aprovável × estado emitível

Três conceitos que se parecem num relatório e não se parecem em nada na decisão. A distinção
é o que impede uma aprovação humana de liberar o que tecnicamente não pode ser emitido.

| | **Blocker técnico** | **Exceção aprovável** |
|---|---|---|
| O que é | falta informação para formar o documento | há informação, e ela foge da política |
| Exemplos | `SEM_PRECO`, `CUSTO_A_COTAR`, fiscal irresolvido, frete CIF sem tarifa | preço abaixo do recomendado, margem abaixo da alvo, premissa antiga mantida |
| Quem resolve | quem tem o dado | quem tem alçada (`can_approve_quotes`) |
| Aprovação dispensa? | **não** — nenhuma alçada cria o número que falta | é exatamente para isso que ela existe |
| Onde vive | `wf.blockers_da_cotacao()` | `wf.excecoes_da_cotacao()` |

**Cotação SEM exceção não passa por aprovação.** `wf.avaliar` define
`precisa_aprovacao = bool(excecoes)`: sem exceção, `pode_emitir` é verdadeiro assim que não
houver blocker, e a emissão sai com `SnapshotEmissao.aprovacao_id = None`. Não existe ritual
de "aprovar a própria cotação" — e `solicitar_aprovacao` **recusa** abrir pedido quando não
há exceção ("Não há o que aprovar"), então nem forçando a rota se cria a autoaprovação.

**Estado emitível** é derivado, nunca digitado:

```
pode_emitir = sem blocker
              e (sem exceção  ou  exceção com aprovação vigente do fingerprint atual)
              e estado não imutável
```

## 9.5 Cada estado tem um dono canônico

**C-NEW-09.** `POST /cotacoes/{id}/status` validava a transição e nada mais. Como `TRANSICOES`
permite `rascunho → aguardando_aprovacao → aprovada → emitida → enviada`, um vendedor
comissionado percorria a cadeia inteira por ali — sem `can_approve_quotes`, sem
`AprovacaoCotacao`, sem fingerprint, sem `ws.avaliar()` e sem `SnapshotEmissao`. A cotação
ficava "emitida" com o documento congelado inexistente, e o PDF saía **final**.

O estado deixou de ser um campo que se escolhe:

| Estado | Dono canônico | O que ele confere |
|---|---|---|
| `aguardando_aprovacao` | `ws.solicitar_aprovacao` | que existe exceção, e registra o fingerprint |
| `aprovada` | `ws.decidir` | alçada, fingerprint visto, e grava a decisão |
| `emitida` | `ws.emitir` | revalida blockers e exceções, congela em `SnapshotEmissao` |
| `enviada` | `ws.marcar_enviada` | que houve emissão antes |
| `cancelada` | `ws.cancelar` | registra o motivo |
| `rascunho` | `POST /status` | **única** transição que sobrou — retira privilégio |

A tela deriva os botões da `Prontidao` (`cotacoes.acoes_do_workflow`), não da tabela de
estados: "Emitir" só aparece quando `pode_emitir`; enquanto houver impedimento, o que aparece
é o motivo. Blockers e exceções são exibidos no card de Situação — antes o sistema sabia do
item sem preço e não contava a ninguém.

**PDF final exige `SnapshotEmissao`** (C-NEW-10). Sair sem marca d'água afirma que existe
documento emitido; a prova disso é o snapshot, não o campo `status`. A prévia de rascunho
continua saindo marcada, mesmo com item incompleto — é ferramenta de trabalho.

---

# 10. Aprovação

## 10.1 Quando é exigida

Duas regras **independentes** (`workflow.excecoes_do_item`):

1. **`PRECO_ABAIXO_RECOMENDADO`** — `preco_negociado < preco_recomendado`.
   Exige aprovação **mesmo com margem saudável**. A autonomia de desconto do vendedor é zero.
2. **`MARGEM_ABAIXO_ALVO`** — `margem_liquida < margem_padrao_pct − 0,00005`.
   A tolerância de meio ponto-base existe porque o centavo comercial move a margem, e isso
   é arredondamento, não exceção.

Mais uma da cotação inteira: **`PREMISSA_DESATUALIZADA_MANTIDA`**, quando o usuário optou
por manter premissas anteriores às vigentes.

**Preço acima do recomendado é livre**, com recálculo de comissão.

## 10.2 Teste item a item

`excecoes_da_cotacao` percorre **cada item**. Desconto no item A compensado por acréscimo no
B continua sendo exceção do A — o total esconderia a decisão.

## 10.3 Fingerprint

`workflow.fingerprint()` — SHA-256 de uma serialização canônica.

**Campos materiais do item** (`CAMPOS_MATERIAIS_ITEM`): `produto_id`, `quantidade`,
`preco_base`, `preco_recomendado`, `preco_negociado`, `faturamento`, `custo_unitario`,
`margem_padrao_pct`, `margem_liquida`, `comissao_pct`, `icms_pct`, `difal_pct`,
`encargo_pct`, `custo_referencia_id`, `condicao_pagamento_id`,
`aliquota_interestadual_id`, `premissas_pinadas`.

**Campos materiais da cotação** (`CAMPOS_MATERIAIS_COTACAO`): `cliente_id`,
`estado_destino`, `uf_origem_fiscal`, `contribuinte_icms`, `finalidade`,
`condicao_pagamento`, `freight_type`, `freight_valor`, `revisao`.

**Fora do fingerprint de propósito:** `observacoes` e notas internas — são descritivas,
não mudam economia nem contexto fiscal.

`_canonico()` normaliza números: `100`, `100.0` e `"100.00"` produzem a mesma string, então
reformatar não invalida aprovação. `None` é distinto de `0`.

## 10.4 Invalidação

Mudou qualquer campo material → a decisão vira **`INVALIDADA`**. Não por revogação: ela era
sobre outra configuração. `_recalcular_todos_itens` (`cotacoes.py:318`) dispara a
invalidação junto com o recálculo, para a tela não precisar lembrar de fazê-lo.

## 10.5 Quem aprova

`Usuario.aprova_cotacoes` (`models.py`):
- OWNER sempre pode
- ADMIN pode se `can_approve_quotes` for verdadeiro — e a flag vem **desligada**

`can_manage_economics` **não** concede alçada de desconto: administrar premissa é manter o
cadastro; aprovar cotação é autorizar abrir mão de receita numa venda específica.

## 10.6 Append-only e emissão

`AprovacaoCotacao` não é editada — decisões são registros. `SnapshotEmissao` pina
`aprovacao_id` **e** o fingerprint, não um booleano.

## 10.7 Revisão pós-emissão

POST `/cotacoes/{id}/revisao` cria nova revisão com `cotacao_origem_id` apontando para a
anterior. A anterior fica íntegra, com seu snapshot e seu número. As revisões de uma
proposta continuam sendo **o mesmo negócio** no pipeline.

## 10.8 O que aprovação NÃO faz

Blocker duro **não é aprovável**. `A_COTAR`, `REVIEW_REQUIRED` e frete CIF irresolvido não
passam por alçada nenhuma — aprovação é decisão comercial e não cria o número que falta.

---

# 11. CRM e pipeline

## 11.1 Cliente e prospect

Não existe `Lead → Prospect → Conta`. `Cliente` é a organização **e** o prospect: o que muda
entre eles é *quanto se sabe*.

- Criar aceita nome (e telefone) — `crm_service.criar_cliente`
- `dados_fiscais_faltando()` lista o que falta; **emitir** continua exigindo os dados
- `cliente_por_cnpj()` e `clientes_parecidos()` ajudam a evitar duplicata

## 11.2 Oportunidade

- `responsavel_id` **não é ACL**. Quem vê o quê segue as permissões; o filtro "minhas
  oportunidades" organiza o dia
- **Etapas** (`EtapaOportunidade`): `PROSPECCAO` · `CONTATO` · `QUALIFICACAO` · `COTACAO` ·
  `NEGOCIACAO` · `DECISAO`
- **Status** (`StatusOportunidade`): `ABERTA` · `GANHA` · `PERDIDA` — eixo **separado** da etapa
- **Origem**: `INBOUND` · `OUTBOUND` · `INDICACAO` · `EVENTO` · `PARCERIA` · `CARTEIRA` · `OUTRO`

**O pipeline não é máquina de estados.** Avança, volta e pula etapa. O obrigatório é
**registrar**: `OportunidadeEtapaHistorico`, append-only.

## 11.3 Vínculo com cotação

- `vincular_cotacao()` — **cross-client é recusado pelo servidor**
- `cotacao_mais_recente()` / `metrics_service.proposta_relevante()` determinam a proposta que
  representa o negócio

## 11.4 Ganhar, perder, reabrir

**GANHA** (`crm_service.marcar_ganha`): passa por `validar_compromisso_firme`. Bloqueiam:
custo `ESTIMADO` não confirmado, `REVALIDAR` não reconfirmado, `A_COTAR`, frete CIF
irresolvido, exceção sem aprovação. Grava `valor_fechado` e `cotacao_vencedora_id`.
**Não cria pedido nem PO.** GANHA é terminal.

**PERDIDA**: exige motivo estruturado (`MotivoPerda`: `PRECO`, `PRAZO`, `CONCORRENTE`,
`SEM_RETORNO`, `PROJETO_CANCELADO`, `FORA_DE_ESCOPO`, `OUTRO`).

**REABRIR**: ato explícito, **não apaga o evento de perda**.

## 11.5 Atividades

Tipos: `LIGACAO` · `EMAIL` · `REUNIAO` · `FOLLOW_UP` · `OUTRO`.
`proxima_atividade()` devolve a próxima pendente. `esta_atrasada()` é derivado.

---

# 12. Dashboard e relatórios

**Todas as definições moram em `app/metrics_service.py`.** Dashboard, CSV e testes chamam
as mesmas funções.

## 12.1 Definições

| Métrica | Definição no código |
|---|---|
| `proposta_relevante` | cotações da oportunidade; canceladas fora; **maior revisão dentro de cada genealogia**; entre propostas paralelas vale a de atualização mais recente, `id` desempata |
| `valor_cotado_atual` | derivado da proposta relevante. **Sem coluna** |
| `valor_de_pipeline` | valor cotado quando existe; senão `valor_estimado`. **Ausente ≠ R$ 0,00** |
| `taxa_de_conversao` | `ganhas ÷ (ganhas + perdidas)`. Abertas fora do denominador. Sem encerradas → **`None`**, não `0%` |
| `aging` | dias desde `criado_em`, só para abertas |
| `tempo_ate_fechamento` | `fechado_em − criado_em` |
| `tempo_em_etapas` | derivado de `OportunidadeEtapaHistorico` |
| margem agregada | `Σ lucro ÷ Σ receita`, **nunca** a média dos percentuais. Item sem dado econômico fica de fora |

**R1 e R2 nunca são somadas.**

## 12.2 Estado atual × evento histórico

Um negócio perdido e **reaberto está aberto**: não conta como perdido em nenhuma métrica de
estado. O evento de perda continua auditável em `OportunidadeEtapaHistorico`.

`_fechada_no_periodo()` faz encerradas entrarem pela janela do desfecho, enquanto abertas
entram sempre — mesma regra na tela e no CSV, e por isso os dois batem.

## 12.3 Painéis

- `painel_comercial` — por etapa, origem, responsável, motivo de perda
- `painel_de_cotacoes` — relatório de **documentos**: cada revisão é uma linha
- `painel_de_aprovacoes` — pendentes e decididas na janela
- `painel_economico` — margem agregada, margem por fornecedor
- `saude_operacional` — seção 17

## 12.4 CSV

Três arquivos, mesmos filtros da tela. `_csv()` devolve `Response` simples, não
`StreamingResponse`. A coluna econômica é condicionada a `ve_economia`; o número de linhas
não muda.

---

# 13. Permissões

## 13.1 Papéis

`Papel`: `OWNER` · `ADMIN` · `VENDEDOR_INTERNO` · `VENDEDOR_COMISSIONADO`.

`PAPEIS_ECONOMICOS` = OWNER, ADMIN. `PAPEIS_ADMINISTRATIVOS` = OWNER, ADMIN.

## 13.2 Matriz

| | OWNER | ADMIN | VEND. INTERNO | VEND. COMISSIONADO |
|---|---|---|---|---|
| `/`, `/comercial`, `/pipeline`, `/oportunidades` | sim | sim | sim | sim |
| `/clientes`, `/cotacoes`, `/produtos` | sim | sim | sim | sim |
| `/relatorios` | sim | sim | sim (sem economia) | sim (sem economia) |
| `/calculadora` | sim | sim | **403** | **403** |
| `/relatorios/economico` | sim | sim | **403** | **403** |
| `/saude` | sim | sim | **403** | **403** |
| `/health/detalhe` | sim | sim | **403** | **403** |
| `/admin`, `/configuracoes`, `/importar` | sim | sim | **403** | **403** |
| memória do preço do item | sim | sim | **403** | **403** |
| Alterar premissa econômica | sim | se `can_manage_economics` | não | não |
| Aprovar desconto | sim | se `can_approve_quotes` | não | não |
| Gerenciar usuários | sim | se `can_manage_users` | não | não |
| **Vê custo, CNET, EXW, margem, lucro, markup** | sim | sim | **não** | **não** |
| Vê preço, faturamento, quantidade | sim | sim | sim | sim |

O vendedor **comissionado** também não vê a própria comissão: ela é calculada no servidor
sem lhe ser exibida (`permissoes.ve_economia`, docstring).

## 13.3 Flags granulares

| Flag | Default | Semântica |
|---|---|---|
| `can_manage_users` | `False` | Gerir gente. **Não** concede gestão econômica |
| `can_manage_economics` | `True` | Versionar premissa. Vem ligada por não haver hoje um segundo administrador de quem separar |
| `can_approve_quotes` | `False` | Alçada de desconto. **Alçada se concede, não se herda** |

OWNER sempre pode as três, por propriedade no modelo — um sistema com um único OWNER e a
flag desligada ficaria sem ninguém capaz de criar acesso.

## 13.4 Mecânica

- **Autenticação** no middleware, uma vez, para todas as rotas — rota nova não nasce aberta
- **Autorização** no endpoint, via `app/permissoes.py`
- **Endpoint que existe só para expor economia é NEGADO** (403), não filtrado. Endpoint
  **comercial** é filtrado, não negado
- **Lista de permissão, não de bloqueio**: `CAMPOS_ITEM_COMERCIAL`,
  `CAMPOS_PRODUTO_COMERCIAL`, `CAMPOS_TOTAIS_COMERCIAL`. Campo novo não vaza por esquecimento
- `sem_confidenciais()` é rede de segurança recursiva, usada **depois** da lista de
  permissão, não no lugar dela

## 13.5 Sessão

- Hash **argon2id** em `Usuario.senha_hash`
- Cookie carrega **só `{uid, v}`**; papel, `ativo` e nome vêm do banco **a cada request**
- `HttpOnly`, `SameSite=lax`, `Secure` em produção, expiração **12 h**
- `sessao_versao` invalida cookies abertos quando a senha muda ou a conta é desativada
- `ANARA_SECRET_KEY` obrigatória: sem ela e com `ANARA_ENV=producao`, o processo **não sobe**

---

# 14. Admin e versionamento

`app/admin_service.py` (958 linhas). **Nada aqui faz `UPDATE valor = novo`.**

## 14.1 Preview → apply

```
preview_*(…)  → Proposta com token = _hash_estado(estado observado)
                    ↓  o navegador devolve o TOKEN, nunca os valores
aplicar_*(…)  → recomputa o hash; diferente → ConflitoDeVersao
```

Os valores são **recalculados no servidor**. O preview informa o **escopo**: quantos SKUs a
mudança alcança (`escopo_da_premissa`, `escopo_da_margem`, `nivel_da_regra`).

## 14.2 Versionamento

Versão antiga fecha com `valid_to`; versão nova nasce com `substitui_versao`.
`referencia_vigente`, `resolver_margem` e `resolver_encargo` resolvem **por data** — vigência
futura funciona.

## 14.3 NO_CHANGE × reconfirmação

A comparação é pela **identidade econômica completa** — valor, bruto, status **e** evidência
(fonte, documento, data).

- Mesmo preço com **fonte nova** → **reconfirmação**, vira versão. "O fornecedor confirmou
  em 05/09 que continua 100" é informação econômica
- Só diferença de escrita (`"100"` × `"100,00"`, `" Fonte A "` × `"fonte a"`) → **no-op**

## 14.4 Rollback e exclusão

Voltar ao valor antigo é criar **V3**, não apagar V2.
`referencia_esta_em_uso()` → referência que já participou de cotação **encerra vigência**;
não some. `apagar_referencia()` só vale para o que nunca foi usado.

## 14.5 Casamento na importação

`_casar_sku()`: SKU exato, ou campos estruturados suficientes.
Ambíguo → `REVIEW_REQUIRED`. Inexistente → `SKU_NAO_ENCONTRADO`. São coisas diferentes.
**Nunca casamento por nome.**
`preview_importacao` / `aplicar_importacao` com dry run.

## 14.6 Pinning e detecção

`premissas_desatualizadas()` **detecta e só detecta** — rascunho não se atualiza sozinho.
A cotação fica presa à versão **exata** pelos campos de pinning (seção 4.2).

## 14.7 Trilha

`registrar()` grava em `AuditLog`: ator, papel, ação, escopo, antes, depois, motivo, origem,
resultado, correlação do lote. Visível em `/admin/trilha`.

## 14.8 Validações

`valida_decimal` · `valida_percentual` · `valida_vigencia` · `valida_fonte`.
`DadoInvalido` vira mensagem legível — **nunca stack trace, nunca segredo**
(`admin.py:_erro`).

---

# 15. Auditoria e imutabilidade

| Objeto | Natureza |
|---|---|
| `AuditLog` | **append-only** |
| `AprovacaoCotacao` | **append-only** — decisões não são editadas |
| `OportunidadeEtapaHistorico` | **append-only** |
| `SnapshotEmissao` | **congelado** — reconstruir o documento não depende de lookup vivo |
| `CustoReferencia` | versionado — versão antiga fecha, não some |
| `Premissa`, `MaterialPreco`, `CmtPreco`, `ParametroKTC`, `MargemRegra`, `CondicaoPagamento` | versionados por vigência |
| `BaseImportacao`, `BasePremissaPonte` | legado preservado, intocado |

**Uma cotação histórica preserva:** número, revisão, snapshot, fingerprint, premissas
pinadas por **id e versão**, e a memória do preço item a item.

**Uma revisão altera:** nada da anterior. Cria registro novo com `cotacao_origem_id`.

**Genealogia:** `cotacao_origem_id` encadeia as revisões; `metrics_service` usa isso para
nunca somar R1 e R2.

---

# 16. PDF

`app/pdf_bridge.py` → `gerar_cotacao.py` (gerador legado aprovado, **não reescrito**).

- **Rascunho:** número recebe `"(RASCUNHO — não emitida)"`. Revisão > 1 recebe `"· rev. N"`
- **O PDF comercial não leva** custo, CNET, margem, lucro, markup, comissão nem fornecedor —
  e há teste que falha se levar
- **Quem pode gerar:** autenticado (rota `/cotacoes/{id}/pdf`)
- **Condição para sair:** nenhum blocker duro. Com blocker, devolve JSON com `erro`,
  `motivos` (lista por item) e `detalhe`
- Preview e final são **a mesma folha** para quem recebe — daí a marca textual

---

# 17. Saúde operacional

`metrics_service.saude_operacional()` (`metrics_service.py:513`), exibida em `/saude`.

## 17.1 Catálogo — INFORMATIVO

| Check | Valor hoje |
|---|---|
| `skus_ativos` | 340 |
| `skus_sem_custo` — sem `custo_unitario` | 45 |
| `referencias_versionadas` | 161 |
| `referencias_vigentes_por_status` | `CONFIRMADO` 38, `A_COTAR` 9 |

## 17.2 Blockers — HARD BLOCKER

| Check | Critério | Hoje |
|---|---|---|
| `cotacoes_com_a_cotar` | item com `status_custo_item == A_COTAR` | 0 |
| `cotacoes_com_review_required` | `status_custo_item` ou `status_fiscal` = `REVIEW_REQUIRED` | 0 |
| `cotacoes_com_pagamento_irresolvido` | `status_pagamento == REVIEW_REQUIRED` | 0 |
| `total_blockers` | | **0** |

## 17.3 Avisos — WARNING

| Check | Critério | Hoje |
|---|---|---|
| `cotacoes_com_estimado_pendente` | `confirmation_pending` verdadeiro | 0 |
| `cotacoes_com_revalidar` | `status_custo_item == REVALIDAR` | 0 |
| `total_avisos` | | **0** |

## 17.4 Comercial — WARNING

`aprovacoes_pendentes` 0 · `oportunidades_sem_atividade` 0 · `atividades_atrasadas` 0

## 17.5 Pendências conhecidas — INFORMATIVO

`PENDENCIAS_CONHECIDAS` (`metrics_service.py:597`) — dez itens, renderizados na tela.
Ver seção 18.

---

# 18. Pendências conhecidas

| ID | Problema | Impacto | Seguro hoje? | O que o sistema faz enquanto não resolve | Antes do piloto? | Antes de produção? |
|---|---|---|---|---|---|---|
| **C-NEW-01** | ICMS da prestação de frete: incluso ou gross-up `÷(1−12%)`? Três evidências não reconciliadas | **ALTO** — muda todo preço CIF | Sim | `icms_situacao = DESCONHECIDO`; bloqueia CIF | Não, se usar FOB | **Sim** |
| **C-NEW-06** | Fiel depositário 0,5% da NF: incide sempre? Muda RV de 0,30% para 0,80% | **ALTO** — termo da equação | Sim | Componente `DESCONHECIDO`; bloqueia CIF | Não, se usar FOB | **Sim** |
| **C-NEW-02** | GRIS 0,10%: incide sempre? O exemplo da tabela o omite | MÉDIO | Sim | Componente `DESCONHECIDO` | Não | **Sim** |
| **C-NEW-08** | Base do pedágio: peso real ou taxado | MÉDIO | Sim | `pedagio_base = DESCONHECIDO`; segue quando os pesos coincidem, bloqueia quando divergem | Não | **Sim** |
| **C-NEW-04** | Volume/cubagem por SKU ausente | MÉDIO | Sim | Hierarquia de resolução do peso taxado (seção 8.4) | Não | Recomendado |
| **Q-L** | Origem logística de Daune e Decor | MÉDIO | Sim | Campos vazios em `fornecedor`; CIF desses dois fica `A_COTAR` | Não | **Sim** |
| **C-NEW-03** | Passo Fundo-RS: região sem tarifa | BAIXO | Sim | `FRETE_A_COTAR` para a região e para qualquer cidade fora da cobertura — **sem aproximar por região vizinha** | Não | Recomendado |
| **C-NEW-07** | Tabela TRANSAL vence 31/12/2026 | MÉDIO | Sim | Vigência cadastrada; vencida sem substituta bloqueia | Não | **Sim** |
| **B-18** | Poda apagando o backup recém-criado | — | — | **RESOLVIDO.** Ordena pelo carimbo do nome e preserva o recém-criado. Deixou de ser teórico nesta rodada: o backup de segurança da sessão foi apagado quando o diretório passou de 30 | — | — |
| **B-22** | `scripts/backup_banco.py` não aplica o limite de retenção | BAIXO | Sim | O diretório cresce; a poda do `app/migrations.py` funciona | Não | Recomendado |
| **B-19** | `classificar_base.py` e `importar_fornecedores_nacionais.py` quebram com cenário fiscal bloqueado; `comparar_regressao.py` importa função removida na Onda 1 | MÉDIO | Sim | Nenhum efeito no uso normal — são scripts de manutenção | Não | Recomendado |
| **B-20** | `GET /logout` muda estado via GET; com `SameSite=lax` um link de terceiro derruba a sessão | BAIXO | Sim | Não vaza dado; a pessoa loga de novo | Não | Recomendado |
| **B-17** | Duas fontes Daune com bases diferentes: o custo persistido de 13 SKUs é **1,5123× o bruto** da fonte, razão constante entre produtos sem relação | ALTO | Sim | A migração usou o bruto da fonte do fornecedor, que é rastreável | Recomendado | **Sim** |
| **B-16** | `Produto.gsm` nulo em 28 dos 31 SKUs de `Duvet Insert`; a gramatura vive dentro da string do nome | MÉDIO | Sim | Casamento por campos estruturados é impossível para gramatura nessa família | Não | Recomendado |
| **B-13** | 21 divergências entre `models.py` e o esquema real do banco: 5 índices, 2 FKs, 10 NOT NULL, 4 booleanos | MÉDIO | Sim | Consequência do `ALTER TABLE ADD COLUMN` do SQLite | Não | **Sim** |
| **B-21** | `ANARA_DB_URL` não isolava a aplicação — só o Alembic a lia. Apontar para uma cópia migrava a cópia e **escrevia na produção** | — | — | **RESOLVIDO.** `app/db.py` resolve a variável; `scripts/smoke_test.py` prova o isolamento antes de escrever e aborta se não conseguir | — | — |

## 18.1 Publicação remota: BLOQUEADA

Uma senha compartilhada saiu do código na Sessão 4 mas **continua nos commits `413d6bd` e
`165d75e`**. Além disso, `referencia/` versiona tabela de preço de fornecedor.

Enquanto isso: **sem remote, sem push, sem GitHub.** Confirmado: `git remote -v` está vazio.

Liberar exige sanitização do histórico ou decisão explícita de que a credencial aposentada é
inócua.

---

# 19. Runtime

## 19.1 Startup

```bash
cd ~/Anara-Cotacao
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8420 --reload
```

Porta **8420**, ligada a `127.0.0.1` — não exposto à rede.

## 19.2 Variáveis de ambiente

| Variável | Obrigatória? | Efeito |
|---|---|---|
| `ANARA_SECRET_KEY` | **Sim em produção** | Assina o cookie. Com `ANARA_ENV=producao` e sem ela, o processo **não sobe**. Em desenvolvimento, gera chave aleatória por processo — cada reinício derruba as sessões |
| `ANARA_ENV` | não | `producao` ativa `Secure` no cookie e a exigência acima |
| `ANARA_DB_URL` | não | Sobrepõe o caminho do banco **para a aplicação inteira** — app, scripts e migrations |
| `ANARA_SENHA_BOOTSTRAP` | não | Senha para `criar_usuario.py` sem prompt |

`.env` existe localmente, `chmod 600`, ignorado pelo Git. `.env.example` é o modelo.

## 19.3 Banco

`app/db.py`:
```python
DB_PATH = os.path.expanduser("~/Anara-Cotacao/data/anara.db")
DB_URL  = os.environ.get("ANARA_DB_URL", "").strip() or f"sqlite:///{DB_PATH}"
```
`caminho_do_banco()` devolve o arquivo que o processo está usando — **serve para provar
isolamento**.

## 19.4 Migrations

```bash
python3 -m alembic upgrade head     # termina em 0017
```

## 19.5 Criar o primeiro OWNER

Dois caminhos:

```bash
python3 scripts/criar_usuario.py --email voce@anara.com.br --nome "Nome" \
    --papel OWNER --gerencia-usuarios
```
A senha vem de `ANARA_SENHA_BOOTSTRAP` ou é digitada **sem eco**. Nunca de argumento —
argumento aparece no `ps` e fica no histórico do shell. Não existe senha default.

Ou pela tela `/primeiro-acesso`, que só existe enquanto não há nenhum usuário.

## 19.6 Healthcheck

```bash
curl -s http://127.0.0.1:8420/health     # {"status":"ok","database":"ok"}
```

## 19.7 Smoke test e proteção do banco

```bash
python3 scripts/smoke_test.py
```

Sobe um uvicorn real numa porta livre, contra uma **cópia** do banco. Faz login, percorre
23 rotas, executa dois fluxos de ponta a ponta, gera PDF. **60 verificações.**

Três camadas de proteção, todas adicionadas depois do B-21:

1. `app/db.py` honra `ANARA_DB_URL` — a aplicação inteira, não só o Alembic
2. `provar_isolamento()` compara `caminho_do_banco()` do processo servidor com o da produção
   **antes de qualquer escrita**, e **aborta** se não conseguir provar
3. Conferência final do **tamanho e mtime** do banco de produção

## 19.8 Backup

```bash
python3 scripts/backup_banco.py backup --motivo antes-de-mexer
```
`app/migrations.py` também faz backup automático. Marcos ficam em
`~/Anara-Cotacao-Backups/` (fora do alcance da poda — ver B-18).

## 19.9 Verificações

```bash
python3 -m pytest -q                              # 895 passando
python3 scripts/comparar_baseline_sessao3b.py     # prova que nenhum preço mudou
```

`pytest.ini` define `testpaths = tests`, para o coletor não pegar `scripts/smoke_test.py`.

---

# 20. Experiência atual do usuário

Descrição objetiva dos fluxos principais, para revisão de UX.

## 20.1 Primeiro acesso

1. Usuário abre `http://127.0.0.1:8420`
2. É redirecionado para `/login`; com banco vazio, `/login` redireciona para `/primeiro-acesso`
3. Vê o card centralizado: logo Anara, wordmark, tagline, texto *"Nenhuma conta existe ainda.
   Crie o acesso do administrador — esta tela só aparece uma vez."*
4. Preenche **Nome**, **E-mail**, **Senha**, **Repita a senha**
5. Clica em **Criar acesso e entrar**
6. É levado a `/` já logado

## 20.2 Login

1. Usuário abre `http://127.0.0.1:8420`
2. É redirecionado para `/login?next=/`
3. Vê o card com logo, wordmark, tagline, campos **E-mail** e **Senha**, botão **Entrar**
4. Erra a senha → recarrega a mesma tela com `"E-mail ou senha inválidos."`, status 401
5. Acerta → vai para `next`, ou `/`

## 20.3 Criar cliente e oportunidade

1. Menu lateral → **Clientes** (`/clientes`)
2. Vê a lista e um formulário de criação
3. Preenche nome (mínimo) e envia → volta à lista com o cliente criado
4. Clica no nome → `/clientes/{id}`, com blocos: dados, contatos, oportunidades, cotações
5. Se faltarem dados fiscais, a tela lista quais
6. Adiciona contato pelo formulário do bloco Contatos
7. Menu → **Oportunidades** → cria com cliente, título, responsável, origem
8. Vai para `/oportunidades/{id}`: dados, histórico de etapas, atividades, cotações, timeline

## 20.4 Montar cotação

1. Em `/oportunidades/{id}`, clica na ação de criar cotação — ou menu → **Cotações** →
   **Nova**
2. Em `/cotacoes/nova`: escolhe cliente, condição de pagamento, origem, destino, contribuinte,
   frete, validade
3. Envia → vai para `/cotacoes/{id}`
4. A tela abre com **Produtos** primeiro: busca de produto, quantidade, preço
5. Adiciona item → a linha aparece com o preço recomendado, formado com as premissas vigentes
6. Digita preço diferente → margem recalculada, e a exceção comercial é marcada, se houver
7. Abaixo, **Resumo comercial** e depois **Dados da venda** (cenário comercial e fiscal)
8. Muda destino, condição de pagamento, contribuinte ou tipo de frete → aparece
   *"Há alterações ainda não aplicadas ao cálculo"*, e Gerar PDF fica inerte
9. Clica em **Atualizar cenário e recalcular** → *"Cenário atualizado e preços recalculados"*
10. *Mais detalhes* recolhe prazo, validade, responsável, contato, origem fiscal, origem
    logística, observações e termos
10. Clica em **Gerar PDF** → ou baixa o arquivo, ou recebe JSON com `erro`, `motivos` e
    `detalhe`

## 20.5 Aprovar

1. Vendedor solicita: POST `/cotacoes/{id}/aprovacao/solicitar`, com justificativa
2. Aprovador abre `/aprovacoes` e vê a fila
3. Clica num pedido → `/aprovacoes/{pedido_id}`: cabeçalho da cotação (cliente, destino,
   condição), totais recomendado × negociado × diferença, e os motivos estruturados
4. Clica em **Aprovar** ou **Rejeitar**, com comentário
5. Se a cotação mudou entre a abertura da tela e a decisão, o servidor recusa
   (`AprovacaoVencida`)

## 20.6 Emitir

1. Em `/cotacoes/{id}`, com aprovação válida e sem blocker, POST `/emitir`
2. O status vira `emitida`, cria-se `SnapshotEmissao`, e os campos do cabeçalho param de
   aceitar alteração
3. POST `/enviar` marca `enviada`
4. Para mudar qualquer coisa: POST `/revisao` cria R2

---

# 21. Mapa de código

| Domínio | Arquivos |
|---|---|
| **Dinheiro / precisão** | `app/dinheiro.py` |
| **Pricing** | `app/pricing_engine.py` · `app/pricing_service.py` · `app/margin_rules.py` · `app/payment_terms.py` |
| **KTC industrial** | `app/ktc_engine.py` · `app/nationalization.py` · `app/fontes_ktc.py` · `app/spec_parser.py` |
| **Custo** | `app/custo_service.py` · `app/matching.py` |
| **Fiscal** | `app/fiscal_rules.py` |
| **Frete** | `app/frete_engine.py` · `app/frete_service.py` · `app/peso.py` |
| **Workflow** | `app/workflow.py` · `app/workflow_service.py` · `app/routers/workflow.py` |
| **CRM** | `app/crm_service.py` · `app/routers/crm.py` · `app/routers/clientes.py` |
| **Reports** | `app/metrics_service.py` · `app/routers/relatorios_comerciais.py` · `app/routers/relatorios.py` · `app/relatorios.py` |
| **Admin** | `app/admin_service.py` · `app/routers/admin.py` · `app/routers/configuracoes.py` · `app/config_service.py` |
| **Segurança** | `app/auth.py` · `app/permissoes.py` · `app/confidencial.py` · `app/routers/login.py` |
| **Cotação (HTTP)** | `app/routers/cotacoes.py` · `app/routers/calculadora.py` · `app/routers/produtos.py` |
| **Importação** | `app/excel_import.py` · `app/routers/importar.py` |
| **PDF** | `app/pdf_bridge.py` · `gerar_cotacao.py` |
| **Persistência** | `app/models.py` · `app/db.py` · `app/migrations.py` · `app/seeds.py` · `alembic/` |
| **Arquivamento** | `app/arquivamento.py` |
| **Busca / nomes** | `app/busca.py` · `app/nomes.py` |

---

# 22. Funcionalidades que o sistema NÃO possui hoje

Seção explícita, para não confundir especificação com implementação.

## 22.1 Comercial

- **Não existe pedido.** GANHA registra o resultado comercial e nada mais
- **Não existe faturamento, NF-e nem integração com ERP**
- **Não existe reabertura de negócio GANHO** — GANHA é terminal
- **Não existe envio de e-mail.** "Enviada" é marcação manual de estado
- **Não existe assinatura eletrônica nem aceite do cliente pelo sistema**
- **Não existe cálculo de comissão a pagar** — a comissão entra na formação do preço, mas
  não há apuração por vendedor

## 22.2 Gestão

- ~~Não existe tela de usuários~~ — **existe desde o Product Cleanup**: `/admin/usuarios`
- **Não existe recuperação de senha** — nem "esqueci minha senha", nem e-mail de reset
- **Não existe autenticação de dois fatores**
- **Não existe log de acesso** (quem entrou, quando). Há `ultimo_login_em`, sobrescrito a
  cada login
- **Não existe multi-empresa ou multi-tenant**

## 22.3 Catálogo e preço

- **Não há motor industrial para lençol com elástico, roupão e chinelo** — sem geometria
  confirmada
- **Não há tabela de frete com origem São Paulo** — só Itajaí-SC
- **Não há volume/cubagem cadastrado por SKU**
- **Não há reclassificação automática do `REVIEW_REQUIRED` legado**
- **Não há promoção automática de `ESTIMADO` para `CONFIRMADO`**

## 22.4 Infraestrutura

- **Não está publicado.** Sem remote, sem push, sem domínio, sem HTTPS. Só `127.0.0.1:8420`
- **Não usa Postgres** — previsto, mas hoje é SQLite
- **Não há backup automático agendado** — o backup é comando manual (mais o automático de
  `migrations.py`)
- **Não há fila, worker nem tarefa assíncrona**
- **Não há API pública documentada** — as rotas JSON existem para as telas

## 22.5 Relatórios

- **Não há gráfico** nos relatórios — são tabelas e números
- **Não há exportação para Excel pela interface** — os CSVs existem; a geração de XLSX é por
  script (`scripts/gerar_lista_precos.py`, não commitado)
- **Não há relatório de comissão por vendedor**

---

# 24. Fase 3C — redesign comercial, Dashboard OWNER/ADMIN e PDF cliente (16/09/2026)

## 24.1 Camada visual

Um design system só (`app/static/css/anara.css`): sidebar compacta, topbar baixa com título,
filtros e ação principal, conteúdo em toda a largura, tabelas densas, pills de status,
modais/popovers (`app/static/js/ui.js`), timeline, stepper de pós-venda, quadro kanban,
painel sticky e gráficos em SVG puro (`app/static/js/dashboard.js`) — **sem CDN**. Templates
redesenhados: `login`, `base`, `vendas_list`, `venda_detail`, `clientes_list`,
`cliente_detail`, `cotacoes_list`, `cotacao_detail` (+ partial `_cotacao_situacao`),
`dashboard`, `produtos_list`. As telas administrativas anteriores herdam a régua nova sem
redesenho.

Menu por papel (decidido no template a partir de `permissoes`, autorizado no backend):
vendedora Vendas · Clientes · Cotações · Produtos; OWNER/ADMIN + Dashboard (`/`), Aprovações
(alçada), Admin. Relatórios mora dentro do Admin. Sem saudação.

## 24.2 Vendas, Venda, Clientes

- `/vendas`: lista (Cliente, Projeto, Valor, Status inline, Cotação atual, Última atualização,
  Responsável), filtros busca/status/cliente/responsável/período (`atualizado_em`), `+ Nova
  venda` em modal; `?vista=quadro` mostra só Rascunho/Enviado/Negociação com contagem/total e
  drag-and-drop → `POST /vendas/{id}/status`. Vendido/Perdido continuam ações explícitas.
- `/vendas/{id}`: 70/30 — registro rápido e modal de atualização (+ próxima ação), timeline
  única e humanizada (`crm.timeline`: tipos traduzidos por `rotulos.EVENTO_TIMELINE`, avanço
  automático sem prefixo de código), resumo, cotação atual e revisões, próxima atividade,
  pós-venda com stepper; financeiro só para quem vê economia (403 no backend, como antes).
- `/clientes`: `metrics_service.clientes_resumo` (uma passada) → total comprado, nº vendas,
  última compra, em aberto e **status financeiro derivado** (Atrasado só quando a alçada
  marcou; Em aberto; Em dia). `/clientes/{id}`: KPIs de `cliente_360`, abas, modais;
  `POST /clientes/{id}/editar` (CNPJ de outro cliente ativo → 409; finalidade só do enum;
  AuditLog `UPDATE_CLIENT`).

## 24.3 Cotações e negociação reativa

`/cotacoes/{id}` monta o painel já com `comercial_service.payload_vendedora` /
`payload_admin` (`_negociacao_inicial`). O JavaScript (`app/static/js/cotacao.js`) **não
calcula economia**: preço digitado → `POST /negociacao/preview` (debounce) → pinta linha,
subtotal, frete, total, desconto, comissão estimada e status de autonomia; confirmar →
`POST /negociacao` (grava, invalida aprovação) e relê `GET /cotacoes/{id}/painel`
(HTML das ações válidas, derivadas de `ws.avaliar`). Quantidade → `PUT /itens/{item}` e
releitura. Recusa do servidor (Daune travado, 409) → toast humano e rollback do campo.
`_item_vendedora` ganhou `desconto_linha_pct` (exibição) e `preco_travado` (já na lista
de permissão). OWNER/ADMIN têm "Economia da proposta" fechada por padrão, com
`payload_admin.economia`.

Cabeçalho: `observacao_cliente` (vai à proposta) × `observacoes` (interna, nunca vai) —
migration 0020. Frete na tela: Anara entrega (CIF, com valor opcional; sem valor usa a
tabela da rota quando houver), Por conta do cliente (FOB), A combinar. Motor de frete
nacional definitivo **não** foi implementado (C-NEW de frete continuam abertos).

## 24.4 Dashboard OWNER/ADMIN

`metrics_service.dashboard_admin(session, periodo, FiltrosDashboard)` e `serie_mensal`
(12 meses; `ano_anterior` só com dado real). Filtro de vendedora/cliente corta a venda;
fornecedor/família cortam os itens da vencedora (a venda entra se sobrar item). Vendido =
`valor_fechado` por `won_em`; lucro/margem/desconto/comissão = itens da vencedora; faturado
por `faturado_em`; pago por `pago_em`; a receber = pós-venda em aberto. `periodo_de` ganhou
`trimestre` e `12m`. Análises: performance por vendedora, top clientes e concentração Top 5,
funil (abertas por etapa) e aging, mix fornecedor/família, impacto dos descontos (faixas ×
margem e por venda), pós-venda, motivos de perda, rentabilidade por cliente. Nada é inventado:
sem dado, `None` e "—".

## 24.5 PDF — PROPOSTA COMERCIAL ANARA

`app/pdf_bridge.montar_documento` produz `header/items/totals` por **lista de permissão**
(`CAMPOS_HEADER`, `CAMPOS_ITEM`, `CAMPOS_TOTAIS`), varre códigos operacionais e recusa com
`PdfInseguro`; `app/pdf_proposta.build_pdf` só desenha. Final usa `SnapshotEmissao`
(itens, cliente, frete, número, revisão, data de emissão); rascunho usa a cotação viva com
faixa e marca d'água (imagem, não texto) "RASCUNHO — NÃO ENVIAR AO CLIENTE" em toda
página. Fechamento (subtotal, frete, TOTAL DA PROPOSTA) são as últimas linhas da tabela de
itens, que repete o cabeçalho a cada página; rodapé "Página X de Y". Frete por extenso
(`Frete nacional: R$ X` / `Por conta do cliente` / `A combinar` / `a definir` só em
rascunho). Sem preço recomendado, tabela, desconto, fornecedor, custo, margem, comissão.
`gerar_cotacao.py` (gerador do Excel) permanece no repositório, sem uso pela plataforma.

## 24.6 Demonstração e inspeção visual

`scripts/demo_3c.py --db <cópia> [--serve porta]` copia o banco, migra a cópia, semeia dois
usuários de demonstração e um cenário comercial pequeno, e sobe o servidor; nunca toca em
`data/anara.db`. `scripts/visual_3c.py` fotografa as telas (desktop e celular) com
Playwright e acusa economia no texto da vendedora, HTTP ≥ 400 e erro de console.

# 23. Check final

| Verificação | Resultado |
|---|---|
| HEAD | `6600f68` — inalterado durante toda a rodada |
| Alembic | `0017` — **nenhuma migration** |
| Suíte | **895 passando**, 0 falhas, 60 s |
| Baseline | 5.900 ROUNDING_CORRIGIDO · 4.540 IGUAL · **0 NAO_EXPLICADA** |
| Histórico econômico | 54 + 765 células IGUAL · **0 mudanças** |
| Catálogo | `custo_unitario` e `preco_base` **348 IGUAL** cada |
| Banco real | 19 cotações · 48 itens · 340 SKUs · 1 usuário · 1 cliente · 2 registros de auditoria |
| Dados de teste no banco | **zero** |
| Integridade | `ok` |
| Push / remote | **nenhum** — `git remote -v` vazio |

## O que mudou economicamente, e por quê

Uma coisa só, autorizada e provada: **precificação nova passou a resolver o custo pelas
premissas vigentes** em vez de ler `Produto.custo_unitario`. Sem isso, trocar o dólar não
mudava o preço de cotação nenhuma, e o item gravado se contradizia com a própria memória.

Cotação histórica, snapshot, pino e fingerprint continuam intocados — é o que os 54 + 765
IGUAL do baseline medem.

## Documentos irmãos

- `PRODUCT_CLEANUP_REPORT.md` — antes → depois e os bugs encontrados
- `PILOT_READINESS.md` — o que confiar e o que ainda está pendente

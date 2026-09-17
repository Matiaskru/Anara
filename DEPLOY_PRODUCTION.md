# Deploy em produção — Anara Cotações

Roteiro operacional para colocar `~/Anara-Cotacao` no ar em **Railway + PostgreSQL**, a
partir de um **GitHub privado**. Nenhum valor secreto aparece aqui; os nomes das
variáveis estão em `.env.example`. Preparado em 17/09/2026; nada foi publicado nessa data.

Princípio: **o esquema é do Alembic, os dados são do migrador, o segredo é do ambiente.**
A aplicação em produção não cria tabela, não faz backup de arquivo e não conhece
`127.0.0.1:8420` — ouve em `0.0.0.0:$PORT` pelo comando do `Procfile`.

---

## PREPARAÇÃO

Na máquina de origem (Mac), antes de qualquer conta externa:

- [ ] Árvore limpa e suíte verde: `git status --short` vazio; `python3 -m pytest -q` sem falha.
- [ ] Histórico **sanitizado** (17/09/2026): a senha compartilhada antiga foi removida de
      todos os commits com `git filter-repo --replace-text`; os hashes históricos mudaram
      (mapa em `ANARA_EXECUTION_STATE.md`). Bundle anterior à reescrita:
      `~/Anara-Cotacao-Backups/anara_git_pre_sanitize_*.bundle`. Confirme que o valor
      antigo não aparece em commit algum: `git log --all -p | grep -c '<valor antigo>'`
      deve devolver `0` (digite o valor só no terminal, nunca em arquivo).
- [ ] **ROTACIONAR_CREDENCIAL** — a senha antiga não vale mais na plataforma (a única conta
      real usa outra senha, provado em 17/09/2026). Se ela foi reaproveitada em qualquer
      outro lugar (e-mail, Wi-Fi, planilha protegida), troque lá **antes** do push.
- [ ] Decisão sobre `referencia/` (tabelas de preço de fornecedor, PDFs de cotação,
      docx de orçamento — 932 KB). **Não é lida em runtime.** Só
      `scripts/importar_fornecedores_nacionais.py` e `tests/test_tarifario_daune_anastacio.py`
      a usam. Recomendação: **não subir ao GitHub**. Para tirar do histórico (após copiar a
      pasta para `~/Anara-Cotacao-Backups/referencia/`):
      `git filter-repo --path referencia --invert-paths --force` e acrescentar
      `referencia/` ao `.gitignore`. Impacto: o teste do tarifário Daune pula quando a
      pasta não existe; o script de importação precisa da pasta local.
- [ ] **Dados herdados (D-01 no audit):** em 17/09/2026 11:25 a conta OWNER apagou de vez 17
      cotações herdadas já arquivadas. Se foi intencional, atualize/aposente os quatro
      testes-guardiões do conjunto herdado; se não, restaure
      `~/Anara-Cotacao-Backups/anara_pre_exclusao_lote_owner_20260917-112554.db` (`BACKUP.md`)
      **antes** do cutover — o migrador leva o que estiver em `data/anara.db`.
- [ ] Gere a chave de sessão e guarde no gerenciador de senhas (não em arquivo do repo):
      `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] Tenha em mãos: SMTP (host, porta, usuário, senha, remetente) e o domínio desejado.

## GITHUB PRIVADO

1. Crie o repositório **privado** e **vazio** (sem README, sem .gitignore, sem licença).
2. Na pasta do projeto:
   ```bash
   git remote add origin git@github.com:<conta>/anara-cotacao.git
   git push -u origin main
   ```
3. Confira no GitHub que **não** existem `data/anara.db`, `.env`, `*.log`, `data/backups/`
   (o `.gitignore` os exclui; `git ls-files | grep -E '\.(db|env|log)$'` deve ser vazio).
4. Settings → Collaborators: só quem opera o deploy. Branch `main` protegida é opcional.

## RAILWAY

Builder: **Railpack (Python nativo)** — escolhido por ser o mais simples e reprodutível:
`requirements.txt` + `.python-version` (3.12) bastam, sem Dockerfile para manter. O
`railway.json` já declara build, start, pre-deploy e healthcheck.

Passos exatos:

1. **Criar projeto** — New Project → *Deploy from GitHub repo* (ainda não conecte; só crie).
2. **Adicionar PostgreSQL** — *+ New → Database → PostgreSQL*. Anote o nome do serviço
   (ex.: `Postgres`).
3. **Conectar GitHub privado** — *+ New → GitHub Repo* → autorize o app do Railway só para
   este repositório → selecione `anara-cotacao`, branch `main`. **Desligue o deploy
   automático por enquanto** (Settings → *Deploy triggers*) até as variáveis existirem.
4. **Configurar DATABASE_URL** — no serviço da aplicação, *Variables → + New Variable →
   Add Reference* → `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (URL interna, sem custo
   de egress).
5. **Cadastrar env vars** — a tabela da seção ENV VARS abaixo (`ANARA_ENV`,
   `ANARA_SECRET_KEY`, `ANARA_BASE_URL`, `ANARA_MAIL_*`). Sem `ANARA_SECRET_KEY` o
   processo **não sobe** — é o comportamento certo.
6. **Configurar pre-deploy** — já vem do `railway.json`
   (`preDeployCommand: alembic upgrade head`). Confira em Settings → *Deploy* que aparece
   "Pre-deploy command: alembic upgrade head" e "Healthcheck path: /health". Se a migration
   falhar, o deploy **aborta** e a versão anterior continua no ar.
7. **Deploy** — *Deploy* (ou religue o trigger e faça um push). Acompanhe os logs: deve
   aparecer `alembic ... Running upgrade ... -> 0022` e depois
   `Uvicorn running on http://0.0.0.0:<porta>`.
8. **Gerar domínio temporário** — Settings → *Networking → Generate Domain*
   (`<algo>.up.railway.app`). Coloque esse endereço em `ANARA_BASE_URL` (com `https://`)
   e redeploy.
9. **Smoke test** — seção SMOKE TEST abaixo. Só depois dele migre os dados (seção
   MIGRAÇÃO) e refaça o smoke com os dados reais.
10. **Custom domain** — seção CUSTOM DOMAIN.

## POSTGRES

- O serviço PostgreSQL do Railway expõe duas URLs: **`DATABASE_URL`** (rede interna —
  use na aplicação) e **`DATABASE_PUBLIC_URL`** (TCP proxy — use só do seu Mac, para
  migrar os dados e para `pg_dump`).
- `postgres://` e `postgresql://` são aceitos; a aplicação normaliza para o driver
  psycopg 3. Uma senha com `%` funciona.
- O esquema nasce **só** de `alembic upgrade head` (pre-deploy). A aplicação não cria
  tabela no Postgres; se faltar coluna, o log da subida diz `esquema desatualizado … rode
  alembic upgrade head` e a causa é um deploy que pulou o pre-deploy.
- Enums (`cotacao.status`, `fornecedor.tipo`, `fornecedor.cost_method_padrao`) são
  `VARCHAR(64)` no Postgres (migration `0022`), iguais ao SQLite. Booleanos são `boolean`
  de verdade: `1`/`0` não existem mais em SQL cru.
- Chaves estrangeiras **são aplicadas** no Postgres (no SQLite nunca foram). O migrador
  valida todas antes de declarar sucesso.

## ENV VARS

| Variável | Obrigatória | Valor |
|---|---|---|
| `ANARA_ENV` | sim | `producao` |
| `ANARA_SECRET_KEY` | sim | 64+ caracteres gerados; valores de exemplo são recusados |
| `ANARA_BASE_URL` | sim | `https://<domínio>` — sem barra final; muda quando o domínio muda |
| `DATABASE_URL` | sim | referência `${{Postgres.DATABASE_URL}}` |
| `PORT` | injetada pelo Railway | não defina |
| `ANARA_MAIL_HOST` | para e-mail | host SMTP |
| `ANARA_MAIL_PORT` | para e-mail | `587` (STARTTLS) ou `465`/`25` conforme o provedor |
| `ANARA_MAIL_USER` | para e-mail | usuário SMTP |
| `ANARA_MAIL_PASSWORD` | para e-mail | senha SMTP |
| `ANARA_MAIL_FROM` | para e-mail | `Anara Cotações <cotacoes@dominio>` |
| `ANARA_MAIL_TLS` | não | `1` (padrão) ou `0` |
| `ANARA_DB_URL` | **não em produção** | só para apontar um processo local a outra base |
| `ANARA_MAIL_BACKEND` | **não em produção** | `memoria` é só da suíte de testes |

Nunca: `.env` no repositório, segredo em `railway.json`, segredo em log (a aplicação não
imprime valor de variável; `url_segura()` esconde a senha do banco).

## EMAIL

"Esqueci minha senha" e o link de primeiro acesso dependem de SMTP. Em produção **não
existe** backend de desenvolvimento: sem `ANARA_MAIL_HOST/PORT/USER/PASSWORD/FROM` o log
avisa `Recuperação de senha por e-mail NÃO está operacional` na subida, a tela responde a
frase genérica e **nada é enviado**. `/health/detalhe` (autenticado) mostra
`recuperacao_senha_por_email.operacional`.

Garantias que valem em produção: token de 30 min, uso único, só o hash no banco, um token
novo encerra os anteriores, redefinir derruba as sessões abertas, resposta idêntica para
e-mail existente e inexistente, link montado sobre `ANARA_BASE_URL` (https).

Teste após configurar: abra `/esqueci-senha` com o e-mail do OWNER, receba o e-mail,
redefina, entre com a senha nova. O envio é SMTP com **STARTTLS** (porta 587,
`ANARA_MAIL_TLS=1`); SSL implícito na 465 **não** é suportado — use 587. `ANARA_MAIL_TLS=0`
só faz sentido num relay interno sem TLS.

## MIGRAÇÃO

Dados: `data/anara.db` (SQLite) → PostgreSQL do Railway. Uma vez, no cutover, com o
servidor local **parado**.

```bash
# 1. parar o servidor local e fazer o backup FINAL do SQLite (fica para sempre)
pkill -f "uvicorn app.main:app"
python3 scripts/backup_banco.py backup --motivo cutover-postgres
cp data/anara.db ~/Anara-Cotacao-Backups/anara_cutover_$(date +%Y%m%d-%H%M).db
shasum -a 256 data/anara.db ~/Anara-Cotacao-Backups/anara_cutover_*.db

# 2. trabalhar numa CÓPIA e levá-la ao head (0022 é no-op no SQLite)
cp data/anara.db /tmp/anara_cutover.db
ANARA_DB_URL=sqlite:////tmp/anara_cutover.db python3 -m alembic upgrade head

# 3. o Postgres do Railway já está em head (pre-deploy do primeiro deploy). Confirme:
ANARA_DB_URL='<DATABASE_PUBLIC_URL>' python3 -m alembic current      # → 0022 (head)

# 4. carregar (recusa destino com dados; as 3 tabelas semeadas pelas migrations são substituídas)
python3 scripts/migrar_sqlite_para_postgres.py \
    --origem /tmp/anara_cutover.db --destino '<DATABASE_PUBLIC_URL>' \
    --relatorio ~/Anara-Cotacao-Backups/migracao_postgres_$(date +%Y%m%d-%H%M).json

# 5. repetir só a prova (idempotente)
python3 scripts/migrar_sqlite_para_postgres.py \
    --origem /tmp/anara_cutover.db --destino '<DATABASE_PUBLIC_URL>' --so-verificar
```

O script: preserva todos os `id`s, resolve o ciclo `cotacao ↔ oportunidade`, reposiciona
as sequences em `MAX(id)`, compara contagem, PKs e **cada coluna de cada linha**, valida
todas as FKs e só sai com `0` sem divergência. Ensaio completo em 17/09/2026: 1.231 linhas,
36 tabelas, 0 divergências, 0 órfãos. Se precisar recarregar: `--substituir-destino
<nome do banco>` (esvazia e recarrega na mesma transação). A origem nunca é alterada.

Usuários, senhas (hash argon2id), clientes, vendas, cotações, itens, snapshots,
aprovações, premissas versionadas, trilha de auditoria e tokens de senha migram juntos.
`alembic_version` não é copiada (o destino já tem a sua).

## PRIMEIRO DEPLOY

Ordem que evita retrabalho:

1. Variáveis cadastradas (ENV VARS) → 2. deploy (o pre-deploy cria o esquema no Postgres
vazio) → 3. domínio temporário em `ANARA_BASE_URL` → 4. smoke com o banco vazio: `/health`,
`/login` — sem conta alguma, `/primeiro-acesso` cria o primeiro OWNER **apenas se você
não for migrar os dados**; como vai migrar, **não crie conta aqui** → 5. MIGRAÇÃO → 6.
smoke com dados reais (login do OWNER com a senha atual, que migrou no hash) → 7. e-mail:
teste de "Esqueci minha senha" → 8. avisar a equipe.

Se o deploy falhar no pre-deploy, nada sobe: leia o log do Alembic, corrija localmente,
teste (`ANARA_TEST_DB_URL` contra um Postgres local), commit, push.

## SMOKE TEST

Ensaio local que reproduz o ambiente de produção (comando do `Procfile`, `ANARA_ENV=producao`,
cookie Secure, sem SMTP, OWNER e SELLER, PDF rascunho/final, URLs proibidas, log sem segredo):

```bash
python3 scripts/smoke_producao.py                       # cópia do SQLite
python3 scripts/smoke_producao.py --postgres postgresql://anara@127.0.0.1:55432/anara_smoke
```

No ar (manual, 10 minutos):

- [ ] `https://<domínio>/health` → `{"status":"ok","database":"ok"}`
- [ ] OWNER: login → **Dashboard**; Admin; Cotações → uma cotação → "Economia da proposta"
- [ ] OWNER: PDF de uma cotação **emitida** abre, sem faixa de rascunho, sem custo/margem
- [ ] SELLER: login → **Vendas**; digitar `/dashboard` volta para Vendas; `/admin`,
      `/configuracoes`, `/calculadora`, `/relatorios/economico` → página 403
- [ ] SELLER: uma cotação mostra "Sua comissão estimada" e nenhum custo/margem/piso
- [ ] Cadeado do navegador (HTTPS) e cookie `anara_session` com `Secure`
- [ ] `https://<domínio>/static/fonts/Didot.ttf` deslogado → 404; logado → a fonte
- [ ] "Esqueci minha senha" → e-mail chega → link `https://…/redefinir-senha?token=…`

## CUSTOM DOMAIN

1. Railway → serviço → Settings → *Networking → Custom Domain* → `cotacoes.<empresa>.com.br`.
2. No DNS do domínio, crie o **CNAME** que o Railway mostrar (apex sem CNAME: use `www`
   ou um subdomínio). Propagação: minutos a horas. TLS é automático (Let's Encrypt).
3. Troque `ANARA_BASE_URL` para o domínio novo e redeploy — os links de e-mail seguem a
   variável, não o `Host` do pedido.
4. Mantenha o domínio `.up.railway.app` só até confirmar o novo; depois remova.

## DEPLOY FUTURO

Rotina para qualquer mudança (código, template, migration):

1. Local: `python3 -m pytest -q` verde; se houver migration nova, também
   `ANARA_TEST_DB_URL=postgresql://…/anara_suite python3 -m pytest -q tests/test_producao_deploy.py`
   e `ANARA_DB_URL=postgresql://…/anara_teste python3 -m alembic upgrade head`.
2. `git commit` → `git push origin main`. O Railway constrói, roda `alembic upgrade head`
   e só então troca o processo. **Migration que falha = deploy que não acontece**; a
   versão anterior segue no ar.
3. Sem `--reload`, sem debug, sem editar dado à mão no Postgres. Ajuste de premissa é pela
   tela de Admin (versionado e auditado).
4. Migration só **aditiva** (coluna/tabela nova, nullable ou com default). Renomear ou
   apagar coluna exige duas releases (código tolerante primeiro, DDL depois).

## ROLLBACK

- **Código**: Railway → Deployments → deployment anterior → *Redeploy*. Instantâneo.
- **Migration aditiva** (o caso normal): o código antigo ignora a coluna nova; não
  precisa de `downgrade`.
- **Migration com dado transformado**: `alembic downgrade <rev>` só se o `downgrade()`
  daquela migration for confiável; senão, **restaure o `pg_dump`** feito antes do deploy
  (BACKUP/RESTORE) e redeploy do código anterior.
- **Cutover inteiro**: o SQLite final (`~/Anara-Cotacao-Backups/anara_cutover_*.db`) sobe
  de volta em `iniciar_plataforma.py` no Mac. Dados criados no Postgres depois do cutover
  ficam só lá — decida o ponto de retorno antes de voltar.

## BACKUP/RESTORE

**Antes de todo deploy com migration e diariamente**, do Mac (ou de um cron externo):

```bash
# dump lógico, comprimido, com carimbo — guarde fora do repositório
pg_dump '<DATABASE_PUBLIC_URL>' -Fc -f ~/Anara-Cotacao-Backups/pg/anara_$(date +%Y%m%d-%H%M).dump
shasum -a 256 ~/Anara-Cotacao-Backups/pg/anara_*.dump | tail -1
```

Ative também o backup do próprio Railway (Postgres → *Backups*: diário, retenção 7+ dias).
Um backup que nunca foi restaurado não é backup — **ensaie**:

```bash
# restaurar num banco de ensaio (nunca por cima da produção sem decisão explícita)
createdb -h 127.0.0.1 -p 55432 -U anara anara_ensaio
pg_restore -h 127.0.0.1 -p 55432 -U anara -d anara_ensaio --no-owner --no-privileges \
    ~/Anara-Cotacao-Backups/pg/anara_<carimbo>.dump
ANARA_DB_URL=postgresql://anara@127.0.0.1:55432/anara_ensaio python3 -m alembic current
```

Restore de verdade em produção: `pg_restore --clean --if-exists --no-owner --no-privileges
-d '<DATABASE_PUBLIC_URL>' arquivo.dump` com a aplicação **parada** (Railway → *Remove
deployment* ou réplicas 0), depois redeploy. O SQLite de origem é histórico:
`~/Anara-Cotacao-Backups/anara_cutover_*.db` + `BACKUP.md` para restaurá-lo localmente.

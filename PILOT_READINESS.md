# Anara — prontidão para o piloto

Escrito ao fim da Sessão 8. O objetivo aqui não é vender o sistema: é dizer com precisão o
que ele faz, o que ele não faz, e o que ainda não se deve confiar. Um documento de prontidão
que esconde pendência atrapalha mais do que ajuda.

**Manual completo virá depois.** Este é o mínimo para você abrir o sistema, usar de verdade e
descobrir o que precisa mudar.

---

## 1. Como subir o sistema

Cinco passos, com os comandos reais do projeto.

**1. Configurar o ambiente**

```bash
cp .env.example .env
```

Gere a chave de sessão e cole no `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Só `ANARA_SECRET_KEY` é realmente necessária. Sem ela, em desenvolvimento, o sistema sobe com
uma chave aleatória por processo — funciona, mas cada reinício derruba as sessões abertas.

**2. Fazer backup antes de qualquer coisa**

```bash
python3 scripts/backup_banco.py backup --motivo antes-do-piloto
```

**3. Aplicar as migrations**

```bash
python3 -m alembic upgrade head
```

Deve terminar em `0017`.

**4. Criar o primeiro OWNER** — ainda não existe nenhum usuário

```bash
python3 scripts/criar_usuario.py --email voce@anara.com.br --nome "Matias" --papel OWNER --gerencia-usuarios
```

A senha é digitada sem aparecer na tela. Não passe senha por argumento: ela ficaria no
histórico do shell e no `ps`.

Para se dar alçada de aprovação de descontos, rode depois:

```bash
python3 scripts/criar_usuario.py --email voce@anara.com.br --papel OWNER
```

(OWNER já aprova por padrão; a flag `can_approve_quotes` existe para separar ADMINs.)

**5. Subir o servidor**

```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8420 --reload
```

Abra **http://127.0.0.1:8420** — cai na tela de login.

> O ícone "Plataforma Anara" e o `iniciar_plataforma.py` continuam funcionando; o comando
> acima é o equivalente explícito.

**Conferir que está no ar**, de outro terminal:

```bash
curl -s http://127.0.0.1:8420/health
```

Deve responder `{"status":"ok","database":"ok"}`.

---

## 2. Como verificar tudo antes de confiar

```bash
python3 -m pytest -q
```
715 testes, todos passando.

```bash
python3 scripts/smoke_test.py
```
Sobe o servidor de verdade numa **cópia** do banco, faz login, percorre 23 rotas, executa dois
fluxos de ponta a ponta, gera PDF e confere que o banco de produção não foi tocado. 60
verificações. Ele **aborta** se não conseguir provar o isolamento — ver o B-21 abaixo.

```bash
python3 scripts/comparar_baseline_sessao3b.py
```
Prova que nenhum preço mudou.

---

## 3. O que está pronto

| Área | Situação |
|---|---|
| **Precificação** | Motor KTC industrial, nacionalização, Daune, Decor, fronha §18, waterfall completo |
| **Fiscal** | ICMS por item, origem fiscal por operação, DIFAL, FCP, 5 condições de pagamento |
| **Frete comercial** | TRANSAL, grupos logísticos, CF no numerador e RV no denominador |
| **Precisão** | `Decimal` no núcleo, `ROUND_HALF_UP`, reconciliação ao centavo, rateio sem perda |
| **Acesso** | Login por pessoa com argon2, 4 papéis, confidencialidade econômica no servidor |
| **Admin** | Versionamento de premissas com preview/apply, vigência futura, trilha de auditoria |
| **Workflow** | Aprovação por fingerprint, emissão imutável, revisões, compromisso firme |
| **CRM** | Clientes, contatos, oportunidades, pipeline, atividades, ganho/perda |
| **Relatórios** | Painel comercial, econômico, saúde operacional, CSV |

---

## 4. O que NÃO usar ainda

**Frete CIF.** As pendências abaixo estão abertas e o sistema **bloqueia** a emissão de
cotação CIF em vez de inventar um número. Para o piloto, use **FOB** — o frete fica com o
cliente e nada bloqueia.

| ID | Pendência | Quem responde |
|---|---|---|
| C-NEW-01 | ICMS da prestação: incluso ou gross-up? Três evidências não reconciliadas | TRANSAL |
| C-NEW-02 | O GRIS de 0,10% incide sempre? | TRANSAL |
| C-NEW-06 | O fiel depositário de 0,5% da NF incide quando? | TRANSAL |
| C-NEW-08 | Pedágio incide sobre peso real ou taxado? | TRANSAL |
| C-NEW-04 | Volume por SKU — cadastro ausente | Operação |
| Q-L | De onde Daune e Decor embarcam? | Você |
| C-NEW-03 | Passo Fundo-RS: região sem tarifa | TRANSAL |

**Custos em `A_COTAR` e `REVIEW_REQUIRED`.** O sistema não forma preço para eles e não deixa
emitir. Isso é o comportamento correto, não um defeito — mas significa que parte do catálogo
ainda não está cotável. Veja quais em **Saúde operacional**.

**121 SKUs com `REVIEW_REQUIRED` legado.** O rótulo antigo tem sentido diferente do canônico;
a reclassificação exige o gate de reconciliação e ainda não foi feita.

**WON não vira pedido.** Marcar uma oportunidade como ganha registra o resultado comercial e
nada mais: não existe PO, faturamento nem integração com ERP.

---

## 5. Pendências conhecidas

| ID | O quê | Impacto no piloto |
|---|---|---|
| **B-18** | A poda de `data/backups/` pode apagar o backup recém-criado, porque `copy2` preserva o mtime e o desempate cai na ordem do `listdir` | Baixo. Mitigação: os marcos ficam em `~/Anara-Cotacao-Backups/`, onde nada poda |
| **B-19** | `classificar_base.py` e `importar_fornecedores_nacionais.py` quebram com cenário fiscal bloqueado; `comparar_regressao.py` importa função removida na Onda 1 | Nenhum no uso normal — são scripts de manutenção |
| **B-20** | `GET /logout` muda estado via GET; um link de terceiro clicado derruba a sessão | Baixo. Não vaza dado; a pessoa loga de novo |
| **B-21** | `ANARA_DB_URL` não isolava a aplicação — só o Alembic a lia. **Corrigido nesta sessão** | Nenhum agora. Foi descoberto porque o smoke test escreveu 21 linhas no banco histórico; o banco foi restaurado do backup e as 18 cotações e 45 itens estavam byte a byte idênticos |

### Publicação remota: BLOQUEADA

A senha compartilhada saiu do código na Sessão 4, mas continua nos commits `413d6bd` e
`165d75e`. Enquanto ela estiver no histórico: **sem remote, sem push, sem GitHub**. Some-se
que `referencia/` versiona tabela de preço de fornecedor.

Liberar exige sanitização do histórico ou decisão explícita de que a credencial aposentada é
inócua. É assunto para **antes** da primeira publicação, não durante o piloto local.

---

## 6. Checklist antes do primeiro uso real

- [ ] `cp .env.example .env` e gerar a `ANARA_SECRET_KEY`
- [ ] `python3 scripts/backup_banco.py backup --motivo antes-do-piloto`
- [ ] `python3 -m alembic upgrade head` → termina em `0017`
- [ ] `python3 scripts/criar_usuario.py ...` → criar o OWNER
- [ ] `python3 -m pytest -q` → 715 passando
- [ ] `python3 scripts/smoke_test.py` → 60 ok, 0 falhas
- [ ] Subir o servidor e fazer login
- [ ] Abrir **Saúde operacional** e ver o que está bloqueado no catálogo
- [ ] Fazer **uma cotação FOB de teste** ponta a ponta e conferir o PDF
- [ ] Conferir que o vendedor **não** vê custo nem margem (entrar com um usuário
      `VENDEDOR_INTERNO` e olhar as mesmas telas)

---

## 7. Por onde começar a olhar

| Tela | Para quê |
|---|---|
| `/comercial` | O dia: atrasadas, para hoje, negócios sem próximo passo |
| `/pipeline` | O funil por etapa |
| `/cotacoes/nova` | Montar uma proposta |
| `/relatorios` | Painel comercial |
| `/saude` | O que está travado, e por quê |
| `/admin` | Trocar câmbio, custo de um SKU, margem — sempre com preview |
| `/aprovacoes` | Descontos aguardando decisão |

---

## 8. O que fazer com o que você encontrar

Este piloto existe para **simplificar**. Se uma tela pedir informação demais, se um caminho
tiver cliques a mais, se um termo não for o que você usa no dia a dia — anote. A camada
técnica está sólida e testada; a interface é V1 e foi feita para mudar depois que você usar.

O que **não** deve mudar sem uma sessão própria: as regras econômicas, fiscais e de
aprovação. Elas têm baseline, testes de regressão e trilha, e cada mudança nelas é medida
contra o histórico.

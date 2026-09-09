# Anara — prontidão para o piloto

Atualizado em 09/09/2026, ao fim do Product Cleanup. O objetivo não é vender o sistema: é
dizer com precisão o que ele faz, o que não faz, e o que ainda não se deve confiar. Um
documento de prontidão que esconde pendência atrapalha mais do que ajuda.

---

## Como subir

```bash
cd ~/Anara-Cotacao
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8420 --reload
```

Abra **http://127.0.0.1:8420**. Sem nenhuma conta no banco, a tela de login leva ao
**primeiro acesso**, onde o primeiro OWNER é criado pelo navegador. Depois disso, usuários se
gerenciam em **Admin → Usuários** — o terminal deixou de ser necessário para isso.

Conferir que está no ar:

```bash
curl -s http://127.0.0.1:8420/health
```

---

## O QUE CONFIAR

### Precificação

| | |
|---|---|
| Motor KTC industrial | tecido, CMT, waste, encolhimento, 2ª qualidade, margem KTC |
| Nacionalização | frete internacional, I.I., outras despesas, câmbio |
| Fornecedor nacional | bruto → créditos de compra → CNET |
| Precisão | `Decimal` no núcleo, `ROUND_HALF_UP`, reconciliação ao centavo |
| **Premissa vigente** | **precificação nova sempre usa a versão vigente** — corrigido nesta rodada |
| Histórico | cotação emitida nunca muda; snapshot e pinos congelados |

**Prova viva:** trocar o câmbio em Admin → Premissas e criar uma cotação nova resulta em
preço diferente, com `custo_unitario`, `memoria_json` e `premissas_pinadas` **coerentes entre
si**. Cotação anterior não se mexe.

### Fiscal

ICMS por item, origem fiscal por operação, DIFAL, FCP por regra cadastrada, 5 condições de
pagamento. Origem fiscal e origem logística são conceitos separados e nomeados por extenso na
tela.

### Comercial

CRM com clientes, contatos, oportunidades, pipeline e atividades. Cotação com workflow
completo: rascunho → aprovação → emissão → envio, com revisões e genealogia. Aprovação por
fingerprint. PDF de rascunho marcado.

### Acesso

Login por pessoa com argon2id. Quatro papéis e três permissões granulares, independentes
entre si. Vendedor não vê custo, margem, lucro nem markup — **na resposta da API**, não só na
tela.

### Premissas vigentes hoje

| Premissa | Valor | Vigente desde |
|---|---|---|
| Câmbio USD → BRL | **R$ 5,19** | 08/09/2026 |
| Frete internacional | US$ 0,516 /kg | 28/08/2026 |
| Outras despesas de importação | US$ 0,2488 /un | 28/08/2026 |
| PIS/COFINS | 7,59% | 28/08/2026 |

> Estes são os **valores vigentes**, não exemplos. Quando o dólar mudar, esta tabela fica
> desatualizada — a fonte de verdade é sempre Admin → Premissas.

---

## O QUE AINDA ESTÁ PENDENTE

### Frete CIF — use FOB no piloto

O sistema **bloqueia** a emissão CIF em vez de inventar um número. Quatro perguntas para a
TRANSAL, três delas sobre o mesmo termo da equação:

| ID | Pergunta | Peso |
|---|---|---|
| **C-NEW-01** | O ICMS da prestação já está incluso, ou o valor sofre gross-up de ÷(1−12%)? | **P0** |
| **C-NEW-06** | A taxa de fiel depositário de 0,5% da NF incide sempre? | **P0** |
| C-NEW-02 | O GRIS de 0,10% incide sempre? | P1 |
| C-NEW-08 | O pedágio incide sobre peso real ou taxado? | P1 |

Composição hoje: ADV 0,20% (confirmado) + GRIS 0,10% (aberto) + fiel depositário 0,5%
(aberto). Se o fiel depositário incidir sempre, a taxa variável salta de 0,30% para 0,80%.

**Mais duas, de outra natureza:**

- **C-NEW-03** — Passo Fundo-RS existe na tabela sem tarifa, mínimo ou prazo. Cidade fora das
  238 cobertas fica a cotar, sem aproximar por região vizinha.
- **C-NEW-07** — a tabela TRANSAL **vence em 31/12/2026**. Vencida sem substituta, bloqueia.

### Origem logística de Daune e Decor — Q-L

Ambas embarcam de **São Paulo**. A única tabela de frete cadastrada tem origem **Itajaí-SC**.
Não existe tarifa saindo de São Paulo, então o CIF desses dois fornecedores fica a cotar — e o
sistema está certo em travar.

Destravar exige uma tabela de frete com origem São Paulo, de qualquer transportadora.

### 45 SKUs sem custo

De 340 ativos. Sem custo não há o que derivar; os outros 295 cotam normalmente.

| Fornecedor | SKUs | | Família | SKUs |
|---|---|---|---|---|
| Kazareen (KTC) | 29 | | Duvet Insert | 14 |
| Daune | 15 | | Fitted Sheet · Pool Towel · Bathrobe | 18 |
| Decor Tricot | 1 | | Duvet Cover · Pillow Case | 8 |

### Outras pendências registradas

| ID | O quê | Impacto no piloto |
|---|---|---|
| **B-17** | Duas fontes Daune com bases diferentes — razão constante de 1,5123 entre os documentos | Confirmar com a Daune qual é a base da planilha Trousseau |
| **B-16** | Gramatura não estruturada: 28 de 31 SKUs de Duvet Insert sem `gsm` | Casamento por campos estruturados impossível nessa família |
| **B-13** | 21 divergências entre `models.py` e o esquema real do banco | Nenhum no uso normal |
| **B-19** | Dois scripts de manutenção quebram com cenário fiscal bloqueado | Nenhum no uso normal |
| **B-20** | `GET /logout` muda estado via GET | Baixo: a pessoa loga de novo |
| **B-22** | `scripts/backup_banco.py` não aplica o limite de retenção | **Não bloqueante.** O diretório cresce; a poda do `app/migrations.py` funciona |
| **C-NEW-04** | Volume por SKU ausente | Só importa no CIF, quando peso real e taxado divergem |

**B-18 está resolvido** — a poda deixou de apagar o backup recém-criado.

### 121 SKUs com `REVIEW_REQUIRED` legado

O rótulo antigo tem sentido diferente do canônico. A reclassificação exige o gate de
reconciliação e não foi feita.

### WON não vira pedido

Marcar uma oportunidade como ganha registra o resultado comercial e nada mais. Não existe PO,
faturamento nem integração com ERP.

---

## Publicação remota: BLOQUEADA

Uma senha compartilhada saiu do código na Sessão 4, mas continua nos commits `413d6bd` e
`165d75e`. Além disso, `referencia/` versiona tabela de preço de fornecedor.

**Piloto local: permitido. Push e publicação: bloqueados** até o histórico ser sanitizado ou
haver decisão explícita de que a credencial aposentada é inócua.

---

## Antes do primeiro uso real

- [ ] Fazer backup: `python3 scripts/backup_banco.py backup --motivo antes-do-piloto`
- [ ] Subir o servidor e entrar
- [ ] Abrir **Admin → Saúde do sistema** e ver o que está travado
- [ ] Fazer **uma cotação FOB de teste** ponta a ponta e conferir o PDF
- [ ] Criar um usuário `VENDEDOR_INTERNO` em Admin → Usuários e conferir que ele **não** vê
      custo nem margem nas mesmas telas
- [ ] Mandar as 4 perguntas para a TRANSAL
- [ ] Pedir cotação de frete com origem São Paulo

---

## Por onde começar

| Tela | Para quê |
|---|---|
| **Meu dia** | O que precisa de atenção agora |
| **Pipeline** | O funil por etapa |
| **Cotações → Nova** | Montar uma proposta em cinco campos |
| **Relatórios** | Comercial, econômico, cotações, aprovações |
| **Admin** | Premissas, catálogo, usuários, auditoria, saúde |

## O que fazer com o que você encontrar

Este piloto existe para simplificar. Se uma tela pedir informação demais, se um caminho tiver
cliques a mais, se um termo não for o que você usa no dia a dia — anote.

O que **não** deve mudar sem uma sessão própria: as regras econômicas, fiscais e de aprovação.
Elas têm baseline, testes de regressão e trilha, e cada mudança nelas é medida contra o
histórico.

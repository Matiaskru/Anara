# Product Cleanup — relatório

Rodada de limpeza de produto executada em 08–09/09/2026, em três lotes, sobre o HEAD
aprovado da Sessão 8 (`6600f68`).

O escopo era transformar funcionalidade existente em produto utilizável: navegação,
apresentação, entrada de dados, tratamento de erro e linguagem. **A economia ficou
congelada** — e ficou, com uma exceção autorizada que este documento explica.

| | Antes | Depois |
|---|---|---|
| Testes | 715 | **895** |
| Tempo da suíte | ~62 s | 60 s |
| Baseline econômico | 5.900 / 4.540 / 0 | **5.900 / 4.540 / 0** |
| Mudanças no histórico | 0 | **0** |
| Migrations | — | **nenhuma** |

---

## 1. Antes → depois

| O que era | O que é |
|---|---|
| Nova Cotação com **18 campos** | **5 campos**: cliente, contato, destino, condição, frete |
| Menu lateral com **15 linhas** | **8**, por tarefa |
| `/admin` e `/configuracoes` como destinos concorrentes | **Admin único**, com 13 áreas; configurações viraram sub-rota |
| Clique em Gerar PDF terminando em `{"erro": ...}` | **Página de erro em português**, com o que resolver e o caminho de volta |
| `REVIEW_REQUIRED` como texto de tela | "Revisão necessária" — o código continua no banco e na trilha |
| Dólar atualizado e cotação nova com custo velho | **Custo resolvido pelas premissas vigentes** a cada precificação |
| Botão que gravava estado legado sem volta | Estados herdados são **somente leitura histórica** |
| "Origem da venda", genérica e ambígua | **Origem fiscal da NF** e **Origem logística do embarque**, separadas |
| Campo novo na tela com preço do cenário anterior | **"Há alterações ainda não aplicadas ao cálculo"** + ações de saída inertes |
| Usuários só por `scripts/criar_usuario.py` | **Gestão mínima em `/admin/usuarios`** |
| Poda de backup apagando o backup recém-criado | Ordenação pelo carimbo do nome + preservação explícita |
| Fila de aprovações oferecida a quem vê economia | Oferecida a quem **tem alçada** |
| Premissa nova sem aviso no rascunho | **"Existem premissas mais recentes disponíveis"** + manter / atualizar |
| "Relatório econômico" como botão solto | **Relatórios** com quatro abas |
| Tela de saúde com lista plana de códigos | **Agrupada por assunto**, descrição humana antes do código |

---

## 2. Bugs reais descobertos nesta rodada

### HARD BLOCKER — a alteração do câmbio não chegava ao preço

**O pior achado da rodada.** `Produto.custo_unitario` é coluna persistida e não é recalculada
quando uma premissa versionada muda. Os caminhos que formavam preço liam a coluna, então uma
cotação criada **depois** de o dólar mudar saía com o custo de antes.

O item se contradizia: preço formado com R$ 5,11, `memoria_json` recalculado com R$ 5,19 e
`premissas_pinadas` apontando para a versão 5,19. O documento afirmava ter sido formado com um
câmbio que não formou o preço dele.

**Alcance:** 170 de 241 SKUs KTC com custo persistido estavam defasados. Prova numérica no
SKU *Lençol plano hotel 160x310 · 300 fios*:

```
                    FX 5,11      FX 5,19
EXW US$             10,6289      10,6289   ← igual
NET USD             11,6666      11,6666   ← igual
CNET BRL            59,6165      60,5498   ← muda
preço recomendado   119,69       121,56    ← muda

delta preço  +R$ 1,87  (+1,562%)
razão cambial          (+1,566%)
```

**Correção (Caminho A, autorizado):** `ps.custo_para_precificar()` resolve o custo pelas
premissas vigentes; os quatro caminhos que formam preço passaram a usá-lo. Fornecedor
nacional e SKU KTC sem EXW continuam idênticos — `custo_net()` já devolvia exatamente a
coluna para esses dois casos.

### Bypass semântico no status de custo

`CotacaoItem.status_custo_item` recebia dois vocabulários: `StatusCusto`
(`CONFIRMADO`, `A_COTAR`…) e `CostConfidence` (`CALCULATED`, `QUOTED`…). Os portões do
workflow só entendem o primeiro.

**210 SKUs ativos** com `QUOTED` ou `CALCULATED` produziam um status que não bloqueava nem
avisava. Que os 121 com `REVIEW_REQUIRED` bloqueassem era **coincidência de string** — o mesmo
texto existe nos dois vocabulários.

Corrigido com `ps.status_canonico_do_custo()`, que deriva o status de **como o custo foi
resolvido**. O método continua registrado em `cost_method`, `custo_confianca` e na memória.

### Três caminhos para estado legado

`fechada`, `pedido` e `perdida` são estados herdados sem transição de saída. Havia três portas
para dentro deles, e todas foram usadas no primeiro dia de uso real:

1. `POST /aceite` com `virar_pedido=sim` — o botão "Salvar e virar pedido"
2. `POST /status` aceitando qualquer valor de `StatusCotacao` sem consultar o workflow
3. A tela renderizando `StatusCotacao` inteiro como botões

A cotação `ANARA-2026-0019` caiu em `pedido` por causa disso — sem aprovação, sem emissão,
sem snapshot e sem volta. Arquivada como evidência, não apagada.

### B-18 — a poda apagando o backup recém-criado

Deixou de ser teórico nesta rodada: o diretório passou de 30 arquivos quando o backup de
segurança da sessão foi criado, e **o backup de segurança sumiu**.

`shutil.copy2` preserva o mtime da origem; como todo backup é cópia do mesmo `anara.db`, todos
ficavam com mtime idêntico e o desempate caía na ordem arbitrária de `os.listdir`. Corrigido
ordenando pelo carimbo do nome, com o recém-criado explicitamente preservado.

### `estado_origem` reescrito a cada salvamento

`Form("São Paulo")` na rota contra `default="Santa Catarina"` no modelo. O default da rota
vencia em toda criação **e em todo salvamento**, reescrevendo silenciosamente o campo. E o
motor fiscal nunca usou esse campo — ele resolve por `uf_origem_fiscal`.

### Duplo clique gerando conflito de versão

A trilha registrou: alteração do câmbio gravada às 23:11:14, segundo envio 1,7 s depois com o
token do estado anterior, recusado com `CONFLITO`. Do lado de quem clicou, um erro logo depois
de uma alteração bem-sucedida. O botão agora desliga no primeiro clique.

### Defeitos de teste corrigidos

| Teste | Problema | Correção |
|---|---|---|
| Guardiões de histórico (4) | Ancorados em `len(cotacoes) == 18` — toda cotação real quebrava a suíte | Ancorados na **identidade** do conjunto herdado, via `relatorios/baseline_fase0.json` |
| `test_migration_e_idempotente` | Comparava ponte **armazenada** contra **reconstruída**; e exigia que `criado_em` diferisse | Duas reconstruções frescas; `criado_em` diferente passou a ser aceitável, não obrigatório |
| `test_backup_b18.py` (v1) | Monkeypatch de globais do módulo — rodar com `test_fundacao` levava **65 minutos** | Parâmetros `pasta`/`maximo`; **53,55 s** para os dois juntos |
| `test_aceite_vira_pedido` | Afirmava o bypass como comportamento correto | Reescrito: aceite registra sem mover a cotação |
| `test_duplicar_reconfere_custo` | Verificava que o custo **não** era reconferido | Compara contra `custo_para_precificar` |
| `test_e2e_venda_normal` | `session.add` com id fixo derrubava a sessão inteira | `session.merge` |
| Comparador de baseline | Classificava mudança cambial legítima como `NAO_EXPLICADA` | Classe `PREMISSA_CAMBIAL_NOVA`, aceita **só** na razão cambial exata |

---

## 3. Dados

**Cotação `ANARA-2026-0019` arquivada**, não apagada. Evidência preservada: status `pedido`,
3 itens, `observacoes_pedido = "teste teste teste"`, criada manualmente em 05/09.

**Nada foi removido do banco.** 19 cotações, 48 itens, 340 SKUs, 1 usuário, 1 cliente.
Zero dados automatizados de teste.

**35 backups reais preservados** (37,6 MB). Cinco seriam candidatos à poda pela regra nova;
nenhum foi removido.

---

## 4. Deliberadamente não alterado

- Fórmulas de pricing, CNET, KTC, Daune, Decor, nacionalização
- Fiscal: ICMS, DIFAL, FCP, PIS/COFINS
- Comissão, margem, condições de pagamento
- `Decimal`, arredondamento, `dinheiro()`
- Versionamento, pinning, fingerprint, genealogia, snapshots
- `Produto.custo_unitario` — coluna preservada, sem job de sincronização em massa
- Os 121 SKUs com `REVIEW_REQUIRED` legado — reclassificar exige gate de reconciliação
- `scripts/backup_banco.py` — não poda; registrado como **B-22**, não bloqueante

---

## 5. Pendências abertas

Ver `PILOT_READINESS.md` para a lista com impacto no piloto.

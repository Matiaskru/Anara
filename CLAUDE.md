# Sistema Anara — instruções operacionais

Plataforma comercial e de precificação da Anara (enxoval hoteleiro). Fornecedores: **KTC**
(Egito, EXW, importado), **Daune** e **Decor Tricot** (nacionais).

**Antes de qualquer trabalho neste repositório, leia:**
- `AUDIT_ANARA_MASTER.md` — auditoria read-only do código atual, matriz de bugs e pendências
- `IMPLEMENTATION_PLAN_ANARA.md` — Fase 0 + Ondas 1 a 8, com escopo, testes e risco de cada uma
- `ANARA_EXECUTION_STATE.md` — **estado atual e o que está autorizado agora**
- SUPER PROMPT v2 (`referencia/SUPER_PROMPT_ANARA_v2.txt`) — é a
  fonte de verdade de negócio. Não copie o conteúdo dele para cá; consulte

## Stack

Python 3.12 · FastAPI · SQLModel/SQLAlchemy · SQLite (Postgres previsto para produção) ·
Jinja2 · ReportLab · openpyxl · pytest. ~8.800 linhas, 56 módulos, 14 templates, 173 testes.

## Princípios inegociáveis

1. **O cálculo é determinístico.** Motores puros (`ktc_engine`, `nationalization`,
   `pricing_engine`, `fiscal_rules`, `margin_rules`, `payment_terms`, `peso`) não importam
   FastAPI, Jinja nem banco. Regra financeira não mora na UI. IA não participa da matemática.
2. **Não invente premissa financeira ou fiscal.** Se faltar dado, é `REVIEW_REQUIRED` ou
   `A_COTAR` — nunca um número plausível. Fallback silencioso é proibido: ICMS chutado em 18% e
   I.I. = 0 são bugs conhecidos a corrigir, não comportamento aceitável.
3. **Snapshot e histórico são intocáveis.** Cotação emitida não muda quando a premissa muda.
   Premissa nova fecha a versão anterior (`valid_to`), não sobrescreve. `BaseImportacao` e
   snapshots antigos são preservados. Banco nunca é resetado; exclusão exige arquivamento antes
   e faz backup.
4. **Toda referência de custo responde "de onde veio este número?"** — método, valor, fonte,
   documento, data, validade, usuário, versão, confiança.

## Estados de confiança

São **cinco**, e a diferença entre eles é *de onde veio o número*, não quanto ele parece bom.

| Estado | De onde vem | Pode cotar? | PDF? | Pedido/WON? |
|---|---|---|---|---|
| CONFIRMADO | referência direta, atual e confiável | sim | sim | sim |
| ESTIMADO | **proxy**: curva, análogo forte, interpolação documentada | sim | sim | **não** enquanto `confirmation_pending = true` |
| REVALIDAR | referência **direta** que envelheceu, venceu ou tem anomalia | sim, **com alerta** | sim | **não** antes de reconfirmar |
| A_COTAR | não existe base segura | **não** gera preço automático | **não** | não |
| REVIEW_REQUIRED | problema crítico de premissa, fiscal, rastreabilidade ou dado | bloqueia conclusão | **não** | não |

As três distinções que mais custam caro se forem confundidas:

- **REVALIDAR ≠ ESTIMADO** — o REVALIDAR tem número próprio, direto; o ESTIMADO veio de proxy.
- **REVALIDAR ≠ A_COTAR** — o REVALIDAR já tem número utilizável; o A_COTAR não tem nenhum.
- **REVALIDAR ≠ REVIEW_REQUIRED** — envelhecer não é erro de cálculo.

**ESTIMADO nunca é promovido a CONFIRMADO em silêncio.**

`FRETE_A_COTAR` e `FRETE_REVIEW_REQUIRED` bloqueiam o PDF quando o frete é CIF. `A_COMBINAR` é
termo comercial deliberado e **não** bloqueia.

> **Atenção ao legado.** Os 121 SKUs hoje marcados `REVIEW_REQUIRED` e os 156 com
> `precisa_revisao` carregam o sentido **antigo** do campo. `legacy REVIEW_REQUIRED ≠
> REVIEW_REQUIRED canônico`. Reclassificar exige o gate de reconciliação da Onda 1 — nunca
> conversão automática.

## Papéis

- **OWNER** — acesso total, não pode ser removido acidentalmente
- **ADMIN** — custos, margens, câmbio, premissas, fiscal, fretes, aprovações, relatórios.
  Gerenciar usuários é permissão granular (`can_manage_users`): gerente sim, administrativo não
- **VENDEDOR_INTERNO** — todos os clientes e cotações; cria e edita; gera PDF quando permitido
- **VENDEDOR_COMISSIONADO** — só a própria carteira

**Vendedor não vê custo, EXW, custo NET, margem interna, markup, impostos detalhados nem
premissas** — e isso vale na **resposta da API**, não só na tela. Vendedor não edita margem alvo
nem markup. Permissão é verificada no backend; URL/API sem permissão → **403**.

## Acesso, papéis e confidencialidade (Sessão 4)

**A autorização é do backend. Esconder campo no HTML não é autorização** — quem recebeu o
payload lê no devtools. Toda decisão mora em `app/permissoes.py` e `app/confidencial.py`, e é
aplicada **antes** de a resposta ser montada.

- **Autenticação:** e-mail + senha por pessoa, hash **argon2id** em `Usuario.senha_hash`.
  A senha compartilhada acabou. Bootstrap é `scripts/criar_usuario.py`, com a senha vindo de
  `ANARA_SENHA_BOOTSTRAP` ou digitada sem eco — **nunca** de argumento nem do código
- **`ANARA_SECRET_KEY` é obrigatória.** Sem ela e com `ANARA_ENV=producao`, o processo **não
  sobe**; em desenvolvimento, gera chave aleatória por processo. Nunca um default conhecido
- **O cookie carrega só `id` e `versao`** — o papel é lido do banco a cada request.
  `HttpOnly`, `SameSite=lax`, `Secure` em produção, 12 h. `sessao_versao` invalida os cookies
  abertos quando a senha muda ou a conta é desativada
- **Endpoint que existe só para expor economia é NEGADO** (403) ao vendedor, não filtrado:
  memória do preço do item e do produto, configurações, calculadora, importação, relatórios.
  Endpoint **comercial** é filtrado, não negado — o vendedor precisa dele para cotar
- **A lista é de permissão, não de bloqueio.** `CAMPOS_ITEM_COMERCIAL` declara o que pode
  passar; campo novo não vaza por esquecimento
- **O PDF comercial não leva custo, CNET, margem, lucro, markup, comissão nem fornecedor** —
  e há teste que falha se levar

Ao criar rota nova: se ela devolve número econômico, o corte é `conforme_papel(...)` ou
`exigir_economia(request)`. Rota que só um administrador deve abrir usa `exigir_admin(request)`.

## Aprovação

Qualquer preço negociado **abaixo** do recomendado exige aprovação administrativa, mesmo que a
margem final continue boa — a autonomia de desconto do vendedor é zero. Preço acima do
recomendado é livre, com recálculo de comissão. Alteração posterior invalida a aprovação
anterior. Enquanto aguarda aprovação: salva como rascunho, **não** gera PDF final.

## Armadilhas de cálculo que já custaram retrabalho

- **Waste divide:** `consumo / (1 − waste)`. Nunca `× (1 + waste)`
- **Margem KTC é sobre o preço final:** `custo / (1 − 0,15)`. Não é `custo × 1,15`
- O **"II 1%"** da planilha industrial da KTC é perda de 2ª qualidade, **não** Imposto de
  Importação
- **Margem líquida ≠ markup**; **margem KTC ≠ margem Anara**
- **Peso real da KTC nunca** é substituído por estimativa
- **Carga final do DIFAL entra como está** — não recalcular por base simples/dupla/FEM
- **Contribuinte não se infere pelo estado**, e contribuinte ≠ consumidor final
- **Origem fiscal é atributo da operação/NF, não do fornecedor.** Origem logística ≠ origem
  fiscal. Itajaí-SC ser o ponto de entrada da KTC não prova a origem fiscal da venda. Sem
  evidência da origem, é `REVIEW_REQUIRED` — nunca um default
- **Condição de pagamento desconhecida não se interpola.** Não contar barras, não somar 1,6% por
  parcela, não devolver como confirmada. Exige condição cadastrada ou override autorizado
- **Preço bruto de fornecedor nacional não é preço de venda.** Bruto → créditos → CUSTO NET →
  fiscal, financeiro, comissão, frete → margem → preço recomendado
- Taxa por kg de toalha **já é EXW final** — não aplicar CMT, 2ª qualidade nem margem por cima
- Casamento de produto é por **campos estruturados**, nunca por nome
- **Não fazer matemática monetária em `float`** e converter no fim: o erro já entrou. E
  **não usar `round()`** para dinheiro — a régua é `dinheiro()`

## Precisão monetária — uma régua só (Sessão 3B)

**Todo o núcleo econômico é `Decimal`, e a política mora em `app/dinheiro.py`.** Não existe
segunda régua: `pricing_engine`, `frete_engine`, `ktc_engine`, `nationalization`,
`fiscal_rules`, `custo_service`, os serviços, os templates e o PDF importam dali.

- **Nunca `Decimal(float)`.** `Decimal(0.1)` congela o erro binário. Use `D()`, que converte
  pela representação **textual** do float. `D(0.18) == Decimal("0.18")`
- **Precisão interna de 34 dígitos**, declarada em `PRECISAO_INTERNA`. Nada é quantizado no
  meio da cadeia: EXW, câmbio, consumo de tecido, taxas e divisões trabalham cheios
- **Dinheiro comercial: 2 casas, `ROUND_HALF_UP`** — `dinheiro()`. `1,005 → 1,01`. O `round()`
  do Python faz banker's rounding sobre binário e devolve 1,0; não serve
- **A quantização acontece UMA vez**, quando o preço vira preço: forma-se o preço preciso,
  arredonda-se, e **todos os componentes são recompostos sobre o preço arredondado**
- **`margem_alvo` ≠ `margem_liquida`.** Pedir 14% e cobrar R$ 377,12 entrega 13,9998%, e é
  isso que a memória registra. Nunca reportar a margem teórica como se fosse a real
- **Lucro é resíduo** da receita menos os componentes já quantizados — é o que faz a linha
  fechar ao centavo sem "aproximadamente"
- **Total da linha = preço unitário comercial × quantidade**, quantizado. Nunca um total
  teórico próprio, que divergiria do que o cliente confere
- **Rateio pelo maior resto** (`ratear_centavos`), com desempate pela ordem canônica:
  R$ 100,00 entre 3 itens dá 33,34 + 33,33 + 33,33, e não 99,99
- **Custo NET não é quantizado** — é custo interno. Só o preço comercial vira centavo

**Persistência: as colunas continuam `REAL`, e isso foi medido.** No SQLite,
`Numeric(18,2)` do SQLAlchemy vira afinidade REAL igual a `Float` e ainda **trunca na
leitura**: `34.71540940423179` volta `34.72` e a alíquota `0.0759` volta `0.08`. Como o
histórico guarda preços com a precisão cheia do float, migrar reescreveria cotações emitidas.
A ponte é `D()` na entrada e `para_float()` na saída — exata nos dois sentidos para quantia já
quantizada. **Não criar migration `Numeric` sem reabrir essa medição.**

## Como trabalhar neste projeto

1. **Uma etapa por autorização.** Nunca encadeie ondas. Terminou a etapa autorizada, **pare**.
2. Antes de cada onda: baseline. Depois: suíte completa + relatório do que mudou de número e
   por qual regra. Nada é publicado sem esse relatório.
3. Bug corrigido ganha teste de regressão. Regra crítica ganha teste que falha se for quebrada.
4. Discrepância nova vira ID novo no `AUDIT_ANARA_MASTER.md`. Nunca resolver em silêncio.
5. Decisão sem impacto econômico/fiscal/comercial: escolha a mais simples e documente. Decisão
   com impacto: registre como pergunta aberta e implemente o resto.

## Estado atual (04/09/2026)

Aprovadas e persistidas: **Fase 0**, **Sessão 0.1**, **Sessão 1** (fiscal por item, DIFAL,
condições de pagamento), **Sessão 2** (custo versionado por SKU, Daune, fronha, edredom 280 g) e
**Sessão 3A** (frete comercial TRANSAL, grupos logísticos, CF/RV no waterfall).

**Sessão 3B — Decimal e reconciliação monetária: EXECUTADA, aguardando auditoria.**
Alembic segue em `0009` — nenhuma migration, pelo motivo medido na seção de precisão acima.

As pendências de frete (ICMS da prestação, GRIS, fiel depositário, base do pedágio, volume por
SKU, origem logística de Daune e Decor) continuam **congeladas e fora de escopo**.

**Sessão 4 — segurança, papéis e confidencialidade: EXECUTADA, aguardando auditoria.**
Alembic em `0010` (tabela `usuario`, aditiva).

O repositório é Git **local**. A senha compartilhada **saiu do código** na Sessão 4, mas
continua nos commits `413d6bd` e `165d75e`. **Publicação remota segue bloqueada** até o
histórico ser sanitizado ou haver decisão explícita de que a credencial aposentada é inócua —
e, de todo modo, `referencia/` versiona tabela de preço de fornecedor. Sem remote, sem push.

Antes de qualquer sessão: o procedimento de `BACKUP.md`. Depois: comparar contra
`relatorios/baseline_fase0.json`, que continua sendo o baseline imutável.

Consulte `ANARA_EXECUTION_STATE.md` antes de agir.

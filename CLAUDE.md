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

## Administração de premissas (Sessão 5)

**Atualizar não é editar histórico.** Nada em `app/admin_service.py` faz
`UPDATE valor = novo`: a versão antiga fica com sua fonte e sua data, uma versão nova nasce,
e **a data decide** qual delas o próximo cálculo usa.

- **Escopo declarado e conferido.** Mexer no SKU X não toca no SKU Y, na família nem no
  fornecedor. O preview mostra quantos SKUs a mudança alcança — é o que impede confundir
  "ajustei um item" com "ajustei o fornecedor inteiro"
- **`preview → aplicar`, sempre.** O preview devolve um **token** com o hash do estado
  observado; o apply recomputa e recusa se algo mudou nesse meio-tempo (`ConflitoDeVersao`).
  O navegador devolve o token, nunca os valores — esses são recalculados no servidor
- **Vigência futura funciona.** `referencia_vigente`, `resolver_margem` e `resolver_encargo`
  resolvem **por data**. Antes da Sessão 5 os três ignoravam `valid_from`, e cadastrar algo
  para 2027 mudava o preço no mesmo instante
- **NO_OP ≠ RECONFIRMAÇÃO.** A comparação é pela **identidade econômica** completa —
  `cs.identidade_economica()`: valor, bruto, status **e** evidência (fonte, documento, data).
  Mesmo preço com fonte nova é **reconfirmação**: vira versão, porque "o fornecedor
  confirmou em 05/09 que continua 100" é informação econômica e some se virar NO_OP. Só
  diferença de escrita ("100" × "100,00", " Fonte A " × "fonte a") é no-op de verdade
- **Casamento inequívoco ou nada.** SKU exato, ou campos estruturados suficientes. Ambíguo →
  `REVIEW_REQUIRED`; inexistente → `SKU_NAO_ENCONTRADO`. As duas coisas são diferentes, e
  fuzzy match de custo econômico é errar o preço com convicção
- **A cotação fica presa à versão EXATA.** `CotacaoItem` pina `custo_referencia_id`,
  `custo_referencia_versao`, `margem_regra_id`, `condicao_pagamento_id`,
  `aliquota_interestadual_id` e `premissas_pinadas`. Guardar o valor protege o dinheiro;
  guardar o **id** protege a genealogia — sem ele, uma versão cadastrada depois com vigência
  retroativa mudaria a resposta de "qual versão formou este preço". **Nunca reconstruir a
  autoria de uma cotação por `referencia_em(data)`**
- **Rascunho não atualiza sozinho.** `premissas_desatualizadas()` **detecta e só detecta**
- **Delete físico só no que nunca foi usado.** Referência que já participou de cotação
  encerra vigência; não some. Voltar ao valor antigo é criar V3, não apagar V2
- **`can_manage_economics`** separa ver a economia de poder alterá-la. `can_manage_users`
  não concede isso — gerir gente não é gerir número. OWNER sempre pode
- **Trilha em `AuditLog`:** ator, papel, ação, escopo, antes, depois, motivo, origem,
  resultado e correlação do lote. Nunca senha, hash, cookie ou segredo
- Lista **fechada** de premissas editáveis pela tela. Formulário genérico sobre `chave`
  deixaria alguém cadastrar "icms = banana" e achar que configurou algo

## Aprovação e política comercial (Fase 3A — 16/09/2026)

**A autonomia de desconto deixou de ser zero em 16/09/2026.** A política comercial canônica
(`app/politica_comercial.py`, `app/comercial_service.py`, `MargemRegra` com política) dá à
vendedora um **piso de margem** por item, congelado no item (`piso_margem_pct`):

- **Daune:** margem-alvo 12%, piso 12% (sem colchão), comissão **fixa 5%**, preço
  **travado** — vendedora e admin recebem 409 ao tentar outro unitário na cotação comum.
  Mudar isso é nova política versionada, não override
- **Decor Tricot:** alvo 12%, piso 10%, comissão 10% → 5%
- **Demais (KTC, geral):** alvo = anterior + 2 p.p., piso = anterior − 1 p.p., comissão 10% → 5%
- **Comissão é da cotação, não do item:** desconto **ponderado por valor** do bloco variável
  (não-Daune) → `max(5%, 10% × (1 − desconto))` → presa pela **comissão máxima que preserva o
  piso** de cada item (da decomposição canônica do motor) → nunca abaixo de 5% sozinha
- **Abaixo do piso com 5%** é **exceção comercial** (`MARGEM_ABAIXO_PISO`), aprovável pelo
  workflow canônico — não é blocker. Validação **item a item**; um item rentável não esconde outro
- **Preço acima do recomendado** é livre; a comissão nunca passa de 10%
- **Item anterior à política** (sem `politica_comercial`) continua avaliado como foi formado
  (comissão por faixa, autonomia zero) e é apontado por `premissas_desatualizadas`; reprecificar é
  ato explícito
- **A vendedora vê a SUA comissão estimada** (R$ e taxa efetiva da cotação) — mudança deliberada
  da regra da Sessão 4. Continua sem ver custo, margem, piso, lucro, comissão por item e a
  mecânica de proteção. É **estimativa de pricing** (base: receita comercial); a comissão pagável
  é do financeiro (OQ-01 no audit)
- Alteração de preço, quantidade ou política invalida a aprovação anterior (os campos da política
  entram no fingerprint). Enquanto aguarda aprovação: rascunho, **não** gera PDF final
- **Frete** não entra no desconto nem na base da comissão
- Impacto e trilha: `relatorios/impacto_politica_comercial_2026-09-16.md`,
  `scripts/aplicar_politica_comercial_2026_09_16.py`, AuditLog correlação
  `politica-comercial-2026-09-16-*`. **Offline V2 está STALE** — não distribuir como preço

## Workflow comercial (Sessão 6)

Estados: `rascunho` → `aguardando_aprovacao` → `aprovada` → `emitida` → `enviada`, com
`cancelada` a partir de qualquer um. `rascunho` e `enviada` já existiam com esta semântica e
foram reaproveitados. `fechada`, `pedido` e `perdida` são **legados** (WON/LOST) e ficam fora
do workflow — dar sentido a eles é da Sessão 7.

- **Aprovação aprova uma CONFIGURAÇÃO, não uma cotação.** Toda decisão fica presa a um
  `fingerprint` do estado material. Mudou item, quantidade, preço, destino fiscal, condição
  ou premissa — a decisão vira `INVALIDADA`: ela era sobre outra proposta
- **Exceção comercial desde 16/09/2026:** margem realizada abaixo do **piso** do item
  (`MARGEM_ABAIXO_PISO`) — ver "Aprovação e política comercial". Preço travado (Daune) ou item
  anterior à política: preço abaixo do recomendado continua sendo exceção (`PRECO_ABAIXO`)
- **`preco_recomendado` ≠ `preco_base`.** O recomendado é o que o motor forma para o cenário
  **desta** cotação; o base é a referência do catálogo, formada noutro contexto fiscal.
  Medir desconto contra o base faria toda venda interestadual parecer exceção
- **Exceção é detectada por ITEM.** Desconto no item A compensado por acréscimo no B
  continua sendo exceção do A
- **Blocker duro ≠ exceção comercial.** `A_COTAR`, `REVIEW_REQUIRED` e frete CIF irresolvido
  **não são aprováveis** — aprovação é decisão comercial, não cria o número que falta
- **`ESTIMADO` e `REVALIDAR` emitem proposta mas não comprometem.**
  `validar_compromisso_firme()` bloqueia; aprovar toda proposta estimada seria burocracia
  sem conteúdo. **A reconfirmação da referência libera o compromisso** sem tocar no
  documento: o preço fica congelado, mas "posso me comprometer hoje?" é pergunta sobre o
  presente. Sem essa porta o bloqueio seria eterno — o item emitido é imutável e seu status
  nunca mudaria. O item **não** é promovido: continua registrando como o preço se formou
- **Emitido é imutável**, e a recusa é do servidor (`exigir_editavel`). Mudança pós-emissão
  cria **revisão** — a anterior fica íntegra, com seu snapshot e seu número
- **`SnapshotEmissao` congela o documento** para que reconstruí-lo não dependa de lookup
  vivo, e pina `aprovacao_id` + fingerprint — não um "aprovado = sim"
- **`can_approve_quotes` é permissão própria.** Administrar premissa
  (`can_manage_economics`) não autoriza desconto. OWNER sempre pode
- **PDF de rascunho sai marcado.** Preview e final são a mesma folha para quem recebe

## CRM comercial (Sessão 7 → Fase 3B, 16/09/2026)

**Cliente** é a organização — e também o prospect. Não existe `Lead → Prospect → Conta`: o
que muda entre eles é *quanto se sabe*, não *o que são*. O CRM aceita empresa com nome e
telefone; **emitir cotação continua exigindo os dados fiscais**, e essa validação não foi
afrouxada. CNPJ repetido de cliente ativo é recusado; nome parecido, não.

**"Venda" é o nome de interface da Oportunidade** — não há tabela nova. Uma venda é um
projeto/negociação com um cliente (`Clara Resorts — Renovação enxoval 2026`), com várias
revisões de cotação dentro. **Oportunidade ≠ Cotação**: a cotação tem o workflow técnico da
Sessão 6; a venda tem **um status comercial só**, e são coisas diferentes:

    ABERTA + RASCUNHO → Rascunho · ABERTA + ENVIADO → Enviado · ABERTA + NEGOCIACAO → Negociação
    GANHA → Vendido · PERDIDA → Perdido

- Três etapas abertas, nos dois sentidos, trocadas inline (`POST /vendas/{id}/status`) com
  histórico append-only. `PROSPECCAO/CONTATO/QUALIFICACAO/COTACAO/DECISAO` são
  `ETAPAS_LEGADAS`: legíveis, nunca criadas
- **Automação só do óbvio:** venda nova = Rascunho; cotação emitida/enviada move Rascunho →
  Enviado (ator "sistema (automático)"); venda em Negociação não volta; Negociação, Vendido e
  Perdido nunca são marcados sozinhos
- **Vendido** = `marcar_ganha` → `validar_compromisso_firme` (blocker duro impede), grava
  `cotacao_vencedora_id`, `valor_fechado`, `won_em` e abre o pós-venda em AGUARDANDO_ENTREGA.
  **Perdido** exige motivo estruturado; reabrir preserva o evento e volta à última etapa aberta
- **Toda cotação nova pertence a uma venda** (`exigir_venda`): existente do mesmo cliente ou
  criada pelo nome do projeto. Cross-client é 409. As 21 cotações históricas ficam com
  `oportunidade_id` NULO e visíveis em Cotações como "Sem venda vinculada (legado)" —
  **backfill não aplicado**: `relatorios/fase3b_proposta_backfill_cotacoes.md` é read-only
- **Registrar atualização** (`AtualizacaoComercial`, append-only) ≠ atividade; pode criar a
  próxima atividade junto. Timeline (`crm.timeline`) compõe as tabelas canônicas — nunca
  economia para a vendedora
- **Cliente 360** (`metrics_service.cliente_360`): total comprado = Σ `valor_fechado` das
  GANHAS (cotação enviada não é compra; R1/R2 nunca somam), ticket médio, última compra
  (`won_em`), em andamento, em aberto, atrasado, pago
- **Pós-venda V1:** AGUARDANDO_ENTREGA → (entrega) → AGUARDANDO_PAGAMENTO → PAGO, com
  ATRASADO marcado por alçada financeira e corrigível para PAGO. Vendedora edita entrega
  prevista, entrega e observação; OWNER/ADMIN registram faturamento/documento, pagamento
  previsto, PAGO e ATRASADO (`AuditLog`). **Vendido ≠ faturado ≠ pago** — três fatos, três
  colunas. Sem pagamento parcial
- **Vendedora entra em `/vendas`**; `/` redireciona para lá. Menu: Vendas · Clientes ·
  Cotações · Produtos (+ Relatórios; Aprovações e Admin por permissão). Continua sem custo,
  CNET, margem, lucro, piso, economia, premissas e Admin
- Métricas para o Dashboard Admin (Fase 3C) já em `metrics_service.painel_vendas`:
  vendido/faturado/pago, lucro e margem agregada da vencedora, ticket, conversão, desconto
  ponderado, comissão estimada, por vendedor/cliente/fornecedor/família, aging, pós-venda
- **Fora desta fase, de propósito:** redesign visual, Dashboard novo, **PDF cliente** (Fase
  3C), frete nacional (pausado), cotador offline (STALE)

- **`responsavel_id` não é ACL.** Quem vê o quê segue a Sessão 4; o filtro "minhas
  oportunidades" organiza o dia, não esconde negócio de colega
- **Pipeline não é máquina de estados**, ao contrário da cotação: avança, volta e pula
  etapa. O que é obrigatório é **registrar** — `OportunidadeEtapaHistorico`, append-only
- **Persistir fato, derivar métrica.** `valor_estimado` é palpite manual; o **valor cotado é
  derivado** da cotação mais recente e não tem coluna; `valor_fechado` é snapshot do ganho.
  "Atrasada" também é derivado (`due_em < agora` e não concluída) — um campo assim só seria
  verdadeiro enquanto alguém lembrasse de atualizá-lo
- **GANHA passa por `validar_compromisso_firme`.** Custo `ESTIMADO` não confirmado,
  `REVALIDAR` não reconfirmado, `A_COTAR`, frete CIF irresolvido ou exceção sem aprovação
  **impedem** fechar. Não cria pedido nem PO
- **PERDIDA exige motivo estruturado**; reabrir é ato explícito e **não apaga o evento de
  perda**. GANHA é terminal nesta versão
- **Cross-client é recusado pelo servidor**: cotação do Hotel B não entra no negócio do
  Hotel A. O resultado não seria erro visível — seria um funil que parece certo e aponta
  para o cliente errado
- **Confidencialidade não muda porque o dado virou card.** Preço e total comerciais podem
  aparecer no pipeline; custo, margem, lucro e markup, não

## Relatórios e runtime (Sessão 8)

**As definições métricas moram em `app/metrics_service.py`, um lugar só.** Dashboard, CSV e
testes chamam as mesmas funções — é o que impede a tela dizer 42% e o CSV dizer 39%.

- **Persistir fato, derivar métrica.** Conversão, aging, ticket médio, valor de pipeline e
  tempo em etapa não têm coluna: ficariam errados no dia em que alguém fechasse um negócio
  sem passar pela tela que os atualiza
- **Estado atual ≠ evento histórico.** Um negócio perdido e reaberto está **aberto** — não
  conta como perdido em nenhuma métrica de estado. O evento de perda continua auditável
- **`valor_cotado_atual` é determinístico:** só cotações da oportunidade, canceladas fora,
  maior revisão dentro de cada genealogia (R1 e R2 **nunca somadas**), e entre propostas
  paralelas vale a de atualização mais recente, com o `id` desempatando
- **Conversão = ganhas ÷ (ganhas + perdidas).** Abertas não entram no denominador. Sem
  encerradas, o resultado é **`None`**, não `0%` — "0% de conversão" afirma um fracasso que
  não aconteceu
- **Margem agregada é `Σ lucro ÷ Σ receita`**, nunca a média dos percentuais. Item sem dado
  econômico fica de fora; incluí-lo como zero afirmaria prejuízo que ninguém apurou
- **Valor ausente ≠ R$ 0,00.** Somar zero encolheria o pipeline artificialmente
- **Saúde separa bloqueio de aviso.** "23 problemas" não diz nada; o que trava a emissão e o
  que só pede atenção são coisas diferentes, e cada contagem diz o que está contando
- **`ANARA_DB_URL` isola o sistema inteiro** — aplicação, scripts e migrations. Até a Sessão 8
  só o Alembic a lia, e apontar para uma cópia migrava a cópia e **escrevia na produção**
  (B-21). Use `python3 scripts/smoke_test.py` para exercitar o sistema com o servidor de
  verdade: ele prova o isolamento antes de escrever qualquer coisa

Subir o sistema, criar o primeiro OWNER e o checklist do piloto estão em `PILOT_READINESS.md`.

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

**Sessão 5 — administração de premissas e versionamento: EXECUTADA, aguardando auditoria.**
Alembic em `0013` (`auditlog`, vigência da condição de pagamento, `can_manage_economics`, pinning das premissas no item).

**Sessão 6 — workflow comercial e aprovações: EXECUTADA, aguardando auditoria.** Alembic em `0016`.

**Sessão 7 — CRM, pipeline e UX comercial: EXECUTADA, aguardando auditoria.** Alembic em `0017`.

**Sessão 8 — relatórios, saúde e prontidão para o piloto: EXECUTADA.** Sem migration nova — relatórios são derivados. O sistema sobe, autentica e responde: `scripts/smoke_test.py`.

**Fase 3A — política comercial canônica (16/09/2026): EXECUTADA.** Alembic em `0018`.

**Fase 3B — CRM comercial simples, Cliente 360 e pós-venda (16/09/2026): EXECUTADA.**
Alembic em `0019`. 1176 testes. Próxima: **Fase 3C** (Dashboard Admin + redesign completo da
UX + PDF cliente final) — não iniciada, não autorizada.

O repositório é Git **local**. A senha compartilhada **saiu do código** na Sessão 4, mas
continua nos commits `413d6bd` e `165d75e`. **Publicação remota segue bloqueada** até o
histórico ser sanitizado ou haver decisão explícita de que a credencial aposentada é inócua —
e, de todo modo, `referencia/` versiona tabela de preço de fornecedor. Sem remote, sem push.

Antes de qualquer sessão: o procedimento de `BACKUP.md`. Depois: comparar contra
`relatorios/baseline_fase0.json`, que continua sendo o baseline imutável.

Consulte `ANARA_EXECUTION_STATE.md` antes de agir.

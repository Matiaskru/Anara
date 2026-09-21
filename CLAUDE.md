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

Python 3.12 · FastAPI · SQLModel/SQLAlchemy · SQLite local / PostgreSQL em produção (`DATABASE_URL`) ·
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

## Aprovação e política comercial (Fase 3A — 16/09/2026) — SUCEDIDA em 21/09/2026

> Vale para os itens que pinaram `POLITICA_COMERCIAL_2026-09-16`. Item novo usa a política de
> 21/09 (seção seguinte). Nada abaixo foi apagado do código: é a mecânica legada, preservada.

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

## Política comercial de 21/09/2026 — B2B, tabela 2×, comissão por item

**Sucede a política de 16/09/2026** (que continua legível para os itens que a pinaram). A mecânica
de cada item é decidida pelo **rótulo** em `CotacaoItem.politica_comercial`
(`politica_comercial.versao_da_politica`): `POLITICA_COMERCIAL_2026-09-21`, `…2026-09-16` ou
nenhum. Nunca por "tem política ou não".

- **Três preços por item, no cenário da cotação:** `preco_recomendado` **é o B2B**; `preco_tabela =
  dinheiro(fator × B2B)` (premissa `fator_tabela` = 2); `preco_negociado` é a proposta.
  `Produto.preco_base` **não** é tabela: é o "B2B de referência" do cenário padrão do catálogo
  (recalculado pelo script), informativo.
- **B2B = menor preço em centavos com margem ≥ alvo** (`pricing_engine.preco_b2b`): forma fechada,
  desce até um centavo que falha, sobe até o primeiro que cumpre — não é "HALF_UP + 1 centavo".
  Formado com **comissão 5% sobre a receita líquida do ICMS suportado pela Anara**
  (`TaxRuleSet.comissao_base_icms_pct = icms_pct − fcp_pct`: próprio + DIFAL do remetente; nunca
  FCP, PIS/COFINS, encargo, frete). Políticas anteriores continuam com base bruta (0).
- **Margens FINAIS no B2B** (`MargemRegra` com `politica` 21/09, sem `piso_pct`): KTC toalhas 16 ·
  Bathrobe 14 · lençóis <300 fios 20 · 300–399 22 · ≥400 23 · Pillow Case/Duvet Cover 19 (≥400: 20)
  · demais KTC 19 (≥400: 20) · Daune 13 · Decor 13 · ELIS 14. **Sem regra geral e sem fallback de
  15%**: produto sem regra entra sem preço com blocker `SEM_REGRA_DE_MARGEM`.
- **B2B é o piso de autonomia.** `negociado < B2B` → exceção `PRECO_ABAIXO_B2B` (aprovável, presa
  ao fingerprint). Acima é livre. Não há piso de margem separado.
- **Comissão por ITEM** pela escada do desconto sobre a tabela do item: 0% → 10 · (0,10] → 9 ·
  (10,20] → 8 · (20,30] → 7 · (30,40] → 6 · >40 → 5 (premissa `comissao_faixas_desconto`;
  10,00% → 9, 10,01% → 8; o B2B cai em 5%). Nenhum item limita a taxa de outro; nada de
  `c_max_i`, função contínua ou markup. Total = Σ; taxa efetiva = Σ comissão ÷ Σ base.
- **Alavanca persistida = desconto sobre a tabela** (`modo_edicao = "desconto"`, `valor_editado`;
  `modo_negociacao` guarda se digitou preço ou desconto). Mudou o cenário → B2B e tabela refeitos,
  **mesmo desconto reaplicado, preço absoluto muda** (CR-01 continua garantido). Se cair abaixo do
  novo B2B, vira exceção — nunca é puxado para o B2B em silêncio. Preço digitado vira desconto
  efetivo; desconto digitado gera preço com `ROUND_UP` ao centavo (o efetivo nunca passa do pedido).
- **Daune não é mais travado.** **Decor**: valores são CUSTO DE COMPRA → `CustoReferencia`
  `DECOR_DIRECT` com crédito PIS/COFINS 9,25% e **sem** crédito de ICMS (fator 0,9075); o pedido de
  revisão "custo ou venda" foi encerrado por essa migração. **ELIS** (cobertor Boa Noite Casal):
  fornecedor nacional, origem SP, R$ 68,38 custo líquido direto, 14%.
- **Fronhas:** allowance de costing 2% (`ParametroKTC quality_allowance` escopo `Pillow Case`,
  planilha "Pillow Case Costing sheet" — **não é I.I.**); nomenclatura canônica **ABAS** (a
  palavra legada de catálogo só é lida, nunca escrita/exibida — há teste que varre). Toalhas
  100/0 e 90/10 têm o mesmo preço; a composição só identifica o SKU.
- **Fiscal (27 UFs):** `app/fiscal_2026_09_21.py` cadastra `icms_interno_base` das 27 UFs e
  `RegraFcp` **por família do escopo** (AL 1%, RJ 2%, SE 1%; demais 0%). Família fora do escopo
  continua DESCONHECIDA e bloqueia. Base única para não contribuinte é a regra adotada (não
  reabrir base dupla); FCP não é adicional universal por UF — BA/PE/PI ficam em 0% para cama/banho
  sem enquadramento específico por produto/NCM (não copiar a coluna FEM legada);
  `carga_final`/`base_dupla`/`fem` são só benchmark histórico.
- **Frete:** `FRETE_MANUAL_CONFIRMADO` — CIF com valor digitado e `freight_manual_confirmado`
  soma ao total, entra no fingerprint e **não** depende dos blockers do motor TRANSAL; fora do
  preço unitário/comissão/margem. CIF sem confirmação continua pelo motor (e bloqueia).
- **Vendedora vê**: tabela, B2B, proposta, desconto %, **a comissão dela por item e total**,
  autonomia. **Não vê**: base comissionável, ICMS deduzido, faixa como mecânica, custo, margem,
  lucro. PDF: só preço final (allowlist inalterada). Primeiro acesso confirma o perfil
  ("Vendedora"/"Administrativo") **autorizado pelo servidor**; escolher outro é recusado (403).
- **Sinal / entrada é COMPOSIÇÃO, não condição** (`app/payment_terms.encargo_com_sinal`,
  migration 0024): `Cotacao.percentual_sinal` (fração 0–1, default 0) + `condicao_pagamento` (=
  condição do **saldo**). O sinal é pago à vista **sem encargo**; `encargo_efetivo = (1 − sinal) ×
  encargo_do_saldo` entra no `TaxRuleSet` no mesmo lugar do encargo (0% + 30/60/90 → 4,8%; 30% →
  3,36%; 50% + 30/60 → 1,60%; 100% → 0%, saldo irrelevante mesmo bloqueado). Sinal 0 devolve o
  **mesmo objeto** resolvido da tabela — comportamento tradicional. Nenhum desconto por
  antecipação. Mudar sinal/saldo é MATERIAL (recalcula B2B/tabela/preço preservando o desconto,
  invalida aprovação; `percentual_sinal` entra no fingerprint só quando > 0 — cotação antiga
  mantém o hash). Item pina `percentual_sinal` e `encargo_saldo_pct`; snapshot guarda
  `percentual_sinal`, `condicao_saldo`, `condicao_pagamento_texto` ("30% de sinal + 70% em 30/60/90
  dias") e `encargo_efetivo_pct` — o PDF final lê o texto do snapshot. Validação 0 ≤ sinal ≤ 100
  (negativo, > 100, NaN, texto → 400 sem alterar nada). Tela: checkbox "Possui sinal / entrada" →
  percentual + saldo + texto; a vendedora **nunca** vê encargo efetivo, fórmula ou fator (o encargo
  por condição também saiu do formulário `/cotacoes/nova` para quem não vê economia). A condição
  opaca `SINAL30+30/60/90` não é mais semeada e é **desativada** pelo script (etapa `sinal`);
  CARTÃO continua bloqueado (sem taxa) — exceto com sinal 100%.
- **I.I. econômico KTC/Egito = 0% (22/09/2026, migration 0025) — ECONOMIA REAL × FORMAÇÃO
  COMERCIAL.** Duas grandezas, nunca confundidas:
  * **CNET real** (`custo_para_precificar` → `net_brl`; `Produto.custo_unitario`,
    `CotacaoItem.custo_unitario`): EXW + frete (peso × US$/kg) + outras despesas, **I.I. = 0**
    (`pricing_service.II_ECONOMICO_KTC`), × câmbio. É o único custo — forma lucro, margem
    realizada, dashboard, relatórios, snapshots novos. Nenhuma alíquota positiva de I.I. entra
    em custo vigente da KTC; as linhas de `NcmRegra` com 3,5%/1,62% foram **encerradas**
    (`valid_to` 22/09) e as vigentes trazem NCM + I.I. 0%.
  * **Referência comercial de precificação** (`nationalization.referencia_comercial`;
    `memoria["base_comercial_brl"]`; `CotacaoItem.base_comercial_precificacao`): o mesmo
    waterfall com a **proteção comercial** do SKU no lugar do imposto — a alíquota preferencial
    que formava o custo até 22/09, pinada por SKU (`Produto.protecao_comercial_pct`, script) e
    por família (`ParametroKTC protecao_comercial_pct`: 3,5% cama/banho/roupão/chinelo, 1,62%
    travesseiro/protetor/topper, 0% Blanket/Duvet Insert/Bath Rug/Face Towel e famílias sem
    alíquota confiável). Forma **B2B, tabela e preco_base** — exatamente onde estavam (paridade
    provada: 280 SKUs × 9 cenários, 0 diferenças). **Não é custo, tributo nem despesa**; nunca
    entra em lucro, margem, dashboard ou base da comissão. Família sem regra de proteção →
    `REVIEW_REQUIRED` (não se inventa alíquota).
  * `ps.bases_de_preco(session, produto)` → `(custo_real, base_comercial, memória)`; o router
    forma preço com `base_comercial` (`_calcular(..., base_comercial=)`, `_base_de_preco(item)`)
    e economia com `custo`. `preco_recomendado` = **B2B comercial** (piso de autonomia);
    `preco_b2b_economico` = o B2B que o custo real daria (diagnóstico interno). Margens-alvo não
    mudaram; a margem **realizada** subiu onde havia I.I. Item anterior a 22/09 (sem pino) segue
    com o custo que congelou até ser reprecificado explicitamente. Vendedora não vê nada disso.
- **Como aplicar no banco real** (aplicado LOCALMENTE em 21–22/09/2026): `alembic upgrade head`
  (0023 + 0024 + 0025, aditivas) e `scripts/aplicar_dados_2026_09_21.py --aplicar` (encerra 16/09,
  cria 21/09, fiscal, Decor, ELIS, cotação KTC 29/07, BL-001, **ii_zero** — pino da proteção por
  SKU, regra por família, NCM versionada —, ABAS, preco_base + cache do CNET real, desativa a
  condição opaca de sinal). Preview sem `--aplicar`. Relatórios: `scripts/relatorios_2026_09_21.py`.
  Validação: `tests/test_politica_2026_09_21.py`, `tests/test_sinal_2026_09_21.py`,
  `tests/test_ii_zero_2026_09_22.py`, `scripts/politica_2026_09_21/` (e2e Playwright — geral e de
  sinal —, matriz × oracle com `--sinais`, fuzz, política vigente, `auditoria_confidencialidade.py`),
  `scripts/ii_zero_2026_09_22/baseline_ktc.py` (baseline ANTES/DEPOIS + paridade). Atenção: os
  seeds de startup já inserem as partes **aditivas** (regras 21/09, premissas, matriz fiscal, ELIS
  fornecedor, NCM vigente 0%, proteção por família); encerramentos e migrações de dados são do script.

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

## Interface comercial e PDF cliente (Fase 3C — 16/09/2026)

**O frontend não calcula economia.** Toda tela redesenhada chama o endpoint canônico e
pinta a resposta: negociação (`/cotacoes/{id}/negociacao[/preview]`), item
(`PUT /cotacoes/{id}/itens/{item}`), situação/ações (`GET /cotacoes/{id}/painel`, HTML
derivado de `ws.avaliar`). Fórmula em template ou JavaScript é regressão.

- **Menu por papel**: vendedora Vendas · Clientes · Cotações · Produtos; OWNER/ADMIN +
  Dashboard (`/dashboard`), Aprovações (alçada) e Admin. Relatórios mora no Admin. Sem saudação
- **Uma linguagem visual** (`app/static/css/anara.css`, `app/static/js/ui.js`): sidebar
  compacta, topbar baixa, tabelas densas, pills, modais/popovers, timeline, stepper, kanban,
  painel sticky, gráficos em SVG sem CDN. Telas antigas herdam a régua
- **Status da venda é inline** (popover/drag-and-drop → `POST /vendas/{id}/status`);
  Vendido/Perdido são ações explícitas. Timeline única e em português — nunca enum, ID,
  rota, fingerprint ou AuditLog bruto (`rotulos.EVENTO_TIMELINE`)
- **Cotação**: dados comerciais em grid, tabela Produto/Qtd./Recomendado/Seu preço/
  Desconto/Total, Daune "🔒 fixo" sem explicar margem/comissão, painel sticky com Produtos,
  Frete, TOTAL, Desconto, Sua comissão estimada e ✓/⚠ de autonomia. OWNER/ADMIN abrem
  "Economia da proposta" (fechada por padrão). `observacoes` é **interna** e nunca vai ao
  PDF; `observacao_cliente` (migration 0020) é o que o cliente lê
- **Dashboard OWNER/ADMIN** consome `metrics_service.dashboard_admin` e `serie_mensal`
  (filtros período/vendedora/cliente/fornecedor/família). Vendido ≠ faturado ≠ pago; sem
  dado é `None`/"—", nunca zero inventado
- **PDF = PROPOSTA COMERCIAL ANARA** (`app/pdf_bridge.py` → `app/pdf_proposta.py`): lista
  de permissão explícita (`CAMPOS_HEADER/ITEM/TOTAIS`) + varredura de códigos
  (`PdfInseguro`); final nasce do `SnapshotEmissao`; rascunho sai com faixa e marca d'água
  "RASCUNHO — NÃO ENVIAR AO CLIENTE"; preço negociado final, sem recomendado, tabela,
  desconto, fornecedor, custo, margem, comissão ou código (`A_COTAR`, `REVIEW_REQUIRED`…);
  frete por extenso. Há teste que extrai o texto e falha se algo disso aparecer
- **Demonstração só em cópia**: `scripts/demo_3c.py --db <cópia> --serve <porta>`;
  inspeção visual `scripts/visual_3c.py`. Nunca apontar para `data/anara.db`

## Acesso por perfil e recuperação de senha (17/09/2026)

- **O papel decide o destino, não a pessoa:** `landing()` manda vendedora para `/vendas` e
  OWNER/ADMIN para `/dashboard`; `/` redireciona por papel. Login sem seletor de papel
- **Zero economia para a vendedora em HTML e JSON**, por lista de permissão no servidor —
  e `tests/test_auth_perfis.py` varre todas as telas e endpoints dela. O `situacao` devolve
  `PRECISA_APROVACAO`, nunca `MARGEM_ABAIXO_PISO`; bloqueios saem por
  `rotulos.BLOCKER_COMERCIAL`; o aviso de premissas não mostra valores internos
- **Recuperação de senha por token** (`app/recuperacao_senha.py`): só o `sha256` fica em
  `passwordresettoken` (migration 0021), 30 min, uso único, o novo encerra os anteriores,
  redefinir derruba as sessões (`sessao_versao`) e vai ao `AuditLog` **sem** senha nem token.
  "Esqueci minha senha" responde sempre a mesma frase, exista a conta ou não
- **E-mail** (`app/mail.py`): SMTP por `ANARA_MAIL_*`; sem SMTP, DEV escreve no log
  `anara.mail` e produção declara `recuperacao_senha_por_email` não operacional. Nunca
  mostrar token/link na página
- **Usuários:** conta nova nasce sem senha conhecida pelo gestor (link de primeiro acesso,
  48 h); "Enviar redefinição" por pessoa; senha atual nunca é exibida
- **"Cara de teste" não é feature**: `arquivamento.candidatas_a_teste` é interna e não
  aparece em Cotações

## Produção: banco por URL, Postgres, Railway (17/09/2026)

**O esquema é do Alembic, os dados são do migrador, o segredo é do ambiente.** O roteiro
completo está em `DEPLOY_PRODUCTION.md`; o que segue é o que o código passou a assumir.

- **Banco por URL**: `ANARA_DB_URL` (isolamento explícito) > `DATABASE_URL` (injetada pela
  plataforma) > `data/anara.db` **relativo ao repositório** — `~/Anara-Cotacao/...` não
  existe mais em `app/`. `postgres://` vira `postgresql+psycopg://` (`app.db.normalizar_url`);
  `check_same_thread` só vai para o SQLite; `url_segura()` é a única forma da URL que pode
  aparecer em log
- **No PostgreSQL a aplicação não altera esquema.** `migrations.migrar()` só reporta o que
  falta (`pendentes`) e `fazer_backup()` devolve `""`; quem cria coluna é `alembic upgrade
  head` no pre-deploy. Migration que falha aborta o deploy — é o comportamento desejado
- **SQLite nunca aplicou FK; o Postgres aplica.** `auditlog.ator_id` precisa de usuário
  real; a suíte grava os quatro atores de teste quando roda com `ANARA_TEST_DB_URL`
- **Booleano em SQL cru vai como parâmetro** (`:sim` → `True`), nunca `= 1`. Enums
  (`cotacao.status`, `fornecedor.tipo`, `fornecedor.cost_method_padrao`) são
  `Enum(native_enum=False, length=64)` → `VARCHAR(64)` nos dois bancos, gravando o **nome**
  do membro (migration `0022`, no-op no SQLite)
- **`0.0.0.0:$PORT` pelo `Procfile`/`railway.json`**; local continua `127.0.0.1:8420` por
  `iniciar_plataforma.py`. `/health` público e mínimo é o healthcheck
- **Produção recusa segredo com cara de exemplo** (`auth.chave_e_placeholder`) e avisa na
  subida, sem valor de variável, quando `ANARA_BASE_URL` não é https ou o banco é SQLite
- **Fontes licenciadas (`/static/fonts/`) só para quem está autenticado**; anônimo recebe
  404. O resto de `/static` é público
- **Cutover**: `scripts/migrar_sqlite_para_postgres.py` (origem somente-leitura, destino
  vazio ou `--substituir-destino <nome>`, uma transação, ids preservados, sequences em
  `MAX(id)`, comparação linha a linha, FKs validadas, relatório JSON).
  `scripts/smoke_producao.py [--postgres URL]` sobe o servidor como no Railway e ataca como
  na internet — OWNER, SELLER, URLs proibidas, PDF, log sem segredo
- **Histórico Git sanitizado em 17/09/2026**: a senha compartilhada antiga não existe em
  commit algum; os hashes anteriores mudaram (mapa em `ANARA_EXECUTION_STATE.md`).
  `referencia/` continua sendo o motivo para não fazer push sem decidir (ver PREPARAÇÃO)

## Auditoria de crise — reprecificação e confiança do custo (17/09/2026)

- **Mudou o cenário (destino, contribuinte, condição, frete, finalidade, origem, alíquota) →
  todo preço é reformado** na margem-alvo do item, a aprovação cai e a tela avisa. Preço
  negociado era decisão sobre OUTRO cenário e não sobrevive; renegocia-se sobre o preço certo
  (`_recalcular_todos_itens`, `comercial_service.cenario_dos_itens_divergiu`)
- **Editar quantidade não muda a alavanca do item** (`PUT /itens/{id}` sem `modo`); preço e
  quantidade ≤ 0 são recusados. "Atualizar e recalcular" leva a alavanca à política vigente
- **Status canônico do custo**: cotação KTC direta com frescor STALE ou sem data → REVALIDAR;
  `preco_ktc_usd` histórico → REVIEW_REQUIRED; nacionalização com I.I./peso assumidos → 
  REVIEW_REQUIRED; nacional com `precisa_revisao` sem referência versionada → REVALIDAR.
  REVALIDAR cota e emite, não fecha venda
- **Motor recusa medida/gramatura ≤ 0**; EXW ≤ 0 não nacionaliza; `preco_base` de produto sem
  custo não é preço nem referência
- Oracles e scanners em `scripts/crisis/` (matriz fiscal, fuzz, goldens, backtest, Playwright);
  regressão em `tests/crisis/`. Relatório: `~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917/`

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
Alembic em `0019`.

**Fase 3C — redesign da plataforma comercial, Dashboard OWNER/ADMIN e PDF cliente
(16/09/2026): EXECUTADA.** Alembic em `0020` (`cotacao.observacao_cliente`, aditiva).
**Hardening de acesso por perfil, login e recuperação de senha (17/09/2026): EXECUTADO.**
Alembic em `0021` (`passwordresettoken`, aditiva). Próximas (não iniciadas, não
autorizadas): frete nacional definitivo, cadastro fiscal pendente, offline V3, cutover
(publicação real).

**Auditoria de crise P0 (17/09/2026): EXECUTADA** — bug de reprecificação corrigido (CR-01/02),
sete correções, 0 P0 aberto, 1.436 testes (SQLite e Postgres). Ver `AUDIT_ANARA_MASTER.md` §11.

**Política comercial de 21/09/2026 (B2B · tabela 2× · comissão por item · fiscal 27 UFs ·
Daune/Decor/ELIS · fronhas · frete manual · primeiro acesso · SINAL/ENTRADA como composição ·
I.I. econômico KTC 0% com proteção comercial separada do custo): IMPLEMENTADA, COMMITADA
LOCALMENTE (sem push), APLICADA AO BANCO LOCAL.** Alembic em `0025`. Ver seção "Política
comercial de 21/09/2026" e `ANARA_EXECUTION_STATE.md` para os números da última validação.

**Preparação para produção (17/09/2026): EXECUTADA.** Alembic em `0022` (enums como
VARCHAR, no-op no SQLite). O repositório é Git **local**, sem remote e sem push. O
histórico foi **sanitizado** (a senha compartilhada antiga não está em nenhum commit; bundle
anterior em `~/Anara-Cotacao-Backups/`). `referencia/` ainda versiona tabela de preço de
fornecedor — decidir antes do push (`DEPLOY_PRODUCTION.md` → PREPARAÇÃO). Publicar é ato
manual e autorizado à parte.

Antes de qualquer sessão: o procedimento de `BACKUP.md`. Depois: comparar contra
`relatorios/baseline_fase0.json`, que continua sendo o baseline imutável.

Consulte `ANARA_EXECUTION_STATE.md` antes de agir.

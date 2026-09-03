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

| Estado | Pode cotar? | Pode gerar PDF? | Pode virar pedido/WON? |
|---|---|---|---|
| CONFIRMADO | sim | sim | sim |
| ESTIMADO | sim | sim | **não**, enquanto `confirmation_pending = true` — só após um Admin confirmar |
| A_COTAR | não gera preço automático | **não** | não |
| REVIEW_REQUIRED | bloqueia conclusão | **não** | não |

`FRETE_A_COTAR` e `FRETE_REVIEW_REQUIRED` bloqueiam o PDF quando o frete é CIF. `A_COMBINAR` é
termo comercial deliberado e **não** bloqueia.

## Papéis

- **OWNER** — acesso total, não pode ser removido acidentalmente
- **ADMIN** — custos, margens, câmbio, premissas, fiscal, fretes, aprovações, relatórios.
  Gerenciar usuários é permissão granular (`can_manage_users`): gerente sim, administrativo não
- **VENDEDOR_INTERNO** — todos os clientes e cotações; cria e edita; gera PDF quando permitido
- **VENDEDOR_COMISSIONADO** — só a própria carteira

**Vendedor não vê custo, EXW, custo NET, margem interna, markup, impostos detalhados nem
premissas** — e isso vale na **resposta da API**, não só na tela. Vendedor não edita margem alvo
nem markup. Permissão é verificada no backend; URL/API sem permissão → **403**.

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
- Taxa por kg de toalha **já é EXW final** — não aplicar CMT, 2ª qualidade nem margem por cima
- Casamento de produto é por **campos estruturados**, nunca por nome

## Como trabalhar neste projeto

1. **Uma etapa por autorização.** Nunca encadeie ondas. Terminou a etapa autorizada, **pare**.
2. Antes de cada onda: baseline. Depois: suíte completa + relatório do que mudou de número e
   por qual regra. Nada é publicado sem esse relatório.
3. Bug corrigido ganha teste de regressão. Regra crítica ganha teste que falha se for quebrada.
4. Discrepância nova vira ID novo no `AUDIT_ANARA_MASTER.md`. Nunca resolver em silêncio.
5. Decisão sem impacto econômico/fiscal/comercial: escolha a mais simples e documente. Decisão
   com impacto: registre como pergunta aberta e implemente o resto.

## Estado atual (03/09/2026)

Auditoria, plano e **Fase 0 — Fundação** concluídos. Nenhuma regra de negócio foi alterada:
os 173 testes originais continuam passando sem edição, e nenhuma cotação histórica mudou de
número. As **Ondas 1 a 8 não estão autorizadas** — a Fase 0 ter terminado não autoriza a Onda 1.

O repositório agora é Git, e é **local**: a senha compartilhada de `app/auth.py:10-11` está no
histórico desde o commit inicial. Sem remote e sem push até a Onda 4 ou até uma sanitização de
histórico autorizada em separado.

Antes de qualquer onda: o procedimento de `BACKUP.md`. Depois: comparar contra
`relatorios/baseline_fase0.json`.

Consulte `ANARA_EXECUTION_STATE.md` antes de agir.

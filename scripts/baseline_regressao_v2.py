#!/usr/bin/env python3
"""Baseline ampliado da Fase 0 — retrato numérico do sistema ANTES das ondas.

Para que serve: quando a Onda 1 mudar a regra fiscal, a Onda 2 o custo Daune e a Onda 3
o frete, é preciso poder dizer **exatamente** o que mudou, em qual SKU, em qual cenário,
e por qual regra. Este arquivo é a fotografia contra a qual essa comparação é feita.

O que ele registra:

* 339 SKUs × 6 cenários fiscais × 5 condições de pagamento — preço recomendado pela
  margem-alvo do próprio SKU, margem resultante, markup, comissão, impostos e lucro;
* o custo NET recalculado hoje pelos motores (e a diferença contra o custo gravado);
* as 18 cotações com seus totais, os 45 itens com todos os campos econômicos e o
  sha256 da memória de preço congelada em cada um;
* as 4 `BaseImportacao` inteiras;
* as premissas versionadas vigentes que produziram esses números;
* o digest do banco por tabela e por coluna.

**Registra o estado atual, inclusive onde ele está errado.** Os bugs conhecidos do
`AUDIT_ANARA_MASTER.md` — B-01 (interestadual nacional saindo a 4%), B-06 (fallback
silencioso de ICMS), B-09 (custo Daune sem crédito) — aparecem aqui como estão hoje.
Corrigir agora, para o baseline ficar "bonito", destruiria a única referência que torna
a correção verificável depois. Cada um é corrigido na onda correspondente, e a diferença
tem que ficar explicável linha a linha.

Somente leitura: o banco é aberto em modo `ro` do SQLite.

Uso:
    python3 scripts/baseline_regressao_v2.py                    # gera e salva
    python3 scripts/baseline_regressao_v2.py --verificar        # confere reprodutibilidade
    python3 scripts/baseline_regressao_v2.py --limite 40        # amostra, para teste rápido
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import config_service as cfg  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.fiscal_rules import resolver_icms_estruturado  # noqa: E402
from app.models import (  # noqa: E402
    BaseImportacao, CmtPreco, CondicaoPagamento, Cotacao, CotacaoItem, EstadoFiscal,
    Fornecedor, MargemRegra, MaterialPreco, NcmRegra, ParametroKTC, Premissa, Produto,
    RegraFiscalVenda, ToalhaPreco,
)
from app.pricing_engine import calcular_por_margem  # noqa: E402
from scripts.fundacao import DB_PATH, estado_banco  # noqa: E402

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "relatorios", "baseline_fase0.json")

# --- os 6 cenários fiscais -------------------------------------------------------------
# Origem São Paulo (é o `catalogo_origem` configurado). Destinos escolhidos para que a
# Onda 1 seja mensurável: hoje os quatro cenários interestaduais resolvem pela mesma
# regra; depois da Onda 1, Minas Gerais deve virar a faixa de 12% e a Bahia a de 7% para
# fornecedor NACIONAL, enquanto a KTC (importada) segue em 4%. Se algum desses seis
# números mudar sem que a onda explique, é regressão.
CENARIOS = [
    ("São Paulo", "São Paulo", True),      # intraestadual, contribuinte
    ("São Paulo", "São Paulo", False),     # intraestadual, não contribuinte
    ("São Paulo", "Minas Gerais", True),   # interestadual, contribuinte  → futura faixa 12%
    ("São Paulo", "Minas Gerais", False),  # interestadual, consumidor final
    ("São Paulo", "Bahia", True),          # interestadual, contribuinte  → futura faixa 7%
    ("São Paulo", "Bahia", False),         # interestadual, consumidor final
]

# --- as 5 condições de pagamento -------------------------------------------------------
# São as cinco com encargo confirmado nas decisões de 03/09 (1,6% · 3,2% · 4,8% · 6,4% ·
# 8,0%). "À VISTA" e as duas sem encargo confirmado ficam registradas em `premissas`,
# fora da grade.
CONDICOES = ["30", "30/60", "30/60/90", "30/60/90/120", "30/60/90/120/150"]


def chave_cenario(origem: str, destino: str, contribuinte: bool) -> str:
    return f"{origem}|{destino}|{'SIM' if contribuinte else 'NAO'}"


def _json(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def _linha(obj, colunas) -> dict:
    return {c: _json(getattr(obj, c, None)) for c in colunas}


def _sha256_txt(texto) -> str:
    if texto is None:
        return None
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def abrir_sessao(db_path: str = DB_PATH) -> Session:
    """Engine somente leitura: uma escrita acidental vira erro, não vira dado perdido."""
    engine = create_engine(f"sqlite:///file:{os.path.abspath(db_path)}?mode=ro&uri=true")
    return Session(engine)


# ---------------------------------------------------------------------------
def gerar(db_path: str = DB_PATH, limite: int = 0, com_estado_banco: bool = True) -> dict:
    with abrir_sessao(db_path) as s:
        estados = s.exec(select(EstadoFiscal)).all()
        regras_fiscais = s.exec(select(RegraFiscalVenda)).all()
        fallback = cfg.num(s, "icms_fallback_pct", 0.18)

        # --- cenários fiscais ---
        cenarios = {}
        for origem, destino, contrib in CENARIOS:
            icms, regra = resolver_icms_estruturado(regras_fiscais, estados, origem, destino,
                                                    contrib, fallback=fallback)
            cenarios[chave_cenario(origem, destino, contrib)] = {
                "origem": origem, "destino": destino, "contribuinte": contrib,
                "icms": icms, "regra": regra,
            }

        # --- as 30 combinações cenário × condição, cada uma com seu TaxRuleSet ---
        grade_regras = {}
        for origem, destino, contrib in CENARIOS:
            for condicao in CONDICOES:
                virtual = Cotacao(cliente_id=0, estado_origem=origem, estado_destino=destino,
                                  contribuinte_icms=contrib, condicao_pagamento=condicao)
                regras, contexto = ps.regras_da_cotacao(s, virtual)
                chave = f"{chave_cenario(origem, destino, contrib)}#{condicao}"
                grade_regras[chave] = {
                    "regras": regras,
                    "resumo": {
                        "icms_pct": regras.icms_pct,
                        "pis_cofins_pct": regras.pis_cofins_pct,
                        "encargo_pct": regras.encargo_financeiro_pct,
                        "taxa_fixa": regras.taxa_fixa(),
                        "encargo_label": contexto["encargo_label"],
                        "encargo_confirmado": contexto["encargo_confirmado"],
                        "comissao_tabela": [list(f) for f in regras.comissao_tabela],
                        "icms_regra": contexto["icms_regra"],
                    },
                }

        # --- produtos ---
        consulta = select(Produto).order_by(Produto.sku_key)
        produtos = s.exec(consulta).all()
        if limite:
            produtos = produtos[:limite]

        linhas = []
        for p in produtos:
            margem = ps.margem_padrao(s, p)
            custo_memoria = ps.custo_net(s, p)
            custo_recalculado = custo_memoria.get("net_brl")

            grade = {}
            for chave, item in grade_regras.items():
                if not p.custo_unitario:
                    grade[chave] = None            # sem custo não se inventa margem
                    continue
                r = calcular_por_margem(p.custo_unitario, 1.0, margem.margem_pct,
                                        item["regras"], preco_base=p.preco_base)
                comissao_pct = (r.comissao / r.faturamento) if r.faturamento else 0.0
                grade[chave] = [r.preco_negociado, r.margem_liquida, r.markup_implicito,
                                comissao_pct, r.impostos, r.lucro]

            linhas.append({
                "id": p.id,
                "sku": p.sku_key,
                "nome": p.nome,
                "familia": p.familia,
                "categoria": p.categoria,
                "fornecedor_id": p.fornecedor_id,
                "cost_method": p.cost_method,
                "custo_confianca": p.custo_confianca,
                "precisa_revisao": bool(p.precisa_revisao),
                "custo_unitario": p.custo_unitario,
                "preco_base": p.preco_base,
                "custo_net_recalculado": custo_recalculado,
                "custo_delta_pct": ((custo_recalculado / p.custo_unitario - 1)
                                    if p.custo_unitario and custo_recalculado else None),
                "custo_avisos": custo_memoria.get("avisos", []),
                "exw_usd": custo_memoria.get("exw_usd"),
                "exw_origem": custo_memoria.get("exw_origem"),
                "ncm": p.ncm,
                "ii_aplicado": p.ii_aplicado,
                "peso_kg": p.peso_kg,
                "peso_tipo": p.peso_tipo,
                "margem_alvo": margem.margem_pct,
                "margem_regra": margem.regra,
                "grade": grade,
            })

        # --- cotações e itens ---
        cotacoes_db = s.exec(select(Cotacao).order_by(Cotacao.id)).all()
        itens_db = s.exec(select(CotacaoItem).order_by(CotacaoItem.id)).all()
        itens_por_cotacao = {}
        for i in itens_db:
            itens_por_cotacao.setdefault(i.cotacao_id, []).append(i)

        cols_cotacao = ["id", "numero", "cliente_id", "vendedor", "status", "condicao_pagamento",
                        "estado_origem", "estado_destino", "contribuinte_icms", "frete",
                        "criado_em", "validade_em", "base_importacao_id", "pdf_gerado_em",
                        "arquivada_em", "arquivada_motivo", "validade_dias", "freight_type",
                        "freight_valor", "freight_incluso", "emitida_em", "aceite_em",
                        "icms_aplicado", "icms_regra", "pis_cofins_pct",
                        "encargo_financeiro_pct"]
        cols_item = ["id", "cotacao_id", "produto_id", "ordem", "nome_produto", "especificacao",
                     "categoria", "quantidade", "custo_unitario", "preco_base",
                     "preco_negociado", "margem_liquida", "faturamento", "custo_total", "lucro",
                     "diferenca_pct_vs_base", "modo_edicao", "valor_editado", "fornecedor_id",
                     "fornecedor_nome", "cost_method", "margem_padrao_pct", "margem_regra",
                     "comissao_pct", "impostos", "comissao_valor", "markup_implicito"]

        cotacoes = []
        for c in cotacoes_db:
            meus = itens_por_cotacao.get(c.id, [])
            faturamento = sum(i.faturamento or 0.0 for i in meus)
            custo_total = sum(i.custo_total or 0.0 for i in meus)
            lucro = sum(i.lucro or 0.0 for i in meus)
            linha = _linha(c, cols_cotacao)
            linha["totais"] = {
                "itens": len(meus),
                "faturamento": faturamento,
                "custo_total": custo_total,
                "lucro": lucro,
                "impostos": sum(i.impostos or 0.0 for i in meus),
                "comissao": sum(i.comissao_valor or 0.0 for i in meus),
                "margem_consolidada": (lucro / faturamento) if faturamento else None,
            }
            cotacoes.append(linha)

        itens = []
        for i in itens_db:
            linha = _linha(i, cols_item)
            linha["memoria_sha256"] = _sha256_txt(i.memoria_json)
            linha["memoria_bytes"] = len(i.memoria_json or "")
            itens.append(linha)

        # --- bases de importação (o que a ponte precisa preservar) ---
        cols_base = ["id", "importado_em", "nome_arquivo", "observacoes", "icms_pct",
                     "pis_cofins_pct", "encargo_financeiro_pct", "comissao_tabela_json",
                     "origem_uf", "icms_por_estado_json", "cenarios_fiscais_json",
                     "cambio_usd_brl", "frete_usd_kg", "outras_desp_usd_un"]
        bases = []
        for b in s.exec(select(BaseImportacao).order_by(BaseImportacao.id)).all():
            linha = _linha(b, cols_base)
            linha["sha256"] = _sha256_txt(json.dumps(linha, sort_keys=True, ensure_ascii=False))
            linha["produtos_ligados"] = len(
                s.exec(select(Produto).where(Produto.base_importacao_id == b.id)).all())
            linha["cotacoes_ligadas"] = len(
                s.exec(select(Cotacao).where(Cotacao.base_importacao_id == b.id)).all())
            bases.append(linha)

        # --- premissas versionadas vigentes ---
        premissas = {
            "premissa": [_linha(x, ["id", "chave", "valor_num", "valor_txt", "unidade",
                                    "valid_from", "valid_to", "ativo", "fonte"])
                         for x in s.exec(select(Premissa).order_by(Premissa.id)).all()],
            "condicaopagamento": [_linha(x, ["id", "codigo", "label", "encargo_pct",
                                             "encargo_confirmado", "ordem", "ativo"])
                                  for x in s.exec(select(CondicaoPagamento)
                                                  .order_by(CondicaoPagamento.id)).all()],
            "margemregra": [_linha(x, ["id", "nome", "fornecedor_id", "familia", "sku_key",
                                       "min_thread_count", "max_thread_count", "margem_pct",
                                       "prioridade", "ativo"])
                            for x in s.exec(select(MargemRegra).order_by(MargemRegra.id)).all()],
            "regrafiscalvenda": [_linha(x, ["id", "origem", "destino", "contribuinte",
                                            "icms_venda", "regra", "prioridade", "ativo"])
                                 for x in s.exec(select(RegraFiscalVenda)
                                                 .order_by(RegraFiscalVenda.id)).all()],
            "estadofiscal": [_linha(x, ["id", "estado", "uf", "aliquota_interestadual",
                                        "aliquota_interna", "carga_final", "ativo"])
                             for x in s.exec(select(EstadoFiscal)
                                             .order_by(EstadoFiscal.id)).all()],
            "ncmregra": [_linha(x, ["id", "familia", "ncm", "ii_original", "ii_preferencial",
                                    "prioridade", "ativo", "confiavel"])
                         for x in s.exec(select(NcmRegra).order_by(NcmRegra.id)).all()],
            "parametroktc": [_linha(x, ["id", "chave", "escopo", "valor", "ativo"])
                             for x in s.exec(select(ParametroKTC)
                                             .order_by(ParametroKTC.id)).all()],
            "materialpreco": [_linha(x, ["id", "material", "thread_count", "weave",
                                         "price_usd_m2", "ativo"])
                              for x in s.exec(select(MaterialPreco)
                                              .order_by(MaterialPreco.id)).all()],
            "cmtpreco": [_linha(x, ["id", "familia", "construcao", "cmt_usd", "ativo"])
                         for x in s.exec(select(CmtPreco).order_by(CmtPreco.id)).all()],
            "toalhapreco": [_linha(x, ["id", "familia", "subcategoria", "gsm", "price_usd_kg",
                                       "preco_final", "ativo"])
                            for x in s.exec(select(ToalhaPreco)
                                            .order_by(ToalhaPreco.id)).all()],
            "fornecedor": [_linha(x, ["id", "codigo", "nome", "tipo", "moeda_custo",
                                      "cost_method_padrao", "ativo"])
                           for x in s.exec(select(Fornecedor).order_by(Fornecedor.id)).all()],
        }

    com_custo = [linha for linha in linhas if linha["custo_unitario"]]
    resumo = {
        "skus": len(linhas),
        "skus_com_custo": len(com_custo),
        "skus_sem_custo": len(linhas) - len(com_custo),
        "skus_precisa_revisao": sum(1 for linha in linhas if linha["precisa_revisao"]),
        "cenarios_fiscais": len(cenarios),
        "condicoes_pagamento": len(CONDICOES),
        "celulas_da_grade": sum(1 for linha in linhas for v in linha["grade"].values()
                                if v is not None),
        "cotacoes": len(cotacoes),
        "itens": len(itens),
        "bases_importacao": len(bases),
        "faturamento_total_historico": sum(c["totais"]["faturamento"] for c in cotacoes),
        "lucro_total_historico": sum(c["totais"]["lucro"] for c in cotacoes),
    }

    saida = {
        "meta": {
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "gerado_por": "scripts/baseline_regressao_v2.py",
            "fase": "Fase 0 — Fundação",
            "banco": os.path.abspath(db_path),
            "limite_aplicado": limite or None,
            "cenarios": [list(c) for c in CENARIOS],
            "condicoes": CONDICOES,
            "observacao": ("Retrato do comportamento ATUAL, com os bugs conhecidos do audit "
                           "ainda presentes. Não corrigir nada aqui para 'melhorar' o "
                           "baseline: a correção é da onda correspondente e a diferença "
                           "precisa ficar explicável."),
        },
        "resumo": resumo,
        "cenarios_fiscais": cenarios,
        "grade_regras": {k: v["resumo"] for k, v in grade_regras.items()},
        "premissas": premissas,
        "produtos": linhas,
        "cotacoes": cotacoes,
        "itens": itens,
        "bases_importacao": bases,
    }
    if com_estado_banco:
        saida["estado_banco"] = estado_banco(db_path)
    return saida


def comparaveis(baseline: dict) -> dict:
    """Só o que precisa ser reprodutível — tira carimbo de tempo e digest de arquivo."""
    return {k: v for k, v in baseline.items() if k not in ("meta", "estado_banco")}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DB_PATH)
    p.add_argument("--saida", default=SAIDA)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--verificar", action="store_true",
                   help="regenera e compara com o arquivo salvo, sem sobrescrever")
    a = p.parse_args()

    baseline = gerar(a.db, limite=a.limite)

    if a.verificar:
        with open(a.saida) as f:
            salvo = json.load(f)
        atual = json.loads(json.dumps(comparaveis(baseline), ensure_ascii=False))
        igual = atual == comparaveis(salvo)
        print("REPRODUTÍVEL: " + ("SIM — números idênticos ao arquivo salvo"
                                  if igual else "NÃO — houve divergência"))
        if not igual:
            for secao in atual:
                if atual[secao] != comparaveis(salvo).get(secao):
                    print(f"  divergiu: {secao}")
        return 0 if igual else 1

    os.makedirs(os.path.dirname(os.path.abspath(a.saida)), exist_ok=True)
    with open(a.saida, "w") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=1)

    r = baseline["resumo"]
    print(f"baseline salvo em {a.saida} ({os.path.getsize(a.saida) / 1e6:.1f} MB)")
    print(f"  {r['skus']} SKUs ({r['skus_sem_custo']} sem custo) × "
          f"{r['cenarios_fiscais']} cenários × {r['condicoes_pagamento']} condições "
          f"= {r['celulas_da_grade']} células")
    print(f"  {r['cotacoes']} cotações · {r['itens']} itens · "
          f"{r['bases_importacao']} bases de importação")
    print(f"  faturamento histórico R$ {r['faturamento_total_historico']:,.2f} · "
          f"lucro R$ {r['lucro_total_historico']:,.2f}")
    print("\ncenários fiscais capturados:")
    for chave, c in baseline["cenarios_fiscais"].items():
        print(f"  {chave:38s} ICMS {c['icms']:.4f}  {c['regra'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

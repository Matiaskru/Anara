"""Relatório de qualidade da base — o que dá para cotar com segurança e o que precisa de atenção.

A regra do projeto é que qualidade de dado vale mais que cobertura: é melhor dizer "precisa
validar custo" do que apresentar um preço bonito apoiado em nada. Este módulo produz esse
retrato, e é o mesmo material que alimenta a tela de relatório e o arquivo JSON.
"""
from collections import Counter, defaultdict

from sqlmodel import Session, select

from app import pricing_service as ps
from app.models import CustoReferencia, Fornecedor, Produto


def qualidade_da_base(session: Session) -> dict:
    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}

    por_fornecedor = Counter()
    por_metodo = Counter()
    por_confianca = Counter()
    por_frescor = Counter()
    por_familia = Counter()
    problemas = []
    listas = defaultdict(list)

    for p in produtos:
        fornecedor = fornecedores.get(p.fornecedor_id)
        nome_fornecedor = fornecedor.nome if fornecedor else "— sem fornecedor —"
        por_fornecedor[nome_fornecedor] += 1
        por_metodo[p.cost_method or "—"] += 1
        por_confianca[p.custo_confianca or "—"] += 1
        por_familia[p.familia or "—"] += 1

        frescor = ps.frescor(session, p.custo_ref_data)
        por_frescor[frescor["status"]] += 1

        registro = {
            "id": p.id, "nome": p.nome, "categoria": p.categoria, "familia": p.familia,
            "fornecedor": nome_fornecedor, "cost_method": p.cost_method,
            "confianca": p.custo_confianca, "custo": p.custo_unitario,
            "preco_base": p.preco_base, "margem_padrao": p.margem_padrao_pct,
            "custo_ref": p.custo_ref_valor, "custo_ref_data": p.custo_ref_data,
            "custo_ref_documento": p.custo_ref_documento,
            "custo_ref_cliente": p.custo_ref_cliente,
            "frescor": frescor["status"], "dias": frescor["dias"],
            "exw_cotado": p.exw_cotado_usd, "exw_calculado": p.exw_calculado_usd,
            "diferenca_pct": p.exw_diferenca_pct,
            "peso_kg": p.peso_kg, "peso_tipo": p.peso_tipo,
            "motivo": p.revisao_motivo,
        }

        chave_fornecedor = fornecedor.codigo if fornecedor else "SEM_FORNECEDOR"
        listas[chave_fornecedor].append(registro)
        if p.cost_method == "KTC_CALCULATED":
            listas["ktc_calculados"].append(registro)
        if p.cost_method == "KTC_QUOTED":
            listas["ktc_cotados"].append(registro)
        if frescor["status"] == "STALE":
            listas["stale"].append(registro)
        if p.precisa_revisao:
            listas["revisao"].append(registro)
            problemas.append(registro)
        if not p.custo_unitario:
            listas["sem_custo"].append(registro)

    referencias = session.exec(select(CustoReferencia)).all()

    return {
        "total": len(produtos),
        "por_fornecedor": dict(por_fornecedor),
        "por_metodo": dict(por_metodo),
        "por_confianca": dict(por_confianca),
        "por_frescor": dict(por_frescor),
        "por_familia": dict(sorted(por_familia.items(), key=lambda kv: -kv[1])),
        "referencias_registradas": len(referencias),
        "referencias_nao_aplicadas": sum(1 for r in referencias if not r.aplicado),
        "listas": {k: v for k, v in listas.items()},
        "problemas": problemas,
    }


def indicadores(session: Session) -> list:
    """Algodão, petróleo e idade das tabelas de material — acompanhamento, não gatilho de preço.

    Nenhum desses números entra em fórmula de custo. Eles servem para o Matias decidir **quando
    pedir preço novo para a KTC**; o preço só muda quando a KTC confirmar material novo.
    """
    from datetime import date

    from app import config_service as cfg
    from app.models import MaterialPreco

    linhas = []
    for chave, rotulo in [("indice_algodao", "Índice de algodão"),
                          ("indice_petroleo", "Índice de petróleo")]:
        premissa = cfg.premissa(session, chave)
        if premissa is None:
            continue
        linhas.append({"rotulo": rotulo, "valor": premissa.valor_num,
                       "detalhe": f"desde {premissa.valid_from:%d/%m/%Y}",
                       "nota": "Indicador de acompanhamento — não altera preço automaticamente."})

    for material in cfg.materiais(session):
        dias = (date.today() - material.valid_from).days if material.valid_from else None
        if dias is not None and dias > 30:
            linhas.append({
                "rotulo": f"{material.material} ({material.plain_or_stripe})",
                "valor": material.price_usd_m2,
                "detalhe": f"sem atualização há {dias} dias",
                "nota": "Preço de material parado — vale confirmar com a KTC."})
    return linhas


def perguntas_para_ktc(session: Session) -> list:
    """O que ainda falta pedir para a KTC para fechar os buracos da base."""
    dados = qualidade_da_base(session)
    listas = dados["listas"]
    perguntas = []

    sem_rastro = [p for p in listas.get("revisao", [])
                  if p["cost_method"] == "LEGACY_EXCEL"]
    if sem_rastro:
        perguntas.append({
            "assunto": "Preço atual dos SKUs sem cotação rastreável",
            "quantos": len(sem_rastro),
            "pedido": "Cotação atualizada (ou confirmação do último preço) para os SKUs que hoje só "
                      "têm custo herdado da planilha antiga, sem documento de origem.",
            "exemplos": [p["nome"] for p in sem_rastro[:8]],
        })

    estimados = [p for p in listas.get("KTC", []) if p["peso_tipo"] and p["peso_tipo"] != "REAL KTC"]
    if estimados:
        perguntas.append({
            "assunto": "Peso real por SKU",
            "quantos": len(estimados),
            "pedido": "Peso líquido real por peça — hoje esses SKUs usam peso estimado por área × "
                      "GSM, e o peso é o que define o frete internacional.",
            "exemplos": [p["nome"] for p in estimados[:8]],
        })

    toalhas = [p for p in listas.get("KTC", [])
               if (p["familia"] or "") in ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel",
                                           "Wash Cloth")]
    if toalhas:
        perguntas.append({
            "assunto": "Preço por kg de fio por construção de toalha",
            "quantos": len(toalhas),
            "pedido": "Tabela de preço por kg por construção (composição, GSM, fio simples ou "
                      "retorcido, liso ou listrado). Hoje só temos os dois valores genéricos "
                      "(US$ 8,00 fio simples e US$ 8,50 retorcido), que a própria KTC disse variar "
                      "por construção — por isso nenhuma toalha é calculada industrialmente.",
            "exemplos": [p["nome"] for p in toalhas[:8]],
        })

    perguntas.append({
        "assunto": "Fórmula industrial das famílias que faltam",
        "quantos": None,
        "pedido": "Regra de consumo/allowance para fronha (oxford e com aba), lençol com elástico "
                  "(altura e cantos), roupão, chinelo, protetor e topper de colchão, e insert de "
                  "edredom. Sem isso essas famílias continuam por preço cotado — o sistema não "
                  "inventa fórmula.",
        "exemplos": [],
    })
    perguntas.append({
        "assunto": "Custo dos acabamentos",
        "quantos": None,
        "pedido": "Custo de bordado (por 1.000 pontos), botões, snap, zíper e flange, para poder "
                  "somar em 'outros custos' e fechar o cálculo de peças com acabamento.",
        "exemplos": [],
    })
    perguntas.append({
        "assunto": "NCM dos roupões",
        "quantos": None,
        "pedido": "Confirmação do NCM correto do roupão. O cadastro atual (6309.00.10) é de artigos "
                  "usados; hoje a Anara aplica 3,5% por regra de família. Se a regra cair sem NCM "
                  "correto, a alíquota vira 35%.",
        "exemplos": [],
    })
    return perguntas

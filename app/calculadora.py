"""Calculadora de custo KTC — o preço de um produto que ainda não está no catálogo.

É a memória do preço rodando ao contrário: em vez de partir de um SKU salvo, parte do que a
pessoa digita (família, medida, tecido, gramatura) e devolve o mesmo waterfall — especificação
→ motor industrial → EXW → nacionalização → custo NET → cenário fiscal → margem → preço.

Serve para o momento em que o cliente pede uma medida que não existe no catálogo. Como usa
exatamente os mesmos motores da cotação, o número que sai aqui é o mesmo que sairia se o
produto estivesse cadastrado — não existe "conta paralela".

Quando a família não tem fórmula validada pela KTC, a calculadora **não inventa**: diz que não
sabe e oferece registrar o pedido, para virar cotação a pedir ao fornecedor.
"""
from typing import Optional

from sqlmodel import Session, select

from app import config_service as cfg
from app import pricing_service as ps
from app.models import (
    CostConfidence, CostMethod, Cotacao, Fornecedor, MaterialPreco, Produto, ToalhaPreco,
)
from app.spec_parser import FAMILIAS_CALCULAVEIS

FAMILIAS_TOALHA = ("Bath Towel", "Hand Towel", "Bath Mat", "Pool Towel", "Wash Cloth")

# família → (rótulo em português, categoria do catálogo, tipo de entrada)
FAMILIAS = [
    ("Flat Sheet", "Lençol plano", "Lençol Plano", "tecido"),
    ("Top Sheet", "Lençol de cima", "Lençol Plano", "tecido"),
    ("Duvet Cover", "Capa duvet", "Capa Duvet", "tecido"),
    ("Bath Towel", "Toalha de banho", "Toalha Banho", "toalha"),
    ("Hand Towel", "Toalha de rosto", "Toalha Rosto", "toalha"),
    ("Bath Mat", "Toalha de piso", "Toalha Piso", "toalha"),
    ("Pool Towel", "Toalha de piscina", "Toalha Piscina", "toalha"),
    ("Wash Cloth", "Toalha de lavabo", "Toalha Lavabo", "toalha"),
    ("Fitted Sheet", "Lençol com elástico", "Lençol com Elástico", "sem_formula"),
    ("Pillow Case", "Fronha", "Fronha com Aba", "sem_formula"),
    ("Bathrobe", "Roupão", "Roupão", "sem_formula"),
    ("Duvet Insert", "Edredom / insert", "Edredom / Insert", "sem_formula"),
    ("Mattress Protector", "Protetor de colchão", "Protetor Colchão", "sem_formula"),
    ("Mattress Topper", "Topper de colchão", "Topper Colchão", "sem_formula"),
    ("Slipper", "Chinelo", "Chinelos", "sem_formula"),
]

CATEGORIA_POR_FAMILIA = {f: categoria for f, _rotulo, categoria, _tipo in FAMILIAS}
ROTULO_POR_FAMILIA = {f: rotulo for f, rotulo, _categoria, _tipo in FAMILIAS}
TIPO_POR_FAMILIA = {f: tipo for f, _rotulo, _categoria, tipo in FAMILIAS}


def opcoes(session: Session) -> dict:
    """O que a tela precisa oferecer: só tecido que a KTC realmente cotou."""
    materiais = []
    for m in cfg.materiais(session):
        materiais.append({
            "id": m.id, "material": m.material, "plain_or_stripe": m.plain_or_stripe,
            "thread_count": m.thread_count, "cotton_pct": m.cotton_pct,
            "poliester_pct": m.poliester_pct, "price_usd_m2": m.price_usd_m2,
            "rotulo": f"{m.material} · {'listrado' if m.plain_or_stripe == 'stripe' else 'liso'} "
                      f"· US$ {m.price_usd_m2:.2f}/m²",
        })
    toalhas = [{"subcategoria": t.subcategoria, "plain_or_stripe": t.plain_or_stripe,
                "price_usd_kg": t.price_usd_kg, "fonte": t.fonte}
               for t in session.exec(select(ToalhaPreco)).all() if t.ativo and t.subcategoria]
    return {
        "familias": [{"familia": f, "rotulo": r, "tipo": t} for f, r, _c, t in FAMILIAS],
        "materiais": materiais,
        "toalhas": toalhas,
    }


def produto_simulado(session: Session, familia: str, largura_cm: Optional[float],
                     comprimento_cm: Optional[float], material_id: Optional[int] = None,
                     gsm: Optional[int] = None, plain_or_stripe: str = "plain",
                     outros_custos_usd: float = 0.0,
                     acabamento: Optional[str] = None) -> Produto:
    """Monta um Produto **em memória** — não vai para o banco. Serve só para alimentar os
    mesmos motores que a cotação usa, sem duplicar regra nenhuma."""
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    material = session.get(MaterialPreco, material_id) if material_id else None

    produto = Produto(
        sku_key="__simulado__", nome=ROTULO_POR_FAMILIA.get(familia, familia),
        categoria=CATEGORIA_POR_FAMILIA.get(familia), familia=familia,
        fornecedor_id=ktc.id if ktc else None,
        cost_method=CostMethod.ktc_calculated.value,
        custo_confianca=CostConfidence.calculated.value,
        largura_cm=largura_cm, comprimento_cm=comprimento_cm,
        gsm=gsm, plain_or_stripe=plain_or_stripe, acabamento=acabamento,
    )
    if material:
        produto.material_ref = material.material
        produto.thread_count = material.thread_count
        produto.cotton_pct = material.cotton_pct
        produto.poliester_pct = material.poliester_pct
        produto.weave = material.weave
        produto.plain_or_stripe = material.plain_or_stripe
    produto.nome = _nome_simulado(produto)
    return produto


def _nome_simulado(produto: Produto) -> str:
    """Nome de exibição do simulado. Sem campo estruturado nenhum (o caso do pedido de roupão),
    fica só o rótulo da família — melhor do que 'Produto simulado'."""
    from app.nomes import nome_canonico
    produto.nome_original = produto.nome
    return nome_canonico(produto)


def calcular(session: Session, familia: str, largura_cm: Optional[float],
             comprimento_cm: Optional[float], material_id: Optional[int] = None,
             gsm: Optional[int] = None, plain_or_stripe: str = "plain",
             quantidade: float = 1, outros_custos_usd: float = 0.0,
             margem_override: Optional[float] = None, acabamento: Optional[str] = None,
             cotacao: Optional[Cotacao] = None) -> dict:
    """Devolve a memória do preço completa, no mesmo formato da tela de memória da cotação."""
    if TIPO_POR_FAMILIA.get(familia) == "sem_formula" or familia not in FAMILIAS_CALCULAVEIS:
        return {
            "calculavel": False,
            "familia": familia,
            "motivo": ("A KTC nunca demonstrou a regra de consumo dessa família, então o sistema "
                       "não calcula — calcular seria chutar. Dá para registrar o pedido e pedir "
                       "cotação a eles."),
        }

    produto = produto_simulado(session, familia, largura_cm, comprimento_cm, material_id, gsm,
                               plain_or_stripe, outros_custos_usd, acabamento)
    memoria = ps.memoria_do_preco(session, produto, cotacao, quantidade=quantidade,
                                  margem_override=margem_override)
    memoria["calculavel"] = memoria.get("comercial") is not None
    memoria["outros_custos_usd"] = outros_custos_usd
    memoria["quantidade"] = quantidade
    memoria["aviso_preco"] = (
        "Preço calculado com o preço de material que está cadastrado hoje. Nas últimas cotações "
        "a KTC praticou de 4% a 15% abaixo disso — é preço para cotar, não custo de compra fechado.")
    return memoria


def salvar_no_catalogo(session: Session, familia: str, largura_cm: Optional[float],
                       comprimento_cm: Optional[float], material_id: Optional[int] = None,
                       gsm: Optional[int] = None, plain_or_stripe: str = "plain",
                       acabamento: Optional[str] = None, calculavel: bool = True,
                       observacao: Optional[str] = None) -> Produto:
    """Grava o produto simulado no catálogo, para poder ser cotado e reaproveitado.

    Calculável entra com o custo do motor industrial. Sem fórmula entra sem custo, marcado para
    revisão — vira um pedido de cotação à KTC, e aparece no relatório de qualidade.
    """
    produto = produto_simulado(session, familia, largura_cm, comprimento_cm, material_id, gsm,
                               plain_or_stripe, acabamento=acabamento)
    medida = (f"{int(largura_cm)}x{int(comprimento_cm)}"
              if largura_cm and comprimento_cm else "sem medida")
    detalhe = produto.material_ref or (f"{gsm} GSM" if gsm else "sem tecido")
    produto.sku_key = f"CALC · {familia} · {medida} · {detalhe} · {plain_or_stripe}"

    existente = session.exec(select(Produto).where(Produto.sku_key == produto.sku_key)).first()
    if existente:
        return existente

    if calculavel and familia in FAMILIAS_CALCULAVEIS:
        memoria = ps.custo_net(session, produto)
        produto.custo_unitario = memoria.get("net_brl")
        produto.custo_net_usd = memoria.get("net_usd")
        produto.exw_calculado_usd = (memoria.get("industrial") or {}).get("exw_usd")
        produto.precisa_revisao = False
        produto.revisao_motivo = ("Criado pela calculadora a partir dos parâmetros atuais da KTC. "
                                  "Ainda não foi cotado com a fábrica.")
        produto.custo_ref_tipo = "EXW_CALCULATED"
        produto.custo_ref_documento = "Calculadora de custo KTC"
    else:
        produto.cost_method = CostMethod.manual.value
        produto.custo_confianca = CostConfidence.review_required.value
        produto.custo_unitario = None
        produto.precisa_revisao = True
        produto.revisao_motivo = ("Pedido registrado pela calculadora: família sem fórmula "
                                  "industrial confirmada. Precisa de cotação da KTC.")
        produto.custo_ref_tipo = "A_COTAR"
        produto.custo_ref_documento = "Pedido registrado na calculadora"

    if observacao:
        produto.custo_ref_nota = observacao

    peso = ps.peso_do_produto(session, produto)
    if peso.peso_kg:
        produto.peso_kg, produto.peso_tipo, produto.peso_fonte = (peso.peso_kg, peso.tipo,
                                                                  peso.fonte)
    margem = ps.margem_padrao(session, produto)
    produto.margem_padrao_pct = margem.margem_pct
    if produto.custo_unitario:
        from app.pricing_engine import calcular_por_margem
        # O preço-base do catálogo é do PRODUTO: o cenário fiscal é resolvido com ele, não
        # em abstrato. Sem cenário resolvido, o produto fica sem preço-base — não se inventa.
        regras, ctx = ps.regras_da_cotacao(session, ps.cenario_padrao_catalogo(session), produto)
        if regras is not None:
            produto.preco_base = calcular_por_margem(produto.custo_unitario, 1,
                                                     margem.margem_pct, regras).preco_negociado
        else:
            produto.preco_base = None
            produto.precisa_revisao = True
            produto.revisao_motivo = f"Preço-base não formado: {ctx.get('motivo_bloqueio')}"

    session.add(produto)
    session.commit()
    session.refresh(produto)
    return produto

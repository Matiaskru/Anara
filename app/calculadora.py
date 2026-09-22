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
from app.dinheiro import D0, para_float
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
    ("Pillow Case", "Fronha", "Fronha com Aba", "fronha"),
    ("Bathrobe", "Roupão", "Roupão", "sem_formula"),
    ("Duvet Insert", "Edredom / insert", "Edredom / Insert", "sem_formula"),
    ("Mattress Protector", "Protetor de colchão", "Protetor Colchão", "sem_formula"),
    ("Mattress Topper", "Topper de colchão", "Topper Colchão", "sem_formula"),
    ("Slipper", "Chinelo", "Chinelos", "sem_formula"),
]

CATEGORIA_POR_FAMILIA = {f: categoria for f, _rotulo, categoria, _tipo in FAMILIAS}
ROTULO_POR_FAMILIA = {f: rotulo for f, rotulo, _categoria, _tipo in FAMILIAS}
TIPO_POR_FAMILIA = {f: tipo for f, _rotulo, _categoria, tipo in FAMILIAS}


def opcoes(session: Session, economia: bool = True) -> dict:
    """O que a tela precisa oferecer: só tecido que a KTC realmente cotou.

    `economia=False` (vendedora): o tecido é escolhido pelo nome, sem o preço por m² — esse é
    custo de fornecedor e não sai do servidor para o navegador dela. As taxas por kg de toalha
    também não: ela não precisa delas para escolher gramatura e tamanho.
    """
    materiais = []
    for m in cfg.materiais(session):
        rotulo = f"{m.material} · {'listrado' if m.plain_or_stripe == 'stripe' else 'liso'}"
        linha = {"id": m.id, "material": m.material, "plain_or_stripe": m.plain_or_stripe,
                 "thread_count": m.thread_count, "cotton_pct": m.cotton_pct,
                 "poliester_pct": m.poliester_pct,
                 "rotulo": rotulo + (f" · US$ {m.price_usd_m2:.2f}/m²" if economia else "")}
        if economia:
            linha["price_usd_m2"] = m.price_usd_m2
        materiais.append(linha)
    toalhas = ([{"subcategoria": t.subcategoria, "plain_or_stripe": t.plain_or_stripe,
                 "price_usd_kg": t.price_usd_kg, "fonte": t.fonte}
                for t in session.exec(select(ToalhaPreco)).all() if t.ativo and t.subcategoria]
               if economia else [])
    return {
        "familias": [{"familia": f, "rotulo": r, "tipo": t} for f, r, _c, t in FAMILIAS],
        "materiais": materiais,
        "toalhas": toalhas,
    }


#: Composições de toalha oferecidas na calculadora e no catálogo. Decisão da KTC (21/09/2026):
#: 100% algodão e 90/10 têm o MESMO preço industrial quando tamanho, GSM, subcategoria e
#: construção são iguais — a composição identifica o SKU, não muda o custo.
COMPOSICOES_TOALHA = {"100/0": (1.0, 0.0), "90/10": (0.9, 0.1)}


def produto_simulado(session: Session, familia: str, largura_cm: Optional[float],
                     comprimento_cm: Optional[float], material_id: Optional[int] = None,
                     gsm: Optional[int] = None, plain_or_stripe: str = "plain",
                     outros_custos_usd: float = 0.0,
                     acabamento: Optional[str] = None, abas: Optional[int] = None,
                     flap_cm: Optional[float] = None, festone: bool = False,
                     composicao_toalha: Optional[str] = None) -> Produto:
    """Monta um Produto **em memória** — não vai para o banco. Serve só para alimentar os
    mesmos motores que a cotação usa, sem duplicar regra nenhuma."""
    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    # Tecido só existe onde o motor industrial consome tecido: lençol/capa duvet ("tecido") e
    # fronha. **Toalha não tem fios** — ela é cotada por área × GSM (`ToalhaPreco`, US$/kg), e
    # aceitar um `material_id` aqui gravava o `thread_count` do tecido no SKU: a toalha de
    # 650 g/m² virava "200 fios" no nome, no catálogo, na cotação e no PDF (22/09/2026). O
    # formulário manda o campo mesmo escondido, então a recusa é do servidor.
    material = (session.get(MaterialPreco, material_id)
                if (material_id and TIPO_POR_FAMILIA.get(familia) in ("tecido", "fronha")) else None)

    produto = Produto(
        sku_key="__simulado__", nome=ROTULO_POR_FAMILIA.get(familia, familia),
        categoria=CATEGORIA_POR_FAMILIA.get(familia), familia=familia,
        fornecedor_id=ktc.id if ktc else None,
        cost_method=CostMethod.ktc_calculated.value,
        custo_confianca=CostConfidence.calculated.value,
        largura_cm=largura_cm, comprimento_cm=comprimento_cm,
        gsm=gsm, plain_or_stripe=plain_or_stripe, acabamento=acabamento,
    )
    if TIPO_POR_FAMILIA.get(familia) == "toalha":
        algodao, poliester = COMPOSICOES_TOALHA.get(composicao_toalha or "100/0", (1.0, 0.0))
        produto.cotton_pct, produto.poliester_pct = algodao, poliester
        # a especificação técnica da toalha é a gramatura; fios não se aplica e fica NULO
        produto.thread_count = None
    if TIPO_POR_FAMILIA.get(familia) == "fronha":
        # A construção da fronha (§18) viaja no cadastro estruturado — é de lá que o motor
        # lê abas, flap e festonê. Sem informar, vale o standard: 0 abas, flap 20 cm.
        produto.construcao = f"{int(abas or 0)} abas" if abas else "standard"
        produto.fechamento = f"flap {flap_cm:g} cm" if flap_cm else "flap 20 cm"
        if festone:
            produto.acabamento = ((acabamento + " · ") if acabamento else "") + "festonê"
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


class EntradaInvalida(ValueError):
    """Medida, gramatura ou construção fora do que o motor aceita. Nunca vira preço."""


def validar_entrada(familia: str, largura_cm, comprimento_cm, gsm=None, abas=None,
                    flap_cm=None) -> None:
    """Recusa o que não é uma peça: medida ou gramatura zero/negativa/não numérica, número de
    abas fora do §18, flap não positivo. Até 17/09/2026 largura −190 produzia consumo
    negativo e a nacionalização devolvia um custo pequeno e positivo que virava preço."""
    def positivo(v):
        try:
            return v is not None and float(v) > 0 and float(v) != float("inf")
        except (TypeError, ValueError):
            return False
    tipo = TIPO_POR_FAMILIA.get(familia)
    if tipo in ("tecido", "fronha", "toalha"):
        if not positivo(largura_cm) or not positivo(comprimento_cm):
            raise EntradaInvalida("Largura e comprimento precisam ser números maiores que zero.")
        if float(largura_cm) > 1000 or float(comprimento_cm) > 1000:
            raise EntradaInvalida("Medida acima de 10 m não é uma peça de enxoval — confira a unidade (cm).")
    if tipo == "toalha" and not positivo(gsm):
        raise EntradaInvalida("Gramatura (g/m²) precisa ser maior que zero.")
    if tipo == "fronha":
        if abas not in (None, 0, 2, 3, 4):
            raise EntradaInvalida("Fronha só com 0, 2, 3 ou 4 abas (§18).")
        if flap_cm is not None and not positivo(flap_cm):
            raise EntradaInvalida("Flap precisa ser maior que zero.")


def calcular(session: Session, familia: str, largura_cm: Optional[float],
             comprimento_cm: Optional[float], material_id: Optional[int] = None,
             gsm: Optional[int] = None, plain_or_stripe: str = "plain",
             quantidade: float = 1, outros_custos_usd: float = 0.0,
             margem_override: Optional[float] = None, acabamento: Optional[str] = None,
             cotacao: Optional[Cotacao] = None, abas: Optional[int] = None,
             flap_cm: Optional[float] = None, festone: bool = False,
             composicao_toalha: Optional[str] = None) -> dict:
    """Devolve a memória do preço completa, no mesmo formato da tela de memória da cotação."""
    if TIPO_POR_FAMILIA.get(familia) == "sem_formula" or familia not in FAMILIAS_CALCULAVEIS:
        return {
            "calculavel": False,
            "familia": familia,
            "motivo": ("A KTC nunca demonstrou a regra de consumo dessa família, então o sistema "
                       "não calcula — calcular seria chutar. Dá para registrar o pedido e pedir "
                       "cotação a eles."),
        }

    try:
        validar_entrada(familia, largura_cm, comprimento_cm, gsm, abas, flap_cm)
    except EntradaInvalida as e:
        return {"calculavel": False, "familia": familia, "motivo": str(e), "entrada_invalida": True}
    produto = produto_simulado(session, familia, largura_cm, comprimento_cm, material_id, gsm,
                               plain_or_stripe, outros_custos_usd, acabamento,
                               abas=abas, flap_cm=flap_cm, festone=festone,
                               composicao_toalha=composicao_toalha)
    memoria = ps.memoria_do_preco(session, produto, cotacao, quantidade=quantidade,
                                  margem_override=margem_override)
    memoria["calculavel"] = memoria.get("comercial") is not None
    memoria["outros_custos_usd"] = outros_custos_usd
    memoria["quantidade"] = quantidade
    memoria["aviso_preco"] = (
        "Preço calculado com o preço de material que está cadastrado hoje. Nas últimas cotações "
        "a KTC praticou de 4% a 15% abaixo disso — é preço para cotar, não custo de compra fechado.")
    return memoria


#: O que a calculadora devolve a quem **não** vê economia. Lista de permissão: o resultado
#: comercial é construído campo a campo a partir da memória, nunca "a memória sem alguns
#: campos" — é a mesma regra do `confidencial.py`, aplicada à tela de produto personalizado.
CAMPOS_RESULTADO_COMERCIAL = (
    "calculavel", "familia", "familia_rotulo", "motivo", "entrada_invalida", "nome",
    "especificacao", "quantidade", "preco_b2b", "preco_tabela", "preco_proposto",
    "desconto_vs_tabela_pct", "comissao_estimada_pct", "comissao_estimada_valor", "total",
    "situacao", "situacao_rotulo", "situacao_explicacao", "pendencias", "pode_adicionar",
    "aviso",
)

#: Pendência interna → frase operacional que a vendedora pode agir em cima, sem economia.
#: Nunca se repassa o texto interno do motor (ele fala de peso de embarque, I.I., proteção
#: comercial e alíquota — vocabulário que é economia).
PENDENCIA_COMERCIAL = {
    "peso": "A fábrica ainda não confirmou o peso desta peça. Dá para cotar; o administrativo "
            "confirma antes do compromisso firme.",
    "protecao_comercial": "Esta família ainda não tem regra de preço cadastrada. Peça ao "
                          "administrativo para cadastrá-la antes de usar na proposta.",
}
AVISO_COMERCIAL = ("Preço calculado com os parâmetros de hoje da fábrica. Vale para propor; "
                   "o compromisso firme depende da confirmação do administrativo.")
SEM_PRECO_COMERCIAL = ("Este produto ainda não forma preço automático. Registre o pedido e o "
                       "administrativo cota com a fábrica.")


def resultado_comercial(memoria: dict) -> dict:
    """A resposta da calculadora para VENDEDOR_INTERNO / VENDEDOR_COMISSIONADO.

    Mesma conta, mesmos motores: o que muda é o que volta na resposta. Entra o que ela precisa
    para negociar — descrição, B2B (piso de autonomia), tabela, preço proposto, desconto, a
    comissão dela, total e a situação em linguagem comercial. **Não** entra custo, CNET, EXW,
    base comercial, proteção, I.I., margem (alvo ou realizada), lucro, markup, nacionalização,
    memória nem premissa de formação — nada disso é montado aqui, então não há o que vazar.

    A comissão sai da mesma função canônica da cotação (`politica_comercial.comissao_do_item`);
    a base comissionável que a produz fica de fora, como na tela de negociação.
    """
    from app import politica_comercial as pol
    from app import pricing_service as ps
    from app import rotulos
    from app.confidencial import apenas, sem_confidenciais

    if not memoria.get("calculavel"):
        # dois casos caem aqui: família sem fórmula / entrada inválida (o motor devolve
        # `motivo`) e produto que a política não precifica (sem regra de margem, cenário
        # bloqueado) — nos dois, a vendedora recebe o que falta, nunca um número.
        motivo = memoria.get("motivo") or SEM_PRECO_COMERCIAL
        return {"calculavel": False, "familia": memoria.get("familia"),
                "motivo": motivo, "pendencias": [motivo],
                "entrada_invalida": bool(memoria.get("entrada_invalida")),
                "pode_adicionar": False}

    produto = memoria.get("produto") or {}
    custo = memoria.get("custo") or {}
    comercial = memoria.get("comercial") or {}
    b2b = memoria.get("b2b") or {}
    quantidade = D0(memoria.get("quantidade") or 1)
    situacao = ps.status_canonico_do_custo(custo.get("net_brl"), custo)
    pendencias = [PENDENCIA_COMERCIAL[p] for p in (custo.get("premissas_faltantes") or [])
                  if p in PENDENCIA_COMERCIAL]
    if memoria.get("bloqueado"):
        pendencias.append("O cenário desta cotação ainda não está resolvido (destino, "
                          "condição de pagamento ou frete). Ajuste no cabeçalho da cotação.")

    corpo = {
        "calculavel": True,
        "familia": produto.get("familia") or memoria.get("familia"),
        "nome": produto.get("nome"),
        "especificacao": produto.get("especificacao") or produto.get("nome"),
        "quantidade": para_float(quantidade),
        "situacao": situacao,
        "situacao_rotulo": rotulos.situacao_comercial(situacao),
        "situacao_explicacao": rotulos.explicacao_comercial(situacao),
        "pendencias": pendencias,
        "aviso": AVISO_COMERCIAL,
        "pode_adicionar": bool(comercial.get("preco_negociado")),
    }
    if b2b.get("preco_b2b") and b2b.get("preco_tabela"):
        preco = D0(comercial.get("preco_negociado") or b2b["preco_b2b"])
        tabela, piso = D0(b2b["preco_tabela"]), D0(b2b["preco_b2b"])
        c = pol.comissao_do_item(tabela, piso, preco, quantidade,
                                 D0(b2b.get("icms_base_comissao_pct")))
        corpo.update({
            "preco_b2b": para_float(piso), "preco_tabela": para_float(tabela),
            "preco_proposto": para_float(preco),
            "desconto_vs_tabela_pct": para_float(c.desconto_vs_tabela),
            "comissao_estimada_pct": para_float(c.taxa_pct),
            "comissao_estimada_valor": para_float(c.comissao_valor),
            "total": comercial.get("faturamento"),
        })
    else:
        # sem política de preço vigente para o produto: não se inventa número comercial
        corpo["pode_adicionar"] = False
        corpo["pendencias"] = pendencias or [SEM_PRECO_COMERCIAL]
    # rede de segurança: a lista de permissão e a varredura recursiva do `confidencial.py`
    return sem_confidenciais(apenas(corpo, CAMPOS_RESULTADO_COMERCIAL))


def salvar_no_catalogo(session: Session, familia: str, largura_cm: Optional[float],
                       comprimento_cm: Optional[float], material_id: Optional[int] = None,
                       gsm: Optional[int] = None, plain_or_stripe: str = "plain",
                       acabamento: Optional[str] = None, calculavel: bool = True,
                       observacao: Optional[str] = None, abas: Optional[int] = None,
                       flap_cm: Optional[float] = None, festone: bool = False,
                       composicao_toalha: Optional[str] = None) -> Produto:
    """Grava o produto simulado no catálogo, para poder ser cotado e reaproveitado.

    Calculável entra com o custo do motor industrial. Sem fórmula entra sem custo, marcado para
    revisão — vira um pedido de cotação à KTC, e aparece no relatório de qualidade.
    """
    if calculavel and familia in FAMILIAS_CALCULAVEIS:
        validar_entrada(familia, largura_cm, comprimento_cm, gsm, abas, flap_cm)   # levanta
    produto = produto_simulado(session, familia, largura_cm, comprimento_cm, material_id, gsm,
                               plain_or_stripe, acabamento=acabamento, abas=abas,
                               flap_cm=flap_cm, festone=festone,
                               composicao_toalha=composicao_toalha)
    medida = (f"{int(largura_cm)}x{int(comprimento_cm)}"
              if largura_cm and comprimento_cm else "sem medida")
    # toalha é identificada pela gramatura; tecido plano e fronha, pelo material
    detalhe = produto.material_ref or (f"{gsm} GSM" if gsm else "sem tecido")
    produto.sku_key = f"CALC · {familia} · {medida} · {detalhe} · {plain_or_stripe}"
    if TIPO_POR_FAMILIA.get(familia) == "toalha":
        # composições diferentes são SKUs diferentes (mesmo custo, por decisão da KTC)
        produto.sku_key += f" · {composicao_toalha or '100/0'}"
    if TIPO_POR_FAMILIA.get(familia) == "fronha":
        # construções diferentes são SKUs diferentes: 4 abas com festonê não é a standard
        produto.sku_key += f" · {produto.construcao} · {produto.fechamento}" + \
            (" · festonê" if festone else "")

    existente = session.exec(select(Produto).where(Produto.sku_key == produto.sku_key)).first()
    if existente:
        return existente

    memoria = {}
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
        from app.pricing_engine import calcular_por_margem, preco_b2b
        from app.politica_comercial import ROTULO_2026_09_21
        # O preço-base do catálogo é do PRODUTO: o cenário fiscal é resolvido com ele, não
        # em abstrato. Sem cenário resolvido, o produto fica sem preço-base — não se inventa.
        # Forma-se sobre a BASE COMERCIAL (referência comercial; 22/09/2026), nunca sobre o
        # custo real — exatamente como o SKU equivalente do catálogo: sem arbitragem entre
        # "customizado" e "catalogado". Política 21/09: preco_base = B2B de referência.
        regras, ctx = ps.regras_da_cotacao(session, ps.cenario_padrao_catalogo(session), produto)
        base = memoria.get("base_comercial_brl") or produto.custo_unitario
        if regras is not None:
            from app.dinheiro import para_float
            if margem.politica == ROTULO_2026_09_21:
                produto.preco_base = para_float(preco_b2b(base, margem.margem_pct, regras).preco_negociado)
            else:
                produto.preco_base = para_float(
                    calcular_por_margem(base, 1, margem.margem_pct, regras).preco_negociado)
        else:
            produto.preco_base = None
            produto.precisa_revisao = True
            produto.revisao_motivo = f"Preço-base não formado: {ctx.get('motivo_bloqueio')}"

    session.add(produto)
    session.commit()
    session.refresh(produto)
    return produto

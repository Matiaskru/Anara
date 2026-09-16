"""O que é confidencial, e como um payload comercial é montado.

Este módulo existe por causa de um erro fácil de cometer: devolver o objeto do ORM inteiro —
ou o dicionário inteiro que a tela de admin usa — para um vendedor, confiando que o template
não vai imprimir os campos. O navegador recebeu; o usuário lê. Filtro de tela não é controle
de acesso.

A regra aqui é **lista de permissão, não lista de bloqueio.** Um payload comercial declara os
campos que pode conter; tudo o mais é descartado. A diferença aparece no dia em que alguém
adiciona `custo_medio_ponderado` ao dicionário: com lista de bloqueio, o campo novo vaza até
alguém lembrar de proibi-lo; com lista de permissão, ele simplesmente não passa.

`CAMPOS_CONFIDENCIAIS` existe assim mesmo, para duas coisas que a lista de permissão não faz:
varrer estruturas aninhadas de forma recursiva, e servir de asserção nos testes.
"""
from typing import Any, Dict, Iterable, Optional

#: Nomes de campo que **nunca** vão para um payload de vendedor, em nenhum nível de
#: aninhamento. A lista usa os nomes reais do sistema — não os genéricos.
CAMPOS_CONFIDENCIAIS = frozenset({
    # custo e sua formação
    "custo", "custo_unitario", "custo_total", "custo_net", "custo_net_usd", "cnet",
    "cnet_brl", "custo_ref_valor", "custo_ref_documento", "custo_ref_tipo", "custo_ref_data",
    "custo_ref_moeda", "valor_bruto", "gross", "source_cost", "supplier_cost",
    "cost_reference", "custo_medio",
    # motor industrial e nacionalização
    "exw_usd", "exw_calculado_usd", "exw_cotado_usd", "exw_diferenca_usd",
    "exw_diferenca_pct", "exw_origem", "exw_cotado_fonte", "preco_ktc_usd", "net_usd",
    "net_brl", "frete_usd_un", "frete_usd_kg", "ii_usd", "ii_aplicado", "material_price_usd_m2",
    "price_usd_kg", "price_usd_m2", "cmt_usd", "cmt_ref", "material_ref", "outras_desp_usd_un",
    "fx_usd_brl", "cambio_usd_brl", "shrinkage", "waste", "quality_allowance", "ktc_margin",
    "industrial", "nacionalizacao", "drivers", "consumo_m2", "area_painel_m2",
    # créditos de compra
    "icms_credito", "pis_cofins_credito", "base_pis_cofins",
    # resultado interno
    "lucro", "lucro_total", "margem", "margem_liquida", "margem_alvo", "margem_real",
    "margem_padrao_pct", "margem_regra", "margem_total", "markup", "markup_implicito",
    "comissao", "comissao_pct", "comissao_valor", "comissao_tabela",
    "impostos", "encargo_pct", "preco_preciso", "difal_valor",
    # frete interno
    "cf_logistico", "rv_logistico_pct", "rv_logistico_valor", "frete_peso", "frete_cf",
    "frete_rv", "tarifa_aplicada", "cf", "cf_unitario",
    # memória e premissas
    "memoria", "memoria_json", "memoria_calculo", "memoria_do_preco", "memoria_fiscal",
    "etapas", "waterfall", "premissas", "cost_method", "custo_confianca",
    # política comercial de 16/09/2026 — a mecânica de proteção da margem é interna. A
    # vendedora vê a comissão estimada da cotação (`comissao_estimada_*`); não vê o piso,
    # a comissão por item, a máxima para o piso nem quanto a negociação consumiu de margem.
    "piso_margem_pct", "piso_pct", "margem_piso", "margem_anterior_pct",
    "comissao_formacao_pct", "comissao_aplicada_pct", "comissao_max_piso_pct",
    "comissao_maxima_piso_pct", "comissao_proporcional_pct", "comissao_variavel_pct",
    "comissao_base_pct", "comissao_min_pct", "comissao_travada_valor",
    "comissao_variavel_valor", "receita_comissionavel", "desconto_ratio_comissao",
    "recomendado_variavel", "negociado_variavel", "limitada_pelo_piso", "limitada_por",
    "absorvido_por_comissao", "absorvido_por_margem", "absorvido_por_impostos_e_frete",
    "margem_realizada_pct", "margem_agregada_pct", "viola_piso", "deficit_unitario",
    "tolerancia_unitaria", "economia", "politica_comercial",
})

#: Campos que um item de cotação pode mostrar a um vendedor. Tudo o mais é cortado.
CAMPOS_ITEM_COMERCIAL = (
    "id", "produto_id", "ordem", "nome_produto", "especificacao", "categoria",
    "quantidade", "preco_negociado", "preco_base", "faturamento",
    "diferenca_pct_vs_base", "modo_edicao", "valor_editado",
    # Fase 3A: o recomendado é a referência que a vendedora negocia a partir de; se a linha é
    # editável (Daune não é) e por quê.
    "preco_recomendado", "editavel", "motivo_nao_editavel", "preco_travado", "total_linha",
)

#: Campos que um produto pode mostrar num resultado de busca comercial.
CAMPOS_PRODUTO_COMERCIAL = (
    "id", "nome", "especificacao", "categoria", "familia", "preco_base",
    "fornecedor", "sem_custo", "thread_count", "gsm", "precisa_revisao", "preco_travado",
)

#: Totais de cotação que um vendedor pode ver: o que ele vai cobrar, e nada sobre o que custa.
CAMPOS_TOTAIS_COMERCIAL = ("faturamento", "num_itens",
                           "comissao_estimada_valor", "comissao_estimada_pct_efetiva")


def _limpar(valor: Any) -> Any:
    """Remove recursivamente qualquer chave confidencial de dicionários e listas."""
    if isinstance(valor, dict):
        return {k: _limpar(v) for k, v in valor.items() if k not in CAMPOS_CONFIDENCIAIS}
    if isinstance(valor, (list, tuple)):
        return [_limpar(v) for v in valor]
    return valor


def sem_confidenciais(payload: Any) -> Any:
    """Rede de segurança: varre a estrutura inteira e corta o que for confidencial.

    Usada **depois** da lista de permissão, não no lugar dela — pega o campo confidencial que
    entrou aninhado dentro de um sub-dicionário que a lista de permissão deixou passar inteiro.
    """
    return _limpar(payload)


def apenas(payload: Dict[str, Any], campos: Iterable[str]) -> Dict[str, Any]:
    """Lista de permissão: devolve só os campos declarados, na ordem declarada."""
    return {c: payload[c] for c in campos if c in payload}


def item_comercial(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Representação de um item de cotação para quem não vê economia."""
    return sem_confidenciais(apenas(payload, CAMPOS_ITEM_COMERCIAL))


def totais_comerciais(payload: Dict[str, Any]) -> Dict[str, Any]:
    return apenas(payload, CAMPOS_TOTAIS_COMERCIAL)


def produto_comercial(payload: Dict[str, Any]) -> Dict[str, Any]:
    return sem_confidenciais(apenas(payload, CAMPOS_PRODUTO_COMERCIAL))


def conforme_papel(payload: Dict[str, Any], pode_ver_economia: bool,
                   comercial=item_comercial) -> Dict[str, Any]:
    """O payload completo para quem pode ver; a versão comercial para quem não pode.

    É a única forma que os endpoints usam. Escrever `if papel == ...` dentro de uma rota é
    exatamente o que este módulo existe para evitar.
    """
    return payload if pode_ver_economia else comercial(payload)


def encontrar_confidenciais(payload: Any, caminho: str = "") -> list:
    """Onde há campo confidencial nesta estrutura. Ferramenta de teste e de auditoria.

    Devolve os caminhos (`itens[0].custo_unitario`), não os valores — um relatório de
    vazamento não precisa repetir o vazamento.
    """
    achados = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            aqui = f"{caminho}.{k}" if caminho else k
            if k in CAMPOS_CONFIDENCIAIS:
                achados.append(aqui)
            achados.extend(encontrar_confidenciais(v, aqui))
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            achados.extend(encontrar_confidenciais(v, f"{caminho}[{i}]"))
    return achados

"""Ponte entre a plataforma e o gerador de PDF já aprovado
(~/Anara-Cotacao/gerar_cotacao.py). Não reescreve nada do gerador — só monta
os dicts header/items/totals no formato que build_pdf() já espera, a partir
dos registros do banco, e chama a função diretamente.
"""
import importlib.util
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/Anara-Cotacao
GERAR_COTACAO_PATH = os.path.join(BASE_DIR, "gerar_cotacao.py")

_spec = importlib.util.spec_from_file_location("gerar_cotacao_legacy", GERAR_COTACAO_PATH)
_gerar_cotacao = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gerar_cotacao)


def gerar_pdf_para_cotacao(cotacao, cliente, itens) -> str:
    frete_texto = cotacao.frete or ""
    rotulos_frete = {"CIF": "CIF", "FOB": "FOB", "A_COMBINAR": "A combinar", "OUTRO": "Outro"}
    tipo_frete = rotulos_frete.get(cotacao.freight_type or "", cotacao.freight_type or "")
    if tipo_frete and frete_texto:
        frete_final = f"{tipo_frete} — {frete_texto}"
    else:
        frete_final = tipo_frete or frete_texto or "A combinar"

    header = {
        "numero": cotacao.numero,
        "cliente": cliente.nome if cliente else None,
        "cnpj": cliente.cnpj_cpf if cliente else None,
        "cidade": cliente.cidade_uf if cliente else None,
        "telefone": cliente.telefone if cliente else None,
        "email": cliente.email if cliente else None,
        "contato": cotacao.contato_nome or (cliente.contato_nome if cliente else None),
        "departamento": cotacao.departamento_contato or (cliente.departamento if cliente else None),
        "prazo_entrega": cotacao.prazo_entrega,
        "local_entrega": cotacao.local_entrega,
        "obs": cotacao.observacoes,
        "data": cotacao.criado_em.strftime("%d/%m/%Y") if cotacao.criado_em else "",
        "validade": cotacao.validade_em.strftime("%d/%m/%Y") if cotacao.validade_em else "",
        "vendedor": cotacao.vendedor,
        "condicao_pagamento": cotacao.condicao_pagamento or "30",  # única pra cotação inteira
        "frete": frete_final,
        "termos": cotacao.termos_texto,
        "mostrar_aceite": True,
    }

    items = []
    for i, it in enumerate(itens, start=1):
        qtd = it.quantidade
        if qtd == int(qtd):
            qtd = int(qtd)
        # "desconto" só faz sentido mostrar pro cliente quando o preço negociado é
        # menor que o preço-base; quando o vendedor negociou um preço IGUAL ou
        # ACIMA do preço-base (comum agora que o preço é livre), não existe
        # desconto — mostra o preço negociado puro, sem sugerir desconto negativo.
        if it.preco_base and it.preco_negociado < it.preco_base:
            preco_unit = it.preco_base
            desc = (it.preco_base - it.preco_negociado) / it.preco_base
        else:
            preco_unit = it.preco_negociado
            desc = 0.0
        items.append({
            "n": i,
            "produto": it.nome_produto,
            "spec": it.especificacao,
            "qtd": qtd,
            "preco_unit": preco_unit,
            "desc": desc,
            "preco_final": it.preco_negociado,
            "total": it.faturamento,
        })

    subtotal = sum(i["total"] for i in items)
    totals = {"subtotal": subtotal, "frete": 0, "total_geral": subtotal, "total_itens": len(items)}

    nome_arquivo = (cotacao.numero or f"cotacao_{cotacao.id}").replace("/", "-")
    out_path = os.path.join(tempfile.gettempdir(), f"anara_{nome_arquivo}.pdf")
    _gerar_cotacao.build_pdf(out_path, header, items, totals)
    return out_path

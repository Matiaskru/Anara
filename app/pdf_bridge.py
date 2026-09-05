"""Ponte entre a plataforma e o gerador de PDF já aprovado
(~/Anara-Cotacao/gerar_cotacao.py). Não reescreve nada do gerador — só monta
os dicts header/items/totals no formato que build_pdf() já espera, a partir
dos registros do banco, e chama a função diretamente.
"""
import importlib.util
import os
import tempfile

from app.dinheiro import D0, ZERO, dinheiro, divide, para_float, soma

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/Anara-Cotacao
GERAR_COTACAO_PATH = os.path.join(BASE_DIR, "gerar_cotacao.py")

_spec = importlib.util.spec_from_file_location("gerar_cotacao_legacy", GERAR_COTACAO_PATH)
_gerar_cotacao = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gerar_cotacao)


def gerar_pdf_para_cotacao(cotacao, cliente, itens, *, rascunho: bool = False) -> str:
    """Monta o PDF. `rascunho=True` marca o documento como não emitido.

    A marca existe porque um preview e um documento final são a mesma folha de papel para
    quem recebe. Sem ela, uma proposta ainda em negociação — talvez com aprovação pendente —
    chegaria ao cliente indistinguível da versão fechada. A marca é textual e discreta: o
    layout aprovado não é redesenhado.
    """
    frete_texto = cotacao.frete or ""
    rotulos_frete = {"CIF": "CIF", "FOB": "FOB", "A_COMBINAR": "A combinar", "OUTRO": "Outro"}
    tipo_frete = rotulos_frete.get(cotacao.freight_type or "", cotacao.freight_type or "")
    if tipo_frete and frete_texto:
        frete_final = f"{tipo_frete} — {frete_texto}"
    else:
        frete_final = tipo_frete or frete_texto or "A combinar"

    numero = cotacao.numero
    revisao = getattr(cotacao, "revisao", 1) or 1
    if revisao > 1:
        numero = f"{numero} · rev. {revisao}"
    if rascunho:
        numero = f"{numero}  (RASCUNHO — não emitida)"

    header = {
        "numero": numero,
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
        # O documento comercial só mostra quantia em centavos. Um `preco_base` histórico
        # gravado com 14 casas (havia isso antes da Sessão 3B) é quantizado aqui — o cliente
        # não recebe R$ 34,71540940423179.
        base = dinheiro(it.preco_base) if it.preco_base else None
        negociado = dinheiro(it.preco_negociado)
        if base and negociado < base:
            preco_unit = base
            desc = divide(base - negociado, base) or ZERO
        else:
            preco_unit = negociado
            desc = ZERO
        items.append({
            "n": i,
            "produto": it.nome_produto,
            "spec": it.especificacao,
            "qtd": qtd,
            "preco_unit": para_float(preco_unit),
            "desc": para_float(desc),
            "preco_final": para_float(negociado),
            "total": para_float(dinheiro(it.faturamento)),
        })

    # Soma em Decimal: o total do PDF é o que o cliente confere somando as linhas na mão.
    subtotal = para_float(soma(i["total"] for i in items))
    totals = {"subtotal": subtotal, "frete": 0, "total_geral": subtotal, "total_itens": len(items)}

    nome_arquivo = (cotacao.numero or f"cotacao_{cotacao.id}").replace("/", "-")
    out_path = os.path.join(tempfile.gettempdir(), f"anara_{nome_arquivo}.pdf")
    _gerar_cotacao.build_pdf(out_path, header, items, totals)
    return out_path

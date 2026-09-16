"""Ponte entre a plataforma e o PDF da proposta (Fase 3C).

O gerador (`app.pdf_proposta.build_pdf`) só sabe desenhar. **Tudo** que chega a ele passa
por aqui, e passa por **lista de permissão**: cada campo do cabeçalho, de cada item e dos
totais é montado nominalmente. Não existe "pega o item e tira o custo" — o que não está
declarado em `CAMPOS_*` não existe para o papel.

Três regras que valem para qualquer documento:

* o cliente vê o preço **negociado** final; preço recomendado, preço de tabela e desconto
  interno não vão ao documento;
* a observação **interna** (`Cotacao.observacoes`) nunca vai; só `observacao_cliente`;
* código operacional nunca aparece como texto (`A_COTAR`, `REVIEW_REQUIRED`, `C-NEW`…): a
  ponte varre todos os textos antes de desenhar e recusa se encontrar um.

O PDF **final** nasce do `SnapshotEmissao` — o documento congelado na emissão —, não da
cotação viva. O rascunho lê a cotação como está e sai marcado.
"""
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Iterable, Optional

from app import pdf_proposta
from app.confidencial import encontrar_confidenciais
from app.dinheiro import D0, ZERO, dinheiro, para_float, soma

#: Mantido por compatibilidade com os testes de segurança, que espiam `_gerar_cotacao.build_pdf`.
_gerar_cotacao = pdf_proposta

#: Campos que o cabeçalho da proposta pode levar. Lista fechada.
CAMPOS_HEADER = ("numero", "revisao", "data", "validade", "cliente", "cnpj", "cidade",
                 "contato", "departamento", "local_entrega", "condicao_pagamento",
                 "prazo_entrega", "frete", "obs", "termos", "vendedor", "rascunho",
                 "mostrar_aceite")
#: Campos de cada linha. Sem desconto, sem recomendado, sem fornecedor.
CAMPOS_ITEM = ("n", "produto", "spec", "qtd", "preco_final", "total")
#: Totais. Frete separado dos produtos; nada de custo, lucro ou margem.
CAMPOS_TOTAIS = ("subtotal", "frete", "frete_texto", "total_geral", "total_itens")

#: Códigos operacionais que nunca podem virar texto do cliente.
CODIGOS_PROIBIDOS = (
    "A_COTAR", "REVIEW_REQUIRED", "REVALIDAR", "SEM_PRECO", "C-NEW", "TRACEABLE_LEGACY",
    "MARGEM_ABAIXO_PISO", "PRECO_ABAIXO", "seller_publishable", "FRETE_A_COTAR",
    "FRETE_REVIEW_REQUIRED", "ESTIMADO", "CONFIRMADO", "fingerprint", "approval_id",
)
#: Palavras que denunciam economia interna num texto livre (observação, termos).
PALAVRAS_PROIBIDAS = ("CNET", "EXW", "custo", "margem", "markup", "comissão", "comissao",
                      "lucro", "piso", "AuditLog", "memória de cálculo")


class PdfInseguro(RuntimeError):
    """A ponte recusou desenhar: um dado interno chegaria ao cliente."""


ROTULO_FRETE = {"CIF": "Frete nacional", "FOB": "Por conta do cliente",
                "A_COMBINAR": "A combinar", "OUTRO": "Conforme combinado"}


def _texto_frete(tipo: Optional[str], valor, texto_livre: Optional[str], rascunho: bool):
    """Como o frete aparece para o cliente — nunca um código.

        CIF com valor      → "Frete nacional: R$ X"      (e o valor entra no total)
        FOB                → "Por conta do cliente"
        A_COMBINAR         → "A combinar"
        CIF sem valor      → rascunho: "a definir"; final: não acontece (blocker)
    """
    tipo = (tipo or "").upper()
    v = D0(valor) if valor not in (None, "") else ZERO
    if tipo == "CIF":
        if v > ZERO:
            rotulo = f"Frete nacional: {pdf_proposta.brl(v)}"
        else:
            rotulo = "Frete nacional: a definir" if rascunho else "Frete nacional"
    else:
        rotulo = ROTULO_FRETE.get(tipo, "A combinar")
    if texto_livre:
        rotulo = f"{rotulo} — {texto_livre.strip()}"
    return rotulo, (v if tipo == "CIF" and v > ZERO else None)


def _varrer_textos(valores: Iterable) -> list:
    achados = []
    for v in valores:
        if not isinstance(v, str):
            continue
        for codigo in CODIGOS_PROIBIDOS:
            if codigo in v:
                achados.append(codigo)
    return achados


def _conferir(header: dict, items: list, totals: dict):
    """Rede de segurança: recusa o documento se algo interno tiver chegado até aqui."""
    vazamentos = encontrar_confidenciais({"header": header, "items": items, "totals": totals})
    extras = [k for k in header if k not in CAMPOS_HEADER]
    extras += [k for it in items for k in it if k not in CAMPOS_ITEM]
    extras += [k for k in totals if k not in CAMPOS_TOTAIS]
    codigos = _varrer_textos(list(header.values()) + [v for it in items for v in it.values()]
                             + list(totals.values()))
    if vazamentos or extras or codigos:
        raise PdfInseguro(f"dado interno no PDF: confidenciais={vazamentos} extras={extras} "
                          f"codigos={codigos}")


def _data(v) -> str:
    if not v:
        return ""
    try:
        return v.strftime("%d/%m/%Y")
    except AttributeError:
        try:
            return datetime.fromisoformat(str(v)).strftime("%d/%m/%Y")
        except ValueError:
            return str(v)


def _linhas(itens_como_dicts: list) -> list:
    items = []
    for i, it in enumerate(itens_como_dicts, start=1):
        quantidade = D0(it["quantidade"])
        negociado = dinheiro(D0(it["preco_negociado"]))
        total = dinheiro(D0(it["faturamento"])) if it.get("faturamento") not in (None, "") \
            else dinheiro(negociado * quantidade)
        items.append({"n": i, "produto": it["nome"], "spec": it.get("especificacao") or None,
                      "qtd": para_float(quantidade), "preco_final": para_float(negociado),
                      "total": para_float(total)})
    return items


def montar_documento(cotacao, cliente, itens, *, rascunho: bool = False, snapshot=None,
                     condicao_label: Optional[str] = None) -> tuple:
    """Os três dicionários da proposta, já filtrados. Não desenha nada.

    Com `snapshot`, itens, cliente, totais e frete vêm do documento congelado na emissão;
    a cotação viva só fornece o que o snapshot não guarda (condições, termos, contato).
    """
    if snapshot is not None:
        cli = json.loads(snapshot.cliente_json or "{}")
        itens_dicts = json.loads(snapshot.itens_json or "[]")
        frete_json = json.loads(snapshot.frete_json or "{}")
        numero = snapshot.numero or cotacao.numero
        revisao = snapshot.revisao or cotacao.revisao or 1
        data = _data(snapshot.emitido_em)
        cliente_nome, cnpj, cidade = cli.get("nome"), cli.get("cnpj_cpf"), cli.get("cidade_uf")
        frete_valor = frete_json.get("valor") if frete_json.get("cif") else None
    else:
        itens_dicts = [{"nome": it.nome_produto, "especificacao": it.especificacao,
                        "quantidade": it.quantidade, "preco_negociado": it.preco_negociado,
                        "faturamento": it.faturamento} for it in itens]
        numero = cotacao.numero
        revisao = getattr(cotacao, "revisao", 1) or 1
        data = _data(cotacao.issued_em or cotacao.criado_em)
        cliente_nome = cliente.nome if cliente else None
        cnpj = cliente.cnpj_cpf if cliente else None
        cidade = cliente.cidade_uf if cliente else None
        frete_valor = cotacao.freight_valor

    items = _linhas(itens_dicts)
    subtotal = soma(D0(i["total"]) for i in items)
    frete_texto, frete_no_total = _texto_frete(cotacao.freight_type, frete_valor, cotacao.frete,
                                               rascunho)
    total_geral = subtotal + (dinheiro(frete_no_total) if frete_no_total is not None else ZERO)

    header = {
        "numero": (f"{numero}  (RASCUNHO)" if rascunho and numero else numero) or "",
        "revisao": int(revisao),
        "data": data,
        "validade": _data(cotacao.validade_em),
        "cliente": cliente_nome,
        "cnpj": cnpj,
        "cidade": cidade,
        "contato": cotacao.contato_nome or (cliente.contato_nome if cliente else None),
        "departamento": cotacao.departamento_contato or (cliente.departamento if cliente else None),
        "local_entrega": cotacao.local_entrega,
        "condicao_pagamento": condicao_label or cotacao.condicao_pagamento or "",
        "prazo_entrega": cotacao.prazo_entrega,
        "frete": frete_texto,
        # SÓ a observação para o cliente. `cotacao.observacoes` é interna e não entra.
        "obs": getattr(cotacao, "observacao_cliente", None) or None,
        "termos": cotacao.termos_texto,
        "vendedor": cotacao.vendedor,
        "rascunho": bool(rascunho),
        "mostrar_aceite": True,
    }
    totals = {"subtotal": para_float(subtotal),
              "frete": para_float(dinheiro(frete_no_total)) if frete_no_total is not None else None,
              "frete_texto": None if frete_no_total is not None else frete_texto.split(" — ")[0],
              "total_geral": para_float(total_geral), "total_itens": len(items)}
    _conferir(header, items, totals)
    return header, items, totals


def gerar_pdf_para_cotacao(cotacao, cliente, itens, *, rascunho: bool = False, snapshot=None,
                           condicao_label: Optional[str] = None) -> str:
    """Monta e desenha a proposta. `rascunho=True` marca o documento em todas as páginas."""
    header, items, totals = montar_documento(cotacao, cliente, itens, rascunho=rascunho,
                                             snapshot=snapshot, condicao_label=condicao_label)
    nome_arquivo = re.sub(r"[^A-Za-z0-9_-]+", "-", str(cotacao.numero or f"cotacao_{cotacao.id}"))
    sufixo = "-rascunho" if rascunho else ""
    out_path = os.path.join(tempfile.gettempdir(), f"anara_{nome_arquivo}{sufixo}.pdf")
    _gerar_cotacao.build_pdf(out_path, header, items, totals)
    return out_path

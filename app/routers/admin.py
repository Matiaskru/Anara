"""Área administrativa — manter premissa econômica sem tocar em código nem em histórico.

Toda rota de escrita aqui segue o mesmo caminho, e ele não tem atalho:

    exigir_economia_gerenciavel(request)   quem pode alterar
    → preview(...)                         o que mudaria, para quem, com que impacto
    → aplicar(..., proposta)               só se o estado ainda for o que o preview viu

Duas coisas que este módulo deliberadamente **não** faz:

* **não aceita o corpo inteiro do formulário como comando.** Cada rota lê os campos que
  declara, um a um. Passar `request.form()` para o ORM seria mass assignment: bastaria
  acrescentar um campo escondido ao POST para escrever numa coluna que ninguém autorizou;
* **não confia no navegador para o conteúdo econômico.** O apply recebe do formulário apenas
  o token do preview e os mesmos campos que o preview recebeu — e recalcula tudo do zero. O
  token serve para provar que o estado não mudou, não para transportar valores.
"""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from app import admin_service as adm
from app import config_service as cfg
from app import custo_service as cs
from app.db import get_session
from app.models import (
    CondicaoPagamento, Fornecedor, MargemRegra, Premissa, Produto,
)
from app.permissoes import exigir_admin, exigir_economia_gerenciavel
from app.templating import templates

router = APIRouter()

#: As premissas que a tela oferece. Lista fechada de propósito: um formulário genérico
#: sobre `Premissa.chave` deixaria alguém criar "icms = banana" e achar que configurou algo.
PREMISSAS_EDITAVEIS = [
    ("fx_usd_brl", "Câmbio USD → BRL", "R$/US$", "num"),
    ("frete_int_usd_kg", "Frete internacional", "US$/kg", "num"),
    ("outras_desp_usd_un", "Outras despesas de importação", "US$/un", "num"),
    ("pis_cofins_pct", "PIS/COFINS", "fração (0,0759 = 7,59%)", "num"),
]


def _data(valor: Optional[str]) -> Optional[date]:
    """Data do formulário. Vazio é 'a partir de hoje', não erro."""
    if not (valor or "").strip():
        return None
    try:
        return date.fromisoformat(valor.strip())
    except ValueError:
        raise adm.DadoInvalido(f"Data inválida: '{valor}'. Use o formato AAAA-MM-DD.")


def _erro(mensagem: str, status: int = 400):
    """Erro administrativo legível. Nunca stack trace, nunca segredo."""
    return JSONResponse({"erro": mensagem}, status_code=status)


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------
@router.get("/admin", response_class=HTMLResponse)
def painel(request: Request, session: Session = Depends(get_session)):
    exigir_admin(request)
    premissas = []
    for chave, rotulo, unidade, _tipo in PREMISSAS_EDITAVEIS:
        atual = cfg.premissa(session, chave)
        premissas.append({
            "chave": chave, "rotulo": rotulo, "unidade": unidade,
            "valor": atual.valor_num if atual else None,
            "desde": atual.valid_from if atual else None,
            "fonte": atual.fonte if atual else None,
            "escopo": adm.escopo_da_premissa(session, chave),
        })
    margens = sorted(session.exec(select(MargemRegra)).all(),
                     key=lambda r: (r.prioridade, r.id or 0))
    condicoes = sorted(session.exec(select(CondicaoPagamento)).all(),
                       key=lambda c: (c.ordem, c.versao or 1))
    return templates.TemplateResponse(request, "admin_painel.html", {
        "active": "admin", "premissas": premissas, "margens": margens,
        "condicoes": condicoes, "trilha": adm.trilha(session, limite=25),
        "niveis": {r.id: adm.nivel_da_regra(r) for r in margens},
        "fornecedores": {f.id: f.nome for f in session.exec(select(Fornecedor)).all()},
    })


# ---------------------------------------------------------------------------
# Custo por SKU — o caso central
# ---------------------------------------------------------------------------
@router.get("/admin/sku/{produto_id}", response_class=HTMLResponse)
def sku(request: Request, produto_id: int, session: Session = Depends(get_session)):
    """Histórico de versões de um SKU. V1 continua consultável para sempre."""
    exigir_admin(request)
    produto = session.get(Produto, produto_id)
    if produto is None:
        return _erro("SKU não encontrado.", 404)
    versoes = cs.versoes(session, produto_id)
    vigente = cs.referencia_vigente(session, produto_id)
    return templates.TemplateResponse(request, "admin_sku.html", {
        "active": "admin", "produto": produto, "versoes": list(reversed(versoes)),
        "vigente": vigente, "hoje": date.today(),
        "trilha": [t for t in adm.trilha(session, entidade="CustoReferencia", limite=50)
                   if (t.escopo or "").endswith(produto.sku_key or "\0")],
    })


@router.post("/admin/sku/{produto_id}/custo/preview")
def preview_custo(request: Request, produto_id: int, cnet: str = Form(...),
                  status: str = Form(...), fonte: str = Form(...),
                  documento: str = Form(""), valor_bruto: str = Form(""),
                  vigencia: str = Form(""), motivo: str = Form(""),
                  session: Session = Depends(get_session)):
    """O que mudaria. **Não grava.**"""
    exigir_economia_gerenciavel(request)
    try:
        prop = adm.preview_custo_sku(
            session, produto_id, cnet_brl=cnet, status=status, fonte=fonte,
            documento=documento or None, valor_bruto=valor_bruto or None,
            vigente_a_partir_de=_data(vigencia), motivo=motivo or None)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    payload = prop.como_dict()
    if prop.mudancas:
        payload["impacto"] = adm.simular_impacto_custo(session, produto_id, cnet)
    return JSONResponse(payload)


@router.post("/admin/sku/{produto_id}/custo/aplicar")
def aplicar_custo(request: Request, produto_id: int, cnet: str = Form(...),
                  status: str = Form(...), fonte: str = Form(...), token: str = Form(...),
                  documento: str = Form(""), valor_bruto: str = Form(""),
                  vigencia: str = Form(""), motivo: str = Form(""),
                  session: Session = Depends(get_session)):
    """Cria a versão nova. O SKU vizinho não é tocado."""
    ator = exigir_economia_gerenciavel(request)
    try:
        prop = adm.preview_custo_sku(
            session, produto_id, cnet_brl=cnet, status=status, fonte=fonte,
            documento=documento or None, valor_bruto=valor_bruto or None,
            vigente_a_partir_de=_data(vigencia), motivo=motivo or None)
        # O token que o navegador devolve tem de bater com o estado observado AGORA.
        # Recalcular a proposta aqui é o que impede o formulário de carregar um escopo
        # diferente do revisado.
        prop.token = token
        nova = adm.aplicar_custo_sku(
            session, produto_id, prop, ator=ator, cnet_brl=cnet, status=status, fonte=fonte,
            documento=documento or None, valor_bruto=valor_bruto or None,
            vigente_a_partir_de=_data(vigencia), motivo=motivo or None)
        session.commit()
    except adm.ConflitoDeVersao as e:
        session.commit()          # a trilha do conflito fica gravada
        return _erro(str(e), 409)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    if nova is None:
        return JSONResponse({"situacao": adm.NO_OP,
                             "mensagem": "Nada a versionar: o valor já era esse."})
    return JSONResponse({"situacao": "APLICADO", "versao": nova.versao,
                         "vigente_desde": str(nova.valid_from),
                         "mensagem": f"Versão V{nova.versao} criada para {nova.sku_key}."})


# ---------------------------------------------------------------------------
# Premissa global
# ---------------------------------------------------------------------------
@router.post("/admin/premissa/preview")
def preview_premissa(request: Request, chave: str = Form(...), valor: str = Form(...),
                     fonte: str = Form(...), vigencia: str = Form(""),
                     motivo: str = Form(""), session: Session = Depends(get_session)):
    exigir_economia_gerenciavel(request)
    if chave not in {c for c, _r, _u, _t in PREMISSAS_EDITAVEIS}:
        return _erro(f"A premissa '{chave}' não é editável por esta tela.", 403)
    try:
        prop = adm.preview_premissa(session, chave, valor_num=valor, fonte=fonte,
                                    vigente_a_partir_de=_data(vigencia),
                                    motivo=motivo or None)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    return JSONResponse(prop.como_dict())


@router.post("/admin/premissa/aplicar")
def aplicar_premissa(request: Request, chave: str = Form(...), valor: str = Form(...),
                     fonte: str = Form(...), token: str = Form(...),
                     vigencia: str = Form(""), motivo: str = Form(""),
                     session: Session = Depends(get_session)):
    ator = exigir_economia_gerenciavel(request)
    if chave not in {c for c, _r, _u, _t in PREMISSAS_EDITAVEIS}:
        return _erro(f"A premissa '{chave}' não é editável por esta tela.", 403)
    try:
        prop = adm.preview_premissa(session, chave, valor_num=valor, fonte=fonte,
                                    vigente_a_partir_de=_data(vigencia),
                                    motivo=motivo or None)
        prop.token = token
        nova = adm.aplicar_premissa(session, chave, prop, ator=ator, valor_num=valor,
                                    fonte=fonte, vigente_a_partir_de=_data(vigencia),
                                    motivo=motivo or None)
        session.commit()
    except adm.ConflitoDeVersao as e:
        session.commit()
        return _erro(str(e), 409)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    if nova is None:
        return JSONResponse({"situacao": adm.NO_OP, "mensagem": "Valor idêntico ao vigente."})
    return JSONResponse({"situacao": "APLICADO", "vigente_desde": str(nova.valid_from),
                         "mensagem": f"'{chave}' versionada. Alcance: {prop.skus_afetados} SKU(s)."})


# ---------------------------------------------------------------------------
# Margem
# ---------------------------------------------------------------------------
def _escopo_da_form(fornecedor_id: str, familia: str, sku_key: str):
    """Traduz os campos do formulário para o nível da regra — e nada mais."""
    return (int(fornecedor_id) if (fornecedor_id or "").strip() else None,
            (familia or "").strip() or None,
            (sku_key or "").strip() or None)


@router.post("/admin/margem/preview")
def preview_margem(request: Request, margem: str = Form(...), nome: str = Form(...),
                   fonte: str = Form(...), fornecedor_id: str = Form(""),
                   familia: str = Form(""), sku_key: str = Form(""),
                   prioridade: int = Form(50), vigencia: str = Form(""),
                   motivo: str = Form(""), session: Session = Depends(get_session)):
    exigir_economia_gerenciavel(request)
    fid, fam, sku = _escopo_da_form(fornecedor_id, familia, sku_key)
    try:
        prop = adm.preview_margem(session, margem_pct=margem, nome=nome, fornecedor_id=fid,
                                  familia=fam, sku_key=sku, prioridade=prioridade,
                                  fonte=fonte, vigente_a_partir_de=_data(vigencia),
                                  motivo=motivo or None)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    return JSONResponse(prop.como_dict())


@router.post("/admin/margem/aplicar")
def aplicar_margem(request: Request, margem: str = Form(...), nome: str = Form(...),
                   fonte: str = Form(...), token: str = Form(...),
                   fornecedor_id: str = Form(""), familia: str = Form(""),
                   sku_key: str = Form(""), prioridade: int = Form(50),
                   vigencia: str = Form(""), motivo: str = Form(""),
                   session: Session = Depends(get_session)):
    ator = exigir_economia_gerenciavel(request)
    fid, fam, sku = _escopo_da_form(fornecedor_id, familia, sku_key)
    try:
        prop = adm.preview_margem(session, margem_pct=margem, nome=nome, fornecedor_id=fid,
                                  familia=fam, sku_key=sku, prioridade=prioridade,
                                  fonte=fonte, vigente_a_partir_de=_data(vigencia),
                                  motivo=motivo or None)
        prop.token = token
        nova = adm.aplicar_margem(session, prop, ator=ator, margem_pct=margem, nome=nome,
                                  fornecedor_id=fid, familia=fam, sku_key=sku,
                                  prioridade=prioridade, fonte=fonte,
                                  vigente_a_partir_de=_data(vigencia), motivo=motivo or None)
        session.commit()
    except adm.ConflitoDeVersao as e:
        session.commit()
        return _erro(str(e), 409)
    except adm.DadoInvalido as e:
        return _erro(str(e))
    if nova is None:
        return JSONResponse({"situacao": adm.NO_OP, "mensagem": "Margem idêntica à vigente."})
    return JSONResponse({"situacao": "APLICADO", "regra_id": nova.id,
                         "mensagem": f"Regra criada no nível {prop.escopo} "
                                     f"— alcança {prop.skus_afetados} SKU(s)."})


# ---------------------------------------------------------------------------
# Trilha
# ---------------------------------------------------------------------------
@router.get("/admin/trilha", response_class=HTMLResponse)
def ver_trilha(request: Request, entidade: str = "",
               session: Session = Depends(get_session)):
    exigir_admin(request)
    return templates.TemplateResponse(request, "admin_trilha.html", {
        "active": "admin", "entidade": entidade,
        "linhas": adm.trilha(session, entidade=entidade or None, limite=200),
    })


# ---------------------------------------------------------------------------
# Rascunho com premissa desatualizada
# ---------------------------------------------------------------------------
@router.get("/admin/cotacao/{cotacao_id}/premissas")
def premissas_do_rascunho(request: Request, cotacao_id: int,
                          session: Session = Depends(get_session)):
    """Detecta — e **só** detecta. Nada é recalculado por abrir esta rota."""
    exigir_admin(request)
    from app.models import Cotacao, CotacaoItem
    cot = session.get(Cotacao, cotacao_id)
    if cot is None:
        return _erro("Cotação não encontrada.", 404)
    itens = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.cotacao_id == cotacao_id)).all()
    return JSONResponse(adm.premissas_desatualizadas(session, cot, itens))

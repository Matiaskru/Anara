"""Painel de configurações — separado do uso comercial do dia a dia.

Tudo o que muda preço mora aqui: câmbio, frete internacional, despesas de nacionalização,
preço de material, CMT, encolhimento, waste, perda de 2ª qualidade, margem KTC, NCM/II, ICMS,
PIS/COFINS, encargo financeiro, comissão, margens-alvo por fornecedor/família, termos e
validade padrão.

Toda alteração pede confirmação explícita e é versionada: a versão anterior é fechada com
`valid_to` em vez de ser apagada, e cotação já emitida não muda por causa disso.
"""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import config_service as cfg
from app.db import get_session
from app.models import (
    CmtPreco, CondicaoPagamento, EstadoFiscal, Fornecedor, MargemRegra, MaterialPreco, NcmRegra,
    ParametroKTC, Premissa, RegraFiscalVenda, ToalhaPreco,
)
from app.templating import templates
from app.permissoes import exigir_admin

router = APIRouter()

PREMISSAS_CRITICAS = {"fx_usd_brl", "frete_int_usd_kg", "outras_desp_usd_un", "pis_cofins_pct",
                      "icms_fallback_pct"}


@router.get("/configuracoes", response_class=HTMLResponse)
def painel(request: Request, aba: str = "premissas", session: Session = Depends(get_session)):
    exigir_admin(request)
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
    contexto = {
        "active": "configuracoes", "aba": aba,
        "premissas": [p for p in session.exec(select(Premissa).order_by(Premissa.chave)).all()
                      if p.ativo],
        "materiais": cfg.materiais(session),
        "cmts": [c for c in session.exec(select(CmtPreco).order_by(CmtPreco.familia)).all()
                 if c.ativo],
        "toalhas": [t for t in session.exec(select(ToalhaPreco)).all() if t.ativo],
        "parametros": [p for p in session.exec(select(ParametroKTC)
                                               .order_by(ParametroKTC.chave)).all()
                       if p.ativo and p.chave not in ("gsm_por_tc", "gsm_por_familia",
                                                      "peso_tecnico_familia")],
        "tabelas_peso": [p for p in session.exec(select(ParametroKTC)
                                                 .order_by(ParametroKTC.chave)).all()
                         if p.ativo and p.chave in ("gsm_por_tc", "gsm_por_familia",
                                                    "peso_tecnico_familia")],
        "ncms": session.exec(select(NcmRegra).order_by(NcmRegra.prioridade, NcmRegra.familia)).all(),
        "estados": session.exec(select(EstadoFiscal).order_by(EstadoFiscal.estado)).all(),
        "regras_fiscais": session.exec(select(RegraFiscalVenda)).all(),
        "margens": session.exec(select(MargemRegra).order_by(MargemRegra.prioridade,
                                                             MargemRegra.nome)).all(),
        "condicoes": cfg.condicoes_pagamento(session, apenas_ativas=False),
        "fornecedor_por_id": fornecedores,
        "comissao": cfg.tabela_comissao(session),
    }
    return templates.TemplateResponse(request, "configuracoes.html", contexto)


@router.post("/configuracoes/premissa")
def salvar_premissa(request: Request, chave: str = Form(...), valor: str = Form(...), confirmar: str = Form(""),
                    fonte: str = Form(""), session: Session = Depends(get_session)):
    exigir_admin(request)
    if chave in PREMISSAS_CRITICAS and confirmar != "sim":
        return RedirectResponse(url="/configuracoes?aba=premissas&erro=confirmacao", status_code=303)
    atual = cfg.premissa(session, chave)
    if atual is not None and atual.valor_num is not None:
        try:
            numero = float(str(valor).replace(",", "."))
        except ValueError:
            return RedirectResponse(url="/configuracoes?aba=premissas&erro=numero", status_code=303)
        cfg.definir(session, chave, valor_num=numero, fonte=fonte or "Painel de configurações")
    else:
        cfg.definir(session, chave, valor_txt=valor, fonte=fonte or "Painel de configurações")
    return RedirectResponse(url="/configuracoes?aba=premissas&ok=1", status_code=303)


@router.post("/configuracoes/margem")
def salvar_margem(request: Request, regra_id: int = Form(...), margem_pct: float = Form(...),
                  session: Session = Depends(get_session)):
    """Muda a margem padrão. Preço-base e cotações novas passam a usar; emitidas não mudam."""
    exigir_admin(request)
    regra = session.get(MargemRegra, regra_id)
    if regra:
        regra.margem_pct = margem_pct / 100 if margem_pct > 1 else margem_pct
        session.add(regra)
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=margens&ok=1", status_code=303)


@router.post("/configuracoes/condicao")
def salvar_condicao(request: Request, condicao_id: int = Form(...), encargo_pct: str = Form(""),
                    ativo: str = Form("sim"), session: Session = Depends(get_session)):
    exigir_admin(request)
    condicao = session.get(CondicaoPagamento, condicao_id)
    if condicao:
        if encargo_pct.strip() == "":
            condicao.encargo_pct = None
            condicao.encargo_confirmado = False
        else:
            valor = float(encargo_pct.replace(",", "."))
            condicao.encargo_pct = valor / 100 if valor > 1 else valor
            condicao.encargo_confirmado = True
        condicao.ativo = (ativo == "sim")
        session.add(condicao)
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=pagamento&ok=1", status_code=303)


@router.post("/configuracoes/material")
def salvar_material(request: Request, material_id: int = Form(...), price_usd_m2: float = Form(...),
                    confirmar: str = Form(""), session: Session = Depends(get_session)):
    """Preço de material novo abre uma versão nova; a anterior é fechada, não apagada."""
    exigir_admin(request)
    if confirmar != "sim":
        return RedirectResponse(url="/configuracoes?aba=ktc&erro=confirmacao", status_code=303)
    atual = session.get(MaterialPreco, material_id)
    if atual and abs(atual.price_usd_m2 - price_usd_m2) > 1e-9:
        atual.valid_to = date.today()
        atual.ativo = False
        session.add(atual)
        session.add(MaterialPreco(
            material=atual.material, thread_count=atual.thread_count, weave=atual.weave,
            cotton_pct=atual.cotton_pct, poliester_pct=atual.poliester_pct,
            plain_or_stripe=atual.plain_or_stripe, price_usd_m2=price_usd_m2,
            fonte="Painel de configurações", fonte_data=date.today(),
            notas=f"Substitui US$ {atual.price_usd_m2}/m² vigente até {date.today():%d/%m/%Y}."))
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=ktc&ok=1", status_code=303)


@router.post("/configuracoes/parametro")
def salvar_parametro(request: Request, parametro_id: int = Form(...), valor: float = Form(...),
                     confirmar: str = Form(""), session: Session = Depends(get_session)):
    exigir_admin(request)
    if confirmar != "sim":
        return RedirectResponse(url="/configuracoes?aba=ktc&erro=confirmacao", status_code=303)
    atual = session.get(ParametroKTC, parametro_id)
    if atual and abs(atual.valor - valor) > 1e-12:
        atual.valid_to = date.today()
        atual.ativo = False
        session.add(atual)
        session.add(ParametroKTC(chave=atual.chave, escopo=atual.escopo, valor=valor,
                                 fonte="Painel de configurações",
                                 notas=f"Substitui {atual.valor} vigente até {date.today():%d/%m/%Y}."))
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=ktc&ok=1", status_code=303)


@router.post("/configuracoes/cmt")
def salvar_cmt(request: Request, cmt_id: int = Form(...), cmt_usd: float = Form(...), confirmar: str = Form(""),
               session: Session = Depends(get_session)):
    exigir_admin(request)
    if confirmar != "sim":
        return RedirectResponse(url="/configuracoes?aba=ktc&erro=confirmacao", status_code=303)
    atual = session.get(CmtPreco, cmt_id)
    if atual and abs(atual.cmt_usd - cmt_usd) > 1e-9:
        atual.valid_to = date.today()
        atual.ativo = False
        session.add(atual)
        session.add(CmtPreco(familia=atual.familia, construcao=atual.construcao, cmt_usd=cmt_usd,
                             fonte="Painel de configurações",
                             notas=f"Substitui US$ {atual.cmt_usd} vigente até {date.today():%d/%m/%Y}."))
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=ktc&ok=1", status_code=303)


@router.post("/configuracoes/fiscal")
def salvar_fiscal(request: Request, estado_id: int = Form(...), carga_final: float = Form(...),
                  aliquota_interna: float = Form(...), confirmar: str = Form(""),
                  session: Session = Depends(get_session)):
    """Carga final e alíquota interna por estado. O sistema usa a carga como está — não recalcula."""
    exigir_admin(request)
    if confirmar != "sim":
        return RedirectResponse(url="/configuracoes?aba=fiscal&erro=confirmacao", status_code=303)
    estado = session.get(EstadoFiscal, estado_id)
    if estado:
        estado.carga_final = carga_final / 100 if carga_final > 1 else carga_final
        estado.aliquota_interna = aliquota_interna / 100 if aliquota_interna > 1 else aliquota_interna
        estado.fonte = "Painel de configurações"
        session.add(estado)
        session.commit()
    return RedirectResponse(url="/configuracoes?aba=fiscal&ok=1", status_code=303)

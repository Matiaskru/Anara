"""Tabela de preços (10/11/2026): a Tabela comercial das vendedoras dentro do sistema, com o preço FIXO.

Rota de OPERAÇÃO, como a calculadora: qualquer usuário autenticado abre. Só preço comercial, para todos os
papéis (a página não tem modo econômico).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app import config_service as cfg
from app import tabela_precos as tp
from app.db import get_session
from app.permissoes import exigir_autenticado
from app.templating import templates

router = APIRouter()

#: O cenário da planilha quando ninguém escolheu nada.
PADRAO = {"estado": "São Paulo", "contribuinte": "nao", "condicao": "30"}


@router.get("/tabela-precos", response_class=HTMLResponse)
def pagina(request: Request, estado: str = "", contribuinte: str = "", condicao: str = "",
           session: Session = Depends(get_session)):
    exigir_autenticado(request)
    estado = estado if estado in tp.ESTADOS else PADRAO["estado"]
    contribuinte = contribuinte if contribuinte in ("sim", "nao") else PADRAO["contribuinte"]
    condicao = condicao if condicao in tp.PRAZOS else PADRAO["condicao"]
    # Os prazos são os da tabela publicada (cartão: "consultar o administrativo"); o rótulo vem do cadastro.
    rotulos = {c.codigo: c.label for c in cfg.condicoes_pagamento(session, apenas_ativas=False)}
    linhas = tp.montar(estado, contribuinte == "sim", condicao)
    itens = [x for x in linhas if x.tipo == "item"]
    return templates.TemplateResponse(request, "tabela_precos.html", {
        "active": "tabela_precos", "linhas": linhas, "versao": tp.versao(),
        "n_itens": len(itens), "n_sob_consulta": sum(1 for x in itens if x.situacao == "SOB_CONSULTA"),
        "estados": tp.ESTADOS, "condicoes": [(c, rotulos.get(c, c)) for c in tp.PRAZOS],
        "estado": estado, "contribuinte": contribuinte, "condicao": condicao,
    })

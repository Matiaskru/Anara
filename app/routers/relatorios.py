from fastapi import APIRouter, Depends, Request
import json

from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlmodel import Session

from app.db import get_session
from app.relatorios import indicadores, perguntas_para_ktc, qualidade_da_base
from app.templating import templates

router = APIRouter()


@router.get("/relatorios/qualidade", response_class=HTMLResponse)
def qualidade(request: Request, lista: str = "revisao", session: Session = Depends(get_session)):
    dados = qualidade_da_base(session)
    return templates.TemplateResponse(request, "relatorio_qualidade.html", {
        "active": "relatorios", "d": dados, "lista_atual": lista,
        "itens": dados["listas"].get(lista, []),
        "perguntas": perguntas_para_ktc(session),
        "indicadores": indicadores(session),
        "listas_disponiveis": [
            ("revisao", "Precisam de revisão"), ("ktc_calculados", "KTC calculados"),
            ("ktc_cotados", "KTC cotados"), ("stale", "Preço vencido (STALE)"),
            ("sem_custo", "Sem custo cadastrado"), ("DAUNE", "Daune"),
            ("DECOR_TRICOT", "Decor Tricot"), ("KTC", "KTC (todos)"),
        ],
    })


@router.get("/relatorios/qualidade.json")
def qualidade_json(session: Session = Depends(get_session)):
    """Mesmo relatório em JSON — datas viram texto ISO para poder ser salvo e comparado."""
    dados = qualidade_da_base(session)
    dados["perguntas_para_ktc"] = perguntas_para_ktc(session)
    dados["indicadores"] = indicadores(session)
    return Response(content=json.dumps(dados, ensure_ascii=False, default=str),
                    media_type="application/json")

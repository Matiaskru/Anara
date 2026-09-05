"""Relatórios, saúde operacional e exportação.

Tudo aqui é **leitura**. Nenhuma rota deste módulo escreve uma linha — abrir um relatório
não pode mudar o estado do sistema, e é o tipo de coisa que se garante escrevendo, não
torcendo.

Dashboard, CSV e testes chamam **as mesmas funções** de `metrics_service`. É o que impede o
sintoma clássico: a tela dizer 42% e o CSV dizer 39% porque cada um recalculou à sua moda.
"""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlmodel import Session, select

from app import crm_service as crm
from app import metrics_service as mx
from app.db import get_session
from app.models import Cliente, Oportunidade, Usuario
from app.permissoes import (
    exigir_autenticado, exigir_economia, usuario_da_request, ve_economia,
)
from app.templating import templates

router = APIRouter()


def _periodo(atalho: str, inicio: str, fim: str) -> mx.Periodo:
    try:
        return mx.periodo_de(atalho, inicio or None, fim or None)
    except ValueError:
        raise crm.DadoInvalido("Datas inválidas. Use o formato AAAA-MM-DD.")


def _filtros(request: Request, responsavel: str) -> Optional[int]:
    """`eu` vira o id de quem está olhando. Filtro, não controle de acesso."""
    eu = usuario_da_request(request)
    if responsavel == "eu":
        return getattr(eu, "id", None)
    return int(responsavel) if responsavel.isdigit() else None


# ---------------------------------------------------------------------------
# Relatórios comerciais
# ---------------------------------------------------------------------------
@router.get("/relatorios", response_class=HTMLResponse)
def relatorios(request: Request, periodo: str = "", inicio: str = "", fim: str = "",
               responsavel: str = "", session: Session = Depends(get_session)):
    """Painel comercial. Todo papel autenticado entra; o que muda é o que a tela mostra."""
    exigir_autenticado(request)
    janela = _periodo(periodo, inicio, fim)
    painel = mx.painel_comercial(session, janela,
                                 responsavel_id=_filtros(request, responsavel))
    return templates.TemplateResponse(request, "relatorios.html", {
        "active": "relatorios", "painel": painel, "periodo": janela,
        "atalho": periodo, "inicio": inicio, "fim": fim, "responsavel": responsavel,
        "cotacoes": mx.painel_de_cotacoes(session, janela),
        "aprovacoes": mx.painel_de_aprovacoes(session, janela),
        "usuarios": session.exec(select(Usuario).where(Usuario.ativo == True)).all(),  # noqa: E712
        "ve_eco": ve_economia(request),
    })


@router.get("/relatorios/economico", response_class=HTMLResponse)
def economico(request: Request, periodo: str = "", inicio: str = "", fim: str = "",
              session: Session = Depends(get_session)):
    """Indicadores internos — **negado** a quem não vê economia, não filtrado.

    Um relatório econômico sem os números não é um relatório: é uma página que finge
    funcionar. A recusa é honesta.
    """
    exigir_economia(request)
    janela = _periodo(periodo, inicio, fim)
    return templates.TemplateResponse(request, "relatorio_economico.html", {
        "active": "relatorios", "periodo": janela, "atalho": periodo,
        "inicio": inicio, "fim": fim,
        "economico": mx.painel_economico(session, janela),
        "saude": mx.saude_operacional(session),
    })


@router.get("/saude", response_class=HTMLResponse)
def saude(request: Request, session: Session = Depends(get_session)):
    """O que está travado, o que precisa de atenção, e o que se sabe que falta."""
    exigir_economia(request)
    return templates.TemplateResponse(request, "saude.html", {
        "active": "saude", "saude": mx.saude_operacional(session),
    })


# ---------------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------------
def _csv(linhas, cabecalho, nome: str) -> Response:
    """CSV como resposta simples.

    Não é `StreamingResponse` de propósito: o arquivo é montado inteiro em memória de
    qualquer jeito — são dezenas de linhas, não gigabytes —, então o streaming não economiza
    nada e só torna o corpo mais difícil de ler em teste. `;` como separador porque é o que
    o Excel em português abre sem perguntar nada.
    """
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    carimbo = datetime.utcnow().strftime("%Y%m%d-%H%M")
    return Response(
        content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="anara-{nome}-{carimbo}.csv"'})


@router.get("/relatorios/oportunidades.csv")
def csv_oportunidades(request: Request, periodo: str = "", inicio: str = "", fim: str = "",
                      responsavel: str = "", status: str = "",
                      session: Session = Depends(get_session)):
    """Mesmos filtros da tela, mesmas definições — o CSV **reconcilia** com o dashboard.

    A coluna econômica só entra para quem pode vê-la; o resto do arquivo é idêntico, o que
    mantém o número de linhas igual para todo mundo.
    """
    exigir_autenticado(request)
    janela = _periodo(periodo, inicio, fim)
    responsavel_id = _filtros(request, responsavel)
    eco = ve_economia(request)

    todas = session.exec(select(Oportunidade)).all()
    if responsavel_id is not None:
        todas = [o for o in todas if o.responsavel_id == responsavel_id]
    if status:
        todas = [o for o in todas if o.status == status]
    # Encerradas entram pela janela do desfecho; abertas entram sempre — é a mesma regra
    # que o painel usa, e por isso os dois batem.
    todas = [o for o in todas
             if o.status == mx.ABERTA or mx._fechada_no_periodo(o, janela)]

    clientes = {c.id: c.nome for c in session.exec(select(Cliente)).all()}
    usuarios = {u.id: u.nome for u in session.exec(select(Usuario)).all()}

    cabecalho = ["id", "cliente", "oportunidade", "etapa", "status", "responsavel",
                 "origem", "criada_em", "previsao", "valor_estimado", "valor_cotado",
                 "valor_fechado", "motivo_perda", "aging_dias", "dias_ate_fechamento"]
    if eco:
        cabecalho.append("cotacao_vencedora_id")

    linhas = []
    for o in sorted(todas, key=lambda x: x.criado_em or datetime.min):
        linha = [
            o.id, clientes.get(o.cliente_id, ""), o.titulo, o.etapa, o.status,
            usuarios.get(o.responsavel_id, ""), o.origem or "",
            o.criado_em.strftime("%Y-%m-%d") if o.criado_em else "",
            o.data_prevista_fechamento or "",
            o.valor_estimado if o.valor_estimado is not None else "",
            mx.valor_cotado_atual(session, o.id) if o.status == mx.ABERTA else "",
            o.valor_fechado if o.valor_fechado is not None else "",
            o.motivo_perda or "",
            mx.aging(o) if o.status == mx.ABERTA else "",
            mx.tempo_ate_fechamento(o) or "",
        ]
        if eco:
            linha.append(o.cotacao_vencedora_id or "")
        linhas.append(linha)
    return _csv(linhas, cabecalho, "oportunidades")


@router.get("/relatorios/atividades.csv")
def csv_atividades(request: Request, responsavel: str = "",
                   session: Session = Depends(get_session)):
    exigir_autenticado(request)
    agora = datetime.utcnow()
    atividades = crm.atividades_de(session, responsavel_id=_filtros(request, responsavel),
                                   limite=5000)
    usuarios = {u.id: u.nome for u in session.exec(select(Usuario)).all()}
    linhas = [[a.id, a.tipo, a.titulo, usuarios.get(a.responsavel_id, ""),
               a.due_em.strftime("%Y-%m-%d %H:%M") if a.due_em else "",
               "sim" if a.concluida_em else "nao",
               "sim" if crm.esta_atrasada(a, agora) else "nao",
               a.oportunidade_id or ""]
              for a in atividades]
    return _csv(linhas, ["id", "tipo", "titulo", "responsavel", "quando", "concluida",
                         "atrasada", "oportunidade_id"], "atividades")


@router.get("/relatorios/cotacoes.csv")
def csv_cotacoes(request: Request, periodo: str = "", inicio: str = "", fim: str = "",
                 session: Session = Depends(get_session)):
    """Relatório de **documentos**: cada revisão é uma linha, e o cabeçalho diz isso."""
    exigir_autenticado(request)
    from app import workflow as wf
    from app import workflow_service as ws
    from app.models import Cotacao

    janela = _periodo(periodo, inicio, fim)
    cotacoes = [c for c in session.exec(select(Cotacao)).all()
                if janela.contem(c.criado_em)]
    clientes = {c.id: c.nome for c in session.exec(select(Cliente)).all()}
    eco = ve_economia(request)

    cabecalho = ["id", "numero", "revisao", "cliente", "status", "criada_em",
                 "emitida_em", "oportunidade_id", "total_comercial"]
    if eco:
        cabecalho += ["total_recomendado", "diferenca"]

    linhas = []
    for c in sorted(cotacoes, key=lambda x: (x.numero or "", x.revisao or 1)):
        itens = ws.itens_de(session, c.id)
        resumo = wf.resumo_comercial(itens) if itens else {}
        linha = [c.id, c.numero or "", c.revisao, clientes.get(c.cliente_id, ""),
                 c.status, c.criado_em.strftime("%Y-%m-%d") if c.criado_em else "",
                 c.issued_em.strftime("%Y-%m-%d") if c.issued_em else "",
                 c.oportunidade_id or "", resumo.get("total_negociado", "")]
        if eco:
            linha += [resumo.get("total_recomendado", ""), resumo.get("diferenca", "")]
        linhas.append(linha)
    return _csv(linhas, cabecalho, "cotacoes")


# ---------------------------------------------------------------------------
# Saúde técnica
# ---------------------------------------------------------------------------
@router.get("/health/detalhe")
def health_detalhe(request: Request, session: Session = Depends(get_session)):
    """Healthcheck autenticado, com um pouco mais de detalhe.

    O `/health` público responde o mínimo — nunca versão de migration, caminho de arquivo
    nem contagem de dados, porque um endpoint aberto que descreve a instalação é
    reconhecimento gratuito para quem estiver sondando.
    """
    exigir_economia(request)
    from app.db import engine

    saida = {"status": "ok", "banco": "desconhecido", "migration": None, "tabelas": {}}
    try:
        from sqlalchemy import text
        with engine.connect() as conexao:
            conexao.execute(text("select 1"))
            saida["banco"] = "ok"
            versao = conexao.execute(text("select version_num from alembic_version")).first()
            saida["migration"] = versao[0] if versao else None
    except Exception as erro:                       # noqa: BLE001
        saida["status"] = "degradado"
        saida["banco"] = f"erro: {type(erro).__name__}"

    from app.models import Cotacao, Produto
    saida["tabelas"] = {
        "produtos": len(session.exec(select(Produto)).all()),
        "cotacoes": len(session.exec(select(Cotacao)).all()),
        "oportunidades": len(session.exec(select(Oportunidade)).all()),
        "usuarios": len(session.exec(select(Usuario)).all()),
    }
    return JSONResponse(saida)

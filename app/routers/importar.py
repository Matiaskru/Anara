import json
import os
import uuid

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.excel_import import comissao_tabela_to_json, ler_excel, montar_diff
from app.models import BaseImportacao, Produto
from app.templating import templates

router = APIRouter()

TMP_DIR = os.path.expanduser("~/Anara-Cotacao/data/tmp_uploads")
os.makedirs(TMP_DIR, exist_ok=True)


@router.get("/importar", response_class=HTMLResponse)
def form(request: Request):
    return templates.TemplateResponse(request, "importar.html", {"active": "importar"})


@router.post("/importar/preview", response_class=HTMLResponse)
async def preview(request: Request, arquivo: UploadFile,
                   session: Session = Depends(get_session)):
    token = uuid.uuid4().hex
    tmp_path = os.path.join(TMP_DIR, f"{token}_{arquivo.filename}")
    with open(tmp_path, "wb") as f:
        f.write(await arquivo.read())

    resultado = ler_excel(tmp_path)

    if resultado.aviso and not resultado.produtos:
        return templates.TemplateResponse(request, "importar.html", {
            "active": "importar", "erro": resultado.aviso,
        })

    produtos_atuais = {p.sku_key: p for p in session.exec(select(Produto)).all()}
    diffs = montar_diff(produtos_atuais, resultado)
    diffs.sort(key=lambda d: {"novo": 0, "removido": 1, "alterado": 2, "igual": 3}[d.tipo])

    novos = sum(1 for d in diffs if d.tipo == "novo")
    removidos = sum(1 for d in diffs if d.tipo == "removido")
    alterados = sum(1 for d in diffs if d.tipo == "alterado")
    iguais = sum(1 for d in diffs if d.tipo == "igual")

    return templates.TemplateResponse(request, "importar_preview.html", {
        "active": "importar", "diffs": diffs,
        "novos": novos, "removidos": removidos, "alterados": alterados, "iguais": iguais,
        "nome_arquivo": arquivo.filename, "token": os.path.basename(tmp_path),
        "aviso": resultado.aviso,
    })


@router.post("/importar/confirmar")
def confirmar(request: Request, token: str = Form(...), nome_arquivo: str = Form(...),
              session: Session = Depends(get_session)):
    tmp_path = os.path.join(TMP_DIR, token)
    if not os.path.exists(tmp_path):
        return RedirectResponse(url="/importar", status_code=303)

    resultado = ler_excel(tmp_path)

    base = BaseImportacao(
        nome_arquivo=nome_arquivo,
        icms_pct=resultado.icms_pct, pis_cofins_pct=resultado.pis_cofins_pct,
        encargo_financeiro_pct=resultado.encargo_financeiro_pct,
        comissao_tabela_json=comissao_tabela_to_json(resultado.comissao_tabela),
        origem_uf=resultado.origem_uf,
        icms_por_estado_json=json.dumps(resultado.icms_por_estado),
        cenarios_fiscais_json=json.dumps(resultado.cenarios_fiscais),
        cambio_usd_brl=resultado.cambio_usd_brl,
        frete_usd_kg=resultado.frete_usd_kg,
        outras_desp_usd_un=resultado.outras_desp_usd_un,
    )
    session.add(base)
    session.commit()
    session.refresh(base)

    MEMORIA = ("ncm", "ii_aplicado", "peso_kg", "peso_fonte", "peso_tipo",
               "preco_ktc_usd", "frete_usd_un", "custo_net_usd", "cotacao_origem")
    ESTRUTURADOS = ("familia", "subcategoria", "largura_cm", "comprimento_cm", "gsm",
                    "thread_count", "cotton_pct", "poliester_pct", "weave", "plain_or_stripe",
                    "construcao", "acabamento", "material_ref")

    ktc = session.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
    cenario = ps.cenario_padrao_catalogo(session)
    regras, _ctx = ps.regras_da_cotacao(session, cenario)

    produtos_atuais = {p.sku_key: p for p in session.exec(select(Produto)).all()}
    vistos = set()
    preservados = 0

    for p in resultado.produtos:
        vistos.add(p.sku_key)
        existente = produtos_atuais.get(p.sku_key)
        novo = existente is None
        produto = existente or Produto(sku_key=p.sku_key, nome=p.nome)

        produto.categoria = p.categoria
        produto.nome = p.nome
        produto.especificacao = p.especificacao
        produto.base_importacao_id = base.id
        produto.ativo = True

        # peso real informado pela KTC nunca é substituído pelo estimado da planilha
        peso_real_atual = produto.peso_tipo == "REAL KTC"
        for campo in MEMORIA:
            if peso_real_atual and campo in ("peso_kg", "peso_fonte", "peso_tipo"):
                continue
            setattr(produto, campo, getattr(p, campo))

        if produto.fornecedor_id is None:
            produto.fornecedor_id = ktc.id if ktc else None
        dados = parse_produto(produto.categoria, produto.nome, produto.especificacao)
        for campo in ESTRUTURADOS:
            if getattr(produto, campo, None) in (None, "") and dados.get(campo) is not None:
                setattr(produto, campo, dados[campo])

        # o custo da planilha só entra quando não há custo melhor
        custo_calculado = produto.cost_method == CostMethod.ktc_calculated.value
        tem_fonte_mais_nova = bool(produto.custo_ref_data and produto.custo_ref_data > date(2026, 8, 1))
        if novo or not (custo_calculado or tem_fonte_mais_nova):
            produto.custo_unitario = p.custo_unitario
            if novo:
                produto.cost_method = CostMethod.legacy_excel.value
                produto.custo_confianca = CostConfidence.legacy.value
                produto.custo_ref_valor = p.preco_ktc_usd
                produto.custo_ref_moeda = "USD"
                produto.custo_ref_documento = nome_arquivo
                produto.custo_ref_tipo = "EXW_QUOTED"
                produto.precisa_revisao = True
                produto.revisao_motivo = ("Veio da planilha nesta importação. Confirmar se o preço "
                                          "KTC é atual antes de usar em volume.")
        else:
            preservados += 1
            session.add(CustoReferencia(
                produto_id=produto.id, sku_key=produto.sku_key,
                fornecedor_id=produto.fornecedor_id, tipo="EXW_QUOTED",
                valor=p.preco_ktc_usd or p.custo_unitario,
                moeda="USD" if p.preco_ktc_usd else "BRL",
                documento=nome_arquivo, cliente_documento="ANARA",
                confianca=CostConfidence.legacy.value, aplicado=False,
                notas=("Valor da planilha guardado sem aplicar: o produto já tem custo calculado "
                       "pelo motor industrial ou preço de documento mais recente.")))

        margem = ps.margem_padrao(session, produto)
        produto.margem_padrao_pct = margem.margem_pct
        if produto.custo_unitario:
            produto.preco_base = calcular_por_margem(produto.custo_unitario, 1, margem.margem_pct,
                                                     regras).preco_negociado
        session.add(produto)
        session.flush()

    for sku_key, existente in produtos_atuais.items():
        if sku_key not in vistos and existente.ativo:
            # SKU que sumiu da planilha é desativado, nunca apagado — e fornecedor nacional não
            # depende da planilha para existir
            if existente.cost_method != CostMethod.national_supplier.value:
                existente.ativo = False
                session.add(existente)

    session.commit()
    os.remove(tmp_path)

    return RedirectResponse(url=f"/produtos?importados={len(vistos)}&preservados={preservados}",
                            status_code=303)

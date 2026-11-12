"""Itens da Tabela de preços que ainda não existem no catálogo — cadastrar pelo Admin (12/11/2026).

A Tabela de preços ganhou, em 10/11, os itens da cotação KTC ANARA20102026 (20/10/2026): tapetes de banheiro
(N53–N57) e 600 fios 100% algodão (N58–N80). Para a vendedora cotá-los, cada um precisa existir como SKU, com a
`sku_key` que a lista da tabela usa (`app/tabela_comercial.SKUS_KTC_20_10`). A especificação de cada item
(família, medidas, construção, EXW cotado) está em `app/ktc_20_10_2026_itens.json` — a mesma fonte que formou o
preço fixo da tabela (`scripts/tabela_precos_2026_11_10/precos_ktc_20_10.py`).

O produto é montado como a calculadora monta (`calculadora.produto_simulado`) e a cotação é registrada pelo
caminho do Admin → Produtos e custos (`governanca_produtos.registrar_exw_ktc`: versão com fonte, documento e
auditoria). Peso: estimativa da família (a KTC não informou) — status ESTIMADO. O B2B é o da Tabela de preços
(`pricing_service.b2b_da_tabela_fixa`). Idempotente: SKU que já existe não é recriado.

600 fios: não existe tecido 600TC no cadastro de tecidos. O EXW é o COTADO (o motor industrial não entra); o
tecido 400TC 100% algodão só preenche composição/trama, e o SKU grava 600 fios e "600TC Sateen 100% Cotton".
"""
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from sqlmodel import Session, select

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ktc_20_10_2026_itens.json")
ORIGEM = "catalogo-tabela-2026-11-12"
CENARIO = {"estado_destino": "São Paulo", "contribuinte": "nao", "condicao_pagamento": "30"}


def carregar() -> dict:
    with open(ARQUIVO, encoding="utf-8") as f:
        dados = json.load(f)
    dados["data_ref"] = date.fromisoformat(dados["data"])
    return dados


def fonte(item: dict, dados: dict) -> str:
    return f"{dados['documento']} · SN {item['sn']}"


def material_por_nome(session: Session, nome: str):
    """O tecido pelo NOME (o id muda de banco para banco). Exige exatamente um ativo, liso."""
    from app.models import MaterialPreco
    achados = [m for m in session.exec(select(MaterialPreco).where(MaterialPreco.material == nome)).all()
               if getattr(m, "ativo", True) and m.plain_or_stripe == "plain"]
    if len(achados) != 1:
        raise ValueError(f"tecido {nome!r}: {len(achados)} cadastros ativos lisos (esperado 1)")
    return achados[0]


def produto_novo(session: Session, item: dict, dados: dict):
    """Produto em memória (fora da sessão) do item, com o EXW cotado e a `sku_key` da tabela."""
    from app import calculadora as calc
    from app.models import CostMethod
    if item["grupo"] == "tapete":
        p = calc.produto_simulado(session, familia=item["familia"], largura_cm=item["largura_cm"],
                                  comprimento_cm=item["comprimento_cm"], gsm=item["gsm"],
                                  composicao_toalha=item["composicao_toalha"])
    else:
        mat = material_por_nome(session, item["material"])
        kw = dict(familia=item["familia"], largura_cm=item["largura_cm"], comprimento_cm=item["comprimento_cm"],
                  material_id=mat.id)
        if item["familia"] == "Pillow Case":
            kw["abas"] = item["abas"] or None
        p = calc.produto_simulado(session, **kw)
        p.thread_count = item["thread_count"]
        p.material_ref = item["material_ref"]
        p.nome = calc._nome_simulado(p)
    p.sku_key = item["sku_key"]
    p.cost_method = CostMethod.ktc_quoted.value
    p.exw_cotado_usd = item["exw_usd"]
    p.exw_cotado_data = dados["data_ref"]
    p.exw_cotado_fonte = fonte(item, dados)
    return p


@dataclass
class LinhaPrevia:
    codigo: str
    nome: str
    produto_id: Optional[int]          # None = ainda não existe
    ativo: bool
    b2b: Optional[float]               # B2B da cotação (SP · não contribuinte · 30 dias)
    b2b_tabela: Optional[float]
    status: str

    @property
    def ok(self) -> bool:
        return self.ativo and self.b2b is not None and self.b2b == self.b2b_tabela


def _b2b(session, produto, cen):
    from app import pricing_service as ps
    from app.workflow import CUSTO_BLOQUEIA
    custo, mem0 = ps.custo_para_precificar(session, produto)
    status = ps.status_do_produto(session, produto, custo, mem0)
    mem = ps.memoria_do_preco(session, produto, cen)
    valor = (mem.get("b2b") or {}).get("preco_b2b")
    ok = bool(valor) and status not in CUSTO_BLOQUEIA and not mem.get("bloqueado") \
        and not mem0.get("premissas_faltantes")
    return (round(float(valor), 2) if ok else None), status


def previa(session: Session) -> List[LinhaPrevia]:
    """Cada item novo: existe? com que B2B sairia? — sem gravar nada (produto em memória, fora da sessão)."""
    from app import pricing_service as ps
    from app import tabela_precos as tp
    from app.models import Produto
    from app.routers.calculadora import _cenario_do_form
    dados = carregar()
    cen = _cenario_do_form(session, CENARIO)
    linhas = []
    with ps.cache_de_leitura(session):
        for it in dados["novos"]:
            existente = session.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first()
            prod = existente if existente is not None else produto_novo(session, it, dados)
            b2b, status = _b2b(session, prod, cen)
            linhas.append(LinhaPrevia(codigo=it["codigo"], nome=prod.nome,
                                      produto_id=existente.id if existente is not None else None,
                                      ativo=(existente.ativo if existente is not None else True),
                                      b2b=b2b, b2b_tabela=tp.preco_b2b(it["codigo"], "São Paulo", False, "30"),
                                      status=status if existente is not None else f"novo · {status}"))
    return linhas


def cadastrar(session: Session, ator) -> dict:
    """Cria os itens que faltam (sem commit — quem chama decide) e recalcula o B2B de referência do catálogo."""
    from app import admin_service as adm
    from app import governanca_produtos as gov
    from app import pricing_service as ps
    from app.dados_2026_09_21 import recalcular_preco_base_catalogo
    from app.models import Produto
    dados = carregar()
    criados, existentes = [], []
    for it in dados["novos"]:
        if session.exec(select(Produto).where(Produto.sku_key == it["sku_key"])).first() is not None:
            existentes.append(it["codigo"])
            continue
        prod = produto_novo(session, it, dados)
        prod.precisa_revisao = False
        prod.custo_ref_nota = f"Item {it['codigo']} da Tabela de preços (cotação KTC 20/10, SN {it['sn']})"
        peso = ps.peso_do_produto(session, prod)
        if peso.peso_kg:
            prod.peso_kg, prod.peso_tipo, prod.peso_fonte = peso.peso_kg, peso.tipo, peso.fonte
        prod.margem_padrao_pct = ps.margem_padrao(session, prod).margem_pct
        session.add(prod)
        session.flush()
        motivo = (f"Item {it['codigo']} da Tabela de preços (cotação KTC ANARA20102026 de 20/10/2026), "
                  "cadastrado no catálogo para a vendedora cotar")
        adm.registrar(session, ator=ator, acao="CRIAR_PRODUTO", entidade="Produto", entidade_id=prod.id,
                      escopo=f"SKU {prod.sku_key}", antes="—", depois=prod.nome, motivo=motivo, origem=ORIGEM)
        gov.registrar_exw_ktc(session, prod, exw_usd=it["exw_usd"], data_ref=dados["data_ref"],
                              documento=dados["documento"], fonte=fonte(it, dados), motivo=motivo, ator=ator)
        criados.append(it["codigo"])
    preco_base = recalcular_preco_base_catalogo(session)
    ps.invalidar_cache_de_leitura()
    return {"criados": criados, "ja_existiam": existentes, "preco_base": preco_base}

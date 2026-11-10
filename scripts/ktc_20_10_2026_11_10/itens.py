"""Itens da cotação KTC ANARA20102026 (20/10/2026) que entram no sistema em 10/11/2026 — fonte única.

`itens.json` traz:
  - `atualizar`: os 21 SKUs 250 fios 70/30 da Tabela comercial que recebem o EXW de 20/10;
  - `novos`: os 28 itens que estão na Tabela de preços e não existiam no catálogo — tapetes de banheiro
    (N53–N57) e 600 fios 100% algodão (N58–N80).

`produto_novo` monta o produto do item NOVO exatamente como a calculadora monta (`produto_simulado`), com o
EXW cotado. É a mesma função que forma o preço fixo da Tabela de preços
(`scripts/tabela_precos_2026_11_10/precos_ktc_20_10.py`) e que grava o SKU no catálogo (`aplicar.py`): o
preço da cotação sai igual ao da tabela enquanto o câmbio for o da tabela (5,19).

600 fios: não existe tecido 600TC no cadastro de tecidos. O EXW é o COTADO (o motor industrial não entra); o
tecido 400TC 100% algodão só preenche composição/trama, e o SKU grava 600 fios e "600TC Sateen 100% Cotton"
— é o número de fios que escolhe a regra de margem (≥ 400 fios).
"""
import json
import os
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))


def carregar():
    dados = json.load(open(os.path.join(AQUI, "itens.json"), encoding="utf-8"))
    dados["data_ref"] = date.fromisoformat(dados["data"])
    return dados


def material_por_nome(session, nome):
    """O tecido pelo NOME (o id muda de banco para banco). Exige exatamente um ativo, liso."""
    from sqlmodel import select
    from app.models import MaterialPreco
    achados = [m for m in session.exec(select(MaterialPreco).where(MaterialPreco.material == nome)).all()
               if getattr(m, "ativo", True) and m.plain_or_stripe == "plain"]
    if len(achados) != 1:
        raise SystemExit(f"tecido {nome!r}: {len(achados)} cadastros ativos lisos (esperado 1) — nada foi gravado")
    return achados[0]


def produto_novo(session, item, dados):
    """Produto em memória (não vai para a sessão) do item novo, com o EXW cotado."""
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
    p.cost_method = CostMethod.ktc_quoted.value
    p.exw_cotado_usd = item["exw_usd"]
    p.exw_cotado_data = dados["data_ref"]
    p.exw_cotado_fonte = fonte(item, dados)
    return p


def fonte(item, dados):
    return f"{dados['documento']} · SN {item['sn']}"

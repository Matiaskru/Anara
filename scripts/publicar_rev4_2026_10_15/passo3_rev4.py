"""Passo 3 — REV4 (13/10/2026, pedido da Silvana): SKU do lençol de baixo King 400 fios 100% algodão
280x290 (P074 da tabela), pelo motor (KTC_CALCULATED; peso calculado pelo motor, não gravado).

Portado de `~/Anara-Cotacao-Backups/tabela_comercial_gerador/sincronizar_rev4.py`. Idempotente por
sku_key. Não faz commit — quem decide é `aplicar.py`.
"""
from sqlmodel import select

from app import admin_service as adm
from app import calculadora as calc
from app import pricing_service as ps
from app.models import MaterialPreco, Produto

ORIGEM = "rev4-pendencias-silvana-2026-10-13"
NAO_COPIAR = {"id", "sku_key", "nome", "especificacao", "custo_unitario", "preco_base", "custo_ref_valor",
              "custo_ref_moeda", "custo_ref_data", "custo_ref_documento", "status_custo", "precisa_revisao",
              "revisao_motivo", "criado_em", "atualizado_em", "exw_cotado_usd", "exw_cotado_data",
              "exw_cotado_fonte", "peso_kg", "peso_tipo", "peso_fonte", "peso_data", "peso_documento"}
KING_400 = dict(sku_key="KTC · Bottom Sheet · 400TC Sateen 100% algodão plain · 280x290",
                nome="Lençol de baixo King 280x290 · 400 fios · 100% algodão",
                especificacao="280x290 · 400 fios · 100% algodão · sateen · colchão 1,80x2,00x0,30",
                ncm="6302.31.00")


def criar_king_400(s, ator):
    m400 = s.exec(select(MaterialPreco).where(MaterialPreco.material == "400TC Sateen 100% Cotton",
                                              MaterialPreco.plain_or_stripe == "plain",
                                              MaterialPreco.ativo == True)).one()  # noqa: E712
    p = s.exec(select(Produto).where(Produto.sku_key == KING_400["sku_key"])).first()
    novo = p is None
    if novo:
        mem = calc.produto_simulado(s, familia="Bottom Sheet", largura_cm=280, comprimento_cm=290, material_id=m400.id)
        cols = [c.name for c in Produto.__table__.columns if c.name not in NAO_COPIAR]
        p = Produto(**{c: getattr(mem, c) for c in cols})
        for k, v in KING_400.items():
            setattr(p, k, v)
        p.ativo = True
        s.add(p)
        s.flush()
        adm.registrar(s, ator=ator, acao="CRIAR_SKU", entidade="Produto", entidade_id=p.id, escopo=f"SKU {p.sku_key}",
                      antes="—", depois="ativo · KTC_CALCULATED",
                      motivo="Pedido da Silvana (13/10): lençol de baixo King 400 fios (P074 da tabela) não existia no catálogo",
                      origem=ORIGEM)
    custo, mem = ps.custo_para_precificar(s, p)
    print(f"400 King lençol de baixo: {'criado' if novo else 'já existia'} SKU {p.id} · {mem.get('metodo_exw')} · "
          f"EXW {mem.get('exw_usd'):.4f} · CNET {custo:.4f} · status {ps.status_do_produto(s, p, custo, mem)}")

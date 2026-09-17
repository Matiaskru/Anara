"""Cria, na cópia do servidor de UI (porta 8451), uma cotação mista KTC + Daune + Decor com 10
itens em modo margem, para o teste Playwright de transições. Imprime o id."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
A = os.path.expanduser("~/Anara-Cotacao-Backups/CRISIS_AUDIT_20260917/trabalho/ui.db")
os.environ["ANARA_DB_URL"] = f"sqlite:///{A}"
from scripts.crisis.ambiente import RequestFalsa, chamar, usuario_admin
from sqlmodel import Session, select
from app.db import engine
from app import pricing_service as ps
from app.models import Cliente, Cotacao, Fornecedor, Produto
from app.routers.cotacoes import adicionar_item
with Session(engine) as session:
    cliente = session.exec(select(Cliente)).first()
    cot = Cotacao(cliente_id=cliente.id, uf_origem_fiscal="SP", estado_destino="São Paulo", contribuinte_icms=True,
                  finalidade="REVENDA", condicao_pagamento="30", status="rascunho", freight_type="FOB", numero="UI-MISTA")
    session.add(cot); session.commit(); session.refresh(cot)
    escolhidos = []
    for codigo, n in (("KTC", 6), ("DAUNE", 2), ("DECOR_TRICOT", 2)):
        forn = session.exec(select(Fornecedor).where(Fornecedor.codigo == codigo)).first()
        for p in session.exec(select(Produto).where(Produto.fornecedor_id == forn.id, Produto.ativo == True)).all():  # noqa: E712
            custo, mem = ps.custo_para_precificar(session, p)
            if custo and ps.status_canonico_do_custo(custo, mem) == "CONFIRMADO":
                escolhidos.append(p)
                if len([x for x in escolhidos if x.fornecedor_id == forn.id]) == n:
                    break
    for i, p in enumerate(escolhidos):
        chamar(adicionar_item, RequestFalsa(usuario_admin()), cotacao_id=cot.id, produto_id=p.id, quantidade=float(3 + i), modo="margem", valor=None, session=session)
        session.commit()
    print(cot.id, len(escolhidos))

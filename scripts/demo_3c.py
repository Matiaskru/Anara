#!/usr/bin/env python3
"""Ambiente de demonstração da Fase 3C — numa CÓPIA do banco, nunca no `data/anara.db`.

    python3 scripts/demo_3c.py --db /tmp/anara-demo.db            # prepara a cópia e semeia
    python3 scripts/demo_3c.py --db /tmp/anara-demo.db --serve 8431   # ... e sobe o servidor

O que ele faz, nesta ordem:

1. copia `data/anara.db` para o caminho pedido (ou reaproveita a cópia se já existir e
   `--reset` não for passado) e aponta `ANARA_DB_URL` para ela **antes** de importar o app;
2. aplica as migrations na cópia (`alembic upgrade head`);
3. semeia um cenário comercial pequeno e reconhecível: dois usuários de demonstração
   (dona e vendedora), três clientes, vendas em cada status, cotações com Daune/Decor/KTC,
   uma venda vendida com pós-venda e uma perdida;
4. opcionalmente sobe o `uvicorn` sem `--reload`.

Usuários de demonstração (só existem na cópia):

    dona@anara.demo       senha  demo-dona-2026     OWNER
    vendedora@anara.demo  senha  demo-vend-2026     VENDEDOR_INTERNO

Serve para inspeção visual (Playwright/screenshots) e para o piloto de UX. O script é
idempotente por marcador: se a vendedora de demonstração já existe, não semeia de novo.
"""
import argparse
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

DONA = ("dona@anara.demo", "Marina Duarte", "demo-dona-2026")
VENDEDORA = ("vendedora@anara.demo", "Carolina Reis", "demo-vend-2026")


def preparar(caminho: str, reset: bool) -> None:
    origem = os.path.join(RAIZ, "data", "anara.db")
    if reset and os.path.exists(caminho):
        os.remove(caminho)
    if not os.path.exists(caminho):
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        shutil.copy2(origem, caminho)
        print(f"cópia criada: {caminho}")
    os.environ["ANARA_DB_URL"] = f"sqlite:///{caminho}"
    proc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=RAIZ,
                          env=dict(os.environ), capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-800:])
        raise SystemExit("alembic falhou na cópia")


def semear() -> None:
    from datetime import date, datetime, timedelta
    from types import SimpleNamespace

    from sqlmodel import Session, select

    from app import crm_service as crm
    from app import pos_venda_service as pv
    from app import workflow_service as ws
    from app.auth import hash_senha
    from app.db import engine
    from app.migrations import backfill, migrar
    from app.models import Fornecedor, Produto, Usuario
    from app.seeds import semear as seeds

    migrar(verbose=False)
    backfill(verbose=False)
    seeds(verbose=False)

    class Req:
        def __init__(self, u):
            self.state = SimpleNamespace(usuario=u)
            self.url = SimpleNamespace(path="/")
            self.headers = {"accept": "application/json"}
            self.cookies = {}
            self.query_params = {}
            self.scope = {"type": "http", "path": "/"}

    with Session(engine) as s:
        if s.exec(select(Usuario).where(Usuario.email == VENDEDORA[0])).first():
            print("cenário de demonstração já existe — nada a semear")
            return
        dona = Usuario(email=DONA[0], nome=DONA[1], senha_hash=hash_senha(DONA[2]),
                       papel="OWNER", can_manage_users=True, can_approve_quotes=True,
                       criado_por="demo_3c")
        vend = Usuario(email=VENDEDORA[0], nome=VENDEDORA[1], senha_hash=hash_senha(VENDEDORA[2]),
                       papel="VENDEDOR_INTERNO", criado_por="demo_3c")
        s.add(dona); s.add(vend); s.commit(); s.refresh(dona); s.refresh(vend)

        clientes = {}
        for nome, cidade, cnpj, fin in (
                ("Clara Resorts", "São Paulo", "12.345.678/0001-90", "REVENDA"),
                ("Hotel Praia Azul", "Rio de Janeiro", "23.456.789/0001-01", "USO_CONSUMO"),
                ("Pousada da Serra", "Minas Gerais", "34.567.890/0001-12", "REVENDA")):
            c = crm.criar_cliente(s, ator=dona, nome=nome, cidade_uf=cidade, cnpj_cpf=cnpj,
                                  finalidade=fin, telefone="(11) 3333-0000",
                                  email=f"compras@{nome.lower().replace(' ', '')}.com.br",
                                  contato_nome="Compras")
            s.commit()
            crm.criar_contato(s, ator=dona, cliente_id=c.id, nome="Ana Souza", cargo="Compras",
                              email="ana@cliente.com.br", telefone="(11) 99999-0000", principal=True)
            s.commit()
            clientes[nome] = c

        forn = {f.codigo: f for f in s.exec(select(Fornecedor)).all()}
        daune = s.exec(select(Produto).where(Produto.id == 242)).first()
        decor = s.exec(select(Produto).where(Produto.id == 274)).first()
        ktc = s.exec(select(Produto).where(Produto.fornecedor_id == forn["KTC"].id)
                     .where(Produto.ativo == True).where(Produto.custo_unitario > 0)  # noqa: E712
                     .order_by(Produto.id)).first()
        produtos = [p for p in (daune, decor, ktc) if p is not None]

        from app.routers.cotacoes import adicionar_item, criar_cotacao_da_venda

        def cotar(op, ator, itens, destino="São Paulo"):
            cot = criar_cotacao_da_venda(s, Req(ator), op, ator=ator, estado_destino=destino,
                                         contribuinte_icms=True, freight_type="FOB",
                                         condicao_pagamento="30")
            cot.uf_origem_fiscal = "SP"
            cot.finalidade = "REVENDA"
            cot.prazo_entrega = "30 dias após confirmação"
            s.add(cot); s.commit()
            for produto, qtd in itens:
                try:
                    adicionar_item(Req(dona), cot.id, produto_id=produto.id, quantidade=qtd,
                                   modo="margem", valor=None, session=s)
                except Exception as erro:  # noqa: BLE001
                    print(f"  item {produto.nome} não entrou: {erro}")
            s.commit()
            return cot

        def venda(nome_cliente, titulo, resp, etapa="RASCUNHO"):
            op = crm.criar_oportunidade(s, ator=resp, cliente_id=clientes[nome_cliente].id,
                                        titulo=titulo, responsavel_id=resp.id)
            s.commit()
            if etapa != "RASCUNHO":
                crm.mudar_etapa(s, op, etapa, ator=resp)
                s.commit()
            return op

        # 1. Rascunho, sem cotação ainda
        v1 = venda("Pousada da Serra", "Enxoval das suítes novas", vend)
        crm.registrar_atualizacao(s, v1, ator=vend, texto="Primeira reunião: 18 suítes, quer travesseiros de pluma.",
                                  proxima_atividade={"titulo": "Enviar proposta inicial", "tipo": "EMAIL",
                                                     "due_em": datetime.utcnow() + timedelta(days=2)})
        s.commit()
        # 2. Enviado, com cotação emitida (Daune + Decor)
        v2 = venda("Clara Resorts", "Renovação do enxoval 2026", vend)
        c2 = cotar(v2, vend, [(daune, 40), (decor, 24)])
        try:
            ws.emitir(s, c2, ator=dona); s.commit()
        except Exception as erro:  # noqa: BLE001
            print("  emissão v2 falhou:", erro)
        crm.registrar_atualizacao(s, v2, ator=vend, texto="Proposta enviada ao comprador por e-mail.",
                                  proxima_atividade={"titulo": "Ligar para o comprador", "tipo": "LIGACAO",
                                                     "due_em": datetime.utcnow() - timedelta(days=1)})
        s.commit()
        # 3. Negociação, com revisão em rascunho
        v3 = venda("Hotel Praia Azul", "Torre B — travesseiros e protetores", vend, etapa="NEGOCIACAO")
        c3 = cotar(v3, vend, [(daune, 120), (decor, 30)] + ([(ktc, 60)] if ktc else []),
                   destino="Rio de Janeiro")
        crm.registrar_atualizacao(s, v3, ator=vend, texto="Cliente pediu desconto nas peseiras; avaliando.")
        s.commit()
        # 4. Vendida, aguardando pagamento
        v4 = venda("Clara Resorts", "Reposição de travesseiros — ala sul", vend)
        c4 = cotar(v4, vend, [(daune, 60)])
        try:
            ws.emitir(s, c4, ator=dona); s.commit()
            crm.marcar_ganha(s, v4, c4.id, ator=vend); s.commit()
            pv.definir_entrega_prevista(s, v4, ator=vend, prevista_em=date.today() + timedelta(days=10))
            pv.registrar_entrega(s, v4, ator=vend)
            pv.registrar_faturamento(s, v4, ator=dona, faturado_em=date.today(), numero_documento_fiscal="NF-e 1042")
            pv.definir_pagamento_previsto(s, v4, ator=dona, previsto_em=date.today() + timedelta(days=30))
            s.commit()
        except Exception as erro:  # noqa: BLE001
            print("  venda v4 não fechou:", erro)
        # 5. Vendida e paga (mês passado)
        v5 = venda("Hotel Praia Azul", "Edredons — piloto 20 quartos", dona)
        c5 = cotar(v5, dona, [(decor, 20), (daune, 20)], destino="Rio de Janeiro")
        try:
            ws.emitir(s, c5, ator=dona); s.commit()
            crm.marcar_ganha(s, v5, c5.id, ator=dona); s.commit()
            v5.won_em = datetime.utcnow() - timedelta(days=40)
            pv.registrar_entrega(s, v5, ator=dona)
            pv.registrar_faturamento(s, v5, ator=dona, faturado_em=date.today() - timedelta(days=30),
                                     numero_documento_fiscal="NF-e 0987")
            pv.marcar_pago(s, v5, ator=dona, pago_em=datetime.utcnow() - timedelta(days=5))
            s.commit()
        except Exception as erro:  # noqa: BLE001
            print("  venda v5 não fechou:", erro)
        # 6. Perdida
        v6 = venda("Pousada da Serra", "Toalhas — piscina", vend, etapa="ENVIADO")
        cotar(v6, vend, [(decor, 10)], destino="Minas Gerais")
        crm.marcar_perdida(s, v6, ator=vend, motivo="PRECO", comentario="Fechou com fornecedor local.")
        s.commit()
        print("cenário de demonstração semeado")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="caminho da CÓPIA do banco")
    ap.add_argument("--reset", action="store_true", help="recria a cópia do zero")
    ap.add_argument("--serve", type=int, default=0, help="porta para subir o servidor")
    args = ap.parse_args()
    preparar(os.path.abspath(args.db), args.reset)
    semear()
    if args.serve:
        os.environ.setdefault("ANARA_SECRET_KEY", "demo-3c-nao-usar-em-producao")
        os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app",
                                   "--host", "127.0.0.1", "--port", str(args.serve)])


if __name__ == "__main__":
    main()

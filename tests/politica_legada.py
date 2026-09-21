"""Apoio de teste: colocar a política de 16/09/2026 de volta como VIGENTE, temporariamente.

A política de 21/09/2026 encerrou as regras de 16/09 (`valid_to`) e criou as suas. Os testes
que provam a mecânica de 16/09 — comissão única do bloco variável, piso, Daune travado —
continuam valendo para os itens que a pinaram, mas passam pelos routers, que resolvem a regra
vigente HOJE. Este contexto reabre as regras de 16/09 e desativa as de 21/09 enquanto durar,
e restaura tudo depois. É contexto de teste legado, não código de produção.
"""
from contextlib import contextmanager

from sqlmodel import select

from app import politica_comercial as pol
from app.models import MargemRegra


@contextmanager
def politica_16_09_vigente(session):
    regras = session.exec(select(MargemRegra)).all()
    estado = {}
    for r in regras:
        estado[r.id] = (r.valid_to, r.ativo)
        if r.politica == pol.ROTULO:
            r.valid_to = None
            session.add(r)
        elif r.politica == pol.ROTULO_2026_09_21:
            r.ativo = False
            session.add(r)
    session.commit()
    try:
        yield
    finally:
        for r in session.exec(select(MargemRegra)).all():
            if r.id in estado:
                r.valid_to, r.ativo = estado[r.id]
                session.add(r)
        session.commit()

"""Calculadora (23/11/2026): listrado que virava liso e fronha "sem abas" que saía "com aba" (Ines, Wyndham)."""
import pytest
from sqlmodel import select

from app import calculadora as calc
from app import config_service as cfg
from app.models import MaterialPreco


def _tecido(session, fios, listra, composicao="70/30"):
    for m in cfg.materiais(session):
        if m.thread_count == fios and m.plain_or_stripe == listra and composicao in (m.material or ""):
            return m
    pytest.skip(f"tecido {fios} {composicao} {listra} não semeado")


def test_listrado_nao_cai_no_sku_liso_de_mesmo_tecido(session):
    liso = _tecido(session, 250, "plain")
    listrado = _tecido(session, 250, "stripe")
    assert liso.material == listrado.material          # mesmo nome de tecido: era isso que colidia
    a = calc.salvar_no_catalogo(session, "Flat Sheet", 300, 300, material_id=liso.id)
    b = calc.salvar_no_catalogo(session, "Flat Sheet", 300, 300, material_id=listrado.id)
    session.commit()
    assert a.id != b.id and a.plain_or_stripe == "plain" and b.plain_or_stripe == "stripe"
    assert "listrado" in b.nome and "listrado" not in a.nome
    assert b.sku_key.endswith("· stripe")
    # de novo: reaproveita cada um (idempotente)
    assert calc.salvar_no_catalogo(session, "Flat Sheet", 300, 300, material_id=listrado.id).id == b.id


def test_sku_antigo_listrado_com_chave_plain_nao_engole_o_liso(session):
    """Produto gravado antes da correção: listrado com chave '· plain'. Pedir o liso cria o liso, à parte."""
    listrado = _tecido(session, 250, "stripe")
    velho = calc.salvar_no_catalogo(session, "Flat Sheet", 210, 310, material_id=listrado.id)
    velho.sku_key = velho.sku_key.replace("· stripe", "· plain")       # como ficou em produção
    session.add(velho)
    session.commit()
    liso = _tecido(session, 250, "plain")
    novo = calc.salvar_no_catalogo(session, "Flat Sheet", 210, 310, material_id=liso.id)
    session.commit()
    assert novo.id != velho.id and novo.plain_or_stripe == "plain" and "listrado" not in novo.nome


@pytest.mark.parametrize("abas,esperado", [(None, "sem abas"), (2, "2 abas"), (3, "3 abas"), (4, "4 abas")])
def test_fronha_da_calculadora_diz_as_abas_e_nao_com_aba(session, abas, esperado):
    tecido = next(m for m in cfg.materiais(session) if m.thread_count == 300 and m.plain_or_stripe == "plain")
    p = calc.salvar_no_catalogo(session, "Pillow Case", 50, 70, material_id=tecido.id, abas=abas)
    session.commit()
    assert p.nome.startswith("Fronha 50x70") and p.nome.endswith(f"· {esperado}"), p.nome
    assert "com aba" not in p.nome and p.nome.count("abas") == 1

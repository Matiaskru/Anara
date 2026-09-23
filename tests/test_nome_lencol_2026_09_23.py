"""Lençol de cima não é lençol plano (23/09/2026).

Relato da operação: *"Estou selecionando lençol de cima e sai lençol plano"*. A causa era o
nome canônico ser montado pela **categoria**, e `Flat Sheet` e `Top Sheet` dividirem a
categoria "Lençol Plano" no catálogo. A família — a única coisa que distingue as duas peças —
não entrava na conta.

Não é cosmético: lençol de cima e lençol plano têm medida e uso diferentes, e o nome é o que
vai para a cotação, para o PDF e para o pedido da fábrica. O cliente recebe o que está escrito.
"""
import pytest
from sqlmodel import select

from app import calculadora as calc
from app.models import Fornecedor, Produto
from app.nomes import nome_canonico


def _produto(familia, categoria, **kw):
    dados = dict(sku_key="x", nome="x", familia=familia, categoria=categoria,
                 largura_cm=260, comprimento_cm=250, thread_count=300, cotton_pct=1.0)
    dados.update(kw)
    return Produto(**dados)


def test_1_lencol_de_cima_nao_vira_lencol_plano():
    assert nome_canonico(_produto("Top Sheet", "Lençol de Cima")).startswith("Lençol de cima")


def test_2_o_sku_legado_tambem_se_corrige(session):
    """Os 7 Top Sheet do catálogo foram gravados com categoria 'Lençol Plano'.

    A família desempata, então o nome sai certo **sem** precisar mexer na categoria deles.
    """
    assert nome_canonico(_produto("Top Sheet", "Lençol Plano")).startswith("Lençol de cima")


def test_3_lencol_plano_continua_lencol_plano():
    assert nome_canonico(_produto("Flat Sheet", "Lençol Plano")).startswith("Lençol plano")
    assert nome_canonico(_produto("Fitted Sheet", "Lençol com Elástico")).startswith(
        "Lençol com elástico")


def test_4_a_calculadora_oferece_lencol_de_cima_com_categoria_propria():
    """Top Sheet deixou de herdar a categoria do Flat Sheet — senão o nome recai no dela."""
    assert calc.ROTULO_POR_FAMILIA["Top Sheet"] == "Lençol de cima"
    assert calc.CATEGORIA_POR_FAMILIA["Top Sheet"] != calc.CATEGORIA_POR_FAMILIA["Flat Sheet"]


def test_5_produto_da_calculadora_nasce_com_o_nome_certo(session):
    p = calc.produto_simulado(session, "Top Sheet", 260, 250, material_id=None)
    assert p.nome.startswith("Lençol de cima"), p.nome
    assert p.familia == "Top Sheet"


def test_6_nenhum_top_sheet_ativo_fica_chamado_de_lencol_plano(session):
    """Varredura: depois do reparo, nenhum lençol de cima se apresenta como plano."""
    for p in session.exec(select(Produto).where(Produto.ativo == True)).all():   # noqa: E712
        if (p.familia or "").strip().lower() == "top sheet":
            assert nome_canonico(p).startswith("Lençol de cima"), p.sku_key


# ===========================================================================
# Lençol de baixo: mesma peça do plano quando não tem elástico
# ===========================================================================
def test_7_lencol_de_baixo_e_oferecido_e_e_calculavel():
    from app.spec_parser import FAMILIAS_CALCULAVEIS
    assert calc.ROTULO_POR_FAMILIA["Bottom Sheet"] == "Lençol de baixo"
    assert calc.TIPO_POR_FAMILIA["Bottom Sheet"] == "tecido"      # não é "sob consulta"
    assert "Bottom Sheet" in FAMILIAS_CALCULAVEIS


def test_8_mesmo_tamanho_fios_e_composicao_dao_o_mesmo_preco(session):
    """A regra que a operação descreveu: plano, de cima e de baixo sem elástico são o mesmo pano.

    Não é coincidência de número — `calcular_bottom_sheet(com_elastico=False)` e
    `calcular_flat_sheet` chamam os dois `calcular_tecido_plano` com painel único.
    """
    from app import pricing_service as ps
    material = next(m for m in __import__("app.config_service", fromlist=["x"]).materiais(session)
                    if m.thread_count == 300)
    custos = {}
    for familia in ("Flat Sheet", "Top Sheet", "Bottom Sheet"):
        p = calc.produto_simulado(session, familia, 200, 290, material_id=material.id)
        custos[familia] = ps.calcular_exw(session, p).exw_usd
    assert custos["Flat Sheet"] is not None
    assert custos["Top Sheet"] == custos["Flat Sheet"] == custos["Bottom Sheet"], custos


def test_9_com_elastico_continua_sem_formula(session):
    """A outra metade da regra: com elástico não tem fórmula aprovada e não se inventa uma."""
    from app import pricing_service as ps
    p = calc.produto_simulado(session, "Bottom Sheet", 200, 290, material_id=None)
    p.construcao = "com elástico"
    resultado = ps.calcular_exw(session, p)
    assert resultado.exw_usd is None
    assert "formula_fitted" in resultado.faltando
    assert calc.TIPO_POR_FAMILIA["Fitted Sheet"] == "sem_formula"


def test_10_o_nome_do_lencol_de_baixo_nao_se_confunde(session):
    p = calc.produto_simulado(session, "Bottom Sheet", 200, 290, material_id=None)
    assert p.nome.startswith("Lençol de baixo"), p.nome


# ===========================================================================
# Fronha: as abas escolhidas precisam aparecer
# ===========================================================================
def test_11_fronha_salva_pela_calculadora_mostra_as_abas(session):
    """Ela escolheu 4 abas e a cotação não dizia nada — a conta estava certa, o nome não."""
    material = next(m for m in __import__("app.config_service", fromlist=["x"]).materiais(session)
                    if m.thread_count == 300)
    p = calc.salvar_no_catalogo(session, "Pillow Case", 50, 70, material_id=material.id, abas=4)
    session.commit()
    assert "4 abas" in p.nome, p.nome
    assert p.construcao == "4 abas"


def test_12_fronha_standard_nao_ganha_ruido_no_nome(session):
    material = next(m for m in __import__("app.config_service", fromlist=["x"]).materiais(session)
                    if m.thread_count == 300)
    p = calc.salvar_no_catalogo(session, "Pillow Case", 50, 90, material_id=material.id)
    session.commit()
    assert "abas" not in p.nome, p.nome

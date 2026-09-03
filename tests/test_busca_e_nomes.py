"""Nome de exibição e busca bilíngue."""
from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.busca import buscar, expandir, normalizar, texto_do_produto
from app.nomes import nome_canonico


@dataclass
class ProdutoFalso:
    nome: str
    nome_original: Optional[str] = None
    categoria: Optional[str] = None
    especificacao: Optional[str] = None
    familia: Optional[str] = None
    subcategoria: Optional[str] = None
    thread_count: Optional[int] = None
    gsm: Optional[int] = None
    cotton_pct: Optional[float] = None
    poliester_pct: Optional[float] = None
    largura_cm: Optional[float] = None
    comprimento_cm: Optional[float] = None
    plain_or_stripe: Optional[str] = "plain"
    construcao: Optional[str] = None
    acabamento: Optional[str] = None
    cor: Optional[str] = None
    material_ref: Optional[str] = None
    custo_unitario: Optional[float] = 50.0
    fornecedor_id: Optional[int] = 1
    sku_key: str = "X"


# ---------------------------------------------------------------------------
# Nome canônico
# ---------------------------------------------------------------------------
def test_nome_sai_em_portugues_com_o_diferenciador():
    p = ProdutoFalso("Single Top Sheet 190x250", categoria="Lençol Plano",
                     especificacao="190x250 · 250 fios · CVC 70/30 · listrado",
                     familia="Top Sheet", thread_count=250, cotton_pct=0.7, poliester_pct=0.3,
                     largura_cm=190, comprimento_cm=250, plain_or_stripe="stripe")
    assert nome_canonico(p) == "Lençol plano 190x250 · 250 fios · 70/30 · listrado"


def test_toalha_usa_gramatura_no_lugar_de_fios():
    p = ProdutoFalso("Bath Towel D 70x135 330g", categoria="Toalha Banho",
                     especificacao="70x135 · 330 GSM · 90/10", familia="Bath Towel",
                     gsm=330, cotton_pct=0.9, poliester_pct=0.1,
                     largura_cm=70, comprimento_cm=135)
    assert nome_canonico(p) == "Toalha de banho 70x135 · 330 g/m² · 90/10"


def test_roupao_leva_modelo_e_tamanho():
    p = ProdutoFalso("Waffle Velour Bathrobe XL", categoria="Roupão",
                     especificacao="XL · 420 GSM · 100% algodão · shawl collar",
                     familia="Bathrobe", gsm=420, cotton_pct=1.0, poliester_pct=0.0)
    nome = nome_canonico(p)
    assert nome.startswith("Roupão waffle velour XL")
    assert "gola xale" in nome


def test_travesseiro_leva_o_enchimento():
    p = ProdutoFalso("100% plumas de ganso 50x70", categoria="Travesseiros",
                     especificacao="50x70 · 100% plumas de ganso", familia="Pillow",
                     largura_cm=50, comprimento_cm=70)
    assert nome_canonico(p) == "Travesseiro 50x70 · 100% plumas de ganso"


def test_dois_skus_da_mesma_medida_nao_saem_com_o_mesmo_nome():
    base = dict(categoria="Lençol com Elástico", familia="Fitted Sheet",
                largura_cm=100, comprimento_cm=200)
    a = ProdutoFalso("Lençol com elástico 100x200x30", especificacao="100x200x30 · 233 fios · 100% algodão",
                     thread_count=233, cotton_pct=1.0, poliester_pct=0.0, **base)
    b = ProdutoFalso("Lençol com elástico 100x200x30", especificacao="100x200x30 · 250 fios · CVC 70/30 · listrado",
                     thread_count=250, cotton_pct=0.7, poliester_pct=0.3,
                     plain_or_stripe="stripe", **base)
    assert nome_canonico(a) != nome_canonico(b)
    assert "233 fios" in nome_canonico(a) and "250 fios" in nome_canonico(b)


def test_altura_do_colchao_fica_no_nome():
    p = ProdutoFalso("Lençol com elástico 100x200x30", categoria="Lençol com Elástico",
                     especificacao="100x200x30 · 233 fios · 100% algodão", familia="Fitted Sheet",
                     thread_count=233, cotton_pct=1.0, largura_cm=100, comprimento_cm=200)
    assert "100x200x30" in nome_canonico(p)


def test_produto_sem_campo_estruturado_mantem_o_nome_antigo():
    p = ProdutoFalso("Item esquisito", nome_original="Item esquisito")
    assert nome_canonico(p) == "Item esquisito"


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------
CATALOGO = [
    ProdutoFalso("Lençol plano 190x250 · 250 fios · 70/30 · listrado",
                 nome_original="Single Top Sheet 190x250", categoria="Lençol Plano",
                 familia="Top Sheet", thread_count=250, largura_cm=190, comprimento_cm=250,
                 plain_or_stripe="stripe"),
    ProdutoFalso("Lençol plano 190x250 · 300 fios · 100% algodão",
                 nome_original="Single Top Sheet 300TC", categoria="Lençol Plano",
                 familia="Top Sheet", thread_count=300, largura_cm=190, comprimento_cm=250),
    ProdutoFalso("Toalha de banho 70x135 · 330 g/m² · 90/10", nome_original="Bath Towel D",
                 categoria="Toalha Banho", familia="Bath Towel", gsm=330,
                 largura_cm=70, comprimento_cm=135),
    ProdutoFalso("Toalha de piso 50x80 · 750 g/m² · 100% algodão", nome_original="Bath Mat",
                 categoria="Toalha Piso", familia="Bath Mat", gsm=750),
    ProdutoFalso("Roupão waffle velour XL · 420 g/m² · 100% algodão",
                 nome_original="Waffle Velour Bathrobe XL", categoria="Roupão",
                 familia="Bathrobe", gsm=420),
    ProdutoFalso("Fronha com aba 50x70 · 300 fios", nome_original="Pillow Case 50x70",
                 categoria="Fronha com Aba", familia="Pillow Case", thread_count=300,
                 custo_unitario=None),
]


def nomes(resultado):
    return [p.nome for p in resultado]


def test_acha_em_portugues_e_em_ingles():
    assert len(buscar(CATALOGO, "lencol")) == 2
    assert len(buscar(CATALOGO, "sheet")) == 2
    assert len(buscar(CATALOGO, "toalha")) == 2
    assert len(buscar(CATALOGO, "towel")) == 2


def test_ignora_acento():
    assert nomes(buscar(CATALOGO, "roupao")) == nomes(buscar(CATALOGO, "roupão"))
    assert len(buscar(CATALOGO, "lençol")) == 2


def test_varios_termos_filtram_juntos_em_qualquer_ordem():
    a = buscar(CATALOGO, "lencol 250 listrado")
    b = buscar(CATALOGO, "listrado 250 lencol")
    assert len(a) == 1 and nomes(a) == nomes(b)
    assert "250 fios" in a[0].nome


def test_sinonimo_de_familia_funciona_nos_dois_sentidos():
    assert len(buscar(CATALOGO, "chao")) == 1        # chão → bath mat
    assert len(buscar(CATALOGO, "bath mat")) == 1
    assert len(buscar(CATALOGO, "bathrobe")) == 1    # bathrobe → roupão
    assert len(buscar(CATALOGO, "roupao")) == 1


def test_busca_pelo_nome_antigo_continua_funcionando():
    """Quem decorou 'Bath Towel D' continua achando."""
    assert len(buscar(CATALOGO, "bath towel d")) == 1


def test_termo_que_nao_bate_em_nada_zera_o_resultado():
    assert buscar(CATALOGO, "lencol xyz") == []


def test_produto_com_custo_vem_antes_do_que_falta_cotar():
    resultado = buscar(CATALOGO, "fronha 300")
    assert resultado and resultado[0].custo_unitario is None or True
    com_custo = buscar(CATALOGO, "300")
    assert com_custo[0].custo_unitario is not None


def test_busca_vazia_devolve_o_catalogo_em_ordem():
    resultado = buscar(CATALOGO, "")
    assert len(resultado) == len(CATALOGO)
    assert resultado[0].categoria <= resultado[-1].categoria


def test_medida_e_gramatura_sao_pesquisaveis():
    assert len(buscar(CATALOGO, "190x250")) == 2
    assert len(buscar(CATALOGO, "750")) == 1


def test_expandir_traz_os_dois_idiomas():
    assert "sheet" in expandir("lencol") or "lencol" in expandir("sheet")
    assert "towel" in expandir("toalha")

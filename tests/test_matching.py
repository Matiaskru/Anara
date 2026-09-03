"""Casamento por especificação estruturada — nunca por nome."""
from dataclasses import dataclass
from typing import Optional

from app.fontes_ktc import PI_23_08, itens
from app.matching import COM_DIVERGENCIA, EXATO, SEM_MATCH, casar, melhor_match

ITENS_PI = list(itens(PI_23_08))


@dataclass
class ProdutoFalso:
    nome: str
    familia: Optional[str] = None
    largura_cm: Optional[float] = None
    comprimento_cm: Optional[float] = None
    thread_count: Optional[int] = None
    gsm: Optional[int] = None
    cotton_pct: Optional[float] = None
    plain_or_stripe: str = "plain"
    acabamento: Optional[str] = None


def test_casa_produto_identico():
    p = ProdutoFalso("Duvet Cover Single 190x260 300TC", "Duvet Cover", 190, 260, 300,
                     cotton_pct=1.0)
    m = melhor_match(p, ITENS_PI, "Duvet Cover Single 190x260 300TC · open bag")
    assert m.status == EXATO and m.item["preco_usd"] == 20.30


def test_listrado_nao_casa_com_liso_sem_aviso():
    """O 250TC do catálogo é listrado; a PI é lisa e mais barata. Casa, mas com divergência."""
    p = ProdutoFalso("Single Top Sheet 190x250", "Top Sheet", 190, 250, 250, cotton_pct=0.7,
                     plain_or_stripe="stripe")
    m = melhor_match(p, ITENS_PI, "Single Top Sheet 190x250 · listrado")
    assert m.status == COM_DIVERGENCIA
    assert any("listrado" in d for d in m.divergencias)


def test_fronha_bordada_nao_casa_com_fronha_lisa():
    p = ProdutoFalso("Pillow Case c/Aba Bordada 50x70+5", "Pillow Case", 50, 70, 300,
                     cotton_pct=1.0, acabamento="bordado")
    m = melhor_match(p, ITENS_PI, "Pillow Case c/Aba Bordada 50x70+5 · bordado")
    assert m.status == SEM_MATCH


def test_fronha_sem_oxford_nao_casa_com_oxford():
    """O documento diz oxford de 3 lados; o catálogo não diz quantos lados — não dá para supor."""
    p = ProdutoFalso("Fronha sem aba 50x70", "Pillow Case", 50, 70, 300, cotton_pct=1.0)
    m = melhor_match(p, ITENS_PI, "Fronha sem aba 50x70 · 300 fios")
    assert m.status == SEM_MATCH


def test_roupao_waffle_nao_casa_com_roupao_liso():
    waffle = ProdutoFalso("Waffle Velour Bathrobe XL", "Bathrobe", gsm=420, cotton_pct=1.0)
    liso = ProdutoFalso("Plain Velour Bathrobe XL", "Bathrobe", gsm=420, cotton_pct=1.0)
    m1 = melhor_match(waffle, ITENS_PI, "Waffle Velour Bathrobe XL · shawl collar")
    m2 = melhor_match(liso, ITENS_PI, "Plain Velour Bathrobe XL · shawl collar")
    assert m1.item["preco_usd"] == 26.00
    assert m2.item["preco_usd"] == 25.00


def test_tamanho_diferente_nao_casa():
    p = ProdutoFalso("Waffle Velour Bathrobe M", "Bathrobe", gsm=420, cotton_pct=1.0)
    m = melhor_match(p, ITENS_PI, "Waffle Velour Bathrobe M · shawl collar")
    assert m.status == SEM_MATCH


def test_familia_diferente_nunca_casa():
    p = ProdutoFalso("Protetor de fronha 50x70", "Pillow Protector", 50, 70, 300, cotton_pct=1.0)
    assert melhor_match(p, ITENS_PI, p.nome).status == SEM_MATCH


def test_medida_diferente_nao_casa():
    p = ProdutoFalso("Duvet Cover 200x270", "Duvet Cover", 200, 270, 300, cotton_pct=1.0)
    assert melhor_match(p, ITENS_PI, "Duvet Cover 200x270 · open bag").status == SEM_MATCH


def test_divergencia_de_composicao_e_registrada():
    """Toalha do catálogo diz 100% algodão; a PI diz 90/10. Casa, mas com a diferença anotada."""
    p = ProdutoFalso("Hand Towel 50x85 550g", "Hand Towel", 50, 85, gsm=550, cotton_pct=1.0)
    m = casar(p, ITENS_PI[32], "Hand Towel 50x85 550g")
    assert m.status == COM_DIVERGENCIA
    assert any("composição" in d for d in m.divergencias)

"""Protetor de fronha → Protetor de travesseiro (26/10/2026, pedido da operação).

O nome que vai para a proposta passa a ser "Protetor de travesseiro"; a busca acha pelos dois nomes;
o script reescreve só o `nome` gravado dos SKUs da família, com trilha, e é idempotente.
"""
from itertools import count

from sqlmodel import select

from app.busca import buscar
from app.models import AuditLog, Produto
from app.nomes import nome_canonico
from tests.test_busca_e_nomes import ProdutoFalso

_SEQ = count(1)


def _protetor(nome="Protetor de fronha 50x70 · 300 fios"):
    return ProdutoFalso(nome, categoria="Protetor de Fronha", especificacao="50x70 · 300 fios",
                        familia="Pillow Protector", thread_count=300, largura_cm=50, comprimento_cm=70)


def test_nome_da_peca_e_protetor_de_travesseiro():
    assert nome_canonico(_protetor()) == "Protetor de travesseiro 50x70 · 300 fios"


def test_busca_acha_pelo_nome_novo_e_pelo_antigo():
    catalogo = [_protetor("Protetor de travesseiro 50x70 · 300 fios")]
    assert len(buscar(catalogo, "protetor de travesseiro")) == 1
    assert len(buscar(catalogo, "protetor de fronha")) == 1


def test_script_renomeia_so_o_nome_dos_protetores_com_trilha_e_e_idempotente(session):
    from scripts.renomear_protetor_travesseiro_2026_10_26 import renomear

    n = next(_SEQ)
    prot = Produto(sku_key=f"__teste_protetor_{n}__", nome="Protetor de fronha 50x70 · 300 fios",
                   nome_original="Protetor de fronha 50x70", categoria="Protetor de Fronha",
                   especificacao="50x70 · 300 fios", familia="Pillow Protector", thread_count=300,
                   largura_cm=50, comprimento_cm=70, preco_base=59.98)
    fronha = Produto(sku_key=f"__teste_fronha_{n}__", nome="Fronha com aba 50x70 · 300 fios · 100% algodão",
                     categoria="Fronha com Aba", familia="Pillow Case", thread_count=300, cotton_pct=1.0,
                     largura_cm=50, comprimento_cm=70)
    session.add(prot)
    session.add(fronha)
    session.commit()

    mudancas = renomear(session)
    session.commit()
    assert [(m[0], m[2], m[3]) for m in mudancas if m[0] == prot.id] == [
        (prot.id, "Protetor de fronha 50x70 · 300 fios", "Protetor de travesseiro 50x70 · 300 fios")]
    session.refresh(prot)
    session.refresh(fronha)
    assert prot.nome == "Protetor de travesseiro 50x70 · 300 fios"
    assert (prot.nome_original, prot.categoria, prot.sku_key, prot.preco_base) == (
        "Protetor de fronha 50x70", "Protetor de Fronha", f"__teste_protetor_{n}__", 59.98)
    assert fronha.nome == "Fronha com aba 50x70 · 300 fios · 100% algodão"
    log = session.exec(select(AuditLog).where(AuditLog.acao == "RENAME_PRODUCT",
                                              AuditLog.entidade_id == prot.id)).one()
    assert (log.versao_anterior, log.versao_nova) == ("Protetor de fronha 50x70 · 300 fios",
                                                      "Protetor de travesseiro 50x70 · 300 fios")
    assert [m for m in renomear(session) if m[0] == prot.id] == []      # segunda vez: nada

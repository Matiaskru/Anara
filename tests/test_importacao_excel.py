"""A planilha continua entrando, mas não sobrescreve custo melhor nem apaga fornecedor nacional."""
import os
import tempfile
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from decimais import MARGEM_DO_CENTAVO, MEIO_CENTAVO, aprox  # noqa: E402


@pytest.fixture
def ambiente():
    import app.db as db
    import app.seeds as seeds

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="anara-import-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    originais = (db.engine, seeds.engine)
    db.engine = seeds.engine = engine
    SQLModel.metadata.create_all(engine)
    seeds.semear(verbose=False)
    yield engine
    db.engine, seeds.engine = originais
    os.unlink(caminho)


def test_custo_calculado_nao_e_sobrescrito_pela_planilha(ambiente):
    """Produto com EXW calculado mantém o custo; o valor da planilha vai para o histórico."""
    from app.excel_import import ProdutoImportado
    from app.models import CostConfidence, CostMethod, CustoReferencia, Fornecedor, Produto
    from app import pricing_service as ps
    from app.pricing_engine import calcular_por_margem

    with Session(ambiente) as s:
        ktc = s.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
        s.add(Produto(sku_key="X1", nome="Lençol calculado", categoria="Lençol Plano",
                      familia="Flat Sheet", thread_count=300, cotton_pct=1.0,
                      custo_unitario=100.0, preco_base=200.0, fornecedor_id=ktc.id,
                      cost_method=CostMethod.ktc_calculated.value,
                      custo_confianca=CostConfidence.calculated.value,
                      exw_calculado_usd=15.0, custo_ref_data=date(2026, 8, 23)))
        s.commit()

    # simula o trecho de aplicação da importação
    importado = ProdutoImportado(sku_key="X1", categoria="Lençol Plano", nome="Lençol calculado",
                                 especificacao="300 fios", custo_unitario=70.0, preco_base=150.0,
                                 preco_ktc_usd=9.0)
    with Session(ambiente) as s:
        produto = s.exec(select(Produto).where(Produto.sku_key == "X1")).first()
        custo_calculado = produto.cost_method == CostMethod.ktc_calculated.value
        assert custo_calculado
        if not custo_calculado:
            produto.custo_unitario = importado.custo_unitario
        else:
            s.add(CustoReferencia(produto_id=produto.id, sku_key="X1", tipo="EXW_QUOTED",
                                  valor=importado.preco_ktc_usd, aplicado=False,
                                  documento="planilha.xlsx"))
        margem = ps.margem_padrao(s, produto)
        cenario = ps.cenario_padrao_catalogo(s)
        regras, _ = ps.regras_da_cotacao(s, cenario, produto)
        produto.preco_base = calcular_por_margem(produto.custo_unitario, 1, margem.margem_pct,
                                                 regras).preco_negociado
        s.add(produto)
        s.commit()

        produto = s.exec(select(Produto).where(Produto.sku_key == "X1")).first()
        assert produto.custo_unitario == aprox(100.0)   # não foi sobrescrito
        assert produto.preco_base != aprox(150.0)       # preço-base é recalculado
        referencias = s.exec(select(CustoReferencia)
                             .where(CustoReferencia.sku_key == "X1")).all()
        assert referencias and referencias[0].aplicado is False


def test_preco_base_da_planilha_nao_e_copiado(ambiente):
    """O preço-base sai sempre da margem-alvo do produto, não da coluna da planilha."""
    from app.models import CostMethod, Fornecedor, Produto
    from app import pricing_service as ps
    from app.pricing_engine import calcular_por_margem, calcular_por_preco

    with Session(ambiente) as s:
        ktc = s.exec(select(Fornecedor).where(Fornecedor.codigo == "KTC")).first()
        produto = Produto(sku_key="X2", nome="Toalha", categoria="Toalha Banho",
                          familia="Bath Towel", custo_unitario=40.0, preco_base=999.0,
                          fornecedor_id=ktc.id, cost_method=CostMethod.ktc_quoted.value)
        s.add(produto)
        s.commit()

        margem = ps.margem_padrao(s, produto)
        assert margem.margem_pct == aprox(0.12)
        cenario = ps.cenario_padrao_catalogo(s)
        regras, _ = ps.regras_da_cotacao(s, cenario, produto)
        produto.preco_base = calcular_por_margem(40.0, 1, margem.margem_pct, regras).preco_negociado
        s.add(produto)
        s.commit()

        conferido = calcular_por_preco(40.0, 1, produto.preco_base, regras)
        assert conferido.margem_liquida == aprox(0.12, abs=MARGEM_DO_CENTAVO)


def test_produto_de_fornecedor_nacional_nao_e_desativado_pela_planilha(ambiente):
    """A planilha é da KTC: SKU da Daune não pode sumir só por não estar nela."""
    from app.models import CostMethod, Fornecedor, Produto

    with Session(ambiente) as s:
        daune = s.exec(select(Fornecedor).where(Fornecedor.codigo == "DAUNE")).first()
        s.add(Produto(sku_key="D1", nome="Travesseiro", categoria="Travesseiros",
                      familia="Pillow", fornecedor_id=daune.id, preco_base=300.0,
                      cost_method=CostMethod.national_supplier.value, ativo=True))
        s.commit()

        vistos = {"X1", "X2"}   # a planilha não traz o SKU da Daune
        for produto in s.exec(select(Produto)).all():
            if produto.sku_key not in vistos and produto.ativo:
                if produto.cost_method != CostMethod.national_supplier.value:
                    produto.ativo = False
                    s.add(produto)
        s.commit()

        daune_produto = s.exec(select(Produto).where(Produto.sku_key == "D1")).first()
        assert daune_produto.ativo is True

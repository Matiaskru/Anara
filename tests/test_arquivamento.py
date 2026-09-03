"""Arquivar e apagar: limpar teste sem perder histórico."""
import os
import tempfile
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture
def ambiente():
    import app.db as db
    import app.migrations as migrations
    import app.seeds as seeds

    fd, caminho = tempfile.mkstemp(suffix=".db", prefix="anara-arq-")
    os.close(fd)
    engine = create_engine(f"sqlite:///{caminho}", connect_args={"check_same_thread": False})
    originais = (db.engine, migrations.engine, seeds.engine)
    db.engine = migrations.engine = seeds.engine = engine
    SQLModel.metadata.create_all(engine)
    seeds.semear(verbose=False)
    yield engine
    db.engine, migrations.engine, seeds.engine = originais
    os.unlink(caminho)


def criar(s, **extra):
    from app.models import Cliente, Cotacao, CotacaoItem, StatusCotacao
    if not s.exec(select(Cliente)).first():
        s.add(Cliente(nome="Hotel Teste"))
        s.commit()
    dados = dict(numero=f"ANARA-2026-{len(s.exec(select(Cotacao)).all())+1:04d}", cliente_id=1)
    dados.update(extra)
    c = Cotacao(**dados)
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


def test_arquivar_tira_da_vista_sem_apagar(ambiente):
    from app.arquivamento import arquivar
    from app.models import Cotacao
    with Session(ambiente) as s:
        c = criar(s)
        arquivar(s, c.id, "teste")
        s.refresh(c)
        assert c.arquivada_em is not None and c.arquivada_motivo == "teste"
        assert s.get(Cotacao, c.id) is not None       # continua no banco


def test_restaurar_traz_de_volta(ambiente):
    from app.arquivamento import arquivar, restaurar
    with Session(ambiente) as s:
        c = criar(s)
        arquivar(s, c.id)
        restaurar(s, c.id)
        s.refresh(c)
        assert c.arquivada_em is None


def test_nao_apaga_sem_arquivar_antes(ambiente):
    from app.arquivamento import apagar
    from app.models import Cotacao
    with Session(ambiente) as s:
        c = criar(s)
        resultado = apagar(s, c.id)
        assert resultado["ok"] is False
        assert "Arquive antes" in resultado["erro"]
        assert s.get(Cotacao, c.id) is not None


def test_apagar_arquivada_remove_a_cotacao_e_os_itens(ambiente):
    from app.arquivamento import apagar, arquivar
    from app.models import Cotacao, CotacaoItem
    with Session(ambiente) as s:
        c = criar(s)
        s.add(CotacaoItem(cotacao_id=c.id, ordem=0, nome_produto="X", quantidade=1,
                          custo_unitario=10, preco_base=20, preco_negociado=20,
                          margem_liquida=0.1, faturamento=20, custo_total=10, lucro=2))
        s.commit()
        arquivar(s, c.id)
        resultado = apagar(s, c.id)
        assert resultado["ok"] is True and resultado["itens"] == 1
        assert s.get(Cotacao, c.id) is None
        assert s.exec(select(CotacaoItem).where(CotacaoItem.cotacao_id == c.id)).all() == []
        assert os.path.exists(resultado["backup"])     # cópia do banco antes de apagar


def test_apagar_avisa_quando_a_cotacao_parece_registro_de_verdade(ambiente):
    from app.arquivamento import motivos_para_pensar_duas_vezes
    from app.models import StatusCotacao
    with Session(ambiente) as s:
        c = criar(s, status=StatusCotacao.fechada, pdf_gerado_em=datetime.utcnow(),
                  aceite_em=datetime.utcnow(), aceite_responsavel="Fulano")
        avisos = motivos_para_pensar_duas_vezes(c, itens=3)
        assert any("fechada" in a for a in avisos)
        assert any("PDF" in a for a in avisos)
        assert any("aceite" in a for a in avisos)
        assert any("3 item" in a for a in avisos)


def test_lote_arquiva_varias(ambiente):
    from app.arquivamento import arquivar_em_lote
    with Session(ambiente) as s:
        ids = [criar(s).id for _ in range(4)]
        assert arquivar_em_lote(s, ids, "limpeza") == 4


def test_lote_apaga_so_o_que_esta_arquivado(ambiente):
    from app.arquivamento import apagar_em_lote, arquivar
    from app.models import Cotacao
    with Session(ambiente) as s:
        arquivada, viva = criar(s), criar(s)
        arquivar(s, arquivada.id)
        resultado = apagar_em_lote(s, [arquivada.id, viva.id])
        assert resultado["apagadas"] == 1
        assert resultado["recusadas"] == [viva.id]
        assert s.get(Cotacao, viva.id) is not None


def test_sugestao_de_teste_nao_pega_cotacao_emitida(ambiente):
    from app.arquivamento import candidatas_a_teste
    from app.models import StatusCotacao
    with Session(ambiente) as s:
        rascunho = criar(s)
        criar(s, status=StatusCotacao.enviada)
        criar(s, pdf_gerado_em=datetime.utcnow())
        sugeridas = {c["id"] for c in candidatas_a_teste(s)}
        assert rascunho.id in sugeridas and len(sugeridas) == 1


def test_sugestao_ignora_rascunho_que_tem_trabalho_dentro(ambiente):
    """Rascunho com itens é trabalho não enviado, não lixo de teste."""
    from app.arquivamento import candidatas_a_teste
    from app.models import CotacaoItem
    with Session(ambiente) as s:
        vazia, com_itens = criar(s), criar(s)
        s.add(CotacaoItem(cotacao_id=com_itens.id, ordem=0, nome_produto="Lençol", quantidade=50,
                          custo_unitario=50, preco_base=100, preco_negociado=100,
                          margem_liquida=0.16, faturamento=5000, custo_total=2500, lucro=800))
        s.commit()
        sugeridas = {c["id"] for c in candidatas_a_teste(s)}
        assert vazia.id in sugeridas and com_itens.id not in sugeridas


def test_cliente_arquivado_sai_da_lista_e_pode_levar_as_cotacoes(ambiente):
    from app.arquivamento import arquivar_cliente
    from app.models import Cliente, Cotacao
    with Session(ambiente) as s:
        c = criar(s)
        resultado = arquivar_cliente(s, 1, arquivar_junto=True)
        assert resultado["ok"] and resultado["cotacoes"] == 1
        assert s.get(Cliente, 1).ativo is False
        s.refresh(c)
        assert c.arquivada_em is not None

"""Controle de amostras — o saldo é a soma das movimentações, nunca um campo editável.

TEM AMOSTRA → ENVIOU → REGISTROU PRA QUEM / PRA QUÊ → VOLTOU OU NÃO VOLTOU. Só isso.

Cada movimentação mexe em dois números, e o efeito de cada tipo mora em `EFEITO`, um lugar só:

    tipo         disponível   em circulação
    ENTRADA          +q             0
    SAIDA            −q            +q
    RETORNO          +q            −q        retornou normalmente
    CONSUMIDA         0            −q        ficou com o cliente / não retorna
    DANIFICADA        0            −q
    PERDIDA           0            −q
    OUTRO             0            −q        saiu de circulação por outro motivo (com observação)
    AJUSTE           ±q             0        só OWNER/ADMIN, com motivo (contagem física)

Ninguém apaga nem edita movimentação: corrigir é registrar outra. Por isso a pergunta "por
que o saldo é este?" sempre tem resposta — é o histórico.
"""
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import AmostraMovimentacao, AmostraProduto, Cliente, Produto, Usuario

ENTRADA, SAIDA, RETORNO, AJUSTE = "ENTRADA", "SAIDA", "RETORNO", "AJUSTE"
CONSUMIDA, DANIFICADA, PERDIDA, OUTRO = "CONSUMIDA", "DANIFICADA", "PERDIDA", "OUTRO"

#: tipo → (efeito no disponível, efeito em circulação), multiplicado pela quantidade
EFEITO = {
    ENTRADA: (1, 0), SAIDA: (-1, 1), RETORNO: (1, -1),
    CONSUMIDA: (0, -1), DANIFICADA: (0, -1), PERDIDA: (0, -1), OUTRO: (0, -1),
    AJUSTE: (1, 0),
}
#: o que tira peça de circulação (retorno e baixas) — fecha o que estava com alguém
SAEM_DE_CIRCULACAO = (RETORNO, CONSUMIDA, DANIFICADA, PERDIDA, OUTRO)

ROTULO_TIPO = {
    ENTRADA: "Entrada", SAIDA: "Saída", RETORNO: "Retorno", CONSUMIDA: "Consumida",
    DANIFICADA: "Danificada", PERDIDA: "Perdida", OUTRO: "Outro", AJUSTE: "Ajuste",
}
MOTIVOS_SAIDA = ["Enviado para avaliação", "Apresentação / reunião", "Showroom",
                 "Feira / evento", "Uso interno", "Outro"]
#: condição do retorno → tipo da movimentação
CONDICOES_RETORNO = [
    (RETORNO, "Retornou normalmente"),
    (DANIFICADA, "Danificada"),
    (CONSUMIDA, "Consumida / não retorna"),
    (PERDIDA, "Perdida"),
    (OUTRO, "Outro / não retorna"),
]


class AmostraInvalida(HTTPException):
    """Recusa legível (400/409). O handler global vira página para clique e JSON para fetch."""

    def __init__(self, detalhe: str, status: int = 400):
        super().__init__(status_code=status, detail=detalhe)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def saldo_de(movimentos) -> dict:
    disponivel = circulacao = 0
    for m in movimentos:
        d, c = EFEITO[m.tipo]
        disponivel += d * m.quantidade
        circulacao += c * m.quantidade
    return {"disponivel": disponivel, "circulacao": circulacao, "total": disponivel + circulacao}


def movimentos(session: Session, amostra_id: int) -> list:
    return list(session.exec(
        select(AmostraMovimentacao)
        .where(AmostraMovimentacao.amostra_produto_id == amostra_id)
        .order_by(AmostraMovimentacao.data, AmostraMovimentacao.criado_em, AmostraMovimentacao.id)
    ).all())


def saldo(session: Session, amostra_id: int) -> dict:
    return saldo_de(movimentos(session, amostra_id))


def _chave_destino(cliente_id, cliente_texto) -> str:
    """Quem está com a peça: o cliente do CRM, ou o texto livre (sem diferença de caixa)."""
    if cliente_id:
        return f"c{cliente_id}"
    texto = " ".join((cliente_texto or "").split()).lower()
    return f"t:{texto}" if texto else ""


def _nomes_de_clientes(session: Session, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {c.id: c.nome for c in session.exec(select(Cliente).where(Cliente.id.in_(ids))).all()}


def rotulo_destino(m, nomes: dict) -> str:
    if m.cliente_id:
        return nomes.get(m.cliente_id) or f"Cliente #{m.cliente_id}"
    return (m.cliente_texto or "").strip()


def fora(session: Session, amostra_id: int, movs=None) -> list:
    """O que ainda está fora, por destinatário: saídas menos o que voltou ou saiu de circulação."""
    movs = movs if movs is not None else movimentos(session, amostra_id)
    nomes = _nomes_de_clientes(session, [m.cliente_id for m in movs])
    abertos = {}
    for m in movs:
        if m.tipo != SAIDA and m.tipo not in SAEM_DE_CIRCULACAO:
            continue
        chave = _chave_destino(m.cliente_id, m.cliente_texto)
        linha = abertos.setdefault(chave, {"chave": chave, "rotulo": rotulo_destino(m, nomes) or "—",
                                           "quantidade": 0, "desde": None})
        if m.tipo == SAIDA:
            if linha["quantidade"] <= 0:
                linha["desde"] = m.data
            linha["quantidade"] += m.quantidade
        else:
            linha["quantidade"] -= m.quantidade
    return [l for l in abertos.values() if l["quantidade"] > 0]


def historico(session: Session, amostra_id: int, movs=None) -> list:
    """Cronológico. Quantidade com o sinal do que aconteceu com o disponível (saída −2,
    retorno +1); baixa sem sinal, porque não mexe no disponível — sai de circulação."""
    movs = movs if movs is not None else movimentos(session, amostra_id)
    nomes = _nomes_de_clientes(session, [m.cliente_id for m in movs])
    condicao = dict(CONDICOES_RETORNO)
    linhas = []
    for m in movs:
        if m.tipo in (ENTRADA, RETORNO):
            qtd = f"+{m.quantidade}"
        elif m.tipo == SAIDA:
            qtd = f"−{m.quantidade}"
        elif m.tipo == AJUSTE:
            qtd = f"+{m.quantidade}" if m.quantidade > 0 else f"−{abs(m.quantidade)}"
        else:
            qtd = str(m.quantidade)
        destino = rotulo_destino(m, nomes)
        uso = " · ".join(x for x in (destino, m.motivo if m.tipo == SAIDA else None) if x)
        if m.tipo in SAEM_DE_CIRCULACAO:
            obs = " · ".join(x for x in (condicao.get(m.tipo), m.observacao) if x)
        elif m.tipo == AJUSTE:
            obs = " · ".join(x for x in (m.motivo, m.observacao) if x)
        else:
            obs = m.observacao or ""
        linhas.append({"data": m.data, "usuario": m.usuario_nome or "—",
                       "tipo": ROTULO_TIPO.get(m.tipo, m.tipo), "codigo_tipo": m.tipo,
                       "quantidade": qtd, "uso": uso or "—", "observacao": obs or "—"})
    return linhas


def identificacao(amostra: AmostraProduto, produto: Optional[Produto]) -> tuple:
    """(nome, especificação) que a tela mostra: os do produto do catálogo, ou os da avulsa."""
    if produto is not None:
        return produto.nome, produto.especificacao or ""
    if amostra.produto_id:
        return f"Produto #{amostra.produto_id}", ""
    return amostra.nome or "Amostra avulsa", amostra.especificacao or ""


def painel(session: Session) -> dict:
    """A lista da aba Amostras: um linha por produto, com saldo e a última movimentação."""
    amostras = session.exec(select(AmostraProduto).where(AmostraProduto.ativo == True)).all()  # noqa: E712
    if not amostras:
        return {"linhas": [], "disponiveis": 0, "em_circulacao": 0, "produtos": 0}
    ids = [a.id for a in amostras]
    todos = session.exec(select(AmostraMovimentacao)
                         .where(AmostraMovimentacao.amostra_produto_id.in_(ids))
                         .order_by(AmostraMovimentacao.data, AmostraMovimentacao.criado_em,
                                   AmostraMovimentacao.id)).all()
    por_amostra = defaultdict(list)
    for m in todos:
        por_amostra[m.amostra_produto_id].append(m)
    produtos = {p.id: p for p in session.exec(
        select(Produto).where(Produto.id.in_([a.produto_id for a in amostras if a.produto_id]))).all()}
    nomes = _nomes_de_clientes(session, [m.cliente_id for m in todos])
    linhas = []
    for a in amostras:
        movs = por_amostra.get(a.id, [])
        s = saldo_de(movs)
        ultima = max(movs, key=lambda m: (m.criado_em, m.id)) if movs else None
        p = produtos.get(a.produto_id) if a.produto_id else None
        nome, especificacao = identificacao(a, p)
        linhas.append({
            "id": a.id, "produto": p, "nome": nome,
            "sku": p.sku_key if p else "", "especificacao": especificacao,
            **s, "ultima": ultima,
            "ultima_rotulo": (rotulo_destino(ultima, nomes) or ROTULO_TIPO.get(ultima.tipo, ""))
            if ultima else "",
        })
    linhas.sort(key=lambda l: (l["nome"] or "").lower())
    return {"linhas": linhas, "produtos": len(linhas),
            "disponiveis": sum(l["disponivel"] for l in linhas),
            "em_circulacao": sum(l["circulacao"] for l in linhas)}


# ---------------------------------------------------------------------------
# Escrita — todas passam por aqui
# ---------------------------------------------------------------------------
def _quantidade(valor, *, permite_zero=False) -> int:
    try:
        q = int(str(valor).strip())
    except (TypeError, ValueError):
        raise AmostraInvalida("Quantidade precisa ser um número inteiro.")
    if q < 0 or (q == 0 and not permite_zero):
        raise AmostraInvalida("Quantidade precisa ser maior que zero.")
    return q


def _data(valor) -> date:
    if isinstance(valor, date):
        return valor
    if not valor:
        return date.today()
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        raise AmostraInvalida("Data inválida.")


def _travar(session: Session, amostra_id: int) -> AmostraProduto:
    """Lê a amostra com trava de linha (no PostgreSQL), para duas saídas simultâneas não
    passarem as duas pela conferência do disponível. No SQLite a escrita já é serial."""
    amostra = session.exec(select(AmostraProduto).where(AmostraProduto.id == amostra_id)
                           .with_for_update()).first()
    if amostra is None or not amostra.ativo:
        raise AmostraInvalida("Amostra não encontrada.", status=404)
    return amostra


def _gravar(session: Session, amostra: AmostraProduto, tipo: str, quantidade: int, data_mov: date,
            ator: Usuario, **campos) -> AmostraMovimentacao:
    m = AmostraMovimentacao(amostra_produto_id=amostra.id, tipo=tipo, quantidade=quantidade,
                            data=data_mov, usuario_id=getattr(ator, "id", None),
                            usuario_nome=getattr(ator, "nome", None), criado_em=datetime.utcnow(),
                            **campos)
    session.add(m)
    session.flush()
    return m


def _texto(v) -> Optional[str]:
    v = " ".join((v or "").split())
    return v or None


def adicionar_produto(session: Session, *, produto_id, quantidade_inicial, observacao,
                      ator: Usuario, data_mov=None) -> AmostraProduto:
    try:
        produto = session.get(Produto, int(produto_id))
    except (TypeError, ValueError):
        produto = None
    if produto is None or not produto.ativo:
        raise AmostraInvalida("Produto não encontrado no catálogo.", status=404)
    if session.exec(select(AmostraProduto).where(AmostraProduto.produto_id == produto.id)).first():
        raise AmostraInvalida(f"{produto.nome} já está no controle de amostras.", status=409)
    qtd = _quantidade(quantidade_inicial, permite_zero=True)
    amostra = AmostraProduto(produto_id=produto.id, observacao=_texto(observacao),
                             criado_por_id=getattr(ator, "id", None))
    session.add(amostra)
    session.flush()
    if qtd:
        _gravar(session, amostra, ENTRADA, qtd, _data(data_mov), ator,
                observacao=_texto(observacao) or "Quantidade inicial")
    return amostra


def adicionar_avulsa(session: Session, *, nome, especificacao, quantidade_inicial, observacao,
                     ator: Usuario, data_mov=None) -> AmostraProduto:
    """Amostra de peça que NÃO existe no catálogo (ex.: travesseiro que a Anara ainda não vende).

    Não cria produto nem preço — só o controle da amostra, com nome e especificação próprios.
    A mesma peça (nome + especificação) não entra duas vezes.
    """
    nome, especificacao = _texto(nome), _texto(especificacao)
    if nome is None:
        raise AmostraInvalida("Informe o nome da amostra.")
    chave = (nome.lower(), (especificacao or "").lower())
    for a in session.exec(select(AmostraProduto).where(AmostraProduto.produto_id == None)).all():  # noqa: E711
        if ((a.nome or "").lower(), (a.especificacao or "").lower()) == chave:
            raise AmostraInvalida(f"{nome} já está no controle de amostras.", status=409)
    qtd = _quantidade(quantidade_inicial, permite_zero=True)
    amostra = AmostraProduto(produto_id=None, nome=nome, especificacao=especificacao,
                             observacao=_texto(observacao), criado_por_id=getattr(ator, "id", None))
    session.add(amostra)
    session.flush()
    if qtd:
        _gravar(session, amostra, ENTRADA, qtd, _data(data_mov), ator,
                observacao=_texto(observacao) or "Quantidade inicial")
    return amostra


def registrar_entrada(session: Session, amostra_id: int, *, quantidade, observacao, data_mov,
                      ator: Usuario) -> AmostraMovimentacao:
    amostra = _travar(session, amostra_id)
    return _gravar(session, amostra, ENTRADA, _quantidade(quantidade), _data(data_mov), ator,
                   observacao=_texto(observacao))


def registrar_saida(session: Session, amostra_id: int, *, quantidade, cliente_id, cliente_texto,
                    motivo, observacao, data_mov, ator: Usuario) -> AmostraMovimentacao:
    amostra = _travar(session, amostra_id)
    qtd = _quantidade(quantidade)
    if motivo not in MOTIVOS_SAIDA:
        raise AmostraInvalida("Escolha o motivo do envio/uso.")
    cid = None
    if cliente_id not in (None, "", "0"):
        try:
            cliente = session.get(Cliente, int(cliente_id))
        except (TypeError, ValueError):
            cliente = None
        if cliente is None:
            raise AmostraInvalida("Cliente não encontrado.", status=404)
        cid = cliente.id
    texto = _texto(cliente_texto)
    if cid is None and texto is None:
        raise AmostraInvalida("Informe para quem foi: escolha o cliente ou escreva o nome/uso.")
    disponivel = saldo(session, amostra.id)["disponivel"]
    if qtd > disponivel:
        raise AmostraInvalida(f"Só há {disponivel} disponível(is) — não dá para registrar a "
                              f"saída de {qtd}.", status=409)
    return _gravar(session, amostra, SAIDA, qtd, _data(data_mov), ator, cliente_id=cid,
                   cliente_texto=None if cid else texto, motivo=motivo,
                   observacao=_texto(observacao))


def registrar_retorno(session: Session, amostra_id: int, *, quantidade, condicao, destino,
                      observacao, data_mov, ator: Usuario) -> AmostraMovimentacao:
    """Retorno (volta ao disponível) ou baixa (consumida, danificada, perdida, outro).

    `destino` é a chave de quem está com a peça (de `fora`): o retorno sai da conta daquele
    cliente/uso, e não pode devolver mais do que está com ele.
    """
    amostra = _travar(session, amostra_id)
    qtd = _quantidade(quantidade)
    if condicao not in dict(CONDICOES_RETORNO):
        raise AmostraInvalida("Escolha a condição do retorno.")
    if condicao == OUTRO and not _texto(observacao):
        raise AmostraInvalida("Na condição 'Outro / não retorna', descreva o que aconteceu na observação.")
    movs = movimentos(session, amostra.id)
    com_quem = {l["chave"]: l for l in fora(session, amostra.id, movs)}
    if destino not in com_quem:
        raise AmostraInvalida("Escolha com quem estava a amostra.")
    if qtd > com_quem[destino]["quantidade"]:
        raise AmostraInvalida(f"Com {com_quem[destino]['rotulo']} há {com_quem[destino]['quantidade']} "
                              f"em circulação — não dá para registrar {qtd}.", status=409)
    # o destinatário é o da saída: mesmo cliente do CRM, ou o mesmo texto
    saida = next(m for m in reversed(movs)
                 if m.tipo == SAIDA and _chave_destino(m.cliente_id, m.cliente_texto) == destino)
    return _gravar(session, amostra, condicao, qtd, _data(data_mov), ator,
                   cliente_id=saida.cliente_id, cliente_texto=saida.cliente_texto,
                   observacao=_texto(observacao))


def ajustar(session: Session, amostra_id: int, *, disponivel_contado, motivo, data_mov,
            ator: Usuario) -> AmostraMovimentacao:
    """Contagem física ≠ sistema. Não sobrescreve nada: grava a DIFERENÇA, com motivo."""
    amostra = _travar(session, amostra_id)
    if not _texto(motivo):
        raise AmostraInvalida("O ajuste exige motivo.")
    contado = _quantidade(disponivel_contado, permite_zero=True)
    atual = saldo(session, amostra.id)["disponivel"]
    diferenca = contado - atual
    if diferenca == 0:
        raise AmostraInvalida(f"A contagem ({contado}) já é o disponível do sistema — nada a ajustar.")
    return _gravar(session, amostra, AJUSTE, diferenca, _data(data_mov), ator,
                   motivo=_texto(motivo), observacao=f"Disponível {atual} → {contado} (contagem)")

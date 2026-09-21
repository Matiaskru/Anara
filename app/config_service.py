"""Leitura das premissas versionadas — o único lugar que sabe onde cada número mora.

Nada de alíquota, câmbio, frete, material, CMT ou margem escrito à mão em fórmula ou tela:
tudo vem daqui, com vigência (`valid_from`/`valid_to`) e fonte. Ao mudar uma premissa, a
versão antiga é fechada e uma nova é aberta — histórico nunca é sobrescrito.
"""
import json
from datetime import date
from typing import List, Optional

from sqlmodel import Session, select

from app.models import (
    CmtPreco, CondicaoPagamento, MaterialPreco, ParametroKTC, Premissa, ToalhaPreco,
)

# defaults de última instância — só valem se a premissa ainda não foi cadastrada
FALLBACKS = {
    "fx_usd_brl": 5.11,
    "frete_int_usd_kg": 0.516,
    "outras_desp_usd_un": 0.2487532709,
    # NOMINAL. O efetivo da venda é derivado por item — `pricing_engine.pis_cofins_efetivo()`.
    "pis_cofins_nominal_pct": 0.0925,
    # LEGADO: efetivo fixo da metodologia anterior, mantido só para ler cotação antiga. Nenhum
    # caminho de precificação nova lê esta chave desde 09/09/2026.
    "pis_cofins_pct": 0.0759,
    "validade_dias": 5,
    "icms_fallback_pct": 0.18,
}

TABELA_COMISSAO_PADRAO = [(0.0, 0.05), (0.6, 0.06), (0.7, 0.07), (0.8, 0.08), (0.9, 0.09), (1.0, 0.10)]


def _vigente(query, ref: Optional[date] = None):
    ref = ref or date.today()
    return [r for r in query
            if r.ativo and (r.valid_from is None or r.valid_from <= ref)
            and (r.valid_to is None or r.valid_to >= ref)]


# ---------------------------------------------------------------------------
# Premissas simples (chave → número/texto)
# ---------------------------------------------------------------------------
def premissa(session: Session, chave: str, ref: Optional[date] = None):
    linhas = _vigente(session.exec(select(Premissa).where(Premissa.chave == chave)).all(), ref)
    if not linhas:
        return None
    linhas.sort(key=lambda p: (p.valid_from or date.min, p.id or 0))
    return linhas[-1]


def num(session: Session, chave: str, padrao: Optional[float] = None,
        ref: Optional[date] = None) -> Optional[float]:
    p = premissa(session, chave, ref)
    if p is not None and p.valor_num is not None:
        return p.valor_num
    return padrao if padrao is not None else FALLBACKS.get(chave)


def txt(session: Session, chave: str, padrao: Optional[str] = None,
        ref: Optional[date] = None) -> Optional[str]:
    p = premissa(session, chave, ref)
    if p is not None and p.valor_txt:
        return p.valor_txt
    return padrao


def definir(session: Session, chave: str, valor_num: Optional[float] = None,
            valor_txt: Optional[str] = None, fonte: Optional[str] = None,
            notas: Optional[str] = None, descricao: Optional[str] = None,
            unidade: Optional[str] = None) -> Premissa:
    """Fecha a versão vigente e abre uma nova. Nunca apaga a anterior."""
    hoje = date.today()
    atual = premissa(session, chave)
    if atual is not None:
        if atual.valor_num == valor_num and atual.valor_txt == valor_txt:
            return atual
        atual.valid_to = hoje
        atual.ativo = False
        session.add(atual)
    nova = Premissa(chave=chave, valor_num=valor_num, valor_txt=valor_txt, fonte=fonte,
                    notas=notas, descricao=descricao or (atual.descricao if atual else None),
                    unidade=unidade or (atual.unidade if atual else None), valid_from=hoje)
    session.add(nova)
    session.commit()
    session.refresh(nova)
    return nova


# ---------------------------------------------------------------------------
# Tabela de comissão
# ---------------------------------------------------------------------------
def tabela_comissao(session: Session, ref: Optional[date] = None) -> List[tuple]:
    bruto = txt(session, "comissao_tabela", ref=ref)
    if bruto:
        try:
            return [tuple(x) for x in json.loads(bruto)]
        except (ValueError, TypeError):
            pass
    return list(TABELA_COMISSAO_PADRAO)


# ---------------------------------------------------------------------------
# Condições de pagamento (encargo financeiro centralizado)
# ---------------------------------------------------------------------------
def condicoes_pagamento(session: Session, apenas_ativas: bool = True) -> List[CondicaoPagamento]:
    linhas = session.exec(select(CondicaoPagamento).order_by(CondicaoPagamento.ordem)).all()
    return [c for c in linhas if c.ativo] if apenas_ativas else list(linhas)


def condicao_pagamento(session: Session, codigo: str) -> Optional[CondicaoPagamento]:
    return session.exec(select(CondicaoPagamento)
                        .where(CondicaoPagamento.codigo == (codigo or ""))).first()


def condicao_textual(session: Session, cotacao) -> str:
    """Condição de pagamento por extenso, com o sinal quando houver (21/09/2026).

    "30 dias" · "30% de sinal + 70% em 30/60/90 dias" · "100% à vista (sinal)". É o texto
    que vai ao PDF, ao snapshot e à tela — nunca o encargo nem a fórmula.
    """
    from app.payment_terms import rotulo_condicao
    codigo = getattr(cotacao, "condicao_pagamento", None) or ""
    condicao = condicao_pagamento(session, codigo)
    label = getattr(condicao, "label", None) or codigo
    return rotulo_condicao(getattr(cotacao, "percentual_sinal", 0) or 0, label)


# ---------------------------------------------------------------------------
# Motor industrial KTC — materiais, CMT, fios de toalha, parâmetros
# ---------------------------------------------------------------------------
def material_preco(session: Session, material: str, plain_or_stripe: str = "plain",
                   ref: Optional[date] = None) -> Optional[MaterialPreco]:
    linhas = _vigente(session.exec(select(MaterialPreco)).all(), ref)
    alvo = (material or "").strip().lower()
    listra = (plain_or_stripe or "plain").strip().lower()
    exatos = [m for m in linhas if m.material.strip().lower() == alvo
              and (m.plain_or_stripe or "plain").lower() == listra]
    if exatos:
        return sorted(exatos, key=lambda m: m.valid_from or date.min)[-1]
    return None


def materiais(session: Session, ref: Optional[date] = None) -> List[MaterialPreco]:
    return sorted(_vigente(session.exec(select(MaterialPreco)).all(), ref),
                  key=lambda m: (m.material, m.plain_or_stripe))


def cmt_preco(session: Session, familia: str, construcao: Optional[str] = None,
              ref: Optional[date] = None) -> Optional[CmtPreco]:
    linhas = _vigente(session.exec(select(CmtPreco)).all(), ref)
    alvo = (familia or "").strip().lower()
    candidatos = [c for c in linhas if c.familia.strip().lower() == alvo]
    if construcao:
        com_construcao = [c for c in candidatos
                          if (c.construcao or "").strip().lower() == construcao.strip().lower()]
        if com_construcao:
            return com_construcao[-1]
    genericos = [c for c in candidatos if not c.construcao]
    return genericos[-1] if genericos else (candidatos[-1] if candidatos else None)


def toalha_preco(session: Session, subcategoria: Optional[str] = None,
                 composicao: Optional[str] = None, gsm: Optional[int] = None,
                 yarn_type: Optional[str] = None, plain_or_stripe: str = "plain",
                 ref: Optional[date] = None) -> Optional[ToalhaPreco]:
    """Match rigoroso: só devolve preço quando a combinação está cadastrada.

    Sem cadastro não se inventa preço — o produto vai para REVIEW_REQUIRED ou continua
    pelo último preço KTC cotado.
    """
    linhas = _vigente(session.exec(select(ToalhaPreco)).all(), ref)

    def bate(t):
        if subcategoria and (t.subcategoria or "").strip().lower() != subcategoria.strip().lower():
            return False
        if composicao and (t.composicao or "").strip().lower() != composicao.strip().lower():
            return False
        if gsm is not None and t.gsm is not None and int(t.gsm) != int(gsm):
            return False
        if yarn_type and (t.yarn_type or "").strip().lower() != yarn_type.strip().lower():
            return False
        if (t.plain_or_stripe or "plain").lower() != (plain_or_stripe or "plain").lower():
            return False
        return True

    candidatos = [t for t in linhas if bate(t)]
    if not candidatos:
        return None
    candidatos.sort(key=lambda t: (t.subcategoria is not None, t.gsm is not None,
                                   t.valid_from or date.min))
    return candidatos[-1]


def parametro_ktc(session: Session, chave: str, escopo: Optional[str] = None,
                  padrao: Optional[float] = None, ref: Optional[date] = None) -> Optional[float]:
    """Parâmetro do motor industrial. Escopo mais específico ganha; cai pro global se não houver."""
    linhas = _vigente(session.exec(select(ParametroKTC).where(ParametroKTC.chave == chave)).all(), ref)
    if escopo:
        especificos = [p for p in linhas if (p.escopo or "").strip().lower() == escopo.strip().lower()]
        if especificos:
            return sorted(especificos, key=lambda p: p.valid_from or date.min)[-1].valor
    globais = [p for p in linhas if not p.escopo]
    if globais:
        return sorted(globais, key=lambda p: p.valid_from or date.min)[-1].valor
    return padrao

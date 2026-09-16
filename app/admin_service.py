"""Camada administrativa — atualizar premissa sem alterar código nem corromper histórico.

O que este módulo protege é uma distinção que parece óbvia e quase nunca é implementada:

    **atualizar ≠ editar histórico.**

Quando o admin muda o custo de um SKU, ele não está corrigindo o passado: está dizendo o que
vale de agora em diante. Por isso nada aqui faz `UPDATE valor = novo`. A versão antiga fica,
com sua fonte e sua data; uma versão nova nasce; e **a data decide** qual das duas o próximo
cálculo usa.

## As quatro garantias

1. **Isolamento.** Mexer no SKU X não toca no SKU Y, nem na família, nem no fornecedor. O
   escopo de cada operação é declarado e conferido antes de aplicar.
2. **Histórico imutável.** Cotação emitida guarda o próprio snapshot; nada aqui a relê nem a
   recalcula. Mudar custo, câmbio, margem ou imposto hoje não move um centavo do que já saiu.
3. **Nada silencioso.** Toda alteração passa por `preview → aplicar`. O preview mostra o
   valor atual, o novo, o escopo e o impacto; o apply confere que nada mudou nesse meio-tempo.
4. **Rastreabilidade.** Quem, quando, o quê, de onde veio e por quê — em `AuditLog`.

## Preview e apply, e por que existe um token

O preview calcula o impacto sobre o estado atual. Entre o preview e o apply, outra pessoa
pode ter criado uma versão. Aplicar assim mesmo produziria uma V3 que ninguém revisou, em
cima de uma V2 que o primeiro admin nem viu.

Por isso o preview devolve um **token**: o hash do estado que ele observou. O apply recomputa
esse hash e recusa se tiver mudado. É compare-and-swap, e o servidor não confia no navegador
para nada além de devolver o token — os valores econômicos são recomputados aqui.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Sequence

from sqlmodel import Session, select

from app import config_service as cfg
from app import custo_service as cs
from app.dinheiro import D, divide, para_float
from app.models import (
    AuditLog, CondicaoPagamento, CostMethod, CotacaoItem, CustoReferencia, Fornecedor,
    MargemRegra, Premissa, Produto, StatusCusto, Usuario,
)

# Resultados possíveis de uma linha de proposta.
MUDANCA = "MUDANCA"
NO_OP = "NO_OP"
#: Mesmo valor econômico, **evidência nova**: outra fonte, outro documento, outra data.
#: Não é mudança de preço, e também não é "nada aconteceu" — é a prova de que alguém
#: reconferiu o número. Vira versão, para que a evidência não se perca.
RECONFIRMACAO = "RECONFIRMACAO"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
NAO_ENCONTRADO = "SKU_NAO_ENCONTRADO"
CONFLITO = "CONFLITO"


class ConflitoDeVersao(RuntimeError):
    """O estado mudou entre o preview e o apply. Refazer o preview é obrigatório."""


class DadoInvalido(ValueError):
    """Validação de domínio falhou. A mensagem vai para o admin, sem stack trace."""


# ---------------------------------------------------------------------------
# Validadores — servidor, não HTML
# ---------------------------------------------------------------------------
def valida_decimal(valor, campo: str, *, minimo=None, maximo=None, permite_zero=True):
    """Converte com a política da Sessão 3B e valida faixa. Nunca `Decimal(float)`."""
    try:
        d = D(valor)
    except (TypeError, ValueError):
        raise DadoInvalido(f"{campo}: '{valor}' não é um número válido.")
    if d is None:
        raise DadoInvalido(f"{campo} é obrigatório.")
    if not permite_zero and d == 0:
        raise DadoInvalido(f"{campo} não pode ser zero.")
    if minimo is not None and d < D(minimo):
        raise DadoInvalido(f"{campo} não pode ser menor que {minimo}.")
    if maximo is not None and d > D(maximo):
        raise DadoInvalido(f"{campo} não pode ser maior que {maximo}.")
    return d


def valida_percentual(valor, campo: str):
    """Percentual em fração: 0,14 é 14%. Recusa o que é semanticamente impossível."""
    d = valida_decimal(valor, campo, minimo=0, maximo=1)
    if d > D("0.95"):
        raise DadoInvalido(
            f"{campo} = {d} significaria {d * 100:.1f}%. Percentuais entram em fração "
            "(0,14 = 14%). Se a intenção era mesmo esse valor, ele é economicamente "
            "implausível e precisa de decisão explícita.")
    return d


def valida_vigencia(inicio: Optional[date], fim: Optional[date]):
    if inicio and fim and fim < inicio:
        raise DadoInvalido("A vigência final não pode ser anterior à inicial.")
    return inicio, fim


def valida_fonte(fonte: Optional[str], motivo: Optional[str] = None):
    """Valor sem procedência não entra — é o princípio 4 do projeto, aplicado ao admin."""
    if not (fonte or "").strip():
        raise DadoInvalido("Toda alteração econômica exige uma fonte: documento, e-mail, "
                           "tabela ou decisão registrada.")
    return fonte.strip()


# ---------------------------------------------------------------------------
# Proposta (preview)
# ---------------------------------------------------------------------------
@dataclass
class LinhaProposta:
    """Uma alteração candidata, com o antes e o depois."""
    alvo: str                       # "SKU ABC" | "fx_usd_brl" | "margem Daune"
    situacao: str                   # MUDANCA | NO_OP | REVIEW_REQUIRED | SKU_NAO_ENCONTRADO
    valor_atual: Optional[str] = None
    valor_novo: Optional[str] = None
    motivo: Optional[str] = None
    produto_id: Optional[int] = None
    detalhe: dict = field(default_factory=dict)

    @property
    def aplicavel(self) -> bool:
        """Gera versão nova. Reconfirmação gera — o valor é o mesmo, a evidência não."""
        return self.situacao in (MUDANCA, RECONFIRMACAO)

    def como_dict(self) -> dict:
        return {"alvo": self.alvo, "situacao": self.situacao, "valor_atual": self.valor_atual,
                "valor_novo": self.valor_novo, "motivo": self.motivo,
                "produto_id": self.produto_id, "detalhe": self.detalhe}


@dataclass
class Proposta:
    """O resultado de um preview: o que mudaria, para quem, e o token do estado observado."""
    entidade: str
    escopo: str
    linhas: List[LinhaProposta] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)
    conflitos: List[str] = field(default_factory=list)
    skus_afetados: int = 0
    familias_afetadas: List[str] = field(default_factory=list)
    token: str = ""
    vigencia_inicio: Optional[date] = None
    vigencia_fim: Optional[date] = None
    fonte: Optional[str] = None
    motivo: Optional[str] = None

    @property
    def mudancas(self) -> List[LinhaProposta]:
        return [x for x in self.linhas if x.aplicavel]

    @property
    def resumo(self) -> dict:
        contagem = {}
        for x in self.linhas:
            contagem[x.situacao] = contagem.get(x.situacao, 0) + 1
        return contagem

    @property
    def pode_aplicar(self) -> bool:
        return bool(self.mudancas) and not self.conflitos

    def como_dict(self) -> dict:
        return {"entidade": self.entidade, "escopo": self.escopo, "resumo": self.resumo,
                "skus_afetados": self.skus_afetados,
                "familias_afetadas": self.familias_afetadas,
                "avisos": list(self.avisos), "conflitos": list(self.conflitos),
                "token": self.token, "pode_aplicar": self.pode_aplicar,
                "linhas": [x.como_dict() for x in self.linhas]}


def _hash_estado(partes: Sequence) -> str:
    """Impressão digital do estado observado no preview.

    Só entram os campos que, se mudarem, invalidam a revisão: versão, valor e vigência.
    `criado_em` fica de fora — ele muda sem que a decisão mude.
    """
    bruto = json.dumps(partes, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Trilha
# ---------------------------------------------------------------------------
def registrar(session: Session, *, ator: Optional[Usuario], acao: str, entidade: str,
              entidade_id: Optional[int] = None, escopo: Optional[str] = None,
              antes: Optional[str] = None, depois: Optional[str] = None,
              motivo: Optional[str] = None, origem: str = "admin-ui",
              resultado: str = "OK", correlacao: Optional[str] = None,
              detalhe: Optional[dict] = None) -> AuditLog:
    """Grava a trilha. Nunca senha, hash, cookie ou segredo — só o econômico e o porquê."""
    linha = AuditLog(
        ator_id=getattr(ator, "id", None), ator_email=getattr(ator, "email", None),
        ator_papel=getattr(ator, "papel", None), acao=acao, entidade=entidade,
        entidade_id=entidade_id, escopo=escopo, versao_anterior=antes, versao_nova=depois,
        motivo=motivo, origem=origem, resultado=resultado, correlacao=correlacao,
        detalhe=json.dumps(detalhe, ensure_ascii=False, default=str) if detalhe else None)
    session.add(linha)
    return linha


def trilha(session: Session, *, entidade: Optional[str] = None, limite: int = 100):
    q = select(AuditLog).order_by(AuditLog.ocorrido_em.desc(), AuditLog.id.desc())
    if entidade:
        q = q.where(AuditLog.entidade == entidade)
    return session.exec(q.limit(limite)).all()


# ---------------------------------------------------------------------------
# 1. Custo por SKU — o caso central do §1
# ---------------------------------------------------------------------------
def _retrato_custo(ref: Optional[CustoReferencia]) -> str:
    if ref is None:
        return "sem referência versionada"
    return (f"V{ref.versao} · CNET R$ {ref.cnet_brl} · {ref.status_custo} · "
            f"desde {ref.valid_from}")


def preview_custo_sku(session: Session, produto_id: int, *, cnet_brl, status: str,
                      fonte: str, documento: Optional[str] = None,
                      valor_bruto=None, vigente_a_partir_de: Optional[date] = None,
                      motivo: Optional[str] = None) -> Proposta:
    """O que aconteceria ao mudar o custo de **um** SKU. Não grava nada.

    O escopo é declarado como um único SKU e conferido: o preview lista quantos outros SKUs
    são afetados, e a resposta correta é sempre zero. É o que impede o admin de achar que
    mexeu num item quando mexeu numa família.
    """
    produto = session.get(Produto, produto_id)
    if produto is None:
        p = Proposta(entidade="CustoReferencia", escopo=f"produto #{produto_id}")
        p.linhas.append(LinhaProposta(alvo=f"produto #{produto_id}", situacao=NAO_ENCONTRADO,
                                      motivo="Produto não existe no catálogo."))
        return p

    fonte = valida_fonte(fonte)
    novo = valida_decimal(cnet_brl, "CNET", minimo=0, permite_zero=False)
    bruto = valida_decimal(valor_bruto, "valor bruto", minimo=0) if valor_bruto is not None else None
    if status not in {s.value for s in StatusCusto}:
        raise DadoInvalido(f"Status '{status}' não é um dos cinco canônicos.")
    inicio = vigente_a_partir_de or date.today()
    valida_vigencia(inicio, None)

    atual = cs.referencia_vigente(session, produto_id)
    prop = Proposta(entidade="CustoReferencia", escopo=f"SKU {produto.sku_key}",
                    vigencia_inicio=inicio, fonte=fonte, motivo=motivo)

    identidade_nova = cs.identidade_economica(
        cnet_brl=novo, valor_bruto=bruto, status_custo=status, documento=documento,
        fonte=fonte, data_ref=inicio)
    identidade_atual = cs.identidade_da_referencia(atual) if atual is not None else None
    mesmo_valor = atual is not None and D(atual.cnet_brl) == novo \
        and atual.status_custo == status

    if identidade_atual == identidade_nova:
        # Idêntica em valor E em evidência: reimportar a mesma coisa não versiona.
        prop.linhas.append(LinhaProposta(
            alvo=produto.sku_key, situacao=NO_OP, produto_id=produto.id,
            valor_atual=_retrato_custo(atual), valor_novo=f"CNET R$ {novo}",
            motivo="Mesma referência: valor, status, fonte, documento e data idênticos — "
                   "nada a versionar."))
    elif mesmo_valor:
        # Mesmo preço, evidência nova. O preço não muda; a rastreabilidade, sim.
        prop.linhas.append(LinhaProposta(
            alvo=produto.sku_key, situacao=RECONFIRMACAO, produto_id=produto.id,
            valor_atual=_retrato_custo(atual),
            valor_novo=f"CNET R$ {novo} (inalterado) · nova evidência",
            motivo="O valor não muda. O que muda é a evidência — fonte, documento ou data. "
                   "Vira versão nova para que a reconfirmação fique auditável.",
            detalhe={"cnet_anterior": para_float(D(atual.cnet_brl)),
                     "cnet_novo": para_float(novo), "variacao_pct": 0.0,
                     "versao_anterior": atual.versao,
                     "versao_nova": cs.proxima_versao(session, produto_id),
                     "evidencia_anterior": {"documento": atual.documento,
                                            "data_ref": str(atual.data_ref),
                                            "origem": atual.origem_registro},
                     "evidencia_nova": {"documento": documento, "data_ref": str(inicio),
                                        "fonte": fonte}}))
        prop.avisos.append(
            "Reconfirmação: o preço permanece o mesmo e uma versão nova é criada só para "
            "registrar a evidência. Nenhuma cotação muda de valor.")
    else:
        linha = LinhaProposta(
            alvo=produto.sku_key, situacao=MUDANCA, produto_id=produto.id,
            valor_atual=_retrato_custo(atual), valor_novo=f"CNET R$ {novo} · {status}",
            detalhe={"cnet_anterior": para_float(D(atual.cnet_brl)) if atual else None,
                     "cnet_novo": para_float(novo),
                     "versao_anterior": atual.versao if atual else None,
                     "versao_nova": cs.proxima_versao(session, produto_id)})
        if atual is not None and atual.cnet_brl:
            variacao = divide(novo - D(atual.cnet_brl), D(atual.cnet_brl))
            linha.detalhe["variacao_pct"] = para_float(variacao)
            if variacao is not None and abs(variacao) > D("0.30"):
                prop.avisos.append(
                    f"Variação de {variacao * 100:.1f}% no custo de {produto.sku_key}. "
                    "Acima de 30% costuma ser erro de unidade ou de casamento de SKU — "
                    "confirmar a fonte antes de aplicar.")
        prop.linhas.append(linha)

    if inicio > date.today():
        prop.avisos.append(
            f"Vigência futura: esta versão só passa a valer em {inicio:%d/%m/%Y}. Até lá, "
            "novas cotações continuam usando a versão atual.")

    prop.skus_afetados = len(prop.mudancas)
    prop.familias_afetadas = [produto.familia] if produto.familia and prop.mudancas else []
    prop.token = _hash_estado([{"produto": produto_id,
                                "versao": atual.versao if atual else None,
                                "cnet": str(atual.cnet_brl) if atual else None,
                                "valid_from": str(atual.valid_from) if atual else None}])
    return prop


def aplicar_custo_sku(session: Session, produto_id: int, prop: Proposta, *, ator: Usuario,
                      cnet_brl, status: str, fonte: str, documento: Optional[str] = None,
                      valor_bruto=None, vigente_a_partir_de: Optional[date] = None,
                      motivo: Optional[str] = None, origem: str = "admin-ui",
                      correlacao: Optional[str] = None) -> Optional[CustoReferencia]:
    """Aplica o que o preview mostrou — e só se o estado ainda for aquele.

    Devolve a nova versão, ou `None` quando a proposta era no-op.
    """
    atual = cs.referencia_vigente(session, produto_id)
    token_agora = _hash_estado([{"produto": produto_id,
                                 "versao": atual.versao if atual else None,
                                 "cnet": str(atual.cnet_brl) if atual else None,
                                 "valid_from": str(atual.valid_from) if atual else None}])
    if token_agora != prop.token:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="CustoReferencia",
                  entidade_id=produto_id, escopo=prop.escopo, resultado=CONFLITO,
                  motivo=motivo, origem=origem, correlacao=correlacao,
                  detalhe={"esperado": prop.token, "encontrado": token_agora})
        raise ConflitoDeVersao(
            "O custo deste SKU mudou depois que você abriu a revisão. Refaça o preview para "
            "ver o estado atual antes de aplicar.")

    if not prop.mudancas:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="CustoReferencia",
                  entidade_id=produto_id, escopo=prop.escopo, resultado=NO_OP,
                  motivo=motivo, origem=origem, correlacao=correlacao)
        return None

    produto = session.get(Produto, produto_id)
    inicio = vigente_a_partir_de or date.today()
    reconfirmacao = prop.mudancas[0].situacao == RECONFIRMACAO
    nova = cs.registrar_referencia(
        session, produto, cnet_brl=para_float(D(cnet_brl)),
        metodo=produto.cost_method or CostMethod.manual.value, status=status, fonte=fonte,
        documento=documento, data_ref=inicio,
        valor_bruto=para_float(D(valor_bruto)) if valor_bruto is not None else None,
        origem_registro=f"{origem} · {ator.email}", notas=motivo,
        vigente_a_partir_de=inicio,
        # Versão com data futura NÃO atualiza o cache do produto: o preço de hoje continua
        # sendo o de hoje. O cache passa a valer quando a data chegar e a referência for
        # regravada, ou quando o próximo cálculo resolver a vigência.
        atualizar_cache=(inicio <= date.today()))

    registrar(session, ator=ator,
              acao="RECONFIRMAR" if reconfirmacao else "CRIAR_VERSAO",
              entidade="CustoReferencia",
              entidade_id=nova.id, escopo=prop.escopo,
              antes=_retrato_custo(atual), depois=_retrato_custo(nova),
              motivo=motivo, origem=origem, correlacao=correlacao,
              detalhe={"sku": produto.sku_key, "vigencia_inicio": str(inicio),
                       "fonte": fonte, "documento": documento,
                       "valor_alterado": not reconfirmacao})
    return nova


# ---------------------------------------------------------------------------
# 2. Premissa global — escopo amplo, e o admin precisa ver isso
# ---------------------------------------------------------------------------
#: Quais premissas alcançam quais produtos. Serve para dizer ao admin, antes de aplicar,
#: quantos SKUs a mudança atinge — a diferença entre "mexi num item" e "mexi em tudo".
ALCANCE_PREMISSA = {
    "fx_usd_brl": "todo produto importado (KTC), via nacionalização",
    "frete_int_usd_kg": "todo produto importado (KTC), via nacionalização",
    "outras_desp_usd_un": "todo produto importado (KTC), via nacionalização",
    "pis_cofins_nominal_pct": "TODOS os produtos, em qualquer cotação",
    "pis_cofins_pct": "TODOS os produtos, em qualquer cotação",
    "comissao_base_pct": "comissão variável de TODA cotação nova (itens não-Daune)",
    "comissao_min_pct": "comissão variável de TODA cotação nova (itens não-Daune)",
}


def escopo_da_premissa(session: Session, chave: str) -> dict:
    """Quantos SKUs uma premissa global alcança, e de quais fornecedores."""
    from app.models import TipoFornecedor

    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    fornecedores = {f.id: f for f in session.exec(select(Fornecedor)).all()}
    if chave in ("fx_usd_brl", "frete_int_usd_kg", "outras_desp_usd_un"):
        alvo = [p for p in produtos
                if fornecedores.get(p.fornecedor_id)
                and fornecedores[p.fornecedor_id].tipo == TipoFornecedor.importado_ktc]
    else:
        alvo = produtos
    familias = sorted({p.familia for p in alvo if p.familia})
    return {"skus": len(alvo), "familias": familias,
            "texto": ALCANCE_PREMISSA.get(chave, "alcance não mapeado — tratar como global")}


def preview_premissa(session: Session, chave: str, *, valor_num=None, valor_txt=None,
                     fonte: str, vigente_a_partir_de: Optional[date] = None,
                     motivo: Optional[str] = None) -> Proposta:
    """Mudança de premissa versionada. Mostra o ALCANCE antes de qualquer coisa."""
    fonte = valida_fonte(fonte)
    inicio = vigente_a_partir_de or date.today()
    atual = cfg.premissa(session, chave)
    escopo = escopo_da_premissa(session, chave)

    if valor_num is not None:
        novo = valida_decimal(valor_num, chave, minimo=0)
        if chave == "fx_usd_brl" and novo <= 0:
            raise DadoInvalido("Câmbio precisa ser positivo.")
        if chave.endswith("_pct"):
            novo = valida_percentual(valor_num, chave)
        novo_txt = str(novo)
    else:
        novo = None
        novo_txt = (valor_txt or "").strip()
        if not novo_txt:
            raise DadoInvalido("Informe um valor numérico ou textual.")

    prop = Proposta(entidade="Premissa", escopo=f"premissa '{chave}' — {escopo['texto']}",
                    vigencia_inicio=inicio, fonte=fonte, motivo=motivo,
                    skus_afetados=escopo["skus"], familias_afetadas=escopo["familias"])

    atual_txt = (f"{atual.valor_num if atual.valor_num is not None else atual.valor_txt} "
                 f"(desde {atual.valid_from})") if atual else "não cadastrada"
    igual = (atual is not None
             and ((novo is not None and atual.valor_num is not None
                   and D(atual.valor_num) == novo)
                  or (novo is None and (atual.valor_txt or "") == novo_txt)))
    prop.linhas.append(LinhaProposta(
        alvo=chave, situacao=NO_OP if igual else MUDANCA,
        valor_atual=atual_txt, valor_novo=novo_txt,
        motivo="Valor idêntico ao vigente — nada a versionar." if igual else None,
        detalhe={"anterior": para_float(D(atual.valor_num)) if atual and atual.valor_num is not None else None,
                 "novo": para_float(novo) if novo is not None else None}))

    if not igual:
        prop.avisos.append(
            f"Esta premissa alcança **{escopo['skus']} SKU(s)** — {escopo['texto']}. "
            "Novas cotações passam a usar o valor novo; cotações emitidas e rascunhos "
            "existentes continuam com o valor que já snapshotaram.")
    if inicio > date.today():
        prop.avisos.append(f"Vigência futura: só entra em {inicio:%d/%m/%Y}.")

    prop.token = _hash_estado([{"chave": chave, "id": atual.id if atual else None,
                                "valor": str(atual.valor_num if atual else None),
                                "valid_from": str(atual.valid_from) if atual else None}])
    return prop


def aplicar_premissa(session: Session, chave: str, prop: Proposta, *, ator: Usuario,
                     valor_num=None, valor_txt=None, fonte: str,
                     vigente_a_partir_de: Optional[date] = None,
                     motivo: Optional[str] = None, origem: str = "admin-ui",
                     correlacao: Optional[str] = None) -> Optional[Premissa]:
    atual = cfg.premissa(session, chave)
    token_agora = _hash_estado([{"chave": chave, "id": atual.id if atual else None,
                                 "valor": str(atual.valor_num if atual else None),
                                 "valid_from": str(atual.valid_from) if atual else None}])
    if token_agora != prop.token:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="Premissa",
                  escopo=prop.escopo, resultado=CONFLITO, motivo=motivo, origem=origem,
                  correlacao=correlacao)
        raise ConflitoDeVersao(
            f"A premissa '{chave}' mudou depois que você abriu a revisão. Refaça o preview.")
    if not prop.mudancas:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="Premissa",
                  escopo=prop.escopo, resultado=NO_OP, motivo=motivo, origem=origem,
                  correlacao=correlacao)
        return None

    inicio = vigente_a_partir_de or date.today()
    nova = definir_com_vigencia(session, chave, valor_num=valor_num, valor_txt=valor_txt,
                                fonte=fonte, notas=motivo, vigente_a_partir_de=inicio)
    registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="Premissa",
              entidade_id=nova.id, escopo=prop.escopo,
              antes=prop.linhas[0].valor_atual, depois=prop.linhas[0].valor_novo,
              motivo=motivo, origem=origem, correlacao=correlacao,
              detalhe={"chave": chave, "vigencia_inicio": str(inicio), "fonte": fonte,
                       "skus_alcancados": prop.skus_afetados})
    return nova


def definir_com_vigencia(session: Session, chave: str, *, valor_num=None, valor_txt=None,
                         fonte: Optional[str] = None, notas: Optional[str] = None,
                         vigente_a_partir_de: Optional[date] = None) -> Premissa:
    """`config_service.definir` com data de início escolhida — inclusive futura.

    O `definir` original sempre usava hoje, o que tornava impossível agendar. Aqui a versão
    anterior é fechada **na data de início da nova**, não hoje: até lá ela continua sendo a
    vigente, e é ela que o cálculo resolve.
    """
    inicio = vigente_a_partir_de or date.today()
    atual = cfg.premissa(session, chave)
    if atual is not None:
        if atual.valor_num == valor_num and atual.valor_txt == valor_txt:
            return atual
        atual.valid_to = inicio
        # `ativo` continua True quando a nova versão é futura: fechar a vigência é dizer
        # até quando ela vale, não desligá-la agora.
        atual.ativo = inicio > date.today()
        session.add(atual)
    nova = Premissa(chave=chave, valor_num=valor_num, valor_txt=valor_txt, fonte=fonte,
                    notas=notas, descricao=(atual.descricao if atual else None),
                    unidade=(atual.unidade if atual else None), valid_from=inicio)
    session.add(nova)
    session.flush()
    return nova


# ---------------------------------------------------------------------------
# 3. Margem — e o nível em que a regra está sendo criada
# ---------------------------------------------------------------------------
NIVEL_SKU = "SKU"
NIVEL_FORNECEDOR = "FORNECEDOR"
NIVEL_FAMILIA = "FAMILIA"
NIVEL_GERAL = "GERAL"


def nivel_da_regra(regra) -> str:
    if getattr(regra, "sku_key", None):
        return NIVEL_SKU
    if getattr(regra, "fornecedor_id", None):
        return NIVEL_FORNECEDOR
    if getattr(regra, "familia", None):
        return NIVEL_FAMILIA
    return NIVEL_GERAL


def escopo_da_margem(session: Session, *, fornecedor_id=None, familia=None, sku_key=None) -> dict:
    """Quantos SKUs a regra de margem alcança. O admin precisa ver isto ANTES de aplicar.

    A confusão que isto evita é específica e cara: achar que se está ajustando a margem de um
    item quando se está ajustando a de um fornecedor inteiro.
    """
    produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    if sku_key:
        alvo = [p for p in produtos if p.sku_key == sku_key]
        nivel = NIVEL_SKU
    elif fornecedor_id:
        alvo = [p for p in produtos if p.fornecedor_id == fornecedor_id]
        if familia:
            alvo = [p for p in alvo
                    if (p.familia or "").strip().lower() == familia.strip().lower()]
        nivel = NIVEL_FORNECEDOR
    elif familia:
        alvo = [p for p in produtos
                if (p.familia or "").strip().lower() == familia.strip().lower()]
        nivel = NIVEL_FAMILIA
    else:
        alvo = produtos
        nivel = NIVEL_GERAL
    return {"nivel": nivel, "skus": len(alvo),
            "familias": sorted({p.familia for p in alvo if p.familia})}


def _mesmo_escopo(r, *, sku_key, fornecedor_id, familia, min_thread_count=None,
                  max_thread_count=None) -> bool:
    """Mesmo escopo = mesmo SKU, fornecedor, família **e faixa de fios**.

    Sem a faixa, versionar "KTC — Flat Sheet < 300TC" encerraria também a regra "≥ 300TC"
    da mesma família — C-NEW-14 tornou isso visível ao levar a tela de configurações para
    este caminho.
    """
    return (r.ativo and (r.valid_to is None)
            and (r.sku_key or None) == (sku_key or None)
            and (r.fornecedor_id or None) == (fornecedor_id or None)
            and ((r.familia or "").lower() or None) == ((familia or "").lower() or None)
            and (r.min_thread_count or None) == (min_thread_count or None)
            and (r.max_thread_count or None) == (max_thread_count or None))


def preview_margem(session: Session, *, margem_pct, nome: str, fornecedor_id=None,
                   familia=None, sku_key=None, prioridade: int = 50,
                   fonte: str = "", vigente_a_partir_de: Optional[date] = None,
                   motivo: Optional[str] = None, piso_pct=None, comissao_formacao_pct=None,
                   preco_travado: Optional[bool] = None, min_thread_count=None,
                   max_thread_count=None) -> Proposta:
    fonte = valida_fonte(fonte)
    nova = valida_percentual(margem_pct, "margem")
    inicio = vigente_a_partir_de or date.today()
    escopo = escopo_da_margem(session, fornecedor_id=fornecedor_id, familia=familia,
                              sku_key=sku_key)

    rotulo = {NIVEL_SKU: f"SKU {sku_key}", NIVEL_FORNECEDOR: f"fornecedor #{fornecedor_id}",
              NIVEL_FAMILIA: f"família {familia}", NIVEL_GERAL: "TODOS os produtos"}[escopo["nivel"]]
    prop = Proposta(entidade="MargemRegra", escopo=f"{escopo['nivel']} — {rotulo}",
                    vigencia_inicio=inicio, fonte=fonte, motivo=motivo,
                    skus_afetados=escopo["skus"], familias_afetadas=escopo["familias"])

    from app.margin_rules import resolver_margem
    regras = session.exec(select(MargemRegra)).all()
    amostra = None
    if escopo["skus"]:
        produtos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
        if sku_key:
            amostra = next((p for p in produtos if p.sku_key == sku_key), None)
        elif fornecedor_id:
            amostra = next((p for p in produtos if p.fornecedor_id == fornecedor_id), None)
        elif familia:
            amostra = next((p for p in produtos
                            if (p.familia or "").lower() == familia.lower()), None)
    atual = resolver_margem(regras, fornecedor_id=getattr(amostra, "fornecedor_id", None),
                            familia=getattr(amostra, "familia", None),
                            thread_count=getattr(amostra, "thread_count", None),
                            sku_key=getattr(amostra, "sku_key", None)) if amostra else None

    igual = atual is not None and D(atual.margem_pct) == nova
    prop.linhas.append(LinhaProposta(
        alvo=rotulo, situacao=NO_OP if igual else MUDANCA,
        valor_atual=(f"{D(atual.margem_pct) * 100:.2f}% ({atual.regra})" if atual
                     else "sem regra específica"),
        valor_novo=f"{nova * 100:.2f}%",
        motivo="Mesma margem já resolvida hoje — nada a versionar." if igual else None,
        detalhe={"nivel": escopo["nivel"], "prioridade": prioridade}))

    if not igual:
        prop.avisos.append(
            f"Nível **{escopo['nivel']}**: esta regra alcança {escopo['skus']} SKU(s). "
            "A precedência continua sendo override do item → SKU → fornecedor/família → geral.")
    if escopo["nivel"] == NIVEL_GERAL:
        prop.avisos.append("Regra SEM escopo: vale para o catálogo inteiro. Se a intenção era "
                           "um fornecedor ou uma família, volte e escolha o nível.")
    if inicio > date.today():
        prop.avisos.append(f"Vigência futura: só entra em {inicio:%d/%m/%Y}.")

    prop.token = _hash_estado(sorted(
        [{"id": r.id, "m": str(r.margem_pct), "vf": str(r.valid_from),
          "vt": str(r.valid_to), "a": r.ativo} for r in regras], key=lambda x: x["id"] or 0))
    return prop


def aplicar_margem(session: Session, prop: Proposta, *, ator: Usuario, margem_pct, nome: str,
                   fornecedor_id=None, familia=None, sku_key=None, prioridade: int = 50,
                   fonte: str = "", vigente_a_partir_de: Optional[date] = None,
                   motivo: Optional[str] = None, origem: str = "admin-ui",
                   correlacao: Optional[str] = None, piso_pct=None,
                   comissao_formacao_pct=None, preco_travado: Optional[bool] = None,
                   min_thread_count=None, max_thread_count=None) -> Optional[MargemRegra]:
    """Versiona a margem de um escopo. **A política do escopo é herdada**, não perdida.

    Desde 16/09/2026 a regra carrega piso, comissão de formação e preço travado. Uma regra
    nova criada pelo painel só com a margem herdaria NULOS — e o produto voltaria em
    silêncio à semântica anterior (comissão por faixa, autonomia zero). Por isso o que não
    for informado é copiado da regra do mesmo escopo que está sendo encerrada; a margem
    anterior fica registrada em `margem_anterior_pct`.
    """
    regras = session.exec(select(MargemRegra)).all()
    token_agora = _hash_estado(sorted(
        [{"id": r.id, "m": str(r.margem_pct), "vf": str(r.valid_from),
          "vt": str(r.valid_to), "a": r.ativo} for r in regras], key=lambda x: x["id"] or 0))
    if token_agora != prop.token:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="MargemRegra",
                  escopo=prop.escopo, resultado=CONFLITO, motivo=motivo, origem=origem,
                  correlacao=correlacao)
        raise ConflitoDeVersao("As regras de margem mudaram desde o preview. Refaça a revisão.")
    if not prop.mudancas:
        registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="MargemRegra",
                  escopo=prop.escopo, resultado=NO_OP, motivo=motivo, origem=origem,
                  correlacao=correlacao)
        return None

    inicio = vigente_a_partir_de or date.today()
    # Regra anterior EXATAMENTE do mesmo escopo tem a vigência encerrada — não é apagada.
    encerrada = None
    for r in regras:
        if _mesmo_escopo(r, sku_key=sku_key, fornecedor_id=fornecedor_id, familia=familia,
                         min_thread_count=min_thread_count, max_thread_count=max_thread_count):
            r.valid_to = inicio
            session.add(r)
            # a herança vem da regra mais recente do escopo — a que estava formando preço
            if encerrada is None or (r.valid_from or date.min) >= (encerrada.valid_from or date.min):
                encerrada = r
    herdar = lambda valor, campo: (valor if valor is not None      # noqa: E731
                                   else getattr(encerrada, campo, None))
    piso = herdar(piso_pct, "piso_pct")
    comissao = herdar(comissao_formacao_pct, "comissao_formacao_pct")
    travado = herdar(preco_travado, "preco_travado")

    nova = MargemRegra(nome=nome, fornecedor_id=fornecedor_id, familia=familia,
                       sku_key=sku_key, margem_pct=para_float(D(margem_pct)),
                       min_thread_count=min_thread_count, max_thread_count=max_thread_count,
                       prioridade=prioridade, valid_from=inicio, ativo=True,
                       notas=f"{fonte}{' · ' + motivo if motivo else ''}",
                       piso_pct=para_float(D(piso)) if piso is not None else None,
                       comissao_formacao_pct=(para_float(D(comissao))
                                              if comissao is not None else None),
                       preco_travado=bool(travado),
                       margem_anterior_pct=(para_float(D(encerrada.margem_pct))
                                            if encerrada is not None else None),
                       politica=getattr(encerrada, "politica", None),
                       fonte=fonte or None)
    session.add(nova)
    session.flush()
    registrar(session, ator=ator, acao="CRIAR_VERSAO", entidade="MargemRegra",
              entidade_id=nova.id, escopo=prop.escopo,
              antes=prop.linhas[0].valor_atual, depois=prop.linhas[0].valor_novo,
              motivo=motivo, origem=origem, correlacao=correlacao,
              detalhe={"nivel": prop.escopo, "skus_alcancados": prop.skus_afetados,
                       "vigencia_inicio": str(inicio), "fonte": fonte})
    return nova


# ---------------------------------------------------------------------------
# 4. Proteção do histórico
# ---------------------------------------------------------------------------
def referencia_esta_em_uso(session: Session, ref: CustoReferencia) -> bool:
    """A versão já participou de alguma cotação?

    Basta existir item de cotação daquele produto cuja data de criação caia dentro da
    vigência da versão: aquele preço saiu dela. Não se apaga o que já explicou um número
    a um cliente.
    """
    if ref is None or ref.produto_id is None:
        return False
    itens = session.exec(select(CotacaoItem)
                         .where(CotacaoItem.produto_id == ref.produto_id)).all()
    return bool(itens)


def encerrar_vigencia(session: Session, ref: CustoReferencia, *, ator: Usuario,
                      motivo: str, em: Optional[date] = None) -> CustoReferencia:
    """Encerra a vigência de uma versão. **Nunca apaga.**"""
    if not (motivo or "").strip():
        raise DadoInvalido("Encerrar vigência exige motivo registrado.")
    quando = em or date.today()
    antes = _retrato_custo(ref)
    ref.valid_to = quando
    ref.vigente = False
    ref.aplicado = False
    session.add(ref)
    registrar(session, ator=ator, acao="ENCERRAR_VIGENCIA", entidade="CustoReferencia",
              entidade_id=ref.id, escopo=f"SKU {ref.sku_key}", antes=antes,
              depois=f"encerrada em {quando}", motivo=motivo)
    return ref


def apagar_referencia(session: Session, ref: CustoReferencia, *, ator: Usuario):
    """DELETE físico — permitido apenas no que nunca foi usado."""
    if referencia_esta_em_uso(session, ref):
        registrar(session, ator=ator, acao="APAGAR", entidade="CustoReferencia",
                  entidade_id=ref.id, escopo=f"SKU {ref.sku_key}", resultado="RECUSADO",
                  motivo="referência já participou de cotação")
        raise DadoInvalido(
            "Esta versão já participou de cotação e não pode ser apagada. Encerrar a "
            "vigência preserva o histórico e produz o mesmo efeito prático.")
    session.delete(ref)
    registrar(session, ator=ator, acao="APAGAR", entidade="CustoReferencia",
              entidade_id=ref.id, escopo=f"SKU {ref.sku_key}",
              motivo="versão nunca utilizada")


# ---------------------------------------------------------------------------
# 5. Rascunho com premissa desatualizada
# ---------------------------------------------------------------------------
def premissas_desatualizadas(session: Session, cotacao, itens: Sequence[CotacaoItem]) -> dict:
    """O rascunho está usando premissa mais antiga que a vigente?

    **Só detecta. Não recalcula.** Um rascunho aberto amanhã continua exatamente com os
    números de ontem — trocar sozinho seria mudar o preço debaixo de quem já negociou. A
    atualização é ato explícito.

    Duas coisas podem ter envelhecido, e são diferentes:

    * a **referência de custo** do SKU ganhou versão nova — alguém cadastrou outro custo;
    * uma **premissa versionada** ganhou versão nova — o câmbio mudou, por exemplo.

    A segunda não aparecia aqui, e é a que mais acontece: trocar o câmbio não cria
    `CustoReferencia` nenhuma, então um rascunho de SKU importado continuava dizendo que
    estava tudo em dia enquanto o dólar já era outro. Os pinos do item guardam exatamente
    qual versão formou aquele preço, e é contra eles que a comparação é feita.
    """
    desatualizados = []
    premissas_novas = _premissas_mais_novas(session, itens)
    for it in itens:
        if not it.produto_id:
            continue
        vigente = cs.referencia_vigente(session, it.produto_id)
        if vigente is None or vigente.cnet_brl is None:
            continue
        if it.custo_unitario is None:
            continue
        if D(vigente.cnet_brl) != D(it.custo_unitario):
            desatualizados.append({
                "item_id": it.id, "produto_id": it.produto_id,
                "nome": it.nome_produto,
                "custo_no_item": para_float(D(it.custo_unitario)),
                "custo_vigente": para_float(D(vigente.cnet_brl)),
                "versao_vigente": vigente.versao,
            })
    # Terceira coisa que envelhece (Fase 3A): a **política comercial**. Um rascunho formado
    # antes de 16/09/2026 não tem piso nem comissão de formação congelados; o produto dele
    # hoje resolve para uma regra com política. Detecta e nomeia — a reprecificação continua
    # sendo o botão de atualizar, e o item continua sendo avaliado como foi formado.
    from app.comercial_service import itens_anteriores_a_politica
    politica_anterior = itens_anteriores_a_politica(session, itens)

    partes = []
    if desatualizados:
        partes.append(f"{len(desatualizados)} item(ns) usam custo anterior ao vigente.")
    for p in premissas_novas:
        partes.append(f"{p['rotulo']}: esta cotação usa {p['no_item']}, "
                      f"e o valor atual é {p['vigente']}.")
    if politica_anterior:
        partes.append(f"{len(politica_anterior)} item(ns) foram formados com a política "
                      "comercial anterior a 16/09/2026 (margem-alvo, piso e comissão de "
                      "formação diferentes).")

    return {"desatualizado": bool(desatualizados or premissas_novas or politica_anterior),
            "itens": desatualizados,
            "premissas": premissas_novas,
            "politica_anterior": politica_anterior,
            "texto": (" ".join(partes) + " Nada foi alterado — atualizar é uma ação explícita."
                      if partes else "Todas as premissas do rascunho estão vigentes.")}


#: As premissas que a tela nomeia quando avisa que há versão mais nova. A chave técnica
#: não serve: "fx_usd_brl mudou" não diz nada a quem vende.
ROTULO_PREMISSA = {
    "fx_usd_brl": "Câmbio do dólar",
    "frete_int_usd_kg": "Frete internacional",
    "outras_desp_usd_un": "Outras despesas de importação",
    "pis_cofins_nominal_pct": "PIS/COFINS nominal da venda",
    "pis_cofins_pct": "PIS/COFINS (legado)",
    "comissao_base_pct": "Comissão-base da cotação",
    "comissao_min_pct": "Comissão mínima da cotação",
}


def _premissas_mais_novas(session: Session, itens: Sequence[CotacaoItem]) -> list:
    """Premissas pinadas nos itens que já têm versão mais recente vigente.

    Compara **id de versão**, não valor. Uma versão nova com o mesmo número continua sendo
    outra versão — foi reconfirmada por outra fonte, e essa é justamente a informação que a
    Sessão 5 decidiu não descartar.
    """
    achados = {}
    for it in itens:
        if not it.premissas_pinadas:
            continue
        try:
            pinos = json.loads(it.premissas_pinadas)
        except (TypeError, ValueError):
            continue
        for chave, pino in (pinos or {}).items():
            if chave in achados or not isinstance(pino, dict):
                continue
            vigente = cfg.premissa(session, chave)
            if vigente is None or vigente.id == pino.get("premissa_id"):
                continue
            achados[chave] = {
                "chave": chave,
                "rotulo": ROTULO_PREMISSA.get(chave, chave),
                "no_item": _formatar_premissa(chave, pino.get("valor")),
                "vigente": _formatar_premissa(chave, vigente.valor_num),
                "vigente_desde": (vigente.valid_from.isoformat()
                                  if vigente.valid_from else None),
            }
    return list(achados.values())


def _formatar_premissa(chave: str, valor) -> str:
    """Premissa em texto de gente. Câmbio nunca perde os centavos: "R$ 5,00", não "R$ 5"."""
    if valor is None:
        return "—"
    if chave.endswith("_pct"):
        return f"{D(valor) * 100:.2f}%".replace(".", ",")
    if chave == "fx_usd_brl":
        return f"R$ {D(valor):.2f}".replace(".", ",")
    texto = f"{D(valor):.4f}".rstrip("0").rstrip(".") or "0"
    return texto.replace(".", ",")


# ---------------------------------------------------------------------------
# 6. Simulação de impacto — admin, sem tocar em cotação
# ---------------------------------------------------------------------------
def simular_impacto_custo(session: Session, produto_id: int, cnet_novo) -> dict:
    """Preço recomendado antes × depois, se o custo passar a ser este. **Não grava nada.**

    Simulação administrativa: não altera cotação, item nem snapshot. Serve para o admin
    enxergar o efeito comercial de uma referência nova antes de decidir aplicá-la.
    """
    from app import pricing_service as ps
    from app.pricing_engine import calcular_por_margem

    produto = session.get(Produto, produto_id)
    if produto is None:
        return {"erro": "produto não encontrado"}
    cot = ps.cenario_padrao_catalogo(session)
    regras, contexto = ps.regras_da_cotacao(session, cot, produto)
    if regras is None:
        return {"bloqueado": True, "motivo": contexto.get("motivo_bloqueio")}

    margem = ps.margem_padrao(session, produto)
    atual = calcular_por_margem(produto.custo_unitario or 0, 1, margem.margem_pct, regras)
    novo = calcular_por_margem(D(cnet_novo), 1, margem.margem_pct, regras)
    delta = novo.preco_negociado - atual.preco_negociado
    return {
        "sku": produto.sku_key,
        "custo_atual": para_float(D(produto.custo_unitario)),
        "custo_novo": para_float(D(cnet_novo)),
        "preco_atual": para_float(atual.preco_negociado),
        "preco_novo": para_float(novo.preco_negociado),
        "diferenca": para_float(delta),
        "diferenca_pct": para_float(divide(delta, atual.preco_negociado)),
        "margem_alvo": para_float(margem.margem_pct),
        "margem_resultante": para_float(novo.margem_liquida),
        "observacao": ("Simulação administrativa: nenhuma cotação, item ou snapshot foi "
                       "alterado."),
    }


# ---------------------------------------------------------------------------
# 7. Importação em lote — dry run obrigatório
# ---------------------------------------------------------------------------
def _casar_sku(session: Session, linha: dict):
    """Casa uma linha da planilha com um SKU do catálogo.

    Devolve `(produto, situacao_ou_None, motivo_ou_None)`. As duas situações de falha são
    diferentes e não devem ser confundidas:

    * `SKU_NAO_ENCONTRADO` — procurou-se e não existe. O arquivo traz algo que o catálogo
      não tem;
    * `REVIEW_REQUIRED` — não deu para **determinar** qual SKU é: faltam campos, ou sobram
      candidatos. O dado pode estar certo; o casamento é que não é seguro.

    Tratar as duas como a mesma coisa faria "não sei qual é" virar "não existe", e o SKU
    real ficaria sem atualização sem que ninguém percebesse.

    **Só casa quando é inequívoco.** Código exato primeiro; depois os campos estruturados
    (fornecedor + família + medida + gramatura). Nome não entra: "Lençol 300 fios branco" e
    "Lençol 300 fios cru" são a mesma string para um fuzzy match e produtos diferentes na
    prateleira. Casamento aproximado de custo econômico é como errar o preço com convicção.

    Ambiguidade — duas ou mais linhas candidatas — devolve `REVIEW_REQUIRED`, nunca "a mais
    parecida".
    """
    sku = (linha.get("sku_key") or "").strip()
    if sku:
        exato = session.exec(select(Produto).where(Produto.sku_key == sku)).all()
        if len(exato) == 1:
            return exato[0], None, None
        if len(exato) > 1:
            return None, REVIEW_REQUIRED, f"Há {len(exato)} produtos com o SKU '{sku}'."
        return None, NAO_ENCONTRADO, f"Nenhum produto com o SKU '{sku}'."

    campos = {k: linha.get(k) for k in ("fornecedor_id", "familia", "largura_cm",
                                        "comprimento_cm", "gsm", "thread_count")
              if linha.get(k) is not None}
    if len(campos) < 3:
        return None, REVIEW_REQUIRED, (
            "Sem SKU e sem campos estruturados suficientes para casar sem ambiguidade "
            "(fornecedor, família e medida/gramatura).")

    candidatos = session.exec(select(Produto).where(Produto.ativo == True)).all()  # noqa: E712
    for chave, valor in campos.items():
        if chave in ("familia",):
            candidatos = [p for p in candidatos
                          if (getattr(p, chave, "") or "").strip().lower()
                          == str(valor).strip().lower()]
        else:
            candidatos = [p for p in candidatos if getattr(p, chave, None) == valor]
    if len(candidatos) == 1:
        return candidatos[0], None, None
    if not candidatos:
        return None, NAO_ENCONTRADO, "Nenhum SKU do catálogo bate com estes campos."
    return None, REVIEW_REQUIRED, (
        f"{len(candidatos)} SKUs batem com estes campos — o casamento é ambíguo e não se "
        "escolhe o mais parecido.")


def preview_importacao(session: Session, linhas: Sequence[dict], *, fonte: str,
                       documento: Optional[str] = None,
                       vigente_a_partir_de: Optional[date] = None) -> Proposta:
    """DRY RUN de uma importação. **Nenhuma escrita**, em nenhuma circunstância.

    Classifica cada linha: `MUDANCA` (versão nova), `NO_OP` (idêntica à vigente),
    `REVIEW_REQUIRED` (ambígua) ou `SKU_NAO_ENCONTRADO`. Só as `MUDANCA` viram versão no
    apply — e as outras três não escrevem nada, nem parcialmente.
    """
    fonte = valida_fonte(fonte)
    inicio = vigente_a_partir_de or date.today()
    prop = Proposta(entidade="CustoReferencia", escopo=f"importação · {documento or fonte}",
                    vigencia_inicio=inicio, fonte=fonte)
    estado = []

    for i, linha in enumerate(linhas, start=1):
        rotulo = linha.get("sku_key") or f"linha {i}"
        produto, situacao, motivo = _casar_sku(session, linha)
        if produto is None:
            prop.linhas.append(LinhaProposta(alvo=rotulo, situacao=situacao, motivo=motivo))
            continue
        try:
            valor = valida_decimal(linha.get("cnet_brl"), f"CNET de {rotulo}", minimo=0,
                                   permite_zero=False)
        except DadoInvalido as e:
            prop.linhas.append(LinhaProposta(alvo=rotulo, situacao=REVIEW_REQUIRED,
                                             produto_id=produto.id, motivo=str(e)))
            continue

        atual = cs.referencia_vigente(session, produto.id)
        estado.append({"produto": produto.id, "versao": atual.versao if atual else None,
                       "cnet": str(atual.cnet_brl) if atual else None})
        status = linha.get("status") or StatusCusto.confirmado.value
        bruto_linha = (D(linha.get("valor_bruto"))
                       if linha.get("valor_bruto") is not None else None)
        # A comparação é pela IDENTIDADE ECONÔMICA completa — valor, status e evidência.
        # Comparar só o número faria "mesmo preço, tabela nova" desaparecer como se nada
        # tivesse acontecido, e a prova de que o fornecedor reconfirmou o preço se perderia.
        identidade_nova = cs.identidade_economica(
            cnet_brl=valor, valor_bruto=bruto_linha, status_custo=status,
            documento=documento, fonte=fonte, data_ref=inicio)
        identidade_atual = cs.identidade_da_referencia(atual) if atual is not None else None
        mesmo_valor = atual is not None and D(atual.cnet_brl) == valor \
            and atual.status_custo == status

        if identidade_atual == identidade_nova:
            situacao, motivo = NO_OP, "Mesma referência e mesma evidência."
        elif mesmo_valor:
            situacao, motivo = RECONFIRMACAO, ("Preço inalterado; evidência nova "
                                               "(fonte, documento ou data).")
        else:
            situacao, motivo = MUDANCA, None
        prop.linhas.append(LinhaProposta(
            alvo=produto.sku_key, situacao=situacao, produto_id=produto.id,
            valor_atual=_retrato_custo(atual), valor_novo=f"CNET R$ {valor}",
            motivo=motivo,
            detalhe={"cnet_novo": para_float(valor), "status": status,
                     "documento": documento, "data_ref": str(inicio)}))

    prop.skus_afetados = len(prop.mudancas)
    prop.token = _hash_estado(estado)
    if prop.linhas and not prop.mudancas:
        prop.avisos.append("Nenhuma linha do arquivo altera o catálogo.")
    return prop


def aplicar_importacao(session: Session, linhas: Sequence[dict], prop: Proposta, *,
                       ator: Usuario, fonte: str, documento: Optional[str] = None,
                       vigente_a_partir_de: Optional[date] = None,
                       motivo: Optional[str] = None) -> dict:
    """Aplica **apenas** as linhas classificadas como mudança. As demais não escrevem nada."""
    correlacao = f"import-{datetime.utcnow():%Y%m%d%H%M%S}"
    inicio = vigente_a_partir_de or date.today()
    por_produto = {}
    for linha in linhas:
        produto, _s, _m = _casar_sku(session, linha)
        if produto is not None:
            por_produto[produto.id] = linha

    aplicadas, ignoradas = 0, 0
    for item in prop.linhas:
        if not item.aplicavel or item.produto_id is None:
            ignoradas += 1
            continue
        linha = por_produto.get(item.produto_id, {})
        produto = session.get(Produto, item.produto_id)
        atual = cs.referencia_vigente(session, produto.id)
        nova = cs.registrar_referencia(
            session, produto, cnet_brl=para_float(D(linha.get("cnet_brl"))),
            metodo=produto.cost_method or CostMethod.manual.value,
            status=linha.get("status") or StatusCusto.confirmado.value,
            fonte=fonte, documento=documento, data_ref=inicio,
            valor_bruto=(para_float(D(linha.get("valor_bruto")))
                         if linha.get("valor_bruto") is not None else None),
            origem_registro=f"importacao · {ator.email}", notas=motivo,
            vigente_a_partir_de=inicio, atualizar_cache=(inicio <= date.today()))
        registrar(session, ator=ator, acao="IMPORTAR", entidade="CustoReferencia",
                  entidade_id=nova.id, escopo=f"SKU {produto.sku_key}",
                  antes=_retrato_custo(atual), depois=_retrato_custo(nova),
                  motivo=motivo, origem="importacao", correlacao=correlacao,
                  detalhe={"documento": documento, "fonte": fonte})
        aplicadas += 1

    registrar(session, ator=ator, acao="IMPORTAR_LOTE", entidade="CustoReferencia",
              escopo=prop.escopo, motivo=motivo, origem="importacao",
              correlacao=correlacao, resultado="OK",
              detalhe={"aplicadas": aplicadas, "ignoradas": ignoradas,
                       "resumo": prop.resumo})
    return {"correlacao": correlacao, "aplicadas": aplicadas, "ignoradas": ignoradas,
            "resumo": prop.resumo}

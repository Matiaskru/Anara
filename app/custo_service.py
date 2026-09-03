"""Referência de custo por SKU — versionada, rastreável e não destrutiva.

O problema que este módulo resolve: até aqui, atualizar o custo de um SKU era escrever por cima
de `Produto.custo_unitario`. O valor anterior sumia, e com ele a resposta para "de onde veio
este número?". Pior: não havia como atualizar **um** SKU sem risco de arrastar outros num
processo em massa.

A regra passa a ser: **atualizar custo é criar uma versão nova.**

    v1  valid_from 01/08  valid_to 12/08  vigente=False   fonte: tabela Trousseau
    v2  valid_from 12/08  valid_to  —     vigente=True    fonte: Linha Hotelaria 12.08.26

A versão anterior continua no banco, auditável, com sua fonte e sua data. Três garantias caem
de graça dessa modelagem:

* **isolamento** — a versão é por `produto_id`; mexer no SKU X não toca no SKU Y;
* **histórico intocado** — cotação emitida guarda o próprio snapshot em
  `CotacaoItem.memoria_json` e não relê a referência, então nada é reprecificado por efeito
  colateral;
* **auditoria** — qualquer versão passada pode ser lida, com fonte, data e a memória do cálculo
  que produziu aquele CNET.

`Produto.custo_unitario` continua existindo como **cache** da versão vigente, porque o motor
comercial e as telas o leem. Ele é atualizado ao publicar uma versão, nunca editado à mão por
este módulo.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Session, select

from app.models import (
    CostMethod, CustoReferencia, Fornecedor, Produto, StatusCusto, STATUS_QUE_PRECIFICAM,
)

# --- Daune: créditos de ENTRADA aprovados -----------------------------------------------
# Atenção: este é o crédito da COMPRA. Não tem relação com o ICMS da VENDA, que a Sessão 1
# resolve por operação. Misturar os dois foi o erro que o B-09 registrou.
DAUNE_ICMS_CREDITO = 0.12
DAUNE_PIS_COFINS_CREDITO = 0.0925


@dataclass
class CustoNacional:
    """CNET de fornecedor nacional a partir do preço BRUTO, com a conta aberta."""
    gross: float
    icms_credito: float
    base_pis_cofins: float
    pis_cofins_credito: float
    cnet: float
    fator: float

    def como_dict(self) -> dict:
        return {
            "gross": self.gross, "icms_credito": self.icms_credito,
            "base_pis_cofins": self.base_pis_cofins,
            "pis_cofins_credito": self.pis_cofins_credito,
            "cnet": self.cnet, "fator_efetivo": self.fator,
            "formula": ("CNET = gross − gross×12% − (gross − gross×12%)×9,25%"),
        }


def cnet_nacional(gross: float, icms_pct: float = DAUNE_ICMS_CREDITO,
                  pis_cofins_pct: float = DAUNE_PIS_COFINS_CREDITO) -> CustoNacional:
    """CUSTO NET a partir do preço bruto do fornecedor nacional.

    Calculado **pelos componentes**, não pelo fator arredondado de 0,7986 — o fator é
    conferência, não fórmula. Parte sempre do bruto da fonte: aplicar o fator sobre um custo já
    persistido produziria um número sem significado, porque esse custo pode ter vindo de outro
    documento.
    """
    if gross is None or gross <= 0:
        raise ValueError("preço bruto do fornecedor precisa ser positivo")
    icms = gross * icms_pct
    base = gross - icms
    pis_cofins = base * pis_cofins_pct
    cnet = gross - icms - pis_cofins
    return CustoNacional(gross=gross, icms_credito=icms, base_pis_cofins=base,
                         pis_cofins_credito=pis_cofins, cnet=cnet, fator=cnet / gross)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def versoes(session: Session, produto_id: int) -> List[CustoReferencia]:
    """Todas as versões de um SKU, da mais antiga para a mais nova."""
    linhas = session.exec(
        select(CustoReferencia)
        .where(CustoReferencia.produto_id == produto_id)
        .where(CustoReferencia.versao.is_not(None))).all()
    return sorted(linhas, key=lambda r: (r.versao or 0, r.id or 0))


def referencia_vigente(session: Session, produto_id: int) -> Optional[CustoReferencia]:
    """A versão em vigor. `None` quando o SKU ainda não tem referência versionada."""
    vigentes = [r for r in versoes(session, produto_id) if r.vigente]
    return vigentes[-1] if vigentes else None


def referencia_em(session: Session, produto_id: int, quando: date) -> Optional[CustoReferencia]:
    """Qual versão estava valendo numa data — é o que torna o histórico auditável."""
    for r in reversed(versoes(session, produto_id)):
        inicio = r.valid_from or date.min
        fim = r.valid_to
        if inicio <= quando and (fim is None or quando < fim):
            return r
    return None


def proxima_versao(session: Session, produto_id: int) -> int:
    existentes = versoes(session, produto_id)
    return (max((r.versao or 0) for r in existentes) + 1) if existentes else 1


# ---------------------------------------------------------------------------
# Escrita — sempre aditiva
# ---------------------------------------------------------------------------
def registrar_referencia(session: Session, produto: Produto, *, cnet_brl: float,
                         metodo: str, status: str, fonte: str, documento: Optional[str] = None,
                         data_ref: Optional[date] = None, valor_bruto: Optional[float] = None,
                         moeda: str = "BRL", memoria: Optional[dict] = None,
                         origem_registro: str = "sistema", notas: Optional[str] = None,
                         confirmation_pending: Optional[bool] = None,
                         vigente_a_partir_de: Optional[date] = None,
                         atualizar_cache: bool = True) -> CustoReferencia:
    """Cria uma versão nova e fecha a anterior. **Nunca sobrescreve.**

    Exige fonte: valor sem procedência não entra. É a tradução do princípio de que toda
    referência de custo responde de onde veio o número.
    """
    if not fonte:
        raise ValueError("referência de custo exige fonte — valor sem procedência não entra")
    if status not in {s.value for s in StatusCusto}:
        raise ValueError(f"status '{status}' não é um dos cinco canônicos")
    if metodo not in {m.value for m in CostMethod}:
        raise ValueError(f"método '{metodo}' não está no enum de métodos de custo")

    inicio = vigente_a_partir_de or date.today()
    anterior = referencia_vigente(session, produto.id)

    # Registrar de novo a MESMA referência não cria versão: rodar a reconciliação duas vezes
    # não pode encher o histórico de versões no-op. Versão nova exige número, fonte ou
    # documento diferentes.
    if (anterior is not None
            and anterior.cnet_brl == cnet_brl
            and anterior.valor_bruto == valor_bruto
            and anterior.documento == documento
            and anterior.status_custo == status):
        return anterior
    if anterior is not None:
        anterior.vigente = False
        anterior.valid_to = inicio
        anterior.aplicado = False
        session.add(anterior)

    if confirmation_pending is None:
        confirmation_pending = status == StatusCusto.estimado.value

    nova = CustoReferencia(
        produto_id=produto.id, sku_key=produto.sku_key, fornecedor_id=produto.fornecedor_id,
        tipo=metodo, valor=cnet_brl, moeda=moeda, data_ref=data_ref or inicio,
        documento=documento, confianca=status, aplicado=True, notas=notas,
        versao=proxima_versao(session, produto.id), valid_from=inicio, valid_to=None,
        vigente=True, metodo_custo=metodo, status_custo=status,
        confirmation_pending=confirmation_pending, valor_bruto=valor_bruto, cnet_brl=cnet_brl,
        memoria_calculo=json.dumps(memoria or {}, ensure_ascii=False, default=str),
        origem_registro=f"{origem_registro} · {fonte}",
        substitui_versao=(anterior.versao if anterior else None),
    )
    session.add(nova)
    session.flush()

    # O cache do produto acompanha a versão vigente — e só ele. Status que não formam preço
    # (A_COTAR, REVIEW_REQUIRED) não escrevem custo nenhum no catálogo.
    if atualizar_cache:
        if status in STATUS_QUE_PRECIFICAM:
            produto.custo_unitario = cnet_brl
        produto.cost_method = metodo
        produto.custo_ref_valor = valor_bruto if valor_bruto is not None else cnet_brl
        produto.custo_ref_moeda = moeda
        produto.custo_ref_data = data_ref or inicio
        produto.custo_ref_documento = documento or fonte
        session.add(produto)
    return nova


def registrar_daune(session: Session, produto: Produto, gross: float, *, fonte: str,
                    documento: Optional[str] = None, data_ref: Optional[date] = None,
                    status: str = StatusCusto.confirmado.value,
                    origem_registro: str = "reconciliacao-daune",
                    notas: Optional[str] = None) -> CustoReferencia:
    """Registra uma referência Daune partindo do preço BRUTO do fornecedor."""
    conta = cnet_nacional(gross)
    return registrar_referencia(
        session, produto, cnet_brl=conta.cnet, metodo=CostMethod.daune_direct.value,
        status=status, fonte=fonte, documento=documento, data_ref=data_ref,
        valor_bruto=gross, memoria=conta.como_dict(), origem_registro=origem_registro,
        notas=notas)


def registrar_special_quoted(session: Session, produto: Produto, *, exw_usd: float,
                             peso_kg: Optional[float], ncm: Optional[str], fonte: str,
                             documento: str, data_ref: date, validade: Optional[date] = None,
                             descricao: Optional[str] = None, construcao: Optional[str] = None,
                             medida: Optional[str] = None, observacao: Optional[str] = None,
                             cnet_brl: Optional[float] = None,
                             origem_registro: str = "cadastro-manual") -> CustoReferencia:
    """Cadastro manual de EXW cotado direto pela KTC (`KTC_SPECIAL_QUOTED`).

    Existe para produto que não tem fórmula industrial aprovada — fitted sheet com elástico,
    chinelo, bordado extraordinário — mas tem preço cotado. Depois de cadastrada, a referência
    entra no **mesmo motor determinístico** de nacionalização e pricing: não há caminho paralelo.

    Nenhum campo de procedência é opcional: sem documento e sem data, o valor não entra.
    """
    if not documento or not data_ref:
        raise ValueError("KTC_SPECIAL_QUOTED exige documento e data de referência")
    if exw_usd is None or exw_usd <= 0:
        raise ValueError("EXW cotado precisa ser positivo")

    memoria = {"exw_usd": exw_usd, "peso_kg": peso_kg, "ncm": ncm, "descricao": descricao,
               "construcao": construcao, "medida": medida, "observacao": observacao,
               "validade": validade.isoformat() if validade else None,
               "caminho": "EXW cotado → nacionalização → CNET"}
    # O EXW fica gravado no produto para a nacionalização usar; o CNET em reais é calculado
    # pelo motor de sempre. Quando o chamador já o tem, passa pronto.
    produto.exw_cotado_usd = exw_usd
    produto.exw_cotado_data = data_ref
    produto.exw_cotado_fonte = fonte
    if peso_kg:
        produto.peso_kg = peso_kg
        produto.peso_fonte = fonte
    if ncm:
        produto.ncm = ncm
    session.add(produto)

    return registrar_referencia(
        session, produto, cnet_brl=cnet_brl if cnet_brl is not None else 0.0,
        metodo=CostMethod.ktc_special_quoted.value,
        status=StatusCusto.confirmado.value, fonte=fonte, documento=documento,
        data_ref=data_ref, valor_bruto=exw_usd, moeda="USD", memoria=memoria,
        origem_registro=origem_registro, notas=observacao,
        atualizar_cache=cnet_brl is not None)


def status_do_produto(session: Session, produto: Produto) -> str:
    """Status canônico do SKU: da versão vigente, se houver.

    Sem versão versionada, devolve `REVIEW_REQUIRED` **sem** tocar no rótulo legado — o
    `custo_confianca` antigo continua onde está, para a reconciliação decidir.
    """
    ref = referencia_vigente(session, produto.id)
    if ref and ref.status_custo:
        return ref.status_custo
    return StatusCusto.review_required.value

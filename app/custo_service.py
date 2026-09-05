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
from decimal import Decimal
from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Session, select

from app.dinheiro import D, para_float
from app.models import (
    CostMethod, CustoReferencia, Fornecedor, Produto, StatusCusto, STATUS_QUE_PRECIFICAM,
)

# --- Daune: créditos de ENTRADA aprovados -----------------------------------------------
# Atenção: este é o crédito da COMPRA. Não tem relação com o ICMS da VENDA, que a Sessão 1
# resolve por operação. Misturar os dois foi o erro que o B-09 registrou.
DAUNE_ICMS_CREDITO = Decimal("0.12")
DAUNE_PIS_COFINS_CREDITO = Decimal("0.0925")


@dataclass
class CustoNacional:
    """CNET de fornecedor nacional a partir do preço BRUTO, com a conta aberta."""
    gross: Decimal
    icms_credito: Decimal
    base_pis_cofins: Decimal
    pis_cofins_credito: Decimal
    cnet: Decimal
    fator: Decimal

    def como_dict(self) -> dict:
        # Memória: vai para JSON e para o snapshot da referência, então sai em float.
        return {
            "gross": para_float(self.gross), "icms_credito": para_float(self.icms_credito),
            "base_pis_cofins": para_float(self.base_pis_cofins),
            "pis_cofins_credito": para_float(self.pis_cofins_credito),
            "cnet": para_float(self.cnet), "fator_efetivo": para_float(self.fator),
            "formula": ("CNET = gross − gross×12% − (gross − gross×12%)×9,25%"),
        }


def cnet_nacional(gross, icms_pct=DAUNE_ICMS_CREDITO,
                  pis_cofins_pct=DAUNE_PIS_COFINS_CREDITO) -> CustoNacional:
    """CUSTO NET a partir do preço bruto do fornecedor nacional.

    Calculado **pelos componentes**, não pelo fator arredondado de 0,7986 — o fator é
    conferência, não fórmula. Parte sempre do bruto da fonte: aplicar o fator sobre um custo já
    persistido produziria um número sem significado, porque esse custo pode ter vindo de outro
    documento.
    """
    gross = D(gross)
    if gross is None or gross <= 0:
        raise ValueError("preço bruto do fornecedor precisa ser positivo")
    icms_pct, pis_cofins_pct = D(icms_pct), D(pis_cofins_pct)
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


def referencia_vigente(session: Session, produto_id: int,
                       quando: Optional[date] = None) -> Optional[CustoReferencia]:
    """A versão em vigor **na data**. `None` quando o SKU não tem referência versionada.

    Resolve por vigência, não pelo flag `vigente` (Sessão 5). A diferença aparece na versão
    com data futura: ela já está gravada e já é a "mais nova não superada", mas **não** é a
    que vale hoje. Antes desta mudança, cadastrar um custo para 01/01/2027 mudava o preço
    imediatamente — que é o oposto de agendar.

    O flag `vigente` continua significando "não foi superada por outra versão" e serve à
    listagem administrativa; quem decide o número do cálculo é a data.
    """
    return referencia_em(session, produto_id, quando or date.today())


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
#: O que identifica economicamente uma referência de custo.
#:
#: Não é só o número. Uma referência responde "de onde veio este valor", e a resposta tem
#: duas metades: **quanto** (CNET, bruto, status) e **com base em quê** (fonte, documento,
#: data da fonte). Duas linhas com o mesmo CNET e evidências diferentes NÃO são a mesma
#: referência — a segunda é uma reconfirmação, e perdê-la apagaria a prova de que alguém
#: conferiu o preço numa data posterior.
CAMPOS_DE_IDENTIDADE = ("cnet_brl", "valor_bruto", "status_custo", "documento",
                        "fonte", "data_ref")


def identidade_economica(*, cnet_brl=None, valor_bruto=None, status_custo=None,
                         documento=None, fonte=None, data_ref=None) -> tuple:
    """A impressão digital de uma referência, normalizada.

    Normaliza para que diferença de **escrita** não vire diferença de **conteúdo**:
    `"100"`, `"100.00"` e `100.0` produzem a mesma entrada; `" tabela A "` e `"tabela A"`
    também. É o que faz "R$ 100,00 vs 100.00" ser no-op de verdade, sem precisar comparar
    strings cruas.
    """
    def numero(v):
        d = D(v)
        return None if d is None else d.normalize()

    def texto(v):
        t = (v or "").strip()
        return t.lower() or None

    return (numero(cnet_brl), numero(valor_bruto), texto(status_custo),
            texto(documento), texto(fonte), data_ref)


def identidade_da_referencia(ref) -> tuple:
    """A identidade econômica de uma versão já gravada.

    `registrar_referencia` compõe `origem_registro` acrescentando a fonte no fim — e quem
    chama já pode ter composto um prefixo próprio (`"admin-ui · fulano@anara"`). Por isso a
    fonte é o **último** segmento, não o segundo: pegar o segundo traria o e-mail do autor
    junto e faria duas gravações da mesma fonte parecerem fontes diferentes.
    """
    origem = ref.origem_registro or ""
    fonte = origem.rsplit(" · ", 1)[-1] if " · " in origem else origem
    return identidade_economica(cnet_brl=ref.cnet_brl, valor_bruto=ref.valor_bruto,
                                status_custo=ref.status_custo, documento=ref.documento,
                                fonte=fonte, data_ref=ref.data_ref)


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
    # não pode encher o histórico de versões no-op. A comparação é pela **identidade
    # econômica** completa — valor, status E evidência —, então mesmo preço com fonte nova
    # continua sendo versão nova: é uma reconfirmação, e perdê-la apagaria a prova de que
    # alguém conferiu o número numa data posterior.
    if anterior is not None and identidade_da_referencia(anterior) == identidade_economica(
            cnet_brl=cnet_brl, valor_bruto=valor_bruto, status_custo=status,
            documento=documento, fonte=fonte, data_ref=data_ref or inicio):
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
    # Fronteira de persistência: a coluna é REAL. O CNET NÃO é quantizado em centavos aqui —
    # é custo interno, e arredondá-lo mudaria o preço de venda de 32 SKUs sem que ninguém
    # tivesse pedido. Só o preço COMERCIAL vira centavo.
    return registrar_referencia(
        session, produto, cnet_brl=para_float(conta.cnet), metodo=CostMethod.daune_direct.value,
        status=status, fonte=fonte, documento=documento, data_ref=data_ref,
        valor_bruto=para_float(D(gross)), memoria=conta.como_dict(),
        origem_registro=origem_registro, notas=notas)


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

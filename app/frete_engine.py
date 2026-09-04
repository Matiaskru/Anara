"""Frete comercial suportado pela Anara — motor puro.

A ideia central, e a razão de este módulo existir separado: **nem todo componente de frete é
um valor fixo**. Alguns são (frete-peso, pedágio, paletização); outros são percentuais sobre o
valor da nota fiscal (ADV, GRIS, fiel depositário), e portanto dependem da própria receita que
o pricing ainda vai formar.

Tratar os dois grupos como um número fixo somado antes do preço congelaria o percentual sobre
um preço preliminar — e o resultado não fecharia com a margem-alvo. Por isso o motor devolve
**dois** números:

    CF  custo fixo do embarque, em R$          → entra no NUMERADOR do pricing
    RV  rate variável sobre a receita, em %    → entra no DENOMINADOR do pricing

e só depois do preço formado é que o RV é recomposto em reais.

Nada aqui conhece banco, FastAPI ou Jinja: recebe as linhas já lidas e devolve o resultado com
a memória de como chegou nele.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from app.dinheiro import D, D0, ZERO, dinheiro, para_float, ratear_centavos

OK = "OK"
ESTIMADO = "FRETE_ESTIMADO"
A_COTAR = "FRETE_A_COTAR"
REVIEW_REQUIRED = "FRETE_REVIEW_REQUIRED"
ICMS_REVIEW_REQUIRED = "FRETE_ICMS_REVIEW_REQUIRED"

APLICA = "APLICA"
NAO_APLICA = "NAO_APLICA"
DESCONHECIDO = "DESCONHECIDO"

FIXO = "FIXO"
POR_PESO = "POR_PESO"
PERCENTUAL_NF = "PERCENTUAL_NF"
PERCENTUAL_FRETE = "PERCENTUAL_FRETE"
POR_HORA = "POR_HORA"

# Status que impedem emitir documento comercial final.
STATUS_BLOQUEIA_EMISSAO = {A_COTAR, REVIEW_REQUIRED, ICMS_REVIEW_REQUIRED}


@dataclass
class ResultadoFrete:
    """Frete de um grupo logístico, decomposto e auditável."""
    status: str = OK
    frete_peso: Optional[Decimal] = None
    cf_logistico: Optional[Decimal] = None       # R$ fixos do embarque
    rv_logistico_pct: Optional[Decimal] = None   # fração da receita
    peso_real_kg: Optional[Decimal] = None
    peso_cubado_kg: Optional[Decimal] = None
    peso_taxado_kg: Optional[Decimal] = None
    peso_taxado_fonte: Optional[str] = None
    regiao_destino: Optional[str] = None
    tarifa_aplicada: Optional[Decimal] = None
    faixa: Optional[str] = None
    minimo_aplicado: bool = False
    componentes_fixos: Dict[str, Decimal] = field(default_factory=dict)
    componentes_percentuais: Dict[str, Decimal] = field(default_factory=dict)
    motivo: Optional[str] = None
    avisos: List[str] = field(default_factory=list)

    @property
    def bloqueado(self) -> bool:
        return self.status in STATUS_BLOQUEIA_EMISSAO

    def valor_rv(self, receita) -> Decimal:
        """RV recomposto em reais — só depois de o preço existir."""
        return dinheiro(D0(receita) * D0(self.rv_logistico_pct))

    def total(self, receita) -> Decimal:
        return D0(self.cf_logistico) + self.valor_rv(receita)

    def como_dict(self) -> dict:
        return {
            "status": self.status, "frete_peso": para_float(self.frete_peso),
            "cf_logistico": para_float(self.cf_logistico),
            "rv_logistico_pct": para_float(self.rv_logistico_pct),
            "peso_real_kg": para_float(self.peso_real_kg),
            "peso_cubado_kg": para_float(self.peso_cubado_kg),
            "peso_taxado_kg": para_float(self.peso_taxado_kg),
            "peso_taxado_fonte": self.peso_taxado_fonte,
            "regiao_destino": self.regiao_destino,
            "tarifa_aplicada": para_float(self.tarifa_aplicada),
            "faixa": self.faixa, "minimo_aplicado": self.minimo_aplicado,
            "componentes_fixos": {k: para_float(v) for k, v in self.componentes_fixos.items()},
            "componentes_percentuais": {k: para_float(v)
                                        for k, v in self.componentes_percentuais.items()},
            "motivo": self.motivo, "avisos": list(self.avisos),
        }


def _bloqueio(status: str, motivo: str, **campos) -> ResultadoFrete:
    return ResultadoFrete(status=status, motivo=motivo, **campos)


# ---------------------------------------------------------------------------
# Peso taxado
# ---------------------------------------------------------------------------
def peso_cubado(volume_m3, fator_kg_m3) -> Optional[Decimal]:
    v, f = D(volume_m3), D(fator_kg_m3)
    if v is None or f is None:
        return None
    return v * f


def resolver_peso_taxado(peso_real, volume_m3, fator_kg_m3,
                         peso_taxado_confirmado=None,
                         fonte_confirmada: Optional[str] = None):
    """Peso que a transportadora cobra. Devolve `(peso, cubado, fonte, motivo_ou_None)`.

    A regra é `max(peso_real, volume × fator)`, e é por isso que **peso real sozinho não
    basta**: uma carga leve e volumosa é cobrada pelo volume. Quando o volume não existe e a
    cubagem poderia mudar o frete, o resultado é bloqueio — não se estima volume a partir das
    dimensões do produto sem premissa logística cadastrada.
    """
    peso_real = D(peso_real)
    peso_taxado_confirmado = D(peso_taxado_confirmado)
    if peso_taxado_confirmado:
        return peso_taxado_confirmado, None, (fonte_confirmada
                                              or "CARRIER_CONFIRMED_TAXABLE_WEIGHT"), None
    cubado = peso_cubado(volume_m3, fator_kg_m3)
    if cubado is None:
        if not peso_real:
            return None, None, None, ("Sem peso real e sem volume: não há base para calcular "
                                      "o frete.")
        return None, None, None, (
            f"Peso real de {peso_real:g} kg conhecido, mas o volume do embarque não. A cubagem "
            f"pode aumentar o peso taxado (fator {fator_kg_m3 or '—'} kg/m³) e mudar o frete — "
            "não se estima volume a partir das dimensões do produto.")
    if not peso_real:
        return cubado, cubado, "SHIPMENT_VOLUME", None
    if cubado > peso_real:
        return cubado, cubado, "SHIPMENT_VOLUME", None
    return peso_real, cubado, "PESO_REAL", None


# ---------------------------------------------------------------------------
# Frete-peso e mínimo
# ---------------------------------------------------------------------------
def faixa_para(faixas: Sequence, regiao: str, peso):
    """Linha da faixa que contém o peso. `None` quando a região não está na tabela."""
    peso = D0(peso)
    candidatas = [f for f in faixas
                  if getattr(f, "ativo", True)
                  and (f.regiao_destino or "").strip().upper() == (regiao or "").strip().upper()]
    for f in sorted(candidatas, key=lambda f: D0(f.peso_de)):
        if peso >= D0(f.peso_de) and (f.peso_ate is None or peso <= D0(f.peso_ate)):
            return f
    return None


def frete_peso_de(tarifa, peso_taxado, unidade: str) -> Decimal:
    """Frete-peso na unidade **declarada na tabela**, nunca suposta.

    `BRL_POR_TONELADA` é o que o cabeçalho da TRANSAL diz — "(P/TONELADA)" — e o exemplo da
    própria planilha confirma: 500 kg × R$ 598/t = R$ 299,00.

    Não quantiza: é componente intermediário. O centavo só aparece no CF do grupo.
    """
    tarifa, peso_taxado = D0(tarifa), D0(peso_taxado)
    if unidade == "BRL_POR_TONELADA":
        return tarifa * peso_taxado / 1000
    if unidade == "BRL_POR_KG":
        return tarifa * peso_taxado
    if unidade == "BRL_POR_EMBARQUE":
        return tarifa
    raise ValueError(f"unidade de tarifa não suportada: {unidade!r}")


# ---------------------------------------------------------------------------
# Resolução do grupo
# ---------------------------------------------------------------------------
def calcular_frete_grupo(tabela, faixas: Sequence, componentes: Sequence,
                         regiao_destino: Optional[str],
                         peso_real_kg=None, volume_m3=None,
                         valor_mercadoria=None,
                         peso_taxado_confirmado=None,
                         adicionais_pedidos: Optional[Dict[str, float]] = None,
                         ref_data=None) -> ResultadoFrete:
    """Frete de um grupo logístico, separado em CF (R$) e RV (% da receita).

    `adicionais_pedidos` são os serviços que alguém **pediu** — agendamento, paletização, horas
    de TDE. Um adicional existir na tabela não o torna devido: nada entra sem ser pedido ou sem
    estar marcado como automático.
    """
    adicionais_pedidos = adicionais_pedidos or {}
    peso_real_kg = D(peso_real_kg)
    valor_mercadoria = D(valor_mercadoria)

    if tabela is None:
        return _bloqueio(A_COTAR, "Não há tabela de frete que atenda a origem logística e a "
                                  "transportadora deste grupo.")
    if getattr(tabela, "valid_to", None) and ref_data and tabela.valid_to < ref_data:
        return _bloqueio(REVIEW_REQUIRED,
                         f"A tabela de frete venceu em {tabela.valid_to:%d/%m/%Y} e não há "
                         "versão vigente no lugar. Tabela vencida não é usada em silêncio.")
    if not regiao_destino:
        return _bloqueio(A_COTAR, "Cidade de destino não está na cobertura desta tabela. Não se "
                                  "escolhe cidade próxima nem tarifa média.")

    # --- peso taxado ---
    peso, cubado, fonte, motivo = resolver_peso_taxado(
        peso_real_kg, volume_m3, getattr(tabela, "fator_cubagem_kg_m3", None),
        peso_taxado_confirmado)
    if peso is None:
        return _bloqueio(REVIEW_REQUIRED, motivo, peso_real_kg=peso_real_kg,
                         peso_cubado_kg=cubado, regiao_destino=regiao_destino)

    base = dict(peso_real_kg=peso_real_kg, peso_cubado_kg=cubado, peso_taxado_kg=peso,
                peso_taxado_fonte=fonte, regiao_destino=regiao_destino)

    # --- frete-peso e mínimo ---
    faixa = faixa_para(faixas, regiao_destino, peso)
    if faixa is None:
        return _bloqueio(A_COTAR, f"A região {regiao_destino} não tem faixa cadastrada nesta "
                                  "tabela.", **base)
    if faixa.tarifa is None:
        return _bloqueio(A_COTAR, f"A região {regiao_destino} consta na tabela **sem tarifa** — "
                                  "não se aproxima por região vizinha.", **base)

    bruto = frete_peso_de(faixa.tarifa, peso, tabela.tarifa_unidade)
    minimo = D0(faixa.frete_minimo)
    # O mínimo substitui APENAS o componente frete-peso. Os demais componentes somam por fora.
    frete_peso = max(bruto, minimo)
    minimo_aplicado = frete_peso > bruto
    base.update(tarifa_aplicada=D(faixa.tarifa), minimo_aplicado=minimo_aplicado,
                faixa=f"{D0(faixa.peso_de):g}–{D0(faixa.peso_ate):g} {tabela.faixa_unidade}"
                      if faixa.peso_ate else f">{D0(faixa.peso_de):g} {tabela.faixa_unidade}")

    fixos: Dict[str, Decimal] = {"frete_peso": frete_peso}
    percentuais: Dict[str, Decimal] = {}
    avisos: List[str] = []
    if minimo_aplicado:
        avisos.append(f"Frete mínimo de R$ {minimo:,.2f} substituiu o frete-peso calculado de "
                      f"R$ {bruto:,.2f}. Os demais componentes somam por fora.")

    # --- componentes ---
    vigentes = [c for c in componentes if getattr(c, "ativo", True)]
    for comp in sorted(vigentes, key=lambda c: c.codigo):
        pedido = comp.codigo in adicionais_pedidos
        if not (comp.automatico or pedido):
            continue                      # existir na tabela não torna o adicional devido
        situacao = (comp.situacao or DESCONHECIDO).strip().upper()
        if situacao == NAO_APLICA:
            continue
        if situacao == DESCONHECIDO:
            return _bloqueio(
                REVIEW_REQUIRED,
                f"A aplicabilidade de '{comp.nome}' ({comp.codigo}) não está resolvida para "
                "esta operação, e o componente é material para o preço. Ausência de decisão "
                "não é 0% — cadastrar APLICA ou NAO_APLICA antes de cotar.", **base)

        if comp.tipo == PERCENTUAL_NF:
            if valor_mercadoria is None:
                return _bloqueio(REVIEW_REQUIRED,
                                 f"'{comp.nome}' incide sobre o valor da NF, e o valor de "
                                 "mercadoria do grupo não está determinado.", **base)
            percentuais[comp.codigo] = D0(comp.valor)
        elif comp.tipo == POR_PESO:
            peso_do_componente, motivo_base = _peso_para_componente(tabela, peso, peso_real_kg)
            if peso_do_componente is None:
                return _bloqueio(REVIEW_REQUIRED, motivo_base, **base)
            fixos[comp.codigo] = D0(comp.valor) * peso_do_componente
        elif comp.tipo == FIXO:
            fixos[comp.codigo] = D0(comp.valor) * D0(adicionais_pedidos.get(comp.codigo, 1) or 1)
        elif comp.tipo == POR_HORA:
            horas = D0(adicionais_pedidos.get(comp.codigo, 0) or 0)
            fixos[comp.codigo] = D0(comp.valor) * horas
        elif comp.tipo == PERCENTUAL_FRETE:
            fixos[comp.codigo] = D0(comp.valor) * frete_peso

    # --- ICMS da prestação ---
    icms = (getattr(tabela, "icms_situacao", DESCONHECIDO) or DESCONHECIDO).strip().upper()
    if icms == DESCONHECIDO:
        return _bloqueio(
            ICMS_REVIEW_REQUIRED,
            "O tratamento do ICMS desta tabela de frete não está provado: o documento diz "
            "'ICMS conforme legislação', há informação de que a tarifa já o inclui, e o exemplo "
            "da própria planilha faz gross-up. Isso muda materialmente o valor — não se escolhe "
            "por aproximação.", **base)
    if icms == APLICA and tabela.icms_pct:
        # tarifa SEM ICMS: precisa do gross-up da alíquota cadastrada, nunca de 12% hardcoded
        antes = sum(fixos.values(), ZERO)
        fator = 1 - D0(tabela.icms_pct)
        if fator <= 0:
            return _bloqueio(REVIEW_REQUIRED, "Alíquota de ICMS do frete inválida.", **base)
        for k in list(fixos):
            fixos[k] = fixos[k] / fator
        avisos.append(f"Gross-up de ICMS de {D0(tabela.icms_pct):.2%} aplicado aos componentes "
                      f"fixos: R$ {antes:,.2f} → R$ {sum(fixos.values(), ZERO):,.2f}.")
        percentuais = {k: v / fator for k, v in percentuais.items()}

    return ResultadoFrete(
        # O CF é a quantia que o pricing vai usar: aqui, e só aqui, ela vira centavos.
        status=OK, frete_peso=frete_peso, cf_logistico=dinheiro(sum(fixos.values(), ZERO)),
        rv_logistico_pct=sum(percentuais.values(), ZERO),
        componentes_fixos=fixos, componentes_percentuais=percentuais,
        avisos=avisos, **base)


def _peso_para_componente(tabela, peso_taxado: float, peso_real: Optional[float]):
    """Qual peso o componente por kg usa. Ambiguidade só bloqueia quando muda o número."""
    base = (getattr(tabela, "pedagio_base", DESCONHECIDO) or DESCONHECIDO).strip().upper()
    if base == "PESO_TAXADO":
        return peso_taxado, None
    if base == "PESO_REAL":
        return peso_real, None
    if peso_real is not None and D0(peso_real) == D0(peso_taxado):
        # real e taxado coincidem: a ambiguidade não altera o resultado
        return peso_taxado, None
    return None, ("A tabela não declara se o pedágio incide sobre o peso real ou sobre o peso "
                  f"taxado, e neste embarque eles diferem ({peso_real:g} kg × {peso_taxado:g} "
                  "kg). A diferença muda o valor — cadastrar `pedagio_base`.")


# ---------------------------------------------------------------------------
# Rateio
# ---------------------------------------------------------------------------
def ratear(valor_total, pesos: Sequence) -> List[Decimal]:
    """Rateio proporcional em **quantias de centavo** que somam exatamente o total.

    Mudou na Sessão 3B. Antes, as parcelas saíam com a precisão cheia da divisão
    (33,333333…) e só a última absorvia o resíduo — a soma fechava, mas nenhuma parcela era
    uma quantia cobrável, e arredondar cada uma na exibição fazia R$ 100,00 virar R$ 99,99 na
    tela. Agora o arredondamento acontece **dentro** do rateio, pelo método do maior resto
    (`app.dinheiro.ratear_centavos`): as parcelas já saem em centavos, o resíduo vai para as
    de maior resto fracionário, e a soma continua exata.

        R$ 100,00 entre 3 itens iguais → 33,34 · 33,33 · 33,33

    Sem base de rateio (peso total zero), divide igualmente — e quem chama registra o
    critério usado.
    """
    return ratear_centavos(valor_total, pesos)

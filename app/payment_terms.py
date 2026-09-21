"""Condição de pagamento e encargo financeiro — uma regra, um lugar.

Reescrito na Onda 1. O encargo de cada condição é uma linha de tabela versionada, e **só isso**.

O que saiu daqui, e por quê: a versão anterior tinha uma "rede" que contava as barras da string
("30/60/90" → 3 parcelas → 4,8%) para condições não cadastradas, e devolvia 1,6% quando o código
vinha vazio. Isso é interpolação — produz um encargo plausível para uma condição que ninguém
aprovou, e o número seguia para o preço. A regra do projeto é explícita: **não interpolar, não
aproximar, não contar barras, não inferir parcela**. Condição que não está cadastrada exige
premissa versionada ou override autorizado; sem isso, o cálculo é bloqueado.

Condições canônicas (todas cadastradas em `CondicaoPagamento`):

    À vista 0% · 30 DD 1,6% · 30/60 3,2% · 30/60/90 4,8% · 30/60/90/120 6,4% ·
    30/60/90/120/150 8,0%

## Sinal / entrada (21/09/2026)

Sinal **não é uma condição opaca** (a linha `SINAL30+30/60/90`, sem taxa, foi desativada): é
uma **composição** de duas coisas que a cotação guarda separadas — o percentual pago à vista
(`percentual_sinal`, fração 0–1) e a condição do **saldo** (`condicao_pagamento`, uma das
canônicas acima). O sinal é recebido à vista e **não carrega encargo**; o encargo da condição do
saldo incide só sobre a parte financiada:

    encargo_efetivo = (1 − percentual_sinal) × encargo_condicao_saldo

    0% + 30/60/90 → 4,8%    30% + 30/60/90 → 3,36%    50% + 30/60 → 1,60%    100% → 0%

Não há desconto adicional por antecipação; o único efeito do sinal é reduzir o encargo. Com
sinal de 100% o saldo é irrelevante (encargo zero, mesmo que a condição do saldo esteja
bloqueada). Com sinal de 0% o resultado é **exatamente** o encargo da condição — a composição
devolve o próprio `EncargoResolvido`, sem tocar nele. `encargo_com_sinal` é a única função que
faz essa conta; o motor recebe o encargo efetivo no mesmo lugar em que sempre recebeu o encargo.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from app.dinheiro import D, ZERO

_MIN_DATA = date.min

OK = "OK"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class EncargoResolvido:
    pct: Decimal
    confirmado: bool
    label: str
    origem: str                 # "tabela" | "override" | "bloqueado"
    status: str = OK
    aviso: Optional[str] = None
    motivo: Optional[str] = None

    @property
    def bloqueado(self) -> bool:
        return self.status == REVIEW_REQUIRED


def _bloqueio(label: str, motivo: str) -> EncargoResolvido:
    return EncargoResolvido(pct=ZERO, confirmado=False, label=label, origem="bloqueado",
                            status=REVIEW_REQUIRED, aviso=motivo, motivo=motivo)


def _vigente_em(c, ref) -> bool:
    """A versão da condição que vale nesta data (Sessão 5).

    Uma condição pode ter mais de uma linha: a que valeu até ontem e a que passa a valer
    amanhã. Linha sem datas — as oito herdadas — vale sempre.
    """
    if ref is None:
        return True
    inicio = getattr(c, "valid_from", None)
    fim = getattr(c, "valid_to", None)
    if inicio is not None and inicio > ref:
        return False
    if fim is not None and fim <= ref:
        return False
    return True


def resolver_encargo(condicoes: Sequence, codigo: str,
                     override_pct: Optional[float] = None,
                     override_motivo: Optional[str] = None,
                     ref=None) -> EncargoResolvido:
    """Encargo financeiro da condição. Só devolve número quando há premissa ou override.

    `override_pct` existe para o caso autorizado — uma condição negociada fora da tabela. Quem
    usa precisa registrar o motivo; sem motivo, o override é recusado.
    """
    codigo = (codigo or "").strip()

    if override_pct is not None:
        if not override_motivo:
            return _bloqueio(codigo or "(sem condição)",
                             "Override de encargo financeiro exige motivo registrado.")
        return EncargoResolvido(D(override_pct), True, codigo or "(override)", "override",
                                aviso=f"Encargo por override autorizado: {override_motivo}")

    if not codigo:
        return _bloqueio("(sem condição)",
                         "Condição de pagamento não informada. O encargo financeiro não é "
                         "presumido — escolha uma condição cadastrada.")

    candidatas = [c for c in condicoes
                  if (c.codigo or "").strip().lower() == codigo.lower()
                  and getattr(c, "ativo", True) and _vigente_em(c, ref)]
    # mais recente primeiro: entre duas versões vigentes, ganha a que começou depois
    candidatas.sort(key=lambda c: (getattr(c, "valid_from", None) is not None,
                                   getattr(c, "valid_from", None) or _MIN_DATA,
                                   getattr(c, "versao", 1), c.id or 0))
    for c in reversed(candidatas):
        if c.encargo_pct is None:
            return _bloqueio(
                c.label,
                f"A condição '{c.label}' está cadastrada mas não tem encargo financeiro "
                "confirmado. Cadastrar a taxa no painel antes de usar comercialmente.")
        return EncargoResolvido(D(c.encargo_pct), bool(c.encargo_confirmado), c.label,
                                "tabela")

    return _bloqueio(
        codigo,
        f"Condição de pagamento '{codigo}' não está cadastrada. O encargo não é estimado "
        "por contagem de parcelas — cadastre a condição com sua taxa, ou use um override "
        "autorizado.")


# ---------------------------------------------------------------------------
# Sinal / entrada — composição, não condição (21/09/2026)
# ---------------------------------------------------------------------------
UM = Decimal("1")
CEM = Decimal("100")


class SinalInvalido(ValueError):
    """Percentual de sinal fora de 0–100% (ou que não é número). Nunca é corrigido em silêncio."""


def validar_percentual_sinal(valor) -> Decimal:
    """Normaliza o percentual de sinal para FRAÇÃO (0–1). Aceita fração ou percentual textual.

    * `None`, `""` → 0 (sem sinal);
    * número/str em 0–1 → fração; em (1, 100] → percentual (30 → 0,30);
    * negativo, > 100, NaN, infinito, texto → `SinalInvalido`.

    O formulário manda percentual ("30"); o modelo guarda fração (0.30). A ambiguidade entre
    "1" (= 1%) e "1" (= 100%) é resolvida a favor de FRAÇÃO: 1 = 100%. Quem digita 1% de sinal
    digita "1" no campo em percentual e o formulário envia "1" — por isso a rota converte
    explicitamente com `percentual=True`, e só o caminho interno usa a fração.
    """
    if valor is None:
        return ZERO
    if isinstance(valor, bool):
        raise SinalInvalido("Percentual de sinal inválido.")
    if isinstance(valor, str):
        texto = valor.strip().replace("%", "").replace(",", ".")
        if not texto:
            return ZERO
        valor = texto
    try:
        d = D(valor)                        # a ponte segura do projeto: float → repr → Decimal
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise SinalInvalido("Percentual de sinal inválido.") from exc
    if d is None or not d.is_finite():
        raise SinalInvalido("Percentual de sinal inválido.")
    if d < 0:
        raise SinalInvalido("O sinal não pode ser negativo.")
    if d > CEM:
        raise SinalInvalido("O sinal não pode passar de 100%.")
    if d > UM:
        d = d / CEM
    return d


def percentual_sinal_do_formulario(texto) -> Decimal:
    """Campo do formulário em PERCENTUAL (0–100) → fração. "30" → 0,30; "1" → 0,01; "100" → 1."""
    if texto is None:
        return ZERO
    if isinstance(texto, str):
        t = texto.strip().replace("%", "").replace(",", ".")
        if not t:
            return ZERO
    else:
        t = texto
    try:
        d = D(t)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise SinalInvalido("Percentual de sinal inválido.") from exc
    if d is None or not d.is_finite():
        raise SinalInvalido("Percentual de sinal inválido.")
    if d < 0:
        raise SinalInvalido("O sinal não pode ser negativo.")
    if d > CEM:
        raise SinalInvalido("O sinal não pode passar de 100%.")
    return d / CEM


def _pct_texto(fracao: Decimal) -> str:
    """0,30 → "30"; 0,335 → "33,5" (sem zeros à direita, vírgula decimal)."""
    p = (fracao * CEM).normalize()
    texto = format(p, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return (texto or "0").replace(".", ",")


def rotulo_condicao(percentual_sinal, label_saldo: str) -> str:
    """Texto comercial da condição: "30% de sinal + 70% em 30/60/90 dias".

    Sem sinal, é o rótulo da condição; com 100%, "100% à vista (sinal)". É o que vai para o
    PDF, para o snapshot e para a tela — nunca o encargo.
    """
    sinal = validar_percentual_sinal(percentual_sinal)
    if sinal == 0:
        return label_saldo or ""
    if sinal >= UM:
        return "100% à vista (sinal)"
    return f"{_pct_texto(sinal)}% de sinal + {_pct_texto(UM - sinal)}% em {label_saldo or '—'}"


def encargo_com_sinal(encargo_saldo: EncargoResolvido, percentual_sinal) -> EncargoResolvido:
    """Encargo efetivo da composição sinal + saldo. **A única fórmula do sinal.**

        encargo_efetivo = (1 − percentual_sinal) × encargo_condicao_saldo

    * sinal 0 → devolve `encargo_saldo` **tal qual** (mesmo objeto: comportamento tradicional);
    * sinal 100% → encargo zero, confirmado, sem bloqueio — o saldo é irrelevante;
    * 0 < sinal < 100% com saldo bloqueado → continua bloqueado (a parte financiada precisa de
      uma condição cadastrada); com saldo resolvido → proporção, mantendo `confirmado` e o aviso.
    """
    sinal = validar_percentual_sinal(percentual_sinal)
    if sinal == 0:
        return encargo_saldo
    if sinal >= UM:
        return EncargoResolvido(pct=ZERO, confirmado=True, label=rotulo_condicao(UM, ""),
                                origem="sinal", status=OK, aviso=None, motivo=None)
    if encargo_saldo.bloqueado:
        return EncargoResolvido(pct=ZERO, confirmado=False,
                                label=rotulo_condicao(sinal, encargo_saldo.label),
                                origem="bloqueado", status=REVIEW_REQUIRED,
                                aviso=encargo_saldo.aviso, motivo=encargo_saldo.motivo)
    return EncargoResolvido(pct=(UM - sinal) * D(encargo_saldo.pct),
                            confirmado=encargo_saldo.confirmado,
                            label=rotulo_condicao(sinal, encargo_saldo.label),
                            origem="sinal", status=OK, aviso=encargo_saldo.aviso, motivo=None)

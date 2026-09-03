"""Regras fiscais da venda — o único lugar que decide qual ICMS entra no preço.

Reescrito na Onda 1. Duas mudanças estruturais em relação ao motor anterior:

1. **O fiscal é resolvido por ITEM, não por cotação.** Uma cotação pode conter KTC (importada),
   Daune e Decor (nacionais) e produzir três alíquotas diferentes no mesmo documento. O que
   decide não é o cabeçalho da cotação: é a combinação origem fiscal × destino × natureza da
   mercadoria × contribuinte × finalidade.

2. **Não existe fallback.** O motor antigo devolvia 18% quando não achava o cenário, e 4% fixo
   quando a UF não estava na tabela. Isso produzia número plausível e errado. Agora, cenário que
   não se resolve devolve `REVIEW_REQUIRED` com o motivo escrito — e quem chama tem que tratar.

O que **não** mudou, de propósito:

* a **carga final** do DIFAL continua sendo usada como está, sem recálculo por base simples,
  base dupla ou FEM;
* **contribuinte nunca se infere do estado**;
* origem **fiscal** é atributo da operação e não se confunde com origem logística.

As funções aqui são puras: recebem as linhas já lidas do banco e devolvem um resultado. Não
conhecem sessão, FastAPI nem Jinja.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# Consumidor final é DERIVADO da finalidade. Não é um valor do enum de finalidade.
FINALIDADES_CONSUMIDOR_FINAL = {"USO_CONSUMO", "ATIVO_IMOBILIZADO"}
FINALIDADES_VALIDAS = {"REVENDA", "INDUSTRIALIZACAO", "USO_CONSUMO", "ATIVO_IMOBILIZADO"}

OK = "OK"
REVIEW_REQUIRED = "REVIEW_REQUIRED"

NAO_APLICAVEL = "NAO_APLICAVEL"
REMETENTE = "REMETENTE"
DESTINATARIO = "DESTINATARIO"


@dataclass
class ResultadoFiscal:
    """Resolução fiscal de um item, com a memória de como se chegou nela.

    `icms_pct` é a carga que **efetivamente reduz a receita da Anara** — é o número que entra no
    waterfall. Quando o DIFAL é de responsabilidade do destinatário, ele aparece em `difal_pct`
    para a memória interna, mas **não** está somado em `icms_pct`.
    """
    status: str = OK
    icms_pct: Optional[float] = None
    regra: str = ""
    fonte: Optional[str] = None
    origem_fiscal: Optional[str] = None
    uf_origem: Optional[str] = None
    uf_destino: Optional[str] = None
    contribuinte: Optional[bool] = None
    finalidade: Optional[str] = None
    consumidor_final: Optional[bool] = None
    difal_pct: Optional[float] = None
    difal_responsavel: str = NAO_APLICAVEL
    difal_entra_na_margem: bool = False
    motivo: Optional[str] = None
    avisos: List[str] = field(default_factory=list)

    @property
    def bloqueado(self) -> bool:
        return self.status == REVIEW_REQUIRED

    def como_dict(self) -> dict:
        return {
            "status": self.status, "icms_pct": self.icms_pct, "regra": self.regra,
            "fonte": self.fonte, "origem_fiscal": self.origem_fiscal,
            "uf_origem": self.uf_origem, "uf_destino": self.uf_destino,
            "contribuinte": self.contribuinte, "finalidade": self.finalidade,
            "consumidor_final": self.consumidor_final, "difal_pct": self.difal_pct,
            "difal_responsavel": self.difal_responsavel,
            "difal_entra_na_margem": self.difal_entra_na_margem,
            "motivo": self.motivo, "avisos": list(self.avisos),
        }


def _bloqueio(motivo: str, **campos) -> ResultadoFiscal:
    return ResultadoFiscal(status=REVIEW_REQUIRED, motivo=motivo, **campos)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def normalizar_uf(estados: Sequence, valor: Optional[str]) -> Optional[str]:
    """Aceita "SP", "sp", "São Paulo" e devolve sempre a sigla. Desconhecido → None."""
    alvo = (valor or "").strip().lower()
    if not alvo:
        return None
    if len(alvo) == 2:
        for e in estados:
            if (e.uf or "").strip().lower() == alvo:
                return e.uf.strip().upper()
        return None
    for e in estados:
        if (e.estado or "").strip().lower() == alvo:
            return (e.uf or "").strip().upper()
    return None


def linha_estado(estados: Sequence, uf: Optional[str]):
    alvo = (uf or "").strip().lower()
    for e in estados:
        if not getattr(e, "ativo", True):
            continue
        if (e.uf or "").strip().lower() == alvo:
            return e
    return None


def consumidor_final_de(finalidade: Optional[str]) -> Optional[bool]:
    """DERIVAÇÃO — a única. Consumidor final nunca é digitado; é consequência da finalidade."""
    if not finalidade:
        return None
    return (finalidade or "").strip().upper() in FINALIDADES_CONSUMIDOR_FINAL


def _vigente(linha, ref_data=None) -> bool:
    if not getattr(linha, "ativo", True):
        return False
    if ref_data is None:
        return True
    if getattr(linha, "valid_from", None) and linha.valid_from > ref_data:
        return False
    if getattr(linha, "valid_to", None) and linha.valid_to < ref_data:
        return False
    return True


def resolver_aliquota_interestadual(linhas: Sequence, uf_origem: str, uf_destino: str,
                                    origem_fiscal: str, ncm: Optional[str] = None,
                                    produto_id: Optional[int] = None, ref_data=None):
    """Acha a alíquota interestadual aplicável. Devolve `(linha, None)` ou `(None, motivo)`.

    A exceção vem antes da regra geral: linha por produto vence linha por NCM, que vence a linha
    do par de UF. É assim que uma mercadoria importada que **não** se enquadre nos 4% pode ser
    tratada sem alterar código.
    """
    candidatas = [
        r for r in linhas
        if _vigente(r, ref_data)
        and (r.uf_origem or "").strip().upper() == (uf_origem or "").strip().upper()
        and (r.uf_destino or "").strip().upper() == (uf_destino or "").strip().upper()
        and (r.origem_fiscal or "").strip().upper() == (origem_fiscal or "").strip().upper()
    ]
    if not candidatas:
        return None, (f"Não há alíquota interestadual cadastrada para "
                      f"{uf_origem}→{uf_destino} com mercadoria {origem_fiscal}.")

    especificas = [r for r in candidatas if r.produto_id and r.produto_id == produto_id]
    if not especificas and ncm:
        especificas = [r for r in candidatas
                       if r.ncm and r.ncm.strip() == (ncm or "").strip()]
    escolhidas = especificas or [r for r in candidatas if not r.produto_id and not r.ncm]
    if not escolhidas:
        return None, (f"As linhas cadastradas para {uf_origem}→{uf_destino} ({origem_fiscal}) "
                      "são exceções por produto/NCM e nenhuma se aplica a este item.")
    return sorted(escolhidas, key=lambda r: (r.prioridade, r.id or 0))[0], None


# ---------------------------------------------------------------------------
# Resolução principal — por item
# ---------------------------------------------------------------------------
def resolver_fiscal_item(regras_explicitas: Sequence, estados: Sequence,
                         aliquotas_interestaduais: Sequence,
                         uf_origem: Optional[str], uf_destino: Optional[str],
                         origem_fiscal: Optional[str], contribuinte: Optional[bool],
                         finalidade: Optional[str], ncm: Optional[str] = None,
                         produto_id: Optional[int] = None, ref_data=None) -> ResultadoFiscal:
    """Resolve a carga de ICMS de **um item**. Sem fallback: o que não resolve, bloqueia.

    Ordem:

    1. variáveis obrigatórias presentes e válidas;
    2. regra explícita cadastrada (ex.: dentro de SP é 18%, contribuinte ou não);
    3. mesmo estado → alíquota interna do destino;
    4. interestadual + contribuinte → tabela de alíquota interestadual por natureza;
    5. interestadual + não contribuinte → carga final do destino, usada como está.
    """
    contexto = dict(uf_origem=uf_origem, uf_destino=uf_destino, origem_fiscal=origem_fiscal,
                    contribuinte=contribuinte, finalidade=finalidade)

    # --- 1. variáveis obrigatórias ---
    if not uf_origem:
        return _bloqueio("Origem fiscal da operação não pôde ser determinada. Origem logística "
                         "não serve como origem fiscal.", **contexto)
    if not uf_destino:
        return _bloqueio("UF de destino não informada ou desconhecida na tabela de estados.",
                         **contexto)
    if contribuinte is None:
        return _bloqueio("Não está definido se o cliente é contribuinte de ICMS. Não se infere "
                         "isso pelo estado.", **contexto)
    if not finalidade:
        return _bloqueio("Finalidade da operação não definida.", **contexto)
    if (finalidade or "").strip().upper() not in FINALIDADES_VALIDAS:
        return _bloqueio(f"Finalidade '{finalidade}' não é válida.", **contexto)
    if not origem_fiscal:
        return _bloqueio("Natureza fiscal da mercadoria (nacional ou importada) não definida "
                         "para este item.", **contexto)

    finalidade = finalidade.strip().upper()
    origem_fiscal = origem_fiscal.strip().upper()
    uf_origem = uf_origem.strip().upper()
    uf_destino = uf_destino.strip().upper()
    consumidor_final = consumidor_final_de(finalidade)
    base = dict(contexto, uf_origem=uf_origem, uf_destino=uf_destino,
                origem_fiscal=origem_fiscal, finalidade=finalidade,
                consumidor_final=consumidor_final)

    destino = linha_estado(estados, uf_destino)
    if destino is None:
        return _bloqueio(f"UF de destino {uf_destino} não está na tabela de estados fiscais.",
                         **base)

    # --- 2. regra explícita ---
    explicitas = [r for r in regras_explicitas
                  if _vigente(r, ref_data)
                  and normalizar_uf(estados, r.origem) == uf_origem
                  and normalizar_uf(estados, r.destino) == uf_destino
                  and bool(r.contribuinte) == bool(contribuinte)]
    if explicitas:
        melhor = sorted(explicitas, key=lambda r: (getattr(r, "prioridade", 100), r.id or 0))[0]
        return ResultadoFiscal(
            icms_pct=float(melhor.icms_venda), regra=melhor.regra,
            fonte=f"RegraFiscalVenda#{melhor.id}", difal_responsavel=NAO_APLICAVEL, **base)

    # --- 3. mesmo estado ---
    if uf_origem == uf_destino:
        return ResultadoFiscal(
            icms_pct=float(destino.aliquota_interna),
            regra=f"Intraestadual {uf_destino} — alíquota interna, sem DIFAL",
            fonte=f"EstadoFiscal#{destino.id}.aliquota_interna",
            difal_responsavel=NAO_APLICAVEL, **base)

    # --- 4 e 5. interestadual ---
    linha, motivo = resolver_aliquota_interestadual(
        aliquotas_interestaduais, uf_origem, uf_destino, origem_fiscal, ncm, produto_id, ref_data)
    if linha is None:
        return _bloqueio(motivo, **base)

    interestadual = float(linha.aliquota)
    carga_final = float(destino.carga_final)
    difal = max(carga_final - interestadual, 0.0)

    if contribuinte:
        if not consumidor_final:
            # Revenda ou industrialização: o destinatário credita e segue a cadeia.
            return ResultadoFiscal(
                icms_pct=interestadual,
                regra=(f"Interestadual {uf_origem}→{uf_destino}, mercadoria {origem_fiscal.lower()}, "
                       f"contribuinte para {finalidade.lower().replace('_', '/')} — "
                       f"{interestadual:.2%}, sem DIFAL"),
                fonte=f"AliquotaInterestadual#{linha.id}",
                difal_pct=None, difal_responsavel=NAO_APLICAVEL, **base)
        # Contribuinte que consome: há DIFAL, e quem recolhe é o destinatário.
        # Registrado na memória, **fora** do waterfall da Anara.
        return ResultadoFiscal(
            icms_pct=interestadual,
            regra=(f"Interestadual {uf_origem}→{uf_destino}, mercadoria {origem_fiscal.lower()}, "
                   f"contribuinte consumidor final — {interestadual:.2%} destacado; DIFAL de "
                   f"{difal:.2%} recolhido pelo destinatário"),
            fonte=f"AliquotaInterestadual#{linha.id} + EstadoFiscal#{destino.id}.carga_final",
            difal_pct=difal, difal_responsavel=DESTINATARIO, difal_entra_na_margem=False, **base)

    # Não contribuinte: consumidor final por natureza; o remetente recolhe o DIFAL, e ele
    # entra no waterfall. A carga final do destino já embute o diferencial e é usada como está.
    return ResultadoFiscal(
        icms_pct=carga_final,
        regra=(f"Interestadual {uf_origem}→{uf_destino} para não contribuinte — carga final de "
               f"{uf_destino} ({carga_final:.2%}), coluna 'Carga Final' usada como está; "
               f"DIFAL de {difal:.2%} recolhido pelo remetente"),
        fonte=f"EstadoFiscal#{destino.id}.carga_final",
        difal_pct=difal, difal_responsavel=REMETENTE, difal_entra_na_margem=True, **base)


# ---------------------------------------------------------------------------
# Legado — preservado para reproduzir cotação histórica
# ---------------------------------------------------------------------------
def resolver_icms(cenarios: List[dict], origem: str, destino: str, contribuinte: bool,
                  fallback: float = 0.18) -> Tuple[float, str]:
    """Resolução pelo snapshot antigo (lista de dicts salva em `BaseImportacao`).

    **Só existe para reproduzir cotação já emitida exatamente como foi emitida.** Cotação nova
    passa por `resolver_fiscal_item`. O fallback aqui é parte do retrato histórico — não é
    comportamento aceitável para cálculo novo.
    """
    if not origem or not destino:
        return fallback, "Origem/destino não informados — ICMS padrão aplicado (snapshot legado)"
    for cen in cenarios:
        if (cen.get("origem") == origem and cen.get("destino") == destino
                and bool(cen.get("contribuinte")) == bool(contribuinte)):
            return float(cen["icms_venda"]), cen.get("regra", "")
    return fallback, ("Cenário não encontrado no snapshot da base — ICMS padrão aplicado "
                      "(snapshot legado)")

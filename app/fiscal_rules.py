"""Regras fiscais da venda — o único lugar que decide qual ICMS entra no preço.

Reescrito na Onda 1. Duas mudanças estruturais em relação ao motor anterior:

1. **O fiscal é resolvido por ITEM, não por cotação.** Uma cotação pode conter KTC (importada),
   Daune e Decor (nacionais) e produzir três alíquotas diferentes no mesmo documento. O que
   decide não é o cabeçalho da cotação: é a combinação origem fiscal × destino × natureza da
   mercadoria × contribuinte × finalidade.

2. **Não existe fallback.** O motor antigo devolvia 18% quando não achava o cenário, e 4% fixo
   quando a UF não estava na tabela. Isso produzia número plausível e errado. Agora, cenário que
   não se resolve devolve `REVIEW_REQUIRED` com o motivo escrito — e quem chama tem que tratar.

3. **Todo percentual está sobre a MESMA base: o preço final.** É o que o waterfall resolve.
   A coluna `EstadoFiscal.carga_final` **saiu do motor**: ela é o DIFAL convertido para uma base
   anterior à inclusão do ICMS de destino — `(interna − 4%)/(1 − interna)` — e portanto não é um
   percentual da receita final nem pode ser somado a um. Ela fica na tabela para rastreabilidade
   da apuração histórica, e nada mais.

O que **não** mudou, de propósito:

* **contribuinte nunca se infere do estado**;
* origem **fiscal** é atributo da operação e não se confunde com origem logística;
* FCP/FEM **não se infere pela UF** — exige regra cadastrada por produto/NCM/operação.

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
    icms_pct: Optional[float] = None            # TOTAL suportado pela Anara sobre a receita
    aliquota_interestadual: Optional[float] = None
    aliquota_interna_destino: Optional[float] = None
    fcp_pct: Optional[float] = None
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
            "status": self.status, "icms_pct": self.icms_pct,
            "aliquota_interestadual": self.aliquota_interestadual,
            "aliquota_interna_destino": self.aliquota_interna_destino,
            "fcp_pct": self.fcp_pct, "regra": self.regra,
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


def resolver_fcp(regras_fcp: Sequence, uf_destino: str, ncm: Optional[str] = None,
                 produto_id: Optional[int] = None, familia: Optional[str] = None,
                 ref_data=None):
    """FCP/FECP da operação. Devolve `(pct_ou_None, motivo_ou_None, texto)`.

    **Ausência de regra NÃO é zero.** Zero é uma afirmação — "esta operação não sofre FCP" — e
    afirmar isso sem fonte subestima o preço em 2 pontos numa UF que cobra. Por isso a regra tem
    três situações, e a falta de linha cai em DESCONHECIDO:

    * `APLICA`       → incide, com a alíquota da linha;
    * `NAO_APLICA`   → comprovadamente não incide; devolve 0,0 com a fonte;
    * `DESCONHECIDO` → devolve `None`, e quem chama decide se bloqueia.

    Só o chamador sabe se o FCP é material para aquele cenário: quando o DIFAL é do
    destinatário, um FCP não resolvido não afeta a margem da Anara e não precisa bloquear.
    """
    candidatas = [r for r in regras_fcp
                  if _vigente(r, ref_data)
                  and (r.uf_destino or "").strip().upper() == (uf_destino or "").strip().upper()]

    def cobre(r):
        if r.produto_id:
            return r.produto_id == produto_id
        if r.ncm:
            return (r.ncm or "").strip() == (ncm or "").strip()
        if r.familia:
            return (r.familia or "").strip().lower() == (familia or "").strip().lower()
        return True          # linha geral da UF, cadastrada deliberadamente

    aplicaveis = [r for r in candidatas if cobre(r)]
    if not aplicaveis:
        return None, (f"A aplicabilidade do FCP/FECP em {uf_destino} não está resolvida para "
                      "este item. Ausência de regra não é 0% — cadastrar a linha de FCP "
                      f"(APLICA ou NAO_APLICA) para {uf_destino} antes de cotar."), ""

    escolhida = sorted(aplicaveis, key=lambda r: (r.prioridade, r.id or 0))[0]
    situacao = (getattr(escolhida, "situacao", "") or "DESCONHECIDO").strip().upper()
    if situacao == "NAO_APLICA":
        return 0.0, None, (f"FCP de {uf_destino}: não se aplica a este item "
                           f"({escolhida.fonte or escolhida.regra or 'fonte não registrada'})")
    if situacao == "APLICA":
        return float(escolhida.fcp_pct), None, (
            f"FCP de {uf_destino}: {float(escolhida.fcp_pct):.2%} "
            f"({escolhida.regra or 'cadastrado'})")
    return None, (f"A regra de FCP de {uf_destino} que alcança este item está marcada "
                  f"DESCONHECIDO ({escolhida.regra or 'sem detalhe'}). Não se assume 0%."), ""


def icms_interno_base_de(destino):
    """Base interna do destino, SEM FCP. Devolve `(pct_ou_None, motivo_ou_None)`.

    A coluna `aliquota_interna` não tem significado único entre as UFs: para o RJ ela guarda
    22%, que é a base de 20% **mais** o FECP de 2%. Somar FCP sobre ela produziria 24%. Por
    isso a base tem coluna própria, e o que não foi determinado bloqueia.
    """
    base = getattr(destino, "icms_interno_base", None)
    if base is not None:
        return float(base), None
    if getattr(destino, "interna_inclui_fcp", None) is False:
        return float(destino.aliquota_interna), None
    return None, (f"A alíquota interna de {destino.uf} ({destino.aliquota_interna:.2%}) não tem "
                  "semântica determinada: não se sabe se já inclui FCP/FECP. Cadastrar "
                  "`icms_interno_base` antes de cotar este destino.")


# ---------------------------------------------------------------------------
# Resolução principal — por item
# ---------------------------------------------------------------------------
def resolver_fiscal_item(regras_explicitas: Sequence, estados: Sequence,
                         aliquotas_interestaduais: Sequence,
                         uf_origem: Optional[str], uf_destino: Optional[str],
                         origem_fiscal: Optional[str], contribuinte: Optional[bool],
                         finalidade: Optional[str], ncm: Optional[str] = None,
                         produto_id: Optional[int] = None, familia: Optional[str] = None,
                         regras_fcp: Sequence = (), ref_data=None) -> ResultadoFiscal:
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
            aliquota_interna_destino=float(destino.aliquota_interna), fcp_pct=0.0,
            fonte=f"RegraFiscalVenda#{melhor.id}", difal_responsavel=NAO_APLICAVEL, **base)

    # --- 3. mesmo estado ---
    if uf_origem == uf_destino:
        return ResultadoFiscal(
            icms_pct=float(destino.aliquota_interna),
            aliquota_interna_destino=float(destino.aliquota_interna), fcp_pct=0.0,
            regra=f"Intraestadual {uf_destino} — alíquota interna {destino.aliquota_interna:.2%}, "
                  "sem DIFAL interestadual",
            fonte=f"EstadoFiscal#{destino.id}.aliquota_interna",
            difal_responsavel=NAO_APLICAVEL, **base)

    # --- 4 e 5. interestadual ---
    linha, motivo = resolver_aliquota_interestadual(
        aliquotas_interestaduais, uf_origem, uf_destino, origem_fiscal, ncm, produto_id, ref_data)
    if linha is None:
        return _bloqueio(motivo, **base)

    interestadual = float(linha.aliquota)

    # A base interna do destino é o ICMS **sem** FCP. O DIFAL sobre a receita final é a
    # diferença entre ela e a interestadual da operação; o FCP soma por fora. Usar a coluna
    # `aliquota_interna` aqui contaria o FECP duas vezes onde ela já o embute (RJ: 22%).
    base_interna, motivo_base = icms_interno_base_de(destino)
    fcp, motivo_fcp, texto_fcp = resolver_fcp(regras_fcp, uf_destino, ncm, produto_id,
                                              familia, ref_data)
    difal = max(base_interna - interestadual, 0.0) if base_interna is not None else None
    comum = dict(aliquota_interestadual=interestadual, aliquota_interna_destino=base_interna)

    if contribuinte and not consumidor_final:
        # Revenda ou industrialização: o destinatário credita e segue a cadeia. Sem DIFAL de
        # consumidor final, e o FCP dele não é ônus da Anara.
        return ResultadoFiscal(
            icms_pct=interestadual, fcp_pct=0.0,
            regra=(f"Interestadual {uf_origem}→{uf_destino}, mercadoria {origem_fiscal.lower()}, "
                   f"contribuinte para {finalidade.lower().replace('_', '/')} — "
                   f"{interestadual:.2%}, sem DIFAL"),
            fonte=f"AliquotaInterestadual#{linha.id}",
            difal_pct=None, difal_responsavel=NAO_APLICAVEL, **comum, **base)

    if contribuinte:
        # Contribuinte que consome: há DIFAL, e quem recolhe é o destinatário. O preço da Anara
        # não depende dele nem do FCP — então base interna ou FCP não resolvidos **não
        # bloqueiam**: o DIFAL fica em branco, com o motivo, e o preço sai.
        detalhe = (f"DIFAL de {difal:.2%} ({base_interna:.2%} − {interestadual:.2%})"
                   if difal is not None else "DIFAL de valor não determinável")
        r = ResultadoFiscal(
            icms_pct=interestadual, fcp_pct=0.0,
            regra=(f"Interestadual {uf_origem}→{uf_destino}, mercadoria {origem_fiscal.lower()}, "
                   f"contribuinte consumidor final — {interestadual:.2%} destacado; {detalhe} "
                   "recolhido pelo destinatário"),
            fonte=f"AliquotaInterestadual#{linha.id}",
            difal_pct=difal, difal_responsavel=DESTINATARIO, difal_entra_na_margem=False,
            **comum, **base)
        r.avisos.append("DIFAL e FCP são do destinatário — não reduzem a margem da Anara.")
        for m in (motivo_base, motivo_fcp):
            if m:
                r.avisos.append(m)
        return r

    # Não contribuinte: consumidor final por natureza. O remetente recolhe o DIFAL e o FCP, e
    # os dois entram no waterfall. Aqui ambos são materiais para o preço — então base interna
    # não determinada ou FCP desconhecido **bloqueiam**. Zero por omissão subestimaria a carga.
    if base_interna is None:
        return _bloqueio(motivo_base, **comum, **base)
    if fcp is None:
        return _bloqueio(motivo_fcp, **comum, **base)

    total = interestadual + difal + fcp
    r = ResultadoFiscal(
        icms_pct=total, fcp_pct=fcp,
        regra=(f"Interestadual {uf_origem}→{uf_destino} para não contribuinte — "
               f"{interestadual:.2%} de ICMS de origem + {difal:.2%} de DIFAL "
               f"({base_interna:.2%} − {interestadual:.2%}) recolhido pelo remetente"
               + (f" + {fcp:.2%} de FCP" if fcp else "")
               + f" = {total:.2%} sobre a receita"),
        fonte=f"AliquotaInterestadual#{linha.id} + EstadoFiscal#{destino.id}.icms_interno_base",
        difal_pct=difal, difal_responsavel=REMETENTE, difal_entra_na_margem=True,
        **comum, **base)
    if texto_fcp:
        r.avisos.append(texto_fcp)
    return r


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

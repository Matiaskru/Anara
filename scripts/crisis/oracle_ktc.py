"""Oracle independente do motor industrial KTC + nacionalização (auditoria de crise, seção 8).

Implementação **do zero**, só com `decimal` e `sqlite3`, a partir das fontes de negócio:

* `referencia/KTC_Pricing_Master_Simple.xlsx`, aba "KTC Actual Pricing Model" (E..L):
  hemming (+2+2 cm) → shrinkage (×1,03 / ×1,05) → consumo (m²) → waste (÷0,97) → tecido
  (× USD/m²) → + CMT → "II 1%" = perda de 2ª qualidade (÷0,99) → margem KTC (÷0,85) → EXW;
* `referencia/SUPER_PROMPT_ANARA_v2.txt` §14–§18 (materiais, CMT, fronha §18, toalha por
  kg = EXW final) e §23 (nacionalização: EXW + frete USD/kg × peso + II + outras → NET USD
  → × FX → NET BRL);
* base do I.I.: o SUPER_PROMPT §23 não fixa a base explicitamente; a fonte operacional
  (`Sistema de preços Anara novo (whatsapp).xlsx`, 01_CUSTOS, reforma ago/2026) define
  `Base_Aduaneira_USD = Preço_USD + Frete_USD_un` e `II_USD = Base × II%`. O oracle usa
  essa base (EXW + frete) e documenta a escolha.

Este módulo NÃO importa `app.ktc_engine` nem `app.nationalization`. As premissas são lidas
direto da CÓPIA do banco (sqlite3 puro), com a mesma regra de vigência das tabelas
versionadas (ativo, valid_from ≤ hoje ≤ valid_to).

Uso como script: compara o oracle com o motor oficial (`app.pricing_service`) para todos os
produtos KTC calculáveis e grava `AUDIT/oracle_ktc_comparacao.csv`. Só aí `app.*` é
importado — pela camada oficial, nunca pelo oracle.
"""
import csv
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, getcontext
from typing import Dict, List, Optional

getcontext().prec = 34

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

# ---------------------------------------------------------------------------
# Conversão segura (float → texto → Decimal; nunca Decimal(float))
# ---------------------------------------------------------------------------
def dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return Decimal(repr(v))
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# 1. Motor industrial — funções puras
# ---------------------------------------------------------------------------
@dataclass
class EtapasOracle:
    """Waterfall do oracle, etapa a etapa, para o CSV de comparação."""
    largura_hem: Optional[Decimal] = None
    comprimento_hem: Optional[Decimal] = None
    largura_shr: Optional[Decimal] = None
    comprimento_shr: Optional[Decimal] = None
    area_painel_m2: Optional[Decimal] = None
    consumo_bruto_m2: Optional[Decimal] = None
    consumo_waste_m2: Optional[Decimal] = None
    tecido_usd: Optional[Decimal] = None
    producao_usd: Optional[Decimal] = None
    qualidade_usd: Optional[Decimal] = None
    exw_usd: Optional[Decimal] = None
    peso_teorico_kg: Optional[Decimal] = None
    motivo: Optional[str] = None
    calculavel: bool = False


def exw_tecido_plano(largura_cm, comprimento_cm, *, hem_w_cm, hem_l_cm, shrinkage, waste,
                     price_usd_m2, cmt_usd, other_usd, quality_allowance, ktc_margin,
                     paineis=1) -> EtapasOracle:
    """Cadeia da KTC (colunas E..L): hem → shrink → consumo → waste → tecido → CMT → 2ª → margem."""
    e = EtapasOracle()
    faltas = [n for n, v in (("largura", largura_cm), ("comprimento", comprimento_cm),
                             ("hem_w", hem_w_cm), ("hem_l", hem_l_cm), ("shrinkage", shrinkage),
                             ("waste", waste), ("price_m2", price_usd_m2), ("cmt", cmt_usd),
                             ("quality", quality_allowance), ("margin", ktc_margin))
              if v is None]
    if faltas:
        e.motivo = "faltam: " + ", ".join(faltas)
        return e
    W, L = dec(largura_cm), dec(comprimento_cm)
    e.largura_hem = W + dec(hem_w_cm)
    e.comprimento_hem = L + dec(hem_l_cm)
    e.largura_shr = e.largura_hem * (1 + dec(shrinkage))
    e.comprimento_shr = e.comprimento_hem * (1 + dec(shrinkage))
    e.area_painel_m2 = e.largura_shr * e.comprimento_shr / Decimal(10000)
    e.consumo_bruto_m2 = e.area_painel_m2 * Decimal(int(paineis))
    e.consumo_waste_m2 = e.consumo_bruto_m2 / (1 - dec(waste))
    e.tecido_usd = e.consumo_waste_m2 * dec(price_usd_m2)
    e.producao_usd = e.tecido_usd + dec(cmt_usd) + (dec(other_usd) or Decimal(0))
    e.qualidade_usd = e.producao_usd / (1 - dec(quality_allowance))
    e.exw_usd = e.qualidade_usd / (1 - dec(ktc_margin))
    e.calculavel = True
    return e


def corte_fronha_s18(W, L, F, abas: int, A=Decimal(5)):
    """§18 do SUPER_PROMPT: (W_cut, L_cut) por número de abas."""
    W, L, F, A = dec(W), dec(L), dec(F), dec(A)
    if abas == 0:
        return W + 4, 2 * L + F + 5
    if abas == 2:
        return W + 4, 2 * L + F + 5 + 4 * A
    if abas == 3:
        return W + 4 + A, 2 * L + F + 5 + 4 * A
    if abas == 4:
        return W + 4 + 2 * A, 2 * L + F + 5 + 4 * A
    raise ValueError(f"{abas} abas não está no §18")


def exw_fronha(largura_cm, comprimento_cm, *, flap_cm, abas, festone, shrinkage, waste,
               price_usd_m2, quality_allowance, ktc_margin) -> EtapasOracle:
    """§18: corte já embute as sobras (sem bainha genérica), CMT 0,50 (0 abas) ou 0,75, festonê
    +0,10 antes da 2ª qualidade e da margem."""
    if abas not in (0, 2, 3, 4):
        e = EtapasOracle()
        e.motivo = f"{abas} abas fora do §18"
        return e
    w_cut, l_cut = corte_fronha_s18(largura_cm, comprimento_cm, flap_cm, abas)
    cmt = Decimal("0.50") if abas == 0 else Decimal("0.75")
    other = Decimal("0.10") if festone else Decimal(0)
    return exw_tecido_plano(w_cut, l_cut, hem_w_cm=0, hem_l_cm=0, shrinkage=shrinkage,
                            waste=waste, price_usd_m2=price_usd_m2, cmt_usd=cmt,
                            other_usd=other, quality_allowance=quality_allowance,
                            ktc_margin=ktc_margin, paineis=1)


def exw_toalha(largura_cm, comprimento_cm, gsm, price_usd_kg) -> EtapasOracle:
    """§17: peso = W × L × GSM ÷ 10.000.000; EXW = peso × USD/kg (taxa já é EXW final)."""
    e = EtapasOracle()
    if not (largura_cm and comprimento_cm and gsm):
        e.motivo = "sem dimensão/GSM"
        return e
    if price_usd_kg is None:
        e.motivo = "sem preço por kg para a construção"
        return e
    e.peso_teorico_kg = dec(largura_cm) * dec(comprimento_cm) * dec(gsm) / Decimal(10_000_000)
    e.exw_usd = e.peso_teorico_kg * dec(price_usd_kg)
    e.calculavel = True
    return e


# ---------------------------------------------------------------------------
# 2. Nacionalização — função pura
# ---------------------------------------------------------------------------
@dataclass
class NacOracle:
    exw_usd: Decimal
    peso_kg: Decimal
    frete_usd: Decimal
    base_ii_usd: Decimal
    ii_pct: Decimal
    ii_usd: Decimal
    outras_usd: Decimal
    net_usd: Decimal
    fx: Decimal
    cnet_brl: Decimal


def nacionalizar(exw_usd, peso_kg, ii_pct, *, frete_usd_kg, outras_desp_usd_un, fx_usd_brl) -> NacOracle:
    """§23: EXW + frete (USD/kg × peso) + II (sobre EXW + frete, cf. 01_CUSTOS) + outras → NET USD → × FX."""
    exw = dec(exw_usd)
    peso = dec(peso_kg) if peso_kg is not None else Decimal(0)
    ii = dec(ii_pct) if ii_pct is not None else Decimal(0)
    frete = peso * dec(frete_usd_kg)
    base = exw + frete
    ii_usd = base * ii
    net = exw + frete + ii_usd + dec(outras_desp_usd_un)
    return NacOracle(exw, peso, frete, base, ii, ii_usd, dec(outras_desp_usd_un), net,
                     dec(fx_usd_brl), net * dec(fx_usd_brl))


# ---------------------------------------------------------------------------
# 3. Premissas lidas direto do SQLite (cópia), sem app.*
# ---------------------------------------------------------------------------
class Premissas:
    """Leitura crua das tabelas versionadas. Vigência: ativo e valid_from ≤ hoje ≤ valid_to."""

    def __init__(self, caminho_db: str, hoje: Optional[date] = None):
        self.hoje = (hoje or date.today()).isoformat()
        self.con = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def _vigentes(self, tabela: str, extra: str = "", params=()):
        # vigência com fim EXCLUSIVO (`valid_to > hoje`), como o app (`_vigente_hoje`): a linha
        # encerrada em 22/09/2026 não vale mais em 22/09/2026
        sql = (f"select * from {tabela} where ativo=1 and (valid_from is null or valid_from<=?) "
               f"and (valid_to is null or valid_to>?) {extra}")
        return self.con.execute(sql, (self.hoje, self.hoje) + tuple(params)).fetchall()

    def premissa_num(self, chave: str) -> Optional[Decimal]:
        linhas = self._vigentes("premissa", "and chave=?", (chave,))
        if not linhas:
            return None
        linhas.sort(key=lambda r: ((r["valid_from"] or ""), r["id"]))
        return dec(linhas[-1]["valor_num"])

    def parametro(self, chave: str, escopo: Optional[str] = None) -> Optional[Decimal]:
        linhas = self._vigentes("parametroktc", "and chave=?", (chave,))
        if escopo:
            esp = [r for r in linhas if (r["escopo"] or "").strip().lower() == escopo.strip().lower()]
            if esp:
                esp.sort(key=lambda r: (r["valid_from"] or ""))
                return dec(esp[-1]["valor"])
        glob = [r for r in linhas if not r["escopo"]]
        if glob:
            glob.sort(key=lambda r: (r["valid_from"] or ""))
            return dec(glob[-1]["valor"])
        return None

    def tabela_parametro(self, chave: str) -> Dict[str, Decimal]:
        return {r["escopo"]: dec(r["valor"]) for r in self._vigentes("parametroktc", "and chave=?", (chave,))
                if r["escopo"]}

    def material(self, material: str, plain_or_stripe: str):
        alvo = (material or "").strip().lower()
        listra = (plain_or_stripe or "plain").strip().lower()
        linhas = [r for r in self._vigentes("materialpreco")
                  if r["material"].strip().lower() == alvo
                  and (r["plain_or_stripe"] or "plain").lower() == listra]
        if not linhas:
            return None
        linhas.sort(key=lambda r: (r["valid_from"] or ""))
        return linhas[-1]

    def cmt(self, familia: str, construcao: Optional[str] = None) -> Optional[Decimal]:
        alvo = (familia or "").strip().lower()
        cands = [r for r in self._vigentes("cmtpreco") if r["familia"].strip().lower() == alvo]
        if construcao:
            c = [r for r in cands if (r["construcao"] or "").strip().lower() == construcao.strip().lower()]
            if c:
                return dec(c[-1]["cmt_usd"])
        g = [r for r in cands if not r["construcao"]]
        if g:
            return dec(g[-1]["cmt_usd"])
        return dec(cands[-1]["cmt_usd"]) if cands else None

    def toalha_usd_kg(self, subcategoria: str, gsm, yarn_type, plain_or_stripe: str):
        """Match rigoroso: subcategoria (família), GSM (se a linha tiver), yarn, plain/stripe."""
        linhas = self._vigentes("toalhapreco")
        cands = []
        for t in linhas:
            if subcategoria and (t["subcategoria"] or "").strip().lower() != subcategoria.strip().lower():
                continue
            if gsm is not None and t["gsm"] is not None and int(t["gsm"]) != int(gsm):
                continue
            if yarn_type and (t["yarn_type"] or "").strip().lower() != yarn_type.strip().lower():
                continue
            if (t["plain_or_stripe"] or "plain").lower() != (plain_or_stripe or "plain").lower():
                continue
            cands.append(t)
        if not cands:
            return None, None
        cands.sort(key=lambda t: (t["subcategoria"] is not None, t["gsm"] is not None, t["valid_from"] or ""))
        t = cands[-1]
        return dec(t["price_usd_kg"]), bool(t["preco_final"])

    def ii_por_familia(self, familia: str, categoria: str, ncm: Optional[str]):
        regras = self._vigentes("ncmregra")
        fam = (familia or "").strip().lower()
        cat = (categoria or "").strip().lower()
        por_fam = [r for r in regras if r["familia"] and r["familia"].strip().lower() in (fam, cat)]
        if por_fam:
            r = sorted(por_fam, key=lambda r: r["prioridade"])[0]
            return (dec(r["ii_preferencial"]), bool(r["confiavel"]), r["ncm"])
        if ncm:
            por_ncm = [r for r in regras if (r["ncm"] or "").strip() == ncm.strip()]
            if por_ncm:
                r = sorted(por_ncm, key=lambda r: r["prioridade"])[0]
                return (dec(r["ii_preferencial"]), bool(r["confiavel"]), r["ncm"])
        return None, None, None

    def produtos_ktc(self):
        return self.con.execute(
            "select p.* from produto p join fornecedor f on f.id=p.fornecedor_id "
            "where f.codigo='KTC' order by p.id").fetchall()


# ---------------------------------------------------------------------------
# 4. Resolução de um produto pelo oracle (regras do SUPER_PROMPT)
# ---------------------------------------------------------------------------
FAMILIAS_PLANO = {"flat sheet", "top sheet", "bottom sheet"}
FAMILIAS_FRONHA = {"pillow case", "pillowcase", "fronha"}
FAMILIAS_TOALHA = {"bath towel", "hand towel", "face towel", "pool towel", "beach towel",
                   "bath mat", "wash cloth", "towel"}
#: §15: thread counts sem cobertura confiável — NÃO calcular automaticamente.
TC_NAO_CALCULAVEIS = {200, 233, 500, 800}


def shrinkage_escopo(cotton_pct) -> str:
    c = dec(cotton_pct)
    return "COTTON" if (c is not None and c >= Decimal("0.999")) else "CVC"


def abas_do_cadastro(construcao: Optional[str], acabamento: Optional[str]) -> int:
    texto = f"{construcao or ''} {acabamento or ''}".lower()
    if "oxford" in texto:
        return 4
    m = re.search(r"(\d)\s*abas?", texto)
    return int(m.group(1)) if m else 0


def flap_do_cadastro(fechamento: Optional[str], padrao=Decimal(20)) -> Decimal:
    m = re.search(r"(\d+(?:[.,]\d+)?)", fechamento or "")
    return Decimal(m.group(1).replace(",", ".")) if m else padrao


@dataclass
class ResultadoOracle:
    familia: str
    etapas: EtapasOracle
    peso_kg: Optional[Decimal] = None
    peso_tipo: Optional[str] = None
    ii_pct: Optional[Decimal] = None
    ii_confiavel: Optional[bool] = None
    protecao_pct: Optional[Decimal] = None      # proteção comercial de precificação (não é I.I.)
    nac: Optional[NacOracle] = None
    avisos: List[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def peso_oracle(prem: Premissas, p: sqlite3.Row):
    """§17/§19 prioridade: peso real KTC → peso/m² da família (calibrado PI) → área×GSM → técnico."""
    if p["peso_kg"] and (p["peso_tipo"] == "REAL KTC" or p["peso_tipo"] is None):
        return dec(p["peso_kg"]), "REAL KTC"
    W, L = dec(p["largura_cm"]), dec(p["comprimento_cm"])
    fam = p["familia"]
    kg_m2 = prem.tabela_parametro("peso_kg_m2_familia")
    if W and L and fam in kg_m2:
        return (W * L / 10000) * kg_m2[fam], "ESTIMADO peso/m² família"
    if W and L:
        gsm = dec(p["gsm"])
        origem = "GSM do produto"
        if gsm is None and p["thread_count"] is not None:
            gsm = prem.tabela_parametro("gsm_por_tc").get(str(int(p["thread_count"])))
            origem = "GSM por TC"
        if gsm is None and fam:
            gsm = prem.tabela_parametro("gsm_por_familia").get(fam)
            origem = "GSM da família"
        if gsm:
            return (W * L / 10000) * gsm / 1000, f"ESTIMADO área×GSM ({origem})"
    tec = prem.tabela_parametro("peso_tecnico_familia")
    if fam in tec:
        return tec[fam], "TÉCNICO"
    return None, None


def resolver_produto(prem: Premissas, p: sqlite3.Row) -> ResultadoOracle:
    fam = (p["familia"] or "").strip().lower()
    r = ResultadoOracle(p["familia"] or "", EtapasOracle())
    waste = prem.parametro("waste")
    # 21/09/2026: o allowance é por família quando a KTC o declara (fronha 2%, planilha
    # "Pillow Case Costing sheet"); as demais seguem o global — o oracle lê como o motor lê
    qa = prem.parametro("quality_allowance", p["familia"])
    margem = prem.parametro("ktc_margin")
    shrink = prem.parametro("shrinkage", shrinkage_escopo(p["cotton_pct"]))
    r.params = {"waste": waste, "quality_allowance": qa, "ktc_margin": margem, "shrinkage": shrink}

    if fam in FAMILIAS_TOALHA:
        usd_kg, final = prem.toalha_usd_kg(p["familia"], p["gsm"], p["yarn_type"], p["plain_or_stripe"] or "plain")
        r.params.update({"price_usd_kg": usd_kg, "preco_final": final})
        r.etapas = exw_toalha(p["largura_cm"], p["comprimento_cm"], p["gsm"], usd_kg)
        if final is False:
            r.avisos.append("taxa por kg não marcada como preço final — regra §17 diz que é EXW final")
    elif fam in FAMILIAS_PLANO or fam == "duvet cover" or fam in FAMILIAS_FRONHA:
        if p["thread_count"] in TC_NAO_CALCULAVEIS:
            r.avisos.append(f"§15: TC {p['thread_count']} não é calculável automaticamente")
        mat = prem.material(p["material_ref"], p["plain_or_stripe"] or "plain") if p["material_ref"] else None
        price = dec(mat["price_usd_m2"]) if mat else None
        r.params["price_usd_m2"] = price
        if mat is None:
            r.etapas.motivo = "sem material_ref/preço de material"
            return r
        # consistência do cadastro × material (estrutura, não nome)
        if mat["thread_count"] is not None and p["thread_count"] is not None and int(mat["thread_count"]) != int(p["thread_count"]):
            r.avisos.append(f"TC do produto ({p['thread_count']}) ≠ TC do material ({mat['thread_count']})")
        if mat["cotton_pct"] is not None and p["cotton_pct"] is not None and abs(dec(mat["cotton_pct"]) - dec(p["cotton_pct"])) > Decimal("0.001"):
            r.avisos.append(f"algodão do produto ({p['cotton_pct']}) ≠ do material ({mat['cotton_pct']})")
        if fam in FAMILIAS_FRONHA:
            texto = (p["acabamento"] or "").lower()
            if any(x in texto for x in ("bordado", "logotipo", "logo")):
                r.etapas.motivo = "bordado/logotipo → KTC_SPECIAL_QUOTED"
                return r
            abas = abas_do_cadastro(p["construcao"], p["acabamento"])
            flap = flap_do_cadastro(p["fechamento"])
            r.params.update({"abas": abas, "flap_cm": flap, "festone": "feston" in texto})
            r.etapas = exw_fronha(p["largura_cm"], p["comprimento_cm"], flap_cm=flap, abas=abas,
                                  festone="feston" in texto, shrinkage=shrink, waste=waste,
                                  price_usd_m2=price, quality_allowance=qa, ktc_margin=margem)
        else:
            if fam == "bottom sheet" and any(x in f"{p['construcao'] or ''} {p['nome'] or ''}".lower()
                                             for x in ("elástico", "elastico", "fitted")):
                r.etapas.motivo = "bottom sheet com elástico: sem fórmula aprovada"
                return r
            hem_w = prem.parametro("hem_width_total_cm", p["familia"])
            hem_l = prem.parametro("hem_length_total_cm", p["familia"])
            paineis = prem.parametro("paineis", p["familia"]) or Decimal(1)
            if fam == "duvet cover" and paineis < 2:
                paineis = Decimal(2)
            cmt = prem.cmt(p["familia"], p["construcao"])
            r.params.update({"hem_w": hem_w, "hem_l": hem_l, "paineis": paineis, "cmt": cmt})
            r.etapas = exw_tecido_plano(p["largura_cm"], p["comprimento_cm"], hem_w_cm=hem_w,
                                        hem_l_cm=hem_l, shrinkage=shrink, waste=waste,
                                        price_usd_m2=price, cmt_usd=cmt, other_usd=0,
                                        quality_allowance=qa, ktc_margin=margem, paineis=int(paineis))
        if p["acabamento"] and r.etapas.calculavel:
            r.avisos.append(f"acabamento sem custo cadastrado: {p['acabamento']} (CALCULATED_PARTIAL)")
    else:
        r.etapas.motivo = f"família '{p['familia']}' sem fórmula industrial"
        return r

    r.peso_kg, r.peso_tipo = peso_oracle(prem, p)
    # 22/09/2026: I.I. ECONÔMICO KTC/Egito = 0% por decisão. A regra de NCM vigente tem de
    # dizer o mesmo — alíquota positiva vigente é divergência, não custo.
    ii_regra, r.ii_confiavel, _ = prem.ii_por_familia(p["familia"], p["categoria"], p["ncm"])
    r.ii_pct = Decimal(0)
    if ii_regra is not None and ii_regra != 0:
        r.avisos.append(f"NcmRegra vigente com I.I. {ii_regra} ≠ 0 — I.I. econômico KTC é 0% desde 22/09/2026")
    # proteção comercial de precificação (não é imposto): pino do SKU, senão regra da família
    r.protecao_pct = dec(p["protecao_comercial_pct"]) if p["protecao_comercial_pct"] is not None \
        else prem.parametro("protecao_comercial_pct", p["familia"])
    return r


def nacionalizar_com_premissas(prem: Premissas, exw_usd, peso_kg, ii_pct) -> NacOracle:
    return nacionalizar(exw_usd, peso_kg, ii_pct,
                        frete_usd_kg=prem.premissa_num("frete_int_usd_kg"),
                        outras_desp_usd_un=prem.premissa_num("outras_desp_usd_un"),
                        fx_usd_brl=prem.premissa_num("fx_usd_brl"))


# ---------------------------------------------------------------------------
# 5. Comparação com o motor oficial (só aqui entra app.*)
# ---------------------------------------------------------------------------
TOL_EXW = Decimal("0.001")
TOL_CNET = Decimal("0.01")
TOL_PESO = Decimal("0.0001")


def _f(v, casas=6):
    if v is None:
        return ""
    return f"{dec(v):.{casas}f}"


def main():
    from scripts.crisis.ambiente import preparar, AUDIT, caminho_copia
    session = preparar("ktc")
    from app import pricing_service as ps
    from app.models import Produto

    prem = Premissas(caminho_copia("ktc"))
    hoje = date.today()
    linhas = []
    achados = []
    fx = prem.premissa_num("fx_usd_brl")
    frete_kg = prem.premissa_num("frete_int_usd_kg")
    outras = prem.premissa_num("outras_desp_usd_un")
    print(f"premissas do banco: fx={fx} frete_usd_kg={frete_kg} outras={outras} (hoje {hoje})")

    for row in prem.produtos_ktc():
        produto = session.get(Produto, row["id"])
        oficial = ps.calcular_exw(session, produto)
        if oficial.exw_usd is None:
            continue   # só os que o motor oficial consegue calcular
        orc = resolver_produto(prem, row)
        memoria = ps.custo_net(session, produto)
        nac_of = memoria.get("nacionalizacao") or {}
        etapas_of = {e.nome: dec(e.valor) for e in oficial.etapas}
        exw_of = dec(oficial.exw_usd)
        exw_or = orc.etapas.exw_usd

        # peso que o motor oficial usou (custo_net: produto.peso_kg, senão estimativa)
        peso_of = dec(produto.peso_kg) if produto.peso_kg is not None else dec((memoria.get("peso") or {}).get("peso_kg"))
        # nacionalização oficial sobre o EXW que o custo_net efetivamente usou
        exw_usado_of = dec(memoria.get("exw_usd"))
        ii_of = dec((memoria.get("ncm") or {}).get("ii"))
        nac_or_mesmo_exw = nacionalizar_com_premissas(prem, exw_usado_of, peso_of, orc.ii_pct) if exw_usado_of is not None else None
        # oracle fim a fim (EXW oracle + peso oracle + II oracle)
        nac_or = nacionalizar_com_premissas(prem, exw_or, orc.peso_kg, orc.ii_pct) if exw_or is not None else None

        dif_exw = (exw_of - exw_or) if (exw_or is not None) else None
        dif_cnet_mesmo_exw = (dec(nac_of.get("net_brl")) - nac_or_mesmo_exw.cnet_brl) if (nac_of.get("net_brl") is not None and nac_or_mesmo_exw) else None
        cnet_of_motor = None
        if produto.cost_method == "KTC_CALCULATED" and nac_of.get("net_brl") is not None:
            cnet_of_motor = dec(nac_of["net_brl"])
        dif_cnet_fim_a_fim = (cnet_of_motor - nac_or.cnet_brl) if (cnet_of_motor is not None and nac_or) else None
        dif_peso = (peso_of - orc.peso_kg) if (peso_of is not None and orc.peso_kg is not None) else None
        # referência comercial (o antigo waterfall com a proteção no lugar do I.I.) — forma o
        # preço, não o custo; tem de bater com `base_comercial_brl` do motor
        # só onde o motor nacionalizou (custo lido do catálogo não se decompõe: base = custo)
        base_of = dec(memoria.get("base_comercial_brl")) if memoria.get("referencia_comercial") else None
        ref_or = (nacionalizar_com_premissas(prem, exw_usado_of, peso_of, orc.protecao_pct)
                  if (base_of is not None and exw_usado_of is not None and orc.protecao_pct is not None) else None)
        dif_ref = (base_of - ref_or.cnet_brl) if (base_of is not None and ref_or is not None) else None

        status = "OK"
        notas = list(orc.avisos)
        if exw_or is None:
            status = "DIVERGENCIA_REGRA"
            notas.append(f"oracle não calcula: {orc.etapas.motivo}")
        elif abs(dif_exw) >= TOL_EXW:
            status = "DIVERGENCIA_EXW"
        if dif_cnet_mesmo_exw is not None and abs(dif_cnet_mesmo_exw) >= TOL_CNET:
            status = "DIVERGENCIA_NACIONALIZACAO"
        if dif_peso is not None and abs(dif_peso) >= TOL_PESO:
            notas.append(f"peso oficial {peso_of} ≠ oracle {orc.peso_kg} ({orc.peso_tipo}); "
                         f"produto.peso_tipo={produto.peso_tipo}")
            if status == "OK":
                status = "DIVERGENCIA_PESO"
        if ii_of is not None and orc.ii_pct is not None and ii_of != orc.ii_pct:
            status = "DIVERGENCIA_II"
        if any("≠ 0" in a for a in orc.avisos):
            status = "DIVERGENCIA_II"
        if (base_of is None) != (ref_or is None) or (dif_ref is not None and abs(dif_ref) >= TOL_CNET):
            status = "DIVERGENCIA_REFERENCIA_COMERCIAL"
        if any(a.startswith("§15") for a in orc.avisos) and status == "OK":
            status = "REGRA_TC_NAO_CALCULAVEL"

        linhas.append({
            "produto_id": row["id"], "sku": row["sku_key"], "familia": row["familia"],
            "cost_method": produto.cost_method,
            "dimensao": f"{row['largura_cm']:g}x{row['comprimento_cm']:g}" if row["largura_cm"] and row["comprimento_cm"] else "",
            "tc": row["thread_count"] or "", "cotton_pct": row["cotton_pct"] if row["cotton_pct"] is not None else "",
            "plain_or_stripe": row["plain_or_stripe"] or "", "gsm": row["gsm"] or "",
            "material_ref": row["material_ref"] or "", "construcao": row["construcao"] or "",
            "acabamento": row["acabamento"] or "",
            "status_oficial": oficial.status,
            "of_largura_hem": _f(etapas_of.get("Largura com bainha"), 4), "or_largura_hem": _f(orc.etapas.largura_hem, 4),
            "of_consumo_waste_m2": _f(etapas_of.get("Consumo com waste")), "or_consumo_waste_m2": _f(orc.etapas.consumo_waste_m2),
            "of_tecido_usd": _f(etapas_of.get("Custo do tecido")), "or_tecido_usd": _f(orc.etapas.tecido_usd),
            "of_producao_usd": _f(etapas_of.get("Custo de produção")), "or_producao_usd": _f(orc.etapas.producao_usd),
            "of_qualidade_usd": _f(etapas_of.get("Após perda de 2ª qualidade")), "or_qualidade_usd": _f(orc.etapas.qualidade_usd),
            "of_peso_toalha_kg": _f(etapas_of.get("Peso da peça")), "or_peso_toalha_kg": _f(orc.etapas.peso_teorico_kg),
            "of_exw_usd": _f(exw_of), "or_exw_usd": _f(exw_or), "dif_exw_usd": _f(dif_exw),
            "of_exw_usado_na_nac": _f(exw_usado_of), "exw_origem_oficial": memoria.get("exw_origem") or "",
            "of_peso_kg": _f(peso_of), "or_peso_kg": _f(orc.peso_kg), "or_peso_tipo": orc.peso_tipo or "",
            "produto_peso_tipo": produto.peso_tipo or "",
            "of_frete_usd": _f(nac_of.get("frete_usd")), "or_frete_usd": _f(nac_or_mesmo_exw.frete_usd if nac_or_mesmo_exw else None),
            "of_ii_pct": _f(ii_of, 4), "or_ii_pct": _f(orc.ii_pct, 4),
            "of_ii_usd": _f(nac_of.get("ii_usd")), "or_ii_usd": _f(nac_or_mesmo_exw.ii_usd if nac_or_mesmo_exw else None),
            "of_net_usd": _f(nac_of.get("net_usd")), "or_net_usd": _f(nac_or_mesmo_exw.net_usd if nac_or_mesmo_exw else None),
            "of_cnet_brl": _f(nac_of.get("net_brl")), "or_cnet_brl_mesmo_exw": _f(nac_or_mesmo_exw.cnet_brl if nac_or_mesmo_exw else None),
            "dif_cnet_mesmo_exw": _f(dif_cnet_mesmo_exw),
            "protecao_comercial_pct": _f(orc.protecao_pct, 4),
            "of_base_comercial_brl": _f(base_of), "or_base_comercial_brl": _f(ref_or.cnet_brl if ref_or else None),
            "dif_base_comercial": _f(dif_ref),
            "or_cnet_brl_fim_a_fim": _f(nac_or.cnet_brl if nac_or else None),
            "dif_cnet_fim_a_fim(so_KTC_CALCULATED)": _f(dif_cnet_fim_a_fim),
            "status": status, "notas": " | ".join(notas),
        })

    os.makedirs(AUDIT, exist_ok=True)
    destino = os.path.join(AUDIT, "oracle_ktc_comparacao.csv")
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)

    from collections import Counter
    c = Counter(l["status"] for l in linhas)
    print(f"produtos calculáveis pelo motor oficial: {len(linhas)}")
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}")
    for l in linhas:
        if l["status"] != "OK":
            print(f"  [{l['status']}] #{l['produto_id']} {l['sku']} | exw of={l['of_exw_usd']} or={l['or_exw_usd']} "
                  f"| cnet of={l['of_cnet_brl']} or={l['or_cnet_brl_mesmo_exw']} | {l['notas']}")
    print("gravado:", destino)


if __name__ == "__main__":
    main()

"""Frete comercial: liga o motor puro ao banco e monta os grupos logísticos.

Duas decisões que o módulo protege:

**A tabela não é padrão de ninguém.** Uma `TabelaFrete` só resolve o grupo cuja transportadora
e cuja origem logística batem com as dela. Não existe "usar a TRANSAL-Itajaí porque é a única
cadastrada": grupo com origem incompatível vai para `FRETE_A_COTAR`, com o motivo escrito.

**A base dos percentuais é o grupo, não a cotação.** ADV, GRIS e fiel depositário incidem sobre
o valor da nota do embarque. Com dois grupos, aplicar cada percentual sobre o total da cotação
cobraria o adicional duas vezes.
"""
import json
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from sqlmodel import Session, select

from app import frete_engine as fe
from app.models import (
    CoberturaFrete, ComponenteFrete, CotacaoItem, FaixaFrete, Fornecedor, GrupoLogistico,
    Produto, TabelaFrete, TipoFrete,
)


def _chave(texto: Optional[str]) -> str:
    base = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn")
    return base.strip().upper()


@dataclass
class ItemDoGrupo:
    item_id: Optional[int]
    produto_id: Optional[int]
    quantidade: float
    valor_mercadoria: float
    peso_kg: Optional[float]
    volume_m3: Optional[float]


# ---------------------------------------------------------------------------
# Resolução de tabela e destino
# ---------------------------------------------------------------------------
def tabela_para_origem(session: Session, origem_cidade: Optional[str], origem_uf: Optional[str],
                       transportadora_id: Optional[int] = None,
                       ref_data: Optional[date] = None):
    """Tabela vigente cuja origem logística casa com a do grupo. `(tabela, motivo)`."""
    ref_data = ref_data or date.today()
    candidatas = session.exec(select(TabelaFrete).where(TabelaFrete.ativo == True)).all()  # noqa: E712
    if transportadora_id:
        candidatas = [t for t in candidatas if t.transportadora_id == transportadora_id]
    if not candidatas:
        return None, "Nenhuma tabela de frete cadastrada."

    compativeis = [t for t in candidatas
                   if _chave(t.origem_logistica_cidade) == _chave(origem_cidade)
                   and _chave(t.origem_logistica_uf) == _chave(origem_uf)]
    if not compativeis:
        origens = sorted({f"{t.origem_logistica_cidade}-{t.origem_logistica_uf}"
                          for t in candidatas})
        return None, (f"A origem logística deste grupo ({origem_cidade or '—'}-"
                      f"{origem_uf or '—'}) não tem tabela de frete. Cadastradas: "
                      f"{', '.join(origens)}. Não se usa a tabela de outra origem por falta "
                      "de opção.")

    vigentes = [t for t in compativeis
                if (t.valid_from or date.min) <= ref_data
                and (t.valid_to is None or t.valid_to >= ref_data)]
    if not vigentes:
        mais_nova = sorted(compativeis, key=lambda t: t.valid_to or date.min)[-1]
        return mais_nova, (f"A tabela de {mais_nova.origem_logistica_cidade} venceu em "
                           f"{mais_nova.valid_to:%d/%m/%Y}.")
    return sorted(vigentes, key=lambda t: t.versao)[-1], None


def regiao_do_destino(session: Session, tabela_id: int, cidade: Optional[str],
                      uf: Optional[str] = None) -> Optional[str]:
    """Cidade → região tarifária, pela cobertura da própria tabela.

    Sem linha, o destino está fora da cobertura. **Nunca** se escolhe a cidade mais próxima,
    a tarifa média nem a região geograficamente parecida.
    """
    if not cidade:
        return None
    linhas = session.exec(select(CoberturaFrete)
                          .where(CoberturaFrete.tabela_id == tabela_id)).all()
    alvo = _chave(cidade)
    for linha in linhas:
        if _chave(linha.cidade) == alvo and (uf is None or linha.uf is None
                                             or _chave(linha.uf) == _chave(uf)):
            return linha.regiao_destino
    return None


# ---------------------------------------------------------------------------
# Cálculo de um grupo
# ---------------------------------------------------------------------------
def calcular_grupo(session: Session, grupo: GrupoLogistico,
                   adicionais: Optional[Dict[str, float]] = None,
                   ref_data: Optional[date] = None) -> fe.ResultadoFrete:
    """Resolve o frete de um grupo e grava o resultado nele."""
    ref_data = ref_data or date.today()
    tabela, motivo_tabela = tabela_para_origem(
        session, grupo.origem_cidade, grupo.origem_uf, grupo.transportadora_id, ref_data)

    if tabela is None:
        resultado = fe.ResultadoFrete(status=fe.A_COTAR, motivo=motivo_tabela)
    else:
        grupo.tabela_id = tabela.id
        regiao = grupo.regiao_destino or regiao_do_destino(
            session, tabela.id, grupo.destino_cidade, grupo.destino_uf)
        faixas = session.exec(select(FaixaFrete)
                              .where(FaixaFrete.tabela_id == tabela.id)).all()
        componentes = session.exec(select(ComponenteFrete)
                                   .where(ComponenteFrete.tabela_id == tabela.id)).all()
        resultado = fe.calcular_frete_grupo(
            tabela, faixas, componentes, regiao,
            peso_real_kg=grupo.peso_real_kg, volume_m3=grupo.volume_m3,
            valor_mercadoria=grupo.valor_mercadoria,
            peso_taxado_confirmado=grupo.peso_taxado_kg if grupo.peso_taxado_fonte else None,
            adicionais_pedidos=adicionais, ref_data=ref_data)
        if motivo_tabela and not resultado.bloqueado:
            resultado.avisos.append(motivo_tabela)

    grupo.regiao_destino = resultado.regiao_destino or grupo.regiao_destino
    grupo.peso_cubado_kg = resultado.peso_cubado_kg
    grupo.peso_taxado_kg = resultado.peso_taxado_kg or grupo.peso_taxado_kg
    grupo.peso_taxado_fonte = resultado.peso_taxado_fonte or grupo.peso_taxado_fonte
    grupo.frete_peso = resultado.frete_peso
    grupo.cf_logistico = resultado.cf_logistico
    grupo.rv_logistico_pct = resultado.rv_logistico_pct
    grupo.status = resultado.status
    grupo.motivo = resultado.motivo
    grupo.memoria_json = json.dumps(resultado.como_dict(), ensure_ascii=False, default=str)
    session.add(grupo)
    return resultado


# ---------------------------------------------------------------------------
# Montagem dos grupos a partir da cotação
# ---------------------------------------------------------------------------
def origem_logistica_do_produto(session: Session, produto: Optional[Produto]):
    """(cidade, uf, fornecedor_id) da origem LOGÍSTICA — que não é a origem fiscal."""
    if produto is None or not produto.fornecedor_id:
        return None, None, None
    f = session.get(Fornecedor, produto.fornecedor_id)
    if f is None:
        return None, None, None
    return (getattr(f, "origem_logistica_cidade", None),
            getattr(f, "origem_logistica_uf", None), f.id)


def agrupar_itens(session: Session, cotacao, itens: List[CotacaoItem]) -> List[dict]:
    """Um grupo por origem logística. Itens de origens diferentes não viram uma carga só."""
    grupos: Dict[tuple, dict] = {}
    for it in itens:
        produto = session.get(Produto, it.produto_id) if it.produto_id else None
        cidade, uf, fornecedor_id = origem_logistica_do_produto(session, produto)
        chave = (_chave(cidade), _chave(uf), fornecedor_id)
        g = grupos.setdefault(chave, {
            "origem_cidade": cidade, "origem_uf": uf, "fornecedor_id": fornecedor_id,
            "itens": [], "valor_mercadoria": 0.0, "peso_real_kg": 0.0, "sem_peso": False,
        })
        g["itens"].append(ItemDoGrupo(
            item_id=it.id, produto_id=it.produto_id, quantidade=it.quantidade or 0.0,
            valor_mercadoria=it.faturamento or 0.0,
            peso_kg=(produto.peso_kg if produto else None),
            volume_m3=None))
        g["valor_mercadoria"] += it.faturamento or 0.0
        if produto is not None and produto.peso_kg:
            g["peso_real_kg"] += produto.peso_kg * (it.quantidade or 0.0)
        else:
            g["sem_peso"] = True
    return list(grupos.values())


def ratear_para_itens(resultado: fe.ResultadoFrete, itens: List[ItemDoGrupo]) -> List[dict]:
    """Rateia o CF do grupo pelos itens. O RV é percentual e não se rateia: acompanha a receita.

    Critério: participação no **peso taxado atribuível** ao item. Sem peso individual, cai para
    participação no valor de mercadoria — e o critério usado fica registrado no item, porque um
    rateio sem critério declarado não é auditável.
    """
    if not itens:
        return []
    pesos = [(i.peso_kg or 0.0) * (i.quantidade or 0.0) for i in itens]
    criterio = "peso_taxado_atribuivel"
    if sum(pesos) <= 0:
        pesos = [i.valor_mercadoria or 0.0 for i in itens]
        criterio = "valor_de_mercadoria"
    if sum(pesos) <= 0:
        pesos = [1.0] * len(itens)
        criterio = "igual_por_item"

    parcelas = fe.ratear(resultado.cf_logistico or 0.0, pesos)
    return [{"item_id": i.item_id, "cf": parcela, "criterio": criterio,
             "rv_pct": resultado.rv_logistico_pct or 0.0}
            for i, parcela in zip(itens, parcelas)]


def frete_da_cotacao(session: Session, cotacao, itens: List[CotacaoItem],
                     ref_data: Optional[date] = None) -> dict:
    """Resolve todos os grupos de uma cotação CIF e devolve o consolidado.

    FOB não gera frete suportado pela Anara: a responsabilidade é do cliente, e isso fica
    registrado em vez de virar zero mudo.
    """
    if (cotacao.freight_type or "").upper() != TipoFrete.cif.value:
        return {"cif": False, "responsavel": "CLIENTE", "grupos": [],
                "frete_total": 0.0, "bloqueado": False,
                "memoria": "Frete FOB ou a combinar — não é suportado pela Anara."}

    resultados, total_cf, bloqueios = [], 0.0, []
    for dados in agrupar_itens(session, cotacao, itens):
        grupo = GrupoLogistico(
            cotacao_id=cotacao.id, origem_cidade=dados["origem_cidade"],
            origem_uf=dados["origem_uf"], fornecedor_id=dados["fornecedor_id"],
            destino_cidade=getattr(cotacao, "destino_cidade", None),
            destino_uf=cotacao.estado_destino,
            peso_real_kg=(None if dados["sem_peso"] else dados["peso_real_kg"]),
            valor_mercadoria=dados["valor_mercadoria"])
        r = calcular_grupo(session, grupo, ref_data=ref_data)
        if r.bloqueado:
            bloqueios.append(f"{dados['origem_cidade'] or 'origem indefinida'}: {r.motivo}")
        total_cf += r.cf_logistico or 0.0
        resultados.append({"grupo": grupo, "resultado": r, "itens": dados["itens"]})

    return {"cif": True, "responsavel": "ANARA", "grupos": resultados,
            "cf_total": total_cf, "bloqueado": bool(bloqueios), "motivos": bloqueios}

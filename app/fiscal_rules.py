"""Regras fiscais da venda — o único lugar que decide qual ICMS entra no preço.

O cenário é definido por **três campos independentes**: estado de origem, estado de destino e
se o cliente é contribuinte de ICMS. Nunca se infere "contribuinte" a partir do estado.

Ordem de resolução (a primeira que bater ganha):

1. **regra explícita** cadastrada em `RegraFiscalVenda` (ex.: dentro de SP é 18% para
   contribuinte e para não contribuinte);
2. **mesmo estado** → alíquota interna do destino, sem DIFAL;
3. **interestadual + contribuinte** → alíquota interestadual (4%, Res. Senado 13/2012 para
   bem importado);
4. **interestadual + não contribuinte** → coluna **Carga Final** da tabela de estados,
   usada como está. O sistema **não recalcula** a carga a partir de Base Simples/Base
   Dupla/FEM — essas colunas ficam guardadas só como memória de apuração.

Sem cenário na tabela, devolve o fallback com aviso — nunca quebra o cálculo, e nunca finge
que encontrou a regra.

As funções aqui são puras: recebem as linhas já lidas do banco (ou do snapshot de uma cotação
antiga) e devolvem `(aliquota, regra)`.
"""
from typing import List, Optional, Sequence, Tuple

ICMS_PADRAO_FALLBACK = 0.18


def resolver_icms_estruturado(regras_explicitas: Sequence, estados: Sequence,
                              origem: str, destino: str, contribuinte: bool,
                              fallback: float = ICMS_PADRAO_FALLBACK) -> Tuple[float, str]:
    """`regras_explicitas`: linhas de RegraFiscalVenda. `estados`: linhas de EstadoFiscal."""
    if not origem or not destino:
        return fallback, "Origem/destino não informados — ICMS padrão aplicado"

    explicitas = [r for r in regras_explicitas
                  if getattr(r, "ativo", True)
                  and (r.origem or "").strip().lower() == origem.strip().lower()
                  and (r.destino or "").strip().lower() == destino.strip().lower()
                  and bool(r.contribuinte) == bool(contribuinte)]
    if explicitas:
        melhor = sorted(explicitas, key=lambda r: (getattr(r, "prioridade", 100), r.id or 0))[0]
        return float(melhor.icms_venda), melhor.regra

    linha_destino = _estado(estados, destino)
    mesmo_estado = origem.strip().lower() == destino.strip().lower()

    if mesmo_estado:
        if linha_destino is None:
            return fallback, (f"Venda dentro de {destino}, mas o estado não está na tabela — "
                              "ICMS padrão aplicado")
        return float(linha_destino.aliquota_interna), (
            f"Intraestadual {destino} — alíquota interna, sem DIFAL")

    if contribuinte:
        aliquota = float(linha_destino.aliquota_interestadual) if linha_destino else 0.04
        return aliquota, ("Interestadual — Res. Senado 13/2012 (bem importado); "
                          "contribuinte credita a diferença")

    if linha_destino is None:
        return fallback, (f"Destino {destino} não encontrado na tabela de estados — "
                          "ICMS padrão aplicado")
    return float(linha_destino.carga_final), (
        f"Interestadual — não contribuinte: carga final de {destino} "
        "(coluna 'Carga Final', usada como está)")


def _estado(estados: Sequence, nome: str):
    alvo = (nome or "").strip().lower()
    for e in estados:
        if not getattr(e, "ativo", True):
            continue
        if (e.estado or "").strip().lower() == alvo or (e.uf or "").strip().lower() == alvo:
            return e
    return None


def resolver_icms(cenarios: List[dict], origem: str, destino: str, contribuinte: bool,
                  fallback: float = ICMS_PADRAO_FALLBACK) -> Tuple[float, str]:
    """Resolução pelo snapshot antigo (lista de dicts salva em BaseImportacao).

    Preservada para reproduzir cotações históricas exatamente como foram emitidas. Cotação nova
    usa `resolver_icms_estruturado`.
    """
    if not origem or not destino:
        return fallback, "Origem/destino não informados — ICMS padrão aplicado"
    for cen in cenarios:
        if (cen.get("origem") == origem and cen.get("destino") == destino
                and bool(cen.get("contribuinte")) == bool(contribuinte)):
            return float(cen["icms_venda"]), cen.get("regra", "")
    return fallback, "Cenário não encontrado na tabela de regras fiscais — ICMS padrão aplicado"

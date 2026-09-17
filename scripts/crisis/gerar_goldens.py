#!/usr/bin/env python3
"""§18 — Gera `tests/crisis/goldens.json`: 120 casos com waterfall completo calculados por
fórmulas INDEPENDENTES (só `decimal`), nunca pelo motor. O teste compara o motor com eles."""
import itertools
import json
import os
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 34
C = lambda x: Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
PIS_NOMINAL = Decimal("0.0925")

# cenários rotulados (origem SP): icms total suportado pela Anara, fcp, difal informativo
CENARIOS = {
    "A_SP_SP_nao_contribuinte_uso_consumo_30": dict(icms="0.18", fcp="0", encargo="0.016", destino="SP", contribuinte=False, finalidade="USO_CONSUMO", cond="30"),
    "B_SP_MG_importada_contribuinte_revenda_30_60": dict(icms="0.04", fcp="0", encargo="0.032", destino="MG", contribuinte=True, finalidade="REVENDA", cond="30/60", natureza="IMPORTADA"),
    "B2_SP_RJ_importada_contribuinte_uso_consumo_a_vista": dict(icms="0.04", fcp="0", encargo="0", destino="RJ", contribuinte=True, finalidade="USO_CONSUMO", cond="À VISTA", natureza="IMPORTADA"),
    "C_SP_RJ_importada_nao_contribuinte_30_60_90": dict(icms="0.22", fcp="0.02", encargo="0.048", destino="RJ", contribuinte=False, finalidade="USO_CONSUMO", cond="30/60/90", natureza="IMPORTADA"),
    "D_SP_PR_nacional_contribuinte_revenda_30": dict(icms="0.12", fcp="0", encargo="0.016", destino="PR", contribuinte=True, finalidade="REVENDA", cond="30", natureza="NACIONAL"),
    "D2_SP_BA_nacional_contribuinte_ativo_30_60_90_120_150": dict(icms="0.07", fcp="0", encargo="0.08", destino="BA", contribuinte=True, finalidade="ATIVO_IMOBILIZADO", cond="30/60/90/120/150", natureza="NACIONAL"),
    "E_SP_RJ_nacional_nao_contribuinte_30": dict(icms="0.22", fcp="0.02", encargo="0.016", destino="RJ", contribuinte=False, finalidade="USO_CONSUMO", cond="30", natureza="NACIONAL"),
    "E2_SP_SP_nacional_nao_contribuinte_30_60_90_120": dict(icms="0.18", fcp="0", encargo="0.064", destino="SP", contribuinte=False, finalidade="USO_CONSUMO", cond="30/60/90/120", natureza="NACIONAL"),
}
# políticas (fornecedor → margem, piso, comissão de formação)
POLITICAS = {
    "KTC_sheets_300": ("0.20", "0.17", "0.10"), "KTC_sheets_250": ("0.18", "0.15", "0.10"),
    "KTC_toalha": ("0.14", "0.11", "0.10"), "KTC_demais": ("0.17", "0.14", "0.10"),
    "DAUNE": ("0.12", "0.12", "0.05"), "DECOR": ("0.12", "0.10", "0.10"),
    "PERSONALIZADO_300": ("0.20", "0.17", "0.10"),
}
CUSTOS = ["31.3150116405054", "58.20319904767298", "96.27298102522158", "152.15861988686095", "199.146882", "33.981817045971", "12.5", "1235.51"]
QTDS = [1, 3, 10, 42, 100]
goldens = []
i = 0
for (nome_c, cen), (nome_p, (margem, piso, cform)), variante in itertools.product(CENARIOS.items(), POLITICAS.items(), (0, 1)):
    custo = Decimal(CUSTOS[(i + 3 * variante) % len(CUSTOS)]); qtd = QTDS[(i + 2 * variante) % len(QTDS)]; i += 1
    icms, fcp, enc = Decimal(cen["icms"]), Decimal(cen["fcp"]), Decimal(cen["encargo"])
    m, pi, cf = Decimal(margem), Decimal(piso), Decimal(cform)
    pc = PIS_NOMINAL * (1 - (icms - fcp))
    denom = 1 - icms - pc - enc - cf - m
    preco_preciso = custo / denom
    preco = C(preco_preciso)
    receita = C(preco * qtd)
    impostos = C(receita * (icms + pc + enc))
    comissao = C(receita * cf)
    custo_total = C(custo * qtd)
    lucro = receita - impostos - comissao - custo_total
    margem_real = lucro / receita
    # desconto até o piso: maior desconto que ainda deixa margem = piso com comissão mínima 5%
    cmin = Decimal("0.05")
    preco_piso = C(custo / (1 - icms - pc - enc - cmin - pi))
    goldens.append({
        "id": len(goldens) + 1, "cenario": nome_c, "politica": nome_p, "natureza": cen.get("natureza", "IMPORTADA"),
        "destino": cen["destino"], "contribuinte": cen["contribuinte"], "finalidade": cen["finalidade"], "condicao": cen["cond"],
        "cnet": str(custo), "quantidade": qtd, "icms_pct": str(icms), "fcp_pct": str(fcp), "pis_cofins_efetivo_pct": str(pc),
        "encargo_pct": str(enc), "comissao_formacao_pct": str(cf), "margem_alvo": str(m), "piso": str(pi),
        "preco_preciso": str(preco_preciso), "preco": str(preco), "receita": str(receita), "impostos": str(impostos),
        "comissao": str(comissao), "custo_total": str(custo_total), "lucro": str(lucro), "margem_realizada": str(margem_real),
        "difal_informativo": None, "preco_no_piso_com_comissao_minima": str(preco_piso),
    })
# personalizado 190x250 300TC (CNET conhecido do caso crítico: 58.2032) em 4 cenários + Daune travado
for nome_c in ("A_SP_SP_nao_contribuinte_uso_consumo_30", "C_SP_RJ_importada_nao_contribuinte_30_60_90", "B_SP_MG_importada_contribuinte_revenda_30_60", "D_SP_PR_nacional_contribuinte_revenda_30"):
    cen = CENARIOS[nome_c]; custo = Decimal("58.20319904767298"); qtd = 10
    icms, fcp, enc = Decimal(cen["icms"]), Decimal(cen["fcp"]), Decimal(cen["encargo"])
    m, pi, cf = Decimal("0.20"), Decimal("0.17"), Decimal("0.10")
    pc = PIS_NOMINAL * (1 - (icms - fcp)); preco = C(custo / (1 - icms - pc - enc - cf - m)); receita = C(preco * qtd)
    impostos = C(receita * (icms + pc + enc)); comissao = C(receita * cf); custo_total = C(custo * qtd); lucro = receita - impostos - comissao - custo_total
    goldens.append({"id": len(goldens) + 1, "cenario": nome_c, "politica": "PERSONALIZADO_190x250_300TC", "natureza": "IMPORTADA",
                    "destino": cen["destino"], "contribuinte": cen["contribuinte"], "finalidade": cen["finalidade"], "condicao": cen["cond"],
                    "cnet": str(custo), "quantidade": qtd, "icms_pct": str(icms), "fcp_pct": str(fcp), "pis_cofins_efetivo_pct": str(pc), "encargo_pct": str(enc),
                    "comissao_formacao_pct": str(cf), "margem_alvo": str(m), "piso": str(pi), "preco_preciso": str(custo / (1 - icms - pc - enc - cf - m)), "preco": str(preco),
                    "receita": str(receita), "impostos": str(impostos), "comissao": str(comissao), "custo_total": str(custo_total), "lucro": str(lucro), "margem_realizada": str(lucro / receita),
                    "difal_informativo": None, "preco_no_piso_com_comissao_minima": str(C(custo / (1 - icms - pc - enc - Decimal("0.05") - pi)))})
out = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tests", "crisis", "goldens.json")
json.dump(goldens, open(out, "w"), ensure_ascii=False, indent=1)
print(len(goldens), "goldens →", out)

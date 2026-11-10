"""Preço FIXO dos itens novos da Tabela de preços (cotação KTC ANARA20102026, 20/10/2026): tapetes de banheiro
(N53–N57) e 600 fios 100% algodão (N58–N80).

Política de 10/11/2026 (Matias): o preço da tabela fica parado; câmbio e custo mexem na margem. A REV5 foi
formada a câmbio 5,19 — os itens novos entram na mesma base: calculados UMA vez pelo sistema, numa cópia do
banco a 5,19 (B2B na margem-alvo da família), em todos os cenários (27 UFs × contribuinte × 6 prazos), e
gravados como preço fixo. Tapetes = os valores que a Ines passou ao cliente (61x91 R$ 193,98; 69x122
R$ 292,89 em SP, 30 dias); 600 fios = a planilha de 30/10 (mesma cotação, mesmo câmbio).

O produto é montado por `scripts/ktc_20_10_2026_11_10/itens.py` — o mesmo que grava o SKU no catálogo.

Uso: ANARA_DB_URL=sqlite:///<cópia a 5,19> python3 scripts/tabela_precos_2026_11_10/precos_ktc_20_10.py <saida.json>
Só leitura (nada é adicionado à sessão; rollback no fim). Recusa rodar se o câmbio da cópia não for 5,19.
"""
import json
import os
import sys

SAIDA = os.path.abspath(sys.argv[1])
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts", "ktc_20_10_2026_11_10"))
from sqlmodel import Session, select  # noqa: E402

import itens as fonte_itens  # noqa: E402
from app import config_service as cfg  # noqa: E402
from app import pricing_service as ps  # noqa: E402
from app.db import engine, url_segura  # noqa: E402
from app.models import CondicaoPagamento, EstadoFiscal  # noqa: E402
from app.routers.calculadora import _cenario_do_form  # noqa: E402


def main():
    print("banco:", url_segura())
    dados = fonte_itens.carregar()
    out = {"documento": dados["documento"], "prazos": [], "itens": []}
    with Session(engine) as s, ps.cache_de_leitura(s):
        fx = cfg.num(s, "fx_usd_brl")
        assert abs(fx - 5.19) < 1e-9, f"cópia com câmbio {fx}; a tabela é formada a 5,19"
        estados = sorted({e.estado for e in s.exec(select(EstadoFiscal)).all() if e.ativo and e.valid_to is None})
        conds = [c.codigo for c in s.exec(select(CondicaoPagamento).order_by(CondicaoPagamento.ordem)).all()
                 if c.ativo and c.encargo_pct is not None]
        out["prazos"] = conds
        cenarios = {(uf, co, cond): _cenario_do_form(s, {"estado_destino": uf, "contribuinte": co,
                                                          "condicao_pagamento": cond})
                    for uf in estados for co in ("sim", "nao") for cond in conds}
        for item in dados["novos"]:
            p = fonte_itens.produto_novo(s, item, dados)
            custo, mem0 = ps.custo_para_precificar(s, p)
            if mem0.get("premissas_faltantes"):
                raise SystemExit(f"{item['codigo']}: premissa faltando {mem0['premissas_faltantes']}")
            precos = {}
            for uf in estados:
                for co, rotulo in (("sim", "SIM"), ("nao", "NÃO")):
                    linha = []
                    for cond in conds:
                        b = ps.memoria_do_preco(s, p, cenarios[(uf, co, cond)]).get("b2b") or {}
                        linha.append(b.get("preco_b2b"))
                    precos[f"{uf}|{rotulo}"] = linha
            out["itens"].append({"codigo": item["codigo"], "nome_sistema": p.nome, "exw_usd": item["exw_usd"],
                                 "precos": precos})
            print(item["codigo"], p.nome[:60], "SP/NÃO:", precos["São Paulo|NÃO"])
        s.rollback()
    json.dump(out, open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()

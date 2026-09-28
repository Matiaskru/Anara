#!/usr/bin/env python3
"""Reconfirma as referências de custo que envelheceram (28/09/2026).

    python3 scripts/revalidar_2026_09_28/reconfirmar_referencias.py                 # só lista
    python3 scripts/revalidar_2026_09_28/reconfirmar_referencias.py --aplicar --ator EMAIL

## O que é reconfirmar, e o que NÃO é

`REVALIDAR` não é erro de cálculo: é referência **direta e utilizável** que passou do limite
de frescor (30 dias fresca, 60 dias envelhecendo). Ela cota e emite proposta com alerta; o que
ela não faz é sustentar compromisso firme — fechar venda — antes de alguém dizer "conferi, o
número continua valendo".

Reconfirmar é exatamente esse ato, e ele é do dono do negócio, não do script. Por isso:

* **`--ator` é obrigatório** com `--aplicar`: a decisão fica assinada no `AuditLog`, com a
  pessoa que a tomou. Reconfirmação sem autor é evidência sem procedência.
* Cada reconfirmação vira **versão nova** de `CustoReferencia` (`confirmar_referencia`), com
  fonte e motivo — a anterior fecha vigência e continua consultável. Nada é sobrescrito.
* **O documento de origem não é tocado.** `exw_cotado_usd`, `exw_cotado_data` e o frescor
  ficam como estão: a cotação da KTC continua sendo de 29/07 e continua envelhecendo. O que
  muda é a decisão registrada sobre ela, e é a referência vigente que o motor passa a seguir.
* **Os limites de frescor não são alterados.** Mudar 30/60 é premissa de negócio e tem o seu
  próprio caminho, no Admin.

## O que ele recusa

O caminho canônico (`governanca_produtos.confirmar_referencia`) recusa sozinho quando o motor
aponta premissa faltando, quando não há custo, quando o custo veio do catálogo sem evidência e
quando o peso é estimado por analogia. Este script não contorna nenhuma dessas recusas — ele
as reporta. Liberar é fornecer evidência, nunca declarar que ela existe.

## Segurança

Sem `--aplicar` ele **não escreve nada**: apenas lê e lista. Não existe modo "preview com
rollback" aqui — rollback não é rede de proteção confiável quando o caminho canônico grava por
dentro (foi assim que um preview zerou 36 rascunhos em 22/09/2026). Para ensaiar, aponte
`ANARA_DB_URL` para uma **cópia** e rode com `--aplicar` nela.
"""
import argparse
import os
import sys
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ANARA_SECRET_KEY", "reconfirmar-2026-09-28-local-0123456789abcdefgh")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava; sem isso, só lista")
    ap.add_argument("--ator", help="e-mail de quem está reconfirmando (obrigatório com --aplicar)")
    ap.add_argument("--motivo", default="Reconfirmação em lote: preços da KTC conferidos e "
                                        "mantidos; a cotação de origem não mudou.")
    a = ap.parse_args()

    from sqlmodel import Session, select
    import app.db as db
    from app import governanca_produtos as gov
    from app import pricing_service as ps
    from app.models import Produto, Usuario

    with Session(db.engine) as s:
        ator = None
        if a.aplicar:
            if not a.ator:
                print("RECUSADO: --aplicar exige --ator EMAIL. A reconfirmação é uma decisão "
                      "assinada; sem autor ela não tem procedência.", file=sys.stderr)
                return 2
            ator = s.exec(select(Usuario).where(Usuario.email == a.ator)).first()
            if ator is None:
                print(f"RECUSADO: usuário '{a.ator}' não encontrado.", file=sys.stderr)
                return 2

        alvos = []
        with ps.cache_de_leitura(s):
            for p in s.exec(select(Produto).where(Produto.ativo == True)).all():  # noqa: E712
                custo, mem = ps.custo_para_precificar(s, p)
                if ps.status_do_produto(s, p, custo, mem) == "REVALIDAR":
                    dias = ((date.today() - p.exw_cotado_data).days
                            if p.exw_cotado_data else None)
                    alvos.append((p, custo, p.exw_cotado_data, dias))

        print(f"SKUs em REVALIDAR: {len(alvos)}")
        if not a.aplicar:
            for p, custo, data, dias in sorted(alvos, key=lambda x: (x[2] or date.min, x[0].sku_key)):
                idade = f"{data} ({dias} dias)" if data else "sem data de cotação"
                print(f"  {p.familia:<20} {idade:<26} CNET {custo:>9.2f}  {p.sku_key[:46]}")
            print("\nSó listagem — nada gravado. Use --aplicar --ator EMAIL para reconfirmar.")
            return 0

        fonte = (f"Reconfirmação em lote de {date.today().strftime('%d/%m/%Y')} por "
                 f"{ator.email}. Documento de origem inalterado.")
        feitos, recusados = [], []
        for p, _custo, _data, _dias in alvos:
            try:
                r = gov.confirmar_referencia(s, p, fonte=fonte, motivo=a.motivo, ator=ator)
                feitos.append((p, r["versao"]))
            except gov.AcaoInvalida as e:
                # recusa do motor é resultado esperado, não falha do script: premissa
                # faltando, custo sem evidência ou peso por analogia não se reconfirmam
                recusados.append((p, str(e)))
        s.commit()

        print(f"\nreconfirmados: {len(feitos)} · recusados pelo motor: {len(recusados)}")
        for p, versao in feitos:
            print(f"  OK  V{versao}  {p.familia:<20} {p.sku_key[:52]}")
        for p, erro in recusados:
            print(f"  --  {p.familia:<20} {p.sku_key[:40]}\n      {erro[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

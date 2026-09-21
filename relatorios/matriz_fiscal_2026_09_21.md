# Matriz fiscal 21/09/2026 — origem SP

Base única para não contribuinte: ICMS próprio (interestadual) + DIFAL (interna − interestadual) + FCP, tudo do remetente.
Contribuinte revenda/industrialização: só a interestadual. Contribuinte uso/ativo: interestadual; DIFAL do destinatário (não reduz a margem).
Colunas `benchmark_*` são a tabela 'Projeto Anara — DIFAL' — rastreabilidade; NÃO entram no motor.

## O que o motor resolve (família do escopo: Flat Sheet)

| UF | Natureza | Contribuinte | Finalidade | ICMS que reduz a receita | Interestadual | DIFAL | FCP | Responsável DIFAL |
|---|---|---|---|---:|---:|---:|---:|---|
| AC | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| AC | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.00% | 0.00% | DESTINATARIO |
| AC | IMPORTADA | não | USO_CONSUMO | 19.00% | 4.00% | 15.00% | 0.00% | REMETENTE |
| AC | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| AC | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 12.00% | 0.00% | DESTINATARIO |
| AC | NACIONAL | não | USO_CONSUMO | 19.00% | 7.00% | 12.00% | 0.00% | REMETENTE |
| AL | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| AL | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.50% | 0.00% | DESTINATARIO |
| AL | IMPORTADA | não | USO_CONSUMO | 21.50% | 4.00% | 16.50% | 1.00% | REMETENTE |
| AL | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| AL | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.50% | 0.00% | DESTINATARIO |
| AL | NACIONAL | não | USO_CONSUMO | 21.50% | 7.00% | 13.50% | 1.00% | REMETENTE |
| AM | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| AM | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| AM | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| AM | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| AM | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| AM | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| AP | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| AP | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 14.00% | 0.00% | DESTINATARIO |
| AP | IMPORTADA | não | USO_CONSUMO | 18.00% | 4.00% | 14.00% | 0.00% | REMETENTE |
| AP | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| AP | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 11.00% | 0.00% | DESTINATARIO |
| AP | NACIONAL | não | USO_CONSUMO | 18.00% | 7.00% | 11.00% | 0.00% | REMETENTE |
| BA | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| BA | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.50% | 0.00% | DESTINATARIO |
| BA | IMPORTADA | não | USO_CONSUMO | 20.50% | 4.00% | 16.50% | 0.00% | REMETENTE |
| BA | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| BA | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.50% | 0.00% | DESTINATARIO |
| BA | NACIONAL | não | USO_CONSUMO | 20.50% | 7.00% | 13.50% | 0.00% | REMETENTE |
| CE | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| CE | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| CE | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| CE | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| CE | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| CE | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| DF | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| DF | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| DF | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| DF | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| DF | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| DF | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| ES | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| ES | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 13.00% | 0.00% | DESTINATARIO |
| ES | IMPORTADA | não | USO_CONSUMO | 17.00% | 4.00% | 13.00% | 0.00% | REMETENTE |
| ES | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| ES | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 10.00% | 0.00% | DESTINATARIO |
| ES | NACIONAL | não | USO_CONSUMO | 17.00% | 7.00% | 10.00% | 0.00% | REMETENTE |
| GO | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| GO | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.00% | 0.00% | DESTINATARIO |
| GO | IMPORTADA | não | USO_CONSUMO | 19.00% | 4.00% | 15.00% | 0.00% | REMETENTE |
| GO | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| GO | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 12.00% | 0.00% | DESTINATARIO |
| GO | NACIONAL | não | USO_CONSUMO | 19.00% | 7.00% | 12.00% | 0.00% | REMETENTE |
| MA | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| MA | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 19.00% | 0.00% | DESTINATARIO |
| MA | IMPORTADA | não | USO_CONSUMO | 23.00% | 4.00% | 19.00% | 0.00% | REMETENTE |
| MA | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| MA | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 16.00% | 0.00% | DESTINATARIO |
| MA | NACIONAL | não | USO_CONSUMO | 23.00% | 7.00% | 16.00% | 0.00% | REMETENTE |
| MG | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| MG | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 14.00% | 0.00% | DESTINATARIO |
| MG | IMPORTADA | não | USO_CONSUMO | 18.00% | 4.00% | 14.00% | 0.00% | REMETENTE |
| MG | NACIONAL | sim | REVENDA | 12.00% | 12.00% | — | 0.00% | NAO_APLICAVEL |
| MG | NACIONAL | sim | USO_CONSUMO | 12.00% | 12.00% | 6.00% | 0.00% | DESTINATARIO |
| MG | NACIONAL | não | USO_CONSUMO | 18.00% | 12.00% | 6.00% | 0.00% | REMETENTE |
| MS | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| MS | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 13.00% | 0.00% | DESTINATARIO |
| MS | IMPORTADA | não | USO_CONSUMO | 17.00% | 4.00% | 13.00% | 0.00% | REMETENTE |
| MS | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| MS | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 10.00% | 0.00% | DESTINATARIO |
| MS | NACIONAL | não | USO_CONSUMO | 17.00% | 7.00% | 10.00% | 0.00% | REMETENTE |
| MT | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| MT | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 13.00% | 0.00% | DESTINATARIO |
| MT | IMPORTADA | não | USO_CONSUMO | 17.00% | 4.00% | 13.00% | 0.00% | REMETENTE |
| MT | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| MT | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 10.00% | 0.00% | DESTINATARIO |
| MT | NACIONAL | não | USO_CONSUMO | 17.00% | 7.00% | 10.00% | 0.00% | REMETENTE |
| PA | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| PA | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.00% | 0.00% | DESTINATARIO |
| PA | IMPORTADA | não | USO_CONSUMO | 19.00% | 4.00% | 15.00% | 0.00% | REMETENTE |
| PA | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| PA | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 12.00% | 0.00% | DESTINATARIO |
| PA | NACIONAL | não | USO_CONSUMO | 19.00% | 7.00% | 12.00% | 0.00% | REMETENTE |
| PB | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| PB | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| PB | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| PB | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| PB | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| PB | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| PE | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| PE | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.50% | 0.00% | DESTINATARIO |
| PE | IMPORTADA | não | USO_CONSUMO | 20.50% | 4.00% | 16.50% | 0.00% | REMETENTE |
| PE | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| PE | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.50% | 0.00% | DESTINATARIO |
| PE | NACIONAL | não | USO_CONSUMO | 20.50% | 7.00% | 13.50% | 0.00% | REMETENTE |
| PI | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| PI | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 18.50% | 0.00% | DESTINATARIO |
| PI | IMPORTADA | não | USO_CONSUMO | 22.50% | 4.00% | 18.50% | 0.00% | REMETENTE |
| PI | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| PI | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 15.50% | 0.00% | DESTINATARIO |
| PI | NACIONAL | não | USO_CONSUMO | 22.50% | 7.00% | 15.50% | 0.00% | REMETENTE |
| PR | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| PR | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.50% | 0.00% | DESTINATARIO |
| PR | IMPORTADA | não | USO_CONSUMO | 19.50% | 4.00% | 15.50% | 0.00% | REMETENTE |
| PR | NACIONAL | sim | REVENDA | 12.00% | 12.00% | — | 0.00% | NAO_APLICAVEL |
| PR | NACIONAL | sim | USO_CONSUMO | 12.00% | 12.00% | 7.50% | 0.00% | DESTINATARIO |
| PR | NACIONAL | não | USO_CONSUMO | 19.50% | 12.00% | 7.50% | 0.00% | REMETENTE |
| RJ | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| RJ | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| RJ | IMPORTADA | não | USO_CONSUMO | 22.00% | 4.00% | 16.00% | 2.00% | REMETENTE |
| RJ | NACIONAL | sim | REVENDA | 12.00% | 12.00% | — | 0.00% | NAO_APLICAVEL |
| RJ | NACIONAL | sim | USO_CONSUMO | 12.00% | 12.00% | 8.00% | 0.00% | DESTINATARIO |
| RJ | NACIONAL | não | USO_CONSUMO | 22.00% | 12.00% | 8.00% | 2.00% | REMETENTE |
| RN | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| RN | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| RN | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| RN | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| RN | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| RN | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| RO | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| RO | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.50% | 0.00% | DESTINATARIO |
| RO | IMPORTADA | não | USO_CONSUMO | 19.50% | 4.00% | 15.50% | 0.00% | REMETENTE |
| RO | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| RO | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 12.50% | 0.00% | DESTINATARIO |
| RO | NACIONAL | não | USO_CONSUMO | 19.50% | 7.00% | 12.50% | 0.00% | REMETENTE |
| RR | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| RR | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| RR | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| RR | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| RR | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| RR | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |
| RS | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| RS | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 13.00% | 0.00% | DESTINATARIO |
| RS | IMPORTADA | não | USO_CONSUMO | 17.00% | 4.00% | 13.00% | 0.00% | REMETENTE |
| RS | NACIONAL | sim | REVENDA | 12.00% | 12.00% | — | 0.00% | NAO_APLICAVEL |
| RS | NACIONAL | sim | USO_CONSUMO | 12.00% | 12.00% | 5.00% | 0.00% | DESTINATARIO |
| RS | NACIONAL | não | USO_CONSUMO | 17.00% | 12.00% | 5.00% | 0.00% | REMETENTE |
| SC | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| SC | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 13.00% | 0.00% | DESTINATARIO |
| SC | IMPORTADA | não | USO_CONSUMO | 17.00% | 4.00% | 13.00% | 0.00% | REMETENTE |
| SC | NACIONAL | sim | REVENDA | 12.00% | 12.00% | — | 0.00% | NAO_APLICAVEL |
| SC | NACIONAL | sim | USO_CONSUMO | 12.00% | 12.00% | 5.00% | 0.00% | DESTINATARIO |
| SC | NACIONAL | não | USO_CONSUMO | 17.00% | 12.00% | 5.00% | 0.00% | REMETENTE |
| SE | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| SE | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 15.00% | 0.00% | DESTINATARIO |
| SE | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 15.00% | 1.00% | REMETENTE |
| SE | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| SE | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 12.00% | 0.00% | DESTINATARIO |
| SE | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 12.00% | 1.00% | REMETENTE |
| SP | IMPORTADA | sim | REVENDA | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| SP | IMPORTADA | sim | USO_CONSUMO | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| SP | IMPORTADA | não | USO_CONSUMO | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| SP | NACIONAL | sim | REVENDA | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| SP | NACIONAL | sim | USO_CONSUMO | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| SP | NACIONAL | não | USO_CONSUMO | 18.00% | — | — | 0.00% | NAO_APLICAVEL |
| TO | IMPORTADA | sim | REVENDA | 4.00% | 4.00% | — | 0.00% | NAO_APLICAVEL |
| TO | IMPORTADA | sim | USO_CONSUMO | 4.00% | 4.00% | 16.00% | 0.00% | DESTINATARIO |
| TO | IMPORTADA | não | USO_CONSUMO | 20.00% | 4.00% | 16.00% | 0.00% | REMETENTE |
| TO | NACIONAL | sim | REVENDA | 7.00% | 7.00% | — | 0.00% | NAO_APLICAVEL |
| TO | NACIONAL | sim | USO_CONSUMO | 7.00% | 7.00% | 13.00% | 0.00% | DESTINATARIO |
| TO | NACIONAL | não | USO_CONSUMO | 20.00% | 7.00% | 13.00% | 0.00% | REMETENTE |

## Base interna cadastrada × benchmark

| UF | Base interna (motor) | Interna c/ FCP | FCP escopo | benchmark base simples | benchmark base dupla | benchmark carga final | benchmark FEM |
|---|---:|---:|---:|---:|---:|---:|---:|
| AC | 19.00% | 19.00% | 0.00% | 15.00% | 18.52% | 18.52% | 0.00% |
| AL | 20.50% | 21.50% | 1.00% | 16.00% | 20.00% | 20.00% | 0.00% |
| AM | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| AP | 18.00% | 18.00% | 0.00% | 14.00% | 17.07% | 14.00% | 0.00% |
| BA | 20.50% | 20.50% | 0.00% | 16.50% | 20.75% | 22.75% | 2.00% |
| CE | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| DF | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| ES | 17.00% | 17.00% | 0.00% | 13.00% | 15.66% | 15.66% | 0.00% |
| GO | 19.00% | 19.00% | 0.00% | 15.00% | 18.52% | 18.52% | 0.00% |
| MA | 23.00% | 23.00% | 0.00% | 19.00% | 24.68% | 19.00% | 0.00% |
| MG | 18.00% | 18.00% | 0.00% | 14.00% | 17.07% | 17.07% | 0.00% |
| MS | 17.00% | 17.00% | 0.00% | 13.00% | 15.66% | 13.00% | 0.00% |
| MT | 17.00% | 17.00% | 0.00% | 13.00% | 15.66% | 13.00% | 0.00% |
| PA | 19.00% | 19.00% | 0.00% | 15.00% | 18.52% | 18.52% | 0.00% |
| PB | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| PE | 20.50% | 20.50% | 0.00% | 16.50% | 20.75% | 22.75% | 2.00% |
| PI | 22.50% | 22.50% | 0.00% | 18.50% | 23.87% | 25.87% | 2.00% |
| PR | 19.50% | 19.50% | 0.00% | 15.50% | 19.25% | 19.25% | 0.00% |
| RJ | 20.00% | 22.00% | 2.00% | 18.00% | 23.08% | 20.00% | 2.00% |
| RN | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| RO | 19.50% | 19.50% | 0.00% | 15.50% | 19.25% | 19.25% | 0.00% |
| RR | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 16.00% | 0.00% |
| RS | 17.00% | 17.00% | 0.00% | 13.00% | 15.66% | 15.66% | 0.00% |
| SC | 17.00% | 17.00% | 0.00% | 13.00% | 15.66% | 13.00% | 0.00% |
| SE | 19.00% | 20.00% | 1.00% | 15.00% | 18.52% | 18.52% | 0.00% |
| SP | 18.00% | 18.00% | 0.00% | 14.00% | 17.07% | 17.07% | 0.00% |
| TO | 20.00% | 20.00% | 0.00% | 16.00% | 20.00% | 20.00% | 0.00% |

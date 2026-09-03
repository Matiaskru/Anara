# Backup, restore e rollback — Anara

Política escrita na Fase 0. Vale para o banco `data/anara.db`, que é o único lugar onde
mora dado que não pode ser reconstruído: cotações emitidas, snapshots, custos, premissas
publicadas e histórico.

**O banco não está no Git, e não deve estar.** Git guarda código e documentação; dado de
produção tem rollback por arquivo. As duas coisas são independentes e as duas precisam
existir antes de qualquer onda.

---

## Onde ficam os backups

| Lugar | Quem escreve | Poda automática |
|---|---|---|
| `data/backups/` | `scripts/backup_banco.py` e `app/migrations.py` | **sim** — `app/migrations.py` mantém 30 e apaga o resto |
| `~/Anara-Cotacao-Backups/` | `backup_dados.py` (cópia diária) | mantém 30 diários |
| `~/Anara-Cotacao-Backups/` (marcos) | cópia manual, nome próprio | **não** |

> **Atenção.** `app/migrations.py::fazer_backup` mantém apenas os 30 backups mais recentes
> de `data/backups/` e apaga os demais **sem avisar**. Por isso todo ponto de rollback que
> precisa sobreviver — antes de uma onda, antes de uma migração de dados em massa — é
> copiado também para `~/Anara-Cotacao-Backups/` com nome próprio, onde nada poda.
> O ponto de rollback desta fase é `~/Anara-Cotacao-Backups/anara_fase0_pre_20260903-080837.db`.

---

## Comandos

```bash
# retrato do banco: hash do arquivo, integridade, contagens e digest por tabela/coluna
python3 scripts/backup_banco.py estado --json relatorios/estado.json

# cópia verificada (usa a API de backup do SQLite: consistente mesmo com a plataforma aberta)
python3 scripts/backup_banco.py backup --motivo antes-da-onda-1

# o que existe
python3 scripts/backup_banco.py listar

# um backup presta? (PRAGMA integrity_check + contagens)
python3 scripts/backup_banco.py verificar data/backups/anara.db.fase0-pre-20260903-080837

# ENSAIO: restaura numa cópia temporária e compara — não toca no banco de produção
python3 scripts/backup_banco.py restaurar data/backups/anara.db.fase0-pre-... --ensaio

# restore de verdade (faz backup de segurança do banco atual antes de sobrescrever)
python3 scripts/backup_banco.py restaurar data/backups/anara.db.fase0-pre-... --confirmar
```

`backup` **não apaga nada** por conta própria. Só poda se receber `--reter N`, e diz quais
arquivos removeu.

---

## Procedimento antes de qualquer onda

1. Fechar a plataforma (ou aceitar que a API de backup do SQLite dá uma cópia consistente
   mesmo com ela aberta — é por isso que ela é usada em vez de `cp`).
2. `python3 scripts/backup_banco.py estado --json relatorios/<onda>_antes.json`
3. `python3 scripts/backup_banco.py backup --motivo antes-da-<onda>`
4. Copiar esse backup para `~/Anara-Cotacao-Backups/` com nome próprio.
5. `python3 scripts/backup_banco.py restaurar <backup> --ensaio` — um backup que nunca foi
   restaurado é uma suposição, não um backup.
6. Rodar a onda.
7. `python3 scripts/backup_banco.py estado --json relatorios/<onda>_depois.json` e comparar.

A comparação é por **digest de coluna**: coluna nova aparece como nova, e toda coluna que
já existia tem que ter o mesmo hash. É assim que se afirma "nenhuma cotação histórica
mudou" sem depender de conferência visual.

---

## Rollback

**Dado:** restore do backup (`--confirmar`). O comando faz uma cópia de segurança do banco
atual antes de sobrescrever, então um restore errado também é reversível.

**Esquema:** `python3 -m alembic downgrade <revisão>`. O downgrade da ponte
(`0002 → 0001`) foi ensaiado numa cópia e devolve o banco ao estado anterior com todos os
dados herdados intactos.

**Código:** `git` local. O commit `Estado herdado do sistema Anara (pré-Fase 0)` é o ponto
de retorno anterior a qualquer alteração desta fase.

Ordem recomendada quando algo dá errado numa onda: primeiro restore do dado, depois
`git checkout` do código, nunca o contrário — código novo lendo dado velho é situação
conhecida; dado novo lido por código velho não é.

---

## O que este procedimento não cobre

* **Backup fora da máquina.** Tudo hoje é local. Enquanto for local, um problema de disco
  leva o banco e os backups juntos. Resolver isso é parte da Onda 4 (produção).
* **Point-in-time recovery.** Restaura-se um backup inteiro, não um instante arbitrário.
* **PostgreSQL.** A política acima é do SQLite. A Onda 4 troca o banco e a política de
  backup muda junto (`pg_dump`, WAL archiving).

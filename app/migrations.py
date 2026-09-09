"""Migrations incrementais e idempotentes do SQLite da Anara.

Regra do projeto: **nunca resetar o banco**. Aqui só se cria tabela nova e se acrescenta
coluna nova; nada é apagado nem renumerado. Antes de qualquer alteração de esquema é feito um
backup do arquivo do banco em `data/backups/`.

Roda sozinho no startup da aplicação (`init_db`), e também pode ser chamado direto:

    python3 -m app.migrations
"""
import os
import re
import shutil
from datetime import datetime

from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from app.db import DB_PATH, engine
import app.models  # noqa: F401  — registra as tabelas no metadata

BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")
MAX_BACKUPS = 30


def arquivo_do_banco() -> str:
    """O arquivo que **este processo** está usando agora, lido do `engine` vivo.

    `DB_PATH` é o caminho *default*, fixado na importação. Ler o global aqui era a mesma
    classe de defeito que a Sessão 8 corrigiu no Alembic (B-21): quem apontasse o sistema para
    outra base — `ANARA_DB_URL`, homologação, smoke test — continuava fazendo backup **da
    produção**, não da base em uso. O backup ia para o lugar errado e, pior, era do arquivo
    errado.

    O caso que tornou isso visível foi a suíte: `tests/test_arquivamento.py` troca
    `migrations.engine` por um banco temporário e exercita `apagar()`, que faz backup antes de
    remover. Cada execução da suíte copiava `data/anara.db` para `data/backups/` — cerca de 30
    arquivos numa tarde. Só leitura, então nada se perdeu, mas `MAX_BACKUPS` é 30: a poda
    passou a rodar, e backup real e backup de teste disputavam as mesmas trinta vagas.

    Em produção nada muda: `engine` aponta para `DB_PATH`, e o caminho resolvido é o mesmo.
    """
    caminho = getattr(getattr(engine, "url", None), "database", None)
    return caminho or DB_PATH


def pasta_de_backups(arquivo_do_banco_em_uso: str = "") -> str:
    """`backups/` ao lado do banco em uso. Banco temporário, backups temporários."""
    arquivo = arquivo_do_banco_em_uso or arquivo_do_banco()
    return os.path.join(os.path.dirname(arquivo) or ".", "backups")


def fazer_backup(motivo: str = "migration") -> str:
    """Copia o arquivo do banco antes de mexer no esquema. Devolve o caminho do backup."""
    origem = arquivo_do_banco()
    if not origem or not os.path.exists(origem):
        return ""
    pasta = pasta_de_backups(origem)
    os.makedirs(pasta, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # O nome carrega o basename do banco de origem: em produção é `anara.db`, exatamente como
    # antes; num banco temporário, é o nome dele — e a cópia não se confunde com a de produção.
    destino = os.path.join(pasta, f"{os.path.basename(origem)}.{motivo}-{stamp}")
    shutil.copy2(origem, destino)
    _podar_backups(preservar=destino, pasta=pasta,
                   prefixo=f"{os.path.basename(origem)}.")
    return destino


#: `anara.db.<motivo>-AAAAMMDD-HHMMSS`. O carimbo é o que ordena; o motivo é texto livre e
#: não pode participar da decisão de quem é apagado.
_CARIMBO = re.compile(r"-(\d{8}-\d{6})$")


def _idade_do_backup(caminho: str):
    """Chave de ordenação: o carimbo do nome, com o mtime como desempate.

    **B-18.** A poda ordenava por `os.path.getmtime`, e `shutil.copy2` **preserva o mtime da
    origem** — então todas as cópias do mesmo `anara.db` ficavam com o mesmo mtime. Com o
    empate, quem decidia era a ordem arbitrária de `os.listdir`, e o arquivo recém-criado
    podia ser justamente o removido. Foi o que aconteceu quando o diretório passou de 30
    arquivos: o backup de segurança desta sessão desapareceu no instante em que foi feito.

    Antes disso a ordenação era alfabética, e aí o **motivo** decidia — um backup com motivo
    de letra baixa morria na frente de um antigo com letra alta. Trocar nome por mtime moveu
    o problema em vez de encerrá-lo; a informação correta sempre esteve no carimbo.
    """
    achado = _CARIMBO.search(os.path.basename(caminho))
    return (achado.group(1) if achado else "", os.path.getmtime(caminho))


def _podar_backups(preservar: str = "", pasta: str = "", maximo: int = 0,
                   prefixo: str = "") -> list:
    """Mantém os `maximo` backups mais recentes. Nunca remove `preservar`.

    `pasta`, `maximo` e `prefixo` são parâmetros — e não leitura dos globais — para que o
    teste exercite a função num diretório próprio sem trocar o estado do módulo. Trocar
    global em teste vaza para quem rodar depois, e neste módulo o global é o caminho do banco.

    `prefixo` existe desde que o backup passou a nomear o arquivo pelo banco de origem: num
    banco temporário os backups se chamam `<nome>.db.<motivo>-<carimbo>`, e uma poda presa ao
    literal `anara.db.` ou não os enxergaria, ou — pior — enxergaria os da produção.
    """
    pasta = pasta or BACKUP_DIR
    maximo = maximo or MAX_BACKUPS
    prefixo = prefixo or "anara.db."
    if not os.path.isdir(pasta):
        return []
    caminhos = [os.path.join(pasta, f) for f in os.listdir(pasta)
                if f.startswith(prefixo)]
    if len(caminhos) <= maximo:
        return []
    alvo = os.path.abspath(preservar) if preservar else None
    removidos = []
    for antigo in sorted(caminhos, key=_idade_do_backup)[:-maximo]:
        if alvo and os.path.abspath(antigo) == alvo:
            continue          # o recém-criado nunca é o descartado
        os.remove(antigo)
        removidos.append(antigo)
    return removidos


def _sqlite_tipo(coluna) -> str:
    tipo = coluna.type.__class__.__name__.upper()
    if "INT" in tipo or tipo == "BOOLEAN":
        return "INTEGER"
    if "FLOAT" in tipo or "NUMERIC" in tipo or "DECIMAL" in tipo:
        return "FLOAT"
    if "DATETIME" in tipo:
        return "DATETIME"
    if tipo == "DATE":
        return "DATE"
    return "VARCHAR"


def _default_sql(coluna):
    """Só usa DEFAULT quando é constante — SQLite não aceita default dinâmico em ADD COLUMN."""
    d = coluna.default
    if d is None or getattr(d, "is_callable", False):
        return None
    valor = getattr(d, "arg", None)
    if callable(valor) or valor is None:
        return None
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, (int, float)):
        return str(valor)
    if hasattr(valor, "value"):          # Enum
        return f"'{valor.value}'"
    return "'" + str(valor).replace("'", "''") + "'"


def migrar(verbose: bool = True) -> dict:
    """Cria tabelas novas e acrescenta colunas novas. Idempotente."""
    inspetor = inspect(engine)
    tabelas_existentes = set(inspetor.get_table_names())
    tabelas_novas = [t for t in SQLModel.metadata.tables if t not in tabelas_existentes]

    colunas_faltando = []
    for nome_tabela, tabela in SQLModel.metadata.tables.items():
        if nome_tabela not in tabelas_existentes:
            continue
        atuais = {c["name"] for c in inspetor.get_columns(nome_tabela)}
        for coluna in tabela.columns:
            if coluna.name not in atuais:
                colunas_faltando.append((nome_tabela, coluna))

    if not tabelas_novas and not colunas_faltando:
        return {"backup": "", "tabelas_criadas": [], "colunas_adicionadas": []}

    backup = fazer_backup()
    if verbose and backup:
        print(f"[migrations] backup do banco em {backup}")

    SQLModel.metadata.create_all(engine)

    adicionadas = []
    with engine.begin() as con:
        for nome_tabela, coluna in colunas_faltando:
            ddl = f'ALTER TABLE "{nome_tabela}" ADD COLUMN "{coluna.name}" {_sqlite_tipo(coluna)}'
            padrao = _default_sql(coluna)
            if padrao is not None:
                ddl += f" DEFAULT {padrao}"
            con.execute(text(ddl))
            adicionadas.append(f"{nome_tabela}.{coluna.name}")
            if verbose:
                print(f"[migrations] + {nome_tabela}.{coluna.name}")

    if verbose and tabelas_novas:
        print(f"[migrations] tabelas criadas: {', '.join(sorted(tabelas_novas))}")

    return {"backup": backup, "tabelas_criadas": sorted(tabelas_novas),
            "colunas_adicionadas": adicionadas}


def backfill(verbose: bool = True) -> dict:
    """Preenche o que as colunas novas precisam ter em registros antigos.

    Não altera valor comercial nenhum de cotação histórica: só numera cotações sem número e
    completa campos operacionais em branco.
    """
    from sqlmodel import Session, select

    from app.models import Cotacao, TipoFrete

    resultado = {"cotacoes_numeradas": 0, "frete_preenchido": 0}
    with Session(engine) as session:
        cotacoes = session.exec(select(Cotacao).order_by(Cotacao.id)).all()
        usados = {c.numero for c in cotacoes if c.numero}
        for c in cotacoes:
            if not c.numero:
                ano = (c.criado_em or datetime.utcnow()).year
                c.numero = _proximo_numero(ano, usados, sugestao=c.id)
                usados.add(c.numero)
                resultado["cotacoes_numeradas"] += 1
                session.add(c)
            if not c.freight_type:
                c.freight_type = TipoFrete.cif.value
                resultado["frete_preenchido"] += 1
                session.add(c)
        session.commit()
    if verbose and any(resultado.values()):
        print(f"[migrations] backfill: {resultado}")
    return resultado


def _proximo_numero(ano: int, usados: set, sugestao: int) -> str:
    """Numeração histórica preservada: usa o id da cotação como sequência, sem renumerar nada."""
    n = sugestao
    while f"ANARA-{ano}-{n:04d}" in usados:
        n += 1
    return f"ANARA-{ano}-{n:04d}"


if __name__ == "__main__":
    print(migrar())
    print(backfill())

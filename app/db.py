# ---------------------------------------------------------------------------
# app/db.py — acesso ao SQLite e definição do esquema (USUARIO, DISPOSITIVO, EVENTO).
# Onde roda: notebook da casa, dentro do processo do webSentinela (app/main.py,
#   app/listener.py) e do init_db.py.
# Conexões: lê WEBSENTINELA_DB (caminho do banco) do ambiente; padrão websentinela.db
#   na raiz do projeto. Todo acesso ao banco no projeto passa por conexao().
# ---------------------------------------------------------------------------
"""Acesso ao SQLite (websentinela.db) e definição do esquema."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Raiz do projeto (pasta websentinela/) e caminho do banco, sobrescrevível por
# variável de ambiente (útil para testar com uma cópia do banco).
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("WEBSENTINELA_DB", BASE_DIR / "websentinela.db"))

# Esquema das três tabelas. Os CHECKs garantem perfil e ativo válidos; EVENTO tem
# FK com ON DELETE CASCADE (excluir um dispositivo apaga seus eventos). O índice
# por data_hora acelera a lista de últimos eventos do painel.
SCHEMA = """
CREATE TABLE IF NOT EXISTS USUARIO (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT    NOT NULL,
    login       TEXT    NOT NULL UNIQUE,
    senha_hash  TEXT    NOT NULL,
    perfil      TEXT    NOT NULL CHECK (perfil IN ('ADM', 'APOIO')),
    ativo       INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE IF NOT EXISTS DISPOSITIVO (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id  TEXT    NOT NULL UNIQUE,
    mac      TEXT    NOT NULL UNIQUE,
    comodo   TEXT    NOT NULL DEFAULT '',
    ativo    INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE IF NOT EXISTS EVENTO (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo_id  INTEGER NOT NULL REFERENCES DISPOSITIVO(id) ON DELETE CASCADE,
    tipo            TEXT    NOT NULL,
    valor           TEXT,
    data_hora       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evento_data_hora ON EVENTO (data_hora DESC);
"""


# Abre uma conexão: linhas acessíveis por nome (sqlite3.Row) e chaves estrangeiras
# ligadas (no SQLite elas vêm desligadas por padrão).
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Uso: `with conexao() as conn:`. Conexão curta por operação, segura para chamadas
# a partir de threads diferentes (o listener grava eventos por aqui).
@contextmanager
def conexao():
    """Conexão curta: commit ao sair sem erro, rollback em caso de exceção."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Cria as tabelas se não existirem (chamado pelo init_db.py e no startup do app).
def criar_esquema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

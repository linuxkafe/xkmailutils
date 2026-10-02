"""Acesso ao SQLite e migrações de esquema.

Porquê stdlib e não SQLAlchemy: não está instalado, e o projecto não pode ganhar
dependências. Quatro tabelas não justificam um ORM. O custo é este ficheiro
concentrar o esquema — por isso está listado como *critical file* no
CLAUDE.md. (NFR-10)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Versão do esquema. Incrementar sempre que `migrate()` acrescenta DDL, e
#: acrescentar o bloco correspondente em `_MIGRATIONS`.
SCHEMA_VERSION = 4

#: Estrutura por omissão de uma assinatura guardada.
#:
#: Literal, e não `signatures.renderer.DEFAULT_LAYOUT`: `db.py` é o módulo de
#: baixo nível e importar um módulo de functionality para preencher um `DEFAULT`
#: de esquema inverte a ordem das dependências. A ligação entre os dois é
#: assegurada por `tests/test_editor_flows.py`, que falha se este valor divergir
#: do que o renderer usa.
DEFAULT_SIGNATURE_LAYOUT = "stack"

#: Statements idempotentes aplicados em ordem. SQLite não faz rollback de
#: schema; por isso só se acrescenta, nunca se reescreve uma migração já aplicada.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        email             TEXT    NOT NULL UNIQUE,
        password_hash     TEXT    NOT NULL,
        is_admin          INTEGER NOT NULL DEFAULT 0,
        is_active         INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT    NOT NULL,
        password_changed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        fingerprint   TEXT    NOT NULL,
        user_agent    TEXT    NOT NULL DEFAULT '',
        label         TEXT    NOT NULL DEFAULT '',
        created_at    TEXT    NOT NULL,
        last_seen_at  TEXT    NOT NULL,
        UNIQUE (user_id, fingerprint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS otp_codes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        device_fp    TEXT    NOT NULL DEFAULT '',
        code_hash    TEXT    NOT NULL,
        purpose      TEXT    NOT NULL DEFAULT 'login',
        attempts     INTEGER NOT NULL DEFAULT 0,
        consumed_at  TEXT,
        created_at   TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash    TEXT PRIMARY KEY,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        device_id     INTEGER REFERENCES devices(id) ON DELETE SET NULL,
        csrf_token    TEXT    NOT NULL,
        created_at    TEXT    NOT NULL,
        expires_at    TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS invites (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        email        TEXT    NOT NULL,
        token_hash   TEXT    NOT NULL UNIQUE,
        created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at   TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL,
        accepted_at  TEXT,
        revoked_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier  TEXT    NOT NULL,
        ip          TEXT    NOT NULL DEFAULT '',
        success     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        filename    TEXT    NOT NULL UNIQUE,
        content_type TEXT   NOT NULL,
        byte_size   INTEGER NOT NULL,
        width       INTEGER NOT NULL,
        height      INTEGER NOT NULL,
        created_at  TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signatures (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name        TEXT    NOT NULL DEFAULT 'assinatura principal',
        theme       TEXT    NOT NULL DEFAULT 'dark',
        fields_json TEXT    NOT NULL DEFAULT '{}',
        logo_id     INTEGER REFERENCES logos(id) ON DELETE SET NULL,
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL,
        UNIQUE (user_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipient_lists (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name       TEXT    NOT NULL,
        created_at TEXT    NOT NULL,
        UNIQUE (user_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS list_addresses (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        list_id                INTEGER NOT NULL REFERENCES recipient_lists(id) ON DELETE CASCADE,
        email                  TEXT    NOT NULL,
        name                   TEXT    NOT NULL DEFAULT '',
        confirmed_at           TEXT,
        confirmation_hash      TEXT,
        confirmation_expires_at TEXT,
        confirmation_attempts  INTEGER NOT NULL DEFAULT 0,
        confirmation_sent_at   TEXT,
        unsubscribed_at        TEXT,
        created_at             TEXT    NOT NULL,
        UNIQUE (list_id, email)
    )
    CREATE TABLE IF NOT EXISTS list_envios (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        list_id                INTEGER NOT NULL REFERENCES recipient_lists(id) ON DELETE CASCADE,
        subject                TEXT    NOT NULL,
        body                   TEXT    NOT NULL,
        state                  TEXT    NOT NULL DEFAULT 'agendado',
        scheduled_at           TEXT    NOT NULL,
        claimed_at             TEXT,
        sent_at                TEXT,
        failed_at              TEXT,
        created_at             TEXT    NOT NULL,
        CHECK (state IN ('agendado', 'enviando', 'enviado', 'falhado'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_otp_user ON otp_codes (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_identifier ON login_attempts (identifier, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_signatures_user ON signatures (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_recipient_lists_user ON recipient_lists (user_id)",
    # O cooldown da confirmação é por endereço, em qualquer lista: espalhar o
    # mesmo endereço por cinco listas não contorna o cooldown.
    "CREATE INDEX IF NOT EXISTS idx_list_addresses_email ON list_addresses (email)",
    "CREATE INDEX IF NOT EXISTS idx_list_addresses_list ON list_addresses (list_id)",
)


def _add_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    """Acrescenta uma coluna se ainda não existir.

    `ALTER TABLE ... ADD COLUMN` **não** é idempotente no SQLite: a segunda vez
    levanta `duplicate column name`. Todas as entradas de `_MIGRATIONS` são
    `CREATE ... IF NOT EXISTS` e por isso são seguras de reexecutar; um `ALTER`
    não é, e `migrate()` reexecuta a lista toda em cada arranque. O guarda aqui
    é o que mantém a lista no mesmo formato — toda idempotente.

    Porquê coluna nova em vez de guardar o layout dentro de `fields_json`:
    `fields_json` é o que o utilizador escreve no formulário, e o layout é uma
    escolha da interface. Misturá-los faria com que uma assinatura restaurada de
    um backup antigo trouxesse um layout que o utilizador nunca escolheu.
    """
    existentes = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existentes:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_otp_user ON otp_codes (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_identifier ON login_attempts (identifier, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_signatures_user ON signatures (user_id)",
)

TABLE_NAMES = (
    "users",
    "recipient_lists",
    "list_addresses",
    "devices",
    "otp_codes",
    "sessions",
    "invites",
    "login_attempts",
    "logos",
    "signatures",
)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Abre a base de dados com as garantias que a app precisa.

    `foreign_keys` é desligado por omissão no SQLite; sem esta linha as
    `ON DELETE CASCADE` da migração não existem e um `DELETE` de utilizador
    deixa órfãos.

    `check_same_thread=False` é necessário, não preguiçoso: o FastAPI executa
    handlers síncronos e dependências com *generator* num thread pool do
    anyio, e a criação e o fecho de uma dependência de request podem correr em
    threads diferentes. A ligação nunca é partilhada entre requests — nasce
    dentro de um e morre dentro do mesmo.
    """
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transacção explícita. `isolation_level=None` desliga o autocommit do
    driver, portanto o commit é nosso e explícito em cada bloco."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> None:
    """Aplica as migrações pendentes. Idempotente.

    O `user_version` é escrito *depois* do commit, não dentro da transacção: um
    reader concorrente que veja `user_version` já actualizado tem de poder
    assumir que as tabelas existem.
    """
    with transaction(conn):
        for statement in _MIGRATIONS:
            conn.execute(statement)
        # Fora de `_MIGRATIONS` porque não é um statement: é um guarda. Ver a
        # docstring de `_add_column`.
        _add_column(
            conn,
            "signatures",
            "layout",
            f"TEXT NOT NULL DEFAULT '{DEFAULT_SIGNATURE_LAYOUT}'",
        )
        _add_column(
            conn,
            "recipient_lists",
            "cadence_seconds",
            "INTEGER NOT NULL DEFAULT 2",
        )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def loads_fields(raw: str) -> dict[str, Any]:
    """Lê a coluna JSON de campos da assinatura.

    Devolve `{}` em vez de rebentar: um registo com JSON corrompido não pode
    impedir o utilizador de entrar na aplicação.
    """
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def dumps_fields(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "dumps_fields",
    "loads_fields",
    "migrate",
    "transaction",
]

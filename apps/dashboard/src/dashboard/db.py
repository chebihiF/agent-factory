"""Persistance SQLite des événements d'agents et des tokens (stdlib `sqlite3`)."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = "vva.db"
DEFAULT_PROJECT = "viral-video-agents"  # projet des événements sans champ `project` (#68)
EVENT_COLUMNS = (
    "event_id",
    "ts",
    "hook_event_name",
    "session_id",
    "agent_type",
    "agent_id",
    "tool_name",
    "project",
    "issue",
    "model",
)
EventRow = tuple[str | int | None, ...]
TOKEN_COLUMNS = ("point_id", "ts", "session_id", "agent_id", "model", "type", "count")
TokenRow = tuple[str, str, str, str | None, str, str, int]
ControlRow = tuple[bool, str | None, int, str]
DEFAULT_CAP_TOKENS = 500_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    hook_event_name TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    agent_type      TEXT,
    agent_id        TEXT,
    tool_name       TEXT,
    received_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_agent_id ON events (agent_id);
CREATE TABLE IF NOT EXISTS token_usage (
    point_id    TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    agent_id    TEXT,
    model       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('input', 'output', 'cacheRead', 'cacheCreation')),
    count       INTEGER NOT NULL CHECK (count >= 0),
    -- même format que ts (isoformat microsecondes, +00:00)
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage (ts);
-- une seule ligne (id = 1) : bilan des lots OTLP authentifiés et lisibles (#184), pour que
-- /tokens dise pourquoi elle est vide (rien reçu / tout rejeté / pas de compteur de tokens)
CREATE TABLE IF NOT EXISTS otlp_receipt (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    last_at  TEXT,
    batches  INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO otlp_receipt (id) VALUES (1);
-- une seule ligne (id = 1) : pause des agents et plafond de tokens sur 24 h (#8)
CREATE TABLE IF NOT EXISTS controls (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    paused     INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    reason     TEXT CHECK (reason IN ('manuel', 'plafond')),
    cap_tokens INTEGER NOT NULL DEFAULT 500000 CHECK (cap_tokens > 0),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK ((paused = 0 AND reason IS NULL) OR (paused = 1 AND reason IS NOT NULL))
);
INSERT OR IGNORE INTO controls (id) VALUES (1);
-- dernière réponse GitHub par URL (#186) : ETag + corps JSON public, relus au redémarrage
-- (requête conditionnelle, liste affichée même sous la limite de 60 req/h) ; aucun jeton
CREATE TABLE IF NOT EXISTS github_cache (
    url        TEXT PRIMARY KEY,
    etag       TEXT NOT NULL,
    body       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    return (
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "  # noqa: S608 - noms constants
        f"VALUES ({', '.join('?' for _ in columns)})"
    )


_INSERT = _insert_sql("events", EVENT_COLUMNS)
# Colonnes ajoutées après coup : `project` (#68), `issue` et `model` (#181).
_ADD_COLUMNS = (
    f"ALTER TABLE events ADD COLUMN project TEXT NOT NULL DEFAULT '{DEFAULT_PROJECT}'",
    "ALTER TABLE events ADD COLUMN issue INTEGER",
    "ALTER TABLE events ADD COLUMN model TEXT",
)
_INSERT_TOKENS = _insert_sql("token_usage", TOKEN_COLUMNS)


BUSY_TIMEOUT_S = 5.0
_initialized: set[Path] = set()
_init_lock = threading.Lock()


def db_path() -> Path:
    return Path(os.environ.get("VVA_DB_PATH") or DEFAULT_DB_PATH)


def add_event_columns(conn: sqlite3.Connection) -> None:
    """Colonnes `project`, `issue`, `model`, seule définition : base neuve ou antérieure.

    Idempotent : « duplicate column » (déjà là, ou ajoutée par un autre processus) est ignoré.
    """
    for statement in _ADD_COLUMNS:
        try:
            with conn:
                conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project ON events (project)")
    # agrégation des sous-agents de /interactions (#181)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_session_agent ON events (session_id, agent_id, ts)"
    )


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    add_event_columns(conn)


def _ensure_schema(conn: sqlite3.Connection, path: Path) -> None:
    """Crée le schéma au premier accès à un fichier donné (une seule fois par processus)."""
    with _init_lock:
        if path not in _initialized:
            init_schema(conn)
            _initialized.add(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Ouvre la base désignée par `VVA_DB_PATH` et garantit le schéma."""
    path = db_path().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    try:
        _ensure_schema(conn, path)
        yield conn
    finally:
        conn.close()


def _insert_many(conn: sqlite3.Connection, sql: str, rows: Sequence[Sequence[object]]) -> int:
    with conn:
        before = conn.total_changes
        conn.executemany(sql, rows)
        return conn.total_changes - before


def insert_events(conn: sqlite3.Connection, rows: Sequence[EventRow]) -> int:
    """Insère un lot de façon atomique ; ignore les `event_id` connus. Renvoie le nb inséré."""
    return _insert_many(conn, _INSERT, rows)


def insert_token_usage(conn: sqlite3.Connection, rows: Sequence[TokenRow]) -> int:
    """Insère un lot de façon atomique ; ignore les `point_id` connus. Renvoie le nb inséré."""
    return _insert_many(conn, _INSERT_TOKENS, rows)


_UPDATE_RECEIPT = (
    "UPDATE otlp_receipt SET last_at = ?, batches = batches + 1, "
    "accepted = accepted + ?, rejected = rejected + ? WHERE id = 1"
)


def insert_token_batch(
    conn: sqlite3.Connection, rows: Sequence[TokenRow], rejected: int, at: str
) -> int:
    """Points de tokens et bilan du lot dans UNE transaction. Renvoie le nb de lignes insérées."""
    with conn:
        before = conn.total_changes
        conn.executemany(_INSERT_TOKENS, rows)
        inserted = conn.total_changes - before
        conn.execute(_UPDATE_RECEIPT, (at, len(rows), rejected))
        return inserted


def otlp_receipt(conn: sqlite3.Connection) -> tuple[str | None, int, int, int]:
    """(dernier lot, nb de lots, points retenus, points rejetés)."""
    row = conn.execute("SELECT last_at, batches, accepted, rejected FROM otlp_receipt").fetchone()
    return (row[0], int(row[1]), int(row[2]), int(row[3]))


_TOUCH = "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def read_controls(conn: sqlite3.Connection) -> ControlRow:
    """(en pause, motif, plafond, mis à jour) ; la ligne unique est créée avec le schéma."""
    paused, reason, cap, updated_at = conn.execute(
        "SELECT paused, reason, cap_tokens, updated_at FROM controls WHERE id = 1"
    ).fetchone()
    return bool(paused), reason, int(cap), str(updated_at)


def pause(conn: sqlite3.Connection, reason: str) -> None:
    """Met en pause ; sans effet si déjà en pause (le premier motif est conservé)."""
    with conn:
        conn.execute(
            f"UPDATE controls SET paused = 1, reason = ?, {_TOUCH} "  # noqa: S608 - constante
            "WHERE id = 1 AND paused = 0",
            (reason,),
        )


def resume(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            f"UPDATE controls SET paused = 0, reason = NULL, {_TOUCH} "  # noqa: S608 - constante
            "WHERE id = 1 AND paused = 1"
        )


def set_cap(conn: sqlite3.Connection, cap_tokens: int) -> None:
    with conn:
        conn.execute(
            f"UPDATE controls SET cap_tokens = ?, {_TOUCH} WHERE id = 1",  # noqa: S608 - constante
            (cap_tokens,),
        )


GithubRow = tuple[str, str, float]  # (etag, corps JSON, heure du dernier succès, epoch s)


def read_github_cache(conn: sqlite3.Connection, url: str) -> GithubRow | None:
    row = conn.execute(
        "SELECT etag, body, fetched_at FROM github_cache WHERE url = ?", (url,)
    ).fetchone()
    return (str(row[0]), str(row[1]), float(row[2])) if row else None


def write_github_cache(
    conn: sqlite3.Connection, url: str, etag: str, body: str, fetched_at: float
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO github_cache (url, etag, body, fetched_at) VALUES (?, ?, ?, ?)",
            (url, etag, body, fetched_at),
        )


def touch_github_cache(conn: sqlite3.Connection, url: str, fetched_at: float) -> None:
    with conn:
        conn.execute("UPDATE github_cache SET fetched_at = ? WHERE url = ?", (fetched_at, url))

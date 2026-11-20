"""Ingestion authentifiée des événements d'agents (#3)."""

from __future__ import annotations

import hmac
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard import db, ingest
from dashboard.main import app

TOKEN = "test-token-not-a-secret"  # noqa: S105 - jeton factice de test
URL = "/api/v1/events"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "agent-event.schema.json"


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vva.db"
    monkeypatch.setenv("VVA_DB_PATH", str(path))
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(app)


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def event(event_id: str = "evt-1", **extra: Any) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "ts": "2026-11-18T10:00:00+00:00",
        "hook_event_name": "PreToolUse",
        "session_id": "sess-1",
        "agent_type": "backend",
        "agent_id": "agent-1",
        "tool_name": "Bash",
        **extra,
    }


def count_events(path: Path) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE name = 'events'").fetchall()
        if not tables:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
    return int(row[0])


# Scénario : événement valide accepté
def test_valid_event_is_accepted_and_stored(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event(), headers=auth())
    assert resp.status_code == 202
    assert resp.json() == {"inserted": 1, "duplicates": 0}
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_id, hook_event_name, agent_id, tool_name FROM events"
        ).fetchone()
    assert row == ("evt-1", "PreToolUse", "agent-1", "Bash")


def test_optional_fields_may_be_null_or_absent(client: TestClient, db_path: Path) -> None:
    minimal = {k: v for k, v in event().items() if k not in {"agent_id", "tool_name"}}
    minimal["agent_type"] = None
    resp = client.post(URL, json=minimal, headers=auth())
    assert resp.status_code == 202
    assert count_events(db_path) == 1


def test_ts_is_stored_in_utc(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event(ts="2026-11-18T11:00:00+01:00"), headers=auth())
    assert resp.status_code == 202
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT ts FROM events").fetchone()
    assert row == ("2026-11-18T10:00:00+00:00",)


@pytest.mark.parametrize("ts", [1763460000, "2026-11-18T10:00:00"], ids=["entier", "sans-fuseau"])
def test_ts_must_be_aware_datetime_string(client: TestClient, db_path: Path, ts: int | str) -> None:
    assert client.post(URL, json=event(ts=ts), headers=auth()).status_code == 422
    assert count_events(db_path) == 0


# Taille du corps
def test_body_over_limit_is_413(client: TestClient, db_path: Path) -> None:
    body = b" " * (ingest.MAX_BODY_BYTES + 1)
    resp = client.post(URL, content=body, headers=auth())
    assert resp.status_code == 413
    assert count_events(db_path) == 0


def test_body_over_limit_without_content_length_is_413(client: TestClient, db_path: Path) -> None:
    def chunks() -> Any:
        for _ in range(ingest.MAX_BODY_BYTES // 1024 + 2):
            yield b" " * 1024

    resp = client.post(URL, content=chunks(), headers=auth())
    assert resp.status_code == 413
    assert count_events(db_path) == 0


def test_body_size_checked_after_auth(client: TestClient) -> None:
    body = b" " * (ingest.MAX_BODY_BYTES + 1)
    assert client.post(URL, content=body, headers=auth("wrong")).status_code == 401


# Persistance
def test_schema_is_created_once_and_uses_wal(
    client: TestClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = db.init_schema

    def spy(conn: sqlite3.Connection) -> None:
        calls.append("init")
        real(conn)

    monkeypatch.setattr(db, "init_schema", spy)
    client.post(URL, json=event("a"), headers=auth())
    client.post(URL, json=event("b"), headers=auth())
    assert len(calls) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_sqlite_runs_in_threadpool(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    offloaded: list[str] = []
    real = ingest.run_in_threadpool

    async def spy(func: Any, *args: Any) -> Any:
        offloaded.append(func.__name__)
        return await real(func, *args)

    monkeypatch.setattr(ingest, "run_in_threadpool", spy)
    assert client.post(URL, json=event(), headers=auth()).status_code == 202
    assert offloaded == ["store_events"]


# Scénario : jeton absent ou faux
@pytest.mark.parametrize(
    "headers",
    [{}, auth("wrong"), {"Authorization": TOKEN}, {"Authorization": "Basic abc"}],
    ids=["absent", "faux", "sans-bearer", "basic"],
)
def test_missing_or_wrong_token_is_401(
    client: TestClient, db_path: Path, headers: dict[str, str]
) -> None:
    resp = client.post(URL, json=event(), headers=headers)
    assert resp.status_code == 401
    assert count_events(db_path) == 0


def test_auth_is_checked_before_body_validation(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, content=b"{not json", headers=auth("wrong"))
    assert resp.status_code == 401
    resp = client.post(URL, json=event(prompt="secret"), headers={})
    assert resp.status_code == 401
    assert count_events(db_path) == 0


def test_token_compared_in_constant_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ingest.hmac, "compare_digest", spy)
    client.post(URL, json=event(), headers=auth())
    assert calls


@pytest.mark.parametrize("value", [None, ""], ids=["absent", "vide"])
def test_missing_ingest_token_closes_service(
    client: TestClient, db_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("INGEST_TOKEN")
    else:
        monkeypatch.setenv("INGEST_TOKEN", value)
    for headers in ({}, auth(""), auth(TOKEN)):
        resp = client.post(URL, json=event(), headers=headers)
        assert resp.status_code == 503
    assert count_events(db_path) == 0


# Scénario : idempotence
def test_resending_same_event_is_200_without_duplicate(client: TestClient, db_path: Path) -> None:
    assert client.post(URL, json=event(), headers=auth()).status_code == 202
    resp = client.post(URL, json=event(), headers=auth())
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 0, "duplicates": 1}
    assert count_events(db_path) == 1


def test_batch_with_some_new_events_is_202(client: TestClient, db_path: Path) -> None:
    client.post(URL, json=event("a"), headers=auth())
    resp = client.post(URL, json=[event("a"), event("b")], headers=auth())
    assert resp.status_code == 202
    assert resp.json() == {"inserted": 1, "duplicates": 1}
    assert count_events(db_path) == 2


# Scénario : champ interdit
def test_unknown_field_is_422_and_nothing_written(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event(prompt="raconte une histoire"), headers=auth())
    assert resp.status_code == 422
    assert count_events(db_path) == 0


@pytest.mark.parametrize("missing", ["event_id", "ts", "hook_event_name", "session_id"])
def test_missing_required_field_is_422(client: TestClient, db_path: Path, missing: str) -> None:
    body = event()
    del body[missing]
    assert client.post(URL, json=body, headers=auth()).status_code == 422
    assert count_events(db_path) == 0


def test_invalid_json_is_422(client: TestClient) -> None:
    resp = client.post(URL, content=b"{not json", headers=auth())
    assert resp.status_code == 422


# Lots
def test_batch_of_100_is_accepted(client: TestClient, db_path: Path) -> None:
    batch = [event(f"evt-{i}") for i in range(100)]
    assert client.post(URL, json=batch, headers=auth()).status_code == 202
    assert count_events(db_path) == 100


@pytest.mark.parametrize("size", [0, 101], ids=["vide", "trop-grand"])
def test_batch_out_of_bounds_is_422(client: TestClient, db_path: Path, size: int) -> None:
    batch = [event(f"evt-{i}") for i in range(size)]
    assert client.post(URL, json=batch, headers=auth()).status_code == 422
    assert count_events(db_path) == 0


def test_batch_is_atomic(client: TestClient, db_path: Path) -> None:
    batch = [event("ok-1"), event("ok-2"), event("bad", prompt="x")]
    assert client.post(URL, json=batch, headers=auth()).status_code == 422
    assert count_events(db_path) == 0


# Contrat : le modèle pydantic est aligné sur contracts/agent-event.schema.json
def test_model_matches_contract_schema() -> None:
    contract = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    model = ingest.AgentEvent.model_json_schema()
    assert contract["additionalProperties"] is False
    assert model["additionalProperties"] is False
    assert set(model["properties"]) == set(contract["properties"])
    assert set(model["required"]) == set(contract["required"])
    for name, spec in contract["properties"].items():
        nullable = isinstance(spec["type"], list) and "null" in spec["type"]
        types = {t.get("type") for t in model["properties"][name].get("anyOf", [])}
        assert nullable == ("null" in types), name


# Cas limites (QA)
def test_unicode_darija_roundtrip(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event("evt-ع", tool_name="كتابة"), headers=auth())
    assert resp.status_code == 202
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT event_id, tool_name FROM events").fetchone()
    assert row == ("evt-ع", "كتابة")


def test_duplicate_inside_same_batch_stored_once(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=[event("d"), event("d")], headers=auth())
    assert resp.status_code == 202
    assert count_events(db_path) == 1


@pytest.mark.parametrize("bad", [{"event_id": ""}, {"ts": "2026-11-18T10:00:00"}, {"ts": "hier"}])
def test_empty_id_and_invalid_ts_are_422(client: TestClient, db_path: Path, bad: dict) -> None:
    assert client.post(URL, json=event(**bad), headers=auth()).status_code == 422
    assert count_events(db_path) == 0


def test_indexes_on_ts_and_agent_id(client: TestClient, db_path: Path) -> None:
    client.post(URL, json=event(), headers=auth())
    with sqlite3.connect(db_path) as conn:
        cols = {
            c[2]
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            for c in conn.execute(f'PRAGMA index_info("{name}")')
        }
    assert {"ts", "agent_id"} <= cols


# Scénario (#68) : ancien événement accepté
def test_event_without_project_is_stored_with_default(client: TestClient, db_path: Path) -> None:
    assert client.post(URL, json=event(), headers=auth()).status_code == 202
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT project FROM events").fetchone()
    assert row == ("viral-video-agents",)


# Scénario (#68) : nouvel événement stocké avec son projet
def test_event_with_project_is_stored(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event(project="agent-factory"), headers=auth())
    assert resp.status_code == 202
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT project FROM events").fetchone()
    assert row == ("agent-factory",)


@pytest.mark.parametrize("bad", [None, "", 42, "a" * 101, "a b", "../x", "-x"])
def test_invalid_project_is_422(client: TestClient, db_path: Path, bad: object) -> None:
    assert client.post(URL, json=event(project=bad), headers=auth()).status_code == 422
    assert count_events(db_path) == 0


OLD_EVENTS_TABLE = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, hook_event_name TEXT NOT NULL,
    session_id TEXT NOT NULL, agent_type TEXT, agent_id TEXT, tool_name TEXT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
INSERT INTO events (event_id, ts, hook_event_name, session_id)
VALUES ('old-1', '2026-11-17T10:00:00+00:00', 'Stop', 'sess-0');
"""


def test_existing_database_is_migrated(client: TestClient, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(OLD_EVENTS_TABLE)
    resp = client.post(URL, json=event(project="agent-factory"), headers=auth())
    assert resp.status_code == 202
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT event_id, project FROM events ORDER BY ts").fetchall()
        indexed = {c[2] for c in conn.execute("PRAGMA index_info('idx_events_project')")}
    assert rows == [("old-1", "viral-video-agents"), ("evt-1", "agent-factory")]
    assert indexed == {"project"}


# Contrat (#68) : défaut et exemples du schéma alignés sur le modèle
def test_contract_project_default_and_examples() -> None:
    contract = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert contract["properties"]["project"]["default"] == ingest.DEFAULT_PROJECT
    assert "project" not in contract["required"]
    examples = contract["examples"]
    assert {"project" in e for e in examples} == {True, False}
    for example in examples:
        ingest.AgentEvent.model_validate_json(json.dumps(example))


def test_init_schema_twice_on_old_database_is_idempotent(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(OLD_EVENTS_TABLE)
        db.init_schema(conn)
        db.init_schema(conn)  # colonne déjà là (ou ajoutée par un autre processus)
        columns = [c[1] for c in conn.execute("PRAGMA table_info(events)")]
        rows = conn.execute("SELECT event_id, project FROM events").fetchall()
    assert columns.count("project") == 1
    assert rows == [("old-1", ingest.DEFAULT_PROJECT)]


def test_migration_reraises_other_sqlite_errors(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "vide.db") as conn, pytest.raises(sqlite3.OperationalError):
        db.add_event_columns(conn)  # pas de table events : erreur autre que « duplicate column »


# #181 : ticket et modèle optionnels, rétrocompatibles
def test_event_with_issue_and_model_is_stored(client: TestClient, db_path: Path) -> None:
    resp = client.post(URL, json=event(issue=42, model="claude-opus-5-5"), headers=auth())
    assert resp.status_code == 202
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT issue, model FROM events").fetchone()
    assert row == (42, "claude-opus-5-5")


def test_old_event_without_issue_and_model_is_accepted(client: TestClient, db_path: Path) -> None:
    assert client.post(URL, json=event(), headers=auth()).status_code == 202
    explicit = event("e2", issue=None, model=None)
    assert client.post(URL, json=explicit, headers=auth()).status_code == 202
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT issue, model FROM events").fetchall()
    assert rows == [(None, None), (None, None)]


@pytest.mark.parametrize(
    "bad",
    [
        {"issue": 0},
        {"issue": -1},
        {"issue": "42"},
        {"issue": 4.2},
        {"issue": True},
        {"issue": 10**9},
        {"model": 42},
        {"model": "m" * 129},
    ],
)
def test_invalid_issue_or_model_is_422(client: TestClient, db_path: Path, bad: dict) -> None:
    assert client.post(URL, json=event(**bad), headers=auth()).status_code == 422
    assert count_events(db_path) == 0


def test_old_database_gets_issue_and_model_columns(client: TestClient, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(OLD_EVENTS_TABLE)
    assert client.post(URL, json=event(issue=7, model="m"), headers=auth()).status_code == 202
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT event_id, issue, model FROM events ORDER BY ts").fetchall()
    assert rows == [("old-1", None, None), ("evt-1", 7, "m")]


def test_contract_issue_and_model_bounds() -> None:
    props = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]
    model = ingest.AgentEvent.model_json_schema()["properties"]
    assert props["issue"]["type"] == ["integer", "null"]
    assert props["model"]["type"] == ["string", "null"]
    issue_spec = next(t for t in model["issue"]["anyOf"] if t["type"] == "integer")
    assert issue_spec["minimum"] == props["issue"]["minimum"] == 1
    assert issue_spec["maximum"] == props["issue"]["maximum"] == 10**9 - 1
    model_spec = next(t for t in model["model"]["anyOf"] if t["type"] == "string")
    assert model_spec["maxLength"] == props["model"]["maxLength"] == 128


def test_contract_documents_issue_and_model() -> None:
    text = (SCHEMA_PATH.parent / "ingest-api.md").read_text(encoding="utf-8")
    assert "`issue`" in text and "`model`" in text and "#181" in text

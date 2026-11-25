"""Pause et plafond de tokens (#8) : API GET /api/v1/control lue par le hook.

Pause, reprise et plafond aussi depuis l'onglet admin « Contrôles » (#185,
voir test_controls_page.py) ou le terminal du tech lead.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard import auth, db
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
INGEST = "jeton-de-test-pas-un-secret"  # noqa: S105 - jeton factice de test
BEARER = {"Authorization": f"Bearer {INGEST}"}
CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "control-api.md"


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vva.db"
    monkeypatch.setenv("VVA_DB_PATH", str(path))
    monkeypatch.setenv("INGEST_TOKEN", INGEST)
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    auth.LOGIN_LIMITER.reset()
    return path


def new_client() -> TestClient:
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def logged_in() -> TestClient:
    c = new_client()
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


def state() -> dict[str, object]:
    resp = new_client().get("/api/v1/control", headers=BEARER)
    assert resp.status_code == 200
    body: dict[str, object] = resp.json()
    return body


def terminal(action: Callable[..., None], *args: object) -> None:
    """Ce que fait le tech lead depuis son terminal (aucune route d'écriture)."""
    with db.connect() as conn:
        action(conn, *args)


def seed_tokens(count: int, age: timedelta = timedelta(hours=1)) -> None:
    ts = (datetime.now(UTC) - age).isoformat(timespec="microseconds")
    with db.connect() as conn:
        db.insert_token_usage(conn, [("p1", ts, "s", "backend", "opus", "input", count)])


def test_initial_state_running_with_default_cap(db_path: Path) -> None:
    body = state()
    assert body["paused"] is False
    assert body["reason"] is None
    assert body["cap_tokens"] == 500_000
    assert body["tokens_24h"] == 0


# --- Pause / reprise depuis le terminal ------------------------------------------------------
def test_pause_from_terminal_is_exposed_to_api(db_path: Path) -> None:
    terminal(db.pause, "manuel")
    body = state()
    assert body["paused"] is True and body["reason"] == "manuel"
    terminal(db.resume)
    assert state()["paused"] is False


# --- API : jeton Bearer ----------------------------------------------------------------------
def test_api_requires_bearer(db_path: Path) -> None:
    c = new_client()
    assert c.get("/api/v1/control").status_code == 401
    assert c.get("/api/v1/control", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_api_session_cookie_is_not_enough(db_path: Path) -> None:
    assert logged_in().get("/api/v1/control").status_code == 401


def test_api_closed_without_configured_token(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INGEST_TOKEN")
    assert new_client().get("/api/v1/control", headers=BEARER).status_code == 503


def test_api_is_no_store_and_has_documented_keys(db_path: Path) -> None:
    resp = new_client().get("/api/v1/control", headers=BEARER)
    assert resp.headers["cache-control"] == "no-store"
    assert set(resp.json()) == {"paused", "reason", "cap_tokens", "tokens_24h", "updated_at"}


# --- Plafond ------------------------------------------------------------------------------
def test_cap_reached_pauses_with_reason(db_path: Path) -> None:
    seed_tokens(500_000)
    body = state()
    assert body["paused"] is True
    assert body["reason"] == "plafond"
    assert body["tokens_24h"] == 500_000


def test_below_cap_does_not_pause(db_path: Path) -> None:
    seed_tokens(499_999)
    assert state()["paused"] is False


def test_tokens_older_than_24h_do_not_count(db_path: Path) -> None:
    seed_tokens(900_000, age=timedelta(days=2))
    assert state()["paused"] is False


def test_manual_pause_keeps_its_reason_over_cap(db_path: Path) -> None:
    terminal(db.pause, "manuel")
    seed_tokens(600_000)
    assert state()["reason"] == "manuel"


def test_resume_while_cap_is_reached_pauses_again(db_path: Path) -> None:
    seed_tokens(500_000)
    state()
    terminal(db.resume)
    assert state()["paused"] is True


def test_raising_the_cap_then_resume(db_path: Path) -> None:
    seed_tokens(500_000)
    state()
    terminal(db.set_cap, 800_000)
    terminal(db.resume)
    body = state()
    assert body["cap_tokens"] == 800_000 and body["paused"] is False


def test_unreadable_db_is_503(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = db_path.parent / "dossier.db"
    broken.mkdir()
    monkeypatch.setenv("VVA_DB_PATH", str(broken))
    assert new_client().get("/api/v1/control", headers=BEARER).status_code == 503


# --- Contrat ------------------------------------------------------------------------------------
def test_contract_documents_api_keys_env_and_terminal() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert "`GET /api/v1/control`" in text
    assert "`POST /controls/resume`" in text
    for key in ("paused", "reason", "cap_tokens", "tokens_24h", "updated_at"):
        assert f"`{key}`" in text, key
    for name in ("VVA_CONTROL_URL", "VVA_INGEST_TOKEN", "PreToolUse", "code 2", "db.resume"):
        assert name in text, name
    served = set(app.openapi()["paths"])
    assert "/api/v1/control" in served and "/controls/resume" in served

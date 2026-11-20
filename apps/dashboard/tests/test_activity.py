"""Lecture des événements d'agents (#5, #68, #181), partagée par `/`, `/interactions`, `/stream`.

La page `/activity` a été retirée par #179 (tableau de bord unique).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard import activity, auth, db
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
TEMPLATES = Path(activity.__file__).parent / "templates"


@pytest.fixture
def db_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vva.db"
    monkeypatch.setenv("VVA_DB_PATH", str(path))
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    auth.LOGIN_LIMITER.reset()
    return path


@pytest.fixture
def client(db_file: Path) -> TestClient:
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


def seed(n: int, **extra: Any) -> None:
    rows = [
        (
            f"evt-{i:03d}",
            f"2026-11-18T10:{i // 60:02d}:{i % 60:02d}+00:00",
            "PreToolUse",
            "sess-1",
            extra.get("agent_type", "backend"),
            extra.get("agent_id", f"agent-{i % 3}"),
            extra.get("tool_name", "Bash"),
            extra.get("project", "viral-video-agents"),
            extra.get("issue"),
            extra.get("model"),
        )
        for i in range(n)
    ]
    with db.connect() as conn:
        db.insert_events(conn, rows)


def test_activity_page_is_gone(client: TestClient) -> None:
    assert client.get("/activity").status_code == 404


def test_latest_events_are_bounded_and_newest_first(db_file: Path) -> None:
    assert activity.MAX_EVENTS == 100
    seed(130)
    with db.connect() as conn:
        events = activity.latest_events(conn)
    assert len(events) == 100
    assert events[0].event_id == "evt-129"


# Gabarits : tokens.css uniquement, aucune ressource distante
@pytest.mark.parametrize("name", ["login.html", "interactions.html"])
def test_templates_use_only_design_tokens(name: str) -> None:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/tokens.css">' in text
    assert not re.search(r"https?://", text), "aucun CDN ni ressource distante"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text), "couleurs en dur"
    style = "".join(re.findall(r"<style>(.*?)</style>", text, re.S))
    assert not re.search(r"font-family:(?!\s*var\()", style), "polices via variables"


def test_static_tokens_served(client: TestClient) -> None:
    resp = client.get("/static/tokens.css")
    assert resp.status_code == 200
    assert "--color-bg" in resp.text


# #68 : chaque événement lu porte son projet (préalable au filtre)
def test_latest_events_carry_project(db_file: Path) -> None:
    seed(1, project="agent-factory")
    with db.connect() as conn:
        [event] = activity.latest_events(conn)
    assert event.project == "agent-factory"


def seed_projects(**counts: int) -> None:
    """`viral_video_agents=2` : 2 événements (agents distincts) pour ce projet."""
    rows = [
        (
            f"{name}-{i}",
            f"2026-11-18T10:00:{n * 10 + i:02d}+00:00",
            "PreToolUse",
            "sess-1",
            "qa",
            f"{name}-agent-{i}",
            "Bash",
            name.replace("_", "-"),
            None,
            None,
        )
        for n, (name, count) in enumerate(counts.items())
        for i in range(count)
    ]
    with db.connect() as conn:
        db.insert_events(conn, rows)


def test_counts_use_one_grouped_query(db_file: Path) -> None:
    seed_projects(viral_video_agents=2, agent_factory=1, autre=1)
    statements: list[str] = []
    with db.connect() as conn:
        conn.set_trace_callback(statements.append)
        counts = activity.agent_counts(conn)
    assert sum("GROUP BY" in s for s in statements) == 1
    assert counts == {"": 4, "viral-video-agents": 2, "agent-factory": 1, "autre": 1}


def test_counts_match_the_event_windows(db_file: Path) -> None:
    rows = [
        (f"x-{i}", f"2026-11-19T10:{i // 60:02d}:{i % 60:02d}+00:00", "Stop", "s", "qa", f"a-{i}")
        + (None, "autre", None, None)
        for i in range(120)
    ]
    with db.connect() as conn:
        db.insert_events(conn, rows)
        counts = activity.agent_counts(conn)
        everyone = activity.agents(activity.latest_events(conn))
        in_project = activity.agents(activity.latest_events(conn, project="autre"))
    assert counts[""] == len(everyone) == 100
    assert counts["autre"] == len(in_project) == 100


def test_agent_model_is_latest_known(db_file: Path) -> None:
    rows = [
        ("m1", "2026-11-18T10:00:00+00:00", "SubagentStart", "s", "qa", "a", None, "p", 3, "m-old"),
        ("m2", "2026-11-18T10:00:01+00:00", "PreToolUse", "s", "qa", "a", "Bash", "p", 3, "m-new"),
        ("m3", "2026-11-18T10:00:02+00:00", "PostToolUse", "s", "qa", "a", "Bash", "p", 3, None),
    ]
    with db.connect() as conn:
        db.insert_events(conn, rows)
        (agent,) = activity.agents(activity.latest_events(conn))
    assert (agent.event_id, agent.model, agent.issue) == ("m3", "m-new", 3)

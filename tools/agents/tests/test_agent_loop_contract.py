"""Contrat #8/#18 : la boucle lit la vraie réponse de GET /api/v1/control du dashboard."""

from __future__ import annotations

from pathlib import Path

import agent_loop as al
import pytest
from fastapi.testclient import TestClient

from dashboard import db
from dashboard.main import app

INGEST = "jeton-de-test-pas-un-secret"  # noqa: S105 - jeton factice de test


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path / "vva.db"))
    monkeypatch.setenv("INGEST_TOKEN", INGEST)
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def control(client: TestClient) -> al.ControlState | None:
    resp = client.get("/api/v1/control", headers={"Authorization": f"Bearer {INGEST}"})
    assert resp.status_code == 200
    return al.parse_control(resp.content)


def test_etat_par_defaut_du_dashboard_lisible_par_la_boucle(client: TestClient) -> None:
    assert control(client) == al.ControlState(paused=False, cap_tokens=500_000, tokens_24h=0)


def test_pause_du_dashboard_arrete_la_boucle(client: TestClient) -> None:
    control(client)  # crée le schéma
    with db.connect() as conn:
        db.pause(conn, "manuel")
    state = control(client)
    assert state is not None
    assert state.paused


def test_plafond_du_dashboard_transmis_a_la_boucle(client: TestClient) -> None:
    control(client)
    with db.connect() as conn:
        db.set_cap(conn, 1_234)
    state = control(client)
    assert state is not None
    assert state.cap_tokens == 1_234

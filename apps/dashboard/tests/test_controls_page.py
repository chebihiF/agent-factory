"""Onglet « Contrôles » (#185) : lecture seule pour le visiteur, Pause et Plafond pour l'admin.

Spec : docs/design/pause-plafond.md ; contrat : contracts/control-api.md ; ADR-005.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from dashboard import admin, auth, db, github
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
ADMIN_PASSWORD = "admin-de-test-pas-un-secret"  # noqa: S105 - valeur factice de test
INGEST = "jeton-de-test-pas-un-secret"  # noqa: S105 - jeton factice de test
BEARER = {"Authorization": f"Bearer {INGEST}"}
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "apps" / "dashboard" / "src" / "dashboard"


def unreachable_github(path: str) -> Any:
    raise URLError("réseau coupé")


@pytest.fixture(autouse=True)
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vva.db"
    monkeypatch.setenv("VVA_DB_PATH", str(path))
    monkeypatch.setenv("INGEST_TOKEN", INGEST)
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    hashed = auth.hash_password(ADMIN_PASSWORD, iterations=1_000)
    monkeypatch.setenv(admin.ADMIN_HASH_ENV, hashed)
    auth.LOGIN_LIMITER.reset()
    admin.reset()
    monkeypatch.setattr(github, "CACHE", github.GithubCache(unreachable_github))
    return path


def new_client() -> TestClient:
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def visitor() -> TestClient:
    c = new_client()
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


def admin_client() -> TestClient:
    c = new_client()
    assert c.post("/admin/login", data={"password": ADMIN_PASSWORD}).status_code == 303
    return c


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "jeton CSRF absent"
    return match.group(1)


def controls() -> tuple[bool, str | None, int, str]:
    with db.connect() as conn:
        return db.read_controls(conn)


def dump(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return "\n".join(conn.iterdump())


def norm(html: str) -> str:
    """Espace fine insécable des milliers (filtre `thousands`) -> espace simple."""
    return html.replace("\u202f", " ")


def post_forms(html: str) -> list[str]:
    return re.findall(r'<form method="post" action="([^"]+)"', html)


# --- Accès ----------------------------------------------------------------------------------------
def test_controls_redirects_anonymous_to_login() -> None:
    resp = new_client().get("/controls")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    assert resp.headers["cache-control"] == "no-store"


def test_visitor_sees_state_read_only_without_any_command() -> None:
    resp = visitor().get("/controls")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    html = norm(resp.text)
    assert "Lecture seule. Les commandes sont réservées à l'admin." in html
    assert "500 000" in html and "● En marche" in html
    assert post_forms(html) == [] and "hx-post" not in html and "<button" not in html
    assert 'href="/admin/login"' in html and 'aria-label="Connexion admin"' in html


def test_visitor_cannot_open_a_confirmation_by_url() -> None:
    html = visitor().get("/controls?confirmer=pause&plafond=3000000").text
    assert post_forms(html) == [] and 'name="csrf"' not in html


def test_admin_sees_commands_and_logout_but_no_write_form_before_confirming() -> None:
    html = norm(admin_client().get("/controls").text)
    assert "● Connecté en admin" in html
    assert post_forms(html) == ["/admin/logout"]
    assert 'href="/controls?confirmer=pause"' in html and "Mettre en pause" in html
    assert '<form method="get" action="/controls"' in html and 'name="plafond"' in html
    assert 'inputmode="numeric"' in html and "Entier de 1 à 1 000 000 000" in html


def test_contrôles_tab_is_in_the_navigation() -> None:
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", visitor().get("/controls").text, re.S)
    assert nav and 'href="/controls" aria-current="page"' in nav.group(1)
    assert "Contrôles" in nav.group(1)


# --- Scénario : mettre en pause avec confirmation -----------------------------------------------
def test_pause_after_confirmation_shows_en_pause() -> None:
    client = admin_client()
    panel = client.get("/controls?confirmer=pause").text
    assert "Mettre tous les agents en pause ?" in panel and "Annuler" in panel
    assert set(post_forms(panel)) == {"/controls/pause", "/admin/logout"}
    resp = client.post("/controls/pause", data={"csrf": csrf_of(panel), "confirm": "oui"})
    assert resp.status_code == 303 and resp.headers["location"] == "/controls?fait=pause"
    assert resp.headers["cache-control"] == "no-store"
    assert controls()[:2] == (True, "manuel")
    html = client.get("/controls?fait=pause").text
    assert "❚❚ En pause" in html and "Mettre en pause" not in html
    assert "❚❚ En pause" in visitor().get("/controls").text


def test_pause_without_confirmation_sends_nothing() -> None:
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    for data in ({"csrf": token}, {"csrf": token, "confirm": "non"}):
        assert client.post("/controls/pause", data=data).status_code == 422
    assert controls()[0] is False


# --- Reprise (décision du parent du 24/11) ------------------------------------------------------
def pause_now() -> None:
    with db.connect() as conn:
        db.pause(conn, "manuel")


def test_resume_after_confirmation_shows_en_marche() -> None:
    pause_now()
    client = admin_client()
    html = client.get("/controls").text
    assert 'href="/controls?confirmer=reprise"' in html and "Reprendre les agents" in html
    panel = client.get("/controls?confirmer=reprise").text
    assert "Reprendre tous les agents ?" in panel
    assert set(post_forms(panel)) == {"/controls/resume", "/admin/logout"}
    resp = client.post("/controls/resume", data={"csrf": csrf_of(panel), "confirm": "oui"})
    assert resp.status_code == 303 and resp.headers["location"] == "/controls?fait=reprise"
    assert resp.headers["cache-control"] == "no-store"
    assert controls()[:2] == (False, None)
    html = client.get("/controls?fait=reprise").text
    assert "✓ Agents relancés." in html and "● En marche" in html


def test_resume_without_confirmation_sends_nothing() -> None:
    pause_now()
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=reprise").text)
    assert client.post("/controls/resume", data={"csrf": token}).status_code == 422
    assert controls()[0] is True


def test_visitor_never_sees_the_resume_command() -> None:
    pause_now()
    html = visitor().get("/controls?confirmer=reprise").text
    assert "Reprendre" not in html and post_forms(html) == []


# --- Scénario : régler le plafond ---------------------------------------------------------------
def test_cap_is_shown_confirmed_applied_and_displayed() -> None:
    client = admin_client()
    panel = norm(client.get("/controls?plafond=3000000").text)
    assert "Changer le plafond ?" in panel
    assert "500 000 → 3 000 000" in panel and "+500 %" in panel
    data = {"csrf": csrf_of(panel), "cap": "3000000", "confirm": "oui"}
    resp = client.post("/controls/cap", data=data)
    assert resp.status_code == 303 and resp.headers["location"] == "/controls?fait=plafond"
    assert controls()[2] == 3_000_000
    html = norm(client.get("/controls?fait=plafond").text)
    assert "Plafond enregistré. 3 000 000 tokens / 24 h" in html
    api = new_client().get("/api/v1/control", headers=BEARER).json()
    assert api["cap_tokens"] == 3_000_000


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "Il faut au moins 1 token."),
        ("0", "Il faut au moins 1 token."),
        ("abc", "Chiffres seulement."),
        ("1,5", "Chiffres seulement."),
        ("-5", "Chiffres seulement."),
        ("١٢", "Chiffres seulement."),
        ("1000000001", "Trop grand : le maximum est 1 000 000 000 tokens / 24 h."),
        ("9" * 400, "Trop grand : le maximum est 1 000 000 000 tokens / 24 h."),
    ],
    ids=[
        "vide",
        "zero",
        "lettres",
        "virgule",
        "negatif",
        "chiffres-arabes",
        "trop-grand",
        "enorme",
    ],
)
def test_invalid_cap_is_422_and_changes_nothing(raw: str, message: str) -> None:
    client = admin_client()
    preview = client.get("/controls", params={"plafond": raw})
    assert preview.status_code == 422 and message in norm(preview.text)
    assert "/controls/cap" not in post_forms(preview.text)
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    resp = client.post("/controls/cap", data={"csrf": token, "cap": raw, "confirm": "oui"})
    assert resp.status_code == 422 and message in norm(resp.text)
    assert resp.headers["cache-control"] == "no-store"
    assert controls()[2] == 500_000


def test_bounds_one_and_one_billion_are_accepted() -> None:
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    for raw, value in (("1", 1), ("1000000000", 1_000_000_000)):
        data = {"csrf": token, "cap": raw, "confirm": "oui"}
        assert client.post("/controls/cap", data=data).status_code == 303
        assert controls()[2] == value


# --- Scénario : accès protégé et unique écriture ------------------------------------------------
@pytest.mark.parametrize("path", ["/controls/pause", "/controls/resume", "/controls/cap"])
def test_write_routes_refuse_visitor_and_anonymous_with_no_store(path: str) -> None:
    for client in (new_client(), visitor()):
        resp = client.post(path, data={"confirm": "oui", "cap": "7"})
        assert resp.status_code == 401
        assert resp.headers["cache-control"] == "no-store"
    assert controls()[:3] == (False, None, 500_000)


@pytest.mark.parametrize("path", ["/controls/pause", "/controls/resume", "/controls/cap"])
def test_write_routes_refuse_missing_or_wrong_csrf(path: str) -> None:
    client = admin_client()
    for data in ({"confirm": "oui", "cap": "7"}, {"csrf": "faux", "confirm": "oui", "cap": "7"}):
        resp = client.post(path, data=data)
        assert resp.status_code == 403
        assert resp.headers["cache-control"] == "no-store"
    assert controls()[:3] == (False, None, 500_000)


def test_write_routes_refused_after_5_failed_logins() -> None:
    client = new_client()
    for _ in range(5):
        client.post("/admin/login", data={"password": "faux"})
    assert client.post("/admin/login", data={"password": ADMIN_PASSWORD}).status_code == 429
    assert client.post("/controls/pause", data={"confirm": "oui"}).status_code == 401
    assert controls()[0] is False


WRITES = {("POST", "/controls/pause"), ("POST", "/controls/resume"), ("POST", "/controls/cap")}
NON_GET = {
    ("POST", "/login"),
    ("POST", "/admin/login"),
    ("POST", "/admin/logout"),
    ("POST", "/api/v1/events"),
    ("POST", "/v1/metrics"),
    *WRITES,
}


def routes() -> set[tuple[str, str]]:
    """(méthode, chemin) de toutes les routes servies (schéma OpenAPI : routeurs inclus)."""
    paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    return {(method.upper(), path) for path, ops in paths.items() for method in ops}


def test_only_pause_and_cap_routes_write_controls(db_path: Path) -> None:
    """Exclusivité : aucune autre route ne change la base (ingestion : événements, à part)."""
    assert {r for r in routes() if r[0] != "GET"} == NON_GET
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    before = dump(db_path)
    for method, path in sorted(routes() - WRITES):
        if path in ("/stream", "/api/v1/events", "/v1/metrics", "/admin/logout"):
            continue
        form = {"csrf": token, "confirm": "oui", "cap": "7", "password": "faux"}
        client.request(method, path, data=form if method == "POST" else None)
    assert dump(db_path) == before
    writer = re.compile(r"\bdb\.(pause|resume|set_cap)\b")
    sources = {p.name for p in SRC.glob("*.py") if writer.search(p.read_text())}
    assert sources == {"controls.py"}
    client.post("/controls/pause", data={"csrf": token, "confirm": "oui"})
    client.post("/controls/cap", data={"csrf": token, "cap": "7", "confirm": "oui"})
    assert controls()[:3] == (True, "manuel", 7)
    client.post("/controls/resume", data={"csrf": token, "confirm": "oui"})
    assert controls()[0] is False


def test_ingestion_routes_never_touch_controls() -> None:
    before = controls()
    event = {
        "event_id": "evt-1",
        "ts": "2026-11-25T10:00:00+00:00",
        "hook_event_name": "PreToolUse",
        "session_id": "sess-1",
        "agent_type": "backend",
        "agent_id": "agent-1",
    }
    assert new_client().post("/api/v1/events", json=event, headers=BEARER).status_code < 300
    new_client().post("/v1/metrics", json={"resourceMetrics": []}, headers=BEARER)
    assert controls() == before


# --- Contrat ------------------------------------------------------------------------------------
def test_control_contract_documents_admin_write_routes() -> None:
    text = (ROOT / "contracts" / "control-api.md").read_text(encoding="utf-8")
    for needle in (
        "`GET /controls`",
        "`POST /controls/pause`",
        "`POST /controls/cap`",
        "1 000 000 000",
        "401",
        "403",
        "422",
        "429",
        "no-store",
        "ADR-005",
    ):
        assert needle in text, needle

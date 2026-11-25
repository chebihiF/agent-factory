"""Connexion admin (#185, ADR-005) : seule porte des commandes Pause et Plafond.

Mot de passe admin (hash PBKDF2 en env), cookie `vva_admin` signé (SameSite=Strict, 8 h, 30 min
d'inactivité), jeton CSRF lié à la session, 5 essais/min par IP, réponses `no-store`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard import admin, auth
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
ADMIN_PASSWORD = "admin-de-test-pas-un-secret"  # noqa: S105 - valeur factice de test
ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(admin, "SESSIONS", admin.SessionStore(clock=fake))
    return fake


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path / "vva.db"))
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    hashed = auth.hash_password(ADMIN_PASSWORD, iterations=1_000)
    monkeypatch.setenv(admin.ADMIN_HASH_ENV, hashed)
    auth.LOGIN_LIMITER.reset()
    admin.reset()


def new_client(ip: str = "203.0.113.7") -> TestClient:
    return TestClient(
        app, base_url="https://testserver", follow_redirects=False, client=(ip, 50000)
    )


def admin_login(client: TestClient, password: str = ADMIN_PASSWORD) -> int:
    return client.post("/admin/login", data={"password": password}).status_code


def admin_client() -> TestClient:
    c = new_client()
    assert admin_login(c) == 303
    return c


def csrf_of(client: TestClient) -> str:
    html = client.get("/controls?confirmer=pause").text
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "jeton CSRF absent du formulaire de confirmation"
    return match.group(1)


# --- Page de connexion admin --------------------------------------------------------------------
def test_admin_login_page_is_separate_no_store_and_has_password_field() -> None:
    resp = new_client().get("/admin/login")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert '<form method="post" action="/admin/login">' in resp.text
    assert 'type="password"' in resp.text and 'autocomplete="current-password"' in resp.text
    assert "Connexion admin" in resp.text


def test_good_password_opens_a_strict_session_cookie() -> None:
    resp = new_client().post("/admin/login", data={"password": ADMIN_PASSWORD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/controls"
    assert resp.headers["cache-control"] == "no-store"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{admin.COOKIE_NAME}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Max-Age=28800", "Path=/"):
        assert attribute.lower() in cookie.lower(), attribute
    assert ADMIN_PASSWORD not in cookie


@pytest.mark.parametrize("password", ["", "faux", PASSWORD, "مرحبا كلمة السر"])
def test_wrong_password_is_neutral_401_without_cookie(password: str) -> None:
    resp = new_client().post("/admin/login", data={"password": password})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    assert "Mot de passe incorrect." in resp.text
    assert resp.headers["cache-control"] == "no-store"


def test_dashboard_password_is_not_the_admin_password() -> None:
    assert admin_login(new_client(), PASSWORD) == 401


def test_oversized_body_is_413() -> None:
    resp = new_client().post("/admin/login", data={"password": "x" * 5000})
    assert resp.status_code == 413


# --- Admin désactivé ----------------------------------------------------------------------------
@pytest.mark.parametrize("missing", [admin.ADMIN_HASH_ENV, auth.SESSION_KEY_ENV])
def test_admin_disabled_without_env_is_403_everywhere(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    client = admin_client()
    monkeypatch.delenv(missing)
    for method, path in [
        ("GET", "/admin/login"),
        ("POST", "/admin/login"),
        ("POST", "/controls/pause"),
        ("POST", "/controls/cap"),
        ("POST", "/admin/logout"),
    ]:
        resp = client.request(method, path, data={"password": ADMIN_PASSWORD})
        assert resp.status_code == 403, (method, path)
        assert resp.headers["cache-control"] == "no-store"
        assert "Accès admin désactivé." in resp.text


def test_admin_disabled_when_its_hash_is_the_dashboard_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(admin.ADMIN_HASH_ENV, os.environ[auth.PASSWORD_HASH_ENV])
    for method, path in [("GET", "/admin/login"), ("POST", "/admin/login")]:
        resp = new_client().request(method, path, data={"password": PASSWORD})
        assert resp.status_code == 403 and "Accès admin désactivé." in resp.text


def test_admin_refused_when_its_password_is_the_dashboard_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(admin.ADMIN_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    resp = new_client().post("/admin/login", data={"password": PASSWORD})
    assert resp.status_code == 403 and "set-cookie" not in resp.headers
    assert "doit différer" in resp.text


# --- Limite 5 essais / minute par IP ------------------------------------------------------------
def test_login_limited_to_5_attempts_per_minute_per_ip() -> None:
    client = new_client("198.51.100.1")
    assert [admin_login(client, "faux") for _ in range(5)] == [401] * 5
    resp = client.post("/admin/login", data={"password": ADMIN_PASSWORD})
    assert resp.status_code == 429
    assert resp.headers["cache-control"] == "no-store"
    assert 1 <= int(resp.headers["retry-after"]) <= 60
    assert "set-cookie" not in resp.headers
    assert admin_login(new_client("198.51.100.2")) == 303


def test_wrong_password_tells_remaining_attempts() -> None:
    resp = new_client().post("/admin/login", data={"password": "faux"})
    assert "Il te reste 4 essais cette minute." in resp.text


def test_ip_refused_attempts_do_not_consume_the_global_budget() -> None:
    client = new_client("198.51.100.9")
    for _ in range(admin.GLOBAL_MAX + 5):
        admin_login(client, "faux")
    assert admin_login(new_client("198.51.100.10")) == 303


def test_global_refusal_does_not_consume_the_ip_budget() -> None:
    for n in range(admin.GLOBAL_MAX):
        assert admin_login(new_client(f"192.0.2.{n}"), "faux") == 401
    victim = new_client("198.51.100.11")
    assert admin_login(victim, "faux") == 429
    admin.GLOBAL_LIMITER.reset()
    assert admin.LOGIN_LIMITER.remaining("198.51.100.11") == admin.PER_IP_MAX


def test_ip_window_slides_after_a_minute() -> None:
    fake = Clock()
    limiter = admin.KeyedLimiter(max_attempts=5, window_s=60, clock=fake)
    assert all(limiter.allow("a") for _ in range(5))
    assert not limiter.allow("a") and limiter.allow("b")
    fake.now += 60
    assert limiter.allow("a")


def test_global_backstop_bounds_distributed_attempts() -> None:
    codes = [admin_login(new_client(f"192.0.2.{n}"), "faux") for n in range(admin.GLOBAL_MAX + 1)]
    assert codes[:-1] == [401] * admin.GLOBAL_MAX
    assert codes[-1] == 429


# --- Session : 8 h absolues, 30 min d'inactivité glissantes -------------------------------------
def test_session_expires_after_30_min_idle(clock: Clock) -> None:
    client = admin_client()
    clock.now += admin.IDLE_TTL_S
    assert client.post("/controls/pause").status_code == 401


def test_activity_slides_the_idle_window(clock: Clock) -> None:
    client = admin_client()
    for _ in range(3):
        clock.now += admin.IDLE_TTL_S - 60
        assert "Connecté en admin" in client.get("/controls").text


def test_session_expires_after_8_hours_even_when_active(clock: Clock) -> None:
    client = admin_client()
    while clock.now < 1000.0 + admin.ABSOLUTE_TTL_S - 600:
        clock.now += 600
        assert "Connecté en admin" in client.get("/controls").text
    clock.now += 600
    assert "Connecté en admin" not in client.get("/controls").text


@pytest.mark.parametrize(
    "cookie",
    ["", "x", "abc.def", "a" * 500, "sid.signature-fausse"],
    ids=["vide", "court", "inconnu", "trop-long", "signature-fausse"],
)
def test_forged_admin_cookie_is_401(cookie: str) -> None:
    client = new_client()
    client.cookies.set(admin.COOKIE_NAME, cookie)
    assert client.post("/controls/pause", data={"confirm": "oui"}).status_code == 401


def test_cookie_with_valid_sid_but_other_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = admin_client()
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "t" * 48)
    assert client.post("/controls/pause").status_code == 401


def test_changing_the_admin_password_closes_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    client = admin_client()
    monkeypatch.setenv(admin.ADMIN_HASH_ENV, auth.hash_password("autre", iterations=1_000))
    assert client.post("/controls/pause").status_code == 401


# --- Déconnexion et CSRF ------------------------------------------------------------------------
def test_logout_closes_the_session_server_side() -> None:
    client = admin_client()
    stolen = client.cookies.get(admin.COOKIE_NAME)
    resp = client.post("/admin/logout", data={"csrf": csrf_of(client)})
    assert resp.status_code == 303 and resp.headers["location"] == "/controls"
    replay = new_client()
    replay.cookies.set(admin.COOKIE_NAME, stolen or "")
    assert replay.post("/controls/pause").status_code == 401


def test_relogin_closes_the_previous_session_of_the_cookie() -> None:
    client = admin_client()
    old = client.cookies.get(admin.COOKIE_NAME) or ""
    assert admin_login(client) == 303
    replay = new_client()
    replay.cookies.set(admin.COOKIE_NAME, old)
    assert replay.post("/controls/pause").status_code == 401
    assert "Connecté en admin" in client.get("/controls").text


def test_logout_without_session_clears_the_cookie() -> None:
    client = new_client()
    client.cookies.set(admin.COOKIE_NAME, "perime.signature")
    resp = client.post("/admin/logout")
    assert resp.status_code == 401
    cookie = resp.headers["set-cookie"].lower()
    assert cookie.startswith(f"{admin.COOKIE_NAME}=") and "max-age=0" in cookie


def test_logout_requires_csrf() -> None:
    client = admin_client()
    assert client.post("/admin/logout", data={"csrf": "faux"}).status_code == 403
    assert "Connecté en admin" in client.get("/controls").text


def test_csrf_token_of_another_session_is_refused() -> None:
    other = csrf_of(admin_client())
    client = admin_client()
    resp = client.post("/controls/pause", data={"csrf": other, "confirm": "oui"})
    assert resp.status_code == 403


def test_non_ascii_csrf_is_refused_without_error() -> None:
    client = admin_client()
    resp = client.post("/controls/pause", data={"csrf": "é" * 43, "confirm": "oui"})
    assert resp.status_code == 403


# --- Contrat et ADR -----------------------------------------------------------------------------
def test_routes_contract_documents_admin_session() -> None:
    text = (ROOT / "contracts" / "dashboard-routes.md").read_text(encoding="utf-8")
    for needle in (
        "`ADMIN_PASSWORD_HASH`",
        "`GET /admin/login`",
        "`POST /admin/login`",
        "`POST /admin/logout`",
        "`vva_admin`",
        "SameSite=Strict",
        "30 min",
        "CSRF",
        "par IP",
    ):
        assert needle in text, needle


def test_adr_005_records_the_decision() -> None:
    text = (ROOT / "docs" / "adr" / "ADR-005-auth-admin.md").read_text(encoding="utf-8")
    for needle in ("PBKDF2", "SameSite=Strict", "CSRF", "30 min", "8 h", "#185"):
        assert needle in text, needle

"""Chaîne complète OTLP -> token_usage -> /tokens, et diagnostic de la page vide (#184)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard import auth, db, tokens
from dashboard.main import app

TOKEN = "test-token-not-a-secret"  # noqa: S105 - jeton factice de test
PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
URL = "/v1/metrics"
MINUTE = 60 * 10**9
SECRET = "SECRET-A-NE-JAMAIS-RENVOYER"  # noqa: S105 - marqueur de test


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vva.db"
    monkeypatch.setenv("VVA_DB_PATH", str(path))
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    auth.LOGIN_LIMITER.reset()
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


def stored_rows(path: Path) -> int:
    with db.connect() as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()
    assert path.exists()
    return int(count)


def bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def attrs(**kv: str) -> list[dict[str, Any]]:
    return [{"key": k.replace("_", "."), "value": {"stringValue": v}} for k, v in kv.items()]


def token_metric(*points: dict[str, Any], temporality: int = 1) -> dict[str, Any]:
    sum_ = {"aggregationTemporality": temporality, "isMonotonic": True, "dataPoints": list(points)}
    return {"name": "claude_code.token.usage", "unit": "tokens", "sum": sum_}


def dp(type_: str, value: int, **extra: str) -> dict[str, Any]:
    """Point tel qu'émis par l'exportateur JS : nanosecondes en chaîne, `asInt` numérique."""
    now = time.time_ns()
    base = {"session_id": "s1", "model": "claude-opus-4-1", "type": type_, **extra}
    return {
        "attributes": attrs(**base),
        "startTimeUnixNano": str(now - MINUTE),
        "timeUnixNano": str(now),
        "asInt": value,
    }


def batch(*metrics: dict[str, Any]) -> dict[str, Any]:
    scope = {"scope": {"name": "com.anthropic.claude_code"}, "metrics": list(metrics)}
    return {"resourceMetrics": [{"resource": {"attributes": []}, "scopeMetrics": [scope]}]}


# Scénario : une charge OTLP de test alimente token_usage puis /tokens
def test_otlp_payload_feeds_token_usage_and_tokens_page(client: TestClient, db_path: Path) -> None:
    body = batch(
        token_metric(dp("input", 1200), dp("output", 300), dp("input", 50, agent_name="backend"))
    )
    assert client.post(URL, json=body, headers=bearer()).status_code == 200
    assert stored_rows(db_path) == 3
    page = client.get("/tokens").text
    assert "Aucune donnée" not in page
    assert "backend" in page and "claude-opus-4-1" in page
    assert tokens.thousands(1550) in page  # modèle : 1200 + 300 + 50 sur 24 h


# Scénario : charge invalide -> 4xx, jamais d'écho
@pytest.mark.parametrize(
    ("content", "ctype", "status"),
    [
        (f"{{{SECRET}".encode(), "application/json", 400),
        (f'{{"resourceMetrics": "{SECRET}"}}'.encode(), "application/json", 400),
        (
            f'{{"resourceMetrics": [{{"scopeMetrics": "{SECRET}"}}]}}'.encode(),
            "application/json",
            400,
        ),
        (SECRET.encode(), "application/x-protobuf", 415),
    ],
    ids=["json-tronque", "enveloppe-chaine", "scope-chaine", "protobuf"],
)
def test_invalid_payload_is_4xx_without_echo(
    client: TestClient, db_path: Path, content: bytes, ctype: str, status: int
) -> None:
    resp = client.post(URL, content=content, headers={**bearer(), "Content-Type": ctype})
    assert resp.status_code == status
    assert SECRET not in resp.text
    assert stored_rows(db_path) == 0


# Scénario : aucune donnée -> la page explique pourquoi et que faire
def test_empty_page_without_any_export_explains_what_to_do(client: TestClient) -> None:
    page = client.get("/tokens").text
    assert "Aucune donnée" in page
    assert "Aucun envoi OTLP reçu" in page
    assert "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT" in page and "/v1/metrics" in page


def test_empty_page_when_every_point_is_rejected(client: TestClient) -> None:
    cumulative = batch(token_metric(dp("input", 10), dp("output", 5), temporality=2))
    assert client.post(URL, json=cumulative, headers=bearer()).status_code == 200
    page = client.get("/tokens").text
    assert "2 points" in page and "rejetés" in page
    assert "TEMPORALITY_PREFERENCE=delta" in page
    assert "Aucun envoi OTLP reçu" not in page


def test_empty_page_when_exports_carry_no_token_counter(client: TestClient) -> None:
    other = {"name": "claude_code.cost.usage", "sum": {"aggregationTemporality": 1}}
    assert client.post(URL, json=batch(other), headers=bearer()).status_code == 200
    page = client.get("/tokens").text
    assert "sans compteur" in page and "claude_code.token.usage" in page


def test_accepted_export_shows_last_reception_instead_of_hint(client: TestClient) -> None:
    client.post(URL, json=batch(token_metric(dp("input", 7))), headers=bearer())
    page = client.get("/tokens").text
    assert "Dernier envoi OTLP" in page
    assert "Aucun envoi OTLP reçu" not in page


def test_receipt_counts_accumulate_and_ignore_failed_requests(client: TestClient) -> None:
    client.post(URL, json=batch(token_metric(dp("input", 7))), headers=bearer())
    client.post(URL, content=b"{", headers={**bearer(), "Content-Type": "application/json"})
    client.post(URL, json=batch(token_metric(dp("input", 8))))  # sans jeton : 401
    with db.connect() as conn:
        receipt = tokens.receipt(conn)
    assert (receipt.batches, receipt.accepted, receipt.rejected) == (1, 1, 0)
    assert receipt.last_at is not None

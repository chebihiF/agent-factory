"""deploy.sh + compose.yaml (#30, #185) : clé de session stable, hashs transmis, fermés par défaut.

#185 : hash admin et FORWARDED_ALLOW_IPS (sous-réseau du réseau Traefik, jamais « * »).

Tout se passe dans un dossier temporaire : faux docker/curl dans le PATH, état dans tmp_path.
Aucun secret réel : la clé et le hash générés ne servent qu'au test.
"""

from __future__ import annotations

import os
import stat
import subprocess
from typing import Any
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent / "deploy"
DOCKERFILE = HERE.parents[1] / "apps" / "dashboard" / "Dockerfile"
COMPOSE = DEPLOY / "compose.yaml"
VERSION = "1.2.3"
FAKE_DOCKER = """#!/usr/bin/env bash
{
  echo "ARGS:$*"
  echo "SESSION_KEY:${DASHBOARD_SESSION_KEY-<unset>}"
  echo "PASSWORD_HASH:${DASHBOARD_PASSWORD_HASH-<unset>}"
  echo "ADMIN_HASH:${ADMIN_PASSWORD_HASH-<unset>}"
  echo "FORWARDED:${FORWARDED_ALLOW_IPS-<unset>}"
} >> "$FAKE_LOG"
# healthz dans le conteneur (blue/green, #25) : renvoie la version attendue
case "$*" in
  *" exec "*) echo '{"version":"'"$IMAGE_TAG"'"}' ;;
  "network inspect "*) printf '%s' "${FAKE_SUBNETS-}" ;;
esac
exit 0
"""
# Répond à /healthz (version) et à / (titre) avec la même ligne : suffit au smoke test.
FAKE_CURL = """#!/usr/bin/env bash
echo '{"version":"'"$FAKE_VERSION"'"} Agents Dashboard'
"""


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path | dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake(bin_dir, "docker", FAKE_DOCKER)
    _fake(bin_dir, "curl", FAKE_CURL)
    state = tmp_path / "state"
    state.mkdir()
    (state / "proxy.env").write_text("DASHBOARD_HOST=dashboard.test\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "VVA_STATE_DIR": str(state),
        "FAKE_LOG": str(tmp_path / "docker.log"),
        "FAKE_VERSION": VERSION,
        "VVA_PROBE": "0",
    }
    return {"state": state, "env": env, "log": tmp_path / "docker.log"}


def deploy(sb: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(DEPLOY / "deploy.sh"), "ghcr.io/example/vva-dashboard", VERSION],  # noqa: S607
        env=sb["env"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_session_key_created_600_and_kept_between_deploys(sandbox: dict[str, Any]) -> None:
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    key_file = sandbox["state"] / "session_key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    first = key_file.read_text().strip()
    assert len(first) == 64  # openssl rand -hex 32
    assert not (sandbox["state"] / "session_key.tmp").exists()

    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert key_file.read_text().strip() == first


def test_no_hash_file_keeps_login_closed_at_deploy(sandbox: dict[str, Any]) -> None:
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    log = sandbox["log"].read_text()
    assert "PASSWORD_HASH:\n" in log  # vide => /login répond 503 côté appli
    assert "SESSION_KEY:<unset>" not in log


def test_hash_file_is_passed_to_container_env(sandbox: dict[str, Any]) -> None:
    encoded = "pbkdf2_sha256:600000:00112233:44556677"  # faux, jamais vérifié ici
    (sandbox["state"] / "dashboard_password_hash").write_text(encoded + "\n")
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"PASSWORD_HASH:{encoded}" in sandbox["log"].read_text()


def test_compose_passes_both_secrets_from_environment() -> None:
    text = COMPOSE.read_text()
    assert "DASHBOARD_SESSION_KEY: ${DASHBOARD_SESSION_KEY:-}" in text
    assert "DASHBOARD_PASSWORD_HASH: ${DASHBOARD_PASSWORD_HASH:-}" in text


# --- #185 : hash admin et IP réelle des clients ---------------------------------------------------
def test_admin_hash_absent_keeps_admin_disabled(sandbox: dict[str, Any]) -> None:
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ADMIN_HASH:\n" in sandbox["log"].read_text()  # vide => admin désactivé (403)


def test_admin_hash_file_is_passed_to_container_env(sandbox: dict[str, Any]) -> None:
    encoded = "pbkdf2_sha256:600000:8899aabb:ccddeeff"  # faux, jamais vérifié ici
    (sandbox["state"] / "admin_password_hash").write_text(encoded + "\n")
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"ADMIN_HASH:{encoded}" in sandbox["log"].read_text()


def test_forwarded_ips_are_the_traefik_network_subnets(sandbox: dict[str, Any]) -> None:
    (sandbox["state"] / "proxy.env").write_text("DASHBOARD_HOST=d.test\nPROXY_NETWORK=n8n_default\n")
    sandbox["env"]["FAKE_SUBNETS"] = "172.18.0.0/16,fd00:1::/64,"
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    log = sandbox["log"].read_text()
    assert "ARGS:network inspect n8n_default" in log
    assert "FORWARDED:172.18.0.0/16,fd00:1::/64\n" in log


def test_unreadable_network_falls_back_to_localhost(sandbox: dict[str, Any]) -> None:
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "FORWARDED:127.0.0.1\n" in sandbox["log"].read_text()


def test_explicit_forwarded_ips_in_proxy_env_win(sandbox: dict[str, Any]) -> None:
    (sandbox["state"] / "proxy.env").write_text("DASHBOARD_HOST=d.test\nFORWARDED_ALLOW_IPS=10.0.0.2\n")
    res = deploy(sandbox)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "FORWARDED:10.0.0.2\n" in sandbox["log"].read_text()


@pytest.mark.parametrize("value", ["*", "10.0.0.0/8,*", "10.0.0.1;id", "  "])
def test_wildcard_or_odd_forwarded_ips_abort_the_deploy(
    sandbox: dict[str, Any], value: str
) -> None:
    (sandbox["state"] / "proxy.env").write_text(
        f"DASHBOARD_HOST=d.test\nFORWARDED_ALLOW_IPS='{value}'\n"
    )
    res = deploy(sandbox)
    assert res.returncode != 0
    assert "FORWARDED_ALLOW_IPS" in res.stderr
    assert " up " not in (sandbox["log"].read_text() if sandbox["log"].exists() else "")


def test_compose_passes_admin_hash_and_forwarded_ips() -> None:
    text = COMPOSE.read_text()
    assert "ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH:-}" in text
    assert "FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-127.0.0.1}" in text


def test_dockerfile_trusts_only_localhost_by_default() -> None:
    text = DOCKERFILE.read_text()
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in text and "--proxy-headers" in text
    assert "--forwarded-allow-ips" not in text and "ALLOW_IPS=*" not in text

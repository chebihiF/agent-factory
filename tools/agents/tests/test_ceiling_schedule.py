"""Planification du plafond de tokens (#210) : launchctl et gh sont factices."""

from __future__ import annotations

import ast
import os
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

import agent_loop
import ceiling_schedule as cs
import pytest

import token_report as tr
from dashboard.db import init_schema

ROOT = Path(__file__).resolve().parents[3]
SHELL = ROOT / "tools" / "agents" / "bin" / "ceiling.sh"
REPO = "owner/repo"
BOT_TOKEN = "jeton-bot" + "-factice"  # noqa: S105 - valeur factice de test
OTHER_TOKEN = "jeton-herite" + "-factice"  # noqa: S105 - valeur factice de test

# gh factice : trace chaque appel avec le GH_TOKEN reçu ; FAKE_TOKEN vide = bot indisponible.
FAKE_GH = """#!/bin/sh
printf '%s|%s\\n' "${GH_TOKEN-}" "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "auth token") [ -n "$FAKE_TOKEN" ] || exit "${FAKE_CODE:-1}"; printf '%s\\n' "$FAKE_TOKEN" ;;
  "issue view") printf '{"labels":[],"comments":[]}\\n' ;;
  "issue comment") cat > /dev/null ;;
  *) exit 1 ;;
esac
"""


class FakeLaunchctl:
    def __init__(self, bootstrap: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.bootstrap = bootstrap

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return self.bootstrap if args[0] == "bootstrap" else 0


def _db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    init_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO token_usage (point_id, ts, session_id, agent_id, model, type, count)"
            " VALUES ('p', '2026-11-18T10:00:00+00:00', 's1', 'devops', 'm', 'output', 200000)"
        )
    conn.close()
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VVA_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))
    monkeypatch.setenv("VVA_CEILING_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VVA_DB_PATH", str(_db(tmp_path / "v & co.db")))
    monkeypatch.setenv("VVA_AGENTS_JOURNAL", str(tmp_path / "j.jsonl"))
    return tmp_path


def _plist(tmp: Path) -> Path:
    return tmp / "LaunchAgents" / f"{cs.label()}.plist"


# Scénario : installation


def test_install_charge_un_plist_sans_jeton(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", OTHER_TOKEN)
    monkeypatch.setenv("PATH", ".:/usr/bin:relatif")
    launchctl = FakeLaunchctl()
    assert cs.main(["install", "--repo", REPO], launchctl) == 0
    path = _plist(env)
    assert [c[0] for c in launchctl.calls] == ["bootout", "bootstrap"]
    assert launchctl.calls[-1][-1] == str(path)
    text = path.read_text(encoding="utf-8")
    assert OTHER_TOKEN not in text and "TOKEN" not in text and "@" not in text
    plist = plistlib.loads(text.encode("utf-8"))
    assert plist["Label"] == cs.label() and ".ceiling." in cs.label()
    assert plist["ProgramArguments"] == [
        "/bin/sh",
        str(SHELL),
        "--db",
        str((env / "v & co.db").resolve()),
        "--journal",
        str((env / "j.jsonl").resolve()),
        "--repo",
        REPO,
    ]
    assert plist["EnvironmentVariables"] == {"PATH": "/usr/bin"}
    assert plist["StartInterval"] == 15 * 60
    assert plist["RunAtLoad"] is False and plist["Umask"] == 0o077
    assert plist["StandardOutPath"] == str(env / "logs" / "ceiling.log")
    assert stat.S_IMODE((env / "logs").stat().st_mode) == 0o700


def test_intervalle_configurable_puis_uninstall(env: Path) -> None:
    launchctl = FakeLaunchctl()
    assert cs.main(["install", "--repo", REPO, "--interval", "60"], launchctl) == 0
    assert plistlib.loads(_plist(env).read_bytes())["StartInterval"] == 3600
    assert cs.main(["uninstall"], launchctl) == 0
    assert not _plist(env).exists()
    assert launchctl.calls[-1][0] == "bootout"


@pytest.mark.parametrize(
    "args",
    [
        ["--repo", "pas un depot; rm"],
        ["--repo", REPO, "--interval", "0"],
        ["--repo", REPO, "--interval", "1441"],
        ["--repo", REPO, "--db", "/nulle/part.db"],
    ],
)
def test_install_refuse_une_entree_invalide(env: Path, args: list[str]) -> None:
    launchctl = FakeLaunchctl()
    with pytest.raises(SystemExit):
        cs.main(["install", *args], launchctl)
    assert launchctl.calls == [] and not _plist(env).exists()


def test_echec_de_launchctl_signale(env: Path) -> None:
    with pytest.raises(SystemExit, match="bootstrap"):
        cs.main(["install", "--repo", REPO], FakeLaunchctl(bootstrap=5))
    assert not _plist(env).exists()


def test_label_distinct_par_chemin_de_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    labels = []
    for root in ("/a/usine", "/b/usine", "/a/usine"):
        monkeypatch.setattr(agent_loop, "ROOT", Path(root))
        labels.append(cs.label())
    assert labels[0] == labels[2] != labels[1]
    assert re.fullmatch(r"agent-factory\.ceiling\.usine\.[0-9a-f]{8}", labels[0])


def test_install_affiche_la_base_retenue(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cs.main(["install", "--repo", REPO], FakeLaunchctl()) == 0
    assert f"base {(env / 'v & co.db').resolve()}" in capsys.readouterr().out


# Scénario : jeton lu à l'exécution, ou absent


def _run_shell(
    tmp: Path, token: str, code: int = 1
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    journal = tmp / "j.jsonl"
    tr.append_record(journal, tr.link_record("s1", 42, "2026-11-18T09:00:00+00:00"))
    log = tmp / "gh.log"
    done = subprocess.run(  # noqa: S603 - script du dépôt, gh factice en tête du PATH
        ["/bin/sh", str(SHELL), "--db", str(_db(tmp / "v.db")), "--journal", str(journal)]
        + ["--repo", REPO],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": OTHER_TOKEN,
            "FAKE_GH_LOG": str(log),
            "FAKE_TOKEN": token,
            "FAKE_CODE": str(code),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return done, log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("code", [0, 1])  # sortie vide « réussie » ou gh en échec
def test_jeton_absent_erreur_claire_sans_ecriture(tmp_path: Path, code: int) -> None:
    done, calls = _run_shell(tmp_path, "", code)
    assert done.returncode == 1
    assert "infinitySoftMA" in done.stderr and "indisponible" in done.stderr
    assert calls == ["|auth token -u infinitySoftMA"]  # aucun appel issue, jeton hérité écarté


def test_jeton_du_bot_remplace_le_jeton_herite_sans_fuite(tmp_path: Path) -> None:
    done, calls = _run_shell(tmp_path, BOT_TOKEN)
    assert done.returncode == 0, done.stderr
    assert [c.split("|")[1].split()[:2] for c in calls[1:]] == [
        ["issue", "view"],
        ["issue", "comment"],
    ]
    assert all(c.startswith(f"{BOT_TOKEN}|") for c in calls[1:])
    assert all(BOT_TOKEN not in c.split("|", 1)[1] for c in calls)
    assert BOT_TOKEN not in done.stdout + done.stderr


# Makefile et hygiène


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
def test_make_ceiling_install_exige_repo() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("REPO", "DB", "INTERVAL")}
    done = subprocess.run(  # noqa: S603 - refus avant toute installation
        ["make", "ceiling-install"],  # noqa: S607
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0 and "REPO=" in done.stderr


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
@pytest.mark.parametrize(
    ("target", "expected"),
    [("ceiling-install", "ceiling.sh install"), ("ceiling-uninstall", "ceiling.sh uninstall")],
)
def test_make_cibles_appellent_le_script(target: str, expected: str) -> None:
    done = subprocess.run(  # noqa: S603 - make -n : rien n'est exécuté
        ["make", "-n", target, f"REPO={REPO}"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert expected in done.stdout


def test_compatible_python39() -> None:
    source = (ROOT / "tools" / "agents" / "ceiling_schedule.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 9))

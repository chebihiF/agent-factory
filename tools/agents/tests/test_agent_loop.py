"""Boucle autonome planifiée (#18) : gh, contrôles, pool, session et launchctl sont injectés."""

from __future__ import annotations

import ast
import json
import os
import plistlib
import shutil
import stat
import subprocess
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import agent_loop as al
import jsonschema  # type: ignore[import-untyped]  # pas de stubs (dépendance de dev)
import pytest

AGENTS = Path(__file__).resolve().parents[1]
ROOT = AGENTS.parents[1]
EXAMPLE = AGENTS / "config" / "loop.example.yml"
STATE_SCHEMA = ROOT / "contracts" / "agent-loop-state.schema.json"
TODAY = date(2026, 11, 20)
CONFIG = al.Config(
    hours=("09:00", "14:30"),
    daily_token_budget=1_000,
    max_tickets_per_day=2,
    control_url="https://dashboard.example.org/api/v1/control",
)
RUNNING = al.ControlState(paused=False, cap_tokens=500_000, tokens_24h=0)
BASE = "daily_token_budget: 1\nmax_tickets_per_day: 1\ncontrol_url: https://a/b\n"


def issue(n: int, *labels: str, body: str = "", status: str = "Prêt") -> dict[str, Any]:
    return {
        "number": n,
        "labels": [{"name": name} for name in labels],
        "body": body,
        "projectItems": [{"status": {"name": status}}],
    }


class Fakes:
    """gh, contrôle, pool et session en mémoire ; chaque appel est enregistré."""

    def __init__(
        self,
        issues: list[dict[str, Any]],
        control: al.ControlState | None = RUNNING,
        usage: int | None = 300,
    ) -> None:
        self.issues, self.control, self.usage = issues, control, usage
        self.pooled: list[int] = []
        self.sessions: list[tuple[Path, str]] = []

    def gh(self, args: Sequence[str]) -> str:
        return json.dumps(self.issues)

    def read_control(self, url: str) -> al.ControlState | None:
        return self.control

    def pool(self, n: int) -> tuple[str, Path]:
        self.pooled.append(n)
        return f"feat/{n}-ticket", Path(f"/postes/{n}")

    def session(self, cwd: Path, prompt: str) -> int | None:
        self.sessions.append((cwd, prompt))
        return self.usage

    def deps(self) -> al.Deps:
        return al.Deps(gh=self.gh, control=self.read_control, pool=self.pool, session=self.session)


def run_tick(fakes: Fakes, state_dir: Path, today: date = TODAY) -> str:
    return al.tick(CONFIG, fakes.deps(), state_dir, today)


def write_state(state_dir: Path, day: date, tokens: int, tickets: int = 0) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {"day": day.isoformat(), "tokens": tokens, "tickets": tickets}
    (state_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")


# --- Critères d'acceptation ---------------------------------------------------------------


def test_prise_du_ticket_prioritaire(tmp_path: Path) -> None:
    fakes = Fakes([issue(5, "P1"), issue(7, "P0")])
    code = run_tick(fakes, tmp_path)
    assert fakes.pooled == [7]
    assert len(fakes.sessions) == 1
    cwd, prompt = fakes.sessions[0]
    assert cwd == Path("/postes/7") and prompt.startswith("/ticket 7")
    assert code == "ran ticket=7 tokens=300"


def test_pause_aucune_session(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], control=al.ControlState(True, 500_000, 0))
    assert run_tick(fakes, tmp_path) == "paused"
    assert fakes.pooled == [] and fakes.sessions == []


def test_plafond_du_dashboard_atteint_aucune_session(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], control=al.ControlState(False, 100, 100))
    assert run_tick(fakes, tmp_path) == "cap-reached"
    assert fakes.sessions == []


def test_budget_epuise_aucune_session_avant_le_lendemain(tmp_path: Path) -> None:
    write_state(tmp_path, TODAY, tokens=CONFIG.daily_token_budget)
    fakes = Fakes([issue(7, "P0")])
    assert run_tick(fakes, tmp_path) == "budget-exhausted"
    assert fakes.sessions == []
    lendemain = date(2026, 11, 21)
    assert run_tick(fakes, tmp_path, lendemain).startswith("ran ticket=7")


def test_budget_cumule_et_depassement_bloque_la_suite(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], usage=CONFIG.daily_token_budget + 5)
    run_tick(fakes, tmp_path)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"day": "2026-11-20", "tokens": 1_005, "tickets": 1, "last_ticket": 7}
    assert run_tick(fakes, tmp_path) == "budget-exhausted"
    assert len(fakes.sessions) == 1


def test_tickets_max_par_jour(tmp_path: Path) -> None:
    write_state(tmp_path, TODAY, tokens=0, tickets=CONFIG.max_tickets_per_day)
    fakes = Fakes([issue(7, "P0")])
    assert run_tick(fakes, tmp_path) == "max-tickets"
    assert fakes.sessions == []


def test_consommation_inconnue_epuise_le_budget_du_jour(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], usage=None)
    assert run_tick(fakes, tmp_path) == "ran ticket=7 tokens=unknown"
    assert run_tick(fakes, tmp_path) == "budget-exhausted"


def test_ticket_stoppe_ignore_et_suivant_pris(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0", "stop-agents"), issue(5, "P1")])
    assert run_tick(fakes, tmp_path).startswith("ran ticket=5")
    assert fakes.pooled == [5]


def test_aucun_ticket_pret(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0", status="Backlog")])
    assert run_tick(fakes, tmp_path) == "no-ticket"
    assert fakes.sessions == []


def test_controle_illisible_aucune_session(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], control=None)
    assert run_tick(fakes, tmp_path) == "control-unreadable"
    assert fakes.pooled == [] and fakes.sessions == []


@pytest.mark.parametrize(
    "content",
    ["{pas du json", '{"day": "2026-11-20", "tokens": -1, "tickets": 0}', "[]"],
)
def test_compteur_local_illisible_aucune_session(tmp_path: Path, content: str) -> None:
    (tmp_path / "state.json").write_text(content, encoding="utf-8")
    fakes = Fakes([issue(7, "P0")])
    assert run_tick(fakes, tmp_path) == "state-unreadable"
    assert fakes.sessions == []


def test_gh_en_echec_aucune_session(tmp_path: Path) -> None:
    fakes = Fakes([])

    def broken(args: Sequence[str]) -> str:
        raise al.LoopError("gh-failed")

    deps = fakes.deps()
    deps.gh = broken
    assert al.tick(CONFIG, deps, tmp_path, TODAY) == "gh-failed"
    assert fakes.sessions == []


def test_tick_deja_en_cours_ne_lance_rien(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")])
    with al.exclusive(tmp_path) as held:
        assert held
        assert run_tick(fakes, tmp_path) == "busy"
    assert fakes.sessions == []


def test_pool_refuse_sans_session(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")])

    def refused(n: int) -> tuple[str, Path]:
        raise al.LoopError("pool-refused")

    deps = fakes.deps()
    deps.pool = refused
    assert al.tick(CONFIG, deps, tmp_path, TODAY) == "pool-refused"
    assert fakes.sessions == []


def test_ticket_en_echec_reste_pret_non_retente_le_meme_jour(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0"), issue(9, "P1")], usage=10)
    assert run_tick(fakes, tmp_path).startswith("ran ticket=7")
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_ticket"] == 7
    assert run_tick(fakes, tmp_path).startswith("ran ticket=9")  # 7 sauté, resté « Prêt »
    assert fakes.pooled == [7, 9]


def test_seul_ticket_deja_tente_donne_no_ticket_puis_retente_le_lendemain(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], usage=10)
    assert run_tick(fakes, tmp_path).startswith("ran ticket=7")
    assert run_tick(fakes, tmp_path) == "no-ticket"
    assert run_tick(fakes, tmp_path, date(2026, 11, 21)).startswith("ran ticket=7")


def test_etat_sans_dernier_ticket_reste_lisible_et_zero_refuse(tmp_path: Path) -> None:
    write_state(tmp_path, TODAY, tokens=5, tickets=1)
    assert al.load_state(tmp_path, TODAY) == al.DayState(TODAY, 5, 1, None)
    payload = {"day": "2026-11-20", "tokens": 0, "tickets": 0, "last_ticket": 0}
    (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(al.LoopError, match="state-unreadable"):
        al.load_state(tmp_path, TODAY)


def test_tokens_d_une_session_finie_apres_minuit_comptes_sur_le_nouveau_jour(
    tmp_path: Path,
) -> None:
    lendemain = date(2026, 11, 21)
    write_state(tmp_path, TODAY, tokens=100, tickets=1)
    fakes = Fakes([issue(7, "P0")], usage=300)
    code = al.tick(CONFIG, fakes.deps(), tmp_path, TODAY, clock=lambda: lendemain)
    assert code == "ran ticket=7 tokens=300"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"day": "2026-11-21", "tokens": 300, "tickets": 0}


def test_consommation_inconnue_apres_minuit_epuise_le_nouveau_jour(tmp_path: Path) -> None:
    lendemain = date(2026, 11, 21)
    fakes = Fakes([issue(7, "P0")], usage=None)
    al.tick(CONFIG, fakes.deps(), tmp_path, TODAY, clock=lambda: lendemain)
    assert al.tick(CONFIG, fakes.deps(), tmp_path, lendemain) == "budget-exhausted"


def test_session_meme_jour_compte_sur_ce_jour(tmp_path: Path) -> None:
    fakes = Fakes([issue(7, "P0")], usage=300)
    al.tick(CONFIG, fakes.deps(), tmp_path, TODAY, clock=lambda: TODAY)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["day"] == "2026-11-20" and state["tokens"] == 300 and state["tickets"] == 1


# --- Sélection ----------------------------------------------------------------------------


def test_selection_pret_seulement_tri_p0_p3_puis_numero() -> None:
    issues = [
        issue(1, "P3"),
        issue(2, "P0", status="Backlog"),
        issue(9, "P2"),
        issue(4, "P2"),
        issue(3),
    ]
    assert [t.number for t in al.candidates(json.dumps(issues))] == [4, 9, 1, 3]


def test_dependances_ouvertes_ignorees() -> None:
    issues = [
        issue(7, "P0", body="Épique : #12 — Dépend de #14, #15 et de #8 (contrôles)"),
        issue(15, "P1"),
        issue(6, "P2", body="Épique : #12 — Dépend de #3"),
    ]
    assert [t.number for t in al.candidates(json.dumps(issues))] == [15, 6]


def test_dependance_vers_un_autre_depot_ignoree() -> None:
    issues = [issue(7, "P0", body="Dépend de autre/depot#3"), issue(6, "P2")]
    assert [t.number for t in al.candidates(json.dumps(issues))] == [6]


def test_liste_tronquee_refusee() -> None:
    issues = [issue(n) for n in range(1, al.LIST_LIMIT + 1)]
    with pytest.raises(al.LoopError, match="gh-truncated"):
        al.candidates(json.dumps(issues))


@pytest.mark.parametrize("raw", ['{"pas": "une liste"}', "pas du json", '[{"number": "7"}]'])
def test_reponse_gh_illisible_refusee(raw: str) -> None:
    with pytest.raises(al.LoopError, match="gh-unreadable"):
        al.candidates(raw)


# --- Contrôles (#8, lecture seule) --------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "ok"),
    [
        (b'{"paused": false, "reason": null, "cap_tokens": 10, "tokens_24h": 3}', True),
        (b'{"paused": "non", "cap_tokens": 10, "tokens_24h": 3}', False),
        (b'{"paused": false, "cap_tokens": true, "tokens_24h": 3}', False),
        (b'{"paused": false, "cap_tokens": 0, "tokens_24h": 3}', False),
        (b'{"paused": false, "cap_tokens": 10}', False),
        (b"pas du json", False),
    ],
)
def test_etat_de_controle_strict(payload: bytes, ok: bool) -> None:
    expected = al.ControlState(False, 10, 3) if ok else None
    assert al.parse_control(payload) == expected


def test_controle_sans_jeton_illisible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VVA_INGEST_TOKEN", raising=False)
    monkeypatch.setenv("VVA_INGEST_TOKEN_FILE", str(tmp_path / "absent"))
    assert al.read_control(CONFIG.control_url) is None


def test_jeton_lisible_par_d_autres_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / "ingest_token"
    token.write_text("abc\n", encoding="utf-8")
    token.chmod(0o644)
    monkeypatch.delenv("VVA_INGEST_TOKEN", raising=False)
    monkeypatch.setenv("VVA_INGEST_TOKEN_FILE", str(token))
    assert al.ingest_token() is None
    token.chmod(0o600)
    assert al.ingest_token() == "abc"


def test_controle_url_non_https_illisible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_INGEST_TOKEN", "abc")
    assert al.read_control("http://dashboard.example.org/api/v1/control") is None


def test_controle_serveur_injoignable_illisible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_INGEST_TOKEN", "abc")
    assert al.read_control("http://127.0.0.1:9/api/v1/control") is None


# --- Session et poste ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            {
                "modelUsage": {
                    "claude-sonnet-5-5": {
                        "inputTokens": 10,
                        "outputTokens": 20,
                        "cacheReadInputTokens": 30,
                        "cacheCreationInputTokens": 40,
                    },
                    "claude-haiku-5-5": {"inputTokens": 1, "outputTokens": 2},
                }
            },
            73,
        ),
        ({"usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3}}, 3),
        ({"usage": {"input_tokens": -1}}, None),
        ({"usage": {"input_tokens": True}}, None),
        ({"modelUsage": {"m": "x"}}, None),
        ({"result": "pas de compteur"}, None),
    ],
)
def test_tokens_de_la_session(output: dict[str, Any], expected: int | None) -> None:
    assert al.session_tokens(json.dumps(output)) == expected


def test_sortie_de_session_illisible() -> None:
    assert al.session_tokens("Erreur : pas de JSON") is None


def test_session_reelle_prompt_sur_stdin_dans_le_poste(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text(
        "#!/bin/sh\n"
        'cat > "$PWD/prompt.txt"\n'
        'echo \'{"usage": {"input_tokens": 4, "output_tokens": 5}}\'\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("LOOP_SESSION_CMD", str(fake))
    assert al.run_session(tmp_path, "/ticket 7\n") == 9
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "/ticket 7\n"


def test_session_par_defaut_sans_contournement_des_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOP_SESSION_CMD", raising=False)
    command = al.session_command()
    assert command == ["claude", "-p", "--output-format", "json"]
    assert not any("permission" in part or "dangerously" in part for part in command)


def test_delai_depasse_tue_tout_le_groupe_de_processus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake-claude"
    pidfile = tmp_path / "child.pid"
    fake.write_text(
        f"#!/bin/sh\nsleep 60 &\necho $! > {pidfile}\nwait\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("LOOP_SESSION_CMD", str(fake))
    monkeypatch.setattr(al, "SESSION_TIMEOUT_S", 1)
    assert al.run_session(tmp_path, "/ticket 7\n") is None
    child = int(pidfile.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.05)
    assert not _alive(child)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    done = subprocess.run(  # noqa: S603
        ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607
        capture_output=True,
        text=True,
    )
    return bool(done.stdout.strip()) and not done.stdout.strip().startswith("Z")


@pytest.mark.parametrize(
    "command",
    [
        "claude -p --dangerously-skip-permissions",
        "claude -p --permission-mode bypassPermissions",
        "claude -p --permission-mode=bypassPermissions",
        "claude --allow-dangerously-skip-permissions",
        "sh -c 'claude --dangerously-skip-permissions'",
        "claude -p --permission-mode BYPASSPERMISSIONS",
        "claude 'non fermé",
    ],
)
def test_contournement_des_permissions_refuse_sans_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("LOOP_SESSION_CMD", command)
    with pytest.raises(al.LoopError, match="session-cmd-refused"):
        al.session_command()
    fakes = Fakes([issue(7, "P0")])
    assert run_tick(fakes, tmp_path) == "session-cmd-refused"
    assert fakes.pooled == [] and fakes.sessions == []
    assert not (tmp_path / "state.json").exists()


def test_autres_modes_de_permission_acceptes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOP_SESSION_CMD", "claude -p --permission-mode acceptEdits")
    assert al.session_command()[-1] == "acceptEdits"


def test_session_introuvable_consommation_inconnue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOP_SESSION_CMD", str(tmp_path / "absent"))
    assert al.run_session(tmp_path, "/ticket 7\n") is None


def test_pool_imprime_branche_puis_poste(tmp_path: Path) -> None:
    script = tmp_path / "pool.sh"
    script.write_text(f'#!/bin/sh\necho "pool : feat/$1-x"\necho "{tmp_path}"\n', encoding="utf-8")
    script.chmod(0o755)
    assert al.run_pool(7, script) == ("feat/7-x", tmp_path)


@pytest.mark.parametrize(
    "body", ["echo refus >&2\nexit 1\n", 'echo "pool : feat/7-x"\necho /nulle/part\n']
)
def test_pool_refuse(tmp_path: Path, body: str) -> None:
    script = tmp_path / "pool.sh"
    script.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(al.LoopError, match="pool-refused"):
        al.run_pool(7, script)


# --- Configuration (contrat) --------------------------------------------------------------


def test_exemple_de_configuration_valide() -> None:
    config = al.parse_config(EXAMPLE.read_text(encoding="utf-8"))
    assert config.hours and config.daily_token_budget >= 1 and config.max_tickets_per_day >= 1
    assert config.control_url.startswith("https://")


def test_configuration_heures_entre_guillemets() -> None:
    config = al.parse_config('hours: ["09:00", 14:30] # matin, après-midi\n' + BASE)
    assert config.hours == ("09:00", "14:30")


@pytest.mark.parametrize(
    "text",
    [
        "hours: [25:00]\n" + BASE,
        "hours: []\n" + BASE,
        "hours: 09:00\n" + BASE,
        "hours: [09:00, 09:00]\n" + BASE,
        "hours: [09:00]\n" + BASE.replace("budget: 1", "budget: 0"),
        "hours: [09:00]\n" + BASE.replace("per_day: 1", "per_day: x"),
        "hours: [09:00]\n" + BASE.replace("https://a/b", "http://a/b"),
        "hours: [09:00]\ndaily_token_budget: 1\nmax_tickets_per_day: 1\n",
        "hours: [09:00]\n" + BASE + "inconnue: 1\n",
        "hours: [09:00]\n" + BASE + "  indentee: 1\n",
    ],
)
def test_configuration_invalide_refusee(text: str) -> None:
    with pytest.raises(al.LoopError, match="config-invalid"):
        al.parse_config(text)


def test_cli_configuration_absente_aucune_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert al.main(["tick", "--config", str(tmp_path / "absente.yml")]) == 1
    assert "config-invalid" in capsys.readouterr().out


def test_cli_tick_une_ligne_sans_contenu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "loop.yml"
    config_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("VVA_LOOP_DIR", str(tmp_path / "loop"))
    fakes = Fakes([issue(7, "P0", body="secret du ticket")], control=None)
    assert al.main(["tick", "--config", str(config_path)], deps=fakes.deps()) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.rstrip().endswith(" control-unreadable")


def test_etat_conforme_au_schema(tmp_path: Path) -> None:
    run_tick(Fakes([issue(7, "P0")]), tmp_path)
    schema = json.loads(STATE_SCHEMA.read_text(encoding="utf-8"))
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    jsonschema.validate(state, schema)
    assert stat.S_IMODE((tmp_path / "state.json").stat().st_mode) == 0o600


# --- Planification launchd ----------------------------------------------------------------


def test_plist_heures_planifiees(tmp_path: Path) -> None:
    config_path = tmp_path / "loop & co.yml"
    rendered = al.render_plist(CONFIG, config_path, "/usr/bin:/bin", tmp_path / "loop.log")
    plist = plistlib.loads(rendered.encode("utf-8"))
    assert plist["StartCalendarInterval"] == [
        {"Hour": 9, "Minute": 0},
        {"Hour": 14, "Minute": 30},
    ]
    assert plist["ProgramArguments"][-3:] == ["tick", "--config", str(config_path)]
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}
    assert plist["Label"] == al.label()
    assert plist["RunAtLoad"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/usr/bin:/bin", "/usr/bin:/bin"),
        ("/opt/homebrew/bin:.:bin::~/x:/usr/bin", "/opt/homebrew/bin:/usr/bin"),
        ("/usr/bin:/usr/bin", "/usr/bin"),
        ("", "/usr/bin:/bin"),
        (".:relatif", "/usr/bin:/bin"),
    ],
)
def test_path_du_plist_entrees_absolues_seulement(raw: str, expected: str) -> None:
    assert al.absolute_path(raw) == expected


def test_install_ecrit_un_path_absolu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "loop.yml"
    config_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("VVA_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))
    monkeypatch.setenv("VVA_LOOP_DIR", str(tmp_path / "loop"))
    monkeypatch.setenv("PATH", ".:/usr/bin:bin")
    deps = al.Deps(launchctl=FakeLaunchctl())
    assert al.main(["install", "--config", str(config_path)], deps=deps) == 0
    plist = plistlib.loads((tmp_path / "LaunchAgents" / f"{al.label()}.plist").read_bytes())
    assert plist["EnvironmentVariables"]["PATH"] == "/usr/bin"


def test_dossier_d_etat_en_700_meme_preexistant(tmp_path: Path) -> None:
    directory = tmp_path / "loop"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with al.exclusive(directory) as held:
        assert held
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
@pytest.mark.parametrize(("config", "refused"), [("a'b.yml", True), ("a b.yml", False)])
def test_make_loop_install_refuse_l_apostrophe(config: str, refused: bool) -> None:
    done = subprocess.run(  # noqa: S603
        ["make", "-n", "loop-install", f"CONFIG={config}"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert (done.returncode != 0) is refused
    assert ("apostrophe" in done.stdout + done.stderr) is refused


class FakeLaunchctl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return 0


def test_install_puis_uninstall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "loop.yml"
    config_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    agents_dir = tmp_path / "LaunchAgents"
    monkeypatch.setenv("VVA_LAUNCH_AGENTS", str(agents_dir))
    monkeypatch.setenv("VVA_LOOP_DIR", str(tmp_path / "loop"))
    launchctl = FakeLaunchctl()
    deps = al.Deps(launchctl=launchctl)
    assert al.main(["install", "--config", str(config_path)], deps=deps) == 0
    plist_path = agents_dir / f"{al.label()}.plist"
    assert plistlib.loads(plist_path.read_bytes())["Label"] == al.label()
    assert [c[0] for c in launchctl.calls] == ["bootout", "bootstrap"]
    assert launchctl.calls[-1][-1] == str(plist_path)
    assert al.main(["uninstall"], deps=deps) == 0
    assert not plist_path.exists()
    assert launchctl.calls[-1][0] == "bootout"


def test_install_refuse_une_configuration_invalide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "loop.yml"
    config_path.write_text("hours: [99:99]\n", encoding="utf-8")
    monkeypatch.setenv("VVA_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))
    launchctl = FakeLaunchctl()
    deps = al.Deps(launchctl=launchctl)
    assert al.main(["install", "--config", str(config_path)], deps=deps) == 1
    assert launchctl.calls == []


# --- Hygiène ------------------------------------------------------------------------------


def test_compatible_python39() -> None:
    """launchd lance le python3 du Mac (3.9) : syntaxe 3.9 seulement."""
    source = (AGENTS / "agent_loop.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 9))
    assert "datetime.UTC" not in source


def test_enveloppe_executable() -> None:
    assert os.access(AGENTS / "bin" / "agent-loop.sh", os.X_OK)

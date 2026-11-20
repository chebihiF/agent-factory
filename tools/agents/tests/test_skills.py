"""Skills versionnés /ticket, /sprint, /deploy-check (#15, #16) : lecture des fichiers seulement."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import handoff as ho
import pytest

REPO = Path(__file__).resolve().parents[3]
SKILLS = REPO / "skills"
CHECKLIST = SKILLS / "ticket" / "checklist.md"
DOC = REPO / "docs" / "agents.md"
SCRUM = REPO / "docs" / "SCRUM.md"
NAMES = ["ticket", "sprint", "deploy-check"]
FRONTMATTER_KEYS = {"name", "description", "argument-hint"}

# Fichiers génériques (copiés tels quels chez chaque projet) : aucune valeur propre à un projet.
GENERIC = ["skills", "agents", "rules", "hooks", "docs/agents.md"]
FORBIDDEN = [
    r"[0-9]{1,3}(\.[0-9]{1,3}){3}",  # adresse IPv4 (LAN, VPS)
    r"/(Users|home)/",  # chemin machine
    r"hstgr|srv[0-9]{4,}",  # nom d'hôte du VPS
    r"pc[- ]?gpu",  # machine GPU du produit
    r"\.claude/worktrees|feat-[0-9]+-[a-z0-9-]+-[0-9a-f]{6}",  # poste nommé
    r"viral-video-agents",  # dépôt du produit
]
FORBIDDEN_SAMPLES = [
    "ssh 192.168.1.14",
    "cd /Users/x/dev",
    "https://dashboard.srv1241648.hstgr.cloud",
    "pc-gpu wake",
    ".claude/worktrees/feat-13-agents-c21e32",
    "-R owner/viral-video-agents",
]

CONTRACT = {
    "ticket": [
        "stop-agents",
        "rien n'est modifié",
        "En cours",
        "make check",
        "2 échecs",
        "aucun merge",
        "Closes #<n>",
        "feat/<n>-<slug>",
        "gh auth token -u <bot>",
        "Revue",
        "reviewer",
        "--squash",
        "Livré",
        "checklist.md",
        "15 lignes",
    ],
    "sprint": [
        "/sprint move <n> <colonne>",
        "/sprint next",
        "stop-agents",
        "Restant",
        "15 lignes",
        "--single-select-option-id",
        "--iteration-id",
        "duration",
        "Attend l'utilisateur : ajouter l'itération",
        "liste tronquée à 200 cartes",
    ],
    "deploy-check": [
        "/healthz",
        "smoke test",
        "dernier merge",
        "aucune action",
        "confirmation explicite",
        "gh run rerun",
        "type:bug",
        "P0",
        "15 lignes",
        "aucun secret",
        "git rev-parse origin/main",
        "commits/main --jq .sha",
        "^[A-Za-z0-9._-]{1,64}$",
        "gh issue list -l type:bug -S",
        "--body-file",
    ],
}
ABSENT = {
    "sprint": ["updateProjectV2Field", "7 jours"],
    "deploy-check": ["gh pr list", '-b "', "--body "],
}
# Écarts voulus entre la DoD du projet (docs/SCRUM.md) et la checklist générique.
GENERIC_DOD = {
    "si le déclencheur ci-dessous s'applique": "si le déclencheur de `docs/SCRUM.md` s'applique",
    ", aucune donnée d'enfant": " ni donnée personnelle",
}
SCENARIOS = {
    "ticket": ["Boucle nominale", "Ticket bloqué", "Échecs répétés"],
    "sprint": ["État du sprint", "Déplacement", "Sprint suivant"],
    "deploy-check": ["Production saine", "Production en échec"],
}


def _text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "front-matter YAML absent"
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if line.strip())
    return {key.strip(): value.strip() for key, value in pairs}


def _skill_dirs() -> list[str]:
    return sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))


def test_expected_skills_exist() -> None:
    assert set(NAMES) <= set(_skill_dirs())


@pytest.mark.parametrize("name", _skill_dirs())
def test_frontmatter_is_valid(name: str) -> None:
    meta = _frontmatter(_text(name))
    assert set(meta) <= FRONTMATTER_KEYS
    assert meta["name"] == name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", meta["name"])
    assert 20 <= len(meta["description"]) <= 1024
    assert "Usage /" + name in meta["description"]


@pytest.mark.parametrize(("name", "term"), [(n, t) for n, ts in CONTRACT.items() for t in ts])
def test_skill_covers_contract(name: str, term: str) -> None:
    assert term in _text(name)


@pytest.mark.parametrize(("name", "term"), [(n, t) for n, ts in ABSENT.items() for t in ts])
def test_skill_avoids_unsafe_pattern(name: str, term: str) -> None:
    assert term not in _text(name)


@pytest.mark.parametrize(("name", "scenario"), [(n, s) for n, ss in SCENARIOS.items() for s in ss])
def test_gherkin_scenario_is_traced(name: str, scenario: str) -> None:
    assert scenario in _text(name)


def test_ticket_numbers_eight_steps_in_order() -> None:
    numbers = [int(n) for n in re.findall(r"^(\d+)\. ", _text("ticket"), re.MULTILINE)]
    assert numbers == list(range(1, 9))


def _dod(text: str) -> list[str]:
    section = text.split("## Definition of Done", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- \[ \] .+$", section, re.MULTILINE)


def test_checklist_is_the_scrum_definition_of_done() -> None:
    dod = _dod(SCRUM.read_text(encoding="utf-8"))
    for old, new in GENERIC_DOD.items():
        dod = [item.replace(old, new) for item in dod]
    items = re.findall(r"^- \[ \] .+$", CHECKLIST.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(dod) >= 5
    assert items == dod


@pytest.mark.parametrize(
    "path", ["skills/ticket/checklist.md", *(f"skills/{n}/SKILL.md" for n in NAMES)]
)
def test_agents_doc_references_skill_files(path: str) -> None:
    assert path in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("sample", FORBIDDEN_SAMPLES)
def test_forbidden_patterns_detect_samples(sample: str) -> None:
    assert any(re.search(p, sample, re.IGNORECASE) for p in FORBIDDEN)


def test_generic_files_have_no_project_value() -> None:
    args = [arg for pattern in FORBIDDEN for arg in ("-e", pattern)]
    found = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), "grep", "--untracked", "-nIiE", *args, "--", *GENERIC],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode == 1, found.stdout or found.stderr


@pytest.mark.parametrize("name", _skill_dirs())
def test_skill_has_no_secret(name: str) -> None:
    for path in (SKILLS / name).iterdir():
        assert not ho.SECRETS.search(path.read_text(encoding="utf-8")), path.name

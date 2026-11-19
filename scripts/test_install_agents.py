"""install_agents.py : installation locale des agents, skills et hooks dans un projet cible."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import install_agents as ia

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_LINES = [".claude/", "CLAUDE.md"]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def target(tmp_path: Path) -> Path:
    d = tmp_path / "projet"
    d.mkdir()
    git("init", "-q", cwd=d)
    return d


def tree(root: Path) -> dict[str, bytes]:
    files = (p for p in sorted(root.rglob("*")) if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def exclude_lines(repo: Path) -> list[str]:
    return (repo / ".git" / "info" / "exclude").read_text().splitlines()


def test_installation_locale(target: Path) -> None:
    assert ia.main([str(target)]) == 0
    claude = target / ".claude"
    for sub in ("agents", "skills", "hooks", "rules"):
        assert tree(ROOT / sub) == tree(claude / sub), sub
    assert (claude / "agents" / "qa.md").is_file()
    assert (claude / "skills" / "ticket" / "SKILL.md").is_file()
    assert (claude / "hooks" / "lint_file.sh").is_file()


def test_exclude_contient_claude_et_claude_md(target: Path) -> None:
    ia.main([str(target)])
    lines = exclude_lines(target)
    assert all(entry in lines for entry in EXCLUDE_LINES)


def test_exclude_existant_conserve_et_sans_doublon(target: Path) -> None:
    exclude = target / ".git" / "info" / "exclude"
    exclude.write_text("# perso\n*.log")  # sans saut de ligne final
    ia.main([str(target)])
    ia.main([str(target)])
    lines = exclude_lines(target)
    assert lines[:2] == ["# perso", "*.log"]
    assert [ln for ln in lines if ln in EXCLUDE_LINES] == EXCLUDE_LINES


def test_idempotent(target: Path) -> None:
    ia.main([str(target)])
    first = tree(target / ".claude"), exclude_lines(target)
    assert ia.main([str(target)]) == 0
    assert (tree(target / ".claude"), exclude_lines(target)) == first


def test_remet_a_jour_un_fichier_modifie(target: Path) -> None:
    ia.main([str(target)])
    qa = target / ".claude" / "agents" / "qa.md"
    qa.write_text("obsolete\n")
    ia.main([str(target)])
    assert qa.read_bytes() == (ROOT / "agents" / "qa.md").read_bytes()


def test_conserve_les_fichiers_locaux(target: Path) -> None:
    (target / ".claude").mkdir()
    (target / ".claude" / "settings.json").write_text("{}\n")
    (target / ".claude" / "agents").mkdir()
    (target / ".claude" / "agents" / "mien.md").write_text("mien\n")
    ia.main([str(target)])
    assert (target / ".claude" / "settings.json").read_text() == "{}\n"
    assert (target / ".claude" / "agents" / "mien.md").read_text() == "mien\n"


def test_refuse_hors_depot_git(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "pas-git"
    plain.mkdir()
    assert ia.main([str(plain)]) != 0
    assert list(plain.iterdir()) == []
    assert "git" in capsys.readouterr().err


def test_refuse_sous_dossier_du_depot(target: Path) -> None:
    sub = target / "sous"
    sub.mkdir()
    assert ia.main([str(sub)]) != 0
    assert list(sub.iterdir()) == []
    assert not (target / ".git" / "info" / "exclude").read_text().count("CLAUDE.md")


def test_refuse_cible_inexistante(tmp_path: Path) -> None:
    assert ia.main([str(tmp_path / "absent")]) != 0


def test_ne_commite_rien(target: Path) -> None:
    git("config", "user.email", "t@example.org", cwd=target)
    git("config", "user.name", "t", cwd=target)
    (target / "a.txt").write_text("a\n")
    git("add", "a.txt", cwd=target)
    git("commit", "-q", "-m", "init", cwd=target)
    head = git("rev-parse", "HEAD", cwd=target)
    ia.main([str(target)])
    assert git("rev-parse", "HEAD", cwd=target) == head
    assert git("status", "--porcelain", cwd=target) == ""  # tout est exclu
    assert git("ls-files", cwd=target).split() == ["a.txt"]
    assert git("diff", "--cached", "--name-only", cwd=target) == ""


def test_worktree_git(target: Path, tmp_path: Path) -> None:
    git("config", "user.email", "t@example.org", cwd=target)
    git("config", "user.name", "t", cwd=target)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=target)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", "--detach", str(wt), cwd=target)
    assert ia.main([str(wt)]) == 0
    assert (wt / ".claude" / "agents" / "qa.md").is_file()
    assert git("status", "--porcelain", cwd=wt) == ""


def test_overlay_ajoute_un_bloc_dans_claude_md(target: Path, tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.md"
    overlay.write_text("# Propre au projet\n- service X a ne pas toucher\n")
    (target / "CLAUDE.md").write_text("# Mes regles\n")
    assert ia.main([str(target), "--overlay", str(overlay)]) == 0
    text = (target / "CLAUDE.md").read_text()
    assert text.startswith("# Mes regles\n")
    assert "- service X a ne pas toucher" in text
    assert git("status", "--porcelain", cwd=target) == ""


def test_overlay_cree_claude_md_et_remplace_le_bloc(target: Path, tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.md"
    overlay.write_text("version 1\n")
    ia.main([str(target), "--overlay", str(overlay)])
    overlay.write_text("version 2\n")
    ia.main([str(target), "--overlay", str(overlay)])
    ia.main([str(target), "--overlay", str(overlay)])
    text = (target / "CLAUDE.md").read_text()
    assert "version 2" in text
    assert "version 1" not in text
    assert text.count(ia.OVERLAY_BEGIN) == 1


def test_sans_overlay_ne_touche_pas_claude_md(target: Path) -> None:
    (target / "CLAUDE.md").write_text("local\n")
    ia.main([str(target)])
    assert (target / "CLAUDE.md").read_text() == "local\n"


def test_overlay_introuvable(target: Path, tmp_path: Path) -> None:
    assert ia.main([str(target), "--overlay", str(tmp_path / "absent.md")]) != 0
    assert not (target / ".claude").exists()


def test_installe_aussi_depuis_un_autre_source(target: Path, tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "x.md").write_text("x\n")
    assert ia.install(src, target) == 1
    assert (target / ".claude" / "agents" / "x.md").read_text() == "x\n"
    assert not (target / ".claude" / "skills").exists()

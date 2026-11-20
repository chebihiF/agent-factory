"""install_agents.py : installation locale des agents, skills et hooks dans un projet cible."""

from __future__ import annotations

import subprocess
import sys
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


def test_regle_de_session_installee(target: Path) -> None:
    assert ia.main([str(target)]) == 0
    installed = target / ".claude" / "rules" / "session-agents.md"
    assert installed.is_file()
    assert installed.read_bytes() == (ROOT / "rules" / "session-agents.md").read_bytes()


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


def make_src(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return root


# (1) fichiers Claude déjà suivis par git dans la cible


@pytest.mark.parametrize("tracked", ["CLAUDE.md", ".claude/agents/qa.md"])
def test_refuse_si_fichier_claude_suivi(target: Path, tracked: str) -> None:
    f = target / tracked
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x\n")
    git("add", "-f", tracked, cwd=target)
    before = (target / ".git" / "info" / "exclude").read_text()
    assert ia.main([str(target)]) != 0
    assert (target / ".git" / "info" / "exclude").read_text() == before
    assert not (target / ".claude" / "skills").exists()


# (2) liens symboliques


def test_refuse_claude_lien_symbolique(target: Path, tmp_path: Path) -> None:
    outside = tmp_path / "dehors"
    outside.mkdir()
    (target / ".claude").symlink_to(outside)
    assert ia.main([str(target)]) != 0
    assert list(outside.iterdir()) == []


def test_refuse_sous_dossier_lien_symbolique(target: Path, tmp_path: Path) -> None:
    outside = tmp_path / "dehors"
    outside.mkdir()
    (target / ".claude").mkdir()
    (target / ".claude" / "agents").symlink_to(outside)
    assert ia.main([str(target)]) != 0
    assert list(outside.iterdir()) == []


def test_refuse_fichier_destination_lien_symbolique(target: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victime.txt"
    victim.write_text("intact\n")
    (target / ".claude" / "agents").mkdir(parents=True)
    (target / ".claude" / "agents" / "qa.md").symlink_to(victim)
    assert ia.main([str(target)]) != 0
    assert victim.read_text() == "intact\n"


def test_refuse_manifeste_lien_symbolique(target: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victime.txt"
    victim.write_text("intact\n")
    (target / ".claude").mkdir()
    (target / ".claude" / ia.MANIFEST).symlink_to(victim)
    assert ia.main([str(target)]) != 0
    assert victim.read_text() == "intact\n"
    assert not (target / ".claude" / "agents").exists()  # rien écrit


def test_refuse_claude_md_lien_symbolique(target: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victime.md"
    victim.write_text("intact\n")
    (target / "CLAUDE.md").symlink_to(victim)
    overlay = tmp_path / "o.md"
    overlay.write_text("x\n")
    assert ia.main([str(target), "--overlay", str(overlay)]) != 0
    assert victim.read_text() == "intact\n"


def test_ignore_les_liens_symboliques_cote_source(target: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    other = tmp_path / "autre"
    make_src(other, {"x.md": "x\n"})
    src = make_src(tmp_path / "src", {"agents/ok.md": "ok\n"})
    (src / "agents" / "lien.md").symlink_to(secret)
    (src / "skills").symlink_to(other)
    assert ia.install(src, target) == 1
    assert [p.name for p in (target / ".claude" / "agents").iterdir()] == ["ok.md"]
    assert not (target / ".claude" / "skills").exists()


# (3) marqueurs de surcouche


def test_overlay_refuse_les_marqueurs(target: Path, tmp_path: Path) -> None:
    for marker in (ia.OVERLAY_BEGIN, ia.OVERLAY_END):
        overlay = tmp_path / "o.md"
        overlay.write_text(f"texte\n{marker}\n")
        assert ia.main([str(target), "--overlay", str(overlay)]) != 0
    assert not (target / ".claude").exists()


def test_overlay_fin_avant_debut_ne_tronque_pas(target: Path, tmp_path: Path) -> None:
    overlay = tmp_path / "o.md"
    overlay.write_text("surcouche\n")
    (target / "CLAUDE.md").write_text(f"{ia.OVERLAY_END}\nperso\n")
    assert ia.main([str(target), "--overlay", str(overlay)]) == 0
    text = (target / "CLAUDE.md").read_text()
    assert text.startswith(f"{ia.OVERLAY_END}\nperso\n")
    assert "surcouche" in text


def test_overlay_bloc_existant_sans_fin_refuse(target: Path, tmp_path: Path) -> None:
    overlay = tmp_path / "o.md"
    overlay.write_text("surcouche\n")
    original = f"perso\n{ia.OVERLAY_BEGIN}\nreste\n"
    (target / "CLAUDE.md").write_text(original)
    assert ia.main([str(target), "--overlay", str(overlay)]) != 0
    assert (target / "CLAUDE.md").read_text() == original


# (4) manifeste


def test_manifeste_retire_les_fichiers_disparus(target: Path, tmp_path: Path) -> None:
    v1 = make_src(
        tmp_path / "v1", {"agents/a.md": "a\n", "agents/b.md": "b\n", "skills/s/S.md": "s\n"}
    )
    v2 = make_src(tmp_path / "v2", {"agents/a.md": "a2\n"})
    ia.install(v1, target)
    claude = target / ".claude"
    (claude / "agents" / "mien.md").write_text("mien\n")
    (claude / "settings.json").write_text("{}\n")
    ia.install(v2, target)
    assert (claude / "agents" / "a.md").read_text() == "a2\n"
    assert not (claude / "agents" / "b.md").exists()
    assert not (claude / "skills").exists()  # dossier vidé retiré
    assert (claude / "agents" / "mien.md").read_text() == "mien\n"
    assert (claude / "settings.json").read_text() == "{}\n"
    assert (claude / ia.MANIFEST).read_text().split() == ["agents/a.md"]


def test_manifeste_ignore_les_entrees_hors_perimetre(target: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victime.txt"
    victim.write_text("intact\n")
    (target / ".claude").mkdir()
    (target / ".claude" / "settings.json").write_text("{}\n")
    (target / ".claude" / ia.MANIFEST).write_text(
        "../../victime.txt\n/etc/passwd\nsettings.json\nagents/../settings.json\n"
    )
    ia.install(make_src(tmp_path / "src", {"agents/a.md": "a\n"}), target)
    assert victim.read_text() == "intact\n"
    assert (target / ".claude" / "settings.json").read_text() == "{}\n"


def test_manifeste_present_apres_installation_reelle(target: Path) -> None:
    ia.main([str(target)])
    listed = (target / ".claude" / ia.MANIFEST).read_text().split()
    assert "agents/qa.md" in listed
    assert listed == sorted(listed)


# (5) git absent


def test_message_distinct_si_git_absent(
    target: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert ia.main([str(target)]) != 0
    err = capsys.readouterr().err
    assert "git est introuvable" in err
    assert "n'est pas un dépôt" not in err


def test_script_executable_en_ligne_de_commande(target: Path) -> None:
    script = ROOT / "scripts" / "install_agents.py"
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(script), str(target)], capture_output=True, text=True
    )
    assert done.returncode == 0
    assert (target / ".claude" / "agents" / "qa.md").is_file()

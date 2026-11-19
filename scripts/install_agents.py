#!/usr/bin/env python3
"""Installe agents, skills, hooks et règles de l'usine dans le `.claude/` local d'un projet.

Usage : install_agents.py <projet> [--overlay <fichier>]
ou : make install-agents PROJECT=<chemin> [OVERLAY=<fichier>]

- copie `agents/`, `skills/`, `hooks/`, `rules/` vers `<projet>/.claude/` (les fichiers de
  l'usine écrasent leur version locale ; les fichiers propres au projet sont conservés) ;
- ajoute `.claude/` et `CLAUDE.md` à `<projet>/.git/info/exclude` (sans doublon) ;
- `--overlay` : insère le fichier dans un bloc balisé de `<projet>/CLAUDE.md` (créé si absent,
  bloc remplacé à chaque installation, reste du fichier intact) ;
- refuse un dossier qui n'est pas la racine d'un dépôt git ; ne lance jamais `git add` ni `commit`.

Stdlib seule, compatible Python 3.9 (poste du parent) comme 3.12.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_DIRS = ("agents", "skills", "hooks", "rules")
EXCLUDE_ENTRIES = (".claude/", "CLAUDE.md")
OVERLAY_BEGIN = "<!-- agent-factory:overlay:begin -->"
OVERLAY_END = "<!-- agent-factory:overlay:end -->"
FACTORY_ROOT = Path(__file__).resolve().parents[1]


class InstallError(Exception):
    """Entrée invalide : message destiné au parent, sans trace."""


def _git(target: Path, *args: str) -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(target), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InstallError(f"{target} n'est pas un dépôt git") from exc
    return proc.stdout.strip()


def check_target(target: Path) -> Path:
    """Renvoie le chemin de `info/exclude` ; refuse tout ce qui n'est pas la racine d'un dépôt."""
    if not target.is_dir():
        raise InstallError(f"cible introuvable : {target}")
    top = Path(_git(target, "rev-parse", "--show-toplevel"))
    if top.resolve() != target.resolve():
        raise InstallError(f"{target} n'est pas la racine d'un dépôt git ({top})")
    exclude = Path(_git(target, "rev-parse", "--git-path", "info/exclude"))
    return exclude if exclude.is_absolute() else target / exclude


def ensure_excluded(exclude: Path) -> None:
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    present = set(text.splitlines())
    missing = [e for e in EXCLUDE_ENTRIES if e not in present]
    if missing:
        sep = "" if not text or text.endswith("\n") else "\n"
        exclude.write_text(text + sep + "\n".join(missing) + "\n", encoding="utf-8")


def copy_sources(src: Path, claude: Path) -> int:
    """Copie chaque dossier source présent ; renvoie le nombre de fichiers copiés."""
    count = 0
    for name in SOURCE_DIRS:
        if not (src / name).is_dir():
            continue
        for f in sorted((src / name).rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            dest = claude / name / f.relative_to(src / name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            count += 1
    return count


def apply_overlay(overlay: Path, claude_md: Path) -> None:
    block = f"{OVERLAY_BEGIN}\n{overlay.read_text(encoding='utf-8').rstrip()}\n{OVERLAY_END}\n"
    text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    begin, end = text.find(OVERLAY_BEGIN), text.find(OVERLAY_END)
    if begin != -1 and end > begin:
        tail = text[end + len(OVERLAY_END) :].lstrip("\n")
        text = text[:begin] + block + tail
    else:
        text = text + ("\n" if text and not text.endswith("\n") else "") + block
    claude_md.write_text(text, encoding="utf-8")


def install(src: Path, target: Path, overlay: Path | None = None) -> int:
    """Installe `src` dans `target` ; renvoie le nombre de fichiers copiés."""
    if overlay is not None and not overlay.is_file():
        raise InstallError(f"surcouche introuvable : {overlay}")
    exclude = check_target(target)
    ensure_excluded(exclude)  # d'abord : aucun fichier Claude ne peut être vu par git
    count = copy_sources(src, target / ".claude")
    if overlay is not None:
        apply_overlay(overlay, target / "CLAUDE.md")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("project", type=Path, help="racine du dépôt git cible")
    parser.add_argument("--overlay", type=Path, help="fichier propre au projet, ajouté à CLAUDE.md")
    args = parser.parse_args(argv)
    try:
        count = install(FACTORY_ROOT, args.project, args.overlay)
    except InstallError as exc:
        print(f"install-agents : {exc}", file=sys.stderr)
        return 1
    print(f"install-agents : {count} fichiers dans {args.project}/.claude/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

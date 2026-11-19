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
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

SOURCE_DIRS = ("agents", "skills", "hooks", "rules")
EXCLUDE_ENTRIES = (".claude/", "CLAUDE.md")
MANIFEST = ".agent-factory-manifest"
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
    except OSError as exc:
        raise InstallError("git est introuvable (à installer ou à mettre dans le PATH)") from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"{target} n'est pas un dépôt git") from exc
    return proc.stdout.strip()


def check_target(target: Path) -> Path:
    """Renvoie le chemin de `info/exclude` ; refuse tout ce qui n'est pas sûr."""
    if not target.is_dir():
        raise InstallError(f"cible introuvable : {target}")
    top = Path(_git(target, "rev-parse", "--show-toplevel"))
    if top.resolve() != target.resolve():
        raise InstallError(f"{target} n'est pas la racine d'un dépôt git ({top})")
    tracked = _git(target, "ls-files", "--", ".claude", "CLAUDE.md")
    if tracked:
        raise InstallError(
            f"fichiers Claude suivis par git, à retirer de l'index d'abord :\n{tracked}"
        )
    for name in (".claude", "CLAUDE.md"):
        if (target / name).is_symlink():
            raise InstallError(f"{target / name} est un lien symbolique")
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


def source_files(src: Path) -> list[str]:
    """Chemins relatifs (posix, triés) des fichiers à installer ; liens symboliques ignorés."""
    found: list[str] = []
    for name in SOURCE_DIRS:
        root = src / name
        if root.is_symlink() or not root.is_dir():
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not Path(base, d).is_symlink()]
            found += [
                Path(base, f).relative_to(src).as_posix()
                for f in files
                if not Path(base, f).is_symlink()
            ]
    return sorted(found)


def safe_dest(claude: Path, rel: str) -> Path:
    """Destination sous `claude`, sans lien symbolique (fichier ou dossier parent)."""
    dest = claude / rel
    probe = dest
    while probe != claude:
        if probe.is_symlink():
            raise InstallError(f"{probe} est un lien symbolique")
        probe = probe.parent
    if not dest.resolve().is_relative_to(claude.resolve()):
        raise InstallError(f"{dest} sort de {claude}")
    return dest


def copy_sources(src: Path, claude: Path, files: list[str]) -> None:
    dests = {rel: safe_dest(claude, rel) for rel in files}  # tout valider avant d'écrire
    for rel, dest in dests.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dest)


def _listed(manifest: Path) -> list[str]:
    """Entrées du manifeste précédent, limitées à `<dossier source>/…` sans `..`."""
    if not manifest.is_file():
        return []
    entries = manifest.read_text(encoding="utf-8").splitlines()
    return [e for e in entries if _in_scope(e)]


def _in_scope(entry: str) -> bool:
    parts = PurePosixPath(entry).parts
    return len(parts) >= 2 and parts[0] in SOURCE_DIRS and ".." not in parts and entry[0] != "/"


def prune(claude: Path, previous: list[str], current: list[str]) -> None:
    """Retire les fichiers installés avant et absents maintenant (jamais les fichiers du projet)."""
    for rel in sorted(set(previous) - set(current)):
        try:
            dest = safe_dest(claude, rel)
        except InstallError:
            continue
        if not dest.is_file():
            continue
        dest.unlink()
        parent = dest.parent
        while parent != claude and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def render_overlay(overlay: Path, claude_md: Path) -> str:
    body = overlay.read_text(encoding="utf-8").rstrip()
    if OVERLAY_BEGIN in body or OVERLAY_END in body:
        raise InstallError(f"la surcouche {overlay} contient les marqueurs réservés")
    block = f"{OVERLAY_BEGIN}\n{body}\n{OVERLAY_END}\n"
    text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    begin = text.find(OVERLAY_BEGIN)
    if begin == -1:
        return text + ("\n" if text and not text.endswith("\n") else "") + block
    end = text.find(OVERLAY_END, begin)
    if end == -1:
        raise InstallError(f"{claude_md} : bloc de surcouche sans marqueur de fin, à réparer")
    return text[:begin] + block + text[end + len(OVERLAY_END) :].lstrip("\n")


def install(src: Path, target: Path, overlay: Path | None = None) -> int:
    """Installe `src` dans `target` ; renvoie le nombre de fichiers copiés."""
    if overlay is not None and not overlay.is_file():
        raise InstallError(f"surcouche introuvable : {overlay}")
    exclude = check_target(target)
    claude = target / ".claude"
    files = source_files(src)
    claude_md = render_overlay(overlay, target / "CLAUDE.md") if overlay else None
    ensure_excluded(exclude)  # avant toute écriture : aucun fichier Claude visible par git
    previous = _listed(claude / MANIFEST)
    copy_sources(src, claude, files)
    prune(claude, previous, files)
    if files:
        (claude / MANIFEST).write_text("\n".join(files) + "\n", encoding="utf-8")
    if claude_md is not None:
        (target / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    return len(files)


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

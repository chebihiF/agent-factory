#!/usr/bin/env python3
"""Planification launchd du plafond de tokens (#210) : `ceiling.sh install` / `uninstall`.

Le plist rendu ne contient aucun jeton : `ceiling.sh` lit celui du bot à chaque exécution.
Contrat : docs/agents.md. Stdlib ; Python 3.9 (python3 du Mac).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

import agent_loop as al

import token_report as tr

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "bin" / "ceiling.sh"
TEMPLATE = HERE / "launchd" / "ceiling.plist.template"
DEFAULT_INTERVAL_MIN = 15
MAX_INTERVAL_MIN = 1440


def label() -> str:
    return al.label().replace(".loop.", ".ceiling.", 1)


def log_dir() -> Path:
    return Path(os.environ.get("VVA_CEILING_DIR") or Path.home() / ".vva" / "ceiling")


def render_plist(
    repo: str, db: Path, journal: Path, interval_min: int, path_env: str, log: Path
) -> str:
    values = {
        "@LABEL@": label(),
        "@SCRIPT@": str(SCRIPT),
        "@DB@": str(db),
        "@JOURNAL@": str(journal),
        "@REPO@": repo,
        "@ROOT@": str(al.ROOT),
        "@PATH@": path_env,
        "@INTERVAL@": str(interval_min * 60),
        "@LOG@": str(log),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in values.items():
        text = text.replace(placeholder, escape(value))
    return text


def _plist_path() -> Path:
    agents = os.environ.get("VVA_LAUNCH_AGENTS") or Path.home() / "Library" / "LaunchAgents"
    return Path(agents) / f"{label()}.plist"


def install(repo: str, db: Path, journal: Path, interval_min: int, launchctl: al.Launchctl) -> None:
    if not tr.REPO.match(repo):
        raise SystemExit(f"ceiling : dépôt invalide : {repo!r}")
    if not 1 <= interval_min <= MAX_INTERVAL_MIN:
        raise SystemExit(f"ceiling : intervalle hors de 1..{MAX_INTERVAL_MIN} minutes")
    if not db.is_file():
        raise SystemExit(f"ceiling : base introuvable : {db}")
    directory = log_dir()
    al.private_dir(directory)
    path_env = al.absolute_path(os.environ.get("PATH", ""))
    plist = render_plist(
        repo, db.resolve(), journal.resolve(), interval_min, path_env, directory / "ceiling.log"
    )
    target = _plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plist, encoding="utf-8")
    domain = f"gui/{os.getuid()}"
    launchctl(["bootout", f"{domain}/{label()}"])  # absent : sans effet
    if launchctl(["bootstrap", domain, str(target)]) != 0:
        raise SystemExit("ceiling : launchctl bootstrap a échoué")
    print(f"plafond planifié : {label()}, toutes les {interval_min} min")


def uninstall(launchctl: al.Launchctl) -> None:
    launchctl(["bootout", f"gui/{os.getuid()}/{label()}"])
    _plist_path().unlink(missing_ok=True)
    print(f"plafond déplanifié : {label()}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceiling", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("install", help="planifier (launchd)")
    cmd.add_argument("--repo", required=True, help="OWNER/REPO des tickets suivis")
    default_db = Path(os.environ.get("VVA_DB_PATH") or "vva.db")
    cmd.add_argument("--db", type=Path, default=default_db, help="défaut : VVA_DB_PATH, vva.db")
    cmd.add_argument("--journal", type=Path, default=tr._env_path("VVA_AGENTS_JOURNAL"))
    cmd.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN, help="minutes")
    sub.add_parser("uninstall", help="retirer la planification")
    return parser


def main(argv: Sequence[str] | None = None, launchctl: al.Launchctl = al.launchctl_cli) -> int:
    args = _parser().parse_args(argv)
    if args.command == "install":
        install(args.repo, args.db, args.journal, args.interval, launchctl)
    else:
        uninstall(launchctl)
    return 0


if __name__ == "__main__":
    sys.exit(main())

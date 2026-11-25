#!/usr/bin/env python3
"""Plafond de tokens par ticket : alerte au dépassement (#188). Voir rules/token-budget.md.

Lit les totaux par ticket de la télémétrie (même source que token_report.py, base SQLite en
lecture seule) et le label `complex` du ticket via `gh` ; pose un commentaire d'alerte, une
seule fois par ticket et par plafond. Alerte seulement : rien n'est bloqué. Compteurs seuls :
ni prompt, ni identifiant de session. Stdlib ; Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

import token_report as tr

SIMPLE_CEILING = 150_000
COMPLEX_CEILING = 300_000
LABEL_COMPLEX = "complex"
GH_TIMEOUT_S = 30
NONE_LINE = "aucun dépassement de plafond"

# Alias évalués à l'exécution : `X | None` plante en 3.9, d'où Optional.
Gh = Callable[[Sequence[str], Optional[str]], str]  # noqa: UP007, UP045


class CeilingError(Exception):
    """Échec attendu (gh KO, réponse illisible) : une ligne pour le parent."""


def _spaced(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def marker(ceiling: int) -> str:
    return f"<!-- plafond-tokens:{ceiling} -->"


def ceiling_for(labels: Sequence[str]) -> int:
    return COMPLEX_CEILING if LABEL_COMPLEX in labels else SIMPLE_CEILING


def alert_text(total: int, ceiling: int) -> str:
    kind = "ticket complex" if ceiling == COMPLEX_CEILING else "ticket simple"
    return (
        f"{marker(ceiling)}\n"
        f"Plafond de tokens dépassé : {_spaced(total)} tokens mesurés pour un plafond de "
        f"{_spaced(ceiling)} ({kind}). Alerte seulement, rien n'est bloqué : écrire la "
        "passation (200 mots au plus) dans ce ticket et resserrer le périmètre "
        "(rules/token-budget.md)."
    )


def ticket_totals(db: Path, journal: Path) -> dict[int, int]:
    """Total de tokens par numéro de ticket (tous agents) ; les tokens sans ticket sont ignorés."""
    totals = tr.aggregate(tr._query(db, tr.USAGE_SQL), tr.Tickets(tr.read_records(journal)))
    return {t: sum(c.values()) for t, c in totals.tickets.items() if t is not None}


def _issue(number: int, repo: str, gh: Gh) -> tuple[list[str], list[str]]:
    raw = gh(["issue", "view", str(number), "-R", repo, "--json", "labels,comments"], None)
    try:
        data: Any = json.loads(raw)
        labels = [str(x["name"]) for x in data["labels"]]
        bodies = [str(x["body"]) for x in data["comments"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise CeilingError("réponse gh illisible") from exc
    return labels, bodies


def _check_one(number: int, total: int, repo: str, gh: Gh, dry_run: bool) -> str | None:
    labels, bodies = _issue(number, repo, gh)
    ceiling = ceiling_for(labels)
    if total <= ceiling:
        return None
    head = f"#{number} {_spaced(total)} > plafond {_spaced(ceiling)}"
    if any(marker(ceiling) in body for body in bodies):
        return f"{head} : déjà signalé"
    if dry_run:
        return f"{head} : alerte à poster (dry-run)"
    comment = ["issue", "comment", str(number), "-R", repo, "--body-file", "-"]
    try:
        gh(comment, alert_text(total, ceiling))
    except CeilingError as exc:
        return f"{head} : alerte NON postée, gh issue comment a échoué ({exc})"
    return f"{head} : alerte postée"


def check(totals: Mapping[int, int], gh: Gh, repo: str, dry_run: bool = False) -> list[str]:
    """Une ligne par ticket au-dessus d'un plafond ; gh n'est appelé qu'au-dessus du plus bas."""
    lines: list[str] = []
    for number, total in sorted(totals.items()):
        if total <= SIMPLE_CEILING:
            continue
        try:
            line = _check_one(number, total, repo, gh, dry_run)
        except CeilingError as exc:
            line = f"#{number} {_spaced(total)} : ticket illisible ({exc})"
        if line:
            lines.append(line)
    return lines or [NONE_LINE]


def gh_cli(args: Sequence[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - gh du poste, arguments construits ici
            ["gh", *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CeilingError(f"gh a échoué : {type(exc).__name__}") from exc
    return done.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceiling", description=__doc__)
    default_db = Path(os.environ.get("VVA_DB_PATH") or "vva.db")
    parser.add_argument("--db", type=Path, default=default_db, help="défaut : VVA_DB_PATH, vva.db")
    parser.add_argument("--repo", required=True, help="OWNER/REPO des tickets suivis")
    parser.add_argument(
        "--journal",
        type=Path,
        default=tr._env_path("VVA_AGENTS_JOURNAL"),
        help="défaut : VVA_AGENTS_JOURNAL, ~/.vva/agents-journal.jsonl",
    )
    parser.add_argument("--dry-run", action="store_true", help="ne poste rien")
    return parser


def main(argv: list[str] | None = None, gh: Gh = gh_cli) -> int:
    args = _parser().parse_args(argv)
    if not tr.REPO.match(args.repo):
        raise SystemExit(f"ceiling : dépôt invalide : {args.repo!r}")
    totals = ticket_totals(args.db, args.journal)
    for line in check(totals, gh, args.repo, args.dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

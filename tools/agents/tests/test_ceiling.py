"""Plafonds de tokens par ticket et seuils de relève (#188) : alerte, règle et contrat."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import ceiling as ce
import handoff as ho
import pytest

import token_report as tr
from dashboard.db import init_schema

ROOT = Path(__file__).resolve().parents[3]
RULE = ROOT / "rules" / "token-budget.md"
CONTRACT = ROOT / "contracts" / "token-ceiling.md"
SESSION_RULE = ROOT / "rules" / "session-agents.md"
REPO = "owner/repo"


def _db(path: Path, points: Sequence[tuple[str, int]]) -> Path:
    conn = sqlite3.connect(path)
    init_schema(conn)
    with conn:
        conn.executemany(
            "INSERT INTO token_usage (point_id, ts, session_id, agent_id, model, type, count)"
            " VALUES (?, '2026-11-18T10:00:00.000001+00:00', ?, 'dev-backend',"
            " 'claude-sonnet-5-5', 'output', ?)",
            [(f"p{i}", s, n) for i, (s, n) in enumerate(points)],
        )
    conn.close()
    return path


def _journal(path: Path, links: dict[str, int]) -> Path:
    for session, ticket in links.items():
        tr.append_record(path, tr.link_record(session, ticket, "2026-11-18T09:00:00+00:00"))
    return path


class FakeGh:
    """gh injecté : issues {n: (labels, commentaires)} ; trace des commentaires postés."""

    def __init__(self, issues: dict[int, tuple[list[str], list[str]]]) -> None:
        self.issues = issues
        self.posted: list[tuple[int, str]] = []
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], stdin: str | None = None) -> str:
        self.calls.append(list(args))
        number = int(args[2])
        if args[:2] == ["issue", "view"]:
            if number not in self.issues:
                raise ce.CeilingError("introuvable")
            labels, comments = self.issues[number]
            return json.dumps(
                {
                    "labels": [{"name": n} for n in labels],
                    "comments": [{"body": b} for b in comments],
                }
            )
        assert args[:2] == ["issue", "comment"] and stdin is not None
        self.posted.append((number, stdin))
        return ""


def _totals(
    tmp_path: Path, points: Sequence[tuple[str, int]], links: dict[str, int]
) -> dict[int, int]:
    return ce.ticket_totals(_db(tmp_path / "v.db", points), _journal(tmp_path / "j.jsonl", links))


# Scénario : alerte au dépassement du plafond


def test_ticket_simple_au_dessus_de_150k_recoit_une_alerte(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 100_000), ("s1", 60_000)], {"s1": 42})
    gh = FakeGh({42: ([], [])})
    lines = ce.check(totals, gh, REPO)
    assert len(gh.posted) == 1
    number, body = gh.posted[0]
    assert number == 42
    assert "160 000" in body and "150 000" in body
    assert body.startswith(ce.marker(150_000))
    assert any("#42" in line and "alerte" in line for line in lines)


def test_plafond_300k_si_label_complex(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 200_000), ("s2", 310_000)], {"s1": 1, "s2": 2})
    gh = FakeGh({1: (["complex"], []), 2: (["complex", "P1"], [])})
    ce.check(totals, gh, REPO)
    assert [n for n, _ in gh.posted] == [2]  # #1 : 200 k <= 300 k, pas d'alerte
    assert "310 000" in gh.posted[0][1] and "300 000" in gh.posted[0][1]


def test_plafond_exact_n_alerte_pas(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 150_000)], {"s1": 5})
    gh = FakeGh({5: ([], [])})
    assert ce.check(totals, gh, REPO) == ["aucun dépassement de plafond"]
    assert gh.posted == []


def test_sous_le_plafond_aucun_appel_gh(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 10)], {"s1": 5})
    gh = FakeGh({})
    ce.check(totals, gh, REPO)
    assert gh.calls == []


def test_alerte_posee_une_seule_fois(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 170_000)], {"s1": 42})
    gh = FakeGh({42: ([], [f"{ce.marker(150_000)}\nancienne alerte"])})
    lines = ce.check(totals, gh, REPO)
    assert gh.posted == []
    assert any("déjà signalé" in line for line in lines)


def test_ticket_introuvable_ne_bloque_pas_les_autres(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("s1", 200_000), ("s2", 200_000)], {"s1": 7, "s2": 8})
    gh = FakeGh({8: ([], [])})
    lines = ce.check(totals, gh, REPO)
    assert [n for n, _ in gh.posted] == [8]
    assert any("#7" in line and "illisible" in line for line in lines)


def test_tokens_sans_ticket_ignores(tmp_path: Path) -> None:
    assert _totals(tmp_path, [("libre", 900_000)], {}) == {}


def test_alerte_sans_donnee_sensible(tmp_path: Path) -> None:
    totals = _totals(tmp_path, [("session-secrete", 200_000)], {"session-secrete": 3})
    gh = FakeGh({3: ([], [])})
    ce.check(totals, gh, REPO)
    assert "session-secrete" not in gh.posted[0][1]
    assert len(gh.posted[0][1].split()) <= 60


# Ligne de commande


def test_cli_dry_run_ne_poste_rien(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = _db(tmp_path / "v.db", [("s1", 200_000)])
    journal = _journal(tmp_path / "j.jsonl", {"s1": 9})
    argv = ["--db", str(db), "--journal", str(journal), "--repo", REPO, "--dry-run"]
    gh = FakeGh({9: ([], [])})
    assert ce.main(argv, gh) == 0
    assert gh.posted == []
    out = capsys.readouterr().out
    assert "#9" in out and "200 000" in out and "dry-run" in out


def test_cli_depot_invalide(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ce.main(["--db", str(tmp_path / "v.db"), "--repo", "pas un depot; rm"])


def test_cli_base_absente(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ce.main(["--db", str(tmp_path / "absente.db"), "--repo", REPO])


def test_echec_du_commentaire_message_distinct(tmp_path: Path) -> None:
    class CommentFails(FakeGh):
        def __call__(self, args: Sequence[str], stdin: str | None = None) -> str:
            if args[:2] == ["issue", "comment"]:
                raise ce.CeilingError("gh a échoué : CalledProcessError")
            return super().__call__(args, stdin)

    totals = _totals(tmp_path, [("s1", 200_000)], {"s1": 4})
    lines = ce.check(totals, CommentFails({4: ([], [])}), REPO)
    assert any("alerte NON postée" in line and "gh issue comment" in line for line in lines)
    assert not any("illisible" in line for line in lines)


def test_cli_defauts_comme_token_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _db(tmp_path / "v.db", [("s1", 200_000)])
    journal = _journal(tmp_path / "j.jsonl", {"s1": 6})
    monkeypatch.setenv("VVA_DB_PATH", str(db))
    monkeypatch.setenv("VVA_AGENTS_JOURNAL", str(journal))
    assert ce.main(["--repo", REPO, "--dry-run"], FakeGh({6: ([], [])})) == 0
    assert "#6" in capsys.readouterr().out


# Règle et contrat versionnés


def _words(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_regle_chiffres_alignes_sur_le_code() -> None:
    text = _words(RULE)
    assert len(RULE.read_text(encoding="utf-8").splitlines()) <= 40
    assert "150 000" in text and "300 000" in text
    assert ce.SIMPLE_CEILING == 150_000 and ce.COMPLEX_CEILING == 300_000
    assert "3 relectures" in text and "3 fichiers" in text
    assert "60 %" in text and "200 mots" in text


def test_seuils_de_releve() -> None:
    text = _words(RULE)
    assert f"{ho.THRESHOLD:,}".replace(",", " ") in text  # 150 000 : prochaine frontière de phase
    assert "prochaine frontière de phase" in text
    assert "250 000" in text
    assert "immédiate" in text and f"#{ho.ISSUE}" in text


def test_regle_alerte_sans_blocage_et_hooks_locaux() -> None:
    text = _words(RULE)
    assert "alerte seulement" in text
    assert "ceiling.sh" in text
    assert "`.claude/hooks`" in text and "alignés à la main" in text


def test_session_agents_renvoie_a_la_regle() -> None:
    assert "rules/token-budget.md" in SESSION_RULE.read_text(encoding="utf-8")


def test_contrat_documente_entree_sortie() -> None:
    text = _words(CONTRACT)
    for needle in (ce.marker(ce.SIMPLE_CEILING), ce.marker(ce.COMPLEX_CEILING), "`complex`"):
        assert needle in text
    assert "token_usage" in text and "aucune donnée d'enfant" in text
    assert "GH_TOKEN=$(gh auth token -u infinitySoftMA)" in text
    assert "journal est propre au dépôt" in text
    assert "token-ceiling.md" in (ROOT / "contracts" / "README.md").read_text(encoding="utf-8")

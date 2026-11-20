"""Règle de session des agents (#178) : contenu, bornes et référence par le tech lead."""

from __future__ import annotations

import json
from pathlib import Path

import handoff as ho
import pytest

ROOT = Path(__file__).resolve().parents[3]
RULE = ROOT / "rules" / "session-agents.md"
TECH_LEAD = ROOT / "rules" / "tech-lead.md"


def _text() -> str:
    return RULE.read_text(encoding="utf-8")


def test_regle_courte() -> None:
    assert RULE.is_file()
    assert len(_text().splitlines()) <= 40


def test_enchainement_et_blocage() -> None:
    text = _text()
    assert "sans rendre la main" in text
    assert "tout est livré" in text
    assert "Attend l'utilisateur" in text
    assert "`blocked`" in text


def test_passation_alignee_sur_83() -> None:
    text = _text()
    assert f"{ho.THRESHOLD}" in text
    assert f"{ho.MAX_NOTE_LINES} lignes maximum" in text
    assert f"#{ho.ISSUE}" in text
    for command in ("check", "post"):  # sous-commandes citées : elles existent
        assert ho._parser().parse_args([command, *_FILE[command]]).command == command


_FILE = {"check": [], "post": ["--note-file", "n.md"], "relay": ["--note-file", "n.md"]}


def _check(tmp_path: Path, capsys: pytest.CaptureFixture[str], tokens: int, *flags: str) -> str:
    transcript = tmp_path / "t.jsonl"
    usage = {"input_tokens": tokens}
    transcript.write_text(json.dumps({"message": {"usage": usage}}) + "\n", encoding="utf-8")
    session = f"s{tokens}{len(flags)}"
    hook = json.dumps({"session_id": session, "transcript_path": str(transcript)})
    assert ho.main(["check", *flags], stdin=hook) == 0
    return capsys.readouterr().out


def test_seuil_reel_defaut_et_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VVA_HANDOFF_DIR", str(tmp_path / "state"))
    assert "`--threshold <tokens>`" in _text()
    assert _check(tmp_path, capsys, ho.THRESHOLD - 1) == ""  # défaut de la règle : 150000
    assert "additionalContext" in _check(tmp_path, capsys, ho.THRESHOLD)
    assert _check(tmp_path, capsys, 1_000) == ""
    assert "additionalContext" in _check(tmp_path, capsys, 1_000, "--threshold", "500")


def test_releve_independante_sans_chaine() -> None:
    text = _text()
    assert "session indépendante, lancée par le parent humain" in text
    assert "`start_session`\n  puis archivage immédiat de la sortante" in text
    assert "Pas de chaîne parent/enfant\n  de sessions" in text
    assert "Un seul tech lead par brique" in text
    assert "sous-agents (outil Agent) restent autorisés" in text
    assert "relay" not in text.replace("pas de `handoff.sh relay`", "")
    assert "pas dans des messages entre sessions" in text


def test_archivage_sur_accord_explicite() -> None:
    text = _text()
    assert "sans être archivée" in text
    assert "accord explicite du parent" in text


def test_tech_lead_reference_la_regle() -> None:
    assert "rules/session-agents.md" in TECH_LEAD.read_text(encoding="utf-8")

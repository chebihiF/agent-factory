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


_FILE = {"check": [], "post": ["--note-file", "n.md"]}


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
    text = " ".join(_text().split())
    assert "session indépendante, lancée par le parent humain" in text
    assert "`start_session` puis archivage immédiat de la sortante" in text
    assert "Pas de chaîne parent/enfant de sessions (chaque rapport" in text
    assert "Un seul tech lead par brique" in text
    assert "sous-agents (outil Agent) restent autorisés" in text
    assert "relay" not in text.replace("pas de `handoff.sh relay`", "")
    assert "pas dans des messages entre sessions" in text


def test_archivage_sur_accord_explicite() -> None:
    text = " ".join(_text().split())
    assert "demander explicitement au parent l'accord d'archiver" in text
    assert "elle reste ouverte, inactive, sans lancer ni attendre de session" in text
    assert "accord explicite" in text


def test_texte_du_hook_ne_pousse_pas_a_relay() -> None:
    text = ho.request_text(ho.THRESHOLD, ho.THRESHOLD)
    assert "relay" not in text
    assert "handoff.sh post" in text and "session indépendante" in text


def test_tech_lead_reference_la_regle() -> None:
    assert "rules/session-agents.md" in TECH_LEAD.read_text(encoding="utf-8")

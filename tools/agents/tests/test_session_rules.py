"""Règle de session des agents (#178) : contenu, bornes et référence par le tech lead."""

from __future__ import annotations

from pathlib import Path

import handoff as ho

ROOT = Path(__file__).resolve().parents[3]
RULE = ROOT / "rules" / "session-agents.md"
TECH_LEAD = ROOT / "rules" / "tech-lead.md"


def _text() -> str:
    return RULE.read_text(encoding="utf-8")


def test_regle_courte() -> None:
    assert RULE.is_file()
    assert len(_text().splitlines()) <= 60


def test_enchainement_et_blocage() -> None:
    text = _text()
    assert "sans rendre la main" in text
    assert "tout est livré" in text
    assert "Attend l'utilisateur" in text
    assert "`blocked`" in text


def test_passation_alignee_sur_83() -> None:
    text = _text()
    assert f"{ho.THRESHOLD}" in text
    assert "--threshold" in text
    assert f"{ho.MAX_NOTE_LINES} lignes maximum" in text
    assert f"#{ho.ISSUE}" in text
    assert "handoff.sh" in text


def test_archivage_sur_accord_explicite() -> None:
    text = _text()
    assert "sans être archivée" in text
    assert "accord explicite du parent" in text


def test_tech_lead_reference_la_regle() -> None:
    assert "rules/session-agents.md" in TECH_LEAD.read_text(encoding="utf-8")

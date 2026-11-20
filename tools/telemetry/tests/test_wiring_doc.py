"""Procédure de câblage de l'ingestion (#180) : documentée, sans valeur secrète."""

from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "telemetry-cablage.md"


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_doc_names_the_wiring_pieces() -> None:
    text = doc_text()
    for name in (
        "hook_emit.py",
        "ship.py",
        "--background",
        "VVA_INGEST_URL",
        "VVA_INGEST_TOKEN",
        "VVA_PROJECT",
        "events.legacy-",
        ".rejected",
    ):
        assert name in text


def test_doc_replaces_the_old_hook() -> None:
    assert "log_event.sh" in doc_text()  # mentionné comme l'ancien hook à remplacer


def test_doc_holds_no_secret_value() -> None:
    text = doc_text()
    assert not re.search(r"Bearer\s+[A-Za-z0-9_\-]{16,}", text)
    assert not re.search(r"VVA_INGEST_TOKEN\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}", text)
    assert not re.search(r"https?://(?!localhost|127\.0\.0\.1)[\w.-]*\d{1,3}\.\d{1,3}", text)

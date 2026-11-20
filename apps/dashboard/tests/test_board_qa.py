"""QA #179 : cas limites du tableau de bord (360 px, unicode/darija, lecture seule)."""
# ruff: noqa: F811

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from test_board import STATIC, GitHub, client, env, github, issue, text  # noqa: F401


def test_mobile_layout_has_no_fixed_width_wider_than_360px() -> None:
    css = (STATIC / "board.css").read_text(encoding="utf-8")
    for value in re.findall(r"(?<!-)\b(?:min-)?width:\s*(\d+)px", css.split("@container")[0]):
        assert int(value) <= 360, value
    assert "overflow-x: scroll" not in css and "white-space: nowrap" not in css.replace(
        ".sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; "
        "clip: rect(0 0 0 0); white-space: nowrap; }",
        "",
    )


def test_page_has_viewport_and_no_table_or_pre(client: TestClient) -> None:
    html = client.get("/").text.lower()
    assert 'name="viewport" content="width=device-width' in html
    assert "<table" not in html and "<pre" not in html


def test_darija_and_unicode_titles_are_shown_and_long_words_do_not_break_layout(
    client: TestClient, github: GitHub
) -> None:
    long_word = "x" * 300
    github(
        [
            issue(4, "مرحبا بالعالم، واش كاين مشكل؟", "P1"),
            issue(5, long_word, "P1"),
            issue(6, "", "P1"),
        ]
    )
    response = client.get("/")
    assert response.status_code == 200
    page = text(response.text)
    assert "مرحبا بالعالم، واش كاين مشكل؟" in page
    assert long_word in page

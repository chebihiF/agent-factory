"""QA #182 : cas limites de la liste des tickets (lecture seule, unicode, sans jeton, statut)."""
# ruff: noqa: F811

from __future__ import annotations

import re
from urllib.request import Request

from fastapi.testclient import TestClient
from test_board import STATIC, GitHub, client, env, github, issue, text  # noqa: F401

from dashboard import github as github_api


def _section(html: str) -> str:
    return html.split('id="tickets"')[1].split("</section>")[0]


def test_darija_titles_and_epics_are_listed_and_markup_is_escaped(
    client: TestClient, github: GitHub
) -> None:
    github(
        [
            issue(2, "الدار البيضاء", "epic"),
            issue(5, "واش كاين مشكل؟", "P1", body="Epic #2"),
            issue(6, "<script>alert(1)</script>", "P1", body="Epic #2"),
        ]
    )
    html = client.get("/").text
    section = _section(html)
    assert "واش كاين مشكل؟" in section and "الدار البيضاء" in section
    assert "<script>alert(1)</script>" not in section
    assert "&lt;script&gt;" in section


def test_sans_epique_is_shown_only_on_the_ticket_without_epic(
    client: TestClient, github: GitHub
) -> None:
    github(
        [
            issue(2, "Épique A", "epic"),
            issue(5, "Avec", "P1", body="Epic #2"),
            issue(6, "Sans", "P1", body="rien"),
        ]
    )
    assert _section(client.get("/").text).count("Sans épique") == 1


def test_status_is_text_with_a_symbol_never_color_alone(client: TestClient, github: GitHub) -> None:
    github([issue(5, "Un", "P1")])
    page = text(_section(client.get("/").text))
    assert "◇ Prêt" in page
    css = (STATIC / "board.css").read_text(encoding="utf-8")
    assert not re.search(r"font-size:\s*(?:[0-9]|1[0-5])px", css)


def test_tickets_are_read_only_no_form_no_write_route(client: TestClient, github: GitHub) -> None:
    github([issue(5, "Un", "P1")])
    section = _section(client.get("/").text).lower()
    assert "<form" not in section and "<button" not in section and "<input" not in section
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/tickets").status_code in (404, 405)


def test_github_is_read_without_token_and_only_with_get(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[Request] = []

    class Resp:
        headers: dict[str, str] = {}

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self, n: int = -1) -> bytes:
            return b"[]"

    def fake(request: Request, timeout: float = 0) -> Resp:
        seen.append(request)
        return Resp()

    monkeypatch.setattr(github_api, "urlopen", fake)
    github_api.HttpJson()._get("https://api.github.com/repos/x/y/issues")
    assert seen, "aucune requête émise"
    for request in seen:
        assert request.get_method() == "GET" and request.data is None
        assert not any(k.lower() == "authorization" for k in request.headers)


def test_closed_not_planned_ticket_is_not_listed(client: TestClient, github: GitHub) -> None:
    github([issue(5, "Abandonné", "P1", state="closed", reason="not_planned")])
    assert "Abandonné" not in _section(client.get("/").text)


def test_double_refresh_is_idempotent(client: TestClient, github: GitHub) -> None:
    github([issue(5, "Un", "P1")])
    first = text(_section(client.get("/").text))
    assert text(_section(client.get("/").text)) == first

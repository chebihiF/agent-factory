"""Tableau de bord unique en lecture seule (#179) : GET /, spec docs/design/dashboard-refonte.md.

GitHub est toujours simulé ; aucune donnée réelle.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from dashboard import auth, board, db, progress
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(board.__file__).parent
TEMPLATE = HERE / "templates" / "board.html"
STATIC = HERE / "static"
CONTRACT = ROOT / "contracts" / "dashboard-routes.md"
REPO = "chebihiF/viral-video-agents"
ISSUES_URL = f"https://github.com/{REPO}/issues"
Fetch = Callable[[str], Any]


def ago_iso(**delta: float) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat()


def issue(number: int, title: str, *labels: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "number": number,
        "title": title,
        "state": extra.pop("state", "open"),
        "state_reason": extra.pop("reason", None),
        "body": extra.pop("body", "Épique : #2"),
        "labels": [{"name": name} for name in labels],
        "updated_at": extra.pop("updated_at", ago_iso(hours=3)),
    }
    item.update(extra)
    return item


def down(path: str) -> Any:
    raise URLError("réseau coupé")


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]] | Fetch], None]:
    def use(issues: list[dict[str, Any]] | Fetch) -> None:
        if callable(issues):
            fetch: Fetch = issues
        else:

            def fetch(path: str) -> Any:
                return issues if "/issues" in path else []

        monkeypatch.setattr(progress, "CACHE", progress.GithubCache(fetch))

    use([])
    return use


GitHub = Callable[[list[dict[str, Any]] | Fetch], None]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, github: GitHub) -> None:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path / "vva.db"))
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    monkeypatch.delenv("VVA_GITHUB_REPO", raising=False)
    auth.LOGIN_LIMITER.reset()


def new_client() -> TestClient:
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def client(env: None) -> TestClient:
    c = new_client()
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


def seed(*events: tuple[Any, ...]) -> None:
    """(ts, hook, agent_type, agent_id[, issue[, project]]) ; session = « sess-<issue> »."""
    rows = []
    for i, event in enumerate(events):
        ts, hook, agent_type, agent_id, *rest = event
        number, project = (*rest, None, None)[:2]
        session = f"sess-{project}-{number}" if project else f"sess-{number}"
        project = project or db.DEFAULT_PROJECT
        rows.append((f"e{i}", ts, hook, session, agent_type, agent_id, None, project, number, None))
    with db.connect() as conn:
        db.insert_events(conn, rows)


def seed_tokens(*points: tuple[str, str | None, int]) -> None:
    """(session_id, agent, count) ; horodatés il y a une heure."""
    when = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="microseconds")
    rows = [
        (f"p{i}", when, session, agent, "claude-sonnet-5-5", "output", count)
        for i, (session, agent, count) in enumerate(points)
    ]
    with db.connect() as conn:
        db.insert_token_usage(conn, rows)


def section(html: str, ident: str) -> str:
    match = re.search(rf'<section id="{ident}"(.*?)<!-- /{ident} -->', html, re.S)
    assert match, f"section {ident} absente"
    return match.group(1)


def column(html: str, label: str) -> str:
    match = re.search(rf'<section aria-label="{label}"(.*?)</section>', html, re.S)
    assert match, label
    return match.group(1)


def text(html: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", html).replace("&#39;", "'").replace("\u202f", " ")
    return re.sub(r"\s+", " ", plain)


# --- Accès ---------------------------------------------------------------------------------
def test_visitor_is_sent_to_login(env: None) -> None:
    resp = new_client().get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_lands_on_the_board(env: None) -> None:
    resp = new_client().post("/login", data={"password": PASSWORD})
    assert resp.headers["location"] == "/"


def test_board_is_served_without_cache(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert "<h1>Tableau de bord des agents</h1>" in resp.text


# --- Qui fait quoi -------------------------------------------------------------------------
def test_active_agent_shows_role_ticket_and_last_sign_of_life(
    client: TestClient, github: GitHub
) -> None:
    github([issue(182, "Vue Tickets du tableau de bord", "P1", "in-progress")])
    seed(
        (ago_iso(minutes=20, seconds=5), "SubagentStart", "dev-backend", "a1", 182),
        (ago_iso(seconds=40), "PostToolUse", "dev-backend", "a1", 182),
    )
    agents = text(section(client.get("/").text, "agents"))
    assert "Développeur" in agents
    assert "● Au travail" in agents
    assert "Vue Tickets du tableau de bord" in agents
    assert "viral-video-agents" in agents
    assert "Depuis 20 min" in agents
    assert "Signe de vie il y a 40 s" in agents
    assert "182" not in agents


def test_stopped_agent_is_at_rest(client: TestClient) -> None:
    seed(
        (ago_iso(minutes=9), "SubagentStart", "reviewer", "r1"),
        (ago_iso(minutes=8), "SubagentStop", "reviewer", "r1"),
    )
    agents = text(section(client.get("/").text, "agents"))
    assert "Relecteur" in agents and "○ Au repos" in agents
    assert "Aucun ticket indiqué" in agents
    assert "Depuis" not in agents  # au repos : seul le signe de vie compte


def test_working_agent_silent_for_more_than_two_minutes_is_flagged(client: TestClient) -> None:
    seed((ago_iso(minutes=3, seconds=5), "PreToolUse", "qa", "q1"))
    html = section(client.get("/").text, "agents")
    assert 'class="late"' in html
    assert "▲ Signe de vie il y a 3 min" in text(html)


def test_agent_on_a_blocked_ticket_is_blocked(client: TestClient, github: GitHub) -> None:
    github([issue(7, "Choisir les seuils", "P1", "blocked")])
    seed((ago_iso(seconds=10), "PreToolUse", "dev-backend", "a1", 7))
    assert "▲ Bloqué" in text(section(client.get("/").text, "agents"))


# --- Avancement ----------------------------------------------------------------------------
def test_each_ticket_is_in_its_status_column_with_a_readable_title(
    client: TestClient, github: GitHub
) -> None:
    github(
        [
            issue(3, "Câblage livré", state="closed", reason="completed"),
            issue(4, "Ticket prêt", "P1"),
            issue(5, "Ticket en revue", "P1"),
            issue(6, "Idée du backlog", "P2"),
            issue(8, "Ticket en cours", "P1", "in-progress"),
            issue(40, "feat: vue", body="Closes #5", pull_request={}),
        ]
    )
    html = section(client.get("/").text, "avancement")
    labels = re.findall(r'<section aria-label="([^"]+)"', html)
    assert labels == ["Backlog", "Prêt", "En cours", "Revue", "Livré"]
    for label, title in (
        ("Backlog", "Idée du backlog"),
        ("Prêt", "Ticket prêt"),
        ("En cours", "Ticket en cours"),
        ("Revue", "Ticket en revue"),
        ("Livré", "Câblage livré"),
    ):
        assert title in column(html, label), label
    assert "#4" not in text(html)


def test_column_shows_three_cards_then_how_many_more(client: TestClient, github: GitHub) -> None:
    github([issue(n, f"Prêt {n}", "P1") for n in range(10, 15)])
    block = column(client.get("/").text, "Prêt")
    assert block.count('class="card') == 3
    assert "2 de plus" in text(block)
    assert "5 tickets" in text(block)


# --- Quotas et tokens ----------------------------------------------------------------------
def test_tokens_by_agent_by_ticket_and_quota_state(client: TestClient, github: GitHub) -> None:
    github([issue(52, "Sous-titres mot par mot", "P1", "in-progress")])
    seed((ago_iso(minutes=1), "PreToolUse", "dev-backend", "a1", 52))
    seed_tokens(("sess-52", "dev-backend", 120_000), ("sess-52", None, 30_000))
    quotas = text(section(client.get("/").text, "quotas"))
    assert "Développeur" in quotas and "120 000" in quotas
    assert "Fil principal" in quotas
    assert "Sous-titres mot par mot" in quotas and "150 000" in quotas
    assert "30 % utilisé" in quotas and "✓ Confortable" in quotas
    assert "500 000" in quotas


@pytest.mark.parametrize(
    ("used", "word"),
    [(0, "✓ Confortable"), (69, "✓ Confortable"), (70, "▲ Attention"), (91, "✖ Presque épuisé")],
)
def test_gauge_level_has_a_shape_and_a_word(used: int, word: str) -> None:
    assert board.gauge(used, 100).word == word


def test_seven_days_histogram_lists_each_day_with_its_value(client: TestClient) -> None:
    seed_tokens(("s", "qa", 1_234))
    days = re.search(r'<ol class="days"(.*?)</ol>', client.get("/").text, re.S)
    assert days
    assert days.group(1).count("<li") == 7
    assert "1 234" in text(days.group(1))


# --- À faire par toi -----------------------------------------------------------------------
def test_waiting_question_is_in_todo_with_the_expected_action(
    client: TestClient, github: GitHub
) -> None:
    body = "Contexte.\nAttend l'utilisateur : valider les seuils 70 % / 90 % ?\nSuite."
    github([issue(179, "Refonte du dashboard", "P0", "blocked", body=body)])
    html = section(client.get("/").text, "a-faire")
    assert "? Attend ta réponse" in text(html)
    assert "Refonte du dashboard" in html
    assert "valider les seuils 70 % / 90 % ?" in text(html)
    assert f'href="{ISSUES_URL}/179" target="_blank" rel="noopener"' in html
    assert "Ouvrir l'issue sur GitHub ↗" in text(html)
    assert "(nouvel onglet)" in html


def test_open_pull_request_is_to_review_and_blocked_ticket_is_a_decision(
    client: TestClient, github: GitHub
) -> None:
    github(
        [
            issue(40, "feat(media): sous-titres", body="Closes #5", pull_request={}),
            issue(7, "Choisir l'hébergeur", "P1", "blocked"),
        ]
    )
    html = section(client.get("/").text, "a-faire")
    assert "↗ À relire" in text(html) and "feat(media): sous-titres" in html
    assert f'href="https://github.com/{REPO}/pull/40"' in html
    assert "Voir la PR sur GitHub ↗" in text(html)
    assert "◆ Décision" in text(html) and "Choisir l'hébergeur" in text(html)
    assert "2 éléments" in text(html)


# --- États vides et ingestion muette -------------------------------------------------------
def test_without_any_event_the_page_explains_why_and_what_to_do(client: TestClient) -> None:
    page = text(client.get("/").text)
    assert "Aucun événement reçu pour l'instant" in page
    assert "Les agents se lancent depuis le terminal du tech lead" in page
    assert "Tu es à jour" in page
    assert "Aucun agent au travail" in page
    assert "Aucun ticket pour l'instant" in page
    assert "Rien consommé aujourd'hui" in page


def test_silent_ingestion_is_announced(client: TestClient) -> None:
    seed((ago_iso(minutes=12, seconds=5), "SubagentStop", "qa", "q1"))
    html = client.get("/").text
    banner = re.search(r'<div role="status" class="banner warn">(.*?)</div>', html, re.S)
    assert banner
    assert "Aucun signe de vie depuis 12 min" in text(banner.group(1))
    assert "▲ Aucun signe de vie depuis 12 min" in text(html)


def test_fresh_events_show_live_status(client: TestClient) -> None:
    seed((ago_iso(seconds=5), "PreToolUse", "qa", "q1"))
    page = text(client.get("/").text)
    assert "● En direct" in page
    assert "Aucun signe de vie depuis" not in page


# --- Erreurs -------------------------------------------------------------------------------
def test_github_error_keeps_page_and_offers_a_get_reload(
    client: TestClient, github: GitHub
) -> None:
    github(down)
    html = client.get("/?projet=agent-factory").text
    alert = re.search(r'<div role="alert"[^>]*>(.*?)</div>', html, re.S)
    assert alert
    assert "GitHub" in text(alert.group(1))
    assert '<a href="/?projet=agent-factory">Recharger les données</a>' in alert.group(1)


def test_unreadable_database_returns_503_with_alert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path))  # un dossier : base illisible
    resp = client.get("/")
    assert resp.status_code == 503
    assert 'role="alert"' in resp.text and "Recharger les données" in resp.text


# --- Filtre par projet ---------------------------------------------------------------------
def test_project_filter_keeps_only_that_project(client: TestClient) -> None:
    seed(
        (ago_iso(seconds=5), "PreToolUse", "qa", "q1", None, "agent-factory"),
        (ago_iso(seconds=5), "PreToolUse", "scout", "s1", None, "viral-video-agents"),
    )
    html = client.get("/?projet=agent-factory").text
    agents = text(section(html, "agents"))
    assert "Testeur" in agents and "Éclaireur" not in agents
    assert 'value="agent-factory" checked' in html


def test_filtered_project_without_tickets_offers_to_show_all(
    client: TestClient, github: GitHub
) -> None:
    github([issue(4, "Ticket prêt", "P1")])
    html = section(client.get("/?projet=agent-factory").text, "avancement")
    assert "Ticket prêt" not in html
    assert "Aucun ticket pour agent-factory" in text(html)
    assert 'href="/#avancement"' in html


def test_unknown_project_falls_back_to_all_with_a_message(client: TestClient) -> None:
    page = text(client.get("/?projet=inconnu").text)
    assert "Projet inconnu : affichage de tous les projets" in page


# --- Lecture seule, accessibilité, aucune ressource externe --------------------------------
def test_board_sends_no_command(client: TestClient, github: GitHub) -> None:
    github(
        [
            issue(40, "PR", body="Closes #5", pull_request={}),
            issue(7, "Bloqué", "blocked", body="Attend l'utilisateur : oui ?"),
        ]
    )
    html = client.get("/").text.lower()
    assert "hx-post" not in html and 'method="post"' not in html
    assert "<button" not in html.replace("<noscript><button", "")
    assert "lecture seule : cette page n'envoie aucune commande" in text(html)


def test_read_only_mark_uses_an_svg_not_an_emoji() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "<svg" in source
    assert not re.search("[\U0001f000-\U0001faff☀-⛿️]", source)


def test_no_external_resource() -> None:
    for path in (TEMPLATE, STATIC / "board.css", STATIC / "board.js"):
        source = path.read_text(encoding="utf-8")
        assert "http://" not in source, path
        for url in re.findall(r"https://[^\s\"'{)]+", source):
            assert url.startswith("https://github.com/"), url


def test_text_is_at_least_16px() -> None:
    tokens_css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    sizes = dict(re.findall(r"--font-size-([a-z]+):\s*(\d+)px", tokens_css))
    assert sizes and all(int(v) >= 16 for v in sizes.values()), sizes
    assert "--target-min: 44px" in tokens_css
    css = (STATIC / "board.css").read_text(encoding="utf-8")
    for value in re.findall(r"font(?:-size)?:[^;]*?(\d+)px", css):
        assert int(value) >= 16, value


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _themes() -> list[dict[str, str]]:
    css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    blocks = re.findall(r"\{([^{}]*--color-bg[^{}]*)\}", css)
    pattern = r"--color-([a-z0-9-]+):\s*(#[0-9a-f]{6})"
    light = dict(re.findall(pattern, blocks[0]))
    return [light, {**light, **dict(re.findall(pattern, blocks[1]))}]


@pytest.mark.parametrize("theme", _themes(), ids=["clair", "sombre"])
def test_text_colors_reach_4_5_to_1(theme: dict[str, str]) -> None:
    for fg in ("text", "text-muted", "accent", "ok", "warn", "danger", "info"):
        for bg in ("bg", "surface", "surface-2"):
            assert _ratio(theme[fg], theme[bg]) >= 4.5, (fg, bg)
    for state in ("ok", "warn", "danger", "info"):
        assert _ratio(theme[state], theme[f"{state}-bg"]) >= 4.5, state
        assert _ratio(theme["text"], theme[f"{state}-bg"]) >= 4.5, state
    assert _ratio(theme["on-accent"], theme["accent"]) >= 4.5


def test_contract_documents_the_board() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    for needle in ("### `GET /`", "`GET /static/board.js`", "`GET /static/board.css`"):
        assert needle in contract, needle


def test_hidden_network_banner_stays_hidden() -> None:
    css = (STATIC / "board.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css
    assert 'id="erreur-reseau" class="banner danger" hidden' in TEMPLATE.read_text(encoding="utf-8")


# --- Revue : même numéro dans deux projets, aucune écriture, échappement --------------------
def test_same_number_in_another_project_never_borrows_the_tracked_title(
    client: TestClient, github: GitHub
) -> None:
    github([issue(182, "Vue Tickets du tableau de bord", "P1", "blocked")])
    seed(
        (ago_iso(seconds=10), "PreToolUse", "dev-backend", "a1", 182, "agent-factory"),
        (ago_iso(seconds=10), "PreToolUse", "qa", "q1", 182),
    )
    seed_tokens(("sess-agent-factory-182", "dev-backend", 70_000), ("sess-182", "qa", 20_000))
    html = client.get("/").text
    agents = text(section(html, "agents"))
    assert "Ticket hors du dépôt suivi · agent-factory" in agents
    assert "Vue Tickets du tableau de bord · viral-video-agents" in agents
    assert agents.count("▲ Bloqué") == 1  # seul le ticket du dépôt suivi est bloqué
    tickets = re.search(r"Tickets les plus gourmands(.*?)</ul>", html, re.S)
    assert tickets
    rows = text(tickets.group(1))
    assert "Ticket hors du dépôt suivi 70 000" in rows
    assert "Vue Tickets du tableau de bord 20 000" in rows


def test_board_writes_nothing_to_the_database(client: TestClient, github: GitHub) -> None:
    github([issue(4, "Ticket prêt", "P1")])
    seed((ago_iso(seconds=10), "PreToolUse", "qa", "q1", 4))
    seed_tokens(("sess-4", "qa", 600_000))  # plafond dépassé : /controls mettrait en pause
    watcher = sqlite3.connect(db.db_path())
    try:
        before = watcher.execute("PRAGMA data_version").fetchone()[0]
        changes = watcher.total_changes
        assert client.get("/").status_code == 200
        assert client.get("/?projet=agent-factory").status_code == 200
        assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
        assert watcher.total_changes == changes
        assert watcher.execute("SELECT paused FROM controls").fetchone()[0] == 0
    finally:
        watcher.close()


XSS = ("<script>alert(1)</script>", '"><img src=x onerror=alert(1)>')


def test_github_texts_are_escaped(client: TestClient, github: GitHub) -> None:
    github(
        [
            issue(5, XSS[0], "P1", "blocked", body=f"Attend l'utilisateur : {XSS[1]} {XSS[0]}"),
            issue(40, XSS[1], body="Closes #5", pull_request={}),
            issue(6, XSS[1], "P1", "in-progress"),
        ]
    )
    seed((ago_iso(seconds=10), "PreToolUse", "qa", "q1", 5))
    html = client.get("/").text
    for raw in (*XSS, "<img", "<script>alert"):
        assert raw not in html, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&#34;&gt;&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize(
    ("body", "question"),
    [
        ("Contexte\nAttend l'utilisateur : choisir A ou B ?", "choisir A ou B ?"),
        ("- Attend l’utilisateur : valider ?\nsuite", "valider ?"),
        ("Rien ici, sauf : Attend l'utilisateur : en milieu de ligne", None),
        ("> Attend l'utilisateur : citation", None),
        ("```\nAttend l'utilisateur : dans du code\n```", None),
        ("~~~\nAttend l'utilisateur : dans du code\n~~~", None),
    ],
)
def test_question_is_a_line_of_its_own_outside_quotes_and_code(
    body: str, question: str | None
) -> None:
    issues, _ = progress.parse_issues([issue(1, "T", body=body)])
    assert issues[0].question == question

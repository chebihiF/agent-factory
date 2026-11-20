"""GET / : tableau de bord unique, en lecture seule (#179, spec docs/design/dashboard-refonte.md).

Assemble ce qui est déjà collecté : tickets et PR (cache GitHub de `progress`), événements
d'agents (`events`), tokens (`token_usage`) et plafond (`controls`). Aucune écriture : le plafond
est lu tel quel (jamais `controls.current_state`, qui peut mettre en pause).
Contrat : contracts/dashboard-routes.md.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dashboard import activity, auth, controls, db, progress, tokens

SILENCE_AFTER = timedelta(minutes=5)  # ingestion muette au-delà (spec, à confirmer)
LATE_AFTER = timedelta(minutes=2)  # signe de vie en retard pour un agent au travail
CARDS_PER_COLUMN = 3
TOP_TICKETS = 5
DAYS = 7
WARN_PERCENT, DANGER_PERCENT = 70, 90
MAIN_THREAD = "Fil principal"
ROLES = {
    "dev-backend": "Développeur",
    "ai-engineer": "Ingénieur IA",
    "comfyui-engineer": "Ingénieur ComfyUI",
    "devops": "Opérations",
    "qa": "Testeur",
    "product-owner": "Responsable produit",
    "ux-designer": "Designer",
    "reviewer": "Relecteur",
    "scout": "Éclaireur",
}
COLUMNS = (
    ("backlog", "Backlog"),
    ("ready", "Prêt"),
    ("in_progress", "En cours"),
    ("review", "Revue"),
    ("delivered", "Livré"),
)
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
_LAST_TS = "SELECT MAX(ts) FROM events"
_DAYS = "SELECT substr(ts, 1, 10), SUM(count) FROM token_usage WHERE ts >= ? GROUP BY 1"
# Ticket d'une session = dernier ticket connu dans ses événements (une session = un ticket) :
# token_usage.agent_id est un nom d'agent, jamais joint à events.agent_id (token-usage.md).
_BY_TICKET = """
WITH session_issue AS (
  SELECT session_id, issue, MAX(ts) AS last FROM events WHERE issue IS NOT NULL GROUP BY session_id
)
SELECT s.issue, SUM(t.count) FROM token_usage AS t JOIN session_issue AS s USING (session_id)
WHERE t.ts >= ? GROUP BY s.issue ORDER BY 2 DESC, 1 LIMIT ?
"""

Kind = Literal["answer", "review", "decision"]
State = Literal["working", "idle", "blocked"]
Level = Literal["ok", "warn", "danger"]
Ingestion = Literal["none", "silent", "live"]

log = logging.getLogger(__name__)
router = APIRouter()
auth.TEMPLATES.env.filters["thousands"] = tokens.thousands


@dataclass(frozen=True)
class Todo:
    kind: Kind
    title: str
    context: str
    age: str
    url: str


@dataclass(frozen=True)
class AgentRow:
    role: str
    state: State
    ticket: str
    project: str
    since: str
    last_seen: str
    late: bool


@dataclass(frozen=True)
class Card:
    title: str
    url: str


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    count: int
    cards: list[Card]

    @property
    def more(self) -> int:
        return self.count - len(self.cards)


@dataclass(frozen=True)
class Gauge:
    used: int
    cap: int
    percent: int
    level: Level

    @property
    def word(self) -> str:
        return {"ok": "✓ Confortable", "warn": "▲ Attention", "danger": "✖ Presque épuisé"}[
            self.level
        ]


@dataclass(frozen=True)
class Bar:
    label: str
    value: int
    percent: int  # largeur relative à la plus grande valeur


@dataclass(frozen=True)
class Board:
    today: str
    updated_at: str
    project: str | None
    unknown_project: bool
    projects: dict[str, int]
    github_error_at: str | None
    ingestion: Ingestion
    silent_minutes: int
    tickets_here: bool  # False : le filtre vise un projet dont les tickets ne sont pas suivis
    todos: list[Todo]
    agents: list[AgentRow]
    columns: list[Column]
    gauge: Gauge
    by_agent: list[Bar]
    days: list[Bar]
    by_ticket: list[Bar]

    @property
    def tickets_total(self) -> int:
        return sum(c.count for c in self.columns)

    @property
    def tokens_week(self) -> int:
        return sum(d.value for d in self.days)


# --- Formats ------------------------------------------------------------------------------
def span(delta: timedelta) -> str:
    """40 s · 3 min · 2 h 05 · 3 j (arrondi vers le bas)."""
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours >= 24:
        return f"{hours // 24} j"
    return f"{hours} h {minutes % 60:02d}" if minutes % 60 else f"{hours} h"


def french_date(now: datetime) -> str:
    return f"{WEEKDAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]} {now.year}"


def role(agent_type: str | None) -> str:
    if agent_type is None:
        return MAIN_THREAD
    return ROLES.get(agent_type, agent_type)


def gauge(used: int, cap: int) -> Gauge:
    pct = used * 100 // cap if cap > 0 else 0
    level: Level = "danger" if pct > DANGER_PERCENT else "warn" if pct >= WARN_PERCENT else "ok"
    return Gauge(used=used, cap=cap, percent=pct, level=level)


def bars(items: Iterable[tuple[str, int]]) -> list[Bar]:
    pairs = list(items)
    top = max((value for _, value in pairs), default=0)
    return [Bar(label, value, value * 100 // top if top else 0) for label, value in pairs]


def _parse(value: str | None) -> datetime | None:
    return progress._parse_ts(value) if value else None  # noqa: SLF001 - même module métier


def _age(value: str | None, now: datetime) -> str:
    when = _parse(value)
    return f"Dernière activité il y a {span(now - when)}" if when else ""


# --- GitHub : à faire et avancement -------------------------------------------------------
def _issue_url(number: int) -> str:
    return f"https://github.com/{progress.repo()}/issues/{number}"


def _pull_url(number: int) -> str:
    return f"https://github.com/{progress.repo()}/pull/{number}"


def _recent_first(issues: Iterable[progress.Issue]) -> list[progress.Issue]:
    by_number = sorted(issues, key=lambda i: i.number, reverse=True)
    return sorted(by_number, key=lambda i: i.updated_at or "", reverse=True)


def _open_tickets(snapshot: progress.Snapshot) -> list[progress.Issue]:
    return [
        i
        for i in snapshot.issues
        if not i.closed and "epic" not in i.labels and not i.labels & progress.IGNORED_LABELS
    ]


def build_todos(snapshot: progress.Snapshot, now: datetime) -> list[Todo]:
    """Questions « Attend l'utilisateur », PR ouvertes, tickets bloqués (dans cet ordre)."""
    tickets = _recent_first(_open_tickets(snapshot))
    titles = {i.number: i.title for i in snapshot.issues}
    answers = [
        Todo("answer", i.title, i.question, _age(i.updated_at, now), _issue_url(i.number))
        for i in tickets
        if i.question
    ]
    reviews = [
        Todo(
            "review",
            p.title,
            f"Ferme le ticket « {titles[p.issue]} »"
            if p.issue in titles
            else "Pull request ouverte",
            _age(p.updated_at, now),
            _pull_url(p.number),
        )
        for p in sorted(snapshot.pulls, key=lambda p: p.updated_at or "", reverse=True)
    ]
    decisions = [
        Todo(
            "decision",
            i.title,
            "Ticket bloqué : la décision se prend dans l'issue",
            _age(i.updated_at, now),
            _issue_url(i.number),
        )
        for i in tickets
        if "blocked" in i.labels and not i.question
    ]
    return answers + reviews + decisions


def build_columns(snapshot: progress.Snapshot) -> list[Column]:
    in_review = {p.issue for p in snapshot.pulls if p.issue is not None}
    counted = [
        i
        for i in snapshot.issues
        if "epic" not in i.labels
        and not i.labels & progress.IGNORED_LABELS
        and (i.completed or not i.closed)
    ]
    grouped: dict[str, list[progress.Issue]] = {key: [] for key, _ in COLUMNS}
    for item in counted:
        grouped[progress.classify(item, in_review)].append(item)
    return [
        Column(
            key,
            label,
            len(grouped[key]),
            [
                Card(i.title, _issue_url(i.number))
                for i in _recent_first(grouped[key])[:CARDS_PER_COLUMN]
            ],
        )
        for key, label in COLUMNS
    ]


# --- Événements : qui fait quoi -----------------------------------------------------------
def build_agents(
    events: list[activity.Event], now: datetime, snapshot: progress.Snapshot
) -> list[AgentRow]:
    """`events` du plus récent au plus ancien ; actifs d'abord, puis un inactif par rôle."""
    titles = {i.number: i.title for i in snapshot.issues}
    blocked = {i.number for i in snapshot.issues if "blocked" in i.labels}
    oldest: dict[tuple[str | None, str | None], datetime] = {}
    started: dict[tuple[str | None, str | None], datetime] = {}
    for event in events:
        when = _parse(event.ts)
        if when is None:
            continue
        oldest[(event.agent_type, event.agent_id)] = when
        if event.hook_event_name == "SubagentStart":  # dernier démarrage connu
            started.setdefault((event.agent_type, event.agent_id), when)
    started = {**oldest, **started}
    working: list[AgentRow] = []
    idle: list[AgentRow] = []
    for latest in activity.agents(events):
        when = _parse(latest.ts)
        if latest.agent_type is None or when is None:
            continue
        row = _agent_row(latest, when, now, started, titles, blocked)
        if row.state != "idle":
            working.append(row)
        elif all(r.role != row.role for r in idle) and len(idle) < progress.MAX_INACTIVE:
            idle.append(row)
    seen = {r.role for r in working}
    return working + [r for r in idle if r.role not in seen]


def _agent_row(
    latest: activity.Event,
    when: datetime,
    now: datetime,
    started: dict[tuple[str | None, str | None], datetime],
    titles: dict[int, str],
    blocked: set[int],
) -> AgentRow:
    active = latest.hook_event_name != "SubagentStop" and now - when < activity.SILENT_AFTER
    state: State = "idle"
    if active:
        state = "blocked" if latest.issue in blocked else "working"
    if latest.issue is None:
        ticket = "Aucun ticket indiqué"
    else:
        ticket = titles.get(latest.issue, "Ticket hors du dépôt suivi")
    since = started.get((latest.agent_type, latest.agent_id), when)
    return AgentRow(
        role=role(latest.agent_type),
        state=state,
        ticket=ticket,
        project=latest.project,
        since=span(now - since) if active else "",  # au repos : seul le signe de vie compte
        last_seen=f"il y a {span(now - when)}",
        late=state == "working" and now - when > LATE_AFTER,
    )


def ingestion(conn: sqlite3.Connection, now: datetime) -> tuple[Ingestion, int]:
    """État de l'ingestion et minutes écoulées depuis le dernier événement (toutes origines)."""
    last = _parse(conn.execute(_LAST_TS).fetchone()[0])
    if last is None:
        return "none", 0
    silence = now - last
    return ("silent" if silence > SILENCE_AFTER else "live"), int(silence.total_seconds() // 60)


# --- Tokens -------------------------------------------------------------------------------
def build_days(conn: sqlite3.Connection, now: datetime) -> list[Bar]:
    first = (now - timedelta(days=DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    found = {str(day): int(total) for day, total in conn.execute(_DAYS, (first.isoformat(),))}
    days = [first + timedelta(days=n) for n in range(DAYS)]
    return bars((_day_label(d), found.get(d.date().isoformat(), 0)) for d in days)


def _day_label(day: datetime) -> str:
    return f"{WEEKDAYS[day.weekday()][:3]}. {day.day}"


def build_by_ticket(
    conn: sqlite3.Connection, now: datetime, snapshot: progress.Snapshot
) -> list[Bar]:
    titles = {i.number: i.title for i in snapshot.issues}
    cutoff = (now - timedelta(days=DAYS)).isoformat(timespec="microseconds")
    rows = conn.execute(_BY_TICKET, (cutoff, TOP_TICKETS))
    return bars(
        (titles.get(int(number), "Ticket hors du dépôt suivi"), int(total))
        for number, total in rows
    )


def build_by_agent(conn: sqlite3.Connection, now: datetime) -> list[Bar]:
    totals = tokens.totals(conn, now).by_agent
    return bars(
        (MAIN_THREAD if t.label == tokens.MAIN_THREAD_LABEL else role(t.label), t.last_24h)
        for t in sorted(totals, key=lambda t: -t.last_24h)
        if t.last_24h
    )


# --- Assemblage ---------------------------------------------------------------------------
def build(
    conn: sqlite3.Connection,
    snapshot: progress.Snapshot,
    error_at: datetime | None,
    requested: str,
    now: datetime,
) -> Board:
    projects = activity.agent_counts(conn)
    project = requested if requested and requested in projects else None
    events = activity.latest_events(conn, progress.EVENT_WINDOW, project)
    tickets_here = project in (None, progress.repo().split("/")[1])
    shown = snapshot if tickets_here else progress.Snapshot()
    state, minutes = ingestion(conn, now)
    return Board(
        today=french_date(now),
        updated_at=now.strftime("%H:%M:%S"),
        project=project,
        unknown_project=bool(requested) and project is None,
        projects=projects,
        github_error_at=error_at.strftime("%H:%M:%S") if error_at else None,
        ingestion=state,
        silent_minutes=minutes,
        tickets_here=tickets_here,
        todos=build_todos(shown, now),
        agents=build_agents(events, now, snapshot),
        columns=build_columns(shown),
        gauge=gauge(controls.tokens_24h(conn, now), db.read_controls(conn)[2]),
        by_agent=build_by_agent(conn, now),
        days=build_days(conn, now),
        by_ticket=build_by_ticket(conn, now, snapshot),
    )


def load(requested: str) -> Board:
    snapshot, error_at = progress.CACHE.get()
    with db.connect() as conn:
        return build(conn, snapshot, error_at, requested, datetime.now(UTC))


@router.get("/", response_model=None)
async def board_page(request: Request, projet: str = Query("")) -> Response:
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
    reload_url = "/" + (f"?{urlencode({'projet': projet})}" if projet else "")
    context: dict[str, object] = {
        "reload_url": reload_url,
        "repo": progress.repo(),
        "b": None,
        "db_error_at": None,
    }
    status = 200
    try:
        context["b"] = await run_in_threadpool(load, projet)
    except sqlite3.Error as exc:
        log.warning("lecture des événements impossible : %s", type(exc).__name__)
        context["db_error_at"] = datetime.now(UTC).strftime("%H:%M:%S")
        status = 503
    response: HTMLResponse = auth.TEMPLATES.TemplateResponse(
        request, "board.html", context, status_code=status
    )
    response.headers["Cache-Control"] = "no-store"
    return response

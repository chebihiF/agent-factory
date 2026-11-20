"""GET /activity : 100 derniers événements d'agents (contrat : contracts/dashboard-routes.md)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dashboard import auth, db

MAX_EVENTS = 100
_COLUMNS = "event_id, ts, hook_event_name, agent_type, agent_id, tool_name, project, issue, model"
_ORDER = "ORDER BY ts DESC, rowid DESC LIMIT ?"
_SELECT = f"SELECT {_COLUMNS} FROM events {_ORDER}"  # noqa: S608 - constantes
_SELECT_PROJECT = f"SELECT {_COLUMNS} FROM events WHERE project = ? {_ORDER}"  # noqa: S608
# Agents distincts par projet, sur la fenêtre des MAX_EVENTS derniers événements de ce projet (celle
# de la vue filtrée) ; la ligne '' (« Tous ») compte sur les MAX_EVENTS derniers événements toutes
# origines confondues (celle de la vue « Tous ») : elle peut donc être inférieure à la somme.
_COUNTS = """
WITH ranked AS (
  SELECT project, agent_type, agent_id,
         ROW_NUMBER() OVER (PARTITION BY project ORDER BY ts DESC, rowid DESC) AS in_project,
         ROW_NUMBER() OVER (ORDER BY ts DESC, rowid DESC) AS overall
  FROM events
)
SELECT project, COUNT(*) FROM (
  SELECT DISTINCT project, agent_type, agent_id FROM ranked WHERE in_project <= :limit
  UNION ALL
  SELECT DISTINCT '', agent_type, agent_id FROM ranked WHERE overall <= :limit
) GROUP BY project
"""
# Projets toujours proposés par le filtre (#68), même sans événement ; les autres s'ajoutent.
KNOWN_PROJECTS = (db.DEFAULT_PROJECT, "agent-factory")
BADGES = ("badge-info", "badge-warn", "badge-ok", "badge-neutral")
_LAST_ID = "SELECT COALESCE(MAX(rowid), 0) FROM events"
# Sans signe de vie depuis ce délai, un agent n'est plus « en cours » (#181).
SILENT_AFTER = timedelta(minutes=30)

log = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True)
class Event:
    event_id: str
    ts: str
    hook_event_name: str
    agent_type: str | None
    agent_id: str | None
    tool_name: str | None
    project: str
    issue: int | None = None  # absents des événements antérieurs au #181
    model: str | None = None

    @property
    def time_label(self) -> str:
        try:
            return datetime.fromisoformat(self.ts).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return self.ts


def latest_events(
    conn: sqlite3.Connection, limit: int = MAX_EVENTS, project: str | None = None
) -> list[Event]:
    if project is None:
        return [Event(*row) for row in conn.execute(_SELECT, (limit,))]
    return [Event(*row) for row in conn.execute(_SELECT_PROJECT, (project, limit))]


def agent_counts(conn: sqlite3.Connection, limit: int = MAX_EVENTS) -> dict[str, int]:
    """Agents par projet en une requête ; "" = « Tous ». Projets connus toujours présents."""
    found = {str(project): int(n) for project, n in conn.execute(_COUNTS, {"limit": limit})}
    stored = sorted(name for name in found if name and name not in KNOWN_PROJECTS)
    names = ("", *KNOWN_PROJECTS, *stored)
    return {name: found.get(name, 0) for name in names}


@dataclass(frozen=True)
class View:
    """Page /activity : `project` None = tous ; `counts` = agents par projet ("" = tous)."""

    events: list[Event]
    project: str | None
    counts: dict[str, int]
    unknown: bool

    @property
    def badges(self) -> dict[str, str]:
        names = [name for name in self.counts if name]
        return {name: BADGES[i % len(BADGES)] for i, name in enumerate(names)}


def load_view_from(conn: sqlite3.Connection, requested: str) -> View:
    counts = agent_counts(conn)
    project = requested if requested and requested in counts else None
    events = latest_events(conn) if project is None else latest_events(conn, MAX_EVENTS, project)
    return View(events, project, counts, unknown=bool(requested) and project is None)


def load_view(requested: str) -> View:
    with db.connect() as conn:
        return load_view_from(conn, requested)


def last_id(conn: sqlite3.Connection) -> int:
    """Identifiant du dernier événement : curseur de départ du flux SSE (`/stream`)."""
    return int(conn.execute(_LAST_ID).fetchone()[0])


def agents(events: list[Event]) -> list[Event]:
    """Dernier événement de chaque agent, avec ses derniers ticket et modèle connus.

    `events` est trié du plus récent au plus ancien.
    """
    latest: dict[tuple[str | None, str | None], Event] = {}
    issues: dict[tuple[str | None, str | None], int] = {}
    models: dict[tuple[str | None, str | None], str] = {}
    for event in events:
        key = (event.agent_type, event.agent_id)
        latest.setdefault(key, event)
        if event.issue is not None:
            issues.setdefault(key, event.issue)
        if event.model:
            models.setdefault(key, event.model)
    return [replace(e, issue=issues.get(k), model=models.get(k)) for k, e in latest.items()]


def load_last_id() -> int:
    with db.connect() as conn:
        return last_id(conn)


def _page(request: Request, status: int, context: dict[str, object]) -> HTMLResponse:
    response = auth.TEMPLATES.TemplateResponse(
        request, "activity.html", context, status_code=status
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/activity", response_model=None)
async def activity_page(request: Request, projet: str = Query("")) -> Response:
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    try:
        cursor = await run_in_threadpool(load_last_id)  # avant la lecture : aucun trou possible
        view = await run_in_threadpool(load_view, projet)
    except sqlite3.Error as exc:
        log.warning("lecture des événements impossible : %s", type(exc).__name__)
        failed_at = datetime.now(UTC).strftime("%H:%M:%S")
        error: dict[str, object] = {
            "error_at": failed_at,
            "retry_url": "/activity" + (f"?{urlencode({'projet': projet})}" if projet else ""),
            "events": [],
            "agents": [],
            "last_id": None,
        }
        return _page(request, 503, error)
    context: dict[str, object] = {
        "error_at": None,
        "view": view,
        "events": view.events,
        "agents": agents(view.events),
        "last_id": cursor,
    }
    return _page(request, 200, context)

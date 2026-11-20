"""GET /activity : 100 derniers événements d'agents (contrat : contracts/dashboard-routes.md)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dashboard import auth, db

MAX_EVENTS = 100
_COLUMNS = "event_id, ts, hook_event_name, agent_type, agent_id, tool_name, project"
_ORDER = "ORDER BY ts DESC, rowid DESC LIMIT ?"
_SELECT = f"SELECT {_COLUMNS} FROM events {_ORDER}"  # noqa: S608 - constantes
_SELECT_PROJECT = f"SELECT {_COLUMNS} FROM events WHERE project = ? {_ORDER}"  # noqa: S608
_PROJECTS = "SELECT DISTINCT project FROM events ORDER BY project"
# Projets toujours proposés par le filtre (#68), même sans événement ; les autres s'ajoutent.
KNOWN_PROJECTS = (db.DEFAULT_PROJECT, "agent-factory")
BADGES = ("badge-info", "badge-warn")
_LAST_ID = "SELECT COALESCE(MAX(rowid), 0) FROM events"

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


def project_names(conn: sqlite3.Connection) -> list[str]:
    stored = [str(row[0]) for row in conn.execute(_PROJECTS)]
    return [*KNOWN_PROJECTS, *(name for name in stored if name not in KNOWN_PROJECTS)]


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


def load_view(requested: str) -> View:
    with db.connect() as conn:
        everything = latest_events(conn)
        names = project_names(conn)
        counts = {"": len(agents(everything))}
        counts |= {name: len(agents(latest_events(conn, MAX_EVENTS, name))) for name in names}
        project = requested if requested in names else None
        events = everything if project is None else latest_events(conn, MAX_EVENTS, project)
    return View(events, project, counts, unknown=bool(requested) and project is None)


def last_id(conn: sqlite3.Connection) -> int:
    """Identifiant du dernier événement : curseur de départ du flux SSE (`/stream`)."""
    return int(conn.execute(_LAST_ID).fetchone()[0])


def agents(events: list[Event]) -> list[Event]:
    """Dernier événement de chaque agent (`events` est trié du plus récent au plus ancien)."""
    latest: dict[tuple[str | None, str | None], Event] = {}
    for event in events:
        latest.setdefault((event.agent_type, event.agent_id), event)
    return list(latest.values())


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

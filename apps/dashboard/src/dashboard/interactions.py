"""GET /interactions : sessions, sous-agents et durée (contrat : contracts/dashboard-routes.md)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dashboard import activity, auth, db

MAX_SESSIONS = 20
START, STOP = "SubagentStart", "SubagentStop"
_SESSIONS = (
    "SELECT session_id, MIN(ts), MAX(ts), COUNT(*) FROM events "
    "GROUP BY session_id ORDER BY MAX(ts) DESC, MAX(rowid) DESC LIMIT ?"
)
# Un sous-agent par (session, agent_id) ayant un Start ou un Stop : premier Start, dernier Stop,
# dernier événement quel qu'il soit (signe de vie), derniers type et modèle connus (#181).
_SUBAGENTS = """
SELECT session_id, agent_id,
  (SELECT agent_type FROM events t WHERE t.session_id = e.session_id AND t.agent_id = e.agent_id
     AND t.agent_type IS NOT NULL ORDER BY t.ts DESC, t.rowid DESC LIMIT 1),
  MIN(CASE WHEN hook_event_name = ? THEN ts END) AS started_at,
  MAX(CASE WHEN hook_event_name = ? THEN ts END) AS stopped_at,
  MAX(ts),
  (SELECT model FROM events m WHERE m.session_id = e.session_id AND m.agent_id = e.agent_id
     AND m.model IS NOT NULL ORDER BY m.ts DESC, m.rowid DESC LIMIT 1)
FROM events e
WHERE agent_id IS NOT NULL AND session_id IN ({marks})
GROUP BY session_id, agent_id
HAVING started_at IS NOT NULL OR stopped_at IS NOT NULL
ORDER BY MIN(rowid)
"""
SubagentRow = tuple[str, str, str | None, str | None, str | None, str, str | None]

log = logging.getLogger(__name__)
router = APIRouter()


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts).astimezone(UTC) if ts else None
    except ValueError:
        return None


def _label(ts: str | None) -> str:
    parsed = _parse(ts)
    return parsed.strftime("%H:%M:%S") if parsed else "—"


@dataclass(frozen=True)
class Subagent:
    agent_id: str
    agent_type: str | None
    started_at: str | None
    stopped_at: str | None
    model: str | None = None
    silent: bool = False  # démarré, sans Stop ni signe de vie depuis `SILENT_AFTER` (#181)

    @property
    def duration_s(self) -> float | None:
        start, stop = _parse(self.started_at), _parse(self.stopped_at)
        if start is None or stop is None:
            return None
        return max(0.0, (stop - start).total_seconds())

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.stopped_at is None and not self.silent

    @property
    def duration_label(self) -> str:
        seconds = self.duration_s
        if seconds is None:
            return "—"
        whole = int(seconds)
        if whole < 1:
            return "< 1 s"
        if whole < 60:
            return f"{whole} s"
        if whole < 3600:
            return f"{whole // 60} min {whole % 60:02d} s"
        return f"{whole // 3600} h {whole % 3600 // 60:02d} min"

    @property
    def start_label(self) -> str:
        return _label(self.started_at)


@dataclass(frozen=True)
class Session:
    session_id: str
    first_ts: str
    last_ts: str
    event_count: int
    subagents: list[Subagent] = field(default_factory=list)

    @property
    def first_label(self) -> str:
        return _label(self.first_ts)

    @property
    def last_label(self) -> str:
        return _label(self.last_ts)


def _silent(started_at: str | None, stopped_at: str | None, last_seen: str, now: datetime) -> bool:
    """Démarré, sans Stop ni signe de vie lisible depuis `SILENT_AFTER` : plus « en cours »."""
    if started_at is None or stopped_at is not None:
        return False
    seen = _parse(last_seen)
    return seen is None or now - seen > activity.SILENT_AFTER


def _subagents(rows: list[SubagentRow], now: datetime) -> dict[str, list[Subagent]]:
    by_session: dict[str, list[Subagent]] = {}
    for session_id, agent_id, kind, started, stopped, last_seen, model in rows:
        silent = _silent(started, stopped, last_seen, now)
        subagent = Subagent(agent_id, kind, started, stopped, model, silent)
        by_session.setdefault(session_id, []).append(subagent)
    return by_session


def load_sessions(conn: sqlite3.Connection, now: datetime) -> list[Session]:
    heads = conn.execute(_SESSIONS, (MAX_SESSIONS,)).fetchall()
    if not heads:
        return []
    ids = [row[0] for row in heads]
    query = _SUBAGENTS.format(marks=", ".join("?" for _ in ids))  # noqa: S608 - marqueurs
    paired = _subagents(conn.execute(query, (START, STOP, *ids)).fetchall(), now)
    return [
        Session(sid, first, last, count, paired.get(sid, [])) for sid, first, last, count in heads
    ]


def load_page() -> tuple[int, list[Session]]:
    with db.connect() as conn:
        cursor = activity.last_id(conn)  # avant la lecture : aucun trou possible
        return cursor, load_sessions(conn, datetime.now(UTC))


def _page(request: Request, status: int, context: dict[str, object]) -> HTMLResponse:
    response = auth.TEMPLATES.TemplateResponse(
        request, "interactions.html", context, status_code=status
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/interactions", response_model=None)
async def interactions_page(request: Request) -> Response:
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    try:
        cursor, sessions = await run_in_threadpool(load_page)
    except sqlite3.Error as exc:
        log.warning("lecture des interactions impossible : %s", type(exc).__name__)
        failed_at = datetime.now(UTC).strftime("%H:%M:%S")
        return _page(request, 503, {"error_at": failed_at, "sessions": [], "last_id": None})
    return _page(request, 200, {"error_at": None, "sessions": sessions, "last_id": cursor})

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
# Tous les événements d'agent : Start/Stop apparient, les autres sont des signes de vie (#181).
_AGENT_EVENTS = (
    "SELECT session_id, agent_id, agent_type, hook_event_name, ts, model FROM events "
    "WHERE agent_id IS NOT NULL AND session_id IN ({marks}) ORDER BY ts, rowid"
)
AgentRow = tuple[str, str, str | None, str, str, str | None]

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


@dataclass
class _Trace:
    """Événements d'un agent dans une session, lus dans l'ordre chronologique."""

    agent_type: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    last_seen: str | None = None
    model: str | None = None

    def add(self, agent_type: str | None, name: str, ts: str, model: str | None) -> None:
        self.agent_type = agent_type or self.agent_type
        self.model = model or self.model
        self.last_seen = ts
        if name == START:
            self.started_at = self.started_at or ts
        elif name == STOP:
            self.stopped_at = ts

    def silent(self, now: datetime) -> bool:
        seen = _parse(self.last_seen)
        open_ended = self.started_at is not None and self.stopped_at is None
        return open_ended and seen is not None and now - seen > activity.SILENT_AFTER


def _pair_subagents(rows: list[AgentRow], now: datetime) -> dict[str, list[Subagent]]:
    """Apparie Start/Stop par (session, agent_id) : premier Start, dernier Stop."""
    traces: dict[tuple[str, str], _Trace] = {}
    for session_id, agent_id, agent_type, name, ts, model in rows:
        traces.setdefault((session_id, agent_id), _Trace()).add(agent_type, name, ts, model)
    by_session: dict[str, list[Subagent]] = {}
    for (session_id, agent_id), t in traces.items():
        if t.started_at is None and t.stopped_at is None:
            continue  # agent sans Start ni Stop : pas un sous-agent
        subagent = Subagent(
            agent_id, t.agent_type, t.started_at, t.stopped_at, t.model, t.silent(now)
        )
        by_session.setdefault(session_id, []).append(subagent)
    return by_session


def load_sessions(conn: sqlite3.Connection, now: datetime) -> list[Session]:
    heads = conn.execute(_SESSIONS, (MAX_SESSIONS,)).fetchall()
    if not heads:
        return []
    ids = [row[0] for row in heads]
    query = _AGENT_EVENTS.format(marks=", ".join("?" for _ in ids))  # noqa: S608 - marqueurs
    paired = _pair_subagents(conn.execute(query, ids).fetchall(), now)
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

"""Pause des agents et plafond de tokens (contrat : contracts/control-api.md).

Le dashboard est en lecture seule (#179) : plus de page ni de POST. Seule reste l'API
GET /api/v1/control (jeton Bearer d'ingestion), lue par le hook PreToolUse ; pause, reprise et
plafond se règlent depuis le terminal du tech lead (`db.pause`, `db.resume`, `db.set_cap`).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from dashboard import db, ingest, tokens

REASON_CAP = "plafond"

log = logging.getLogger(__name__)
router = APIRouter()


class ControlState(BaseModel):
    """Corps de GET /api/v1/control (documenté dans contracts/control-api.md)."""

    paused: bool
    reason: str | None
    cap_tokens: int
    tokens_24h: int
    updated_at: str


def tokens_24h(conn: sqlite3.Connection, now: datetime) -> int:
    """Total des tokens des dernières 24 h glissantes (même source que /tokens, #7)."""
    return sum(total.last_24h for total in tokens.totals(conn, now).by_model)


def current_state(conn: sqlite3.Connection, now: datetime) -> ControlState:
    """État courant ; le plafond atteint met en pause (motif « plafond ») avant de répondre."""
    paused, reason, cap, updated_at = db.read_controls(conn)
    used = tokens_24h(conn, now)
    if not paused and used >= cap:
        db.pause(conn, REASON_CAP)
        paused, reason, cap, updated_at = db.read_controls(conn)
    return ControlState(
        paused=paused, reason=reason, cap_tokens=cap, tokens_24h=used, updated_at=updated_at
    )


def load_state() -> ControlState:
    with db.connect() as conn:
        return current_state(conn, datetime.now(UTC))


# --- API pour le hook PreToolUse ----------------------------------------------------------------
@router.get("/api/v1/control", dependencies=[Depends(ingest.require_token)], response_model=None)
async def control_api() -> Response:
    headers = {"Cache-Control": "no-store"}
    try:
        state = await run_in_threadpool(load_state)
    except sqlite3.Error as exc:
        log.warning("lecture des contrôles impossible : %s", type(exc).__name__)
        return JSONResponse({"detail": "base illisible"}, status_code=503, headers=headers)
    return JSONResponse(state.model_dump(), headers=headers)

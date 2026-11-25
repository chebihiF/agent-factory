"""Pause des agents et plafond de tokens (contrat : contracts/control-api.md).

API GET /api/v1/control (jeton Bearer d'ingestion), lue par le hook PreToolUse. Onglet
« Contrôles » (#185) : lecture seule pour le visiteur ; Pause et Plafond, confirmés, pour l'admin
(session `dashboard.admin`, ADR-005). Ce sont les seules routes qui écrivent `controls`.
La reprise reste au terminal du tech lead (`db.resume`).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from dashboard import admin, auth, db, ingest, tokens

REASON_CAP = "plafond"
REASON_MANUAL = "manuel"
CAP_MIN, CAP_MAX = 1, 1_000_000_000
CAP_TOO_SMALL = "Il faut au moins 1 token."
CAP_NOT_DIGITS = "Chiffres seulement."
CAP_TOO_BIG = "Trop grand : le maximum est 1 000 000 000 tokens / 24 h."
NOT_CONFIRMED = "Confirmation absente : aucune action n'a été envoyée."
DONE = ("pause", "plafond")

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TEMPLATES.env.filters["thousands"] = tokens.thousands

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


def read_state(conn: sqlite3.Connection, now: datetime) -> ControlState:
    """État courant, sans aucune écriture (onglet « Contrôles »)."""
    paused, reason, cap, updated_at = db.read_controls(conn)
    return ControlState(
        paused=paused,
        reason=reason,
        cap_tokens=cap,
        tokens_24h=tokens_24h(conn, now),
        updated_at=updated_at,
    )


def current_state(conn: sqlite3.Connection, now: datetime) -> ControlState:
    """État courant ; le plafond atteint met en pause (motif « plafond ») avant de répondre."""
    state = read_state(conn, now)
    if not state.paused and state.tokens_24h >= state.cap_tokens:
        db.pause(conn, REASON_CAP)
        state = read_state(conn, now)
    return state


def parse_cap(raw: str) -> int:
    """Plafond saisi : chiffres ASCII, de 1 à 1 000 000 000 ; ValueError porte le message."""
    if raw and not (raw.isascii() and raw.isdigit()):
        raise ValueError(CAP_NOT_DIGITS)
    if len(raw.lstrip("0")) > len(str(CAP_MAX)) or (raw and int(raw) > CAP_MAX):
        raise ValueError(CAP_TOO_BIG)
    if not raw or int(raw) < CAP_MIN:
        raise ValueError(CAP_TOO_SMALL)
    return int(raw)


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


# --- Onglet « Contrôles » (#185) ----------------------------------------------------------------
def _since(updated_at: str) -> str:
    """« HH:MM UTC » du dernier changement, ou chaîne vide si illisible."""
    try:
        return datetime.fromisoformat(updated_at).astimezone(UTC).strftime("%H:%M UTC")
    except ValueError:
        return ""


def _change(old: int, new: int) -> str:
    return f"{round((new - old) * 100 / max(1, old)):+d} %"


def _render(request: Request, context: dict[str, Any], status: int = 200) -> Response:
    state: ControlState = context["state"]
    context["since"] = _since(state.updated_at)
    context["used_pct"] = min(100, round(state.tokens_24h * 100 / max(1, state.cap_tokens)))
    if context.get("cap_new"):
        context["cap_change"] = _change(state.cap_tokens, context["cap_new"])
    return TEMPLATES.TemplateResponse(
        request, "controls.html", context, status_code=status, headers=admin.NO_STORE
    )


def _load() -> ControlState:
    with db.connect() as conn:
        return read_state(conn, datetime.now(UTC))


def _preview(params: dict[str, str]) -> tuple[dict[str, Any], int]:
    """Panneaux de confirmation demandés par l'URL (admin seulement) ; 422 si valeur invalide."""
    context: dict[str, Any] = {"confirm_pause": params.get("confirmer") == "pause"}
    if params.get("fait") in DONE:
        context["done"] = params["fait"]
    if "plafond" not in params:
        return context, 200
    context["cap_raw"] = params["plafond"]
    try:
        context["cap_new"] = parse_cap(params["plafond"])
    except ValueError as exc:
        context["cap_error"] = str(exc)
        return context, 422
    return context, 200


@router.get("/controls", response_model=None)
async def controls_page(request: Request) -> Response:
    session = admin.current(request)
    if session is None and not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    try:
        state = await run_in_threadpool(_load)
    except sqlite3.Error as exc:
        log.warning("lecture des contrôles impossible : %s", type(exc).__name__)
        return Response("Base illisible.", status_code=503, headers=admin.NO_STORE)
    context: dict[str, Any] = {"state": state, "session": session}
    status = 200
    if session is not None:
        extra, status = _preview(dict(request.query_params))
        context.update(extra)
    return _render(request, context, status)


async def _apply(action: Callable[[sqlite3.Connection], None]) -> None:
    def run() -> None:
        with db.connect() as conn:
            action(conn)

    await run_in_threadpool(run)


async def _refuse(request: Request, session: admin.Session, **extra: Any) -> Response:
    """Page admin en 422 : entrée invalide, rien n'est écrit."""
    state = await run_in_threadpool(_load)
    return _render(request, {"state": state, "session": session, **extra}, 422)


def _done(what: str) -> Response:
    return RedirectResponse(f"/controls?fait={what}", status_code=303, headers=admin.NO_STORE)


@router.post("/controls/pause", response_model=None)
async def pause(request: Request) -> Response:
    granted = await admin.authorize(request)
    if isinstance(granted, Response):
        return granted
    session, form = granted
    if form.get("confirm") != "oui":
        return await _refuse(request, session, error=NOT_CONFIRMED)
    await _apply(lambda conn: db.pause(conn, REASON_MANUAL))
    return _done("pause")


@router.post("/controls/cap", response_model=None)
async def set_cap(request: Request) -> Response:
    granted = await admin.authorize(request)
    if isinstance(granted, Response):
        return granted
    session, form = granted
    raw = form.get("cap", "")
    if form.get("confirm") != "oui":
        return await _refuse(request, session, error=NOT_CONFIRMED)
    try:
        cap = parse_cap(raw)
    except ValueError as exc:
        return await _refuse(request, session, cap_raw=raw, cap_error=str(exc))
    await _apply(lambda conn: db.set_cap(conn, cap))
    return _done("plafond")

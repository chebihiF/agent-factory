"""Connexion admin : seule porte des commandes Pause et Plafond (#185, ADR-005).

Mot de passe admin distinct (hash PBKDF2 dans `ADMIN_PASSWORD_HASH`) ; session côté serveur
(mémoire, un seul worker) désignée par le cookie `vva_admin`, signé HMAC avec une clé dérivée de
`DASHBOARD_SESSION_KEY` et du hash ; jeton CSRF lié à la session. Contrat :
contracts/dashboard-routes.md. Stdlib uniquement.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from dashboard import auth

ADMIN_HASH_ENV = "ADMIN_PASSWORD_HASH"  # noqa: S105 - nom de variable, pas un secret
COOKIE_NAME = "vva_admin"
ABSOLUTE_TTL_S = 8 * 3600
IDLE_TTL_S = 30 * 60
MAX_COOKIE_LEN = 128
PER_IP_MAX = 5
GLOBAL_MAX = 30
WINDOW_S = 60
MAX_TRACKED_IPS = 4096
HOME = "/controls"
NO_STORE = {"Cache-Control": "no-store"}
DISABLED = "Accès admin désactivé."
WRONG = "Mot de passe incorrect."
EXPIRED = "Session admin absente ou expirée : reconnecte-toi."
FORBIDDEN = "Requête refusée : jeton de sécurité invalide. Recharge la page Contrôles."
TOO_BIG = "Requête trop volumineuse."

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()


@dataclass
class Session:
    sid: str
    csrf: str
    created: float
    last_seen: float


class SessionStore:
    """Sessions admin en mémoire : 8 h au plus, closes après 30 min sans requête."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def open(self) -> Session:
        now = self.clock()
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now, now)
        with self._lock:
            self._sessions = {k: s for k, s in self._sessions.items() if _alive(s, now)}
            self._sessions[session.sid] = session
        return session

    def touch(self, sid: str) -> Session | None:
        """Session vivante, repoussée de 30 min ; None (et oubliée) si expirée ou inconnue."""
        now = self.clock()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None or not _alive(session, now):
                self._sessions.pop(sid, None)
                return None
            session.last_seen = now
            return session

    def close(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


def _alive(session: Session, now: float) -> bool:
    return now - session.created < ABSOLUTE_TTL_S and now - session.last_seen < IDLE_TTL_S


class KeyedLimiter:
    """Fenêtre glissante par clé (IP) : au plus `max_attempts` essais par `window_s` secondes."""

    def __init__(
        self, max_attempts: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_attempts, self.window_s, self.clock = max_attempts, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _window(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        return hits

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            if len(self._hits) > MAX_TRACKED_IPS:
                self._prune(now)
            hits = self._window(key, now)
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            return True

    def remaining(self, key: str) -> int:
        with self._lock:
            return max(0, self.max_attempts - len(self._window(key, self.clock())))

    def retry_after(self, key: str) -> int:
        """Secondes avant qu'un essai se libère (0 si la limite n'est pas atteinte)."""
        with self._lock:
            now = self.clock()
            hits = self._window(key, now)
            if len(hits) < self.max_attempts:
                return 0
            return max(1, math.ceil(self.window_s - (now - hits[0])))

    def _prune(self, now: float) -> None:
        self._hits = {k: h for k, h in self._hits.items() if h and now - h[-1] < self.window_s}
        if len(self._hits) > MAX_TRACKED_IPS:  # trop d'IP : la limite globale reste en place
            self._hits.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


SESSIONS = SessionStore()
LOGIN_LIMITER = KeyedLimiter(PER_IP_MAX, WINDOW_S)
GLOBAL_LIMITER = KeyedLimiter(GLOBAL_MAX, WINDOW_S)  # borne le coût PBKDF2 multi-IP
GLOBAL_KEY = "*"


def reset() -> None:
    """Oublie sessions et essais (tests)."""
    SESSIONS.clear()
    LOGIN_LIMITER.reset()
    GLOBAL_LIMITER.reset()


def _key() -> bytes | None:
    """Clé de signature admin, ou None si l'admin est désactivé (hash ou clé absents)."""
    encoded = os.environ.get(ADMIN_HASH_ENV, "")
    key = auth.session_key()
    if not encoded or key is None:
        return None
    return hmac.new(key.encode(), f"admin-v1:{encoded}".encode(), hashlib.sha256).digest()


def _sign(key: bytes, sid: str) -> str:
    mac = hmac.new(key, sid.encode(), hashlib.sha256).digest()
    return f"{sid}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


def _sid(key: bytes, token: str) -> str | None:
    if not token or len(token) > MAX_COOKIE_LEN or not token.isascii():
        return None
    sid = token.partition(".")[0]
    return sid if hmac.compare_digest(token.encode(), _sign(key, sid).encode()) else None


def current(request: Request) -> Session | None:
    """Session admin valide de la requête (et repoussée), sinon None."""
    key = _key()
    sid = None if key is None else _sid(key, request.cookies.get(COOKIE_NAME, ""))
    return None if sid is None else SESSIONS.touch(sid)


def page(
    request: Request, status: int, message: str | None = None, *, retry_after: int = 0
) -> HTMLResponse:
    """Page de connexion admin (aussi pour 401/403/413/429), toujours `no-store`."""
    context = {
        "message": message,
        "disabled": _key() is None,
        "retry_after": retry_after,
        "invalid": bool(message and message.startswith(WRONG)),
    }
    response = TEMPLATES.TemplateResponse(
        request, "admin_login.html", context, status_code=status, headers=NO_STORE
    )
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


async def authorize(request: Request) -> tuple[Session, dict[str, str]] | Response:
    """(session, formulaire) d'un POST admin valide ; sinon la réponse de refus."""
    if _key() is None:
        return page(request, 403, DISABLED)
    session = current(request)
    if session is None:
        return page(request, 401, EXPIRED)
    form = await auth.read_form(request)
    if form is None:
        return page(request, 413, TOO_BIG)
    sent = form.get("csrf", "").encode()
    if not hmac.compare_digest(sent, session.csrf.encode()):
        return page(request, 403, FORBIDDEN)
    return session, form


@router.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return page(request, 403, DISABLED) if _key() is None else page(request, 200)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "inconnu"


def _remaining_message(ip: str) -> str:
    left = LOGIN_LIMITER.remaining(ip)
    return f"{WRONG} Il te reste {left} essai{'s' if left > 1 else ''} cette minute."


@router.post("/admin/login", response_model=None)
async def login(request: Request) -> Response:
    key = _key()
    if key is None:
        return page(request, 403, DISABLED)
    ip = _client_ip(request)
    if not LOGIN_LIMITER.allow(ip) or not GLOBAL_LIMITER.allow(GLOBAL_KEY):
        wait = max(LOGIN_LIMITER.retry_after(ip), GLOBAL_LIMITER.retry_after(GLOBAL_KEY), 1)
        message = f"Trop d'essais. Limite de 5 par minute atteinte. Réessaie dans {wait} s."
        return page(request, 429, message, retry_after=wait)
    form = await auth.read_form(request)
    if form is None:
        return page(request, 413, TOO_BIG)
    encoded = os.environ.get(ADMIN_HASH_ENV, "")
    if not await run_in_threadpool(auth.verify_password, form.get("password", ""), encoded):
        return page(request, 401, _remaining_message(ip))
    response = RedirectResponse(HOME, status_code=303, headers=NO_STORE)
    response.set_cookie(
        COOKIE_NAME,
        _sign(key, SESSIONS.open().sid),
        max_age=ABSOLUTE_TTL_S,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/admin/logout", response_model=None)
async def logout(request: Request) -> Response:
    granted = await authorize(request)
    if isinstance(granted, Response):
        return granted
    SESSIONS.close(granted[0].sid)
    response = RedirectResponse(HOME, status_code=303, headers=NO_STORE)
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
    return response

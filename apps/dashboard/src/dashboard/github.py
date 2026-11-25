"""Lecture des tickets GitHub pour le tableau de bord `/` (#26, recentré par #179).

API REST publique **sans jeton** (décision D20, dépôt public), cache mémoire de 3 min, requêtes
conditionnelles (ETag : un 304 ne compte pas dans la limite de 60 requêtes/heure), dernières
données connues en cas d'échec, ETag et dernier instantané persistés en SQLite (#186) pour
survivre aux redémarrages. Contrat : contracts/dashboard-routes.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dashboard import db

DEFAULT_REPO = "chebihiF/viral-video-agents"
API_ROOT = "https://api.github.com"
CACHE_TTL_S = 180.0
MAX_RATE_WAIT_S = 3600.0
MAX_PAGES = 5
HTTP_TIMEOUT_S = 5.0
MAX_BODY_BYTES = 4 * 1024 * 1024

IGNORED_LABELS = frozenset({"decision", "wontfix", "duplicate", "invalid", "question"})
IN_PROGRESS_LABELS = frozenset({"in-progress", "en-cours"})
READY_LABELS = frozenset({"P0", "P1"})
CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
TITLE_REF_RE = re.compile(r"\(#(\d+)\)")
# Ligne à part entière (puce permise) ; jamais une citation « > » ni un bloc de code (#179).
QUESTION_RE = re.compile(
    r"^\s*[-*]?\s*Attend l['’]utilisateur\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
# Épique du ticket : « Epic #72 », « Épique : #2 » (jamais « Parent : #n », qui est un ticket).
EPIC_RE = re.compile(r"\b(?:epic|épique)\s*:?\s*#(\d+)", re.IGNORECASE)
SPRINT_LABEL_RE = re.compile(r"^sprint[\s:_-]+(\d+)$", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*(```|~~~).*?^\s*\1", re.DOTALL | re.MULTILINE)
QUESTION_MAX = 200
NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


log = logging.getLogger(__name__)


# --- Client GitHub ----------------------------------------------------------------------------
def repo() -> str:
    value = os.environ.get("VVA_GITHUB_REPO", "")
    return value if REPO_RE.fullmatch(value) and ".." not in value else DEFAULT_REPO


class RateLimited(Exception):
    """Limite GitHub atteinte : `until` = instant (epoch, s) de réinitialisation."""

    def __init__(self, until: float) -> None:
        super().__init__("limite de l'API GitHub atteinte")
        self.until = until


def _rate_limit(exc: HTTPError) -> RateLimited | None:
    headers = exc.headers
    if headers is None or exc.code not in (403, 429):
        return None
    reset, retry = headers.get("X-RateLimit-Reset"), headers.get("Retry-After")
    if exc.code == 403 and headers.get("X-RateLimit-Remaining") != "0" and retry is None:
        return None  # 403 ordinaire (accès refusé), pas une limite
    if reset and reset.isdigit():
        return RateLimited(float(reset))
    return RateLimited(time.time() + (float(retry) if retry and retry.isdigit() else 60.0))


def _next_url(link: str | None) -> str | None:
    match = NEXT_RE.search(link or "")
    url = match.group(1) if match else None
    return url if url and url.startswith(f"{API_ROOT}/") else None  # jamais un autre hôte


Stored = tuple[str, str, float]  # (etag, corps JSON, heure du dernier succès)


class CacheStore(Protocol):
    """Persistance de la dernière réponse par URL (ETag, corps, heure du dernier succès)."""

    def load(self, url: str) -> Stored | None: ...
    def save(self, url: str, etag: str, body: str, fetched_at: float) -> None: ...
    def touch(self, url: str, fetched_at: float) -> None: ...


class DbStore:
    """`CacheStore` SQLite (table `github_cache`) ; une base illisible ne casse jamais la page."""

    def load(self, url: str) -> Stored | None:
        return self._run(lambda conn: db.read_github_cache(conn, url), None)

    def save(self, url: str, etag: str, body: str, fetched_at: float) -> None:
        self._run(lambda conn: db.write_github_cache(conn, url, etag, body, fetched_at), None)

    def touch(self, url: str, fetched_at: float) -> None:
        self._run(lambda conn: db.touch_github_cache(conn, url, fetched_at), None)

    @staticmethod
    def _run[T](action: Callable[[sqlite3.Connection], T], default: T) -> T:
        try:
            with db.connect() as conn:
                return action(conn)
        except (sqlite3.Error, OSError) as exc:
            log.warning("cache GitHub SQLite indisponible : %s", type(exc).__name__)
            return default


@dataclass(frozen=True)
class _Page:
    etag: str
    data: Any
    link: str | None
    at: float


class HttpJson:
    """GET JSON sur api.github.com, sans jeton, avec ETag (304 = données déjà connues).

    Suit `Link: rel="next"` (au plus `MAX_PAGES` pages) ; lève `RateLimited` sur 403/429 de limite.
    Avec un `store`, ETag et pages survivent au redémarrage ; `peek` les relit sans réseau.
    """

    def __init__(
        self, store: CacheStore | None = None, *, wall: Callable[[], float] = time.time
    ) -> None:
        self._known: dict[str, _Page] = {}
        self._store, self._wall = store, wall

    def __call__(self, path: str) -> Any:
        return self._walk(path, self._get)

    def peek(self, path: str) -> tuple[Any, float] | None:
        """Dernier instantané persisté (toutes pages connues) et heure de son plus ancien succès."""
        times: list[float] = []

        def known(url: str) -> tuple[Any, str | None] | None:
            page = self._recall(url)
            if page is None:
                return None
            times.append(page.at)
            return page.data, page.link

        data = self._walk(path, known)
        return (data, min(times)) if times else None

    def _walk(self, path: str, get: Callable[[str], tuple[Any, str | None] | None]) -> Any:
        url: str | None = f"{API_ROOT}{path}"
        pages: list[Any] = []
        while url and len(pages) < MAX_PAGES:
            got = get(url)
            if got is None:
                break
            data, link = got
            if not isinstance(data, list):
                return data
            pages.extend(data)
            url = _next_url(link)
            if url is None:
                break
        return pages

    def _recall(self, url: str) -> _Page | None:
        """Page connue en mémoire, sinon relue (et validée) depuis la persistance."""
        if url in self._known:
            return self._known[url]
        stored = self._store.load(url) if self._store else None
        if stored is None:
            return None
        try:
            body = json.loads(stored[1])
            link = body.get("link")
            link = link if isinstance(link, str) else None
            page = _Page(stored[0], body["data"], link, stored[2])
        except (ValueError, KeyError, AttributeError, TypeError):
            return None  # ligne illisible : traitée comme absente
        self._known[url] = page
        return page

    def _get(self, url: str) -> tuple[Any, str | None]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "vva-dashboard",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        known = self._recall(url)
        if known:
            headers["If-None-Match"] = known.etag
        try:
            # URL https de api.github.com (constante ou lien validé) : pas de schéma file:/.
            with urlopen(Request(url, headers=headers), timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
                raw = resp.read(MAX_BODY_BYTES + 1)
                etag, link = resp.headers.get("ETag"), resp.headers.get("Link")
        except HTTPError as exc:
            if exc.code == 304 and known:
                self._renew(url, known)
                return known.data, known.link
            limited = _rate_limit(exc)
            if limited:
                raise limited from exc
            raise
        if len(raw) > MAX_BODY_BYTES:
            raise ValueError("réponse GitHub trop volumineuse")
        data = json.loads(raw)
        if etag:
            self._remember(url, _Page(etag, data, link, self._wall()))
        return data, link

    def _remember(self, url: str, page: _Page) -> None:
        self._known[url] = page
        if self._store:
            body = json.dumps({"link": page.link, "data": page.data}, ensure_ascii=False)
            self._store.save(url, page.etag, body, page.at)

    def _renew(self, url: str, page: _Page) -> None:
        """304 : mêmes données, heure du succès avancée (en mémoire et en base)."""
        now = self._wall()
        self._known[url] = _Page(page.etag, page.data, page.link, now)
        if self._store:
            self._store.touch(url, now)


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    closed: bool
    completed: bool  # fermé « completed » (≠ « not planned »)
    labels: frozenset[str]
    updated_at: str | None = None
    question: str | None = None  # ligne « Attend l'utilisateur : … » du corps (#179)
    epic: int | None = None  # numéro de l'épique citée dans le corps (#182)
    sprint: str | None = None  # jalon (milestone) ou label « sprint-N » (#182)


@dataclass(frozen=True)
class Pull:
    """PR ouverte (l'API issues les mêle aux tickets) et ticket qu'elle ferme."""

    number: int
    issue: int | None
    title: str = ""
    updated_at: str | None = None


@dataclass(frozen=True)
class Snapshot:
    issues: tuple[Issue, ...] = ()
    pulls: tuple[Pull, ...] = ()


def _items(raw: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("réponse GitHub inattendue")
    return (item for item in raw if isinstance(item, dict))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _labels(item: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        name
        for label in item.get("labels") or []
        if isinstance(label, dict) and (name := _text(label.get("name")))
    )


def _sprint(item: dict[str, Any], labels: frozenset[str]) -> str | None:
    """Sprint lisible : titre du jalon, sinon label `sprint-N` (« Sprint N ») ; pas le Project."""
    milestone = item.get("milestone")
    title = _text(milestone.get("title")) if isinstance(milestone, dict) else None
    if title and title.strip():
        return title.strip()
    numbers = [int(m.group(1)) for name in labels if (m := SPRINT_LABEL_RE.match(name))]
    return f"Sprint {min(numbers)}" if numbers else None


def parse_issues(raw: Any) -> tuple[tuple[Issue, ...], tuple[Pull, ...]]:
    """Sépare tickets et PR ouvertes (`pull_request` présent) d'une même liste `issues`."""
    issues: list[Issue] = []
    pulls: list[Pull] = []
    for item in _items(raw):
        number = item.get("number")
        if not isinstance(number, int):
            continue
        body, title = _text(item.get("body")) or "", _text(item.get("title")) or ""
        updated = _text(item.get("updated_at"))
        if "pull_request" in item:
            if item.get("state") == "open":
                match = CLOSES_RE.search(body) or TITLE_REF_RE.search(title)
                closes = int(match.group(1)) if match else None
                pulls.append(Pull(number, closes, title, updated))
            continue
        clean = FENCE_RE.sub("", body)
        question, epic = QUESTION_RE.search(clean), EPIC_RE.search(clean)
        labels = _labels(item)
        issues.append(
            Issue(
                number,
                title,
                item.get("state") == "closed",
                item.get("state_reason") == "completed",
                labels,
                updated,
                question.group(1).strip()[:QUESTION_MAX] if question else None,
                int(epic.group(1)) if epic else None,
                _sprint(item, labels),
            )
        )
    return tuple(issues), tuple(pulls)


class GithubCache:
    """Cache mémoire : une lecture GitHub par `ttl`, même après un échec (limite 60 req/h).

    Après une limite atteinte, aucune lecture avant la réinitialisation annoncée (plafonnée).
    `restore` (optionnel) relit au premier accès le dernier instantané persisté : la liste reste
    affichée après un redémarrage, même si GitHub refuse. `data_at` = dernier succès (ou 304).
    """

    def __init__(
        self,
        fetch: Callable[[str], Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        ttl: float = CACHE_TTL_S,
        restore: Callable[[str], tuple[Any, float] | None] | None = None,
    ) -> None:
        self._fetch, self._clock, self._wall, self._ttl = fetch, clock, wall, ttl
        self._restore = restore
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._error_at: datetime | None = None
        self._data_at: float | None = None
        self._next_at: float | None = None
        self._wait = ttl

    def get(self) -> tuple[Snapshot, datetime | None]:
        snapshot, error_at, _ = self.read()
        return snapshot, error_at

    def read(self) -> tuple[Snapshot, datetime | None, float | None]:
        """(instantané, heure de l'erreur, epoch du dernier succès ou 304)."""
        with self._lock:
            now = self._clock()
            if self._next_at is None:
                self._load_persisted()
            if self._next_at is None or now >= self._next_at:
                self._wait = self._ttl
                try:
                    self._refresh()
                finally:
                    self._next_at = now + self._wait
            return self._snapshot, self._error_at, self._data_at

    def _load_persisted(self) -> None:
        if self._restore is None:
            return
        try:
            restored = self._restore(self._issues_path())
            if restored:
                issues, pulls = parse_issues(restored[0])
                self._snapshot, self._data_at = Snapshot(issues, pulls), restored[1]
        except Exception as exc:  # donnée persistée illisible : on repart à froid
            log.warning("instantané GitHub persisté illisible : %s", type(exc).__name__)

    @staticmethod
    def _issues_path() -> str:
        return f"/repos/{repo()}/issues?state=all&per_page=100"

    def _refresh(self) -> None:
        try:
            issues, pulls = parse_issues(self._fetch(self._issues_path()))
            self._snapshot = Snapshot(issues, pulls)
            self._error_at = None
            self._data_at = self._wall()
        except RateLimited as exc:
            log.warning("limite GitHub atteinte")
            self._wait = max(self._ttl, min(exc.until - self._wall(), MAX_RATE_WAIT_S))
            self._error_at = datetime.now(UTC)
        except Exception as exc:  # IncompleteRead, HTTPException, JSON… : jamais de 500
            log.warning("lecture GitHub impossible : %s", type(exc).__name__)
            self._error_at = datetime.now(UTC)


_HTTP = HttpJson(DbStore())
CACHE = GithubCache(_HTTP, restore=_HTTP.peek)


# --- Calculs purs -----------------------------------------------------------------------------
def classify(issue: Issue, in_review: set[int]) -> str:
    if issue.closed:  # (les fermés « not planned » sont exclus en amont)
        return "delivered"
    if issue.number in in_review:
        return "review"
    if issue.labels & IN_PROGRESS_LABELS:
        return "in_progress"
    if "blocked" not in issue.labels and issue.labels & READY_LABELS:
        return "ready"
    return "backlog"


def parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

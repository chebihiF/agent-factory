"""Lecture GitHub du tableau de bord (#26, recentrée par #179) : client, cache, analyse.

GitHub est toujours simulé ; aucune requête réseau.
"""

from __future__ import annotations

import http.client
import json
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from dashboard import github


def item(number: int, *labels: str, **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": extra.pop("title", f"Ticket {number}"),
        "state": extra.pop("state", "open"),
        "state_reason": extra.pop("reason", None),
        "body": extra.pop("body", ""),
        "labels": [{"name": name} for name in labels],
        **extra,
    }


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeGithub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: BaseException | None = None
        self.issues: list[dict[str, Any]] = [item(3, state="closed", reason="completed")]

    def __call__(self, path: str) -> Any:
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.issues


@pytest.fixture
def gh() -> FakeGithub:
    return FakeGithub()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(gh: FakeGithub, clock: Clock) -> github.GithubCache:
    return github.GithubCache(gh, clock=clock, wall=clock)


# --- Cache 3 min et échecs (décision D20) ---------------------------------------------------
def test_only_issues_are_read_once_per_ttl(
    cache: github.GithubCache, gh: FakeGithub, clock: Clock
) -> None:
    cache.get()
    cache.get()
    assert gh.calls == ["/repos/chebihiF/viral-video-agents/issues?state=all&per_page=100"]
    clock.now += 120
    cache.get()
    assert len(gh.calls) == 1
    clock.now += 61
    cache.get()
    assert len(gh.calls) == 2


@pytest.mark.parametrize(
    "error", [URLError("réseau coupé"), http.client.IncompleteRead(b""), TimeoutError("délai")]
)
def test_failure_keeps_last_data_and_is_not_retried_before_ttl(
    cache: github.GithubCache, gh: FakeGithub, clock: Clock, error: BaseException
) -> None:
    assert cache.get()[1] is None
    gh.error = error
    clock.now += 181
    snapshot, error_at = cache.get()
    assert error_at is not None and len(snapshot.issues) == 1
    count = len(gh.calls)
    clock.now += 100
    cache.get()
    assert len(gh.calls) == count


def test_failure_without_data_gives_an_empty_snapshot(
    cache: github.GithubCache, gh: FakeGithub
) -> None:
    gh.error = URLError("réseau coupé")
    snapshot, error_at = cache.get()
    assert snapshot == github.Snapshot() and error_at is not None


def test_rate_limit_waits_for_reset_and_keeps_last_data(
    cache: github.GithubCache, gh: FakeGithub, clock: Clock
) -> None:
    cache.get()
    gh.error = github.RateLimited(until=clock.now + 181 + 1000)
    clock.now += 181
    snapshot, error_at = cache.get()
    assert error_at is not None and len(snapshot.issues) == 1
    count = len(gh.calls)
    clock.now += 900  # TTL dépassé mais réinitialisation GitHub pas encore atteinte
    cache.get()
    assert len(gh.calls) == count
    clock.now += 101
    gh.error = None
    assert cache.get()[1] is None
    assert len(gh.calls) > count


def test_rate_limit_wait_is_capped(cache: github.GithubCache, gh: FakeGithub, clock: Clock) -> None:
    gh.error = github.RateLimited(until=10**9)
    cache.get()
    gh.error = None
    clock.now += github.MAX_RATE_WAIT_S + 1
    assert cache.get()[1] is None


# --- Analyse et statuts -----------------------------------------------------------------------
def test_pull_requests_are_split_from_tickets() -> None:
    issues, pulls = github.parse_issues(
        [
            item(5, "P1"),
            item(40, title="feat: vue", body="Closes #5", pull_request={}, updated_at="t"),
            item(41, state="closed", body="Closes #3", pull_request={}),  # fusionnée : ignorée
        ]
    )
    assert [i.number for i in issues] == [5]
    assert pulls == (github.Pull(40, 5, "feat: vue", "t"),)


@pytest.mark.parametrize(
    ("labels", "state", "reason", "in_review", "expected"),
    [
        ((), "closed", "completed", set(), "delivered"),
        (("P1",), "open", None, {7}, "review"),
        (("P1", "in-progress"), "open", None, set(), "in_progress"),
        (("P0",), "open", None, set(), "ready"),
        (("P1", "blocked"), "open", None, set(), "backlog"),
        (("P2",), "open", None, set(), "backlog"),
    ],
)
def test_classify(
    labels: tuple[str, ...], state: str, reason: str | None, in_review: set[int], expected: str
) -> None:
    issues, _ = github.parse_issues([item(7, *labels, state=state, reason=reason)])
    assert github.classify(issues[0], in_review) == expected


def test_arabic_darija_title_is_kept_intact() -> None:
    title = 'شنو الجديد "<b>" ؟'
    issues, _ = github.parse_issues([item(2, title=title)])
    assert issues[0].title == title


# Client HTTP GitHub : sans jeton, conditionnel (ETag), URL fixe
class FakeResponse:
    def __init__(self, payload: Any, etag: str = '"v1"', link: str | None = None) -> None:
        self._raw = json.dumps(payload).encode()
        self.headers = {"ETag": etag, **({"Link": link} if link else {})}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._raw


def test_http_client_sends_no_token_and_reuses_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    answers: list[Any] = [
        FakeResponse([{"a": 1}]),
        HTTPError("u", 304, "Not Modified", Message(), None),
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(req)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[no-any-return]

    monkeypatch.setattr(github, "urlopen", fake_urlopen)
    http = github.HttpJson()
    assert http("/repos/o/r/tags") == [{"a": 1}]
    assert http("/repos/o/r/tags") == [{"a": 1}]  # 304 : ne compte pas dans la limite
    assert seen[0].full_url.startswith("https://api.github.com/repos/o/r/tags")
    assert seen[0].get_header("Authorization") is None
    assert seen[1].get_header("If-none-match") == '"v1"'


def test_http_client_propagates_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(req: urllib.request.Request, timeout: float) -> FakeResponse:
        raise HTTPError("u", 403, "rate limit", Message(), None)

    monkeypatch.setattr(github, "urlopen", refuse)
    with pytest.raises(HTTPError):
        github.HttpJson()("/repos/o/r/issues")


@pytest.mark.parametrize("value", ["", "a/b/c", "../x", "a b/c", "o/r?x=1"])
def test_repo_setting_is_validated(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_GITHUB_REPO", value)
    assert github.repo() == github.DEFAULT_REPO


def test_repo_setting_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_GITHUB_REPO", "octo/cat")
    assert github.repo() == "octo/cat"


def _urlopen_from(
    answers: list[Any], seen: list[urllib.request.Request]
) -> Callable[..., FakeResponse]:
    def fake(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(req)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[no-any-return]

    return fake


def _next(n: int) -> str:
    return f'<https://api.github.com/repos/o/r/issues?page={n}>; rel="next", <x>; rel="last"'


def test_http_client_follows_next_pages_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    answers = [FakeResponse([n], link=_next(n + 1)) for n in range(1, 10)]
    monkeypatch.setattr(github, "urlopen", _urlopen_from(answers, seen))
    assert github.HttpJson()("/repos/o/r/issues?per_page=100") == [1, 2, 3, 4, 5]
    assert len(seen) == github.MAX_PAGES == 5


def test_http_client_never_follows_a_foreign_next_link(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    evil = '<https://evil.example/x>; rel="next"'
    answers = [FakeResponse([1], link=evil), FakeResponse([2])]
    monkeypatch.setattr(github, "urlopen", _urlopen_from(answers, seen))
    assert github.HttpJson()("/repos/o/r/issues") == [1]
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5000"}),
        (429, {"X-RateLimit-Reset": "5000"}),
    ],
)
def test_http_client_reports_rate_limit_reset(
    monkeypatch: pytest.MonkeyPatch, code: int, headers: dict[str, str]
) -> None:
    msg = Message()
    for key, value in headers.items():
        msg[key] = value
    seen: list[urllib.request.Request] = []
    answers: list[Any] = [HTTPError("u", code, "limit", msg, None)]
    monkeypatch.setattr(github, "urlopen", _urlopen_from(answers, seen))
    with pytest.raises(github.RateLimited) as err:
        github.HttpJson()("/repos/o/r/issues")
    assert err.value.until == 5000


def test_plain_403_is_not_a_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Any] = [HTTPError("u", 403, "interdit", Message(), None)]
    monkeypatch.setattr(github, "urlopen", _urlopen_from(answers, []))
    with pytest.raises(HTTPError):
        github.HttpJson()("/repos/o/r/issues")


def test_timeout_is_passed_to_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_timeouts: list[float] = []

    def fake(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen_timeouts.append(timeout)
        return FakeResponse([])

    monkeypatch.setattr(github, "urlopen", fake)
    github.HttpJson()("/repos/o/r/issues")
    assert seen_timeouts == [github.HTTP_TIMEOUT_S] and 0 < github.HTTP_TIMEOUT_S <= 10


# --- Persistance SQLite de l'ETag et de l'instantané (#186) ------------------------------------
ISSUES_URL = "https://api.github.com/repos/o/r/issues?per_page=100"
PATH = "/repos/o/r/issues?per_page=100"


class MemoryStore:
    """Double de `github.DbStore` : (etag, corps JSON, heure du succès) par URL."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, str, float]] = {}

    def load(self, url: str) -> tuple[str, str, float] | None:
        return self.rows.get(url)

    def save(self, url: str, etag: str, body: str, fetched_at: float) -> None:
        self.rows[url] = (etag, body, fetched_at)

    def touch(self, url: str, fetched_at: float) -> None:
        etag, body, _ = self.rows[url]
        self.rows[url] = (etag, body, fetched_at)


def _persisted(store: MemoryStore, at: float = 500.0) -> None:
    body = json.dumps({"link": None, "data": [item(7, "P0")]})
    store.rows[ISSUES_URL] = ('"old"', body, at)


def test_success_is_persisted_with_its_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    monkeypatch.setattr(github, "urlopen", _urlopen_from([FakeResponse([{"a": 1}])], []))
    github.HttpJson(store=store, wall=lambda: 42.0)(PATH)
    etag, body, at = store.rows[ISSUES_URL]
    assert (etag, at) == ('"v1"', 42.0)
    assert json.loads(body)["data"] == [{"a": 1}]


def test_restart_sends_the_persisted_etag_and_reuses_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore()
    _persisted(store)
    seen: list[urllib.request.Request] = []
    answers: list[Any] = [HTTPError("u", 304, "Not Modified", Message(), None)]
    monkeypatch.setattr(github, "urlopen", _urlopen_from(answers, seen))
    http = github.HttpJson(store=store, wall=lambda: 900.0)
    assert http(PATH) == [item(7, "P0")]
    assert seen[0].get_header("If-none-match") == '"old"'
    assert seen[0].get_header("Authorization") is None
    assert store.rows[ISSUES_URL][2] == 900.0  # 304 : l'heure des données avance


def test_peek_rebuilds_all_persisted_pages_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore()
    next_link = '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'
    store.rows[ISSUES_URL] = ('"1"', json.dumps({"link": next_link, "data": [1]}), 10.0)
    store.rows["https://api.github.com/repos/o/r/issues?page=2"] = (
        '"2"',
        json.dumps({"link": None, "data": [2]}),
        8.0,
    )
    monkeypatch.setattr(github, "urlopen", _urlopen_from([], []))  # toute requête ferait échouer
    assert github.HttpJson(store=store).peek(PATH) == ([1, 2], 8.0)
    assert github.HttpJson(store=MemoryStore()).peek(PATH) is None


def test_unreadable_persisted_row_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    store.rows[ISSUES_URL] = ('"x"', "pas du json", 1.0)
    seen: list[urllib.request.Request] = []
    monkeypatch.setattr(github, "urlopen", _urlopen_from([FakeResponse([1])], seen))
    http = github.HttpJson(store=store)
    assert http.peek(PATH) is None
    assert http(PATH) == [1]
    assert seen[0].get_header("If-none-match") is None


def test_restart_shows_persisted_list_when_github_is_rate_limited(
    gh: FakeGithub, clock: Clock
) -> None:
    gh.error = github.RateLimited(until=clock.now + 3000)
    persisted_at = 1_700_000_000.0
    cache = github.GithubCache(
        gh,
        clock=clock,
        wall=clock,
        restore=lambda path: ([item(7, "P0")], persisted_at),
    )
    snapshot, error_at, data_at = cache.read()
    assert [i.number for i in snapshot.issues] == [7]
    assert error_at is not None and data_at == persisted_at
    clock.now += 1000  # avant la réinitialisation : aucune nouvelle requête
    cache.read()
    assert len(gh.calls) == 1


def test_data_time_follows_every_successful_read(gh: FakeGithub, clock: Clock) -> None:
    cache = github.GithubCache(gh, clock=clock, wall=clock, restore=lambda path: ([], 5.0))
    assert cache.read()[2] == clock.now
    gh.error = OSError("réseau")
    clock.now += 181
    assert cache.read()[2] == 1000.0  # échec : l'heure du dernier succès reste affichée


def test_db_store_round_trip_and_no_secret(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path / "vva.db"))
    store = github.DbStore()
    assert store.load(ISSUES_URL) is None
    store.save(ISSUES_URL, '"e"', '{"data": []}', 1.0)
    store.save(ISSUES_URL, '"f"', '{"data": [1]}', 2.0)  # remplace
    store.touch(ISSUES_URL, 3.0)
    assert store.load(ISSUES_URL) == ('"f"', '{"data": [1]}', 3.0)


def test_db_store_never_raises_on_a_broken_database(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path))  # un dossier : base illisible
    store = github.DbStore()
    assert store.load(ISSUES_URL) is None
    store.save(ISSUES_URL, '"e"', "{}", 1.0)
    store.touch(ISSUES_URL, 2.0)

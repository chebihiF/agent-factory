#!/usr/bin/env python3
"""Boucle autonome planifiée (#18) : un ticket « Prêt » par tick, sous budget de tokens.

`tick` (lancé par launchd aux heures de la config) lit les compteurs du jour, les contrôles du
dashboard (#8, lecture seule), choisit le ticket le plus prioritaire, prend un poste du pool
(#14) et y lance une session neuve `/ticket <n>`. Tout contrôle illisible = aucune session.
`install` / `uninstall` gèrent l'agent launchd. Contrat : docs/agents.md et
contracts/agent-loop-state.schema.json. Stdlib ; Python 3.9 (python3 du Mac).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from email.message import Message
from pathlib import Path
from typing import IO, Any, Optional
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT = HERE / "bin" / "agent-loop.sh"
POOL = ROOT / "infra" / "scripts" / "pool.sh"
TEMPLATE = HERE / "launchd" / "agents-loop.plist.template"
DEFAULT_SESSION = "claude -p --output-format json"  # aucun contournement des permissions
LIST_LIMIT = 500
LIST_ARGS = ["issue", "list", "--state", "open", "--limit", str(LIST_LIMIT)] + [
    "--json",
    "number,labels,body,projectItems",
]
READY = "Prêt"
STOP = "stop-agents"
PRIORITIES = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
NO_PRIORITY = len(PRIORITIES)
REQUIRED = frozenset({"day", "tokens", "tickets"})
KEYS = ("hours", "daily_token_budget", "max_tickets_per_day", "control_url")
HOUR = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
COUNT = re.compile(r"^[0-9]{1,13}$")
DEPENDS = re.compile(r"\bd[ée]pend", re.IGNORECASE)
LOCAL_REF = re.compile(r"(?<![\w/])#(\d+)")
FOREIGN_REF = re.compile(r"[\w.-]+/[\w.-]+#\d+")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Cache lu exclu du total : relu à chaque tour, il gonflerait le budget sans consommation neuve.
MODEL_KEYS = ("inputTokens", "outputTokens", "cacheCreationInputTokens")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")
CONTROL_TIMEOUT_S = 5
MAX_CONTROL_BYTES = 65_536
GH_TIMEOUT_S = 60
POOL_TIMEOUT_S = 300
SESSION_TIMEOUT_S = 4 * 3600
LAUNCHCTL_TIMEOUT_S = 30
KILL_WAIT_S = 5
FORBIDDEN_IN_SESSION = ("dangerously-skip-permissions", "bypasspermissions")
DEFAULT_PATH = "/usr/bin:/bin"


class LoopError(Exception):
    """Arrêt attendu : `code` est un mot-clé fixe, jamais une donnée d'entrée."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Config:
    hours: tuple[str, ...]
    daily_token_budget: int
    max_tickets_per_day: int
    control_url: str


@dataclass(frozen=True)
class ControlState:
    paused: bool
    cap_tokens: int
    tokens_24h: int


@dataclass(frozen=True)
class Ticket:
    number: int
    priority: int
    labels: frozenset[str]
    ready: bool
    deps: Optional[tuple[int, ...]]  # noqa: UP045 - évalué en 3.9 ; None = invérifiable


@dataclass(frozen=True)
class DayState:
    day: date
    tokens: int
    tickets: int
    last_ticket: Optional[int] = None  # noqa: UP045 - dernier n tenté ce jour, jamais retenté


# --- configuration ----------------------------------------------------------------------


def parse_config(text: str) -> Config:
    """Sous-ensemble YAML : « clé: valeur » à plat, `hours` en liste [HH:MM, …]."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = "" if raw.lstrip().startswith("#") else raw.split(" #")[0].rstrip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        if line[0].isspace() or not sep or key not in KEYS or key in values or not value.strip():
            raise LoopError("config-invalid", f"ligne {number}")
        values[key] = value.strip()
    missing = [key for key in KEYS if key not in values]
    if missing:
        raise LoopError("config-invalid", f"clé manquante : {missing[0]}")
    return Config(
        hours=_hours(values["hours"]),
        daily_token_budget=_count(values, "daily_token_budget"),
        max_tickets_per_day=_count(values, "max_tickets_per_day"),
        control_url=_url(values["control_url"]),
    )


def _hours(value: str) -> tuple[str, ...]:
    if not (value.startswith("[") and value.endswith("]")):
        raise LoopError("config-invalid", "hours : liste [HH:MM, …] attendue")
    items = [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
    if not items or len(set(items)) != len(items) or not all(HOUR.match(h) for h in items):
        raise LoopError("config-invalid", "hours : HH:MM distinctes attendues")
    return tuple(items)


def _count(values: dict[str, str], key: str) -> int:
    value = values[key]
    if not COUNT.match(value) or int(value) < 1:
        raise LoopError("config-invalid", f"{key} : entier ≥ 1 attendu")
    return int(value)


def url_allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def _url(value: str) -> str:
    if not url_allowed(value):
        raise LoopError("config-invalid", "control_url : https (http sur localhost seulement)")
    return value


def load_config(path: Path) -> Config:
    try:
        return parse_config(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise LoopError("config-invalid", "fichier illisible") from exc


# --- compteurs du jour (compteurs seuls, sans contenu) ----------------------------------


def state_dir() -> Path:
    return Path(os.environ.get("VVA_LOOP_DIR") or Path.home() / ".vva" / "loop")


def _int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise LoopError("state-unreadable")
    return value


def load_state(directory: Path, today: date) -> DayState:
    path = directory / "state.json"
    if not path.exists():
        return DayState(today, 0, 0)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not REQUIRED <= set(data) <= REQUIRED | {"last_ticket"}:
            raise LoopError("state-unreadable")
        day = date.fromisoformat(data["day"])
    except (OSError, ValueError, TypeError) as exc:
        raise LoopError("state-unreadable") from exc
    last = _int(data["last_ticket"]) if "last_ticket" in data else None
    if last == 0:
        raise LoopError("state-unreadable")
    state = DayState(day, _int(data["tokens"]), _int(data["tickets"]), last)
    if day > today:
        raise LoopError("state-unreadable")
    return state if day == today else DayState(today, 0, 0)


def save_state(directory: Path, state: DayState) -> None:
    payload: dict[str, Any] = {
        "day": state.day.isoformat(),
        "tokens": state.tokens,
        "tickets": state.tickets,
    }
    if state.last_ticket is not None:
        payload["last_ticket"] = state.last_ticket
    tmp = directory / "state.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(payload).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, directory / "state.json")


def private_dir(directory: Path) -> None:
    """Dossier d'état en 700, même s'il existait déjà ou si l'umask l'a élargi."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)


@contextlib.contextmanager
def exclusive(directory: Path) -> Iterator[bool]:
    """Verrou du tick : faux si un autre tick (ou sa session) tourne encore."""
    private_dir(directory)
    fd = os.open(directory / "lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


# --- sélection du ticket ----------------------------------------------------------------


def _dependencies(body: str) -> Optional[tuple[int, ...]]:  # noqa: UP045
    """Numéros cités après « Dépend de » ; None si une dépendance est dans un autre dépôt."""
    found: list[int] = []
    for line in body.splitlines():
        match = DEPENDS.search(line)
        if match is None:
            continue
        rest = line[match.end() :]
        if FOREIGN_REF.search(rest):
            return None
        found.extend(int(n) for n in LOCAL_REF.findall(rest))
    return tuple(found)


def _ticket(item: Any) -> Ticket:
    if not isinstance(item, dict) or type(item.get("number")) is not int:
        raise LoopError("gh-unreadable")
    labels = frozenset(_names(item.get("labels")))
    statuses = set(_names(entry.get("status") for entry in _dicts(item.get("projectItems"))))
    body = item.get("body")
    priority = min((PRIORITIES[lab] for lab in labels if lab in PRIORITIES), default=NO_PRIORITY)
    deps = _dependencies(body if isinstance(body, str) else "")
    return Ticket(item["number"], priority, labels, READY in statuses, deps)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _names(values: Any) -> Iterator[str]:
    """Champ `name` texte des objets d'une liste gh (labels, statuts) ; le reste est ignoré."""
    for entry in values if isinstance(values, (list, Iterator)) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str):
            yield name


def candidates(raw: str) -> list[Ticket]:
    """Tickets « Prêt », sans stop-agents, dépendances fermées ; tri P0→P3 puis numéro."""
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise LoopError("gh-unreadable") from exc
    if not isinstance(items, list):
        raise LoopError("gh-unreadable")
    if len(items) >= LIST_LIMIT:
        raise LoopError("gh-truncated")
    tickets = [_ticket(item) for item in items]
    still_open = {t.number for t in tickets}
    chosen = [
        t
        for t in tickets
        if t.ready
        and STOP not in t.labels
        and t.deps is not None
        and not still_open.intersection(t.deps)
    ]
    return sorted(chosen, key=lambda t: (t.priority, t.number))


def gh_cli(args: Sequence[str]) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - gh du poste, arguments fixes
            ["gh", *args],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LoopError("gh-failed") from exc
    return done.stdout


# --- contrôles du dashboard (#8, lecture seule, échec fermé) ----------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Aucune redirection suivie : le jeton ne part jamais vers une autre URL."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def ingest_token() -> str | None:
    """`VVA_INGEST_TOKEN`, sinon fichier `VVA_INGEST_TOKEN_FILE` (600 exigé)."""
    env = os.environ.get("VVA_INGEST_TOKEN", "").strip()
    if env:
        return env
    path = Path(os.environ.get("VVA_INGEST_TOKEN_FILE") or Path.home() / ".vva" / "ingest_token")
    try:
        if path.stat().st_mode & 0o077:
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def parse_control(raw: bytes) -> ControlState | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or type(data.get("paused")) is not bool:
        return None
    cap, used = data.get("cap_tokens"), data.get("tokens_24h")
    if type(cap) is not int or type(used) is not int or cap < 1 or used < 0:
        return None
    return ControlState(data["paused"], cap, used)


def read_control(url: str) -> ControlState | None:
    """État des contrôles, ou None s'il est illisible (URL, jeton, réseau, réponse)."""
    token = ingest_token()
    if not url_allowed(url) or token is None:
        return None
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    try:
        with _OPENER.open(request, timeout=CONTROL_TIMEOUT_S) as response:
            if response.status != 200:
                return None
            return parse_control(response.read(MAX_CONTROL_BYTES))
    except (OSError, ValueError):  # dont HTTPError, URLError, délai
        return None


# --- poste (#14) et session neuve -------------------------------------------------------


def run_pool(n: int, script: Path = POOL) -> tuple[str, Path]:
    """Poste libre du pool : pool.sh imprime « pool : <branche> » puis le chemin du poste."""
    try:
        done = subprocess.run(  # noqa: S603 - script du dépôt, argument numérique
            [str(script), str(n)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=POOL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LoopError("pool-refused") from exc
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or len(lines) < 2 or not lines[-2].startswith("pool : "):
        raise LoopError("pool-refused")
    poste = Path(lines[-1])
    if not (poste.is_absolute() and poste.is_dir()):
        raise LoopError("pool-refused")
    return lines[-2][len("pool : ") :], poste


def ticket_prompt(n: int, branch: str) -> str:
    return (
        f"/ticket {n} (poste déjà attribué par la boucle autonome : branche {branch} ; "
        "ne relance pas make ticket)\n"
    )


def session_command() -> list[str]:
    """Commande de session ; tout contournement des permissions est refusé (échec fermé)."""
    try:
        command = shlex.split(os.environ.get("LOOP_SESSION_CMD") or DEFAULT_SESSION)
    except ValueError as exc:
        raise LoopError("session-cmd-refused", "guillemets non fermés") from exc
    if not command or any(f in part.lower() for part in command for f in FORBIDDEN_IN_SESSION):
        raise LoopError("session-cmd-refused", "contournement des permissions interdit")
    return command


def _sum(usage: Any, keys: Sequence[str]) -> int | None:
    if not isinstance(usage, dict):
        return None
    values = [usage.get(key, 0) for key in keys]
    if any(type(v) is not int or v < 0 for v in values):
        return None
    return sum(int(v) for v in values)


def session_tokens(output: str) -> int | None:
    """Tokens de la session (`modelUsage`, sinon `usage`) ; None si illisible."""
    try:
        data = json.loads(output.strip().splitlines()[-1] if output.strip() else "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    models = data.get("modelUsage")
    if isinstance(models, dict) and models:
        totals = [_sum(usage, MODEL_KEYS) for usage in models.values()]
        return None if None in totals else sum(t for t in totals if t is not None)
    return _sum(data["usage"], USAGE_KEYS) if "usage" in data else None


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """Tue toute l'arborescence de la session (elle est chef de son groupe)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=KILL_WAIT_S)


def run_session(cwd: Path, prompt: str) -> int | None:
    """Session neuve dans le poste, prompt sur stdin ; sa sortie n'est jamais conservée."""
    command = session_command()
    try:
        proc = subprocess.Popen(  # noqa: S603 - commande fixée par le parent (LOOP_SESSION_CMD)
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            text=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return None
    try:
        output, _ = proc.communicate(prompt, timeout=SESSION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return None
    except BaseException:
        _kill_group(proc)
        raise
    return session_tokens(output)


def launchctl_cli(args: Sequence[str]) -> int:
    try:
        return subprocess.run(  # noqa: S603 - launchctl, arguments fixes
            ["launchctl", *args],  # noqa: S607
            capture_output=True,
            timeout=LAUNCHCTL_TIMEOUT_S,
            check=False,
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


# Alias évalués à l'exécution : `X | None` plante en 3.9, d'où Optional.
Gh = Callable[[Sequence[str]], str]
Control = Callable[[str], Optional[ControlState]]  # noqa: UP045
Pool = Callable[[int], tuple[str, Path]]
Session = Callable[[Path, str], Optional[int]]  # noqa: UP045
Launchctl = Callable[[Sequence[str]], int]
Clock = Callable[[], date]


@dataclass
class Deps:
    gh: Gh = field(default=gh_cli)
    control: Control = field(default=read_control)
    pool: Pool = field(default=run_pool)
    session: Session = field(default=run_session)
    launchctl: Launchctl = field(default=launchctl_cli)


# --- tick -------------------------------------------------------------------------------


def _blocked(config: Config, state: DayState, deps: Deps) -> str | None:
    """Motif d'arrêt avant tout ticket, dans l'ordre : budget local, puis contrôles (#8)."""
    if state.tokens >= config.daily_token_budget:
        return "budget-exhausted"
    if state.tickets >= config.max_tickets_per_day:
        return "max-tickets"
    control = deps.control(config.control_url)
    if control is None:
        return "control-unreadable"
    if control.paused:
        return "paused"
    if control.tokens_24h >= control.cap_tokens:
        return "cap-reached"
    return None


def _run(
    config: Config,
    deps: Deps,
    directory: Path,
    today: date,
    clock: Optional[Clock],  # noqa: UP045
) -> str:
    session_command()  # refus des contournements avant tout poste ou compteur
    state = load_state(directory, today)
    blocked = _blocked(config, state, deps)
    if blocked:
        return blocked
    # Un ticket déjà tenté ce jour (échec resté « Prêt ») n'est pas retenté avant le lendemain.
    tickets = [t for t in candidates(deps.gh(LIST_ARGS)) if t.number != state.last_ticket]
    if not tickets:
        return "no-ticket"
    n = tickets[0].number
    branch, poste = deps.pool(n)
    state = replace(state, tickets=state.tickets + 1, last_ticket=n)
    save_state(directory, state)
    used = deps.session(poste, ticket_prompt(n, branch))
    end = clock() if clock else today
    if end > today:  # session finie après minuit : ses tokens comptent sur le nouveau jour
        state = DayState(end, 0, 0)
    # Consommation inconnue : on la suppose égale au budget (échec fermé jusqu'au lendemain).
    tokens = state.tokens + used if used is not None else config.daily_token_budget
    save_state(directory, replace(state, tokens=max(tokens, state.tokens)))
    return f"ran ticket={n} tokens={'unknown' if used is None else used}"


def tick(
    config: Config,
    deps: Deps,
    directory: Path,
    today: date,
    clock: Optional[Clock] = None,  # noqa: UP045 - date du jour à la fin de la session
) -> str:
    """Un tick planifié : au plus une session ; renvoie un code d'une ligne, sans contenu."""
    with exclusive(directory) as held:
        if not held:
            return "busy"
        try:
            return _run(config, deps, directory, today, clock)
        except LoopError as exc:
            return exc.code


# --- launchd ----------------------------------------------------------------------------


def label() -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", ROOT.name.lower()).strip("-") or "projet"
    return f"agent-factory.loop.{slug}"


def _calendar(hours: Sequence[str]) -> str:
    entries = []
    for hour in hours:
        hh, mm = (int(part) for part in hour.split(":"))
        entries.append(
            f"    <dict><key>Hour</key><integer>{hh}</integer>"
            f"<key>Minute</key><integer>{mm}</integer></dict>"
        )
    return "\n".join(entries)


def render_plist(config: Config, config_path: Path, path_env: str, log: Path) -> str:
    values = {
        "@LABEL@": label(),
        "@SCRIPT@": str(SCRIPT),
        "@CONFIG@": str(config_path),
        "@ROOT@": str(ROOT),
        "@PATH@": path_env,
        "@LOG@": str(log),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in values.items():
        text = text.replace(placeholder, escape(value))
    return text.replace("@CALENDAR@", _calendar(config.hours))


def absolute_path(raw: str) -> str:
    """PATH du plist : entrées absolues seulement (jamais « . » ni chemin relatif)."""
    entries = [e for e in dict.fromkeys(raw.split(":")) if e.startswith("/")]
    return ":".join(entries) or DEFAULT_PATH


def _plist_path() -> Path:
    agents = os.environ.get("VVA_LAUNCH_AGENTS") or Path.home() / "Library" / "LaunchAgents"
    return Path(agents) / f"{label()}.plist"


def install(config_path: Path, deps: Deps) -> None:
    config = load_config(config_path)
    directory = state_dir()
    private_dir(directory)
    path_env = absolute_path(os.environ.get("PATH", ""))
    plist = render_plist(config, config_path.resolve(), path_env, directory / "loop.log")
    target = _plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plist, encoding="utf-8")
    domain = f"gui/{os.getuid()}"
    deps.launchctl(["bootout", f"{domain}/{label()}"])  # absent : sans effet
    if deps.launchctl(["bootstrap", domain, str(target)]) != 0:
        raise LoopError("launchctl-failed")
    print(f"boucle installée : {label()}, {len(config.hours)} heure(s) par jour")


def uninstall(deps: Deps) -> None:
    deps.launchctl(["bootout", f"gui/{os.getuid()}/{label()}"])
    _plist_path().unlink(missing_ok=True)
    print(f"boucle désinstallée : {label()}")


# --- ligne de commande ------------------------------------------------------------------


def _default_config() -> Path:
    return Path(os.environ.get("VVA_LOOP_CONFIG") or Path.home() / ".vva" / "loop.yml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-loop", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("tick", "un tick planifié"), ("install", "planifier (launchd)")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--config", type=Path, default=None)
    sub.add_parser("uninstall", help="retirer la planification")
    return parser


def _tick_cli(config_path: Path, deps: Deps) -> int:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        config = load_config(config_path)
    except LoopError as exc:
        print(f"{stamp} {exc.code}")
        print(f"agent-loop : {exc.detail}", file=sys.stderr)
        return 1
    print(f"{stamp} {tick(config, deps, state_dir(), date.today(), date.today)}")
    return 0


def main(argv: Sequence[str] | None = None, *, deps: Deps | None = None) -> int:
    args = _parser().parse_args(argv)
    deps = deps or Deps()
    config_path = getattr(args, "config", None) or _default_config()
    if args.command == "tick":
        return _tick_cli(config_path, deps)
    try:
        if args.command == "install":
            install(config_path, deps)
        else:
            uninstall(deps)
    except LoopError as exc:
        print(f"agent-loop : {exc.code} {exc.detail}".rstrip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

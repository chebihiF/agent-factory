"""Émetteur de hooks : stdin JSON -> ligne JSONL minimale (#4)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from dashboard import ingest

SCRIPT = Path(__file__).resolve().parents[1] / "hook_emit.py"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "agent-event.schema.json"
SENSITIVE = "contenu-sensible-XYZ"


def run_hook(
    payload: str, tmp_path: Path, target: Path | None = None, **extra_env: str
) -> tuple[subprocess.CompletedProcess[str], float]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VVA_")}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(extra_env)
    if target is not None:
        env["VVA_TELEMETRY_FILE"] = str(target)
    start = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - interpréteur courant, script du dépôt
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=5,
        check=False,
    )
    return proc, time.monotonic() - start


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def subagent_start() -> dict[str, Any]:
    return {
        "hook_event_name": "SubagentStart",
        "session_id": "sess-1",
        "agent_type": "dev-backend",
        "agent_id": "agent-42",
        "transcript_path": "/home/x/.claude/projects/t.jsonl",
        "cwd": "/home/x/dev",
    }


def post_tool_use() -> dict[str, Any]:
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-1",
        "agent_id": "agent-42",
        "agent_type": "dev-backend",
        "tool_name": "Bash",
        "prompt": SENSITIVE,
        "tool_input": {"command": f"echo {SENSITIVE}"},
        "tool_response": {"stdout": SENSITIVE},
        "tool_use_id": "toolu_1",
    }


# Scénario : hook écrit un événement
def test_subagent_start_appends_line(tmp_path: Path) -> None:
    target = tmp_path / "t" / "events.jsonl"
    proc, _ = run_hook(json.dumps(subagent_start()), tmp_path, target)
    assert proc.returncode == 0
    [line] = lines(target)
    assert line["hook_event_name"] == "SubagentStart"
    assert line["session_id"] == "sess-1"
    assert line["agent_type"] == "dev-backend"
    assert line["agent_id"] == "agent-42"
    assert line["tool_name"] is None
    assert line["ts"].endswith("+00:00")
    uuid.UUID(line["event_id"])


def test_default_path_is_claude_telemetry(tmp_path: Path) -> None:
    proc, _ = run_hook(json.dumps(subagent_start()), tmp_path)
    assert proc.returncode == 0
    assert len(lines(tmp_path / ".claude" / "telemetry" / "events.jsonl")) == 1


def test_lines_are_appended_with_unique_ids(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    for _ in range(3):
        run_hook(json.dumps(subagent_start()), tmp_path, target)
    ids = [line["event_id"] for line in lines(target)]
    assert len(set(ids)) == 3


# Scénario : aucun contenu sensible
def test_post_tool_use_has_no_sensitive_content(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    run_hook(json.dumps(post_tool_use()), tmp_path, target)
    raw = target.read_text(encoding="utf-8")
    assert SENSITIVE not in raw
    [line] = lines(target)
    assert line["tool_name"] == "Bash"
    assert set(line) == {
        "event_id",
        "ts",
        "hook_event_name",
        "session_id",
        "agent_type",
        "agent_id",
        "tool_name",
    }  # ni issue ni model : omis quand inconnus (serveur antérieur au #181 : pas de 422)


# Contrat : la ligne est conforme à contracts/agent-event.schema.json
@pytest.mark.parametrize("payload", [subagent_start(), post_tool_use()])
def test_line_matches_contract(tmp_path: Path, payload: dict[str, Any]) -> None:
    target = tmp_path / "events.jsonl"
    run_hook(json.dumps(payload), tmp_path, target)
    [line] = lines(target)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert set(line) <= set(schema["properties"])
    assert set(schema["required"]) <= set(line)
    ingest.AgentEvent.model_validate_json(json.dumps(line))


def test_overlong_values_are_truncated_to_contract(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    payload = subagent_start() | {"agent_type": "a" * 500, "session_id": "s" * 500}
    run_hook(json.dumps(payload), tmp_path, target)
    [line] = lines(target)
    ingest.AgentEvent.model_validate_json(json.dumps(line))


def test_non_string_values_become_null(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    run_hook(json.dumps(subagent_start() | {"agent_id": {"x": SENSITIVE}}), tmp_path, target)
    [line] = lines(target)
    assert line["agent_id"] is None


@pytest.mark.parametrize(
    "payload",
    ["", "pas du json", "[1, 2]", json.dumps({"hook_event_name": "X"})],
)
def test_invalid_input_writes_nothing_and_exits_0(tmp_path: Path, payload: str) -> None:
    target = tmp_path / "events.jsonl"
    proc, _ = run_hook(payload, tmp_path, target)
    assert proc.returncode == 0
    assert not target.exists()


# Scénario : le hook ne bloque jamais
def test_write_failure_exits_0_fast(tmp_path: Path) -> None:
    blocker = tmp_path / "fichier"
    blocker.write_text("x", encoding="utf-8")
    proc, elapsed = run_hook(json.dumps(subagent_start()), tmp_path, blocker / "events.jsonl")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert elapsed < 1.0


def test_stdin_never_closed_exits_0_under_one_second(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VVA_")}
    env["VVA_TELEMETRY_FILE"] = str(tmp_path / "events.jsonl")
    start = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - interpréteur courant, script du dépôt
        [sys.executable, str(SCRIPT)], stdin=subprocess.PIPE, env=env, cwd=tmp_path
    )
    try:
        code = proc.wait(timeout=3)
    finally:
        proc.kill()
        assert proc.stdin is not None
        proc.stdin.close()
    assert code == 0
    assert time.monotonic() - start < 1.5


def test_darija_arabic_values_roundtrip_utf8(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    payload = subagent_start() | {"agent_type": "وكيل-مطوّر", "session_id": "جلسة-1"}
    proc, _ = run_hook(json.dumps(payload, ensure_ascii=False), tmp_path, target)
    assert proc.returncode == 0
    [line] = lines(target)
    assert line["agent_type"] == "وكيل-مطوّر"
    assert line["session_id"] == "جلسة-1"
    ingest.AgentEvent.model_validate_json(json.dumps(line))


def test_concurrent_hooks_write_whole_lines(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    env = {k: v for k, v in os.environ.items() if not k.startswith("VVA_")}
    env["VVA_TELEMETRY_FILE"] = str(target)
    procs = [
        subprocess.Popen(  # noqa: S603 - interpréteur courant, script du dépôt
            [sys.executable, str(SCRIPT)], stdin=subprocess.PIPE, text=True, env=env
        )
        for _ in range(8)
    ]
    for proc in procs:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(subagent_start()))
        proc.stdin.close()
    assert [p.wait(timeout=5) for p in procs] == [0] * 8
    assert len({line["event_id"] for line in lines(target)}) == 8


# Revue : stdin lu en entier (pas de troncature du JSON au-delà de 1 Mio)
def test_large_payload_is_read_entirely(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    payload = post_tool_use() | {"tool_response": {"stdout": "x" * (3 << 20)}}
    proc, _ = run_hook(json.dumps(payload), tmp_path, target)
    assert proc.returncode == 0
    [line] = lines(target)
    assert line["tool_name"] == "Bash"


# #68 : projet émetteur (VVA_PROJECT) ; absent => champ omis, l'ingestion applique le défaut
def test_project_from_env_is_written(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    run_hook(json.dumps(subagent_start()), tmp_path, target, VVA_PROJECT="agent-factory")
    [line] = lines(target)
    assert line["project"] == "agent-factory"
    assert ingest.AgentEvent.model_validate_json(json.dumps(line)).project == "agent-factory"


@pytest.mark.parametrize("value", ["", "a b", "../x", "a" * 101])
def test_invalid_project_is_omitted(tmp_path: Path, value: str) -> None:
    target = tmp_path / "events.jsonl"
    run_hook(json.dumps(subagent_start()), tmp_path, target, VVA_PROJECT=value)
    [line] = lines(target)
    assert "project" not in line
    assert ingest.AgentEvent.model_validate_json(json.dumps(line)).project == "viral-video-agents"


# #68 : le gabarit des hooks de A déclare son projet
def test_settings_template_sets_project() -> None:
    template = SCHEMA_PATH.parents[1] / "hooks" / "settings.example.json"
    env = json.loads(template.read_text(encoding="utf-8"))["env"]
    assert env["VVA_PROJECT"] == "agent-factory"
    payload = {"event_id": "e", "ts": "2026-11-20T10:00:00+00:00", "hook_event_name": "Stop"}
    event = payload | {"session_id": "s", "project": env["VVA_PROJECT"]}
    assert ingest.AgentEvent.model_validate_json(json.dumps(event)).project == "agent-factory"


# #181 : ticket déduit de la branche (ou du worktree), modèle lu dans le hook
def fake_repo(root: Path, head: str) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head, encoding="utf-8")
    return root


def fake_worktree(root: Path, head: str, relative: bool = False) -> Path:
    gitdir = root / "main" / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text(head, encoding="utf-8")
    tree = root / "wt"
    tree.mkdir()
    pointer = Path("..", "main", ".git", "worktrees", "wt") if relative else gitdir
    (tree / ".git").write_text(f"gitdir: {pointer}\n", encoding="utf-8")
    return tree


def emitted(tmp_path: Path, cwd: Path, **hook: Any) -> dict[str, Any]:
    target = tmp_path / "events.jsonl"
    payload = subagent_start() | {"cwd": str(cwd)} | hook
    proc, _ = run_hook(json.dumps(payload), tmp_path, target)
    assert proc.returncode == 0
    [line] = lines(target)
    ingest.AgentEvent.model_validate_json(json.dumps(line))
    return line


def test_issue_from_feature_branch(tmp_path: Path) -> None:
    repo = fake_repo(tmp_path / "repo", "ref: refs/heads/feat/42-xyz\n")
    assert emitted(tmp_path, repo)["issue"] == 42


def test_issue_from_subdirectory_of_repo(tmp_path: Path) -> None:
    repo = fake_repo(tmp_path / "repo", "ref: refs/heads/fix/7-bug\n")
    sub = repo / "apps" / "x"
    sub.mkdir(parents=True)
    assert emitted(tmp_path, sub)["issue"] == 7


@pytest.mark.parametrize("relative", [False, True])
def test_issue_from_worktree(tmp_path: Path, relative: bool) -> None:
    tree = fake_worktree(tmp_path, "ref: refs/heads/feat/181-lien\n", relative)
    assert emitted(tmp_path, tree)["issue"] == 181


def test_issue_from_real_git_branch(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git absent")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "-q", "-b", "feat/42-xyz", str(repo)], check=True)  # noqa: S603
    assert emitted(tmp_path, repo)["issue"] == 42


@pytest.mark.parametrize(
    "head",
    [
        "ref: refs/heads/main\n",
        "ref: refs/heads/feat/extraction-initiale\n",
        "ref: refs/heads/feat/0-zero\n",
        "ref: refs/heads/feat/42x-collé\n",
        "ref: refs/heads/feat/12345678901-trop-long\n",
        "0123456789abcdef0123456789abcdef01234567\n",  # HEAD détachée
        "",
    ],
)
def test_branch_without_number_gives_null_issue(tmp_path: Path, head: str) -> None:
    repo = fake_repo(tmp_path / "repo", head)
    assert "issue" not in emitted(tmp_path, repo)


def test_no_repository_gives_null_issue(tmp_path: Path) -> None:
    assert "issue" not in emitted(tmp_path, tmp_path / "absent")


def test_issue_falls_back_to_project_dir(tmp_path: Path) -> None:
    repo = fake_repo(tmp_path / "repo", "ref: refs/heads/feat/42-xyz\n")
    target = tmp_path / "events.jsonl"
    payload = {k: v for k, v in subagent_start().items() if k != "cwd"}
    run_hook(json.dumps(payload), tmp_path, target, CLAUDE_PROJECT_DIR=str(repo))
    [line] = lines(target)
    assert line["issue"] == 42


def test_model_from_hook(tmp_path: Path) -> None:
    line = emitted(tmp_path, tmp_path, model="claude-opus-5-5")
    assert line["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("model", [None, "", 42, {"id": SENSITIVE}])
def test_missing_or_invalid_model_is_null(tmp_path: Path, model: object) -> None:
    line = emitted(tmp_path, tmp_path, model=model)
    assert "model" not in line
    assert SENSITIVE not in json.dumps(line)


def test_overlong_model_is_truncated(tmp_path: Path) -> None:
    assert emitted(tmp_path, tmp_path, model="m" * 500)["model"] == "m" * 128

"""Non-régression (#210) : hooks/settings.example.json n'appelle plus l'ancien log_event.sh."""

from __future__ import annotations

import json
import re
from pathlib import Path

import check_hooks

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "hooks" / "settings.example.json"


def commands() -> dict[str, list[str]]:
    return check_hooks.commands_by_event(json.loads(TEMPLATE.read_text(encoding="utf-8")))


def test_gabarit_conforme_au_cablage_de_la_telemetrie() -> None:
    assert check_hooks.hook_problems(commands()) == []
    assert any("hook_emit.py" in c for c in commands()["SubagentStart"])
    assert any("ship.py" in c and "--background" in c for c in commands()["Stop"])


def test_gabarit_ne_cite_que_des_scripts_existants() -> None:
    text = "\n".join(c for found in commands().values() for c in found)
    installed = re.findall(r"\.claude/(hooks/[\w.-]+)", text)  # copiés par install-agents
    tools = re.findall(r"(tools/[\w./-]+\.py)", text)
    assert installed and tools
    assert [p for p in installed + tools if not (ROOT / p).is_file()] == []

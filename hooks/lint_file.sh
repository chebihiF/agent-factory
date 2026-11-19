#!/usr/bin/env bash
# PostToolUse(Edit|Write) : corrige puis vérifie le fichier Python modifié. Silencieux si OK.
f="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')"
case "$f" in *.py) ;; *) exit 0 ;; esac
cd "$CLAUDE_PROJECT_DIR" || exit 0
uv run -q ruff check -q --fix "$f" >/dev/null 2>&1
uv run -q ruff format -q "$f" >/dev/null 2>&1
out="$(uv run -q ruff check -q --output-format=concise "$f" 2>&1)" || { echo "$out" >&2; exit 2; }
exit 0

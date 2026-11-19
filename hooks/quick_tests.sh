#!/usr/bin/env bash
# Stop : lint + tests rapides ; renvoie les échecs à Claude (exit 2), une seule relance.
active="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))')"
[ "$active" = "True" ] && exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
git diff --quiet HEAD -- '*.py' 2>/dev/null && [ -z "$(git ls-files -o --exclude-standard -- '*.py')" ] && exit 0
out="$(make -s lint test 2>&1)" || { echo "$out" | tail -30 >&2; exit 2; }
exit 0

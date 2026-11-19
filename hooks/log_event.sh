#!/usr/bin/env bash
# SubagentStart/SubagentStop/PostToolUse : journal JSONL local, source du dashboard (Sprint 1).
mkdir -p "$CLAUDE_PROJECT_DIR/.claude/telemetry"
python3 -c '
import json, sys, time
e = json.load(sys.stdin)
keep = {k: e.get(k) for k in ("hook_event_name", "session_id", "agent_type", "agent_id", "tool_name")}
keep["ts"] = time.time()
print(json.dumps({k: v for k, v in keep.items() if v is not None}))
' >> "$CLAUDE_PROJECT_DIR/.claude/telemetry/events.jsonl"
exit 0

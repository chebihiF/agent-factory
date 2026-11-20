#!/bin/sh
# Boucle autonome planifiée (#18). Voir docs/agents.md.
# tick [--config F] (lancé par launchd) | install [--config F] | uninstall.
exec python3 "$(dirname "$0")/../agent_loop.py" "$@"

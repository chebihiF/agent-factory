#!/bin/sh
# Plafond de tokens par ticket : alerte au dépassement (#188). Voir rules/token-budget.md.
# Ex. : ceiling.sh --db vva.db --repo OWNER/REPO [--dry-run]
exec python3 "$(dirname "$0")/../ceiling.py" "$@"

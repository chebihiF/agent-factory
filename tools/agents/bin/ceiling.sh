#!/bin/sh
# Plafond de tokens par ticket : alerte au dépassement (#188). Voir rules/token-budget.md.
# Ex. : ceiling.sh --db vva.db --repo OWNER/REPO [--dry-run]
# Planification launchd (#210) : ceiling.sh install --repo OWNER/REPO | uninstall.
here=$(dirname "$0")
case "${1-}" in
  install | uninstall) exec python3 "$here/../ceiling_schedule.py" "$@" ;;
esac
# Jeton du bot lu à chaque exécution : jamais écrit, jamais en argument. Il remplace tout jeton
# hérité, pour qu'aucun commentaire ne parte sous un autre compte. Absent : aucun appel à GitHub.
bot=infinitySoftMA
if ! token=$(unset GH_TOKEN GITHUB_TOKEN && gh auth token -u "$bot" 2>/dev/null) || [ -z "$token" ]; then
  echo "ceiling : jeton du compte bot $bot indisponible (gh absent, compte non connecté ou trousseau verrouillé) ; rien n'est écrit sur GitHub" >&2
  exit 1
fi
GH_TOKEN=$token exec python3 "$here/../ceiling.py" "$@"

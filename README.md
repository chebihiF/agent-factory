# agent-factory

Usine d'agents : rôles et règles des agents, skills, hooks, télémétrie et dashboard de pilotage
(activité, tokens, avancement, pause). Zéro frais récurrent, 100 % open source.
Décision de séparation : `docs/adr/ADR-004-separation-usine-produit.md`.

- Démarrer : `make sync && make check && make run`
- Carte du dépôt : `docs/MAP.md` · Fonctionnement Scrum : `docs/SCRUM.md`
- Agents, skills, hooks : `agents/`, `skills/`, `hooks/`, `rules/` (génériques ; chaque projet
  consommateur les copie dans son `.claude/` local et ajoute sa surcouche, jamais commitée)
- Prod : URL dans la variable de dépôt `DASHBOARD_URL` (déployée uniquement par le pipeline) ·
  Mot de passe de connexion : `infra/scripts/set-dashboard-password.sh`

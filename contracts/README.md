# contracts

Contrats versionnés entre les agents (hooks, télémétrie) et le dashboard.

| Contrat | Rôle |
|---|---|
| [`agent-event.schema.json`](agent-event.schema.json) | Événement de hook d'agent envoyé au dashboard (métadonnées uniquement) |
| [`ingest-api.md`](ingest-api.md) | `POST /api/v1/events` : ingestion des événements, authentification par jeton |
| [`token-usage.md`](token-usage.md) | `POST /v1/metrics` : compteurs de tokens OTLP/HTTP JSON |
| [`control-api.md`](control-api.md) | Pause des agents et plafond de tokens |
| [`dashboard-routes.md`](dashboard-routes.md) | Pages et configuration du dashboard |
| [`tickets.schema.json`](tickets.schema.json) | Ligne de la liste des tickets du dashboard (titre, statut, sprint, épique, PR) |
| [`agents-journal.schema.json`](agents-journal.schema.json) | Ligne du journal local des agents (session vers ticket) |
| [`agent-loop-state.schema.json`](agent-loop-state.schema.json) | Compteurs du jour de la boucle autonome (budget de tokens, tickets) |

# contracts

Contrats versionnés entre les agents (hooks, télémétrie) et le dashboard.

| Contrat | Rôle |
|---|---|
| [`agent-event.schema.json`](agent-event.schema.json) | Événement de hook d'agent envoyé au dashboard (métadonnées uniquement) |
| [`ingest-api.md`](ingest-api.md) | `POST /api/v1/events` : ingestion des événements, authentification par jeton |
| [`token-usage.md`](token-usage.md) | `POST /v1/metrics` : compteurs de tokens OTLP/HTTP JSON |
| [`control-api.md`](control-api.md) | Pause des agents et plafond de tokens |
| [`dashboard-routes.md`](dashboard-routes.md) | Pages et configuration du dashboard |
| [`progress.schema.json`](progress.schema.json) | Réponse de `GET /api/v1/progress` : avancement du projet et des agents |
| [`agents-journal.schema.json`](agents-journal.schema.json) | Ligne du journal local des agents (session vers ticket) |

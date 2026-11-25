# Plafond de tokens par ticket (#188)

`GH_TOKEN=$(gh auth token -u infinitySoftMA) tools/agents/bin/ceiling.sh --repo <OWNER/REPO> [--db <base>] [--journal <fichier>] [--dry-run]`.
`--db` et `--journal` ont les défauts de `token-report.sh` (`VVA_DB_PATH` puis `./vva.db` ; `VVA_AGENTS_JOURNAL`).
Règle : [`rules/token-budget.md`](../rules/token-budget.md).

## Entrée

- Total de tokens par numéro de ticket : table `token_usage` ([contrat](token-usage.md)) en
  lecture seule, session rattachée au ticket par le journal local
  ([contrat](agents-journal.schema.json)), comme `token-report.sh`. Les tokens sans ticket sont
  ignorés.
- Label du ticket, lu par `gh issue view` : `complex` ou non.
- Le journal est propre au dépôt : il ne mémorise pas le dépôt d'un ticket, donc `--repo` doit être
  le dépôt dont les tickets alimentent ce journal, sinon les numéros sont lus dans le mauvais dépôt.

## Plafonds

| Ticket | Plafond (tokens) |
|---|---|
| simple | 150 000 |
| `complex` | 300 000 |

Le plafond est dépassé quand le total est strictement supérieur. `gh` n'est appelé que pour un
ticket au-dessus de 150 000.

## Sortie

- Un commentaire par ticket et par plafond, qui commence par `<!-- plafond-tokens:150000 -->`
  (simple) ou `<!-- plafond-tokens:300000 -->` (`complex`) et indique le total et le plafond. Si le
  commentaire existe déjà, rien n'est reposté.
- Sur stdout, une ligne par ticket dépassant le plafond (`#42 160 000 > plafond 150 000 : alerte
  postée`), ou `aucun dépassement de plafond`. Un ticket illisible (gh KO) est signalé sans
  arrêter les autres.
- Le commentaire ne contient que des compteurs : aucune donnée d'enfant, aucun secret, aucun
  identifiant de session. Aucun service payant. Alerte seulement, aucun blocage.

Compte GitHub : le commentaire part sous le compte du `gh` actif ; le forcer par `GH_TOKEN` comme
ci-dessus (bot `infinitySoftMA`). Un échec de `gh issue comment` est signalé par « alerte NON postée »
(distinct d'un ticket illisible).

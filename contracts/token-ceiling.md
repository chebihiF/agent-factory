# Plafond de tokens par ticket (#188)

`tools/agents/bin/ceiling.sh --db <base> --repo <OWNER/REPO> [--journal <fichier>] [--dry-run]`.
Règle : [`rules/token-budget.md`](../rules/token-budget.md).

## Entrée

- Total de tokens par numéro de ticket : table `token_usage` ([contrat](token-usage.md)) en
  lecture seule, session rattachée au ticket par le journal local
  ([contrat](agents-journal.schema.json)), comme `token-report.sh`. Les tokens sans ticket sont
  ignorés.
- Label du ticket, lu par `gh issue view` : `complex` ou non.

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

Compte GitHub : celui du `gh` actif ; le projet impose le sien via `GH_TOKEN` s'il le faut.

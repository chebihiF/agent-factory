# Agents : mesure des tokens et routage des modèles (#19)

Outils : `tools/agents/bin/token-report.sh` (enveloppe) et `tools/agents/token_report.py`
(stdlib, Python 3.9 du Mac comme 3.12). Règles : `tools/agents/config/model-routing.yml`.

## Rapport (≤ 15 lignes, compteurs seuls)

```sh
tools/agents/bin/token-report.sh --db vva.db          # = token-report.sh report --db vva.db
```

Sortie : total, une ligne par ticket (7 plus gros, puis « autres tickets », puis « sans
ticket ») avec la répartition par agent, totaux par agent et par modèle, ratio d'échecs
d'outils par ticket, tickets au-dessus de 2× la médiane, promotions vers Opus. Jamais de
prompt, de contenu ni d'identifiant de session ; un nom d'agent ou de modèle hors
`[A-Za-z0-9._:-]{1,64}` est affiché `?`.

### Durée par ticket (#78)

Chaque ligne de ticket se termine par `· <durée>, CI <part> %[, pauses <durée>]`, par exemple
`#42 1 000 : dev-backend 700 · 3 h 10, CI 25 %, pauses 1 h 20`.

- **Durée** : du premier au dernier événement de télémétrie (table `events`, tous types) du
  ticket, rattaché par session comme les tokens. C'est une approximation de « En cours -> Livré »
  (la carte du Project n'est pas lue). Un ticket avec événements mais sans tokens est listé (`0`).
- **Pauses** : somme des silences de plus de 30 minutes entre deux événements du ticket (quota
  atteint ou attente humaine : la télémétrie ne les distingue pas).
- **Attente CI** : union des intervalles des exécutions (`startedAt` -> `updatedAt`, CI et
  release en parallèle comptés une fois) des branches `<type>/<n>-…`, coupée à la période du
  ticket (premier -> dernier événement), rapportée à sa durée. Sans source : `CI n/d`. Seuls le
  numéro de ticket et les durées sont retenus, jamais le nom de branche ni de contenu.
- **Limite connue** : le journal ne mémorise pas le dépôt d'un ticket. `--ci-repo` doit donc
  être le dépôt des tickets suivis ; si deux dépôts ont des numéros qui se chevauchent, seule la
  période du ticket écarte les exécutions de l'autre. Les tickets absents du journal sont ignorés.
- `gh run list` est limité à 200 exécutions : si la liste est pleine, un avertissement sur
  stderr signale que l'attente CI des tickets anciens peut être sous-estimée.

```sh
tools/agents/bin/token-report.sh --db vva.db --ci-repo OWNER/REPO   # `gh run list` (réseau)
gh run list -R OWNER/REPO --limit 200 --json headBranch,startedAt,updatedAt > runs.json
tools/agents/bin/token-report.sh --db vva.db --ci-json runs.json    # hors ligne
```

Le rapport reste local et sans réseau par défaut ; `gh` n'est appelé qu'avec `--ci-repo`
(échec de `gh` : `CI n/d`, jamais d'erreur).

### Sources

| Donnée | Source | Note |
|--------|--------|------|
| tokens par session, agent, modèle | table `token_usage` ([contrat](../contracts/token-usage.md)) | `agent_id` NULL = fil principal |
| durée et pauses par ticket | table `events` (horodatages seuls) | voir « Durée par ticket » |
| attente CI par ticket | `gh run list --json headBranch,startedAt,updatedAt` | facultatif, `--ci-repo` ou `--ci-json` |
| appels et échecs d'outils | table `events` ([contrat](../contracts/agent-event.schema.json)) | `PostToolUse`, `PostToolUseFailure` |
| session -> ticket, échecs notés, promotions | journal local `~/.vva/agents-journal.jsonl` ([contrat](../contracts/agents-journal.schema.json)) | `VVA_AGENTS_JOURNAL` pour un autre chemin |

La base est lue **en lecture seule** depuis un fichier SQLite local (`--db`, sinon
`VVA_DB_PATH`, sinon `./vva.db`). Pas d'appel réseau : la base de prod (`/data/vva.db` du
conteneur dashboard) se lit sur une copie cohérente (API `backup` de `sqlite3`, puis `scp`),
faite à la main par le parent. Le dashboard n'expose pas ces agrégats par ticket (hors
périmètre, #7).

### Rattachement d'une session à un ticket

La télémétrie ne porte ni branche ni ticket (seulement `session_id`). Le hook `link` lit le
JSON du hook (`session_id`, `cwd`), lit la branche du poste (`git branch --show-current`) et,
si elle suit `feat|fix|chore|docs|test|ci/<n>-…`, note `session -> n` dans le journal
(seulement quand le ticket change). Un point de tokens va au dernier rattachement antérieur
de sa session (une session réutilisée pour un autre ticket du même poste est donc découpée
dans le temps), sinon au premier ; sans rattachement : « sans ticket ». Les sous-agents
partagent la session du fil qui les lance.

Câblage local (`.claude/settings.json`, jamais commité), à côté des hooks de
[`tools/telemetry`](../tools/telemetry/README.md) :

```json
"SessionStart":      [{ "hooks": [{ "type": "command", "timeout": 2,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/token-report.sh\" link" }] }],
"UserPromptSubmit":  [{ "hooks": [{ "type": "command", "timeout": 2,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/token-report.sh\" link" }] }],
"PostToolUseFailure": [{ "matcher": "*", "hooks": [{ "type": "command", "timeout": 2,
  "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/telemetry/hook_emit.py\"" }] }]
```

Le hook `link` ne garde que `session_id` et le numéro de ticket (le prompt du hook
`UserPromptSubmit` n'est jamais écrit) et ne bloque jamais (code 0, git limité à 1,5 s).

## Routage des modèles

Priorité : agent à modèle fixe > label du ticket > escalade > défaut.

| Règle | Modèle |
|-------|--------|
| défaut | Sonnet |
| agent `scout` | Haiku (toujours) |
| agent `reviewer` | Opus (toujours) |
| agent `tech-lead` (session principale) | Sonnet (toujours) |
| label `complex` | Opus |
| 2 échecs notés pour le même agent sur le même ticket | Opus pour la relance |

```sh
tools/agents/bin/token-report.sh fail 42 dev-backend               # noter un échec
tools/agents/bin/token-report.sh route dev-backend --ticket 42 --label complex
```

`route` affiche l'identifiant du modèle à passer à l'agent (`model:` de l'appel) et, si
c'est une promotion, l'écrit dans le journal avec son motif (`complex`, `2 échecs`), une
seule fois par (ticket, agent, motif). Les échecs se comptent par (ticket, agent) : l'échec
d'un `qa` ne promeut pas `dev-backend`. Ticket ≥ 1 ; agent et label : `[A-Za-z0-9._:-]{1,64}`. Le
rapport liste ces promotions ; un usage d'Opus par un agent routé ailleurs sans promotion
écrite apparaît « (non tracée) ».

Les fichiers d'agents (`.claude/agents/*.md`) restent locaux et ne sont jamais commités : leur
champ `model` doit égaler le modèle de base du routage. `tools/agents/tests` le vérifie quand
ces fichiers sont présents sur le poste (test ignoré en CI).

## Passation automatique (#83)

Quand le contexte d'une session gonfle, la note de passation et la session suivante sont
automatiques. Outil : `tools/agents/bin/handoff.sh` (`tools/agents/handoff.py`, stdlib, Python
3.9). Le tech lead reste sur Sonnet par défaut (`tech-lead: sonnet` du routage) ; Opus seulement
pour le label `complex` (agents d'implémentation) et le `reviewer`.

```sh
tools/agents/bin/handoff.sh check                      # hook UserPromptSubmit (JSON sur stdin)
tools/agents/bin/handoff.sh post  --note-file note.md   # valider et poster dans #21
tools/agents/bin/handoff.sh latest                      # dernière note marquée de #21
tools/agents/bin/handoff.sh relay --note-file note.md   # post, puis session neuve
```

- **Seuil** : contexte = dernier `usage` du fil principal du transcript (input + cache lu +
  cache créé ; les sous-agents sont ignorés). L'alerte douce à 100 000 tokens est gérée ailleurs :
  `check` ne fait rien en dessous de **150 000**. Au seuil, une seule fois par session (marqueur
  `~/.vva/handoff/<session>.done`, `VVA_HANDOFF_DIR`), il émet `additionalContext` : le tech lead
  écrit la note puis lance `relay`. `check` ne bloque jamais (exit 0, silencieux si erreur).
- **Note** (contrat) : ≤ 10 lignes, rubriques `Fait`, `Reste`, `Fichiers clés`, `Pièges` en début
  de ligne ; refusée si elle contient un motif de secret (`token: …`, `api_key=…`, `ghp_…`,
  `-----BEGIN`, chaîne alphanumérique de 32+ caractères…). Aucune donnée d'enfant.
- **Poste** : commentaire de #21 commençant par `<!-- passation -->` (`gh`, dépôt
  `HANDOFF_REPO`). `latest` ne renvoie que la dernière note marquée, d'un auteur de
  `HANDOFF_AUTHORS` (défaut `chebihiF,infinitySoftMA`), jamais l'historique.
- **Relais** : `relay` poste, puis lance `claude -p` (surchargeable : `HANDOFF_CMD`) détaché, le
  prompt (la dernière note seule) sur stdin. Si le démarrage est refusé ou échoue dans les 5 s :
  la note reste postée, une ligne sur stderr prévient le parent, exit 0. La sortie de la session
  relayée va dans `~/.vva/handoff/relay.log` (600, jamais le prompt).

Câblage à faire par le parent (`.claude/settings.json`, local, jamais commité) :

```json
"UserPromptSubmit": [{ "hooks": [{ "type": "command", "timeout": 5,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/handoff.sh\" check" }] }]
```

## Limites

- Échec d'une tâche : noté à la main (`fail`) par le tech lead ; la télémétrie ne le détecte
  pas. Le ratio du rapport porte sur les échecs **d'outils** (`PostToolUseFailure`), à câbler.
- Tokens antérieurs au hook `link` : « sans ticket ». Une session sur `main` aussi.
- Le label `complex` n'est pas lu sur GitHub : le passer à `route --label`.
- Coûts en euros : hors périmètre.

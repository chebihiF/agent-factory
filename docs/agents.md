# Installer les agents dans un projet (#71)

Les agents, skills, hooks et règles de l'usine vivent ici (`agents/`, `skills/`, `hooks/`,
`rules/`). Un projet les utilise **sans les commiter** : ils sont copiés dans son `.claude/`
local.

```sh
make install-agents PROJECT=../mon-projet                        # = scripts/install_agents.py ../mon-projet
make install-agents PROJECT=../mon-projet OVERLAY=surcouche.md   # avec la partie propre au projet
```

Ce que fait la commande (stdlib, Python 3.9 ou 3.12) :

| Étape | Détail |
|-------|--------|
| Contrôle | refuse une cible inexistante, hors dépôt git, qui n'est pas la racine du dépôt, ou dont `git ls-files -- .claude CLAUDE.md` n'est pas vide (`git rm --cached` d'abord) ; refuse un `.claude` ou `CLAUDE.md` lien symbolique ; message distinct si `git` est introuvable |
| Exclusion | ajoute `.claude/` et `CLAUDE.md` à `<cible>/.git/info/exclude` (avant toute copie, sans doublon, contenu existant conservé ; fonctionne dans un worktree) |
| Copie | `agents/`, `skills/`, `hooks/`, `rules/` vers `<cible>/.claude/<même nom>/` ; liens symboliques ignorés côté source, refusés côté cible (fichier ou dossier parent), destination toujours sous `<cible>/.claude/` |
| Manifeste | `<cible>/.claude/.agent-factory-manifest` liste les fichiers installés ; à l'installation suivante, ceux qui ne font plus partie de l'usine sont retirés (dossiers vidés compris), jamais un fichier absent du manifeste |
| Surcouche | `--overlay <fichier>` : le fichier est inséré dans un bloc balisé `agent-factory:overlay` de `<cible>/CLAUDE.md` (créé si absent ; une surcouche qui contient elle-même les marqueurs est refusée, un bloc existant sans marqueur de fin aussi) |
| Git | aucun `git add`, aucun commit dans la cible |

- **Idempotente** : relancer met à jour les fichiers de l'usine (ils écrasent la version locale) ;
  les fichiers propres au projet (`settings.json`, un agent maison, le reste de `CLAUDE.md`) ne
  sont jamais touchés ni supprimés. Le bloc de surcouche est remplacé, pas dupliqué.
- **Surcouche** : tout ce qui est propre au projet (machines, chemins, dépôt, règles produit)
  va dans un fichier du projet, hors de l'usine. Aucun secret dedans : `CLAUDE.md` reste local,
  mais la surcouche peut être copiée par erreur ailleurs.
- `rules/tech-lead.md` est installé dans `.claude/rules/` ; le `CLAUDE.md` du projet y renvoie
  ou le recopie (la surcouche s'y ajoute).
- **Limite** : la cible `PROJECT` ne doit pas contenir d'apostrophe (`'`) avec `make` (le chemin est
  entre apostrophes dans le Makefile) ; sinon appeler directement
  `python3 scripts/install_agents.py "<chemin>"`.
- Le câblage des hooks (`.claude/settings.json`) reste manuel : gabarit `hooks/settings.example.json`.
- Vérification : `git -C <cible> status --porcelain` ne montre rien, `git -C <cible> ls-files .claude CLAUDE.md` est vide.

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

## Plafond de tokens par ticket (#188)

Règle : `rules/token-budget.md` ; contrat : `contracts/token-ceiling.md`. Outil :
`tools/agents/bin/ceiling.sh` (`tools/agents/ceiling.py`, stdlib, Python 3.9).

```sh
tools/agents/bin/ceiling.sh --db vva.db --repo OWNER/REPO
tools/agents/bin/ceiling.sh --repo OWNER/REPO --dry-run  # simule
```

Mêmes défauts que `token-report.sh` (`VVA_DB_PATH`, `VVA_AGENTS_JOURNAL`). Le journal est propre au
dépôt : `--repo` doit être celui dont les tickets alimentent ce journal. Plafonds : 150 000 tokens (ticket simple),
300 000 (label `complex`). Au-dessus, un commentaire d'alerte (total et plafond) est posé sur le
ticket, une seule fois par plafond. Alerte seulement. À lancer à la main ou par la planification
ci-dessous ; les seuils des hooks locaux sont alignés à la main sur la règle.

**Jeton** : à chaque exécution, `ceiling.sh` lit `gh auth token -u infinitySoftMA` et l'impose comme
`GH_TOKEN` (un jeton hérité est ignoré) ; il n'est ni écrit ni passé en argument. Bot indisponible
(`gh` absent, compte non connecté, trousseau verrouillé) : message sur stderr, code 1, aucun appel
sur les tickets.

### Planification (#210)

```sh
make ceiling-install REPO=OWNER/REPO                           # base : VVA_DB_PATH, sinon ./vva.db
make ceiling-install REPO=OWNER/REPO DB=<base> INTERVAL=30     # intervalle en minutes (défaut 15)
launchctl list | grep agent-factory.ceiling                    # la tâche est chargée
make ceiling-uninstall                                         # retour arrière
```

- `ceiling-install` rend `tools/agents/launchd/ceiling.plist.template` (label
  `agent-factory.ceiling.<dossier du dépôt>.<hash court du chemin du clone>`, `StartInterval`, `PATH` réduit à ses entrées
  absolues, aucun jeton) dans `~/Library/LaunchAgents`, puis `launchctl bootstrap gui/<uid>`.
  Elle affiche la base retenue ; si `bootstrap` échoue, le plist est supprimé. Clone déplacé :
  `ceiling-uninstall` avant le déplacement (le label change avec le chemin).
  Relancer pour changer un réglage. Base absente, dépôt ou intervalle (1 à 1440) invalide : refus.
- Chemins de la base et du journal figés à l'installation (absolus). La base est une copie locale
  (voir « Sources ») : la planification ne la rafraîchit pas, les alertes datent de la copie.
- Journal : `~/.vva/ceiling/ceiling.log` (dossier 700, fichier 600 ; `VVA_CEILING_DIR` pour un
  autre dossier) : numéros de tickets, compteurs et erreurs, sans prompt, jeton ni donnée
  d'enfant. Non horodaté et sans rotation.
- Smoke test : `tools/agents/bin/ceiling.sh --repo OWNER/REPO --db <base> --dry-run`.

## Passation automatique (#83)

Quand le contexte d'une session gonfle, la note de passation et la session suivante sont
automatiques. Outil : `tools/agents/bin/handoff.sh` (`tools/agents/handoff.py`, stdlib, Python
3.9). Le tech lead reste sur Sonnet par défaut (`tech-lead: sonnet` du routage) ; Opus seulement
pour le label `complex` (agents d'implémentation) et le `reviewer`.

```sh
tools/agents/bin/handoff.sh check                      # hook UserPromptSubmit (JSON sur stdin)
tools/agents/bin/handoff.sh check --threshold 120000   # seuil de phase en tokens (défaut 150000)
tools/agents/bin/handoff.sh post  --note-file note.md   # valider et poster dans #21
tools/agents/bin/handoff.sh latest                      # dernière note marquée de #21
tools/agents/bin/handoff.sh relay --note-file note.md   # déconseillé (voir Relais)
```

- **Seuil** : contexte = dernier `usage` du fil principal du transcript (input + cache lu +
  cache créé ; les sous-agents sont ignorés). L'alerte douce à 100 000 tokens est gérée ailleurs :
  `check` ne fait rien en dessous de **150 000** (réglable : `--threshold <tokens>`, à placer
  dans la commande du hook). Deux seuils alignés sur `rules/token-budget.md` §3 : **150 000** =
  relève à la prochaine frontière de phase ; **250 000** (`IMMEDIATE_THRESHOLD`) = relève
  immédiate. Chaque seuil, une seule fois par session (marqueurs `~/.vva/handoff/<session>.done`
  et `<session>.now.done`, `VVA_HANDOFF_DIR`), il émet `additionalContext` : le tech lead
  écrit la note et la poste avec `post` ; la relève est une session indépendante
  (`rules/session-agents.md`). `check` ne bloque jamais (exit 0, silencieux si erreur).
- **Note** (contrat) : ≤ 10 lignes, rubriques `Fait`, `Reste`, `Fichiers clés`, `Pièges` en début
  de ligne ; refusée si elle contient un motif de secret (`token: …`, `api_key=…`, `ghp_…`,
  `-----BEGIN`, chaîne alphanumérique de 32+ caractères…). Aucune donnée d'enfant.
- **Poste** : commentaire de #21 commençant par `<!-- passation -->` (`gh`, dépôt
  `HANDOFF_REPO`). `latest` ne renvoie que la dernière note marquée, d'un auteur de
  `HANDOFF_AUTHORS` (défaut `chebihiF,infinitySoftMA`), jamais l'historique.
- **Relais (déconseillé)** : la règle `rules/session-agents.md` interdit qu'une session en
  démarre une autre (pas de chaîne parent/enfant) ; flux nominal = `post`. `relay` poste, puis lance `claude -p` (surchargeable : `HANDOFF_CMD`) détaché, le
  prompt (la dernière note seule) sur stdin. Si le démarrage est refusé ou échoue dans les 5 s :
  la note reste postée, une ligne sur stderr prévient le parent, exit 0. La sortie de la session
  relayée va dans `~/.vva/handoff/relay.log` (600, jamais le prompt).

Câblage à faire par le parent (`.claude/settings.json`, local, jamais commité) :

```json
"UserPromptSubmit": [{ "hooks": [{ "type": "command", "timeout": 5,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/handoff.sh\" check" }] }]
```

## Compte rendu (CR) des agents en base (#61)

Chaque agent termine sa tâche par un CR de **5 lignes au plus**, une rubrique par ligne en début
de ligne : `Fait : …`, `Fichiers : …`, `Reste : …`, `Blocage : … (ou « aucun »)`. Les
définitions d'agents (`.claude/agents/*.md`, locales) reprennent ce format dans leur « Rapport
final ». Outil : `tools/agents/bin/cr.sh` (`tools/agents/cr.py`, stdlib, Python 3.9).

- **Écriture** : `cr.sh record` (hooks `SubagentStop` et `SessionEnd`, JSON sur stdin) lit
  `last_assistant_message` (sinon la fin du transcript), extrait les rubriques et ajoute une ligne
  à `agent_reports` ([contrat](../contracts/agent-report.schema.json)) : `ticket` (numéro de la
  branche du poste, sinon NULL), `agent` (`agent_type` ; `tech-lead` en fin de session), `ts`
  (UTC), `summary`, `status` (`blocked` si Blocage renseigné, `partial` si Reste renseigné, `done`
  sinon, `no-cr` si aucune rubrique : rien n'est alors recopié). Une ligne contenant un motif de
  secret est remplacée par `[masqué]`. Jamais de prompt ni de donnée d'enfant dans un CR.
- **Base** : SQLite locale `~/.vva/agent-reports.db` (600 ; `VVA_CR_DB` pour un autre chemin).
- **Silence** : base illisible, JSON invalide ou plus de 1,5 s : code 0, aucune sortie, l'agent n'est
  jamais interrompu.
- **Lecture** : `make status` (`cr.sh status`) affiche au plus 10 lignes, les CR arrivés depuis la
  dernière lecture (marqueur `last_read` dans la base), les plus récents en dernier ; « Aucun
  nouveau CR » sinon. Base illisible : une ligne sur stderr, code 1.

Câblage local (`.claude/settings.json`, jamais commité), à côté des autres hooks :

```json
"SubagentStop": [{ "hooks": [{ "type": "command", "timeout": 2,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/cr.sh\" record" }] }],
"SessionEnd":   [{ "hooks": [{ "type": "command", "timeout": 2,
  "command": "\"$CLAUDE_PROJECT_DIR/tools/agents/bin/cr.sh\" record" }] }]
```

## Skills /ticket, /sprint, /deploy-check (#15, #16)

Versions génériques, versionnées hors `.claude/` ; chaque projet les copie dans son
`.claude/skills/` local (jamais commité) :

| Skill | Fichier | Contrat |
|-------|---------|---------|
| `/ticket <n>` | [`skills/ticket/SKILL.md`](../skills/ticket/SKILL.md), DoD [`skills/ticket/checklist.md`](../skills/ticket/checklist.md) | 8 étapes ; `stop-agents` → rien n'est modifié ; 2 échecs de `make check` → carte « En cours », aucun merge ; PR `Closes #<n>` par le bot, merge squash, cartes En cours → Revue → Livré |
| `/sprint` | [`skills/sprint/SKILL.md`](../skills/sprint/SKILL.md) | `status` ≤ 15 lignes par colonne ; `move <n> <colonne>` ; `run` ; `next` (alias `close`) : itération suivante de 7 jours, cartes non livrées reportées |
| `/deploy-check` | [`skills/deploy-check/SKILL.md`](../skills/deploy-check/SKILL.md) | version servie vs dernier merge, smoke test en lecture seule ; KO → bug `type:bug` P0 automatique, rollback par le pipeline proposé puis lancé après confirmation explicite du propriétaire |

`checklist.md` est une copie de la DoD de [`SCRUM.md`](SCRUM.md), qui reste la source de vérité.

**Surcouche du projet** (locale, jamais commitée, ni ici ni chez le projet) : dépôt, propriétaire
et numéro du Project, compte bot `<bot>`, issue journal, postes du pool, agents propres au
projet (délégation de l'étape 3 de `/ticket`), URL de prod et services voisins de
`/deploy-check`. Elle se réapplique dans le `.claude/` local après copie des skills.

`tools/agents/tests/test_skills.py` vérifie le front-matter (`name` = dossier, `description`
avec `Usage /<name>`), le contrat et les scénarios Gherkin de chaque skill, l'égalité
checklist = DoD, et qu'aucun fichier générique (`skills/`, `agents/`, `rules/`, `hooks/`, cette
page) ne contient de valeur propre à un projet (`git grep` : adresse IP, chemin machine, nom
d'hôte du serveur, machine GPU, poste nommé, dépôt du produit) ni de secret.

## Limites

- Échec d'une tâche : noté à la main (`fail`) par le tech lead ; la télémétrie ne le détecte
  pas. Le ratio du rapport porte sur les échecs **d'outils** (`PostToolUseFailure`), à câbler.
- Tokens antérieurs au hook `link` : « sans ticket ». Une session sur `main` aussi.
- Le label `complex` n'est pas lu sur GitHub : le passer à `route --label`.
- Coûts en euros : hors périmètre.

## Boucle autonome planifiée (#18)

Aux heures configurées, la boucle prend le ticket « Prêt » le plus prioritaire et le traite par
une session neuve `/ticket <n>`, dans un budget de tokens quotidien. Outil :
`tools/agents/bin/agent-loop.sh` (`tools/agents/agent_loop.py`, stdlib, Python 3.9).
Planification : launchd du Mac (0 €), un seul ticket à la fois (hors périmètre : parallélisme,
serveur distant).

```sh
cp tools/agents/config/loop.example.yml ~/.vva/loop.yml   # local, jamais commité ; à adapter
make loop-install            # CONFIG=<fichier> (sans apostrophe) pour un autre chemin ; à relancer si hours change
make loop-uninstall
tools/agents/bin/agent-loop.sh tick --config ~/.vva/loop.yml   # un tick à la main
```

**Config** (contrat ; clé inconnue, manquante ou invalide = `config-invalid`, aucune session) :

| Clé | Valeur |
|---|---|
| `hours` | liste `[HH:MM, …]`, heures locales distinctes ; une entrée `StartCalendarInterval` chacune |
| `daily_token_budget` | entier ≥ 1, tokens par jour cumulés localement |
| `max_tickets_per_day` | entier ≥ 1 |
| `control_url` | `GET /api/v1/control` du dashboard ([contrat](../contracts/control-api.md)) : `https`, ou `http` sur localhost |

**Un tick**, dans l'ordre ; le premier arrêt l'emporte et s'écrit en une ligne
`<date> <code>` dans `~/.vva/loop/loop.log` (jamais titre, prompt, jeton ni contenu) :

1. Verrou `~/.vva/loop/lock` : un tick ou une session encore en cours → `busy`.
2. Compteurs du jour `~/.vva/loop/state.json` (600, [schéma](../contracts/agent-loop-state.schema.json),
   remis à zéro au changement de date locale) : budget atteint → `budget-exhausted` jusqu'au
   lendemain ; tickets du jour atteints → `max-tickets` ; fichier illisible ou daté du futur →
   `state-unreadable`.
3. Contrôles du dashboard (#8, lecture seule) : `paused` → `paused` ; `tokens_24h >= cap_tokens` →
   `cap-reached`. **Échec fermé** (à l'inverse du hook `PreToolUse`) : jeton absent, fichier jeton
   lisible par d'autres que vous, URL refusée, réseau, délai (5 s), code ≠ 200, redirection ou JSON
   invalide → `control-unreadable`, aucune session.
4. Sélection (`gh issue list` du dépôt courant) : statut Project « Prêt », sans `stop-agents`,
   toutes les dépendances fermées (numéros `#n` après « Dépend de » ; une dépendance vers un autre
   dépôt rend le ticket inéligible), tri P0→P3 (sans priorité en dernier) puis numéro. Rien →
   `no-ticket` ; gh en échec, réponse illisible ou liste tronquée (500) → `gh-*`.
5. Poste du pool (#14, `infra/scripts/pool.sh`, qui relit `stop-agents` et « Prêt ») ; refus →
   `pool-refused`. Le ticket est compté **avant** la session, et noté (`last_ticket`) : un ticket
   tenté reste inéligible jusqu'au lendemain, même resté « Prêt » après un échec (→ `no-ticket`
   s'il est le seul).
6. Session neuve dans le poste : `claude -p --output-format json`, prompt `/ticket <n>` sur stdin
   (délai max 4 h ; au délai, tout le groupe de processus de la session est tué). Les tokens
   (`modelUsage`, sinon `usage` : input, output et cache créé) s'ajoutent au compteur. Le **cache lu
   est exclu** du total : relu à chaque tour, il gonflerait le budget sans consommation neuve.
   Sortie illisible = consommation inconnue = **budget du jour épuisé**. Une session finie après
   minuit compte ses tokens sur le nouveau jour. La sortie n'est jamais conservée.
   → `ran ticket=<n> tokens=<n|unknown>`.

Le budget est vérifié avant chaque ticket : une session en cours n'est pas interrompue et peut
dépasser le budget ; la boucle s'arrête alors jusqu'au lendemain.

**Permissions** : la commande par défaut ne contourne aucune permission ; une session sans
surveillance n'obtient que ce que vos réglages locaux (`.claude/settings*.json`) autorisent.
Surcharge locale possible : `LOOP_SESSION_CMD`, mais `--dangerously-skip-permissions` et
`--permission-mode bypassPermissions` sont **refusés** (`session-cmd-refused`, aucune session).

| Variable | Rôle | Défaut |
|---|---|---|
| `VVA_LOOP_CONFIG` | fichier de config | `~/.vva/loop.yml` |
| `VVA_LOOP_DIR` | compteurs, verrou, journal | `~/.vva/loop` |
| `VVA_INGEST_TOKEN` / `VVA_INGEST_TOKEN_FILE` | jeton Bearer de `/api/v1/control` ; fichier en 600 exigé | `~/.vva/ingest_token` |
| `LOOP_SESSION_CMD` | commande de session | `claude -p --output-format json` |
| `VVA_LAUNCH_AGENTS` | dossier du plist | `~/Library/LaunchAgents` |

`make loop-install` rend `tools/agents/launchd/agents-loop.plist.template` (label
`agent-factory.loop.<dossier du dépôt>`, `PATH` du moment de l'installation réduit à ses entrées absolues, aucun secret), puis
`launchctl bootstrap gui/<uid>`. launchd lance un tick manqué (Mac en veille) au réveil.

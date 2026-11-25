# Contrôles : pause des agents et plafond de tokens (#8)

Le parent met les agents en pause ou règle le plafond de tokens depuis l'onglet « Contrôles » du
dashboard, connecté en **admin** (#185, [ADR-005](../docs/adr/ADR-005-auth-admin.md)) ; le tech lead
peut aussi le faire depuis son terminal. Le reste du dashboard est en lecture seule (#179). Le hook
`PreToolUse` du Mac lit cet état avant chaque outil. Alignement code / contrat vérifié par
`apps/dashboard/tests/test_controls.py`, `test_controls_page.py` et `tools/telemetry/tests/test_gate.py`.

## État

Une seule ligne SQLite (table `controls`, voir `db.py`), créée avec le schéma.

| Champ | Type | Sens |
|---|---|---|
| `paused` | booléen | `true` : les agents ne doivent exécuter aucun outil |
| `reason` | `"manuel"`, `"plafond"` ou `null` | motif de la pause (`null` si `paused=false`) ; le premier motif est conservé |
| `cap_tokens` | entier ≥ 1 | plafond, en tokens sur 24 h glissantes, tous agents (défaut **500 000**) |
| `tokens_24h` | entier | total des tokens des dernières 24 h (même source que `/tokens`, [`token-usage.md`](token-usage.md)) |
| `updated_at` | texte ISO 8601 UTC | dernier changement d'état |

**Plafond** : évalué à chaque lecture de l'état par l'API (le tableau de bord `/` lit sans écrire). Si `tokens_24h >= cap_tokens` et
que les agents tournent, l'état passe à `paused=true`, `reason="plafond"`. Reprendre alors que le
plafond est encore atteint ne sert à rien (la lecture suivante remet en pause) : relever le plafond
(ou attendre que la fenêtre de 24 h se vide), puis reprendre. Abaisser le plafond
sous la consommation met en pause. Hors périmètre : plafond par agent, notifications.

## API pour le hook : `GET /api/v1/control`

`Authorization: Bearer <INGEST_TOKEN>` (même jeton que l'ingestion, [`ingest-api.md`](ingest-api.md) :
comparaison en temps constant, 503 si non configuré). La session de navigateur n'ouvre pas cette route.

| Code | Cas | Corps |
|---|---|---|
| 200 | état courant, `Cache-Control: no-store` | `{"paused": false, "reason": null, "cap_tokens": 500000, "tokens_24h": 0, "updated_at": "..."}` |
| 401 | jeton absent ou faux | `{"detail": "..."}` |
| 503 | jeton non configuré, ou base illisible | `{"detail": "..."}` |

## Onglet « Contrôles » (#185)

Spec : `docs/design/pause-plafond.md`. Session admin, cookie, CSRF et limite de connexion :
[`dashboard-routes.md`](dashboard-routes.md). Toutes les réponses sont `Cache-Control: no-store`.

| Route | Accès | Réponses |
|---|---|---|
| `GET /controls` | session du dashboard ou admin | 200 état en lecture seule (aucun formulaire POST, aucun bouton) ; vue admin : bouton « Mettre en pause » et champ « Nouveau plafond » ; 303 vers `/login` sans session ; 503 base illisible ; ne fait que lire (pas de pause automatique au plafond) |
| `GET /controls?confirmer=pause` | admin | panneau « Mettre tous les agents en pause ? » : seul endroit où existe le formulaire `POST /controls/pause` |
| `GET /controls?plafond=<n>` | admin | panneau « Changer le plafond ? » (`ancien → nouveau`, écart en %) : seul endroit du formulaire `POST /controls/cap` ; **422** valeur invalide |
| `POST /controls/pause` | admin + CSRF | champs `csrf`, `confirm=oui` ; 303 vers `/controls?fait=pause` (`db.pause(conn, "manuel")`) |
| `POST /controls/cap` | admin + CSRF | champs `csrf`, `confirm=oui`, `cap` ; 303 vers `/controls?fait=plafond` (`db.set_cap`) |

Refus des deux `POST`, dans cet ordre : **403** admin désactivé (`ADMIN_PASSWORD_HASH` ou
`DASHBOARD_SESSION_KEY` absents) ; **401** session admin absente, expirée ou fermée (la session du
dashboard seule ne suffit pas) ; **413** corps > 4 Ko ; **403** jeton CSRF absent ou faux ; **422**
`confirm` différent de `oui` (sans confirmation, rien n'est écrit) ou plafond invalide. **429** :
limite de connexion admin (5 essais par minute et par IP), voir `POST /admin/login`.

Plafond saisi : chiffres ASCII seulement (« Chiffres seulement. »), de **1 à 1 000 000 000**
(« Il faut au moins 1 token. », « Trop grand : le maximum est 1 000 000 000 tokens / 24 h. »).
Pas de route de reprise (`POST /controls/resume` : 404) : la reprise reste au terminal ci-dessous.

## Réglage depuis le terminal du tech lead

Sur le VPS, dans le conteneur du dashboard (même `VVA_DB_PATH`) :

```sh
uv run python -c "from dashboard import db
with db.connect() as c: db.pause(c, 'manuel')"      # pause manuelle
uv run python -c "from dashboard import db
with db.connect() as c: db.set_cap(c, 800000)"     # plafond (entier >= 1)
uv run python -c "from dashboard import db
with db.connect() as c: db.resume(c)"              # reprise
```

`db.pause`, `db.resume` et `db.set_cap` sont les seules écritures de la table `controls`, avec la
pause automatique au plafond ci-dessus. Côté HTTP, seules `POST /controls/pause` et
`POST /controls/cap` écrivent (test d'exclusivité dans `test_controls_page.py`).

## Hook `PreToolUse` : `tools/telemetry/pretool_gate.py`

Stdlib uniquement. Ne lit pas stdin (aucun prompt ni argument d'outil ne le traverse).

- `paused=true` : message sur stderr (« Agents en pause (motif : …) »), sortie en **code 2** ;
  Claude Code n'exécute pas l'outil et affiche le message. Sinon code 0, sans sortie.
- **Repli sûr (fail-open)** : serveur injoignable, délai (2 s par requête, 4 s au total), code ≠ 200
  (dont 401 et 503), JSON invalide, redirection (jamais suivie) ou configuration absente/refusée :
  le hook utilise le dernier état en cache s'il a **au plus 300 s** (5 min), sinon il **laisse passer**
  (code 0). Le délai global de 4 s applique la même règle (cache en pause : code 2). Choix assumé : une panne du VPS ne doit pas figer le travail ; la pause n'est donc garantie
  que si le VPS répond ou que le cache est frais. Un cache daté du futur est ignoré.
- Journal (une ligne `date code`, jamais jeton, URL, outil ni contenu) : `fetch-failed`, `cache-used`,
  `fail-open`, `blocked`. Fichiers d'état en `0600`, journal tourné à 256 Ko.

| Variable | Rôle | Défaut |
|---|---|---|
| `VVA_CONTROL_URL` | URL complète de `/api/v1/control` : `https`, ou `http` sur localhost seulement | requis |
| `VVA_INGEST_TOKEN` | jeton Bearer (= `INGEST_TOKEN` du VPS) | requis |
| `VVA_GATE_CACHE` | cache du dernier état (`paused`, `reason`, `fetched_at`) | `$CLAUDE_PROJECT_DIR/.claude/telemetry/gate-cache.json` |
| `VVA_GATE_LOG` | journal | `$CLAUDE_PROJECT_DIR/.claude/telemetry/gate.log` |

### Câblage (réglages locaux, jamais versionnés)

À placer dans **votre** `.claude/settings.local.json` (ou `~/.claude/settings.json`) ; le jeton et l'URL
restent dans l'environnement local :

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "timeout": 5,
        "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/telemetry/pretool_gate.py\"" }] }
    ]
  }
}
```

# Routes du dashboard

Pages HTML servies au parent (épique #2, ticket #5). L'ingestion est décrite dans
[`ingest-api.md`](ingest-api.md). Alignement code / contrat vérifié par
`apps/dashboard/tests/test_auth.py::test_contract_documents_routes_env_and_cookie`.

## Configuration (variables d'environnement, jamais dans le dépôt)

| Variable | Contenu |
|---|---|
| `DASHBOARD_PASSWORD_HASH` | hash du mot de passe : `pbkdf2_sha256:<itérations>:<sel hex>:<hash hex>` (PBKDF2-HMAC-SHA256, stdlib ; 600 000 itérations par défaut) |
| `DASHBOARD_SESSION_KEY` | clé HMAC de signature des sessions, **32 caractères minimum** (ex. `python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `ADMIN_PASSWORD_HASH` | hash du mot de passe **admin** (#185), même format et même commande que `DASHBOARD_PASSWORD_HASH`, mot de passe différent ; absent, vide ou identique à `DASHBOARD_PASSWORD_HASH` : admin désactivé (403). En prod : `/opt/vva/admin_password_hash`, posé par `VVA_REMOTE_FILE=/opt/vva/admin_password_hash infra/scripts/set-dashboard-password.sh` |
| `FORWARDED_ALLOW_IPS` | proxys dont uvicorn croit `X-Forwarded-For` (`--proxy-headers`) : sous-réseau(x) du réseau Traefik, calculé par `deploy.sh` (`docker network inspect $PROXY_NETWORK`) ou fixé dans `proxy.env` ; jamais `*` (refusé) ; défaut de l'image `127.0.0.1` |

Le séparateur `:` (pas de `$`) évite toute interpolation dans un `.env` ou un shell.
Générer le hash sans l'écrire dans l'historique du shell :

```sh
uv run python -c "import getpass; from dashboard.auth import hash_password; print(hash_password(getpass.getpass()))"
```

Variable absente, vide, ou clé trop courte : la connexion est **fermée** (503).
Changer `DASHBOARD_SESSION_KEY` invalide toutes les sessions ouvertes.

## Session

Cookie `vva_session` = `<expiration epoch>.<HMAC-SHA256 base64url>`, signé avec
`DASHBOARD_SESSION_KEY`, vérifié en temps constant. Attributs : `HttpOnly`, `Secure`,
`SameSite=Lax`, `Path=/`, durée 8 h (`Max-Age` et expiration signée). Aucune donnée
personnelle dans le cookie. Le cookie `Secure` impose HTTPS (Traefik en prod).

### Session admin (#185, [ADR-005](../docs/adr/ADR-005-auth-admin.md))

Seule porte des commandes Pause, Reprise et Plafond ([`control-api.md`](control-api.md)). Cookie `vva_admin`
= `<identifiant aléatoire 256 bits>.<HMAC-SHA256 base64url>`, clé dérivée de `DASHBOARD_SESSION_KEY`
et de `ADMIN_PASSWORD_HASH` (changer l'un ou l'autre ferme les sessions admin), vérifié en temps
constant. Attributs : `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, `Max-Age=28800`. La
session vit côté serveur (mémoire du processus, un seul worker) : **8 h** au plus après la
connexion, fermée après **30 min** sans requête admin (fenêtre glissante : chaque requête admin la
repousse), fermée par la déconnexion, une nouvelle connexion depuis le même navigateur (l'ancienne
session du cookie est fermée) ou un redémarrage. Jeton **CSRF** aléatoire lié à la session,
champ caché `csrf` de chaque formulaire POST admin, comparé en temps constant.

| Route | Accès | Réponses (toutes `no-store`) |
|---|---|---|
| `GET /admin/login` | public | 200 formulaire (champ `password`), sans navigation ; 403 admin désactivé |
| `POST /admin/login` | public, limité | 303 vers `/controls` + cookie `vva_admin` ; 401 « Mot de passe incorrect. Il te reste N essais cette minute. » ; 403 admin désactivé, ou mot de passe admin égal à celui du dashboard ; 413 corps > 4 Ko ; 429 + `Retry-After` |
| `POST /admin/logout` | admin + CSRF | 303 vers `/controls`, session fermée côté serveur, cookie effacé ; 401 sans session (cookie effacé aussi) ; 403 CSRF |

Limite de `POST /admin/login` : **5 essais par minute par IP** (fenêtre glissante, adresse vue par
uvicorn `--proxy-headers` et `FORWARDED_ALLOW_IPS`), plus un plafond global de 30 essais par minute
toutes IP (borne le coût PBKDF2 d'une attaque répartie). Les deux limites sont vérifiées avant d'en
consommer une : un essai refusé (429) ne compte ni pour l'IP ni pour le global. Ordre des contrôles :
configuration, limite, taille, mot de passe ; une tentative admise compte qu'elle réussisse ou non.

## Routes

Retirées par #179 (tableau de bord unique, lecture seule) : `/progress`, `/api/v1/progress` (et
`progress.schema.json`), `/activity` : 404. `/controls` revient avec #185 (onglet « Contrôles »,
voir [`control-api.md`](control-api.md)).

| Route | Accès | Réponses |
|---|---|---|
| `GET /healthz` | public | 200 `{"status": "ok", "version": "<APP_VERSION>"}` — aucune donnée d'événement |
| `GET /` | session | 200 tableau de bord unique en lecture seule (#179, voir ci-dessous) ; 303 vers `/login` sans session valide (aucune lecture) ; 503 si la base est illisible ; jamais mis en cache |
| `GET /login` | public | 200 formulaire (champ `password`) |
| `POST /login` | public, limité | voir ci-dessous |
| `GET /interactions` | session | 200 page ; 303 vers `/login` sans session valide (aucune lecture de la base) ; 503 si la base est illisible |
| `GET /tokens` | session | 200 page (totaux par agent et modèle, 24 h / 7 j) ; 303 vers `/login` sans session valide ; voir [`token-usage.md`](token-usage.md) |
| `GET /controls` | session ou admin | onglet « Contrôles » (#185) : lecture seule, commandes Pause, Reprise et Plafond pour l'admin ; voir [`control-api.md`](control-api.md) |
| `GET /stream` | session | 200 `text/event-stream` ; **401** sans session valide (aucune lecture de la base) ; 503 si la base est illisible |
| `GET /static/live.js` | public | client temps réel (`EventSource`), sans dépendance ni CDN |
| `GET /static/tokens.css` | public | feuille de style du design system |
| `GET /static/nav.css` | public | coque commune des pages (#192-#194, spec `docs/design/pause-plafond.md`) : un seul `<nav aria-label="Navigation principale">` (Accueil, Tokens, Interactions, Contrôles ; `aria-current="page"` sur la page active), barre d'onglets basse (cibles 56 px, zone sûre iOS) sous 900 px, en-tête à navigation horizontale dès 900 px, contenu 1200 px au plus |
| `GET /static/board.js` | public | rafraîchissement de `/` par GET toutes les 5 s et envoi du filtre (JS natif, sans CDN) |
| `GET /static/board.css` | public | style du tableau de bord (variables de `tokens.css` uniquement) |

### `POST /login`

Corps `application/x-www-form-urlencoded`, champ `password`, 4 Ko maximum.

| Code | Cas |
|---|---|
| 303 | bon mot de passe : pose `vva_session`, redirige vers `/` (#179) |
| 401 | mot de passe faux, vide ou absent — message neutre « Identifiants invalides. » |
| 413 | corps > 4 Ko |
| 429 | plus de **5 tentatives par minute** (fenêtre glissante, globale : compte unique), en-tête `Retry-After: 60` |
| 503 | connexion non configurée |

L'ordre des contrôles est : configuration, limite, taille, mot de passe. Une tentative
compte dans la limite qu'elle réussisse ou non.

### `GET /`

Tableau de bord unique (#179, spec `docs/design/dashboard-refonte.md`) : **lecture seule**. Aucune
commande, aucun `hx-post` ni `<form method="post">` ; les actions sont des liens GitHub
(`target="_blank" rel="noopener"`, « (nouvel onglet) » dans le nom accessible), des ancres
internes et le formulaire GET du filtre. Réponses en `no-store`. Cinq sections :

- **À faire par toi** (lecture GitHub ci-dessous, dépôt `VVA_GITHUB_REPO`) : « ? Attend ta
  réponse » = ticket ouvert dont le corps contient une ligne `Attend l'utilisateur : <question>`
  (début de ligne, puce `-`/`*` permise ; ni citation `>` ni bloc de code ``` ou ~~~)
  (question affichée, 200 caractères au plus ; les commentaires ne sont pas lus, pour rester sous
  la limite de 60 requêtes/heure) ; « ↗ À relire » = PR ouvertes ; « ◆ Décision » = tickets
  `blocked` sans cette ligne. Épiques (label `epic`) et labels ignorés exclus.
- **Qui fait quoi** (500 derniers événements) : un agent par (`agent_type`, `agent_id`), rôle en
  français, ticket identifié par (`project`, `issue`) : titre GitHub seulement si `project` = nom du
  dépôt `VVA_GITHUB_REPO`, sinon « Ticket hors du dépôt suivi » (même numéro, autre projet) ; état « ● Au travail » (dernier événement ≠ `SubagentStop`, moins de 30 min) / « ▲ Bloqué »
  (au travail sur un ticket `blocked`) / « ○ Au repos » ; titre du ticket (jamais son numéro),
  projet, « Depuis » (dernier `SubagentStart`, sinon premier événement de la fenêtre), « Signe de vie »
  (« ▲ » en gras au-delà de 2 min pour un agent au travail). Actifs, puis un inactif par rôle (5 au plus).
- **Avancement des tickets** : colonnes Backlog / Prêt / En cours / Revue / Livré (statuts de la
  lecture GitHub ci-dessous), compteur, 3 titres par colonne (plus récents d'abord) et « N de plus ».
- **Liste des tickets** (#182, section `#tickets`, même source que l'Avancement) : une ligne par
  ticket suivi, de Backlog à Livré puis les plus récents d'abord, 100 au plus (« N de plus » et lien
  vers les issues GitHub). Chaque ligne : titre (lien GitHub, nouvel onglet), statut (« ○ Backlog »,
  « ◇ Prêt », « ● En cours », « ↗ Revue », « ✓ Livré », forme et mot), sprint, épique, lien vers la
  PR ouverte qui le ferme. Forme d'une ligne : [`tickets.schema.json`](tickets.schema.json) (ajout
  rétrocompatible : aucune route ni champ existant modifié). **Sprint** = titre du jalon (milestone)
  GitHub, sinon label `sprint-N` (« Sprint N »), sinon « Sprint non renseigné » : l'itération du Project
  GitHub n'est pas lue (GraphQL, jeton requis ; décision D20 = aucun jeton). **Épique** = titre de
  l'issue `epic` citée dans le corps (« Epic #72 », « Épique : #2 » ; hors blocs de code) ; épique
  citée mais absente de la lecture : « Épique hors de la liste lue » ; « Sans épique » seulement sans
  citation. GitHub injoignable et rien en cache : « Les tickets ne peuvent pas être lus » + « Recharger
  les données » et lien vers les issues. Filtre sur un autre projet : « Aucun ticket pour <projet> ».
  Aucun numéro de ticket affiché.
- **Quotas et tokens** : jauge = tokens des 24 h glissantes ÷ plafond (`controls.cap_tokens`, lu sans
  écrire) ; « ✓ Confortable » < 70 % ≤ « ▲ Attention » ≤ 90 % < « ✖ Presque épuisé » ; tokens par
  agent (24 h), par jour (7 jours, date UTC de `ts`) et 5 tickets les plus gourmands (7 jours) :
  ticket d'une session = dernier couple (`project`, `issue`) connu dans ses événements
  (`token_usage.session_id`), regroupé sur ce couple, jamais une jointure sur `agent_id` (voir
  `token-usage.md`). Valeurs toujours écrites en texte.

Filtre `?projet=<nom>` (projets et compteurs ci-dessous) : il restreint « Qui fait quoi » ;
un projet autre que celui de `VVA_GITHUB_REPO` vide « À faire » et « Avancement » (« Aucun ticket pour
<projet> », lien « Afficher tous les projets ») ; les tokens ne portent pas de projet et restent
globaux. Projet inconnu : « Tous » avec message `role="status"`.

Aucune écriture : la page ne fait que des `SELECT` (vérifié par `PRAGMA data_version` et
`total_changes` dans `tests/test_board.py`). Seule exception, comme toutes les routes : `db.connect()`
crée le fichier et le schéma s'ils sont absents (une fois par processus, `CREATE … IF NOT EXISTS` et
la ligne unique de `controls`). Textes GitHub (titres, question) toujours échappés par Jinja.

États : en direct (« ● En direct · mis à jour à HH:MM:SS UTC ») ; ingestion muette = dernier
événement (toutes origines) de plus de **5 min** : bandeau `role="status"` et pastille « ▲ Aucun signe
de vie depuis N min » ; aucun événement : bandeau qui explique quoi vérifier ; erreur GitHub :
bandeau `role="alert"` avec l'heure et le lien GET « Recharger les données » (garde `?projet=`),
dernières données gardées ; base illisible : 503, même bandeau. Chaque vue vide a son message
(« Tu es à jour », « Aucun agent au travail », « Aucun ticket pour l'instant », « Rien consommé
aujourd'hui ») ; les agents se lancent depuis le terminal du tech lead, jamais depuis la page.
`static/board.js` recharge `#contenu` par GET toutes les 5 s (pas si l'onglet est caché ou si le
focus est dans la zone) ; en cas d'échec, bandeau `role="alert"` et données précédentes conservées.

### Projets et compteurs du filtre (`activity.agent_counts`)

Projets proposés : `viral-video-agents`, `agent-factory`, puis ceux présents en base ; chaque libellé
porte son nombre d'agents, calculé en une requête `GROUP BY project` : agents distincts parmi les 100
derniers événements **du projet** ; « Tous » = agents distincts parmi les 100 derniers événements
**toutes origines**, donc possiblement inférieur à la somme des projets. Formulaire GET natif (radios
`name="projet"` ; `board.js` l'envoie au clic, à Espace ou à Entrée, **pas aux flèches**, WCAG 3.2.2 ;
bouton dans `<noscript>`) : l'URL est partageable et le retour arrière fonctionne.

### `GET /interactions`

Lit la table `events` : les **20 dernières sessions** (`session_id`, triées par dernier `ts`
décroissant ; nombre d'événements, première et dernière heure UTC) et, pour chacune, ses
sous-agents. Un sous-agent = un `agent_id` : premier `SubagentStart` et dernier
`SubagentStop` de la même session ; durée = `Stop - Start` (`2 min 02 s`, `12 s`,
`< 1 s`, `1 h 02 min`), « — » sans `Start` ou sans `Stop` ; état « ○ En cours » /
« ● Terminé » ; un sous-agent démarré, sans `Stop` ni aucun événement de son `agent_id` depuis
plus de **30 min** (`activity.SILENT_AFTER`) est « ◌ Terminé sans signal », jamais « En cours » (#181).
Un `ts` illisible compte comme absence de signe de vie. Agrégation en SQL (`GROUP BY session_id,
agent_id`, index `idx_events_session_agent`). Colonne « Modèle » : dernier `model` connu de ce
sous-agent, « — » sinon (les hooks de sous-agents ne le fournissent pas toujours). États vide (« Aucune interaction pour l'instant ») et erreur
(`role="alert"`, « Réessayer ») ; `no-store`. Mise à jour en direct par `GET /stream`.

### `GET /stream`

Flux Server-Sent Events (`Content-Type: text/event-stream`, `Cache-Control: no-store`,
`X-Accel-Buffering: no`). Session exigée : **401** sans cookie valide (réponse vide, pas de
redirection : un `EventSource` ne la suivrait pas).

- `id:` = `rowid` croissant de la table `events` (ordre d'insertion ; la table ne doit
  jamais être compactée par `VACUUM`, qui renumérote) ;
- `data:` = JSON sur une ligne : `event_id`, `ts`, `time_label` (UTC), `hook_event_name`,
  `agent_type`, `agent_id`, `tool_name` (`null` si absent), `project` (#68), `issue` et `model` (#181, `null` si absents) ; ni `session_id` ni autre donnée ;
- première ligne `retry: 2000` ; commentaire `: keepalive` toutes les 15 s sans événement ;
- sondage de la base toutes les 0,5 s : un événement ingéré est émis en moins de 2 s ;
- curseur de départ : en-tête `Last-Event-ID` (reprise automatique du navigateur), sinon
  paramètre `?after=<id>` (la page `/interactions` embarque son dernier id dans
  `data-last-id`), sinon dernier id connu (rien n'est rejoué). Valeur illisible : ignorée.
  Reprise = événements d'`id` strictement supérieur : ni perte ni doublon ;
- une connexion dure 5 min au plus, puis le serveur la ferme : le navigateur reprend avec
  `Last-Event-ID` et la session est revérifiée (expirée : 401, le flux s'arrête).

Client : `static/live.js` (JS maison, ~80 lignes, pas l'extension HTMX `sse` : aucune
dépendance à vendoriser). Il ajoute la ligne au flux (`<ol data-feed>`, 100 lignes max, sans
doublon d'`event_id`) puis rafraîchit le tableau « Agents » / les sessions en rechargeant la
page et en remplaçant la zone `data-refresh`. DOM construit par `textContent` (aucun HTML
injecté).
Client : `static/live.js` (JS maison, pas l'extension HTMX `sse` : aucune dépendance à vendoriser).
Il rafraîchit les sessions de `/interactions` en rechargeant la page et en remplaçant la zone
`data-refresh`. DOM construit par `textContent` (aucun HTML injecté).

### Lecture GitHub (`dashboard.github`, utilisée par `GET /`)

- **GitHub** (décision D20 : dépôt public, **aucun jeton**) : API REST `api.github.com`, dépôt
  `VVA_GITHUB_REPO` (défaut `chebihiF/viral-video-agents`, forme `propriétaire/nom` validée). Une seule
  lecture : `issues` (tickets **et** PR, `state=all`, `Link: rel="next"` suivi jusqu'à 5 pages = 500 au
  plus, seuls liens `api.github.com` suivis). Cache mémoire de **3 min** partagé, requêtes
  conditionnelles (ETag ; un 304 ne compte pas dans la limite de 60 requêtes/heure). Toute erreur
  (réseau, JSON, `IncompleteRead`…) : dernières données connues (ou vides) et heure de l'erreur
  (bandeau `role="alert"`), pas de nouvelle tentative avant l'échéance du cache. Sur 403/429 de limite,
  aucune lecture avant `X-RateLimit-Reset` (attente plafonnée à 1 h). Aucun détail d'erreur exposé.
- **Statuts** : livré = fermé avec `state_reason = completed` (fermé « not planned » : exclu) ; revue =
  PR ouverte dont le corps contient `Closes/Fixes/Resolves #n` (ou le titre `(#n)`) ; en cours = label
  `in-progress` ; prêt = `P0`/`P1` non `blocked` ; backlog = le reste. Exclus : PR, épiques, labels
  `decision`, `wontfix`, `duplicate`, `invalid`, `question`.

Limites connues : la limite de 5 essais/minute est globale (compte unique : un tiers peut verrouiller la connexion une minute) et en mémoire (un seul worker ; remise à zéro au redémarrage). Pas de déconnexion : seule la rotation de `DASHBOARD_SESSION_KEY` révoque les sessions (ticket de suivi).

# Câblage de l'ingestion des événements d'agents (#180)

Objectif : que les événements des agents atteignent le dashboard. Les réglages ci-dessous sont
**locaux** (poste du développeur) : `.claude/` n'est jamais versionné. Détail des scripts :
[`tools/telemetry/README.md`](../tools/telemetry/README.md) ; contrats :
[`contracts/agent-event.schema.json`](../contracts/agent-event.schema.json),
[`contracts/ingest-api.md`](../contracts/ingest-api.md).

## 1. Hooks : remplacer `log_event.sh` par `hook_emit.py`

Dans le `.claude/settings.json` local, pour `SubagentStart`, `SubagentStop` et `PostToolUse`,
remplacer la commande `.claude/hooks/log_event.sh` (chemin local du hook installé, non versionné) par :

```
python3 "$CLAUDE_PROJECT_DIR/tools/telemetry/hook_emit.py"
```

Ajouter à `Stop` (en plus des hooks existants) l'envoi en arrière-plan, qui rend la main aussitôt :

```
python3 "$CLAUDE_PROJECT_DIR/tools/telemetry/ship.py" --background
```

Chaque événement devient une ligne au format courant : `event_id` (UUID), `ts` ISO 8601 UTC,
`project`, jamais de prompt ni de sortie d'outil.

## 2. Variables d'environnement (noms seulement)

| Variable | Rôle |
|---|---|
| `VVA_INGEST_URL` | URL complète de l'ingestion (`https`, ou `http` sur localhost) |
| `VVA_INGEST_TOKEN` | jeton Bearer, identique à `INGEST_TOKEN` côté serveur |
| `VVA_PROJECT` | projet émetteur (`agent-factory` ici) |

À définir dans l'environnement local (bloc `"env"` du `settings.json` local, `~/.zshenv` ou
trousseau). **Jamais** de valeur dans le dépôt, une PR, un ticket ou un log. Si l'une des deux
premières manque, `ship.py` le dit sur stderr (code 2), n'envoie rien et ne touche pas au curseur ;
en `--background` il sort en silence.

## 3. Ancien journal (`ts` epoch, sans `event_id` ni `project`)

Au premier passage, `ship.py` reconnaît l'ancien format, le range dans
`.claude/telemetry/events.legacy-AAAAMMJJ.jsonl` et ne l'envoie jamais : plus de boucle de rejet
(`events.jsonl.rejected` cesse de grossir). Le `mv` manuel ci-dessous, vers un dossier **hors
dépôt**, est l'étape **obligatoire** : c'est lui qui garantit que l'ancien journal ne reste
pas dans le dépôt ni ne soit rejoué. À faire sessions fermées :

```sh
mkdir -p ~/.vva/archive
mv .claude/telemetry/events.jsonl ~/.vva/archive/events-ancien-format.jsonl
rm -f .claude/telemetry/events.jsonl.offset
mv .claude/telemetry/events.jsonl.rejected ~/.vva/archive/ 2>/dev/null || true
```

Ne **pas** rejouer cette archive vers le dashboard sans décision explicite. Un convertisseur
jetable reste possible en local, jamais commité.

## 4. Vérification

```sh
echo '{"hook_event_name":"SubagentStart","session_id":"t"}' | python3 tools/telemetry/hook_emit.py
tail -n 1 .claude/telemetry/events.jsonl      # event_id, ts ISO, project présents
python3 tools/telemetry/ship.py; echo $?      # 0 : file vidée ; 2 : variable ou URL à corriger
```

## 5. Reste manuel (hors dépôt)

- Poser `INGEST_TOKEN` en production et le même jeton en `VVA_INGEST_TOKEN` sur le poste : geste
  du propriétaire, aucune action sur la prod par les agents.
- Appliquer les sections 1 à 3 au `.claude/` local de chaque poste de travail.
- Vérifier sur le dashboard que les événements du poste apparaissent après un tour d'agent.

## 6. Vérifier les tokens (OTLP) en production (#184)

À faire par le propriétaire (accès prod et jeton). Aucune valeur `OTEL_*` ni jeton ne se copie
dans un ticket, une PR ou un log : ne coller que les codes HTTP et les lignes de `/tokens`.
`<dashboard>` = hôte du dashboard ; `VVA_INGEST_TOKEN` est déjà dans l'environnement du poste.

**a. La route répond-elle et le jeton est-il bon ?**

```sh
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://<dashboard>/v1/metrics \
  -H "Authorization: Bearer $VVA_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Attendu : `200`. `401` : jeton différent de `INGEST_TOKEN` du serveur ; `503` : `INGEST_TOKEN` non posé
en prod ; `404`/`502` : routage Traefik ou conteneur arrêté.

**b. Une charge de test arrive-t-elle jusqu'à `/tokens` ?** (ajoute 1 token factice « verif-otlp »)

```sh
NOW=$(python3 -c 'import time; print(time.time_ns())')
curl -sS -w '\n%{http_code}\n' -X POST https://<dashboard>/v1/metrics \
  -H "Authorization: Bearer $VVA_INGEST_TOKEN" -H 'Content-Type: application/json' \
  -d '{"resourceMetrics":[{"scopeMetrics":[{"metrics":[{"name":"claude_code.token.usage","sum":{"aggregationTemporality":1,"dataPoints":[{"attributes":[{"key":"session.id","value":{"stringValue":"verif-184"}},{"key":"model","value":{"stringValue":"verif-model"}},{"key":"type","value":{"stringValue":"input"}},{"key":"agent.name","value":{"stringValue":"verif-otlp"}}],"startTimeUnixNano":"'"$((NOW-60000000000))"'","timeUnixNano":"'"$NOW"'","asInt":1}]}}]}]}]}'
```

Attendu : `{}` puis `200`. Sur `https://<dashboard>/tokens` : une ligne `verif-otlp` et une ligne
`verif-model` à 1 token. Si oui, la chaîne serveur (route, jeton, base, page) est saine.

**c. Claude Code exporte-t-il vraiment ?** Redémarrer Claude Code (les `OTEL_*` du `settings.json`
local ne sont lus qu'au démarrage), travailler une minute, recharger `/tokens`. Lire le bas de
page « Dernier envoi OTLP » (ou, page vide, le message d'explication) :

| Constat sur `/tokens` | Cause probable | Action |
|---|---|---|
| « Aucun envoi OTLP reçu » alors que (a) et (b) passent | variables `OTEL_*` absentes, mal nommées ou jamais rechargées côté poste ; ou 401 | revoir le tableau de `tools/telemetry/README.md`, redémarrer ; `docker logs <conteneur vva> 2>&1 \| grep 'POST /v1/metrics'` montre les codes (401 = jeton) |
| « … points de tokens rejetés » | `session.id`/`model`/`type` absent ou temporalité cumulative | `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`, `OTEL_METRICS_INCLUDE_SESSION_ID=true` |
| « … sans compteur `claude_code.token.usage` » | version de Claude Code sans ce compteur, ou nom changé | comparer le nom de métrique émis, ouvrir un ticket |
| Lots reçus mais aucun point de moins de 7 jours | exportateur arrêté | relancer Claude Code |

Compte rendu attendu dans #184 : « source alimentée : oui/non » + résultat de (a), (b), (c).

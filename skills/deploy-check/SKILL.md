---
name: deploy-check
description: Vérifie la prod après un merge (run release, version servie, smoke test en lecture seule), ouvre un bug P0 si KO et propose le rollback par le pipeline. Usage /deploy-check.
---
# /deploy-check

`$DASHBOARD_URL` : URL publique de l'application (variable de dépôt : `gh variable get DASHBOARD_URL`).
Services voisins à vérifier sur le même hôte : surcouche locale du projet. Lecture seule
jusqu'à l'étape 4 : aucune commande sur le serveur, aucun `docker` ni `ssh` à la main.

1. **Run release** : `gh run list -w release -b main -L1` ; en cours → `gh run watch <id>
   --exit-status` (en arrière-plan) ; échec → seulement le log du job en échec
   (`gh run view --job <id> --log-failed`, grep error).
2. **Versions** : version déployée = `curl -fsS "$DASHBOARD_URL/healthz"` ; version attendue =
   celle du dernier merge sur `main` (`gh pr list -B main -s merged -L1 --json number,mergeCommit`,
   dernier tag `v*` ou `<tag>-<sha7>`).
3. **Smoke test** (lecture seule) : `/healthz` répond 200 avec la version attendue ; `curl -fsS
   "$DASHBOARD_URL/"` contient le titre de la page (« Agents Dashboard ») ; services voisins OK.
4. **KO** (smoke test rouge ou version ≠ dernier merge) :
   - bug ouvert **automatiquement** (sauf bug ouvert pour la même version) :
     `gh issue create -l type:bug -l P0 -t "fix(prod): smoke test rouge <version>" -b "<log utile>"` ;
   - pipeline déjà revenu seul à la dernière version saine (rollback automatique du déploiement) :
     le noter, rien d'autre ;
   - sinon **rollback proposé** vers la dernière version saine (dernier run release vert) par le
     mécanisme du pipeline, `gh run rerun <id du run sain>`, lancé seulement après
     **confirmation explicite** du propriétaire (ne jamais toucher un service en cours sans
     accord), puis `/healthz` relu.

## Critères d'acceptation (traçabilité)

| Scénario | Étapes | Rapport |
|---|---|---|
| Production saine | 1 à 3 | version déployée = dernier merge, aucune action |
| Production en échec | 4 | bug P0 créé (lien), rollback proposé puis lancé après confirmation, version restée en prod |

Rapport : **15 lignes** au plus, aucun secret ni donnée personnelle (ni jeton, ni en-tête, ni
contenu utilisateur ; les logs collés dans le bug sont filtrés).

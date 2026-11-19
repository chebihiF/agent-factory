---
name: deploy-check
description: Vérifie la prod du dashboard après un merge (run release, /healthz, page) et pilote un rollback si besoin. Usage /deploy-check.
---
# /deploy-check

`$DASHBOARD_URL` : URL publique du dashboard (variable de dépôt du même nom : `gh variable get DASHBOARD_URL`).

1. Dernier run : `gh run list -w release -b main -L1` ; s'il est en cours, attends-le avec `gh run watch <id> --exit-status` (en arrière-plan) ; s'il a échoué, lis seulement le log du job en échec (`gh run view --job <id> --log-failed`, grep error).
2. Prod : `curl -fsS "$DASHBOARD_URL/healthz"` → version attendue (dernier tag `v*`, ou `<tag>-<sha7>`) ; `curl -fsS "$DASHBOARD_URL/"` contient « Agents Dashboard ». Services voisins sur le même hôte : voir la surcouche du projet.
3. KO après rollback automatique : rapporte la version restée en prod (`/healthz`) et ouvre un `type:bug` P0 avec le log utile.
4. Rollback manuel uniquement avec l'accord du propriétaire : relancer le run release d'une version précédente (`gh run rerun`), jamais de commande docker à la main sur le serveur.
Rapport ≤ 8 lignes.

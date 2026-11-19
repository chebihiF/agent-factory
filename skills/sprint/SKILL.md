---
name: sprint
description: État du sprint en cours du Project Scrum du dépôt courant, enchaînement des tickets Prêt, clôture et ouverture du sprint suivant. Usage /sprint [status|run|close].
---
# /sprint [status|run|close]

Project : celui du dépôt (propriétaire et numéro dans la surcouche locale ; Status : Backlog/Prêt/En cours/Revue/Livré ; itération « Sprint », 1 semaine).

- **status** (défaut) : `gh project item-list <numéro> --owner <propriétaire> --format json -L 100` filtré sur l'itération courante ; affiche un tableau compact n° / titre / priorité / statut, ≤ 15 lignes.
- **run** : prend les tickets « Prêt » du sprint courant par priorité (P0 → P3). Exécute `/ticket <n>` pour chacun. Deux tickets sans fichier commun peuvent partir en parallèle, chacun dans sa propre session et sur son propre poste du pool. Stoppe sur `stop-agents`, sur un échec répété ou sur une décision qui revient au propriétaire.
- **close** : tickets non livrés → sprint suivant (champ Sprint) ; résumé ≤ 10 lignes (livré, reporté, tokens si disponibles) posté en commentaire de l'épique active ; demande au `product-owner` de passer en « Prêt » les tickets du sprint suivant qui respectent la DoR.

---
name: sprint
description: Sprint en cours du Project Scrum du dépôt courant — état par colonne, déplacement d'une carte, enchaînement des tickets Prêt, clôture et ouverture du sprint suivant. Usage /sprint [status|move <n> <colonne>|run|next].
---
# /sprint [status | move <n> <colonne> | run | next]

Project du dépôt : propriétaire `<owner>` et numéro `<num>` dans la surcouche locale. Champs :
`Status` (Backlog / Prêt / En cours / Revue / Livré) et itération `Sprint` (durée : champ `duration` de l'itération, en jours).
Identifiants, lus une fois par appel :
- projet : `gh project view <num> --owner <owner> --format json --jq .id` ;
- champs et options : `gh project field-list <num> --owner <owner> --format json` ;
- cartes : `gh project item-list <num> --owner <owner> --format json -L 200` (carte du ticket
  `n` : `content.number == n` ; sprint courant : `startDate ≤ aujourd'hui < startDate + duration`
  jours). Si la liste compte 200 cartes, elle est peut-être tronquée : le signaler dans le
  rapport (« liste tronquée à 200 cartes ») au lieu de présenter un état faux.

## /sprint status (défaut) — scénario « État du sprint »
Cartes du sprint courant groupées par colonne (`Status`), une ligne par colonne :
`Colonne (k) : #n titre court [P0]…`, puis `Restant : <cartes non Livré>` et `stop-agents :
#n…` (labels des cartes). **15 lignes** au plus ; au-delà, « +k » par colonne.

## /sprint move <n> <colonne> — scénario « Déplacement »
Colonne inconnue → refus, liste des colonnes valides. Sinon
`gh project item-edit --id <item> --project-id <projet> --field-id <Status> --single-select-option-id <option>`,
puis relire la carte : la colonne affichée doit être `<colonne>`. Ticket absent du Project →
`gh project item-add <num> --owner <owner> --url <url de l'issue>` d'abord.

## /sprint run
Tickets « Prêt » du sprint courant par priorité (P0 → P3) : `/ticket <n>` pour chacun. Deux
tickets sans fichier commun peuvent partir en parallèle, chacun dans sa session et sur son
poste du pool. Arrêt sur `stop-agents`, échec répété ou décision qui revient au propriétaire.

## /sprint next (alias : close) — scénario « Sprint suivant »
1. Itérations : `gh api graphql` sur le champ `Sprint` (`... on ProjectV2IterationField
   { id configuration { iterations { id title startDate duration } } }`). Suivante = première
   dont `startDate` suit le sprint courant.
2. Absente → ne jamais la créer par l'API (la mutation de configuration du champ remplace
   toutes les itérations et écraserait les existantes) : ticket `blocked`, ligne
   « Attend l'utilisateur : ajouter l'itération » dans le ticket et le journal du projet,
   aucune carte déplacée.
3. Chaque carte non « Livré » du sprint courant est reportée :
   `gh project item-edit --id <item> --project-id <projet> --field-id <Sprint> --iteration-id <suivante>`.
   Relire : ces cartes portent la nouvelle itération, aucune n'est restée sur l'ancienne.
4. Résumé ≤ 10 lignes (livré, reporté, tokens si disponibles : `token-report.sh`) en commentaire
   de l'épique active ; rétro dans le journal du projet ; le `product-owner` passe en « Prêt »
   les tickets du nouveau sprint qui respectent la DoR.

Rapport : **15 lignes** au plus. Aucun secret ni donnée personnelle dans les commentaires.

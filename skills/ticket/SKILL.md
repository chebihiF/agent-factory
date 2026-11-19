---
name: ticket
description: Boucle complète d'un ticket GitHub du dépôt courant — de l'issue « Prêt » au merge et à la carte « Livré ». Usage /ticket <n°>.
---
# /ticket <n>

Tech lead. Économise les tokens : ne lis que le ticket et les fichiers qu'il cite.
Valeurs propres au projet (dépôt, numéro du Project, compte bot, postes) : surcouche locale du projet.

1. `gh issue view <n>` — refuse si label `stop-agents` ou DoR incomplète (`docs/SCRUM.md`) ; dans ce cas, commente l'issue et arrête.
2. Carte → « En cours » : `gh project item-edit` (Project du dépôt, champ Status).
3. Poste : uniquement un poste du pool (`make ticket N=<n>`, jamais de nouveau poste). Branche : `git fetch -q origin && git switch --no-track -c feat/<n>-<slug> origin/main` (`fix/` pour un bug).
4. Délègue au bon agent avec un brief de 3 lignes (n° d'issue, fichiers, critère de fin) :
   US/TS backend → `dev-backend` (**label `complex` → `model: opus`**), IA → `ai-engineer`, infra → `devops` ; agents propres au projet : voir sa surcouche.
5. `reviewer` sur la branche ; si CHANGEMENTS DEMANDÉS, renvoie au dev (2 tours max, puis demande au propriétaire).
6. `qa` : verdict ACCEPTÉ requis.
7. `make check`, puis `git add <fichiers>` **avant** `make map` (la carte lit l'index git), `git add docs/MAP.md`, commit Conventional Commits (jamais de mention de Claude). Après un rebase sur `origin/main`, refaire `make map`.
8. `git push -u origin <branche>` (compte bot via l'URL de push du dépôt, si le projet l'autorise).
9. PR par le bot : `GH_TOKEN=$(gh auth token -u <bot>) gh pr create -B main -t "<titre CC>" -b "Closes #<n> …"` puis `gh pr merge <url> --auto --squash`. Carte → « Revue ». Si la PR reste `BEHIND` (checks verts mais `main` a avancé) : `GH_TOKEN=$(gh auth token -u <bot>) gh pr update-branch <n°>`, l'auto-merge repart seul.
10. Après merge : `/deploy-check` si le projet déploie, puis carte → « Livré ». Rapport final ≤ 15 lignes.

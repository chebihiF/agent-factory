---
name: ticket
description: Boucle complète d'un ticket GitHub du dépôt courant, de l'issue « Prêt » au merge squash et à la carte « Livré », sans intervention du propriétaire. Usage /ticket <n>.
---
# /ticket <n>

Tech lead, depuis un poste déjà existant du pool (création de poste hors périmètre). Entrée :
le numéro `n` de l'issue ; les commandes `gh` visent le dépôt courant. Économie de tokens : ne
lire que le ticket et les fichiers qu'il cite. Valeurs propres au projet (Project, compte bot
`<bot>`, journal, agents en plus, droit de push) : surcouche locale du projet.

## Contrat (8 étapes)

1. **Lire le ticket** : `gh issue view <n>`. Label `stop-agents` → arrêt immédiat : rien n'est
   modifié (ni carte, ni branche, ni commentaire), l'arrêt est signalé dans le rapport. DoR de
   `docs/SCRUM.md` incomplète → commenter l'issue et arrêter.
2. **Carte « En cours »** (`/sprint move <n> "En cours"`). Poste du pool (`make ticket N=<n>`,
   jamais de nouveau poste), branche `feat/<n>-<slug>` (`fix/` pour un bug) :
   `git fetch -q origin && git switch --no-track -c feat/<n>-<slug> origin/main`.
3. **Implémenter** en déléguant avec un brief de 3 lignes (n° d'issue, fichiers, critère de fin) :
   backend → `dev-backend`, IA → `ai-engineer`, infra → `devops` (agents propres au projet : surcouche).
   Modèle : `tools/agents/bin/token-report.sh route <agent> --ticket <n> --label <label>` (`complex` → Opus).
   Seuls les fichiers listés par le ticket sont touchés ; tout écart est signalé, jamais improvisé.
4. **`make check` vert**, puis `git add <fichiers>` **avant** `make map` (la carte lit l'index git),
   `git add docs/MAP.md` ; refaire `make map` après un rebase. Au plus **2 échecs** : au deuxième,
   arrêt ; la carte reste « En cours », ni push, ni PR, aucun merge ; la cause (première erreur de
   `make check`) est rapportée et l'échec noté (`token-report.sh fail <n> <agent>`).
5. **PR** : commit en Conventional Commits (jamais de mention d'assistant), `git push -u origin
   feat/<n>-<slug>` par le compte bot (si le projet l'autorise), puis
   `GH_TOKEN=$(gh auth token -u <bot>) gh pr create -B main -t "<titre CC>" -b "Closes #<n> …"`.
6. **Carte « Revue »**, agent `reviewer` sur le diff (`git diff origin/main...`) selon le
   déclencheur de `docs/SCRUM.md`, puis `qa` (verdict ACCEPTÉ requis). Changements demandés →
   retour à l'étape 3, 2 tours max ; ensuite ticket bloqué : label `blocked`, ligne « Attend
   l'utilisateur : <question> » dans le ticket et dans le journal du projet, poste libéré.
7. **Merge squash** quand les checks sont verts : `gh pr merge <url> --auto --squash`. PR `BEHIND`
   (checks verts, `main` a avancé) → `GH_TOKEN=$(gh auth token -u <bot>) gh pr update-branch <pr>`.
8. **Carte « Livré »** après le merge, `/deploy-check` vert si le projet déploie et la DoD de
   [`checklist.md`](checklist.md) cochée.

Sortie : rapport de **15 lignes** au plus (fait, fichiers, preuves, risques, prochaine action).

## Critères d'acceptation (traçabilité)

| Scénario | Étapes | Preuve attendue |
|---|---|---|
| Boucle nominale | 1 à 8 | PR `Closes #<n>` par le bot depuis `feat/<n>-<slug>` ; carte En cours → Revue → Livré après le merge squash ; rapport ≤ 15 lignes |
| Ticket bloqué | 1 | label `stop-agents` : rien n'est modifié, arrêt signalé |
| Échecs répétés | 4 | 2 échecs de `make check` : carte « En cours », aucun merge, cause rapportée |

## Interdits

Jamais de push sur `main`, de `--force`, de suppression de branche distante ni de tag à la main.
Aucun secret ni donnée personnelle dans le dépôt, la PR ou le rapport. 0 € : aucun service payant.

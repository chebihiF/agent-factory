# Plafonds de tokens et seuils de relève

Source de vérité versionnée (`rules/token-budget.md`), installée sous `.claude/rules/` par
`make install-agents`. Valeurs proposées : le tech lead les ajuste et les note dans #21.

## 1. Plafond par ticket

- Ticket simple : 150 000 tokens au plus ; ticket `complex` : 300 000.
- Au plus 3 relectures (`reviewer`) et 3 fichiers touchés par ticket ; au-delà, découper.
- Mesure : télémétrie des tokens, `tools/agents/bin/ceiling.sh --db <base> --repo <OWNER/REPO>`
  (`--dry-run` pour simuler). Au dépassement, un commentaire d'alerte (total et plafond) est posé
  sur le ticket, une seule fois par plafond : alerte seulement, aucun agent n'est bloqué.
- Contrat : `contracts/token-ceiling.md`.

## 2. Passation dans le ticket à 60 % du contexte

- À 60 % du contexte de sa session, l'agent écrit dans le ticket une passation de 200 mots au plus :
  fait, reste, fichiers clés. Ni secret, ni donnée d'enfant.

## 3. Seuils de relève du tech lead

- 150 000 tokens de contexte : relève à la prochaine frontière de phase (ticket livré, PR créée),
  passation dans #21 (`rules/session-agents.md`, `handoff.sh post`).
- 250 000 tokens : relève immédiate, passation dans #21, sans attendre la frontière.

## 4. Hooks locaux

Les hooks de `.claude/hooks` restent locaux (jamais commités) : leurs seuils sont alignés à la main
sur cette règle ; en cas d'écart, cette règle fait foi.

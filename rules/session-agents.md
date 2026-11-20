# Règles de session des agents (enchaînement et passation)

Source de vérité versionnée (`rules/session-agents.md`), installée dans le projet cible sous
`.claude/rules/session-agents.md` par `make install-agents` (#71).
Le tech lead (`rules/tech-lead.md`) et les sous-agents l'appliquent.

## 1. Enchaînement des tickets

- Le tech lead termine un ticket, puis prend aussitôt le ticket Prêt suivant du sprint,
  sans rendre la main au parent.
- Il ne s'arrête que dans deux cas : tout est livré, ou tout ce qui reste est `blocked`
  avec « Attend l'utilisateur : <question précise> ».
- Ticket dont la décision dépasse le mandat : label `blocked`, ligne
  « Attend l'utilisateur : <question> » dans le ticket et dans le journal du projet (#21),
  puis ticket Prêt suivant qui n'en dépend pas.
- Décision technique ordinaire (ADR, droits accordés) : trancher et noter dans le journal.

## 2. Passation au seuil de contexte

- Seuil : 150000 tokens de contexte (défaut). Configurable : option `--threshold <tokens>` de
  `handoff.sh check`, dans la commande du hook (`docs/agents.md`).
- Au seuil, le tech lead (le hook ne mesure que le fil principal) écrit une
  note de 10 lignes maximum (fait / reste / fichiers clés / pièges), au format validé par
  `tools/agents/bin/handoff.sh post` (#83) : ni secret, ni donnée d'enfant ; il la poste dans #21.
- La relève est une session indépendante, lancée par le parent humain ; à défaut, `start_session`
  puis archivage immédiat de la sortante (accord du parent, §3). Pas de chaîne parent/enfant
  de sessions, pas de `handoff.sh relay` : chaque rapport de fin de tour réveille le parent.
- Un seul tech lead par brique. Les sous-agents (outil Agent) restent autorisés ; au seuil, un
  sous-agent s'arrête et rend à son appelant un rapport de passation (mêmes rubriques).
- Rapports dans l'issue de suivi (#21) et les PR, pas dans des messages entre sessions.
- Ne pas réimplémenter la détection du seuil : le hook de #83 est la seule source.

## 3. Archivage de l'ancienne session

- La session sortante s'archive uniquement avec l'accord explicite du parent, donné pour cette
  session ; sinon elle reste ouverte, sans être archivée.
  Pas d'accord = pas d'archivage ; ne jamais le déduire d'un accord antérieur.

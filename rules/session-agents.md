# Règles de session des agents (enchaînement et passation)

Source de vérité versionnée, installée dans `.claude/rules/` par `make install-agents` (#71).
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

- Seuil : 150000 tokens de contexte (défaut). Configurable : option `--threshold` de
  `handoff.sh check` dans la commande du hook (`docs/agents.md`).
- Au seuil, toute session (tech lead comme sous-agent) :
  1. écrit une note de 10 lignes maximum (fait / reste / fichiers clés / pièges), au format
     validé par `tools/agents/bin/handoff.sh` (#83) : ni secret, ni donnée d'enfant ;
  2. la poste dans #21 (`handoff.sh post`) ;
  3. ouvre une session neuve qui ne lit que cette note (`handoff.sh relay`), pas l'historique ;
  4. ferme l'ancienne session.
- Ne pas réimplémenter la détection du seuil : le hook de #83 est la seule source.

## 3. Archivage de l'ancienne session

- Par défaut, l'ancienne session est fermée sans être archivée.
- Archiver seulement après un accord explicite du parent, donné pour cette session.
  Pas d'accord = pas d'archivage ; ne jamais le déduire d'un accord antérieur.

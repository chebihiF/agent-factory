# Refonte du dashboard des agents (ticket #179)

Maquette (privée, bureau 1280 et mobile 360, clair/sombre/auto) : https://claude.ai/artifact/KDBKiaKKk8K7unUA4Yv21S . Ouverture directe sur l'écran bureau REMPLI, état « Temps réel ». Variante unique ; Activité et Suivi fusionnés. Données d'exemple. À valider par le parent avant tout développement.

## Décision du parent : LECTURE SEULE
Le dashboard n'envoie aucune commande et ne lance aucun agent. Il affiche (GET) ; toute action se fait sur GitHub ou dans le terminal du tech lead. Un bandeau « Lecture seule : cette page n'envoie aucune commande » est sous le titre.

## Objectif et tâche principale
En 5 secondes le parent sait : (1) ai-je quelque chose à faire ? (2) qui travaille sur quoi ? (3) où en sont les tickets ? (4) combien de quota reste-t-il ?

## Structure HTML sémantique
```
<main>
  <header>  h1 · date et sprint · mention « Lecture seule » · <form aria-label="Filtre par projet"> (variante A de filtre-projet.md) · pastille de connexion (role="status")
  <nav aria-label="Aller à une section">  4 ancres, < 1000 px seulement, sticky
  <div role="alert|status">  bandeau erreur / ingestion muette
  <section id="a-faire">    h2 « À faire par toi » + compteur · <ul> de cartes (3 types)
  <section id="agents">     h2 « Qui fait quoi » · <ul> une ligne par agent
  <section id="avancement"> h2 « Avancement des tickets » · 5 <section aria-label>
  <section id="quotas">     h2 « Quotas et tokens » : jauges · par agent · 7 jours (<ol>) · tickets les plus gourmands
</main>
```
Mise en page par `@container` : >= 1000 px, « À faire » (5/12) et « Qui fait quoi » (7/12) côte à côte, kanban 5 colonnes, quotas 2 colonnes ; en dessous tout empilé.

## Actions : lecture seule
| Élément de la maquette | Effet réel |
|---|---|
| « Ouvrir l'issue sur GitHub ↗ » (carte Attend ta réponse, carte Décision, état vide Tickets) | lien `<a target="_blank" rel="noopener">` vers l'issue (ou la liste des issues) ; la réponse se tape sur GitHub ; nom accessible « (nouvel onglet) » |
| « Voir la PR sur GitHub ↗ » (carte À relire) | lien vers la PR ; la relecture se fait sur GitHub |
| Boutons « Oui / Non » de la décision | RETIRÉS (remplacés par le lien vers l'issue) |
| « Répondre », « Choisir un ticket prêt », « Créer un ticket » | RETIRÉS (libellés qui laissaient croire à une commande) |
| « Voir les tickets prêts », « Aller à l'avancement », « Aller à la liste des agents », barre de navigation | ancres internes de la page |
| Filtre par projet (radios), « Afficher tous les projets » | change l'affichage (`?projet=`), lecture seule |
| « Recharger les données » (bandeau erreur) | relance le `hx-get` de lecture |
| Cartes du kanban, lignes d'agents | non cliquables ; amélioration possible plus tard : titre = lien vers l'issue |
| Boutons « États » et « Thème » en haut de maquette | hors produit (outil de démonstration) ; le thème réel suit `prefers-color-scheme` |
Aucun `hx-post`, aucun `<form method="post">`, aucun jeton d'écriture côté dashboard. Les états vides n'invitent jamais à « lancer » : « Les agents se lancent depuis le terminal du tech lead, pas depuis cette page ».
À corriger au développement : le pictogramme œil de la mention « Lecture seule » doit être un SVG (pas un emoji).

## Composants
- **Carte « À faire »** : bordure gauche + étiquette forme et texte : « ? Attend ta réponse » (danger), « ↗ À relire » (info), « ◆ Décision » (warn) ; titre clair, contexte, ancienneté, 1 lien GitHub >= 44 px.
- **Ligne agent** : rôle en français ; « ● Au travail » / « ○ Au repos » / « ▲ Bloqué » ; titre du ticket (jamais son numéro) + projet ; « Depuis » ; « Signe de vie » (> 2 min pour un agent au travail : warn gras).
- **Colonne kanban** : titre, compteur, 3 cartes + « N de plus » ; carte en cours encadrée 2 px.
- **Jauge** : barre + « N % utilisé » + mot (✓ Confortable < 70 % / ▲ Attention 70-90 % / ✖ Presque épuisé > 90 %) + remise à zéro. Plein / rayures diagonales / rayures verticales.
- **Barres de tokens**, **histogramme 7 jours** : valeur toujours en texte.

## États vides soignés (composant `.empty`)
Cadre pointillé 2 px, illustration SVG inline (`aria-hidden`, `var(--…)`), titre h3 20 px, 1 phrase utile, 1 action (lien ou ancre, jamais une commande).
| Vue | Illustration | Texte | Action |
|---|---|---|---|
| À faire par toi | pastille verte cochée | « Tu es à jour » ; se remplit seule dès que quelque chose t'attend sur GitHub | ancre « Aller à l'avancement » |
| Qui fait quoi | petit robot | « Aucun agent au travail » ; se remplit dès qu'un agent démarre ; ils se lancent depuis le terminal du tech lead | ancre « Voir les tickets prêts » |
| Avancement | 5 colonnes en pointillé | « Aucun ticket pour l'instant » ; colonnes remplies dès qu'un ticket existe sur GitHub | lien « Ouvrir les issues sur GitHub ↗ » + « Afficher tous les projets » |
| Quotas et tokens | cadran à zéro | « Rien consommé aujourd'hui » | ancre « Aller à la liste des agents » |
Maquette : « Rien à faire » (seule la vue À faire vide) et « Tout vide » (4 vues vides). Filtre sans résultat : même composant, « Aucun agent pour <projet> » + « Afficher tous les projets ».

## Autres états
| État | Comportement |
|---|---|
| Temps réel (défaut) | « ● En direct · mis à jour il y a N s » (pulsation coupée si reduced-motion) |
| Chargement | `aria-busy="true"`, squelettes, compteurs « — », filtre utilisable |
| Erreur | bandeau `role="alert"` + heure des dernières données + « Recharger les données » ; dernières données gardées |
| Ingestion muette | serveur OK mais aucun événement depuis N min : bandeau `role="status"` (warn), pastille « ▲ Aucun signe de vie depuis 12 min », signes de vie en warn |

## Composants HTMX (lecture seule)
- `#contenu` : `hx-get="/?projet=…" hx-trigger="load, every 5s"` (ou SSE), `hx-swap="innerHTML"`, `hx-include` garde le filtre, `hx-push-url` sur le filtre.
- Le serveur rend la liste ou le bloc `.empty` de la vue (`{% if not items %}`).
- Erreur réseau : `htmx:sendError` / `htmx:responseError` affiche le bandeau ; « Recharger les données » relance le `hx-get`.
- Ingestion muette : calculée côté serveur (âge du dernier événement > seuil, proposé 5 min).

## Accessibilité (WCAG 2.2 AA)
Texte >= 16 px ; contrastes de `tokens.css` conservés ; focus 2 px ; cibles >= 44 px ; clavier complet ; état = forme + libellé ; illustrations `aria-hidden` ; liens externes annoncés « nouvel onglet » ; `prefers-reduced-motion` ; aucune police ni lib externe ; aucun numéro de ticket brut.

## Tokens à changer dans `apps/dashboard/src/dashboard/static/tokens.css` (après validation)
Couleurs inchangées. Échelle relevée :
```
--font-size-title: 26px;   /* était 24 */
--font-size-heading: 20px; /* était 18 */
--font-size-body: 16px;    /* était 15 */
--font-size-small: 16px;   /* était 13 */
--font-size-label: 16px;   /* était 12 */
--font-size-data: 16px;    /* était 14, mono, chiffres tabulaires */
--target-min: 44px;        /* nouveau : cible tactile */
```

## Questions pour le parent
1. « À faire par toi » en premier : OK ?
2. Seuils de jauge 70 % / 90 % et silence d'ingestion 5 min : OK ?
3. Rôles d'agents en français plutôt que noms techniques : OK ?
4. Sources de « À relire » et « Décision » : PR ouvertes et issues `blocked` : à confirmer.
5. Illustrations simples (robot, colonnes, cadran) : OK, ou ton plus sobre ?
6. Les cartes du kanban doivent-elles devenir des liens vers l'issue GitHub ?

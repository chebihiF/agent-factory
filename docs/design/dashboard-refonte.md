# Refonte du dashboard des agents (ticket #179)

Maquette (privée, bureau 1280 et mobile 360, clair/sombre/auto) : https://claude.ai/artifact/KDBKiaKKk8K7unUA4Yv21S . Ouverture directe sur l'écran bureau REMPLI, état « Temps réel » (jamais une page vide) ; le mobile est le 2e artboard ; des boutons en haut de page basculent les états. Variante unique ; Activité et Suivi fusionnés. Données d'exemple. À valider par le parent avant tout développement.

## Objectif et tâche principale
En 5 secondes le parent sait : (1) ai-je quelque chose à faire ? (2) qui travaille sur quoi ? (3) où en sont les tickets ? (4) combien de quota reste-t-il ? Ordre des sections = cette priorité.

## Structure HTML sémantique
```
<main>
  <header>  h1 · date et sprint · <form aria-label="Filtre par projet"> (variante A de filtre-projet.md) · pastille de connexion (role="status")
  <nav aria-label="Aller à une section">  4 ancres, < 1000 px seulement, sticky
  <div role="alert|status">  bandeau erreur / ingestion muette
  <section id="a-faire">    h2 « À faire par toi » + compteur · <ul> de cartes (3 types)
  <section id="agents">     h2 « Qui fait quoi » · <ul> une ligne par agent
  <section id="avancement"> h2 « Avancement des tickets » · 5 <section aria-label> (Backlog, Prêt, En cours, Revue, Livré)
  <section id="quotas">     h2 « Quotas et tokens » : jauges · par agent · 7 jours (<ol>) · tickets les plus gourmands
</main>
```
Mise en page par `@container` : >= 1000 px, « À faire » (5/12) et « Qui fait quoi » (7/12) côte à côte, kanban 5 colonnes, quotas 2 colonnes ; en dessous tout empilé.

## Composants
- **Carte « À faire »** : bordure gauche + étiquette forme et texte : « ? Attend ta réponse » (danger), « ↗ À relire » (info), « ◆ Décision » (warn) ; titre clair, contexte, ancienneté, 1-2 boutons >= 44 px.
- **Ligne agent** : rôle en français ; « ● Au travail » / « ○ Au repos » / « ▲ Bloqué » ; titre du ticket (jamais son numéro) + projet ; « Depuis » ; « Signe de vie » (> 2 min pour un agent au travail : warn gras).
- **Colonne kanban** : titre, compteur, 3 cartes + « N de plus » ; carte en cours encadrée 2 px.
- **Jauge** : barre + « N % utilisé » + mot (✓ Confortable < 70 % / ▲ Attention 70-90 % / ✖ Presque épuisé > 90 %) + remise à zéro. Plein / rayures diagonales / rayures verticales.
- **Barres de tokens**, **histogramme 7 jours** : valeur toujours en texte.

## États vides soignés (composant `.empty`)
Cadre pointillé 2 px, illustration SVG inline (`aria-hidden`, couleurs en `var(--…)`), titre h3 20 px, 1 phrase utile (max 44 car. de large), 1 action claire (bouton >= 44 px). Chaque vue a le sien, affiché à la place de son contenu, titre de section et compteur conservés (« 0 ») :
| Vue | Illustration | Texte | Action |
|---|---|---|---|
| À faire par toi | pastille verte cochée | « Tu es à jour » : aucune question, relecture ni décision n'attend ton avis | « Voir l'avancement » |
| Qui fait quoi | petit robot endormi | « Tous les agents se reposent » : aucun agent n'a encore travaillé, comment en lancer un | « Choisir un ticket prêt » |
| Avancement / Tickets | 5 colonnes en pointillé | « Aucun ticket pour l'instant » : les colonnes se remplissent dès qu'un ticket existe dans ce projet | « Créer un ticket » + « Voir tous les projets » |
| Quotas et tokens | cadran à zéro | « Rien consommé aujourd'hui » : jauges à 0 %, détails dès le premier travail | « Voir les agents » |
Deux boutons de la maquette : « Rien à faire » (seule la vue À faire est vide : cas heureux) et « Tout vide » (les 4 vues vides, page entière conçue, jamais blanche). Filtre sans résultat : même composant, texte « Aucun agent pour <projet> » + « Voir tous les projets ».

## Autres états
| État | Comportement |
|---|---|
| Temps réel (défaut) | « ● En direct · mis à jour il y a N s » (pulsation coupée si reduced-motion) |
| Chargement | `aria-busy="true"`, squelettes, compteurs « — », filtre utilisable |
| Erreur | bandeau `role="alert"` + heure des dernières données + « Réessayer » ; dernières données gardées |
| Ingestion muette | serveur OK mais aucun événement depuis N min : bandeau `role="status"` (warn), pastille « ▲ Aucun signe de vie depuis 12 min », signes de vie en warn |

## Composants HTMX
- `#contenu` : `hx-get="/?projet=…" hx-trigger="load, every 5s"` (ou SSE), `hx-swap="innerHTML"`, `hx-include` garde le filtre, `hx-push-url` sur le filtre.
- Le serveur rend soit la liste, soit le bloc `.empty` de la vue (gabarits Jinja `{% if not items %}`).
- Décisions : `hx-post`, `hx-swap="outerHTML"` sur la carte ; boutons désactivés pendant la requête.
- Erreur réseau : `htmx:sendError` / `htmx:responseError` affiche le bandeau ; « Réessayer » relance le `hx-get`.
- Ingestion muette : calculée côté serveur (âge du dernier événement > seuil, proposé 5 min).

## Accessibilité (WCAG 2.2 AA)
Texte >= 16 px partout ; contrastes de `tokens.css` conservés ; focus 2 px ; cibles >= 44 px ; clavier complet ; état = forme + libellé ; illustrations décoratives `aria-hidden` ; `prefers-reduced-motion` ; aucune police ni lib externe ; aucun numéro de ticket brut.

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
5. Les illustrations simples (robot, colonnes, cadran) te conviennent-elles, ou veux-tu un ton plus sobre ?

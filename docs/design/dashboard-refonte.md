# Refonte du dashboard des agents (ticket #179)

Maquette (privée, mobile 360 et bureau 1280, 5 états, clair/sombre/auto) : https://claude.ai/artifact/KDBKiaKKk8K7unUA4Yv21S . Variante unique (recommandée) ; les écrans Activité et Suivi sont fusionnés en une page. Données d'exemple, aucune donnée réelle. À valider par le parent avant tout développement.

## Objectif et tâche principale
Le parent ouvre la page pour savoir en 5 secondes : (1) ai-je quelque chose à faire ? (2) qui travaille sur quoi ? (3) où en sont les tickets ? (4) combien de quota me reste-t-il ? L'ordre des sections suit cette priorité.

## Structure HTML sémantique
```
<main>
  <header>  h1 · date et sprint · <form aria-label="Filtre par projet"> (variante A de filtre-projet.md) · pastille de connexion (role="status")
  <nav aria-label="Aller à une section">  4 ancres, visible seulement < 1000 px, sticky
  <div role="alert|status">  bandeau erreur / ingestion muette (si besoin)
  <section id="a-faire">    h2 « À faire par toi » + compteur · <ul> de cartes (3 types)
  <section id="agents">     h2 « Qui fait quoi » · <ul> une ligne par agent
  <section id="avancement"> h2 « Avancement des tickets » · 5 <section aria-label> (Backlog, Prêt, En cours, Revue, Livré), compteur, 3 cartes + « N de plus »
  <section id="quotas">     h2 « Quotas et tokens » : jauges · par agent (jour) · 7 jours (<ol>) · tickets les plus gourmands
</main>
```
Mise en page par `@container` : >= 1000 px, « À faire » (5/12) et « Qui fait quoi » (7/12) côte à côte, kanban 5 colonnes, quotas 2 colonnes ; en dessous tout est empilé.

## Composants
- **Carte « À faire »** : bordure gauche + étiquette forme et texte : « ? Attend ta réponse » (danger), « ↗ À relire » (info), « ◆ Décision » (warn). Titre clair, contexte et ancienneté, 1-2 boutons >= 44 px.
- **Ligne agent** : rôle en français ; état « ● Au travail » / « ○ Au repos » / « ▲ Bloqué » ; titre du ticket (jamais son numéro) + projet en pastille texte ; « Depuis » ; « Signe de vie ». Signe de vie > 2 min pour un agent au travail : `color-warn` gras.
- **Colonne kanban** : titre, compteur, cartes (titre, projet, agent ou « Prêt à prendre ») ; carte « en cours » encadrée 2 px accent.
- **Jauge** : barre + « N % utilisé » + mot (✓ Confortable < 70 % / ▲ Attention 70-90 % / ✖ Presque épuisé > 90 %) + « Remise à zéro dans… ». Remplissage : plein (ok), rayures diagonales (warn), rayures verticales (danger).
- **Barres de tokens** et **histogramme 7 jours** : valeur toujours en texte (« 412 k »), jour courant hachuré.

## États
| État | Comportement |
|---|---|
| Temps réel | « ● En direct · mis à jour il y a N s » (pulsation coupée si reduced-motion) |
| Chargement | `aria-busy="true"`, squelettes, compteurs « — », filtre utilisable |
| Vide | « Aucun agent n'a encore travaillé » + 3 étapes pour lancer un ticket |
| Erreur | bandeau `role="alert"` « Impossible de joindre le serveur… données de HH:MM » + « Réessayer » ; dernières données gardées et datées |
| Ingestion muette | serveur OK mais aucun événement depuis N min : bandeau `role="status"` (warn), pastille « ▲ Aucun signe de vie depuis 12 min », signes de vie des agents au travail en warn |
| Filtre | persiste au rafraîchissement ; vide filtré : « Aucun agent pour <projet> » + « Voir tous les projets » |

## Composants HTMX
- `#contenu` : `hx-get="/?projet=…" hx-trigger="load, every 5s"` (ou SSE), `hx-swap="innerHTML"`, `hx-include` garde le filtre, `hx-push-url` sur le filtre.
- Décisions : `hx-post`, `hx-swap="outerHTML"` sur la carte ; boutons désactivés pendant la requête.
- Erreur réseau : `htmx:sendError` / `htmx:responseError` affiche le bandeau ; « Réessayer » relance le `hx-get`.
- Ingestion muette : calculée côté serveur (âge du dernier événement > seuil, proposé 5 min).

## Accessibilité (WCAG 2.2 AA)
Texte >= 16 px partout ; contrastes de `tokens.css` conservés (AA clair/sombre) ; focus 2 px ; cibles >= 44 px ; clavier complet (boutons, liens, radios natifs) ; état = forme + libellé ; `prefers-reduced-motion` ; aucune police ni lib externe ; aucun numéro de ticket brut.

## Tokens à changer dans `apps/dashboard/src/dashboard/static/tokens.css` (après validation)
Couleurs inchangées. Échelle relevée pour le minimum 16 px :
```
--font-size-title: 26px;   /* était 24 */
--font-size-heading: 20px; /* était 18 */
--font-size-body: 16px;    /* était 15 */
--font-size-small: 16px;   /* était 13 */
--font-size-label: 16px;   /* était 12 */
--font-size-data: 16px;    /* était 14, mono, chiffres tabulaires */
--target-min: 44px;        /* nouveau : cible tactile */
```
Motifs de jauge en CSS pur à partir de `--color-ok|warn|danger`.

## Questions pour le parent
1. « À faire par toi » en premier (priorité aux décisions) : OK ?
2. Seuils de jauge 70 % / 90 % et seuil de silence 5 min : OK ?
3. Rôles d'agents en français (Développeur, Relecteur…) plutôt que noms techniques : OK ?
4. Sources de « À relire » et « Décision » : PR ouvertes et issues `blocked` GitHub : à confirmer.

# Écran « Accueil » (`/`, ticket #179)

Maquette (A recommandée, B alternative, A téléphone ; données d'exemple) : https://claude.ai/artifact/44WXrkCXbHK5Jkb9Yb5Xht (lien privé, section « Refonte #179 »). Remplace l'accueil actuel ; intègre le [filtre par projet](filtre-projet.md) validé. Tokens : `tokens.css` (ajouts : `--font-size-kpi`, `--target-min`, `--layout-max`, `--border-strong`, `--bp-wide`).

## Objectif et lecture en 5 secondes
Le parent répond à 4 questions dans cet ordre, de haut en bas : **que dois-je faire ?** · **où en est le sprint ?** · **qui fait quoi ?** · **combien ça coûte ?**

## Structure HTML sémantique
```
<a class="skip" href="#contenu">Aller au contenu</a>
<nav aria-label="Navigation principale">  Accueil · Tokens · Interactions · Contrôles (Suivi et Activité fusionnés dans l'accueil)
<main id="contenu">
  <header>  h1 « Accueil » · sous-titre sprint/date · pastille <span role="status"> « En direct · il y a 2 s » · <button> « Tout mettre en pause »
  <form aria-label="Filtre par projet">  fieldset/legend + 3 radios (spec filtre-projet.md) · <p role="status"> « Affichage : … »
  <section aria-labelledby>  h2 « À faire par toi » + badge N · <ul> : étiquette (Décision | Valider | Pousser) + phrase + lien-bouton
  <section aria-label="Résumé">  4 tuiles : Livrés S3 (N/M + jauge) · En cours · en revue · Agents au travail (N/M, erreurs) · Tokens du jour (+ jauge % plafond)
  <div class="cols">  >= 900 px : 2fr / 1fr ; sinon une colonne
    <section> h2 « Qui fait quoi » : <table> actifs (Agent+statut+projet · Ticket · Étape · Depuis · Tokens) + <details> « N agents inactifs »
    <section> h2 « Avancement du sprint » : barre empilée role="img" + légende avec effectifs
    <section> h2 « Tokens par agent » : barres + valeur mono
    <section> h2 « Derniers événements » : <ol aria-live="polite">
```
Variante B (tableau par étape : Prêt / En cours / Revue / Livré, cartes) et `<select>` du projet : à choisir par le parent ; mêmes données et mêmes blocs « À faire » et résumé.

## Règles de contenu
- « À faire par toi » est **toujours en premier**, bordure `--border-strong`. Sources : tickets `blocked` / « Attend l'utilisateur », maquettes `needs-design` prêtes, branches revue+QA vertes à pousser, erreurs de pipeline. Chaque ligne : type, phrase d'une ligne, ancienneté, **un** bouton d'action.
- Vide : « Rien à faire. Les agents avancent seuls. » (jamais un bloc absent).
- Étape d'un ticket : ✓ fait, ▶ en cours (gras), libellé toujours écrit. Statuts : ● Actif, ○ Inactif, ▲ Erreur, ❚❚ En pause ; jamais la couleur seule.
- Barre du sprint : Livré = plein, Revue = hachures, En cours = pointillé, Prêt = contour (motifs de [progress.md](progress.md)).
- Étiquettes d'action : texte + fond `*-bg` (warn = Décision, info = Valider, ok = Pousser).

## États
| État | Comportement |
|---|---|
| Temps réel | pastille « En direct » ; blocs rafraîchis (HTMX/SSE), focus et filtre conservés |
| Chargement | `aria-busy="true"`, valeurs « — », pas de saut de mise en page |
| Vide | agents : « Aucun agent n'a encore travaillé » ; à-faire : message ci-dessus |
| Pause | bandeau `role="status"` warn, agents « ❚❚ En pause », bouton devient « Reprendre » ; pause **globale** (tous projets) |
| Erreur | bandeau `role="alert"` danger + heure des dernières données + « Réessayer » ; blocs figés |
| Déconnecté | pastille « Hors ligne depuis HH:MM », données grisées par texte (« dernières données ») |

## Composants HTMX
- Chaque section a son endpoint fragment (`/fragments/a-faire`, `/resume`, `/agents`, `/sprint`, `/tokens`, `/evenements`), `hx-get` + `hx-trigger="load, every 5s"` + `hx-include="[name=projet]"`, ou SSE existant ; `hx-swap="innerHTML"`.
- Filtre : `hx-get="/"` `hx-trigger="change"` `hx-target="#contenu"` `hx-push-url="true"` ; `?projet=` (absent = tous).
- Pause : `hx-post="/controls/pause"` (resp. `resume`), `hx-swap="outerHTML"` sur l'en-tête ; bouton `disabled` pendant la requête ; confirmation en ligne (pas de `confirm()`).
- Pas de lecture seule du jeton ni de donnée enfant à l'écran.

## Accessibilité (WCAG 2.2 AA)
Lien d'évitement, focus 2 px visible, cibles >= 44 px (`--target-min`), contrastes via tokens, flux `aria-live="polite"` (pas assertive), jauges `role="img"` + `aria-label` en pourcentage, tableau avec `caption` masquée et `th scope`, tout au clavier, `prefers-reduced-motion` (aucune animation requise), mobile d'abord : une colonne, segments empilés, tableau en défilement horizontal.

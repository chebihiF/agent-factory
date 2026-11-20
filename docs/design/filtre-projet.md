# Filtre par projet (ticket #68)

Maquette (2 variantes, clair ; données d'exemple) : https://claude.ai/artifact/44WXrkCXbHK5Jkb9Yb5Xht (lien privé). Étend [Activité des agents](activity.md) ; aucun nouveau token.

## Objectif
Le dashboard est multi-projets (`viral-video-agents`, `agent-factory`) : filtrer agents, tuiles et flux par projet, ou tout voir.

## Recommandée : A, groupe de segments
`<form aria-label="Filtre par projet"><fieldset><legend>Projet</legend>` avec 3 `<input type="radio" name="projet">` (natif, clavier : flèches) : « Tous les projets » (défaut), `viral-video-agents`, `agent-factory`. Chaque libellé porte le nombre d'agents (mono). Segment actif : `color-accent` + `color-on-accent` + gras (jamais la couleur seule : le radio coché reste visible). Cibles >= 44 px ; sur mobile les segments passent à la ligne.
Vue « Tous » : colonne **Projet** dans le tableau (pastille `color-info-bg` / `color-warn-bg` + nom, distinction par le texte). Vue filtrée : colonne masquée, titre de section « Agents · <projet> ».
Phrase d'état `role="status"` : « Affichage : <projet> · N agents ».

## B (alternative) : `<select>` natif
Compact dans l'en-tête, même comportement ; moins découvrable, utile si > 4 projets.

## États
| État | Comportement |
|---|---|
| Temps réel | le filtre persiste lors des rafraîchissements ; les compteurs se mettent à jour |
| Chargement | `aria-busy="true"` sur tuiles/tableau/flux, valeurs « — » ; contrôle utilisable |
| Vide | « Aucun agent pour <projet> pour l'instant » + lien « Voir tous les projets » |
| Erreur | bandeau `role="alert"` existant ; filtre inchangé |
| Pause | inchangé ; la pause reste globale (à confirmer) |

## HTMX
- Radios : `hx-get="/activite"` `hx-trigger="change"` `hx-target="#contenu"` `hx-push-url="true"` ; paramètre `?projet=` (absent = tous) ; sondage `every 5s` et SSE conservent le paramètre via `hx-include`.
- Valeur initiale lue dans l'URL côté serveur (lien partageable, retour arrière fonctionnel). Pas de JS requis.
- Projet inconnu dans l'URL : retomber sur « Tous » avec message `role="status"`.

## Accessibilité
Focus visible 2 px, `prefers-reduced-motion` respecté, contrastes AA via tokens existants, annonce du changement par `role="status"`.

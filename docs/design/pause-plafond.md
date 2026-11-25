# Écran « Contrôles » : monitoring en lecture seule + commandes admin (ticket #185)

Maquette (lien privé, v6) : https://claude.ai/artifact/7idGD6RBVWcmgRUdvGimC2 — artboard `Main` = mobile 360 px, artboard `Bureau` = 1280 px, 10 états chacun. Maquette v6 validée par le parent. Tokens : `tokens.css` existant, aucun ajout obligatoire.

> Périmètre livré par #192, #193, #194 : sections « Responsive et navigation » et « Accessibilité » (barre d'onglets, bascule 900 px, cibles, polices, cartes empilées). Connexion admin, Pause, Reprise et Plafond viennent avec la PR d'authentification admin ; l'onglet « Contrôles » est ajouté à ce moment-là.

## Principe (décision du parent 24/11)
- Par défaut, le dashboard est une plateforme de **monitoring en lecture seule : aucun bouton d'action** pour le visiteur.
- Un compte **admin** (le parent seul) se connecte ; une fois connecté apparaissent en plus : **Pause, Reprise, Plafond de tokens**. La connexion admin remplace le mot de passe par action : confirmation **simple (clic confirmé)**.
- Le mécanisme d'authentification (session, cookie, jeton, hachage) n'est pas décidé ici : seul le visuel est spécifié ; le tech lead tranche la technique. Côté rendu, tout fragment de commande est émis **uniquement** si la session est admin (jamais masqué par CSS seul), et le serveur refuse les POST sans session admin.
- Plafond : entier de **1 à 1 000 000 000** tokens, sur **24 h glissantes**.

## Responsive et navigation
Bascule à `--bp-wide` (900 px).
- **< 900 px** : barre d'application haute (60 px : logo, `h1`, bouton cadenas 48 px « Connexion admin » pour le visiteur), bande admin (voir plus bas), `<main>` en cartes (padding 16, écart 16), **barre d'onglets basse** `<nav aria-label="Navigation principale">` (Accueil, Tokens, Interactions, Contrôles ; icône 24 + libellé 13 px ; cible 56 px ; `aria-current="page"` = pastille `--color-surface-2` + libellé gras ; `padding-bottom: max(10px, env(safe-area-inset-bottom))`). La page déclare `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`.
- **>= 900 px** : page web classique. En-tête 64 px (logo + « Dashboard », navigation horizontale à onglets soulignés 3 px accent, zone droite : pastille d'état + accès admin), contenu centré `max-width: 1200px`, `h1` 28 px, puis **tableau de lecture** dans une carte : colonnes Commande · État · Détail (visiteur) ; + Action (admin). Confirmation et saisie du plafond s'ouvrent en ligne sous la ligne concernée, sur toute la largeur.
- Icônes : SVG inline locaux, trait 2 px, `currentColor`, `aria-hidden="true"`. Jeu : accueil, barres, bulle, curseurs, pause, lecture, jauge, cadenas, œil, alerte, coche.

## Vue visiteur (défaut)
- Mobile : bandeau info `role="note"` « Lecture seule. Les commandes sont réservées à l'admin. », carte Pause (état seul), carte Plafond (valeur mono, jauge, « N utilisés · P % »).
- Bureau : tableau 3 colonnes, `<caption>` masquée, `th scope`. Phrase d'intro « Lecture seule… ».
- Accès admin **discret** : mobile = bouton cadenas icône en haut à droite (`aria-label="Connexion admin"`, 48 px) ; bureau = lien « Admin » avec cadenas, bordure fine, à droite de l'en-tête (44 px). Pas de bouton d'action nulle part.
- En pause : bandeau `role="status"` warn « En pause depuis HH:MM » + pastille ❚❚, toujours en lecture seule.

## Écran de connexion admin
Page « Connexion admin » (mobile : carte pleine largeur ; bureau : carte 440 px centrée). Champs : **Identifiant** (`type="text" autocomplete="username" autocapitalize="none" spellcheck="false"`) et **Mot de passe** (`type="password" autocomplete="current-password"` + bouton « Afficher le mot de passe » `aria-pressed`), bouton primaire « Se connecter », lien « Retour à la vue lecture seule ». Champs et boutons 48 px, police 16 px.
- Erreur : bandeau `role="alert"` danger « Identifiant ou mot de passe incorrect. Il te reste N essais cette minute. » ; deux champs `aria-invalid="true"` + `aria-describedby` (« Vérifie les deux champs puis réessaie. »), bordure danger. Ne jamais dire lequel est faux.
- Limite 5 essais / min : bandeau `role="alert"` « Trop d'essais. Limite de 5 par minute atteinte. Réessaie dans N s. » ; champs et bouton `disabled` (« Réessayer dans N s ») ; annonce à l'ouverture et à la réactivation seulement. Le lien de retour reste actif.
- Succès : redirection vers Contrôles en vue admin.

## Vue admin connectée
- Indicateur permanent « ● Connecté en admin » (`role="status"`, fond `--color-info-bg`, texte `--color-info`) + bouton **« Se déconnecter »** (44 px min). Mobile : bande pleine largeur sous l'en-tête, texte 15 px gras ; bureau : pastille + bouton dans l'en-tête.
- Contenu = vue monitoring + commandes : bouton « Mettre en pause » (contour `--color-warn`) / « Reprendre les agents » (primaire) ; champ « Nouveau plafond » (`type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="off"`, police 16 px, suffixe « tokens / 24 h », indice « Entier de 1 à 1 000 000 000, chiffres seulement. ») + bouton « Modifier le plafond » **`disabled` tant que la valeur est invalide ou vide**.
- **Confirmation simple en ligne** (pas de `confirm()`, pas de modale, pas de mot de passe) : panneau « Mettre tous les agents en pause ? » [Confirmer la pause] [Annuler] ; « Changer le plafond ? » + résumé `2 000 000 → 3 000 000`, « tokens / 24 h, soit +50 % » [Confirmer le plafond] [Annuler]. Focus sur le bouton de confirmation à l'ouverture (ou le titre), Échap/Annuler rend le focus au bouton d'origine. Mobile : boutons empilés (confirmer pleine largeur, annuler fantôme) ; bureau : en ligne à droite.
- En pause : bandeau warn, bouton « Reprendre les agents » (même confirmation simple). Succès plafond : bandeau ok `role="status"` « Plafond enregistré. N tokens / 24 h ».
- Valeur hors bornes (message sous le champ, `aria-invalid`, aucun appel serveur) : « Trop grand : le maximum est 1 000 000 000 tokens / 24 h. », « Il faut au moins 1 token. » (0 ou vide), « Chiffres seulement. » (lettres, virgule, point). Validation refaite côté serveur (422).
- Session expirée : retour à la vue lecture seule avec bandeau info « Session expirée, reconnecte-toi » ; les actions en cours ne sont pas appliquées.
- Chargement : bouton `disabled` + « Envoi… », `aria-busy="true"`. Erreur réseau : bandeau `role="alert"` « Action non effectuée, réessaie », état inchangé.

## États dessinés (mobile et bureau)
1 Visiteur actif · 2 Visiteur en pause · 3 Connexion · 4 Connexion refusée · 5 Connexion bloquée (5/min) · 6 Admin connecté · 7 Admin : confirmer la pause · 8 Admin : confirmer le plafond · 9 Admin : en pause + plafond enregistré · 10 Admin : valeur hors bornes.
Un état n'est jamais porté par la couleur seule : glyphe (●, ❚❚, ▲, ✓ ou icône) + libellé.

## Composants HTMX
- Fragments de commandes rendus seulement pour une session admin. Pause : `hx-post="/controls/pause"` (resp. `/controls/resume`), `hx-target` = la section, `hx-swap="outerHTML"`. Plafond : `hx-post="/controls/cap"` (valeur) ; 422 valeur invalide (panneau avec message), 401/403 session absente ou expirée (rendu lecture seule + bandeau).
- Connexion : `hx-post` du formulaire ; 401 identifiants (bandeau + champs), 429 limite avec `Retry-After`. Déconnexion : `hx-post`/formulaire POST, retour à la vue lecture seule.
- Ouverture/fermeture des confirmations : `hx-get` d'un fragment ou `<details>` ; « Afficher le mot de passe » : petit script local ou omis en v1.
- Aucun secret renvoyé dans une réponse ni mis dans l'URL ; aucune donnée enfant à l'écran. L'état « En pause » alimente aussi l'accueil (lecture seule pour le visiteur ; « Tout mettre en pause » / « Reprendre » uniquement en admin).

## Accessibilité (WCAG 2.2 AA)
Cibles >= 44 px (`--target-min` ; 48 px champs et boutons mobiles, onglets 56 px), police des champs >= 16 px (pas de zoom automatique iOS), focus 2 px visible, contrastes via tokens, tout au clavier, label visible sur chaque champ, erreurs reliées par `aria-describedby`, `aria-current="page"`, `prefers-reduced-motion` (aucune animation), un seul `<nav>` principal exposé (masquer l'autre balisage en `display: none`), jamais de défilement horizontal dès 360 px. Zone sûre iOS : `env(safe-area-inset-bottom)` sur la barre basse, `viewport-fit=cover`.

## Questions pour le parent
1. L'onglet « Contrôles » reste-t-il visible du visiteur (lecture seule, hypothèse de la maquette) ou le renommer (ex. « Pilotage ») ?
2. Reprise des agents : un clic confirmé suffit-il (hypothèse) ?
3. Durée de session admin et inactivité : à trancher par le tech lead (technique).

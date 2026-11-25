# ADR-005 — Connexion admin pour les commandes Pause et Plafond

- Statut : accepté (tech lead) — 2026-11-25
- Liens : #185, ADR-002 (stack), `contracts/dashboard-routes.md`, `contracts/control-api.md`,
  `docs/design/pause-plafond.md`

## Contexte
Décision du parent (24/11) : le dashboard reste une plateforme de monitoring en lecture seule ; un
compte admin (le parent seul) fait apparaître Pause et Plafond de tokens, avec une confirmation
simple. Le mécanisme d'authentification était à trancher par le tech lead. Contraintes : 0 €, stdlib,
un seul worker uvicorn derrière Traefik (HTTPS), usage depuis un téléphone.

## Décision
- **Mot de passe admin distinct** du mot de passe du dashboard : hash PBKDF2-HMAC-SHA256 (600 000
  itérations, sel aléatoire, comparaison en temps constant) dans `ADMIN_PASSWORD_HASH`, jamais en
  clair. Variable absente : admin désactivé (403), le dashboard reste en lecture seule.
- **Session côté serveur** (mémoire), désignée par le cookie `vva_admin` signé HMAC-SHA256 avec une
  clé dérivée de `DASHBOARD_SESSION_KEY` et du hash (pas de nouveau secret de signature ; changer le
  mot de passe ferme les sessions). `HttpOnly`, `Secure`, `SameSite=Strict`. Durée **8 h** au plus,
  fermée après **30 min** d'inactivité (glissante). Déconnexion réelle (session supprimée).
- **CSRF** : jeton aléatoire lié à la session dans chaque formulaire POST admin, en plus de
  `SameSite=Strict`.
- **Limite** : 5 essais/min par IP sur `POST /admin/login`, plus 30/min toutes IP (coût PBKDF2).
- **Confirmation côté serveur** : le formulaire POST n'existe que dans le panneau de confirmation et
  exige `confirm=oui` (422 sinon). Codes : 401 non authentifié, 403 CSRF ou admin désactivé, 422
  entrée invalide, 429 limite ; toutes les réponses `no-store`.
- Seules `POST /controls/pause` et `POST /controls/cap` écrivent (test d'exclusivité).

## Conséquences
- Sessions et compteurs en mémoire : un redémarrage (ou un déploiement) déconnecte l'admin ;
  incompatible avec plusieurs workers (déjà le cas de la limite de `/login`).
- Limite « par IP » : effective seulement si uvicorn voit l'IP réelle (`--proxy-headers` et
  `FORWARDED_ALLOW_IPS` = IP de Traefik) ; sinon elle se comporte comme une limite globale.
- Déploiement : `ADMIN_PASSWORD_HASH` doit être transmis au conteneur (compose, `deploy.sh`) ; tant
  qu'il ne l'est pas, l'admin est désactivé.
- Écarts à la maquette : pas de champ « Identifiant » (compte unique), pas de bouton « Reprendre »
  (hors du périmètre du ticket, reprise au terminal), confirmation par panneau GET sans JavaScript.

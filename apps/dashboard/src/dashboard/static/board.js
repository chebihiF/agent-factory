// Tableau de bord (#179) : lecture seule. Recharge #contenu par GET toutes les 5 s (sans
// dépendance : ni CDN, ni bibliothèque) ; aucune requête d'écriture n'est jamais envoyée.
(function () {
  "use strict";
  // Filtre par projet : formulaire GET natif (?projet=). Flèches = déplacer la sélection sans
  // recharger (WCAG 3.2.2) ; clic, Espace ou Entrée envoient (#68).
  var filter = document.querySelector("form[data-autosubmit]");
  if (filter) {
    var FOCUS_KEY = "vva-filter-focus";
    var lastKey = "";
    var viaKeyboard = false;
    var submitFilter = function () {
      try { if (viaKeyboard) window.sessionStorage.setItem(FOCUS_KEY, "1"); } catch (e) { /* ignoré */ }
      filter.requestSubmit();
    };
    filter.addEventListener("pointerdown", function () { lastKey = ""; viaKeyboard = false; });
    filter.addEventListener("keydown", function (e) {
      lastKey = e.key;
      viaKeyboard = true;
      if (e.key === "Enter") { e.preventDefault(); submitFilter(); }
    });
    filter.addEventListener("change", function () {
      if (lastKey.indexOf("Arrow") !== 0) submitFilter();
    });
    try {
      if (window.sessionStorage.getItem(FOCUS_KEY)) {
        window.sessionStorage.removeItem(FOCUS_KEY);
        var checked = filter.querySelector('input[name="projet"]:checked');
        if (checked) checked.focus();
      }
    } catch (e) { /* stockage indisponible */ }
  }

  var content = document.getElementById("contenu");
  var failure = document.getElementById("erreur-reseau");
  if (!content) return;
  var REFRESH_MS = 5000;

  function showFailure(on) {
    if (!failure) return;
    failure.hidden = !on;
    if (on) failure.setAttribute("role", "alert"); else failure.removeAttribute("role");
  }

  async function refresh() {
    if (document.hidden || content.contains(document.activeElement)) return; // ne pas voler le focus
    content.setAttribute("aria-busy", "true");
    try {
      var res = await fetch(content.dataset.src || "/", { credentials: "same-origin", cache: "no-store" });
      if (res.redirected) { window.location.assign("/login"); return; }
      var next = new DOMParser().parseFromString(await res.text(), "text/html").getElementById("contenu");
      if (!res.ok || !next) { showFailure(true); return; } // dernières données gardées
      content.replaceChildren.apply(content, Array.prototype.slice.call(next.childNodes));
      showFailure(false);
    } catch (e) {
      showFailure(true); // réseau coupé : l'affichage actuel reste
    } finally {
      content.removeAttribute("aria-busy");
    }
  }
  setInterval(refresh, REFRESH_MS);
})();

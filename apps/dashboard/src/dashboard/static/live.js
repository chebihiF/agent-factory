// Temps réel : flux SSE /stream (EventSource, reprise automatique par Last-Event-ID).
// Le DOM est construit via textContent / DOMParser : aucune donnée n'est injectée comme HTML.
(function () {
  "use strict";
  // Filtre par projet (#68) : formulaire GET natif (?projet=, lien partageable).
  // WCAG 3.2.2 : les flèches déplacent la sélection du radio sans recharger la page (le focus
  // reste en place) ; clic, Espace ou Entrée envoient. Après un envoi au clavier, le focus revient
  // sur le radio coché de la page rechargée.
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
      if (lastKey.indexOf("Arrow") !== 0) submitFilter(); // ArrowDown/Up/Left/Right : pas d'envoi
    });
    try {
      if (window.sessionStorage.getItem(FOCUS_KEY)) {
        window.sessionStorage.removeItem(FOCUS_KEY);
        var checked = filter.querySelector('input[name="projet"]:checked');
        if (checked) checked.focus();
      }
    } catch (e) { /* stockage indisponible : le focus n'est simplement pas rendu */ }
  }

  function currentProject() {
    var checked = document.querySelector('input[name="projet"]:checked');
    return checked ? checked.value : "";
  }

  var root = document.querySelector("[data-stream]");
  if (!root || !window.EventSource) return;

  var MAX_FEED = 100;
  var REFRESH_DELAY_MS = 400;
  var status = document.getElementById("live-status");
  var refreshSelector = root.getAttribute("data-refresh");
  var timer = null;

  function setStatus(text) {
    if (status) status.textContent = text;
  }

  function span(text, className) {
    var el = document.createElement("span");
    if (className) el.className = className;
    el.textContent = text;
    return el;
  }

  function code(text) {
    var el = document.createElement("code");
    el.textContent = text;
    return el;
  }

  function dash(value) {
    return value || "—";
  }

  function alreadyShown(feed, id) {
    var items = feed.children;
    for (var i = 0; i < items.length; i++) {
      if (items[i].getAttribute("data-event-id") === id) return true;
    }
    return false;
  }

  function addToFeed(feed, ev) {
    if (alreadyShown(feed, ev.event_id)) return;
    var li = document.createElement("li");
    li.setAttribute("data-event-id", ev.event_id);
    var time = document.createElement("time");
    time.setAttribute("datetime", ev.ts);
    time.textContent = ev.time_label;
    var who = span(dash(ev.agent_type) + " · ");
    who.appendChild(code(dash(ev.agent_id)));
    var what = span(ev.hook_event_name + " · ");
    what.appendChild(span(dash(ev.tool_name), "muted"));
    li.append(time, who, what);
    feed.insertBefore(li, feed.firstChild);
    while (feed.children.length > MAX_FEED) feed.removeChild(feed.lastChild);
  }

  function refresh() {
    timer = null;
    fetch(window.location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        return r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status));
      })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html").querySelector(refreshSelector);
        var target = document.querySelector(refreshSelector);
        if (next && target) target.replaceChildren.apply(target, Array.from(next.childNodes).map(function (n) {
          return document.importNode(n, true);
        }));
        updateCounts(new DOMParser().parseFromString(html, "text/html"));
      })
      .catch(function () {});
  }

  function updateCounts(doc) {
    var counts = document.querySelectorAll("[data-count]");
    for (var i = 0; i < counts.length; i++) {
      var key = counts[i].getAttribute("data-count");
      var fresh = Array.prototype.find.call(doc.querySelectorAll("[data-count]"), function (el) {
        return el.getAttribute("data-count") === key;
      });
      if (fresh) counts[i].textContent = fresh.textContent;
    }
    var status = doc.getElementById("filter-status");
    var shown = document.getElementById("filter-status");
    if (status && shown) shown.textContent = status.textContent;
  }

  function scheduleRefresh() {
    if (refreshSelector && timer === null) timer = window.setTimeout(refresh, REFRESH_DELAY_MS);
  }

  var url = "/stream?after=" + encodeURIComponent(root.getAttribute("data-last-id") || "0");
  var source = new EventSource(url, { withCredentials: true });
  source.onopen = function () { setStatus("● En direct"); };
  source.onerror = function () {
    setStatus(source.readyState === EventSource.CLOSED ? "○ Session expirée, reconnectez-vous" : "○ Reconnexion…");
  };
  source.onmessage = function (message) {
    var ev;
    try { ev = JSON.parse(message.data); } catch (e) { return; }
    var feed = document.querySelector("[data-feed]");
    var project = currentProject();
    var matches = !project || ev.project === project; // autre projet : seuls les compteurs bougent
    if (feed && matches) addToFeed(feed, ev);
    if (matches && refreshSelector && !document.querySelector(refreshSelector)) {
      window.location.reload(); // état vide : la page n'a encore ni tableau ni flux à compléter
      return;
    }
    scheduleRefresh();
  };
})();

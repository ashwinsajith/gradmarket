// GradMarket — progressive enhancement over server-rendered markup. No data
// fetch: everything here works on the DOM the server sent, and the page is
// complete without it (no reveal, wordmark always shown, groups static).
"use strict";

(function () {
  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hasObserver = "IntersectionObserver" in window;

  // --- reveal: hero and section heads, once each, never rows ---
  var revealed = document.querySelectorAll("[data-reveal]");
  if (reduceMotion || !hasObserver) {
    revealed.forEach(function (el) { el.classList.add("is-revealed"); });
  } else {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-revealed");
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.15 });
    revealed.forEach(function (el) { revealObserver.observe(el); });
  }

  // --- nav wordmark: only once the hero's own name has scrolled away ---
  var nav = document.querySelector("[data-nav]");
  var hero = document.querySelector("[data-hero]");
  if (nav && hero && hasObserver) {
    nav.classList.add("nav--brand-hidden");
    // The margin trims the sticky nav's height off the viewport, so the hero
    // counts as gone once it's behind the nav. intersectionRatio > 0 because
    // a hero whose bottom edge exactly touches the viewport's top edge still
    // reports isIntersecting — e.g. right after following the #roles link.
    new IntersectionObserver(function (entries) {
      var entry = entries[0];
      nav.classList.toggle("nav--brand-hidden", entry.isIntersecting && entry.intersectionRatio > 0);
    }, { rootMargin: "-" + nav.offsetHeight + "px 0px 0px 0px" }).observe(hero);
  }

  // --- arrivals tooltip ---
  var plot = document.querySelector("[data-arrivals]");
  var tip = document.querySelector("[data-arrivals-tip]");
  if (plot && tip) {
    plot.addEventListener("pointerover", function (e) {
      var bar = e.target.closest(".bar");
      if (!bar) return;
      var n = Number(bar.dataset.arrived);
      tip.textContent = bar.dataset.day + " · " + n + (n === 1 ? " posting" : " postings");
      tip.hidden = false;
      var centre = bar.offsetLeft + bar.offsetWidth / 2;
      var half = tip.offsetWidth / 2;
      tip.style.left = Math.max(half, Math.min(plot.clientWidth - half, centre)) + "px";
    });
    plot.addEventListener("pointerleave", function () { tip.hidden = true; });
  }

  // --- filters, freshness groups, sorting ---
  var form = document.querySelector("[data-filters]");
  if (!form) return;

  // Group headers stick just under the filter bar, whose height changes as
  // its controls wrap.
  if ("ResizeObserver" in window) {
    new ResizeObserver(function () {
      root.style.setProperty("--filters-h", form.offsetHeight + "px");
    }).observe(form);
  }

  var rows = Array.prototype.slice.call(document.querySelectorAll("#roles tr.role"));
  var groups = Array.prototype.slice.call(document.querySelectorAll("#roles tbody.group"));
  // Scoped to the list's own elements. A document-wide lookup once matched a
  // chart bar instead of the count — the chart is never touched from here,
  // and the filters never change it.
  var list = document.getElementById("roles");
  var flat = list.querySelector("[data-flat]");
  var sortNote = list.querySelector("[data-sort-note]");
  var count = form.querySelector("[data-count]");
  var empty = list.querySelector("[data-empty]");
  var headers = document.querySelectorAll("#roles th [data-sort]");
  var grouped = true;
  var current = null; // { key, descending } once a column is sorted

  function plural(n) { return n === 1 ? "1 role" : n + " roles"; }

  function applyFilters() {
    var q = form.elements.q.value.trim().toLowerCase();
    var city = form.elements.city.value;
    var company = form.elements.company.value;
    var source = form.elements.source.value;
    var newOnly = form.elements["new"].checked;
    var shown = 0;

    rows.forEach(function (row) {
      var d = row.dataset;
      var match =
        (!q || d.title.indexOf(q) !== -1 || d.company.indexOf(q) !== -1 || d.location.indexOf(q) !== -1) &&
        (!city || d.cities.split("|").indexOf(city) !== -1) &&
        (!company || d.company === company) &&
        (!source || d.source === source) &&
        (!newOnly || d.new === "true");
      row.hidden = !match;
      if (match) shown += 1;
    });

    // A group with nothing left to show disappears, as an empty one does
    // on the server.
    groups.forEach(function (group) {
      var visible = group.querySelectorAll("tr.role:not([hidden])").length;
      group.hidden = !grouped || visible === 0;
      group.querySelector("[data-group-count]").textContent = plural(visible);
    });

    count.textContent = plural(shown);
    empty.hidden = shown !== 0;
  }

  form.addEventListener("input", applyFilters);
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  // Each comparator sorts its column ascending in its own terms: titles
  // A-Z, first seen oldest first, open fewest days first. (First seen and
  // open are one axis read opposite ways.) A first click sorts the way
  // people usually want; a second click flips it.
  var byFirstSeen = function (a, b) { return Date.parse(a.dataset.firstSeen) - Date.parse(b.dataset.firstSeen); };
  var ascending = {
    title: function (a, b) { return a.dataset.title.localeCompare(b.dataset.title); },
    "first-seen": byFirstSeen,
    open: function (a, b) { return byFirstSeen(b, a); }
  };
  var firstClickDescending = { title: false, "first-seen": true, open: true };

  function setAriaSort(button, direction) {
    headers.forEach(function (other) { other.parentElement.removeAttribute("aria-sort"); });
    if (button) button.parentElement.setAttribute("aria-sort", direction);
  }

  // Sorting by a column collapses the groups into one flat list...
  headers.forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.dataset.sort;
      var descending = current && current.key === key ? !current.descending : firstClickDescending[key];
      current = { key: key, descending: descending };

      rows.sort(function (a, b) { return (descending ? -1 : 1) * ascending[key](a, b); });
      var frag = document.createDocumentFragment();
      rows.forEach(function (row) { frag.appendChild(row); });
      flat.appendChild(frag);

      grouped = false;
      flat.hidden = false;
      sortNote.hidden = false;
      setAriaSort(button, descending ? "descending" : "ascending");
      applyFilters();
    });
  });

  // ...and this puts them back, each row in its group in server order.
  list.querySelector("[data-regroup]").addEventListener("click", function () {
    groups.forEach(function (group) {
      rows
        .filter(function (row) { return row.dataset.group === group.dataset.group; })
        .sort(function (a, b) { return Number(a.dataset.order) - Number(b.dataset.order); })
        .forEach(function (row) { group.appendChild(row); });
    });
    grouped = true;
    current = null;
    flat.hidden = true;
    sortNote.hidden = true;
    setAriaSort(null);
    applyFilters();
    headers[0].focus(); // the "back" button that had focus is now hidden
  });
})();

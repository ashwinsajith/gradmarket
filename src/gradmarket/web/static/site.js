// GradMarket — filtering, sorting and reveal motion over server-rendered
// rows. No data fetch: everything here works on the DOM the server sent.
"use strict";

(function () {
  // --- reveal: hero stats and section heads, once each, never rows ---
  var revealed = document.querySelectorAll("[data-reveal]");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealed.forEach(function (el) { el.classList.add("is-revealed"); });
  } else {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-revealed");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.15 });
    revealed.forEach(function (el) { observer.observe(el); });
  }

  // --- filters ---
  var form = document.querySelector("[data-filters]");
  var tbody = document.querySelector("[data-rows]");
  if (!form || !tbody) return;

  var rows = Array.prototype.slice.call(tbody.rows);
  var count = document.querySelector("[data-count]");
  var empty = document.querySelector("[data-empty]");

  function applyFilters() {
    var q = form.elements.q.value.trim().toLowerCase();
    var company = form.elements.company.value;
    var source = form.elements.source.value;
    var newOnly = form.elements["new"].checked;
    var shown = 0;

    rows.forEach(function (row) {
      var d = row.dataset;
      var match =
        (!q || d.title.indexOf(q) !== -1 || d.company.indexOf(q) !== -1 || d.location.indexOf(q) !== -1) &&
        (!company || d.company === company) &&
        (!source || d.source === source) &&
        (!newOnly || d.new === "true");
      row.hidden = !match;
      if (match) shown += 1;
    });

    count.textContent = shown === 1 ? "1 role" : shown + " roles";
    empty.hidden = shown !== 0;
  }

  form.addEventListener("input", applyFilters);
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  // --- sorting ---
  // Each comparator sorts its column ascending in its own terms: titles
  // A-Z, first seen oldest date first, open fewest days first. (First seen
  // and open are one axis read opposite ways.) A first click on a column
  // sorts it the way people usually want it; a second click flips it.
  var headers = document.querySelectorAll("th [data-sort]");
  var byFirstSeen = function (a, b) { return Date.parse(a.dataset.firstSeen) - Date.parse(b.dataset.firstSeen); };
  var ascending = {
    title: function (a, b) { return a.dataset.title.localeCompare(b.dataset.title); },
    "first-seen": byFirstSeen,
    open: function (a, b) { return byFirstSeen(b, a); }
  };
  var firstClickDescending = { title: false, "first-seen": true, open: true };
  var current = { key: "first-seen", descending: true }; // the server's order

  headers.forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.dataset.sort;
      var descending = current.key === key ? !current.descending : firstClickDescending[key];
      current = { key: key, descending: descending };

      rows.sort(function (a, b) { return (descending ? -1 : 1) * ascending[key](a, b); });
      var frag = document.createDocumentFragment();
      rows.forEach(function (row) { frag.appendChild(row); });
      tbody.appendChild(frag);

      headers.forEach(function (other) { other.parentElement.removeAttribute("aria-sort"); });
      button.parentElement.setAttribute("aria-sort", descending ? "descending" : "ascending");
    });
  });
})();

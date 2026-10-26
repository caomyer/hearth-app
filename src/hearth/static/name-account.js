/*
  Naming an account hearth does not see: the ledger name follows the details as they are
  typed, until the member types one of their own, and the preview shows the account as it will
  read, with the member's own net worth only while they alone are to own it. Nothing here is needed: without it, a ledger name left empty is proposed when the form
  is sent, and the preview reads as the page drew it.
*/
(function () {
  "use strict";

  var form = document.querySelector("form[data-name-account]");
  if (!form) return;
  var ledger = form.elements.ledger;
  var auto = form.elements.auto;
  var member = ledger.dataset.member || "";

  function component(text) {
    var words = (text || "").match(/[A-Za-z0-9]+/g) || [];
    return words.map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); }).join("");
  }

  function proposal() {
    var kind = form.querySelector("input[name=kind]:checked");
    var root = kind ? kind.dataset.root : "Assets";
    var where = (form.elements.where.value.match(/[A-Za-z0-9]+/g) || []);
    var known = where.map(function (w) { return w.toLowerCase(); });
    var rest = (form.elements.name.value.match(/[A-Za-z0-9]+/g) || []).filter(function (w) {
      return known.indexOf(w.toLowerCase()) < 0;
    });
    var name = rest.length ? component(rest.join(" ")) : component(form.elements.name.value);
    if (!name) return "";
    return [root, component(member), where.length ? component(where[0]) : "", name]
      .filter(Boolean).join(":");
  }

  function preview() {
    var name = form.elements.name.value.trim();
    var where = form.elements.where.value.trim();
    var mask = form.elements.mask.value.trim();
    var shown = document.querySelector("[data-preview=name]");
    var second = document.querySelector("[data-preview=where]");
    if (shown) shown.textContent = name || "The new account";
    var worth = document.querySelector("[data-preview=worth]");
    var owner = form.elements.owners.options[form.elements.owners.selectedIndex];
    if (worth && owner) worth.hidden = !owner.hasAttribute("data-alone");
    if (second) {
      second.textContent = [where, mask ? "•• " + mask : ""]
        .filter(Boolean).join(" · ");
    }
  }

  function follow() {
    if (auto.value === "1") ledger.value = proposal();
    preview();
  }

  ledger.addEventListener("input", function () {
    auto.value = ledger.value.trim() ? "0" : "1";
  });
  form.addEventListener("input", function (event) {
    if (event.target !== ledger) follow();
  });
  form.addEventListener("change", function (event) {
    if (event.target !== ledger) follow();
  });
  follow();
})();

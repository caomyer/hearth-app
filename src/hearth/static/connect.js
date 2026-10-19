/*
  Plaid Link, opened on the one page that loads Plaid's script, the statement chooser that
  sends as soon as a file is chosen, and the mapping row that asks for a new account's name only
  while a new account is chosen. Nothing here reads or keeps a token: Link hands the browser a
  one-time public token, which goes straight back to hearth in a form.
*/
(function () {
  "use strict";

  document.querySelectorAll("select[data-target]").forEach(function (select) {
    var row = select.closest(".map");
    select.addEventListener("change", function () {
      var isNew = select.value === "new";
      row.querySelectorAll("[data-when-new]").forEach(function (el) {
        el.hidden = !isNew;
      });
      row.querySelectorAll("[data-when-existing]").forEach(function (el) {
        el.hidden = isNew;
      });
    });
  });

  document.querySelectorAll("input[data-submit-on-choose]").forEach(function (input) {
    input.addEventListener("change", function () {
      if (input.files && input.files.length) input.form.submit();
    });
  });

  var host = document.getElementById("plaid-link");
  if (!host || typeof window.Plaid === "undefined") return;

  var success = document.getElementById(host.dataset.success || host.dataset.update);
  var handler = window.Plaid.create({
    token: host.dataset.linkToken,
    onSuccess: function (publicToken, metadata) {
      success.elements.public_token.value = publicToken;
      if (host.dataset.success) {
        success.elements.institution.value =
          (metadata && metadata.institution && metadata.institution.name) || "";
      }
      success.submit();
    },
    onExit: function () {
      window.location.assign("/connect?outcome=link-closed");
    },
  });
  handler.open();
})();

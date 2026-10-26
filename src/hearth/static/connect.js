/*
  Plaid Link, opened on the pages that load Plaid's script, the statement chooser that sends as
  soon as a file is chosen, and the mapping row that asks for a new account's name only while a
  new account is chosen. Link hands the browser a one-time public token, which goes straight
  back to hearth in a form and is never kept.

  A bank may sign the member in on its own page, taking over this tab; Plaid then brings them
  back to /connect/oauth, where Link must carry on with the same Link token it was opened with.
  So the token is kept in this tab's session storage, and only this tab's, from the moment Link
  opens until it finishes either way or Plaid stops accepting it. The page Plaid comes back to
  is given no token by hearth.

  When Link closes without a connection, what it says of why goes to hearth in a form: Plaid's
  error codes, the step reached, the bank, and Plaid's request id. Never a token.
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

  var KEPT = "hearth.plaid-link";
  var resuming = host.hasAttribute("data-resume");
  var attempt = resuming ? kept() : opened();
  forget();
  if (resuming && !attempt) {
    leave({ resumed: "1", lost: "1" });
    return;
  }
  sessionStorage.setItem(KEPT, JSON.stringify(attempt));

  var lastEvent = "";
  var config = {
    token: attempt.token,
    onSuccess: function (publicToken, metadata) {
      forget();
      var form = document.getElementById(attempt.item ? "reconnected-form" : "exchange-form");
      form.elements.public_token.value = publicToken;
      if (attempt.item) {
        form.elements.item_id.value = attempt.item;
      } else {
        form.elements.institution.value =
          (metadata && metadata.institution && metadata.institution.name) || "";
      }
      form.submit();
    },
    onEvent: function (eventName) {
      // The bank's own sign-in, once reached, decides what closing can have cost.
      if (/OAUTH/.test(lastEvent) && !/OAUTH/.test(eventName)) return;
      lastEvent = eventName;
    },
    onExit: function (error, metadata) {
      var e = error || {};
      var m = metadata || {};
      var institution = m.institution || {};
      leave({
        error_type: e.error_type,
        error_code: e.error_code,
        status: m.status,
        last_event: lastEvent,
        institution_id: institution.institution_id,
        institution_name: institution.name,
        request_id: m.request_id,
        link_session_id: m.link_session_id,
        resumed: resuming ? "1" : "",
      });
    },
  };
  if (resuming) config.receivedRedirectUri = window.location.href;
  window.Plaid.create(config).open();

  function opened() {
    return {
      token: host.dataset.linkToken,
      item: host.dataset.item || "",
      expires: host.dataset.expires || "",
    };
  }

  function kept() {
    var found;
    try {
      found = JSON.parse(sessionStorage.getItem(KEPT) || "null");
    } catch (_) {
      return null;
    }
    if (!found || !found.token) return null;
    if (found.expires && Date.parse(found.expires) <= Date.now()) return null;
    return found;
  }

  function forget() {
    sessionStorage.removeItem(KEPT);
  }

  function leave(fields) {
    forget();
    var form = document.getElementById("exit-form");
    Object.keys(fields).forEach(function (name) {
      form.elements[name].value = fields[name] || "";
    });
    form.submit();
  }
})();

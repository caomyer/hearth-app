/*
  Review from the keyboard. A first review can be a thousand rows, and nobody works through
  that with a mouse. Every shortcut does what a visible control on the page does, so the page
  works the same without this script; the script only moves focus and presses those controls.

  j / k      next and previous item (a group, or a transaction)
  a          accept the focused transaction, or the focused group's suggestions
  s          skip: leave it waiting and move on
  c          change the category; Enter books it, Esc comes back
  x          exclude: not a transaction
  ] / [      next and previous group, or page when going one by one
  u          undo the last decision
*/
(() => {
  "use strict";

  const items = () => Array.from(document.querySelectorAll("[data-group], [data-row]")).filter((el) => !el.closest("details:not([open])"));
  const groups = () => Array.from(document.querySelectorAll("[data-group]"));
  let current = null;

  function show(item) {
    if (!item) return;
    if (current) current.classList.remove("is-current");
    current = item;
    item.classList.add("is-current");
    if (!item.hasAttribute("tabindex")) item.setAttribute("tabindex", "-1");
    item.focus({ preventScroll: true });
    item.scrollIntoView({ block: "nearest" });
  }

  function step(list, by) {
    if (!list.length) return;
    const at = list.indexOf(current);
    if (at === -1) return show(list[by > 0 ? 0 : list.length - 1]);
    show(list[Math.min(Math.max(at + by, 0), list.length - 1)]);
  }

  function owningGroup(item) {
    return item && (item.matches("[data-group]") ? item : item.closest("[data-group]"));
  }

  function jumpGroup(by) {
    const list = groups();
    if (!list.length) {
      const link = document.querySelector(by > 0 ? "[data-next-page]" : "[data-prev-page]");
      if (link) window.location.assign(link.href);
      return;
    }
    const at = list.indexOf(owningGroup(current));
    show(list[at === -1 ? (by > 0 ? 0 : list.length - 1) : Math.min(Math.max(at + by, 0), list.length - 1)]);
  }

  function submit(form) {
    if (!form || form.dataset.busy) return;
    form.dataset.busy = "true";
    form.requestSubmit();
  }

  function accept(item) {
    if (!item) return;
    if (item.matches("[data-group]")) {
      // A group without one clear suggestion has no accept: each of its rows needs a look.
      const form = item.querySelector("[data-accept-group]");
      return form ? submit(form) : show(item.querySelector("[data-row]"));
    }
    const form = item.querySelector("[data-decide]");
    const box = form && form.querySelector("input[name=account]");
    if (box && !box.value.trim()) return change(item);
    submit(form);
  }

  function change(item) {
    const box = item && item.querySelector("[data-decide] input[name=account]");
    if (!box) return;
    box.focus();
    box.select();
  }

  const keys = {
    j: () => step(items(), 1),
    k: () => step(items(), -1),
    s: () => step(items(), 1),
    a: () => accept(current),
    c: () => change(current),
    x: () => current && current.matches("[data-row]") && submit(current.querySelector("[data-exclude]")),
    u: () => submit(document.querySelector("[data-undo]")),
    "]": () => jumpGroup(1),
    "[": () => jumpGroup(-1),
  };

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const field = event.target.closest && event.target.closest("input, select, textarea");
    if (field) {
      if (event.key === "Escape" && field.closest("[data-row]")) {
        event.preventDefault();
        show(field.closest("[data-row]"));
      }
      return;
    }
    const action = keys[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  });

  // Clicking or tabbing into an item makes it the one the keys act on.
  document.addEventListener("focusin", (event) => {
    const item = event.target.closest && event.target.closest("[data-row], [data-group]");
    if (item && item !== current) {
      if (current) current.classList.remove("is-current");
      current = item.matches("[data-row]") ? item : owningGroup(item);
      current.classList.add("is-current");
    }
  });

  document.querySelectorAll("[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => select.form.requestSubmit());
  });

  // Back from a decision: carry on where it left off, at the next row or group.
  const resume = document.querySelector("[data-focus]") || (new URLSearchParams(location.search).has("outcome") && items()[0]);
  if (resume) show(resume);
})();

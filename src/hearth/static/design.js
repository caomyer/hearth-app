// Behaviour shared by the design artefacts: chart tooltips and the review-only theme switch.
// Product behaviour that the server would own (sharing previews, step navigation) lives in each page.
(() => {
  const root = document.documentElement;

  // Theme: system by default. The switch exists so a reviewer can check both modes without changing their OS.
  const themes = ["system", "light", "dark"];
  const apply = (t) => {
    if (t === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", t);
    document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
      b.textContent = `Theme: ${t}`;
    });
  };
  let theme = "system";
  try {
    theme = localStorage.getItem("hearth-design-theme") || "system";
  } catch {
    // Storage can be unavailable for file:// pages; fall back to the system theme.
  }
  apply(theme);
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme-toggle]");
    if (!b) return;
    theme = themes[(themes.indexOf(theme) + 1) % themes.length];
    try {
      localStorage.setItem("hearth-design-theme", theme);
    } catch {
      // Not persisting is fine; the switch still applies for this page.
    }
    apply(theme);
  });

  // Tooltips enhance, never gate: every value is also in the chart's table twin.
  document.querySelectorAll(".chart-wrap").forEach((wrap) => {
    const tip = document.createElement("div");
    tip.className = "tip";
    tip.setAttribute("aria-hidden", "true");
    const tv = document.createElement("div");
    tv.className = "tv";
    const tl = document.createElement("div");
    tl.className = "tl";
    tip.append(tv, tl);
    wrap.append(tip);
    const show = (el) => {
      const [value, label] = (el.dataset.tip || "").split("|");
      tv.textContent = value;
      tl.textContent = label || "";
      const w = wrap.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      tip.style.left = `${Math.min(Math.max(r.left + r.width / 2 - w.left, 70), w.width - 70)}px`;
      tip.style.top = `${r.top - w.top}px`;
      tip.classList.add("on");
    };
    wrap.querySelectorAll("[data-tip]").forEach((el) => {
      el.addEventListener("pointerenter", () => show(el));
      el.addEventListener("pointerleave", () => tip.classList.remove("on"));
    });
  });
})();

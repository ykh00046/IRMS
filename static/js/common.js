/**
 * common.js — DOMContentLoaded bootstrap for IRMS shared UI.
 *
 * After the split-common-js PDCA cycle (2026-05), this file holds only
 * the on-page DOM wiring that runs once per page load:
 *   - mobile nav-toggle hamburger
 *   - initTableScrollHints invocation on DOMContentLoaded
 *
 * Public IRMS.* surface and HTTP clients live in static/js/common/*.js;
 * load those 12 modules before this file. See:
 *   - docs/archive/2026-05/split-common-js/ (PDCA documents)
 *   - docs/02-design/features/split-common-js.design.md §5.2 (load order)
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  // Sidebar drawer toggle (mobile) — opens .app-sidebar with a backdrop.
  const navToggle = document.getElementById("nav-toggle");
  const sidebar = document.getElementById("app-sidebar");
  const backdrop = document.getElementById("app-backdrop");
  if (navToggle && sidebar) {
    const setSidebar = (open) => {
      sidebar.classList.toggle("open", open);
      navToggle.classList.toggle("active", open);
      navToggle.setAttribute("aria-expanded", String(open));
      if (backdrop) backdrop.hidden = !open;
    };
    navToggle.addEventListener("click", () =>
      setSidebar(!sidebar.classList.contains("open")),
    );
    if (backdrop) backdrop.addEventListener("click", () => setSidebar(false));
    sidebar.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => setSidebar(false));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && sidebar.classList.contains("open")) {
        setSidebar(false);
        navToggle.focus();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", IRMS.initTableScrollHints);
  } else {
    IRMS.initTableScrollHints();
  }
})();

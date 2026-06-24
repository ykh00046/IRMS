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

  const navToggle = document.getElementById("nav-toggle");
  const topNav = document.querySelector(".top-nav");
  if (navToggle && topNav) {
    navToggle.addEventListener("click", () => {
      const isOpen = topNav.classList.toggle("open");
      navToggle.classList.toggle("active", isOpen);
      navToggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", IRMS.initTableScrollHints);
  } else {
    IRMS.initTableScrollHints();
  }
})();

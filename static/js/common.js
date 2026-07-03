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

// ── 작업자 세션 유지(전 화면 공통) ─────────────────────────────
// 배합↔점도↔기록을 오가며 작업하므로, 어떤 화면이든 떠 있는 동안은
// 작업자 세션(유휴 5분)이 만료되지 않게 2분마다 연장한다. 세션이 없으면
// 401 이 조용히 무시된다(로그인 페이지로 끌고 가지 않도록 request() 대신
// 순수 fetch 사용). 화면을 닫으면 하트비트가 멈춰 5분 뒤 자연 만료.
(function () {
  "use strict";
  setInterval(() => {
    fetch("/api/blend/session/me", { credentials: "same-origin" }).catch(() => {});
  }, 120000);
})();

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


// ── LOT 대사 배지(책임자 메뉴에만 존재) ───────────────────────────────────
// 이 화면은 들어와야만 숫자가 보이는 자리라 아무도 열지 않았다(2026-09-29 검토).
// 메뉴에 숫자를 붙여 "지금 볼 것이 있는가"를 사이드바가 먼저 말하게 한다.
// 배지 자리는 책임자에게만 렌더되므로 담당자 화면에서는 요청 자체가 없다.
(function () {
  "use strict";
  const badge = document.getElementById("nav-lot-audit-count");
  if (!badge) return;
  fetch("/api/blend/lot-audit/count", { credentials: "same-origin" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data) return;
      const n = Number(data.unresolved || 0);
      if (!n) return;                 // 0 이면 배지를 띄우지 않는다 — 조용한 것이 정상이다.
      badge.textContent = String(n);
      badge.title = `미해소 LOT ${n}건`;
      badge.hidden = false;
    })
    .catch(() => {});
})();

// ── 원재료·코드 감시 배너(Recipe-Intake · 책임자 화면 전용) ──────────────────
// 배너 자리는 책임자에게만 렌더된다. 확인할 항목이 있으면 첫 항목과 건수를, 하루 넘게
// 갱신이 없으면 '감시 결과 오래됨'을 띄운다. 조용하면 아무것도 보이지 않는다.
(function () {
  "use strict";
  const banner = document.getElementById("rw-banner");
  if (!banner) return;
  const modal = document.getElementById("rw-modal");
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const when = (iso) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  fetch("/api/recipe-watch", { credentials: "same-origin" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data || !data.received) return;
      const items = Array.isArray(data.items) ? data.items : [];
      const action = items.filter((i) => i.action_needed);
      if (data.stale) {
        banner.textContent = `원재료 감시 결과 오래됨 · 마지막 수신 ${when(data.received_at)}`;
        banner.classList.add("is-stale");
      } else if (action.length) {
        const first = action[0];
        const head = [first.kind, first.code, first.name].filter(Boolean).join(" ");
        const more = action.length > 1 ? ` 외 ${action.length - 1}건` : "";
        banner.textContent = `원재료 확인 필요 ${action.length}건 · ${head}${more}`;
      } else {
        return;
      }
      banner.hidden = false;
      // 확인 필요 항목을 먼저, 나머지는 받은 순서대로.
      const rows = action.concat(items.filter((i) => !i.action_needed));
      document.getElementById("rw-modal-body").innerHTML = rows.length
        ? rows.map((i) => `<tr class="${i.action_needed ? "rw-action" : ""}">`
            + `<td>${esc(i.kind)}</td><td class="rw-code">${esc(i.code)}</td>`
            + `<td>${esc(i.name)}</td><td>${esc(i.detail)}</td></tr>`).join("")
        : '<tr><td colspan="4">확인할 항목이 없습니다.</td></tr>';
      document.getElementById("rw-modal-sub").textContent =
        [data.erp_file, data.received_at ? `수신 ${when(data.received_at)}` : ""].filter(Boolean).join(" · ");
      banner.addEventListener("click", () => {
        modal.hidden = false;
        document.getElementById("rw-modal-close").focus();
      });
      document.getElementById("rw-modal-close").addEventListener("click", () => { modal.hidden = true; });
      modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") modal.hidden = true; });
    })
    .catch(() => {});
})();

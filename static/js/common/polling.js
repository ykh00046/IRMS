/**
 * polling.js — negative-stock banner poller (60s interval).
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*): none (auto-bootstrap only).
 *
 * Side effects (executed on script parse):
 *   if (document.getElementById("negative-stock-banner")) {
 *     pollNegativeStock();
 *     setInterval(pollNegativeStock, 60000);
 *   }
 *
 * Double-init guarded by IRMS._negStockPollingStarted.
 *
 * Dependencies: core.js (request), format.js (escapeHtml).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  if (IRMS._negStockPollingStarted) return;
  const { request } = IRMS._core;
  const { escapeHtml } = IRMS;

  async function pollNegativeStock() {
    const banner = document.getElementById("negative-stock-banner");
    const list = document.getElementById("neg-stock-list");
    if (!banner || !list) return;
    try {
      const data = await request("/materials/stock");
      const negatives = (data.items || []).filter((m) => m.status === "negative");
      const lows = (data.items || []).filter((m) => m.status === "low");
      if (negatives.length === 0 && lows.length === 0) {
        banner.hidden = true;
        return;
      }
      const parts = [];
      if (negatives.length > 0) {
        const names = negatives.slice(0, 3).map((m) => escapeHtml(m.name)).join(", ");
        const more = negatives.length > 3 ? ` 외 ${negatives.length - 3}건` : "";
        parts.push(`재고 부족: ${names}${more}`);
      }
      if (lows.length > 0) {
        const names = lows.slice(0, 3).map((m) => escapeHtml(m.name)).join(", ");
        const more = lows.length > 3 ? ` 외 ${lows.length - 3}건` : "";
        parts.push(`임계치 미달: ${names}${more}`);
      }
      list.innerHTML = parts.join(" · ");
      banner.hidden = false;
    } catch (_e) {
      // silent fail — don't disrupt page
    }
  }

  // 로그인 사용자가 있을 때만 폴링한다. 무로그인 개방 페이지(/blend, /viscosity)에서
  // /materials/stock(인증 필요) 호출이 401 → 로그인 리다이렉트되는 것을 방지.
  const shell = document.querySelector(".site-shell");
  const loggedIn = !!(shell && shell.dataset && shell.dataset.currentUsername);
  if (loggedIn && document.getElementById("negative-stock-banner")) {
    IRMS._negStockPollingStarted = true;
    pollNegativeStock();
    setInterval(pollNegativeStock, 60000);
  }
})();

/**
 * stock_page.js — 재고·발주·LOT 페이지(/stock)의 상단 KPI + 탭 전환.
 * 자재/LOT/예측/발주 실제 테이블·쓰기는 각 탭 JS(stock.js·lot.js·forecast.js·
 * orders.js)가 담당한다. 여기서는 4개 KPI 카드 숫자와 탭 전환만 처리한다.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;

  // ── 탭 전환 (management.js 미로드 페이지용 경량 구현) ──
  function initTabs() {
    const tabs = document.querySelectorAll(".mgmt-tabs .mgmt-tab");
    const panels = document.querySelectorAll(".tab-panel");
    tabs.forEach((btn) =>
      btn.addEventListener("click", () => {
        tabs.forEach((b) => b.classList.remove("active"));
        panels.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        const panel = document.getElementById(`tab-${btn.dataset.tab}`);
        if (panel) panel.classList.add("active");
      }),
    );
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  async function loadKpis() {
    if (!request) return;
    try {
      const stock = (await request("/materials/stock")).items || [];
      setText("kpi-materials", stock.length);
      setText(
        "kpi-low",
        stock.filter((m) => m.status === "low" || m.status === "negative").length,
      );
    } catch (_e) {
      /* KPI 실패는 탭 데이터에 영향 없음 */
    }
    try {
      const lots = (await request("/materials/lots")).items || [];
      setText(
        "kpi-expiring",
        lots.filter(
          (l) => l.expiry_state === "expiring_soon" || l.expiry_state === "expired",
        ).length,
      );
    } catch (_e) {
      /* noop */
    }
    try {
      const orders = (await request("/orders")).items || [];
      setText(
        "kpi-pending",
        orders.filter(
          (o) => (o.receipt_status || "pending") !== "received" && o.status === "sent",
        ).length,
      );
    } catch (_e) {
      /* noop */
    }
  }

  function init() {
    initTabs();
    loadKpis();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

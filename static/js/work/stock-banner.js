/**
 * stock-banner module — 재고 상태(저/음수) 폴링 및 배너 표시.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createStockBanner(ctx)
 * Returns: { refresh, start }
 * ctx deps:
 *   - ctx.dom.workStockBanner
 *   - ctx.state.lowStockSet (Set<number>; weighing-render가 참조)
 *
 * Polling: 30s setInterval. 이중 start 가드(module-private stockTimer).
 * lowStockSet은 새 Set으로 교체하지 않고 clear()+add()로 갱신해
 * 다른 모듈의 참조를 무효화하지 않는다.
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  const STOCK_POLL_INTERVAL_MS = 30000;

  NS.work.createStockBanner = function (ctx) {
    const { dom, state } = ctx;
    let stockTimer = null;

    async function refresh() {
      try {
        const res = await fetch("/api/materials/stock");
        if (!res.ok) return;
        const data = await res.json();
        state.lowStockSet.clear();
        let neg = 0;
        let low = 0;
        (data.items || []).forEach((m) => {
          if (m.status === "negative") {
            state.lowStockSet.add(m.id);
            neg += 1;
          } else if (m.status === "low") {
            state.lowStockSet.add(m.id);
            low += 1;
          }
        });
        if (dom.workStockBanner) {
          if (neg || low) {
            const parts = [];
            if (neg) parts.push(`음수 재고 ${neg}개`);
            if (low) parts.push(`임계치 미달 ${low}개`);
            dom.workStockBanner.textContent = `⚠ 재고 주의: ${parts.join(", ")} - 책임자에게 알려주세요`;
            dom.workStockBanner.hidden = false;
          } else {
            dom.workStockBanner.hidden = true;
          }
        }
      } catch (_) {
        /* ignore network errors */
      }
    }

    function start() {
      if (stockTimer) {
        clearInterval(stockTimer);
      }
      stockTimer = setInterval(refresh, STOCK_POLL_INTERVAL_MS);
    }

    return { refresh, start };
  };
})();

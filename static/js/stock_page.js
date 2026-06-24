/**
 * stock_page.js — 재고·발주·LOT 통합 조회 페이지 (/stock).
 * 기존 읽기 API(/materials/stock, /materials/lots, /orders)를 모아 시안의
 * KPI + 자재 재고 + LOT 만료 + 발주·입고 레이아웃으로 렌더한다(읽기 전용).
 * (management 탭 컨트롤러 static/js/stock.js 와는 별개)
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;

  const num = (v) => (Number(v) || 0).toLocaleString("ko-KR");
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  function chip(label, kind) {
    return `<span class="stock-chip stock-chip-${kind}">${esc(label)}</span>`;
  }

  const STOCK_STATUS = {
    ok: ["정상", "ok"],
    low: ["부족", "warn"],
    negative: ["마이너스", "danger"],
  };

  const EXPIRY_STATUS = {
    ok: ["정상", "ok"],
    expiring_soon: ["임박", "warn"],
    expired: ["만료", "danger"],
    none: ["무기한", "muted"],
  };

  function dday(days) {
    if (days == null || days === "") return "-";
    const d = Number(days);
    if (d > 0) return `D-${d}`;
    if (d === 0) return "D-DAY";
    return `D+${Math.abs(d)}`;
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function renderStock(items) {
    const body = document.getElementById("stock-body");
    setText("kpi-materials", items.length);
    const low = items.filter((m) => m.status === "low" || m.status === "negative").length;
    setText("kpi-low", low);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="stock-empty">자재가 없습니다.</td></tr>';
      return;
    }
    body.innerHTML = items
      .map((m) => {
        const [label, kind] = STOCK_STATUS[m.status] || ["-", "muted"];
        return `<tr>
          <td>${esc(m.name)}</td>
          <td class="muted">${esc(m.category || "-")}</td>
          <td class="num mono">${num(m.stock_quantity)}</td>
          <td class="num mono muted">${num(m.stock_threshold)}</td>
          <td>${chip(label, kind)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderLots(items) {
    const body = document.getElementById("lot-body");
    const risky = items.filter(
      (l) => l.expiry_state === "expiring_soon" || l.expiry_state === "expired",
    ).length;
    setText("kpi-expiring", risky);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="stock-empty">등록된 LOT이 없습니다.</td></tr>';
      return;
    }
    body.innerHTML = items
      .map((l) => {
        const state = l.expiry_state || "none";
        const [label, kind] = EXPIRY_STATUS[state] || ["-", "muted"];
        return `<tr>
          <td class="mono">${esc(l.lot_no || "-")}</td>
          <td>${esc(l.material_name || "-")}</td>
          <td class="mono muted">${esc(l.expiry_date || "무기한")}</td>
          <td class="num mono">${dday(l.days_until)}</td>
          <td>${chip(label, kind)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderOrders(items) {
    const body = document.getElementById("order-body");
    const pending = items.filter(
      (o) => (o.receipt_status || "pending") !== "received" && o.status === "sent",
    ).length;
    setText("kpi-pending", pending);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="stock-empty">발주 내역이 없습니다.</td></tr>';
      return;
    }
    const RECEIPT = {
      pending: ["대기", "warn"],
      partial: ["부분 입고", "warn"],
      received: ["입고 완료", "ok"],
    };
    body.innerHTML = items
      .map((o) => {
        const [rl, rk] = RECEIPT[o.receipt_status || "pending"] || ["-", "muted"];
        return `<tr>
          <td class="mono">${esc(o.order_no || o.id)}</td>
          <td>${esc(o.status_label || o.status || "-")}</td>
          <td class="num mono">${num(o.item_count)}</td>
          <td class="num mono">${num(o.total_qty)}</td>
          <td>${chip(rl, rk)}</td>
          <td class="muted">${esc((o.created_at || "").slice(0, 10))}</td>
        </tr>`;
      })
      .join("");
  }

  async function load() {
    if (!request) return;
    const tasks = [
      ["/materials/stock", renderStock, "stock-body", 5],
      ["/materials/lots", renderLots, "lot-body", 5],
      ["/orders", renderOrders, "order-body", 6],
    ];
    await Promise.all(
      tasks.map(async ([path, render, bodyId, cols]) => {
        try {
          const payload = await request(path);
          render(payload.items || []);
        } catch (err) {
          const body = document.getElementById(bodyId);
          if (body) {
            body.innerHTML = `<tr><td colspan="${cols}" class="stock-empty">불러오기 실패: ${esc(err.message || err)}</td></tr>`;
          }
        }
      }),
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();

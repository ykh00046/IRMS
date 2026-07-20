/**
 * dashboard.js — 운영 대시보드 컨트롤러.
 *
 * 배합 실적(blend_records) + 점도 현황 기반. 구 계량 지표(완료 레시피·계량
 * 단계·처리량·계량 편차)는 데이터가 더 이상 쌓이지 않아 2026-07 재구축.
 */
document.addEventListener("DOMContentLoaded", () => {
  const presetBtns = document.querySelectorAll(".preset-btn");
  const fromInput = document.getElementById("dash-from");
  const toInput = document.getElementById("dash-to");

  let trendChart = null;
  let productsChart = null;

  const PREF_KEY = "irms_dashboard_range";

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function todayISO() {
    return new Date().toISOString().slice(0, 10);
  }

  function addDaysISO(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function setPresetActive(preset) {
    presetBtns.forEach((button) => {
      button.classList.toggle("active", button.dataset.preset === preset);
    });
  }

  function computeRangeFromPreset(preset) {
    if (preset === "today") return { from: todayISO(), to: todayISO() };
    if (preset === "30d") return { from: addDaysISO(-29), to: todayISO() };
    return { from: addDaysISO(-6), to: todayISO() };
  }

  function getCurrentRange() {
    const from = fromInput.value;
    const to = toInput.value;
    if (from && to) return { from, to };
    return computeRangeFromPreset("7d");
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function qs(range) {
    return `from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`;
  }

  function fmtNumber(value, digits = 0) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    });
  }

  async function loadAll() {
    const range = getCurrentRange();
    const main = document.querySelector("main.page-grid") || document.body;
    IRMS.showLoading(main);
    try {
      const [summary, trend, products, workers, recent] = await Promise.all([
        fetchJSON(`/api/dashboard/summary?${qs(range)}`),
        fetchJSON(`/api/dashboard/trend?${qs(range)}`),
        fetchJSON(`/api/dashboard/products?${qs(range)}&limit=10`),
        fetchJSON(`/api/dashboard/workers?${qs(range)}`),
        fetchJSON("/api/dashboard/recent?limit=10"),
      ]);
      renderSummary(summary);
      renderTrend(trend);
      renderProducts(products);
      renderWorkers(workers);
      renderRecent(recent);
    } catch (error) {
      IRMS.notify(`대시보드 불러오기 실패: ${error.message}`, "error");
    } finally {
      IRMS.hideLoading(main);
    }
  }

  function renderSummary(data) {
    document.getElementById("card-blend-count").textContent = fmtNumber(data.blend_count);
    document.getElementById("card-weight").textContent = fmtNumber(data.total_weight_g, 1);
    document.getElementById("card-products").textContent = fmtNumber(data.product_count);
    // '결재 대기' 카드는 제거됨(결재 현장 미사용) — 요소가 있을 때만 채운다(방어).
    const approval = document.getElementById("card-approval");
    if (approval) {
      approval.textContent = fmtNumber(data.approval_pending);
      approval.style.color = data.approval_pending > 0 ? cssVar("--status-warning", "#c98212") : "";
    }
    const anomaly = document.getElementById("card-visc-anomaly");
    anomaly.textContent = fmtNumber(data.viscosity_anomaly);
    anomaly.style.color = data.viscosity_anomaly > 0 ? cssVar("--status-error", "#d8453f") : "";
    const due = data.viscosity_due_today || [];
    const dueEl = document.getElementById("card-visc-due");
    dueEl.textContent = fmtNumber(due.length);
    dueEl.style.color = due.length > 0 ? cssVar("--status-error", "#d8453f") : "";
    document.getElementById("card-visc-due-codes").textContent = due.length
      ? due.join(", ")
      : "알림 대상 모두 입력됨";
  }

  function renderTrend(data) {
    const labels = data.points.map((point) => point.date.slice(5));
    const counts = data.points.map((point) => point.blend_count);
    const weights = data.points.map((point) => point.total_weight_g);
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(document.getElementById("chart-trend"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "배합 건수",
            data: counts,
            borderColor: cssVar("--brand", "#1b4079"),
            backgroundColor: "rgba(27, 64, 121, 0.15)",
            yAxisID: "y",
            tension: 0.25,
          },
          {
            label: "총 배합량 (g)",
            data: weights,
            // 주의: 이 앱에서 --accent 는 네이비 별칭 — 오렌지는 --accent-secondary
            borderColor: cssVar("--accent-secondary", "#f47c26"),
            backgroundColor: "rgba(244, 124, 38, 0.15)",
            yAxisID: "y1",
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { type: "linear", position: "left", beginAtZero: true },
          y1: {
            type: "linear",
            position: "right",
            beginAtZero: true,
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  function renderProducts(data) {
    const items = data.items || [];
    const labels = items.map((item) => item.product_name);
    const values = items.map((item) => item.total_weight_g);
    if (productsChart) productsChart.destroy();
    productsChart = new Chart(document.getElementById("chart-products"), {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "총 배합량 (g)",
          data: values,
          backgroundColor: cssVar("--brand-mid", "#2c5d9b"),
          // 항목이 1~2개일 때 가로 막대가 패널 높이를 다 채우는 과대 표시 방지
          maxBarThickness: 48,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
      },
    });
  }

  function renderWorkers(data) {
    const body = document.getElementById("workers-body");
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="4"><div class="empty-state">데이터 없음</div></td></tr>';
      return;
    }
    body.innerHTML = items
      .map(
        (w) => `
          <tr>
            <td>${IRMS.escapeHtml(w.worker)}</td>
            <td class="num">${fmtNumber(w.blend_count)}</td>
            <td class="num">${fmtNumber(w.total_weight_g, 1)}</td>
            <td class="num">${fmtNumber(w.product_count)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderRecent(data) {
    const body = document.getElementById("recent-body");
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6"><div class="empty-state">배합 기록 없음</div></td></tr>';
      return;
    }
    body.innerHTML = items
      .map((r) => {
        // LOT 이 {반제품명}{YYMMDD}{순번} 이라 반제품 열은 중복 — LOT 한 칸으로 통합
        const lot = r.reactor
          ? `${IRMS.escapeHtml(r.product_lot)}<span class="muted small dash-lot-reactor">반응기 ${r.reactor}</span>`
          : IRMS.escapeHtml(r.product_lot);
        const workDate = (r.work_date || "-").length === 10 ? r.work_date.slice(5) : (r.work_date || "-");
        return `
          <tr>
            <td>${lot}</td>
            <td>${IRMS.escapeHtml(workDate)}</td>
            <td>${IRMS.escapeHtml(r.worker || "-")}</td>
            <td class="num">${fmtNumber(r.total_amount, 1)}</td>
            <td>${r.has_viscosity ? "입력" : '<span class="muted">미입력</span>'}</td>
            <td>${r.approved ? "완료" : '<span class="muted">대기</span>'}</td>
          </tr>
        `;
      })
      .join("");
  }

  function persistRange(range) {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(range));
    } catch {}
  }

  function restoreRange() {
    try {
      const saved = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (saved && saved.from && saved.to) {
        fromInput.value = saved.from;
        toInput.value = saved.to;
        setPresetActive(null);
        return;
      }
    } catch {}
    const range = computeRangeFromPreset("7d");
    fromInput.value = range.from;
    toInput.value = range.to;
  }

  presetBtns.forEach((button) => {
    button.addEventListener("click", () => {
      const preset = button.dataset.preset;
      setPresetActive(preset);
      const range = computeRangeFromPreset(preset);
      fromInput.value = range.from;
      toInput.value = range.to;
      persistRange(range);
      loadAll();
    });
  });

  document.getElementById("dash-apply").addEventListener("click", () => {
    if (!fromInput.value || !toInput.value) {
      IRMS.notify("시작일과 종료일을 모두 입력하세요.", "warn");
      return;
    }
    setPresetActive(null);
    persistRange({ from: fromInput.value, to: toInput.value });
    loadAll();
  });
  document.getElementById("dash-refresh").addEventListener("click", loadAll);
  document.getElementById("dash-export").addEventListener("click", () => {
    window.location.assign(`/api/dashboard/export?${qs(getCurrentRange())}`);
  });

  restoreRange();
  loadAll();
});

document.addEventListener("DOMContentLoaded", () => {
  const presetBtns = document.querySelectorAll(".preset-btn");
  const fromInput = document.getElementById("dash-from");
  const toInput = document.getElementById("dash-to");
  const applyBtn = document.getElementById("dash-apply");
  const refreshBtn = document.getElementById("dash-refresh");

  const cardCompleted = document.getElementById("card-completed");
  const cardMeasurement = document.getElementById("card-measurement");
  const cardWeight = document.getElementById("card-weight");
  const cardThroughput = document.getElementById("card-throughput");
  const cardActualCoverage = document.getElementById("card-actual-coverage");
  const cardVarianceTotal = document.getElementById("card-variance-total");

  const operatorsBody = document.getElementById("operators-body");
  const materialModal = document.getElementById("material-modal");
  const materialModalTitle = document.getElementById("material-modal-title");
  const materialModalBody = document.getElementById("material-modal-body");
  const materialModalClose = document.getElementById("material-modal-close");

  const varianceModal = document.getElementById("variance-modal");
  const varianceModalTitle = document.getElementById("variance-modal-title");
  const varianceModalBody = document.getElementById("variance-modal-body");
  const varianceModalClose = document.getElementById("variance-modal-close");

  const trendCanvas = document.getElementById("chart-trend");
  const materialsCanvas = document.getElementById("chart-materials");
  const throughputCanvas = document.getElementById("chart-throughput");
  const varianceCanvas = document.getElementById("chart-variance");

  let trendChart = null;
  let materialsChart = null;
  let throughputChart = null;
  let varianceChart = null;
  let currentMaterials = [];
  let currentVariances = [];

  const PREF_KEY = "irms_dashboard_range";
  const ALERT_STATUS_LABEL = { urgent: "긴급", soon: "임박" };
  const EXPIRY_STATE_LABEL = { expired: "만료", expiring_soon: "임박" };

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

  function fmtPct(value) {
    return value === null || value === undefined ? "-" : Number(value).toFixed(1);
  }

  async function loadAll() {
    const range = getCurrentRange();
    const main = document.querySelector("main.page-grid") || document.body;
    IRMS.showLoading(main);
    try {
      const [summary, trend, materials, throughput, operators, varianceSummary, variances] =
        await Promise.all([
          fetchJSON(`/api/dashboard/summary?${qs(range)}`),
          fetchJSON(`/api/dashboard/trend?${qs(range)}`),
          fetchJSON(`/api/dashboard/materials?${qs(range)}&limit=10`),
          fetchJSON(`/api/dashboard/throughput?${qs(range)}`),
          fetchJSON(`/api/dashboard/operators?${qs(range)}`),
          fetchJSON(`/api/dashboard/variance/summary?${qs(range)}`),
          fetchJSON(`/api/dashboard/variance/materials?${qs(range)}&limit=10`),
        ]);
      renderSummary(summary, varianceSummary);
      renderTrend(trend);
      renderMaterials(materials);
      renderThroughput(throughput);
      renderOperators(operators);
      renderVarianceSummary(varianceSummary);
      renderVariances(variances);
    } catch (error) {
      IRMS.notify(`대시보드 불러오기 실패: ${error.message}`, "error");
    } finally {
      IRMS.hideLoading(main);
    }
  }

  function renderSummary(data, variance) {
    cardCompleted.textContent = fmtNumber(data.completed_recipe_count);
    cardMeasurement.textContent = fmtNumber(data.measurement_count);
    cardWeight.textContent = fmtNumber(data.total_weight_g, 2);
    cardThroughput.textContent = fmtNumber(data.throughput_per_hour, 1);
    cardActualCoverage.textContent = fmtPct(variance.coverage_pct);
    cardVarianceTotal.textContent = fmtNumber(variance.deviation_total_g, 2);
  }

  function renderTrend(data) {
    const labels = data.points.map((point) => point.date.slice(5));
    const counts = data.points.map((point) => point.completed_count);
    const weights = data.points.map((point) => point.total_weight_g);
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(trendCanvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "완료 레시피",
            data: counts,
            borderColor: "#2563eb",
            backgroundColor: "rgba(37, 99, 235, 0.15)",
            yAxisID: "y",
            tension: 0.25,
          },
          {
            label: "목표 사용량 (g)",
            data: weights,
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.15)",
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

  function renderMaterials(data) {
    currentMaterials = data.items || [];
    const labels = currentMaterials.map((item) => item.material_name);
    const values = currentMaterials.map((item) => item.total_weight_g);
    if (materialsChart) materialsChart.destroy();
    materialsChart = new Chart(materialsCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "사용량 (g)", data: values, backgroundColor: "#16a34a" }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const item = currentMaterials[elements[0].index];
          if (item) openMaterialDrill(item.material_id, item.material_name);
        },
      },
    });
  }

  function renderThroughput(data) {
    const labels = (data.by_day || []).map((point) => point.date.slice(5));
    const values = (data.by_day || []).map((point) => point.throughput_per_hour);
    if (throughputChart) throughputChart.destroy();
    throughputChart = new Chart(throughputCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "회/시간", data: values, backgroundColor: "#8b5cf6" }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  function renderOperators(data) {
    const items = data.items || [];
    if (!items.length) {
      operatorsBody.innerHTML = '<tr><td colspan="4"><div class="empty-state">데이터 없음</div></td></tr>';
      return;
    }
    operatorsBody.innerHTML = items
      .map(
        (op) => `
          <tr>
            <td>${IRMS.escapeHtml(op.operator)}</td>
            <td class="num">${fmtNumber(op.measurement_count)}</td>
            <td class="num">${fmtNumber(op.total_weight_g, 2)}</td>
            <td class="num">${fmtNumber(op.completed_recipe_count)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderVarianceSummary(data) {
    document.getElementById("variance-measured-count").textContent = fmtNumber(data.measured_count);
    document.getElementById("variance-actual-count").textContent = fmtNumber(data.actual_count);
    document.getElementById("variance-target-total").textContent = fmtNumber(data.target_total_g, 2);
    document.getElementById("variance-actual-total").textContent = fmtNumber(data.actual_total_g, 2);
    document.getElementById("variance-abs-total").textContent = fmtNumber(data.absolute_deviation_total_g, 2);
  }

  function renderVariances(data) {
    currentVariances = data.items || [];
    const labels = currentVariances.map((item) => item.material_name);
    const values = currentVariances.map((item) => item.absolute_deviation_g);
    if (varianceChart) varianceChart.destroy();
    varianceChart = new Chart(varianceCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "|편차| (g)", data: values, backgroundColor: "#dc2626" }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const item = currentVariances[elements[0].index];
          if (item) openVarianceDrill(item.material_id, item.material_name);
        },
      },
    });
  }

  async function loadForecastAlert() {
    const card = document.getElementById("forecast-alert");
    if (!card) return;
    try {
      const res = await fetch("/api/dashboard/forecast-alert", { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.reorder_recommended) {
        card.hidden = true;
        return;
      }
      document.getElementById("forecast-alert-summary").textContent =
        `발주 권장 ${data.reorder_recommended}건 (긴급 ${data.urgent}, 임박 ${data.soon})`;
      document.getElementById("forecast-alert-body").innerHTML = data.items
        .map((item) => {
          const cls = item.status === "urgent" ? "stock-negative" : "stock-low";
          return `
            <tr>
              <td>${IRMS.escapeHtml(item.name)}</td>
              <td><span class="stock-status ${cls}">${ALERT_STATUS_LABEL[item.status] || item.status}</span></td>
              <td class="num">${item.days_remaining ?? "-"}</td>
              <td>${IRMS.escapeHtml(item.predicted_stockout_date || "-")}</td>
              <td class="num">${fmtNumber(item.recommended_order_qty, 2)}</td>
            </tr>`;
        })
        .join("");
      card.hidden = false;
    } catch {
      card.hidden = true;
    }
  }

  function ddayLabel(n) {
    if (n === null || n === undefined) return "-";
    if (n < 0) return `D+${-n}`;
    if (n === 0) return "D-day";
    return `D-${n}`;
  }

  async function loadExpiryAlert() {
    const card = document.getElementById("expiry-alert");
    if (!card) return;
    try {
      const res = await fetch("/api/dashboard/expiry-alert", { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.total_alert) {
        card.hidden = true;
        return;
      }
      document.getElementById("expiry-alert-summary").textContent =
        `유통기한 주의 ${data.total_alert}건 (만료 ${data.expired}, 임박 ${data.expiring_soon})`;
      document.getElementById("expiry-alert-body").innerHTML = data.items
        .map((item) => {
          const cls = item.expiry_state === "expired" ? "stock-negative" : "stock-low";
          return `
            <tr>
              <td>${IRMS.escapeHtml(item.material_name)}</td>
              <td>${IRMS.escapeHtml(item.lot_no || "-")}</td>
              <td><span class="stock-status ${cls}">${EXPIRY_STATE_LABEL[item.expiry_state] || item.expiry_state}</span></td>
              <td>${IRMS.escapeHtml(item.expiry_date || "-")}</td>
              <td class="num">${ddayLabel(item.days_until)}</td>
              <td class="num">${fmtNumber(item.remaining_quantity, 2)}</td>
            </tr>`;
        })
        .join("");
      card.hidden = false;
    } catch {
      card.hidden = true;
    }
  }

  async function openMaterialDrill(materialId, materialName) {
    const range = getCurrentRange();
    try {
      const data = await fetchJSON(`/api/dashboard/materials/${materialId}/recipes?${qs(range)}`);
      materialModalTitle.textContent = `${materialName} · 상세`;
      const recipes = data.recipes || [];
      materialModalBody.innerHTML = recipes.length
        ? recipes
            .map(
              (recipe) => `
                <tr>
                  <td>${IRMS.escapeHtml(recipe.product_name || "-")}</td>
                  <td>${IRMS.escapeHtml(recipe.ink_name || "-")}</td>
                  <td class="num">${fmtNumber(recipe.weight_g, 2)}</td>
                  <td>${IRMS.escapeHtml(recipe.measured_by)}</td>
                  <td>${IRMS.formatDateTime(recipe.measured_at)}</td>
                </tr>
              `,
            )
            .join("")
        : '<tr><td colspan="5"><div class="empty-state">데이터 없음</div></td></tr>';
      materialModal.hidden = false;
    } catch (error) {
      IRMS.notify(`재료 상세 조회 실패: ${error.message}`, "error");
    }
  }

  async function openVarianceDrill(materialId, materialName) {
    const range = getCurrentRange();
    try {
      const data = await fetchJSON(`/api/dashboard/variance/materials/${materialId}/recipes?${qs(range)}`);
      varianceModalTitle.textContent = `${materialName} · 편차 상세`;
      const recipes = data.recipes || [];
      varianceModalBody.innerHTML = recipes.length
        ? recipes
            .map(
              (recipe) => `
                <tr>
                  <td>${IRMS.escapeHtml(recipe.product_name || "-")}</td>
                  <td>${IRMS.escapeHtml(recipe.ink_name || "-")}</td>
                  <td class="num">${fmtNumber(recipe.target_weight_g, 2)}</td>
                  <td class="num">${fmtNumber(recipe.actual_weight_g, 2)}</td>
                  <td class="num">${fmtNumber(recipe.deviation_g, 2)}</td>
                  <td class="num">${recipe.deviation_pct === null ? "-" : fmtNumber(recipe.deviation_pct, 2)}</td>
                  <td>${IRMS.escapeHtml(recipe.measured_by)}</td>
                  <td>${IRMS.formatDateTime(recipe.measured_at)}</td>
                </tr>
              `,
            )
            .join("")
        : '<tr><td colspan="8"><div class="empty-state">실측 데이터 없음</div></td></tr>';
      varianceModal.hidden = false;
    } catch (error) {
      IRMS.notify(`편차 상세 조회 실패: ${error.message}`, "error");
    }
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

  applyBtn.addEventListener("click", () => {
    if (!fromInput.value || !toInput.value) {
      IRMS.notify("시작일과 종료일을 모두 입력하세요.", "warn");
      return;
    }
    setPresetActive(null);
    persistRange({ from: fromInput.value, to: toInput.value });
    loadAll();
  });
  refreshBtn.addEventListener("click", loadAll);
  materialModalClose.addEventListener("click", () => {
    materialModal.hidden = true;
  });
  varianceModalClose.addEventListener("click", () => {
    varianceModal.hidden = true;
  });

  restoreRange();
  loadAll();
  loadForecastAlert();
  loadExpiryAlert();
});

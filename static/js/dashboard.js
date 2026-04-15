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

  const operatorsBody = document.getElementById("operators-body");
  const materialModal = document.getElementById("material-modal");
  const materialModalTitle = document.getElementById("material-modal-title");
  const materialModalBody = document.getElementById("material-modal-body");
  const materialModalClose = document.getElementById("material-modal-close");

  const trendCanvas = document.getElementById("chart-trend");
  const materialsCanvas = document.getElementById("chart-materials");
  const throughputCanvas = document.getElementById("chart-throughput");

  let trendChart = null;
  let materialsChart = null;
  let throughputChart = null;
  let currentMaterials = [];

  const PREF_KEY = "irms_dashboard_range";

  function todayISO() {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  }
  function addDaysISO(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function setPresetActive(preset) {
    presetBtns.forEach((b) => b.classList.toggle("active", b.dataset.preset === preset));
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
    return `from=${range.from}&to=${range.to}`;
  }

  async function loadAll() {
    const range = getCurrentRange();
    try {
      const [summary, trend, materials, throughput, operators] = await Promise.all([
        fetchJSON(`/api/dashboard/summary?${qs(range)}`),
        fetchJSON(`/api/dashboard/trend?${qs(range)}`),
        fetchJSON(`/api/dashboard/materials?${qs(range)}&limit=10`),
        fetchJSON(`/api/dashboard/throughput?${qs(range)}`),
        fetchJSON(`/api/dashboard/operators?${qs(range)}`),
      ]);
      renderSummary(summary);
      renderTrend(trend);
      renderMaterials(materials);
      renderThroughput(throughput);
      renderOperators(operators);
    } catch (error) {
      IRMS.notify(`대시보드 로드 실패: ${error.message}`, "error");
    }
  }

  function renderSummary(data) {
    cardCompleted.textContent = (data.completed_recipe_count || 0).toLocaleString();
    cardMeasurement.textContent = (data.measurement_count || 0).toLocaleString();
    cardWeight.textContent = (data.total_weight_g || 0).toLocaleString();
    cardThroughput.textContent = (data.throughput_per_hour || 0).toFixed(1);
  }

  function renderTrend(data) {
    const labels = data.points.map((p) => p.date.slice(5));
    const counts = data.points.map((p) => p.completed_count);
    const weights = data.points.map((p) => p.total_weight_g);
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
            label: "총 사용량(g)",
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
          y: { type: "linear", position: "left", beginAtZero: true, title: { display: true, text: "건" } },
          y1: { type: "linear", position: "right", beginAtZero: true, title: { display: true, text: "g" }, grid: { drawOnChartArea: false } },
        },
      },
    });
  }

  function renderMaterials(data) {
    currentMaterials = data.items || [];
    const labels = currentMaterials.map((m) => m.material_name);
    const values = currentMaterials.map((m) => m.total_weight_g);
    if (materialsChart) materialsChart.destroy();
    materialsChart = new Chart(materialsCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "사용량(g)", data: values, backgroundColor: "#16a34a" }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const idx = elements[0].index;
          const mat = currentMaterials[idx];
          if (mat) openMaterialDrill(mat.material_id, mat.material_name);
        },
      },
    });
  }

  function renderThroughput(data) {
    const labels = (data.by_day || []).map((d) => d.date.slice(5));
    const values = (data.by_day || []).map((d) => d.throughput_per_hour);
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
            <td class="num">${op.measurement_count.toLocaleString()}</td>
            <td class="num">${op.total_weight_g.toLocaleString()}</td>
            <td class="num">${op.completed_recipe_count.toLocaleString()}</td>
          </tr>
        `,
      )
      .join("");
  }

  async function openMaterialDrill(materialId, materialName) {
    const range = getCurrentRange();
    try {
      const data = await fetchJSON(`/api/dashboard/materials/${materialId}/recipes?${qs(range)}`);
      materialModalTitle.textContent = `${materialName} · 상세`;
      const recipes = data.recipes || [];
      if (!recipes.length) {
        materialModalBody.innerHTML = '<tr><td colspan="5"><div class="empty-state">해당 기간 데이터 없음</div></td></tr>';
      } else {
        materialModalBody.innerHTML = recipes
          .map(
            (r) => `
              <tr>
                <td>${IRMS.escapeHtml(r.product_name || "-")}</td>
                <td>${IRMS.escapeHtml(r.ink_name || "-")}</td>
                <td class="num">${r.weight_g.toLocaleString()}</td>
                <td>${IRMS.escapeHtml(r.measured_by)}</td>
                <td>${IRMS.formatDateTime(r.measured_at)}</td>
              </tr>
            `,
          )
          .join("");
      }
      materialModal.hidden = false;
    } catch (error) {
      IRMS.notify(`재료 상세 조회 실패: ${error.message}`, "error");
    }
  }

  presetBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.preset;
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
  materialModalClose.addEventListener("click", () => { materialModal.hidden = true; });

  function persistRange(range) {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(range)); } catch {}
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

  restoreRange();
  loadAll();
});

(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (message) { console.log(message); };

  const $ = (id) => document.getElementById(id);
  const isManager = Boolean($("visc-settings-btn"));

  const state = {
    products: [],
    currentId: null,
    analysis: null,
    blendRecords: [],
    selectedBlendId: null,
    selectedBlendDetail: null,
    periodChart: null,
    granularity: "quarter",
    year: null,
  };

  const STATUS_LABEL = { normal: "정상", warn: "경고", anomaly: "이상" };
  const REASON_LABEL = {
    spec_high: "상한 초과",
    spec_low: "하한 미만",
    sigma_high: "+kσ 초과",
    sigma_low: "-kσ 미만",
    warn_high: "2σ 경고",
    warn_low: "2σ 경고",
  };
  const TREND_LABEL = {
    run_up: "연속 상승",
    run_down: "연속 하락",
    shift_high: "중심선 상단 치우침",
    shift_low: "중심선 하단 치우침",
  };
  const PERIOD_ALERT_LABEL = {
    anomaly_spike: (item) => `${item.period} 이상 급증 (${item.prev_count}건 → ${item.anomaly_count}건)`,
    mean_shift_up: (item) => `${item.period} 평균 상승 (+${fmt(item.delta, 2)})`,
    mean_shift_down: (item) => `${item.period} 평균 하락 (${fmt(item.delta, 2)})`,
  };

  function fmt(value, digits) {
    if (value === null || value === undefined || value === "") return "-";
    return Number(value).toFixed(digits === undefined ? 1 : digits);
  }

  function productLabel(product) {
    if (!product) return "-";
    return product.name && product.name !== product.code
      ? `${product.code} · ${product.name}`
      : product.code;
  }

  function currentProduct() {
    return state.analysis ? state.analysis.product : null;
  }

  async function loadOverview() {
    const data = await request("/viscosity/overview");
    state.products = data.items || [];
    renderProductSelect();
    if (!state.currentId && state.products.length) {
      state.currentId = state.products[0].id;
    }
    if (state.currentId) {
      const product = state.products.find((item) => item.id === state.currentId);
      state.year = product ? product.year : null;
      await loadProduct(state.currentId);
    }
  }

  function renderProductSelect() {
    const select = $("visc-product-select");
    select.innerHTML = "";
    state.products.forEach((product) => {
      const option = document.createElement("option");
      option.value = String(product.id);
      const warning = product.anomaly_count > 0 ? ` · 이상 ${product.anomaly_count}` : "";
      option.textContent = `${productLabel(product)}${warning}`;
      option.selected = product.id === state.currentId;
      select.appendChild(option);
    });
    select.onchange = () => {
      const product = state.products.find((item) => item.id === Number(select.value));
      if (!product) return;
      state.currentId = product.id;
      state.year = product.year;
      loadProduct(product.id);
    };
  }

  async function loadProduct(productId) {
    state.analysis = await request(`/viscosity/products/${productId}`, {
      query: { granularity: state.granularity, year: state.year },
    });
    renderYearSelect();
    renderCards();
    renderTrendBanner();
    renderPeriodAlerts();
    renderPeriods();
    renderReadings();
    renderCondition();
    await loadBlendRecordsForProduct(state.analysis.product);
  }

  function renderYearSelect() {
    const select = $("visc-year");
    const years = (state.analysis && state.analysis.available_years) || [];
    select.innerHTML = "";
    years.forEach((year) => {
      const option = document.createElement("option");
      option.value = String(year);
      option.textContent = `${year}년`;
      select.appendChild(option);
    });
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "전체";
    select.appendChild(all);
    select.value = state.year === null || state.year === undefined ? "" : String(state.year);
  }

  function renderCards() {
    const analysis = state.analysis;
    const stats = analysis.stats;
    const last = analysis.readings.length ? analysis.readings[analysis.readings.length - 1] : null;
    $("visc-card-count").textContent = stats.n;
    $("visc-card-latest").textContent = last ? fmt(last.viscosity) : "-";
    $("visc-card-latest-date").textContent = last && last.measured_date ? last.measured_date : "-";
    $("visc-card-mean").textContent = stats.mean === null ? "-" : `${fmt(stats.center)} ± ${fmt(stats.std)}`;
    $("visc-card-anomaly").textContent = analysis.counts.anomaly;
    $("visc-card-warn").textContent = analysis.counts.warn;
    $("visc-control-summary").textContent = controlSummary();
  }

  function renderCondition() {
    const product = currentProduct();
    if (!product) return;
    const rpm = product.rpm != null ? `${fmt(product.rpm, 0)} rpm` : "RPM 미설정";
    const temp = product.temperature != null ? `${fmt(product.temperature)} °C` : "온도 미설정";
    $("visc-cond").textContent = `측정 조건 · ${rpm} · ${temp}`;
  }

  function controlSummary() {
    const analysis = state.analysis;
    if (!analysis) return "관리 기준 -";
    const stats = analysis.stats;
    const product = analysis.product;
    const parts = [];
    if (stats.center !== null) parts.push(`중심 ${fmt(stats.center)}`);
    if (stats.lcl !== null && stats.ucl !== null) parts.push(`관리 ${fmt(stats.lcl)}~${fmt(stats.ucl)}`);
    if (product.lower_limit !== null || product.upper_limit !== null) {
      parts.push(`규격 ${product.lower_limit ?? "-"}~${product.upper_limit ?? "-"}`);
    }
    return parts.length ? `관리 기준 · ${parts.join(" · ")}` : "관리 기준이 아직 없습니다.";
  }

  function renderTrendBanner() {
    const trends = state.analysis.trends || [];
    const banner = $("visc-trend-banner");
    if (!trends.length) {
      banner.hidden = true;
      return;
    }
    $("visc-trend-text").textContent = trends
      .map((trend) => `${TREND_LABEL[trend.type] || trend.type} (${trend.length}회 연속)`)
      .join(" · ");
    banner.hidden = false;
  }

  function renderPeriodAlerts() {
    const alerts = (state.analysis && state.analysis.period_alerts) || [];
    const banner = $("visc-period-alert");
    if (!alerts.length) {
      banner.hidden = true;
      return;
    }
    $("visc-period-alert-text").textContent = alerts
      .map((item) => (PERIOD_ALERT_LABEL[item.type] || (() => item.type))(item))
      .join(" · ");
    banner.hidden = false;
  }

  function renderReadings() {
    const body = $("visc-readings-body");
    body.innerHTML = "";
    const rows = state.analysis.readings.slice().reverse();
    if (!rows.length) {
      body.appendChild(emptyRow(isManager ? 8 : 7, "등록된 점도 측정이 없습니다."));
      return;
    }
    rows.forEach((reading) => {
      const row = document.createElement("tr");
      if (reading.status === "anomaly") row.className = "row-anomaly";
      else if (reading.status === "warn") row.className = "row-warn";

      appendTextCell(row, reading.measured_date || "-");
      appendTextCell(row, reading.lot_no);
      appendTextCell(row, fmt(reading.viscosity), "num");
      appendStatusCell(row, reading);
      appendTextCell(row, reading.recipe_material || "-");
      appendTextCell(row, reading.material_lot || "-");
      appendTextCell(row, reading.memo || "-");

      if (isManager) {
        const cell = document.createElement("td");
        const button = document.createElement("button");
        button.className = "visc-del-btn";
        button.type = "button";
        button.textContent = "삭제";
        button.addEventListener("click", () => deleteReading(reading.id, reading.lot_no));
        cell.appendChild(button);
        row.appendChild(cell);
      }
      body.appendChild(row);
    });
  }

  function appendStatusCell(row, reading) {
    const cell = document.createElement("td");
    const status = document.createElement("span");
    status.className = `visc-status ${reading.status}`;
    status.textContent = STATUS_LABEL[reading.status] || reading.status;
    cell.appendChild(status);
    const reasons = (reading.reasons || []).map((item) => REASON_LABEL[item] || item).join(", ");
    if (reasons) {
      const reason = document.createElement("span");
      reason.className = "muted small";
      reason.textContent = ` ${reasons}`;
      cell.appendChild(reason);
    }
    row.appendChild(cell);
  }

  function renderPeriods() {
    const periods = state.analysis.periods || [];
    const body = $("visc-period-body");
    body.innerHTML = "";
    if (!periods.length) {
      body.appendChild(emptyRow(9, "측정일이 있는 데이터가 없습니다."));
    } else {
      periods.forEach((period) => {
        const row = document.createElement("tr");
        if (period.anomaly_count > 0) row.className = "row-anomaly";
        appendTextCell(row, period.period);
        appendTextCell(row, period.count, "num");
        appendTextCell(row, fmt(period.mean), "num");
        appendDeltaCell(row, period.mean_delta);
        appendTextCell(row, fmt(period.std), "num");
        appendTextCell(row, fmt(period.min), "num");
        appendTextCell(row, fmt(period.max), "num");
        appendTextCell(row, period.anomaly_count, "num");
        appendTextCell(row, period.warn_count, "num");
        body.appendChild(row);
      });
    }
    renderPeriodChart(periods);
  }

  function appendDeltaCell(row, delta) {
    const cell = document.createElement("td");
    cell.className = "num";
    if (delta === null || delta === undefined) {
      cell.textContent = "-";
    } else {
      const span = document.createElement("span");
      const isFlat = delta === 0;
      span.className = isFlat ? "visc-delta-flat" : delta > 0 ? "visc-delta-up" : "visc-delta-down";
      span.textContent = `${delta > 0 ? "+" : ""}${fmt(delta, 2)}`;
      cell.appendChild(span);
    }
    row.appendChild(cell);
  }

  function renderPeriodChart(periods) {
    const canvas = $("visc-period-chart");
    const labels = periods.map((period) => period.period);
    const data = periods.map((period) => period.mean);
    const colors = periods.map((period) => {
      if (period.anomaly_count > 0) return getCssVar("--status-error");
      if (period.warn_count > 0) return getCssVar("--status-warning");
      return getCssVar("--brand-mid");
    });
    const datasets = [{
      type: "bar",
      label: "기간 평균",
      data,
      backgroundColor: colors,
      order: 2,
    }];
    const center = state.analysis.stats.center;
    if (center !== null && center !== undefined && labels.length) {
      datasets.push({
        type: "line",
        label: "중심",
        data: labels.map(() => center),
        borderColor: getCssVar("--status-success"),
        borderDash: [4, 4],
        borderWidth: 1,
        pointRadius: 0,
        order: 1,
      });
    }
    if (state.periodChart) state.periodChart.destroy();
    state.periodChart = new Chart(canvas.getContext("2d"), {
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { boxWidth: 12, font: { size: 10 } } },
          tooltip: {
            callbacks: {
              afterBody: (items) => {
                const period = periods[items[0].dataIndex];
                if (!period) return [];
                return [
                  `건수: ${period.count}`,
                  `표준편차: ${fmt(period.std)}`,
                  `범위: ${fmt(period.min)} ~ ${fmt(period.max)}`,
                  `이상: ${period.anomaly_count} · 경고: ${period.warn_count}`,
                ];
              },
            },
          },
        },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  async function loadBlendRecordsForProduct(product) {
    state.blendRecords = [];
    state.selectedBlendId = null;
    state.selectedBlendDetail = null;
    renderBlendRecords();
    if (!product) return;

    const found = await findBlendRecords(product);
    state.blendRecords = await hydrateBlendRecords(found.slice(0, 20));
    state.selectedBlendId = state.blendRecords.length ? state.blendRecords[0].id : null;
    state.selectedBlendDetail = selectedRecord();
    renderBlendRecords();
    if (state.selectedBlendId) {
      await selectBlendRecord(state.selectedBlendId, { focus: false });
    }
  }

  async function findBlendRecords(product) {
    const queries = Array.from(new Set([product.code, product.name].filter(Boolean)));
    const byId = new Map();
    for (const query of queries) {
      const data = await request("/blend/records", { query: { search: query } });
      (data.items || []).forEach((record) => byId.set(record.id, record));
    }
    const records = Array.from(byId.values());
    const exact = records.filter((record) =>
      record.product_name === product.code || record.product_name === product.name
    );
    return exact.length ? exact : records;
  }

  async function hydrateBlendRecords(records) {
    return Promise.all(records.map(async (record) => {
      try {
        const detail = await request(`/blend/records/${record.id}`);
        return Object.assign({}, record, {
          details: detail.details || [],
          viscosity: detail.viscosity || [],
        });
      } catch (_error) {
        return Object.assign({}, record, { details: [], viscosity: [] });
      }
    }));
  }

  function renderBlendRecords() {
    renderBlendSelect();
    renderBlendTable();
    renderSelectedBlend();
  }

  function renderBlendSelect() {
    const select = $("visc-blend-record");
    select.innerHTML = "";
    if (!currentProduct()) {
      select.appendChild(option("", "반제품을 선택하세요"));
      select.disabled = true;
      return;
    }
    if (!state.blendRecords.length) {
      select.appendChild(option("", "연계 가능한 배합 기록이 없습니다"));
      select.disabled = true;
      return;
    }
    state.blendRecords.forEach((record) => {
      const item = option(String(record.id), `${record.product_lot} · ${record.work_date || "-"} · ${record.worker || "-"}`);
      item.selected = record.id === state.selectedBlendId;
      select.appendChild(item);
    });
    select.disabled = false;
  }

  function renderBlendTable() {
    const body = $("visc-blend-body");
    body.innerHTML = "";
    $("visc-record-count").textContent = state.blendRecords.length ? `최근 ${state.blendRecords.length}건` : "0건";
    if (!state.blendRecords.length) {
      body.appendChild(emptyRow(5, "이 반제품의 배합 기록이 없습니다."));
      return;
    }
    state.blendRecords.forEach((record) => {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", record.id === state.selectedBlendId);
      row.addEventListener("click", () => selectBlendRecord(record.id, { focus: true }));
      appendTextCell(row, record.product_lot);
      appendTextCell(row, record.work_date || "-");
      appendTextCell(row, record.worker || "-");
      appendTextCell(row, record.total_amount == null ? "-" : `${fmt(record.total_amount)} g`, "num");
      const cell = document.createElement("td");
      const chip = document.createElement("span");
      const hasLinkedViscosity = (record.viscosity || []).length > 0;
      chip.className = hasLinkedViscosity ? "visc-link-chip done" : "visc-link-chip pending";
      chip.textContent = hasLinkedViscosity ? "등록됨" : "대기";
      cell.appendChild(chip);
      row.appendChild(cell);
      body.appendChild(row);
    });
  }

  function renderSelectedBlend() {
    const box = $("visc-selected-blend");
    const detail = state.selectedBlendDetail;
    const record = selectedRecord();
    if (!record) {
      box.innerHTML = "<b>배합 기록을 선택하세요.</b><span>배합 기록이 없으면 먼저 배합 화면에서 실적을 저장해야 합니다.</span>";
      setSubmitEnabled(false);
      return;
    }
    if (!detail) {
      box.innerHTML = `<b>${record.product_lot}</b><span>선택한 배합 기록 정보를 불러오는 중입니다.</span>`;
      setSubmitEnabled(false);
      return;
    }
    const linked = linkedReadings();
    const info = [
      `${detail.product_name}`,
      `${detail.work_date || "-"} ${detail.work_time || ""}`.trim(),
      `작업자 ${detail.worker || "-"}`,
      `총량 ${fmt(detail.total_amount)} g`,
    ].join(" · ");
    const status = linked.length
      ? `<span class="visc-blocked">이미 점도 ${fmt(linked[0].viscosity)}가 등록되어 있습니다.</span>`
      : '<span class="visc-linked">점도값만 입력하면 이 LOT에 연결됩니다.</span>';
    box.innerHTML = `<b>${detail.product_lot}</b><span>${info}</span>${status}`;
    setSubmitEnabled(linked.length === 0);
  }

  async function selectBlendRecord(recordId, options) {
    state.selectedBlendId = Number(recordId);
    $("visc-blend-record").value = String(recordId);
    renderBlendRecords();
    try {
      state.selectedBlendDetail = selectedRecord() || await request(`/blend/records/${recordId}`);
      renderBlendRecords();
      if (options && options.focus) $("visc-value").focus();
    } catch (error) {
      $("visc-selected-blend").innerHTML = `<b>배합 기록을 불러오지 못했습니다.</b><span>${error.message}</span>`;
      setSubmitEnabled(false);
    }
  }

  function selectedRecord() {
    return state.blendRecords.find((record) => record.id === state.selectedBlendId) || null;
  }

  function linkedReadings() {
    return (state.selectedBlendDetail && state.selectedBlendDetail.viscosity) || [];
  }

  function setSubmitEnabled(enabled) {
    $("visc-submit").disabled = !enabled;
    $("visc-value").disabled = !enabled;
    $("visc-memo").disabled = !enabled;
  }

  async function submitReading(event) {
    event.preventDefault();
    const error = $("visc-form-error");
    error.hidden = true;
    const recordId = Number($("visc-blend-record").value);
    const value = Number($("visc-value").value);
    if (!recordId) {
      showFormError("배합 기록을 선택하세요.");
      return;
    }
    if (!(value > 0)) {
      showFormError("점도값을 입력하세요.");
      return;
    }
    try {
      await request(`/blend/records/${recordId}/viscosity`, {
        method: "POST",
        body: { viscosity: value, memo: $("visc-memo").value.trim() || null },
      });
      $("visc-value").value = "";
      $("visc-memo").value = "";
      await loadProduct(state.currentId);
      warnNewReading(value);
      notify(`점도를 등록했습니다. (${fmt(value)})`, "success");
    } catch (error_) {
      showFormError(error_.message);
    }
  }

  function showFormError(message) {
    const error = $("visc-form-error");
    error.textContent = message;
    error.hidden = false;
  }

  function warnNewReading(value) {
    const row = state.analysis && state.analysis.new_reading;
    const result = $("visc-form-result");
    if (!row || row.status === "normal") {
      result.hidden = true;
      return;
    }
    const reasons = (row.reasons || []).map((item) => REASON_LABEL[item] || item).join(", ");
    if (row.status === "anomaly") {
      result.textContent = `이상값입니다. 점도 ${fmt(value)} · ${reasons}`;
      result.className = "visc-form-result anomaly";
      result.hidden = false;
      notify(result.textContent, "error");
    } else {
      result.textContent = `경고 구간입니다. 점도 ${fmt(value)} · ${reasons}`;
      result.className = "visc-form-result warn";
      result.hidden = false;
      notify(result.textContent, "warn");
    }
  }

  async function deleteReading(readingId, lotNo) {
    if (!window.confirm(`측정 기록을 삭제할까요? (LOT ${lotNo})`)) return;
    try {
      await request(`/viscosity/readings/${readingId}`, { method: "DELETE" });
      notify("측정 기록을 삭제했습니다.", "success");
      await loadProduct(state.currentId);
    } catch (error) {
      notify(`삭제 실패: ${error.message}`, "error");
    }
  }

  function openSettings() {
    const product = currentProduct();
    if (!product) return;
    $("visc-settings-title").textContent = `반제품 설정 · ${product.code}`;
    $("visc-set-name").value = product.name;
    $("visc-set-target").value = product.target ?? "";
    $("visc-set-lower").value = product.lower_limit ?? "";
    $("visc-set-upper").value = product.upper_limit ?? "";
    $("visc-set-sigma").value = product.sigma_k;
    $("visc-set-rpm").value = product.rpm ?? "";
    $("visc-set-temp").value = product.temperature ?? "";
    $("visc-set-active").checked = product.is_active;
    $("visc-settings-error").hidden = true;
    $("visc-settings-modal").hidden = false;
  }

  function numOrNull(id) {
    const value = $(id).value.trim();
    return value === "" ? null : Number(value);
  }

  async function saveSettings(event) {
    event.preventDefault();
    const error = $("visc-settings-error");
    error.hidden = true;
    const body = {
      name: $("visc-set-name").value.trim(),
      target: numOrNull("visc-set-target"),
      lower_limit: numOrNull("visc-set-lower"),
      upper_limit: numOrNull("visc-set-upper"),
      sigma_k: Number($("visc-set-sigma").value),
      rpm: numOrNull("visc-set-rpm"),
      temperature: numOrNull("visc-set-temp"),
      is_active: $("visc-set-active").checked,
    };
    try {
      await request(`/viscosity/products/${state.currentId}`, { method: "PATCH", body });
      $("visc-settings-modal").hidden = true;
      notify("반제품 설정을 저장했습니다.", "success");
      await loadOverview();
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  async function createProduct(event) {
    event.preventDefault();
    const error = $("visc-new-error");
    error.hidden = true;
    const body = {
      code: $("visc-new-code").value.trim(),
      name: $("visc-new-name").value.trim(),
    };
    try {
      const created = await request("/viscosity/products", { method: "POST", body });
      $("visc-new-form").reset();
      notify(`반제품을 추가했습니다. ${created.code}`, "success");
      state.currentId = created.id;
      $("visc-settings-modal").hidden = true;
      await loadOverview();
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  function exportCsv() {
    if (!state.currentId) return;
    window.location.assign(`/api/viscosity/products/${state.currentId}/export`);
  }

  function bind() {
    $("visc-form").addEventListener("submit", submitReading);
    $("visc-refresh").addEventListener("click", () => loadOverview());
    $("visc-blend-record").addEventListener("change", (event) => {
      selectBlendRecord(Number(event.target.value), { focus: true });
    });
    $("visc-year").addEventListener("change", () => {
      const value = $("visc-year").value;
      state.year = value === "" ? null : Number(value);
      if (state.currentId) loadProduct(state.currentId);
    });
    $("visc-gran-toggle").querySelectorAll("button[data-gran]").forEach((button) => {
      button.addEventListener("click", () => {
        if (state.granularity === button.dataset.gran) return;
        state.granularity = button.dataset.gran;
        $("visc-gran-toggle").querySelectorAll("button").forEach((item) =>
          item.classList.toggle("active", item === button)
        );
        if (state.currentId) loadProduct(state.currentId);
      });
    });
    const settingsButton = $("visc-settings-btn");
    if (settingsButton) {
      settingsButton.addEventListener("click", openSettings);
      $("visc-settings-close").addEventListener("click", () => {
        $("visc-settings-modal").hidden = true;
      });
      $("visc-settings-form").addEventListener("submit", saveSettings);
      $("visc-new-form").addEventListener("submit", createProduct);
      $("visc-export-btn").addEventListener("click", exportCsv);
    }
  }

  function appendTextCell(row, value, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value;
    row.appendChild(cell);
  }

  function emptyRow(colSpan, message) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = colSpan;
    cell.className = "muted";
    cell.textContent = message;
    row.appendChild(cell);
    return row;
  }

  function option(value, label) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    return item;
  }

  function getCssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    bind();
    loadOverview().catch((error) => notify(`불러오기 실패: ${error.message}`, "error"));
  });
})();

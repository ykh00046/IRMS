(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (message) { console.log(message); };

  // 순수 헬퍼 라이브러리 — 라이브러리에서 분리된 포맷터/라벨맵/DOM 문자열 빌더.
  // 동일한 이름으로 분해 할당하므로 기존 호출부는 그대로 동작한다.
  const {
    STATUS_LABEL,
    REASON_LABEL,
    TREND_LABEL,
    PERIOD_ALERT_LABEL,
    fmt,
    productLabel,
    linkedReadingsForRecord,
    latestViscosityLabel,
    appendTextCell,
    emptyRow,
    appendDeltaCell,
    option,
    controlSummary,
    periodChartDatasets,
  } = window.IRMS.viscLib;

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
    reactor: null,
  };

  function selectedProduct() {
    const input = $("visc-product-select");
    const value = input ? input.value.trim().toLowerCase() : "";
    if (!value) return null;
    return state.products.find((product) => {
      const code = String(product.code || "").trim().toLowerCase();
      const name = String(product.name || "").trim().toLowerCase();
      const label = productLabel(product).trim().toLowerCase();
      return value === code || value === name || value === label;
    }) || null;
  }

  function currentProduct() {
    return state.analysis ? state.analysis.product : null;
  }

  async function loadOverview() {
    const data = await request("/viscosity/overview");
    state.products = data.items || [];
    if (!state.currentId && state.products.length) {
      state.currentId = state.products[0].id;
    }
    renderProductSelect();
    if (state.currentId) {
      const product = state.products.find((item) => item.id === state.currentId);
      state.year = product ? product.year : null;
      await loadProduct(state.currentId);
    }
  }

  function renderProductSelect() {
    const input = $("visc-product-select");
    const list = $("visc-product-names");
    if (list) list.innerHTML = "";
    state.products.forEach((product) => {
      const opt = document.createElement("option");
      opt.value = productLabel(product);
      if (list) list.appendChild(opt);
    });
    const current = state.products.find((item) => item.id === state.currentId);
    if (input && current) input.value = productLabel(current);
    if (input._pickerBound) return;
    // 포커스 시 비움 → datalist 가 현재값으로 필터되지 않고 전체 목록이 뜸.
    // 선택 없이 나가면(blur) 현재 반제품 라벨로 원복.
    input.addEventListener("focus", () => {
      input.value = "";
    });
    input.addEventListener("blur", () => {
      if (selectedProduct()) return;
      const cur = state.products.find((item) => item.id === state.currentId);
      input.value = cur ? productLabel(cur) : "";
    });
    input.addEventListener("input", () => {
      const product = selectedProduct();
      if (!product) return;
      input.blur(); // 선택 확정 — 드롭다운 닫기
      if (product.id === state.currentId) return;
      state.currentId = product.id;
      state.year = product.year;
      state.reactor = null; // 반제품이 바뀌면 반응기 필터 초기화
      loadProduct(product.id);
    });
  }

  async function loadProduct(productId) {
    state.analysis = await request(`/viscosity/products/${productId}`, {
      query: { granularity: state.granularity, year: state.year, reactor: state.reactor },
    });
    renderYearSelect();
    renderReactorControls();
    renderCards();
    renderTrendBanner();
    renderPeriodAlerts();
    renderPeriods();
    renderCondition();
    await loadBlendRecordsForProduct(state.analysis.product);
  }

  function renderYearSelect() {
    const select = $("visc-year");
    const years = (state.analysis && state.analysis.available_years) || [];
    select.innerHTML = "";
    years.forEach((year) => {
      const opt = document.createElement("option");
      opt.value = String(year);
      opt.textContent = `${year}년`;
      select.appendChild(opt);
    });
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "전체";
    select.appendChild(all);
    select.value = state.year === null || state.year === undefined ? "" : String(state.year);
  }

  // 반응기 진행 반제품일 때만 툴바 반응기 필터 + 등록 폼 반응기 선택을 노출.
  function renderReactorControls() {
    const product = currentProduct();
    const use = Boolean(product && product.use_reactor);
    const label = $("visc-reactor-label");
    const select = $("visc-reactor");
    if (label) label.hidden = !use;
    if (select) {
      select.hidden = !use;
      if (use) {
        select.innerHTML = "";
        const all = document.createElement("option");
        all.value = "";
        all.textContent = "전체(반응기)";
        select.appendChild(all);
        [1, 2, 3, 4].forEach((n) => {
          const opt = document.createElement("option");
          opt.value = String(n);
          opt.textContent = `반응기 ${n}`;
          select.appendChild(opt);
        });
        select.value = state.reactor == null ? "" : String(state.reactor);
      }
    }
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
    $("visc-control-summary").textContent = controlSummary(analysis);
  }

  function renderCondition() {
    const product = currentProduct();
    if (!product) return;
    const rpm = product.rpm != null ? `${fmt(product.rpm, 0)} rpm` : "RPM 미설정";
    const temp = product.temperature != null ? `${fmt(product.temperature)} °C` : "온도 미설정";
    $("visc-cond").textContent = `측정 조건 · ${rpm} · ${temp}`;
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

  function renderPeriodChart(periods) {
    const canvas = $("visc-period-chart");
    const center = state.analysis.stats.center;
    const { labels, datasets } = periodChartDatasets(periods, center, getCssVar);
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
    const records = visibleBlendRecords();
    if (records.length && !records.some((record) => record.id === state.selectedBlendId)) {
      state.selectedBlendId = records[0].id;
      state.selectedBlendDetail = selectedRecord();
    } else if (!records.length) {
      state.selectedBlendId = null;
      state.selectedBlendDetail = null;
    }
    $("visc-blend-record").value = state.selectedBlendId ? String(state.selectedBlendId) : "";
    renderBlendTable(records);
    renderSelectedBlend();
  }

  function visibleBlendRecords() {
    const filter = $("visc-blend-filter").value.trim().toLowerCase();
    const openOnly = $("visc-open-only").checked;
    return state.blendRecords.filter((record) => {
      if (openOnly && linkedReadingsForRecord(record).length) return false;
      if (!filter) return true;
      return [
        record.product_lot,
        record.work_date,
        record.worker,
        record.total_amount,
        latestViscosityLabel(record),
      ]
        .join(" ")
        .toLowerCase()
        .includes(filter);
    });
  }

  function renderBlendTable(records) {
    const body = $("visc-blend-body");
    body.innerHTML = "";
    $("visc-record-count").textContent = state.blendRecords.length
      ? `${records.length} / ${state.blendRecords.length}건`
      : "0건";
    if (!records.length) {
      body.appendChild(emptyRow(5, "이 반제품의 배합 기록이 없습니다."));
      return;
    }
    records.forEach((record) => {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", record.id === state.selectedBlendId);
      row.addEventListener("click", () => selectBlendRecord(record.id, { focus: true }));
      appendTextCell(row, record.product_lot);
      appendTextCell(row, record.work_date || "-");
      appendTextCell(row, record.worker || "-");
      appendTextCell(row, record.total_amount == null ? "-" : `${fmt(record.total_amount)} g`, "num");
      appendViscosityCell(row, record);
      body.appendChild(row);
    });
  }

  function appendViscosityCell(row, record) {
    const linked = linkedReadingsForRecord(record);
    if (!linked.length) {
      const cell = document.createElement("td");
      cell.className = "num muted";
      cell.textContent = "미입력";
      row.appendChild(cell);
      return;
    }

    const reading = linked[0];
    const cell = document.createElement("td");
    cell.className = "num visc-reading-cell";
    const value = document.createElement("span");
    value.className = "visc-reading-value";
    value.textContent = fmt(reading.viscosity);
    cell.appendChild(value);
    if (reading.measured_date) {
      const date = document.createElement("span");
      date.className = "muted small";
      date.textContent = ` ${reading.measured_date}`;
      cell.appendChild(date);
    }
    if (reading.reactor) {
      const rx = document.createElement("span");
      rx.className = "muted small";
      rx.textContent = ` · 반응기 ${reading.reactor}`;
      cell.appendChild(rx);
    }
    if (isManager) {
      const button = document.createElement("button");
      button.className = "visc-del-btn";
      button.type = "button";
      button.textContent = "삭제";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteReading(reading.id, reading.lot_no);
      });
      cell.appendChild(button);
    }
    row.appendChild(cell);
  }

  function renderSelectedBlend() {
    const box = $("visc-selected-row");
    const detail = state.selectedBlendDetail;
    const record = selectedRecord();
    if (!record) {
      box.textContent = "배합 기록 표에서 미등록 행을 선택하세요.";
      setSubmitEnabled(false);
      return;
    }
    if (!detail) {
      box.textContent = `${record.product_lot} 정보를 불러오는 중입니다.`;
      setSubmitEnabled(false);
      return;
    }
    const linked = linkedReadings();
    box.textContent = linked.length
      ? `${detail.product_lot} · 점도 ${fmt(linked[0].viscosity)} 등록`
      : `${detail.product_lot} · ${detail.work_date || "-"} · ${detail.worker || "-"} 선택`;
    setSubmitEnabled(linked.length === 0);
  }

  async function selectBlendRecord(recordId, options) {
    state.selectedBlendId = Number(recordId);
    $("visc-blend-record").value = String(recordId || "");
    renderBlendRecords();
    try {
      state.selectedBlendDetail = selectedRecord() || await request(`/blend/records/${recordId}`);
      renderBlendRecords();
      if (options && options.focus) $("visc-value").focus();
    } catch (error) {
      $("visc-selected-row").textContent = `배합 기록을 불러오지 못했습니다. ${error.message}`;
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
      // 반응기는 배합 실적에서 지정하고 점도는 실적에서 물려받는다(여기서 입력하지 않음).
      await request(`/blend/records/${recordId}/viscosity`, {
        method: "POST",
        body: { viscosity: value, memo: $("visc-memo").value.trim() || null },
      });
      $("visc-value").value = "";
      $("visc-memo").value = "";
      const selectedId = recordId;
      await loadProduct(state.currentId);
      if (selectedId) await selectBlendRecord(selectedId, { focus: false });
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

  // ── 반제품 관리 모달: 전체 목록 → 행 선택 → 수정 / 새 반제품 추가 ──
  // 수정 대상은 화면에서 보고 있는 반제품과 무관하게 목록에서 고른다.
  let settingsProducts = [];
  let settingsId = null;
  let recipeCandidates = [];

  async function openSettings() {
    try {
      const data = await request("/viscosity/products");
      settingsProducts = data.items || [];
    } catch (error_) {
      notify(`반제품 목록을 불러오지 못했습니다: ${error_.message}`, "error");
      return;
    }
    const current = settingsProducts.find((p) => p.id === state.currentId) || settingsProducts[0];
    fillSettingsForm(current || null);
    renderSettingsList();
    loadRecipeCandidates().catch(() => {});
    $("visc-settings-error").hidden = true;
    $("visc-new-error").hidden = true;
    $("visc-settings-modal").hidden = false;
  }

  // 반제품 추가 후보 = (완성) 레시피 중 아직 점도 반제품이 없는 제품명
  async function loadRecipeCandidates() {
    const data = await request("/blend/recipes");
    const existing = new Set(
      settingsProducts.flatMap((p) => [String(p.code).toLowerCase(), String(p.name).toLowerCase()])
    );
    recipeCandidates = (data.items || [])
      .map((r) => String(r.product_name || "").trim())
      .filter((name) => name && !existing.has(name.toLowerCase()));
    const list = $("visc-recipe-candidates");
    if (list) {
      list.innerHTML = "";
      recipeCandidates.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        list.appendChild(opt);
      });
    }
  }

  function renderSettingsList() {
    const body = $("visc-prod-list");
    body.innerHTML = "";
    if (!settingsProducts.length) {
      body.appendChild(emptyRow(5, "반제품이 없습니다. 아래에서 추가하세요."));
      return;
    }
    settingsProducts.forEach((product) => {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", product.id === settingsId);
      row.style.cursor = "pointer";
      appendTextCell(row, product.code);
      appendTextCell(row, product.name);
      appendTextCell(row, product.remind_daily ? "켜짐" : "-");
      appendTextCell(row, product.use_reactor ? "사용" : "-");
      appendTextCell(row, product.is_active ? "사용" : "중지");
      row.addEventListener("click", () => {
        fillSettingsForm(product);
        renderSettingsList();
      });
      body.appendChild(row);
    });
  }

  function fillSettingsForm(product) {
    settingsId = product ? product.id : null;
    $("visc-settings-title").textContent = product
      ? `반제품 설정 · ${product.code}`
      : "반제품 설정";
    $("visc-set-name").value = product ? product.name : "";
    $("visc-set-target").value = product ? (product.target ?? "") : "";
    $("visc-set-lower").value = product ? (product.lower_limit ?? "") : "";
    $("visc-set-upper").value = product ? (product.upper_limit ?? "") : "";
    $("visc-set-sigma").value = product ? product.sigma_k : 3;
    $("visc-set-rpm").value = product ? (product.rpm ?? "") : "";
    $("visc-set-temp").value = product ? (product.temperature ?? "") : "";
    $("visc-set-remind").checked = Boolean(product && product.remind_daily);
    $("visc-set-reactor").checked = Boolean(product && product.use_reactor);
    $("visc-set-active").checked = product ? product.is_active : true;
  }

  function numOrNull(id) {
    const value = $(id).value.trim();
    return value === "" ? null : Number(value);
  }

  async function saveSettings(event) {
    event.preventDefault();
    const error = $("visc-settings-error");
    error.hidden = true;
    if (!settingsId) {
      error.textContent = "수정할 반제품을 목록에서 선택하세요.";
      error.hidden = false;
      return;
    }
    const body = {
      name: $("visc-set-name").value.trim(),
      target: numOrNull("visc-set-target"),
      lower_limit: numOrNull("visc-set-lower"),
      upper_limit: numOrNull("visc-set-upper"),
      sigma_k: Number($("visc-set-sigma").value),
      rpm: numOrNull("visc-set-rpm"),
      temperature: numOrNull("visc-set-temp"),
      remind_daily: $("visc-set-remind").checked,
      use_reactor: $("visc-set-reactor").checked,
      is_active: $("visc-set-active").checked,
    };
    try {
      const updated = await request(`/viscosity/products/${settingsId}`, { method: "PATCH", body });
      notify(`저장했습니다: ${updated.code}`, "success");
      // 모달은 열어 둔 채 목록 갱신(여러 반제품 연속 관리), 본화면은 뒤에서 갱신
      settingsProducts = settingsProducts.map((p) => (p.id === updated.id ? updated : p));
      renderSettingsList();
      loadOverview().catch(() => {});
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  async function createProduct(event) {
    event.preventDefault();
    const error = $("visc-new-error");
    error.hidden = true;
    const name = $("visc-new-code").value.trim();
    // 레시피 연동 강제: 후보(점도 반제품이 없는 레시피)에서만 선택 가능
    const hit = recipeCandidates.find((c) => c.toLowerCase() === name.toLowerCase());
    if (!hit) {
      error.textContent = "레시피 목록에서 선택하세요. (이미 반제품이 있거나 레시피에 없는 제품)";
      error.hidden = false;
      return;
    }
    try {
      const created = await request("/viscosity/products", {
        method: "POST",
        body: { code: hit, name: hit },
      });
      $("visc-new-form").reset();
      notify(`반제품을 추가했습니다: ${created.code}`, "success");
      settingsProducts = [...settingsProducts, created];
      fillSettingsForm(created);   // 이어서 기준값 입력하도록 수정 폼에 로드
      renderSettingsList();
      loadRecipeCandidates().catch(() => {});
      loadOverview().catch(() => {});
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
    $("visc-blend-filter").addEventListener("input", renderBlendRecords);
    $("visc-open-only").addEventListener("change", renderBlendRecords);
    $("visc-year").addEventListener("change", () => {
      const value = $("visc-year").value;
      state.year = value === "" ? null : Number(value);
      if (state.currentId) loadProduct(state.currentId);
    });
    $("visc-reactor").addEventListener("change", () => {
      const value = $("visc-reactor").value;
      state.reactor = value === "" ? null : Number(value);
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

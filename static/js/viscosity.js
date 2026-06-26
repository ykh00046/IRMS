/**
 * viscosity.js — 점도 등록·추세·이상 분석 페이지 컨트롤러.
 *
 * 데이터: GET /api/viscosity/overview, /viscosity/products/{id}
 * 등록  : POST /api/viscosity/readings (작업자)
 * 설정  : PATCH/POST /api/viscosity/products (관리자)
 * CSRF·인증 리다이렉트는 IRMS._core.request 가 처리한다.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = (IRMS.notify || function (m) { console.log(m); });

  const $ = (id) => document.getElementById(id);
  // 관리자 전용 UI(설정 버튼)는 서버 템플릿이 권한에 따라 렌더한다.
  const isManager = !!$("visc-settings-btn");
  const state = {
    products: [], currentId: null, analysis: null,
    chart: null, periodChart: null, granularity: "quarter", year: null,
  };

  const TREND_LABEL = {
    run_up: "연속 상승 추세",
    run_down: "연속 하락 추세",
    shift_high: "중심선 상향 치우침",
    shift_low: "중심선 하향 치우침",
  };
  const STATUS_LABEL = { normal: "정상", warn: "경고", anomaly: "이상" };
  const REASON_LABEL = {
    spec_high: "관리상한 초과", spec_low: "관리하한 미만",
    sigma_high: "+kσ 초과", sigma_low: "-kσ 미만",
    warn_high: "2σ 경고(상)", warn_low: "2σ 경고(하)",
  };

  function fmt(value, digits) {
    if (value === null || value === undefined) return "-";
    return Number(value).toFixed(digits === undefined ? 1 : digits);
  }

  async function loadOverview() {
    const data = await request("/viscosity/overview");
    state.products = data.items || [];
    renderTabs();
    if (!state.currentId && state.products.length) {
      state.currentId = state.products[0].id;
    }
    if (state.currentId) {
      const cur = state.products.find((p) => p.id === state.currentId);
      state.year = cur ? cur.year : null;  // 기본: 제품의 최신 연도
      await loadProduct(state.currentId);
    }
  }

  function renderTabs() {
    // 상단 품목 토글 대신 단일 드롭다운으로 한 제품만 본다.
    const sel = $("visc-product-select");
    if (!sel) return;
    sel.innerHTML = "";
    state.products.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id;
      const nm = p.name && p.name !== p.code ? `${p.code} · ${p.name}` : p.code;
      opt.textContent = p.anomaly_count > 0 ? `${nm} (이상 ${p.anomaly_count})` : nm;
      if (p.id === state.currentId) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = () => {
      const p = state.products.find((x) => x.id === Number(sel.value));
      if (!p) return;
      state.currentId = p.id;
      state.year = p.year; // 제품 전환 시 그 제품의 최신 연도로
      loadProduct(p.id);
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
    renderChart();
    renderPeriods();
    renderReadings();
    renderCondition();
    loadBlendRecordsForProduct(state.analysis.product && state.analysis.product.code);
  }

  function renderCondition() {
    const el = $("visc-cond");
    if (!el || !state.analysis) return;
    const p = state.analysis.product;
    const rpm = p.rpm != null ? `${p.rpm} rpm` : "rpm 미설정";
    const temp = p.temperature != null ? `${p.temperature}°C` : "온도 미설정";
    el.textContent = `측정 조건 · ${rpm} · ${temp}`;
  }

  function renderYearSelect() {
    const sel = $("visc-year");
    if (!sel) return;
    const years = (state.analysis && state.analysis.available_years) || [];
    sel.innerHTML = "";
    years.forEach((y) => {
      const opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = `${y}년`;
      sel.appendChild(opt);
    });
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "전체(연도비교)";
    sel.appendChild(all);
    sel.value = state.year === null || state.year === undefined ? "" : String(state.year);
  }

  function renderCards() {
    const a = state.analysis;
    const s = a.stats;
    const last = a.readings.length ? a.readings[a.readings.length - 1] : null;
    $("visc-card-count").textContent = s.n;
    $("visc-card-latest").textContent = last ? fmt(last.viscosity) : "-";
    $("visc-card-latest-date").textContent = last && last.measured_date ? last.measured_date : "-";
    $("visc-card-mean").textContent =
      s.mean === null ? "-" : `${fmt(s.center)} ± ${fmt(s.std)}`;
    $("visc-card-anomaly").textContent = a.counts.anomaly;
    $("visc-card-warn").textContent = a.counts.warn;

    const ctl = [];
    if (s.center !== null) ctl.push(`중심선 ${fmt(s.center)}`);
    if (s.lcl !== null && s.ucl !== null) ctl.push(`관리한계 ${fmt(s.lcl)}~${fmt(s.ucl)}`);
    if (a.product.lower_limit !== null || a.product.upper_limit !== null) {
      ctl.push(`spec ${a.product.lower_limit ?? "−"}~${a.product.upper_limit ?? "−"}`);
    }
    $("visc-control-summary").textContent = ctl.join(" · ");
  }

  function renderTrendBanner() {
    const trends = state.analysis.trends || [];
    const banner = $("visc-trend-banner");
    if (!trends.length) {
      banner.hidden = true;
      return;
    }
    $("visc-trend-text").textContent = trends
      .map((t) => `${TREND_LABEL[t.type] || t.type} (${t.length}회 연속)`)
      .join(" · ");
    banner.hidden = false;
  }

  function renderChart() {
    if (!$("visc-chart")) return;  // 점도 추세 차트 제거됨 — 기간별 차트로 통합
    const a = state.analysis;
    const s = a.stats;
    // 그래프는 최근 30개만 표시(전체는 아래 측정 이력에서 확인).
    const chartReadings = a.readings.slice(-30);
    const labels = chartReadings.map((r) => r.measured_date || r.lot_no);
    const values = chartReadings.map((r) => r.viscosity);
    const pointColors = chartReadings.map((r) =>
      r.status === "anomaly" ? "#dc2626" : r.status === "warn" ? "#d97706" : "#1b4079"
    );

    const flat = (v) => (v === null ? null : chartReadings.map(() => v));
    const datasets = [
      {
        label: "점도",
        data: values,
        borderColor: "#1b4079",
        backgroundColor: pointColors,
        pointBackgroundColor: pointColors,
        pointRadius: 4,
        tension: 0.2,
        order: 1,
      },
    ];
    const addLine = (label, value, color, dash) => {
      if (value === null || value === undefined) return;
      datasets.push({
        label,
        data: flat(value),
        borderColor: color,
        borderDash: dash,
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
        order: 2,
      });
    };
    addLine("중심선", s.center, "#16a34a", [4, 4]);
    addLine("UCL", s.ucl, "#dc2626", [6, 4]);
    addLine("LCL", s.lcl, "#dc2626", [6, 4]);
    addLine("경고(상)", s.uwl, "#d97706", [2, 3]);
    addLine("경고(하)", s.lwl, "#d97706", [2, 3]);
    addLine("관리상한", a.product.upper_limit, "#7c3aed", [8, 3]);
    addLine("관리하한", a.product.lower_limit, "#7c3aed", [8, 3]);

    if (state.chart) state.chart.destroy();
    state.chart = new Chart($("visc-chart").getContext("2d"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { boxWidth: 12, font: { size: 10 } } },
          tooltip: {
            callbacks: {
              afterBody: (items) => {
                const idx = items[0].dataIndex;
                const r = chartReadings[idx];
                const parts = [];
                if (r.status !== "normal") parts.push(`상태: ${STATUS_LABEL[r.status]}`);
                if (r.memo) parts.push(`메모: ${r.memo}`);
                if (r.recipe_material) parts.push(`레시피: ${r.recipe_material}`);
                return parts;
              },
            },
          },
        },
        scales: { x: { ticks: { maxTicksLimit: 12, font: { size: 9 } } } },
      },
    });
  }

  const PERIOD_ALERT_LABEL = {
    anomaly_spike: (a) => `${a.period} 이상 급증 (${a.prev_count}→${a.anomaly_count}건)`,
    mean_shift_up: (a) => `${a.period} 평균 상향 이동 (전기대비 +${fmt(a.delta, 2)} ≥ 1σ)`,
    mean_shift_down: (a) => `${a.period} 평균 하향 이동 (전기대비 ${fmt(a.delta, 2)} ≤ -1σ)`,
  };

  function renderPeriodAlerts() {
    const alerts = (state.analysis && state.analysis.period_alerts) || [];
    const banner = $("visc-period-alert");
    if (!alerts.length) {
      banner.hidden = true;
      return;
    }
    const gran = state.granularity === "month" ? "월" : "분기";
    $("visc-period-alert-text").textContent =
      `${gran} 단위 알림 — ` +
      alerts.map((a) => (PERIOD_ALERT_LABEL[a.type] || (() => a.type))(a)).join(" · ");
    banner.hidden = false;
  }

  function renderPeriods() {
    const a = state.analysis;
    const periods = a.periods || [];
    const body = $("visc-period-body");
    body.innerHTML = "";

    if (!periods.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 9;
      td.className = "muted";
      td.textContent = "측정일이 있는 데이터가 없습니다.";
      tr.appendChild(td);
      body.appendChild(tr);
    } else {
      periods.forEach((p) => {
        const tr = document.createElement("tr");
        if (p.anomaly_count > 0) tr.className = "row-anomaly";
        let deltaHtml = '<span class="muted">–</span>';
        if (p.mean_delta !== null && p.mean_delta !== undefined) {
          const up = p.mean_delta > 0;
          const flat = p.mean_delta === 0;
          const arrow = flat ? "→" : up ? "▲" : "▼";
          const cls = flat ? "visc-delta-flat" : up ? "visc-delta-up" : "visc-delta-down";
          const sign = up ? "+" : "";
          deltaHtml = `<span class="${cls}">${arrow} ${sign}${fmt(p.mean_delta, 2)}</span>`;
        }
        const cells = [
          [p.period, ""],
          [p.count, "num"],
          [fmt(p.mean), "num"],
          [deltaHtml, "num html"],
          [fmt(p.std), "num"],
          [fmt(p.min), "num"],
          [fmt(p.max), "num"],
          [p.anomaly_count, "num"],
          [p.warn_count, "num"],
        ];
        cells.forEach(([val, cls]) => {
          const td = document.createElement("td");
          if (cls.includes("num")) td.className = "num";
          if (cls.includes("html")) td.innerHTML = val;
          else td.textContent = val;
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
    }

    // 기간별 평균 막대 + 중심선 기준선
    const labels = periods.map((p) => p.period);
    const means = periods.map((p) => p.mean);
    const colors = periods.map((p) =>
      p.anomaly_count > 0 ? "#dc2626" : p.warn_count > 0 ? "#d97706" : "#2563eb"
    );
    const datasets = [{
      type: "bar",
      label: "기간 평균",
      data: means,
      backgroundColor: colors,
      order: 2,
    }];
    const center = a.stats.center;
    if (center !== null && center !== undefined && labels.length) {
      datasets.push({
        type: "line",
        label: "중심선",
        data: labels.map(() => center),
        borderColor: "#16a34a",
        borderDash: [4, 4],
        borderWidth: 1,
        pointRadius: 0,
        order: 1,
      });
    }

    if (state.periodChart) state.periodChart.destroy();
    state.periodChart = new Chart($("visc-period-chart").getContext("2d"), {
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { boxWidth: 12, font: { size: 10 } } },
          tooltip: {
            callbacks: {
              afterBody: (items) => {
                const p = periods[items[0].dataIndex];
                if (!p) return [];
                return [
                  `건수: ${p.count}`,
                  `σ: ${fmt(p.std)}`,
                  `범위: ${fmt(p.min)} ~ ${fmt(p.max)}`,
                  `이상: ${p.anomaly_count} · 경고: ${p.warn_count}`,
                ];
              },
            },
          },
        },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  function renderReadings() {
    const a = state.analysis;
    const body = $("visc-readings-body");
    body.innerHTML = "";
    const rows = a.readings.slice().reverse();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = isManager ? 8 : 7;
      td.className = "muted";
      td.textContent = "등록된 측정이 없습니다.";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      if (r.status === "anomaly") tr.className = "row-anomaly";
      else if (r.status === "warn") tr.className = "row-warn";

      const reasons = (r.reasons || []).map((x) => REASON_LABEL[x] || x).join(", ");
      const statusHtml =
        `<span class="visc-status ${r.status}">${STATUS_LABEL[r.status]}</span>` +
        (reasons ? ` <span class="muted small">${reasons}</span>` : "");

      const cells = [
        r.measured_date || "-",
        r.lot_no,
        fmt(r.viscosity),
        statusHtml,
        r.recipe_material || "-",
        r.material_lot || "-",
        r.memo || "-",
      ];
      cells.forEach((c, i) => {
        const td = document.createElement("td");
        if (i === 2) td.className = "num";
        if (i === 3) td.innerHTML = c;
        else td.textContent = c;
        tr.appendChild(td);
      });
      if (isManager) {
        const td = document.createElement("td");
        const del = document.createElement("button");
        del.className = "visc-del-btn";
        del.textContent = "삭제";
        del.addEventListener("click", () => deleteReading(r.id, r.lot_no));
        td.appendChild(del);
        tr.appendChild(td);
      }
      body.appendChild(tr);
    });
  }

  const REASON_TEXT = {
    spec_high: "관리상한 초과", spec_low: "관리하한 미만",
    sigma_high: "+kσ 초과", sigma_low: "-kσ 미만",
    warn_high: "2σ 경고", warn_low: "2σ 경고",
  };

  function warnNewReading(value) {
    const a = state.analysis;
    const row = a.new_reading;
    const result = $("visc-form-result");
    if (!row || row.status === "normal") {
      result.hidden = true;
      notify(`점도가 등록되었습니다. (${fmt(value)})`, "success");
      return;
    }
    const s = a.stats;
    const band =
      s.lcl !== null && s.ucl !== null ? ` 관리한계 ${fmt(s.lcl)}~${fmt(s.ucl)}` : "";
    const reasons = (row.reasons || []).map((x) => REASON_TEXT[x] || x).join(", ");
    if (row.status === "anomaly") {
      const msg = `⚠ 이상값! 점도 ${fmt(value)} — ${reasons}.${band}`;
      result.textContent = msg;
      result.className = "visc-form-result anomaly";
      result.hidden = false;
      notify(msg, "error");
    } else {
      const msg = `주의: 점도 ${fmt(value)} 가 경고 구간입니다 (${reasons}).${band}`;
      result.textContent = msg;
      result.className = "visc-form-result warn";
      result.hidden = false;
      notify(msg, "warn");
    }
  }

  // 등록은 배합 기록에 연계 — LOT·측정일은 기록에서 자동(수동 입력 없음)
  async function loadBlendRecordsForProduct(code) {
    const sel = $("visc-blend-record");
    if (!sel) return;
    sel.innerHTML = "";
    if (!code) { sel.innerHTML = '<option value="">제품을 선택하세요</option>'; return; }
    try {
      const d = await request("/blend/records", { query: { search: code } });
      const recs = (d.items || []).filter((r) => r.product_name === code);
      if (!recs.length) {
        sel.innerHTML = '<option value="">연계할 배합 기록이 없습니다</option>';
        return;
      }
      recs.forEach((r) => {
        const o = document.createElement("option");
        o.value = String(r.id);
        o.textContent = `${r.product_lot} · ${r.work_date || ""}`;
        sel.appendChild(o);
      });
    } catch (_e) {
      sel.innerHTML = '<option value="">불러오기 실패</option>';
    }
  }

  async function submitReading(event) {
    event.preventDefault();
    const err = $("visc-form-error");
    err.hidden = true;
    const recId = $("visc-blend-record").value;
    const value = Number($("visc-value").value);
    if (!recId) { err.textContent = "배합 기록을 선택하세요."; err.hidden = false; return; }
    if (!(value > 0)) { err.textContent = "점도값을 입력하세요."; err.hidden = false; return; }
    try {
      await request(`/blend/records/${recId}/viscosity`, {
        method: "POST",
        body: { viscosity: value, memo: $("visc-memo").value.trim() || null },
      });
      $("visc-form").reset();
      await loadProduct(state.currentId);  // 화면 새로고침(이력·차트·카드)
      warnNewReading(value);
      await refreshTabBadges();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  async function deleteReading(readingId, lotNo) {
    if (!window.confirm(`측정을 삭제할까요? (LOT ${lotNo})`)) return;
    try {
      await request(`/viscosity/readings/${readingId}`, { method: "DELETE" });
      notify("삭제되었습니다.", "success");
      await loadProduct(state.currentId);
      await refreshTabBadges();
    } catch (e) {
      notify(`삭제 실패: ${e.message}`, "error");
    }
  }

  async function refreshTabBadges() {
    const data = await request("/viscosity/overview");
    state.products = data.items || [];
    renderTabs();
  }

  // ---- 관리자: 반제품 설정 모달 ----------------------------------------
  function currentProduct() {
    return state.analysis ? state.analysis.product : null;
  }

  function openSettings() {
    const p = currentProduct();
    if (!p) return;
    $("visc-settings-title").textContent = `반제품 설정 · ${p.code}`;
    $("visc-set-name").value = p.name;
    $("visc-set-target").value = p.target ?? "";
    $("visc-set-lower").value = p.lower_limit ?? "";
    $("visc-set-upper").value = p.upper_limit ?? "";
    $("visc-set-sigma").value = p.sigma_k;
    $("visc-set-rpm").value = p.rpm ?? "";
    $("visc-set-temp").value = p.temperature ?? "";
    $("visc-set-active").checked = p.is_active;
    $("visc-settings-error").hidden = true;
    $("visc-settings-modal").hidden = false;
  }

  function numOrNull(id) {
    const v = $(id).value.trim();
    return v === "" ? null : Number(v);
  }

  async function saveSettings(event) {
    event.preventDefault();
    const err = $("visc-settings-error");
    err.hidden = true;
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
      notify("반제품 설정이 저장되었습니다.", "success");
      await loadOverview();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  async function createProduct(event) {
    event.preventDefault();
    const err = $("visc-new-error");
    err.hidden = true;
    const body = {
      code: $("visc-new-code").value.trim(),
      name: $("visc-new-name").value.trim(),
    };
    try {
      const created = await request("/viscosity/products", { method: "POST", body });
      $("visc-new-form").reset();
      notify(`반제품 추가됨: ${created.code}`, "success");
      state.currentId = created.id;
      $("visc-settings-modal").hidden = true;
      await loadOverview();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  function exportCsv() {
    if (!state.currentId) return;
    window.location.assign(`/api/viscosity/products/${state.currentId}/export`);
  }

  function bind() {
    $("visc-form").addEventListener("submit", submitReading);
    $("visc-refresh").addEventListener("click", () => loadOverview());

    const yearSel = $("visc-year");
    if (yearSel) {
      yearSel.addEventListener("change", () => {
        const v = yearSel.value;
        state.year = v === "" ? null : Number(v);
        if (state.currentId) loadProduct(state.currentId);
      });
    }

    const granToggle = $("visc-gran-toggle");
    if (granToggle) {
      granToggle.querySelectorAll("button[data-gran]").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (state.granularity === btn.dataset.gran) return;
          state.granularity = btn.dataset.gran;
          granToggle.querySelectorAll("button").forEach((b) =>
            b.classList.toggle("active", b === btn)
          );
          if (state.currentId) loadProduct(state.currentId);
        });
      });
    }
    const settingsBtn = $("visc-settings-btn");
    if (settingsBtn) {
      settingsBtn.addEventListener("click", openSettings);
      $("visc-settings-close").addEventListener("click", () => {
        $("visc-settings-modal").hidden = true;
      });
      $("visc-settings-form").addEventListener("submit", saveSettings);
      $("visc-new-form").addEventListener("submit", createProduct);
      $("visc-export-btn").addEventListener("click", exportCsv);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    bind();
    loadOverview().catch((e) => notify(`불러오기 실패: ${e.message}`, "error"));
  });
})();

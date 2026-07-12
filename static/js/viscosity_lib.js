/**
 * viscosity_lib.js — pure helpers for the viscosity page controller.
 *
 * Split from static/js/viscosity.js during the viscosity split PDCA cycle
 * (2026-07). Every member is a pure helper: it takes its inputs as parameters
 * and returns a value, referencing none of the controller closure bindings
 * (state, $, request, notify, isManager, chart instances, document/DOM
 * lookups). The controller injects any DOM-reading capability it needs (for
 * example the CSS-variable resolver passed into periodChartDatasets).
 *
 * Exports (window.IRMS.viscLib):
 *   STATUS_LABEL, REASON_LABEL, TREND_LABEL, PERIOD_ALERT_LABEL,
 *   fmt, productLabel, linkedReadingsForRecord, latestViscosityLabel,
 *   appendTextCell, emptyRow, appendDeltaCell, option, controlSummary,
 *   periodChartDatasets
 *
 * Side effects: none (attaches to window.IRMS.viscLib only).
 * Dependencies: window.IRMS namespace (initialized by common/core.js).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

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

  function linkedReadingsForRecord(record) {
    return (record && record.viscosity) || [];
  }

  function latestViscosityLabel(record) {
    const linked = linkedReadingsForRecord(record);
    return linked.length ? fmt(linked[0].viscosity) : "미입력";
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

  function option(value, label) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    return item;
  }

  // 관리 기준 요약 문자열. analysis 를 인자로 받아 동일한 출력을 반환한다.
  function controlSummary(analysis) {
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

  // 기간 차트의 labels/datasets 을 순수하게 조립한다. DOM 에서 CSS 변수를
  // 읽는 일은 컨트롤러가 resolveCss 콜백으로 주입한다(라이브러리는 DOM 를
  // 직접 참조하지 않는다). 동일 periods/center/resolveCss 에 대해 동일한
  // datasets 을 반환한다.
  function periodChartDatasets(periods, center, resolveCss) {
    const labels = periods.map((period) => period.period);
    const data = periods.map((period) => period.mean);
    const colors = periods.map((period) => {
      if (period.anomaly_count > 0) return resolveCss("--status-error");
      if (period.warn_count > 0) return resolveCss("--status-warning");
      return resolveCss("--brand-mid");
    });
    const datasets = [{
      type: "bar",
      label: "기간 평균",
      data,
      backgroundColor: colors,
      order: 2,
    }];
    if (center !== null && center !== undefined && labels.length) {
      datasets.push({
        type: "line",
        label: "중심",
        data: labels.map(() => center),
        borderColor: resolveCss("--status-success"),
        borderDash: [4, 4],
        borderWidth: 1,
        pointRadius: 0,
        order: 1,
      });
    }
    return { labels, datasets };
  }

  IRMS.viscLib = {
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
  };
})();

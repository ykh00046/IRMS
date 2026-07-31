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
    // 표본이 적어 통계 관리한계를 아직 쓰지 않는 상태를 분명히 알린다 — 예전에는
    // 관리한계가 없어도 이유가 화면에 없어서, 판정이 왜 느슨한지 알 수 없었다.
    if (stats.sigma_ready === false) {
      const need = stats.sigma_min_samples || 8;
      parts.push(`기준 축적 중 (측정 ${stats.n ?? 0}/${need}건 · 규격 판정만 적용)`);
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

  // 측정일(ISO date) → 기간 버킷 키. 백엔드 _period_key 와 동일한 규칙을 그대로
  // 옮긴 것(day/week/month/quarter/year). 개별 측정을 기간 축(차트 라벨)에 정확히
  // 얹기 위해 필요하다 — 라벨이 일치해야 Chart.js 가 그 구간 위에 점을 찍는다.
  function isoWeekKey(year, month, day) {
    const date = new Date(Date.UTC(year, month - 1, day));
    const dayNum = (date.getUTCDay() + 6) % 7;         // 월=0 … 일=6
    date.setUTCDate(date.getUTCDate() - dayNum + 3);   // 그 주의 목요일
    const isoYear = date.getUTCFullYear();
    const firstThursday = new Date(Date.UTC(isoYear, 0, 4));
    const week = 1 + Math.round(
      ((date - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7,
    );
    return `${String(isoYear).padStart(4, "0")}-W${String(week).padStart(2, "0")}`;
  }

  function periodKeyForDate(dateStr, granularity) {
    if (!dateStr) return null;
    const year = parseInt(String(dateStr).slice(0, 4), 10);
    const month = parseInt(String(dateStr).slice(5, 7), 10);
    if (!Number.isFinite(year) || !(month >= 1 && month <= 12)) return null;
    if (granularity === "year") return String(year).padStart(4, "0");
    if (granularity === "month") {
      return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
    }
    if (granularity === "day" || granularity === "week") {
      const day = parseInt(String(dateStr).slice(8, 10), 10);
      if (!Number.isFinite(day)) return null;
      const probe = new Date(year, month - 1, day);
      if (Number.isNaN(probe.getTime())) return null;
      if (granularity === "day") {
        return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      }
      return isoWeekKey(year, month, day);
    }
    return `${year}-Q${Math.floor((month - 1) / 3) + 1}`;
  }

  // 개별 측정 오버레이 데이터셋 — 이상·제외 측정을 기간 막대 위에 점으로 얹는다.
  // 기간 평균 막대는 유효값 기준(중심선/관리밴드가 그것에 걸린다)이라, 규격을 벗어난
  // 사건이 있었다는 사실이 집계에 묻힌다. 각 이상/제외 측정을 실제 값 높이에 개별 점으로
  // 찍어, 통계에서 빠졌어도 "그날 규격 이탈이 있었다"가 눈에 남게 한다. periodLabels 에
  // 없는(표시 구간 밖) 측정은 건너뛴다. 동일 입력에 동일 datasets 반환(순수).
  function readingOverlayDatasets(readings, granularity, periodLabels, resolveCss) {
    const labelSet = new Set(periodLabels || []);
    const anomalyPts = [];
    const excludedPts = [];
    (readings || []).forEach((r) => {
      const key = periodKeyForDate(r.measured_date, granularity);
      if (!key || !labelSet.has(key)) return;
      if (r.status === "excluded" || r.excluded) {
        excludedPts.push({ x: key, y: r.viscosity });
      } else if (r.status === "anomaly") {
        anomalyPts.push({ x: key, y: r.viscosity });
      }
    });
    const datasets = [];
    if (anomalyPts.length) {
      datasets.push({
        type: "scatter",
        label: "이상 측정",
        data: anomalyPts,
        backgroundColor: resolveCss("--status-error"),
        borderColor: resolveCss("--status-error"),
        pointStyle: "triangle",
        radius: 5,
        hoverRadius: 6,
        order: 0,
      });
    }
    if (excludedPts.length) {
      datasets.push({
        type: "scatter",
        label: "제외",
        data: excludedPts,
        backgroundColor: "transparent",
        borderColor: resolveCss("--text-tertiary"),
        pointStyle: "crossRot",
        borderWidth: 2,
        radius: 6,
        hoverRadius: 7,
        order: 0,
      });
    }
    return datasets;
  }

  // 기간 차트 y축의 하한/상한 후보. 막대 그래프인데 축이 데이터에 맞춰 확대되면
  // 4,850 과 4,900 처럼 사실상 같은 값이 두 배 차이나는 막대로 보인다 — 품질 판단을
  // 하는 화면에서 이건 그림이 거짓말을 하는 것이다. 축을 관리한계·규격에 걸어두면
  // 기간이 바뀌어도 눈금이 흔들리지 않고, 막대 길이를 서로 비교할 수 있다.
  // suggested* 는 범위를 넓히기만 하므로 실제 값이 한계를 벗어나면 그건 그대로 보인다.
  function periodChartYBounds(stats, product) {
    const candidates = [];
    const push = (v) => { if (v !== null && v !== undefined && Number.isFinite(Number(v))) candidates.push(Number(v)); };
    push(stats && stats.center);
    push(stats && stats.lcl);
    push(stats && stats.ucl);
    push(product && product.lower_limit);
    push(product && product.upper_limit);
    if (candidates.length < 2) return {};   // 기준이 없으면 기존대로 자동
    const min = Math.min(...candidates);
    const max = Math.max(...candidates);
    const pad = (max - min) * 0.15 || Math.abs(max) * 0.05 || 1;
    return { suggestedMin: min - pad, suggestedMax: max + pad };
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
    periodChartYBounds,
    periodKeyForDate,
    readingOverlayDatasets,
  };
})();

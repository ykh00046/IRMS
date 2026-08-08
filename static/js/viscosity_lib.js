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
 *   controlBandHtml, periodChartDatasets
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

  // 전기 대비 — 방향만 말하고 좋고 나쁨은 말하지 않는다.
  //
  // 예전에는 오르면 빨강(--status-error), 내리면 파랑(--brand)이었다. 그런데 점도는
  // 오르는 게 나쁜 값이 아니라 중심에서 벗어나는 게 나쁜 값이다. 목표 50 인 반제품에서
  // 47.0(-4.45)은 파랑, 51.4(+3.00)은 빨강으로 떴는데 중심에 가까운 쪽은 51.4 다 —
  // 색이 정확히 반대를 말하고 있었다(2026-08-08). 방향은 화살표로 충분하고, 규격
  // 이탈은 '이상'·'경고' 열과 행 배경색이 이미 말한다.
  function appendDeltaCell(row, delta) {
    const cell = document.createElement("td");
    cell.className = "num";
    if (delta === null || delta === undefined) {
      cell.textContent = "-";
    } else {
      const span = document.createElement("span");
      span.className = "visc-delta";
      const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "";
      span.textContent = `${arrow}${arrow ? " " : ""}${delta > 0 ? "+" : ""}${fmt(delta, 2)}`;
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

  // 관리 기준 한 줄 텍스트(controlSummary) 옆에 두는 '관리 밴드' 그림(순수 HTML 빌더).
  // 가로 밴드 하나(~44px)로 구간 관계와 최근 측정값의 위치를 한눈에 보여준다:
  //   · 규격 범위(product.lower_limit~upper_limit) 를 트랙 전체 폭으로
  //   · 그 안의 관리 한계(stats.lcl~ucl) 구간을 색으로(초록 = 관리 내)
  //   · 경고 한계(stats.uwl/lwl)가 있으면 관리~경고 사이를 주황으로
  //   · 중심(stats.center)을 세로 파선으로
  //   · lastValue(최근 측정값, null 가능)를 아래꼭짓점 삼각형 마커+값 라벨로
  // 좌표는 트랙 [lo, hi] 기준 백분율(순수 산술). 규격이 있으면 규격 기준, 없으면 관리
  // 한계 기준, 둘 다 없으면 빈 문자열(그릴 게 없다). 색은 인라인 hex 금지 — CSS 클래스로
  // (.visc-band-*) 빼고 viscosity.css 가 입힌다. rescaleBarsHtml(P-4) 와 같은 규약.
  function controlBandHtml(analysis, lastValue) {
    if (!analysis || !analysis.stats || !analysis.product) return "";
    const stats = analysis.stats;
    const product = analysis.product;
    const lo = product.lower_limit;
    const hi = product.upper_limit;
    const hasSpec = lo != null && hi != null && Number(lo) < Number(hi);
    // 트랙 범위 결정: 규격 우선, 없으면 관리 한계, 둘 다 없으면 그릴 게 없다.
    let tLo, tHi;
    if (hasSpec) {
      tLo = Number(lo);
      tHi = Number(hi);
    } else if (stats.lcl != null && stats.ucl != null && Number(stats.lcl) < Number(stats.ucl)) {
      tLo = Number(stats.lcl);
      tHi = Number(stats.ucl);
    } else {
      return "";
    }
    const span = tHi - tLo;
    // 값 → 트랙 기준 백분율(0~100, 클램프).
    const pct = (v) => {
      if (v == null || !Number.isFinite(Number(v))) return null;
      return Math.max(0, Math.min(100, ((Number(v) - tLo) / span) * 100));
    };

    // 색 구간을 쌓는다. 관리 한계(lcl/ucl)·경고 한계(uwl/lwl) 모두 트랙 범위 안으로
    // 클램프해 그린다(한계가 규격 밖이면 규격 끝까지가 그 색이다).
    const segments = [];
    const lclP = pct(stats.lcl);
    const uclP = pct(stats.ucl);
    const lwlP = pct(stats.lwl);
    const uwlP = pct(stats.uwl);
    const hasControl = lclP != null && uclP != null && uclP > lclP;
    const hasWarn = lwlP != null && uwlP != null && uwlP > lwlP
      && hasControl && lwlP > lclP && uwlP < uclP;
    if (hasControl) {
      // 관리 한계 바깥(규격 내) = 빨강(규격은 통과하지만 관리 밖). 규격이 곧 관리 한계면
      // 이 구간은 0폭이 되어 자연히 보이지 않는다.
      if (lclP > 0) segments.push(zone(0, lclP, "spec"));
      if (hasWarn) {
        // 경고 구간(관리~경고 사이) = 주황. 중앙(lwl~uwl) = 초록(목표).
        segments.push(zone(lclP, lwlP, "warn"));
        segments.push(zone(lwlP, uwlP, "target"));
        segments.push(zone(uwlP, uclP, "warn"));
      } else {
        // 경고 한계가 없으면 관리 밴드(lcl~ucl) 통째로 초록.
        segments.push(zone(lclP, uclP, "target"));
      }
      if (uclP < 100) segments.push(zone(uclP, 100, "spec"));
    } else {
      // 관리 한계가 없으면 트랙 전체를 중립(규격만 있는 상태)으로.
      segments.push(zone(0, 100, "spec"));
    }

    // 중심 파선 — 값이 있을 때만.
    const centerP = pct(stats.center);
    const centerLine = centerP != null
      ? `<span class="visc-band-center" style="left:${fmt(centerP, 2)}%"></span>`
      : "";

    // 최근 측정값 마커(아래꼭짓점 삼각형) + 값 라벨. null 이면 그리지 않는다.
    const valueP = pct(lastValue);
    const marker = valueP != null
      ? `<span class="visc-band-marker" style="left:${fmt(valueP, 2)}%">`
        + `<span class="visc-band-marker-tri"></span>`
        + `<span class="visc-band-marker-label">${fmt(lastValue)}</span>`
        + `</span>`
      : "";

    return `<div class="visc-band">`
      + `<div class="visc-band-track">`
      + segments.join("")
      + centerLine
      + `</div>`
      + `<div class="visc-band-scale">`
      + `<span class="visc-band-lo">${fmt(tLo)}</span>`
      + `<span class="visc-band-hi">${fmt(tHi)}</span>`
      + `</div>`
      + marker
      + `</div>`;
  }

  // 밴드 색 구간 하나의 HTML. left/width 는 퍼센트(순수 산술). colorClass 는 CSS 가
  // 입히는 색(target=초록, warn=주황, spec=빨강 계열). 0폭 구간은 빈 문자열(안 그림).
  function zone(left, right, colorClass) {
    const w = Math.max(0, right - left);
    if (w <= 0) return "";
    return `<span class="visc-band-zone ${colorClass}" style="left:${fmt(left, 2)}%;width:${fmt(w, 2)}%"></span>`;
  }

  // 기간 차트의 labels/datasets 을 순수하게 조립한다. DOM 에서 CSS 변수를
  // 읽는 일은 컨트롤러가 resolveCss 콜백으로 주입한다(라이브러리는 DOM 를
  // 직접 참조하지 않는다). 동일 periods/center/resolveCss 에 대해 동일한
  // datasets 을 반환한다.
  function periodChartDatasets(periods, center, resolveCss, limits) {
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
    // 규격 상·하한 — 이상 여부를 판단하는 화면인데 그림에는 중심선 하나뿐이라,
    // 막대가 규격을 넘었는지는 표의 '이상' 열을 따로 봐야 알 수 있었다(2026-08-08).
    // 관리한계(±kσ)가 아니라 규격을 그린다: 현장이 합불을 가르는 선은 규격이다.
    if (limits && labels.length) {
      const line = (label, value) => ({
        type: "line",
        label,
        data: labels.map(() => value),
        borderColor: resolveCss("--status-error"),
        borderDash: [2, 3],
        borderWidth: 1,
        pointRadius: 0,
        order: 1,
      });
      // ⚠ Number(null) 은 0 이고 0 은 유한수다 — Number.isFinite 만으로 거르면 규격을
      // 정하지 않은 반제품에 0 위치의 '규격 하한' 선이 그어진다(없는 기준을 그리는 셈).
      const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
      const lower = num(limits.lower);
      const upper = num(limits.upper);
      if (lower !== null && Number.isFinite(lower)) {
        datasets.push(line("규격 하한", lower));
      }
      if (upper !== null && Number.isFinite(upper)) {
        datasets.push(line("규격 상한", upper));
      }
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
  //
  // ⚠ 관리한계만 후보로 두면 한계를 벗어난 값에서 막대가 사라진다: suggested* 는
  // 범위를 넓히기만 하므로, 데이터 최솟값이 suggestedMin 보다 낮으면 Chart.js 가 축
  // 하한을 그 값에 정확히 맞춘다. 그러면 그 막대의 윗변이 축 바닥과 같아져 높이 0 이
  // 되고, 하필 그 값이 이상 측정이라 가장 봐야 할 막대만 안 보인다(2026-08-08 확인:
  // PB 35.0 이상 측정의 막대가 통째로 사라졌다). 그래서 실제 데이터 범위도 후보에
  // 넣어 축이 항상 데이터보다 아래위로 여유를 갖게 한다.
  function periodChartYBounds(stats, product, periods) {
    const candidates = [];
    const push = (v) => { if (v !== null && v !== undefined && Number.isFinite(Number(v))) candidates.push(Number(v)); };
    push(stats && stats.center);
    push(stats && stats.lcl);
    push(stats && stats.ucl);
    push(product && product.lower_limit);
    push(product && product.upper_limit);
    // 실제 값(구간 평균 + 전체 최소/최대) — 한계 밖으로 나간 값도 막대로 보이게.
    (periods || []).forEach((period) => push(period && period.mean));
    push(stats && stats.min);
    push(stats && stats.max);
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
    controlBandHtml,
    periodChartDatasets,
    periodChartYBounds,
    periodKeyForDate,
    readingOverlayDatasets,
  };
})();

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
 *   controlBandHtml, periodChartDatasets, periodChartYBounds, periodKeyForDate,
 *   readingOverlayDatasets, sourcePbLinkedReadings, sourcePbScatterDatasets,
 *   pbLinkNotice
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
  //
  // 기간 평균은 **선**이다(2026-08-13 재설계). 막대는 "0 에서 얼마나 큰가"를 말하는
  // 그림이라, 370~400 사이를 오가는 점도에서는 막대 길이가 전부 비슷해 변동을 읽을
  // 수 없었고 128.4 같은 이상값만 유독 짧은 막대 하나로 묻혔다. 선은 값 사이의
  // 차이(기울기)를 말하므로 같은 데이터에서 추세가 그대로 드러난다. 구간의 판정
  // (이상/경고)은 선 위 점 색으로 남긴다.
  function periodChartDatasets(periods, center, resolveCss, limits) {
    const labels = periods.map((period) => period.period);
    const data = periods.map((period) => period.mean);
    const colors = periods.map((period) => {
      if (period.anomaly_count > 0) return resolveCss("--status-error");
      if (period.warn_count > 0) return resolveCss("--status-warning");
      return resolveCss("--brand-mid");
    });
    const radii = periods.map((period) => (period.anomaly_count > 0 ? 6 : 3));
    const datasets = [{
      type: "line",
      label: "기간 평균",
      data,
      borderColor: resolveCss("--brand-mid"),
      backgroundColor: resolveCss("--brand-mid"),
      pointBackgroundColor: colors,
      pointBorderColor: colors,
      pointRadius: radii,
      pointHoverRadius: 7,
      borderWidth: 2,
      tension: 0.15,
      spanGaps: true,
      fill: false,
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
    const plainPts = [];
    (readings || []).forEach((r) => {
      const key = periodKeyForDate(r.measured_date, granularity);
      if (!key || !labelSet.has(key)) return;
      if (r.status === "excluded" || r.excluded) {
        excludedPts.push({ x: key, y: r.viscosity, lot: r.lot_no, date: r.measured_date });
      } else if (r.status === "anomaly") {
        anomalyPts.push({ x: key, y: r.viscosity, lot: r.lot_no, date: r.measured_date });
      } else {
        // 정상·경고 개별 측정 — 기간 평균 선 뒤에 옅은 점으로 깔아, 평균 하나가
        // 지워버린 '그 구간 안의 흩어짐'을 남긴다(2026-08-13 재설계).
        plainPts.push({ x: key, y: r.viscosity, lot: r.lot_no, date: r.measured_date });
      }
    });
    const datasets = [];
    if (plainPts.length) {
      datasets.push({
        type: "scatter",
        label: "개별 측정",
        data: plainPts,
        backgroundColor: resolveCss("--text-tertiary"),
        borderColor: resolveCss("--text-tertiary"),
        radius: 2.5,
        hoverRadius: 5,
        order: 3,
      });
    }
    if (anomalyPts.length) {
      datasets.push({
        type: "scatter",
        label: "이상 측정",
        data: anomalyPts,
        backgroundColor: resolveCss("--status-error"),
        borderColor: resolveCss("--status-error"),
        pointStyle: "triangle",
        radius: 8,
        hoverRadius: 10,
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
    // ① 그림이 반드시 담아야 하는 값들 — 중심·규격·실제 측정(구간 평균, 전체 최소/최대).
    const base = [];
    const pushTo = (list, v) => {
      if (v !== null && v !== undefined && Number.isFinite(Number(v))) list.push(Number(v));
    };
    pushTo(base, stats && stats.center);
    pushTo(base, product && product.lower_limit);
    pushTo(base, product && product.upper_limit);
    (periods || []).forEach((period) => pushTo(base, period && period.mean));
    pushTo(base, stats && stats.min);
    pushTo(base, stats && stats.max);

    const candidates = base.slice();
    // ② 통계 관리한계(±kσ)는 축을 고정해 주는 좋은 기준이지만, 이상값 하나가 σ 를
    //    부풀리면 한계가 데이터에서 멀찍이 떨어진다(측정 79~82 인데 lcl 31.7). 그 값을
    //    축에 넣으면 정상 구간이 화면 위쪽에 납작하게 눌려 변동을 다시 못 읽는다
    //    (막대를 선으로 바꾼 이유가 그것이다). 데이터·규격 범위에서 크게 벗어난
    //    한계는 축 후보에서 뺀다 — 관리한계 자체는 관리 밴드 그림이 따로 말한다.
    //
    //    '멀다'의 기준은 규격 폭이다: 규격이 정해진 반제품이라면 그 폭이 이 반제품에서
    //    의미 있는 눈금 간격이다. 규격 밴드를 그 폭의 절반만큼 넓힌 범위 안에 드는
    //    한계만 축에 쓴다. 규격이 없으면 판단 기준이 없으므로 종전대로 한계를 쓴다
    //    (그때는 한계가 유일한 고정점이다 — 눈금이 구간마다 흔들리지 않게 하는 원래 의도).
    const limits = [];
    pushTo(limits, stats && stats.lcl);
    pushTo(limits, stats && stats.ucl);
    if (limits.length) {
      const specLo = product && product.lower_limit;
      const specHi = product && product.upper_limit;
      const hasSpec = specLo !== null && specLo !== undefined && specLo !== ""
        && specHi !== null && specHi !== undefined && specHi !== ""
        && Number(specHi) > Number(specLo);
      if (!hasSpec) {
        limits.forEach((v) => candidates.push(v));
      } else {
        const width = Number(specHi) - Number(specLo);
        const lo = Number(specLo) - width * 0.5;
        const hi = Number(specHi) + width * 0.5;
        limits.forEach((v) => { if (v >= lo && v <= hi) candidates.push(v); });
      }
    }
    if (candidates.length < 2) return {};   // 기준이 없으면 기존대로 자동
    const min = Math.min(...candidates);
    const max = Math.max(...candidates);
    const pad = (max - min) * 0.15 || Math.abs(max) * 0.05 || 1;
    return { suggestedMin: min - pad, suggestedMax: max + pad };
  }

  // ── PB 연계 ────────────────────────────────────────────────────────────
  // '사용한 PB' 점도가 매칭된 측정만, 최신순으로. 표와 산점도가 같은 목록을 쓰도록
  // 한 곳에서 만든다(표는 20건씩 잘라 쓰고 그림은 전부 쓴다).
  function sourcePbLinkedReadings(readings) {
    return (readings || [])
      .filter((r) => r && r.source_pb_viscosity != null && String(r.material_lot || "").trim())
      .slice()
      .sort((a, b) => String(b.measured_date || "").localeCompare(String(a.measured_date || "")));
  }

  // hex(#rrggbb) → rgba — 오래된 점을 흐리게 그릴 때 쓴다. hex 가 아니면 원본 유지.
  function withAlpha(color, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(String(color || "").trim());
    if (!m) return color;
    const num = parseInt(m[1], 16);
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  // 최소제곱 직선 적합 + 상관계수. "관계는 그림이 말한다"의 다음 단계 — 점 76개만
  // 뿌려서는 한눈에 안 들어온다는 현장 지적(2026-08-13)에 따라, 결론(추세선·요약
  // 문장)을 그래프가 직접 말하게 한다. 이상 판정 점은 적합에서 뺀다(σ 통계와 동일
  // 원칙 — 이상 하나가 기울기를 끌고 가면 안 된다).
  function pbLinearFit(points) {
    const pts = (points || []).filter(
      (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y),
    );
    const n = pts.length;
    if (n < 3) return null;
    const mx = pts.reduce((s, p) => s + p.x, 0) / n;
    const my = pts.reduce((s, p) => s + p.y, 0) / n;
    let sxx = 0;
    let syy = 0;
    let sxy = 0;
    pts.forEach((p) => {
      sxx += (p.x - mx) * (p.x - mx);
      syy += (p.y - my) * (p.y - my);
      sxy += (p.x - mx) * (p.y - my);
    });
    if (sxx === 0 || syy === 0) return null;   // x 나 y 가 전부 같은 값 — 직선 무의미
    const slope = sxy / sxx;
    return {
      slope,
      intercept: my - slope * mx,
      r: sxy / Math.sqrt(sxx * syy),
      n,
      minX: Math.min.apply(null, pts.map((p) => p.x)),
      maxX: Math.max.apply(null, pts.map((p) => p.x)),
    };
  }

  // 산점도 위 한 줄 결론. 세기 구분은 통상 기준(|r| 0.2/0.4/0.7).
  function pbScatterSummary(fit) {
    if (!fit) return "표본이 적어 상관을 말하기 어렵습니다.";
    const a = Math.abs(fit.r);
    const rText = `r=${fit.r.toFixed(2)}, ${fit.n}건`;
    if (a < 0.2) {
      return `뚜렷한 상관 없음 (${rText}) — 이 반제품 점도는 사용한 PB 점도와 무관하게 움직입니다.`;
    }
    const strength = a >= 0.7 ? "상관 뚜렷" : a >= 0.4 ? "상관 중간" : "상관 약함";
    const slope = fit.slope;
    const slopeText = `사용한 PB 점도가 1 높으면 이 반제품은 약 ${slope >= 0 ? "+" : ""}${slope.toFixed(1)}`;
    return `${strength} (${rText}) — ${slopeText}`;
  }

  // PB 점도(x) ↔ 이 반제품 점도(y) 산점도 데이터셋. 표만으로는 "48cp PB 로 만들면
  // 80" 같은 관계가 76행을 눈으로 훑어야 보였다 — 관계는 그림이 말하는 게 맞다.
  // 이상 판정 측정은 붉은 삼각형으로 따로 뽑아 관계에서 벗어난 점이 드러나게 한다.
  //
  // options(선택): { fit, limits: {lower, upper, center}, recent }
  //   · fit    — pbLinearFit 결과(추세선). 호출부가 요약 문장과 같은 적합을 공유.
  //   · limits — 규격 상·하한·중심(기간 차트와 동일 규약)을 가로 기준선으로.
  //   · recent — 최근 몇 건을 진하게 그릴지(기본 10). 나머지는 흐리게 — 점이 다
  //              같은 얼굴이라 최근 상태가 안 보인다는 지적의 답.
  function sourcePbScatterDatasets(readings, resolveCss, options) {
    const opts = options || {};
    const recentN = Number.isFinite(opts.recent) ? opts.recent : 10;
    const linked = sourcePbLinkedReadings(readings);   // 최신순
    const recentPts = [];
    const olderPts = [];
    const flagged = [];
    linked.forEach((r, index) => {
      const point = {
        x: Number(r.source_pb_viscosity),
        y: Number(r.viscosity),
        lot: r.material_lot || "",
        date: r.measured_date || "",
        status: r.status || "",
      };
      if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return;
      if (r.status === "anomaly") flagged.push(point);
      else if (index < recentN) recentPts.push(point);
      else olderPts.push(point);
    });
    const brand = resolveCss("--brand-mid");
    const datasets = [];
    const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
    const allX = recentPts.concat(olderPts, flagged).map((p) => p.x);
    const xMin = allX.length ? Math.min.apply(null, allX) : null;
    const xMax = allX.length ? Math.max.apply(null, allX) : null;
    // 규격/중심 가로 기준선 — 점이 합격 띠 안인지가 즉시 보이게(기간 차트와 동일 규약).
    if (opts.limits && xMin !== null && xMax !== xMin) {
      const refLine = (label, value, color, dash) => ({
        type: "line",
        label,
        data: [{ x: xMin, y: value }, { x: xMax, y: value }],
        borderColor: color,
        borderDash: dash,
        borderWidth: 1,
        pointRadius: 0,
        order: 1,
      });
      const center = num(opts.limits.center);
      const lower = num(opts.limits.lower);
      const upper = num(opts.limits.upper);
      if (center !== null && Number.isFinite(center)) {
        datasets.push(refLine("중심", center, resolveCss("--status-success"), [4, 4]));
      }
      if (lower !== null && Number.isFinite(lower)) {
        datasets.push(refLine("규격 하한", lower, resolveCss("--status-error"), [2, 3]));
      }
      if (upper !== null && Number.isFinite(upper)) {
        datasets.push(refLine("규격 상한", upper, resolveCss("--status-error"), [2, 3]));
      }
    }
    // 추세선 — 눈이 회귀선을 상상하지 않아도 되게 그림이 직접 긋는다.
    const fit = opts.fit;
    if (fit && Number.isFinite(fit.slope) && fit.maxX !== fit.minX) {
      datasets.push({
        type: "line",
        label: "추세선",
        data: [
          { x: fit.minX, y: fit.intercept + fit.slope * fit.minX },
          { x: fit.maxX, y: fit.intercept + fit.slope * fit.maxX },
        ],
        borderColor: withAlpha(brand, 0.8),
        borderDash: [6, 4],
        borderWidth: 2,
        pointRadius: 0,
        order: 1,
      });
    }
    if (olderPts.length) {
      datasets.push({
        type: "scatter",
        label: "이전 측정",
        data: olderPts,
        backgroundColor: withAlpha(brand, 0.3),
        borderColor: withAlpha(brand, 0.3),
        radius: 4,
        hoverRadius: 6,
        order: 2,
      });
    }
    if (recentPts.length) {
      datasets.push({
        type: "scatter",
        label: `최근 ${recentPts.length}건`,
        data: recentPts,
        backgroundColor: brand,
        borderColor: brand,
        radius: 5,
        hoverRadius: 7,
        order: 2,
      });
    }
    if (flagged.length) {
      datasets.push({
        type: "scatter",
        label: "이상",
        data: flagged,
        backgroundColor: resolveCss("--status-error"),
        borderColor: resolveCss("--status-error"),
        pointStyle: "triangle",
        radius: 8,
        hoverRadius: 10,
        order: 2,
      });
    }
    return datasets;
  }

  // PB 연계 탭 안내문. 종전에는 매칭이 0 이면 패널을 통째로 숨겨서, 연계가 안 된
  // 것인지 원래 없는 것인지 화면이 말해 주지 않았다(2026-08-13 검토 6번).
  function pbLinkNotice(pbLink, linkedCount) {
    const withLot = Number((pbLink && pbLink.readings_with_lot) || 0);
    const matched = Number((pbLink && pbLink.matched) || 0);
    if (withLot === 0) {
      return "이 반제품은 PB 연계 기록이 없습니다. 배합에 사용한 PB LOT 이 기록되면 여기에 표시됩니다.";
    }
    if (matched === 0) {
      return `사용한 PB LOT 이 ${withLot}건 기록됐지만, 그 PB 의 점도를 찾지 못했습니다`
        + " (PB 반제품 측정 미등록이거나 LOT 표기가 다릅니다).";
    }
    return `${linkedCount}건 · 사용한 PB의 점도와 나란히`;
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
    sourcePbLinkedReadings,
    sourcePbScatterDatasets,
    pbLinkNotice,
    pbLinearFit,
    pbScatterSummary,
    withAlpha,
  };
})();

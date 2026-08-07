/**
 * insight.js — 배합 분석 (/insight).
 *
 * 대시보드가 '지금 어떤가'를 보는 곳이라면, 이 화면은 기간을 잡고 '무엇이 얼마나,
 * 어떻게 변했나'를 답하는 곳이다. 그래서 두 가지가 화면의 뼈대다:
 *   ① 모든 지표에 직전 동기간 대비를 붙인다 — 숫자 하나는 많고 적음을 말하지 못한다.
 *   ② 시간축을 준다 — 개선/악화는 한 시점 스냅샷으로는 보이지 않는다.
 *
 * 서버 왕복은 GET /blend/analysis 한 번이다(지표·추세·제품·자재·품질을 한꺼번에).
 * LOT 추적만 별도 API 이고 기간 필터를 따르지 않는다(추적은 누락이 더 위험).
 */
(function () {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  const num = (v, d = 0) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", {
        minimumFractionDigits: d, maximumFractionDigits: d,
      });
  // 현장은 kg 로 말한다. g 그대로 두면 1,908,073.9 같은 일곱 자리가 되어 아무도 못 읽는다.
  const kg = (g, d = 1) => num((Number(g) || 0) / 1000, d);

  let state = null;          // 마지막 /blend/analysis 응답
  const charts = {};         // id → Chart 인스턴스
  const sortState = {};      // 표 id → {key, dir}
  let activeTab = "overview";

  // ── 날짜 ────────────────────────────────────────────────────────────────
  // 로컬 날짜 기준 — toISOString() 은 UTC 라 KST 오전 9시 이전에는 '오늘'이 하루 전으로
  // 밀린다. work_date 는 로컬 날짜로 저장되므로(blend_lib.todayISO) 그대로 두면 야간조가
  // 새벽에 조회할 때 그날 배합이 0건으로 보인다.
  function localISO(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function daysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return localISO(d);
  }
  function today() {
    return localISO(new Date());
  }

  function currentQuery() {
    return {
      start_date: $("insight-from").value || undefined,
      end_date: $("insight-to").value || undefined,
      bucket: $("insight-bucket").value || "month",
    };
  }

  // ── 지표 카드 ───────────────────────────────────────────────────────────
  // goodWhen: 'up' | 'down' | null. 취소율은 내려가야 좋고 계량률은 올라가야 좋다 —
  // 화살표 색을 지표마다 뒤집지 않으면 빨강이 무슨 뜻인지 매번 생각해야 한다.
  function deltaChip(cur, prev, opts) {
    if (prev === null || prev === undefined) {
      return '<span class="delta-chip delta-none">비교 기간 없음</span>';
    }
    const diff = Number(cur) - Number(prev);
    const unit = opts.pointUnit ? "%p" : "";
    const rounded = Math.round(diff * 10) / 10;
    if (Math.abs(rounded) < 0.05) {
      return '<span class="delta-chip delta-flat">변화 없음</span>';
    }
    const up = rounded > 0;
    let tone = "delta-flat";
    if (opts.goodWhen === "up") tone = up ? "delta-good" : "delta-bad";
    else if (opts.goodWhen === "down") tone = up ? "delta-bad" : "delta-good";
    else tone = "delta-neutral";
    // 비율 지표는 %p 로만 말한다(30%→33% 를 "10% 증가"로 적으면 오독된다).
    let text;
    if (opts.pointUnit) {
      text = `${up ? "+" : ""}${num(rounded, 1)}${unit}`;
    } else {
      const pct = Number(prev) === 0 ? null : (diff / Math.abs(Number(prev))) * 100;
      const amount = opts.decimals ? num(rounded, opts.decimals) : num(Math.round(rounded));
      text = `${up ? "+" : ""}${amount}` + (pct === null ? "" : ` (${up ? "+" : ""}${num(pct, 1)}%)`);
    }
    return `<span class="delta-chip ${tone}">${esc(text)}</span>`;
  }

  function metricCard(label, value, unit, hint) {
    return `
      <article class="panel metric-card insight-metric-card">
        <span class="metric-label">${esc(label)}</span>
        <strong class="metric-value">${value}<span class="metric-unit">${esc(unit)}</span></strong>
        <div class="insight-metric-foot">${hint || ""}</div>
      </article>`;
  }

  function renderMetrics() {
    const s = state.summary;
    $("insight-metrics").innerHTML = [
      metricCard("배합 건수", num(s.records), "건",
        deltaChip(s.records, s.records_prev, { goodWhen: "up" })),
      metricCard("총 생산량", kg(s.total_weight_g), "kg",
        deltaChip(s.total_weight_g / 1000,
          s.total_weight_prev === null ? null : s.total_weight_prev / 1000,
          { goodWhen: "up", decimals: 1 })),
      metricCard("저울 계량률", num(s.scale_rate, 1), "%",
        deltaChip(s.scale_rate, s.scale_rate_prev, { goodWhen: "up", pointUnit: true })),
      metricCard("취소율", num(s.cancel_rate, 1), "%",
        deltaChip(s.cancel_rate, s.cancel_rate_prev, { goodWhen: "down", pointUnit: true })),
      metricCard("제품 종수", num(s.product_count), "종",
        deltaChip(s.product_count, s.product_count_prev, { goodWhen: null })),
      metricCard("자재 종수", num(s.material_count), "종",
        deltaChip(s.material_count, s.material_count_prev, { goodWhen: null })),
    ].join("");

    const totalActual = state.materials.reduce((a, m) => a + m.total_actual, 0);
    $("insight-material-metrics").innerHTML = [
      metricCard("총 자재 소비량", kg(totalActual), "kg",
        '<span class="muted small">계량 상세 합계</span>'),
      metricCard("자재 종수", num(s.material_count), "종",
        '<span class="muted small">기간 내 1회 이상 투입</span>'),
      metricCard("투입 로스 보정 누계", num(s.loss_comp_total_g, 1), "g",
        '<span class="muted small">보정으로 더 투입된 양</span>'),
    ].join("");

    $("insight-quality-metrics").innerHTML = [
      metricCard("수동 입력 배합", num(s.manual_records), "건",
        `<span class="muted small">완료 ${num(s.records)}건 중</span>`),
      // 값은 건수인데 칩에 비율 증감을 달면 무엇이 변했다는 건지 어긋난다 — 비율은 글로 적는다.
      metricCard("취소 배합", num(s.canceled_records), "건",
        `<span class="muted small">취소율 ${num(s.cancel_rate, 1)}%</span>`),
      metricCard("증량 적용 배합", num(s.rescale_records), "건",
        '<span class="muted small">책임자 승인 필요 건</span>'),
      metricCard("1회 상한 초과", num(s.oversize_records), "건",
        '<span class="muted small">저장은 되되 기록에 남음</span>'),
    ].join("");
  }

  // ── 표 (정렬 가능) ──────────────────────────────────────────────────────
  function sortRows(tableId, rows, defaultKey) {
    const st = sortState[tableId] || { key: defaultKey, dir: "desc" };
    const { key, dir } = st;
    const sorted = rows.slice().sort((a, b) => {
      const x = a[key];
      const y = b[key];
      let c;
      if (typeof x === "number" && typeof y === "number") c = x - y;
      else c = String(x == null ? "" : x).localeCompare(String(y == null ? "" : y), "ko");
      return dir === "asc" ? c : -c;
    });
    const table = $(tableId);
    if (table) {
      table.querySelectorAll("th[data-sort]").forEach((th) => {
        th.classList.toggle("sorted-asc", th.dataset.sort === key && dir === "asc");
        th.classList.toggle("sorted-desc", th.dataset.sort === key && dir === "desc");
      });
    }
    return sorted;
  }

  function bindSort(tableId, defaultKey, rerender) {
    const table = $(tableId);
    if (!table) return;
    table.querySelectorAll("th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        const st = sortState[tableId] || { key: defaultKey, dir: "desc" };
        sortState[tableId] = {
          key,
          // 같은 열을 다시 누르면 방향만 뒤집는다. 새 열은 숫자면 큰 값부터가 자연스럽다.
          dir: st.key === key ? (st.dir === "desc" ? "asc" : "desc") : "desc",
        };
        rerender();
      });
    });
  }

  function emptyRow(cols, text) {
    return `<tr><td colspan="${cols}" class="muted">${esc(text)}</td></tr>`;
  }

  function renderProductTable() {
    const rows = sortRows("insight-product-table", state.products, "batch_count");
    $("insight-product-body").innerHTML = rows.length
      ? rows.map((p) => `
        <tr>
          <td>${esc(p.product_name)}</td>
          <td class="num">${num(p.batch_count)}</td>
          <td class="num">${kg(p.total_amount)}</td>
          <td class="num">${num(p.share, 1)}%</td>
          <td>${esc(p.last_work_date || "-")}</td>
        </tr>`).join("")
      : emptyRow(5, "해당 기간 완료된 배합이 없습니다.");
  }

  function renderMaterialTable() {
    const rows = sortRows(
      "insight-material-table",
      state.materials.map((m) => ({ ...m, diff: m.total_actual - m.total_theory })),
      "total_actual",
    );
    $("insight-material-body").innerHTML = rows.length
      ? rows.map((m) => {
        const diff = Math.round(m.diff * 100) / 100;
        // 색은 '확인할 것'에만 쓴다. 한 행의 허용 편차가 0.05g 이고 자재 하나가 수백
        // 배치에 들어가므로, 누적 차이 ±0.2g 에 경고색을 칠하면 모든 줄이 빨개져서
        // 정작 봐야 할 줄이 묻힌다. 누적 허용 범위(행수×0.05g)의 두 배를 넘을 때만 칠한다.
        // (레시피별 허용 편차를 따로 지정한 경우가 있어 기본값 기준의 근사치다.)
        const envelope = Math.max(1, (m.usage_count || 0) * 0.05 * 2);
        const diffClass = Math.abs(diff) <= envelope
          ? "muted"
          : (diff > 0 ? "variance-over" : "variance-under");
        return `
        <tr>
          <td>${esc(m.material_name)}</td>
          <td class="num">${kg(m.total_actual, 2)}</td>
          <td class="num">${kg(m.total_theory, 2)}</td>
          <td class="num ${diffClass}">${diff > 0 ? "+" : ""}${num(diff, 2)}</td>
          <td class="num">${num(m.usage_count)}</td>
          <td class="num">${num(m.share, 1)}%</td>
          <td class="num">${m.loss_comp_g ? num(m.loss_comp_g, 1) : "-"}</td>
        </tr>`;
      }).join("")
      : emptyRow(7, "해당 기간 자재 사용 기록이 없습니다.");
  }

  function renderQualityTables() {
    const workers = sortRows(
      "insight-worker-table", state.quality.by_worker || [], "manual_records",
    );
    $("insight-mistake-worker").innerHTML = workers.length
      ? workers.map((w) => `
        <tr>
          <td>${esc(w.worker || "")}</td>
          <td class="num">${num(w.records)}</td>
          <td class="num">${num(w.manual_records)}</td>
          <td class="num">${num(w.manual_rate, 1)}%</td>
          <td class="num">${num(w.canceled_records)}</td>
        </tr>`).join("")
      : emptyRow(5, "해당 기간 기록 없음");

    const mats = sortRows(
      "insight-mat-quality-table", state.quality.by_material || [], "manual_rows",
    );
    $("insight-mistake-material").innerHTML = mats.length
      ? mats.map((m) => `
        <tr>
          <td>${esc(m.material_name || "")}</td>
          <td class="num">${num(m.rows)}</td>
          <td class="num">${num(m.manual_rows)}</td>
          <td class="num">${num(m.manual_rate, 1)}%</td>
        </tr>`).join("")
      : emptyRow(4, "수동 입력 없음 — 모두 저울로 계량됨");
  }

  // ── 차트 ────────────────────────────────────────────────────────────────
  // 탭이 숨어 있는 동안(display:none) 캔버스는 크기가 0이라 그려도 찌그러진다.
  // 그래서 차트는 '보이는 탭' 것만 그리고, 탭을 열 때 그 탭 것을 그린다.
  function css(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }
  const PALETTE = [
    "#1b4079", "#2c5d9b", "#3d7ab8", "#c98212", "#1e9d6b",
    "#7b5ea7", "#d8453f", "#4a8fa8", "#8a7f3d", "#a3567c",
    "#5b6b7f", "#2f8f5b",
  ];

  const PARTIAL_BAR = "rgba(27, 64, 121, 0.35)";

  // 막대마다 색이 다른 데이터셋은 Chart.js 가 범례 칩에 '첫 칸 색'을 쓴다. 첫 구간이
  // 잘린 구간이면 범례가 통째로 흐리게 보여, 그 흐림이 무슨 뜻인지 되레 헷갈린다.
  // 범례 칩 색은 데이터셋의 대표색으로 직접 고정한다.
  function solidLegend(colors) {
    return {
      labels: {
        color: css("--text-secondary", "#64748b"),
        boxWidth: 12,
        usePointStyle: true,
        generateLabels: (chart) => chart.data.datasets.map((ds, i) => ({
          text: ds.label,
          fillStyle: colors[i] || ds.borderColor,
          strokeStyle: colors[i] || ds.borderColor,
          lineWidth: 0,
          hidden: !chart.isDatasetVisible(i),
          datasetIndex: i,
        })),
      },
    };
  }

  // 잘린 구간 위에 마우스를 올리면 왜 낮은지 말해 준다.
  function partialFooter(trend) {
    return (items) => {
      const row = trend[items[0] && items[0].dataIndex];
      return row && row.partial
        ? "이 구간은 조회 기간에 일부만 걸쳐 있습니다 — 다른 구간과 길이가 다릅니다."
        : "";
    };
  }

  // 잘린 구간은 라벨에도 표시한다 — 색 구분은 흑백 인쇄에서 사라진다.
  function bucketLabels(trend) {
    return trend.map((x) => (x.partial ? `${x.bucket} (일부)` : x.bucket));
  }

  function kill(id) {
    if (charts[id]) {
      charts[id].destroy();
      delete charts[id];
    }
  }

  function baseOptions(extra) {
    const text = css("--text-secondary", "#64748b");
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: text, boxWidth: 12, usePointStyle: true } },
      },
    }, extra || {});
  }

  function renderTrendChart() {
    const canvas = $("insight-trend-chart");
    if (!canvas || typeof Chart === "undefined") return;
    kill("trend");
    const t = state.trend || [];
    // 잘린 구간(양 끝)은 값이 작을 수밖에 없다 — 표시해 두지 않으면 생산 급감으로 읽힌다.
    const partialCount = t.filter((x) => x.partial).length;
    $("insight-trend-note").textContent = t.length
      ? `${state.bucket === "week" ? "주" : "월"} ${t.length}개 구간`
        + (partialCount ? ` · 흐린 막대 ${partialCount}개는 기간이 잘린 구간` : "")
      : "";
    if (!t.length) return;
    const text = css("--text-secondary", "#64748b");
    charts.trend = new Chart(canvas, {
      data: {
        labels: bucketLabels(t),
        datasets: [
          {
            type: "bar",
            label: "배치 수",
            // 잘린 구간은 옅게 — 색만 흐려도 "이 막대는 다른 막대와 같은 길이의 기간이
            // 아니다"가 한눈에 읽힌다(라벨의 '(일부)'는 흑백 인쇄용 이중 표시).
            data: t.map((x) => x.records),
            backgroundColor: t.map((x) =>
              x.partial ? PARTIAL_BAR : css("--accent-primary", "#1b4079")),
            borderRadius: 4,
            maxBarThickness: 48,
            yAxisID: "y",
            order: 2,
          },
          {
            type: "line",
            label: "생산량(kg)",
            data: t.map((x) => Math.round(x.weight_g / 1000)),
            borderColor: css("--status-warning", "#c98212"),
            backgroundColor: css("--status-warning", "#c98212"),
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 3,
            yAxisID: "y1",
            order: 1,
          },
        ],
      },
      options: baseOptions({
        plugins: {
          legend: solidLegend([css("--accent-primary", "#1b4079"), css("--status-warning", "#c98212")]),
          tooltip: { callbacks: { footer: partialFooter(t) } },
        },
        scales: {
          x: { ticks: { color: text }, grid: { display: false } },
          y: {
            beginAtZero: true, position: "left",
            title: { display: true, text: "배치 수", color: text },
            ticks: { color: text, precision: 0 },
          },
          y1: {
            beginAtZero: true, position: "right",
            title: { display: true, text: "생산량(kg)", color: text },
            ticks: { color: text },
            grid: { drawOnChartArea: false },
          },
        },
      }),
    });
  }

  function renderProductChart() {
    const canvas = $("insight-product-chart");
    if (!canvas || typeof Chart === "undefined") return;
    kill("product");
    const items = (state.products || []).slice();
    $("insight-product-note").textContent = items.length ? `제품 ${items.length}종` : "";
    if (!items.length) return;
    // 상위 8종 + 나머지 묶음 — 조각이 스무 개면 도넛은 아무것도 말해주지 않는다.
    const top = items.slice(0, 8);
    const rest = items.slice(8);
    const labels = top.map((p) => p.product_name);
    const values = top.map((p) => Math.round(p.total_amount / 1000));
    if (rest.length) {
      labels.push(`기타 ${rest.length}종`);
      values.push(Math.round(rest.reduce((a, p) => a + p.total_amount, 0) / 1000));
    }
    charts.product = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: PALETTE, borderWidth: 0 }],
      },
      options: baseOptions({
        cutout: "58%",
        plugins: {
          legend: {
            position: "right",
            labels: { color: css("--text-secondary", "#64748b"), boxWidth: 12, usePointStyle: true },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const total = values.reduce((a, b) => a + b, 0);
                const pct = total ? (ctx.parsed / total) * 100 : 0;
                return `${ctx.label}: ${num(ctx.parsed)} kg (${num(pct, 1)}%)`;
              },
            },
          },
        },
      }),
    });
  }

  function renderMaterialChart() {
    const canvas = $("insight-material-chart");
    if (!canvas || typeof Chart === "undefined") return;
    kill("material");
    const top = (state.materials || []).slice(0, 12);
    if (!top.length) return;
    const text = css("--text-secondary", "#64748b");
    charts.material = new Chart(canvas, {
      type: "bar",
      data: {
        labels: top.map((m) => m.material_name),
        datasets: [{
          label: "실제 사용량(kg)",
          data: top.map((m) => Math.round(m.total_actual / 1000)),
          backgroundColor: css("--accent-primary", "#1b4079"),
          borderRadius: 4,
        }],
      },
      options: baseOptions({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { color: text } },
          y: { ticks: { color: text }, grid: { display: false } },
        },
      }),
    });
  }

  function renderQualityChart() {
    const canvas = $("insight-quality-chart");
    if (!canvas || typeof Chart === "undefined") return;
    kill("quality");
    const t = state.trend || [];
    if (!t.length) return;
    const text = css("--text-secondary", "#64748b");
    charts.quality = new Chart(canvas, {
      data: {
        labels: bucketLabels(t),
        datasets: [
          {
            type: "line",
            label: "저울 계량률(%)",
            data: t.map((x) => x.scale_rate),
            borderColor: css("--status-success", "#1e9d6b"),
            backgroundColor: css("--status-success", "#1e9d6b"),
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 3,
            yAxisID: "y",
            order: 1,
          },
          {
            type: "bar",
            label: "수동 입력(건)",
            data: t.map((x) => x.manual_records),
            backgroundColor: css("--status-warning", "#c98212"),
            borderRadius: 4,
            maxBarThickness: 36,
            yAxisID: "y1",
            order: 3,
          },
          {
            type: "bar",
            label: "취소(건)",
            data: t.map((x) => x.canceled_records),
            backgroundColor: css("--status-error", "#d8453f"),
            borderRadius: 4,
            maxBarThickness: 36,
            yAxisID: "y1",
            order: 2,
          },
        ],
      },
      options: baseOptions({
        scales: {
          x: { ticks: { color: text }, grid: { display: false } },
          y: {
            position: "left", min: 0, max: 100,
            title: { display: true, text: "저울 계량률(%)", color: text },
            ticks: { color: text },
          },
          y1: {
            position: "right", beginAtZero: true,
            title: { display: true, text: "건수", color: text },
            ticks: { color: text, precision: 0 },
            grid: { drawOnChartArea: false },
          },
        },
      }),
    });
  }

  function renderChartsFor(tab) {
    if (!state) return;
    if (tab === "overview") {
      renderTrendChart();
      renderProductChart();
    } else if (tab === "materials") {
      renderMaterialChart();
    } else if (tab === "quality") {
      renderQualityChart();
    }
  }

  // ── 조회 ────────────────────────────────────────────────────────────────
  function renderSummaryLine() {
    const r = state.range;
    const p = state.previous;
    // 양쪽이 다 비면 "전체 ~ 오늘"이 되어 읽기 이상했다 — 그냥 '전체 기간'이라고 쓴다.
    const period = (!r.start && !r.end)
      ? "전체 기간"
      : `${r.start || "처음"} ~ ${r.end || "오늘"}`;
    const days = r.days ? ` (${num(r.days)}일)` : "";
    const compare = p
      ? ` · 비교 기간 ${p.start} ~ ${p.end}`
      : " · 시작·종료를 모두 지정하면 직전 동기간과 비교합니다";
    $("insight-filter-summary").textContent =
      `${period}${days} · 완료 ${num(state.summary.records)}건${compare}`;
  }

  async function loadAll() {
    if (!request) return;
    const btn = $("insight-query");
    IRMS.btnLoading && IRMS.btnLoading(btn, true);
    try {
      state = await request("/blend/analysis", { query: currentQuery() });
      renderSummaryLine();
      renderMetrics();
      renderProductTable();
      renderMaterialTable();
      renderQualityTables();
      renderChartsFor(activeTab);
    } catch (e) {
      IRMS.notify && IRMS.notify(`분석 조회 실패: ${e.message || e}`, "error");
    } finally {
      IRMS.btnLoading && IRMS.btnLoading(btn, false);
    }
  }

  // ── LOT 추적 ────────────────────────────────────────────────────────────
  const STATUS_LABEL = { completed: "완료", canceled: "취소" };

  async function traceMaterialLot() {
    const lot = $("insight-trace-lot").value.trim();
    const body = $("insight-trace-body");
    const summary = $("insight-trace-summary");
    const note = $("insight-trace-note");
    if (!lot) {
      body.innerHTML = emptyRow(8, "자재 LOT을 입력하고 추적하세요.");
      summary.textContent = "";
      note.hidden = true;
      return;
    }
    try {
      const d = await request("/blend/material-lot-trace", { query: { lot } });
      const items = d.items || [];
      summary.textContent = items.length ? `배합 ${num(d.record_count)}건 · 자재 행 ${num(d.total)}건` : "";
      // 서버 상한 도달 — 조용히 자르지 않고 알린다.
      if (d.truncated) {
        note.textContent = `표시 상한 ${num(d.limit || d.total)}행에 도달 — 일부가 잘렸을 수 있습니다. LOT을 더 정확히 입력해 좁히세요.`;
        note.hidden = false;
      } else {
        note.hidden = true;
      }
      body.innerHTML = items.length
        ? items.map((it) => `
          <tr>
            <td>${esc(it.work_date)}</td>
            <td><a class="insight-trace-lot-link" href="/status?search=${encodeURIComponent(it.product_lot)}">${esc(it.product_lot)}</a></td>
            <td>${esc(it.product_name)}</td>
            <td>${esc(it.material_name)}</td>
            <td>${esc(it.material_lot)}</td>
            <td class="num">${num(it.actual_amount, 2)}</td>
            <td>${esc(it.worker)}</td>
            <td>${esc(STATUS_LABEL[it.status] || it.status)}</td>
          </tr>`).join("")
        : emptyRow(8, `'${lot}' 이 투입된 배합 기록이 없습니다.`);
    } catch (e) {
      body.innerHTML = emptyRow(8, `추적 실패: ${e.message || e}`);
      summary.textContent = "";
      note.hidden = true;
    }
  }

  // ── 초기화 ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }

    const TAB_TITLES = {
      overview: "개요", materials: "자재 소비", quality: "품질·이상", trace: "LOT 추적",
    };
    const tabBtns = document.querySelectorAll(".insight-tabs .mgmt-tab");
    tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        tabBtns.forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab;
        activeTab = tab;
        const panel = $(`tab-${tab}`);
        if (panel) panel.classList.add("active");
        const heading = document.querySelector(".topbar-heading");
        if (heading && TAB_TITLES[tab]) heading.textContent = `배합 분석 · ${TAB_TITLES[tab]}`;
        renderChartsFor(tab);
      });
    });

    // 프리셋 — 누른 것만 표시하고, 날짜를 직접 고치면 더는 그 프리셋이 아니므로 해제한다.
    const RANGE_BTNS = [
      "insight-range-month", "insight-range-90", "insight-range-year", "insight-range-all",
    ];
    function markRange(activeId) {
      RANGE_BTNS.forEach((id) => $(id).classList.toggle("active", id === activeId));
    }
    function applyRange(activeId, from, to) {
      markRange(activeId);
      $("insight-from").value = from;
      $("insight-to").value = to;
      loadAll();
    }
    // 전기 대비를 쓰려면 종료일도 있어야 한다 — 프리셋은 항상 오늘로 닫아 준다.
    $("insight-range-month").addEventListener("click",
      () => applyRange("insight-range-month", daysAgo(29), today()));
    $("insight-range-90").addEventListener("click",
      () => applyRange("insight-range-90", daysAgo(89), today()));
    $("insight-range-year").addEventListener("click",
      () => applyRange("insight-range-year", `${new Date().getFullYear()}-01-01`, today()));
    $("insight-range-all").addEventListener("click",
      () => applyRange("insight-range-all", "", ""));
    $("insight-from").addEventListener("change", () => markRange(null));
    $("insight-to").addEventListener("change", () => markRange(null));
    $("insight-query").addEventListener("click", loadAll);
    $("insight-bucket").addEventListener("change", loadAll);

    $("insight-export").addEventListener("click", () => {
      const q = currentQuery();
      const params = new URLSearchParams();
      if (q.start_date) params.set("start_date", q.start_date);
      if (q.end_date) params.set("end_date", q.end_date);
      params.set("bucket", q.bucket);
      window.location.href = `/api/blend/analysis/export?${params.toString()}`;
    });
    $("insight-print").addEventListener("click", () => window.print());

    bindSort("insight-product-table", "batch_count", renderProductTable);
    bindSort("insight-material-table", "total_actual", renderMaterialTable);
    bindSort("insight-worker-table", "manual_records", renderQualityTables);
    bindSort("insight-mat-quality-table", "manual_rows", renderQualityTables);

    $("insight-trace-btn").addEventListener("click", traceMaterialLot);
    $("insight-trace-lot").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) traceMaterialLot();
    });

    markRange("insight-range-90");
    $("insight-from").value = daysAgo(89);
    $("insight-to").value = today();
    loadAll();
  });
})();

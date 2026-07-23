/**
 * insight.js — 배합 분석 (/insight).
 * 완료된 배합 기록(blend_records/blend_details)에서
 *  · 자재별 실제 사용량·비중·건수 집계
 *  · 제품별 배합 빈도(배치 수) 차트 + 배치 상세(이론/실제/편차) + Excel 다운로드
 */
(function () {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 1) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  let productChart = null;

  function isoDaysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }

  function currentRange() {
    return {
      start_date: $("insight-from").value || undefined,
      end_date: $("insight-to").value || undefined,
    };
  }

  // ── 자재 LOT 추적 — 원재료 LOT 이 투입된 배합 기록 역추적(리콜 대응) ──
  // 기간 필터와 무관하게 전 기간을 뒤진다(추적은 누락이 더 위험).
  const STATUS_LABEL = { completed: "완료", canceled: "취소" };

  async function traceMaterialLot() {
    const lot = $("insight-trace-lot").value.trim();
    const body = $("insight-trace-body");
    const summary = $("insight-trace-summary");
    const traceNote = $("insight-trace-note");
    if (!lot) {
      body.innerHTML = '<tr><td colspan="8" class="muted">자재 LOT을 입력하고 추적하세요.</td></tr>';
      summary.textContent = "";
      if (traceNote) traceNote.hidden = true;
      return;
    }
    try {
      const d = await request("/blend/material-lot-trace", { query: { lot } });
      const items = d.items || [];
      summary.textContent = items.length
        ? `배합 ${d.record_count}건 · 자재 행 ${d.total}건`
        : "";
      const note = $("insight-trace-note");
      if (note) {
        // 서버 상한(기본 500행) 도달 — 일부 잘림. LOT을 더 구체적으로 좁히도록 안내.
        if (d.truncated) {
          note.textContent = `표시 상한 ${fmt(d.limit || d.total, 0)}행에 도달 — 일부가 잘렸을 수 있습니다. LOT을 더 정확히 입력해 좁히세요.`;
          note.hidden = false;
        } else {
          note.hidden = true;
        }
      }
      if (!items.length) {
        body.innerHTML = `<tr><td colspan="8" class="muted">'${esc(lot)}' 이 투입된 배합 기록이 없습니다.</td></tr>`;
        return;
      }
      body.innerHTML = items.map((it) =>
        "<tr>"
        + `<td>${esc(it.work_date)}</td>`
        // 제품 LOT 클릭 → 배합 기록 화면을 그 LOT 으로 필터해 열기(딥링크)
        + `<td><a class="insight-trace-lot-link" href="/status?search=${encodeURIComponent(it.product_lot)}">${esc(it.product_lot)}</a></td>`
        + `<td>${esc(it.product_name)}</td>`
        + `<td>${esc(it.material_name)}</td>`
        + `<td>${esc(it.material_lot)}</td>`
        + `<td class="num">${fmt(it.actual_amount, 2)}</td>`
        + `<td>${esc(it.worker)}</td>`
        + `<td>${esc(STATUS_LABEL[it.status] || it.status)}</td>`
        + "</tr>"
      ).join("");
    } catch (e) {
      body.innerHTML = `<tr><td colspan="8" class="muted">추적 실패: ${esc(e.message || e)}</td></tr>`;
      summary.textContent = "";
      if (traceNote) traceNote.hidden = true;
    }
  }

  async function loadMaterials() {
    const { start_date: start, end_date: end } = currentRange();
    const body = $("insight-body");
    body.innerHTML = '<tr><td colspan="6" class="muted">불러오는 중…</td></tr>';
    try {
      const d = await request("/blend/material-usage", { query: { start_date: start, end_date: end } });
      $("metric-records").innerHTML = `${fmt(d.record_count, 0)}<span class="metric-unit">건</span>`;
      $("metric-weight").innerHTML = `${fmt(d.total_weight)}<span class="metric-unit">g</span>`;
      $("metric-materials").innerHTML = `${fmt(d.material_count, 0)}<span class="metric-unit">종</span>`;
      $("insight-filter-summary").textContent =
        `${start || "전체"} ~ ${end || "전체"} · 배합 ${fmt(d.record_count, 0)}건 · 자재 ${fmt(d.material_count, 0)}종`;
      const items = d.items || [];
      const totalW = d.total_weight || 0;
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="6" class="muted">기록이 없습니다.</td></tr>';
        return;
      }
      body.innerHTML = items
        .map((it, i) => {
          const pct = totalW > 0 ? (it.total_actual / totalW) * 100 : 0;
          return (
            `<tr><td>${i + 1}</td><td>${esc(it.material_name)}</td>` +
            `<td class="num">${fmt(it.total_actual)}</td>` +
            `<td class="num">${fmt(pct, 1)}</td>` +
            `<td class="num">${fmt(it.total_theory)}</td>` +
            `<td class="num">${fmt(it.usage_count, 0)}</td></tr>`
          );
        })
        .join("");
    } catch (e) {
      body.innerHTML = `<tr><td colspan="6" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
    }
  }

  function renderProductChart(items) {
    const canvas = $("insight-product-chart");
    if (!canvas || typeof Chart === "undefined") return;
    const top = items.slice(0, 10);
    if (productChart) {
      productChart.destroy();
      productChart = null;
    }
    if (!top.length) return;
    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--accent-primary").trim() || "#e8833a";
    const textColor = styles.getPropertyValue("--text-secondary").trim() || "#64748b";
    productChart = new Chart(canvas, {
      type: "bar",
      data: {
        labels: top.map((it) => it.product_name),
        datasets: [{
          label: "배치 수",
          data: top.map((it) => it.batch_count),
          backgroundColor: accent,
          borderRadius: 4,
          // 항목이 1~2개일 때 막대가 화면 절반을 채우는 과대 표시 방지
          maxBarThickness: 72,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: (ctx) => {
                const it = top[ctx.dataIndex];
                return `총 배합량 ${fmt(it.total_amount)}g · 최근 ${it.last_work_date || "-"}`;
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: textColor } },
          y: { beginAtZero: true, ticks: { color: textColor, precision: 0 } },
        },
      },
    });
  }

  function renderProductSelect(items) {
    const sel = $("insight-product");
    const prev = sel.value;
    sel.innerHTML =
      '<option value="">전체 제품</option>' +
      items
        .map((it) => `<option value="${esc(it.product_name)}">${esc(it.product_name)} (${fmt(it.batch_count, 0)}배치)</option>`)
        .join("");
    if (prev && items.some((it) => it.product_name === prev)) sel.value = prev;
  }

  async function loadProducts() {
    const { start_date: start, end_date: end } = currentRange();
    try {
      const d = await request("/blend/product-usage", { query: { start_date: start, end_date: end } });
      const items = d.items || [];
      $("metric-products").innerHTML = `${fmt(d.product_count, 0)}<span class="metric-unit">종</span>`;
      renderProductChart(items);
      renderProductSelect(items);
    } catch (e) {
      IRMS.notify && IRMS.notify(`제품별 분석 불러오기 실패: ${e.message || e}`, "error");
    }
  }

  function varianceCell(v) {
    if (v === null || v === undefined) return '<td class="num">-</td>';
    const n = Number(v);
    if (n > 0) return `<td class="num variance-over">+${fmt(n, 2)}</td>`;
    if (n < 0) return `<td class="num variance-under">${fmt(n, 2)}</td>`;
    return `<td class="num">0</td>`;
  }

  async function loadDetails() {
    const { start_date: start, end_date: end } = currentRange();
    const product = $("insight-product").value || undefined;
    const body = $("insight-detail-body");
    body.innerHTML = '<tr><td colspan="8" class="muted">불러오는 중…</td></tr>';
    try {
      const d = await request("/blend/batch-details", {
        query: { start_date: start, end_date: end, product },
      });
      const items = d.items || [];
      const note = $("insight-detail-note");
      // 화면 렌더는 최근 DETAIL_DISPLAY_CAP 행으로 제한(백엔드는 작업일 역순으로 이미 최신순).
      // 전체 데이터는 Excel 내보내기(백엔드 상한 10000)로 받는다.
      const DETAIL_DISPLAY_CAP = 200;
      const shown = items.slice(0, DETAIL_DISPLAY_CAP);
      $("insight-detail-summary").textContent =
        `배치 ${fmt(d.batch_count, 0)}건 · 자재 ${fmt(d.material_count, 0)}종 · ` +
        `표시 ${fmt(shown.length, 0)} / 전체 ${fmt(d.total, 0)}${d.truncated ? "+" : ""}행`;
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="8" class="muted">기록이 없습니다.</td></tr>';
        if (note) note.hidden = true;
        return;
      }
      if (note) {
        if (items.length > DETAIL_DISPLAY_CAP || d.truncated) {
          note.textContent = "최근 200건만 표시 — 기간을 좁히거나 Excel 내보내기를 이용하세요.";
          note.hidden = false;
        } else {
          note.hidden = true;
        }
      }
      body.innerHTML = shown
        .map(
          (it) =>
            `<tr><td>${esc(it.work_date)}</td>` +
            `<td>${esc(it.product_lot)}</td>` +
            `<td>${esc(it.material_name)}</td>` +
            `<td>${esc(it.material_lot || "-")}</td>` +
            `<td class="num">${fmt(it.ratio, 1)}</td>` +
            `<td class="num">${fmt(it.theory_amount, 2)}</td>` +
            `<td class="num">${fmt(it.actual_amount, 2)}</td>` +
            varianceCell(it.variance) +
            `</tr>`,
        )
        .join("");
    } catch (e) {
      body.innerHTML = `<tr><td colspan="8" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
      const note = $("insight-detail-note");
      if (note) note.hidden = true;
    }
  }

  function exportDetails() {
    const { start_date: start, end_date: end } = currentRange();
    const product = $("insight-product").value;
    const params = new URLSearchParams();
    if (start) params.set("start_date", start);
    if (end) params.set("end_date", end);
    if (product) params.set("product", product);
    const qs = params.toString();
    window.location.href = `/api/blend/batch-details/export${qs ? `?${qs}` : ""}`;
  }

  // 이상 통계(수동 입력·취소) — 작업자별·자재별 표 채우기. 위 기간 필터 공통.
  async function loadMistakes() {
    const { start_date: start, end_date: end } = currentRange();
    const wbody = $("insight-mistake-worker");
    const mbody = $("insight-mistake-material");
    try {
      const d = await request("/blend/mistake-stats", { query: { start_date: start, end_date: end } });
      const workers = (d && d.by_worker) || [];
      const materials = (d && d.by_material) || [];
      wbody.innerHTML = workers.length
        ? workers.map((w) => `<tr><td>${esc(w.worker || "")}</td><td class="num">${w.records}</td><td class="num">${w.manual_records}</td><td class="num">${w.manual_rate}%</td><td class="num">${w.canceled_records}</td></tr>`).join("")
        : '<tr><td colspan="5" class="muted">해당 기간 기록 없음</td></tr>';
      mbody.innerHTML = materials.length
        ? materials.map((m) => `<tr><td>${esc(m.material_name || "")}</td><td class="num">${m.rows}</td><td class="num">${m.manual_rows}</td><td class="num">${m.manual_rate}%</td></tr>`).join("")
        : '<tr><td colspan="4" class="muted">수동 입력 없음 — 모두 저울로 계량됨</td></tr>';
    } catch (e) {
      wbody.innerHTML = `<tr><td colspan="5" class="muted">조회 실패: ${esc(e.message)}</td></tr>`;
      mbody.innerHTML = "";
    }
  }

  function loadAll() {
    loadMaterials();
    loadProducts().then(loadDetails);
    loadMistakes();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    $("insight-range-month").addEventListener("click", () => {
      $("insight-from").value = isoDaysAgo(30);
      $("insight-to").value = "";
      loadAll();
    });
    $("insight-range-90").addEventListener("click", () => {
      $("insight-from").value = isoDaysAgo(90);
      $("insight-to").value = "";
      loadAll();
    });
    $("insight-range-all").addEventListener("click", () => {
      $("insight-from").value = "";
      $("insight-to").value = "";
      loadAll();
    });
    $("insight-query").addEventListener("click", loadAll);
    $("insight-product").addEventListener("change", loadDetails);
    $("insight-detail-export").addEventListener("click", exportDetails);
    $("insight-trace-btn").addEventListener("click", traceMaterialLot);
    $("insight-trace-lot").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) traceMaterialLot();
    });
    $("insight-from").value = isoDaysAgo(30);
    loadAll();
  });
})();

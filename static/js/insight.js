/**
 * insight.js — 배합 분석 (/insight).
 * 완료된 배합 기록(blend_records/blend_details)에서
 *  · 상단 지표(배합 건수·총 사용 중량·자재 종수·제품 종수)
 *  · 이상 통계(수동 입력·취소) · 자재 LOT 추적 · 제품별 배합 빈도(배치 수) 차트
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

  // 로컬 날짜 기준 — toISOString() 은 UTC 라 KST 오전 9시 이전에는 '오늘'이 하루 전으로
  // 밀린다. 배합 기록의 work_date 는 로컬 날짜로 저장되므로(blend_lib.todayISO), 그대로
  // 두면 야간조가 새벽에 조회할 때 그날 배합이 0건으로 보인다.
  function _localISO(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function isoDaysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return _localISO(d);
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

  // 자재별 사용량 표는 상위 재고 대시보드와 중복이라 제거됨. 다만 상단 지표 카드
  // '총 사용 중량'·'자재 종수'는 이 API 응답으로 채우므로 호출·카드 갱신은 유지한다.
  async function loadMaterials() {
    const { start_date: start, end_date: end } = currentRange();
    try {
      const d = await request("/blend/material-usage", { query: { start_date: start, end_date: end } });
      $("metric-records").innerHTML = `${fmt(d.record_count, 0)}<span class="metric-unit">건</span>`;
      $("metric-weight").innerHTML = `${fmt(d.total_weight)}<span class="metric-unit">g</span>`;
      $("metric-materials").innerHTML = `${fmt(d.material_count, 0)}<span class="metric-unit">종</span>`;
      $("insight-filter-summary").textContent =
        `${start || "전체"} ~ ${end || "전체"} · 배합 ${fmt(d.record_count, 0)}건 · 자재 ${fmt(d.material_count, 0)}종`;
    } catch (e) {
      IRMS.notify && IRMS.notify(`자재 지표 불러오기 실패: ${e.message || e}`, "error");
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

  async function loadProducts() {
    const { start_date: start, end_date: end } = currentRange();
    try {
      const d = await request("/blend/product-usage", { query: { start_date: start, end_date: end } });
      const items = d.items || [];
      $("metric-products").innerHTML = `${fmt(d.product_count, 0)}<span class="metric-unit">종</span>`;
      renderProductChart(items);
    } catch (e) {
      IRMS.notify && IRMS.notify(`제품별 분석 불러오기 실패: ${e.message || e}`, "error");
    }
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
    loadProducts();
    loadMistakes();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    // 프리셋 버튼은 눌러도 모양이 같아서 지금 어느 기간을 보고 있는지 버튼만 봐선
    // 알 수 없었다. 누른 것만 표시하고, 날짜를 직접 고치면 더는 그 프리셋이 아니므로 해제.
    const RANGE_BTNS = ["insight-range-month", "insight-range-90", "insight-range-all"];
    function markRange(activeId) {
      RANGE_BTNS.forEach((id) => $(id).classList.toggle("active", id === activeId));
    }
    function applyRange(activeId, fromValue) {
      markRange(activeId);
      $("insight-from").value = fromValue;
      $("insight-to").value = "";
      loadAll();
    }
    $("insight-range-month").addEventListener("click", () => applyRange("insight-range-month", isoDaysAgo(30)));
    $("insight-range-90").addEventListener("click", () => applyRange("insight-range-90", isoDaysAgo(90)));
    $("insight-range-all").addEventListener("click", () => applyRange("insight-range-all", ""));
    $("insight-from").addEventListener("change", () => markRange(null));
    $("insight-to").addEventListener("change", () => markRange(null));
    $("insight-query").addEventListener("click", loadAll);
    $("insight-trace-btn").addEventListener("click", traceMaterialLot);
    $("insight-trace-lot").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) traceMaterialLot();
    });
    $("insight-from").value = isoDaysAgo(30);
    loadAll();
  });
})();

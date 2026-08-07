/**
 * dashboard.js — 운영 대시보드 컨트롤러.
 *
 * 배합 실적(blend_records) + 점도 현황 기반. 구 계량 지표(완료 레시피·계량
 * 단계·처리량·계량 편차)는 데이터가 더 이상 쌓이지 않아 2026-07 재구축.
 *
 * 이 화면이 답하는 것은 '지금 해야 할 일'이다 — 오늘·최근 며칠의 배합, 점도 미입력,
 * 확인 대기 중인 증량·수기 입력, 최근 기록. 기간을 잡고 경향·자재 소비·품질 추세를
 * 보는 일은 배합 분석(/insight)이 맡는다. 반제품 TOP·작업자별 실적이 양쪽에 다 있고
 * 세는 기준마저 달랐던 것을 2026-08-08 에 정리했다.
 */
document.addEventListener("DOMContentLoaded", () => {
  const presetBtns = document.querySelectorAll(".preset-btn");
  const fromInput = document.getElementById("dash-from");
  const toInput = document.getElementById("dash-to");

  let trendChart = null;

  const PREF_KEY = "irms_dashboard_range";

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  // 로컬 날짜 기준 — toISOString() 은 UTC 라 KST 오전 9시 이전에는 '오늘'이 하루 전으로
  // 밀린다. 배합 기록의 work_date 는 로컬 날짜로 저장되므로(blend_lib.todayISO), 그대로
  // 두면 야간조가 새벽에 조회할 때 그날 배합이 0건으로 보인다.
  function _localISO(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function todayISO() {
    return _localISO(new Date());
  }

  function addDaysISO(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return _localISO(d);
  }

  function setPresetActive(preset) {
    presetBtns.forEach((button) => {
      button.classList.toggle("active", button.dataset.preset === preset);
    });
  }

  function computeRangeFromPreset(preset) {
    if (preset === "today") return { from: todayISO(), to: todayISO() };
    if (preset === "30d") return { from: addDaysISO(-29), to: todayISO() };
    return { from: addDaysISO(-6), to: todayISO() };
  }

  function getCurrentRange() {
    const from = fromInput.value;
    const to = toInput.value;
    if (from && to) return { from, to };
    return computeRangeFromPreset("7d");
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function qs(range) {
    return `from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`;
  }

  function fmtNumber(value, digits = 0) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    });
  }

  async function loadAll() {
    const range = getCurrentRange();
    const main = document.querySelector("main.page-grid") || document.body;
    IRMS.showLoading(main);
    try {
      // 반제품 TOP·작업자별 실적은 배합 분석(/insight)으로 옮겼다(2026-08-08).
      const [summary, trend, recent] = await Promise.all([
        fetchJSON(`/api/dashboard/summary?${qs(range)}`),
        fetchJSON(`/api/dashboard/trend?${qs(range)}`),
        fetchJSON("/api/dashboard/recent?limit=10"),
      ]);
      renderSummary(summary);
      renderTrend(trend);
      renderRecent(recent);
    } catch (error) {
      IRMS.notify(`대시보드 불러오기 실패: ${error.message}`, "error");
    } finally {
      IRMS.hideLoading(main);
    }
  }

  function renderSummary(data) {
    // 값+단위를 한 줄로(insight 카드와 동일한 인라인 span 형태). textContent 는 span 을
    // 지우므로 innerHTML 로 단위 span 을 함께 넣는다.
    document.getElementById("card-blend-count").innerHTML =
      `${fmtNumber(data.blend_count)}<span class="metric-unit">건</span>`;
    // 합계는 kg — 7일치도 g 로 두면 여섯 자리가 되고, 배합 분석도 합계는 kg 로 쓴다.
    // (개별 배치 총량은 두 화면 모두 g 그대로 — 현장이 g 로 다루는 값이다.)
    document.getElementById("card-weight").innerHTML =
      `${fmtNumber((data.total_weight_g || 0) / 1000, 1)}<span class="metric-unit">kg</span>`;
    document.getElementById("card-products").innerHTML =
      `${fmtNumber(data.product_count)}<span class="metric-unit">종</span>`;
    // '결재 대기' 카드·API 필드(approval_pending)는 제거됨(결재 현장 미사용, 2026-07-23).
    const anomaly = document.getElementById("card-visc-anomaly");
    anomaly.textContent = fmtNumber(data.viscosity_anomaly);
    anomaly.style.color = data.viscosity_anomaly > 0 ? cssVar("--status-error", "#d8453f") : "";
    const due = data.viscosity_due_today || [];
    const dueEl = document.getElementById("card-visc-due");
    dueEl.textContent = fmtNumber(due.length);
    dueEl.style.color = due.length > 0 ? cssVar("--status-error", "#d8453f") : "";
    document.getElementById("card-visc-due-codes").textContent = due.length
      ? due.join(", ")
      : "알림 대상 모두 입력됨";
  }

  function renderTrend(data) {
    const labels = data.points.map((point) => point.date.slice(5));
    const counts = data.points.map((point) => point.blend_count);
    // 카드가 kg 로 말하는데 그래프 축만 g 면 같은 화면에서 단위가 갈린다.
    const weights = data.points.map((point) => (point.total_weight_g || 0) / 1000);
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(document.getElementById("chart-trend"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "배합 건수",
            data: counts,
            borderColor: cssVar("--brand", "#1b4079"),
            backgroundColor: "rgba(27, 64, 121, 0.15)",
            yAxisID: "y",
            tension: 0.25,
          },
          {
            label: "총 배합량 (kg)",
            data: weights,
            // 주의: 이 앱에서 --accent 는 네이비 별칭 — 오렌지는 --accent-secondary
            borderColor: cssVar("--accent-secondary", "#f47c26"),
            backgroundColor: "rgba(244, 124, 38, 0.15)",
            yAxisID: "y1",
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { type: "linear", position: "left", beginAtZero: true },
          y1: {
            type: "linear",
            position: "right",
            beginAtZero: true,
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  function renderRecent(data) {
    const body = document.getElementById("recent-body");
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5"><div class="empty-state">배합 기록 없음</div></td></tr>';
      return;
    }
    body.innerHTML = items
      .map((r) => {
        // LOT 이 {반제품명}{YYMMDD}{순번} 이라 반제품 열은 중복 — LOT 한 칸으로 통합
        const lot = r.reactor
          ? `${IRMS.escapeHtml(r.product_lot)}<span class="muted small dash-lot-reactor">반응기 ${r.reactor}</span>`
          : IRMS.escapeHtml(r.product_lot);
        const workDate = (r.work_date || "-").length === 10 ? r.work_date.slice(5) : (r.work_date || "-");
        return `
          <tr>
            <td>${lot}</td>
            <td>${IRMS.escapeHtml(workDate)}</td>
            <td>${IRMS.escapeHtml(r.worker || "-")}</td>
            <td class="num">${fmtNumber(r.total_amount, 1)}</td>
            <td>${r.has_viscosity ? "입력" : '<span class="muted">미입력</span>'}</td>
          </tr>
        `;
      })
      .join("");
  }

  // ── 미확인 증량 카드(책임자 전용) ─────────────────────────────
  // 카드 요소는 책임자에게만 렌더링(can_manage) — 비책임자는 아예 없어 401 호출을 피한다.
  async function loadRescaleAlert() {
    const valueEl = document.getElementById("card-rescale-unacked");
    const listEl = document.getElementById("rescale-alert-list");
    if (!valueEl || !listEl) return;
    try {
      // 증량 부재 + 수기 입력 부재를 한 카드에서 함께 보여준다(둘 다 '책임자 사후 확인'
      // 대상이라 목록을 나누면 놓치기 쉽다). 항목마다 kind 로 확인 endpoint 를 가른다.
      const [rescaleData, manualData] = await Promise.all([
        fetchJSON("/api/blend/rescales/unacked"),
        fetchJSON("/api/blend/manual-absences/unacked"),
      ]);
      const items = [
        ...(rescaleData.items || []).map((r) => ({ ...r, kind: "rescale" })),
        ...(manualData.items || []).map((r) => ({ ...r, kind: "manual" })),
      ].sort((a, b) => String(b.work_date || "").localeCompare(String(a.work_date || "")));
      const total = items.length;
      valueEl.textContent = fmtNumber(total);
      valueEl.style.color =
        total > 0 ? cssVar("--status-error", "#d8453f") : cssVar("--text-muted", "#94a3b8");
      const card = document.getElementById("rescale-alert-card");
      if (card) card.classList.toggle("has-unacked", total > 0);
      if (!items.length) {
        listEl.innerHTML = '<li class="rescale-empty muted">미확인 항목이 없습니다.</li>';
        return;
      }
      listEl.innerHTML = items
        .slice(0, 6)
        .map((r) => {
          const date =
            (r.work_date || "-").length === 10 ? r.work_date.slice(5) : r.work_date || "-";
          const isManual = r.kind === "manual";
          const tag = isManual ? "수기 입력" : "증량";
          const reason = isManual && r.manual_absence_reason
            ? ` <span class="muted">· ${IRMS.escapeHtml(r.manual_absence_reason)}</span>`
            : "";
          return `
            <li class="rescale-item">
              <span class="rescale-item-info">
                <span class="rescale-kind${isManual ? " is-manual" : ""}">${tag}</span>
                <a class="rescale-item-link" href="/status?search=${encodeURIComponent(r.product_lot || "")}"
                   title="배합 기록에서 이 LOT 열어보기"><b>${IRMS.escapeHtml(r.product_lot || "-")}</b></a>
                <span class="muted">·</span> ${IRMS.escapeHtml(r.product_name || "-")}
                <span class="muted">·</span> ${IRMS.escapeHtml(r.worker || "-")}
                <span class="muted">·</span> ${IRMS.escapeHtml(date)}${reason}
              </span>
              <a class="btn btn-sm rescale-open-link"
                 href="/status?search=${encodeURIComponent(r.product_lot || "")}">기록 열기</a>
            </li>`;
        })
        .join("");
    } catch (error) {
      listEl.innerHTML = `<li class="rescale-empty muted">불러오기 실패: ${IRMS.escapeHtml(
        error.message || String(error),
      )}</li>`;
    }
  }

  // [확인] 버튼은 2026-08-05 제거 — 내용을 안 보고 미확인 증량·수기 입력을 해제하는
  // 단방향 경로였다(status.js 의 "확인 처리는 내용을 보는 그 자리에서" 정책과 충돌,
  // 사용자 결정). 카드는 알림판 역할만 하고, 확인 처리는 [기록 열기] → 기록 상세에서.

  function persistRange(range) {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(range));
    } catch {}
  }

  function restoreRange() {
    try {
      const saved = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (saved && saved.from && saved.to) {
        fromInput.value = saved.from;
        toInput.value = saved.to;
        setPresetActive(null);
        return;
      }
    } catch {}
    const range = computeRangeFromPreset("7d");
    fromInput.value = range.from;
    toInput.value = range.to;
  }

  presetBtns.forEach((button) => {
    button.addEventListener("click", () => {
      const preset = button.dataset.preset;
      setPresetActive(preset);
      const range = computeRangeFromPreset(preset);
      fromInput.value = range.from;
      toInput.value = range.to;
      persistRange(range);
      loadAll();
    });
  });

  document.getElementById("dash-apply").addEventListener("click", () => {
    if (!fromInput.value || !toInput.value) {
      IRMS.notify("시작일과 종료일을 모두 입력하세요.", "warn");
      return;
    }
    setPresetActive(null);
    persistRange({ from: fromInput.value, to: toInput.value });
    loadAll();
  });
  document.getElementById("dash-refresh").addEventListener("click", loadAll);
  document.getElementById("dash-export").addEventListener("click", () => {
    window.location.assign(`/api/dashboard/export?${qs(getCurrentRange())}`);
  });

  restoreRange();
  loadAll();
  loadRescaleAlert();
});

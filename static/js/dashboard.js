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
 *
 * 현장 PC 에 상시 띄워 두고 3분마다 자동 갱신한다. 이 화면은 '늘 떠 있는' 화면이라
 * 일부 섹션이 실패해도 나머지는 갱신하고( Promise.allSettled ), 실패한 섹션만 조용히
 * '갱신 실툐' 로 표시한다 — 한 번 실패했다고 전체가 멈추면 옛 숫자를 현재로 읽는다.
 */

// ── ERP 파일 경과: 주말 오탐 보정(순수 함수) ──────────────────────────────
// 금요일에 올린 ERP 파일을 월요일에 보면 달력 경과는 3일이라 곧바로 경고 톤이 떴다.
// 주말(토·일)은 작업이 없으므로, 파일 날짜 다음날~오늘 사이의 주말 일수를 빼서
// '영업일 기준 경과' 로 판정한다. ISO(YYYY-MM-DD) 만 받는 순수 함수 — 테스트 가능.
function _weekendDaysBetween(fileDateISO, todayISO) {
  const start = new Date(fileDateISO + "T00:00:00");
  const end = new Date(todayISO + "T00:00:00");
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return 0;
  let weekends = 0;
  const cur = new Date(start);
  cur.setDate(cur.getDate() + 1); // 파일 당일은 0일 — 다음날부터 센다.
  while (cur <= end) {
    const dow = cur.getDay();
    if (dow === 0 || dow === 6) weekends += 1;
    cur.setDate(cur.getDate() + 1);
  }
  return weekends;
}

// stale_days(순 달력 일수)에서 주말을 빼 영업일 기준 경과로. 파싱 불가면 null.
function effectiveStaleDays(staleDays, fileDateISO, todayISO) {
  if (staleDays === null || staleDays === undefined || Number.isNaN(Number(staleDays))) return null;
  if (!fileDateISO || !todayISO) return Number(staleDays);
  return Number(staleDays) - _weekendDaysBetween(fileDateISO, todayISO);
}

// 검증/콘솔에서 부를 수 있게 노출.
window.IRMS = window.IRMS || {};
window.IRMS.effectiveStaleDays = effectiveStaleDays;

document.addEventListener("DOMContentLoaded", () => {
  const presetBtns = document.querySelectorAll(".preset-btn");
  const fromInput = document.getElementById("dash-from");
  const toInput = document.getElementById("dash-to");

  let trendChart = null;
  // 섹션별 마지막 성공 값 — 한 섹션이 실패해도 그 카드는 마지막 값을 유지한다.
  const cache = { summary: null, trend: null, recent: null, attention: null };

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

  // 저장된 기간이 오늘/7일/30일 프리셋과 정확히 일치하면 그 프리셋을 가린다.
  function matchPreset(range) {
    for (const preset of ["today", "7d", "30d"]) {
      const r = computeRangeFromPreset(preset);
      if (r.from === range.from && r.to === range.to) return preset;
    }
    return null;
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

  // 섹션의 '갱신 실툐' 표시를 켜고 끈다. 실패해도 값 DOM 은 건드리지 않는다(마지막 값 유지).
  function setSectionFail(id, failed) {
    const el = document.getElementById(id);
    if (el) el.hidden = !failed;
  }

  // '오늘 배합' 카드 링크는 /status?from=오늘&to=오늘 — 자정이 지나면 갱신.
  function updateTodayLink() {
    const link = document.getElementById("act-today");
    if (link) link.href = `/status?from=${todayISO()}&to=${todayISO()}`;
  }

  // opts.silent 가 참이면 전면 로딩을 띄우지 않는다 — 자동 갱신은 조용히.
  async function loadAll(opts) {
    const silent = !!(opts && opts.silent);
    const range = getCurrentRange();
    const main = document.querySelector("main.page-grid") || document.body;
    if (!silent) IRMS.showLoading(main);
    updateTodayLink();
    try {
      // 반제품 TOP·작업자별 실적은 배합 분석(/insight)으로 옮겼다(2026-08-08).
      const results = await Promise.allSettled([
        fetchJSON(`/api/dashboard/summary?${qs(range)}`),
        fetchJSON(`/api/dashboard/trend?${qs(range)}`),
        fetchJSON("/api/dashboard/recent?limit=10"),
        fetchJSON("/api/dashboard/attention"),
      ]);
      const [summaryR, trendR, recentR, attentionR] = results;
      const ok = (r) => r.status === "fulfilled";

      // summary — 기간 실적 3카드
      if (ok(summaryR)) {
        cache.summary = summaryR.value;
        renderSummary(cache.summary);
        setSectionFail("dash-period-fail", false);
      } else {
        setSectionFail("dash-period-fail", true); // 값 DOM 은 그대로(마지막 값 유지)
      }

      // trend — 일별 추이 차트
      if (ok(trendR)) {
        cache.trend = trendR.value;
        renderTrend(cache.trend);
        setSectionFail("dash-trend-fail", false);
      } else {
        setSectionFail("dash-trend-fail", true);
      }

      // recent — 최근 배합 기록 표
      if (ok(recentR)) {
        cache.recent = recentR.value;
        renderRecent(cache.recent);
        setSectionFail("dash-recent-fail", false);
      } else {
        setSectionFail("dash-recent-fail", true);
      }

      // 점도 이상은 /summary 가, 나머지는 /attention 이 준다 — 한 렌더러가 합쳐 그린다.
      // /summary 가 실패했으면 점도 이상 값은 알 수 없으므로 보존(덮어쓰지 않는다).
      const anomalySource = ok(summaryR) ? summaryR.value : cache.summary;
      if (ok(attentionR)) {
        cache.attention = attentionR.value;
        renderAttention({
          ...cache.attention,
          viscosity_anomaly: anomalySource ? anomalySource.viscosity_anomaly : undefined,
        });
        setSectionFail("dash-attention-fail", false);
      } else {
        setSectionFail("dash-attention-fail", true);
      }

      const anyFail = results.some((r) => r.status === "rejected");
      stampUpdated(anyFail);
      if (anyFail) {
        const allFail = results.every((r) => r.status === "rejected");
        IRMS.notify(
          allFail ? "대시보드를 갱신하지 못했습니다." : "일부 대시보드 항목을 갱신하지 못했습니다.",
          "warn",
        );
      }
    } finally {
      if (!silent) IRMS.hideLoading(main);
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
    // 점도 이상·미입력은 기간과 무관한 값이라 '지금 조치' 묶음(renderAttention)이 그린다.
  }

  // ── 지금 조치 ────────────────────────────────────────────────
  // 기간 필터를 30일로 바꿔도 여기 숫자는 변하지 않는다 — 그래서 기간 카드와 줄을 나눴다.
  function markAct(id, on) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("needs-action", !!on);
  }

  function renderAttention(data) {
    const anomaly = data.viscosity_anomaly;
    const anomalyEl = document.getElementById("card-visc-anomaly");
    // /summary 실패 시 viscosity_anomaly 가 undefined — 그러면 덮어쓰지 않고 마지막 값 유지.
    if (anomaly !== undefined && anomalyEl) {
      anomalyEl.textContent = fmtNumber(anomaly);
      markAct("act-visc-anomaly", Number(anomaly) > 0);
    }

    const due = data.viscosity_due_today || [];
    const dueEl = document.getElementById("card-visc-due");
    if (dueEl) dueEl.textContent = fmtNumber(due.length);
    // 대상 코드는 3개까지 + '외 N건' — 길어지면 카드 높이가 들쭉날쭉해진다.
    const codesEl = document.getElementById("card-visc-due-codes");
    if (codesEl) {
      if (!due.length) {
        codesEl.textContent = "알림 대상 모두 입력됨";
      } else {
        const shown = due.slice(0, 3);
        const extra = due.length - shown.length;
        codesEl.textContent = extra > 0 ? `${shown.join(", ")} 외 ${extra}건` : shown.join(", ");
      }
    }
    markAct("act-visc-due", due.length > 0);

    // 자재 LOT 기준 파일 — 며칠 지났는지. 파일이 없으면 LOT 검증 자체가 못 돈다.
    const file = data.material_lot_file || {};
    const staleEl = document.getElementById("card-lot-stale");
    const fileEl = document.getElementById("card-lot-file");
    if (!file.found) {
      if (staleEl) staleEl.textContent = "없음";
      if (fileEl) fileEl.textContent = "ERP 파일을 찾지 못했습니다";
      markAct("act-lot-file", true);
    } else {
      const days = file.stale_days;
      if (staleEl) staleEl.textContent = days === null || days === undefined ? "-" : `${days}일 전`;
      if (fileEl) fileEl.textContent = file.file_name || "-";
      // 3영업일 넘게 지난 파일로 원료 LOT 을 판정하고 있으면 알린다.
      // (주말 보정 — 금요일 파일이 월요일에 달력 3일로 경고 뜨는 오탐 방지)
      const effStale = effectiveStaleDays(days, file.file_date, todayISO());
      markAct("act-lot-file", Number(effStale) >= 3);
    }

    const todayEl = document.getElementById("card-today-count");
    if (todayEl) todayEl.textContent = `${fmtNumber(data.today_blend_count)}건`;
    const lastEl = document.getElementById("card-last-blend");
    if (lastEl) {
      lastEl.textContent = data.last_blend_at
        ? `마지막 ${data.last_blend_at}`
        : "기록 없음";
    }
  }

  // partialFail: 어떤 요청이든 실패가 섞이면 '일부 갱신 실툐' 경고 톤(stale) 으로.
  function stampUpdated(partialFail) {
    const el = document.getElementById("dash-updated");
    if (!el) return;
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    const hhmm = `${p(d.getHours())}:${p(d.getMinutes())}`;
    if (partialFail) {
      el.textContent = `일부 갱신 실패 · ${hhmm} 기준`;
      el.classList.add("stale");
    } else {
      el.textContent = `${hhmm} 기준`;
      el.classList.remove("stale");
    }
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
    const card = document.getElementById("rescale-alert-card");
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
      // 세션 만료 등으로 불러오지 못하면 숫자·강조도 함께 리셋 — 빨간 '3건' 이 고정되어
      // 실제론 로그인이 풀렸는데 확인할 게 남은 것처럼 보이는 모순을 막는다.
      valueEl.textContent = "-";
      valueEl.style.color = cssVar("--text-muted", "#94a3b8");
      if (card) card.classList.remove("has-unacked");
      listEl.innerHTML = '<li class="rescale-empty muted">불러오기 실패(로그인 확인)</li>';
    }
  }

  // [확인] 버튼은 2026-08-05 제거 — 내용을 안 보고 미확인 증량·수기 입력을 해제하는
  // 단방향 경로였다(status.js 의 "확인 처리는 내용을 보는 그 자리에서" 정책과 충돌,
  // 사용자 결정). 카드는 알림판 역할만 하고, 확인 처리는 [기록 열기] → 기록 상세에서.

  function persistRange(range) {
    IRMS.savePreference(PREF_KEY, JSON.stringify(range));
  }

  function restoreRange() {
    const saved = IRMS.loadPreference(PREF_KEY, null);
    if (saved) {
      try {
        const range = JSON.parse(saved);
        if (range && range.from && range.to) {
          fromInput.value = range.from;
          toInput.value = range.to;
          setPresetActive(matchPreset(range));
          return;
        }
      } catch {}
    }
    const range = computeRangeFromPreset("7d");
    fromInput.value = range.from;
    toInput.value = range.to;
    setPresetActive("7d");
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

  // 자동 갱신 — 이 화면은 현장 PC 에 띄워 둔다. 수동 새로고침만 있으면 아무도 누르지
  // 않은 채 몇 시간 전 숫자를 현재로 읽는다. 탭이 숨어 있을 때는 부르지 않고(백그라운드
  // 폴링 낭비), 다시 보이면 즉시 한 번 갱신한다. 선택은 이 PC 에 기억한다.
  // 자동 갱신은 전면 로딩 없이 조용히 — 로딩은 [새로고침]/[적용]을 눌렀을 때만.
  const AUTO_MS = 180000;
  const AUTO_KEY = "irms_dashboard_auto";
  const autoChk = document.getElementById("dash-auto");
  let autoTimer = null;

  function refreshAll() {
    loadAll({ silent: true });
    loadRescaleAlert();
  }

  function stopAuto() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }

  function startAuto() {
    stopAuto();
    autoTimer = setInterval(() => {
      if (document.hidden) return;
      refreshAll();
    }, AUTO_MS);
  }

  if (autoChk) {
    const saved = IRMS.loadPreference ? IRMS.loadPreference(AUTO_KEY, "1") : "1";
    autoChk.checked = saved !== "0";
    autoChk.addEventListener("change", () => {
      if (IRMS.savePreference) IRMS.savePreference(AUTO_KEY, autoChk.checked ? "1" : "0");
      if (autoChk.checked) startAuto(); else stopAuto();
    });
    if (autoChk.checked) startAuto();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && autoChk.checked) refreshAll();
    });
  }

  restoreRange();
  updateTodayLink();
  loadAll();
  loadRescaleAlert();
});

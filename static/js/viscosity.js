(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (message) { console.log(message); };

  // 순수 헬퍼 라이브러리 — 라이브러리에서 분리된 포맷터/라벨맵/DOM 문자열 빌더.
  // 동일한 이름으로 분해 할당하므로 기존 호출부는 그대로 동작한다.
  const {
    STATUS_LABEL,
    REASON_LABEL,
    TREND_LABEL,
    PERIOD_ALERT_LABEL,
    fmt,
    productLabel,
    appendTextCell,
    emptyRow,
    appendDeltaCell,
    option,
    controlSummary,
    controlBandHtml,
    periodChartDatasets,
    periodChartYBounds,
    readingOverlayDatasets,
    sourcePbLinkedReadings,
    sourcePbScatterDatasets,
    pbLinkNotice,
    pbLinearFit,
    pbScatterSummary,
  } = window.IRMS.viscLib;

  const $ = (id) => document.getElementById(id);
  const isManager = Boolean($("visc-settings-btn"));
  // 배합 기록 표의 열 수 — 책임자는 뒤에 '관리' 열이 하나 더 붙는다(빈 행 colspan 계산용).
  const BLEND_COLS = isManager ? 7 : 6;

  // ── 모달 접근성 헬퍼 ─────────────────────────────────────────────
  // role/aria-modal 은 템플릿, 여기서는 열 때 포커스 이동·닫을 때 복원 + Esc + 배경
  // 클릭 닫기를 건다. 표준 닫기 경로 = 닫기 버튼 · 배경 클릭 · Esc.
  function createModal(overlayId, opts) {
    opts = opts || {};
    const overlay = $(overlayId);
    if (!overlay) return { open() {}, close() {} };
    let opener = null;
    function open(focusEl) {
      if (overlay.hidden) opener = document.activeElement;
      overlay.hidden = false;
      const target = focusEl
        || (opts.initialFocus && $(opts.initialFocus))
        || overlay.querySelector(".ss-modal");
      if (target && target.focus) setTimeout(() => { try { target.focus(); } catch (_e) { /* noop */ } }, 0);
    }
    function close() {
      if (overlay.hidden) return;
      overlay.hidden = true;
      if (opts.onClose) opts.onClose();
      if (opener && opener.focus) { try { opener.focus(); } catch (_e) { /* noop */ } }
      opener = null;
    }
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    });
    return { open, close };
  }
  let settingsModal = null;
  let excludeModal = null;

  // 기간별 표·차트에 한 번에 그릴 최대 구간 수. '일' 단위 + 연도=전체에서 버킷이
  // 무한정 늘어나(수백~수천 행) 표·차트가 무거워지는 것을 막는다. 전체는 Excel 내보내기로.
  const PERIOD_DISPLAY_CAP = 60;
  // 기간 표는 상위 12행만 먼저 보여준다(차트는 60구간 그대로). 60행이 한 번에
  // 펼쳐져 있으면 화면이 길어지기만 하고 정작 최근 구간을 보려고 스크롤하게 된다.
  const PERIOD_TABLE_ROWS = 12;
  // PB 연계 표도 같은 이유로 20행씩. 76행 전건 나열이 화면 절반을 먹었다.
  const PB_TABLE_ROWS = 20;
  // 배합 기록 목록 [더보기] 단계 — 서버 limit 을 올려 다시 받는다(최대 200).
  const BLEND_LIMIT_STEPS = [20, 50, 100, 200];

  const state = {
    products: [],
    currentId: null,
    analysis: null,
    blendRecords: [],
    blendTotal: 0,
    blendUnregisteredTotal: 0,
    blendLimit: BLEND_LIMIT_STEPS[0],
    blendReturned: 0,
    selectedBlendId: null,
    selectedBlendDetail: null,
    usedPb: null,          // {lot, method, pb_viscosity} — 선택 기록의 '사용한 PB'
    periodChart: null,
    pbChart: null,
    periodRows: PERIOD_TABLE_ROWS,
    pbRows: PB_TABLE_ROWS,
    tab: "register",
    granularity: "day",
    year: null,
    reactor: null,
  };

  // ── 탭 ───────────────────────────────────────────────────────────────
  // 표시 전환만 한다 — 데이터는 반제품 하나를 고를 때 이미 전부 받아 두었다.
  // 규약은 레시피 관리·자재 관리와 동일(.mgmt-tab[data-tab] ↔ .tab-panel#tab-{name}).
  function activateTab(name) {
    state.tab = name;
    document.querySelectorAll(".visc-tabs .mgmt-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${name}`);
    });
    // 숨어 있던 탭의 캔버스는 폭 0 으로 그려져 있다. resize() 만으로는 축과 점 위치가
    // 어긋난 채 남는다(PB 산점도에서 점 아홉이 왼쪽 끝에 뭉쳐 그려졌다) — 보이는 순간
    // 그 탭의 그림을 다시 그린다. 다시 그리는 재료는 이미 받아 둔 analysis 뿐이라
    // 서버를 부르지 않는다.
    if (!state.analysis) return;
    if (name === "trend") renderPeriods();
    if (name === "pb") renderSourcePb();
  }

  function selectedProduct() {
    const sel = $("visc-product-select");
    if (!sel || !sel.value) return null;
    return state.products.find((product) => String(product.id) === sel.value) || null;
  }

  function currentProduct() {
    return state.analysis ? state.analysis.product : null;
  }

  async function loadOverview() {
    const data = await request("/viscosity/overview");
    state.products = data.items || [];
    // 화면에 보이는 선택과 아래 데이터가 항상 일치하도록, 로드는 select 의 실제 값에서
    // 파생한다. 첫 진입이면 native select 가 첫 옵션을 자동 선택하므로 그것을 그대로
    // 따르고, 새로고침/설정 저장 후엔 renderProductSelect 가 유지한 currentId 를 따른다.
    renderProductSelect();
    const sel = $("visc-product-select");
    const chosen =
      sel && sel.value
        ? state.products.find((item) => String(item.id) === sel.value)
        : null;
    if (chosen) {
      await selectProduct(chosen);
    } else {
      showEmptyState();
    }
  }

  // 선택된 반제품 하나로 화면 전체(카드·추세·기간·배합 기록)를 로드한다. 초기 로드와
  // 사용자의 select 변경이 같은 경로를 타서 '선택 ≠ 표시' 불일치를 원천 차단한다.
  function selectProduct(product, options) {
    if (!product) {
      showEmptyState();
      return Promise.resolve();
    }
    state.currentId = product.id;
    state.year = product.year;
    if (options && options.resetReactor) state.reactor = null;
    // 반제품 전환이야말로 가장 많이 쓰는 경로인데 예전엔 여기만 loadProduct 를 직접
    // 불러서, 조회가 실패하면 예외가 그대로 콘솔로 빠지고 화면엔 앞 반제품 숫자가
    // 남았다. 연도·반응기 전환과 같은 래퍼를 태운다.
    return reloadProduct(product.id);
  }

  // 등록된 반제품이 하나도 없어 선택이 비어 있을 때의 안내 상태.
  function showEmptyState() {
    state.currentId = null;
    state.analysis = null;
    state.blendRecords = [];
    state.selectedBlendId = null;
    state.selectedBlendDetail = null;
    ["visc-card-count", "visc-card-latest", "visc-card-mean", "visc-card-anomaly", "visc-card-warn", "visc-card-excluded"]
      .forEach((id) => { $(id).textContent = "-"; });
    $("visc-card-latest-date").textContent = "-";
    $("visc-control-summary").textContent = "관리 기준 -";
    $("visc-cond").textContent = "측정 조건 -";
    $("visc-trend-banner").hidden = true;
    $("visc-period-alert").hidden = true;
    // 그릴 데이터가 없으면 차트 자리에 안내를 되살린다(앞 반제품의 그림이 남지 않도록
    // 인스턴스도 함께 버린다 — 남겨두면 빈 상태인데 이전 추세가 그대로 보인다).
    if (state.periodChart) { state.periodChart.destroy(); state.periodChart = null; }
    if (state.pbChart) { state.pbChart.destroy(); state.pbChart = null; }
    const emptyNote = $("visc-chart-empty");
    if (emptyNote) emptyNote.hidden = false;
    const pbEmptyNote = $("visc-pb-chart-empty");
    if (pbEmptyNote) pbEmptyNote.hidden = false;
    const blendBody = $("visc-blend-body");
    blendBody.innerHTML = "";
    blendBody.appendChild(emptyRow(BLEND_COLS, "반제품을 선택하면 배합 기록이 표시됩니다."));
    $("visc-record-count").textContent = "0건";
    $("visc-blend-record").value = "";
    $("visc-selected-row").textContent = "반제품을 선택하세요.";
    $("visc-blend-more").hidden = true;
    clearUsedPb();
    setSubmitEnabled(false);
    const periodBody = $("visc-period-body");
    periodBody.innerHTML = "";
    periodBody.appendChild(emptyRow(9, "표시할 데이터가 없습니다."));
    $("visc-period-more").hidden = true;
    $("visc-anomaly-panel").hidden = true;
    const pbBody = $("visc-source-pb-body");
    if (pbBody) pbBody.innerHTML = "";
    $("visc-pb-more").hidden = true;
    const pbNote = $("visc-source-pb-note");
    if (pbNote) pbNote.textContent = "";
    const pbEmpty = $("visc-pb-empty");
    if (pbEmpty) { pbEmpty.hidden = false; pbEmpty.textContent = "반제품을 선택하면 PB 연계 측정이 표시됩니다."; }
    setAnomalyCardClickable(0);
  }

  function renderProductSelect() {
    // native select — 클릭하면 전체 목록이 즉시 열리고 리셋된다(옛 datalist 는 값을
    // 지워야 목록이 떠서 불편했다). option value=반제품 id, 표시=반제품 라벨.
    const sel = $("visc-product-select");
    if (!sel) return;
    sel.innerHTML = "";
    // 빈 placeholder 를 첫 옵션으로 — 첫 진입에 특정 반제품이 멋대로 선택돼 보이지 않게
    // (사용자 요청 2026-07-22: "-선택-"). 고르기 전까지는 아래가 빈 안내 상태.
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "— 반제품 선택 —";
    sel.appendChild(ph);
    // 분류로 좁히기 — 반제품이 늘어나면 한 목록에서 찾기 어렵다(배합 화면과 같은 방식).
    // 분류가 지정되지 않은 반제품은 '전체'에서만 보인다.
    const cat = ($("visc-cat-select") && $("visc-cat-select").value) || "";
    state.products
      .filter((product) => !cat || product.category === cat)
      .forEach((product) => {
        const opt = document.createElement("option");
        opt.value = String(product.id);
        opt.textContent = productLabel(product);
        sel.appendChild(opt);
      });
    sel.value = state.currentId ? String(state.currentId) : "";
    if (sel._pickerBound) return;
    sel._pickerBound = true;
    sel.addEventListener("change", () => {
      const product = selectedProduct();
      if (!product || product.id === state.currentId) return;
      selectProduct(product, { resetReactor: true }); // 반제품이 바뀌면 반응기 필터 초기화
    });
  }

  // 반제품·연도·반응기·기간 전환은 모두 loadProduct 를 다시 호출한다. 예전에는 이
  // 호출들이 catch 없이 Promise 를 버려서, 조회가 실패하면 아무 메시지도 없이
  // 셀렉트만 새 값이고 카드·추세·기간표는 이전 조건 값이 그대로 남았다 —
  // 품질 책임자가 그 숫자를 새 조건의 결과로 읽게 된다.
  async function reloadProduct(productId) {
    const main = document.querySelector("main.page-grid") || document.body;
    if (IRMS.showLoading) IRMS.showLoading(main);
    try {
      await loadProduct(productId);
    } catch (error) {
      // 실패했음을 알리고, 이전 조건의 숫자가 남아 오해를 부르지 않게 화면을 비운다.
      // 카드만 지우면 추세 배너·기간표·배합 기록에 앞 조건 값이 그대로 남는다.
      showEmptyState();
      // 셀렉트도 되돌린다. 값이 남아 있으면 같은 반제품을 다시 골라도 change 가
      // 안 떠서, 다른 걸 찍었다 돌아오지 않는 한 재시도가 막힌다.
      const sel = $("visc-product-select");
      if (sel) sel.value = "";
      $("visc-selected-row").textContent = "조회에 실패했습니다. 다시 선택해 주세요.";
      IRMS.notify(`점도 조회 실패: ${error.message || error} — 다시 시도해 주세요.`, "error");
    } finally {
      if (IRMS.hideLoading) IRMS.hideLoading(main);
    }
  }

  async function loadProduct(productId) {
    state.analysis = await request(`/viscosity/products/${productId}`, {
      query: { granularity: state.granularity, year: state.year, reactor: state.reactor },
    });
    renderYearSelect();
    renderReactorControls();
    renderCards();
    renderTrendBanner();
    renderPeriodAlerts();
    state.periodRows = PERIOD_TABLE_ROWS;
    state.pbRows = PB_TABLE_ROWS;
    renderPeriods();
    renderAnomalies();
    renderSourcePb();
    renderCondition();
    await loadBlendRecords({ reset: true });
  }

  function renderYearSelect() {
    const select = $("visc-year");
    const years = (state.analysis && state.analysis.available_years) || [];
    select.innerHTML = "";
    years.forEach((year) => {
      const opt = document.createElement("option");
      opt.value = String(year);
      opt.textContent = `${year}년`;
      select.appendChild(opt);
    });
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "전체";
    select.appendChild(all);
    select.value = state.year === null || state.year === undefined ? "" : String(state.year);
  }

  // 반응기 진행 반제품일 때만 툴바 반응기 필터 + 등록 폼 반응기 선택을 노출.
  function renderReactorControls() {
    const product = currentProduct();
    const use = Boolean(product && product.use_reactor);
    const label = $("visc-reactor-label");
    const select = $("visc-reactor");
    if (label) label.hidden = !use;
    if (select) {
      select.hidden = !use;
      if (use) {
        select.innerHTML = "";
        const all = document.createElement("option");
        all.value = "";
        all.textContent = "전체(반응기)";
        select.appendChild(all);
        [1, 2, 3, 4].forEach((n) => {
          const opt = document.createElement("option");
          opt.value = String(n);
          opt.textContent = `반응기 ${n}`;
          select.appendChild(opt);
        });
        // 미지정 — 반응기 도입 전 과거 데이터 전용 뷰(반응기 뷰에는 섞이지 않는다).
        const noneOpt = document.createElement("option");
        noneOpt.value = "none";
        noneOpt.textContent = "미지정(과거)";
        select.appendChild(noneOpt);
        select.value = state.reactor == null ? "" : String(state.reactor);  // "none" 포함
      }
    }
  }

  // 이상·경고 카드는 건수가 0 이 아닐 때만 색을 준다. 늘 빨강/주황이면 훑어볼 때
  // "0" 도 경보로 읽힌다 — 정상인 제품이 문제 있어 보이면 색이 신호 구실을 못 한다.
  function setCountCard(id, wrapperClass, count) {
    $(id).textContent = count == null ? "-" : count;
    const card = $(id).closest(".metric-card");
    if (card) card.classList.toggle(wrapperClass, Number(count) > 0);
  }

  function renderCards() {
    const analysis = state.analysis;
    // 조회 실패 시 analysis 가 비어 들어온다. 던지지 말고 전부 "-" 로 지운다:
    // 이전 조건의 숫자가 남아 있으면 새 선택의 결과로 잘못 읽힌다.
    if (!analysis) {
      ["visc-card-count", "visc-card-latest", "visc-card-latest-date", "visc-card-mean"]
        .forEach((id) => { $(id).textContent = "-"; });
      setCountCard("visc-card-anomaly", "visc-card-anomaly-on", null);
      setCountCard("visc-card-warn", "visc-card-warn-on", null);
      setCountCard("visc-card-excluded", "visc-card-excluded-on", null);
      $("visc-control-summary").textContent = "";
      return;
    }
    const stats = analysis.stats;
    const last = analysis.readings.length ? analysis.readings[analysis.readings.length - 1] : null;
    // 측정 건수 = 통계에 반영된(유효) 건수. 제외 건수는 아래 '통계 제외' 카드로 따로
    // 드러내, 표본에서 몇 건이 빠졌는지 조용히 묻히지 않게 한다.
    const excludedN = (analysis.counts && analysis.counts.excluded) || stats.excluded_n || 0;
    $("visc-card-count").textContent = stats.n;
    $("visc-card-latest").textContent = last ? fmt(last.viscosity) : "-";
    $("visc-card-latest-date").textContent = last && last.measured_date ? last.measured_date : "-";
    $("visc-card-mean").textContent = stats.mean === null ? "-" : `${fmt(stats.center)} ± ${fmt(stats.std)}`;
    setCountCard("visc-card-anomaly", "visc-card-anomaly-on", analysis.counts.anomaly);
    setAnomalyCardClickable(analysis.counts.anomaly);
    setCountCard("visc-card-warn", "visc-card-warn-on", analysis.counts.warn);
    setCountCard("visc-card-excluded", "visc-card-excluded-on", excludedN);
    // 관리 기준 요약 텍스트 아래에 관리 밴드 그림을 함께 넣는다. controlSummary 는
    // 값에서 만든 안전한 문자열이고, controlBandHtml 은 CSS 클래스만 쓰는 순수 빌더라
    // innerHTML 로 합쳐 넣어도 안전하다. 밴드가 없으면(규격·관리한계 모두 없음) 텍스트만.
    const summaryText = controlSummary(analysis);
    const bandHtml = controlBandHtml(analysis, last ? last.viscosity : null);
    $("visc-control-summary").innerHTML = bandHtml
      ? `<span class="visc-control-summary-text">${IRMS.escapeHtml(summaryText)}</span>${bandHtml}`
      : IRMS.escapeHtml(summaryText);
  }

  // '이상 N건' 카드 → 이상 목록. 0 건이면 갈 곳이 없으므로 눌리는 표시조차 하지
  // 않는다(누를 수 있어 보이는데 아무 일도 안 일어나는 게 가장 나쁘다).
  function setAnomalyCardClickable(count) {
    const card = $("visc-anomaly-card");
    const unit = $("visc-card-anomaly-unit");
    if (!card) return;
    const on = Number(count) > 0;
    card.classList.toggle("is-clickable", on);
    if (on) {
      card.setAttribute("role", "button");
      card.setAttribute("tabindex", "0");
      card.setAttribute("aria-label", `이상 ${count}건 — 목록 보기`);
      if (unit) unit.textContent = "건 · 눌러서 목록";
    } else {
      card.removeAttribute("role");
      card.removeAttribute("tabindex");
      card.removeAttribute("aria-label");
      if (unit) unit.textContent = "건";
    }
  }

  function openAnomalyList() {
    if (!state.analysis || !(state.analysis.counts && state.analysis.counts.anomaly > 0)) return;
    activateTab("trend");
    const panel = $("visc-anomaly-panel");
    if (!panel || panel.hidden) return;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    // 어디를 봐야 하는지 잠깐 표시 — 탭이 바뀌면서 화면이 통째로 달라지기 때문.
    panel.classList.add("is-flash");
    setTimeout(() => panel.classList.remove("is-flash"), 1200);
  }

  // 이상 측정 목록 — 서버가 주는 analysis.anomalies(최신 먼저)를 그대로 쓴다.
  function renderAnomalies() {
    const panel = $("visc-anomaly-panel");
    const body = $("visc-anomaly-body");
    const note = $("visc-anomaly-note");
    if (!panel || !body) return;
    const anomalies = (state.analysis && state.analysis.anomalies) || [];
    if (!anomalies.length) {
      panel.hidden = true;
      body.innerHTML = "";
      return;
    }
    panel.hidden = false;
    if (note) note.textContent = `${anomalies.length}건 · 최근 순`;
    body.innerHTML = "";
    anomalies.forEach((item) => {
      const row = document.createElement("tr");
      row.className = "row-anomaly";
      appendTextCell(row, item.measured_date || "-");
      appendTextCell(row, item.lot_no || "-");
      appendTextCell(row, fmt(item.viscosity), "num");
      const reasons = (item.reasons || []).map((r) => REASON_LABEL[r] || r).join(", ");
      appendTextCell(row, reasons || "-");
      body.appendChild(row);
    });
  }

  function renderCondition() {
    const product = currentProduct();
    if (!product) return;
    const rpm = product.rpm != null ? `${fmt(product.rpm, 0)} rpm` : "RPM 미설정";
    const temp = product.temperature != null ? `${fmt(product.temperature)} °C` : "온도 미설정";
    $("visc-cond").textContent = `측정 조건 · ${rpm} · ${temp}`;
  }

  function renderTrendBanner() {
    const trends = state.analysis.trends || [];
    const banner = $("visc-trend-banner");
    if (!trends.length) {
      banner.hidden = true;
      return;
    }
    $("visc-trend-text").textContent = trends
      .map((trend) => `${TREND_LABEL[trend.type] || trend.type} (${trend.length}회 연속)`)
      .join(" · ");
    banner.hidden = false;
  }

  function renderPeriodAlerts() {
    const alerts = (state.analysis && state.analysis.period_alerts) || [];
    const banner = $("visc-period-alert");
    if (!alerts.length) {
      banner.hidden = true;
      return;
    }
    // '일' 단위에선 하루하루 변동이 전부 알림이 되어 수십 개가 줄줄이 이어졌다
    // (사용자: "보지도 못해"). 최신 알림만 보여주고 나머지는 건수로 접는다 —
    // 구간별 상세는 아래 기간별 표의 '전기 대비' 열에서 그대로 확인 가능.
    const label = (item) => (PERIOD_ALERT_LABEL[item.type] || (() => item.type))(item);
    const latest = alerts[alerts.length - 1];
    const rest = alerts.length - 1;
    $("visc-period-alert-text").textContent =
      rest > 0
        ? `${label(latest)} · 외 ${rest}건 (아래 기간별 표 '전기 대비' 참고)`
        : label(latest);
    banner.hidden = false;
  }

  // 사용한 PB 연계 측정 — 바인더처럼 material_lot(사용한PB)에 PB 점도가 매칭되는
  // 측정이 하나라도 있을 때만 표를 띄운다. 각 바인더 점도 옆에 원료 PB 점도를 놓아
  // "이 PB(48cp)로 만든 바인더는 80" 상관을 바로 읽게 한다.
  const STATUS_KO = { normal: "정상", warn: "경고", anomaly: "이상", excluded: "제외" };
  function renderSourcePb() {
    const panel = $("visc-source-pb-panel");
    const body = $("visc-source-pb-body");
    const note = $("visc-source-pb-note");
    const empty = $("visc-pb-empty");
    const more = $("visc-pb-more");
    if (!panel || !body) return;
    // 탭은 언제나 보인다 — 종전에는 매칭이 0 이면 패널을 통째로 숨겨서 '연계가 안 된
    // 것'과 '원래 없는 것'을 구별할 수 없었다(현장 검토 6번).
    panel.hidden = false;
    const readings = (state.analysis && state.analysis.readings) || [];
    const linked = sourcePbLinkedReadings(readings);
    const pbLink = (state.analysis && state.analysis.pb_link) || null;
    const notice = pbLinkNotice(pbLink, linked.length);
    if (!linked.length) {
      body.innerHTML = "";
      body.appendChild(emptyRow(5, "표시할 PB 연계 측정이 없습니다."));
      if (note) note.textContent = "";
      if (empty) { empty.hidden = false; empty.textContent = notice; }
      if (more) more.hidden = true;
      renderPbChart([]);
      return;
    }
    if (empty) empty.hidden = true;
    if (note) note.textContent = notice;
    const shown = linked.slice(0, state.pbRows);
    body.innerHTML = shown
      .map((r) => {
        const st = STATUS_KO[r.status] || "";
        // 정상은 뱃지 없이 흐린 글자로 — 76개의 초록 '정상' 뱃지가 줄줄이 이어지면
        // 정작 봐야 할 '이상' 이 그 사이에 묻힌다(현장 검토 6번).
        const verdict = r.status === "normal" || !st
          ? `<span class="muted">${IRMS.escapeHtml(st || "-")}</span>`
          : `<span class="visc-status ${IRMS.escapeHtml(r.status)}">${IRMS.escapeHtml(st)}</span>`;
        return `<tr${r.status === "anomaly" ? ' class="row-anomaly"' : ""}>`
          + `<td>${IRMS.escapeHtml(r.measured_date || "-")}</td>`
          + `<td>${IRMS.escapeHtml(r.material_lot)}</td>`
          + `<td class="num">${fmt(r.source_pb_viscosity)}</td>`
          + `<td class="num">${fmt(r.viscosity)}</td>`
          + `<td>${verdict}</td>`
          + "</tr>";
      })
      .join("");
    if (more) {
      more.hidden = linked.length <= shown.length;
      more.textContent = `더보기 (${shown.length}/${linked.length}건)`;
    }
    renderPbChart(linked);
  }

  // PB 점도(x) ↔ 이 반제품 점도(y) 산점도. 표만으로는 관계가 안 보인다.
  // 점만 뿌려서는 여전히 한눈에 안 들어온다는 현장 지적(2026-08-13)에 따라
  // 추세선 + 한 줄 결론 + 규격 기준선 + 최근/과거 명암을 함께 그린다.
  function renderPbChart(linked) {
    const canvas = $("visc-pb-chart");
    const emptyNote = $("visc-pb-chart-empty");
    const summaryEl = $("visc-pb-summary");
    if (!canvas) return;
    // 적합은 이상 판정 점을 뺀 표본으로 — 이상 하나가 기울기를 끌고 가면 안 된다.
    const fitPoints = linked
      .filter((r) => r.status !== "anomaly")
      .map((r) => ({ x: Number(r.source_pb_viscosity), y: Number(r.viscosity) }));
    const fit = pbLinearFit(fitPoints);
    if (summaryEl) {
      summaryEl.hidden = !linked.length;
      summaryEl.textContent = linked.length ? pbScatterSummary(fit) : "";
    }
    const product = (state.analysis && state.analysis.product) || {};
    const stats = (state.analysis && state.analysis.stats) || {};
    const datasets = sourcePbScatterDatasets(linked, getCssVar, {
      fit,
      limits: {
        lower: product.lower_limit,
        upper: product.upper_limit,
        center: stats.center,
      },
    });
    if (emptyNote) emptyNote.hidden = datasets.length > 0;
    if (state.pbChart) { state.pbChart.destroy(); state.pbChart = null; }
    if (!datasets.length) return;
    state.pbChart = new Chart(canvas.getContext("2d"), {
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { boxWidth: 12, font: { size: 12 }, usePointStyle: true } },
          tooltip: {
            // 기준선·추세선 위에서는 툴팁을 열지 않는다 — 점(측정)만 정보가 있다.
            filter: (item) => item.dataset && item.dataset.type === "scatter",
            callbacks: {
              label: (item) => {
                const point = item.raw || {};
                return `PB ${fmt(point.x)} → 점도 ${fmt(point.y)}`;
              },
              afterLabel: (item) => {
                const point = item.raw || {};
                return [`측정일 ${point.date || "-"}`, `사용한 PB LOT ${point.lot || "-"}`];
              },
            },
          },
        },
        scales: {
          x: { type: "linear", title: { display: true, text: "사용한 PB 점도" } },
          y: { type: "linear", title: { display: true, text: "이 반제품 점도" } },
        },
      },
    });
  }

  function renderPeriods() {
    const allPeriods = state.analysis.periods || [];
    const body = $("visc-period-body");
    body.innerHTML = "";
    // 최근 PERIOD_DISPLAY_CAP 개 구간만 표시. periods 는 시간순(오래된→최신) 오름차순이라
    // 끝에서 60개를 잘라(recent) 차트는 그대로 왼→오 시간순으로 그리고, 표는 최신순으로
    // 뒤집어 보여준다. 전체 구간은 Excel 내보내기로.
    const truncated = allPeriods.length > PERIOD_DISPLAY_CAP;
    const recent = truncated ? allPeriods.slice(-PERIOD_DISPLAY_CAP) : allPeriods;
    const more = $("visc-period-more");
    if (!allPeriods.length) {
      body.appendChild(emptyRow(9, "측정일이 있는 데이터가 없습니다."));
      if (more) more.hidden = true;
    } else {
      if (truncated) {
        const note = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 9;
        cell.className = "muted visc-period-truncation";
        cell.textContent =
          "최근 60개 구간만 표시 — 전체 구간은 [전체 Excel] 버튼을 이용하세요.";
        note.appendChild(cell);
        body.appendChild(note);
      }
      // 표는 최신 구간을 위로, 처음에는 12행만. 나머지는 [더보기]로 펼친다.
      const rows = recent.slice().reverse();
      const shown = rows.slice(0, state.periodRows);
      if (more) {
        more.hidden = rows.length <= shown.length;
        more.textContent = `더보기 (${shown.length}/${rows.length}구간)`;
      }
      shown
        .forEach((period) => {
          const row = document.createElement("tr");
          if (period.anomaly_count > 0) row.className = "row-anomaly";
          appendTextCell(row, period.period);
          appendTextCell(row, period.count, "num");
          appendTextCell(row, fmt(period.mean), "num");
          appendDeltaCell(row, period.mean_delta);
          appendTextCell(row, fmt(period.std), "num");
          appendTextCell(row, fmt(period.min), "num");
          appendTextCell(row, fmt(period.max), "num");
          appendTextCell(row, period.anomaly_count, "num");
          appendTextCell(row, period.warn_count, "num");
          body.appendChild(row);
        });
    }
    renderPeriodChart(recent); // 차트는 오름차순(시간순) 유지
  }

  function renderPeriodChart(periods) {
    const canvas = $("visc-period-chart");
    const emptyNote = $("visc-chart-empty");
    if (emptyNote) emptyNote.hidden = (periods || []).length > 0;
    const center = state.analysis.stats.center;
    const product = state.analysis.product || {};
    const { labels, datasets } = periodChartDatasets(periods, center, getCssVar, {
      lower: product.lower_limit,
      upper: product.upper_limit,
    });
    // 이상·제외 개별 측정을 기간 막대 위에 점으로 얹는다 — 통계에서 빠졌어도(제외) /
    // 집계에 묻혀도(이상) 규격 이탈 사건이 눈에 남게. periodChartDatasets 은 순수라
    // 여기서 병합한다. 표시 중인 구간(labels)에 속한 측정만 오버레이한다.
    const overlay = readingOverlayDatasets(
      state.analysis.readings || [],
      state.granularity,
      labels,
      getCssVar,
    );
    const allDatasets = datasets.concat(overlay);
    if (state.periodChart) state.periodChart.destroy();
    state.periodChart = new Chart(canvas.getContext("2d"), {
      data: { labels, datasets: allDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { boxWidth: 12, font: { size: 12 }, usePointStyle: true } },
          tooltip: {
            callbacks: {
              // 개별 측정 점 위에서는 그 측정이 무엇인지(LOT·측정일) 알려 준다.
              afterLabel: (item) => {
                const point = item.raw;
                if (!point || typeof point !== "object" || !point.lot) return [];
                return [`LOT ${point.lot}${point.date ? ` · ${point.date}` : ""}`];
              },
              afterBody: (items) => {
                // 기간 평균 선 위에서만 구간 요약을 보여준다. 오버레이 점(개별 측정·
                // 이상·제외)의 dataIndex 는 자기 데이터셋 기준이라 periods 인덱스와
                // 다르므로 제외한다(라벨로 구분 — 선/점 모두 type 이 line·scatter 다).
                const barItem = items.find(
                  (it) => it.dataset && it.dataset.label === "기간 평균"
                );
                const period = barItem ? periods[barItem.dataIndex] : null;
                if (!period) return [];
                return [
                  `건수: ${period.count}`,
                  `표준편차: ${fmt(period.std)}`,
                  `범위: ${fmt(period.min)} ~ ${fmt(period.max)}`,
                  `이상: ${period.anomaly_count} · 경고: ${period.warn_count}`,
                ];
              },
            },
          },
        },
        scales: {
          // 오버레이 산점도를 섞으면 Chart.js 가 x축을 선형으로 추정해 막대가 어긋난다.
          // 기간 라벨 축을 category 로 명시해 막대·점이 같은 구간 위에 오도록 고정한다.
          x: { type: "category" },
          y: Object.assign(
            { beginAtZero: false },
            periodChartYBounds(state.analysis.stats, state.analysis.product, periods),
          ),
        },
      },
    });
  }

  // 배합 기록 목록 — 서버가 한 번에 준다(GET /viscosity/products/{id}/blend-records).
  //
  // 종전에는 /blend/records 를 코드·이름으로 두 번 검색한 뒤 20건을 낱개 상세 조회해
  // 반제품 하나 고를 때마다 HTTP 22회가 나갔고, '미등록만' 은 그렇게 잘라온 20건에만
  // 걸려서 더 오래된 미등록 LOT 이 있어도 빈 목록이 떴다(현장 검토 4·10번).
  // 검색(q)·미등록 필터·등록 여부 계산은 이제 전부 서버 몫이고 요청은 1회다.
  async function loadBlendRecords(options) {
    const opts = options || {};
    const product = currentProduct();
    if (opts.reset) state.blendLimit = BLEND_LIMIT_STEPS[0];
    if (!product) {
      state.blendRecords = [];
      state.selectedBlendId = null;
      state.selectedBlendDetail = null;
      renderBlendRecords();
      return;
    }
    const query = { limit: state.blendLimit };
    const q = $("visc-blend-filter").value.trim();
    if (q) query.q = q;
    if ($("visc-open-only").checked) query.unregistered = "1";
    // 반응기 필터도 서버로 — 클라이언트에서 거르면 받아온 limit 건 안에서만 걸러져
    // 반응기별 옛 기록을 [더보기] 없이는 못 본다(분석 select 와 같은 규약: 1~4/none).
    if (state.reactor === "none") query.reactor = "none";
    else if (state.reactor != null) query.reactor = String(state.reactor);
    let data;
    try {
      data = await request(`/viscosity/products/${product.id}/blend-records`, { query });
    } catch (error) {
      state.blendRecords = [];
      renderBlendRecords();
      notify(`배합 기록을 불러오지 못했습니다: ${error.message || error}`, "error");
      return;
    }
    const items = data.items || [];
    state.blendReturned = items.length;
    state.blendTotal = data.total || 0;
    state.blendUnregisteredTotal = data.unregistered_total || 0;
    state.blendRecords = items;
    const keep = items.some((r) => r.id === state.selectedBlendId);
    if (!keep) state.selectedBlendId = items.length ? items[0].id : null;
    renderBlendRecords();
    if (state.selectedBlendId) {
      await selectBlendRecord(state.selectedBlendId, { focus: false });
    } else {
      clearUsedPb();
    }
  }

  function renderBlendRecords() {
    const records = state.blendRecords;
    if (records.length && !records.some((record) => record.id === state.selectedBlendId)) {
      state.selectedBlendId = records[0].id;
    } else if (!records.length) {
      state.selectedBlendId = null;
      state.selectedBlendDetail = null;
    }
    state.selectedBlendDetail = selectedRecord();
    $("visc-blend-record").value = state.selectedBlendId ? String(state.selectedBlendId) : "";
    renderBlendTable(records);
    renderSelectedBlend();
  }

  function renderBlendTable(records) {
    const body = $("visc-blend-body");
    body.innerHTML = "";
    const openOnly = $("visc-open-only").checked;
    const scope = openOnly ? state.blendUnregisteredTotal : state.blendTotal;
    $("visc-record-count").textContent = scope
      ? `${records.length} / ${scope}건${openOnly ? " (미등록)" : ""}`
      : "0건";
    // [더보기] — 서버가 limit 만큼만 줬고 전체가 더 많으면 다음 단계로 올려 다시 받는다.
    const more = $("visc-blend-more");
    if (more) {
      const step = nextBlendLimit();
      more.hidden = !(step && state.blendReturned >= state.blendLimit && scope > state.blendReturned);
      more.textContent = `더보기 (${step}건까지)`;
    }
    if (!records.length) {
      body.appendChild(emptyRow(
        BLEND_COLS,
        openOnly
          ? "미등록 배합 기록이 없습니다. (모두 점도가 등록되었습니다)"
          : "이 반제품의 배합 기록이 없습니다.",
      ));
      return;
    }
    records.forEach((record) => {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", record.id === state.selectedBlendId);
      const analysisReading = record.registered ? findReadingByLot(record.product_lot) : null;
      if (analysisReading && (analysisReading.status === "excluded" || analysisReading.excluded)) {
        row.classList.add("row-excluded");
      }
      row.addEventListener("click", () => selectBlendRecord(record.id, { focus: true }));
      appendTextCell(row, record.product_lot);
      appendTextCell(row, record.work_date || "-");
      appendTextCell(row, record.worker || "-");
      appendTextCell(
        row,
        record.total_amount == null ? "-" : `${fmt(record.total_amount)} g`,
        "num visc-col-amount",
      );
      appendViscosityCell(row, record);
      appendStatusCell(row, record);
      // 행 액션(통계 제외/해제·삭제)은 점도 값 칸이 아니라 별도 '관리' 열로 — 점도 칸은
      // 값만 남겨 숫자로 읽히게 한다(DHR 수정표·자재 LOT 표와 같은 방식).
      if (isManager) appendManageCell(row, record);
      body.appendChild(row);
    });
  }

  function nextBlendLimit() {
    return BLEND_LIMIT_STEPS.find((step) => step > state.blendLimit) || null;
  }

  // 관리 열 — 책임자 전용 행 액션. 점도 값과 같은 판정(제외/정상)에 따라 버튼 구성을 바꾼다.
  //
  // 측정 id 는 목록 API 가 아니라 분석 표본(analysis.readings)에서 LOT 로 찾는다.
  // 선택한 연도·반응기 밖의 측정은 표본에 없으므로 버튼을 내지 않는다(그 조건에서
  // 통계 제외/삭제를 눌러도 화면 숫자가 안 바뀌어 무슨 일이 일어났는지 알 수 없다).
  function appendManageCell(row, record) {
    const cell = document.createElement("td");
    cell.className = "visc-manage-cell";
    const reading = record.registered ? findReadingByLot(record.product_lot) : null;
    if (!reading) {
      cell.className = "visc-manage-cell muted";
      cell.textContent = "-";
      row.appendChild(cell);
      return;
    }
    const analysisReading = reading;
    const isExcluded = Boolean(
      analysisReading && (analysisReading.status === "excluded" || analysisReading.excluded)
    );
    // 통계 제외 / 제외 해제 — 삭제와 별개. 제외는 값을 남기고 통계에서만 빼며,
    // 어느 행이든(경고·이상뿐 아니라) 책임자가 치워둘 수 있다.
    if (isExcluded) {
      const inc = document.createElement("button");
      inc.className = "visc-inc-btn";
      inc.type = "button";
      inc.textContent = "제외 해제";
      inc.addEventListener("click", (event) => {
        event.stopPropagation();
        includeReading(reading.id, reading.lot_no);
      });
      cell.appendChild(inc);
    } else {
      const exc = document.createElement("button");
      exc.className = "visc-exclude-btn";
      exc.type = "button";
      exc.textContent = "통계 제외";
      exc.addEventListener("click", (event) => {
        event.stopPropagation();
        openExcludeModal(reading.id, reading.lot_no);
      });
      cell.appendChild(exc);
    }
    const del = document.createElement("button");
    del.className = "visc-del-btn";
    del.type = "button";
    del.textContent = "삭제";
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteReading(reading.id, reading.lot_no);
    });
    cell.appendChild(del);
    row.appendChild(cell);
  }

  // 상태 열 — 행별 판정을 별도 열로 상시 표시(값 옆 배지는 수십 행에서 안 보임 — 사용자 요청).
  function appendStatusCell(row, record) {
    const cell = document.createElement("td");
    if (!record.registered) {
      cell.className = "muted";
      cell.textContent = "미등록";
      row.appendChild(cell);
      return;
    }
    const reading = findReadingByLot(record.product_lot);
    const status = reading ? reading.status : null;
    const badge = document.createElement("span");
    if (status === "excluded" || (reading && reading.excluded)) {
      // 통계 제외 — 삭제하지 않고 통계에서만 뺀 측정. 회색으로 '치워둔' 인상을 주고,
      // 사유·처리자·시각을 인라인 + 툴팁으로 남긴다(사유가 hover 뒤로만 숨으면 놓친다).
      badge.className = "visc-status excluded";
      badge.textContent = "제외";
      const reason = reading.exclude_reason || "";
      const meta = [reading.excluded_by, reading.excluded_at].filter(Boolean).join(" · ");
      badge.title = `통계 제외${reason ? ` · 사유: ${reason}` : ""}${meta ? ` (${meta})` : ""}`;
      cell.appendChild(badge);
      if (reason) {
        const note = document.createElement("span");
        note.className = "visc-exclude-reason muted small";
        note.textContent = reason;
        if (meta) note.title = meta;
        cell.appendChild(note);
      }
      row.appendChild(cell);
      return;
    }
    if (status === "anomaly" || status === "warn") {
      badge.className = `visc-status ${status}`;
      badge.textContent = status === "anomaly" ? "이상" : "경고";
    } else if (status === null || status === undefined) {
      // 분석 표본에 없는 측정(선택 연도 밖 등) — 판정이 '없는' 것이지 '정상'이 아니다.
      // 예전에는 else 로 떨어져 초록 '정상' 배지가 붙었고, 규격을 벗어난 측정이
      // 품질 책임자 화면에서 정상으로 읽혔다.
      badge.className = "visc-status";
      badge.textContent = "판정 없음";
      badge.title = "이 측정은 현재 선택한 연도·조건의 분석 표본에 없어 판정되지 않았습니다.";
    } else {
      badge.className = "visc-status normal";
      badge.textContent = "정상";
    }
    cell.appendChild(badge);
    row.appendChild(cell);
  }

  function appendViscosityCell(row, record) {
    if (!record.registered || record.viscosity == null) {
      const cell = document.createElement("td");
      cell.className = "num muted";
      cell.textContent = "미입력";
      row.appendChild(cell);
      return;
    }
    const analysisReading = findReadingByLot(record.product_lot);
    const isExcluded = Boolean(
      analysisReading && (analysisReading.status === "excluded" || analysisReading.excluded)
    );
    const cell = document.createElement("td");
    cell.className = isExcluded ? "num visc-reading-cell is-excluded" : "num visc-reading-cell";
    const value = document.createElement("span");
    value.className = "visc-reading-value";
    value.textContent = fmt(record.viscosity);
    cell.appendChild(value);
    if (record.reactor) {
      const rx = document.createElement("span");
      rx.className = "muted small";
      rx.textContent = ` · 반응기 ${record.reactor}`;
      cell.appendChild(rx);
    }
    // 행 액션은 '관리' 열(appendManageCell)로 옮겼다 — 여기는 값만.
    row.appendChild(cell);
  }

  function renderSelectedBlend() {
    const box = $("visc-selected-row");
    const record = selectedRecord();
    if (!record) {
      box.textContent = "배합 기록 표에서 미등록 행을 선택하세요.";
      setSubmitEnabled(false);
      return;
    }
    box.textContent = record.registered
      ? `${record.product_lot} · 점도 ${fmt(record.viscosity)} 등록됨`
      : `${record.product_lot} · ${record.work_date || "-"} · ${record.worker || "-"} 선택`;
    setSubmitEnabled(!record.registered);
  }

  async function selectBlendRecord(recordId, options) {
    state.selectedBlendId = Number(recordId);
    $("visc-blend-record").value = String(recordId || "");
    renderBlendRecords();
    if (options && options.focus) {
      const record = selectedRecord();
      if (record && !record.registered) $("visc-value").focus();
    }
    await loadUsedPb(state.selectedBlendId);
  }

  function selectedRecord() {
    return state.blendRecords.find((record) => record.id === state.selectedBlendId) || null;
  }

  // ── 사용한 PB ────────────────────────────────────────────────────────
  // 등록 전에 서버가 무엇을 PB 로 감지했는지 보여준다. matched 면 조용히 한 줄,
  // first_row(자재명 PB 행을 못 찾아 첫 계량 자재로 추정)면 경고 톤 + 수정 입력.
  // 종전에는 이 감지가 화면 밖에서 조용히 저장돼, 엉뚱한 자재 LOT 이 '사용한 PB'
  // 로 박혀도 아무도 몰랐다(현장 검토 2번).
  function clearUsedPb() {
    state.usedPb = null;
    const box = $("visc-usedpb");
    if (box) box.hidden = true;
    const fix = $("visc-usedpb-fix");
    if (fix) fix.hidden = true;
    const input = $("visc-usedpb-lot");
    if (input) input.value = "";
  }

  async function loadUsedPb(recordId) {
    clearUsedPb();
    if (!recordId) return;
    const record = selectedRecord();
    if (record && record.registered) return;   // 이미 등록된 기록에는 물어볼 게 없다
    let data;
    try {
      data = await request(`/viscosity/blend-records/${recordId}/used-pb`);
    } catch (_error) {
      return;                                   // 보조 정보 — 실패해도 등록은 막지 않는다
    }
    if (state.selectedBlendId !== Number(recordId)) return;  // 그새 다른 행을 골랐다
    state.usedPb = data;
    renderUsedPb();
  }

  function renderUsedPb() {
    const box = $("visc-usedpb");
    const text = $("visc-usedpb-text");
    const fix = $("visc-usedpb-fix");
    const input = $("visc-usedpb-lot");
    if (!box || !text) return;
    const info = state.usedPb;
    if (!info) { clearUsedPb(); return; }
    box.hidden = false;
    const lot = info.lot || "";
    if (info.method === "matched") {
      box.className = "visc-usedpb";
      const visc = info.pb_viscosity != null ? ` · 점도 ${fmt(info.pb_viscosity)}` : " · 점도 미등록";
      text.textContent = `사용한 PB: ${lot}${visc}`;
      if (fix) fix.hidden = true;
      if (input) input.value = "";
      return;
    }
    if (info.method === "first_row") {
      box.className = "visc-usedpb is-uncertain";
      text.textContent = lot
        ? `PB 자재를 찾지 못해 첫 계량 자재(${lot})로 추정했습니다 — 확인하세요.`
        : "사용한 PB를 찾지 못했습니다 — 아래에 직접 입력하세요.";
      if (fix) fix.hidden = false;
      if (input && !input.value) input.value = lot;
      return;
    }
    box.className = "visc-usedpb is-uncertain";
    text.textContent = "이 배합 기록에는 사용한 PB LOT 이 없습니다 — 필요하면 직접 입력하세요.";
    if (fix) fix.hidden = false;
  }

  // 배합 실적 연계 측정(list_readings_for_blend)엔 판정(status)이 없다. 재조회한 분석
  // (analyze_product 이 각 측정에 status 부여)에서 같은 LOT 를 찾아 판정을 얻는다.
  function findReadingByLot(lotNo) {
    if (!lotNo || !state.analysis) return null;
    const readings = state.analysis.readings || [];
    for (let i = readings.length - 1; i >= 0; i -= 1) {
      if (readings[i].lot_no === lotNo) return readings[i];
    }
    return null;
  }

  function setSubmitEnabled(enabled) {
    $("visc-submit").disabled = !enabled;
    $("visc-value").disabled = !enabled;
    $("visc-memo").disabled = !enabled;
  }

  async function submitReading(event) {
    event.preventDefault();
    const error = $("visc-form-error");
    error.hidden = true;
    const recordId = Number($("visc-blend-record").value);
    const value = Number($("visc-value").value);
    if (!recordId) {
      showFormError("배합 기록을 선택하세요.");
      return;
    }
    if (!(value > 0)) {
      showFormError("점도값을 입력하세요.");
      return;
    }
    const submittedRecord = selectedRecord();
    const lotNo = submittedRecord ? submittedRecord.product_lot : null;
    const body = {
      viscosity: value,
      memo: $("visc-memo").value.trim() || null,
      product_id: state.currentId,
    };
    // 화면이 고친 '사용한 PB' LOT — 자동 감지가 불확실할 때만 입력칸이 열린다.
    // 값이 있으면 서버가 감지 대신 이 값을 쓴다(method=manual).
    const fixInput = $("visc-usedpb-lot");
    const fixed = fixInput && !$("visc-usedpb-fix").hidden ? fixInput.value.trim() : "";
    if (fixed) body.material_lot = fixed;
    try {
      // 반응기는 배합 실적에서 지정하고 점도는 실적에서 물려받는다(여기서 입력하지 않음).
      // product_id: 현재 선택한 반제품으로 귀속(F13) — 없으면 서버가 레거시 경로(레코드
      // 제품명으로 자동 확보)를 쓴다. 화면 선택을 서버가 무시해 유령 반제품이 생기던 사고 방지.
      const saved = await request(`/blend/records/${recordId}/viscosity`, {
        method: "POST",
        body,
      });
      $("visc-value").value = "";
      $("visc-memo").value = "";
      const selectedId = recordId;
      // 재조회로 방금 등록한 측정의 판정(이상/경고/정상)을 확정한다 — analyze_product 이
      // 각 측정에 status 를 붙이므로 목록 배지·카드·알림이 항상 일치한다.
      await loadProduct(state.currentId);
      if (selectedId) await selectBlendRecord(selectedId, { focus: false });
      // 판정은 제출 버튼 바로 위에 남긴다 — 정상이어도 무엇으로 판정됐는지 보이게.
      showVerdict(value, lotNo, saved && saved.used_pb);
    } catch (error_) {
      showFormError(error_.message);
    }
  }

  // 등록 직후 판정(정상/경고/이상)을 폼 안에 표시한다. 종전에는 정상일 때 토스트
  // 하나로 끝나 "그래서 이 값이 괜찮다는 건가"를 화면이 말해 주지 않았다.
  function showVerdict(value, lotNo, usedPb) {
    const reading = findReadingByLot(lotNo);
    const status = reading ? reading.status : null;
    const result = $("visc-form-result");
    const pbTail = usedPb && usedPb.lot
      ? ` · 사용한 PB ${usedPb.lot}${usedPb.method === "manual" ? "(직접 입력)" : ""}`
      : "";
    const reasons = reading
      ? (reading.reasons || []).map((item) => REASON_LABEL[item] || item).join(", ")
      : "";
    const tail = reasons ? ` · ${reasons}` : "";
    result.hidden = false;
    if (status === "anomaly") {
      result.className = "visc-form-result anomaly";
      result.textContent = `⚠ 이상 판정 — 관리 범위를 벗어났습니다. 책임자에게 알리세요.`
        + ` (점도 ${fmt(value)}${tail})${pbTail}`;
      notify(result.textContent, "error");
      return;
    }
    if (status === "warn") {
      result.className = "visc-form-result warn";
      result.textContent = `⚠ 경고 구간 — 확인이 필요합니다. (점도 ${fmt(value)}${tail})${pbTail}`;
      notify(result.textContent, "warn");
      return;
    }
    result.className = "visc-form-result normal";
    result.textContent = `정상 판정 — 등록했습니다. (점도 ${fmt(value)})${pbTail}`;
    notify(`점도를 등록했습니다. (${fmt(value)})`, "success");
  }

  function showFormError(message) {
    const error = $("visc-form-error");
    error.textContent = message;
    error.hidden = false;
  }

  async function deleteReading(readingId, lotNo) {
    // 복원할 수 없는 동작이라 확인을 받는다. 확인창 두 번은 두 번째를 읽지 않고
    // 누르게 만들 뿐이라 한 번으로 되돌린다(2026-08-13 검토) — 대신 그 한 번이
    // 되돌릴 수 없다는 것과 [통계 제외]라는 대안을 분명히 말한다.
    if (!window.confirm(
      `측정 기록을 삭제할까요? (LOT ${lotNo})\n`
      + "삭제는 되돌릴 수 없습니다. 통계에서만 빼려면 [통계 제외]를 사용하세요."
    )) return;
    try {
      await request(`/viscosity/readings/${readingId}`, { method: "DELETE" });
      notify("측정 기록을 삭제했습니다.", "success");
      await loadProduct(state.currentId);
    } catch (error) {
      notify(`삭제 실패: ${error.message}`, "error");
    }
  }

  // ── 통계 제외 / 제외 해제 (책임자 전용) ──────────────────────────────
  // 삭제와 달리 값은 남기고 통계(평균·σ·추세)에서만 뺀다. 쓰기 요청의 CSRF 는
  // request(=IRMS._core.request)가 x-csrftoken 을 자동 부착한다(이 파일의 등록·삭제와
  // 동일 경로). 제외는 사유가 필수라 모달로 받고, 해제는 사유가 없어 확인만 받는다.
  let excludeTargetId = null;

  function openExcludeModal(readingId, lotNo) {
    const modal = $("visc-exclude-modal");
    if (!modal) return;                 // 담당자 화면에는 모달이 없다(책임자 전용)
    excludeTargetId = readingId;
    $("visc-exclude-title").textContent = `통계 제외 · LOT ${lotNo}`;
    $("visc-exclude-reason").value = "";
    $("visc-exclude-error").hidden = true;
    if (excludeModal) {
      excludeModal.open();  // 사유 textarea 로 포커스 이동(initialFocus)
    } else {
      modal.hidden = false;
      $("visc-exclude-reason").focus();
    }
  }

  function closeExcludeModal() {
    if (excludeModal) { excludeModal.close(); return; }  // close() 가 excludeTargetId 초기화
    const modal = $("visc-exclude-modal");
    if (modal) modal.hidden = true;
    excludeTargetId = null;
  }

  async function submitExclude(event) {
    event.preventDefault();
    const error = $("visc-exclude-error");
    error.hidden = true;
    const reason = $("visc-exclude-reason").value.trim();
    if (!reason) {
      error.textContent = "제외 사유를 입력하세요.";
      error.hidden = false;
      return;
    }
    if (!excludeTargetId) {
      closeExcludeModal();
      return;
    }
    try {
      await request(`/viscosity/readings/${excludeTargetId}/exclude`, {
        method: "POST",
        body: { reason },
      });
      closeExcludeModal();
      IRMS.notify("측정값을 통계에서 제외했습니다.", "success");
      // 재조회로 평균·σ·추세가 즉시 갱신되고, 목록 배지·카드가 일치한다.
      await loadProduct(state.currentId);
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  async function includeReading(readingId, lotNo) {
    // 해제는 사유·제외자·제외시각을 지운다(서버) — "언제든 되돌릴 수 있다"는 약속에서
    // 사유만은 예외임을 결정하는 순간에 말한다(2026-08-05 전수 감사 R-5).
    if (!window.confirm(
      `이 측정값을 다시 통계에 포함할까요? (LOT ${lotNo})\n`
      + "입력했던 제외 사유는 화면에서 지워집니다(감사 이력에는 남습니다)."
    )) return;
    try {
      await request(`/viscosity/readings/${readingId}/include`, { method: "POST" });
      IRMS.notify("측정값을 통계에 다시 포함했습니다.", "success");
      await loadProduct(state.currentId);
    } catch (error) {
      IRMS.notify(`제외 해제 실패: ${error.message}`, "error");
    }
  }

  // ── 반제품 관리 모달: 전체 목록 → 행 선택 → 수정 / 새 반제품 추가 ──
  // 수정 대상은 화면에서 보고 있는 반제품과 무관하게 목록에서 고른다.
  let settingsProducts = [];
  let settingsId = null;
  let recipeCandidates = [];

  async function openSettings() {
    try {
      const data = await request("/viscosity/products");
      settingsProducts = data.items || [];
    } catch (error_) {
      notify(`반제품 목록을 불러오지 못했습니다: ${error_.message}`, "error");
      return;
    }
    const current = settingsProducts.find((p) => p.id === state.currentId) || settingsProducts[0];
    fillSettingsForm(current || null);
    renderSettingsList();
    loadRecipeCandidates().catch(() => {});
    $("visc-settings-error").hidden = true;
    $("visc-new-error").hidden = true;
    refreshReminderSince();
    if (settingsModal) settingsModal.open();
    else $("visc-settings-modal").hidden = false;
  }

  // ── 점도 알림 정리 기준일 ────────────────────────────────────
  // 기준일 이후에 배합한 반제품만 알림 대상이 된다. [지금까지 정리]는 기준일을 오늘로
  // 당겨, 이제 와서 잴 수 없는 지난 배합분을 알림에서 덮는다(기록은 그대로).
  async function refreshReminderSince() {
    const el = $("visc-reminder-since-value");
    if (!el) return;
    try {
      const data = await request("/settings/viscosity-reminder-since");
      el.textContent = data.since || "없음 (전체 대상 알림)";
    } catch (_e) {
      el.textContent = "-";
    }
  }

  async function handleReminderSinceUpdate() {
    if (!window.confirm(
      "지금까지의 미등록 점도를 정리합니다.\n"
      + "오늘 이전에 배합한 건은 앞으로 점도 알림에 뜨지 않습니다"
      + "(기록에는 그대로 남습니다). 계속할까요?",
    )) return;
    const btn = $("visc-reminder-since-btn");
    if (btn) btn.disabled = true;
    try {
      const data = await request("/settings/viscosity-reminder-since", { method: "POST" });
      const el = $("visc-reminder-since-value");
      if (el) el.textContent = data.since;
      notify(`정리했습니다 — ${data.since} 이후 배합분부터 알립니다.`, "success");
    } catch (error_) {
      notify(`정리 실패: ${error_.message}`, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // 반제품 추가 후보 = (완성) 레시피 중 아직 점도 반제품이 없는 제품명
  async function loadRecipeCandidates() {
    const data = await request("/blend/recipes");
    const existing = new Set(
      settingsProducts.flatMap((p) => [String(p.code).toLowerCase(), String(p.name).toLowerCase()])
    );
    recipeCandidates = (data.items || [])
      .map((r) => String(r.product_name || "").trim())
      .filter((name) => name && !existing.has(name.toLowerCase()));
    const list = $("visc-recipe-candidates");
    if (list) {
      list.innerHTML = "";
      recipeCandidates.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        list.appendChild(opt);
      });
    }
  }

  function renderSettingsList() {
    const body = $("visc-prod-list");
    body.innerHTML = "";
    if (!settingsProducts.length) {
      body.appendChild(emptyRow(5, "반제품이 없습니다. 아래에서 추가하세요."));
      return;
    }
    settingsProducts.forEach((product) => {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", product.id === settingsId);
      row.style.cursor = "pointer";
      appendTextCell(row, product.code);
      appendTextCell(row, product.name);
      appendTextCell(row, product.remind_daily ? "켜짐" : "-");
      appendTextCell(row, product.use_reactor ? "사용" : "-");
      appendTextCell(row, product.is_active ? "사용" : "중지");
      row.addEventListener("click", () => {
        fillSettingsForm(product);
        renderSettingsList();
      });
      body.appendChild(row);
    });
  }

  function fillSettingsForm(product) {
    settingsId = product ? product.id : null;
    $("visc-settings-title").textContent = product
      ? `반제품 설정 · ${product.code}`
      : "반제품 설정";
    $("visc-set-name").value = product ? product.name : "";
    $("visc-set-target").value = product ? (product.target ?? "") : "";
    $("visc-set-lower").value = product ? (product.lower_limit ?? "") : "";
    $("visc-set-upper").value = product ? (product.upper_limit ?? "") : "";
    $("visc-set-sigma").value = product ? product.sigma_k : 3;
    $("visc-set-rpm").value = product ? (product.rpm ?? "") : "";
    $("visc-set-temp").value = product ? (product.temperature ?? "") : "";
    $("visc-set-remind").checked = Boolean(product && product.remind_daily);
    $("visc-set-active").checked = product ? product.is_active : true;
  }

  function numOrNull(id) {
    const value = $(id).value.trim();
    return value === "" ? null : Number(value);
  }

  async function saveSettings(event) {
    event.preventDefault();
    const error = $("visc-settings-error");
    error.hidden = true;
    if (!settingsId) {
      error.textContent = "수정할 반제품을 목록에서 선택하세요.";
      error.hidden = false;
      return;
    }
    const body = {
      name: $("visc-set-name").value.trim(),
      target: numOrNull("visc-set-target"),
      lower_limit: numOrNull("visc-set-lower"),
      upper_limit: numOrNull("visc-set-upper"),
      sigma_k: Number($("visc-set-sigma").value),
      rpm: numOrNull("visc-set-rpm"),
      temperature: numOrNull("visc-set-temp"),
      remind_daily: $("visc-set-remind").checked,
      is_active: $("visc-set-active").checked,
    };
    try {
      const updated = await request(`/viscosity/products/${settingsId}`, { method: "PATCH", body });
      notify(`저장했습니다: ${updated.code}`, "success");
      // 모달은 열어 둔 채 목록 갱신(여러 반제품 연속 관리), 본화면은 뒤에서 갱신
      settingsProducts = settingsProducts.map((p) => (p.id === updated.id ? updated : p));
      renderSettingsList();
      loadOverview().catch(() => {});
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  async function createProduct(event) {
    event.preventDefault();
    const error = $("visc-new-error");
    error.hidden = true;
    const name = $("visc-new-code").value.trim();
    // 레시피 연동 강제: 후보(점도 반제품이 없는 레시피)에서만 선택 가능
    const hit = recipeCandidates.find((c) => c.toLowerCase() === name.toLowerCase());
    if (!hit) {
      error.textContent = "레시피 목록에서 선택하세요. (이미 반제품이 있거나 레시피에 없는 제품)";
      error.hidden = false;
      return;
    }
    try {
      const created = await request("/viscosity/products", {
        method: "POST",
        body: { code: hit, name: hit },
      });
      $("visc-new-form").reset();
      notify(`반제품을 추가했습니다: ${created.code}`, "success");
      settingsProducts = [...settingsProducts, created];
      fillSettingsForm(created);   // 이어서 기준값 입력하도록 수정 폼에 로드
      renderSettingsList();
      loadRecipeCandidates().catch(() => {});
      loadOverview().catch(() => {});
    } catch (error_) {
      error.textContent = error_.message;
      error.hidden = false;
    }
  }

  function exportCsv() {
    if (!state.currentId) return;
    // GAP-2: Excel 판정·기간 요약을 화면과 같은 필터(단위/연도/반응기)로 맞추기 위해 현재
    // state 를 쿼리로 넘긴다. 직접 다운로드(GET 내비게이션)라 CSRF 헤더는 불필요하고,
    // 관리 세션 쿠키가 export 의 책임자 강제(정책 ⓑ)를 통과시킨다.
    const params = new URLSearchParams();
    if (state.granularity) params.set("granularity", state.granularity);
    if (state.year !== null && state.year !== undefined) params.set("year", String(state.year));
    if (state.reactor !== null && state.reactor !== undefined) {
      params.set("reactor", String(state.reactor));
    }
    const qs = params.toString();
    const url = `/api/viscosity/products/${state.currentId}/export${qs ? `?${qs}` : ""}`;
    window.location.assign(url);
  }

  function bind() {
    $("visc-form").addEventListener("submit", submitReading);
    $("visc-refresh").addEventListener("click", () => {
      loadOverview().catch((e) =>
        IRMS.notify(`새로고침 실패: ${e.message || e}`, "error"));
    });
    // 분류를 바꾸면 반제품 목록만 다시 그린다. 고른 반제품이 새 분류에 없으면
    // 선택을 비우고 빈 안내 상태로 — 목록에 없는 반제품의 숫자가 남아 있으면
    // 지금 보고 있는 게 무엇인지 어긋난다.
    $("visc-cat-select").addEventListener("change", () => {
      const keep = state.currentId;
      renderProductSelect();
      const sel = $("visc-product-select");
      if (keep && sel && !Array.from(sel.options).some((o) => o.value === String(keep))) {
        sel.value = "";
        showEmptyState();
      }
    });
    // 탭 — 표시 전환만(재요청 없음).
    document.querySelectorAll(".visc-tabs .mgmt-tab").forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });
    // '이상 N건' 카드 → 추세·분석 탭의 이상 목록.
    const anomalyCard = $("visc-anomaly-card");
    if (anomalyCard) {
      anomalyCard.addEventListener("click", openAnomalyList);
      anomalyCard.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openAnomalyList();
        }
      });
    }
    // 검색·미등록 필터는 서버가 건다(전체에서 거른다) — 입력은 300ms 디바운스.
    let searchTimer = null;
    $("visc-blend-filter").addEventListener("input", () => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        loadBlendRecords({ reset: true }).catch(() => {});
      }, 300);
    });
    $("visc-open-only").addEventListener("change", () => {
      loadBlendRecords({ reset: true }).catch(() => {});
    });
    $("visc-blend-more").addEventListener("click", () => {
      const step = nextBlendLimit();
      if (!step) return;
      state.blendLimit = step;
      loadBlendRecords().catch(() => {});
    });
    $("visc-period-more").addEventListener("click", () => {
      state.periodRows += PERIOD_TABLE_ROWS;
      renderPeriods();
    });
    $("visc-pb-more").addEventListener("click", () => {
      state.pbRows += PB_TABLE_ROWS;
      renderSourcePb();
    });
    $("visc-year").addEventListener("change", () => {
      const value = $("visc-year").value;
      state.year = value === "" ? null : Number(value);
      if (state.currentId) reloadProduct(state.currentId);
    });
    $("visc-reactor").addEventListener("change", () => {
      const value = $("visc-reactor").value;
      state.reactor = value === "" ? null : (value === "none" ? "none" : Number(value));
      if (state.currentId) reloadProduct(state.currentId);
    });
    $("visc-gran-toggle").querySelectorAll("button[data-gran]").forEach((button) => {
      button.addEventListener("click", () => {
        if (state.granularity === button.dataset.gran) return;
        state.granularity = button.dataset.gran;
        $("visc-gran-toggle").querySelectorAll("button").forEach((item) => {
          const on = item === button;
          item.classList.toggle("active", on);
          item.setAttribute("aria-pressed", on ? "true" : "false");
        });
        if (state.currentId) reloadProduct(state.currentId);
      });
    });
    const settingsButton = $("visc-settings-btn");
    if (settingsButton) {
      settingsModal = createModal("visc-settings-modal", { initialFocus: "visc-settings-close" });
      settingsButton.addEventListener("click", openSettings);
      $("visc-settings-close").addEventListener("click", () => settingsModal.close());
      $("visc-settings-form").addEventListener("submit", saveSettings);
      $("visc-new-form").addEventListener("submit", createProduct);
      const sinceBtn = $("visc-reminder-since-btn");
      if (sinceBtn) sinceBtn.addEventListener("click", handleReminderSinceUpdate);
      $("visc-export-btn").addEventListener("click", exportCsv);
      $("visc-export-all-btn").addEventListener("click", () => {
        window.location.assign("/api/viscosity/export-all");
      });
    }
    // 통계 제외 모달 (책임자 전용 — 담당자 화면엔 모달이 렌더되지 않는다)
    const excludeForm = $("visc-exclude-form");
    if (excludeForm) {
      // createModal 이 배경 클릭 + Esc 닫기와 포커스 이동/복원을 담당한다.
      excludeModal = createModal("visc-exclude-modal", {
        initialFocus: "visc-exclude-reason",
        onClose: () => { excludeTargetId = null; },
      });
      excludeForm.addEventListener("submit", submitExclude);
      $("visc-exclude-close").addEventListener("click", closeExcludeModal);
    }
  }

  function getCssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    bind();
    loadOverview().catch((error) => notify(`불러오기 실패: ${error.message}`, "error"));
  });
})();

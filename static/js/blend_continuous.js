/**
 * blend_continuous.js — 이어서 계량(연속 배합) 컨트롤러.
 *
 * 한 레시피로 N개 로트를 "자재 열 우선"으로 연속 계량한다: 같은 재료를 로트1·2·3
 * 가로로 연달아 계량하고(통 바꾸는 횟수 최소화), 다음 재료로 넘어간다. 저장은
 * 로트별로 쪼개 배합 기록 N건으로 남긴다(POST /blend/records/continuous).
 *
 * 총 배합량·자재 LOT·서명·반응기·비고는 전 로트 공통. 실제량만 (재료×로트) 셀별.
 * 기준 자재(먼저 계량) 레시피는 로트마다 총량이 달라지므로 이 화면에서 지원하지 않는다
 * (단건 배합 화면에서 진행).
 *
 * 순수 헬퍼는 blend_lib.js(window.IRMS.blendLib) 재사용. CSRF·인증은 IRMS._core.request.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (m) { console.log(m); };

  const {
    esc, TOLERANCE_G, fmt, todayISO, nowTime,
    computeTheoryAmount, findAnchorIndex,
    baseTotalValues, baseTotalLinksHtml,
    rescalePlan, exceedsBatchLimit,
  } = window.IRMS.blendLib;

  const $ = (id) => document.getElementById(id);

  const MIN_LOTS = 1;
  const MAX_LOTS = 12;

  const state = {
    recipes: [],
    current: null,        // /blend/recipes/{id} 응답
    materials: [],        // [{material_id, material_code, material_name, ratio, is_anchor, value_weight}]
    theory: [],           // theory[i] — 전 로트 공통 이론량(총량×ratio)
    sharedLot: [],        // sharedLot[i] — 재료별 자재 LOT(전 로트 공통)
    cells: [],            // cells[i][j] = { actual:"", manual:false }
    lotCount: 2,
    total: 0,
    toleranceG: TOLERANCE_G,
    anchorBlocked: false,
    workers: [],
    sessionWorker: "",
    scaleReady: false,
    workerPad: null,
    // 초과 계량 증량(로트별). lotRescale[j] = null(미사용) 또는 증량 후 그 로트의 총량.
    // lotRescale 이 전부 null 이면 기존 동작(state.total 만 사용)과 완전 동일.
    lotRescale: [],
    // 추가분 입력 모드에 들어간 셀(저울 PRINT 를 추가분으로 합산하기 위한 플래그).
    addModeCell: null,     // {i,j} 또는 null
    // 보류 중인 로트별 증량 제안({j, plan}) — discard 모달 '그래도 증량' 시 재사용.
    pendingContRescale: null,
  };

  // ── 저울 에이전트(현장 PC 127.0.0.1:8787) — 배합 화면과 동일 연동 ──
  const SCALE_URL = "http://127.0.0.1:8787";

  async function detectScale() {
    try {
      const res = await fetch(`${SCALE_URL}/health`, { signal: AbortSignal.timeout(1200) });
      const data = await res.json();
      state.scaleReady = Boolean(data && data.ok);
    } catch (_e) {
      state.scaleReady = false;
    }
  }

  let scaleEventLast = 0;
  let scaleEventSynced = false;

  async function pollScaleEvents() {
    if (!state.scaleReady) { scaleEventSynced = false; return; }
    try {
      const res = await fetch(`${SCALE_URL}/events?after=${scaleEventLast}`, {
        signal: AbortSignal.timeout(1500),
      });
      if (!res.ok) return;
      const data = await res.json();
      const items = data.items || [];
      scaleEventLast = data.last_id || 0;
      if (!scaleEventSynced) { scaleEventSynced = true; return; }
      if (!items.length || !state.materials.length || state.anchorBlocked) return;
      for (const ev of items) {
        const pos = activeScaleCell();
        if (pos === null) {
          notify("모든 셀의 실제량이 입력되어 있습니다. (PRINT 무시)", "warn");
          break;
        }
        fillScaleValue(pos.i, pos.j, ev.value);
        const src = ev.source ? `[${ev.source}] ` : "";
        notify(`${src}저울 입력: ${state.materials[pos.i].material_name} = ${ev.value} g`, "success");
      }
    } catch (_e) { /* 폴링 실패는 조용히 */ }
  }

  // PRINT 가 들어갈 셀: 커서가 있는 실제량 셀 우선, 없으면 첫 미입력 셀(재료→로트 순)
  function activeScaleCell() {
    const focused = document.activeElement;
    if (focused && focused.classList && focused.classList.contains("cont-actual")) {
      return { i: Number(focused.dataset.i), j: Number(focused.dataset.j) };
    }
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        if (state.cells[i][j].actual === "") return { i, j };
      }
    }
    return null;
  }

  function fillScaleValue(i, j, value) {
    const input = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
    if (!input) return;
    // 추가 입력 모드 셀이면 PRINT 값을 추가분으로 합산(누계 = 기존 actual + 입력값).
    if (state.addModeCell && state.addModeCell.i === i && state.addModeCell.j === j) {
      applyAddAmount(i, j, Number(value));
      return;
    }
    input.value = String(value);
    state.cells[i][j].actual = input.value;
    state.cells[i][j].manual = false;  // 저울 입력 — 손입력 표시 해제
    input.classList.remove("manual-warn");
    input.removeAttribute("title");
    updateCellVar(i, j);
    warnIfVariance(i, j);
    focusNextFrom(i, j);
  }

  // ── 작업자 세션 ─────────────────────────────────────────────
  function lockedWorkerName() {
    const worker = $("cont-worker");
    return worker ? worker.value.trim() : "";
  }

  async function loadWorkerNames() {
    try {
      const data = await request("/workers");
      state.workers = (data.items || []).map((w) => w.name);
      const dl = $("worker-names");
      if (dl) dl.innerHTML = state.workers.map((n) => `<option value="${esc(n)}"></option>`).join("");
    } catch (_e) { /* optional */ }
  }

  async function switchWorker(name) {
    const clean = (name || "").trim();
    if (!clean) return false;
    if (clean === state.sessionWorker) return true;
    if (!state.workers.includes(clean)) {
      if (!window.confirm(`처음 보는 이름입니다: "${clean}"\n작업자로 등록하고 교대할까요?`)) return false;
      try {
        await request("/workers", { method: "POST", body: { name: clean } });
        state.workers.push(clean);
        const dl = $("worker-names");
        if (dl) dl.insertAdjacentHTML("beforeend", `<option value="${esc(clean)}"></option>`);
      } catch (e) { notify(`작업자 등록 실패: ${e.message}`, "error"); return false; }
    }
    try {
      await request("/blend/session/login", { method: "POST", body: { worker: clean } });
      state.sessionWorker = clean;
      $("cont-worker").value = clean;
      notify(`작업자 교대: ${clean}`, "success");
      return true;
    } catch (e) {
      notify(`작업자 교대 실패: ${e.message}`, "error");
      return false;
    }
  }

  // ── 전자서명 패드 (배합 화면과 동일) ──────────────────────────
  function attachSignaturePad(canvas) {
    if (!canvas || canvas._padAttached) return canvas && canvas._pad;
    const ctx2 = canvas.getContext("2d");
    ctx2.lineWidth = 2; ctx2.lineCap = "round"; ctx2.strokeStyle = "#111";
    let drawing = false, dirty = false;
    // 빈 서명칸이 깨진 점선 상자처럼 보이지 않게 옅은 안내를 그린다. 첫 획에서 지우고,
    // 비면 다시 그린다. dirty 로 저장 여부를 판단하므로 안내 텍스트는 서명으로 저장되지 않는다.
    const drawHint = () => {
      ctx2.save();
      ctx2.clearRect(0, 0, canvas.width, canvas.height);
      ctx2.fillStyle = "#c4c9d4";
      ctx2.font = "13px Pretendard, sans-serif";
      ctx2.textAlign = "center"; ctx2.textBaseline = "middle";
      ctx2.fillText("여기에 서명", canvas.width / 2, canvas.height / 2);
      ctx2.restore();
    };
    drawHint();
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    };
    const start = (e) => { if (!dirty) ctx2.clearRect(0, 0, canvas.width, canvas.height); drawing = true; const p = pos(e); ctx2.beginPath(); ctx2.moveTo(p.x, p.y); e.preventDefault(); };
    const move = (e) => { if (!drawing) return; const p = pos(e); ctx2.lineTo(p.x, p.y); ctx2.stroke(); dirty = true; e.preventDefault(); };
    const end = () => { drawing = false; };
    canvas.addEventListener("mousedown", start); canvas.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    canvas.addEventListener("touchstart", start); canvas.addEventListener("touchmove", move);
    canvas.addEventListener("touchend", end);
    const pad = {
      clear() { ctx2.clearRect(0, 0, canvas.width, canvas.height); dirty = false; drawHint(); },
      isEmpty() { return !dirty; },
      dataUrl() { return dirty ? canvas.toDataURL("image/png") : null; },
    };
    canvas._padAttached = true; canvas._pad = pad;
    return pad;
  }

  // ── 레시피 로드/선택 ────────────────────────────────────────
  async function loadRecipes() {
    const data = await request("/blend/recipes");
    state.recipes = data.items || [];
    populateRecipeSelect();
  }

  // 분류 → 레시피 2단계 선택(배합 화면과 동일). native select 라 클릭 시 즉시 열리고 리셋.
  function recipesForCategory() {
    const cat = $("cont-recipe-cat") ? $("cont-recipe-cat").value : "";
    if (cat === "") return state.recipes;                       // 전체
    if (cat === "__none__") return state.recipes.filter((r) => !r.category);  // 미분류
    return state.recipes.filter((r) => r.category === cat);
  }

  function populateRecipeSelect() {
    const sel = $("cont-recipe");
    if (!sel) return;
    const prev = sel.value;
    const list = recipesForCategory();
    sel.innerHTML = '<option value="">레시피 선택…</option>'
      + list.map((r) => `<option value="${esc(r.id)}">${esc(r.product_name)}</option>`).join("");
    if (prev && list.some((r) => String(r.id) === prev)) sel.value = prev;
  }

  function selectedRecipeId() {
    return $("cont-recipe").value || "";
  }

  async function onRecipeChange() {
    const id = selectedRecipeId();
    if (!id) return;
    const prevId = state.current && state.current.recipe ? String(state.current.recipe.id) : "";
    if (id === prevId) return;
    const data = await request(`/blend/recipes/${id}`);
    state.current = data;
    state.materials = (data.items || []).map((it) => ({
      material_id: it.material_id,
      material_code: it.material_code,
      material_name: it.material_name,
      ratio: it.ratio,
      is_anchor: it.is_anchor,
      value_weight: it.value_weight,
    }));
    state.toleranceG = (data.recipe && data.recipe.tolerance_g) || TOLERANCE_G;

    // 새 레시피 → 입력 초기화
    $("cont-total").value = "";
    $("cont-note").value = "";
    $("cont-reactor").value = "";
    if (state.workerPad) state.workerPad.clear();
    state.total = 0;
    state.theory = state.materials.map(() => null);
    state.sharedLot = state.materials.map(() => "");
    state.lotRescale = [];          // 레시피 변경 → 증량 오버라이드 전부 리셋(스펙)
    rebuildCells();
    rebuildLotRescale();

    // 기준 자재 레시피는 지원 불가 — 안내 후 표를 비운다.
    state.anchorBlocked = findAnchorIndex(state.materials) >= 0;
    const warn = $("cont-anchor-warn");
    if (state.anchorBlocked) {
      warn.textContent = "이 레시피는 기준 자재(먼저 계량) 방식이라 로트마다 총량이 달라집니다 — "
        + "이어서 계량은 지원하지 않습니다. 배합(단건) 화면에서 진행하세요.";
      warn.hidden = false;
    } else {
      warn.hidden = true;
    }
    renderReactorField();
    renderBaseTotals();
    render();
  }

  function renderReactorField() {
    const field = $("cont-reactor-field");
    if (!field) return;
    const use = Boolean(state.current && state.current.recipe && state.current.recipe.use_reactor);
    field.hidden = !use;
    if (!use) $("cont-reactor").value = "";
  }

  // cells 를 (재료 수 × lotCount)에 맞춘다 — 기존 값 보존.
  function rebuildCells() {
    const next = [];
    for (let i = 0; i < state.materials.length; i++) {
      const prevRow = state.cells[i] || [];
      const row = [];
      for (let j = 0; j < state.lotCount; j++) {
        row.push(prevRow[j] || { actual: "", manual: false });
      }
      next.push(row);
    }
    state.cells = next;
  }

  function recomputeTheory() {
    state.theory = state.materials.map((m) =>
      state.total > 0 ? computeTheoryAmount(m.ratio, state.total) : null
    );
  }

  // ── 로트별 증량(rescale) 핵심 산술 ──────────────────────────
  // lotRescale[j] 가 있으면 그 값, 없으면 공용 총량. lotRescale 전부 null → 기존 동작.
  function lotTotal(j) {
    const override = state.lotRescale && state.lotRescale[j];
    return Math.max(state.total, override || 0);
  }

  // 자재 i 가 로트 j 에서 가져야 할 이론량(로트별 총량 기준).
  // lotRescale[j] 가 없으면 state.theory[i] 와 동일 → 기존 동작 완전 보존.
  function theoryFor(i, j) {
    const m = state.materials[i];
    if (!m) return null;
    const total = lotTotal(j);
    if (!(total > 0)) return null;
    return Math.round((Number(m.ratio) / 100) * total * 1000) / 1000;
  }

  // lotRescale 을 lotCount 에 맞춘다 — 기존 값 보존, 늘어난 칸은 null(미사용).
  function rebuildLotRescale() {
    const next = [];
    for (let j = 0; j < state.lotCount; j++) {
      next.push((state.lotRescale && state.lotRescale[j]) || null);
    }
    state.lotRescale = next;
  }

  // 공정 설명 줄 HTML(전폭). position === 자재 인덱스면 그 자재 앞에, === 자재 수면 끝에.
  // blendLib.stepRowsHtml 은 colspan 이 배합표(7)로 고정이라, 로트 수에 맞춰 별도로 만든다.
  function contStepRowsHtml(steps, position, colspan) {
    return (steps || [])
      .filter((st) => st.position === position)
      .map((st) => `<tr class="blend-step-row"><td colspan="${colspan}">▸ ${esc(st.note)}</td></tr>`)
      .join("");
  }

  // 기본 배합량 버튼(최대 3개) — 레시피 관리에서 지정한 레시피에서만 노출(배합 화면과 동일).
  function renderBaseTotals() {
    const wrap = $("cont-base-links");
    if (!wrap) return;
    const values = (state.anchorBlocked || !state.current) ? [] : baseTotalValues(state.current);
    if (!values.length) { wrap.hidden = true; wrap.innerHTML = ""; return; }
    wrap.innerHTML = baseTotalLinksHtml(values);
    wrap.hidden = false;
  }

  // ── 렌더 ────────────────────────────────────────────────────
  function render() {
    const head = $("cont-mat-head");
    const body = $("cont-mat-body");
    if (!state.materials.length || state.anchorBlocked) {
      head.innerHTML = "";
      body.innerHTML = `<tr><td class="muted">${state.anchorBlocked ? "기준 자재 레시피 — 배합(단건) 화면을 이용하세요." : "레시피를 선택하세요."}</td></tr>`;
      return;
    }
    const lotHeads = [];
    for (let j = 0; j < state.lotCount; j++) {
      // 로트 열마다 색을 순환(cont-lc0~3)해 세로 색띠로 구분 — "지금 몇 번 로트를 재는지" 가독.
      const lc = `cont-lc${j % 4}${j === 0 ? " cont-first-lot" : ""}`;
      // 증량된 로트는 헤더에 조정 총량 표시 + 강조 클래스(주황 계열). 미사용이면 순번만.
      const rescaled = state.lotRescale && state.lotRescale[j];
      const chip = `<span class="cont-lot-chip">로트 ${j + 1}</span>`;
      const totalBadge = rescaled
        ? ` <small class="cont-lot-total">· ${fmt(rescaled)} g</small>`
        : "";
      const emphCls = rescaled ? " cont-lot-rescaled" : "";
      lotHeads.push(
        `<th class="num cont-lot-col ${lc}${emphCls}">${chip}${totalBadge}<br>`
        + `<small class="cont-lot-preview" data-j="${j}">-</small></th>`
      );
    }
    head.innerHTML = "<tr>"
      + '<th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론량(g)</th>'
      + "<th>자재 LOT</th>"
      + lotHeads.join("")
      + "</tr>";

    // 공정 설명 줄(레시피 '설명') — 자재 사이/끝에 전폭 안내 행으로 끼워넣는다.
    const steps = (state.current && state.current.steps) || [];
    const colspan = 5 + state.lotCount;
    const parts = [];
    state.materials.forEach((m, i) => {
      parts.push(contStepRowsHtml(steps, i, colspan));  // 이 자재 앞(=앞선 자재 i개 뒤) 설명
      const cells = [];
      for (let j = 0; j < state.lotCount; j++) {
        const cell = state.cells[i][j];
        // 셀 placeholder 도 로트별 이론(theoryFor) 기준 — 증량 로트는 큰 값 표시.
        const th = theoryFor(i, j);
        const ph = th == null ? "" : fmt(th);
        const lc = `cont-lc${j % 4}${j === 0 ? " cont-first-lot" : ""}`;
        cells.push(
          `<td class="num cont-cell ${lc}">`
          + `<input class="input cont-actual" data-i="${i}" data-j="${j}" type="number" step="any" min="0" `
          + `value="${esc(cell.actual)}" placeholder="${ph}" />`
          + `<span class="cont-var" data-i="${i}" data-j="${j}">-</span>`
          + `</td>`
        );
      }
      // 자재 행 홀짝 줄무늬(cont-mrow-alt) — 넓은 표에서 같은 원재료 행을 가로로 추적.
      parts.push(`<tr class="cont-mrow${i % 2 ? " cont-mrow-alt" : ""}">`
        + `<td>${i + 1}</td>`
        + `<td class="cont-matname">${esc(m.material_name)}</td>`
        + `<td class="num">${fmt(m.ratio, 2)}</td>`
        + `<td class="num cont-theory" data-i="${i}">${fmt(state.theory[i])}</td>`
        + `<td><input class="input cont-lot" data-i="${i}" value="${esc(state.sharedLot[i])}" placeholder="LOT" /></td>`
        + cells.join("")
        + "</tr>");
    });
    parts.push(contStepRowsHtml(steps, state.materials.length, colspan));  // 마지막 자재 뒤 설명
    body.innerHTML = parts.join("");
    bindCellEvents();
    // 편차 표시 초기화
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) updateCellVar(i, j);
    }
    updateLotPreview();
  }

  function bindCellEvents() {
    const body = $("cont-mat-body");
    body.querySelectorAll(".cont-lot").forEach((el) => {
      el.addEventListener("input", () => { state.sharedLot[Number(el.dataset.i)] = el.value; });
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        focusActual(Number(el.dataset.i), 0);
      });
    });
    body.querySelectorAll(".cont-actual").forEach((el) => {
      const i = Number(el.dataset.i);
      const j = Number(el.dataset.j);
      el.addEventListener("input", () => {
        state.cells[i][j].actual = el.value;
        // 저울 연결 중 손입력 → 경고 + 주황 표시(수기 제한 전 준비 단계, 셀당 1회 토스트)
        if (state.scaleReady) {
          if (!state.cells[i][j].manual) {
            notify("저울 연결 중 — 실제량은 저울 PRINT 키로 입력하세요. 수기 입력은 기록에 표시되며, 앞으로 제한될 예정입니다.", "warn big");
          }
          state.cells[i][j].manual = true;
          el.classList.add("manual-warn");
          el.title = "수기 입력됨 — 저울 PRINT 로 다시 계량하면 해제됩니다";
        }
        updateCellVar(i, j);
      });
      el.addEventListener("change", () => warnIfVariance(i, j));
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        // Enter(완료)로 계량을 마치는 순간에도 즉시 경고 — change(blur)에만 기대지 않는다
        warnIfVariance(i, j);
        focusNextFrom(i, j);
      });
    });
  }

  function focusActual(i, j) {
    const el = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
    if (!el) return false;
    el.focus();
    try { el.select(); } catch (_e) { /* number select 미지원 무시 */ }
    return true;
  }

  // 가로 우선 흐름: 같은 재료의 다음 로트 → 없으면 다음 재료 LOT → 없으면 저장.
  function focusNextFrom(i, j) {
    if (j + 1 < state.lotCount) { focusActual(i, j + 1); return; }
    const nextLot = document.querySelector(`.cont-lot[data-i="${i + 1}"]`);
    if (nextLot) { nextLot.focus(); try { nextLot.select(); } catch (_e) { /* noop */ } return; }
    const save = $("cont-save");
    if (save) save.focus();
  }

  function updateCellVar(i, j) {
    const span = document.querySelector(`.cont-var[data-i="${i}"][data-j="${j}"]`);
    if (!span) return;
    const th = theoryFor(i, j);
    const raw = state.cells[i][j].actual;
    const act = raw === "" ? null : Number(raw);
    if (act === null || th == null) { span.textContent = "-"; span.className = "cont-var"; return; }
    const v = Math.round((act - th) * 1000) / 1000;
    const tol = state.toleranceG;
    // 편차 0(정확히 계량)은 "0.00" 반복 노이즈 대신 옅은 체크로 — 넓은 매트릭스가 차분해진다.
    // 편차가 있으면 부호 포함 숫자(허용 내는 중립색, 초과는 var-up/down 색).
    if (v === 0) {
      span.textContent = "✓";
      span.className = "cont-var cont-var-ok";
    } else {
      span.textContent = (v > 0 ? "+" : "") + fmt(v, 2);
      span.className = "cont-var " + (Math.abs(v) <= tol + 1e-9 ? "" : (v > 0 ? "var-up" : "var-down"));
    }
  }

  function warnIfVariance(i, j) {
    const th = theoryFor(i, j);
    const raw = state.cells[i][j].actual;
    if (raw === "" || th == null) return false;
    const v = Math.round((Number(raw) - th) * 1000) / 1000;
    const tol = state.toleranceG;
    if (Math.abs(v) > tol + 1e-9) {
      notify(`허용 편차 초과: ${state.materials[i].material_name} 로트 ${j + 1} — `
        + `이론 ${fmt(th)} / 실제 ${fmt(Number(raw))} (편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}g > ±${tol}g). 다시 계량하세요.`, "error");
      // 초과(+) 방향일 때만 그 로트 증량 제안 모달을 띄운다 — 부족(-)은 다시 계량해야 한다.
      if (v > 0) offerContRescale(j);
      return true;
    }
    return false;
  }

  // 총량을 나중에 입력/변경하면 이론량이 바뀌어 이미 계량한 셀이 초과될 수 있다 —
  // 확정(change) 시점에 전 셀을 재검사해 바로 알린다. 여럿이면 묶어서 한 번에.
  function warnAllVariance() {
    const tol = state.toleranceG;
    const bad = [];
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        const th = theoryFor(i, j);
        if (th == null) continue;
        const raw = state.cells[i][j].actual;
        if (raw === "") continue;
        if (Math.abs(Number(raw) - th) > tol + 1e-9) {
          bad.push({ i, j });
        }
      }
    }
    if (!bad.length) return;
    if (bad.length === 1) { warnIfVariance(bad[0].i, bad[0].j); return; }
    const names = bad.slice(0, 6).map((b) => `${state.materials[b.i].material_name} 로트 ${b.j + 1}`).join(", ");
    notify(`허용 편차(±${tol}g) 초과: ${names}${bad.length > 6 ? " 외" : ""}. 해당 셀을 다시 계량하세요.`, "error");
  }

  function refreshTheoryCells() {
    document.querySelectorAll("#cont-mat-body .cont-theory").forEach((cell) => {
      const i = Number(cell.dataset.i);
      cell.textContent = fmt(state.theory[i]);
    });
    document.querySelectorAll("#cont-mat-body .cont-actual").forEach((act) => {
      const i = Number(act.dataset.i);
      const j = Number(act.dataset.j);
      const th = theoryFor(i, j);
      act.placeholder = th == null ? "" : fmt(th);
    });
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) updateCellVar(i, j);
    }
  }

  async function updateLotPreview() {
    if (!state.current || state.anchorBlocked) return;
    const product = state.current.recipe.product_name;
    const date = $("cont-date").value || todayISO();
    let baseSeq = null;
    let base = "";
    try {
      const data = await request("/blend/next-lot", { query: { product, date } });
      const lot = String(data.next_lot || "");
      const m = lot.match(/^(.*?)(\d{2})$/);
      if (m) { base = m[1]; baseSeq = Number(m[2]); }
    } catch (_e) { /* 미리보기 실패는 무시 */ }
    document.querySelectorAll(".cont-lot-preview").forEach((el) => {
      const j = Number(el.dataset.j);
      if (baseSeq != null) {
        el.textContent = `${base}${String(baseSeq + j).padStart(2, "0")}`;
      } else {
        el.textContent = "-";
      }
    });
  }

  // ── 로트 수 조절 ────────────────────────────────────────────
  function setLotCount(n) {
    const next = Math.max(MIN_LOTS, Math.min(MAX_LOTS, n));
    if (next === state.lotCount) return;
    state.lotCount = next;
    $("cont-lot-count").textContent = String(next);
    rebuildCells();
    rebuildLotRescale();   // 로트 수 변경 → lotRescale 을 새 lotCount 에 맞춘다(기존 값 보존)
    render();
  }

  // ── 초과 계량 증량(rescale) — 로트별 스코프 ─────────────────
  // 배합 화면(blend.js 91caf17) 의 rescale 통합을 로트 단위로 이식. 차이: 초과가 난
  // '그 로트만' 증량한다(다른 로트 절대 불변). rescalePlan(순수, blend_lib) 으로 newTotal
  // 산출 → 25,000g 초과면 #cont-discard-modal, 아니면 #cont-rescale-modal.
  function offerContRescale(j) {
    // 이미 모달 열려 있거나 보류 제안이 있으면 중복 트리거 방지(Enter/change/총량 변경 경로).
    if (!$("cont-rescale-modal").hidden || !$("cont-discard-modal").hidden) return;
    if (state.pendingContRescale) return;
    const currentTotal = lotTotal(j);
    // rescalePlan 은 items=[{ratio, actual_amount, theory_amount}] 받는다 — 로트 j 의 셀로 구성.
    const items = state.materials.map((m, i) => ({
      ratio: m.ratio,
      actual_amount: state.cells[i][j].actual,
      theory_amount: theoryFor(i, j),
    }));
    const plan = rescalePlan(items, currentTotal, state.toleranceG);
    if (!plan.changed) return;
    state.pendingContRescale = { j, plan };
    if (exceedsBatchLimit(plan.newTotal)) {
      openContDiscardModal(j, plan);
    } else {
      openContRescaleModal(j, plan);
    }
  }

  // 증량 제안 모달 본문(배합 화면 buildRescaleSummary 와 동일 구조, 로트 문구만 추가).
  function buildContRescaleSummary(j, plan) {
    const items = state.materials.map((m, i) => ({
      material_name: m.material_name,
      ratio: m.ratio,
      actual_amount: state.cells[i][j].actual,
    }));
    const overRows = plan.rows
      .filter((r) => r.addNeeded !== null)
      .map((r) => ({ ...r, name: items[r.idx] ? items[r.idx].material_name : "" }));
    let html = "";
    const over = overRows.map((r) => esc(r.name)).join(", ");
    if (over) html += `<p class="rescale-summary">초과 자재(로트 ${j + 1}): ${over}</p>`;
    html += `<div class="rescale-totals">`
      + `<span>총 배합량</span>`
      + `<span class="old">${fmt(lotTotal(j))} g</span>`
      + `<span>→</span>`
      + `<span class="new">${fmt(plan.newTotal)} g</span>`
      + `</div>`;
    if (overRows.length) {
      html += `<table class="rescale-add-table"><thead><tr><th>자재</th>`
        + `<th class="num">현재 실제량</th><th class="num">새 이론량</th>`
        + `<th class="num">추가로 넣을 양</th></tr></thead><tbody>`;
      overRows.forEach((r) => {
        const act = items[r.idx] ? items[r.idx].actual_amount : "";
        html += `<tr><td>${esc(r.name)}</td>`
          + `<td class="num">${fmt(Number(act))}</td>`
          + `<td class="num">${fmt(r.newTheory)}</td>`
          + `<td class="num add-cell">+${fmt(r.addNeeded)}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    return html;
  }

  function openContRescaleModal(j, plan) {
    const title = document.getElementById("cont-rescale-modal-title");
    if (title) title.textContent = `로트 ${j + 1} 배합량 증량`;
    const body = $("cont-rescale-modal-body");
    if (body) body.innerHTML = buildContRescaleSummary(j, plan);
    $("cont-rescale-modal").hidden = false;
  }
  function closeContRescaleModal() { $("cont-rescale-modal").hidden = true; }

  function openContDiscardModal(j, plan) {
    const body = $("cont-discard-modal-body");
    if (body) {
      body.innerHTML = `<p>로트 ${j + 1}: 증량하면 총 배합량이 25,000 g 을 초과합니다 `
        + `(예상 ${fmt(plan.newTotal)} g). 폐기를 권장합니다.</p>`;
    }
    $("cont-discard-modal").hidden = false;
  }
  function closeContDiscardModal() { $("cont-discard-modal").hidden = true; }

  // 증량 적용 — 모달 [증량 적용] 또는 #cont-discard-modal [그래도 증량].
  // 그 로트의 lotRescale[j] 를 newTotal 로 올린다. 다른 로트는 절대 건드리지 않는다.
  function applyContRescale() {
    const pending = state.pendingContRescale;
    if (!pending) return;
    state.pendingContRescale = null;
    closeContRescaleModal();
    closeContDiscardModal();
    const { j, plan } = pending;
    state.lotRescale[j] = plan.newTotal;
    // 그 로트 열의 셀 편차·placeholder·헤더 즉시 재계산. 다른 로트는 불변.
    for (let i = 0; i < state.materials.length; i++) {
      updateCellVar(i, j);
      const inp = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
      if (inp) inp.placeholder = theoryFor(i, j) == null ? "" : fmt(theoryFor(i, j));
    }
    renderContLotHeader(j);
    renderAddBadges(j);
    notify(`로트 ${j + 1} 배합량을 ${fmt(plan.newTotal)} g 으로 증량했습니다 — 추가분을 계량하세요.`, "warn");
  }

  // 헤더 한 칸만 다시 그린다(증량 적용 직후 — 전체 render() 보다 가볍다).
  function renderContLotHeader(j) {
    const cols = document.querySelectorAll("#cont-mat-head th.cont-lot-col");
    const th = cols[j];
    if (!th) return;
    const rescaled = state.lotRescale && state.lotRescale[j];
    const chip = `<span class="cont-lot-chip">로트 ${j + 1}</span>`;
    const totalBadge = rescaled ? ` <small class="cont-lot-total">· ${fmt(rescaled)} g</small>` : "";
    // 기존 cont-lc*/cont-first-lot 클래스는 보존, cont-lot-rescaled 만 토글.
    th.classList.toggle("cont-lot-rescaled", Boolean(rescaled));
    th.innerHTML = `${chip}${totalBadge}<br><small class="cont-lot-preview" data-j="${j}">-</small>`;
    // 로트 번호 미리보기(…01/02)는 updateLotPreview 가 비동기로 다시 채운다.
    updateLotPreview();
  }

  // 그 로트의 계량된 셀 중 잔여 addNeeded>0 인 셀에 '추가 +X g' 배지(클릭 → 인라인 입력).
  // 배합 화면 renderAddBadges(91caf17) 와 동일 UX — 셀 스코프(i,j)로 확장한 것만 다르다.
  function renderAddBadges(j) {
    document.querySelectorAll(`#cont-mat-body .blend-add-badge[data-j="${j}"]`).forEach((el) => el.remove());
    if (j == null) return;
    const tol = state.toleranceG;
    const items = state.materials.map((m, i) => ({
      ratio: m.ratio,
      actual_amount: state.cells[i][j].actual,
      theory_amount: theoryFor(i, j),
    }));
    const plan = rescalePlan(items, lotTotal(j), tol);
    plan.rows.forEach((r) => {
      if (r.addNeeded === null || r.addNeeded <= tol + 1e-9) return;
      const td = document.querySelector(`.cont-var[data-i="${r.idx}"][data-j="${j}"]`);
      if (!td) return;
      const badge = document.createElement("button");
      badge.type = "button";
      badge.className = "blend-add-badge";
      badge.dataset.i = String(r.idx);
      badge.dataset.j = String(j);
      badge.textContent = `추가 +${fmt(r.addNeeded)} g`;
      badge.title = "클릭해서 추가분을 입력하세요 (저울 PRINT 도 추가분으로 합산됩니다)";
      badge.addEventListener("click", () => openAddInline(r.idx, j));
      td.appendChild(badge);
    });
  }

  // 셀 안 인라인 추가분 입력 — 배지를 작은 input 으로 교체. Enter 확정 시 누계 합산.
  // blend.js openAddInline(91caf17) 을 셀 스코프로 이식. 배합 화면에서 잡은 버그 3건 가드:
  //   (a) Enter 확정 후 입력칸 제거 시 blur 재발화 이중 합산 — input._applied 플래그
  //   (b) blur 취소 시 잠금 해제(addModeCell=null, 실제량 readOnly 해제)
  //   (c) 추가 모드 중 누계 입력칸 readOnly(직접 타이핑하면 누계가 통째로 덮어써짐)
  function openAddInline(i, j) {
    const td = document.querySelector(`.cont-var[data-i="${i}"][data-j="${j}"]`);
    if (!td) return;
    const badge = td.querySelector(".blend-add-badge");
    if (badge) badge.remove();
    if (td.querySelector(".blend-add-inline")) return;
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.min = "0";
    input.className = "input blend-add-inline";
    input.dataset.i = String(i);
    input.dataset.j = String(j);
    input.placeholder = "추가분 g";
    input.title = "추가분 입력 후 Enter — 누계로 합산됩니다";
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const add = Number(input.value);
      if (!add || !(add > 0)) { input.focus(); return; }
      // (a) Enter 확정 표시 — 입력칸 제거 시 blur 가 한 번 더 발화해 이중 합산되는 것 차단
      input._applied = true;
      applyAddAmount(i, j, add);
    });
    input.addEventListener("blur", () => {
      if (input._applied) return;
      const add = Number(input.value);
      if (add > 0) { input._applied = true; applyAddAmount(i, j, add); return; }
      // (b) 빈 값으로 벗어나면 취소 — 추가 모드·누계 칸 잠금도 함께 해제해야 한다
      input.remove();
      state.addModeCell = null;
      const actualInput = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
      if (actualInput) {
        actualInput.classList.remove("add-mode");
        actualInput.readOnly = false;
      }
      renderAddBadges(j);
    });
    td.appendChild(input);
    // 이 셀을 추가 입력 모드로 — 저울 PRINT 값이 추가분으로 합산된다.
    // (c) 실제량(누계) 칸은 잠근다: 추가 모드 중 직접 타이핑하면 누계가 통째로 덮어써져
    //     기존 계량값이 사라진다(스모크에서 재현된 실수 경로).
    state.addModeCell = { i, j };
    const actualInput = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
    if (actualInput) {
      actualInput.classList.add("add-mode");
      actualInput.readOnly = true;
    }
    input.focus();
  }

  // 추가분을 셀의 누계(actual) 에 합산하고 UI 갱신. blend.js applyAddAmount(91caf17) 이식.
  function applyAddAmount(i, j, add) {
    const cell = state.cells[i] && state.cells[i][j];
    if (!cell) return;
    const prev = cell.actual === "" ? 0 : (Number(cell.actual) || 0);
    const next = prev + Number(add);
    cell.actual = String(Math.round(next * 1000) / 1000);
    cell.manual = false;
    const input = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
    if (input) {
      input.value = cell.actual;
      input.classList.remove("manual-warn");
    }
    // 인라인 입력칸 제거 + 추가 모드 해제(단일 추가 완료). 잔여 배지는 renderAddBadges 가 갱신.
    const inline = document.querySelector(`.blend-add-inline[data-i="${i}"][data-j="${j}"]`);
    if (inline) inline.remove();
    state.addModeCell = null;
    const actualInput = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
    if (actualInput) {
      actualInput.classList.remove("add-mode");
      actualInput.readOnly = false;
    }
    updateCellVar(i, j);
    warnIfVariance(i, j);
    renderAddBadges(j);
  }

  // ── 저장 ────────────────────────────────────────────────────
  async function save() {
    const err = $("cont-error");
    err.hidden = true;
    if (!state.current) { return fail(err, "레시피를 선택하세요."); }
    if (state.anchorBlocked) { return fail(err, "기준 자재 레시피는 이어서 계량을 지원하지 않습니다."); }
    const worker = lockedWorkerName();
    if (!worker) { return fail(err, "작업자를 입력하세요."); }
    if (!(state.total > 0)) { return fail(err, "총 배합량을 입력하세요."); }

    // 반응기 필수 여부
    const useReactor = Boolean(state.current.recipe && state.current.recipe.use_reactor);
    const reactorRaw = useReactor ? $("cont-reactor").value : "";
    if (useReactor && !reactorRaw) { return fail(err, "반응기를 선택하세요."); }

    // 모든 셀 입력 확인
    const missing = [];
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        if (state.cells[i][j].actual === "" || state.cells[i][j].actual == null) {
          missing.push(`로트 ${j + 1} · ${state.materials[i].material_name}`);
        }
      }
    }
    if (missing.length) {
      return fail(err, `실제량 미입력: ${missing.slice(0, 6).join(", ")}${missing.length > 6 ? " 외" : ""}`);
    }

    // 편차 초과 확인(클라이언트 사전 차단 — 서버도 재검사). 로트별 이론(theoryFor) 기준.
    const tol = state.toleranceG;
    const bad = [];
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        const v = Number(state.cells[i][j].actual) - (theoryFor(i, j) || 0);
        if (Math.abs(v) > tol + 1e-9) bad.push(`로트 ${j + 1} · ${state.materials[i].material_name}(${v > 0 ? "+" : ""}${fmt(v, 2)}g)`);
      }
    }
    if (bad.length) {
      notify(`허용 편차 ±${fmt(tol, 2)}g 초과 — 저장할 수 없습니다.`, "error");
      return fail(err, `허용 편차(±${tol}g) 초과: ${bad.slice(0, 6).join(", ")}${bad.length > 6 ? " 외" : ""}. 해당 셀을 다시 계량하세요.`);
    }

    // 작업자 확인/교대
    if (worker !== state.sessionWorker && !(await switchWorker(worker))) return;
    if (!window.confirm(`작업자 '${state.sessionWorker}' 이름으로 ${state.lotCount}개 로트를 저장합니다. 맞습니까?`)) return;

    const lots = [];
    for (let j = 0; j < state.lotCount; j++) {
      lots.push(state.materials.map((m, i) => ({
        material_id: m.material_id,
        material_name: m.material_name,
        material_code: m.material_code,
        ratio: m.ratio,
        theory_amount: theoryFor(i, j),
        actual_amount: Number(state.cells[i][j].actual),
        material_lot: state.sharedLot[i] || null,
        manual_entry: state.cells[i][j].manual === true,
        sequence_order: i + 1,
      })));
    }
    const body = {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      ink_name: state.current.recipe.ink_name,
      position: state.current.recipe.position,
      work_date: $("cont-date").value || todayISO(),
      work_time: $("cont-time").value || nowTime(),
      total_amount: state.total,
      scale: $("cont-scale").value.trim() || null,
      note: $("cont-note").value.trim() || null,
      reactor: reactorRaw ? Number(reactorRaw) : null,
      worker_sign: state.workerPad ? state.workerPad.dataUrl() : null,
      lots,
    };
    // lotRescale 이 하나라도 있으면 lot_totals 전송(그 로트만 큰 총량).
    // 전부 null 이면 미전송 — 기존 동작(total_amount 만)과 완전 동일(스펙).
    const hasLotRescale = state.lotRescale.some((v) => v && v > 0);
    if (hasLotRescale) {
      body.lot_totals = Array.from({ length: state.lotCount }, (_, j) => lotTotal(j));
    }
    try {
      const res = await request("/blend/records/continuous", { method: "POST", body });
      notify(`${res.created}개 로트 저장 완료: ${(res.product_lots || []).join(", ")} — 배합 기록으로 이동합니다.`, "success");
      setTimeout(() => window.location.assign("/status"), 900);
    } catch (e) {
      fail(err, e.message);
    }
  }

  function fail(err, msg) {
    err.textContent = msg;
    err.hidden = false;
  }

  // ── 바인딩/초기화 ───────────────────────────────────────────
  function bind() {
    const onRecipePick = () => onRecipeChange().catch((e) => notify(e.message, "error"));
    const recipeSel = $("cont-recipe");
    recipeSel.addEventListener("change", onRecipePick);
    recipeSel.addEventListener("focus", () => { loadRecipes().catch(() => {}); });
    const catSel = $("cont-recipe-cat");
    if (catSel) {
      catSel.addEventListener("change", () => { populateRecipeSelect(); });
      catSel.addEventListener("focus", () => { loadRecipes().catch(() => {}); });
    }

    $("cont-total").addEventListener("input", () => {
      state.total = Number($("cont-total").value) || 0;
      recomputeTheory();
      refreshTheoryCells();
    });
    // 총량 확정(change) 시 — 이미 계량된 셀이 새 이론량 기준으로 초과면 즉시 경고
    $("cont-total").addEventListener("change", warnAllVariance);
    // 기본 배합량 버튼 클릭 → 총량에 채우고 이론량 재산출(배합 화면과 동일 경로).
    $("cont-base-links").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".blend-base-link");
      if (!btn) return;
      const base = Number(btn.dataset.value);
      if (!(base > 0)) return;
      const totalInput = $("cont-total");
      totalInput.value = String(base);
      totalInput.dispatchEvent(new Event("input", { bubbles: true }));
      warnAllVariance();  // 이미 계량된 셀이 새 이론량 기준으로 초과면 즉시 경고
    });

    $("cont-worker").addEventListener("focus", () => { $("cont-worker").value = ""; });
    $("cont-worker").addEventListener("change", async () => {
      const name = $("cont-worker").value.trim();
      if (name && name !== state.sessionWorker) {
        if (!(await switchWorker(name))) $("cont-worker").value = state.sessionWorker;
      }
    });
    $("cont-worker").addEventListener("blur", () => {
      if (!$("cont-worker").value.trim()) $("cont-worker").value = state.sessionWorker;
    });

    $("cont-date").addEventListener("change", updateLotPreview);
    $("cont-lot-plus").addEventListener("click", () => setLotCount(state.lotCount + 1));
    $("cont-lot-minus").addEventListener("click", () => setLotCount(state.lotCount - 1));
    $("cont-save").addEventListener("click", () => save());

    const extraToggle = $("cont-extra-toggle");
    if (extraToggle) {
      extraToggle.addEventListener("click", () => {
        const box = $("cont-extra");
        const open = box.hidden;
        box.hidden = !open;
        extraToggle.setAttribute("aria-expanded", String(open));
        extraToggle.textContent = (open ? "▾" : "▸") + " 작업시간 · 저울 변경";
      });
    }

    state.workerPad = attachSignaturePad($("cont-worker-sign"));
    const wclr = $("cont-worker-sign-clear");
    if (wclr && state.workerPad) wclr.addEventListener("click", () => state.workerPad.clear());

    // 증량(rescale) 모달 — hidden 속성 토글로만 열고 닫는다(display 직접 지정 금지).
    const rescaleApply = $("cont-rescale-apply");
    if (rescaleApply) rescaleApply.addEventListener("click", applyContRescale);
    const rescaleCancel = $("cont-rescale-cancel");
    if (rescaleCancel) rescaleCancel.addEventListener("click", () => {
      state.pendingContRescale = null;
      closeContRescaleModal();
    });
    const discardForce = $("cont-discard-force");
    if (discardForce) discardForce.addEventListener("click", applyContRescale);
    const discardCancel = $("cont-discard-cancel");
    if (discardCancel) discardCancel.addEventListener("click", () => {
      state.pendingContRescale = null;
      closeContDiscardModal();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    state.sessionWorker = lockedWorkerName();
    $("cont-date").value = todayISO();
    $("cont-time").value = nowTime();
    $("cont-lot-count").textContent = String(state.lotCount);
    rebuildLotRescale();   // lotRescale 을 초기 lotCount(2) 에 맞춰 [null,null] 로 초기화
    bind();
    loadRecipes().catch((e) => notify(`레시피 로드 실패: ${e.message}`, "error"));
    loadWorkerNames();
    detectScale();
    setInterval(detectScale, 30000);
    setInterval(pollScaleEvents, 800);
  });
})();

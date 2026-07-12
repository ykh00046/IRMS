/**
 * blend.js — 배합 실적 컨트롤러.
 *
 * 입력: 레시피 선택 → 총 배합량 → 비율/이론량 자동 → 실제량·자재LOT 입력 → 저장
 * 조회: 기간/작업자/검색 필터 → 목록 → 상세(DHR) → 인쇄/취소
 * CSRF·인증 리다이렉트는 IRMS._core.request 가 처리. 무로그인 개방 화면.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (m) { console.log(m); };

  // 순수 헬퍼 라이브러리 — 포맷터/HTML 빌더/수치 계산. 동일 이름으로 분해 할당하여
  // 기존 호출부를 그대로 유지한다.
  const {
    esc,
    TOLERANCE_G,
    fmt,
    todayISO,
    nowTime,
    rowVariance,
    baseTotalValues,
    materialRowHtml,
    baseTotalLinksHtml,
    bulkRowHtml,
    computeTotals,
    computeTheoryAmount,
    varianceDisplay,
    varianceWarnMessage,
    badVarianceNames,
    varianceBlockMessage,
    option,
    stepRowsHtml,
    lotFallbackText,
    recipeOptionsHtml,
    loadFailOptionHtml,
  } = window.IRMS.blendLib;

  const $ = (id) => document.getElementById(id);

  const state = { recipes: [], current: null, items: [], detailId: null, viscProducts: [], lotMap: {}, workers: [], scaleReady: false, sessionWorker: "" };

  // ── 저울 에이전트(현장 PC의 127.0.0.1:8787, scale_agent/) ────────
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

  // 수동 입력 표시(조용히, 자재 행 단위): 저울이 연결된 상태에서 실제량을 손으로
  //   입력하면 그 자재 행을 '수동 입력'으로 기록만 남긴다(작업자에겐 잠금·경고 없음).
  //   저울 값은 fillScaleValue 가 input 이벤트 없이 채우므로 손입력만 감지되고,
  //   손입력 후 저울로 다시 계량하면 그 행의 수동 표시는 해제된다.
  //   기록 상세에서 나중에 행별 ⚠ 로 확인. 저울 없으면(수동이 정상) 표시 안 함.

  // 저울 값을 idx 행 실제량에 채우고, 수동 Enter 와 동일하게 진행:
  // 다음 행 LOT 로 포커스, 마지막 자재였으면 저장 버튼으로.
  function fillScaleValue(idx, value) {
    const input = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (!input) return;
    input.value = String(value);
    state.items[idx].actual_amount = input.value;
    state.items[idx].manual = false;  // 저울 입력 — 손입력 표시 해제
    updateRowVar(idx);
    updateTotals();
    warnIfVariance(idx);
    const nextLot = document.querySelector(`.blend-lot[data-idx="${idx + 1}"]`);
    if (nextLot) {
      nextLot.focus();
    } else {
      const save = $("blend-save");
      if (save) save.focus();
    }
  }

  // PRINT 키 입력이 들어갈 행: 커서가 있는 행(LOT/실제량) 우선, 없으면 첫 미입력 행
  function activeScaleRow() {
    const focused = document.activeElement;
    if (
      focused && focused.classList
      && (focused.classList.contains("blend-actual") || focused.classList.contains("blend-lot"))
    ) {
      return Number(focused.dataset.idx);
    }
    const idx = state.items.findIndex(
      (it) => it.actual_amount === "" && it.theory_amount != null
    );
    return idx >= 0 ? idx : null;
  }

  // ── 저울 PRINT 키 연동: 에이전트 이벤트 폴링 → 활성 행 자동 입력 ──
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
      // 첫 동기화: 페이지 열기 전 눌렀던 PRINT 잔여 이벤트는 무시
      if (!scaleEventSynced) { scaleEventSynced = true; return; }
      if (!items.length || !state.items.length) return;
      for (const ev of items) {
        const idx = activeScaleRow();
        if (idx === null) {
          notify("모든 자재의 실제량이 입력되어 있습니다. (PRINT 무시)", "warn");
          break;
        }
        fillScaleValue(idx, ev.value);
        const src = ev.source ? `[${ev.source}] ` : "";
        notify(`${src}저울 입력: ${state.items[idx].material_name} = ${ev.value} g`, "success");
      }
    } catch (_e) { /* 폴링 실패는 조용히 — detectScale 이 상태 회복 */ }
  }

  function lockedWorkerName() {
    const worker = $("blend-worker");
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

  // ── 작업자 교대 — 작업자 칸에서 이름을 고르면 로그아웃 없이 세션 전환 ──
  // 공용 단말에서 교대 시 앞사람 이름으로 기록되는 오귀속 방지. 등록된 이름은
  // 즉시 교대, 처음 보는 이름은 등록 확인 후 교대.
  async function switchWorker(name) {
    const clean = (name || "").trim();
    if (!clean) return false;
    if (clean === state.sessionWorker) return true;
    if (!state.workers.includes(clean)) {
      if (!window.confirm(`처음 보는 이름입니다: "${clean}"
작업자로 등록하고 교대할까요?`)) return false;
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
      $("blend-worker").value = clean;
      if ($("bulk-worker")) $("bulk-worker").value = clean;
      notify(`작업자 교대: ${clean}`, "success");
      return true;
    } catch (e) {
      notify(`작업자 교대 실패: ${e.message}`, "error");
      return false;
    }
  }

  // 처음 보는 이름이면 등록 확인. 등록 거부 시 false 반환(저장 중단).
  async function ensureWorker(name) {
    const clean = (name || "").trim();
    if (!clean) return false;
    if (state.workers.includes(clean)) return true;
    if (!window.confirm(`처음 보는 이름입니다: "${clean}"\n작업자로 등록할까요?`)) return false;
    try {
      await request("/workers", { method: "POST", body: { name: clean } });
      state.workers.push(clean);
      const dl = $("worker-names");
      if (dl) dl.insertAdjacentHTML("beforeend", `<option value="${esc(clean)}"></option>`);
      return true;
    } catch (e) { notify(`작업자 등록 실패: ${e.message}`, "error"); return false; }
  }

  // ── 전자서명 패드 (마우스/터치로 직접 그림) ──────────────────
  function attachSignaturePad(canvas) {
    if (!canvas || canvas._padAttached) return canvas && canvas._pad;
    const ctx2 = canvas.getContext("2d");
    ctx2.lineWidth = 2; ctx2.lineCap = "round"; ctx2.strokeStyle = "#111";
    let drawing = false, dirty = false;
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    };
    const start = (e) => { drawing = true; const p = pos(e); ctx2.beginPath(); ctx2.moveTo(p.x, p.y); e.preventDefault(); };
    const move = (e) => { if (!drawing) return; const p = pos(e); ctx2.lineTo(p.x, p.y); ctx2.stroke(); dirty = true; e.preventDefault(); };
    const end = () => { drawing = false; };
    canvas.addEventListener("mousedown", start); canvas.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    canvas.addEventListener("touchstart", start); canvas.addEventListener("touchmove", move);
    canvas.addEventListener("touchend", end);
    const pad = {
      clear() { ctx2.clearRect(0, 0, canvas.width, canvas.height); dirty = false; },
      isEmpty() { return !dirty; },
      dataUrl() { return dirty ? canvas.toDataURL("image/png") : null; },
    };
    canvas._padAttached = true; canvas._pad = pad;
    return pad;
  }

  // ── 모드 전환 ──────────────────────────────────────────────
  function setMode(mode) {
    // /blend = 배합 입력, /blend/bulk = 일괄 생성. 기록 조회는 '배합 기록'(/status) 메뉴로 일원화.
    $("blend-entry-mode").hidden = mode !== "entry";
    $("blend-bulk-mode").hidden = mode !== "bulk";
    if (mode === "bulk") initBulk();
  }

  // ── 일괄 생성 ──────────────────────────────────────────────
  function initBulk() {
    fillBulkRecipes();
    if (!$("bulk-body").children.length) addBulkRow();
    const bulkWorker = $("bulk-worker");
    const worker = lockedWorkerName();
    if (bulkWorker && worker) bulkWorker.value = worker;
    const dhrToggle = $("bulk-dhr");
    if (dhrToggle && !dhrToggle._bound) {
      dhrToggle._bound = true;
      dhrToggle.addEventListener("change", fillBulkRecipes);
    }
  }

  async function fillBulkRecipes() {
    // DHR 전용 체크 시 DHR 전용 레시피, 아니면 일반 레시피 — 배합일지 2종류 소스 분리.
    const sel = $("bulk-recipe");
    const dhr = $("bulk-dhr") && $("bulk-dhr").checked;
    try {
      const d = await request(`/blend/recipes${dhr ? "?dhr=1" : ""}`);
      sel.innerHTML = recipeOptionsHtml(d.items || [], dhr);
    } catch (e) {
      sel.innerHTML = loadFailOptionHtml();
    }
  }

  function addBulkRow() {
    const tr = document.createElement("tr");
    tr.innerHTML = bulkRowHtml();
    tr.querySelector(".bulk-del").addEventListener("click", () => tr.remove());
    $("bulk-body").appendChild(tr);
  }

  async function createBulk() {
    const err = $("bulk-error");
    err.hidden = true;
    const recipe_id = Number($("bulk-recipe").value);
    const worker = lockedWorkerName() || $("bulk-worker").value.trim();
    if (!recipe_id) { err.textContent = "레시피를 선택하세요."; err.hidden = false; return; }
    if (!worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!(await ensureWorker(worker))) return;
    const entries = [];
    $("bulk-body").querySelectorAll("tr").forEach((tr) => {
      const d = tr.querySelector(".bulk-date").value;
      const t = Number(tr.querySelector(".bulk-total").value);
      if (d && t > 0) entries.push({ work_date: d, total_amount: t });
    });
    if (!entries.length) { err.textContent = "유효한 작업일·총량 행을 입력하세요."; err.hidden = false; return; }
    try {
      const res = await request("/blend/records/bulk", {
        method: "POST",
        body: { recipe_id, worker, scale: $("bulk-scale").value.trim() || null,
                entries },
      });
      notify(`${res.created}건 일괄 생성 완료 — 배합 기록으로 이동합니다.`, "success");
      $("bulk-body").innerHTML = "";
      addBulkRow();
      setTimeout(() => window.location.assign("/status"), 800);
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  }

  // ── 배합 입력 ──────────────────────────────────────────────
  async function loadRecipes() {
    const data = await request("/blend/recipes");
    state.recipes = data.items || [];
    // 레시피는 타이핑 필터(datalist): 'sb' 입력 → 후보 좁혀짐 → 선택 시 로드.
    // 주의: 내용이 같으면 datalist 를 다시 그리지 않는다 — 포커스 재조회 때 재구성하면
    // 열린 드롭다운이 즉시 닫히는 증상(여러 번 클릭해야 열림)이 생긴다.
    const dl = $("recipe-names");
    if (dl) {
      const html = state.recipes.map((r) => `<option value="${esc(r.product_name)}"></option>`).join("");
      if (dl.innerHTML !== html) dl.innerHTML = html;
    }
  }

  // 입력한 레시피명(대소문자 무시, 정확 일치)을 레시피 id 로 해석. 부분 입력은 미선택.
  function selectedRecipeId() {
    const name = $("blend-recipe").value.trim().toLowerCase();
    if (!name) return "";
    const hit = state.recipes.find((r) => String(r.product_name || "").trim().toLowerCase() === name);
    return hit ? String(hit.id) : "";
  }

  async function onRecipeChange() {
    const id = selectedRecipeId();
    // 미해석(검색 타이핑 중/비움)은 무시 — 현재 레시피와 입력값을 지우지 않는다.
    if (!id) return;
    // 같은 레시피 재선택은 무시 — 입력 중인 값(실제량·LOT 등)을 보존.
    const prevId = state.current && state.current.recipe ? String(state.current.recipe.id) : "";
    if (id === prevId) return;
    const data = await request(`/blend/recipes/${id}`);
    state.current = data;
    state.items = data.items.map((it) => ({ ...it, actual_amount: "", material_lot: "" }));
    // 레시피가 바뀌면 이전 레시피의 입력을 모두 초기화 — 총량·비고·서명·반응기가
    // 새 레시피에 섞여 들어가는 것을 방지. 총량은 다시 입력(또는 기준량 버튼).
    $("blend-total").value = "";
    $("blend-note").value = "";
    $("blend-reactor").value = "";
    if (state.workerPad) state.workerPad.clear();
    state.items.forEach((it) => { it.theory_amount = null; });
    renderMatRows();
    renderReactorField();
    renderBaseTotalButton();
    updateLotPreview();
    updateInputGuide();
  }

  // '기준량' 버튼(최대 3개) — 레시피 관리에서 기준 배합량을 지정한 레시피에서만 노출.
  // (미지정 레시피는 버튼 없음 — 총량은 직접 입력)
  function renderBaseTotalButton() {
    const wrap = $("blend-base-links");
    if (!wrap) return;
    const values = baseTotalValues(state.current);
    if (!values.length) { wrap.hidden = true; wrap.innerHTML = ""; return; }
    wrap.innerHTML = baseTotalLinksHtml(values);
    wrap.hidden = false;
  }

  // 반응기 진행 반제품(레시피)일 때만 배합 설정에 반응기 선택을 노출한다.
  function renderReactorField() {
    const field = $("blend-reactor-field");
    if (!field) return;
    const use = Boolean(state.current && state.current.recipe && state.current.recipe.use_reactor);
    field.hidden = !use;
    if (!use) $("blend-reactor").value = "";
  }

  function recomputeTheory() {
    const total = Number($("blend-total").value) || 0;
    state.items.forEach((it) => {
      it.theory_amount = computeTheoryAmount(it.ratio, total);
    });
  }

  // 순차 입력 안내: 총 배합량(공백) 강조 → 입력되면 작업자 강조
  function updateInputGuide() {
    const total = $("blend-total");
    const worker = $("blend-worker");
    const totalReady = Number(total.value) > 0;
    total.classList.toggle("needs-input", !totalReady);
    worker.classList.toggle("needs-input", totalReady && !worker.value.trim());
  }

  function renderMatRows() {
    const body = $("blend-mat-body");
    body.innerHTML = "";
    if (!state.items.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted">레시피를 선택하세요.</td></tr>';
      updateTotals();
      return;
    }
    // 공정 설명 줄(레시피 '설명' 열) — 해당 위치에 전폭 안내 행으로 삽입
    const steps = (state.current && state.current.steps) || [];
    state.items.forEach((it, idx) => {
      body.insertAdjacentHTML("beforeend", stepRowsHtml(steps, idx));  // 이 자재 앞(=앞선 자재 idx개 뒤)의 설명
      const tr = document.createElement("tr");
      tr.innerHTML = materialRowHtml(idx, it);
      body.appendChild(tr);
    });
    body.insertAdjacentHTML("beforeend", stepRowsHtml(steps, state.items.length));  // 마지막 자재 뒤 설명
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("input", () => {
        const i = Number(el.dataset.idx);
        state.items[i].actual_amount = el.value;
        // 저울 연결 중 손입력 → 이 자재 행을 조용히 '수동 입력'으로 표시
        if (state.scaleReady) state.items[i].manual = true;
        updateRowVar(i);
        updateTotals();
      })
    );
    // 실제량 입력 완료(blur) 시 허용 편차(±0.05g) 초과면 경고
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("change", () => warnIfVariance(Number(el.dataset.idx)))
    );
    body.querySelectorAll(".blend-lot").forEach((el) =>
      el.addEventListener("input", () => { state.items[Number(el.dataset.idx)].material_lot = el.value; })
    );
    // 키보드 흐름(LOT 먼저): LOT Enter → 같은 행 실제량, 실제량 Enter → 다음 품목 LOT(마지막이면 저장)
    const focusField = (selector) => {
      const t = body.querySelector(selector);
      if (!t) return false;
      t.focus();
      if (typeof t.select === "function") {
        try { t.select(); } catch (_e) { /* number input select 미지원 무시 */ }
      }
      return true;
    };
    body.querySelectorAll(".blend-lot").forEach((el) =>
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        focusField(`.blend-actual[data-idx="${el.dataset.idx}"]`);
      })
    );
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        const next = Number(el.dataset.idx) + 1;
        if (!focusField(`.blend-lot[data-idx="${next}"]`)) {
          const save = document.getElementById("blend-save");
          if (save) save.focus();
        }
      })
    );
    updateTotals();
  }

  function updateRowVar(i) {
    const it = state.items[i];
    const cell = document.querySelector(`.blend-var[data-idx="${i}"]`);
    if (!cell) return;
    const display = varianceDisplay(it);
    cell.textContent = display.text;
    cell.className = display.className;
  }

  function warnIfVariance(i) {
    const it = state.items[i];
    const v = rowVariance(it);
    if (Math.abs(v) > TOLERANCE_G + 1e-9) {
      notify(varianceWarnMessage(it, v), "error");
      return true;
    }
    return false;
  }

  function updateTotals() {
    const { theory, actual, net } = computeTotals(state.items);
    $("blend-theory-total").textContent = state.items.length ? fmt(theory) : "-";
    $("blend-actual-total").textContent = state.items.length ? fmt(actual) : "-";
    const nv = $("blend-net-var");
    nv.textContent = state.items.length ? (net > 0 ? "+" : "") + fmt(net, 2) : "-";
  }

  async function updateLotPreview() {
    const el = $("blend-lot-preview");
    if (!state.current) { el.textContent = "-"; return; }
    const product = state.current.recipe.product_name;
    const date = $("blend-date").value || todayISO();
    // 저장 시 부여될 실제 순번을 서버에서 받아 표시(리터럴 NN 금지).
    try {
      const data = await request("/blend/next-lot", { query: { product, date } });
      el.textContent = data.next_lot;
    } catch (_e) {
      // 조회 실패 시에도 가짜 NN 은 쓰지 않고 순번 없는 베이스만 표시.
      el.textContent = lotFallbackText(product, date);
    }
  }

  async function saveBlend() {
    const err = $("blend-error");
    err.hidden = true;
    if (!state.current) { err.textContent = "레시피를 선택하세요."; err.hidden = false; return; }
    const worker = lockedWorkerName();
    const total = Number($("blend-total").value);
    if (!worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!(total > 0)) { err.textContent = "총 배합량을 입력하세요."; err.hidden = false; return; }
    // 자재별 허용 편차 ±0.05g — 초과 자재가 있으면 저장 차단(합계 편차는 제한 없음)
    const bad = state.items.filter((it) => Math.abs(rowVariance(it)) > TOLERANCE_G + 1e-9);
    if (bad.length) {
      err.textContent = varianceBlockMessage(badVarianceNames(bad));
      err.hidden = false;
      notify(`허용 편차 ±${TOLERANCE_G}g 초과 — 저장할 수 없습니다.`, "error");
      return;
    }
    // 반응기 진행 반제품은 반응기(1~4) 지정 필수.
    const useReactor = Boolean(state.current.recipe && state.current.recipe.use_reactor);
    const reactorRaw = useReactor ? $("blend-reactor").value : "";
    if (useReactor && !reactorRaw) {
      err.textContent = "반응기를 선택하세요."; err.hidden = false;
      notify("반응기를 선택하세요.", "error");
      return;
    }
    // 작업자 칸이 세션과 다르면 먼저 교대(오귀속 방지) — 실패 시 저장 중단
    if (worker !== state.sessionWorker && !(await switchWorker(worker))) return;
    // 저장 직전 작업자 확인 — 교대 잊고 앞사람 이름으로 저장되는 것 차단
    if (!window.confirm(`작업자 '${state.sessionWorker}' 이름으로 저장합니다. 맞습니까?`)) return;
    const body = {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      ink_name: state.current.recipe.ink_name,
      position: state.current.recipe.position,
      worker,
      work_date: $("blend-date").value || todayISO(),
      work_time: $("blend-time").value || nowTime(),
      total_amount: total,
      scale: $("blend-scale").value.trim() || null,
      note: $("blend-note").value.trim() || null,
      reactor: reactorRaw ? Number(reactorRaw) : null,
      worker_sign: state.workerPad ? state.workerPad.dataUrl() : null,
      // 저울 연결 중 손입력 행이 하나라도 있으면 배치를 '수동 입력'으로 기록
      manual_entry: state.items.some((it) => it.manual === true),
      details: state.items.map((it, idx) => ({
        material_id: it.material_id,
        material_name: it.material_name,
        material_code: it.material_code,
        ratio: it.ratio,
        theory_amount: it.theory_amount,
        actual_amount: it.actual_amount === "" ? null : Number(it.actual_amount),
        material_lot: it.material_lot || null,
        sequence_order: idx + 1,
        manual_entry: it.manual === true,
      })),
    };
    try {
      const rec = await request("/blend/records", { method: "POST", body });
      notify(`배합 실적 저장: ${rec.product_lot} (작업자: ${rec.worker})`, "success");
      // 실제량/LOT 초기화 (연속 작업 편의)
      state.items.forEach((it) => { it.actual_amount = ""; it.material_lot = ""; it.manual = false; });
      if (state.workerPad) state.workerPad.clear();
      renderMatRows();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  function bind() {
    const onRecipePick = () => onRecipeChange().catch((e) => notify(e.message, "error"));
    const recipeInput = $("blend-recipe");
    recipeInput.addEventListener("input", onRecipePick);
    recipeInput.addEventListener("change", onRecipePick);
    // 포커스 시 비움 → 이미 선택된 이름으로 datalist 가 필터되지 않고 전체 목록 표시.
    // 선택 없이 나가면 현재 레시피명으로 원복(선택 유지).
    // 목록도 재조회 — 화면을 계속 띄워두는 단말에서 레시피 수정(개정)이 반영되도록.
    recipeInput.addEventListener("focus", () => {
      recipeInput.value = "";
      loadRecipes().catch(() => {});
    });
    recipeInput.addEventListener("blur", () => {
      if (selectedRecipeId()) return;
      recipeInput.value = state.current ? state.current.recipe.product_name : "";
    });
    $("blend-base-links").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".blend-base-link");
      if (!btn) return;
      const base = Number(btn.dataset.value);
      if (!(base > 0)) return;
      const totalInput = $("blend-total");
      totalInput.value = String(base);
      totalInput.dispatchEvent(new Event("input"));  // 이론량 재계산 경로 재사용
    });
    $("blend-total").addEventListener("input", () => {
      recomputeTheory();
      state.items.forEach((_, i) => updateRowVar(i));
      // 이론량 셀 + 실제량 입력칸 안내값 갱신 — data-idx 기준(설명 줄이 끼어도 안전)
      document.querySelectorAll("#blend-mat-body .blend-theory").forEach((cell) => {
        const it = state.items[Number(cell.dataset.idx)];
        if (it) cell.textContent = fmt(it.theory_amount);
      });
      document.querySelectorAll("#blend-mat-body .blend-actual").forEach((act) => {
        const it = state.items[Number(act.dataset.idx)];
        if (it) act.placeholder = it.theory_amount == null ? "" : fmt(it.theory_amount);
      });
      updateTotals();
      updateLotPreview();
      updateInputGuide();
    });
    $("blend-worker").addEventListener("input", updateInputGuide);
    // 교대: 포커스 시 비워 전체 명단 표시(레시피 칸과 동일 UX), 선택/확정 시 세션 전환
    $("blend-worker").addEventListener("focus", () => { $("blend-worker").value = ""; });
    $("blend-worker").addEventListener("change", async () => {
      const name = $("blend-worker").value.trim();
      if (name && name !== state.sessionWorker) {
        if (!(await switchWorker(name))) $("blend-worker").value = state.sessionWorker;
      }
    });
    $("blend-worker").addEventListener("blur", () => {
      if (!$("blend-worker").value.trim()) $("blend-worker").value = state.sessionWorker;
      updateInputGuide();
    });
    const extraToggle = $("blend-extra-toggle");
    if (extraToggle) {
      extraToggle.addEventListener("click", () => {
        const box = $("blend-extra");
        const open = box.hidden;
        box.hidden = !open;
        extraToggle.setAttribute("aria-expanded", String(open));
        extraToggle.textContent = (open ? "▾" : "▸") + " 작업시간 · 저울 변경";
      });
    }
    $("blend-date").addEventListener("change", updateLotPreview);
    $("blend-save").addEventListener("click", () => saveBlend());
    state.workerPad = attachSignaturePad($("blend-worker-sign"));
    const wclr = $("blend-worker-sign-clear");
    if (wclr && state.workerPad) wclr.addEventListener("click", () => state.workerPad.clear());
    $("bulk-add-row").addEventListener("click", addBulkRow);
    $("bulk-create").addEventListener("click", createBulk);
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    state.sessionWorker = lockedWorkerName();
    $("blend-date").value = todayISO();
    $("blend-time").value = nowTime();
    if ($("bulk-worker") && lockedWorkerName()) $("bulk-worker").value = lockedWorkerName();
    bind();
    // 경로로 모드 결정: /blend/bulk = 일괄 생성, 그 외 = 배합 입력
    setMode(location.pathname.replace(/\/+$/, "").endsWith("/bulk") ? "bulk" : "entry");
    updateInputGuide();
    loadRecipes().catch((e) => notify(`레시피 로드 실패: ${e.message}`, "error"));
    loadWorkerNames();
    // 저울 에이전트 감지(있으면 각 행에 [저울] 버튼 노출). 30초마다 재확인.
    detectScale();
    setInterval(detectScale, 30000);
    // 저울 PRINT 키 이벤트 폴링(0.8초) — 누르면 활성 행 실제량 자동 입력.
    setInterval(pollScaleEvents, 800);
    // 작업자 세션 하트비트는 전 화면 공통(common.js)으로 이동 — 배합↔점도↔기록
    // 어디에 있든 세션이 유지된다.
    request("/viscosity/products")
      .then((d) => { state.viscProducts = (d.items || []).filter((p) => p.is_active); })
      .catch(() => {});
  });
})();

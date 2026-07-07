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
  const esc = IRMS.escapeHtml || function (value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };
  const $ = (id) => document.getElementById(id);

  const state = { recipes: [], current: null, items: [], detailId: null, viscProducts: [], lotMap: {}, workers: [], scaleReady: false };

  // 자재별 계량 허용 편차(g). 저울 실측 연동 기준 — 서버(blend_service)와 동일 값.
  const TOLERANCE_G = 0.05;

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

  // 저울 값을 idx 행 실제량에 채우고, 수동 Enter 와 동일하게 진행:
  // 다음 행 LOT 로 포커스, 마지막 자재였으면 저장 버튼으로.
  function fillScaleValue(idx, value) {
    const input = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (!input) return;
    input.value = String(value);
    state.items[idx].actual_amount = input.value;
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

  function fmt(v, d) {
    if (v === null || v === undefined || v === "") return "-";
    // 기본 소수 2자리 — 저울(XP 0.01g) 해상도에 맞춤
    return Number(v).toFixed(d === undefined ? 2 : d);
  }
  function todayISO() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function nowTime() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
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
      const items = d.items || [];
      sel.innerHTML = "";
      const ph = document.createElement("option");
      ph.value = "";
      ph.textContent = items.length ? "레시피 선택…" : (dhr ? "DHR 전용 레시피가 없습니다" : "레시피가 없습니다");
      sel.appendChild(ph);
      items.forEach((r) => {
        const o = document.createElement("option");
        o.value = String(r.id);
        o.textContent = r.product_name;
        sel.appendChild(o);
      });
    } catch (e) {
      sel.innerHTML = '<option value="">로드 실패</option>';
    }
  }

  function addBulkRow() {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><input class="input bulk-date" type="date" value="${todayISO()}" /></td>` +
      `<td class="num"><input class="input bulk-total" type="number" step="0.1" min="0" /></td>` +
      `<td><button class="btn btn-sm bulk-del" type="button">삭제</button></td>`;
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
    const dl = $("recipe-names");
    if (dl) dl.innerHTML = state.recipes.map((r) => `<option value="${esc(r.product_name)}"></option>`).join("");
    $("blend-recipe").value = "";
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
    // 입력 이벤트가 연속으로 와도 선택이 실제로 바뀌었을 때만 반응(중복 API 호출 방지).
    if (id === state._lastRecipeId) return;
    state._lastRecipeId = id;
    const totalRaw = Number($("blend-total").value);
    const query = totalRaw > 0 ? { total: totalRaw } : {};
    const data = await request(`/blend/recipes/${id}`, { query });
    state.current = data;
    state.items = data.items.map((it) => ({ ...it, actual_amount: "", material_lot: "" }));
    // 총 배합량은 비워 둠(입력해야 하는 값임을 인지). 비어 있으면 이론량은 "-"로.
    if (!(totalRaw > 0)) state.items.forEach((it) => { it.theory_amount = null; });
    renderMatRows();
    renderReactorField();
    renderBaseTotalButton();
    updateLotPreview();
    updateInputGuide();
  }

  // '기준량' 버튼 — 레시피 관리에서 기준 배합량을 지정한 레시피에서만 노출.
  // (미지정 레시피는 버튼 없음 — 총량은 직접 입력)
  function baseTotalValue() {
    if (!state.current) return 0;
    return Number(state.current.default_total) || 0;
  }

  function renderBaseTotalButton() {
    const btn = $("blend-total-base");
    if (!btn) return;
    const base = baseTotalValue();
    if (!(base > 0)) { btn.hidden = true; return; }
    btn.textContent = `기준량 ${fmt(base)} g 적용`;
    btn.hidden = false;
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
      // 이론량을 저울/표시 단위(0.01g)로 반올림. 표시값=내부값이라 표시된 이론값을
      // 그대로 계량하면 편차 0. 허용 편차(±0.05g) 판정과도 같은 눈금.
      it.theory_amount = Math.round((it.ratio / 100) * total * 100) / 100;
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
    state.items.forEach((it, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${idx + 1}</td>` +
        `<td>${esc(it.material_name)}</td>` +
        `<td class="num">${fmt(it.ratio, 2)}</td>` +
        `<td class="num blend-theory">${fmt(it.theory_amount)}</td>` +
        `<td><input class="input blend-lot" data-idx="${idx}" value="${esc(it.material_lot)}" placeholder="LOT" /></td>` +
        `<td class="num"><input class="input blend-actual" data-idx="${idx}" type="number" step="any" min="0" value="${esc(it.actual_amount)}" placeholder="${it.theory_amount == null ? "" : fmt(it.theory_amount)}" /></td>` +
        `<td class="num blend-var" data-idx="${idx}">-</td>`;
      body.appendChild(tr);
    });
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("input", () => {
        const i = Number(el.dataset.idx);
        state.items[i].actual_amount = el.value;
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
    const actual = it.actual_amount === "" ? null : Number(it.actual_amount);
    if (actual === null || it.theory_amount === null) { cell.textContent = "-"; cell.className = "num blend-var"; return; }
    const v = Math.round((actual - it.theory_amount) * 1000) / 1000;
    cell.textContent = (v > 0 ? "+" : "") + fmt(v, 2);
    // 허용 편차(±0.05g) 이내면 정상 표시, 초과 시에만 색으로 경고
    cell.className = "num blend-var " + (Math.abs(v) <= TOLERANCE_G + 1e-9 ? "" : v > 0 ? "var-up" : "var-down");
  }

  function rowVariance(it) {
    if (!it || it.actual_amount === "" || it.theory_amount == null) return 0;
    return Math.round((Number(it.actual_amount) - it.theory_amount) * 1000) / 1000;
  }

  function warnIfVariance(i) {
    const it = state.items[i];
    const v = rowVariance(it);
    if (Math.abs(v) > TOLERANCE_G + 1e-9) {
      notify(
        `허용 편차 초과: ${it.material_name} — 이론 ${fmt(it.theory_amount)} / 실제 ${fmt(it.actual_amount)} `
        + `(편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}g > ±${TOLERANCE_G}g). 다시 계량하세요.`,
        "error",
      );
      return true;
    }
    return false;
  }

  function updateTotals() {
    const theory = state.items.reduce((s, it) => s + (it.theory_amount || 0), 0);
    const actual = state.items.reduce((s, it) => s + (it.actual_amount === "" ? 0 : Number(it.actual_amount) || 0), 0);
    $("blend-theory-total").textContent = state.items.length ? fmt(theory) : "-";
    $("blend-actual-total").textContent = state.items.length ? fmt(actual) : "-";
    const net = actual - theory;
    const nv = $("blend-net-var");
    nv.textContent = state.items.length ? (net > 0 ? "+" : "") + fmt(net, 2) : "-";
  }

  async function updateLotPreview() {
    const el = $("blend-lot-preview");
    if (!state.current) { el.textContent = "-"; return; }
    const product = state.current.recipe.product_name;
    const date = $("blend-date").value || todayISO();
    const yymmdd = date.replace(/-/g, "").slice(2, 8);
    // 저장 시 부여될 실제 순번을 서버에서 받아 표시(리터럴 NN 금지).
    try {
      const data = await request("/blend/next-lot", { query: { product, date } });
      el.textContent = data.next_lot;
    } catch (_e) {
      // 조회 실패 시에도 가짜 NN 은 쓰지 않고 순번 없는 베이스만 표시.
      el.textContent = `${product}${yymmdd}`;
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
      const names = bad.map((it) => `${it.material_name}(${rowVariance(it) > 0 ? "+" : ""}${fmt(rowVariance(it), 2)}g)`).join(", ");
      err.textContent = `허용 편차(±${TOLERANCE_G}g)를 초과해 저장할 수 없습니다: ${names}. 해당 자재를 다시 계량하세요.`;
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
    if (!(await ensureWorker(worker))) return;
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
      details: state.items.map((it, idx) => ({
        material_id: it.material_id,
        material_name: it.material_name,
        material_code: it.material_code,
        ratio: it.ratio,
        theory_amount: it.theory_amount,
        actual_amount: it.actual_amount === "" ? null : Number(it.actual_amount),
        material_lot: it.material_lot || null,
        sequence_order: idx + 1,
      })),
    };
    try {
      const rec = await request("/blend/records", { method: "POST", body });
      notify(`배합 실적 저장: ${rec.product_lot}`, "success");
      // 실제량/LOT 초기화 (연속 작업 편의)
      state.items.forEach((it) => { it.actual_amount = ""; it.material_lot = ""; });
      if (state.workerPad) state.workerPad.clear();
      renderMatRows();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  // ── 기록 조회 ──────────────────────────────────────────────
  async function loadWorkers() {
    try {
      const data = await request("/blend/workers");
      const sel = $("rec-worker");
      sel.innerHTML = '<option value="">전체</option>';
      (data.items || []).forEach((w) => {
        const o = document.createElement("option");
        o.value = w; o.textContent = w; sel.appendChild(o);
      });
    } catch (_e) { /* ignore */ }
  }

  async function loadRecords() {
    const query = {
      start_date: $("rec-from").value || undefined,
      end_date: $("rec-to").value || undefined,
      worker: $("rec-worker").value || undefined,
      search: $("rec-search").value.trim() || undefined,
    };
    const data = await request("/blend/records", { query });
    const body = $("rec-body");
    body.innerHTML = "";
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">기록이 없습니다.</td></tr>';
      return;
    }
    items.forEach((r) => {
      const tr = document.createElement("tr");
      tr.className = "blend-row";
      tr.innerHTML =
        `<td>${esc(r.work_date)}</td><td>${esc(r.product_lot)}</td>` +
        `<td>${esc(r.product_name)}</td>` +
        `<td>${esc(r.worker)}</td><td class="num">${fmt(r.total_amount)}</td><td>${esc(r.scale || "-")}</td>`;
      tr.addEventListener("click", () => openDetail(r.id));
      body.appendChild(tr);
    });
  }

  async function openDetail(id) {
    const rec = await request(`/blend/records/${id}`);
    state.detailId = id;
    $("blend-detail-title").textContent = `배합 실적서 — ${rec.product_lot}`;
    const v = rec.variance || {};
    const rows = (rec.details || []).map((d, i) =>
      `<tr>
        <td>${i + 1}</td><td>${esc(d.material_name)}</td>
        <td class="num">${fmt(d.ratio, 2)}</td>
        <td class="num">${fmt(d.theory_amount)}</td>
        <td class="num">${fmt(d.actual_amount)}</td>
        <td class="num ${d.variance > 0 ? "var-up" : d.variance < 0 ? "var-down" : ""}">${d.variance === null ? "-" : (d.variance > 0 ? "+" : "") + fmt(d.variance, 2)}</td>
        <td>${esc(d.material_lot || "-")}</td>
      </tr>`).join("");
    $("blend-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${esc(rec.product_lot)}</b></div>
        <div><span class="dhr-k">제품</span><b>${esc(rec.product_name)}</b></div>
        <div><span class="dhr-k">작업자</span><b>${esc(rec.worker)}</b></div>
        <div><span class="dhr-k">작업일시</span><b>${esc(rec.work_date)} ${esc(rec.work_time || "")}</b></div>
        <div><span class="dhr-k">총 배합량</span><b>${fmt(rec.total_amount)} g</b></div>
        <div><span class="dhr-k">저울</span><b>${esc(rec.scale || "-")}</b></div>
      </div>
      <div class="table-wrap"><table class="blend-table">
        <thead><tr><th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th class="num">편차(g)</th><th>자재 LOT</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3">합계</td><td class="num">${fmt(v.theory_total)}</td><td class="num">${fmt(v.actual_total)}</td><td class="num">${(v.net_variance > 0 ? "+" : "") + fmt(v.net_variance, 2)}</td><td></td></tr></tfoot>
      </table></div>
      ${rec.note ? `<p class="dhr-note">비고: ${esc(rec.note)}</p>` : ""}
      ${renderApprovalSection(rec)}
      ${renderViscositySection(rec)}`;
    $("blend-detail-modal").hidden = false;
  }

  function approvalCell(label, name, at, sign) {
    const img = sign ? `<img class="dhr-sign-img" src="${esc(sign)}" alt="서명" />` : "";
    return `<div class="dhr-sign">
      <div class="dhr-sign-role">${label}</div>
      ${img}
      <div class="dhr-sign-name">${esc(name || "")}</div>
      <div class="dhr-sign-at">${esc(at ? at.slice(0, 16).replace("T", " ") : "")}</div>
    </div>`;
  }

  function renderApprovalSection(rec) {
    // 현장에서는 검토/승인을 하지 않는다 — 작성만 표시(검토/승인 서명은 DHR 출력물에서 합성).
    return `<div class="dhr-approvals dhr-approvals-single">
        ${approvalCell("작성", rec.created_by, rec.created_at, rec.worker_sign)}
      </div>`;
  }

  function renderViscositySection(rec) {
    const linked = rec.viscosity || [];
    const list = linked.length
      ? `<ul class="blend-visc-list">${linked.map((v) =>
          `<li><b>${esc(v.product_code)}</b> ${fmt(v.viscosity)} <span class="muted small">${esc(v.measured_date || "")}${v.created_by ? " · " + esc(v.created_by) : ""}</span></li>`
        ).join("")}</ul>`
      : '<p class="muted small">측정된 점도가 없습니다. (등록은 점도 관리 화면에서)</p>';
    // 점도 등록 UI 는 점도 관리 화면 한 곳 — 여기는 읽기전용 표시만.
    return `<div class="blend-visc-block no-print">
      <h4 class="panel-title">점도 측정</h4>
      ${list}
    </div>`;
  }

  async function cancelDetail() {
    if (!state.detailId) return;
    if (!window.confirm("이 배합 기록을 취소(삭제 표시)할까요?")) return;
    try {
      await request(`/blend/records/${state.detailId}`, { method: "DELETE" });
      notify("기록이 취소되었습니다.", "success");
      $("blend-detail-modal").hidden = true;
      loadRecords();
    } catch (e) { notify(`취소 실패: ${e.message}`, "error"); }
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
      state._lastRecipeId = "";
      loadRecipes().catch(() => {});
    });
    recipeInput.addEventListener("blur", () => {
      if (selectedRecipeId()) return;
      recipeInput.value = state.current ? state.current.recipe.product_name : "";
    });
    $("blend-total-base").addEventListener("click", () => {
      const base = baseTotalValue();
      if (!(base > 0)) return;
      const totalInput = $("blend-total");
      totalInput.value = String(base);
      totalInput.dispatchEvent(new Event("input"));  // 이론량 재계산 경로 재사용
    });
    $("blend-total").addEventListener("input", () => {
      recomputeTheory();
      state.items.forEach((_, i) => updateRowVar(i));
      // 이론량 셀 + 실제량 입력칸 안내값 갱신
      document.querySelectorAll("#blend-mat-body tr").forEach((tr, i) => {
        const cell = tr.querySelector(".blend-theory");
        if (cell && state.items[i]) cell.textContent = fmt(state.items[i].theory_amount);
        const act = tr.querySelector(".blend-actual");
        if (act && state.items[i]) {
          act.placeholder = state.items[i].theory_amount == null ? "" : fmt(state.items[i].theory_amount);
        }
      });
      updateTotals();
      updateLotPreview();
      updateInputGuide();
    });
    $("blend-worker").addEventListener("input", updateInputGuide);
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
    $("rec-apply").addEventListener("click", () => loadRecords().catch((e) => notify(e.message, "error")));
    $("bulk-add-row").addEventListener("click", addBulkRow);
    $("bulk-create").addEventListener("click", createBulk);
    $("rec-export-all").addEventListener("click", () => {
      const q = new URLSearchParams();
      if ($("rec-from").value) q.set("start_date", $("rec-from").value);
      if ($("rec-to").value) q.set("end_date", $("rec-to").value);
      if ($("rec-worker").value) q.set("worker", $("rec-worker").value);
      if ($("rec-search").value.trim()) q.set("search", $("rec-search").value.trim());
      window.location.assign(`/api/blend/records/export-all?${q.toString()}`);
    });
    $("blend-detail-close").addEventListener("click", () => { $("blend-detail-modal").hidden = true; });
    $("blend-detail-cancel").addEventListener("click", cancelDetail);
    $("blend-pdf").addEventListener("click", () => {
      if (state.detailId) window.open(`/api/blend/records/${state.detailId}/pdf`, "_blank");
    });
    $("blend-print").addEventListener("click", () => window.print());
    $("blend-excel").addEventListener("click", () => {
      if (state.detailId) window.location.assign(`/api/blend/records/${state.detailId}/export`);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    $("blend-date").value = todayISO();
    $("blend-time").value = nowTime();
    if ($("bulk-worker") && lockedWorkerName()) $("bulk-worker").value = lockedWorkerName();
    bind();
    // 경로로 모드 결정: /blend/bulk = 일괄 생성, 그 외 = 배합 입력
    setMode(location.pathname.replace(/\/+$/, "").endsWith("/bulk") ? "bulk" : "entry");
    updateInputGuide();
    loadRecipes().catch((e) => notify(`레시피 로드 실패: ${e.message}`, "error"));
    loadWorkers();
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

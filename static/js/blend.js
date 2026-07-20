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
    ANCHOR_BADGE,
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
    findAnchorIndex,
    computeAnchorTheory,
    theoryFromWeights,
    BATCH_LIMIT_G,
    requiredTotalForRow,
    rescalePlan,
    exceedsBatchLimit,
  } = window.IRMS.blendLib;

  const $ = (id) => document.getElementById(id);

  const state = { recipes: [], current: null, items: [], detailId: null, viscProducts: [], lotMap: {}, workers: [], scaleReady: false, sessionWorker: "", anchorIndex: -1, prevAnchorActual: "", toleranceG: TOLERANCE_G, _anchorRecomputing: false,
    // 반제품 원료 LOT 자동 제안: 레시피 자재명 → 최근 product_lot 목록.
    // 자재명이 "배합 기록이 있는 반제품명"과 일치하면 그 제품의 최근 LOT 을 제안.
    // 레시피 선택 시 1회 호출(실패는 조용히 무시 — 제안 없이 기존 동작 유지).
    lotSuggest: {},
    // 미등록 LOT 차단 — (자재명\u0000LOT) → true(등록됨)/false(미등록) 캐시.
    // 동일 (name, lot) 조합의 중복 조회를 막기 위해 한 번 판정하면 보관한다.
    // 레시피가 바뀌면 lotSuggest 와 함께 새로 채워지므로 여기서는 만료 처리하지 않는다.
    lotChecked: {},
    // 초과 계량 증량(rescale). 기준 자재 레시피에서 총량이 기준 자재 실측값으로
    // 파생되므로 증량분을 별도로 보관 — 유효 총량 = max(기준 파생 총량, rescaleTotalG).
    // 레시피 변경/저장 후 초기화 시 0(미사용)으로 리셋.
    rescaleTotalG: 0,
    // 추가분 입력 모드에 들어간 행 인덱스(저울 PRINT 를 추가분으로 합산하기 위한 플래그).
    addModeIdx: null,
    // 보류 중인 증량 제안(newTotal) — discard 모달에서 '그래도 증량' 선택 시 재사용.
    pendingRescale: null,
    // 저울 전용 입력 모드(운영 대시보드 토글). true 면 실제량·증량 인라인 입력이
    // readonly 가 되고 저울 PRINT 로만 입력된다. false(기본)면 동작 변화 없음.
    scaleOnlyInput: false,
  };

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
    updateScaleOnlyBanner();
  }

  // ── 저울 전용 입력 모드(scale-only-input) ───────────────────────
  // 페이지 로드 시 GET 으로 현재 상태를 가져온다(실패 시 false 폴백 — 화면이 죽으면 안 됨).
  // enabled=true 면 실제량 입력칸(.blend-actual)과 증량 추가분 인라인 입력(.blend-add-inline)을
  // readonly 로 잠그고(title 안내), 저울 미연결 시 상시 배너를 띄운다.
  // enabled=false 면 어떤 동작 변화도 없어야 한다(readonly 미적용, 배너 숨김).
  async function loadScaleOnlyInput() {
    try {
      const res = await fetch("/api/settings/scale-only-input", { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.scaleOnlyInput = Boolean(data && data.enabled);
    } catch (_e) {
      state.scaleOnlyInput = false;  // 폴백 — 화면이 죽으면 안 됨
    }
    applyScaleOnlyToRows();
    updateScaleOnlyBanner();
  }

  // 저울 전용 모드일 때 현재 DOM 의 실제량·증량 입력칸에 readonly + title 부여.
  // 새로 렌더되는 행에도 적용되도록 renderMatRows 직후에도 호출한다.
  function applyScaleOnlyToRows() {
    if (!state.scaleOnlyInput) return;
    const titleText = "저울 전용 모드 — 저울 PRINT 로만 입력됩니다";
    document.querySelectorAll("#blend-mat-body .blend-actual").forEach((el) => {
      el.readOnly = true;
      el.title = titleText;
    });
    document.querySelectorAll("#blend-mat-body .blend-add-inline").forEach((el) => {
      el.readOnly = true;
      el.title = titleText;
    });
  }

  // 저울 전용 모드 + 저울 미연결 → 상시 배너(login-error 스타일 재사용).
  // 저울 연결 상태(detectScale 주기 갱신)에 따라 자동 토글.
  function updateScaleOnlyBanner() {
    const banner = document.getElementById("scale-only-banner");
    if (!banner) return;
    const show = state.scaleOnlyInput && !state.scaleReady;
    banner.hidden = !show;
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
    // 저울 PRINT 입력은 input 이벤트가 없으므로 저장 후 자동 로그아웃 해제를 직접 호출
    cancelPostSaveLogout();
    // 추가 입력 모드 행이면 PRINT 값을 추가분으로 합산(누계 = 기존 actual + 입력값).
    if (state.addModeIdx === idx) {
      applyAddAmount(idx, Number(value));
      return;
    }
    input.value = String(value);
    state.items[idx].actual_amount = input.value;
    state.items[idx].manual = false;  // 저울 입력 — 손입력 표시 해제
    input.classList.remove("manual-warn");
    input.removeAttribute("title");
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
    populateRecipeSelect();
  }

  // 분류 → 레시피 2단계 선택. native select 라 클릭하면 전체 목록이 즉시 열리고
  // 리셋된다(옛 datalist 는 값을 지워야 목록이 떠서 불편했다). 분류로 걸러 목록도 짧아짐.
  function recipesForCategory() {
    const cat = $("blend-recipe-cat") ? $("blend-recipe-cat").value : "";
    if (cat === "") return state.recipes;                       // 전체
    if (cat === "__none__") return state.recipes.filter((r) => !r.category);  // 미분류
    return state.recipes.filter((r) => r.category === cat);
  }

  function populateRecipeSelect() {
    const sel = $("blend-recipe");
    if (!sel) return;
    const prev = sel.value;
    const list = recipesForCategory();
    sel.innerHTML = '<option value="">레시피 선택…</option>'
      + list.map((r) => `<option value="${esc(r.id)}">${esc(r.product_name)}</option>`).join("");
    if (prev && list.some((r) => String(r.id) === prev)) sel.value = prev;  // 이전 선택 유지
  }

  // 선택된 레시피 id(옵션 value). 미선택은 "".
  function selectedRecipeId() {
    return $("blend-recipe").value || "";
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
    // 레시피가 선택되면 DHR 카드의 빈 상태 안내를 걷고 LOT·합계를 노출.
    const dhrCard = document.querySelector(".blend-dhr-card");
    if (dhrCard) dhrCard.classList.remove("is-empty");
    // 레시피별 허용 편차(EFFECTIVE) 보존 — 레시피에 tolerance_g 이 없으면 기본값(0.05).
    // 모든 편차 검사·표시는 이 값을 따른다(레시피가 바뀌면 같이 갱신).
    state.toleranceG = (state.current.recipe && state.current.recipe.tolerance_g) || TOLERANCE_G;
    // value_weight(기준 자재 이론량 산출용)·is_anchor(기준 자재 여부) 보존.
    state.items = data.items.map((it) => ({
      ...it, actual_amount: "", material_lot: "",
    }));
    state.anchorIndex = findAnchorIndex(state.items);
    state.prevAnchorActual = "";
    // 레시피가 바뀌면 이전 레시피의 증량분을 버린다 — 새 레시피는 새 총량 기준.
    state.rescaleTotalG = 0;
    state.addModeIdx = null;
    state.pendingRescale = null;
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
    applyAnchorMode();
    updateLotPreview();
    updateInputGuide();
    loadLotSuggest();
  }

  // ── 반제품 원료 LOT 자동 제안 ───────────────────────────────
  // 자재명 전체로 1회 조회 → state.lotSuggest(자재명→[lots]) 보관. 실패는 조용히 무시
  // (제안 없이 기존 동작). 렌더는 이미 끝났으므로 포커스 시점에 state 만 읽는다.
  async function loadLotSuggest() {
    const names = state.items
      .map((it) => (it.material_name || "").trim())
      .filter((n) => n);
    if (!names.length) { state.lotSuggest = {}; return; }
    try {
      const data = await request("/blend/recent-product-lots", {
        query: { names: names.join(","), limit: 5 },
      });
      state.lotSuggest = (data && data.items) || {};
    } catch (_e) {
      state.lotSuggest = {};  // 실패 — 제안 없이 기존 동작 유지
    }
  }

  // 기준 자재 모드 적용 — 레시피에 기준 자재가 있으면:
  //   - 총 배합량 입력 읽기 전용(기준 자재 실측값에서 도출되므로 직접 입력 금지)
  //   - 기준량 빠른 채우기 버튼 미노출(총량 기반 방식이 아님)
  // 기준 자재가 없는 레시피는 이 함수가 아무것도 바꾸지 않는다(100% 기존 동작 유지).
  function applyAnchorMode() {
    const totalInput = $("blend-total");
    if (!totalInput) return;
    if (state.anchorIndex >= 0) {
      totalInput.readOnly = true;
      totalInput.placeholder = "기준 자재 계량 후 자동 산출";
      const links = $("blend-base-links");
      if (links) { links.hidden = true; links.innerHTML = ""; }
    } else {
      totalInput.readOnly = false;
      totalInput.placeholder = "";
    }
  }

  // 기준 자재가 없는 레시피인지 — 기존 총량 기반 흐름 유지.
  function hasAnchor() {
    return state.anchorIndex >= 0;
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

  // 허용 편차는 화면에 상시 표시하지 않는다 — 자재 표 위 라벨은 자리만 차지했다.
  // 편차는 초과했을 때만 알린다: 실제량 입력 후 행 경고 + 저장 시 서버 400.
  // 판정 자체는 state.toleranceG(레시피별 tolerance_g, 없으면 기본 0.05g)로 그대로 동작.

  // 반응기 진행 반제품(레시피)일 때만 배합 설정에 반응기 선택을 노출한다.
  function renderReactorField() {
    const field = $("blend-reactor-field");
    if (!field) return;
    const use = Boolean(state.current && state.current.recipe && state.current.recipe.use_reactor);
    field.hidden = !use;
    if (!use) $("blend-reactor").value = "";
  }

  function recomputeTheory() {
    // 기준 자재 모드에서는 총량 입력이 읽기 전용 — 이론량은 기준 자재 실측값에서
    // 도출되므로 이 총량 기반 재계산 경로를 타지 않는다.
    if (hasAnchor()) return;
    const total = Number($("blend-total").value) || 0;
    // value_weight 비례 방식 — 서버(blend_service.scale_theory)와 동일 산술로
    // 반올림된 ratio(%) 로 인한 57.99 같은 꼬리를 없앤다. value_weight 이 빠진
    // 옛 레시피는 null 배열 반환 → 기존 computeTheoryAmount(ratio, total) 로 폴백.
    const byWeights = theoryFromWeights(state.items, total);
    state.items.forEach((it, i) => {
      it.theory_amount = byWeights[i] !== null
        ? byWeights[i]
        : computeTheoryAmount(it.ratio, total);
    });
  }

  // 순차 입력 안내: 입력해야 하는 칸 강조.
  // 일반 레시피: 총 배합량(공백) → 작업자.
  // 기준 자재 레시피: 총량은 자동 산출(읽기 전용)이므로 기준 자재 실측 칸부터 → 작업자.
  function updateInputGuide() {
    const total = $("blend-total");
    const worker = $("blend-worker");
    if (hasAnchor()) {
      const anchorInput = document.querySelector(`.blend-actual[data-idx="${state.anchorIndex}"]`);
      const it = state.items[state.anchorIndex];
      const anchorReady = Boolean(it && it.actual_amount !== "" && Number(it.actual_amount) > 0);
      total.classList.remove("needs-input");
      if (anchorInput) anchorInput.classList.toggle("needs-input", !anchorReady);
      worker.classList.toggle("needs-input", anchorReady && !worker.value.trim());
      return;
    }
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
    // 기준 자재 모드: 기준 자재의 이론량이 아직 없으면(실측 전) 비기준 자재 입력 잠금.
    const anchorEntered = hasAnchor()
      ? state.items[state.anchorIndex].theory_amount != null
      : false;
    state.items.forEach((it, idx) => {
      body.insertAdjacentHTML("beforeend", stepRowsHtml(steps, idx));  // 이 자재 앞(=앞선 자재 idx개 뒤)의 설명
      const tr = document.createElement("tr");
      const opts = {};
      if (hasAnchor()) {
        if (idx === state.anchorIndex) {
          opts.anchor = true;
        } else if (!anchorEntered) {
          opts.disableActual = true;  // 기준 자재 계량 전까지 비기준 자재 입력 비활성화
        }
      }
      tr.innerHTML = materialRowHtml(idx, it, opts);
      body.appendChild(tr);
    });
    body.insertAdjacentHTML("beforeend", stepRowsHtml(steps, state.items.length));  // 마지막 자재 뒤 설명
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("input", () => {
        const i = Number(el.dataset.idx);
        state.items[i].actual_amount = el.value;
        // 저울 연결 중 손입력 → '수동 입력' 기록 + 경고(수기 제한 전 준비 단계).
        // 행당 1회만 토스트(타이핑 키마다 스팸 방지), 칸은 주황 표시로 남긴다.
        if (state.scaleReady) {
          if (!state.items[i].manual) {
            notify("저울 연결 중 — 실제량은 저울 PRINT 키로 입력하세요. 수기 입력은 기록에 표시되며, 앞으로 제한될 예정입니다.", "warn big");
          }
          state.items[i].manual = true;
          el.classList.add("manual-warn");
          el.title = "수기 입력됨 — 저울 PRINT 로 다시 계량하면 해제됩니다";
        }
        updateRowVar(i);
        updateTotals();
      })
    );
    // 실제량 입력 완료(blur) 시 허용 편차(±state.toleranceG g) 초과면 경고
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("change", () => warnIfVariance(Number(el.dataset.idx)))
    );
    body.querySelectorAll(".blend-lot").forEach((el) => {
      el.addEventListener("input", () => {
        const idx = Number(el.dataset.idx);
        state.items[idx].material_lot = el.value;
        // 타이핑 중이면 제안 목록을 입력값으로 시작하는 것만 필터링해 다시 그린다.
        if (el._lotBox) renderLotSuggest(el);
        // 기준 자재 행의 LOT 편집 — 이미 적용된 이월은 값이 바뀌었으므로 취소하고,
        // 새 값이 등록된 1차 LOT 이면 이월 컨트롤을 (다시) 노출한다.
        if (idx === state.anchorIndex) {
          if (state.items[idx].carried_over) clearCarryOver();
          refreshCarryOverControl();
        }
      });
      // 포커스 시 제안 목록 표시(제안이 있는 자재만). blend_login suggest 패턴 재사용.
      el.addEventListener("focus", () => renderLotSuggest(el));
      // blur 보다 먼저 클릭이 처리되도록 목록 항목은 mousedown 으로 채운다(아래 renderLotSuggest).
      // 여기 blur 는 목록 닫기만 — mousedown 의 preventDefault 가 blur 자체를 막지는 않으므로
      // 약간의 지연을 줘 클릭 핸들러가 먼저 끝나도록 한다(blend_login 과 동일 주의).
      el.addEventListener("blur", () => hideLotSuggest(el));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && el._lotBox) { hideLotSuggest(el); }
      });
      // 미등록 LOT 차단 — 반제품(제안이 있는 자재)만. 편집 확정(change) 시 검증.
      // 일반 자재(제안 없음)는 변화 없음. 미등록이면 #lot-invalid-modal 표시 후 값을 비운다.
      el.addEventListener("change", () => validateLotInput(el));
    });
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
        // Enter(완료)로 계량을 마치는 순간에도 편차 초과를 즉시 알린다 —
        // change(blur) 이벤트에만 기대면 흐름에 따라 경고가 저장 때까지 밀린다.
        warnIfVariance(Number(el.dataset.idx));
        const next = Number(el.dataset.idx) + 1;
        if (!focusField(`.blend-lot[data-idx="${next}"]`)) {
          const save = document.getElementById("blend-save");
          if (save) save.focus();
        }
      })
    );
    updateTotals();
    // 저울 전용 모드가 켜져 있으면 새로 렌더된 행의 실제량 칸도 readonly 로 잠근다.
    applyScaleOnlyToRows();
    // 기준 자재 행의 LOT 가 이미 1차 LOT 이면 이월 컨트롤을 노출(수정 등록 프리필 등).
    refreshCarryOverControl();
  }

  // ── 반제품 원료 LOT 제안 목록(.blend-lot 칸 아래) ───────────────
  // native datalist 는 '클릭해도 목록이 안 열리는' 기존 불만이 있어 쓰지 않는다.
  // blend_login.js 의 suggest 목록 패턴: 입력칸 바로 아래 작은 div, 항목은 button.
  // 항목 mousedown(preventDefault) → LOT 칸 채움 + input 이벤트(state 반영) + 목록 닫기.
  // mousedown 을 쓰는 이유: click 은 blur 보다 늦어 클릭이 blur 에 먹힌다.
  function renderLotSuggest(input) {
    const idx = Number(input.dataset.idx);
    const name = (state.items[idx] && state.items[idx].material_name) || "";
    const lots = (state.lotSuggest && state.lotSuggest[name]) || [];
    if (!lots.length) { hideLotSuggest(input); return; }  // 제안 없는 자재 = 변화 없음
    // 타이핑 필터: 입력값으로 시작하는 LOT 만(빈 값이면 전체). 첫 항목이 최신 LOT.
    // 각 항목은 {lot, total} — total(1차 배치 총량)은 이월 채움 기준값으로 회색으로 같이 표시.
    const q = (input.value || "").trim().toLowerCase();
    const matches = q ? lots.filter((l) => String(l.lot).toLowerCase().startsWith(q)) : lots.slice();
    let box = input._lotBox;
    if (!box) {
      box = document.createElement("div");
      box.className = "lot-suggest";
      // 입력칸의 부모(td) 를 position:relative 기준으로 삼아 바로 아래에 띄운다.
      const anchor = input.parentElement || input.parentNode;
      if (anchor) {
        anchor.style.position = anchor.style.position || "relative";
        anchor.appendChild(box);
      } else {
        document.body.appendChild(box);
      }
      input._lotBox = box;
    }
    box.innerHTML = "";
    matches.forEach((entry) => {
      const lot = entry.lot;
      const item = document.createElement("button");
      item.type = "button";
      item.className = "lot-suggest-item";
      // LOT 텍스트 + 회색 '· N g' 총량 접미(클릭 시 LOT 만 채운다).
      item.textContent = lot;
      if (entry.total != null) {
        const suf = document.createElement("span");
        suf.className = "lot-suggest-total";
        suf.textContent = ` · ${entry.total} g`;
        item.appendChild(suf);
      }
      // blur 보다 먼저 실행되도록 mousedown + preventDefault(blend_login 과 동일 주의).
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = lot;  // LOT 만 채운다(총량은 표시 전용).
        state.items[idx].material_lot = lot;
        input.dispatchEvent(new Event("input"));  // state 반영 경로 재사용
        hideLotSuggest(input);
        input.focus();
      });
      box.appendChild(item);
    });
    box.hidden = !matches.length;
  }

  function hideLotSuggest(input) {
    if (!input._lotBox) return;
    input._lotBox.hidden = true;
  }

  // ── 미등록 LOT 차단(반제품 자재만) ─────────────────────────────
  // 제안(state.lotSuggest)이 있는 자재 = 완료 배합 기록이 있는 반제품. 이 자재의 LOT 칸은
  // 반드시 그 반제품의 실제 product_lot 중 하나여야 한다. 그렇지 않으면(직접 타이핑 오타 등)
  // #lot-invalid-modal 로 막고 값을 비운다. 일반 자재(제안 없음)는 100% 기존 동작 유지.
  //
  // 판정 우선순위: 빈 값(공백 trim) → 통과 / 제안 목록에 있는 값 → 통과 /
  // 그 외 → 서버 /blend/product-lot-exists 로 확인(캐시 state.lotChecked[name\u0000lot] 사용).
  // 네트워크 오류는 통과(loadLotSuggest 와 동일한 fail-open 철학 — 현장 입력을 막지 않는다).
  async function checkLotRegistered(name, lot) {
    if (!lot) return true;
    const lots = (state.lotSuggest && state.lotSuggest[name]) || [];
    // 제안 항목이 이제 {lot, total} 객체이므로 .lot 값으로 비교한다(즉시 통과 판정).
    if (lots.some((e) => String(e && e.lot) === lot)) return true;
    const key = name + "\u0000" + lot;
    if (Object.prototype.hasOwnProperty.call(state.lotChecked, key)) {
      return !!state.lotChecked[key];
    }
    try {
      const data = await request("/blend/product-lot-exists", { query: { name, lot } });
      const ok = Boolean(data && data.exists);
      state.lotChecked[key] = ok;
      return ok;
    } catch (_e) {
      // 조회 실패 — 통과(기존 동작 유지). loadLotSuggest 의 fail-open 철학과 동일.
      return true;
    }
  }

  // .blend-lot 입력칸 하나 검증 — 미등록이면 모달을 띄우고 값·state 를 비운 뒤 다시 포커스.
  async function validateLotInput(input) {
    const idx = Number(input.dataset.idx);
    const item = state.items[idx];
    if (!item) return;
    const name = (item.material_name || "").trim();
    // 제안이 없는 자재(일반 원료)는 검증하지 않는다 — 기존 동작 유지.
    if (!state.lotSuggest || !state.lotSuggest[name]) return;
    const lot = (input.value || "").trim();
    input.value = lot;  // trim 반영
    state.items[idx].material_lot = lot;
    if (await checkLotRegistered(name, lot)) return;  // 등록됨 → 통과
    // 미등록 — 모달 표시. 확인 버튼(hideLotInvalidModal 핸들러)이 값 비우기를 맡는다.
    openLotInvalidModal(name, lot, input);
  }

  function openLotInvalidModal(name, lot, input) {
    const body = $("lot-invalid-modal-body");
    if (body) {
      body.innerHTML = ""
        + `<p><strong>자재명:</strong> ${esc(name)}</p>`
        + `<p><strong>입력한 로트:</strong> ${esc(lot)}</p>`
        + `<p>등록되지 않은 로트입니다. 다시 확인해주세요.</p>`;
    }
    // 확인 버튼이 눌릴 때 값을 비우고 다시 포커스하기 위해 현재 입력칸을 기억해둔다.
    $("lot-invalid-modal")._lotInput = input || null;
    $("lot-invalid-modal").hidden = false;
  }

  function closeLotInvalidModal() { $("lot-invalid-modal").hidden = true; }

  // ── 반응기 이월(carry-over): 기준 자재 행만, 반응기 진행 레시피만 ────
  // 1차 배합(반제품)의 총량을 2차 배합 기준 자재의 실제량으로 그대로 가져오는 기능.
  // 반응기에 이미 1차 제품이 남아 있어 2차에서는 다시 계량하지 않는 경우에 쓴다.
  // 서버가 carried_over=true 행의 actual_amount 를 1차 총량으로 강제(변조 방지)하므로,
  // 여기서는 작업자에게 버튼·확인 모달로 흐름을 제공할 뿐이다.

  // 이월 자격 판정 — 현재 기준 자재 행이고 반응기 레시피일 때만.
  function carryOverEligible() {
    return Boolean(
      hasAnchor()
      && state.current && state.current.recipe && state.current.recipe.use_reactor
    );
  }

  // 기준 자재명의 등록된 1차 LOT 중 현재 LOT 값과 정확히 일치하는 항목을 찾는다.
  // 반환: {lot, total} 또는 null. (제안이 없거나 일치 항목이 없으면 null)
  function findStage1Match(lotValue) {
    if (!hasAnchor()) return null;
    const name = (state.items[state.anchorIndex].material_name || "").trim();
    const lots = (state.lotSuggest && state.lotSuggest[name]) || [];
    const v = (lotValue || "").trim();
    if (!v) return null;
    return lots.find((e) => e && String(e.lot) === v) || null;
  }

  // 기준 자재 행의 LOT 칸(<td>) 아래에 이월 컨트롤(배지 + 버튼)을 띄운다/숨긴다.
  // 1차 LOT 가 선택돼 있을 때만 보이고, 그렇지 않으면 숨긴다.
  function refreshCarryOverControl() {
    const lotInput = document.querySelector(`.blend-lot[data-idx="${state.anchorIndex}"]`);
    if (!lotInput) return;
    const cell = lotInput.parentElement || lotInput.parentNode;  // <td>
    let wrap = cell.querySelector(".carry-over-wrap");
    const match = carryOverEligible() ? findStage1Match(lotInput.value) : null;
    if (!match) {
      // 자격이 없거나 1차 LOT 가 아니면 컨트롤 숨김(이미 적용된 이월도 여기서 취소된다).
      if (wrap) wrap.hidden = true;
      return;
    }
    // 컨트롤이 없으면 한 번 만든다(재렌더 후에도 살아남도록 cell 에 부착).
    if (!wrap) {
      wrap = document.createElement("span");
      wrap.className = "carry-over-wrap";
      // '1차 총량 N g' 안내 배지
      const badge = document.createElement("span");
      badge.className = "carry-over-badge muted";
      // 이월 토글 버튼
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm carry-over-btn";
      btn.textContent = "반응기 이월";
      btn.title = "1차 배합 총량을 이 자재의 실제량으로 가져옵니다";
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openCarryOverModal();
      });
      wrap.appendChild(badge);
      wrap.appendChild(btn);
      cell.appendChild(wrap);
    }
    const badge = wrap.querySelector(".carry-over-badge");
    if (badge) badge.textContent = `1차 총량 ${match.total} g`;
    wrap.hidden = false;
  }

  // 이월 확인 모달 — 1차 총량을 기준 자재 실제량으로 기록함을 안내.
  function openCarryOverModal() {
    if (!hasAnchor()) return;
    const lotInput = document.querySelector(`.blend-lot[data-idx="${state.anchorIndex}"]`);
    const match = findStage1Match(lotInput ? lotInput.value : "");
    if (!match) return;
    const name = (state.items[state.anchorIndex].material_name || "").trim();
    const body = $("carry-over-modal-body");
    if (body) {
      body.innerHTML = ""
        + `<p><strong>자재명:</strong> ${esc(name)}</p>`
        + `<p><strong>1차 로트:</strong> ${esc(match.lot)}</p>`
        + `<p>반응기에 이미 1차 배합 제품이 남아 있어 이 자재는 다시 계량하지 않습니다. `
        + `1차 배합의 총량(<strong>${match.total} g</strong>)을 이 자재의 입력량으로 기록합니다.</p>`
        + `<p class="carry-over-caution">실제로 계량하는 경우에는 사용하지 마세요.</p>`;
    }
    $("carry-over-modal").hidden = false;
  }

  function closeCarryOverModal() { $("carry-over-modal").hidden = true; }

  // 이월 적용 — carried_over=true, 1차 총량을 실제량으로 채우고 읽기 전용 표시.
  // 기준 자재 실측값이 바뀐 것과 동일하게 이론/총량 재산출(applyAnchorRecompute) 경로를 탄다.
  function applyCarryOver() {
    if (!hasAnchor()) return;
    const ai = state.anchorIndex;
    const lotInput = document.querySelector(`.blend-lot[data-idx="${ai}"]`);
    const match = findStage1Match(lotInput ? lotInput.value : "");
    if (!match) return;
    const item = state.items[ai];
    item.carried_over = true;
    item.actual_amount = String(match.total);
    item.manual = false;  // 이월은 계량이 아니므로 수동 입력 표시 해제
    const actualInput = document.querySelector(`.blend-actual[data-idx="${ai}"]`);
    if (actualInput) {
      actualInput.value = String(match.total);
      actualInput.readOnly = true;
      actualInput.classList.add("carried-over");
      actualInput.classList.remove("manual-warn");
      // '이월' 표식 태그 — 클릭하면 이월을 취소(toggle off) 한다.
      let tag = actualInput.parentElement.querySelector(".carry-over-tag");
      if (!tag) {
        tag = document.createElement("span");
        tag.className = "carry-over-tag";
        tag.textContent = "이월";
        tag.title = "클릭하면 이월을 취소하고 다시 계량할 수 있습니다";
        tag.addEventListener("click", () => { clearCarryOver(); });
        actualInput.parentElement.appendChild(tag);
      }
    }
    // 기준 자재 실측값 변경과 동일한 재산출 경로 — 이론량·도출 총량·비기준 자재 잠금 해제.
    state._anchorRecomputing = true;
    try { applyAnchorRecompute(); } finally { state._anchorRecomputing = false; }
    closeCarryOverModal();
  }

  // 이월 취소 — carried_over=false, 읽기 전용·표식 제거, 강제 실제량 비움.
  // LOT 변경/삭제 또는 '이월' 태그 클릭 시 호출. 다시 손/저울로 계량할 수 있게 된다.
  function clearCarryOver() {
    if (!hasAnchor()) return;
    const ai = state.anchorIndex;
    const item = state.items[ai];
    if (!item) return;
    item.carried_over = false;
    item.actual_amount = "";
    item.manual = false;
    const actualInput = document.querySelector(`.blend-actual[data-idx="${ai}"]`);
    if (actualInput) {
      actualInput.value = "";
      // 저울 전용 모드가 켜져 있지 않을 때만 readonly 를 푼다(그 모드는 모든 실제량이 읽기 전용).
      if (!state.scaleOnlyInput) actualInput.readOnly = false;
      actualInput.classList.remove("carried-over");
      const tag = actualInput.parentElement.querySelector(".carry-over-tag");
      if (tag) tag.remove();
    }
    state._anchorRecomputing = true;
    try { applyAnchorRecompute(); } finally { state._anchorRecomputing = false; }
  }

  function updateRowVar(i) {
    const it = state.items[i];
    const cell = document.querySelector(`.blend-var[data-idx="${i}"]`);
    if (!cell) return;
    const display = varianceDisplay(it, state.toleranceG);
    cell.textContent = display.text;
    cell.className = display.className;
    // 기준 자재 행의 실측값 변경(손입력·저울 PRINT 공통) → 이론량·총량 재산출 트리거.
    // 재진입 가드로 applyAnchorRecompute 내 updateRowVar 호출이 다시 트리거하지 않게 막는다.
    if (hasAnchor() && i === state.anchorIndex && !state._anchorRecomputing) {
      state._anchorRecomputing = true;
      try { applyAnchorRecompute(); } finally { state._anchorRecomputing = false; }
    }
  }

  // 기준 자재 우선 계량 — 기준 자재 실측값이 바뀌면(손입력·저울 PRINT 모두) 이론량과
  // 도출 총량을 다시 산출한다. 입력 경로가 updateRowVar 를 공유하므로, 기준 자재 행의
  // updateRowVar 호출에서 이 함수를 트리거한다(fillScaleValue 코드 경로는 건드리지 않음).
  function applyAnchorRecompute() {
    if (!hasAnchor()) return;
    const ai = state.anchorIndex;
    const anchorItem = state.items[ai];
    const anchorActual = anchorItem ? anchorItem.actual_amount : "";
    const anchorActualNum = anchorActual === "" ? null : Number(anchorActual);

    // 기준 자재 값이 '변경'된 경우(빈 값이 아니던 상태에서 다른 값으로) 다른 자재 실측값이
    // 하나라도 있으면 경고 후 비기준 자재 실측값·편차 표시를 모두 지운다.
    const prev = state.prevAnchorActual;
    const hadPrev = prev !== "" && prev !== null;
    const nowHas = anchorActualNum !== null;
    const changed = hadPrev && nowHas && String(prev) !== String(anchorActual);
    if (changed) {
      const othersHaveActual = state.items.some((it, i) => i !== ai && it.actual_amount !== "");
      if (othersHaveActual) {
        notify("기준 자재 값이 변경되어 나머지 자재를 다시 계량해야 합니다", "warn");
        state.items.forEach((it, i) => {
          if (i === ai) return;
          it.actual_amount = "";
          it.manual = false;
          const inp = document.querySelector(`.blend-actual[data-idx="${i}"]`);
          if (inp) inp.value = "";
        });
      }
    }

    // 이론량·총량 재산출 — anchorActual 이 0 이하/빈이면 computeAnchorTheory 가 null 배열 반환.
    const { theoryAmounts, total } = computeAnchorTheory(state.items, ai, anchorActualNum === null ? 0 : anchorActualNum);
    const anchorEntered = theoryAmounts.some((t) => t !== null);
    state.items.forEach((it, i) => { it.theory_amount = theoryAmounts[i]; });

    // 총 배합량 입력(읽기 전용)에 도출 총량 기입
    const totalInput = $("blend-total");
    if (totalInput) totalInput.value = anchorEntered ? String(total) : "";

    // 이론량 셀·실제량 placeholder·입력 활성화 상태 갱신(재렌더 없이 DOM 갱신 — 포커스 유지)
    document.querySelectorAll("#blend-mat-body .blend-theory").forEach((cell) => {
      const i = Number(cell.dataset.idx);
      cell.textContent = fmt(state.items[i].theory_amount);
    });
    document.querySelectorAll("#blend-mat-body .blend-actual").forEach((act) => {
      const i = Number(act.dataset.idx);
      const it = state.items[i];
      if (it) act.placeholder = it.theory_amount == null ? "" : fmt(it.theory_amount);
      // 기준 자재 입력 전이면 비기준 자재 입력 비활성화, 입력 후면 활성화
      if (i !== ai) act.disabled = !anchorEntered;
    });
    // 각 행 편차 표시 갱신(기준 자재는 항상 '-')
    state.items.forEach((_, i) => updateRowVar(i));
    state.prevAnchorActual = anchorActual;
    updateTotals();
    updateLotPreview();
    updateInputGuide();
  }

  function warnIfVariance(i) {
    const it = state.items[i];
    const v = rowVariance(it);
    const tol = state.toleranceG;
    if (Math.abs(v) > tol + 1e-9) {
      notify(varianceWarnMessage(it, v, tol), "error");
      // +방향(초과 계량)일 때만 증량 제안 모달을 띄운다. −방향(부족)은 그 자재를
      // 더 넣으면 끝이므로 기존 토스트만 유지한다.
      if (v > 0) {
        offerRescale();
      }
      return true;
    }
    return false;
  }

  // ── 초과 계량 증량(rescale) 통합 ─────────────────────────
  // 자재를 이론량 초과해 넣었으면 배합 전체를 그 값에 맞춰 증량한다.
  // rescalePlan(순수) 으로 newTotal 계산 → 25,000g 초과면 #discard-modal,
  // 아니면 #rescale-modal. [증량 적용]/[그래도 증량] 선택 시 applyRescale.
  // 반복 초과 시 같은 모달이 다시 뜨고 max 규칙으로 더 커진다.
  function offerRescale() {
    // 이미 모달이 열려 있거나 보류 제안이 있으면 중복 트리거 방지(change·Enter·
    // 총량 변경 경로에서 warnIfVariance 가 여러 번 불릴 수 있다).
    if (!$("rescale-modal").hidden || !$("discard-modal").hidden) return;
    if (state.pendingRescale) return;
    const currentTotal = effectiveCurrentTotal();
    const plan = rescalePlan(state.items, currentTotal, state.toleranceG);
    if (!plan.changed) return;
    state.pendingRescale = plan;
    if (exceedsBatchLimit(plan.newTotal)) {
      openDiscardModal(plan);
    } else {
      openRescaleModal(plan);
    }
  }

  // 증량 계산 기준이 되는 현재 유효 총량.
  // 일반 레시피: 총량 입력값. 기준 자재 레시피: max(기준 파생 총량, rescaleTotalG).
  function effectiveCurrentTotal() {
    if (hasAnchor()) {
      const derived = Number($("blend-total").value) || 0;
      return Math.max(derived, state.rescaleTotalG || 0);
    }
    return Number($("blend-total").value) || 0;
  }

  function buildRescaleSummary(plan) {
    // 초과해 계량된(= addNeeded 산출에 기여한) 행만 추려 미리보기 표에 표시.
    const overRows = plan.rows.filter((r) => r.addNeeded !== null);
    let html = "";
    const over = overRows.map((r) => esc(state.items[r.idx].material_name)).join(", ");
    if (over) {
      html += `<p class="rescale-summary">초과 자재: ${over}</p>`;
    }
    html += `<div class="rescale-totals">`
      + `<span>총 배합량</span>`
      + `<span class="old">${fmt(effectiveCurrentTotal())} g</span>`
      + `<span>→</span>`
      + `<span class="new">${fmt(plan.newTotal)} g</span>`
      + `</div>`;
    if (overRows.length) {
      html += `<table class="rescale-add-table"><thead><tr><th>자재</th>`
        + `<th class="num">현재 실제량</th><th class="num">새 이론량</th>`
        + `<th class="num">추가로 넣을 양</th></tr></thead><tbody>`;
      overRows.forEach((r) => {
        const it = state.items[r.idx];
        html += `<tr><td>${esc(it.material_name)}</td>`
          + `<td class="num">${fmt(it.actual_amount)}</td>`
          + `<td class="num">${fmt(r.newTheory)}</td>`
          + `<td class="num add-cell">+${fmt(r.addNeeded)}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    return html;
  }

  function openRescaleModal(plan) {
    const body = $("rescale-modal-body");
    if (body) body.innerHTML = buildRescaleSummary(plan);
    $("rescale-modal").hidden = false;
  }
  function closeRescaleModal() { $("rescale-modal").hidden = true; }

  function openDiscardModal(plan) {
    const body = $("discard-modal-body");
    if (body) {
      body.innerHTML = `<p>증량하면 총 배합량이 25,000 g 을 초과합니다 `
        + `(예상 ${fmt(plan.newTotal)} g). 폐기를 권장합니다.</p>`;
    }
    $("discard-modal").hidden = false;
  }
  function closeDiscardModal() { $("discard-modal").hidden = true; }

  // 증량 적용 — 모달 [증량 적용] 또는 #discard-modal [그래도 증량].
  // 일반 레시피: 총량 입력값을 newTotal 로 갱신 후 input 이벤트로 이론 재계산 경로 재사용.
  // 기준 자재 레시피: state.rescaleTotalG 를 newTotal 로 올려 도출 총량·이론량·추가분을 갱신.
  // 두 경로 모두 저장 차단·서버 본문은 건드리지 않는다(서버는 총량×비율로 재산출).
  function applyRescale() {
    const plan = state.pendingRescale;
    if (!plan) return;
    state.pendingRescale = null;
    closeRescaleModal();
    closeDiscardModal();
    if (hasAnchor()) {
      // 기준 파생 총량을 넘는 증량분을 보관 — applyAnchorRecompute 가 max 로 반영.
      if (plan.newTotal > (state.rescaleTotalG || 0)) state.rescaleTotalG = plan.newTotal;
      recomputeAnchorRescale(plan);
    } else {
      const totalInput = $("blend-total");
      totalInput.value = String(plan.newTotal);
      // 총량 input 이벤트 경로 재사용 — 이론량 재계산·표 갱신.
      totalInput.dispatchEvent(new Event("input"));
    }
    // 계량된 행에 '추가로 넣을 양' 배지 표시(잔여 addNeeded).
    renderAddBadges();
    notify(`배합량을 ${fmt(plan.newTotal)} g 으로 증량했습니다 — 추가분을 계량하세요.`, "warn");
  }

  // 기준 자재 레시피 증량 반영 — rescalePlan 의 newTheory/addNeeded 를 각 행에 적용.
  // 기준 자재 행도 이론량이 newTheory 로 갱신되고 addNeeded 배지가 표시된다
  // (기준 자재도 추가로 넣어야 총량이 실제로 커진다).
  function recomputeAnchorRescale(plan) {
    const totalInput = $("blend-total");
    if (totalInput) totalInput.value = String(plan.newTotal);
    plan.rows.forEach((r) => {
      const it = state.items[r.idx];
      if (!it || r.newTheory === null) return;
      it.theory_amount = r.newTheory;
      const cell = document.querySelector(`.blend-theory[data-idx="${r.idx}"]`);
      if (cell) cell.textContent = fmt(r.newTheory);
    });
    state.items.forEach((_, i) => updateRowVar(i));
    updateTotals();
    updateLotPreview();
  }

  // 행별 잔여 추가분 배지 렌더링 — addNeeded>0 인 계량 행에 주황 배지(클릭 시 인라인 입력).
  // 추가 후 잔여 ≤ 허용 편차면 배지 제거.
  function renderAddBadges() {
    document.querySelectorAll("#blend-mat-body .blend-add-badge").forEach((el) => el.remove());
    const tol = state.toleranceG;
    const plan = rescalePlan(state.items, effectiveCurrentTotal(), tol);
    plan.rows.forEach((r) => {
      if (r.addNeeded === null || r.addNeeded <= tol + 1e-9) return;
      const td = document.querySelector(`.blend-var[data-idx="${r.idx}"]`);
      if (!td) return;
      const badge = document.createElement("button");
      badge.type = "button";
      badge.className = "blend-add-badge";
      badge.dataset.idx = String(r.idx);
      badge.textContent = `추가 +${fmt(r.addNeeded)} g`;
      badge.title = "클릭해서 추가분을 입력하세요 (저울 PRINT 도 추가분으로 합산됩니다)";
      badge.addEventListener("click", () => openAddInline(r.idx));
      td.appendChild(badge);
    });
  }

  // 행 안 인라인 추가분 입력 — 배지를 작은 input 으로 교체. Enter 확정 시 누계 합산.
  function openAddInline(idx) {
    const td = document.querySelector(`.blend-var[data-idx="${idx}"]`);
    if (!td) return;
    const badge = td.querySelector(".blend-add-badge");
    if (badge) badge.remove();
    if (td.querySelector(".blend-add-inline")) return;
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.min = "0";
    input.className = "input blend-add-inline";
    input.dataset.idx = String(idx);
    input.placeholder = "추가분 g";
    input.title = "추가분 입력 후 Enter — 누계로 합산됩니다";
    // 저울 전용 모드면 증량 추가분 인라인 입력도 잠금(저울 PRINT/addMode 합산으로만).
    if (state.scaleOnlyInput) {
      input.readOnly = true;
      input.title = "저울 전용 모드 — 저울 PRINT 로만 입력됩니다";
    }
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const add = Number(input.value);
      if (!add || !(add > 0)) { input.focus(); return; }
      // Enter 확정 표시 — 입력칸 제거 시 blur 가 한 번 더 발화해 이중 합산되는 것 차단
      input._applied = true;
      applyAddAmount(idx, add);
    });
    input.addEventListener("blur", () => {
      if (input._applied) return;
      const add = Number(input.value);
      if (add > 0) { input._applied = true; applyAddAmount(idx, add); return; }
      // 빈 값으로 벗어나면 취소 — 추가 모드·누계 칸 잠금도 함께 해제해야 한다
      input.remove();
      state.addModeIdx = null;
      const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
      if (actualInput) {
        actualInput.classList.remove("add-mode");
        actualInput.readOnly = false;
      }
      renderAddBadges();
    });
    td.appendChild(input);
    // 이 행을 추가 입력 모드로 — 저울 PRINT 값이 추가분으로 합산된다.
    // 실제량(누계) 칸은 잠근다: 추가 모드 중 직접 타이핑하면 누계가 통째로
    // 덮어써져 기존 계량값이 사라진다(스모크에서 실제 재현된 실수 경로).
    state.addModeIdx = idx;
    const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (actualInput) {
      actualInput.classList.add("add-mode");
      actualInput.readOnly = true;
    }
    input.focus();
  }

  // 추가분을 행의 누계(actual) 에 합산하고 UI 갱신.
  function applyAddAmount(idx, add) {
    const it = state.items[idx];
    if (!it) return;
    const prev = it.actual_amount === "" ? 0 : (Number(it.actual_amount) || 0);
    const next = prev + add;
    it.actual_amount = String(Math.round(next * 1000) / 1000);
    it.manual = false;
    const input = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (input) {
      input.value = it.actual_amount;
      input.classList.remove("manual-warn");
    }
    // 인라인 입력칸 제거 + 추가 모드 해제(단일 추가 완료). 잔여 배지는 renderAddBadges 가 갱신.
    const inline = document.querySelector(`.blend-add-inline[data-idx="${idx}"]`);
    if (inline) inline.remove();
    state.addModeIdx = null;
    const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (actualInput) {
      actualInput.classList.remove("add-mode");
      actualInput.readOnly = false;
    }
    updateRowVar(idx);
    updateTotals();
    warnIfVariance(idx);
    renderAddBadges();
  }

  // 총량을 나중에 입력/변경하면 이론량이 바뀌어 이미 계량한 값이 초과될 수 있다 —
  // 그 순간 바로 알린다(저장 때까지 침묵 금지). 초과 1건이면 상세, 여럿이면 묶어서.
  function warnAllVariance() {
    const tol = state.toleranceG;
    const badIdx = [];
    state.items.forEach((it, i) => {
      if (i === state.anchorIndex || it.actual_amount === "") return;
      if (Math.abs(rowVariance(it)) > tol + 1e-9) badIdx.push(i);
    });
    if (!badIdx.length) return;
    if (badIdx.length === 1) { warnIfVariance(badIdx[0]); return; }
    const names = badIdx.map((i) => state.items[i].material_name).join(", ");
    notify(`허용 편차(±${tol}g) 초과: ${names}. 해당 자재를 다시 계량하세요.`, "error");
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

  // ── 저장 후 자동 로그아웃 ─────────────────────────────────
  // 미저장 입력이 있는 동안은 타임아웃 없음(서버 유휴 12h + 하트비트로 보호).
  // 저장을 마쳐 폼이 빈 상태로 돌아온 뒤에만 카운트를 걸고, 새 입력이
  // 시작되면 즉시 해제한다 — 공용 PC에서 저장 후 방치된 세션 정리.
  const POST_SAVE_LOGOUT_MS = 5 * 60 * 1000;

  function armPostSaveLogout() {
    cancelPostSaveLogout();
    state.postSaveTimer = setTimeout(async () => {
      try { await request("/blend/session/logout", { method: "POST" }); } catch (e) { /* 만료 등 무시 */ }
      window.location.href = "/blend/login?next=/blend";
    }, POST_SAVE_LOGOUT_MS);
  }

  function cancelPostSaveLogout() {
    if (state.postSaveTimer) {
      clearTimeout(state.postSaveTimer);
      state.postSaveTimer = null;
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
    // 자재별 허용 편차 — 초과 자재가 있으면 저장 차단(합계 편차는 제한 없음).
    // 편차는 레시피에서 결정(state.toleranceG). 기준 자재는 편차 검사에서 제외
    // (이론=실측이므로 편차가 무의미).
    const ai = state.anchorIndex;
    const tol = state.toleranceG;
    const bad = state.items.filter((it, i) =>
      i !== ai && Math.abs(rowVariance(it)) > tol + 1e-9
    );
    if (bad.length) {
      err.textContent = varianceBlockMessage(badVarianceNames(bad), tol);
      err.hidden = false;
      notify(`허용 편차 ±${fmt(tol, 2)}g 초과 — 저장할 수 없습니다.`, "error");
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
    // 미등록 LOT 차단 — 반제품(제안 있는 자재) 행의 비어있지 않은 LOT 를 순차 검증.
    // 하나라도 미등록이면 첫 미등록 행의 모달을 띄우고 저장을 중단한다(일반 자재는 제외).
    for (let i = 0; i < state.items.length; i++) {
      const it = state.items[i];
      const name = (it.material_name || "").trim();
      if (!state.lotSuggest || !state.lotSuggest[name]) continue;
      const lot = (it.material_lot || "").trim();
      if (!lot) continue;
      if (!(await checkLotRegistered(name, lot))) {
        const input = document.querySelector(`.blend-lot[data-idx="${i}"]`);
        openLotInvalidModal(name, lot, input || null);
        return;
      }
    }
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
        carried_over: it.carried_over === true,
      })),
    };
    try {
      const rec = await request("/blend/records", { method: "POST", body });
      notify(`배합 실적 저장: ${rec.product_lot} (작업자: ${rec.worker})`, "success");
      // 실제량/LOT 초기화 (연속 작업 편의). 기준 자재 모드면 이론량·총량도 함께 초기화해
      // 다음 배합을 '기준 자재 먼저 계량' 상태로 되돌린다. 이월 표식도 함께 지운다.
      state.items.forEach((it) => {
        it.actual_amount = ""; it.material_lot = ""; it.manual = false; it.carried_over = false;
      });
      if (hasAnchor()) {
        state.items.forEach((it) => { it.theory_amount = null; });
        state.prevAnchorActual = "";
        const totalInput = $("blend-total");
        if (totalInput) totalInput.value = "";
      }
      // 저장 후 다음 배합은 증량분이 없는 깨끗한 상태에서 시작.
      state.rescaleTotalG = 0;
      state.addModeIdx = null;
      state.pendingRescale = null;
      if (state.workerPad) state.workerPad.clear();
      renderMatRows();
      // 저장 완료 → 자동 로그아웃 카운트 시작(새 입력이 시작되면 해제)
      armPostSaveLogout();
      notify("5분간 새 입력이 없으면 자동 로그아웃됩니다", "warn");
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  function bind() {
    const onRecipePick = () => onRecipeChange().catch((e) => notify(e.message, "error"));
    const recipeSel = $("blend-recipe");
    recipeSel.addEventListener("change", onRecipePick);
    // 화면을 계속 띄워두는 단말에서 레시피 추가/개정이 반영되도록 열 때 목록 재조회.
    recipeSel.addEventListener("focus", () => { loadRecipes().catch(() => {}); });
    // 분류 변경 → 레시피 목록 갱신. 분류 select 도 열 때 최신 목록 반영.
    const catSel = $("blend-recipe-cat");
    if (catSel) {
      catSel.addEventListener("change", () => { populateRecipeSelect(); });
      catSel.addEventListener("focus", () => { loadRecipes().catch(() => {}); });
    }
    $("blend-base-links").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".blend-base-link");
      if (!btn) return;
      const base = Number(btn.dataset.value);
      if (!(base > 0)) return;
      const totalInput = $("blend-total");
      totalInput.value = String(base);
      totalInput.dispatchEvent(new Event("input"));  // 이론량 재계산 경로 재사용
      warnAllVariance();  // 이미 계량된 값이 새 이론량 기준으로 초과면 즉시 경고
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
    // 총량 확정(change) 시 — 이미 계량된 자재가 새 이론량 기준으로 초과면 즉시 경고.
    // input(키 입력마다)이 아닌 change 에 걸어 타이핑 중 토스트 스팸을 막는다.
    $("blend-total").addEventListener("change", warnAllVariance);
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
    // 증량 모달 버튼 — hidden 속성 토글만으로 열고 닫는다(display 직접 지정 금지).
    const rescaleApply = $("rescale-apply");
    if (rescaleApply) rescaleApply.addEventListener("click", applyRescale);
    const rescaleCancel = $("rescale-cancel");
    if (rescaleCancel) rescaleCancel.addEventListener("click", () => {
      state.pendingRescale = null;
      closeRescaleModal();
    });
    const discardForce = $("discard-force");
    if (discardForce) discardForce.addEventListener("click", applyRescale);
    const discardCancel = $("discard-cancel");
    if (discardCancel) discardCancel.addEventListener("click", () => {
      // 폐기 선택 — 증량을 적용하지 않는다(기존 초과 토스트·저장 차단 상태 유지).
      state.pendingRescale = null;
      closeDiscardModal();
    });
    // 미등록 LOT 확인 버튼 — 모달 닫고 해당 LOT 칸 값·state 비운 뒤 다시 포커스.
    const lotConfirm = $("lot-invalid-confirm");
    if (lotConfirm) lotConfirm.addEventListener("click", () => {
      const modal = $("lot-invalid-modal");
      const input = modal && modal._lotInput;
      closeLotInvalidModal();
      if (input) {
        const idx = Number(input.dataset.idx);
        if (state.items[idx]) state.items[idx].material_lot = "";
        input.value = "";
        input.focus();
      }
    });
    // 반응기 이월 모달 — 적용/취소. Escape 도 취소(변경 없음).
    const coConfirm = $("carry-over-confirm");
    if (coConfirm) coConfirm.addEventListener("click", applyCarryOver);
    const coCancel = $("carry-over-cancel");
    if (coCancel) coCancel.addEventListener("click", closeCarryOverModal);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("carry-over-modal").hidden) closeCarryOverModal();
    });
    state.workerPad = attachSignaturePad($("blend-worker-sign"));
    const wclr = $("blend-worker-sign-clear");
    if (wclr && state.workerPad) wclr.addEventListener("click", () => state.workerPad.clear());
    $("bulk-add-row").addEventListener("click", addBulkRow);
    $("bulk-create").addEventListener("click", createBulk);
    // 저장 후 자동 로그아웃 해제 — 어떤 폼 입력이든 새 작업이 시작되면(레시피 선택,
    // 실제량·LOT 타이핑, 저울 PRINT 입력 포함) 카운트를 멈춘다. capture 단계라
    // 동적으로 렌더되는 자재 행 입력에도 적용된다.
    document.addEventListener("input", cancelPostSaveLogout, true);
    document.addEventListener("change", cancelPostSaveLogout, true);
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
    // 저울 전용 입력 모드 로드(실패 시 false 폴백). 켜져 있으면 실제량 입력칸 잠금.
    loadScaleOnlyInput();
    // 작업자 세션 하트비트는 전 화면 공통(common.js)으로 이동 — 배합↔점도↔기록
    // 어디에 있든 세션이 유지된다.
    request("/viscosity/products")
      .then((d) => { state.viscProducts = (d.items || []).filter((p) => p.is_active); })
      .catch(() => {});
  });
})();

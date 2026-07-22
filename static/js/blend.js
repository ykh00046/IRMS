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
    missingLotNames,
    missingLotBlockMessage,
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

  const state = { recipes: [], current: null, items: [], viscProducts: [], workers: [], scaleReady: false, sessionWorker: "", anchorIndex: -1, prevAnchorActual: "", toleranceG: TOLERANCE_G, _anchorRecomputing: false,
    // 반제품 원료 LOT 자동 제안: 레시피 자재명 → 최근 product_lot 목록.
    // 자재명이 "배합 기록이 있는 반제품명"과 일치하면 그 제품의 최근 LOT 을 제안.
    // 레시피 선택 시 1회 호출(실패는 조용히 무시 — 제안 없이 기존 동작 유지).
    lotSuggest: {},
    // 미등록 LOT 차단 — (자재명\u0000LOT) → true(등록됨)/false(미등록) 캐시.
    // 동일 (name, lot) 조합의 중복 조회를 막기 위해 한 번 판정하면 보관한다.
    // 레시피가 바뀌면 lotSuggest 와 함께 새로 채워지므로 여기서는 만료 처리하지 않는다.
    lotChecked: {},
    // 미등록 LOT '사유 입력 후 진행' 승인 — (자재명\u0000LOT) → 사유. 승인된 조합은
    // 검증·저장에서 통과시키고, 사유는 저장 시 비고에 남겨 책임자가 사후 확인한다.
    // 레시피 변경·저장 시 초기화.
    lotOverrides: {},
    // 초과 계량 증량(rescale). 기준 자재 레시피에서 총량이 기준 자재 실측값으로
    // 파생되므로 증량분을 별도로 보관 — 유효 총량 = max(기준 파생 총량, rescaleTotalG).
    // 레시피 변경/저장 후 초기화 시 0(미사용)으로 리셋.
    rescaleTotalG: 0,
    // 추가분 입력 모드에 들어간 행 인덱스(저울 PRINT 를 추가분으로 합산하기 위한 플래그).
    addModeIdx: null,
    // 보류 중인 증량 제안(newTotal) — discard 모달에서 '그래도 증량' 선택 시 재사용.
    pendingRescale: null,
    // 증량 후 각 행의 '더 넣어야 할 양'(idx→addNeeded>tol). 편차 셀에 음수(부족) 대신
    // 배지로 넣을 양(양수)만 보이도록 하는 판정에 쓴다. renderAddBadges 가 매번 갱신.
    addPending: {},
    // 증량이 한 번이라도 적용됐는가. true 인 동안만 계량 변경 시 '추가 +X' 배지를 갱신한다
    // (증량 전 일반 계량에서는 미달 행에 배지를 띄우지 않고 음수 편차 그대로 둔다).
    rescaleActive: false,
    // 증량 적용 요약줄(#rescale-applied-summary) 표시용 plan 스냅샷. 저장·초기화·
    // 레시피 변경 시까지 유지(타이핑 중에는 사라지지 않는다).
    rescaleAppliedPlan: null,
    // '방금 증량 취소'용 스냅샷(증량 직전 총량·이론량). 추가분을 넣기 시작하거나
    // 레시피 변경·저장 시 무효화(null). 있으면 #rescale-undo 버튼이 보인다.
    rescaleUndo: null,
    // 저울 전용 입력 모드(운영 대시보드 토글). true 면 실제량·증량 인라인 입력이
    // readonly 가 되고 저울 PRINT 로만 입력된다. false(기본)면 동작 변화 없음.
    scaleOnlyInput: false,
    // 증량 승인 이벤트 목록 — 증량 1회당 1건. 책임자 승인({approval_id, approver}) 또는
    // 책임자 부재 진행({absence_reason})으로 구분. 저장 payload(rescale_events)로 전송.
    // 레시피 변경·저장 시 [] 로 초기화, '방금 증량 취소' 시 마지막 1건 pop.
    // length>=2 이면 3회째 증량 제안 자체가 차단된다(책임자 폐기 협의 유도).
    rescaleEvents: [],
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
    // 저울 PRINT 값이 허용 편차를 벗어나면 다음 LOT 로 넘어가지 않는다 — 해당 실제량
    // 칸에 머물러 재계량(부족: 더 넣기 / 초과: 증량 제안)을 유도.
    if (warnIfVariance(idx)) {
      if (input) { input.focus(); if (input.select) input.select(); }
      return;
    }
    const nextLot = document.querySelector(`.blend-lot[data-idx="${idx + 1}"]`);
    if (nextLot) {
      nextLot.focus();
    } else {
      const save = $("blend-save");
      if (save) save.focus();
    }
  }

  // PRINT 키 입력이 들어갈 행: 합산 모드 행 > 커서가 있는 행(LOT/실제량) > 첫 미입력 행
  function activeScaleRow() {
    // 추가(합산) 모드 중이면 PRINT 는 무조건 그 행으로. 인라인 추가 입력칸은
    // blend-actual/blend-lot 클래스가 아니어서 포커스 감지에 안 걸리고, 그 행의
    // actual 은 이미 채워져 있어 폴백도 그 행을 건너뛰었다 — 부족 보충 PRINT 가
    // 엉뚱한 빈 행으로 가던 버그(2026-07-22 흐름 재검토 BUG-1). 저울 전용 모드의
    // 부족 복구(타이핑 불가)도 이 라우팅이 있어야 성립한다.
    if (state.addModeIdx != null) return state.addModeIdx;
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
    if (idx >= 0) return idx;
    // 기준 자재 레시피는 기준 계량 전 모든 이론이 null — 위 폴백이 못 찾아 PRINT 가
    // 무시되던 공백(GAP-3). 기준 자재 행이 비어 있으면 그 행으로 라우팅한다.
    if (
      state.anchorIndex >= 0
      && state.items[state.anchorIndex]
      && state.items[state.anchorIndex].actual_amount === ""
    ) {
      return state.anchorIndex;
    }
    return null;
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
    state.addPending = {};
    state.rescaleActive = false;
    state.rescaleAppliedPlan = null;
    state.rescaleUndo = null;
    state.rescaleEvents = [];  // 레시피 변경 → 증량 승인 이력 초기화(총 배합량 잠금도 함께 해제)
    state.lotOverrides = {};
    hideRescaleUndo();
    clearRescaleSummary();
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

  // ── 배합 임시 저장·복구 ──────────────────────────────────────
  // 공용 PC 에서 배합 중 자동 로그아웃·창 닫힘으로 계량값이 날아가는 것을 막는다.
  // 진행 중 입력을 이 PC 의 localStorage 에 저장하고(서버·다른 작업 무관), 다음 진입 시
  // 이어서 할지 배너로 묻는다. 저장 완료·버리기 시 삭제. 24시간 지난 초안은 제안 안 함.
  const DRAFT_KEY = "irms.blend.draft";
  let _draftTimer = null;

  function currentDraft() {
    if (!state.current || !state.current.recipe) return null;
    const hasInput = state.items.some((it) =>
      (it.actual_amount !== "" && it.actual_amount != null) || (it.material_lot || "").trim());
    if (!hasInput) return null;  // 의미 있는 입력이 없으면 초안 없음
    return {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      total: $("blend-total").value,
      date: $("blend-date").value,
      time: $("blend-time").value,
      scale: $("blend-scale").value,
      note: $("blend-note").value,
      reactor: $("blend-reactor").value,
      rescaleTotalG: state.rescaleTotalG || 0,
      // 증량 승인 이력 — 각 증량의 before/after 총량 + 승인(approval_id/approver) 또는
      // 부재(absence_reason). 초안에 반드시 함께 보관해야 복구 후 저장 payload(rescale_events)
      // 로 전송되어 추적성이 유지된다(누락 시 서버가 '증량 없음'으로 조용히 저장 — 추적 구멍).
      rescaleEvents: (state.rescaleEvents || []).map((ev) => ({ ...ev })),
      lotOverrides: state.lotOverrides || {},
      items: state.items.map((it) => ({
        material_lot: it.material_lot || "",
        actual_amount: (it.actual_amount === "" || it.actual_amount == null) ? "" : String(it.actual_amount),
        carried_over: it.carried_over === true,
        manual: it.manual === true,
      })),
      savedAt: new Date().toISOString(),
    };
  }

  function scheduleDraftSave() {
    if (_draftTimer) clearTimeout(_draftTimer);
    _draftTimer = setTimeout(() => {
      try {
        const d = currentDraft();
        if (d) localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
      } catch (_e) { /* 저장공간 없음 등 무시 */ }
    }, 600);
  }

  function clearDraft() {
    if (_draftTimer) { clearTimeout(_draftTimer); _draftTimer = null; }
    try { localStorage.removeItem(DRAFT_KEY); } catch (_e) { /* 무시 */ }
  }

  function readDraft() {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (!raw) return null;
      const d = JSON.parse(raw);
      // 24시간 지난 초안은 무시(오래된 잔여 방지).
      if (d && d.savedAt && (Date.now() - Date.parse(d.savedAt)) > 24 * 3600 * 1000) return null;
      return d;
    } catch (_e) { return null; }
  }

  // 진입 시 초안이 있으면 배너로 이어서 할지 묻는다(폼이 비어 있을 때만).
  function offerRestore() {
    const banner = $("blend-restore-banner");
    if (!banner) return;
    const draft = readDraft();
    if (!draft || !draft.recipe_id) { banner.hidden = true; return; }
    const label = $("blend-restore-label");
    if (label) {
      const when = draft.savedAt ? draft.savedAt.slice(0, 16).replace("T", " ") : "";
      label.textContent = `작성 중이던 '${draft.product_name || ""}' 배합이 있습니다${when ? ` (${when})` : ""} — 이어서 하시겠어요?`;
    }
    banner.hidden = false;
  }

  async function restoreDraft() {
    const draft = readDraft();
    if (!draft || !draft.recipe_id) return;
    const recipeSel = $("blend-recipe");
    recipeSel.value = String(draft.recipe_id);
    await onRecipeChange();  // 레시피 로드 + 렌더(빈 상태) — 이후 초안 값을 덮어씌운다.
    if (draft.date) $("blend-date").value = draft.date;
    if (draft.time) $("blend-time").value = draft.time;
    if (draft.scale) $("blend-scale").value = draft.scale;
    if (draft.note) $("blend-note").value = draft.note;
    if (draft.reactor) $("blend-reactor").value = draft.reactor;
    state.lotOverrides = draft.lotOverrides || {};
    state.rescaleTotalG = draft.rescaleTotalG || 0;
    if (state.rescaleTotalG > 0) state.rescaleActive = true;
    // 증량 승인 이력 복구 — onRecipeChange 가 이미 [] 로 리셋했으므로 초안 값으로 되살린다.
    // (얕은 복사로 원본 초안 객체와 분리.) 승인 이력이 있으면 증량 활성으로 간주해
    // 총량 잠금·추가분 배지가 다시 뜨게 한다(일반 레시피는 rescaleTotalG=0 이라 이 신호가 필요).
    state.rescaleEvents = Array.isArray(draft.rescaleEvents)
      ? draft.rescaleEvents.map((ev) => ({ ...ev }))
      : [];
    if (state.rescaleEvents.length) state.rescaleActive = true;
    // '방금 증량 취소' 스냅샷은 세션을 넘겨 복구하지 않는다 — 직전 상태는 이번 세션에서만
    // 의미가 있다. null 로 두어 복구 후 '방금 증량 취소' 버튼이 계속 비활성(숨김)으로 남게 한다.
    state.rescaleUndo = null;
    (draft.items || []).forEach((di, i) => {
      if (!state.items[i]) return;
      state.items[i].material_lot = di.material_lot || "";
      state.items[i].actual_amount = di.actual_amount === "" ? "" : di.actual_amount;
      state.items[i].carried_over = di.carried_over === true;
      state.items[i].manual = di.manual === true;
    });
    if (!hasAnchor() && draft.total) $("blend-total").value = draft.total;
    renderMatRows();  // state 값으로 다시 그림(actual/lot 표시)
    if (hasAnchor()) {
      // 기준 자재 값에서 이론·총량 재산출. prevAnchorActual="" 로 두어 '값 변경→나머지 초기화'
      // 경로를 타지 않게 한다(복원은 변경이 아님).
      state.prevAnchorActual = "";
      state._anchorRecomputing = true;
      try { applyAnchorRecompute(); } finally { state._anchorRecomputing = false; }
    } else if (draft.total) {
      $("blend-total").dispatchEvent(new Event("input"));
    }
    state.items.forEach((_, i) => updateRowVar(i));
    updateTotals();   // updateTotalLock 포함 — 실측이 있으면 총 배합량 잠금 재적용
    updateLotPreview();
    updateInputGuide();
    hideRescaleUndo();  // 복구 세션엔 '방금 증량 취소' 없음(스냅샷을 복구하지 않으므로)
    const banner = $("blend-restore-banner");
    if (banner) banner.hidden = true;
    notify("작성 중이던 배합을 복원했습니다.", "success");
    if (state.rescaleEvents.length) {
      // 증량 이력이 있으면 총량 잠금·추가분 배지를 명시적으로 다시 그리고 1회 안내한다.
      updateTotalLock();
      renderAddBadges();
      notify(`복구된 배합에 증량 ${state.rescaleEvents.length}회가 포함되어 있습니다.`, "warn");
    } else if (state.rescaleActive) {
      renderAddBadges();
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
    updateNextWeighGuide();
  }

  // 계량 순서 안내 — 다음에 계량할 자재(실제량이 빈 첫 행)를 강조한다. 기준 자재 레시피는
  // 기준 자재를 먼저. 저울/클릭 입력 작업자에게 다음 순서를 시각적으로 알려준다.
  function updateNextWeighGuide() {
    const body = $("blend-mat-body");
    if (!body) return;
    body.querySelectorAll("tr.row-next").forEach((tr) => tr.classList.remove("row-next"));
    const empty = (v) => v === "" || v == null;
    // 총량 미입력(일반 레시피)·기준 자재 미계량 전에는 행 강조를 하지 않는다(안내가 앞서지 않게).
    if (!hasAnchor() && !(Number($("blend-total").value) > 0)) return;
    let nextIdx = -1;
    if (hasAnchor() && empty(state.items[state.anchorIndex] && state.items[state.anchorIndex].actual_amount)) {
      nextIdx = state.anchorIndex;
    } else {
      nextIdx = state.items.findIndex((it) => empty(it.actual_amount));
    }
    if (nextIdx < 0) return;  // 모두 계량됨
    const inp = document.querySelector(`.blend-actual[data-idx="${nextIdx}"]`);
    const tr = inp && inp.closest("tr");
    if (tr) tr.classList.add("row-next");
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
        // 증량이 적용된 상태에서 계량하면 '추가 +X'(양수) 배지를 갱신 — 증량 후 채우는
        // 행도 음수 편차 대신 넣을 양이 양수로 보이게 한다. 증량 전에는 갱신하지 않는다.
        if (state.rescaleActive) renderAddBadges();
        updateNextWeighGuide();  // 다음 계량 행 강조 갱신
        scheduleDraftSave();  // 진행분 임시 저장(복구용)
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
        scheduleDraftSave();  // 진행분 임시 저장(복구용)
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
        // 초과·부족 어느 쪽이든 허용 편차를 벗어난 값이 들어있는 채로는 다음 LOT 로
        // 내려가지 않는다(2026-07-22 현장 요구) — 현재 칸에 머물러 재계량/증량을 유도.
        if (warnIfVariance(Number(el.dataset.idx))) {
          el.focus();
          if (el.select) el.select();
          return;
        }
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
    if (lotOverrideKey(name, lot) in state.lotOverrides) return;  // 사유 입력 후 진행 승인됨 → 통과
    if (await checkLotRegistered(name, lot)) return;  // 등록됨 → 통과
    // 미등록 — 모달 표시. 확인 버튼(hideLotInvalidModal 핸들러)이 값 비우기를 맡는다.
    openLotInvalidModal(name, lot, input);
  }

  function lotOverrideKey(name, lot) { return `${name}\u0000${lot}`; }

  // 저장 시 비고에 남길 미등록 LOT 진행 사유 — 실제로 저장에 포함된 승인 조합만.
  function buildOverrideNote() {
    const parts = [];
    state.items.forEach((it) => {
      const name = (it.material_name || "").trim();
      const lot = (it.material_lot || "").trim();
      if (!lot) return;
      const key = lotOverrideKey(name, lot);
      if (key in state.lotOverrides) {
        parts.push(`[미등록 LOT 진행] ${name}/${lot}: ${state.lotOverrides[key]}`);
      }
    });
    return parts.join("\n");
  }

  // 서버 백업 검증용 구조화 미등록 LOT 사유 — state.lotOverrides(자재명\u0000LOT → 사유)를
  // {material_name, material_lot, reason} 목록으로 풀어 보낸다. 클라이언트 검증이
  // 네트워크 장애로 우회(fail-open)될 수 있어 서버가 같은 규칙으로 재확인한다.
  function buildLotOverrides() {
    const out = [];
    Object.keys(state.lotOverrides || {}).forEach((key) => {
      const sep = key.indexOf("\u0000");
      if (sep < 0) return;
      const material_name = key.slice(0, sep);
      const material_lot = key.slice(sep + 1);
      const reason = String(state.lotOverrides[key] || "").trim();
      if (!material_name || !material_lot || !reason) return;
      out.push({ material_name, material_lot, reason });
    });
    return out;
  }

  function openLotInvalidModal(name, lot, input) {
    const body = $("lot-invalid-modal-body");
    if (body) {
      body.innerHTML = ""
        + `<p><strong>자재명:</strong> ${esc(name)}</p>`
        + `<p><strong>입력한 로트:</strong> ${esc(lot)}</p>`
        + `<p>등록되지 않은 로트입니다. 1차 배합 기록이 저장되었는지, LOT 번호가 맞는지 확인하세요.</p>`
        + `<p class="muted small">1차 기록이 아직 없는 정당한 경우에는 아래에 사유를 적고 진행할 수 있습니다(사유는 기록에 남습니다).</p>`;
    }
    // 우회 사유 입력칸 초기화(닫혀 있는 기본 상태로).
    const box = $("lot-override-box");
    const reason = $("lot-override-reason");
    if (reason) reason.value = "";
    if (box) box.hidden = true;
    const modal = $("lot-invalid-modal");
    modal._lotInput = input || null;
    modal._lotName = name;
    modal._lotValue = lot;
    modal.hidden = false;
  }

  function closeLotInvalidModal() { $("lot-invalid-modal").hidden = true; }

  // ── 파생 이월(carry-over): 기준 자재 행만, 파생 레시피만 ────
  // 1차 배합(반제품)의 총량을 2차 배합 기준 자재의 실제량으로 그대로 가져오는 기능.
  // 반응기에 이미 1차 제품이 남아 있어 2차에서는 다시 계량하지 않는 경우에 쓴다.
  // 서버가 carried_over=true 행의 actual_amount 를 1차 총량으로 강제(변조 방지)하므로,
  // 여기서는 작업자에게 버튼·확인 모달로 흐름을 제공할 뿐이다.

  // 이월 자격 판정 — 현재 기준 자재 행이고 파생(is_derived) 레시피일 때만.
  // 파생은 반응기와 독립: 반응기여도 파생이 아니면 이월 없음(예: SBCT-1 은 반응기이나 시작).
  function carryOverEligible() {
    return Boolean(
      hasAnchor()
      && state.current && state.current.recipe && state.current.recipe.is_derived
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

  // 기준 자재 행의 LOT 칸(<td>) 아래 이월 컨트롤. 파생 레시피의 기준 자재 행에서:
  //  - 로트 선택 전: 안내 힌트("반응기 1차 제품 — 로트를 선택하세요")로 이월을 유도(발견성).
  //  - 등록된 1차 로트 입력 후: '1차 총량 N g' 배지 + [파생 이월] 버튼.
  // 파생이 아닌 레시피/일반 행에서는 아무것도 띄우지 않는다.
  function refreshCarryOverControl() {
    const lotInput = document.querySelector(`.blend-lot[data-idx="${state.anchorIndex}"]`);
    if (!lotInput) return;
    const cell = lotInput.parentElement || lotInput.parentNode;  // <td>
    let wrap = cell.querySelector(".carry-over-wrap");
    if (!carryOverEligible()) {
      if (wrap) wrap.hidden = true;
      return;
    }
    // 컨트롤이 없으면 한 번 만든다(재렌더 후에도 살아남도록 cell 에 부착).
    if (!wrap) {
      wrap = document.createElement("span");
      wrap.className = "carry-over-wrap";
      // 로트 선택 전 안내 힌트(발견성) — 파생 레시피 첫 작업자가 중간체를 그냥 계량하지
      // 않도록 "이건 반응기에 있으니 이월하라"고 먼저 알린다.
      const hint = document.createElement("span");
      hint.className = "carry-over-hint";
      hint.style.cssText = "font-size:0.72rem;color:#64748b;";
      hint.textContent = "반응기 1차 제품 — 로트를 선택해 이월하세요";
      // '1차 총량 N g' 안내 배지
      const badge = document.createElement("span");
      badge.className = "carry-over-badge muted";
      // 이월 버튼
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm carry-over-btn";
      btn.textContent = "파생 이월";
      btn.title = "1차 배합 총량을 이 자재의 실제량으로 가져옵니다";
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openCarryOverModal();
      });
      wrap.appendChild(hint);
      wrap.appendChild(badge);
      wrap.appendChild(btn);
      cell.appendChild(wrap);
    }
    const hint = wrap.querySelector(".carry-over-hint");
    const badge = wrap.querySelector(".carry-over-badge");
    const btn = wrap.querySelector(".carry-over-btn");
    const applied = Boolean(state.items[state.anchorIndex] && state.items[state.anchorIndex].carried_over);
    const match = findStage1Match(lotInput.value);
    // 이미 이월 적용됨 → 실제량 칸의 '이월' 태그가 상태를 표시하므로 컨트롤 숨김.
    if (applied) { wrap.hidden = true; return; }
    if (match) {
      if (hint) hint.hidden = true;
      if (badge) { badge.hidden = false; badge.textContent = `1차 총량 ${match.total} g`; }
      if (btn) btn.hidden = false;
    } else {
      // 로트 미선택/미등록 → 힌트만.
      if (hint) hint.hidden = false;
      if (badge) badge.hidden = true;
      if (btn) btn.hidden = true;
    }
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
        + `<p>앞 단계(1차) 제조물을 이어받아 이 자재는 다시 계량하지 않습니다. `
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
    // 증량 대기 행(더 넣어야 할 양이 있는 행)은 음수 편차 대신 '추가 +X' 배지만 보인다
    // (마이너스 표시는 오해의 여지 — 넣을 양은 양수 배지로만). 배지는 renderAddBadges 가 부착.
    const badge = cell.querySelector(".blend-add-badge");
    const pending = state.addPending && state.addPending[i];
    const display = (pending != null && pending > state.toleranceG + 1e-9)
      ? { text: "", className: "num blend-var" }   // 배지가 넣을 양(양수)을 대신 표시
      : varianceDisplay(it, state.toleranceG);
    cell.textContent = display.text;
    cell.className = display.className;
    if (badge) cell.appendChild(badge);  // textContent 대입이 배지를 지우므로 다시 붙인다
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

  // 같은 행·같은 값 중복 경고 억제 — Enter 로 계량을 마치면 keydown 경고 직후
  // 포커스 이동이 change 이벤트를 또 발생시켜 동일 경고가 2번 뜨던 문제(2026-07-22).
  let _lastVarWarn = { key: "", at: 0 };

  function warnIfVariance(i) {
    const it = state.items[i];
    // 증량 대기 행(추가 배지 표시 중)은 편차 경고 대상이 아니다 — 증량으로 이론량이
    // 커져 생긴 '아직 안 넣은 양'이지 잘못 계량한 게 아니다(오탐 신고 2026-07-22:
    // 정확히 계량한 행이 증량 직후 "-3.00g 초과"로 경고). 배지가 넣을 양을 안내한다.
    // 단, 전면 억제는 과했다 — 증량 이후 '새로 계량하다 부족하게 찍은' 행은 팝업이
    // 떠야 한다(현장 신고 2026-07-22: 배지만 생기고 팝업 없음). 일괄 재검사
    // (warnAllVariance — 총량 변경 경로)는 루프에서 addPending 행을 건너뛰므로
    // 오탐 방지는 그대로 유지되고, 여기(직접 입력 경로)서는 팝업을 막지 않는다.
    // 합산 입력 중(addModeIdx)의 반복 팝업은 아래 부족 분기의 가드가 막는다.
    const v = rowVariance(it);
    const tol = state.toleranceG;
    if (Math.abs(v) > tol + 1e-9) {
      const key = `${i}:${it.actual_amount}`;
      const now = Date.now();
      if (_lastVarWarn.key === key && now - _lastVarWarn.at < 1500) return true;
      _lastVarWarn = { key, at: now };
      notify(varianceWarnMessage(it, v, tol), "error");
      if (v > 0) {
        // +방향(초과 계량): 증량 제안 모달.
        offerRescale();
      } else if (state.addModeIdx !== i) {
        // −방향(부족): 부족량 모달로 '추가로 채우기(합산)' 또는 '다시 계량' 제안.
        // 실수로 저울 영점을 눌러 값이 부족하게 찍힌 경우, 처음부터 재계량이 아니라
        // 추가로 올리는 무게를 합산해 목표를 맞추면 된다 — 추가 버튼 시 그 행에 합산 입력을
        // 연다(저울 PRINT 도 합산). 이미 합산 입력 중(addModeIdx)이면 모달 생략.
        const shortage = Math.abs(v);
        showShortageModal(i, shortage);
      }
      return true;
    }
    return false;
  }

  // ── 부족 계량 모달(shortage) ────────────────────────────────
  // window.confirm([확인]/[취소]) 대신 의미가 적힌 두 버튼 모달. 확인/취소가 뭘
  // 의미하는지 모르던 문제 해결. '추가로 채우기' → 그 행에 합산 입력(openAddInline),
  // '다시 계량'(또는 Esc/overlay) → 해당 실제량 칸 포커스+선택.
  let _shortageIdx = null;  // 모달이 열려 있는 동안 대상 행 인덱스 보관.

  function showShortageModal(i, shortage) {
    const it = state.items[i];
    if (!it) return;
    _shortageIdx = i;
    const text = $("shortage-modal-text");
    if (text) {
      text.textContent =
        `이론 ${fmt(it.theory_amount)} g / 실제 ${fmt(Number(it.actual_amount))} g — ${fmt(shortage, 2)} g 부족`
        + "\n추가로 채우기: 더 올리는 무게(입력·저울 PRINT)가 현재 값에 합산됩니다.";
    }
    $("shortage-modal").hidden = false;
  }
  function closeShortageModal() {
    $("shortage-modal").hidden = true;
    _shortageIdx = null;
  }
  function shortageChooseAdd() {
    const idx = _shortageIdx;
    closeShortageModal();
    if (idx != null) openAddWeighModal(idx);
  }
  function shortageChooseReweigh() {
    const idx = _shortageIdx;
    closeShortageModal();
    if (idx == null) return;
    const input = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (input) { input.focus(); input.select(); }
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
    if (!$("rescale-approve-modal").hidden || !$("rescale-block-modal").hidden) return;
    if (state.pendingRescale) return;
    const currentTotal = effectiveCurrentTotal();
    const plan = rescalePlan(state.items, currentTotal, state.toleranceG);
    if (!plan.changed) return;
    // 3회 금지 — 이미 2회 증량된 배합은 3회째 제안 자체를 막고 폐기 협의를 유도한다.
    // pendingRescale 을 설정하지 않으므로 승인 경로 자체가 도달 불가능해진다.
    if (state.rescaleEvents.length >= 2) {
      openRescaleBlockModal();
      return;
    }
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

  // ── 증량 승인 게이트(책임자 승인 없이는 증량 불가) ─────────────────
  // 증량 적용/그래도 증량 클릭 → 즉시 적용하지 않고 이 모달을 띄운다.
  // [승인]: /api/blend/manager-verify 200 → applyRescale + 승인 이벤트 기록.
  // [부재로 진행]: 사유 필수 + 재확인 → applyRescale + '미승인 증량' 이벤트 기록.
  // 부족 채우기(추가 계량)는 이 경로를 타지 않는다 — 승인 불필요.
  function csrfToken() {
    if (IRMS._core && IRMS._core.getCsrfToken) {
      const t = IRMS._core.getCsrfToken();
      if (t) return t;
    }
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  // 증량 재승인(re-auth) 대기 플래그 — 복구된 초안의 만료 승인(approval_id)을
  // 책임자 인증으로 갱신하는 중일 때만 true. 이때 승인 모달의 [승인] 은 새 증량을
  // 확정(finalizeRescale)하지 않고 기존 승인 이벤트의 approval_id 만 갈아끼운다.
  let _rescaleReauthPending = false;

  function openRescaleApproveModal() {
    // 제안/폐기 모달을 닫고 승인 모달을 연다(pendingRescale 은 그대로 보존).
    closeRescaleModal();
    closeDiscardModal();
    const modal = $("rescale-approve-modal");
    if (!modal) return;
    const nameEl = $("rescale-approve-name");
    const pwEl = $("rescale-approve-pw");
    const reasonEl = $("rescale-absence-reason");
    if (nameEl) nameEl.value = "";
    if (pwEl) pwEl.value = "";
    if (reasonEl) reasonEl.value = "";
    hideApproveError();
    modal.hidden = false;
    if (nameEl) nameEl.focus();
  }

  function closeRescaleApproveModal() {
    const modal = $("rescale-approve-modal");
    if (modal) modal.hidden = true;
  }

  // 승인/부재 모달을 취소(Escape/overlay) — 보류 중인 증량 제안을 버린다.
  // 초과 계량 상태는 그대로라 다음 change/Enter 에서 다시 제안이 뜬다.
  function cancelRescaleApprove() {
    state.pendingRescale = null;
    // 재승인 도중 취소면 대기 플래그도 해제 — 이벤트의 만료 approval_id 는 그대로 남고,
    // 작업자가 다시 저장하면 서버 400 → beginRescaleReauth 가 재발동한다(재시도 가능).
    _rescaleReauthPending = false;
    closeRescaleApproveModal();
  }

  function showApproveError(msg) {
    const err = $("rescale-approve-error");
    if (err) { err.textContent = msg; err.hidden = false; }
  }
  function hideApproveError() {
    const err = $("rescale-approve-error");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  // 증량 확정 — pendingRescale 소비 전에 before/after 총량을 잡아 이벤트를 기록한다.
  // applyRescale 이 state.pendingRescale 을 null 로 만들므로 순서가 중요하다.
  function finalizeRescale(meta) {
    const plan = state.pendingRescale;
    if (!plan) return;
    const before_total = effectiveCurrentTotal();
    const after_total = plan.newTotal;
    applyRescale();  // 기존 직접 경로가 쓰던 바로 그 함수(총량·이론량·배지 갱신)
    const ev = { before_total, after_total };
    if (meta && meta.approval_id != null) ev.approval_id = meta.approval_id;
    if (meta && meta.approver != null) ev.approver = meta.approver;
    if (meta && meta.absence_reason != null) ev.absence_reason = meta.absence_reason;
    state.rescaleEvents.push(ev);
  }

  async function submitManagerApproval() {
    const nameEl = $("rescale-approve-name");
    const pwEl = $("rescale-approve-pw");
    const name = nameEl ? nameEl.value.trim() : "";
    const pw = pwEl ? pwEl.value : "";
    if (!name) { showApproveError("책임자 이름을 입력하세요."); if (nameEl) nameEl.focus(); return; }
    if (!pw) { showApproveError("비밀번호를 입력하세요."); if (pwEl) pwEl.focus(); return; }
    hideApproveError();
    const btn = $("rescale-approve-submit");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/blend/manager-verify", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
        body: JSON.stringify({ username: name, password: pw }),
      });
      if (res.status === 401) { showApproveError("비밀번호가 올바르지 않습니다."); return; }
      if (res.status === 403) { showApproveError("책임자 권한이 없습니다."); return; }
      if (!res.ok) { showApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요."); return; }
      const data = await res.json().catch(() => ({}));
      if (_rescaleReauthPending) {
        // ── 증량 재승인 처리 ──
        // 복구된 초안의 승인 이벤트(approval_id)들이 30분 TTL 을 넘겨 저장이 400 났다.
        // 이미 한 번 승인된 증량이므로 새 결정이 아니라 재검증 — 작업자 1회 인증으로
        // 모든 승인 이벤트를 살아있는 새 토큰으로 교체한다. 단, 서버는 approval_id 를
        // 1건당 1회만 소비(used=1)하므로 승인 이벤트가 여러 건이면 건마다 별도 토큰이
        // 필요하다: 첫 건은 방금 발급받은 토큰을 쓰고, 나머지는 같은 자격증명으로 추가
        // 발급한다. 부재(absence_reason) 이벤트는 만료 개념이 없어 손대지 않는다.
        const approvedIdx = state.rescaleEvents
          .map((ev, i) => (ev.approval_id != null ? i : -1))
          .filter((i) => i >= 0);
        const freshIds = [data.approval_id];
        try {
          for (let k = 1; k < approvedIdx.length; k++) {
            const r2 = await fetch("/api/blend/manager-verify", {
              method: "POST",
              credentials: "same-origin",
              headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
              body: JSON.stringify({ username: name, password: pw }),
            });
            if (!r2.ok) throw new Error("verify failed");
            const d2 = await r2.json().catch(() => ({}));
            freshIds.push(d2.approval_id);
          }
        } catch (_e2) {
          showApproveError("재승인 중 오류가 발생했습니다. 다시 시도하세요.");
          return;  // _rescaleReauthPending 유지 — 재시도 가능
        }
        approvedIdx.forEach((evIdx, k) => {
          state.rescaleEvents[evIdx].approval_id = freshIds[k];
          state.rescaleEvents[evIdx].approver = data.approver || name;
        });
        _rescaleReauthPending = false;
        closeRescaleApproveModal();
        notify(`책임자 재승인 완료 (${data.approver || name}) — 다시 저장합니다.`, "success");
        saveBlend();  // 갱신된 토큰으로 저장 재시도
        return;
      }
      closeRescaleApproveModal();
      finalizeRescale({ approval_id: data.approval_id, approver: data.approver || name });
      notify(`책임자 승인 완료 (${data.approver || name}) — 증량을 적용합니다.`, "success");
    } catch (_e) {
      showApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요.");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function submitAbsenceProceed() {
    if (_rescaleReauthPending) {
      // 재승인은 '이미 승인됐던' 증량의 토큰 갱신 전용 — 부재 진행으로는 처리하지 않는다.
      showApproveError("만료된 증량은 책임자 재승인(비밀번호)으로만 다시 저장할 수 있습니다.");
      return;
    }
    const reasonEl = $("rescale-absence-reason");
    const reason = reasonEl ? reasonEl.value.trim() : "";
    if (!reason) { showApproveError("책임자 부재 사유를 입력하세요."); if (reasonEl) reasonEl.focus(); return; }
    if (!window.confirm("책임자 승인 없이 증량을 적용합니다.\n기록에 '미승인 증량'으로 표시되고 책임자 확인 알림이 반복됩니다.")) return;
    hideApproveError();
    closeRescaleApproveModal();
    finalizeRescale({ absence_reason: reason });
    notify("미승인 증량으로 적용했습니다 — 책임자 확인 전까지 알림이 반복됩니다.", "warn");
  }

  // 복구된 초안 저장이 만료된 승인 토큰 때문에 400 났을 때 호출 — 책임자 재인증 모달을
  // 열어 만료 승인을 갱신하도록 안내한다. 승인 이벤트가 하나도 없으면(부재뿐) 대상 아님.
  function beginRescaleReauth() {
    if (!state.rescaleEvents.some((ev) => ev.approval_id != null)) {
      notify("증량 승인 정보를 확인할 수 없습니다 — 새로 배합을 시작하세요.", "error");
      return;
    }
    _rescaleReauthPending = true;
    notify("증량 승인이 만료되었습니다 — 책임자 재인증 후 다시 저장합니다.", "warn");
    openRescaleApproveModal();
  }

  function openRescaleBlockModal() {
    const modal = $("rescale-block-modal");
    if (modal) { modal.hidden = false; return; }
    notify("3회 증량은 불가합니다 — 이 배합은 책임자와 폐기 여부를 협의하세요.", "error big");
  }
  function closeRescaleBlockModal() {
    const modal = $("rescale-block-modal");
    if (modal) modal.hidden = true;
  }

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
    // 되돌리기용 스냅샷(증량 직전 상태) — '방금 증량 취소' 1회 제공. 실수로 증량이 걸렸을 때
    // 레시피를 다시 고르지 않고 이전 총량·이론량으로 복원한다. 추가분을 넣기 시작하면 무효화.
    const totalEl = $("blend-total");
    state.rescaleUndo = {
      total: totalEl ? totalEl.value : "",
      theories: state.items.map((it) => it.theory_amount),
      rescaleTotalG: state.rescaleTotalG || 0,
    };
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
    // 증량 활성 — 이후 계량 변경 시 '추가 +X' 배지를 갱신한다(양수 표시).
    state.rescaleActive = true;
    state.rescaleAppliedPlan = plan;  // 요약줄 표시용(저장·초기화·레시피 변경 시 까지 유지).
    // 계량된 행에 '추가로 넣을 양' 배지 표시(잔여 addNeeded).
    renderAddBadges();
    showRescaleUndo();
    renderRescaleSummary(plan);
    notify(`배합량을 ${fmt(plan.newTotal)} g 으로 증량했습니다 — 추가분을 계량하세요.`, "warn");
  }

  // 증량 적용 요약줄 — 자재별 '더 넣을 양'을 상시 표시(작업자가 얼마나 넣었는지 잊지 않게).
  // 저장 성공·초기화·레시피 변경 전까지 유지(타이핑 중에는 사라지지 않는다).
  function renderRescaleSummary(plan) {
    // 상단 요약줄은 시선 밖이라 폐기(2026-07-22) — 목표·추가분은 각 행 편차 셀의
    // 배지("목표 Y · 추가 +X")가 상시 표시한다. 함수는 호출부 보존을 위해 no-op 으로 남긴다.
    void plan;
  }
  function clearRescaleSummary() {
    const el = $("rescale-applied-summary");
    if (el) { el.hidden = true; el.innerHTML = ""; }
    state.rescaleAppliedPlan = null;
  }

  // ── 증량 되돌리기(방금 증량 취소) ─────────────────────────────
  // 증량 직전 스냅샷으로 총량·이론량·증량 상태를 복원한다. 추가분을 넣기 전까지만 유효.
  function showRescaleUndo() {
    const btn = $("rescale-undo");
    if (btn) btn.hidden = false;
  }
  function hideRescaleUndo() {
    const btn = $("rescale-undo");
    if (btn) btn.hidden = true;
  }
  function restoreRescaleUndo() {
    const snap = state.rescaleUndo;
    if (!snap) { hideRescaleUndo(); return; }
    state.rescaleTotalG = snap.rescaleTotalG || 0;
    state.rescaleActive = false;
    state.addPending = {};
    state.pendingRescale = null;
    state.rescaleEvents.pop();  // 방금 증량의 승인 이벤트도 함께 되돌린다
    state.items.forEach((it, i) => { it.theory_amount = snap.theories[i]; });
    const totalEl = $("blend-total");
    if (totalEl) totalEl.value = snap.total;
    // 추가분 배지 제거 + 이론 셀·편차·합계 갱신.
    document.querySelectorAll("#blend-mat-body .blend-add-badge").forEach((el) => el.remove());
    document.querySelectorAll("#blend-mat-body .blend-theory").forEach((cell) => {
      const i = Number(cell.dataset.idx);
      if (state.items[i]) cell.textContent = fmt(state.items[i].theory_amount);
    });
    state.items.forEach((_, i) => updateRowVar(i));
    updateTotals();
    updateLotPreview();
    state.rescaleUndo = null;
    state.rescaleAppliedPlan = null;
    hideRescaleUndo();
    clearRescaleSummary();
    notify("증량을 취소하고 이전 배합량으로 되돌렸습니다.", "warn");
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
    // 직전 대기 집합을 기억 — 이번에 빠진(충족된) 행은 편차 표시를 복원해야 한다.
    const prevPending = state.addPending || {};
    // 넣어야 할 양이 있는 행 집합을 새로 만든다(편차 셀 음수 숨김 판정용).
    state.addPending = {};
    plan.rows.forEach((r) => {
      if (r.addNeeded === null || r.addNeeded <= tol + 1e-9) return;
      const td = document.querySelector(`.blend-var[data-idx="${r.idx}"]`);
      if (!td) return;
      state.addPending[r.idx] = r.addNeeded;
      // 음수 편차 텍스트를 지우고 배지(넣을 양 양수)만 남긴다.
      td.textContent = "";
      td.className = "num blend-var";
      const badge = document.createElement("button");
      badge.type = "button";
      badge.className = "blend-add-badge";
      badge.dataset.idx = String(r.idx);
      badge.textContent = r.newTheory != null
        ? `목표 ${fmt(r.newTheory)} · 추가 +${fmt(r.addNeeded)} g`
        : `추가 +${fmt(r.addNeeded)} g`;
      badge.title = "클릭해서 추가분을 입력하세요 (저울 PRINT 도 추가분으로 합산됩니다)";
      badge.addEventListener("click", () => openAddWeighModal(r.idx));
      td.appendChild(badge);
    });
    // 이전에 대기였다가 이번에 충족된 행 — 빈칸으로 남지 않게 편차 표시를 다시 그린다.
    Object.keys(prevPending).forEach((k) => {
      const i = Number(k);
      if (!(i in state.addPending)) updateRowVar(i);
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
    // 저울 해상도(2자리)로 누계 — 3자리가 실제량에 스며드는 것을 막는다.
    it.actual_amount = String(Math.round(next * 100) / 100);
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
    // 추가분을 넣기 시작했으면 증량 되돌리기는 위험(추가 실제량과 이전 이론량이 어긋남) — 무효화.
    state.rescaleUndo = null;
    hideRescaleUndo();
    // 추가 계량 모달이 이 행에 열려 있으면 큰 숫자(남은 양) 갱신 + 자동 완료 판정.
    refreshAddWeighModal(idx);
  }

  // ── 추가 계량 모달(add-weigh) ───────────────────────────────
  // 인라인 추가 입력(openAddInline) 대신 큰 남은 양 숫자를 보며 합산. 저울 PRINT 는
  // addModeIdx 경로(fillScaleValue→applyAddAmount)로 자동 합산 — 모달이 열려 있으면
  // applyAddAmount 끝의 refreshAddWeighModal 이 숫자를 갱신한다.
  // _addWeighIdx 는 모달이 열려 있는 대상 행(addModeIdx 와 별개 — applyAddAmount 가
  // addModeIdx 를 null 로 되돌려도 모달 갱신 판정은 _addWeighIdx 로 한다).
  let _addWeighIdx = null;

  function addWeighRemaining(idx) {
    const it = state.items[idx];
    if (!it) return 0;
    const target = Number(it.theory_amount) || 0;
    const cur = it.actual_amount === "" ? 0 : (Number(it.actual_amount) || 0);
    return Math.max(0, Math.round((target - cur) * 100) / 100);
  }

  function openAddWeighModal(idx) {
    // 모달 요소가 없으면(옛 템플릿) 기존 인라인 추가 입력으로 폴백.
    if (!$("add-weigh-modal")) { openAddInline(idx); return; }
    const it = state.items[idx];
    if (!it) return;
    state.addModeIdx = idx;  // 저울 PRINT 가 이 행으로 라우팅되게(activeScaleRow 경유).
    _addWeighIdx = idx;
    // 헤더 자재명 + 목표/현재/남은 렌더.
    $("add-weigh-title").textContent = `추가 계량 — ${it.material_name}`;
    // 저울 전용 모드면 수동 입력+더하기 줄 숨김(PRINT 만으로 합산).
    const row = $("add-weigh-input-row");
    if (row) row.hidden = Boolean(state.scaleOnlyInput);
    // 반드시 '모달을 연 뒤' 숫자를 그린다 — refreshAddWeighModal 은 닫힌 모달이면
    // 갱신을 건너뛰므로, 열기 전에 부르면 목표/현재/남은이 초기 "-" 로 남는다
    // (현장 신고 2026-07-22: 추가 계량 화면 목표값이 "-" 표시).
    $("add-weigh-modal").hidden = false;
    refreshAddWeighModal(idx);
    const input = $("add-weigh-input");
    if (input) { input.value = ""; if (!state.scaleOnlyInput) input.focus(); }
  }

  // 모달 숫자(남은 양/목표·현재) 갱신 + 자동 완료(목표 도달 시 자동 닫기).
  function refreshAddWeighModal(idx) {
    const modal = $("add-weigh-modal");
    if (!modal || modal.hidden) return;
    if (_addWeighIdx !== idx) return;
    const it = state.items[idx];
    if (!it) return;
    const target = Number(it.theory_amount) || 0;
    const cur = it.actual_amount === "" ? 0 : (Number(it.actual_amount) || 0);
    const remaining = addWeighRemaining(idx);
    const remEl = $("add-weigh-remaining");
    if (remEl) remEl.textContent = `+${fmt(remaining, 1)} g`;
    const subEl = $("add-weigh-sub");
    if (subEl) subEl.textContent = `목표 ${fmt(target, 1)} g · 현재 ${fmt(cur, 1)} g`;
    // 자동 완료 — 남은 양이 허용 편차 이내면 성공 안내 후 닫고 다음 LOT 로.
    if (remaining <= state.toleranceG + 1e-9) {
      notify(`${it.material_name} 추가 계량 완료`, "success");
      finishAddWeighModal(idx);
    }
  }

  // 더하기/Enter — 값 합산 후 입력칸 비우고 포커스(모달 숫자는 applyAddAmount 끝에서 갱신).
  function applyAddWeighInput(idx) {
    const input = $("add-weigh-input");
    if (!input) return;
    const add = Number(input.value);
    if (!add || !(add > 0)) { input.focus(); return; }
    input.value = "";
    applyAddAmount(idx, add);
    // applyAddAmount 가 addModeIdx 를 null 로 되돌리므로 모달 진행 중엔 다시 올린다.
    state.addModeIdx = idx;
    if (!state.scaleOnlyInput) input.focus();
  }

  // 자동 완료 — 값 유지한 채 모달 닫고 다음 LOT 칸(또는 저장 버튼)으로 포커스 이동.
  function finishAddWeighModal(idx) {
    $("add-weigh-modal").hidden = true;
    _addWeighIdx = null;
    state.addModeIdx = null;
    const inline = document.querySelector(`.blend-add-inline[data-idx="${idx}"]`);
    if (inline) inline.remove();
    const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
    if (actualInput) { actualInput.classList.remove("add-mode"); actualInput.readOnly = false; }
    renderAddBadges();
    // 다음 자재의 LOT 칸으로 — 없으면(마지막 행) 저장 버튼.
    const next = idx + 1;
    const nextLot = document.querySelector(`.blend-lot[data-idx="${next}"]`);
    if (nextLot) nextLot.focus();
    else { const save = $("blend-save"); if (save) save.focus(); }
  }

  // 수동 닫기 — 완료(값 유지) 또는 다시 계량(실제량 비움). addModeIdx 해제·배지 갱신.
  function closeAddWeighModal(idx, keepValue) {
    $("add-weigh-modal").hidden = true;
    _addWeighIdx = null;
    state.addModeIdx = null;
    if (!keepValue && idx != null) {
      const it = state.items[idx];
      if (it) it.actual_amount = "";
      const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
      if (actualInput) {
        actualInput.value = "";
        actualInput.classList.remove("add-mode");
        actualInput.readOnly = false;
        actualInput.focus();
        if (typeof actualInput.select === "function") {
          try { actualInput.select(); } catch (_e) { /* noop */ }
        }
      }
      updateRowVar(idx);
      updateTotals();
    } else {
      // 완료 — 잔여 인라인 입력 칸·배지 정리. 편차는 다음 상호작용 때 warn 흐름에 맡긴다.
      const inline = document.querySelector(`.blend-add-inline[data-idx="${idx}"]`);
      if (inline) inline.remove();
      const actualInput = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
      if (actualInput) { actualInput.classList.remove("add-mode"); actualInput.readOnly = false; }
    }
    renderAddBadges();
  }

  // 총량을 나중에 입력/변경하면 이론량이 바뀌어 이미 계량한 값이 초과될 수 있다 —
  // 그 순간 바로 알린다(저장 때까지 침묵 금지). 초과 1건이면 상세, 여럿이면 묶어서.
  function warnAllVariance() {
    const tol = state.toleranceG;
    const badIdx = [];
    state.items.forEach((it, i) => {
      if (i === state.anchorIndex || it.actual_amount === "") return;
      if (state.addPending && state.addPending[i] != null) return;  // 증량 대기 — 배지가 안내
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
    updateTotalLock();
  }

  // 총 배합량 잠금 — 자재 실제량이 하나라도 입력되면 총 배합량을 바꿀 수 없다
  // (변경은 승인된 증량으로만 — applyRescale 은 프로그램적으로 .value 를 갱신하므로
  // readOnly 여도 계속 동작한다). 기준 자재 레시피는 이미 총량이 읽기 전용이라 제외.
  // 잠금 중에는 기준량 빠른 채우기 버튼도 비활성화한다. 초기화/레시피 변경 시 자동 해제.
  function updateTotalLock() {
    const totalInput = $("blend-total");
    if (!totalInput) return;
    const anyActual = state.items.some(
      (it) => it.actual_amount !== "" && it.actual_amount != null
    );
    const links = $("blend-base-links");
    if (links) {
      links.querySelectorAll(".blend-base-link").forEach((b) => { b.disabled = anyActual; });
    }
    if (hasAnchor()) return;  // 기준 자재 레시피는 applyAnchorMode 가 이미 읽기 전용 처리
    if (anyActual) {
      totalInput.readOnly = true;
      totalInput.title = "계량 시작 후에는 총 배합량을 바꿀 수 없습니다 (변경은 승인된 증량으로만)";
    } else {
      totalInput.readOnly = false;
      totalInput.removeAttribute("title");
    }
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
    // 자재 LOT 필수 — 실제량을 넣은 행은 LOT 도 반드시 입력. 미등록 LOT '사유 적고 진행'
    // 으로 승인된 행은 이미 material_lot 가 채워져 있어 여기서 만족된다(사유 분기 불필요).
    const lotMissing = missingLotNames(state.items);
    if (lotMissing.length) {
      const msg = missingLotBlockMessage(lotMissing);
      err.textContent = msg; err.hidden = false;
      notify("자재 LOT 를 입력하세요: " + lotMissing.slice(0, 6).join(", ") + (lotMissing.length > 6 ? " …" : ""), "error");
      const firstMissingIdx = state.items.findIndex((it) =>
        (it.actual_amount !== "" && Number(it.actual_amount) > 0) &&
        String(it.material_lot || "").trim() === ""
      );
      if (firstMissingIdx >= 0) {
        const input = document.querySelector(`.blend-lot[data-idx="${firstMissingIdx}"]`);
        if (input) input.focus();
      }
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
      if (lotOverrideKey(name, lot) in state.lotOverrides) continue;  // 사유 입력 후 진행 승인됨
      if (!(await checkLotRegistered(name, lot))) {
        const input = document.querySelector(`.blend-lot[data-idx="${i}"]`);
        openLotInvalidModal(name, lot, input || null);
        return;
      }
    }
    // 승인된 미등록 LOT 이 실제로 저장에 포함되면 사유를 비고 앞에 남긴다(책임자 사후 확인).
    const overrideNote = buildOverrideNote();
    const lotOverrides = buildLotOverrides();
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
      note: [overrideNote, $("blend-note").value.trim()].filter(Boolean).join("\n") || null,
      reactor: reactorRaw ? Number(reactorRaw) : null,
      worker_sign: state.workerPad ? state.workerPad.dataUrl() : null,
      // 서버 백업: 미등록 LOT 사유를 구조화해 보내 클라이언트 fail-open 구멍을 막는다.
      lot_overrides: lotOverrides.length ? lotOverrides : null,
      // 증량 승인 이력 — 각 증량의 before/after 총량 + 승인(approval_id/approver) 또는
      // 부재 진행(absence_reason). 없으면 null. 서버가 유효성(승인 실재 여부)을 재검증한다.
      rescale_events: state.rescaleEvents.length ? state.rescaleEvents : null,
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
      clearDraft();  // 저장 완료 → 임시 저장 삭제
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
      state.addPending = {};
      state.rescaleActive = false;
      state.rescaleUndo = null;
      state.rescaleEvents = [];  // 저장 완료 → 증량 승인 이력 초기화(총 배합량 잠금 해제)
      state.lotOverrides = {};
      hideRescaleUndo();
      clearRescaleSummary();
      if (state.workerPad) state.workerPad.clear();
      renderMatRows();
      // 저장 완료 → 자동 로그아웃 카운트 시작(새 입력이 시작되면 해제)
      armPostSaveLogout();
      notify("5분간 새 입력이 없으면 자동 로그아웃됩니다", "warn");
    } catch (e) {
      // 복구된 초안의 증량 승인(approval_id)은 30분 TTL 을 넘겨 서버가 400 으로 거절할 수 있다.
      // 이 경우 오류만 띄우지 말고 책임자 1회 재인증으로 만료 승인을 갱신 후 자동 재저장한다.
      // (부재 이벤트는 만료 없음 — 승인 이벤트가 있을 때만 재승인 흐름을 탄다.)
      if (String(e.message || "").includes("증량 승인이 유효하지 않습니다") &&
          state.rescaleEvents.some((ev) => ev.approval_id != null)) {
        beginRescaleReauth();
        return;
      }
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
    // [증량 적용]/[그래도 증량] 은 즉시 적용하지 않고 책임자 승인 모달을 띄운다.
    const rescaleApply = $("rescale-apply");
    if (rescaleApply) rescaleApply.addEventListener("click", openRescaleApproveModal);
    const rescaleCancel = $("rescale-cancel");
    if (rescaleCancel) rescaleCancel.addEventListener("click", () => {
      state.pendingRescale = null;
      closeRescaleModal();
    });
    const discardForce = $("discard-force");
    if (discardForce) discardForce.addEventListener("click", openRescaleApproveModal);
    // 증량 승인 모달 — [승인](책임자 검증) / [부재로 진행](사유+재확인). Esc/overlay=취소.
    const approveSubmit = $("rescale-approve-submit");
    if (approveSubmit) approveSubmit.addEventListener("click", () => submitManagerApproval());
    const approvePw = $("rescale-approve-pw");
    if (approvePw) approvePw.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      submitManagerApproval();
    });
    const absenceSubmit = $("rescale-absence-submit");
    if (absenceSubmit) absenceSubmit.addEventListener("click", submitAbsenceProceed);
    const approveModal = $("rescale-approve-modal");
    if (approveModal) approveModal.addEventListener("click", (e) => {
      if (e.target === approveModal) cancelRescaleApprove();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && approveModal && !approveModal.hidden) cancelRescaleApprove();
    });
    // 3회 증량 차단 모달 — 확인만.
    const blockClose = $("rescale-block-close");
    if (blockClose) blockClose.addEventListener("click", closeRescaleBlockModal);
    const blockModal = $("rescale-block-modal");
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && blockModal && !blockModal.hidden) closeRescaleBlockModal();
    });
    const discardCancel = $("discard-cancel");
    if (discardCancel) discardCancel.addEventListener("click", () => {
      // 폐기 선택 — 증량을 적용하지 않는다(기존 초과 토스트·저장 차단 상태 유지).
      state.pendingRescale = null;
      closeDiscardModal();
    });
    // 방금 증량 취소 — 증량 직전 상태로 복원(추가분 넣기 전까지만 노출).
    const rescaleUndoBtn = $("rescale-undo");
    if (rescaleUndoBtn) rescaleUndoBtn.addEventListener("click", restoreRescaleUndo);
    // 미등록 LOT '다시 확인' — 모달 닫고 해당 LOT 칸 값·state 비운 뒤 다시 포커스.
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
    // 미등록 LOT '사유 적고 진행'(안전밸브) — 1클릭: 사유칸 표시 / 2클릭(사유 입력됨):
    // 그 (자재,LOT) 조합을 통과 처리하고 사유 보관(저장 시 비고에 남김). 값은 그대로 둔다.
    const lotProceed = $("lot-invalid-proceed");
    if (lotProceed) lotProceed.addEventListener("click", () => {
      const box = $("lot-override-box");
      const reason = $("lot-override-reason");
      if (box && box.hidden) { box.hidden = false; if (reason) reason.focus(); return; }
      const text = (reason && reason.value.trim()) || "";
      if (!text) { notify("진행 사유를 입력하세요.", "error"); if (reason) reason.focus(); return; }
      const modal = $("lot-invalid-modal");
      const name = modal._lotName, lot = modal._lotValue, input = modal._lotInput;
      state.lotOverrides[lotOverrideKey(name, lot)] = text;
      closeLotInvalidModal();
      if (input) input.focus();
      notify("사유를 남기고 진행합니다 — 이 로트는 기록에 '미등록 진행'으로 남습니다.", "warn");
    });
    // 파생 이월 모달 — 적용/취소. Escape 도 취소(변경 없음).
    const coConfirm = $("carry-over-confirm");
    if (coConfirm) coConfirm.addEventListener("click", applyCarryOver);
    const coCancel = $("carry-over-cancel");
    if (coCancel) coCancel.addEventListener("click", closeCarryOverModal);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("carry-over-modal").hidden) closeCarryOverModal();
    });
    // 부족 계량 모달 — 추가로 채우기(합산) / 다시 계량. Esc/overlay 도 '다시 계량'과 동일.
    const shortageAdd = $("shortage-add-btn");
    if (shortageAdd) shortageAdd.addEventListener("click", shortageChooseAdd);
    const shortageReweigh = $("shortage-reweigh-btn");
    if (shortageReweigh) shortageReweigh.addEventListener("click", shortageChooseReweigh);
    const shortageOverlay = $("shortage-modal");
    if (shortageOverlay) shortageOverlay.addEventListener("click", (e) => {
      if (e.target === shortageOverlay) shortageChooseReweigh();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("shortage-modal").hidden) shortageChooseReweigh();
    });
    // 추가 계량 모달 — 더하기/Enter 합산, 완료(값 유지), 다시 계량(비움). Esc/overlay=완료.
    const awModal = $("add-weigh-modal");
    const awAdd = $("add-weigh-add-btn");
    if (awAdd) awAdd.addEventListener("click", () => {
      if (_addWeighIdx != null) applyAddWeighInput(_addWeighIdx);
    });
    const awInput = $("add-weigh-input");
    if (awInput) awInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      if (_addWeighIdx != null) applyAddWeighInput(_addWeighIdx);
    });
    const awDone = $("add-weigh-done-btn");
    if (awDone) awDone.addEventListener("click", () => {
      if (_addWeighIdx != null) closeAddWeighModal(_addWeighIdx, /*keepValue*/ true);
    });
    const awReweigh = $("add-weigh-reweigh-btn");
    if (awReweigh) awReweigh.addEventListener("click", () => {
      if (_addWeighIdx != null) closeAddWeighModal(_addWeighIdx, /*keepValue*/ false);
    });
    if (awModal) awModal.addEventListener("click", (e) => {
      if (e.target === awModal && _addWeighIdx != null) closeAddWeighModal(_addWeighIdx, true);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && awModal && !awModal.hidden && _addWeighIdx != null) {
        closeAddWeighModal(_addWeighIdx, true);
      }
    });
    // 총 배합량 입력 후 Enter → 첫 자재 LOT 칸으로 커서 이동(계량은 LOT 먼저가 의도).
    // 강제는 아니며 Tab 으로 다른 칸에 갈 수도 있다.
    const totalKb = $("blend-total");
    if (totalKb) totalKb.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const firstLot = document.querySelector("#blend-mat-body .blend-lot");
      if (firstLot) firstLot.focus();
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
    offerRestore();      // 작성 중이던 배합이 있으면 이어서 할지 배너로 제안
    const restoreYes = $("blend-restore-yes");
    if (restoreYes) restoreYes.addEventListener("click", () => { restoreDraft().catch((e) => notify(e.message, "error")); });
    const restoreNo = $("blend-restore-no");
    if (restoreNo) restoreNo.addEventListener("click", () => {
      clearDraft();
      const banner = $("blend-restore-banner"); if (banner) banner.hidden = true;
    });
    // 총량·비고·반응기 변경도 임시 저장에 반영.
    ["blend-total", "blend-note"].forEach((id) => {
      const el = $(id); if (el) el.addEventListener("input", scheduleDraftSave);
    });
    const reactorEl = $("blend-reactor");
    if (reactorEl) reactorEl.addEventListener("change", scheduleDraftSave);
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

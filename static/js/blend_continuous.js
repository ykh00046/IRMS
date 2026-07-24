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
    esc, TOLERANCE_G, fmt, toleranceDecimals, todayISO, nowTime,
    computeTheoryAmount, findAnchorIndex, theoryFromWeights,
    baseTotalValues, baseTotalLinksHtml,
    rescalePlan, exceedsBatchLimit,
    appliedRescaleRowHtml,
  } = window.IRMS.blendLib;

  const $ = (id) => document.getElementById(id);

  const MIN_LOTS = 1;
  const MAX_LOTS = 12;

  const state = {
    recipes: [],
    current: null,        // /blend/recipes/{id} 응답
    materials: [],        // [{material_id, material_code, material_name, ratio, is_anchor, value_weight}]
    theory: [],           // theory[i] — 전 로트 공통 이론량(총량×ratio)
    // cells[i][j] = { actual:"", manual:false, lot:"" }
    // 자재 LOT 은 이제 (자재 × 로트)마다 개별(cell.lot) — 로트별로 다른 원료 봉지를 쓴 실제를
    // 그대로 기록해 추적성을 확보한다. (구: 재료별 전 로트 공통 sharedLot[i] — 제거.)
    cells: [],
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
    // 로트별 증량 적용 plan 스냅샷(요약줄 표시용). lotRescalePlan[j] = plan 또는 null.
    lotRescalePlan: [],
    // 로트별 증량 승인 이벤트(저장 payload lot_rescale_events 로 전송). lotRescaleEvents[j] =
    // [{before_total, after_total, approval_id, approver} | {before_total, after_total, absence_reason}].
    // 증량 1회당 1건. 같은 로트에 2건이면 3회째 제안은 차단(단건 blend.js state.rescaleEvents 의 로트별 버전).
    lotRescaleEvents: [],
    // 추가분 입력 모드에 들어간 셀(저울 PRINT 를 추가분으로 합산하기 위한 플래그).
    addModeCell: null,     // {i,j} 또는 null
    // 작업자가 ⚖ 버튼으로 직접 지정한 저울 PRINT 대상 셀(수동 오버라이드). null 이면 미지정.
    // activeScaleCell 우선순위: (1)추가모드 (2)이 값 (3)포커스 셀 (4)첫 빈 셀. 지정 후 그
    // 셀이 허용 편차 내로 완료·레시피 변경·로트 수 변경 시 해제(sticky, blend.js scaleTargetIdx 의 셀 버전).
    scaleTargetCell: null, // {i,j} 또는 null
    // 증량(rescale) 후 추가 대기 셀 — blend.js state.addPending 의 셀(i:j) 버전.
    // 대기 중인 셀은 편차 경고·음수 표시 대신 '추가 +X' 배지만 보인다.
    addPendingCells: {},   // {"i:j": addNeeded}
    // 보류 중인 로트별 증량 제안({j, plan}) — discard 모달 '그래도 증량' 시 재사용.
    pendingContRescale: null,
    // 저울 전용 입력 모드(운영 대시보드 토글). true 면 실제량·증량 인라인 입력이
    // readonly 가 되고 저울 PRINT 로만 입력된다. false(기본)면 동작 변화 없음.
    scaleOnlyInput: false,
    // 저울 전용 모드 수기 입력 승인 — 책임자 승인 시 {approver}. 이 배합에 한해 실제량
    // 손입력을 허용(잠금 해제)한다. 레시피 변경 시 null 로 되돌려 재잠금(저장은 화면 이동).
    manualApproved: null,
    // 반제품 원료 LOT 자동 제안: 레시피 자재명 → 최근 product_lot 목록(자재별 1회 조회).
    // 자재 LOT 은 셀별이지만 제안 목록은 자재 단위라 (자재 × 로트) 모든 셀이 공유한다.
    lotSuggest: {},
    // 미등록 LOT 차단 — (자재명\u0000LOT) → true(등록됨)/false(미등록) 캐시.
    // 동일 (name, lot) 조합의 중복 조회를 막기 위해 한 번 판정하면 보관한다.
    // 레시피가 바뀌면 lotSuggest 와 함께 새로 채워지므로 여기서는 만료 처리하지 않는다.
    lotChecked: {},
    // 미등록 LOT '사유 입력 후 진행' 승인 — (자재명::LOT) → 사유. 저장 시 비고에 남긴다.
    lotOverrides: {},
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
    updateScaleOnlyBanner();
    updateScaleTargetIndicator();  // 저울 연결 상태 변화 → ⚖ 버튼·대상 표시 토글
  }

  // ── 저울 전용 입력 모드(scale-only-input) ───────────────────────
  // 페이지 로드 시 GET 으로 현재 상태를 가져온다(실패 시 false 폴백 — 화면이 죽으면 안 됨).
  // enabled=true 면 실제량 입력칸(.cont-actual)과 증량 추가분 인라인 입력(.cont-add-inline)을
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
    applyScaleOnlyToCells();
    updateScaleOnlyBanner();
  }

  // 저울 전용 모드일 때 현재 DOM 의 실제량·증량 입력칸에 readonly + title 부여.
  // 새로 렌더되는 셀에도 적용되도록 renderRows 직후에도 호출한다.
  // 책임자 수기 입력 승인(state.manualApproved)이 있으면 이 배합에 한해 잠금을 해제한다.
  // 용수 분류는 수기 입력 허용 — blend.js 와 동일 정책(2026-07-23).
  function isWaterCategoryRecipe() {
    const rid = selectedRecipeId();
    if (rid == null || !Array.isArray(state.recipes)) return false;
    const rec = state.recipes.find((r) => String(r.id) === String(rid));
    return Boolean(rec && rec.category === "용수");
  }

  function applyScaleOnlyToCells() {
    if (!state.scaleOnlyInput) return;  // 모드 아니면 손대지 않음(기본 동작)
    if (isWaterCategoryRecipe()) {
      document.querySelectorAll("#cont-mat-body .cont-actual, #cont-mat-body .blend-add-inline")
        .forEach((el) => { el.readOnly = false; el.removeAttribute("title"); });
      return;
    }
    const lock = !state.manualApproved;
    const titleText = "저울 전용 모드 — 저울 PRINT 로만 입력됩니다";
    document.querySelectorAll("#cont-mat-body .cont-actual").forEach((el) => {
      el.readOnly = lock;
      if (lock) el.title = titleText; else el.removeAttribute("title");
    });
    document.querySelectorAll("#cont-mat-body .blend-add-inline").forEach((el) => {
      el.readOnly = lock;
      if (lock) el.title = titleText; else el.removeAttribute("title");
    });
  }

  // (구) 상단 배너는 아래 컨트롤 줄과 중복 + 해제 방법은 작업자 대상 정보가 아니라 제거
  // (2026-07-23). 저울 미연결 상태는 컨트롤 줄 텍스트가 흡수한다.
  function updateScaleOnlyBanner() {
    const banner = document.getElementById("cont-scale-only-banner");
    if (banner) banner.hidden = true;  // 옛 템플릿 캐시 대비 항상 숨김
    updateManualEntryControl();
  }

  // 저울 전용 모드 수기 입력 승인 컨트롤 — 모드가 켜져 있을 때만 노출. 승인 전에는
  // '수기 입력 승인 요청' 버튼, 승인 후에는 승인자 안내 텍스트만(버튼 숨김).
  function updateManualEntryControl() {
    const box = $("cont-scale-only-control");
    if (!box) return;
    box.hidden = !state.scaleOnlyInput;
    if (!state.scaleOnlyInput) return;
    if (isWaterCategoryRecipe()) {
      const text = $("cont-scale-only-control-text");
      const btn = $("cont-manual-entry-request-btn");
      if (text) text.textContent = "용수 분류 — 수기 입력이 허용됩니다(저울 전용 예외).";
      if (btn) btn.hidden = true;
      box.classList.add("is-approved");
      applyScaleOnlyToCells();
      return;
    }
    const text = $("cont-scale-only-control-text");
    const btn = $("cont-manual-entry-request-btn");
    if (state.manualApproved) {
      if (text) text.textContent = `수기 입력 승인됨 — 승인자 ${state.manualApproved.approver} (이 배합에 한함)`;
      if (btn) btn.hidden = true;
      box.classList.add("is-approved");
    } else {
      // 저울 미연결 상태를 이 한 줄이 흡수 — 별도 상단 배너는 중복이라 폐기(2026-07-23).
      if (text) {
        text.textContent = state.scaleReady
          ? "저울 전용 입력 모드 — 실제량은 저울 PRINT 로만 입력됩니다."
          : "저울 전용 입력 모드 — 저울 연결 대기 중입니다. 연결되면 PRINT 로 입력됩니다.";
      }
      if (btn) btn.hidden = false;
      box.classList.remove("is-approved");
    }
  }

  // ── 저울 전용 모드 수기 입력 승인 게이트 ───────────────────────
  // '수기 입력 승인 요청' → /api/blend/manager-verify(purpose=manual) 200 → 이 배합에 한해
  // 손입력 허용. 부재 경로 없음(승인만). 레시피 변경 시 재잠금(저장은 /status 이동).
  function openManualApproveModal() {
    const modal = $("cont-manual-approve-modal");
    if (!modal) return;
    const nameEl = $("cont-manual-approve-name");
    const pwEl = $("cont-manual-approve-pw");
    if (nameEl) nameEl.value = "";
    if (pwEl) pwEl.value = "";
    hideManualApproveError();
    modal.hidden = false;
    if (nameEl) nameEl.focus();
  }
  function closeManualApproveModal() {
    const modal = $("cont-manual-approve-modal");
    if (modal) modal.hidden = true;
  }
  function showManualApproveError(msg) {
    const err = $("cont-manual-approve-error");
    if (err) { err.textContent = msg; err.hidden = false; }
  }
  function hideManualApproveError() {
    const err = $("cont-manual-approve-error");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  async function submitManualApproval() {
    const nameEl = $("cont-manual-approve-name");
    const pwEl = $("cont-manual-approve-pw");
    const name = nameEl ? nameEl.value.trim() : "";
    const pw = pwEl ? pwEl.value : "";
    if (!name) { showManualApproveError("책임자 이름을 입력하세요."); if (nameEl) nameEl.focus(); return; }
    if (!pw) { showManualApproveError("비밀번호를 입력하세요."); if (pwEl) pwEl.focus(); return; }
    hideManualApproveError();
    const btn = $("cont-manual-approve-submit");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/blend/manager-verify", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
        body: JSON.stringify({ username: name, password: pw, purpose: "manual" }),
      });
      if (res.status === 401) { showManualApproveError("비밀번호가 올바르지 않습니다."); return; }
      if (res.status === 403) { showManualApproveError("책임자 권한이 없습니다."); return; }
      if (!res.ok) { showManualApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요."); return; }
      const data = await res.json().catch(() => ({}));
      const approver = data.approver || name;
      state.manualApproved = { approver };
      closeManualApproveModal();
      applyScaleOnlyToCells();     // 이 배합의 실제량 입력칸 잠금 해제
      updateManualEntryControl();  // 배너 텍스트를 승인 안내로 전환(버튼 숨김)
      notify(`수기 입력 승인 완료 (${approver}) — 이 배합에 한해 손입력이 허용됩니다.`, "success");
    } catch (_e) {
      showManualApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요.");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // 저장 시 비고에 남길 수기 입력 승인 표시(미등록 LOT 사유와 동일 방식으로 append).
  function buildManualApprovalNote() {
    return state.manualApproved ? `[수기 입력 승인] 승인자: ${state.manualApproved.approver}` : "";
  }

  // ── 이어서 계량 임시 저장·복구 ────────────────────────────────
  // 공용 PC 에서 연속 배합 중 자동 로그아웃·창 닫힘으로 계량값(승인된 증량 이력 포함)이
  // 날아가는 것을 막는다. 진행 중 입력을 이 PC 의 localStorage 에 저장하고(서버·다른 작업
  // 무관), 다음 진입 시 이어서 할지 배너로 묻는다. 저장 완료·버리기 시 삭제. 24시간 지난
  // 초안은 제안하지 않는다. (단건 배합 blend.js "irms.blend.draft" 의 이어서 계량 버전 —
  // 셀 매트릭스·로트별 증량에 맞춰 확장.)
  const DRAFT_KEY = "irms.blend.cont.draft";
  let _draftTimer = null;

  function currentDraft() {
    if (!state.current || !state.current.recipe) return null;
    // 의미 있는 입력이 있을 때만 초안을 만든다: 실제량을 넣은 셀이 있거나 자재 LOT 을 적었을 때.
    const hasCell = state.cells.some((row) =>
      row && row.some((c) => c && c.actual !== "" && c.actual != null));
    const hasLot = state.cells.some((row) =>
      row && row.some((c) => c && (c.lot || "").trim()));
    if (!hasCell && !hasLot) return null;
    return {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      lotCount: state.lotCount,
      total: $("cont-total").value,
      date: $("cont-date").value,
      time: $("cont-time").value,
      scale: $("cont-scale").value,
      note: $("cont-note").value,
      reactor: $("cont-reactor").value,
      lotOverrides: state.lotOverrides || {},
      // 셀 매트릭스 — cells[i][j] = {actual, manual, lot}. actual·lot 은 문자열로 통일.
      // 자재 LOT 은 이제 셀별(cell.lot). (구 초안의 sharedLot 은 복구 시 마이그레이션.)
      cells: state.cells.map((row) => (row || []).map((c) => ({
        actual: (c.actual === "" || c.actual == null) ? "" : String(c.actual),
        manual: c.manual === true,
        lot: (c.lot === "" || c.lot == null) ? "" : String(c.lot),
      }))),
      // 로트별 증량 상태 — override 총량·요약 plan·승인 이벤트(깊은 복사). 반드시 함께
      // 보관해야 복구 후 저장 payload(lot_totals·lot_rescale_events)로 전송돼 추적성이
      // 유지된다(누락 시 서버가 '증량 없음'으로 조용히 저장 — 추적 구멍).
      lotRescale: (state.lotRescale || []).map((v) => v || null),
      lotRescalePlan: (state.lotRescalePlan || []).map((p) => p || null),
      lotRescaleEvents: (state.lotRescaleEvents || []).map((e) =>
        e && e.length ? e.map((ev) => ({ ...ev })) : null),
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

  // 진입 시 초안이 있으면 배너로 이어서 할지 묻는다.
  function offerRestore() {
    const banner = $("cont-restore-banner");
    if (!banner) return;
    const draft = readDraft();
    if (!draft || !draft.recipe_id) { banner.hidden = true; return; }
    const label = $("cont-restore-label");
    if (label) {
      const when = draft.savedAt ? draft.savedAt.slice(0, 16).replace("T", " ") : "";
      label.textContent = `작성 중이던 '${draft.product_name || ""}' 이어서 계량이 있습니다${when ? ` (${when})` : ""} — 이어서 하시겠어요?`;
    }
    banner.hidden = false;
  }

  async function restoreDraft() {
    const draft = readDraft();
    if (!draft || !draft.recipe_id) return;
    // 레시피 목록이 아직이면 먼저 로드해 select 옵션이 존재하게 한다(값 세팅이 붙도록).
    if (!state.recipes.length) {
      try { await loadRecipes(); } catch (_e) { /* onRecipeChange 가 id 로 직접 조회 */ }
    }
    // 로트 수를 먼저 맞춰야 onRecipeChange 의 rebuildCells 가 올바른 매트릭스를 만든다.
    if (draft.lotCount) {
      state.lotCount = Math.max(MIN_LOTS, Math.min(MAX_LOTS, Number(draft.lotCount) || 2));
      $("cont-lot-count").textContent = String(state.lotCount);
    }
    const recipeSel = $("cont-recipe");
    recipeSel.value = String(draft.recipe_id);
    await onRecipeChange();  // 레시피 로드 + 초기화 + 빈 렌더 — 이후 초안 값을 덮어씌운다.
    // 기준 자재 레시피면 이어서 계량 자체가 불가 — 복구 중단(빈 상태 유지, 초안 폐기).
    if (state.anchorBlocked) {
      notify("이 레시피는 기준 자재 방식이라 이어서 계량 복구를 지원하지 않습니다.", "warn");
      clearDraft();
      const banner = $("cont-restore-banner"); if (banner) banner.hidden = true;
      return;
    }
    if (draft.date) $("cont-date").value = draft.date;
    if (draft.time) $("cont-time").value = draft.time;
    if (draft.scale) $("cont-scale").value = draft.scale;
    if (draft.note) $("cont-note").value = draft.note;
    if (draft.reactor) $("cont-reactor").value = draft.reactor;
    state.lotOverrides = draft.lotOverrides || {};
    // 총 배합량 복구 → 이론량 재산출(placeholder·편차 기준).
    if (draft.total) {
      $("cont-total").value = draft.total;
      state.total = Number(draft.total) || 0;
      recomputeTheory();
    }
    // 셀 매트릭스 복구(actual/manual/lot).
    // 레거시 마이그레이션: 옛 초안은 자재 LOT 이 전 로트 공통(draft.sharedLot[i]) 이고
    // 셀에 lot 필드가 없다. 이 경우 그 재료의 공통 LOT 을 그 재료의 모든 로트 셀에 복사한다
    // (자연스러운 이월 — 그 시점엔 로트별 구분이 없었으므로 동일 값으로 채우는 것이 안전).
    const legacyShared = Array.isArray(draft.sharedLot) ? draft.sharedLot : null;
    (draft.cells || []).forEach((row, i) => {
      (row || []).forEach((c, j) => {
        if (!(state.cells[i] && state.cells[i][j])) return;
        state.cells[i][j].actual = (c.actual === "" || c.actual == null) ? "" : c.actual;
        state.cells[i][j].manual = c.manual === true;
        let lot = (c && typeof c.lot === "string") ? c.lot : "";
        if (!lot && legacyShared && typeof legacyShared[i] === "string") lot = legacyShared[i] || "";
        state.cells[i][j].lot = lot;
      });
    });
    // 로트별 증량 상태 복구 — onRecipeChange 가 이미 [] 로 리셋했으므로 초안 값으로 되살린다.
    // (깊은 복사로 원본 초안 객체와 분리.)
    state.lotRescale = Array.isArray(draft.lotRescale) ? draft.lotRescale.slice() : [];
    state.lotRescalePlan = Array.isArray(draft.lotRescalePlan) ? draft.lotRescalePlan.slice() : [];
    state.lotRescaleEvents = Array.isArray(draft.lotRescaleEvents)
      ? draft.lotRescaleEvents.map((e) => (e && e.length ? e.map((ev) => ({ ...ev })) : null))
      : [];
    state.addPendingCells = {};  // renderAddBadges 가 로트별로 다시 채운다.
    rebuildLotRescale();  // 현재 lotCount 에 맞춰 패딩(값 보존).
    render();  // 복구된 state 값으로 표를 다시 그린다(실제량·LOT·이론·편차·총량 잠금).
    // 증량된 로트는 요약줄·추가 배지를 명시적으로 다시 그린다.
    renderContRescaleSummary();
    for (let j = 0; j < state.lotCount; j++) {
      if (state.lotRescale[j] > 0) renderAddBadges(j);
    }
    updateContTotalLock();  // 실측이 있으면 공용 총 배합량 잠금 재적용.
    const banner = $("cont-restore-banner");
    if (banner) banner.hidden = true;
    notify("작성 중이던 이어서 계량을 복원했습니다.", "success");
    // 증량 이력이 있으면 1회 안내(blend.js 와 동일 취지).
    if (state.lotRescaleEvents.some((e) => e && e.length)) {
      notify("복구된 계량에 증량 이력이 포함되어 있습니다.", "warn");
    }
    // anti-cheat: 승인 대기 중 새로고침으로 증량 승인 게이트를 우회하지 못하게 한다.
    reofferPendingRescaleAfterRestore();
  }

  // 복구 직후 +방향 허용 편차 초과 셀(=미승인 증량 대기 상태로 창을 닫았던 경우)이 남아 있으면
  // 즉시 그 로트의 증량 제안/승인 모달을 다시 띄운다 — 깨끗한 화면 대신 곧장 승인 게이트로.
  // 이미 승인·적용된 로트의 셀은 renderAddBadges 로 addPending 처리돼 여기서 걸러진다(재트리거 안 함).
  function reofferPendingRescaleAfterRestore() {
    const tol = state.toleranceG;
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        if (state.addPendingCells && state.addPendingCells[`${i}:${j}`] != null) continue;
        const th = theoryFor(i, j);
        const raw = state.cells[i][j].actual;
        if (raw === "" || th == null) continue;
        if (Number(raw) - th > tol + 1e-9) {
          // warnIfVariance(+방향) → offerContRescale(j) → 제안/승인 모달. 첫 초과 셀 하나면 충분.
          warnIfVariance(i, j);
          return;
        }
      }
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

  // PRINT 가 들어갈 셀: 추가(합산) 모드 중이면 무조건 그 셀(배합 화면 activeScaleRow 와
  // 동일). 인라인 추가 입력칸은 cont-actual 클래스가 아니어서 포커스 감지에 안 걸리고,
  // 그 셀의 actual 은 이미 채워져 있어 폴백도 건너뛰었다 — 부족 보충 PRINT 가 엉뚱한
  // 빈 셀로 가던 버그(2026-07-22 흐름 재검토 BUG-1). 커서 우선 → 첫 미입력 셀 폴백.
  function activeScaleCell() {
    if (state.addModeCell) return state.addModeCell;
    // 작업자 수동 지정 셀(sticky) — 포커스보다 우선. 유효한 셀일 때만.
    const t = state.scaleTargetCell;
    if (t && state.cells[t.i] && state.cells[t.i][t.j] && t.j < state.lotCount) {
      return { i: t.i, j: t.j };
    }
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

  // ── 저울 PRINT 대상 셀 지정·표시 ─────────────────────────────
  // ⚖ 버튼으로 대상 셀을 직접 고르면 sticky 로 보관(scaleTargetCell). 다음 PRINT 를 그
  // 셀로 라우팅하고, 어디로 들어갈지 셀 강조(cell-scale-target)로 보이게 한다. 저울
  // 연결 시에만 표시 — 수동 전용 현장 노이즈 제거.
  function setScaleTargetCell(i, j) {
    state.scaleTargetCell = { i, j };
    updateScaleTargetIndicator();
  }

  function updateScaleTargetIndicator() {
    const body = $("cont-mat-body");
    if (!body) return;
    // 수동 지정 셀이 허용 편차 내로 채워졌거나 무효해졌으면 자동 해제.
    const t = state.scaleTargetCell;
    if (t) {
      const cell = state.cells[t.i] && state.cells[t.i][t.j];
      const pend = state.addPendingCells && state.addPendingCells[`${t.i}:${t.j}`] != null;
      const th = theoryFor(t.i, t.j);
      if (!cell || t.j >= state.lotCount) {
        state.scaleTargetCell = null;
      } else if (cell.actual !== "" && !pend && th != null
        && Math.abs(Number(cell.actual) - th) <= state.toleranceG + 1e-9) {
        state.scaleTargetCell = null;
      }
    }
    body.querySelectorAll("td.cell-scale-target").forEach((td) => td.classList.remove("cell-scale-target"));
    body.querySelectorAll(".scale-target-tag").forEach((el) => el.remove());
    body.querySelectorAll(".cont-scale-btn.is-active").forEach((b) => b.classList.remove("is-active"));
    body.classList.toggle("scale-connected", Boolean(state.scaleReady));
    if (!state.scaleReady || state.anchorBlocked) return;
    // 추가 합산 모드(인라인)면 그 셀 ⚖ 만 활성 표시 — 인라인 입력칸이 안내 역할.
    if (state.addModeCell) {
      const b = body.querySelector(`.cont-scale-btn[data-i="${state.addModeCell.i}"][data-j="${state.addModeCell.j}"]`);
      if (b) b.classList.add("is-active");
      return;
    }
    const pos = activeScaleCell();
    if (!pos) return;
    const inp = body.querySelector(`.cont-actual[data-i="${pos.i}"][data-j="${pos.j}"]`);
    const td = inp && inp.closest("td.cont-cell");
    if (td) {
      td.classList.add("cell-scale-target");
      if (!td.querySelector(".scale-target-tag")) {
        const tag = document.createElement("span");
        tag.className = "scale-target-tag";
        tag.textContent = "⚖ 저울";
        td.appendChild(tag);
      }
    }
    const btn = body.querySelector(`.cont-scale-btn[data-i="${pos.i}"][data-j="${pos.j}"]`);
    if (btn) btn.classList.add("is-active");
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
    updateContTotalLock();  // 저울 PRINT 로 첫 실제량이 들어와도 총량 잠금
    input.classList.remove("manual-warn");
    input.removeAttribute("title");
    updateCellVar(i, j);
    const over = warnIfVariance(i, j);
    // 이 셀이 수동 지정 대상이었고 허용 편차 내로 완료됐으면 지정 해제(sticky 종료).
    if (!over && state.scaleTargetCell
      && state.scaleTargetCell.i === i && state.scaleTargetCell.j === j) {
      state.scaleTargetCell = null;
    }
    scheduleDraftSave();  // 저울 PRINT 입력분도 임시 저장(복구용)
    focusNextFrom(i, j);
    updateScaleTargetIndicator();
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
    state.lotOverrides = {};        // 레시피 변경 → 미등록 LOT 진행 승인 리셋
    state.lotRescale = [];          // 레시피 변경 → 증량 오버라이드 전부 리셋(스펙)
    state.lotRescalePlan = [];      // 레시피 변경 → 증량 요약줄도 리셋
    state.lotRescaleEvents = [];    // 레시피 변경 → 증량 승인 이력도 리셋(총량 잠금도 함께 풀림)
    state.addPendingCells = {};     // 레시피 변경 → 증량 대기 셀 억제도 리셋
    state.scaleTargetCell = null;   // 레시피 변경 → 저울 대상 지정 해제
    state.manualApproved = null;    // 레시피 변경 → 수기 입력 승인 해제(다음 배합은 다시 잠금)
    rebuildCells();
    rebuildLotRescale();
    clearContRescaleSummary();
    updateManualEntryControl();     // 승인 해제 반영(배너 텍스트·버튼 복귀)

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
    loadLotSuggest();
  }

  // ── 반제품 원료 LOT 자동 제안 ───────────────────────────────
  // 자재명 전체로 1회 조회 → state.lotSuggest(자재명→[lots]) 보관. 실패는 조용히 무시.
  async function loadLotSuggest() {
    const names = state.materials
      .map((m) => (m.material_name || "").trim())
      .filter((n) => n);
    if (!names.length) { state.lotSuggest = {}; return; }
    try {
      const data = await request("/blend/recent-product-lots", {
        query: { names: names.join(","), limit: 5 },
      });
      state.lotSuggest = (data && data.items) || {};
    } catch (_e) {
      state.lotSuggest = {};
    }
  }

  // .cont-lot 칸 아래 제안 목록. native datalist 금지(클릭 불만) — blend_login suggest 패턴.
  // 항목 mousedown(preventDefault) → LOT 칸 채움 + input 이벤트 + 목록 닫기.
  function renderLotSuggest(input) {
    const i = Number(input.dataset.i);
    const j = Number(input.dataset.j);
    const name = (state.materials[i] && state.materials[i].material_name) || "";
    const lots = (state.lotSuggest && state.lotSuggest[name]) || [];
    if (!lots.length) { hideLotSuggest(input); return; }
    // 각 항목은 {lot, total} — total(1차 배치 총량)은 회색 접미로 같이 표시(클릭은 LOT 만).
    const q = (input.value || "").trim().toLowerCase();
    const matches = q ? lots.filter((l) => String(l.lot).toLowerCase().startsWith(q)) : lots.slice();
    let box = input._lotBox;
    if (!box) {
      box = document.createElement("div");
      box.className = "lot-suggest";
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
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = lot;  // LOT 만 채운다(총량은 표시 전용).
        if (state.cells[i] && state.cells[i][j]) state.cells[i][j].lot = lot;
        input.dispatchEvent(new Event("input"));  // state 반영 경로 재사용
        hideLotSuggest(input);
        input.focus();
      });
      box.appendChild(item);
    });
    box.hidden = !matches.length;
    if (!box.hidden) positionLotSuggest(input, box);
  }

  // 아래 공간이 부족하면(맨 아래 행 등 .table-wrap overflow 로 잘리는 경우) 위로 연다.
  // 배합 화면 renderLotSuggest 와 동일한 보정 — 자세한 배경은 blend.js 참고.
  function positionLotSuggest(input, box) {
    box.classList.remove("lot-suggest--up");
    const wrap = input.closest(".table-wrap");
    if (!wrap) return;
    const inRect = input.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    const boxH = box.offsetHeight || 216;
    const spaceBelow = wrapRect.bottom - inRect.bottom;
    const spaceAbove = inRect.top - wrapRect.top;
    if (spaceBelow < boxH + 8 && spaceAbove > spaceBelow) {
      box.classList.add("lot-suggest--up");
    }
  }

  function hideLotSuggest(input) {
    if (!input._lotBox) return;
    input._lotBox.hidden = true;
  }

  // ── 미등록 LOT 차단(반제품 자재만) ─────────────────────────────
  // 제안(state.lotSuggest)이 있는 자재 = 완료 배합 기록이 있는 반제품. 이 자재의 자재 LOT 칸은
  // 반드시 그 반제품의 실제 product_lot 중 하나여야 한다. 그렇지 않으면(직접 타이핑 오타 등)
  // #cont-lot-invalid-modal 로 막고 값을 비운다. 일반 자재(제안 없음)는 100% 기존 동작 유지.
  // 자재 LOT 이 셀별(cells[i][j].lot)이므로 (자재 × 로트) 셀마다 개별 검증한다.
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

  // 셀 LOT 입력칸 하나 검증 — 미등록이면 모달을 띄우고 값·state 를 비운 뒤 다시 포커스.
  // 자재 LOT 이 셀별(cells[i][j].lot)이므로 (자재 × 로트) 셀마다 개별 검증한다. override 는
  // (자재명, LOT) 키라 같은 봉지를 여러 로트에 쓰면 한 번 승인으로 모두 통과된다.
  async function validateLotInput(input) {
    const i = Number(input.dataset.i);
    const j = Number(input.dataset.j);
    const m = state.materials[i];
    if (!m || !(state.cells[i] && state.cells[i][j])) return;
    const name = (m.material_name || "").trim();
    // 제안이 없는 자재(일반 원료)는 검증하지 않는다 — 기존 동작 유지.
    if (!state.lotSuggest || !state.lotSuggest[name]) return;
    const lot = (input.value || "").trim();
    input.value = lot;  // trim 반영
    state.cells[i][j].lot = lot;
    if (lotOverrideKey(name, lot) in state.lotOverrides) return;  // 사유 입력 후 진행 승인됨 → 통과
    if (await checkLotRegistered(name, lot)) return;  // 등록됨 → 통과
    // 미등록 — 모달 표시. 확인 버튼이 값 비우기를 맡는다(아래 bind 의 cont-lot-invalid-confirm).
    openContLotInvalidModal(name, lot, input);
  }

  // 미등록 LOT '사유 입력 후 진행'(안전밸브) 승인 키·비고 — 배합 화면과 동일 취지.
  function lotOverrideKey(name, lot) { return `${name}::${lot}`; }
  function buildOverrideNote() {
    // 자재 LOT 이 셀별이므로 전 셀을 훑어 승인된 (자재, LOT) 조합을 중복 없이 모은다.
    const parts = [];
    const seen = new Set();
    state.materials.forEach((m, i) => {
      const name = (m.material_name || "").trim();
      (state.cells[i] || []).forEach((c) => {
        const lot = (c && c.lot || "").trim();
        if (!lot) return;
        const key = lotOverrideKey(name, lot);
        if (seen.has(key)) return;
        if (key in state.lotOverrides) {
          seen.add(key);
          parts.push(`[미등록 LOT 진행] ${name}/${lot}: ${state.lotOverrides[key]}`);
        }
      });
    });
    return parts.join("\n");
  }

  function openContLotInvalidModal(name, lot, input) {
    const body = $("cont-lot-invalid-modal-body");
    if (body) {
      body.innerHTML = ""
        + `<p><strong>자재명:</strong> ${esc(name)}</p>`
        + `<p><strong>입력한 로트:</strong> ${esc(lot)}</p>`
        + `<p>등록되지 않은 로트입니다. 1차 배합 기록이 저장되었는지, LOT 번호가 맞는지 확인하세요.</p>`
        + `<p class="muted small">1차 기록이 아직 없는 정당한 경우에는 아래에 사유를 적고 진행할 수 있습니다(사유는 기록에 남습니다).</p>`;
    }
    const box = $("cont-lot-override-box");
    const reason = $("cont-lot-override-reason");
    if (reason) reason.value = "";
    if (box) box.hidden = true;
    const modal = $("cont-lot-invalid-modal");
    modal._lotInput = input || null;
    modal._lotName = name;
    modal._lotValue = lot;
    modal.hidden = false;
  }

  function closeContLotInvalidModal() { $("cont-lot-invalid-modal").hidden = true; }

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
        row.push(prevRow[j] || { actual: "", manual: false, lot: "" });
      }
      next.push(row);
    }
    state.cells = next;
  }

  // 계량값 표시 소수 자릿수 — 현재 레시피 허용 편차(state.toleranceG)를 따른다(표시 전용).
  // 계산·검증·저장은 그대로 2자리 이론 기준을 유지한다.
  function dp() { return toleranceDecimals(state.toleranceG); }

  function recomputeTheory() {
    // value_weight 비례 방식 — 서버(blend_service.scale_theory)와 동일 산술로
    // 반올림된 ratio(%) 로 인한 꼬리를 없앤다. value_weight 이 빠진 옛 레시피는
    // null 배열 → 기존 computeTheoryAmount(ratio, total) 로 폴백. total<=0 이면
    // null 배열 → 폴백도 total>0 검사로 자연히 null(표시 '-').
    const byWeights = theoryFromWeights(state.materials, state.total);
    state.theory = state.materials.map((m, i) =>
      byWeights[i] !== null
        ? byWeights[i]
        : (state.total > 0 ? computeTheoryAmount(m.ratio, state.total) : null)
    );
  }

  // ── 로트별 증량(rescale) 핵심 산술 ──────────────────────────
  // lotRescale[j] 가 있으면 그 값, 없으면 공용 총량. lotRescale 전부 null → 기존 동작.
  function lotTotal(j) {
    const override = state.lotRescale && state.lotRescale[j];
    return Math.max(state.total, override || 0);
  }

  // 자재 i 가 로트 j 에서 가져야 할 이론량(로트별 총량 기준).
  // 증량 안 된 로트는 state.theory[i] 그대로(원값 비례 — 정밀), 증량된 로트만
  // 그 로트의 총량으로 원값 비례 재산출(폴백: 반올림 ratio).
  function theoryFor(i, j) {
    const m = state.materials[i];
    if (!m) return null;
    if (!(state.lotRescale[j] > 0)) return state.theory[i];
    const total = lotTotal(j);
    if (!(total > 0)) return null;
    const byWeights = theoryFromWeights(state.materials, total);
    if (byWeights[i] !== null) return byWeights[i];
    return Math.round((Number(m.ratio) / 100) * total * 100) / 100;
  }

  // lotRescale(과 lotRescalePlan)을 lotCount 에 맞춘다 — 기존 값 보존, 늘어난 칸은 null.
  function rebuildLotRescale() {
    const next = [];
    const nextPlan = [];
    const nextEvents = [];
    for (let j = 0; j < state.lotCount; j++) {
      next.push((state.lotRescale && state.lotRescale[j]) || null);
      nextPlan.push((state.lotRescalePlan && state.lotRescalePlan[j]) || null);
      nextEvents.push((state.lotRescaleEvents && state.lotRescaleEvents[j]) || null);
    }
    state.lotRescale = next;
    state.lotRescalePlan = nextPlan;
    state.lotRescaleEvents = nextEvents;
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
        ? ` <small class="cont-lot-total">· ${fmt(rescaled, dp())} g</small>`
        : "";
      const emphCls = rescaled ? " cont-lot-rescaled" : "";
      lotHeads.push(
        `<th class="num cont-lot-col ${lc}${emphCls}">${chip}${totalBadge}<br>`
        + `<small class="cont-lot-preview" data-j="${j}">-</small></th>`
      );
    }
    // 자재 LOT 은 이제 열이 아니라 각 로트 셀 안(실제량 위)에 들어간다 — 헤더에서 제거.
    head.innerHTML = "<tr>"
      + '<th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론량(g)</th>'
      + lotHeads.join("")
      + "</tr>";

    // 공정 설명 줄(레시피 '설명') — 자재 사이/끝에 전폭 안내 행으로 끼워넣는다.
    const steps = (state.current && state.current.steps) || [];
    const colspan = 4 + state.lotCount;  // 자재 LOT 열 제거 → 4 고정열(#·품목·비율·이론량) + 로트 수
    const parts = [];
    state.materials.forEach((m, i) => {
      parts.push(contStepRowsHtml(steps, i, colspan));  // 이 자재 앞(=앞선 자재 i개 뒤) 설명
      const cells = [];
      for (let j = 0; j < state.lotCount; j++) {
        const cell = state.cells[i][j];
        // 셀 placeholder 도 로트별 이론(theoryFor) 기준 — 증량 로트는 큰 값 표시.
        const th = theoryFor(i, j);
        const ph = th == null ? "" : fmt(th, dp());
        const lc = `cont-lc${j % 4}${j === 0 ? " cont-first-lot" : ""}`;
        // 각 셀 = 자재 LOT(위) + 실제량(아래). LOT 은 셀별(cells[i][j].lot) — 로트마다 다른
        // 봉지를 쓴 실제를 그대로 기록. j>0 셀엔 '↩' 이전-로트 LOT 복사 버튼(명시적 클릭만).
        const copyBtn = j > 0
          ? `<button type="button" class="cont-lot-copy" data-i="${i}" data-j="${j}" tabindex="-1" title="이전 로트 LOT 복사">↩</button>`
          : "";
        cells.push(
          `<td class="num cont-cell ${lc}">`
          + `<div class="cont-lot-wrap">`
          + `<input class="input blend-lot cont-lot cont-cell-lot" data-i="${i}" data-j="${j}" value="${esc(cell.lot || "")}" placeholder="LOT" autocomplete="off" />`
          + copyBtn
          + `</div>`
          + `<input class="input cont-actual" data-i="${i}" data-j="${j}" type="number" step="any" min="0" `
          + `value="${esc(cell.actual)}" placeholder="${ph}" />`
          + `<button type="button" class="cont-scale-btn" data-i="${i}" data-j="${j}" tabindex="-1" title="여기로 저울 입력">⚖</button>`
          + `<span class="cont-var" data-i="${i}" data-j="${j}">-</span>`
          + `</td>`
        );
      }
      // 자재 행 홀짝 줄무늬(cont-mrow-alt) — 넓은 표에서 같은 원재료 행을 가로로 추적.
      parts.push(`<tr class="cont-mrow${i % 2 ? " cont-mrow-alt" : ""}">`
        + `<td>${i + 1}</td>`
        + `<td class="cont-matname">${esc(m.material_name)}</td>`
        + `<td class="num">${fmt(m.ratio, 2)}</td>`
        + `<td class="num cont-theory" data-i="${i}">${fmt(state.theory[i], dp())}</td>`
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
    // 저울 전용 모드가 켜져 있으면 새로 렌더된 셀의 실제량 칸도 readonly 로 잠근다.
    applyScaleOnlyToCells();
    // 이미 계량된 셀이 있으면(로트 수 변경 등 재렌더 후) 총량 잠금 상태를 새 DOM 에 다시 적용.
    updateContTotalLock();
    // 저울 대상 셀 표시 갱신(재렌더 후).
    updateScaleTargetIndicator();
  }

  function bindCellEvents() {
    const body = $("cont-mat-body");
    // ⚖ 셀 지정 버튼 — 증량 대기 셀이면 인라인 추가 입력, 아니면 대상 셀 지정 + 포커스.
    body.querySelectorAll(".cont-scale-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const i = Number(btn.dataset.i);
        const j = Number(btn.dataset.j);
        if (state.addPendingCells && state.addPendingCells[`${i}:${j}`] != null) {
          openAddInline(i, j);
          return;
        }
        setScaleTargetCell(i, j);
        focusActual(i, j);
      });
    });
    body.querySelectorAll(".cont-cell-lot").forEach((el) => {
      const i = Number(el.dataset.i);
      const j = Number(el.dataset.j);
      el.addEventListener("input", () => {
        if (state.cells[i] && state.cells[i][j]) state.cells[i][j].lot = el.value;
        if (el._lotBox) renderLotSuggest(el);
        scheduleDraftSave();  // 진행분 임시 저장(복구용)
      });
      // 포커스 시 제안 목록 표시(제안이 있는 자재만). blend_login suggest 패턴 재사용.
      el.addEventListener("focus", () => renderLotSuggest(el));
      el.addEventListener("blur", () => hideLotSuggest(el));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && el._lotBox) { hideLotSuggest(el); return; }
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        // 셀별 LOT → 그 셀의 실제량으로(LOT-먼저-실제량 흐름).
        focusActual(i, j);
      });
      // 미등록 LOT 차단 — 반제품(제안이 있는 자재)만. 편집 확정(change) 시 셀별 검증.
      // 일반 자재(제안 없음)는 변화 없음. 미등록이면 #cont-lot-invalid-modal 표시 후 값을 비운다.
      el.addEventListener("change", () => validateLotInput(el));
    });
    // ↩ 이전 로트 LOT 복사 — 명시적 클릭만(자동 이월 없음: 봉지 교체를 놓쳐 조용히 잘못
    // 기록되는 것을 막기 위해 사용자가 직접 확인하고 복사한다). 같은 자재의 직전 로트 LOT 을
    // 이 셀에 채우고 검증한 뒤 실제량으로 포커스를 옮긴다.
    body.querySelectorAll(".cont-lot-copy").forEach((btn) => {
      btn.addEventListener("click", () => {
        const i = Number(btn.dataset.i);
        const j = Number(btn.dataset.j);
        copyPrevLot(i, j);
      });
    });
    body.querySelectorAll(".cont-actual").forEach((el) => {
      const i = Number(el.dataset.i);
      const j = Number(el.dataset.j);
      el.addEventListener("input", () => {
        state.cells[i][j].actual = el.value;
        updateContTotalLock();  // 첫 실제량 입력 순간 공용 총량 잠금(승인 우회 방지)
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
        scheduleDraftSave();  // 진행분 임시 저장(복구용)
      });
      el.addEventListener("change", () => warnIfVariance(i, j));
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        // Enter(완료)로 계량을 마치는 순간에도 즉시 경고 — change(blur)에만 기대지 않는다.
        // 허용 편차를 벗어난 값이 들어있는 채로는 다음 칸으로 내려가지 않는다(2026-07-22).
        if (warnIfVariance(i, j)) {
          el.focus();
          try { el.select(); } catch (_e) { /* number select 미지원 무시 */ }
          return;
        }
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

  function focusLot(i, j) {
    const el = document.querySelector(`.cont-cell-lot[data-i="${i}"][data-j="${j}"]`);
    if (!el) return false;
    el.focus();
    try { el.select(); } catch (_e) { /* noop */ }
    return true;
  }

  // 가로 우선 흐름(셀별 LOT): 실제량 완료 → 같은 재료의 다음 로트 LOT → 없으면 다음 재료의
  // 첫 로트 LOT → 없으면 저장. (셀마다 LOT-먼저-실제량이라 다음 셀은 그 셀의 LOT 칸으로.)
  function focusNextFrom(i, j) {
    if (j + 1 < state.lotCount) { focusLot(i, j + 1); return; }
    if (focusLot(i + 1, 0)) return;
    const save = $("cont-save");
    if (save) save.focus();
  }

  // ↩ 이전 로트 LOT 복사 — 같은 자재(i)의 직전 로트(j-1) LOT 을 이 셀(j)에 채운다.
  // 명시적 클릭만(자동 이월 금지). 채운 뒤 미등록 LOT 검증을 돌리고 실제량으로 포커스 이동.
  function copyPrevLot(i, j) {
    if (j <= 0) return;
    const prevCell = state.cells[i] && state.cells[i][j - 1];
    const prev = (prevCell && prevCell.lot || "").trim();
    if (!prev) { notify("이전 로트에 복사할 LOT 이 없습니다.", "warn"); focusLot(i, j); return; }
    if (!(state.cells[i] && state.cells[i][j])) return;
    state.cells[i][j].lot = prev;
    const input = document.querySelector(`.cont-cell-lot[data-i="${i}"][data-j="${j}"]`);
    if (input) {
      input.value = prev;
      validateLotInput(input);  // 미등록이면 모달로 막고 값을 비운다(직접 입력과 동일 경로)
    }
    scheduleDraftSave();
    focusActual(i, j);
  }

  function updateCellVar(i, j) {
    const span = document.querySelector(`.cont-var[data-i="${i}"][data-j="${j}"]`);
    if (!span) return;
    // 증량 후 추가 대기 셀은 음수 편차 대신 배지(renderAddBadges)가 넣을 양을 안내 —
    // 여기서 편차 텍스트를 덮어쓰지 않는다(blend.js addPending 과 동일 규칙).
    if (state.addPendingCells && state.addPendingCells[`${i}:${j}`] != null) return;
    const th = theoryFor(i, j);
    const raw = state.cells[i][j].actual;
    const act = raw === "" ? null : Number(raw);
    if (act === null || th == null) { span.textContent = "-"; span.className = "cont-var"; return; }
    const v = Math.round((act - th) * 100) / 100;
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

  // 같은 셀·같은 값 중복 경고 억제 — Enter 직후 change 이벤트 재발생으로 동일 경고가
  // 2번 뜨던 문제(blend.js 와 동일 패턴, 2026-07-22).
  let _lastVarWarn = { key: "", at: 0 };

  function warnIfVariance(i, j) {
    // 증량 대기 셀(추가 배지 표시 중)은 편차 경고 대상이 아니다 — 증량으로 이론량이
    // 커져 생긴 '아직 안 넣은 양'이지 잘못 계량한 게 아니다(blend.js addPending 과 동일).
    if (state.addPendingCells && state.addPendingCells[`${i}:${j}`] != null) return false;
    const th = theoryFor(i, j);
    const raw = state.cells[i][j].actual;
    if (raw === "" || th == null) return false;
    const v = Math.round((Number(raw) - th) * 100) / 100;
    const tol = state.toleranceG;
    if (Math.abs(v) > tol + 1e-9) {
      const key = `${i}:${j}:${raw}`;
      const now = Date.now();
      if (_lastVarWarn.key === key && now - _lastVarWarn.at < 1500) return true;
      _lastVarWarn = { key, at: now };
      notify(`허용 편차 초과: ${state.materials[i].material_name} 로트 ${j + 1} — `
        + `이론 ${fmt(th)} / 실제 ${fmt(Number(raw))} (편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}g > ±${tol}g). 다시 계량하세요.`, "error");
      // 초과(+) 방향일 때만 그 로트 증량 제안 모달을 띄운다.
      if (v > 0) {
        offerContRescale(j);
      } else {
        // 부족(-): 팝업으로 부족량 명시. 영점 실수 등은 추가로 올린 무게를 더한
        // '합계'를 다시 입력해 맞춘다(이어서 계량은 행별 합산 모드가 없어 합계 재입력 방식).
        window.alert(
          `부족 계량: ${state.materials[i].material_name} (로트 ${j + 1})
`
          + `이론 ${fmt(th, dp())} g / 실제 ${fmt(Number(raw), dp())} g — ${fmt(Math.abs(v), dp())} g 부족

`
          + `저울을 다시 올려 채운 뒤, 최종 무게(합계)를 이 칸에 다시 입력하세요.`
        );
      }
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
      cell.textContent = fmt(state.theory[i], dp());
    });
    document.querySelectorAll("#cont-mat-body .cont-actual").forEach((act) => {
      const i = Number(act.dataset.i);
      const j = Number(act.dataset.j);
      const th = theoryFor(i, j);
      act.placeholder = th == null ? "" : fmt(th, dp());
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
    renderContRescaleSummary();  // 로트 수 변경 → 요약줄도 새 lotCount 에 맞춰 갱신
    scheduleDraftSave();         // 로트 수 변경도 임시 저장(복구용)
  }

  // ── 초과 계량 증량(rescale) — 로트별 스코프 ─────────────────
  // 배합 화면(blend.js 91caf17) 의 rescale 통합을 로트 단위로 이식. 차이: 초과가 난
  // '그 로트만' 증량한다(다른 로트 절대 불변). rescalePlan(순수, blend_lib) 으로 newTotal
  // 산출 → 25,000g 초과면 #cont-discard-modal, 아니면 #cont-rescale-modal.
  function offerContRescale(j) {
    // 이미 모달 열려 있거나 보류 제안이 있으면 중복 트리거 방지(Enter/change/총량 변경 경로).
    if (!$("cont-rescale-modal").hidden || !$("cont-discard-modal").hidden) return;
    if (!$("cont-rescale-approve-modal").hidden || !$("cont-rescale-block-modal").hidden) return;
    if (state.pendingContRescale) return;
    // 3회 금지 — 이미 2회 증량된 '그 로트'는 3회째 제안 자체를 막고 폐기 협의를 유도한다
    // (blend.js 단건과 동일 규칙, 로트별 스코프). pendingContRescale 을 세우지 않으므로 승인 경로 미도달.
    const applied = (state.lotRescaleEvents && state.lotRescaleEvents[j]) || [];
    if (applied.length >= 2) {
      openContRescaleBlockModal();
      return;
    }
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
      + `<span class="old">${fmt(lotTotal(j), dp())} g</span>`
      + `<span>→</span>`
      + `<span class="new">${fmt(plan.newTotal, dp())} g</span>`
      + `</div>`;
    if (overRows.length) {
      html += `<table class="rescale-add-table"><thead><tr><th>자재</th>`
        + `<th class="num">현재 실제량</th><th class="num">새 이론량</th>`
        + `<th class="num">추가로 넣을 양</th></tr></thead><tbody>`;
      overRows.forEach((r) => {
        const act = items[r.idx] ? items[r.idx].actual_amount : "";
        html += `<tr><td>${esc(r.name)}</td>`
          + `<td class="num">${fmt(Number(act), dp())}</td>`
          + `<td class="num">${fmt(r.newTheory, dp())}</td>`
          + `<td class="num add-cell">+${fmt(r.addNeeded, dp())}</td></tr>`;
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
        + `(예상 ${fmt(plan.newTotal, dp())} g). 폐기를 권장합니다.</p>`;
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
    state.lotRescalePlan[j] = plan;
    // 그 로트 열의 셀 편차·placeholder·헤더 즉시 재계산. 다른 로트는 불변.
    for (let i = 0; i < state.materials.length; i++) {
      updateCellVar(i, j);
      const inp = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
      if (inp) inp.placeholder = theoryFor(i, j) == null ? "" : fmt(theoryFor(i, j), dp());
    }
    renderContLotHeader(j);
    renderAddBadges(j);
    renderContRescaleSummary();
    updateContTotalLock();  // 증량은 lotRescale[j] 만 바꾸므로 잠금 상태는 유지 — 방어적 재적용.
    scheduleDraftSave();    // 증량 적용(override 총량·plan) 도 임시 저장(복구용)
    notify(`로트 ${j + 1} 배합량을 ${fmt(plan.newTotal, dp())} g 으로 증량했습니다 — 추가분을 계량하세요.`, "warn");
  }

  // ── 증량 승인 게이트(책임자 승인 없이는 증량 불가) — 로트별 스코프 ────────────
  // 배합 화면 blend.js 의 승인 게이트를 이어서 계량에 이식. [증량 적용]/[그래도 증량]을
  // 누르면 즉시 applyContRescale 하지 않고 이 승인 모달을 띄운다.
  //   [승인]: /api/blend/manager-verify 200 → 그 로트 증량 + {approval_id, approver} 이벤트.
  //   [부재로 진행]: 사유 필수 + 재확인 → 그 로트 증량 + {absence_reason} 이벤트(미승인 증량).
  // 승인 1회 = 증량 1회. 부족 채우기(추가분 계량)는 이 경로를 타지 않는다 — 승인 불필요.
  function csrfToken() {
    if (IRMS._core && IRMS._core.getCsrfToken) {
      const t = IRMS._core.getCsrfToken();
      if (t) return t;
    }
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function openContRescaleApproveModal() {
    // 제안/폐기 모달을 닫고 승인 모달을 연다(pendingContRescale 은 그대로 보존).
    closeContRescaleModal();
    closeContDiscardModal();
    const modal = $("cont-rescale-approve-modal");
    if (!modal) return;
    const pending = state.pendingContRescale;
    const lead = $("cont-rescale-approve-lead");
    if (lead && pending) {
      lead.textContent = `로트 ${pending.j + 1} 증량(→ ${fmt(pending.plan.newTotal, dp())} g)은 책임자 승인이 필요합니다. 책임자 이름과 비밀번호를 입력하세요.`;
    }
    const nameEl = $("cont-rescale-approve-name");
    const pwEl = $("cont-rescale-approve-pw");
    const reasonEl = $("cont-rescale-absence-reason");
    if (nameEl) nameEl.value = "";
    if (pwEl) pwEl.value = "";
    if (reasonEl) reasonEl.value = "";
    hideContApproveError();
    modal.hidden = false;
    if (nameEl) nameEl.focus();
  }

  function closeContRescaleApproveModal() {
    const modal = $("cont-rescale-approve-modal");
    if (modal) modal.hidden = true;
  }

  // 승인/부재 모달 취소(Escape/overlay) — 보류 중인 증량 제안을 버린다. 초과 계량 상태는
  // 그대로라 다음 change/Enter 에서 다시 제안이 뜬다.
  // 허용 편차를 +방향으로 벗어난 셀의 실제량을 모두 비운다 — 증량 제안/승인 거절
  // (다시 계량) 시 초과 상태가 남아 누적되던 사고 방지(blend.js clearOverActuals 동일).
  function clearOverContActuals() {
    const tol = state.toleranceG;
    let firstEl = null;
    state.materials.forEach((_, i) => {
      state.cells[i].forEach((cell, j) => {
        const th = theoryFor(i, j);
        if (cell.actual === "" || th == null) return;
        const v = Number(cell.actual) - th;
        if (v > tol + 1e-9) {
          cell.actual = "";
          const el = document.querySelector(`.cont-actual[data-i="${i}"][data-j="${j}"]`);
          if (el) { el.value = ""; if (!firstEl) firstEl = el; }
          updateCellVar(i, j);
        }
      });
    });
    updateContTotalLock();  // 셀 비움 후 잠금 상태 재평가(별도 합계 함수 없음)
    if (firstEl) {
      firstEl.focus();
      try { firstEl.select(); } catch (_e) { /* noop */ }
      notify("초과 계량 값을 비웠습니다 — 다시 계량하세요.", "warn");
    }
  }

  function cancelContRescaleApprove() {
    state.pendingContRescale = null;
    closeContRescaleApproveModal();
  }

  function showContApproveError(msg) {
    const err = $("cont-rescale-approve-error");
    if (err) { err.textContent = msg; err.hidden = false; }
  }
  function hideContApproveError() {
    const err = $("cont-rescale-approve-error");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  // 증량 확정 — pendingContRescale 소비 전에 그 로트의 before/after 총량을 잡아 이벤트를 기록한다.
  // applyContRescale 이 state.pendingContRescale 을 null 로 만들므로 순서가 중요하다.
  function finalizeContRescale(meta) {
    const pending = state.pendingContRescale;
    if (!pending) return;
    const { j } = pending;
    const before_total = lotTotal(j);          // 증량 전 그 로트의 현재 총량
    const after_total = pending.plan.newTotal;
    applyContRescale();                          // 총량·이론량·배지 갱신(기존 경로 재사용)
    const ev = { before_total, after_total };
    if (meta && meta.approval_id != null) ev.approval_id = meta.approval_id;
    if (meta && meta.approver != null) ev.approver = meta.approver;
    if (meta && meta.absence_reason != null) ev.absence_reason = meta.absence_reason;
    if (!state.lotRescaleEvents[j]) state.lotRescaleEvents[j] = [];
    state.lotRescaleEvents[j].push(ev);
    scheduleDraftSave();  // 증량 승인 이벤트가 반드시 초안에 남도록 즉시 갱신(추적성)
  }

  async function submitContManagerApproval() {
    const nameEl = $("cont-rescale-approve-name");
    const pwEl = $("cont-rescale-approve-pw");
    const name = nameEl ? nameEl.value.trim() : "";
    const pw = pwEl ? pwEl.value : "";
    if (!name) { showContApproveError("책임자 이름을 입력하세요."); if (nameEl) nameEl.focus(); return; }
    if (!pw) { showContApproveError("비밀번호를 입력하세요."); if (pwEl) pwEl.focus(); return; }
    hideContApproveError();
    const btn = $("cont-rescale-approve-submit");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/blend/manager-verify", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
        body: JSON.stringify({ username: name, password: pw }),
      });
      if (res.status === 401) { showContApproveError("비밀번호가 올바르지 않습니다."); return; }
      if (res.status === 403) { showContApproveError("책임자 권한이 없습니다."); return; }
      if (!res.ok) { showContApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요."); return; }
      const data = await res.json().catch(() => ({}));
      closeContRescaleApproveModal();
      finalizeContRescale({ approval_id: data.approval_id, approver: data.approver || name });
      notify(`책임자 승인 완료 (${data.approver || name}) — 증량을 적용합니다.`, "success");
    } catch (_e) {
      showContApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요.");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function submitContAbsenceProceed() {
    const reasonEl = $("cont-rescale-absence-reason");
    const reason = reasonEl ? reasonEl.value.trim() : "";
    if (!reason) { showContApproveError("책임자 부재 사유를 입력하세요."); if (reasonEl) reasonEl.focus(); return; }
    if (!window.confirm("책임자 승인 없이 증량을 적용합니다.\n이 로트는 '미승인 증량'으로 표시되고, 책임자 확인 전까지 알림이 반복됩니다.")) return;
    hideContApproveError();
    closeContRescaleApproveModal();
    finalizeContRescale({ absence_reason: reason });
    notify("미승인 증량으로 적용했습니다 — 책임자 확인 전까지 알림이 반복됩니다.", "warn");
  }

  function openContRescaleBlockModal() {
    const modal = $("cont-rescale-block-modal");
    if (modal) { modal.hidden = false; return; }
    notify("3회 증량은 불가합니다 — 이 로트는 책임자와 폐기 여부를 협의하세요.", "error big");
  }
  function closeContRescaleBlockModal() {
    const modal = $("cont-rescale-block-modal");
    if (modal) modal.hidden = true;
  }

  // 총 배합량 잠금 — 셀 실제량이 하나라도 입력되면 공용 총 배합량(cont-total)을 바꿀 수 없다.
  // (배합 화면 updateTotalLock 의 이어서 계량 버전 — 증량은 lotRescale[j] 로만, 총량 직접 상향으로
  // 승인 게이트를 우회하지 못하게 한다.) 기본 배합량 버튼도 함께 비활성화. 레시피/셀 초기화 시 자동 해제.
  function updateContTotalLock() {
    const totalInput = $("cont-total");
    if (!totalInput) return;
    const anyActual = state.cells.some((row) =>
      row && row.some((c) => c && c.actual !== "" && c.actual != null)
    );
    const links = $("cont-base-links");
    if (links) {
      links.querySelectorAll(".blend-base-link").forEach((b) => { b.disabled = anyActual; });
    }
    if (anyActual) {
      totalInput.readOnly = true;
      totalInput.title = "계량 시작 후에는 총 배합량을 바꿀 수 없습니다 (증량은 로트별 승인으로만)";
    } else {
      totalInput.readOnly = false;
      totalInput.removeAttribute("title");
    }
  }

  // 증량 적용 요약줄(로트별) — 각 증량된 로트의 자재별 '더 넣을 양'을 상시 표시.
  // 저장·레시피 변경 전까지 유지(타이핑 중에는 사라지지 않는다).
  function renderContRescaleSummary() {
    const el = $("cont-rescale-applied-summary");
    if (!el) return;
    const lots = [];
    for (let j = 0; j < state.lotCount; j++) {
      const plan = state.lotRescalePlan && state.lotRescalePlan[j];
      if (!plan) continue;
      const parts = [];
      plan.rows.forEach((r) => {
        if (r.addNeeded != null && Number(r.addNeeded) > 0) {
          const name = state.materials[r.idx] ? state.materials[r.idx].material_name : "";
          parts.push(appliedRescaleRowHtml(name, r));
        }
      });
      if (parts.length) {
        lots.push(`<span class="rescale-applied-title">로트 ${j + 1} (목표 ${fmt(plan.newTotal, 1)}g):</span>` + parts.join(""));
      }
    }
    if (!lots.length) { el.hidden = true; el.innerHTML = ""; return; }
    el.innerHTML = lots.join("");
    el.hidden = false;
  }
  function clearContRescaleSummary() {
    const el = $("cont-rescale-applied-summary");
    if (el) { el.hidden = true; el.innerHTML = ""; }
  }

  // 헤더 한 칸만 다시 그린다(증량 적용 직후 — 전체 render() 보다 가볍다).
  function renderContLotHeader(j) {
    const cols = document.querySelectorAll("#cont-mat-head th.cont-lot-col");
    const th = cols[j];
    if (!th) return;
    const rescaled = state.lotRescale && state.lotRescale[j];
    const chip = `<span class="cont-lot-chip">로트 ${j + 1}</span>`;
    const totalBadge = rescaled ? ` <small class="cont-lot-total">· ${fmt(rescaled, dp())} g</small>` : "";
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
    // 직전 대기 집합(이 lot 만) 기억 — 이번에 빠진(충족된) 셀은 편차 표시를 복원해야 한다.
    const prevPending = {};
    Object.keys(state.addPendingCells || {}).forEach((k) => {
      const parts = k.split(":");
      if (Number(parts[1]) === j) prevPending[k] = state.addPendingCells[k];
    });
    plan.rows.forEach((r) => {
      if (r.addNeeded === null || r.addNeeded <= tol + 1e-9) return;
      const key = `${r.idx}:${j}`;
      // 대기 셀로 등록 — updateCellVar/warnIfVariance 가 이 셀을 억제한다.
      state.addPendingCells[key] = r.addNeeded;
      const td = document.querySelector(`.cont-var[data-i="${r.idx}"][data-j="${j}"]`);
      if (!td) return;
      // 음수 편차 텍스트를 지우고 배지만 남긴다.
      td.textContent = "";
      td.className = "cont-var";
      const badge = document.createElement("button");
      badge.type = "button";
      badge.className = "blend-add-badge";
      badge.dataset.i = String(r.idx);
      badge.dataset.j = String(j);
      badge.textContent = `추가 +${fmt(r.addNeeded, dp())} g`;
      badge.title = "클릭해서 추가분을 입력하세요 (저울 PRINT 도 추가분으로 합산됩니다)";
      badge.addEventListener("click", () => openAddInline(r.idx, j));
      td.appendChild(badge);
    });
    // 이전에 대기였다가 이번에 충족된 셀 — 빈칸으로 남지 않게 편차 표시를 다시 그린다.
    Object.keys(prevPending).forEach((k) => {
      if (!(k in state.addPendingCells)) {
        const parts = k.split(":");
        updateCellVar(Number(parts[0]), Number(parts[1]));
      }
    });
    updateScaleTargetIndicator();  // 증량 대기 배지 변화 → 대상 표시 갱신
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
    // 저울 전용 모드면 증량 추가분 인라인 입력도 잠금(저울 PRINT/addMode 합산으로만).
    // 단, 수기 입력 승인(manualApproved)이 있으면 이 배합에 한해 손입력을 허용한다.
    if (state.scaleOnlyInput && !state.manualApproved) {
      input.readOnly = true;
      input.title = "저울 전용 모드 — 저울 PRINT 로만 입력됩니다";
    }
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
    // 저울 해상도(2자리)로 누계 — 배합 화면(blend.js)/rescalePlan 과 동일 단위 통일.
    cell.actual = String(Math.round(next * 100) / 100);
    cell.manual = false;
    // 추가 적용 셀은 더 이상 대기 상태가 아니다 — 억제 해제(편차·배지가 실제 상태 반영).
    if (state.addPendingCells) delete state.addPendingCells[`${i}:${j}`];
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
    updateContTotalLock();  // 추가분 합산으로 실제량이 채워진 경우도 총량 잠금 유지
    scheduleDraftSave();    // 추가분 합산 결과도 임시 저장(복구용)
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

    // 자재 LOT 필수 — 자재 LOT 이 셀별이므로 실제량을 넣은 (자재 × 로트) 셀마다 LOT 필수.
    // 미등록 LOT '사유 적고 진행' 으로 승인된 셀은 cell.lot 이 채워져 있어 만족된다.
    const lotMissingCells = [];
    let firstMissingLot = null;  // {i, j}
    for (let i = 0; i < state.materials.length; i++) {
      for (let j = 0; j < state.lotCount; j++) {
        const c = state.cells[i][j];
        const hasActual = c && c.actual !== "" && c.actual != null && Number(c.actual) > 0;
        const lot = (c && c.lot || "").trim();
        if (hasActual && !lot) {
          lotMissingCells.push(`로트 ${j + 1} · ${state.materials[i].material_name}`);
          if (!firstMissingLot) firstMissingLot = { i, j };
        }
      }
    }
    if (lotMissingCells.length) {
      notify("자재 LOT 를 입력하세요: " + lotMissingCells.slice(0, 6).join(", ") + (lotMissingCells.length > 6 ? " …" : ""), "error");
      if (firstMissingLot) focusLot(firstMissingLot.i, firstMissingLot.j);
      return fail(err, `자재 LOT 미입력: ${lotMissingCells.slice(0, 6).join(", ")}${lotMissingCells.length > 6 ? " 외" : ""}. LOT 를 입력하세요.`);
    }

    // 작업자 확인/교대
    if (worker !== state.sessionWorker && !(await switchWorker(worker))) return;
    // 미등록 LOT 차단 — 반제품(제안 있는 자재) 셀의 비어있지 않은 자재 LOT 를 (자재 × 로트)
    // 셀마다 순차 검증. 하나라도 미등록이면 그 셀의 모달을 띄우고 저장을 중단한다(일반 자재 제외).
    for (let i = 0; i < state.materials.length; i++) {
      const name = (state.materials[i].material_name || "").trim();
      if (!state.lotSuggest || !state.lotSuggest[name]) continue;
      for (let j = 0; j < state.lotCount; j++) {
        const lot = (state.cells[i][j] && state.cells[i][j].lot || "").trim();
        if (!lot) continue;
        if (lotOverrideKey(name, lot) in state.lotOverrides) continue;  // 사유 입력 후 진행 승인됨
        if (!(await checkLotRegistered(name, lot))) {
          const input = document.querySelector(`.cont-cell-lot[data-i="${i}"][data-j="${j}"]`);
          openContLotInvalidModal(name, lot, input || null);
          return;
        }
      }
    }
    if (!window.confirm(`작업자 '${state.sessionWorker}' 이름으로 ${state.lotCount}개 로트를 저장합니다. 맞습니까?`)) return;
    // 승인된 미등록 LOT 이 저장에 포함되면 사유를 비고 앞에 남긴다(전 로트 공통 비고).
    const overrideNote = buildOverrideNote();

    const lots = [];
    for (let j = 0; j < state.lotCount; j++) {
      lots.push(state.materials.map((m, i) => ({
        material_id: m.material_id,
        material_name: m.material_name,
        material_code: m.material_code,
        ratio: m.ratio,
        theory_amount: theoryFor(i, j),
        actual_amount: Number(state.cells[i][j].actual),
        material_lot: (state.cells[i][j].lot || "").trim() || null,
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
      note: [overrideNote, buildManualApprovalNote(), $("cont-note").value.trim()].filter(Boolean).join("\n") || null,
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
    // 증량 승인 이벤트(로트별) — lots 와 평행(인덱스 j = 로트 j). 이벤트 없는 로트는 null.
    // 하나라도 있으면 전송(서버가 로트마다 validate_rescale_events 로 승인 소비·검증·3회 제한).
    // 전부 없으면 미전송 — 기존 동작(rescale 컬럼 기본값 유지)과 완전 동일.
    const hasRescaleEvents = state.lotRescaleEvents.some((e) => e && e.length);
    if (hasRescaleEvents) {
      body.lot_rescale_events = Array.from({ length: state.lotCount }, (_, j) => {
        const e = state.lotRescaleEvents[j];
        return e && e.length ? e : null;
      });
    }
    try {
      const res = await request("/blend/records/continuous", { method: "POST", body });
      clearDraft();  // 저장 완료 → 임시 저장 삭제(복구 배너가 다시 뜨지 않게)
      notify(`${res.created}개 로트 저장 완료: ${(res.product_lots || []).join(", ")} — 배합 기록으로 이동합니다.`, "success");
      setTimeout(() => window.location.assign("/status"), 900);
    } catch (e) {
      const msg = (e && e.message) || "";
      // 복구된 초안의 증량 승인(approval_id)은 30분 경과 시 서버(validate_rescale_events)에서
      // 만료된다. 단건 배합(blend.js)의 저장 시점 재인증 전체 플로우는 이어서 계량에 아직 없다 —
      // 대신 만료가 확인되면 초과 계량 증량을 다시 승인받도록 명확히 안내한다(간소 정책).
      // 해당 로트의 초과 셀을 다시 확정(Enter/blur)하면 offer→승인 모달이 다시 뜬다.
      if (msg.includes("증량 승인이 유효하지 않습니다")) {
        notify("증량 승인이 만료되었습니다 — 초과 계량 증량을 다시 승인받으세요(책임자 재인증 필요). "
          + "해당 로트의 초과 셀 값을 다시 확정하면 승인 창이 다시 뜹니다.", "error big");
        return fail(err, "증량 승인이 만료되었습니다(30분 경과). 초과 계량 증량을 다시 승인받은 뒤 저장하세요 — 책임자 재인증 필요.");
      }
      fail(err, msg);
    }
  }

  function fail(err, msg) {
    err.textContent = msg;
    err.hidden = false;
  }

  // ── 바인딩/초기화 ───────────────────────────────────────────
  function bind() {
    // 셀 포커스 이동 시 저울 대상 표시 갱신(delegated — 재렌더돼도 유지). tbody 는
    // render 가 innerHTML 만 갈아끼우므로 한 번만 부착한다. focusout 후 새 focusin 이
    // activeElement 를 확정하도록 microtask 로 지연 갱신.
    const matBody = $("cont-mat-body");
    if (matBody) {
      matBody.addEventListener("focusin", updateScaleTargetIndicator);
      matBody.addEventListener("focusout", () => setTimeout(updateScaleTargetIndicator, 0));
    }
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
      scheduleDraftSave();  // 총 배합량 변경도 임시 저장(복구용)
    });
    // 비고·반응기·저울 변경도 임시 저장에 반영(단건 배합 blend.js 와 동일).
    const noteEl = $("cont-note");
    if (noteEl) noteEl.addEventListener("input", scheduleDraftSave);
    const reactorEl = $("cont-reactor");
    if (reactorEl) reactorEl.addEventListener("change", scheduleDraftSave);
    const scaleEl = $("cont-scale");
    if (scaleEl) scaleEl.addEventListener("input", scheduleDraftSave);
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

    $("cont-date").addEventListener("change", () => { updateLotPreview(); scheduleDraftSave(); });
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

    // 임시 저장 복구 배너 — [이어서 하기]=초안 복원 / [버리기]=초안 삭제.
    const restoreYes = $("cont-restore-yes");
    if (restoreYes) restoreYes.addEventListener("click", () => { restoreDraft().catch((e) => notify(e.message, "error")); });
    const restoreNo = $("cont-restore-no");
    if (restoreNo) restoreNo.addEventListener("click", () => {
      clearDraft();
      const banner = $("cont-restore-banner"); if (banner) banner.hidden = true;
    });

    state.workerPad = attachSignaturePad($("cont-worker-sign"));
    const wclr = $("cont-worker-sign-clear");
    if (wclr && state.workerPad) wclr.addEventListener("click", () => state.workerPad.clear());

    // 증량(rescale) 모달 — hidden 속성 토글로만 열고 닫는다(display 직접 지정 금지).
    // [증량 적용]/[그래도 증량]은 즉시 적용하지 않고 책임자 승인 모달로 게이트한다(승인 1회=증량 1회).
    const rescaleApply = $("cont-rescale-apply");
    if (rescaleApply) rescaleApply.addEventListener("click", openContRescaleApproveModal);
    const rescaleCancel = $("cont-rescale-cancel");
    if (rescaleCancel) rescaleCancel.addEventListener("click", () => {
      state.pendingContRescale = null;
      closeContRescaleModal();
    });
    const discardForce = $("cont-discard-force");
    if (discardForce) discardForce.addEventListener("click", openContRescaleApproveModal);
    const discardCancel = $("cont-discard-cancel");
    if (discardCancel) discardCancel.addEventListener("click", () => {
      state.pendingContRescale = null;
      closeContDiscardModal();
    });
    // 증량 승인 모달 — [승인](책임자 검증) / [부재로 진행](사유+재확인). Esc/overlay=취소.
    const approveSubmit = $("cont-rescale-approve-submit");
    if (approveSubmit) approveSubmit.addEventListener("click", () => submitContManagerApproval());
    const approvePw = $("cont-rescale-approve-pw");
    if (approvePw) approvePw.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      submitContManagerApproval();
    });
    const absenceSubmit = $("cont-rescale-absence-submit");
    if (absenceSubmit) absenceSubmit.addEventListener("click", submitContAbsenceProceed);
    const approveModal = $("cont-rescale-approve-modal");
    // 바깥 클릭으로는 닫히지 않는다 — 미해소 초과 누적 방지(blend.js 와 동일 정책).
    function dismissContApproveWithReweigh() {
      if (!window.confirm("입력한 초과값을 비우고 다시 계량합니다. 계속할까요?")) return;
      state.pendingContRescale = null;
      closeContRescaleApproveModal();
      clearOverContActuals();
    }
    if (approveModal) approveModal.addEventListener("click", (e) => {
      if (e.target === approveModal) {
        showContApproveError("승인, 부재로 진행, 또는 Esc(다시 계량) 중에서 선택하세요.");
      }
    });
    // 저울 전용 모드 수기 입력 승인 — 요청 버튼/모달 [승인]·[취소]·Enter·Esc.
    const manualReq = $("cont-manual-entry-request-btn");
    if (manualReq) manualReq.addEventListener("click", openManualApproveModal);
    const manualSubmit = $("cont-manual-approve-submit");
    if (manualSubmit) manualSubmit.addEventListener("click", () => submitManualApproval());
    const manualCancel = $("cont-manual-approve-cancel");
    if (manualCancel) manualCancel.addEventListener("click", closeManualApproveModal);
    const manualPw = $("cont-manual-approve-pw");
    if (manualPw) manualPw.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      submitManualApproval();
    });
    const manualModal = $("cont-manual-approve-modal");
    if (manualModal) manualModal.addEventListener("click", (e) => {
      if (e.target === manualModal) closeManualApproveModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && manualModal && !manualModal.hidden) closeManualApproveModal();
    });
    // 3회 증량 차단 모달 — 확인만.
    const blockClose = $("cont-rescale-block-close");
    if (blockClose) blockClose.addEventListener("click", closeContRescaleBlockModal);
    const blockModal = $("cont-rescale-block-modal");
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (approveModal && !approveModal.hidden) { dismissContApproveWithReweigh(); return; }
      if (blockModal && !blockModal.hidden) { closeContRescaleBlockModal(); return; }
    });
    // 미등록 LOT 확인 버튼 — 모달 닫고 해당 LOT 칸 값·state 비운 뒤 다시 포커스.
    const lotConfirm = $("cont-lot-invalid-confirm");
    if (lotConfirm) lotConfirm.addEventListener("click", () => {
      const modal = $("cont-lot-invalid-modal");
      const input = modal && modal._lotInput;
      closeContLotInvalidModal();
      if (input) {
        const i = Number(input.dataset.i);
        const j = Number(input.dataset.j);
        if (state.cells[i] && state.cells[i][j]) state.cells[i][j].lot = "";
        input.value = "";
        input.focus();
      }
    });
    // 미등록 LOT '사유 적고 진행'(안전밸브) — 배합 화면과 동일. 1클릭: 사유칸 표시 /
    // 2클릭(사유 입력됨): 그 (자재,LOT) 통과 처리 + 사유 보관(저장 시 비고).
    const lotProceed = $("cont-lot-invalid-proceed");
    if (lotProceed) lotProceed.addEventListener("click", () => {
      const box = $("cont-lot-override-box");
      const reason = $("cont-lot-override-reason");
      if (box && box.hidden) { box.hidden = false; if (reason) reason.focus(); return; }
      const text = (reason && reason.value.trim()) || "";
      if (!text) { notify("진행 사유를 입력하세요.", "error"); if (reason) reason.focus(); return; }
      const modal = $("cont-lot-invalid-modal");
      state.lotOverrides[lotOverrideKey(modal._lotName, modal._lotValue)] = text;
      scheduleDraftSave();  // 미등록 LOT 진행 승인·사유도 임시 저장(복구용)
      const input = modal._lotInput;
      closeContLotInvalidModal();
      if (input) input.focus();
      notify("사유를 남기고 진행합니다 — 이 로트는 기록에 '미등록 진행'으로 남습니다.", "warn");
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
    offerRestore();  // 작성 중이던 이어서 계량이 있으면 이어서 할지 배너로 제안
    detectScale();
    setInterval(detectScale, 30000);
    setInterval(pollScaleEvents, 800);
    // 저울 전용 입력 모드 로드(실패 시 false 폴백). 켜져 있으면 실제량 입력칸 잠금.
    loadScaleOnlyInput();
  });
})();

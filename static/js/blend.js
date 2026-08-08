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

  // 임시저장 슬롯 저장소(최대 3칸) + 레시피 변경 정합성 헬퍼 — blend_drafts.js.
  const blendDrafts = IRMS.blendDrafts;

  // 순수 헬퍼 라이브러리 — 포맷터/HTML 빌더/수치 계산. 동일 이름으로 분해 할당하여
  // 기존 호출부를 그대로 유지한다.
  const {
    esc,
    TOLERANCE_G,
    ANCHOR_BADGE,
    fmt,
    toleranceDecimals,
    todayISO,
    nowTime,
    rowVariance,
    varianceVerdict,
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
    rescaleBarsHtml,
    exceedsBatchLimit,
    pickScaleRow,
    isAddModeRow,
    resolveAddPortion,
    createIdleLogout,
  } = window.IRMS.blendLib;

  const $ = (id) => document.getElementById(id);

  // LOT 검사 모달(공용 컴포넌트) — 배합·다중 계량이 공유. 접두어 없이 기존 id 그대로.
  const lotModal = window.IRMS.blendLotModal.create({
    prefix: "",
    esc,
    notify,
    csrfToken,
  });

  // LOT 등록 여부 조회 캐시(공용) — 부정 결과에만 TTL. 예전에는 state.lotChecked 로
  // 영구 캐시해서, 모달이 시킨 대로 1차 배합을 저장하고 돌아와 같은 LOT 을 다시
  // 입력해도 계속 막혔다(새로고침 전에는 풀리지 않음). 저장 성공 시에도 clear().
  const lotCheckCache = window.IRMS.blendLotModal.createCache({});

  const state = { recipes: [], current: null, items: [], viscProducts: [], workers: [], scaleReady: false, sessionWorker: "", anchorIndex: -1, prevAnchorActual: "", toleranceG: TOLERANCE_G, _anchorRecomputing: false,
    // 이 창이 쓰고 있는 임시저장 슬롯 id(최대 3칸 중 하나). 같은 레시피로 이어서 작업하는
    // 동안은 이 슬롯만 갱신한다. 저장 완료·복구 실패 시 null 로 되돌린다.
    draftSlotId: null,
    // 계량 중 자재 폐기 이력 [{material_name, material_code, amount_g}] — '처음부터 다시'
    // 재계량에서 실제로 버린 자재. 저장 body.discard_events 로 전송돼 기록에 남는다
    // (편차 강제라 최종 수치엔 안 보이는 소모의 유일한 흔적). 초안에도 왕복.
    discardEvents: [],
    // 반제품 원료 LOT 자동 제안: 레시피 자재명 → 최근 product_lot 목록.
    // 자재명이 "배합 기록이 있는 반제품명"과 일치하면 그 제품의 최근 LOT 을 제안.
    // 레시피 선택 시 1회 호출(실패는 조용히 무시 — 제안 없이 기존 동작 유지).
    lotSuggest: {},
    // (LOT 등록 여부 캐시는 모듈 상단 lotCheckCache 로 옮겼다 — 부정 결과에 TTL 이
    //  필요해서다. 예전 state.lotChecked 는 만료가 없어, 모달이 시킨 대로 1차 배합을
    //  저장하고 돌아와도 같은 LOT 이 새로고침 전까지 계속 막혔다.)
    // 앞 단계 기록에 없는 LOT '확인하고 진행' — (자재명\u0000LOT) → 사유(빈 값 가능).
    // **키의 존재 자체**가 작업자가 확인 창에서 '계속' 을 눌렀다는 뜻이다(사유는 선택).
    // 저장 시 비고 + lot_overrides payload 로 나가 서버가 대사용으로 보관한다.
    // 레시피 변경·저장 시 초기화.
    lotOverrides: {},
    // 초과 계량 증량(rescale). 기준 자재 레시피에서 총량이 기준 자재 실측값으로
    // 파생되므로 증량분을 별도로 보관 — 유효 총량 = max(기준 파생 총량, rescaleTotalG).
    // 레시피 변경/저장 후 초기화 시 0(미사용)으로 리셋.
    rescaleTotalG: 0,
    // 추가분 입력 모드에 들어간 행 인덱스(저울 PRINT 를 추가분으로 합산하기 위한 플래그).
    addModeIdx: null,
    // 작업자가 직접 지정한 저울 PRINT 대상 행(수동 오버라이드). null 이면 미지정.
    // activeScaleRow 우선순위: (1)추가모달 (2)이 값 (3)포커스 행 (4)첫 빈 행 (5)기준 폴백.
    // 지정 후 그 행이 허용 편차 내로 완료·레시피 변경·저장·초기화 시 해제(sticky).
    scaleTargetIdx: null,
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
    // 저울 전용 입력 모드(운영 대시보드 토글). true 면 실제량·증량 인라인 입력이
    // readonly 가 되고 저울 PRINT 로만 입력된다. false(기본)면 동작 변화 없음.
    scaleOnlyInput: false,
    // 저울 전용 모드 수기 입력 승인 — 책임자 승인 시 {approver}. 이 배합에 한해 실제량
    // 손입력을 허용(잠금 해제)한다. 저장 완료·초기화·레시피 변경 시 null 로 되돌려 재잠금.
    manualApproved: null,
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
    updateScaleTargetIndicator();  // 저울 연결 상태 변화 → ⚖ 버튼·대상 표시 토글
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
  // 책임자 수기 입력 승인(state.manualApproved)이 있으면 이 배합에 한해 잠금을 해제한다.
  // 용수 분류는 수기 입력 허용 — 물은 유량계/부피 계량이라 저울 전용 잠금이 맞지 않는다
  // (사용자 결정 2026-07-23). 분류는 레시피 목록(state.recipes)에서 찾는다.
  function isWaterCategoryRecipe() {
    const rid = state.current && state.current.recipe ? state.current.recipe.id : null;
    if (rid == null || !Array.isArray(state.recipes)) return false;
    const rec = state.recipes.find((r) => r.id === rid);
    return Boolean(rec && rec.category === "용수");
  }

  function applyScaleOnlyToRows() {
    if (!state.scaleOnlyInput) return;  // 모드 아니면 손대지 않음(기본 동작)
    if (isWaterCategoryRecipe()) {
      // 용수: 잠금 해제(승인 불필요) — 이미 잠겨 있던 입력도 풀어준다.
      document.querySelectorAll("#blend-mat-body .blend-actual, #blend-mat-body .blend-add-inline")
        .forEach((el) => { el.readOnly = false; el.removeAttribute("title"); });
      return;
    }
    const lock = !state.manualApproved;
    const titleText = "저울 전용 모드 — 저울 PRINT 로만 입력됩니다";
    document.querySelectorAll("#blend-mat-body .blend-actual").forEach((el) => {
      el.readOnly = lock;
      if (lock) el.title = titleText; else el.removeAttribute("title");
    });
    document.querySelectorAll("#blend-mat-body .blend-add-inline").forEach((el) => {
      el.readOnly = lock;
      if (lock) el.title = titleText; else el.removeAttribute("title");
    });
  }

  // (구) 상단 배너는 아래 컨트롤 줄과 중복 + 해제 방법은 작업자 대상 정보가 아니라 제거
  // (2026-07-23). 저울 미연결 상태는 컨트롤 줄 텍스트가 흡수한다.
  function updateScaleOnlyBanner() {
    const banner = document.getElementById("scale-only-banner");
    if (banner) banner.hidden = true;  // 옛 템플릿 캐시 대비 항상 숨김
    updateManualEntryControl();
  }

  // 저울 전용 모드 수기 입력 승인 컨트롤 — 모드가 켜져 있을 때만 노출. 승인 전에는
  // '수기 입력 승인 요청' 버튼, 승인 후에는 승인자 안내 텍스트만(버튼 숨김).
  function updateManualEntryControl() {
    const box = $("scale-only-control");
    if (!box) return;
    box.hidden = !state.scaleOnlyInput;
    if (!state.scaleOnlyInput) return;
    if (isWaterCategoryRecipe()) {
      const text = $("scale-only-control-text");
      const btn = $("manual-entry-request-btn");
      if (text) text.textContent = "용수 분류 — 수기 입력이 허용됩니다(저울 전용 예외).";
      if (btn) btn.hidden = true;
      box.classList.add("is-approved");
      applyScaleOnlyToRows();
      return;
    }
    const text = $("scale-only-control-text");
    const btn = $("manual-entry-request-btn");
    if (state.manualApproved) {
      if (text) {
        text.textContent = state.manualApproved.absence_reason
          ? `수기 입력 진행 — 책임자 부재(${state.manualApproved.absence_reason}) · 사후 확인 대상`
          : `수기 입력 승인됨 — 승인자 ${state.manualApproved.approver} (이 배합에 한함)`;
      }
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

  // ── 빠른 사유 태그(채움형) ──────────────────────────────────────
  // [책임자 부재]/[야간 근무] 버튼을 누르면 사유 입력칸에 그 문구를 토글로 넣고 뺀다.
  // 자유 텍스트도 그대로 편집 가능(사유는 " · "로 이어붙인다). 증량·수기 부재 공용.
  function toggleReasonTag(inputEl, tag) {
    if (!inputEl) return;
    const parts = String(inputEl.value || "").split("·").map((s) => s.trim()).filter(Boolean);
    const at = parts.indexOf(tag);
    if (at >= 0) parts.splice(at, 1);
    else parts.push(tag);
    inputEl.value = parts.join(" · ");
    syncReasonTags(inputEl);
  }
  function syncReasonTags(inputEl) {
    if (!inputEl) return;
    const wrap = document.querySelector(`.rescale-absence-tags[data-reason-target="${inputEl.id}"]`);
    if (!wrap) return;
    const val = String(inputEl.value || "");
    wrap.querySelectorAll(".reason-tag").forEach((b) => {
      b.classList.toggle("is-on", val.includes(b.dataset.tag));
    });
  }
  function wireReasonTags() {
    document.querySelectorAll(".rescale-absence-tags .reason-tag").forEach((btn) => {
      btn.addEventListener("click", () => {
        const wrap = btn.closest(".rescale-absence-tags");
        const input = wrap && document.getElementById(wrap.dataset.reasonTarget);
        toggleReasonTag(input, btn.dataset.tag);
      });
    });
  }

  // ── 저울 전용 모드 수기 입력 승인 게이트 ───────────────────────
  // '수기 입력 승인 요청' → /api/blend/manager-verify(purpose=manual) 200 → 이 배합에 한해
  // 손입력 허용. 책임자 부재 시엔 사유(+빠른 사유 태그)만 남기고 진행하며, 그 기록은
  // manual_entry 표시 + 비고의 부재 사유로 책임자가 사후 확인한다. 저장·초기화·레시피
  // 변경 시 재잠금.
  function openManualApproveModal() {
    const modal = $("manual-approve-modal");
    if (!modal) return;
    const nameEl = $("manual-approve-name");
    const pwEl = $("manual-approve-pw");
    const reasonEl = $("manual-absence-reason");
    if (nameEl) nameEl.value = "";
    if (pwEl) pwEl.value = "";
    if (reasonEl) { reasonEl.value = ""; syncReasonTags(reasonEl); }
    hideManualApproveError();
    modal.hidden = false;
    if (nameEl) nameEl.focus();
  }
  function closeManualApproveModal() {
    const modal = $("manual-approve-modal");
    if (modal) modal.hidden = true;
  }
  function showManualApproveError(msg) {
    const err = $("manual-approve-error");
    if (err) { err.textContent = msg; err.hidden = false; }
  }
  function hideManualApproveError() {
    const err = $("manual-approve-error");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  async function submitManualApproval() {
    const nameEl = $("manual-approve-name");
    const pwEl = $("manual-approve-pw");
    const name = nameEl ? nameEl.value.trim() : "";
    const pw = pwEl ? pwEl.value : "";
    if (!name) { showManualApproveError("책임자 이름을 입력하세요."); if (nameEl) nameEl.focus(); return; }
    if (!pw) { showManualApproveError("비밀번호를 입력하세요."); if (pwEl) pwEl.focus(); return; }
    hideManualApproveError();
    const btn = $("manual-approve-submit");
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
      applyScaleOnlyToRows();      // 이 배합의 실제량 입력칸 잠금 해제
      updateManualEntryControl();  // 배너 텍스트를 승인 안내로 전환(버튼 숨김)
      notify(`수기 입력 승인 완료 (${approver}) — 이 배합에 한해 손입력이 허용됩니다.`, "success");
    } catch (_e) {
      showManualApproveError("승인 확인 중 오류가 발생했습니다. 다시 시도하세요.");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // [부재로 진행]: 사유 필수 → 비밀번호 없이 이 배합의 손입력 잠금을 해제하되, 부재
  // 사유를 남겨 저장 시 '미승인 수기 입력'으로 표시된다(manual_entry + 비고). 증량 부재와
  // 달리 반복 알림 루프는 붙이지 않는다 — 성격상 manual_entry 표시로 사후 확인이 성립.
  function submitManualAbsence() {
    const reasonEl = $("manual-absence-reason");
    const reason = reasonEl ? reasonEl.value.trim() : "";
    if (!reason) {
      showManualApproveError("책임자 부재 사유를 입력하세요.");
      if (reasonEl) reasonEl.focus();
      return;
    }
    hideManualApproveError();
    state.manualApproved = { approver: null, absence_reason: reason };
    closeManualApproveModal();
    applyScaleOnlyToRows();
    updateManualEntryControl();
    notify("책임자 부재로 수기 입력을 진행합니다 — 사유가 기록에 남아 사후 확인됩니다.", "warn");
  }

  // 저장 시 비고에 남길 수기 입력 승인/부재 표시(미등록 LOT 사유와 동일 방식으로 append).
  function buildManualApprovalNote() {
    if (!state.manualApproved) return "";
    if (state.manualApproved.absence_reason) {
      return `[수기 입력 · 책임자 부재] 사유: ${state.manualApproved.absence_reason}`;
    }
    return `[수기 입력 승인] 승인자: ${state.manualApproved.approver}`;
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
    // 모달이 열려 있는 동안은 addModeIdx 가 회차마다 꺼지므로(_addWeighIdx) 함께 본다 —
    // 안 그러면 2회차 PRINT 가 합산이 아니라 덮어쓰기가 된다.
    if (isAddModeRow(idx, state.addModeIdx, _addWeighIdx)) {
      // 모달의 저울 상태 선택에 따라 환산: tared=추가분 그대로 / loaded=표시값−현재.
      // 구분 없이 합산하면 영점 안 잡힌 저울의 PRINT(누계)가 이중 계산된다(2026-08-04 시안).
      if (idx === _addWeighIdx && _awMode) {
        const awIt = state.items[idx];
        const cur = awIt && awIt.actual_amount !== "" ? (Number(awIt.actual_amount) || 0) : 0;
        const res = resolveAddPortion(_awMode, Number(value), cur);
        if (!res.ok) {
          notify(res.reason === "not-above-current"
            ? `PRINT 값(${value} g)이 현재 담은 양(${fmt(cur, dp())} g)보다 크지 않습니다 — 저울 상태 선택이 맞는지 [변경]으로 확인하세요.`
            : `PRINT 값(${value} g)을 적용할 수 없습니다 — 값을 확인하세요.`, "error big");
          return;
        }
        applyAddAmount(idx, res.portion);
        return;
      }
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
    // PRINT 는 input 이벤트가 없어 초안 저장이 안 걸렸다 — 마지막 자재의 PRINT 값은
    // 다음 LOT 타이핑이 없으면 초안에 빠져 창 닫힘 복구에서 사라졌다(2026-08-04).
    scheduleDraftSave();
    // 저울 PRINT 값이 허용 편차를 벗어나면 다음 LOT 로 넘어가지 않는다 — 해당 실제량
    // 칸에 머물러 재계량(부족: 더 넣기 / 초과: 증량 제안)을 유도.
    if (warnIfVariance(idx)) {
      if (input) { input.focus(); if (input.select) input.select(); }
      updateScaleTargetIndicator();
      return;
    }
    // 이 행이 수동 지정 대상이었고 허용 편차 내로 완료됐으면 지정 해제(sticky 종료).
    if (state.scaleTargetIdx === idx) state.scaleTargetIdx = null;
    const nextLot = document.querySelector(`.blend-lot[data-idx="${idx + 1}"]`);
    if (nextLot) {
      nextLot.focus();
    } else {
      const save = $("blend-save");
      if (save) save.focus();
    }
    updateScaleTargetIndicator();
  }

  // PRINT 키 입력이 들어갈 행: 합산 모드 행 > 커서가 있는 행(LOT/실제량) > 첫 미입력 행
  function activeScaleRow() {
    // 추가(합산) 모드 중이면 PRINT 는 무조건 그 행으로. 인라인 추가 입력칸은
    // blend-actual/blend-lot 클래스가 아니어서 포커스 감지에 안 걸리고, 그 행의
    // actual 은 이미 채워져 있어 폴백도 그 행을 건너뛰었다 — 부족 보충 PRINT 가
    // 엉뚱한 빈 행으로 가던 버그(2026-07-22 흐름 재검토 BUG-1). 저울 전용 모드의
    // 부족 복구(타이핑 불가)도 이 라우팅이 있어야 성립한다.
    // 우선순위 규칙은 blend_lib.pickScaleRow 가 소유한다(테스트로 잠근 순수 함수).
    // 모달이 열려 있는 행(_addWeighIdx)까지 포함해야 2회차 이후 PRINT 가 다음 품목으로
    // 새지 않는다(현장 신고 2026-08-03).
    const focused = document.activeElement;
    const focusedIdx = (
      focused && focused.classList
      && (focused.classList.contains("blend-actual") || focused.classList.contains("blend-lot"))
    ) ? Number(focused.dataset.idx) : null;
    const picked = pickScaleRow({
      addModeIdx: state.addModeIdx,
      addWeighIdx: _addWeighIdx,
      // 부족 창은 상태 선택 모달로 통합 — 떠 있는 동안 PRINT 는 게이트가 버린다
      // (printBlockingModalVisible 의 scale-state-modal 항목). shortageIdx 는 더 이상
      // 별도 플래그로 쓰지 않으니 null 로 둔다.
      shortageIdx: null,
      stickyIdx: state.scaleTargetIdx,
      stickyValid: state.scaleTargetIdx != null && !!state.items[state.scaleTargetIdx],
      focusedIdx,
    });
    if (picked != null) return picked;
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

  // ── 저울 PRINT 대상 행 지정·표시 ─────────────────────────────
  // 작업자가 ⚖ 버튼으로 대상 행을 직접 고르면 sticky 로 보관(scaleTargetIdx). 저울
  // PRINT 가 어디로 들어갈지 보이게(row-scale-target 강조 + ⚖ 저울 태그) 하고, 다음
  // PRINT 를 그 행으로 라우팅한다. 지정 없으면 포커스/첫 빈 행 폴백은 그대로.
  function setScaleTarget(idx) {
    state.scaleTargetIdx = idx;
    updateScaleTargetIndicator();
  }

  function clearScaleTarget() {
    if (state.scaleTargetIdx == null) return;
    state.scaleTargetIdx = null;
    updateScaleTargetIndicator();
  }

  // 현재 유효 저울 대상 행 계산 후 화면 표시 갱신. 저울 연결(scaleReady) 시에만 노출 —
  // 수동 전용 현장에는 아무 표식도 띄우지 않는다. 추가(합산) 모달이 열려 있으면 그
  // 모달 자체가 안내이므로 행 강조는 지운다.
  function updateScaleTargetIndicator() {
    const body = $("blend-mat-body");
    if (!body) return;
    // 수동 지정 행이 허용 편차 내로 채워졌으면 지정 자동 해제(다음 빈 행으로 이동).
    if (state.scaleTargetIdx != null) {
      const it = state.items[state.scaleTargetIdx];
      const pend = state.addPending && state.addPending[state.scaleTargetIdx] != null;
      if (!it) {
        state.scaleTargetIdx = null;
      } else if (it.actual_amount !== "" && !pend
        && varianceVerdict(Number(it.actual_amount), it.theory_amount, state.toleranceG).within) {
        state.scaleTargetIdx = null;
      }
    }
    body.querySelectorAll("tr.row-scale-target").forEach((tr) => tr.classList.remove("row-scale-target"));
    body.querySelectorAll(".scale-target-tag").forEach((el) => el.remove());
    body.querySelectorAll(".blend-scale-btn.is-active").forEach((b) => b.classList.remove("is-active"));
    // ⚖ 버튼은 저울 연결 시에만 노출(수동 전용 현장 노이즈 제거).
    body.classList.toggle("scale-connected", Boolean(state.scaleReady));
    if (!state.scaleReady) return;
    // 추가 합산 모달이 열려 있으면 모달이 안내 역할 — 행 강조는 생략(버튼만 활성 표시).
    if (state.addModeIdx != null) {
      const b = body.querySelector(`.blend-scale-btn[data-idx="${state.addModeIdx}"]`);
      if (b) b.classList.add("is-active");
      return;
    }
    const idx = activeScaleRow();
    if (idx == null) return;
    const inp = body.querySelector(`.blend-actual[data-idx="${idx}"]`);
    const tr = inp && inp.closest("tr");
    if (tr) tr.classList.add("row-scale-target");
    if (inp && inp.parentElement && !inp.parentElement.querySelector(".scale-target-tag")) {
      const tag = document.createElement("span");
      tag.className = "scale-target-tag";
      tag.textContent = "⚖ 저울";
      inp.parentElement.appendChild(tag);
    }
    const btn = body.querySelector(`.blend-scale-btn[data-idx="${idx}"]`);
    if (btn) btn.classList.add("is-active");
  }

  // 자재 행 실제량 칸에 붙는 ⚖ 버튼. 증량 대기 행이면 추가 합산 모달을 열고, 일반
  // 행이면 그 행을 저울 대상으로 지정하고 실제량 칸에 포커스.
  function buildScaleTargetButton(idx) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "blend-scale-btn";
    btn.dataset.idx = String(idx);
    btn.tabIndex = -1;
    btn.textContent = "⚖";
    btn.title = "여기로 저울 입력";
    btn.addEventListener("click", () => {
      if (state.addPending && state.addPending[idx] != null) {
        requestAddWeigh(idx);
        return;
      }
      setScaleTarget(idx);
      const inp = document.querySelector(`.blend-actual[data-idx="${idx}"]`);
      if (inp) { inp.focus(); if (inp.select) { try { inp.select(); } catch (_e) { /* noop */ } } }
    });
    return btn;
  }

  // 나눠 담기 — 비커 용량을 넘는 자재를 여러 번에 나눠 계량한다.
  // 합산 기계장치(applyAddAmount)는 원래 '부족분 보충'용으로 이미 있었지만, 들어가는
  // 문이 편차 경고밖에 없어서 계획된 분할이 매번 오류로 취급됐다(8kg 씩 3번이면 경고
  // 2번). 여기서 먼저 선언하고 들어가면 그 경로를 오류 없이 그대로 쓴다.
  function buildSplitButton(idx) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "blend-split-btn";
    btn.dataset.idx = String(idx);
    btn.tabIndex = -1;
    btn.textContent = "⊞ 나눠 담기";
    btn.title = "비커에 한 번에 안 들어갈 때 — 이 창에서 끝까지 나눠 담습니다";
    btn.addEventListener("click", () => requestAddWeigh(idx, { split: true }));
    return btn;
  }

  // ── 저울 PRINT 키 연동: 에이전트 이벤트 폴링 → 활성 행 자동 입력 ──
  let scaleEventLast = 0;
  let scaleEventSynced = false;
  // 차단 모달이 열려 있을 때 저울 PRINT 를 한 번 알리고, 그 뒤론 조용히(매 폴링 스팸 방지).
  let _modalPrintWarned = false;

  // 저울 PRINT 를 소비하면 안 되는 차단 모달이 보이는가. 승인 모달(증량 승인·수기 입력 승인)은
  // 포커스를 승인자 이름칸으로 옮겨 라우팅 플래그를 전부 비우고, 제안·폐기·3회 차단·이월·LOT 확인
  // 모달도 버튼 선택을 기다리는 동안 들어온 PRINT 가 폴백('다음 미계량 품목')으로 흘렀다
  // (현장 실측 2026-08-04 — "포커스를 안 뺏으니 안전"이라던 이전 판단은 틀렸다).
  // 추가 계량(add-weigh) 모달은 제외 — PRINT 를 자기 행 합산으로 소비하는 창이다.
  // 부족 흐름은 이제 scale-state-modal 로 통합됐으므로 그 창이 떠 있을 때 PRINT 는 게이트가 버린다.
  function printBlockingModalVisible() {
    // scale-state-modal: 저울 상태(추가분/누계) 선택 전의 PRINT 는 해석할 방법이 없다.
    return ["rescale-approve-modal", "manual-approve-modal", "rescale-modal",
      "discard-modal", "rescale-block-modal", "carry-over-modal",
      "lot-invalid-modal", "scale-state-modal",
      "discard-ask-modal", "batch-discard-modal"].some((id) => { const m = $(id); return m && !m.hidden; });
  }

  async function pollScaleEvents() {
    // 창 단일화 가드에 막힌 창은 저울 이벤트를 소비하지 않는다 — 오버레이는 화면만
    // 덮으므로, 이 가드가 없으면 막힌 창이 다른 창에서 누른 PRINT 값을 자기 행에
    // 채워 넣는다(가드가 막으려던 바로 그 사고). 해제되면 잔여 이벤트는 버리고 재동기화.
    if (window.IRMS && window.IRMS.blendWindowBlocked) { scaleEventSynced = false; return; }
    // 차단 모달이 열려 있으면 PRINT 를 소비하지 않는다 — 필요한 위치가 확정되기 전의 값은
    // 버리고, 창을 마친 뒤 다시 PRINT 를 받는 게 안전하다(엉뚱한 품목 채움 방지).
    // blendWindowBlocked 가드와 같은 방식(이벤트 버리고 재동기화). 1회만 안내(매 폴링 스팸 금지).
    if (printBlockingModalVisible()) {
      if (!_modalPrintWarned) {
        _modalPrintWarned = true;
        notify("안내 창이 열려 있어 저울 PRINT 를 받지 않습니다 — 창의 버튼으로 먼저 마쳐주세요.", "warn");
      }
      // 모달이 열려 있는 동안에도 이벤트 커서는 전진시켜 stale PRINT 를 그 자리에서 버린다.
      // 종전에는 synced=false 로 두고 닫힌 뒤 첫 폴을 통째로 재동기화로 삼켰는데, 그 폴
      // 주기(≤0.8s) 안에 들어온 '그림 선택 직후의 유효한 PRINT'까지 무음 소실됐다(주행
      // 재현 2026-08-05). 커서만 전진하고 적용은 안 하므로 차단 성질은 그대로다.
      try {
        const res = await fetch(`${SCALE_URL}/events?after=${scaleEventLast}`, {
          signal: AbortSignal.timeout(1500),
        });
        if (res.ok) {
          const data = await res.json();
          scaleEventLast = data.last_id || scaleEventLast;
        }
      } catch (_e) { /* 폴링 실패는 조용히 */ }
      return;
    }
    _modalPrintWarned = false;  // 모달이 닫혔으니 다음 열림 때 다시 안내
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
      ...it, actual_amount: "", material_lot: "", portions: [],
    }));
    state.anchorIndex = findAnchorIndex(state.items);
    state.prevAnchorActual = "";
    // 레시피가 바뀌면 이전 레시피의 증량분을 버린다 — 새 레시피는 새 총량 기준.
    state.rescaleTotalG = 0;
    state.addModeIdx = null;
    state.scaleTargetIdx = null;  // 레시피 변경 → 저울 대상 지정 해제
    state.pendingRescale = null;
    state.addPending = {};
    state.rescaleActive = false;
    state.rescaleAppliedPlan = null;
    state.rescaleEvents = [];  // 레시피 변경 → 증량 승인 이력 초기화(총 배합량 잠금도 함께 해제)
    state.discardEvents = [];  // 레시피 변경 → 폐기 이력도 새 배합 기준으로 초기화
    state.lotOverrides = {};
    state.manualApproved = null;  // 레시피 변경 → 수기 입력 승인 해제(다음 배합은 다시 잠금)
    // 레시피가 바뀌면 '같은 저장의 재시도'가 아니다 — 실패한 저장의 멱등 키를 버린다.
    // (그대로 두면 옛 키로 저장돼, 앞 요청이 사실 커밋돼 있었을 때 새 배합 대신 옛
    //  기록이 되돌아온다.)
    _saveRequestId = null;
    clearRescaleSummary();
    // 레시피가 바뀌면 이전 레시피의 입력을 모두 초기화 — 총량·비고·서명·반응기가
    // 새 레시피에 섞여 들어가는 것을 방지. 총량은 다시 입력(또는 기준 버튼).
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
    updateManualEntryControl();  // 승인 해제 반영(배너 텍스트·버튼 복귀)
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
  // 진행 중 입력을 이 PC 의 localStorage 에 최대 3칸까지 저장하고(서버·다른 작업 무관),
  // 끊긴 작업은 "작성 중 배합"(/blend/drafts) 화면에서만 이어간다(진입 배너 폐지).
  // 저장 완료 시 그 슬롯만 삭제. 24시간 지난 초안은 목록에서 빠진다.
  const DRAFT_KIND = "blend";
  let _draftTimer = null;

  function currentDraft() {
    if (!state.current || !state.current.recipe) return null;
    const hasInput = state.items.some((it) =>
      (it.actual_amount !== "" && it.actual_amount != null) || (it.material_lot || "").trim())
      // 폐기 이력도 의미 있는 입력 — 전량 폐기 직후엔 화면이 비지만, 그 폐기 기록은
      // 초안에 남아야 창을 닫아도 다음 저장에 실린다(실물 소모의 유일한 흔적).
      || (state.discardEvents && state.discardEvents.length > 0);
    if (!hasInput) return null;  // 의미 있는 입력이 없으면 초안 없음
    return {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      schema: blendDrafts ? blendDrafts.SCHEMA : 2,
      // 줄마다 품목 식별자(품목코드 우선, 없으면 품목명). 복구 시 위치가 아니라 이 값으로
      // 매칭한다 — 초안 저장 후 재료 순서가 바뀌거나 중간에 삽입/삭제돼도 계량값이
      // 다른 재료 줄로 흘러들지 않는다.
      materials: blendDrafts ? blendDrafts.materialIdentities(state.items) : [],
      // 기준 배합량·허용 편차·기준 자재 설정 스냅샷 — 복구 전 변경 고지에 쓴다.
      recipeMeta: blendDrafts ? blendDrafts.recipeMetaOf(state.current.recipe) : null,
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
      // 자재 폐기 이력 — 초안에 안 실으면 복구 후 저장에서 폐기 흔적이 사라진다.
      discardEvents: (state.discardEvents || []).map((ev) => ({ ...ev })),
      // 수기 입력 승인/부재 진행 상태 — rescaleEvents 와 같은 이유로 반드시 왕복시킨다.
      // 누락하면 복구 후 저장 시 manual_absence_reason 이 null 이 되어, 승인 없이 손계량한
      // 배치가 '미확인' 표시도 사유도 없이 정상 배치로 기록된다(추적 구멍).
      manualApproved: state.manualApproved ? { ...state.manualApproved } : null,
      lotOverrides: state.lotOverrides || {},
      items: state.items.map((it) => ({
        material_lot: it.material_lot || "",
        actual_amount: (it.actual_amount === "" || it.actual_amount == null) ? "" : String(it.actual_amount),
        carried_over: it.carried_over === true,
        manual: it.manual === true,
        // 나눠 담기/추가 계량 회차 내역 — 안 실으면 복구 후 "현재값=1회차"로 뭉개져
        // 몇 번에 얼마씩 담았는지가 사라진다(2026-08-04 봉인 후속). 빈 배열은 생략.
        portions: (Array.isArray(it.portions) && it.portions.length)
          ? it.portions.map(Number) : undefined,
      })),
      savedAt: new Date().toISOString(),
    };
  }

  // 진행 중인 초안이 차지한 슬롯 id. 같은 레시피로 이어서 작업하는 동안은 이 슬롯을
  // 계속 갱신한다(저장할 때마다 새 칸을 만들면 3칸이 같은 작업으로 순식간에 찬다).
  function persistDraft() {
    // 가드에 막힌 창은 공유 초안을 덮어쓰지 않는다(다른 창의 진행분 오염 방지).
    if (window.IRMS && window.IRMS.blendWindowBlocked) return;
    if (!blendDrafts) return;
    const d = currentDraft();
    if (!d) return;
    const id = blendDrafts.saveSlot(DRAFT_KIND, d, null, state.draftSlotId);
    if (id) state.draftSlotId = id;
  }

  function scheduleDraftSave() {
    if (_draftTimer) clearTimeout(_draftTimer);
    _draftTimer = setTimeout(() => {
      try { persistDraft(); } catch (_e) { /* 저장공간 없음 등 무시 */ }
    }, 600);
  }

  // 저장 완료·버리기 — 지금 작업 중인 슬롯 하나만 지운다(다른 초안 2칸은 보존).
  function clearDraft() {
    if (_draftTimer) { clearTimeout(_draftTimer); _draftTimer = null; }
    try {
      if (blendDrafts && state.draftSlotId) blendDrafts.removeSlot(DRAFT_KIND, state.draftSlotId);
    } catch (_e) { /* 무시 */ }
    state.draftSlotId = null;
  }

  // F12: 본인 초안(이 kind)이 1건 이상이면 페이지 로드당 1회 안내 + 사이드바
  // [작성 중 배합] 링크에 개수 배지. 초안 복구로 진입한 경우는 호출부에서 제외한다(중복 안내 금지).
  // localStorage 직접 파싱 금지 — blendDrafts.listAll(localStorage) 사용.
  function notifyDraftCount(kind) {
    if (!blendDrafts || typeof blendDrafts.listAll !== "function") return;
    let slots = [];
    try { slots = blendDrafts.listAll(localStorage).filter((s) => s.kind === kind); }
    catch (_e) { return; }  // 저장소 접근 불가 등 — 조용히 무동작
    if (!slots.length) return;
    notify(`작성 중 배합 ${slots.length}건이 있습니다 — 사이드바 [작성 중 배합]에서 이어서 작업할 수 있습니다.`, "warn");
    // 사이드바 링크에 개수 배지(이미 있으면 갱신).
    const link = document.querySelector('a[href="/blend/drafts"]');
    if (link) {
      const badgeCls = "nav-draft-count-badge";
      let badge = link.querySelector("." + badgeCls);
      if (!badge) {
        badge = document.createElement("span");
        badge.className = badgeCls;
        link.appendChild(badge);
      }
      badge.textContent = String(slots.length);
    }
  }

  // 초안 즉시 저장(동기 flush) — 유휴 자동 로그아웃 직전 진행분을 잃지 않도록,
  // scheduleDraftSave 의 600ms 디바운스를 기다리지 않고 바로 localStorage 에 쓴다.
  function flushDraftNow() {
    try { persistDraft(); } catch (_e) { /* 저장공간 없음 등 무시 */ }
  }

  // 복구 후 남는 안내 상자(사라진 재료의 계량값·신규 재료·기준 배합량 변경).
  // 토스트는 사라지므로, 값이 걸린 고지는 화면에 남긴다.
  function showDraftNotice(html) {
    const box = $("blend-draft-notice");
    const body = $("blend-draft-notice-body");
    if (!box || !body) return;
    if (!html) { box.hidden = true; body.innerHTML = ""; return; }
    body.innerHTML = html;
    box.hidden = false;
  }

  // "작성 중 배합" 화면에서 [이어서 하기]로 넘어온 슬롯을 복원한다.
  // (배너 폐지 후 유일한 진입 경로 — 복구 실행 로직 자체는 그대로 재사용.)
  async function restoreDraft(slotId) {
    const draft = blendDrafts ? blendDrafts.getSlot(DRAFT_KIND, slotId) : null;
    if (!draft || !draft.recipe_id) {
      notify("이어서 할 임시저장을 찾지 못했습니다(만료되었거나 이미 삭제됨).", "warn");
      return;
    }
    state.draftSlotId = draft.id || null;
    // 레시피 목록이 아직이면 먼저 로드하고, 분류 필터를 전체로 되돌려 그 레시피 option 이
    // 반드시 존재하게 한다(분류로 걸러져 있으면 value 지정이 붙지 않는다).
    if (!state.recipes.length) {
      try { await loadRecipes(); } catch (_e) { /* 아래 onRecipeChange 에서 다시 실패 처리 */ }
    }
    const catSel = $("blend-recipe-cat");
    if (catSel && catSel.value !== "") { catSel.value = ""; populateRecipeSelect(); }
    const recipeSel = $("blend-recipe");
    recipeSel.value = String(draft.recipe_id);
    await onRecipeChange();  // 레시피 로드 + 렌더(빈 상태) — 이후 초안 값을 덮어씌운다.
    // 레시피가 삭제·비활성화돼 option 이 없으면 value 지정이 붙지 않아 state.current 가
    // 비어 있다. 이 상태로 진행하면 아래 정합성 판정이 터지므로 여기서 멈춘다(초안은 보존 —
    // 레시피가 되살아나면 다시 이어서 할 수 있다).
    if (!state.current || !state.current.recipe) {
      state.draftSlotId = null;
      notify("이 임시저장의 레시피를 찾을 수 없습니다 — 레시피가 삭제되었거나 비활성화되었습니다.", "error");
      return;
    }
    if (draft.date) $("blend-date").value = draft.date;
    if (draft.time) $("blend-time").value = draft.time;
    if (draft.scale) $("blend-scale").value = draft.scale;
    if (draft.note) $("blend-note").value = draft.note;
    if (draft.reactor) $("blend-reactor").value = draft.reactor;
    state.lotOverrides = draft.lotOverrides || {};
    // 수기 입력 승인/부재 상태 복구 — onRecipeChange 가 null 로 리셋했으므로 되살린다.
    // 이게 없으면 복구 후 저장이 사유 없는 정상 배치로 기록된다.
    state.manualApproved = draft.manualApproved ? { ...draft.manualApproved } : null;
    state.rescaleTotalG = draft.rescaleTotalG || 0;
    if (state.rescaleTotalG > 0) state.rescaleActive = true;
    // 증량 승인 이력 복구 — onRecipeChange 가 이미 [] 로 리셋했으므로 초안 값으로 되살린다.
    // (얕은 복사로 원본 초안 객체와 분리.) 승인 이력이 있으면 증량 활성으로 간주해
    // 총량 잠금·추가분 배지가 다시 뜨게 한다(일반 레시피는 rescaleTotalG=0 이라 이 신호가 필요).
    state.rescaleEvents = Array.isArray(draft.rescaleEvents)
      ? draft.rescaleEvents.map((ev) => ({ ...ev }))
      : [];
    // 자재 폐기 이력 복구 — 초안 저장 시점까지 기록된 폐기가 저장 payload 에 실리게.
    state.discardEvents = Array.isArray(draft.discardEvents)
      ? draft.discardEvents.map((ev) => ({ ...ev }))
      : [];
    if (state.rescaleEvents.length) state.rescaleActive = true;
    // ── 레시피 변경 정합성 ──────────────────────────────────────
    // 초안의 계량값을 '위치'가 아니라 '품목 식별자'로 현재 레시피 줄에 얹는다. 순서가
    // 바뀌거나 중간에 재료가 삽입/삭제돼도 사람이 저울로 잰 값이 제 자리를 찾는다.
    // 식별자가 없는 옛 초안(schema<2)만 종전처럼 위치 기반으로 복구하고 경고한다.
    const diff = blendDrafts
      ? blendDrafts.buildDiff(DRAFT_KIND, draft, { recipe: state.current.recipe, items: state.items })
      : { legacy: true, align: { map: null } };
    const rowMap = diff.align && diff.align.map;
    (draft.items || []).forEach((di, i) => {
      const target = rowMap ? rowMap[i] : i;
      if (target === undefined || target < 0 || !state.items[target]) return;
      state.items[target].material_lot = di.material_lot || "";
      state.items[target].actual_amount = di.actual_amount === "" ? "" : di.actual_amount;
      state.items[target].carried_over = di.carried_over === true;
      state.items[target].manual = di.manual === true;
      // 회차 내역 복구 — 없는 초안(옛 스키마·회차 없음)은 openAddWeighModal 의
      // "현재값=1회차" 재구성에 맡긴다.
      state.items[target].portions = Array.isArray(di.portions)
        ? di.portions.map(Number) : [];
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
    // 복구된 수기 입력 승인/부재 상태를 화면에 반영(잠금 해제 + 배너 문구).
    applyScaleOnlyToRows();
    updateManualEntryControl();
    notify("작성 중이던 배합을 복원했습니다.", "success");
    // 레시피 변경 고지 — 사라진 재료의 계량값은 조용히 버리지 않고 값까지 적어 남긴다.
    if (blendDrafts) {
      const noticeHtml = blendDrafts.restoreNoticeHtml(diff);
      showDraftNotice(noticeHtml);
      if (diff.legacy) {
        notify("레시피 변경 여부를 확인할 수 없는 오래된 임시저장입니다 — 재료별 값을 확인하세요.", "warn");
      }
      if (diff.dropped && diff.dropped.length) {
        notify(`레시피에서 삭제된 재료의 계량값 ${diff.dropped.length}건은 옮기지 못했습니다 — 화면 상단 안내를 확인하세요.`, "error");
      }
    }
    if (state.rescaleEvents.length) {
      // 증량 이력이 있으면 총량 잠금·추가분 배지를 명시적으로 다시 그리고 1회 안내한다.
      updateTotalLock();
      renderAddBadges();
      // 복구된 증량 상태 상시 표시줄도 다시 그린다(F7) — state.rescaleEvents 에서 읽는다.
      renderRescaleSummary();
      notify(`복구된 배합에 증량 ${state.rescaleEvents.length}회가 포함되어 있습니다.`, "warn");
    } else if (state.rescaleActive) {
      renderAddBadges();
    }
    // 승인 회피 방지(사용자 요구 2026-07-22): 승인 모달이 떠 있는 상태에서 새로고침/창
    // 재실행으로 빠져나가도, 복구 직후 미해소 초과(+허용편차 초과) 행이 있으면 즉시
    // 증량 제안→승인 게이트를 다시 띄운다. 새로고침이 승인 우회 수단이 될 수 없다.
    const tol = state.toleranceG;
    const overIdx = state.items.findIndex((it, i) =>
      i !== state.anchorIndex && it.actual_amount !== ""
      && !(state.addPending && state.addPending[i] != null)
      && varianceVerdict(Number(it.actual_amount), it.theory_amount, tol).over);
    if (overIdx >= 0) {
      notify("복구된 배합에 미해소 초과 계량이 있습니다 — 증량 승인 또는 다시 계량이 필요합니다.", "error");
      warnIfVariance(overIdx);
    }
  }

  // 기준 자재 모드 적용 — 레시피에 기준 자재가 있으면:
  //   - 총 배합량 입력 읽기 전용(기준 자재 실측값에서 도출되므로 직접 입력 금지)
  //   - 기준 빠른 채우기 버튼 미노출(총량 기반 방식이 아님)
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

  // '기준' 버튼(최대 3개) — 레시피 관리에서 기준 배합량을 지정한 레시피에서만 노출.
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

  // 계량값 표시 소수 자릿수 — 현재 레시피 허용 편차(state.toleranceG)를 따른다(표시 전용).
  // 계산·검증·저장은 그대로 2자리 이론 기준을 유지한다.
  function dp() { return toleranceDecimals(state.toleranceG); }

  // 이론량 셀의 내용(숫자 + 투입 로스 보정 배지)을 채운다. textContent 가 배지를 지우지
  // 않도록 innerHTML 로 재구성한다(2라운드 2026-08-05). materialRowHtml 의 배지 마크업과 동일.
  function setTheoryCellContent(cell, it) {
    const comp = Number(it && it.loss_comp_g);
    const badge = comp > 0
      ? ` <span class="blend-losscomp-badge" title="투입 로스 보정 ${fmt(comp, 2)}g 포함 — 붓는 로스만큼 더 계량하는 공정 기준입니다">보정 +${fmt(comp, 2)}g</span>`
      : "";
    cell.innerHTML = fmt(it.theory_amount, dp()) + badge;
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
        : computeTheoryAmount(it.ratio, total, it.loss_comp_g);
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
      // 실제량 칸에 ⚖ 저울 대상 지정 버튼을 붙인다(저울 연결 시에만 노출 — CSS).
      // 입력칸+버튼을 <span class="blend-actual-flex"> 로 감싸 td(table-cell) 안에서 flex 로
      // 폭을 나눠 갖는다 — td 자체를 display:flex 로 쓰면 행 경계선(border-bottom)이
      // 이웃 셀과 미세하게 어긋난다(1366px, F4). span 은 td 의 자식이므로 기존 조회
      // (.blend-actual[data-idx] 등)는 그대로 동작한다.
      const actualCell = tr.querySelector(".blend-actual");
      if (actualCell && actualCell.parentElement) {
        const wrap = document.createElement("span");
        wrap.className = "blend-actual-flex";
        actualCell.parentElement.appendChild(wrap);
        wrap.appendChild(actualCell);
        wrap.appendChild(buildScaleTargetButton(idx));
        wrap.appendChild(buildSplitButton(idx));
      }
      body.appendChild(tr);
    });
    body.insertAdjacentHTML("beforeend", stepRowsHtml(steps, state.items.length));  // 마지막 자재 뒤 설명
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("input", () => {
        const i = Number(el.dataset.idx);
        state.items[i].actual_amount = el.value;
        // 손입력을 '수동 입력'으로 기록하는 조건 — ①저울 연결 중이거나 ②저울 전용 모드.
        // ②를 빼면, 저울이 꺼진 채 책임자 승인/부재로 손계량한 배치가 manual_entry=false 로
        // 저장돼 "통제는 승인 + 수동 입력 표시로 이뤄진다"는 설계의 표시가 실제로는 안 남는다
        // (저울이 없을 때가 바로 승인이 필요한 상황이므로 정확히 그때만 누락됐다).
        if (state.scaleReady || state.scaleOnlyInput) {
          if (!state.items[i].manual && state.scaleReady) {
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
      // 그 뒤 ERP 원재료 LOT 검사(제안 없는 자재만) — 경고는 저장을 막지 않는다.
      el.addEventListener("change", () => { validateLotInput(el); checkErpLot(el); });
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
    // 저울 대상 행 표시 갱신(행 재렌더 후).
    updateScaleTargetIndicator();
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
    if (!box.hidden) positionLotSuggest(input, box);
  }

  // 제안 목록은 기본으로 LOT 칸 바로 아래(top:100%)에 뜬다. 그런데 표 컨테이너
  // (.table-wrap)는 가로 스크롤용 overflow-x:auto 를 갖고, CSS 규칙상 세로도 함께
  // 잘린다(overflow-y 가 visible→auto 로 승격). 그래서 맨 아래 행의 자재(예: 2단
  // 제조 2차의 마지막 1차 반제품 행)는 아래로 열리는 제안이 컨테이너 밑단에서 잘려
  // 안 보였다. 아래 공간이 부족하면 위로 열어(bottom:100%) 표 안쪽에 보이게 한다.
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

  // ── 앞 단계 기록에 없는 반제품 LOT 확인(차단 아님) ──────────────
  // 제안(state.lotSuggest)이 있는 자재 = 완료 배합 기록이 있는 반제품. 이 자재의 LOT 칸이
  // 그 반제품의 실제 product_lot 이 아니면 #lot-invalid-modal 로 **확인만** 받는다
  // (2026-08-04 차단 해제 — 반제품을 만들고 곧바로 2차에 투입하는 정당한 경우에도 매번
  //  걸려서 작업자가 사유란에 아무 글자나 치고 넘어갔다. 통제가 형해화되고 비용은 현장이
  //  다 치렀다). 일반 자재(제안 없음)는 100% 기존 동작 유지.
  //
  // 판정 우선순위: 빈 값(공백 trim) → 통과 / 제안 목록에 있는 값 → 통과 /
  // 그 외 → 서버 /blend/product-lot-exists 로 확인(공용 lotCheckCache — '없음'만 TTL 만료).
  // 네트워크 오류는 통과(loadLotSuggest 와 동일한 fail-open 철학 — 현장 입력을 막지 않는다).
  async function checkLotRegistered(name, lot) {
    if (!lot) return true;
    const lots = (state.lotSuggest && state.lotSuggest[name]) || [];
    // 제안 항목이 이제 {lot, total} 객체이므로 .lot 값으로 비교한다(즉시 통과 판정).
    if (lots.some((e) => String(e && e.lot) === lot)) return true;
    const key = lotCheckCache.key(name, lot);
    const cached = lotCheckCache.get(key);
    // undefined = 미지 또는 '없음' 캐시 만료 → 서버에 다시 물어본다(B-1).
    if (cached !== undefined) return cached;
    try {
      const data = await request("/blend/product-lot-exists", { query: { name, lot } });
      const ok = Boolean(data && data.exists);
      lotCheckCache.set(key, ok);
      return ok;
    } catch (_e) {
      // 조회 실패 — 통과(기존 동작 유지). loadLotSuggest 의 fail-open 철학과 동일.
      return true;
    }
  }

  // .blend-lot 입력칸 하나 검증 — 앞 단계 기록에 없으면 확인 창 + 주황 테두리(잔존 표시).
  // 값은 지우지 않는다(ERP 경고와 같은 취급). 잔존 표시를 반제품 쪽에도 붙이는 이유:
  // 차단이 없어졌으므로 모달을 닫은 뒤에도 "이 칸은 확인된 예외"라는 흔적이 더 필요하다.
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
    if (!lot) { setErpLotWarn(input, false); return; }
    // 이미 확인 창을 거친 조합 — 다시 띄우지 않되 잔존 표시는 유지한다.
    if (lotOverrideKey(name, lot) in state.lotOverrides) {
      setErpLotWarn(input, true, "앞 단계 배합 기록에 없는 LOT — 확인하고 진행함");
      return;
    }
    if (await checkLotRegistered(name, lot)) { setErpLotWarn(input, false); return; }  // 등록됨
    setErpLotWarn(input, true, "앞 단계 배합 기록에 없는 LOT 입니다.");
    openLotInvalidModal(name, lot, input);
  }

  function lotOverrideKey(name, lot) { return `${name}\u0000${lot}`; }

  // 저장 시 비고에 남길 '확인하고 진행' 표시 — 실제로 저장에 포함된 조합만.
  // 사유는 선택이므로 비어 있을 수 있다 — 그래도 진행한 사실은 비고에 남긴다.
  function buildOverrideNote() {
    const parts = [];
    state.items.forEach((it) => {
      const name = (it.material_name || "").trim();
      const lot = (it.material_lot || "").trim();
      if (!lot) return;
      const key = lotOverrideKey(name, lot);
      if (key in state.lotOverrides) {
        const reason = String(state.lotOverrides[key] || "").trim();
        parts.push(`[앞 단계 기록에 없는 LOT 진행] ${name}/${lot}${reason ? ": " + reason : " (사유 미기재)"}`);
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
      // 사유가 비어도 버리지 않는다 — 사유가 선택이 된 순간 사유를 필터로 쓰면
      // 대사할 신호가 통째로 사라진다. 키의 존재 자체가 '확인하고 진행함' 이다.
      if (!material_name || !material_lot) return;
      out.push({ material_name, material_lot, reason, acknowledged: true });
    });
    return out;
  }

  // 확인 창을 열면서 **이 자재의 기록된 LOT 후보 목록**을 함께 넘긴다. 목록은 이미
  // 브라우저에 로드돼 있는데(state.lotSuggest) 창은 그걸 안 보여줘서, 작업자가 창을 닫고
  // 칸을 다시 눌러야 볼 수 있었다. 창 안에서 고르면 오타가 이 창 안에서 끝난다.
  function openLotInvalidModal(name, lot, input) {
    const idx = input ? Number(input.dataset.idx) : -1;
    lotModal.openInvalid({
      name, lot, input,
      lots: (state.lotSuggest && state.lotSuggest[name]) || [],
      onPick: (picked) => {
        if (!input) return;
        input.value = picked;
        if (state.items[idx]) state.items[idx].material_lot = picked;
        setErpLotWarn(input, false);        // 등록된 LOT 로 바뀌었으니 잔존 표시 해제
        input.dispatchEvent(new Event("input"));   // state 반영 경로 재사용
        input.focus();
      },
    });
  }

  function closeLotInvalidModal() { lotModal.close(); }

  // ── ERP 원재료 LOT 검사(반제품 자재는 제외) ─────────────────────
  // 반제품(제안이 있는 자재)은 위 validateLotInput 가 다루므로 건드리지 않는다.
  // 일반 원료(제안 없음 + material_code 가 있는 자재)의 LOT 입력 확정(change) 시
  // GET /api/material-lots/check 로 유효성을 본다. valid=False 면 경고 모달을 띄우되
  // 저장을 막지 않는다(값은 지우지 않음). fetch 실패·file_ok=False 는 통과(fail-open).
  function setErpLotWarn(input, on, reason) {
    if (!input) return;
    if (on) {
      input.classList.add("erp-lot-warn");
      input.title = reason || "등록되지 않은 LOT 입니다.";
    } else {
      input.classList.remove("erp-lot-warn");
      input.title = "";
    }
  }

  async function checkErpLot(input) {
    if (!input) return;
    const idx = Number(input.dataset.idx);
    const item = state.items[idx];
    if (!item) return;
    const code = (item.material_code || "").trim();
    if (!code) return;  // 품목코드 없는 자재는 ERP 검사 불가
    const name = (item.material_name || "").trim();
    // 반제품(제안 대상)은 validateLotInput 이 다룬다 — 여기서 제외.
    if (state.lotSuggest && state.lotSuggest[name]) return;
    const lot = (input.value || "").trim();
    if (!lot) { setErpLotWarn(input, false); return; }  // 빈 값이면 경고 해제
    let data;
    try {
      data = await request("/material-lots/check", { query: { code, lot } });
    } catch (_e) {
      // 조회 실패 — 통과(checkLotRegistered 의 fail-open 철학과 동일). 경고도 두지 않는다.
      setErpLotWarn(input, false);
      return;
    }
    if (!data || data.file_ok === false) {
      // 엑셀 파일 문제 — 현장을 막지 않는다(fail-open).
      setErpLotWarn(input, false);
      return;
    }
    if (data.valid) {
      setErpLotWarn(input, false);
      return;
    }
    // 미통과 — 경고 표시(값은 지우지 않음). 모달로 알림.
    // 음수 재고는 소진과 다르다 — ERP 전표 지연/누락 신호(실물은 돌고 있을 확률이
    // 높다). 실제 값을 보여줘야 "재고 0"이라는 거짓 안내가 되지 않는다.
    const stock = Number(data.stock);
    // 경고 이유는 3가지다(목록에 없음 / 재고 0 / 재고 마이너스). 제목도 이유별로 달라야
    // 한다 — 특히 '전산 반영 지연'(마이너스)인데 제목이 "등록되지 않은 LOT" 이면 제목과
    // 본문이 서로 모순된다. reasonKind 를 모달에 넘겨 제목을 고르게 한다.
    const reasonKind = data.source === "erp" ? (stock < 0 ? "negative" : "zero") : "missing";
    const reason =
      data.source === "erp"
        ? (stock < 0
            ? `ERP 재고가 마이너스인 LOT 입니다(재고 ${stock}). 전산 반영 지연일 수 있으니 실물을 확인하세요.`
            : "재고가 소진된 LOT 입니다(재고 0).")
        : "ERP 원재료 목록에 없는 LOT 입니다.";
    setErpLotWarn(input, true, reason);
    openErpLotModal(name, code, lot, reason, reasonKind, input);
  }

  // ERP LOT 경고 모달 — [확인했습니다 · 계속](값 유지) / [책임자 LOT 추가하기](즉석 인증).
  // 본체·인증 POST 는 공용 컴포넌트(lotModal)가 담당. 인증 성공 시 입력의 .erp-lot-warn
  // 주황 테두리를 해제한다(onVerified) — 경고는 모달이 닫혀도 남지만, 수동 LOT 추가로
  // 해소되면 더 이상 강조하지 않는다.
  function openErpLotModal(name, code, lot, reason, reasonKind, input) {
    lotModal.openErp({
      name, code, lot, reason, reasonKind, input,
      onVerified: () => setErpLotWarn(input, false),
    });
  }

  function closeErpLotModal() { lotModal.close(); }

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
      setTheoryCellContent(cell, state.items[i]);
    });
    document.querySelectorAll("#blend-mat-body .blend-actual").forEach((act) => {
      const i = Number(act.dataset.idx);
      const it = state.items[i];
      if (it) act.placeholder = it.theory_amount == null ? "" : fmt(it.theory_amount, dp());
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
    const tol = state.toleranceG;
    const verdict = varianceVerdict(Number(it.actual_amount), it.theory_amount, tol);
    const v = verdict.variance;  // raw 편차 — 판정은 verdict 로, 표시/부족량은 fmt/그대로
    if (!verdict.within) {
      // 나눠 담는 중인 행은 '아직 덜 넣었다'가 정상 상태다 — 계획된 분할을 매 회차
      // 오류로 알리면 8kg 씩 3번 담을 때 경고가 2번 뜬다(실측). 부족 방향일 때만
      // 침묵하고, 초과는 그대로 알린다(그건 분할 중에도 실제 문제다).
      if (_addWeighIdx === i && v < 0) return true;
      const key = `${i}:${it.actual_amount}`;
      const now = Date.now();
      if (_lastVarWarn.key === key && now - _lastVarWarn.at < 1500) return true;
      _lastVarWarn = { key, at: now };
      notify(varianceWarnMessage(it, v, tol), "error");
      if (v > 0) {
        // +방향(초과 계량): 증량 제안 모달.
        // 나눠 담는 중에 넘겨 담았다면 그 창을 먼저 닫는다 — 증량/폐기 모달이 그 위에
        // 겹쳐 뜨면 어느 걸 조작해야 하는지 헷갈린다(실측). 담긴 값은 유지한 채 넘긴다.
        if (_addWeighIdx === i) closeAddWeighModal(i, /*keepValue*/ true);
        offerRescale();
      } else if (state.addModeIdx !== i) {
        // −방향(부족): 저울 상태 선택 모달에 부족 안내를 실어 바로 띄운다(부족 창 통합).
        // 실수로 저울 영점을 눌러 값이 부족하게 찍힌 경우, 처음부터 재계량이 아니라
        // 추가로 올리는 무게를 합산해 목표를 맞추면 된다 — 그림 선택(추가 입력) 또는
        // [처음부터 다시 계량]. 이미 합산 입력 중(addModeIdx)이면 모달 생략.
        const shortage = Math.abs(v);
        openScaleStateModal({
          idx: i,
          options: null,
          shortage: { theory: Number(it.theory_amount) || 0, actual: Number(it.actual_amount) || 0, missing: shortage },
        });
      }
      return true;
    }
    return false;
  }

  // 허용 편차를 +방향으로 벗어난 행의 실제량을 모두 비운다 — 증량 제안/승인을
  // 거절(다시 계량)했을 때 초과 상태가 화면에 남아, 다음 자재로 넘어가며 누적되고
  // 마지막 승인 한 번에 뭉뚱그려 재계산되던 사고 방지(현장 신고 2026-07-22).
  function clearOverActuals() {
    const tol = state.toleranceG;
    let first = null;
    state.items.forEach((it, i) => {
      if (i === state.anchorIndex || it.actual_amount === "") return;
      if (varianceVerdict(Number(it.actual_amount), it.theory_amount, tol).over) {
        it.actual_amount = "";
        const input = document.querySelector(`.blend-actual[data-idx="${i}"]`);
        if (input) input.value = "";
        updateRowVar(i);
        if (first == null) first = i;
      }
    });
    updateTotals();
    if (first != null) {
      const input = document.querySelector(`.blend-actual[data-idx="${first}"]`);
      if (input) { input.focus(); if (input.select) input.select(); }
      notify("초과 계량 값을 비웠습니다 — 다시 계량하세요.", "warn");
    }
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
      + `<span class="old">${fmt(effectiveCurrentTotal(), dp())} g</span>`
      + `<span>→</span>`
      + `<span class="new">${fmt(plan.newTotal, dp())} g</span>`
      + `</div>`;
    // 비율 막대 그림 — "왜 다른 자재도 더 넣는지"를 표보다 먼저 그림으로(P-4).
    html += rescaleBarsHtml(state.items, plan, dp());
    if (overRows.length) {
      html += `<table class="rescale-add-table"><thead><tr><th>자재</th>`
        + `<th class="num">현재 실제량</th><th class="num">새 이론량</th>`
        + `<th class="num">추가로 넣을 양</th></tr></thead><tbody>`;
      overRows.forEach((r) => {
        const it = state.items[r.idx];
        html += `<tr><td>${esc(it.material_name)}</td>`
          + `<td class="num">${fmt(it.actual_amount, dp())}</td>`
          + `<td class="num">${fmt(r.newTheory, dp())}</td>`
          + `<td class="num add-cell">+${fmt(r.addNeeded, dp())}</td></tr>`;
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
        + `(예상 ${fmt(plan.newTotal, dp())} g). 폐기를 권장합니다.</p>`;
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
    if (reasonEl) { reasonEl.value = ""; syncReasonTags(reasonEl); }
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
    // 증량을 몰아온 자재(이론/실제/초과량) — 미확인 증량 알림에서 '어디를 증량했는지' 보여준다.
    if (Array.isArray(plan.drivers) && plan.drivers.length) ev.drivers = plan.drivers;
    state.rescaleEvents.push(ev);
    // applyRescale() 시점엔 이 이벤트가 아직 없어 요약줄이 비어 숨는다 — push 후 재렌더.
    renderRescaleSummary();
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
    renderRescaleSummary(plan);
    notify(`배합량을 ${fmt(plan.newTotal, dp())} g 으로 증량했습니다 — 추가분을 계량하세요.`, "warn");
  }

  // 증량 적용 상시 표시줄 — 증량 N회 적용 사실과 직전 before→after·승인자/부재 사유를
  // 자재 표 위에 한 줄로 남긴다(F7). 총량 잠금만으로는 '지금 증량된 상태인지'가 안 보였다.
  // plan 인자는 호출부 보존을 위해 유지하되, 실제 내용은 state.rescaleEvents(마지막 요소)에서
  // 읽는다 — finalizeRescale 이 채우는 실제 필드(before_total/after_total/approver/absence_reason).
  function renderRescaleSummary(plan) {
    void plan;  // 호환 — 실제 소스는 state.rescaleEvents
    const el = $("rescale-applied-summary");
    if (!el) return;  // 요소가 없으면 조용히 무동작
    const events = state.rescaleEvents || [];
    if (!events.length) { el.hidden = true; el.innerHTML = ""; return; }
    const last = events[events.length - 1];
    // 승인 정보 — approver(정식 승인) 또는 absence_reason(책임자 부재). finalizeRescale 이
    // 둘 중 하나만 채운다. 어느 쪽도 없으면(구경로) 승인 정보 없이 회수만 표시.
    const approval = last.approver
      ? ` · 승인: ${esc(String(last.approver))}`
      : last.absence_reason
        ? ` · 책임자 부재(${esc(String(last.absence_reason))})`
        : "";
    el.innerHTML =
      `증량 ${events.length}회 적용 — `
      + `(${fmt(last.before_total)} → ${fmt(last.after_total)} g)`
      + approval;
    el.hidden = false;
  }
  function clearRescaleSummary() {
    const el = $("rescale-applied-summary");
    if (el) { el.hidden = true; el.innerHTML = ""; }
    state.rescaleAppliedPlan = null;
  }

  // '방금 증량 취소'는 2026-08-05 제거 — 승인제(2026-07-22) 이전의 '실수 클릭 보험'인데,
  // 이제 증량 적용은 책임자 인증을 거치므로 실수 적용이 사실상 불가능하고, 되돌리기는
  // 소비된 승인 토큰만 남기고 기록의 증량 이벤트를 지워 '승인은 있는데 증량 기록이 없는'
  // 유령 승인을 만들었다(사용자 결정). 드문 오승인 복구 = 레시피 재선택 또는 기록 수정.

  // 기준 자재 레시피 증량 반영 — rescalePlan 의 newTheory/addNeeded 를 각 행에 적용.
  // 기준 자재 행도 이론량이 newTheory 로 갱신되고 addNeeded 배지가 표시된다
  // (기준 자재도 추가로 넣어야 총량이 실제로 커진다).
  function recomputeAnchorRescale(plan) {
    const totalInput = $("blend-total");
    if (totalInput) totalInput.value = String(plan.newTotal);
    // 기준 자재 모드의 증량 목표를 rescalePlan 의 newTheory(4자리 ratio 기반)로 두면
    // 서버(원값 value_weight 비례)와 어긋난다 — 실측 0.84 g, 허용 편차의 16배.
    // 기준 행의 newTheory 하나만 취하고 나머지는 computeAnchorTheory 로 다시 풀어
    // 서버 산술과 정확히 일치시킨다(newTotal 결정은 지금의 ratio 기반 그대로 무방).
    const anchorRow = state.anchorIndex >= 0
      ? plan.rows.find((r) => r.idx === state.anchorIndex)
      : null;
    const anchorTheory = anchorRow && anchorRow.newTheory !== null ? anchorRow.newTheory : null;
    const derived = anchorTheory !== null
      ? computeAnchorTheory(state.items, state.anchorIndex, anchorTheory)
      : null;
    plan.rows.forEach((r) => {
      const it = state.items[r.idx];
      if (!it) return;
      const next = (derived && derived.theoryAmounts[r.idx] != null)
        ? derived.theoryAmounts[r.idx]
        : r.newTheory;
      if (next === null || next === undefined) return;
      it.theory_amount = next;
      const cell = document.querySelector(`.blend-theory[data-idx="${r.idx}"]`);
      if (cell) setTheoryCellContent(cell, it);
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
    // 기준 자재(anchor) 모드에서는 배지 목표를 rescalePlan 의 ratio 기반 newTheory 로
    // 쓰면 안 된다 — 이론량 셀은 recomputeAnchorRescale 이 value_weight 원값 비례로
    // 채우므로 두 값이 갈린다. anchor 비율이 작을수록 ratio_i/ratio_anchor 배로 증폭돼,
    // 배지대로 채우면 오히려 편차를 벗어나고 증량을 유발한 행은 배지도 없이 초과로
    // 남아 저장이 막혔다(2026-08-04 회귀 검토 적발). 여기서는 화면이 실제로 쓰는
    // 목표(it.theory_amount)를 기준으로 잔여를 다시 계산해 배지와 셀을 일치시킨다.
    const anchorMode = state.anchorIndex >= 0;
    const rows = anchorMode
      ? plan.rows.map((r) => {
          const it = state.items[r.idx];
          const theory = it && it.theory_amount != null ? Number(it.theory_amount) : null;
          if (theory === null) return { ...r, newTheory: null, addNeeded: null };
          const actualRaw = it.actual_amount;
          const actual = (actualRaw === "" || actualRaw == null) ? null : Number(actualRaw);
          if (actual === null || !Number.isFinite(actual)) {
            return { ...r, newTheory: theory, addNeeded: null };
          }
          return {
            ...r,
            newTheory: theory,
            addNeeded: Math.max(0, Math.round((theory - actual) * 100) / 100),
          };
        })
      : plan.rows;
    // 직전 대기 집합을 기억 — 이번에 빠진(충족된) 행은 편차 표시를 복원해야 한다.
    const prevPending = state.addPending || {};
    // 넣어야 할 양이 있는 행 집합을 새로 만든다(편차 셀 음수 숨김 판정용).
    state.addPending = {};
    rows.forEach((r) => {
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
        ? `목표 ${fmt(r.newTheory, dp())} · 추가 +${fmt(r.addNeeded, dp())} g`
        : `추가 +${fmt(r.addNeeded, dp())} g`;
      badge.title = "클릭해서 추가분을 입력하세요 (저울 PRINT 도 추가분으로 합산됩니다)";
      badge.addEventListener("click", () => requestAddWeigh(r.idx));
      td.appendChild(badge);
    });
    // 이전에 대기였다가 이번에 충족된 행 — 빈칸으로 남지 않게 편차 표시를 다시 그린다.
    Object.keys(prevPending).forEach((k) => {
      const i = Number(k);
      if (!(i in state.addPending)) updateRowVar(i);
    });
    updateScaleTargetIndicator();  // 증량 대기 배지 변화 → 대상 표시 갱신
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
    // 회차 기록 — 작업자가 "몇 번에 걸쳐 얼마씩 담았는지" 화면에서 확인할 수 있게.
    if (Array.isArray(it.portions)) it.portions.push(Math.round(add * 100) / 100);
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
    // 추가 계량 모달이 이 행에 열려 있으면 큰 숫자(남은 양) 갱신 + 자동 완료 판정.
    refreshAddWeighModal(idx);
    // 프로그램 경로는 input 이벤트가 없어 초안 저장이 안 걸린다 — 담기/PRINT 합산 회차가
    // 초안에 빠져, 창 닫힘 복구 시 나눠 담은 값·회차가 통째로 사라졌다(2026-08-04).
    scheduleDraftSave();
  }

  // ── 추가 계량 모달(add-weigh) ───────────────────────────────
  // 인라인 추가 입력(openAddInline) 대신 큰 남은 양 숫자를 보며 합산. 저울 PRINT 는
  // addModeIdx 경로(fillScaleValue→applyAddAmount)로 자동 합산 — 모달이 열려 있으면
  // applyAddAmount 끝의 refreshAddWeighModal 이 숫자를 갱신한다.
  // _addWeighIdx 는 모달이 열려 있는 대상 행(addModeIdx 와 별개 — applyAddAmount 가
  // addModeIdx 를 null 로 되돌려도 모달 갱신 판정은 _addWeighIdx 로 한다).
  let _addWeighIdx = null;

  // ── 저울 상태 선택(scale-state) ─────────────────────────────
  // 나눠 담기/추가 계량 진입 전에 저울 상태를 그림으로 고른다(2026-08-04 시안 확정).
  //   "tared"  — 영점 잡힘: PRINT/입력 = 이번 추가분(현행 합산)
  //   "loaded" — 무게 남음: PRINT/입력 = 지금까지 담은 누계 → 추가분 = 값 − 현재
  // 구분 없이 합산만 하면 영점 안 잡힌 저울의 PRINT(누계)가 이중 계산된다.
  // _awMode 는 모달이 열려 있는 동안만 산다 — 초안에 저장하지 않는다(복구 시점의
  // 물리 상태는 이미 다를 수 있어 다시 물어야 안전). 선택 모달이 떠 있는 동안의
  // PRINT 는 폴러 게이트(printBlockingModalVisible)가 버린다.
  let _awMode = null;            // 추가 계량 모달의 값 해석 모드
  let _scaleStatePending = null; // 선택 후 열 대상 {idx, options} — null 이면 '변경' 재선택
  // 부족 감지로 이 모달이 열렸을 때의 대상 행 인덱스. null 이 아니면 부족 컨텍스트 —
  // [처음부터 다시 계량] 버튼이 보이고 Esc/바깥 클릭으로 닫히지 않는다(부족 창 통합).
  let _scaleStateShortageIdx = null;

  const AW_MODE_LABEL = { tared: "영점이 잡혀 있음", loaded: "무게가 남아 있음" };

  // 저울 상태 선택 모달을 연다. pending 에는 선택적 shortage 컨텍스트 {theory, actual, missing} 가
  // 붙을 수 있다 — 부족 감지로 열린 경우로, 이때 부족 안내줄을 띄우고 [처음부터 다시 계량] 버튼을
  // 노출하며 [취소]는 숨긴다(나가는 글은 그림 둘/다시 계량 뿐). shortage 가 없으면(나눠 담기·변경)
  // 부족줄·다시 계량 버튼을 숨기고 [취소]를 보여준다.
  function openScaleStateModal(pending) {
    const modal = $("scale-state-modal");
    if (!modal) {  // 옛 템플릿 폴백 — 선택 없이 현행(추가분 합산)으로 진행
      if (pending) openAddWeighModal(pending.idx, pending.options, "tared");
      return;
    }
    _scaleStatePending = pending || null;
    const it = state.items[pending ? pending.idx : _addWeighIdx];
    const matEl = $("scale-state-material");
    if (matEl) matEl.textContent = it ? it.material_name : "";
    const shortage = pending && pending.shortage ? pending.shortage : null;
    _scaleStateShortageIdx = shortage && pending && pending.idx != null ? pending.idx : null;
    const shortageEl = $("scale-state-shortage");
    const reweighBtn = $("scale-state-reweigh");
    const cancelBtn = $("scale-state-cancel");
    if (shortage) {
      if (shortageEl) {
        shortageEl.textContent =
          `이론 ${fmt(shortage.theory, dp())} g / 실제 ${fmt(shortage.actual, dp())} g — ${fmt(shortage.missing, dp())} g 부족`;
        shortageEl.hidden = false;
      }
      if (reweighBtn) reweighBtn.hidden = false;
      if (cancelBtn) cancelBtn.hidden = true;
    } else {
      if (shortageEl) { shortageEl.textContent = ""; shortageEl.hidden = true; }
      if (reweighBtn) reweighBtn.hidden = true;
      if (cancelBtn) cancelBtn.hidden = false;
    }
    modal.hidden = false;
    const first = $("scale-state-tared");
    if (first) first.focus();  // 오버레이 뒤 입력 방지 — 봉인 모달 공통 규약
  }

  function closeScaleStateModal() {
    const modal = $("scale-state-modal");
    if (modal) modal.hidden = true;
    _scaleStatePending = null;
    _scaleStateShortageIdx = null;
  }

  function chooseScaleState(mode) {
    const pending = _scaleStatePending;
    closeScaleStateModal();
    if (pending) {
      openAddWeighModal(pending.idx, pending.options, mode);
      return;
    }
    // '변경' 재선택 — 열려 있는 추가 계량 모달의 해석 모드만 전환.
    if (_addWeighIdx != null) {
      _awMode = mode;
      applyAwModeTexts();
      refreshAddWeighModal(_addWeighIdx);
    }
  }

  // 나눠 담기/추가 계량의 유일한 진입문 — 항상 상태 선택을 거친다.
  function requestAddWeigh(idx, options) {
    // 총 배합량 미입력(이론량 없음) 상태에선 목표가 0이라 그림 선택→담기 창이 열리자마자
    // 자동 완료되는 무의미 흐름이 된다(F2). 목표가 있어야만 진입한다.
    if (!state.items[idx] || !(Number(state.items[idx].theory_amount) > 0)) {
      notify('총 배합량을 먼저 입력하세요 — 목표가 있어야 나눠 담기·추가 계량을 시작할 수 있습니다.', 'warn');
      const totalInput = document.getElementById('blend-total');
      if (totalInput) totalInput.focus();
      return;
    }
    openScaleStateModal({ idx, options: options || null });
  }

  // ── 자재 폐기 질문(discard-ask) ─────────────────────────────
  // '처음부터 다시' 재계량 때 담긴 값이 있으면, 그 자재가 숫자 오타였는지(자재 그대로)
  // 실제로 버려지는지(폐기 기록) 먼저 묻는다. 실물 사건과 입력 정정을 버튼 하나로
  // 뭉개지 않기 위한 분기 — 폐기는 저장 시 discard_events 로 기록에 남는다(2026-08-05).
  // ctx = {rows: [{idx, amount}], onProceed} — amount 는 '폐기'를 고르면 기록될 양.
  // 부족 리셋은 담은 양 전체, 초과 비우기는 초과분(덜어낼 양)이다. 질문은 항상 부모
  // 모달을 닫기 전에 그 위에 뜬다 — [돌아가기]가 게이트된 원래 상태로 복귀해, 미해소
  // 초과가 창 없이 방치되는 사고 벡터(2026-07-22 계열)를 만들지 않는다.
  let _discardAskCtx = null;

  function currentActualOf(idx) {
    const it = state.items[idx];
    if (!it || it.actual_amount === "" || it.actual_amount == null) return 0;
    return Number(it.actual_amount) || 0;
  }

  // 재계량 리셋 실행 — 값·회차를 비우고 그 칸에 포커스(열려 있던 창은 모두 닫는다).
  function performResetWeigh(idx) {
    closeDiscardAskModal();
    closeScaleStateModal();
    closeAddWeighModal(idx, /*keepValue*/ false);
  }

  function openDiscardAsk(rows, text, onProceed) {
    const modal = $("discard-ask-modal");
    if (!modal || !rows.length) { onProceed(); return; }  // 옛 템플릿 폴백 — 질문 없이 진행
    _discardAskCtx = { rows, onProceed };
    const matEl = $("discard-ask-material");
    if (matEl) {
      matEl.textContent = rows
        .map((r) => (state.items[r.idx] ? state.items[r.idx].material_name : ""))
        .filter(Boolean)
        .join(" · ");
    }
    const textEl = $("discard-ask-text");
    if (textEl) textEl.textContent = text;
    modal.hidden = false;
    const back = $("discard-ask-back");
    if (back) back.focus();  // 오버레이 뒤 입력 방지 + 실수 Enter 가 파괴적 선택이 안 되게
  }

  function closeDiscardAskModal() {
    const modal = $("discard-ask-modal");
    if (modal) modal.hidden = true;
    _discardAskCtx = null;
  }

  // ── 배치 폐기 기록(batch-discard) ────────────────────────────
  // 과중량 폐기 권장·3회 증량 차단 뒤 책임자와 협의해 배치 전체를 버리기로 한 경우.
  // 저장 없이 화면을 떠나면 실물 소모 최대의 폐기가 무기록이었다(2026-08-05 결정).
  // 제품 LOT 없이 별도 스트림(blend_batch_discards)으로 남는다 — 기록·집계 불변.
  let _batchDiscardSource = null;  // 'overweight' | 'rescale_limit'

  function weighedRowsForDiscard() {
    return state.items
      .map((it, idx) => ({ it, idx }))
      .filter(({ it }) => it.actual_amount !== "" && Number(it.actual_amount) > 0);
  }

  function openBatchDiscardModal(source) {
    const modal = $("batch-discard-modal");
    if (!modal || !state.current || !state.current.recipe) return;
    const rows = weighedRowsForDiscard();
    if (!rows.length) {
      notify("계량된 자재가 없어 폐기로 기록할 내용이 없습니다.", "warn");
      return;
    }
    _batchDiscardSource = source;
    const prodEl = $("batch-discard-product");
    if (prodEl) prodEl.textContent = state.current.recipe.product_name;
    const listEl = $("batch-discard-list");
    if (listEl) {
      listEl.hidden = false;
      listEl.innerHTML = rows.map(({ it }) =>
        `<li class="add-weigh-portion">`
        + `<span class="add-weigh-portion-no">${esc(it.material_name)}</span>`
        + `<span class="add-weigh-portion-amt">${fmt(Number(it.actual_amount), dp())} g</span></li>`
      ).join("");
    }
    const reason = $("batch-discard-reason");
    if (reason) reason.value = "";
    const err = $("batch-discard-error");
    if (err) err.hidden = true;
    modal.hidden = false;
    const back = $("batch-discard-back");
    if (back) back.focus();  // 실수 Enter 가 파괴적 선택이 안 되게
  }

  function closeBatchDiscardModal() {
    const modal = $("batch-discard-modal");
    if (modal) modal.hidden = true;
    _batchDiscardSource = null;
  }

  async function submitBatchDiscard() {
    const reasonEl = $("batch-discard-reason");
    const err = $("batch-discard-error");
    const reason = (reasonEl ? reasonEl.value : "").trim();
    if (!reason) {
      if (err) { err.textContent = "폐기 사유를 입력하세요 — 책임자와 협의한 내용을 남깁니다."; err.hidden = false; }
      if (reasonEl) reasonEl.focus();
      return;
    }
    const rows = weighedRowsForDiscard();
    const btn = $("batch-discard-submit");
    if (btn) btn.disabled = true;
    try {
      await request("/blend/batch-discards", {
        method: "POST",
        body: {
          recipe_id: state.current.recipe.id,
          product_name: state.current.recipe.product_name,
          work_date: $("blend-date").value || todayISO(),
          total_amount: Number($("blend-total").value) || null,
          reason,
          source: _batchDiscardSource || "manual",
          details: rows.map(({ it }) => ({
            material_name: it.material_name,
            material_code: it.material_code || "",
            material_lot: it.material_lot || "",
            actual_amount: Number(it.actual_amount) || 0,
          })),
        },
      });
      // 기록 완료 — 이 배치는 끝났다. 열려 있던 창을 모두 닫고 화면을 새 배합 상태로.
      closeBatchDiscardModal();
      closeDiscardModal();
      closeRescaleBlockModal();
      clearDraft();  // 폐기된 배치의 초안이 되살아나면 안 된다
      const sel = $("blend-recipe");
      if (sel) { sel.value = ""; sel.dispatchEvent(new Event("change", { bubbles: true })); }
      notify("배치 폐기를 기록했습니다 — 책임자 사후 점검(LOT 대사) 화면에서 볼 수 있습니다.", "warn big");
    } catch (e) {
      if (err) { err.textContent = `기록 실패: ${e.message || e}`; err.hidden = false; }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // '처음부터 다시' 진입문(부족 리셋) — 담긴 값이 있으면 폐기 여부를 먼저 묻는다.
  function requestResetWeigh(idx) {
    if (idx == null) return;
    const amount = Math.round(currentActualOf(idx) * 100) / 100;
    if (amount <= 0) { performResetWeigh(idx); return; }
    openDiscardAsk(
      [{ idx, amount }],
      `지금까지 담은 ${fmt(amount, dp())} g — 실제로 버리는 경우에만 폐기로 기록됩니다.`,
      () => performResetWeigh(idx),
    );
  }

  // 초과 비우기 진입문 — 초과 행들의 '덜어낼 양(초과분)'을 모아 폐기 여부를 묻는다.
  // 부족만 묻고 초과는 안 묻던 비대칭 해소(2026-08-05 전수 감사 R-2). prepare 는
  // '진행' 확정 후에만 실행된다(부모 모달 닫기 등 — 돌아가면 아무것도 안 바뀐다).
  function requestClearOverActuals(prepare) {
    const tol = state.toleranceG;
    const rows = [];
    state.items.forEach((it, i) => {
      if (i === state.anchorIndex || it.actual_amount === "") return;
      if (!varianceVerdict(Number(it.actual_amount), it.theory_amount, tol).over) return;
      const over = Math.round((Number(it.actual_amount) - it.theory_amount) * 100) / 100;
      if (over > 0) rows.push({ idx: i, amount: over });
    });
    const proceed = () => {
      closeDiscardAskModal();
      if (prepare) prepare();
      clearOverActuals();
    };
    if (!rows.length) { proceed(); return; }
    const total = Math.round(rows.reduce((s, r) => s + r.amount, 0) * 100) / 100;
    openDiscardAsk(
      rows,
      `초과분 합계 ${fmt(total, dp())} g — 비커에서 덜어내 버리는 경우에만 폐기로 기록됩니다.`,
      proceed,
    );
  }

  // 모드·저울 전용 여부에 따라 칩/안내문/note 를 맞춘다.
  function applyAwModeTexts() {
    const scaleOnly = Boolean(state.scaleOnlyInput);
    const chip = $("add-weigh-state-chip");
    const chipLabel = $("add-weigh-state-label");
    if (chip) chip.hidden = _awMode == null;
    if (chipLabel) chipLabel.textContent = AW_MODE_LABEL[_awMode] || "";
    const guideEl = document.querySelector(".add-weigh-guide");
    if (guideEl) {
      if (_awMode === "loaded") {
        guideEl.innerHTML = scaleOnly
          ? "저울 표시가 <b>아래 목표 값</b>이 될 때까지 담고 <b>PRINT</b>를 누르세요."
          : "저울 표시가 <b>아래 목표 값</b>이 될 때까지 담고, 표시값 전체를 입력하세요.";
      } else {
        guideEl.innerHTML = scaleOnly
          ? "저울에 담기는 만큼 담고 <b>PRINT</b>를 누르세요. 목표를 채우면 자동으로 끝납니다."
          : "한 번에 담기는 만큼 담고 <b>담기</b>를 누르세요. 목표를 채우면 자동으로 끝납니다.";
      }
    }
    const noteEl = $("add-weigh-note");
    if (noteEl) {
      noteEl.hidden = scaleOnly;  // 저울 전용이면 위 안내가 이미 PRINT 를 말한다
      noteEl.textContent = _awMode === "loaded"
        ? "저울이 있으면 PRINT 를 눌러도 됩니다 — 표시값에서 이미 담은 양을 빼고 기록합니다."
        : "저울이 있으면 PRINT 를 눌러도 자동으로 담깁니다.";
    }
  }

  function addWeighRemaining(idx) {
    const it = state.items[idx];
    if (!it) return 0;
    const target = Number(it.theory_amount) || 0;
    const cur = it.actual_amount === "" ? 0 : (Number(it.actual_amount) || 0);
    return Math.max(0, Math.round((target - cur) * 100) / 100);
  }

  function openAddWeighModal(idx, options, mode) {
    // 모달 요소가 없으면(옛 템플릿) 기존 인라인 추가 입력으로 폴백.
    if (!$("add-weigh-modal")) { openAddInline(idx); return; }
    const it = state.items[idx];
    if (!it) return;
    state.addModeIdx = idx;  // 저울 PRINT 가 이 행으로 라우팅되게(activeScaleRow 경유).
    _addWeighIdx = idx;
    // 값 해석 모드 — requestAddWeigh(상태 선택)를 거쳐 들어온다. 미지정이면 현행과
    // 같은 '추가분 합산'(tared) — 폴백 경로에서도 동작이 조용히 바뀌지 않게.
    _awMode = mode || "tared";
    // 이미 담긴 양이 있는데 회차 기록이 비어 있으면 그 값이 1회차다. 안 맞춰두면
    // "현재 9000 g"인데 목록은 비고 다음이 '1회차'로 안내돼 앞뒤가 어긋난다.
    // (Array 여부만 보면 '다시 계량'이 비워둔 [] 를 놓친다 — 실측으로 잡힘)
    const already = it.actual_amount === "" ? 0 : (Number(it.actual_amount) || 0);
    if (!Array.isArray(it.portions)) it.portions = [];
    if (it.portions.length === 0 && already > 0) it.portions = [already];
    // 헤더 자재명 + 목표/현재/남은 렌더.
    // 자재명은 본문 전용 줄에 — 제목에 붙이면 긴 이름이 좁은 헤더에 감긴다(2026-07-23).
    $("add-weigh-title").textContent = (options && options.split) ? "나눠 담기" : "추가 계량";
    const matEl = $("add-weigh-material");
    if (matEl) matEl.textContent = it.material_name;
    // 저울 전용 모드면 수동 입력+더하기 줄 숨김(PRINT 만으로 합산). 안내 문구는
    // 모드(추가분/누계)·저울 전용 여부에 따라 applyAwModeTexts 가 맞춘다.
    const scaleOnly = Boolean(state.scaleOnlyInput);
    const row = $("add-weigh-input-row");
    if (row) row.hidden = scaleOnly;
    applyAwModeTexts();
    // 반드시 '모달을 연 뒤' 숫자를 그린다 — refreshAddWeighModal 은 닫힌 모달이면
    // 갱신을 건너뛰므로, 열기 전에 부르면 목표/현재/남은이 초기 "-" 로 남는다
    // (현장 신고 2026-07-22: 추가 계량 화면 목표값이 "-" 표시).
    $("add-weigh-modal").hidden = false;
    refreshAddWeighModal(idx);
    const input = $("add-weigh-input");
    if (input) { input.value = ""; if (!state.scaleOnlyInput) input.focus(); }
    updateScaleTargetIndicator();  // 모달이 안내 역할 — 행 강조 제거, 해당 ⚖ 활성 표시
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
    const remLabelEl = $("add-weigh-remaining-label");
    // 큰 숫자 = 작업자가 저울 표시창에서 맞춰야 할 값(모드별로 다르다 — 2026-08-05 현장
    // 지적: 두 모드가 같은 '더 담아야 할 양'을 띄워 차이가 안 보였고, 누계 모드에선
    // 표시창이 그 값이 될 때까지만 담는 실수를 부른다).
    //   영점 잡힘: 표시창 = 이번에 담는 양 → 남은 양을 띄운다.
    //   무게 남음: 표시창 = 전체 무게    → 목표 전체를 띄운다.
    if (_awMode === "loaded") {
      if (remLabelEl) remLabelEl.textContent = "저울 표시가 이 값이 될 때까지 담으세요";
      if (remEl) {
        remEl.textContent = `${fmt(target, dp())} g`;
        remEl.classList.add("is-cumulative");
      }
    } else {
      if (remLabelEl) remLabelEl.textContent = "더 담아야 할 양";
      if (remEl) {
        remEl.textContent = `${fmt(remaining, dp())} g`;
        remEl.classList.remove("is-cumulative");
      }
    }
    const subEl = $("add-weigh-sub");
    if (subEl) subEl.textContent = `목표 ${fmt(target, dp())} g · 현재 ${fmt(cur, dp())} g`;
    // 담은 회차를 1회차부터 쌓아 보여준다 — 이 창 안에서 한 자재를 끝낸다는 감각을
    // 주려면 진행 이력이 남아 있어야 한다. 마지막 회차를 강조해 방금 넣은 것을 표시.
    // 아직 안 담았으면 빈 안내를 보여줘 창의 목적이 드러나게 한다.
    const porEl = $("add-weigh-portions");
    const emptyEl = $("add-weigh-portions-empty");
    const list = Array.isArray(it.portions) ? it.portions : [];
    if (emptyEl) emptyEl.hidden = list.length > 0;
    if (porEl) {
      porEl.hidden = list.length === 0;
      porEl.innerHTML = list
        .map((p, n) => {
          const last = n === list.length - 1 ? " is-last" : "";
          return `<li class="add-weigh-portion${last}">`
            + `<span class="add-weigh-portion-no">${n + 1}회차</span>`
            + `<span class="add-weigh-portion-amt">${fmt(p, dp())} g</span></li>`;
        })
        .join("");
    }
    // 다음이 몇 회차인지 입력칸에 — '계속 이어서 담는 중'이라는 신호.
    // 누계 모드에선 넣을 값이 '저울 표시값 전체'다 — 추가분을 적으면 이중 차감된다.
    const inputEl = $("add-weigh-input");
    if (inputEl) inputEl.placeholder = _awMode === "loaded"
      ? `${list.length + 1}회차 — 저울 표시값(전체) g`
      : `${list.length + 1}회차 담을 양 g`;
    // 자동 완료 — 목표에 '딱' 도달했을 때만. remaining 은 Math.max(0,…) 로 0 에서
    // 잘리므로 초과(음수 남음)도 0 으로 보여, 그것만 보면 넘겨 담아도 완료로 오인한다
    // ("완료" 성공 토스트 + "허용 편차 초과" 오류가 동시에 뜨던 버그). 실제 편차의
    // 절댓값으로 판정해, 초과는 완료로 치지 않고 아래 warnIfVariance(초과 경고/증량)에 맡긴다.
    const overshoot = cur - target;  // 양수=초과, 음수=미달
    if (varianceVerdict(cur, target, state.toleranceG).within) {
      notify(`${it.material_name} 추가 계량 완료`, "success");
      finishAddWeighModal(idx);
    }
  }

  // 더하기/Enter — 모드에 따라 값을 추가분으로 환산해 합산(모달 숫자는 applyAddAmount 끝에서 갱신).
  function applyAddWeighInput(idx) {
    const input = $("add-weigh-input");
    if (!input) return;
    const it = state.items[idx];
    const cur = it && it.actual_amount !== "" ? (Number(it.actual_amount) || 0) : 0;
    const res = resolveAddPortion(_awMode || "tared", Number(input.value), cur);
    if (!res.ok) {
      // 누계 모드인데 표시값이 현재 담은 양 이하 — 비커 교체·상태 오선택·덜어냄 신호.
      if (res.reason === "not-above-current") {
        notify(`입력값이 현재 담은 양(${fmt(cur, dp())} g)보다 크지 않습니다 — 저울 상태 선택이 맞는지 [변경]으로 확인하세요.`, "error");
      }
      input.focus();
      return;
    }
    input.value = "";
    applyAddAmount(idx, res.portion);
    // applyAddAmount 가 addModeIdx 를 null 로 되돌리므로 모달 진행 중엔 다시 올린다.
    state.addModeIdx = idx;
    if (!state.scaleOnlyInput) input.focus();
  }

  // 자동 완료 — 값 유지한 채 모달 닫고 다음 LOT 칸(또는 저장 버튼)으로 포커스 이동.
  function finishAddWeighModal(idx) {
    $("add-weigh-modal").hidden = true;
    _addWeighIdx = null;
    _awMode = null;  // 해석 모드는 모달과 같은 수명 — 다음 진입 때 다시 고른다
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
    updateScaleTargetIndicator();  // 모달 완료 후 대상이 다음 행으로 이동한 것을 반영
  }

  // 수동 닫기 — 완료(값 유지) 또는 다시 계량(실제량 비움). addModeIdx 해제·배지 갱신.
  function closeAddWeighModal(idx, keepValue) {
    $("add-weigh-modal").hidden = true;
    _addWeighIdx = null;
    _awMode = null;  // 해석 모드는 모달과 같은 수명 — 다음 진입 때 다시 고른다
    state.addModeIdx = null;
    if (!keepValue && idx != null) {
      const it = state.items[idx];
      // 처음부터 다시 계량 — 누계와 함께 회차 기록도 버린다. 안 지우면 다음 분할에
      // 이전 회차가 붙어 "8000 + 8000 + 5000" 처럼 넣지도 않은 양이 표시된다.
      if (it) { it.actual_amount = ""; it.portions = []; }
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
      if (!varianceVerdict(Number(it.actual_amount), it.theory_amount, tol).within) badIdx.push(i);
    });
    if (!badIdx.length) return;
    if (badIdx.length === 1) { warnIfVariance(badIdx[0]); return; }
    const names = badIdx.map((i) => state.items[i].material_name).join(", ");
    notify(`허용 편차(±${tol}g) 초과: ${names}. 해당 자재를 다시 계량하세요.`, "error");
  }

  function updateTotals() {
    const { theory, actual, net } = computeTotals(state.items);
    $("blend-theory-total").textContent = state.items.length ? fmt(theory, dp()) : "-";
    $("blend-actual-total").textContent = state.items.length ? fmt(actual, dp()) : "-";
    const nv = $("blend-net-var");
    nv.textContent = state.items.length ? (net > 0 ? "+" : "") + fmt(net, dp()) : "-";

    // 순편차는 계량이 끝나야 뜻이 생긴다. 도중에는 '아직 안 넣은 양'이라 늘 큰 음수인데
    // 예전에는 값과 무관하게 늘 주황(#f6a26a)이라, 정상적으로 계량하는 내내 경고처럼
    // 보였다 — 색이 아무 정보도 주지 않으면서 잘못된 뜻만 전했다(2026-08-08).
    // 진행 중에는 라벨을 '남은 양'으로 바꾸고 색을 빼며, 다 채운 뒤에만 판정색을 준다.
    const filled = state.items.filter(
      (it) => it.actual_amount !== "" && it.actual_amount != null,
    ).length;
    const total = state.items.length;
    const done = total > 0 && filled === total;
    const metric = $("blend-net-metric");
    const label = $("blend-net-label");
    if (label) label.textContent = done || !total ? "순편차" : "남은 양";
    if (metric) {
      metric.classList.toggle("is-pending", Boolean(total) && !done);
      // 완료 후 판정 — 각 행이 허용 편차 안으로 강제되므로 합계도 그 범위 안이 정상.
      const envelope = Math.max(state.toleranceG * total, state.toleranceG);
      metric.classList.toggle("is-off", done && Math.abs(net) > envelope);
    }
    const prog = $("blend-progress");
    if (prog) {
      prog.textContent = !total
        ? ""
        : (done ? `계량 완료 — ${total}개 자재` : `계량 ${filled} / ${total} 자재`);
      prog.classList.toggle("done", done);
    }
    updateTotalLock();
  }

  // 총 배합량 잠금 — 자재 실제량이 하나라도 입력되면 총 배합량을 바꿀 수 없다
  // (변경은 승인된 증량으로만 — applyRescale 은 프로그램적으로 .value 를 갱신하므로
  // readOnly 여도 계속 동작한다). 기준 자재 레시피는 이미 총량이 읽기 전용이라 제외.
  // 잠금 중에는 기준 빠른 채우기 버튼도 비활성화한다. 초기화/레시피 변경 시 자동 해제.
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
  // 미저장 입력이 있는 동안은 타임아웃 없음(서버 유휴 8h + 하트비트로 보호).
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

  let _saving = false;   // 중복 저장 방지 — 응답이 늦으면 작업자가 한 번 더 누른다.
  // 저장 멱등 키 — 저장이 성공할 때까지 유지한다. 네트워크가 끊겨 "저장 실패"가 뜬 뒤
  // 다시 저장하면 같은 id 가 실려 가고, 서버는 첫 저장이 이미 커밋됐으면 그 기록을
  // 그대로 돌려준다(같은 계량값이 두 LOT 이 되는 것을 막는다).
  let _saveRequestId = null;

  function newRequestId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
    } catch (e) { /* 구형 브라우저 — 아래 폴백 */ }
    return "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }

  // 중복 저장 가드는 **첫 await 앞**에서 세운다. 예전에는 `if (_saving) return` 검사만
  // 앞에 있고 `_saving = true` 는 작업자 교대·미등록 LOT 조회 등 여러 await 뒤에 있어서,
  // 그 사이에 저장 버튼을 다시 누르면 두 번째 호출도 그대로 통과했다(로트 2건 생성).
  async function saveBlend() {
    if (_saving) return;
    _saving = true;
    const saveBtn = $("blend-save");
    if (saveBtn) saveBtn.disabled = true;   // 즉시 비활성화 — 재클릭 자체를 막는다
    try {
      await saveBlendInner(saveBtn);
    } finally {
      _saving = false;
      if (saveBtn) {
        saveBtn.disabled = false;
        if (saveBtn.dataset.label) saveBtn.textContent = saveBtn.dataset.label;
      }
    }
  }

  async function saveBlendInner(saveBtn) {
    const err = $("blend-error");
    err.hidden = true;
    if (!state.current) { err.textContent = "레시피를 선택하세요."; err.hidden = false; return; }
    // 실제량이 하나도 없으면 저장하지 않는다. 저장 성공 후 화면은 레시피·총량을 유지하므로,
    // 습관적으로 Enter/저장을 한 번 더 누르면 '전부 빈' 기록이 새 LOT 을 받아 저장됐다.
    if (state.items.every((it) => it.actual_amount === "" || it.actual_amount == null)) {
      err.textContent = "계량한 실제량이 없습니다 — 자재를 계량한 뒤 저장하세요.";
      err.hidden = false;
      notify("계량값이 없어 저장하지 않았습니다.", "error");
      return;
    }
    // 전 자재 계량 완료 — 하나라도 실제량이 비면 저장하지 않는다(다중 계량 화면과 대칭).
    // 빈 실제량은 rowVariance 가 편차 0 으로 돌려주기 때문에 위의 편차 차단을 그냥
    // 통과했고, 서버도 예전에는 NULL 로 저장해 그 자재가 '투입 안 됨'으로 집계됐다.
    // 반응기 이월 행은 이월 적용 시 실제량이 채워지므로 여기서 자연히 만족된다.
    const unweighed = state.items
      .filter((it) => it.actual_amount === "" || it.actual_amount == null)
      .map((it) => it.material_name);
    if (unweighed.length) {
      err.textContent = "실제량 미입력: " + unweighed.slice(0, 6).join(", ")
        + (unweighed.length > 6 ? " 외" : "") + ". 모든 자재를 계량한 뒤 저장하세요.";
      err.hidden = false;
      notify("계량하지 않은 자재가 있습니다 — 저장할 수 없습니다.", "error");
      const firstIdx = state.items.findIndex(
        (it) => it.actual_amount === "" || it.actual_amount == null);
      if (firstIdx >= 0) {
        const input = document.querySelector(`.blend-actual[data-idx="${firstIdx}"]`);
        if (input) input.focus();
      }
      return;
    }
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
      i !== ai && !varianceVerdict(Number(it.actual_amount), it.theory_amount, tol).within
    );
    if (bad.length) {
      err.textContent = varianceBlockMessage(badVarianceNames(bad), tol);
      err.hidden = false;
      notify(`허용 편차 ±${fmt(tol, 2)}g 초과 — 저장할 수 없습니다.`, "error");
      return;
    }
    // 자재 LOT 필수 — 실제량을 넣은 행은 LOT 도 반드시 입력. 앞 단계 기록에 없는 LOT 를
    // '확인하고 진행' 한 행도 material_lot 가 채워져 있어 여기서 만족된다(분기 불필요).
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
    // 앞 단계 기록에 없는 반제품 LOT — **저장을 막지 않는다**(2026-08-04 차단 해제).
    // 확인 창을 거치지 않고 여기까지 온 건(조회 실패 fail-open·붙여넣기·초안 복구 등)도
    // acknowledged=false 로 그대로 보낸다 — 사유가 없다고 신호를 버리면 나중에 "그 LOT 이
    // 결국 생겼는지" 대사할 것이 남지 않는다. 입력칸에는 주황 테두리를 남긴다.
    const unacked = [];
    const unackedSeen = new Set();
    for (let i = 0; i < state.items.length; i++) {
      const it = state.items[i];
      const name = (it.material_name || "").trim();
      if (!state.lotSuggest || !state.lotSuggest[name]) continue;
      const lot = (it.material_lot || "").trim();
      if (!lot) continue;
      const key = lotOverrideKey(name, lot);
      if (key in state.lotOverrides) continue;  // 확인 창을 거쳤다(사유는 선택)
      if (unackedSeen.has(key)) continue;
      if (!(await checkLotRegistered(name, lot))) {
        unackedSeen.add(key);
        const input = document.querySelector(`.blend-lot[data-idx="${i}"]`);
        if (input) setErpLotWarn(input, true, "앞 단계 배합 기록에 없는 LOT 입니다.");
        unacked.push({ material_name: name, material_lot: lot, reason: "", acknowledged: false });
      }
    }
    // 확인하고 진행한 LOT 은 사유(있으면)를 비고 앞에 남긴다(책임자 사후 확인).
    const overrideNote = buildOverrideNote();
    const lotOverrides = buildLotOverrides().concat(unacked);
    // 저장 직전 작업자 확인 — 교대 잊고 앞사람 이름으로 저장되는 것 차단
    if (!window.confirm(`작업자 '${state.sessionWorker}' 이름으로 저장합니다. 맞습니까?`)) return;
    // 이 저장 시도의 멱등 키 — 성공할 때까지 재사용한다(재시도 = 같은 요청).
    if (!_saveRequestId) _saveRequestId = newRequestId();
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
      note: [overrideNote, buildManualApprovalNote(), $("blend-note").value.trim()].filter(Boolean).join("\n") || null,
      reactor: reactorRaw ? Number(reactorRaw) : null,
      worker_sign: state.workerPad ? state.workerPad.dataUrl() : null,
      // 대사용: 앞 단계 기록에 없는 LOT 진행 기록(사유는 선택 + 확인 여부). 저장을 막지
      // 않으므로 이건 '통과 허가증'이 아니라 '나중에 대조할 신호'다.
      lot_overrides: lotOverrides.length ? lotOverrides : null,
      // 증량 승인 이력 — 각 증량의 before/after 총량 + 승인(approval_id/approver) 또는
      // 부재 진행(absence_reason). 없으면 null. 서버가 유효성(승인 실재 여부)을 재검증한다.
      rescale_events: state.rescaleEvents.length ? state.rescaleEvents : null,
      // 계량 중 자재 폐기 이력 — '처음부터 다시'에서 실제로 버린 자재(없으면 null).
      discard_events: (state.discardEvents && state.discardEvents.length) ? state.discardEvents : null,
      // 수기 입력을 책임자 부재로 진행했으면 그 사유 — 서버가 기록에 남기고 책임자
      // 확인(ack) 전까지 미확인으로 표시한다(증량 부재와 동일한 사후 확인 루프).
      manual_absence_reason: (state.manualApproved && state.manualApproved.absence_reason) || null,
      // 저울 연결 중 손입력 행이 하나라도 있으면 배치를 '수동 입력'으로 기록
      manual_entry: state.items.some((it) => it.manual === true),
      // 저장 멱등 키 — 응답 유실 후 재시도해도 기록이 두 벌 생기지 않는다.
      request_id: _saveRequestId,
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
    // 가드·비활성화는 saveBlend 가 첫 await 전에 이미 세웠다 — 여기서는 라벨만 바꾼다.
    if (saveBtn) {
      if (!saveBtn.dataset.label) saveBtn.dataset.label = saveBtn.textContent;
      saveBtn.textContent = "저장 중…";
    }
    try {
      const rec = await request("/blend/records", { method: "POST", body });
      notify(`배합 실적 저장: ${rec.product_lot} (작업자: ${rec.worker})`, "success");
      _saveRequestId = null;   // 저장 성공 → 다음 배합은 새 멱등 키
      clearDraft();  // 저장 완료 → 임시 저장 삭제
      // 실제량/LOT 초기화 (연속 작업 편의). 기준 자재 모드면 이론량·총량도 함께 초기화해
      // 다음 배합을 '기준 자재 먼저 계량' 상태로 되돌린다. 이월 표식도 함께 지운다.
      state.items.forEach((it) => {
        it.actual_amount = ""; it.material_lot = ""; it.manual = false; it.carried_over = false;
      });
      // 증량이 있었던 배합은 총량·이론량이 '증량된 값'으로 남아 있다. 그대로 두면 다음
      // 배합이 아무 안내 없이 증량된 총량에서 시작하고(그 기록은 증량 이력 0건), 반복하면
      // 총량이 계단식으로 올라간다 — 증량 2회 제한이 사실상 무력화된다. 증량 직전 총량으로
      // 되돌린다(첫 증량 이벤트의 before_total). 기준 자재 모드는 아래에서 통째로 초기화.
      const firstRescale = (state.rescaleEvents || []).find((ev) => ev && ev.before_total != null);
      let restoredTotal = null;
      if (!hasAnchor() && firstRescale) {
        const totalInput = $("blend-total");
        if (totalInput) {
          totalInput.value = String(firstRescale.before_total);
          restoredTotal = firstRescale.before_total;
        }
      }
      if (hasAnchor()) {
        state.items.forEach((it) => { it.theory_amount = null; });
        state.prevAnchorActual = "";
        const totalInput = $("blend-total");
        if (totalInput) totalInput.value = "";
      }
      // 저장 후 다음 배합은 증량분이 없는 깨끗한 상태에서 시작.
      state.rescaleTotalG = 0;
      state.addModeIdx = null;
      state.scaleTargetIdx = null;  // 저장 완료 → 저울 대상 지정 해제
      state.pendingRescale = null;
      state.addPending = {};
      state.rescaleActive = false;
      state.rescaleEvents = [];  // 저장 완료 → 증량 승인 이력 초기화(총 배합량 잠금 해제)
      state.discardEvents = [];  // 저장 완료 → 폐기 이력은 방금 기록에 실렸다
      state.lotOverrides = {};
      // 저장 성공 → LOT 조회 캐시를 비운다. 방금 저장한 기록으로 '없던' LOT 이 생겼을 수
      // 있고, 다음 배합이 그걸 곧바로 원료로 쓴다(B-1: 시킨 대로 해도 계속 막히던 원인).
      lotCheckCache.clear();
      state.manualApproved = null;  // 저장 완료 → 수기 입력 승인 해제(다음 배합은 다시 잠금)
      clearRescaleSummary();
      // 저장 성공 직후 LOT 미리보기가 방금 저장한 LOT 을 그대로 보여주던 문제(F5) —
      // 다음 순번을 즉시 재조회해 갱신한다(다음 입력 때에야 바뀌던 것을 고친다).
      updateLotPreview();
      if (state.workerPad) state.workerPad.clear();
      // 증량 총량을 되돌렸으면 이론량도 그 총량 기준으로 다시 산출(표시값 정합).
      if (restoredTotal !== null) {
        recomputeTheory();
        notify(`다음 배합을 위해 총 배합량을 증량 전 값(${restoredTotal} g)으로 되돌렸습니다.`, "warn");
      }
      renderMatRows();
      updateManualEntryControl();  // 승인 해제 반영(배너 텍스트·버튼 복귀)
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
      // 실패 시 _saveRequestId 는 그대로 둔다 — 다시 저장하면 같은 멱등 키가 실려 가고,
      // 첫 요청이 사실은 서버에 커밋됐다면 서버가 그 기록을 돌려준다(중복 생성 방지).
      err.textContent = e.message;
      err.hidden = false;
      notify(`저장 실패: ${e.message}`, "error");   // 폼 한 줄만으로는 놓치기 쉽다
    }
    // 저장 버튼 복구·중복 가드 해제는 saveBlend 의 finally 가 담당한다.
  }

  function bind() {
    // 자재 표 입력칸 포커스 이동 시 저울 대상 표시 갱신(delegated — 재렌더돼도 유지).
    // tbody 는 재사용되므로 한 번만 부착한다. focusout 후 새 focusin 이 activeElement 를
    // 확정하도록 microtask 로 지연 갱신.
    const matBody = $("blend-mat-body");
    if (matBody) {
      matBody.addEventListener("focusin", updateScaleTargetIndicator);
      matBody.addEventListener("focusout", () => setTimeout(updateScaleTargetIndicator, 0));
    }
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
        if (it) setTheoryCellContent(cell, it);
      });
      document.querySelectorAll("#blend-mat-body .blend-actual").forEach((act) => {
        const it = state.items[Number(act.dataset.idx)];
        if (it) act.placeholder = it.theory_amount == null ? "" : fmt(it.theory_amount, dp());
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
      // 닫기만 하면 초과 상태가 남아 누적된다 — 비우되, 덜어낸 자재의 폐기 여부를 먼저
      // 묻는다(질문이 제안 모달 위에 뜨고, 돌아가면 제안이 그대로 남는다).
      requestClearOverActuals(() => {
        state.pendingRescale = null;
        closeRescaleModal();
      });
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
    // 승인 모달은 바깥 클릭으로 빠져나갈 수 없다 — 미해소 초과가 누적되는 사고 방지.
    // 나가는 길은 [승인]/[부재로 진행]/[다시 계량](Esc 동일) 세 가지뿐.
    function dismissApproveWithReweigh() {
      if (_rescaleReauthPending) { cancelRescaleApprove(); return; }
      // 옛 window.confirm 을 폐기 질문이 대체한다 — [돌아가기]가 실수 클릭 보험이고,
      // 진행하면 초과값을 비우면서 덜어낸 자재의 폐기 여부까지 기록된다(R-2).
      requestClearOverActuals(() => {
        state.pendingRescale = null;
        closeRescaleApproveModal();
      });
    }
    const approveModal = $("rescale-approve-modal");
    if (approveModal) approveModal.addEventListener("click", (e) => {
      if (e.target === approveModal) {
        showApproveError("승인, 부재로 진행, 또는 [다시 계량] 중에서 선택하세요.");
      }
    });
    const approveReweigh = $("rescale-approve-reweigh");
    if (approveReweigh) approveReweigh.addEventListener("click", dismissApproveWithReweigh);
    document.addEventListener("keydown", (e) => {
      // 폐기 질문이 위에 떠 있으면 그 창의 Esc(돌아가기)에 양보한다.
      const daOpen = $("discard-ask-modal");
      if (daOpen && !daOpen.hidden) return;
      if (e.key === "Escape" && approveModal && !approveModal.hidden) dismissApproveWithReweigh();
    });
    // 저울 전용 모드 수기 입력 승인 — 요청 버튼/모달 [승인]·[취소]·Enter·Esc.
    const manualReq = $("manual-entry-request-btn");
    if (manualReq) manualReq.addEventListener("click", openManualApproveModal);
    const manualSubmit = $("manual-approve-submit");
    if (manualSubmit) manualSubmit.addEventListener("click", () => submitManualApproval());
    const manualCancel = $("manual-approve-cancel");
    if (manualCancel) manualCancel.addEventListener("click", closeManualApproveModal);
    const manualAbsence = $("manual-absence-submit");
    if (manualAbsence) manualAbsence.addEventListener("click", submitManualAbsence);
    const manualPw = $("manual-approve-pw");
    if (manualPw) manualPw.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      submitManualApproval();
    });
    const manualModal = $("manual-approve-modal");
    if (manualModal) manualModal.addEventListener("click", (e) => {
      if (e.target === manualModal) closeManualApproveModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && manualModal && !manualModal.hidden) closeManualApproveModal();
    });
    // 빠른 사유 태그(증량·수기 부재 공용) — 누르면 사유칸 토글 채움.
    wireReasonTags();
    // 3회 증량 차단 모달 — 확인만.
    const blockClose = $("rescale-block-close");
    if (blockClose) blockClose.addEventListener("click", closeRescaleBlockModal);
    const blockModal = $("rescale-block-modal");
    document.addEventListener("keydown", (e) => {
      // 배치 폐기 창이 위에 떠 있으면 그 창의 Esc(돌아가기)에 양보한다.
      const bd = $("batch-discard-modal");
      if (bd && !bd.hidden) return;
      if (e.key === "Escape" && blockModal && !blockModal.hidden) closeRescaleBlockModal();
    });
    // 배치 폐기 기록 — 폐기 권장([배치 폐기 기록…])·3회 차단 모달에서 진입. 봉인 모달.
    const bdModal = $("batch-discard-modal");
    const bdOpenFromDiscard = $("discard-batch-btn");
    if (bdOpenFromDiscard) bdOpenFromDiscard.addEventListener("click", () => openBatchDiscardModal("overweight"));
    const bdOpenFromBlock = $("block-batch-btn");
    if (bdOpenFromBlock) bdOpenFromBlock.addEventListener("click", () => openBatchDiscardModal("rescale_limit"));
    const bdBack = $("batch-discard-back");
    if (bdBack) bdBack.addEventListener("click", closeBatchDiscardModal);
    const bdSubmit = $("batch-discard-submit");
    if (bdSubmit) bdSubmit.addEventListener("click", () => { submitBatchDiscard(); });
    if (bdModal) bdModal.addEventListener("click", (e) => {
      if (e.target === bdModal) notify("사유를 입력해 기록하거나 [돌아가기]를 누르세요.", "warn");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && bdModal && !bdModal.hidden) closeBatchDiscardModal();
    });
    const discardCancel = $("discard-cancel");
    if (discardCancel) discardCancel.addEventListener("click", () => {
      // 폐기 선택 — 증량을 적용하지 않는다(기존 초과 토스트·저장 차단 상태 유지).
      state.pendingRescale = null;
      closeDiscardModal();
    });
    // LOT 검사 모달 — 공용 컴포넌트(lotModal)가 footer 버튼을 한 번에 묶는다.
    // 화면별 동작만 콜백으로: 'LOT 지우고 다시 입력'의 값 비우기, '계속'의 확인 기록 보관.
    lotModal.bind({
      // 'LOT 지우고 다시 입력' — 명시 선택일 때만 값을 지운다(더 이상 자동 삭제 아님).
      onClear: (input) => {
        const idx = Number(input.dataset.idx);
        if (state.items[idx]) state.items[idx].material_lot = "";
        input.value = "";
        setErpLotWarn(input, false);
        input.focus();
      },
      // '확인했습니다 · 계속' — 그 (자재,LOT) 조합을 확인 완료로 표시하고 사유를 보관한다.
      // **사유가 빈 문자열이어도 반드시 기록한다** — 사유를 조건으로 걸면 사유가 선택이 된
      // 순간 대사할 신호가 통째로 사라진다. 값은 그대로 두고 주황 테두리만 남긴다.
      onProceed: (name, lot, reason, input) => {
        state.lotOverrides[lotOverrideKey(name, lot)] = reason || "";
        if (input) setErpLotWarn(input, true, "앞 단계 배합 기록에 없는 LOT — 확인하고 진행함");
      },
    });
    // 파생 이월 모달 — 적용/취소. Escape 도 취소(변경 없음).
    const coConfirm = $("carry-over-confirm");
    if (coConfirm) coConfirm.addEventListener("click", applyCarryOver);
    const coCancel = $("carry-over-cancel");
    if (coCancel) coCancel.addEventListener("click", closeCarryOverModal);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("carry-over-modal").hidden) closeCarryOverModal();
    });
    // 추가 계량 모달 — 더하기/Enter 합산, 완료(값 유지), 다시 계량(비움).
    // Esc·바깥 클릭으로는 닫지 않는다 — 나눠 담는 도중 실수 클릭 한 번에 진행 창(담은 회차
    // 내역)이 사라지던 사고(2026-08-04). 나가는 길은 두 버튼뿐.
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
      // 담긴 값이 있으면 폐기 여부를 먼저 묻는다(오타 정정 vs 실물 폐기 분기).
      if (_addWeighIdx != null) requestResetWeigh(_addWeighIdx);
    });
    // 저울 상태 선택 모달 — 그림 두 장 중 선택, [취소]/[처음부터 다시 계량]/Esc.
    // 바깥 클릭 봉인(다른 봉인 모달과 동일) — 실수 클릭이 선택을 건너뛰면 안 된다.
    // 부족 컨텍스트(_scaleStateShortageIdx)에서는 [취소]가 없고 Esc 도 닫지 않는다 —
    // 나가는 길은 그림 둘 또는 [처음부터 다시 계량] 뿐이다.
    const ssModal = $("scale-state-modal");
    const ssTared = $("scale-state-tared");
    const ssLoaded = $("scale-state-loaded");
    const ssCancel = $("scale-state-cancel");
    const ssReweigh = $("scale-state-reweigh");
    if (ssTared) ssTared.addEventListener("click", () => chooseScaleState("tared"));
    if (ssLoaded) ssLoaded.addEventListener("click", () => chooseScaleState("loaded"));
    if (ssCancel) ssCancel.addEventListener("click", closeScaleStateModal);
    if (ssReweigh) ssReweigh.addEventListener("click", () => {
      // 담긴 값이 있으므로 폐기 여부를 먼저 묻는다(질문이 이 창 위에 겹쳐 뜬다).
      // [돌아가기]면 이 창으로 복귀, 선택하면 값·회차를 비우고 그 칸에 포커스.
      requestResetWeigh(_scaleStateShortageIdx);
    });
    if (ssModal) ssModal.addEventListener("click", (e) => {
      if (e.target === ssModal) notify("두 그림 중 하나를 고르거나 아래 버튼을 누르세요.", "warn");
    });
    document.addEventListener("keydown", (e) => {
      // 폐기 질문이 위에 떠 있으면 그 창의 Esc(돌아가기)에 양보한다.
      const da = $("discard-ask-modal");
      if (da && !da.hidden) return;
      if (e.key === "Escape" && ssModal && !ssModal.hidden) {
        if (_scaleStateShortageIdx != null) {
          notify("두 그림 중 하나를 고르거나 [처음부터 다시 계량]을 누르세요.", "warn");
        } else {
          closeScaleStateModal();
        }
      }
    });
    // 자재 폐기 질문 모달 — [돌아가기]/[폐기 기록]/[숫자 오타]. 바깥 클릭 봉인.
    const daModal = $("discard-ask-modal");
    const daTypo = $("discard-ask-typo");
    const daDiscard = $("discard-ask-discard");
    const daBack = $("discard-ask-back");
    if (daTypo) daTypo.addEventListener("click", () => {
      if (!_discardAskCtx) return;
      const proceed = _discardAskCtx.onProceed;
      proceed();
    });
    if (daDiscard) daDiscard.addEventListener("click", () => {
      if (!_discardAskCtx) return;
      const { rows, onProceed } = _discardAskCtx;
      let count = 0;
      let total = 0;
      rows.forEach((r) => {
        const it = state.items[r.idx];
        if (!it || !(r.amount > 0)) return;
        state.discardEvents.push({
          material_name: it.material_name,
          material_code: it.material_code || "",
          amount_g: r.amount,
        });
        count += 1;
        total += r.amount;
      });
      if (count) {
        const label = count === 1
          ? `${state.items[rows[0].idx].material_name} ${fmt(rows[0].amount, dp())} g`
          : `${count}개 자재 합계 ${fmt(Math.round(total * 100) / 100, dp())} g`;
        notify(`폐기 기록됨: ${label} — 저장 시 기록에 함께 남습니다.`, "warn");
        scheduleDraftSave();  // 폐기 이력도 초안에 즉시(창 닫힘 대비)
      }
      onProceed();
    });
    if (daBack) daBack.addEventListener("click", closeDiscardAskModal);
    if (daModal) daModal.addEventListener("click", (e) => {
      if (e.target === daModal) notify("자재의 행방을 선택하거나 [돌아가기]를 누르세요.", "warn");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && daModal && !daModal.hidden) closeDiscardAskModal();
    });
    const awStateChange = $("add-weigh-state-change");
    if (awStateChange) awStateChange.addEventListener("click", () => openScaleStateModal(null));
    const awDismissGuard = () => {
      notify("담는 중입니다 — [잠시 닫아두기] 또는 [처음부터 다시]로 마쳐주세요.", "warn");
    };
    if (awModal) awModal.addEventListener("click", (e) => {
      if (e.target === awModal && _addWeighIdx != null) awDismissGuard();
    });
    document.addEventListener("keydown", (e) => {
      // 폐기 질문이 위에 떠 있으면 그 창의 Esc(돌아가기)에 양보한다.
      const da = $("discard-ask-modal");
      if (da && !da.hidden) return;
      if (e.key === "Escape" && awModal && !awModal.hidden && _addWeighIdx != null) {
        awDismissGuard();
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
    // 끊긴 작업은 "작성 중 배합"(/blend/drafts)에서만 이어간다 — 진입 배너는 폐지했다.
    // 그 화면의 [이어서 하기]가 sessionStorage 에 슬롯 id 를 남기고 여기로 보낸다.
    const resumeId = blendDrafts ? blendDrafts.takeResume("blend") : null;
    if (resumeId) restoreDraft(resumeId).catch((e) => notify(e.message, "error"));
    // F12: 초안 복구로 진입한 경우(resumeId)가 아니면, 본인 초안이 있는데 빈 폼일 때
    // 안내가 전혀 없던 문제를 고친다 — 초안이 1건 이상이면 페이지 로드당 1회 안내 +
    // 사이드바 [작성 중 배합] 링크에 개수 배지. localStorage 직접 파싱 금지: 라이브러리
    // listAll(localStorage) 사용.
    if (!resumeId) notifyDraftCount("blend");
    const noticeClose = $("blend-draft-notice-close");
    if (noticeClose) noticeClose.addEventListener("click", () => showDraftNotice(""));
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
    // 활동 기반 60분 유휴 자동 로그아웃(공용 PC 보안). 저장 후 5분 로그아웃과 독립적으로
    // 동시 동작 — 먼저 만료되는 쪽이 이긴다. 만료 시 최종 초안 저장 후 홈(/)으로 이동해
    // 재로그인하면 "작성 중 배합" 화면에서 진행분을 이어서 할 수 있다. 작업자 세션이
    // 있을 때만 무장.
    if (createIdleLogout) {
      state.idleLogout = createIdleLogout({
        // window-guard 로 막힌 중복 창은 서버 세션을 공유하므로, 활동이 없어도
        // 만료시켜 주 창의 진행 중 배합 세션을 로그아웃시키면 안 된다. 다른 공유
        // 상태 경로(저울 폴링·flushDraftNow)와 같이 blendWindowBlocked 를 함께 확인한다.
        isActive: () =>
          Boolean(lockedWorkerName()) &&
          !(window.IRMS && window.IRMS.blendWindowBlocked),
        saveDraft: flushDraftNow,
        request: request,
        notify: notify,
        redirectTo: "/",
      });
      state.idleLogout.arm();
    }
  });
})();

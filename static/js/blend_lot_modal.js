/**
 * blend_lot_modal.js — LOT 검사 모달 공용 컴포넌트.
 *
 * 배합(단건)·다중 계량 두 화면이 각각 두 종류의 LOT 검사 모달을 들고 있었다(반제품
 * 미등록 LOT 차단 / ERP 원료 LOT 경고). 네 벌이 90% 동일했고 open/close/wire·책임자
 * 인증 POST 까지 통째로 겹쳐 한 쪽의 수정이 다른 쪽에 안 닿는 사고가 났다.
 *
 * 이 모듈이 마크업의 단일 모달 요소를 양 케이스가 함께 구동한다(blend_lib 패턴:
 * window.IRMS 에 붙고, import 구문 없이 script 태그로 로드).
 *
 *   create(deps) → { openInvalid, openErp, bind }
 *
 *   deps:
 *     prefix   "cont-" | ""           — element id 접두어(두 화면의 기존 id 유지)
 *     esc      (s) => string          — 자재명·LOT 이스케이프(blend_lib.esc)
 *     notify   (msg, level) => void   — 사용자 알림
 *     csrfToken () => string          — 책임자 인증 POST 의 X-CSRF 헤더
 *     request   — (사용 안 함, 호환용; fetch 는 직접)
 *
 *   openInvalid({ name, lot, input })  — 미등록 반제품 LOT(차단). 사유칸 노출.
 *   openErp({ name, code, lot, reason, input, onVerified })  — ERP 원료 LOT(경고). 인증칸 노출.
 *
 *   bind({ onClear, onProceed })  — footer 버튼을 한 번 묶는다(중복 바인딩 가드).
 *     onClear(input)        — '다시 확인'(차단 케이스): 화면별로 LOT 값·state 비우기.
 *                             경고 케이스의 '다시 확인'은 입력에 포커스+선택만(값 유지) —
 *                             onClear 호출 없이 모듈이 직접 처리한다.
 *     onProceed(name, lot, reason, input) — '사유 적고 진행'(차단 케이스): 사유 보관·잔무.
 *                             (예: 다중 계량은 scheduleDraftSave() 도 함께 호출)
 *
 * 보존(현장 동작 그대로):
 *   - fail-open(checkErpLot 이 fetch 실패·file_ok=false 면 통과) — 호출부에서 유지.
 *   - .erp-lot-warn 주황 테두리는 모달이 닫혀도 입력에 남는다(onVerified 가 해제).
 *   - esc() 로 자재명·LOT 문자열 이스케이프.
 *   - 책임자 인증 POST /api/material-lots/manual-verify (body 규약 그대로).
 *   - hidden 속성 토글로만 표시/숨김(.ss-modal[hidden] CSS 규칙).
 */
(function () {
  "use strict";

  const ns = (window.IRMS = window.IRMS || {});

  function create(deps) {
    const d = deps || {};
    const prefix = d.prefix || "";
    const esc = d.esc || function (s) { return String(s == null ? "" : s); };
    const notify = d.notify || function () {};
    const csrfToken = d.csrfToken || function () { return ""; };
    // element id 조립 헬퍼 — 접두어(p) + 기본 id.
    const el = (id) => document.getElementById(prefix + id);

    // 진행 중 케이스("invalid"=차단 / "erp"=경고). footer 버튼 표시를 바꾸는 데 쓴다.
    let currentCase = null;
    // ERP 경고 케이스에서 수동 LOT 추가 성공 시 호출(입력의 .erp-lot-warn 해제용).
    let pendingVerified = null;

    function close() {
      const m = el("lot-invalid-modal");
      if (m) m.hidden = true;
    }

    // ── 케이스 A: 미등록 반제품 LOT(차단) ─────────────────────────
    // 사유칸(reason) 노출, 인증칸(auth) 숨김. footer: [사유 적고 진행][다시 확인].
    function openInvalid(opts) {
      const o = opts || {};
      currentCase = "invalid";
      pendingVerified = null;
      const body = el("lot-invalid-modal-body");
      if (body) {
        body.innerHTML = ""
          + `<p><strong>자재명:</strong> ${esc(o.name)}</p>`
          + `<p><strong>입력한 로트:</strong> ${esc(o.lot)}</p>`
          + `<p>등록되지 않은 로트입니다. 1차 배합 기록이 저장되었는지, LOT 번호가 맞는지 확인하세요.</p>`
          + `<p class="muted small">1차 기록이 아직 없는 정당한 경우에는 아래에 사유를 적고 진행할 수 있습니다(사유는 기록에 남습니다).</p>`;
      }
      const box = el("lot-override-box");
      const reason = el("lot-override-reason");
      if (reason) reason.value = "";
      if (box) box.hidden = true;
      const authBox = el("erp-lot-add-box");
      if (authBox) authBox.hidden = true;
      // footer 버튼 표시: 진행(차단 전용)·다시 확인 보이기, 인증쪽 숨기기.
      showBtn("lot-invalid-proceed", true);
      showBtn("erp-lot-add-toggle", false);
      showBtn("erp-lot-add-submit", false);
      showBtn("lot-invalid-confirm", true);
      const m = el("lot-invalid-modal");
      if (m) {
        m._lotInput = o.input || null;
        m._lotName = o.name;
        m._lotValue = o.lot;
        m.hidden = false;
      }
    }

    // ── 케이스 B: ERP 원료 LOT(경고, 값 유지) ─────────────────────
    // 인증칸(auth) 자리만 준비(기본 숨김 — [책임자 LOT 추가하기] 로 펼침). 사유칸 숨김.
    // footer: [책임자 LOT 추가하기][추가(숨김)][다시 확인]. '다시 확인'은 값 유지+포커스.
    function openErp(opts) {
      const o = opts || {};
      currentCase = "erp";
      pendingVerified = typeof o.onVerified === "function" ? o.onVerified : null;
      const body = el("lot-invalid-modal-body");
      if (body) {
        body.innerHTML = ""
          + `<p><strong>자재명:</strong> ${esc(o.name)}</p>`
          + `<p><strong>품목코드:</strong> ${esc(o.code)}</p>`
          + `<p><strong>입력한 로트:</strong> ${esc(o.lot)}</p>`
          + `<p>${esc(o.reason)}</p>`
          + `<p>LOT 를 제대로 확인해주세요.</p>`;
      }
      const authBox = el("erp-lot-add-box");
      const err = el("erp-lot-add-error");
      if (err) { err.textContent = ""; err.hidden = true; }
      if (authBox) authBox.hidden = true;
      const reasonBox = el("lot-override-box");
      if (reasonBox) reasonBox.hidden = true;
      showBtn("erp-lot-add-submit", false);
      // footer 버튼 표시: 인증 토글·다시 확인 보이기, 사유 진행(차단 전용) 숨기기.
      showBtn("erp-lot-add-toggle", true);
      showBtn("lot-invalid-proceed", false);
      showBtn("lot-invalid-confirm", true);
      ["erp-add-username", "erp-add-password", "erp-add-note"].forEach((id) => {
        const e = el(id);
        if (e) e.value = "";
      });
      const m = el("lot-invalid-modal");
      if (m) {
        m._erpInput = o.input || null;
        m._erpCode = o.code;
        m._erpLot = o.lot;
        m.hidden = false;
      }
    }

    function showBtn(id, on) {
      const b = el(id);
      if (b) b.hidden = !on;
    }

    // ── 책임자 즉석 인증 → 수동 LOT 추가 (케이스 B 전용) ──────────
    // body 규약·오류 문구화·INVALID_CREDENTIALS 변환까지 기존 두 벌과 동일.
    async function submitManualVerify() {
      const err = el("erp-lot-add-error");
      const username = (val("erp-add-username")).trim();
      const password = val("erp-add-password");
      const note = (val("erp-add-note")).trim();
      if (!username || !password) {
        if (err) { err.textContent = "책임자 이름과 비밀번호를 입력하세요."; err.hidden = false; }
        return;
      }
      const m = el("lot-invalid-modal");
      const code = m && m._erpCode;
      const lot = m && m._erpLot;
      try {
        const res = await fetch("/api/material-lots/manual-verify", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
          body: JSON.stringify({
            username, password, material_code: code, lot,
            note: note || undefined,
          }),
        });
        if (!res.ok) {
          // 401/403 — 인라인 오류. 그 외 오류도 인라인.
          let detail = "추가에 실패했습니다.";
          try {
            const j = await res.json();
            if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
            // 서버 규약 코드는 현장 문고로 — 영문 코드가 그대로 보이면 원인을 알 수 없다.
            if (detail === "INVALID_CREDENTIALS") detail = "책임자 이름 또는 비밀번호가 올바르지 않습니다.";
          } catch (_e) { /* 무시 */ }
          if (err) { err.textContent = detail; err.hidden = false; }
          return;
        }
        notify("수동 LOT 를 추가했습니다.", "success");
        // 인증 성공 → 입력의 .erp-lot-warn 주황 테두리 해제(화면 콜백 또는 직접).
        if (typeof pendingVerified === "function") pendingVerified();
        close();
      } catch (e) {
        if (err) { err.textContent = e.message || "추가에 실패했습니다."; err.hidden = false; }
      }
    }

    function val(id) {
      const e = el(id);
      return e ? (e.value || "") : "";
    }

    // ── footer 버튼 바인딩(한 번만 — 중복 가드) ──────────────────
    // onClear(input)        : 차단 케이스 '다시 확인' — 화면별 LOT 비우기.
    // onProceed(name,lot,reason,input) : '사유 적고 진행' — 사유 보관 + 잔무(scheduleDraftSave 등).
    let bound = false;
    function bind(callbacks) {
      if (bound) return;
      bound = true;
      const cb = callbacks || {};
      const onClear = typeof cb.onClear === "function" ? cb.onClear : null;
      const onProceed = typeof cb.onProceed === "function" ? cb.onProceed : null;

      // '다시 확인' — 차단 케이스: 모달 닫고 화면별로 값 비우기(onClear).
      // 경고 케이스: 값 유지, 입력에 포커스+선택.
      const confirmBtn = el("lot-invalid-confirm");
      if (confirmBtn) confirmBtn.addEventListener("click", () => {
        const m = el("lot-invalid-modal");
        const input = m && m._lotInput;
        const erpInput = m && m._erpInput;
        const wasInvalid = currentCase === "invalid";
        close();
        if (wasInvalid) {
          if (onClear && input) onClear(input);
          else if (input) input.focus();
        } else if (erpInput) {
          erpInput.focus();
          if (typeof erpInput.select === "function") { try { erpInput.select(); } catch (_e) {} }
        }
      });

      // '사유 적고 진행'(차단 전용 안전밸브) — 1클릭: 사유칸 표시 / 2클릭(사유 입력됨):
      // 그 (자재,LOT) 조합을 통과 처리하고 사유 보관(저장 시 비고에 남김). 값은 그대로.
      const proceedBtn = el("lot-invalid-proceed");
      if (proceedBtn) proceedBtn.addEventListener("click", () => {
        const box = el("lot-override-box");
        const reason = el("lot-override-reason");
        if (box && box.hidden) { box.hidden = false; if (reason) reason.focus(); return; }
        const text = (reason && reason.value.trim()) || "";
        if (!text) { notify("진행 사유를 입력하세요.", "error"); if (reason) reason.focus(); return; }
        const m = el("lot-invalid-modal");
        const name = m && m._lotName, lot = m && m._lotValue, input = m && m._lotInput;
        if (onProceed) onProceed(name, lot, text, input);
        close();
        if (input) input.focus();
        notify("사유를 남기고 진행합니다 — 이 로트는 기록에 '미등록 진행'으로 남습니다.", "warn");
      });

      // '책임자 LOT 추가하기'(경고 전용) — 인증칸 펼치고 이름칸 포커스.
      const toggleBtn = el("erp-lot-add-toggle");
      if (toggleBtn) toggleBtn.addEventListener("click", () => {
        const box = el("erp-lot-add-box");
        if (box) {
          box.hidden = false;
          showBtn("erp-lot-add-submit", true);
          const u = el("erp-add-username");
          if (u) u.focus();
        }
      });

      // '추가' — 책임자 인증 POST.
      const submitBtn = el("erp-lot-add-submit");
      if (submitBtn) submitBtn.addEventListener("click", submitManualVerify);
    }

    return { openInvalid, openErp, bind, close };
  }

  ns.blendLotModal = { create };
})();

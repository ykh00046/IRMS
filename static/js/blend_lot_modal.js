/**
 * blend_lot_modal.js — LOT 검사 모달 공용 컴포넌트 + LOT 조회 결과 캐시.
 *
 * 배합(단건)·다중 계량 두 화면이 각각 두 종류의 LOT 검사 모달을 들고 있었다(반제품
 * 미등록 LOT / ERP 원재료 LOT 경고). 네 벌이 90% 동일했고 open/close/wire·책임자
 * 인증 POST 까지 통째로 겹쳐 한 쪽의 수정이 다른 쪽에 안 닿는 사고가 났다.
 *
 * 이 모듈이 마크업의 단일 모달 요소를 양 케이스가 함께 구동한다(blend_lib 패턴:
 * window.IRMS 에 붙고, import 구문 없이 script 태그로 로드).
 *
 *   create(deps) → { openInvalid, openErp, bind, close }
 *   createCache(opts) → { key, get, set, clear }
 *
 * ── 2026-08-04 개편 ────────────────────────────────────────────
 * 1) **반제품 LOT 차단 해제.** 앞 단계(1차) 배합 기록에 없는 반제품 LOT 이면 저장을
 *    막고 사유 입력을 요구했다. 반제품을 만들고 곧바로 2차에 투입하는 정당한 경우에
 *    매번 걸려서, 작업자는 사유란에 아무 글자나 치고 넘어갔다 — 통제가 형해화되고
 *    비용은 현장이 다 치렀다. 지금은 ERP 경고와 같은 '가벼운 확인 창'이다.
 *    사유는 선택. 대신 **확인하고 진행한 사실 자체를 반드시 구조화 기록으로 남긴다**
 *    (onProceed(name, lot, reason) — reason 이 "" 여도 호출된다). 사유를 필터로 쓰면
 *    사유가 선택이 되는 순간 대사(자동 대조) 신호가 통째로 사라진다.
 * 2) **포커스를 모달 안으로 가져온다.** LOT 입력 후 Enter → 포커스가 실제량 칸으로
 *    넘어가는 그 순간 모달이 떴는데, 모달이 포커스를 안 가져와서 작업자가 습관대로
 *    친 숫자가 안 보이는 칸에 들어갔다. 여는 즉시 주 버튼(비파괴 '계속')으로 focus().
 *    (autofocus 속성은 hidden 요소에서 동작하지 않으므로 JS 로 해야 한다.)
 * 3) **기록된 LOT 후보 목록을 모달 안에** — 이미 브라우저에 로드된 목록(lots)을
 *    받아 .lot-suggest/.lot-suggest-item 으로 렌더한다. 누르면 채우고 닫힌다.
 * 4) **제목이 '어디에 없는지'를 말한다** — 차단이 풀려 둘 다 경고가 되었으므로 그것이
 *    유일하고 충분한 구분자다. ERP 는 이유(목록에 없음/재고 0/마이너스)별로도 다르다.
 *
 *   deps:
 *     prefix   "cont-" | ""           — element id 접두어(두 화면의 기존 id 유지)
 *     esc      (s) => string          — 자재명·LOT 이스케이프(blend_lib.esc)
 *     notify   (msg, level) => void   — 사용자 알림
 *     csrfToken () => string          — 책임자 인증 POST 의 X-CSRF 헤더
 *
 *   openInvalid({ name, lot, input, lots, onPick })  — 앞 단계 기록에 없는 반제품 LOT.
 *   openErp({ name, code, lot, reason, reasonKind, input, onVerified })  — ERP 경고.
 *
 *   bind({ onClear, onProceed })  — footer 버튼을 한 번 묶는다(중복 바인딩 가드).
 *     onClear(input)        — 'LOT 지우고 다시 입력'(차단 아님, 값 삭제는 명시 선택).
 *     onProceed(name, lot, reason, input) — '확인했습니다 · 계속'. reason 은 "" 가능.
 *
 * 보존(현장 동작 그대로):
 *   - fail-open(checkErpLot 이 fetch 실패·file_ok=false 면 통과) — 호출부에서 유지.
 *   - .erp-lot-warn 주황 테두리는 모달이 닫혀도 입력에 남는다(사라지는 토스트의 반대).
 *     차단이 없어진 반제품 쪽에도 같은 잔존 표시를 붙였다(호출부).
 *   - esc() 로 자재명·LOT 문자열 이스케이프.
 *   - 책임자 인증 POST /api/material-lots/manual-verify (body 규약 그대로).
 *   - hidden 속성 토글로만 표시/숨김(.ss-modal[hidden] CSS 규칙).
 */
(function () {
  "use strict";

  const ns = (window.IRMS = window.IRMS || {});

  // ── LOT 등록 여부 조회 결과 캐시 ────────────────────────────────
  // 예전에는 두 화면이 각자 `state.lotChecked[key] = ok` 로 **영구** 캐시했다. 모달은
  // "1차 배합 기록이 저장되었는지 확인하세요"라고 지시하는데, 작업자가 그대로 1차를
  // 저장하고 돌아와 같은 LOT 을 다시 입력해도 캐시된 '없음'이 그대로 나와 새로고침
  // 전에는 절대 풀리지 않았다 — 시킨 대로 해도 계속 막히는 창. 부정 결과에만 TTL 을
  // 주고(등록은 시간이 지나 사라지지 않으므로 긍정은 영구), 저장 성공 시 clear().
  const DEFAULT_NEGATIVE_TTL_MS = 60 * 1000;

  function createCache(opts) {
    const o = opts || {};
    const ttl = o.negativeTtlMs != null ? o.negativeTtlMs : DEFAULT_NEGATIVE_TTL_MS;
    const now = typeof o.now === "function" ? o.now : function () { return Date.now(); };
    let store = Object.create(null);
    return {
      // (자재명, LOT) → 캐시 키. 두 화면이 같은 규칙을 쓰도록 여기서 만든다.
      key(name, lot) { return String(name) + "\u0000" + String(lot); },
      // true(등록) / false(미등록, TTL 안) / undefined(미지 또는 만료 → 다시 조회)
      get(key) {
        const hit = store[key];
        if (!hit) return undefined;
        if (hit.ok) return true;
        if (now() - hit.at >= ttl) { delete store[key]; return undefined; }
        return false;
      },
      set(key, ok) { store[key] = { ok: !!ok, at: now() }; },
      clear() { store = Object.create(null); },
    };
  }

  function create(deps) {
    const d = deps || {};
    const prefix = d.prefix || "";
    const esc = d.esc || function (s) { return String(s == null ? "" : s); };
    const notify = d.notify || function () {};
    const csrfToken = d.csrfToken || function () { return ""; };
    // element id 조립 헬퍼 — 접두어(p) + 기본 id.
    const el = (id) => document.getElementById(prefix + id);

    // 진행 중 케이스("invalid"=앞 단계 기록 없음 / "erp"=ERP 경고).
    let currentCase = null;
    // ERP 경고 케이스에서 수동 LOT 추가 성공 시 호출(입력의 .erp-lot-warn 해제용).
    let pendingVerified = null;
    // 후보 목록 클릭 시 호출(호출부가 값·state 를 채운다).
    let pendingPick = null;

    function close() {
      const m = el("lot-invalid-modal");
      if (m) m.hidden = true;
    }

    function setTitle(text) {
      const t = el("lot-invalid-modal-title");
      if (t) t.textContent = text;
    }

    function setMaterial(name) {
      const m = el("lot-invalid-material");
      if (m) m.textContent = String(name == null ? "" : name);
    }

    // 모달이 뜨는 순간 포커스를 안으로 가져온다 — 안 그러면 작업자가 습관대로 친
    // 숫자가 오버레이 뒤 실제량 칸에 들어간다. 포커스는 **비파괴** 버튼('계속')에
    // 준다(Enter 습관 입력이 값을 지우지 않도록). 같은 tick 에 다른 코드가 포커스를
    // 옮길 수 있어 setTimeout 으로 한 번 더 확정한다.
    function focusPrimary() {
      const btn = el("lot-invalid-confirm");
      if (!btn || typeof btn.focus !== "function") return;
      try { btn.focus(); } catch (_e) { /* 무시 */ }
      if (typeof setTimeout === "function") {
        setTimeout(() => {
          const m = el("lot-invalid-modal");
          if (m && !m.hidden) { try { btn.focus(); } catch (_e) { /* 무시 */ } }
        }, 0);
      }
    }

    // 기록된 LOT 후보 목록(.lot-suggest 재사용, 인라인 배치). 항목을 누르면 호출부의
    // onPick 이 값을 채우고 모달이 닫힌다 — 오타가 이 창 안에서 끝난다.
    function renderSuggest(lots, onPick) {
      const box = el("lot-invalid-suggest-box");
      const list = el("lot-invalid-suggest");
      pendingPick = typeof onPick === "function" ? onPick : null;
      const items = Array.isArray(lots) ? lots : [];
      if (!box || !list || !items.length || !pendingPick) {
        if (box) box.hidden = true;
        return;
      }
      list.innerHTML = "";
      items.forEach((entry) => {
        const lot = entry && entry.lot != null ? entry.lot : entry;
        if (lot == null || lot === "") return;
        const item = document.createElement("button");
        item.type = "button";
        item.className = "lot-suggest-item";
        item.textContent = String(lot);
        if (entry && entry.total != null) {
          const suf = document.createElement("span");
          suf.className = "lot-suggest-total";
          suf.textContent = ` · ${entry.total} g`;
          item.appendChild(suf);
        }
        item.addEventListener("click", () => {
          const pick = pendingPick;
          close();
          if (pick) pick(String(lot));
        });
        list.appendChild(item);
      });
      box.hidden = false;
    }

    // ── 케이스 A: 앞 단계 배합 기록에 없는 반제품 LOT(경고 — 저장 가능) ──
    // 사유칸(선택) 노출, 인증칸 숨김. footer: [LOT 지우고 다시 입력][확인했습니다 · 계속].
    function openInvalid(opts) {
      const o = opts || {};
      currentCase = "invalid";
      pendingVerified = null;
      // 제목이 '어디에 없는지'를 말한다 — ERP 경고와 구분되는 유일한 표식.
      setTitle("앞 단계 배합 기록에 없는 LOT");
      setMaterial(o.name);
      const body = el("lot-invalid-modal-body");
      if (body) {
        body.innerHTML = ""
          + `<p><strong>입력한 로트:</strong> ${esc(o.lot)}</p>`
          + `<p>이 반제품의 완료된 배합 기록에서 이 로트를 찾지 못했습니다. `
          + `1차 배합 기록이 아직 저장되지 않았거나, 로트 번호에 오타가 있을 수 있습니다.</p>`
          + `<p class="muted small">그대로 진행해도 저장됩니다 — 진행한 사실은 기록에 남아 `
          + `나중에 1차 기록과 대조됩니다.</p>`;
      }
      renderSuggest(o.lots, o.onPick);
      const box = el("lot-override-box");
      const reason = el("lot-override-reason");
      if (reason) reason.value = "";
      if (box) box.hidden = false;   // 사유는 선택 — 접어두지 않고 바로 쓸 수 있게 편다.
      const authBox = el("erp-lot-add-box");
      if (authBox) authBox.hidden = true;
      // footer 버튼 표시: 값 삭제·계속 보이기, 인증쪽 숨기기.
      showBtn("lot-invalid-clear", true);
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
      focusPrimary();
    }

    // ERP 경고 제목 — 이유가 3가지인데 제목이 항상 같았다. 특히 '전산 반영 지연'
    // (재고 마이너스)인 경우 "등록되지 않은"이라는 제목과 본문이 서로 모순됐다.
    function erpTitle(kind) {
      if (kind === "negative") return "ERP 재고가 마이너스인 LOT";
      if (kind === "zero") return "ERP 재고가 0인 LOT";
      return "ERP 원재료 목록에 없는 LOT";
    }

    // ── 케이스 B: ERP 원재료 LOT(경고, 값 유지) ─────────────────────
    // 인증칸 자리만 준비(기본 숨김 — [책임자 LOT 추가하기] 로 펼침). 사유칸 숨김.
    function openErp(opts) {
      const o = opts || {};
      currentCase = "erp";
      pendingVerified = typeof o.onVerified === "function" ? o.onVerified : null;
      setTitle(erpTitle(o.reasonKind));
      setMaterial(o.name);
      const body = el("lot-invalid-modal-body");
      if (body) {
        body.innerHTML = ""
          + `<p><strong>품목코드:</strong> ${esc(o.code)}</p>`
          + `<p><strong>입력한 로트:</strong> ${esc(o.lot)}</p>`
          + `<p>${esc(o.reason)}</p>`
          + `<p class="muted small">그대로 진행해도 저장됩니다 — 로트를 한 번 더 확인하세요.</p>`;
      }
      const suggestBox = el("lot-invalid-suggest-box");
      if (suggestBox) suggestBox.hidden = true;   // ERP 원료는 후보 목록이 없다.
      pendingPick = null;
      const authBox = el("erp-lot-add-box");
      const err = el("erp-lot-add-error");
      if (err) { err.textContent = ""; err.hidden = true; }
      if (authBox) authBox.hidden = true;
      const reasonBox = el("lot-override-box");
      if (reasonBox) reasonBox.hidden = true;
      showBtn("erp-lot-add-submit", false);
      // footer 버튼 표시: 인증 토글·계속 보이기, 값 삭제(반제품 전용) 숨기기.
      showBtn("erp-lot-add-toggle", true);
      showBtn("lot-invalid-clear", false);
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
      focusPrimary();
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
    // onClear(input)        : 'LOT 지우고 다시 입력' — 화면별 LOT 값·state 비우기.
    // onProceed(name,lot,reason,input) : '확인했습니다 · 계속' — reason 은 "" 일 수 있다.
    let bound = false;
    function bind(callbacks) {
      if (bound) return;
      bound = true;
      const cb = callbacks || {};
      const onClear = typeof cb.onClear === "function" ? cb.onClear : null;
      const onProceed = typeof cb.onProceed === "function" ? cb.onProceed : null;

      // 'LOT 지우고 다시 입력' — 반제품 케이스 전용(파괴적, accent 아님).
      const clearBtn = el("lot-invalid-clear");
      if (clearBtn) clearBtn.addEventListener("click", () => {
        const m = el("lot-invalid-modal");
        const input = m && m._lotInput;
        close();
        if (onClear && input) onClear(input);
        else if (input) input.focus();
      });

      // '확인했습니다 · 계속' — 두 케이스 공통 주 버튼(비파괴).
      //   반제품: 사유(선택)를 담아 onProceed 호출 → 그 (자재,LOT) 조합을 통과 처리하고
      //           **사유가 비어 있어도** 구조화 기록을 남긴다(대사 신호 보존).
      //   ERP  : 값 유지, 입력으로 포커스 복귀(경고 테두리는 입력에 그대로 남는다).
      const confirmBtn = el("lot-invalid-confirm");
      if (confirmBtn) confirmBtn.addEventListener("click", () => {
        const m = el("lot-invalid-modal");
        const wasInvalid = currentCase === "invalid";
        if (wasInvalid) {
          const reason = el("lot-override-reason");
          const text = (reason && reason.value.trim()) || "";
          const name = m && m._lotName, lot = m && m._lotValue, input = m && m._lotInput;
          if (onProceed) onProceed(name, lot, text, input);
          close();
          if (input) input.focus();
          notify("확인하고 진행합니다 — 이 로트는 기록에 남아 나중에 대조됩니다.", "warn");
          return;
        }
        const erpInput = m && m._erpInput;
        close();
        // 값을 유지한다 — select() 를 하지 않는 이유: 선택 상태에서 다음 타건이 값을
        // 통째로 지운다('계속'과 모순).
        if (erpInput) erpInput.focus();
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

  ns.blendLotModal = { create, createCache };
})();

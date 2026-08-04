/**
 * blend_lot_modal.js — LOT 검사 모달 공용 컴포넌트 옵션 처리 회귀 테스트.
 *
 * 네 벌이던 LOT 검사 모달을 하나로 합친 뒤, '앞 단계 기록 없음(invalid)'과 'ERP 경고(erp)'
 * 두 케이스가 옵션에 따라 footer 버튼·보조칸 표시를 올바르게 바꾸는지, 그리고 화면별 콜백
 * (onClear=값 비우기 / onProceed=확인 기록)이 누락돼도 터지지 않는지를 잠근다.
 *
 * 2026-08-04 차단 해제 이후의 계약:
 *  - 반제품 LOT 은 더 이상 차단이 아니다. 사유는 **선택** — 빈 사유로도 onProceed 가 불리고,
 *    그래야 "사유 없이 진행한 건"도 대사용 기록으로 남는다(이게 이 개편의 핵심 제약).
 *  - 제목이 '어디에 없는지'를 말한다(앞 단계 배합 기록 / ERP 원재료 목록·재고).
 *  - 기록된 LOT 후보 목록이 창 안에 뜨고, 누르면 채우고 닫힌다.
 *
 * 브라우저 바인딩(document/fetch)이 있어 vm 컨텍스트에 최소 DOM mock 을 심어
 * 모듈을 로드한다(blend_drafts.test.js 의 vm 격리 패턴).
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// ── 최소 DOM mock ──────────────────────────────────────────────
// id → stub element. hidden/value/classList/addEventListener/focus/select.
function makeDom(prefix) {
  const byId = {};
  const E = (id) => {
    if (byId[id]) return byId[id];
    const el = {
      id,
      hidden: false,
      value: "",
      title: "",
      textContent: "",
      innerHTML: "",
      dataset: {},
      children: [],
      _listeners: {},
      classList: { add() {}, remove() {}, contains() { return false; } },
      focus() {},
      select() {},
      appendChild(child) { this.children.push(child); },
      addEventListener(t, fn) { this._listeners[t] = fn; },
    };
    byId[id] = el;
    return el;
  };
  const document = {
    getElementById: (fullId) => {
      // prefix 가 붙은 id 만 노출 — 모듈이 prefix + 기본id 로 조회.
      if (fullId.startsWith(prefix)) return E(fullId);
      return E(fullId);  // prefix "" 일 때는 그대로
    },
    createElement: () => ({
      className: "", textContent: "", innerHTML: "", type: "", hidden: false,
      dataset: {}, style: {}, children: [],
      classList: { add() {}, remove() {} },
      appendChild(child) { this.children.push(child); },
      addEventListener(t, fn) { this._listeners = this._listeners || {}; this._listeners[t] = fn; },
    }),
  };
  return { document, byId };
}

function loadModule(prefix) {
  const { document, byId } = makeDom(prefix);
  const win = { IRMS: {} };
  win.window = win;
  win.document = document;
  const calls = { notify: [], verified: 0 };
  win.fetch = async () => ({ ok: true, json: async () => ({}) });
  const context = {
    window: win,
    document,
    fetch: win.fetch,
    console,
    setTimeout,
  };
  context.window = win;  // 순환: 모듈이 window.IRMS 에 붙는다
  const code = fs.readFileSync("static/js/blend_lot_modal.js", "utf8");
  vm.runInNewContext(code, context, { filename: "blend_lot_modal.js" });
  const api = win.IRMS.blendLotModal.create({
    prefix,
    esc: (s) => String(s == null ? "" : s),
    notify: (m, lvl) => calls.notify.push({ m, lvl }),
    csrfToken: () => "tok",
  });
  return { api, byId, calls, document };
}

test("앞 단계 기록 없음(openInvalid) — 값 삭제·계속 보이고, 인증 토글·추가 숨김", () => {
  const { api, byId } = loadModule("");
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  assert.strictEqual(byId["lot-invalid-clear"].hidden, false, "LOT 지우고 다시 입력 보임");
  assert.strictEqual(byId["erp-lot-add-toggle"].hidden, true, "책임자 LOT 추가 숨김");
  assert.strictEqual(byId["erp-lot-add-submit"].hidden, true, "추가 버튼 숨김");
  assert.strictEqual(byId["lot-invalid-confirm"].hidden, false, "확인했습니다 · 계속 보임");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, false, "모달 표시됨");
});

test("ERP 경고(openErp) — 인증 토글·계속 보이고, 값 삭제(반제품 전용) 숨김", () => {
  const { api, byId } = loadModule("");
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "재고 0", input: null });
  assert.strictEqual(byId["erp-lot-add-toggle"].hidden, false, "책임자 LOT 추가 보임");
  assert.strictEqual(byId["lot-invalid-confirm"].hidden, false, "계속 보임");
  assert.strictEqual(byId["lot-invalid-clear"].hidden, true, "값 삭제(반제품 전용) 숨김");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, false, "모달 표시됨");
});

test("제목이 '어디에 없는지'를 말한다 — 두 케이스의 유일하고 충분한 구분자", () => {
  const { api, byId } = loadModule("");
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  assert.match(byId["lot-invalid-modal-title"].textContent, /앞 단계 배합 기록/);
  api.openErp({ name: "원료", code: "R1", lot: "L1", reason: "x", reasonKind: "missing", input: null });
  assert.match(byId["lot-invalid-modal-title"].textContent, /ERP 원재료 목록/);
});

test("ERP 경고 제목이 이유(목록에 없음/재고 0/마이너스)별로 다르다", () => {
  const { api, byId } = loadModule("");
  const title = () => byId["lot-invalid-modal-title"].textContent;
  api.openErp({ name: "원료", code: "R1", lot: "L1", reason: "x", reasonKind: "negative", input: null });
  const neg = title();
  api.openErp({ name: "원료", code: "R1", lot: "L1", reason: "x", reasonKind: "zero", input: null });
  const zero = title();
  api.openErp({ name: "원료", code: "R1", lot: "L1", reason: "x", reasonKind: "missing", input: null });
  const missing = title();
  assert.notStrictEqual(neg, zero);
  assert.notStrictEqual(zero, missing);
  assert.match(neg, /마이너스/, "전산 반영 지연 케이스는 '등록되지 않은' 과 모순되면 안 된다");
});

test("자재명은 본문 안내와 섞이지 않고 별도 큰 줄(.add-weigh-material)에 들어간다", () => {
  const { api, byId } = loadModule("");
  api.openInvalid({ name: "블루안료 A", lot: "L9", input: null });
  assert.strictEqual(byId["lot-invalid-material"].textContent, "블루안료 A");
});

test("'LOT 지우고 다시 입력' 은 onClear 로 값을 지운다", () => {
  const { api, byId } = loadModule("");
  let cleared = null;
  const fakeInput = { value: "L9", focus() {}, dataset: { idx: "2" } };
  api.bind({ onClear: (input) => { cleared = input; input.value = ""; } });
  api.openInvalid({ name: "안료", lot: "L9", input: fakeInput });
  byId["lot-invalid-clear"]._listeners.click();
  assert.strictEqual(cleared, fakeInput, "onClear 가 입력을 받았다");
  assert.strictEqual(fakeInput.value, "", "값이 지워졌다");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘");
});

test("ERP '계속' 은 onClear 를 부르지 않는다(값 유지 — keep-on-continue)", () => {
  const { api, byId } = loadModule("");
  let cleared = 0;
  const fakeInput = { value: "L1", focus() {}, select() {} };
  api.bind({ onClear: () => { cleared += 1; } });
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "x", input: fakeInput });
  byId["lot-invalid-confirm"]._listeners.click();
  assert.strictEqual(cleared, 0, "경고 케이스는 onClear 를 부르지 않는다(값 유지)");
  assert.strictEqual(fakeInput.value, "L1", "값이 남아있다");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘");
});

test("'확인했습니다 · 계속' 은 onProceed(name, lot, reason) 를 부른다", () => {
  const { api, byId } = loadModule("");
  let captured = null;
  api.bind({ onProceed: (name, lot, reason) => { captured = { name, lot, reason }; } });
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  // 사유칸은 접혀있지 않다 — 선택이므로 바로 쓸 수 있게 펴져 있다.
  assert.strictEqual(byId["lot-override-box"].hidden, false, "사유칸이 처음부터 보인다");
  byId["lot-override-reason"].value = "1차 기록 종이만 있음";
  byId["lot-invalid-confirm"]._listeners.click();
  assert.deepStrictEqual(captured, { name: "안료", lot: "L9", reason: "1차 기록 종이만 있음" });
});

test("사유가 비어도 진행되고 onProceed 가 불린다 — 대사 신호를 버리지 않는다", () => {
  const { api, byId, calls } = loadModule("");
  let called = 0;
  let seenReason = null;
  api.bind({ onProceed: (name, lot, reason) => { called += 1; seenReason = reason; } });
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  byId["lot-override-reason"].value = "";
  byId["lot-invalid-confirm"]._listeners.click();
  assert.strictEqual(called, 1, "빈 사유여도 확인 기록이 남아야 한다(차단 해제 핵심 제약)");
  assert.strictEqual(seenReason, "", "사유는 빈 문자열로 전달된다");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘 — 저장을 막지 않는다");
  assert.ok(!calls.notify.some((c) => c.lvl === "error"), "사유 미기재는 오류가 아니다");
});

test("기록된 LOT 후보를 누르면 onPick 으로 채우고 닫힌다(오타가 창 안에서 끝난다)", () => {
  const { api, byId } = loadModule("");
  let picked = null;
  api.bind({});
  api.openInvalid({
    name: "안료", lot: "L9", input: null,
    lots: [{ lot: "L2601", total: 1000 }, { lot: "L2602" }],
    onPick: (v) => { picked = v; },
  });
  assert.strictEqual(byId["lot-invalid-suggest-box"].hidden, false, "후보 상자 표시");
  const items = byId["lot-invalid-suggest"].children;
  assert.strictEqual(items.length, 2, "후보 2건 렌더");
  items[0]._listeners.click();
  assert.strictEqual(picked, "L2601", "누른 LOT 가 채워진다");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "누르면 닫힌다");
});

test("후보가 없으면 후보 상자를 띄우지 않는다", () => {
  const { api, byId } = loadModule("");
  api.openInvalid({ name: "안료", lot: "L9", input: null, lots: [], onPick: () => {} });
  assert.strictEqual(byId["lot-invalid-suggest-box"].hidden, true);
});

test("옵션 콜백이 없어도 bind/open/close 가 터지지 않는다", () => {
  const { api, byId } = loadModule("");
  assert.doesNotThrow(() => api.bind({}), "빈 콜백으로 bind");
  // onClear 없는 '지우고 다시 입력' → input.focus 폴백 경로도 예외 없음.
  const fakeInput = { value: "L9", focus() {}, dataset: {} };
  assert.doesNotThrow(() => api.openInvalid({ name: "x", lot: "L9", input: fakeInput }));
  assert.doesNotThrow(() => byId["lot-invalid-clear"]._listeners.click());
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true);
});

test("cont- 접두어 — 다중 계량 모달 id 가 prefix 로 조회된다", () => {
  const { api, byId } = loadModule("cont-");
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  assert.strictEqual(byId["cont-lot-invalid-modal"].hidden, false, "cont- 모달 표시");
  assert.strictEqual(byId["cont-lot-invalid-clear"].hidden, false);
  // 접두어 없는 id 는 생성되지 않았다(두 화면이 한 모듈을 쓰되 id 충돌 없음).
  assert.ok(!byId["lot-invalid-modal"], "접두어 없는 id 는 별도 미생성");
});

test("openErp 의 onVerified 가 있으면 수동 LOT 추가 성공 시 호출된다", async () => {
  const { api, byId } = loadModule("");
  let verified = 0;
  api.bind({});
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "x", input: null, onVerified: () => { verified += 1; } });
  // 인증칸 펼치기 → 추가 클릭 → fetch(ok) → onVerified + 닫기
  byId["erp-lot-add-toggle"]._listeners.click();
  byId["erp-add-username"].value = "kim";
  byId["erp-add-password"].value = "pw";
  await byId["erp-lot-add-submit"]._listeners.click();
  assert.strictEqual(verified, 1, "인증 성공 시 onVerified 호출");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘");
});

test("인증 빈 필드 → onVerified 미호출 + 인라인 에러", async () => {
  const { api, byId } = loadModule("");
  let verified = 0;
  api.bind({});
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "x", input: null, onVerified: () => { verified += 1; } });
  byId["erp-lot-add-toggle"]._listeners.click();
  byId["erp-add-username"].value = "";
  byId["erp-add-password"].value = "";
  await byId["erp-lot-add-submit"]._listeners.click();
  assert.strictEqual(verified, 0, "빈 필드면 미호출");
  assert.strictEqual(byId["erp-lot-add-error"].hidden, false, "인라인 에러 표시");
});

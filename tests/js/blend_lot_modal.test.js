/**
 * blend_lot_modal.js — LOT 검사 모달 공용 컴포넌트 옵션 처리 회귀 테스트.
 *
 * 네 벌이던 LOT 검사 모달을 하나로 합친 뒤, '차단(invalid)'과 '경고(erp)' 두 케이스가
 * 옵션에 따라 footer 버튼·보조칸 표시를 올바르게 바꾸는지, 그리고 화면별 콜백
 * (onClear=값 비우기 / onProceed=사유 보관)이 누락돼도 터지지 않는지를 잠근다.
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
      dataset: {},
      _listeners: {},
      classList: { add() {}, remove() {}, contains() { return false; } },
      focus() {},
      select() {},
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

test("차단 케이스(openInvalid) — 사유 진행 버튼 보이고, 인증 토글·추가 숨김", () => {
  const { api, byId } = loadModule("");
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  assert.strictEqual(byId["lot-invalid-proceed"].hidden, false, "사유 적고 진행 보임");
  assert.strictEqual(byId["erp-lot-add-toggle"].hidden, true, "책임자 LOT 추가 숨김");
  assert.strictEqual(byId["erp-lot-add-submit"].hidden, true, "추가 버튼 숨김");
  assert.strictEqual(byId["lot-invalid-confirm"].hidden, false, "다시 확인 보임");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, false, "모달 표시됨");
});

test("경고 케이스(openErp) — 인증 토글·다시 확인 보이고, 사유 진행 숨김(값 유지)", () => {
  const { api, byId } = loadModule("");
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "재고 0", input: null });
  assert.strictEqual(byId["erp-lot-add-toggle"].hidden, false, "책임자 LOT 추가 보임");
  assert.strictEqual(byId["lot-invalid-confirm"].hidden, false, "다시 확인 보임");
  assert.strictEqual(byId["lot-invalid-proceed"].hidden, true, "사유 적고 진행(차단 전용) 숨김");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, false, "모달 표시됨");
});

test("차단 '다시 확인' 은 onClear 로 값을 지운다(clear-on-decline)", () => {
  const { api, byId, document } = loadModule("");
  let cleared = null;
  const fakeInput = { value: "L9", focus() {}, dataset: { idx: "2" } };
  api.bind({ onClear: (input) => { cleared = input; input.value = ""; } });
  api.openInvalid({ name: "안료", lot: "L9", input: fakeInput });
  byId["lot-invalid-confirm"]._listeners.click();
  assert.strictEqual(cleared, fakeInput, "onClear 가 입력을 받았다");
  assert.strictEqual(fakeInput.value, "", "값이 지워졌다");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘");
});

test("경고 '다시 확인' 은 onClear 를 부르지 않는다(값 유지 — keep-on-decline)", () => {
  const { api, byId } = loadModule("");
  let cleared = 0;
  const fakeInput = { value: "L1", focus() {}, select() {} };
  api.bind({ onClear: () => { cleared += 1; } });
  api.openErp({ name: "원료", code: "R001", lot: "L1", reason: "x", input: fakeInput });
  byId["lot-invalid-confirm"]._listeners.click();
  assert.strictEqual(cleared, 0, "경고 케이스는 onClear 를 부르지 않는다(값 유지)");
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true, "모달 닫힘");
});

test("'사유 적고 진행' 은 onProceed(name, lot, reason) 를 부른다", () => {
  const { api, byId } = loadModule("");
  let captured = null;
  api.bind({ onProceed: (name, lot, reason) => { captured = { name, lot, reason }; } });
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  // 1클릭: 사유칸 표시
  byId["lot-invalid-proceed"]._listeners.click();
  assert.strictEqual(byId["lot-override-box"].hidden, false, "사유칸 펼쳐짐");
  // 사유 입력 후 2클릭
  byId["lot-override-reason"].value = "1차 기록 종이만 있음";
  byId["lot-invalid-proceed"]._listeners.click();
  assert.deepStrictEqual(captured, { name: "안료", lot: "L9", reason: "1차 기록 종이만 있음" });
});

test("빈 사유로 진행 시 onProceed 미호출 + 에러 알림(사유 필수)", () => {
  const { api, byId, calls } = loadModule("");
  let called = 0;
  api.bind({ onProceed: () => { called += 1; } });
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  byId["lot-invalid-proceed"]._listeners.click();   // 사유칸 펼침
  byId["lot-override-reason"].value = "";
  byId["lot-invalid-proceed"]._listeners.click();   // 빈 사유 → 거부
  assert.strictEqual(called, 0, "빈 사유면 onProceed 미호출");
  assert.ok(calls.notify.some((c) => /진행 사유/.test(c.m)), "사유 입력 에러 알림");
});

test("옵션 콜백이 없어도 bind/open/close 가 터지지 않는다", () => {
  const { api, byId } = loadModule("");
  assert.doesNotThrow(() => api.bind({}), "빈 콜백으로 bind");
  // onClear 없는 차단 '다시 확인' → input.focus 폴백 경로도 예외 없음.
  const fakeInput = { value: "L9", focus() {}, dataset: {} };
  assert.doesNotThrow(() => api.openInvalid({ name: "x", lot: "L9", input: fakeInput }));
  assert.doesNotThrow(() => byId["lot-invalid-confirm"]._listeners.click());
  assert.strictEqual(byId["lot-invalid-modal"].hidden, true);
});

test("cont- 접두어 — 다중 계량 모달 id 가 prefix 로 조회된다", () => {
  const { api, byId } = loadModule("cont-");
  api.openInvalid({ name: "안료", lot: "L9", input: null });
  assert.strictEqual(byId["cont-lot-invalid-modal"].hidden, false, "cont- 모달 표시");
  assert.strictEqual(byId["cont-lot-invalid-proceed"].hidden, false);
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

/**
 * blend_lot_bugs.test.js — LOT 검사 모달 기능 버그 2건 회귀 잠금.
 *
 * B-1 "시킨 대로 해도 계속 막힌다": 미등록 LOT 조회의 '없음' 결과가 영구 캐시되고
 *     어디서도 초기화되지 않았다(state.lotChecked). 모달은 "1차 배합 기록이
 *     저장되었는지 확인하세요"라고 지시하는데, 실제로 1차를 저장하고 돌아와 같은
 *     LOT 을 다시 입력해도 캐시된 '없음'이 그대로 나와 새로고침 전에는 풀리지 않았다.
 *     → 부정 결과에 TTL + 저장 성공 시 clear().
 *
 * B-2 "모달이 떠도 키보드 포커스가 오버레이 뒤에 있다": LOT 입력 후 Enter → 포커스가
 *     실제량 칸으로 넘어가는 순간 모달이 뜨는데 모달이 포커스를 가져오지 않았다.
 *     작업자가 습관대로 숫자를 치면 안 보이는 칸에 값이 들어간다.
 *     → 모달 열 때 주 버튼으로 focus(). (autofocus 속성은 hidden 요소에서 무동작)
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// ── 최소 DOM mock (blend_lot_modal.test.js 와 동일 패턴 + focus 카운트) ──
function loadModule(prefix) {
  const byId = {};
  const focusLog = [];
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
      _listeners: {},
      classList: { add() {}, remove() {}, contains() { return false; } },
      focus() { focusLog.push(id); },
      select() {},
      appendChild() {},
      addEventListener(t, fn) { this._listeners[t] = fn; },
    };
    byId[id] = el;
    return el;
  };
  const document = {
    getElementById: (fullId) => E(fullId),
    createElement: () => ({
      className: "", textContent: "", innerHTML: "", type: "", hidden: false,
      dataset: {}, style: {},
      classList: { add() {}, remove() {} },
      appendChild() {}, addEventListener() {},
    }),
  };
  const win = { IRMS: {} };
  win.window = win;
  win.document = document;
  win.fetch = async () => ({ ok: true, json: async () => ({}) });
  const context = { window: win, document, fetch: win.fetch, console, setTimeout };
  context.window = win;
  const code = fs.readFileSync("static/js/blend_lot_modal.js", "utf8");
  vm.runInNewContext(code, context, { filename: "blend_lot_modal.js" });
  const api = win.IRMS.blendLotModal.create({
    prefix,
    esc: (s) => String(s == null ? "" : s),
    notify: () => {},
    csrfToken: () => "tok",
  });
  return { api, byId, focusLog, ns: win.IRMS.blendLotModal };
}

// ── B-1: 미등록(부정) 조회 결과의 영구 캐시 ─────────────────────────────
test("B-1 부정 캐시에 TTL 이 있다 — 1차 기록을 저장하고 돌아오면 다시 조회된다", () => {
  const { ns } = loadModule("");
  let clock = 1_000;
  const cache = ns.createCache({ negativeTtlMs: 60_000, now: () => clock });
  const key = cache.key("반제품A", "L2601");
  cache.set(key, false);
  assert.strictEqual(cache.get(key), false, "직후에는 캐시된 '없음' 을 그대로 쓴다");
  clock += 59_000;
  assert.strictEqual(cache.get(key), false, "TTL 안이면 유지");
  clock += 2_000;                      // 총 61초 경과 → 만료
  assert.strictEqual(cache.get(key), undefined,
    "TTL 초과 → 미지(undefined) 로 되돌아가 서버에 다시 물어본다");
});

test("B-1 등록(긍정) 결과는 만료되지 않는다 — 불필요한 재조회 방지", () => {
  const { ns } = loadModule("");
  let clock = 0;
  const cache = ns.createCache({ negativeTtlMs: 1_000, now: () => clock });
  const key = cache.key("반제품A", "L2601");
  cache.set(key, true);
  clock += 10_000_000;
  assert.strictEqual(cache.get(key), true, "긍정 결과는 TTL 대상이 아니다");
});

test("B-1 clear() 로 저장 성공 시 캐시를 통째로 비운다", () => {
  const { ns } = loadModule("");
  const cache = ns.createCache({});
  const key = cache.key("반제품A", "L2601");
  cache.set(key, false);
  cache.clear();
  assert.strictEqual(cache.get(key), undefined, "clear 후에는 미지");
});

test("B-1 두 화면 모두 공용 캐시를 쓰고, 영구 부정 캐시 코드가 남아있지 않다", () => {
  for (const file of ["static/js/blend.js", "static/js/blend_continuous.js"]) {
    const src = fs.readFileSync(file, "utf8");
    assert.ok(/blendLotModal\.createCache\(/.test(src),
      `${file} 이 공용 LOT 조회 캐시(createCache)를 쓴다`);
    assert.ok(!/state\.lotChecked\[key\]\s*=/.test(src),
      `${file} 에 만료 없는 부정 캐시 대입이 남아있지 않다`);
    assert.ok(/lotCheckCache\.clear\(\)/.test(src),
      `${file} 이 저장 성공 시 캐시를 비운다`);
  }
});

// ── B-2: 모달이 포커스를 가져오지 않는다 ────────────────────────────────
test("B-2 openInvalid 는 주 버튼으로 포커스를 가져온다(오버레이 뒤 입력 방지)", () => {
  const { api, focusLog } = loadModule("");
  api.openInvalid({ name: "반제품A", lot: "L2601", input: null });
  assert.ok(focusLog.includes("lot-invalid-confirm"),
    `모달의 주 버튼에 focus() 해야 한다 — 실제 focus 호출: ${JSON.stringify(focusLog)}`);
});

test("B-2 openErp 도 주 버튼으로 포커스를 가져온다", () => {
  const { api, focusLog } = loadModule("cont-");
  api.openErp({ name: "원료", code: "R1", lot: "L1", reason: "재고 0", input: null });
  assert.ok(focusLog.includes("cont-lot-invalid-confirm"),
    `경고 모달도 포커스를 가져와야 한다 — 실제: ${JSON.stringify(focusLog)}`);
});

test("B-2 파괴적 버튼(LOT 지우고 다시 입력)에는 포커스가 가지 않는다", () => {
  const { api, focusLog } = loadModule("");
  api.openInvalid({ name: "반제품A", lot: "L2601", input: null });
  assert.ok(!focusLog.includes("lot-invalid-clear"),
    "Enter 습관 입력이 값을 지우면 안 된다 — 포커스는 비파괴 버튼에");
});

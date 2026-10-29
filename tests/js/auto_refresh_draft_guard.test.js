/**
 * 새 배포 자동 새로고침이 계량 중인 배합 화면을 새로고침하지 않는지 — 회귀 테스트.
 *
 * 결함(2026-10-29 확인): auto-refresh.js 는 옛 초안 키(irms.blend.draft · irms.blend.cont.draft)만
 *   보고 '배합 진행 중'을 판단했다. 초안이 슬롯 키(irms.blend.drafts · irms.blend.cont.drafts)로
 *   옮겨 간 뒤 이 검사는 늘 '없음'이 되어, 배포 직후 30초만 손을 떼도 계량 중 화면이
 *   새로고침됐다(현장 신고: 이어서 하던 배합이 가끔 기록에 없다).
 *
 * 고친 뒤: 배합 화면이 IRMS.blendDraftActive() 로 직접 답하고, auto-refresh 는 그 답을 따른다.
 * 여기서는 auto-refresh.js 를 가짜 window·타이머 위에서 실제로 돌려 확인한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..", "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "js", "common", "auto-refresh.js"), "utf8");

/** auto-refresh.js 를 띄우고, 버전이 바뀐 뒤 무활동 시간이 흐르게 한다. 새로고침 여부를 돌려준다. */
async function runScenario({ hook, storage = {} }) {
  let now = 1_000_000;
  const timers = [];
  let reloaded = false;
  let version = "v1";
  const store = new Map(Object.entries(storage));
  const window = {
    IRMS: hook === undefined ? undefined : { blendDraftActive: hook },
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null) },
    location: { reload: () => { reloaded = true; } },
    addEventListener: () => {},
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearInterval: (id) => { if (timers[id - 1]) timers[id - 1].fn = () => {}; },
  };
  const document = {
    readyState: "complete",
    getElementById: () => null,
    createElement: () => ({ style: {}, setAttribute() {} }),
    body: { appendChild() {} },
  };
  const fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ version }) });
  const ctx = { window, document, fetch, Date: { now: () => now, parse: Date.parse } };
  vm.runInNewContext(SRC, ctx);
  await new Promise((r) => setImmediate(r)); // 최초 기준값(v1)
  version = "v2";
  timers[0].fn(); // 폴링 1회 → 새 버전 감지 → 안전 새로고침 예약
  await new Promise((r) => setImmediate(r));
  now += 60_000; // 1분 무활동
  timers.slice(1).forEach((t) => t.fn()); // 안전 게이트 검사
  return reloaded;
}

const SLOT_DRAFT = JSON.stringify([{ id: "s1", savedAt: new Date().toISOString(), data: {} }]);

test("배합 화면이 진행 중이라고 답하면 새로고침하지 않는다", async () => {
  assert.strictEqual(await runScenario({ hook: () => true }), false);
});

test("슬롯 키에만 초안이 있어도(옛 키 없음) 화면이 진행 중이면 새로고침하지 않는다 — 결함 재현", async () => {
  const reloaded = await runScenario({
    hook: () => true,
    storage: { "irms.blend.cont.drafts": SLOT_DRAFT },
  });
  assert.strictEqual(reloaded, false);
});

test("배합 화면이 진행 중 아님이면 무활동 뒤 새로고침한다", async () => {
  assert.strictEqual(await runScenario({ hook: () => false }), true);
});

test("훅이 예외를 던지면 새로고침을 미룬다", async () => {
  assert.strictEqual(await runScenario({ hook: () => { throw new Error("x"); } }), false);
});

test("훅이 없는 화면은 진행 중 배합이 없으므로 무활동 뒤 새로고침한다", async () => {
  assert.strictEqual(await runScenario({ hook: undefined }), true);
});

test("두 배합 화면이 blendDraftActive 를 초안 슬롯·저장 중 기준으로 노출한다", () => {
  for (const name of ["blend.js", "blend_continuous.js"]) {
    const src = fs.readFileSync(path.join(ROOT, "static", "js", name), "utf8");
    assert.match(
      src,
      /window\.IRMS\.blendDraftActive = \(\) => Boolean\(state\.draftSlotId\) \|\| _saving;/,
      `${name} 에 blendDraftActive 훅이 없다`,
    );
  }
});

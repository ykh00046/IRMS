const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// blend_lib.js 는 브라우저 전역(window/document/setInterval/Date)에 의존한다.
// vm 격리 컨텍스트에 최소 스텁을 심어 로드하고, createIdleLogout 의 타이밍 동작을
// 제어 가능한 가짜 시계/인터벌/이벤트로 검증한다. (로드 시점에는 window 만 필요 —
// document/Date/setInterval 은 함수 호출 시에만 쓰인다.)
function makeEnv() {
  const state = {
    clock: 1_000_000,
    intervalCb: null,       // setInterval 로 등록된 tick
    intervalCleared: false,
    activityHandler: null,  // document.addEventListener 로 등록된 onActivity
    locationHref: null,
    log: [],
  };

  const fakeDocument = {
    addEventListener: (_ev, fn) => { state.activityHandler = fn; },
    removeEventListener: () => {},
  };

  const win = {};
  win.window = win;
  const loc = {};
  Object.defineProperty(loc, "href", {
    get() { return state.locationHref; },
    set(v) { state.locationHref = v; state.log.push("redirect:" + v); },
  });
  win.location = loc;

  const context = {
    console,
    Date: { now: () => state.clock },
    setInterval: (fn) => { state.intervalCb = fn; return 42; },
    clearInterval: () => { state.intervalCleared = true; state.intervalCb = null; },
    document: fakeDocument,
    window: win,
  };

  const code = fs.readFileSync("static/js/blend_lib.js", "utf8");
  vm.runInNewContext(code, context, { filename: "blend_lib.js" });
  return { state, blendLib: win.IRMS.blendLib };
}

const flush = () => new Promise((r) => setImmediate(r));

test("arm() no-ops when isActive() is false", () => {
  const { state, blendLib } = makeEnv();
  const idle = blendLib.createIdleLogout({
    isActive: () => false,
    minutes: 1,
    checkIntervalMs: 1000,
  });
  idle.arm();
  // 세션 없음 → 인터벌을 걸지 않는다(무장 안 함).
  assert.equal(state.intervalCb, null);
});

test("on expiry: saveDraft → request(logout, POST) → redirect, in order", async () => {
  const { state, blendLib } = makeEnv();
  const log = state.log;
  const idle = blendLib.createIdleLogout({
    isActive: () => true,
    minutes: 1,              // idleMs = 60_000
    checkIntervalMs: 1000,
    saveDraft: () => log.push("save"),
    request: (path, init) => {
      log.push("request:" + path + ":" + init.method);
      return Promise.resolve();
    },
    redirectTo: "/",
    logoutPath: "/blend/session/logout",
    notify: () => {},
  });

  idle.arm();
  assert.ok(state.intervalCb, "armed → interval registered");

  // 유휴 임계 초과 후 tick 이 돌면 만료된다.
  state.clock += 60_001;
  state.intervalCb();      // tick() → expire() (async)
  await flush();

  assert.deepEqual(log, ["save", "request:/blend/session/logout:POST", "redirect:/"]);
  assert.equal(state.locationHref, "/");
  assert.equal(state.intervalCleared, true, "disarm cleared the interval");
});

test("activity before threshold prevents expiry", async () => {
  const { state, blendLib } = makeEnv();
  const log = state.log;
  const idle = blendLib.createIdleLogout({
    isActive: () => true,
    minutes: 1,              // idleMs = 60_000
    checkIntervalMs: 1000,
    throttleMs: 0,           // 모든 활동을 즉시 기록
    saveDraft: () => log.push("save"),
    request: () => { log.push("request"); return Promise.resolve(); },
    redirectTo: "/",
    notify: () => {},
  });

  idle.arm();
  assert.ok(state.activityHandler, "activity handler registered");

  state.clock += 50_000;     // 임계 미만
  state.activityHandler();   // 활동 발생 → lastActivity 를 현재 시각으로 갱신
  state.clock += 50_000;     // arm 이후 100s 지났지만 활동 이후로는 50s
  state.intervalCb();        // tick → 만료 안 됨
  await flush();

  assert.equal(state.locationHref, null, "활동이 있었으므로 만료되면 안 된다");
  assert.deepEqual(log, []);

  // 대조: 활동 없이 임계를 넘기면 이번엔 만료된다.
  state.clock += 60_001;
  state.intervalCb();
  await flush();
  assert.equal(state.locationHref, "/", "임계 초과 + 무활동 → 만료");
});

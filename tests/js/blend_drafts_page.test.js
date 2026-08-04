const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// blend_drafts_page.js 는 "작성 중 배합" 화면 컨트롤러다. 최소 DOM 스텁 + 가짜
// localStorage + 가짜 request 로 렌더 경로(목록/빈 상태/변경 배지)와 버튼 동작
// (이어서 하기 → sessionStorage 전달 + 이동 / 삭제 → 슬롯 제거)을 검증한다.
function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

function makeElement(id) {
  return {
    id,
    hidden: false,
    innerHTML: "",
    dataset: {},
    _listeners: {},
    addEventListener(type, fn) { this._listeners[type] = fn; },
    querySelector() { return null; },
  };
}

function makeEnv(options) {
  const opts = options || {};
  const elements = new Map();
  const ids = [
    "drafts-loading", "drafts-list-panel", "drafts-empty-panel", "drafts-body",
  ];
  ids.forEach((id) => elements.set(id, makeElement(id)));

  const state = {
    notices: [],
    navigated: null,
    confirmAnswer: opts.confirmAnswer !== false,
    requests: [],
  };

  const localStorage = makeStorage();
  const sessionStorage = makeStorage();

  const win = {
    localStorage,
    sessionStorage,
    location: { assign(url) { state.navigated = url; } },
    confirm() { return state.confirmAnswer; },
  };
  win.window = win;
  win.IRMS = {
    _core: {
      request(path) {
        state.requests.push(path);
        const id = path.split("/").pop();
        const data = (opts.recipes || {})[id];
        if (!data) return Promise.reject(new Error("not found"));
        return Promise.resolve(data);
      },
    },
    notify(msg, level) { state.notices.push([level || "info", msg]); },
  };

  const domListeners = {};
  const document = {
    getElementById: (id) => elements.get(id) || null,
    addEventListener(type, fn) { domListeners[type] = fn; },
  };

  const context = { console, window: win, document, Promise, setTimeout };
  vm.runInNewContext(fs.readFileSync("static/js/blend_drafts.js", "utf8"), context,
    { filename: "blend_drafts.js" });
  vm.runInNewContext(fs.readFileSync("static/js/blend_drafts_page.js", "utf8"), context,
    { filename: "blend_drafts_page.js" });

  return { state, elements, win, domListeners, drafts: win.IRMS.blendDrafts };
}

const flush = () => new Promise((r) => setImmediate(r));
const ago = (ms) => new Date(Date.now() - ms).toISOString();

// 표 tbody 의 click 핸들러를 실제 DOM 처럼 호출하기 위한 가짜 이벤트.
function clickButton(env, rowIndex, className) {
  const body = env.elements.get("drafts-body");
  // rowHtml 이 만든 문자열에서 해당 행의 data 속성을 뽑아 가짜 target/closest 를 만든다.
  const rows = body.innerHTML.split("<tr ").slice(1);
  const row = rows[rowIndex];
  const kind = /data-kind="([^"]*)"/.exec(row)[1];
  const id = /data-id="([^"]*)"/.exec(row)[1];
  const name = /data-name="([^"]*)"/.exec(row)[1];
  const rowEl = { dataset: { kind, id, name } };
  const target = {
    classList: { contains: (c) => c === className },
    closest: () => rowEl,
  };
  body._listeners.click({ target });
}

test("초안이 없으면 '작성 중 배합이 없습니다' 안내만 보인다", async () => {
  const env = makeEnv({});
  env.domListeners.DOMContentLoaded();
  await flush();

  assert.equal(env.elements.get("drafts-loading").hidden, true);
  assert.equal(env.elements.get("drafts-list-panel").hidden, true);
  assert.equal(env.elements.get("drafts-empty-panel").hidden, false);
});

test("두 화면 초안이 제품명·화면·진행도·저장 시각과 함께 최신순으로 나온다", async () => {
  const recipes = {
    1: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" },
                 { material_code: "C2", material_name: "원료B" }] },
    2: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" }] },
  };
  const env = makeEnv({ recipes });
  const d = env.drafts;
  d.saveSlot("blend", {
    recipe_id: 1, product_name: "배합제품", savedAt: ago(600000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }, { code: "C2", name: "원료B" }],
    items: [{ actual_amount: "10", material_lot: "L" }, { actual_amount: "", material_lot: "" }],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  }, env.win.localStorage);
  d.saveSlot("cont", {
    recipe_id: 2, product_name: "다중제품", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }],
    cells: [[{ actual: "5", lot: "" }, { actual: "", lot: "" }]],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  }, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  const html = env.elements.get("drafts-body").innerHTML;
  assert.equal(env.elements.get("drafts-list-panel").hidden, false);
  assert.ok(html.indexOf("다중제품") < html.indexOf("배합제품"), "최신 초안이 위");
  assert.ok(html.includes("다중 계량"), "어느 화면 것인지 표시");
  assert.ok(html.includes("배합</td>"), "어느 화면 것인지 표시");
  assert.ok(html.includes("1 / 2 칸"), "배합 진행도(2줄 중 1줄 계량)");
  assert.ok(html.includes("1 / 2 칸"), "다중 계량 진행도(2칸 중 1칸 계량)");
  assert.ok(html.includes("이어서 하기") && html.includes("삭제"));
});

test("레시피에서 재료가 사라졌으면 복구 전에 배지와 계량값으로 고지한다", async () => {
  const recipes = {
    1: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" },
                 { material_code: "CX", material_name: "신규재료" }] },
  };
  const env = makeEnv({ recipes });
  env.drafts.saveSlot("blend", {
    recipe_id: 1, product_name: "제품", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }, { code: "C2", name: "원료B" }],
    items: [{ actual_amount: "10", material_lot: "" }, { actual_amount: "152.3", material_lot: "" }],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  }, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  const html = env.elements.get("drafts-body").innerHTML;
  assert.ok(html.includes("재료 삭제 1"));
  assert.ok(html.includes("재료 추가 1"));
  assert.ok(html.includes("원료B") && html.includes("152.30g 계량됨"),
    "사라진 재료의 계량값을 복구 전에 보여준다");
  assert.ok(html.includes("신규재료"));
});

test("레시피 조회에 실패해도 목록은 뜨고 '레시피 확인 불가'로 표시된다", async () => {
  const env = makeEnv({ recipes: {} });   // 모든 조회 실패
  env.drafts.saveSlot("blend", {
    recipe_id: 99, product_name: "제품", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }],
    items: [{ actual_amount: "10", material_lot: "" }],
  }, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  assert.equal(env.elements.get("drafts-list-panel").hidden, false);
  assert.ok(env.elements.get("drafts-body").innerHTML.includes("레시피 확인 불가"));
});

test("[이어서 하기]는 슬롯 id 를 sessionStorage 로 넘기고 그 화면으로 이동한다", async () => {
  const recipes = {
    2: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" }] },
  };
  const env = makeEnv({ recipes });
  const slotId = env.drafts.saveSlot("cont", {
    recipe_id: 2, product_name: "다중제품", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }],
    cells: [[{ actual: "5", lot: "" }]],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  }, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  clickButton(env, 0, "drafts-resume");
  assert.equal(env.state.navigated, "/blend/continuous");
  const handed = JSON.parse(env.win.sessionStorage.getItem("irms.blend.resume"));
  assert.equal(handed.kind, "cont");
  assert.equal(handed.id, slotId);
});

test("[삭제]는 확인 후 그 슬롯만 지우고 목록을 다시 그린다", async () => {
  const recipes = {
    1: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" }] },
  };
  const env = makeEnv({ recipes });
  const d = env.drafts;
  const slot = {
    recipe_id: 1, product_name: "지울것", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }],
    items: [{ actual_amount: "10", material_lot: "" }],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  };
  d.saveSlot("blend", slot, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();
  assert.equal(env.elements.get("drafts-list-panel").hidden, false);

  clickButton(env, 0, "drafts-delete");
  await flush();
  await flush();

  assert.equal(d.readSlots("blend", env.win.localStorage).length, 0);
  assert.equal(env.elements.get("drafts-empty-panel").hidden, false);
});

test("[삭제] 확인창을 취소하면 아무것도 지우지 않는다", async () => {
  const recipes = {
    1: { recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
         items: [{ material_code: "C1", material_name: "원료A" }] },
  };
  const env = makeEnv({ recipes, confirmAnswer: false });
  const d = env.drafts;
  d.saveSlot("blend", {
    recipe_id: 1, product_name: "남길것", savedAt: ago(60000), schema: 2,
    materials: [{ code: "C1", name: "원료A" }],
    items: [{ actual_amount: "10", material_lot: "" }],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  }, env.win.localStorage);

  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();
  clickButton(env, 0, "drafts-delete");
  await flush();

  assert.equal(d.readSlots("blend", env.win.localStorage).length, 1);
});

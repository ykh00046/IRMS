/**
 * 시험 배합(test blend) 화면 순수 규칙 테스트 — docs/test-blend-design.md §8.
 *
 * 브라우저 바인딩이 강한 blend.js 는 직접 임포트할 수 없으므로, 그 화면이 기대는
 * 순수 규칙(자재 검색·목표량 합·비율·시험명 기본값·시험 행 HTML)과 초안 저장소의
 * 시험 슬롯 취급을 여기서 잠근다. 정식 경로가 한 글자도 안 바뀌는 것도 같이 본다.
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

global.window = {};
require(path.join(__dirname, "..", "..", "static", "js", "blend_lib.js"));
const lib = global.window.IRMS.blendLib;

// ── 자재 검색(행 추가 창) ────────────────────────────────────────────
const MATERIALS = [
  { id: 1, name: "PB 바인더", code: "M-100", aliases: ["폴리부타디엔"] },
  { id: 2, name: "MEHQ", code: "M-200", aliases: [] },
  { id: 3, name: "스티렌 모노머", code: "SM-10", aliases: ["SM"] },
  { id: 4, name: "PB 베이스", code: "M-101", aliases: [] },
];

test("자재 검색은 품목코드·자재명·동의어를 모두 본다", () => {
  assert.deepEqual(lib.filterMaterials(MATERIALS, "M-200").map((m) => m.id), [2]);
  assert.deepEqual(lib.filterMaterials(MATERIALS, "mehq").map((m) => m.id), [2]);
  assert.deepEqual(lib.filterMaterials(MATERIALS, "폴리부타디엔").map((m) => m.id), [1]);
  assert.deepEqual(lib.filterMaterials(MATERIALS, "pb").map((m) => m.id), [1, 4]);
});

test("자재 검색은 공백·대소문자를 무시하고 완전일치를 먼저 준다", () => {
  assert.deepEqual(lib.filterMaterials(MATERIALS, " 스티렌 모노머 ").map((m) => m.id), [3]);
  // "SM" 은 3번의 동의어 완전일치, 코드 SM-10 도 앞부분 일치 — 같은 행이라 한 건.
  assert.deepEqual(lib.filterMaterials(MATERIALS, "SM").map((m) => m.id), [3]);
});

test("검색어가 비면 앞에서 limit 개, 못 찾으면 빈 목록", () => {
  assert.equal(lib.filterMaterials(MATERIALS, "", 2).length, 2);
  assert.deepEqual(lib.filterMaterials(MATERIALS, "없는이름"), []);
  assert.deepEqual(lib.filterMaterials(null, "pb"), []);
});

// ── 목표량 합·비율 ──────────────────────────────────────────────────
test("총 배합량은 목표량 합(2자리), 목표량 없는 행은 0 취급", () => {
  const items = [
    { theory_amount: 30 }, { theory_amount: 70.005 }, { theory_amount: null }, {},
  ];
  assert.equal(lib.sumTargets(items), 100.01);
  assert.equal(lib.sumTargets([]), 0);
});

test("비율은 목표량/합×100(2자리), 합이 0 이면 0", () => {
  assert.equal(lib.targetRatio(30, 100), 30);
  assert.equal(lib.targetRatio(1, 3), 33.33);
  assert.equal(lib.targetRatio(30, 0), 0);
  assert.equal(lib.targetRatio(null, 100), 0);
});

test("시험명 기본값은 '{제품명} 시험', 제품명이 없으면 빈 값", () => {
  assert.equal(lib.testDefaultName("APB17"), "APB17 시험");
  assert.equal(lib.testDefaultName("  PB  "), "PB 시험");
  assert.equal(lib.testDefaultName(""), "");
  assert.equal(lib.testDefaultName(null), "");
});

// ── 행 HTML ─────────────────────────────────────────────────────────
const ROW = {
  material_name: "PB 바인더", material_lot: "", actual_amount: "",
  ratio: 30, theory_amount: 300,
};

test("시험 행은 목표량 입력칸·비율 칸·행 삭제 버튼을 갖는다", () => {
  const html = lib.materialRowHtml(0, ROW, { test: true });
  assert.ok(html.includes('class="input blend-target" data-idx="0"'), "목표량 입력칸");
  assert.ok(html.includes('value="300"'), "목표량 초기값");
  assert.ok(html.includes('class="num blend-ratio" data-idx="0"'), "비율 칸(실시간 갱신)");
  assert.ok(html.includes('class="blend-row-del" data-idx="0"'), "행 삭제 버튼");
  assert.ok(html.includes('class="input blend-lot"'), "자재 LOT 칸은 그대로");
  assert.ok(html.includes('class="input blend-actual"'), "실제량 칸은 그대로");
  assert.ok(!html.includes("blend-theory"), "이론량 표시 칸은 없다(입력칸이 대신한다)");
});

test("정식 행 HTML 은 시험 옵션 없이 종전과 같다(목표량 입력칸·삭제 버튼 없음)", () => {
  const html = lib.materialRowHtml(0, ROW);
  assert.ok(html.includes('class="num blend-theory" data-idx="0"'), "이론량 표시 칸");
  assert.ok(!html.includes("blend-target"));
  assert.ok(!html.includes("blend-row-del"));
  assert.equal(html, lib.materialRowHtml(0, ROW, {}), "빈 opts 도 같은 결과");
});

// ── 초안 저장소(시험 슬롯) ──────────────────────────────────────────
function loadDrafts() {
  const win = {};
  win.window = win;
  const context = { console, window: win };
  vm.runInNewContext(fs.readFileSync("static/js/blend_drafts.js", "utf8"), context,
    { filename: "blend_drafts.js" });
  return win.IRMS.blendDrafts;
}

function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

const nowIso = () => new Date().toISOString();

function testSlot(name, extra) {
  return Object.assign({
    is_test: true,
    test_name: name,
    product_name: name,
    recipe_id: null,
    base_recipe_id: null,
    savedAt: nowIso(),
    schema: 2,
    materials: [{ code: "", name: "원료A" }],
    items: [{ material_name: "원료A", theory_amount: 10, actual_amount: "10", material_lot: "T1" }],
  }, extra || {});
}

test("시험 초안은 레시피 없이 저장·조회된다", () => {
  const d = loadDrafts();
  const st = makeStorage();
  const id = d.saveSlot("blend", testSlot("PB 점도 시험"), st);
  assert.ok(id, "레시피가 없어도 슬롯 id 가 나온다");
  const slots = d.readSlots("blend", st);
  assert.equal(slots.length, 1);
  assert.equal(d.isTestSlot(slots[0]), true);
  assert.equal(slots[0].test_name, "PB 점도 시험");
  assert.equal(d.progressOf("blend", slots[0]).filled, 1);
  assert.equal(d.isComplete("blend", slots[0]), true);
});

test("시험 초안은 시험명으로 묶인다 — 같은 이름은 같은 칸, 다른 이름은 새 칸", () => {
  const d = loadDrafts();
  const st = makeStorage();
  const first = d.saveSlot("blend", testSlot("시험 가"), st);
  const again = d.saveSlot("blend", testSlot("시험 가"), st);
  assert.equal(again, first, "같은 시험명은 같은 칸을 갱신한다");
  d.saveSlot("blend", testSlot("시험 나"), st);
  assert.equal(d.readSlots("blend", st).length, 2);
});

test("시험 초안에는 레시피 변경 고지가 없다(대조할 레시피가 없다)", () => {
  const d = loadDrafts();
  const diff = d.buildDiff("blend", testSlot("시험"), null);
  assert.equal(diff.test, true);
  assert.equal(diff.changed, false);
  assert.equal(diff.legacy, false);
  // vm 격리 컨텍스트의 배열은 realm 이 달라 deepEqual 이 통하지 않는다 — 길이로 본다.
  assert.equal(diff.dropped.length, 0);
  assert.equal(diff.added.length, 0);
});

test("정식 초안은 레시피 id 로 묶이고 레시피 없는 초안은 여전히 거부된다", () => {
  const d = loadDrafts();
  const st = makeStorage();
  assert.equal(d.saveSlot("blend", { product_name: "레시피 없음", savedAt: nowIso() }, st), null);
  const a = d.saveSlot("blend", {
    recipe_id: 7, product_name: "제품", savedAt: nowIso(), schema: 2,
    materials: [{ code: "C1", name: "원료A" }], items: [{ actual_amount: "1" }],
  }, st);
  const b = d.saveSlot("blend", {
    recipe_id: 7, product_name: "제품", savedAt: nowIso(), schema: 2,
    materials: [{ code: "C1", name: "원료A" }], items: [{ actual_amount: "2" }],
  }, st);
  assert.equal(b, a, "같은 레시피는 같은 칸");
  assert.equal(d.isTestSlot(d.readSlots("blend", st)[0]), false);
});

// ── 작성 중 배합 목록(시험 칩 · 복구 경로) ──────────────────────────
function makeElement(id) {
  return {
    id,
    hidden: false,
    innerHTML: "",
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    _listeners: {},
    addEventListener(type, fn) { this._listeners[type] = fn; },
    querySelector() { return null; },
  };
}

function makePageEnv() {
  const elements = new Map();
  ["drafts-loading", "drafts-list-panel", "drafts-empty-panel", "drafts-body", "drafts-summary"]
    .forEach((id) => elements.set(id, makeElement(id)));
  const state = { navigated: null, requests: [] };
  const win = {
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    location: { assign(url) { state.navigated = url; } },
    confirm() { return true; },
  };
  win.window = win;
  win.IRMS = {
    _core: {
      request(p) { state.requests.push(p); return Promise.reject(new Error("no recipe")); },
    },
    notify() {},
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

test("목록의 시험 초안은 '시험' 칩을 달고 레시피를 조회하지 않는다", async () => {
  const env = makePageEnv();
  env.drafts.saveSlot("blend", testSlot("PB 점도 시험"), env.win.localStorage);
  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  const html = env.elements.get("drafts-body").innerHTML;
  assert.ok(html.includes(">시험<"), "중립 칩 '시험'");
  assert.ok(html.includes('data-test="1"'));
  assert.ok(!html.includes("레시피 확인 불가"), "시험은 레시피 변경 고지가 없다");
  assert.deepEqual(env.state.requests, [], "레시피 조회를 부르지 않는다");
});

test("시험 초안의 [이어서 하기]는 시험 배합 화면으로 간다", async () => {
  const env = makePageEnv();
  env.drafts.saveSlot("blend", testSlot("PB 점도 시험"), env.win.localStorage);
  env.domListeners.DOMContentLoaded();
  await flush();
  await flush();

  const body = env.elements.get("drafts-body");
  const row = body.innerHTML.split("<tr ")[1];
  const rowEl = {
    dataset: {
      kind: /data-kind="([^"]*)"/.exec(row)[1],
      id: /data-id="([^"]*)"/.exec(row)[1],
      name: /data-name="([^"]*)"/.exec(row)[1],
      test: /data-test="([^"]*)"/.exec(row)[1],
    },
  };
  body._listeners.click({
    target: { classList: { contains: (c) => c === "drafts-resume" }, closest: () => rowEl },
  });
  assert.equal(env.state.navigated, "/blend/test");
});

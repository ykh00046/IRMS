const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// blend_drafts.js 는 window 에만 의존한다(저장소는 인자로 주입 가능). vm 격리 컨텍스트에
// 빈 window 를 심어 로드하고, 가짜 localStorage/sessionStorage 로 슬롯 관리·품목 식별자
// 매칭·옛 초안 마이그레이션을 검증한다.
function load() {
  const win = {};
  win.window = win;
  const context = { console, window: win };
  const code = fs.readFileSync("static/js/blend_drafts.js", "utf8");
  vm.runInNewContext(code, context, { filename: "blend_drafts.js" });
  return win.IRMS.blendDrafts;
}

// vm 격리 컨텍스트가 만든 객체·배열은 realm 이 달라 프로토타입이 다르다 —
// deepStrictEqual 이 값과 무관하게 실패하므로 JSON 왕복으로 이 realm 값으로 정규화한다.
const plain = (v) => JSON.parse(JSON.stringify(v));

function makeStorage(initial) {
  const map = new Map(Object.entries(initial || {}));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    _map: map,
  };
}

// 저장 시각은 실행 시각 기준 상대값으로 만든다(고정 날짜를 쓰면 24시간 만료 규칙 때문에
// 나중에 CI 에서 통째로 만료돼 잠복 실패한다).
const ago = (ms) => new Date(Date.now() - ms).toISOString();
const MIN = 60 * 1000;

function blendDraft(recipeId, name, savedAt, materials, items) {
  return {
    recipe_id: recipeId,
    product_name: name,
    savedAt,
    schema: 2,
    materials: materials || [],
    items: items || [],
    recipeMeta: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
  };
}

// ── 3칸 밀어내기 ───────────────────────────────────────────────

test("슬롯은 최대 3칸 — 4번째 저장 시 가장 오래된 것부터 밀어낸다", () => {
  const d = load();
  const st = makeStorage();
  d.saveSlot("blend", blendDraft(1, "A", ago(40 * MIN)), st);
  d.saveSlot("blend", blendDraft(2, "B", ago(30 * MIN)), st);
  d.saveSlot("blend", blendDraft(3, "C", ago(20 * MIN)), st);
  assert.equal(d.readSlots("blend", st).length, 3);

  d.saveSlot("blend", blendDraft(4, "D", ago(10 * MIN)), st);
  const slots = d.readSlots("blend", st);
  assert.equal(slots.length, d.MAX_SLOTS);
  assert.equal(d.MAX_SLOTS, 3);
  // 최신순 정렬 + 가장 오래된 A 가 빠진다.
  assert.deepEqual(plain(slots.map((s) => s.product_name)), ["D", "C", "B"]);
});

test("같은 레시피로 이어서 작업하면 새 슬롯을 만들지 않고 같은 슬롯을 갱신한다", () => {
  const d = load();
  const st = makeStorage();
  const first = d.saveSlot("blend", blendDraft(7, "같은제품", ago(5 * MIN)), st);
  const second = d.saveSlot("blend", blendDraft(7, "같은제품", ago(1 * MIN)), st, first);
  const third = d.saveSlot("blend", blendDraft(7, "같은제품", ago(0)), st, null);  // id 를 잃어버려도

  assert.equal(second, first, "같은 레시피 → 같은 슬롯 id");
  assert.equal(third, first, "슬롯 id 를 넘기지 않아도 레시피로 같은 슬롯을 찾는다");
  assert.equal(d.readSlots("blend", st).length, 1, "슬롯이 늘어나면 안 된다");
});

test("24시간 지난 슬롯은 목록에서 빠진다", () => {
  const d = load();
  const st = makeStorage();
  d.saveSlot("blend", blendDraft(1, "오래됨", ago(25 * 3600 * 1000)), st);
  d.saveSlot("blend", blendDraft(2, "최근", ago(MIN)), st);
  assert.deepEqual(plain(d.readSlots("blend", st).map((s) => s.product_name)), ["최근"]);
});

test("두 화면 슬롯은 별개 키를 쓰고 listAll 이 최신순으로 합친다", () => {
  const d = load();
  const st = makeStorage();
  d.saveSlot("blend", blendDraft(1, "배합것", ago(10 * MIN)), st);
  d.saveSlot("cont", { recipe_id: 2, product_name: "다중것", savedAt: ago(2 * MIN), schema: 2, materials: [], cells: [] }, st);

  assert.equal(d.readSlots("blend", st).length, 1);
  assert.equal(d.readSlots("cont", st).length, 1);
  const all = d.listAll(st);
  assert.deepEqual(plain(all.map((e) => e.slot.product_name)), ["다중것", "배합것"]);
  assert.deepEqual(plain(all.map((e) => e.label)), ["다중 계량", "배합"]);
  assert.equal(all[0].path, "/blend/continuous");
});

test("removeSlot 은 그 슬롯만 지운다(나머지 초안 보존)", () => {
  const d = load();
  const st = makeStorage();
  const a = d.saveSlot("blend", blendDraft(1, "A", ago(3 * MIN)), st);
  d.saveSlot("blend", blendDraft(2, "B", ago(2 * MIN)), st);
  assert.equal(d.removeSlot("blend", a, st), true);
  assert.deepEqual(plain(d.readSlots("blend", st).map((s) => s.product_name)), ["B"]);
});

// ── 옛 1칸 초안 마이그레이션 ───────────────────────────────────

test("옛 1칸 초안은 새 목록의 첫 항목으로 이월되고 옛 키는 지워진다", () => {
  const d = load();
  const legacy = { recipe_id: 42, product_name: "진행중이던것", savedAt: ago(MIN), items: [] };
  const st = makeStorage({ "irms.blend.draft": JSON.stringify(legacy) });

  const slots = d.readSlots("blend", st);
  assert.equal(slots.length, 1);
  assert.equal(slots[0].product_name, "진행중이던것");
  assert.ok(slots[0].id, "이월된 초안에도 슬롯 id 가 붙는다");
  assert.equal(st.getItem("irms.blend.draft"), null, "옛 키는 지워진다");
  // 이월 결과가 즉시 새 키에 기록돼 다시 읽어도 살아 있다(유실 금지).
  assert.equal(d.readSlots("blend", st).length, 1);
});

test("옛 초안 이월은 이미 있는 슬롯을 밀어내지 않는다", () => {
  const d = load();
  const st = makeStorage();
  d.saveSlot("cont", { recipe_id: 1, product_name: "새것", savedAt: ago(MIN), schema: 2, materials: [], cells: [] }, st);
  st.setItem("irms.blend.cont.draft", JSON.stringify({
    recipe_id: 9, product_name: "옛것", savedAt: ago(5 * MIN), cells: [],
  }));

  const names = plain(d.readSlots("cont", st).map((s) => s.product_name));
  assert.deepEqual(names.sort(), ["새것", "옛것"]);
});

// ── 품목 식별자 매칭 ───────────────────────────────────────────

const M = (code, name) => ({ code, name });

test("재료 순서가 바뀌어도 품목코드로 제 자리를 찾는다", () => {
  const d = load();
  const draftMats = [M("C1", "원료A"), M("C2", "원료B"), M("C3", "원료C")];
  const currentMats = [M("C3", "원료C"), M("C1", "원료A"), M("C2", "원료B")];

  const align = d.alignRows(draftMats, currentMats);
  assert.equal(align.legacy, false);
  assert.deepEqual(plain(align.map), [1, 2, 0]);
  assert.deepEqual(plain(align.addedIdx), []);
  assert.deepEqual(plain(align.removedIdx), []);
});

test("중간에 재료가 삽입돼도 기존 계량값이 밀리지 않는다", () => {
  const d = load();
  const draftMats = [M("C1", "원료A"), M("C2", "원료B"), M("C3", "원료C")];
  const currentMats = [M("C1", "원료A"), M("CX", "신규재료"), M("C2", "원료B"), M("C3", "원료C")];

  const align = d.alignRows(draftMats, currentMats);
  assert.deepEqual(plain(align.map), [0, 2, 3], "위치 인덱스였다면 [0,1,2] 로 한 칸씩 밀렸을 것");
  assert.deepEqual(plain(align.addedIdx), [1]);
  assert.deepEqual(plain(d.addedNames(currentMats, align.addedIdx)), ["신규재료"]);
});

test("품목코드가 없으면 품목명으로 매칭한다(공백·대소문자 무시)", () => {
  const d = load();
  const draftMats = [M("", " 원료 A "), M("", "원료B")];
  const currentMats = [M("C9", "원료B"), M("C8", "원료A")];

  const align = d.alignRows(draftMats, currentMats);
  assert.deepEqual(plain(align.map), [1, 0]);
});

test("식별자가 없는 옛 초안은 legacy 로 표시되고 위치 기반 복구로 넘어간다", () => {
  const d = load();
  const align = d.alignRows([], [M("C1", "원료A")]);
  assert.equal(align.legacy, true);
  assert.equal(align.map, null, "map=null → 호출부가 위치 인덱스로 복구");

  const slot = { recipe_id: 1, savedAt: ago(MIN), items: [] };   // materials 없음
  assert.equal(d.slotIsLegacy(slot), true);
  const diff = d.buildDiff("blend", slot, { recipe: {}, items: [{ material_code: "C1", material_name: "원료A" }] });
  assert.equal(diff.legacy, true);
  assert.equal(diff.changed, false);
});

// ── 사라진 재료 보고 ───────────────────────────────────────────

test("레시피에서 사라진 재료의 계량값은 버리지 않고 값까지 보고한다(단건 배합)", () => {
  const d = load();
  const slot = blendDraft(
    1, "제품", ago(MIN),
    [M("C1", "원료A"), M("C2", "원료B")],
    [
      { actual_amount: "100.5", material_lot: "L1" },
      { actual_amount: "152.3", material_lot: "L2" },
    ],
  );
  const recipeData = { recipe: {}, items: [{ material_code: "C1", material_name: "원료A" }] };

  const diff = d.buildDiff("blend", slot, recipeData);
  assert.equal(diff.legacy, false);
  assert.equal(diff.changed, true);
  assert.deepEqual(plain(diff.align.map), [0, -1]);
  assert.deepEqual(plain(diff.dropped), [{ name: "원료B", text: "152.30g 계량됨" }]);

  const html = d.restoreNoticeHtml(diff);
  assert.ok(html.includes("원료B"), "사라진 재료 이름이 안내에 남아야 한다");
  assert.ok(html.includes("152.30g"), "계량값이 안내에 남아야 한다");
  assert.ok(html.includes("레시피에서 삭제됨"));
});

test("다중 계량은 사라진 재료의 로트별 계량값을 모두 보고한다", () => {
  const d = load();
  const slot = {
    recipe_id: 1, product_name: "제품", savedAt: ago(MIN), schema: 2,
    materials: [M("C1", "원료A"), M("C2", "원료B")],
    cells: [
      [{ actual: "10", lot: "" }, { actual: "10", lot: "" }],
      [{ actual: "152.3", lot: "" }, { actual: "151.9", lot: "" }],
    ],
  };
  const recipeData = { recipe: {}, items: [{ material_code: "C1", material_name: "원료A" }] };

  const diff = d.buildDiff("cont", slot, recipeData);
  assert.deepEqual(plain(diff.dropped), [
    { name: "원료B", text: "로트1 152.30g, 로트2 151.90g 계량됨" },
  ]);
});

test("기준 배합량·허용 편차 변경은 복구 전에 고지된다", () => {
  const d = load();
  const slot = blendDraft(1, "제품", ago(MIN), [M("C1", "원료A")], [{ actual_amount: "10", material_lot: "" }]);
  slot.recipeMeta = { default_totals: [1000, 2000], tolerance_g: 0.05, anchor_material_id: null };

  const diff = d.buildDiff("blend", slot, {
    recipe: { default_totals: [3000], tolerance_g: 0.1, anchor_material_id: null },
    items: [{ material_code: "C1", material_name: "원료A" }],
  });
  assert.equal(diff.changed, true);
  assert.equal(diff.baseNotes.length, 2);
  assert.ok(diff.baseNotes[0].includes("기준 배합량"));
  assert.ok(diff.baseNotes[1].includes("허용 편차"));
});

test("변경이 없으면 고지 문구도 없다", () => {
  const d = load();
  const slot = blendDraft(1, "제품", ago(MIN), [M("C1", "원료A")], [{ actual_amount: "10", material_lot: "" }]);
  const diff = d.buildDiff("blend", slot, {
    recipe: { default_totals: [], tolerance_g: 0.05, anchor_material_id: null },
    items: [{ material_code: "C1", material_name: "원료A" }],
  });
  assert.equal(diff.changed, false);
  assert.equal(d.restoreNoticeHtml(diff), "");
});

test("안내 문구는 재료명을 이스케이프한다(HTML 주입 차단)", () => {
  const d = load();
  const slot = blendDraft(
    1, "제품", ago(MIN),
    [M("", "<img src=x onerror=1>")],
    [{ actual_amount: "1", material_lot: "" }],
  );
  const diff = d.buildDiff("blend", slot, { recipe: {}, items: [] });
  const html = d.restoreNoticeHtml(diff);
  assert.ok(!html.includes("<img"), "원본 태그가 그대로 들어가면 안 된다");
  assert.ok(html.includes("&lt;img"));
});

// ── 진행도 · 화면 간 전달 ───────────────────────────────────────

test("진행도는 계량값이 들어간 칸 수 / 전체 칸 수", () => {
  const d = load();
  assert.deepEqual(
    plain(d.progressOf("blend", { items: [{ actual_amount: "1" }, { actual_amount: "" }, { actual_amount: "3" }] })),
    { filled: 2, total: 3 },
  );
  assert.deepEqual(
    plain(d.progressOf("cont", { cells: [[{ actual: "1" }, { actual: "" }], [{ actual: "" }, { actual: "" }]] })),
    { filled: 1, total: 4 },
  );
});

test("이어서 하기 전달값은 한 번만 읽히고(재사용 방지) 화면이 다르면 안 준다", () => {
  const d = load();
  const ss = makeStorage();
  d.setResume("cont", "slot-1", ss);
  assert.equal(d.takeResume("blend", ss), null, "다른 화면이면 복구하지 않는다");

  d.setResume("cont", "slot-1", ss);
  assert.equal(d.takeResume("cont", ss), "slot-1");
  assert.equal(d.takeResume("cont", ss), null, "새로고침이 복구를 반복 트리거하면 안 된다");
});

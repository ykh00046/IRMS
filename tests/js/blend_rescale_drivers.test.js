/**
 * 증량(배치 rescale) '드라이버' 회귀 테스트 — 어느 자재가 증량을 몰아왔는지 기록한다.
 *
 * 미확인 증량 알림에서 "어디를 증량한 건지"를 보여주려면 rescalePlan 이 최종 총량을
 * 결정한 행(들)을 반환해야 한다. blend.js/blend_continuous.js 의 finalizeRescale 이 이 값을
 * 이벤트에 drivers 로 옮겨 싣고, status.js 가 이벤트당 한 줄 요약을 렌더한다.
 *
 * 여기서는 rescalePlan 순수 함수(증량 전 이론량·실제량·초과량·드라이버)만 잠근다.
 * status.js 의 렌더는 브라우저 바인딩이 강해 직접 임포트할 수 없으므로, "drivers 가 없는
 * 과거 이벤트도 오늘과 같이 렌더되어야 한다"는 계약을 동일한 가드로 여기서 검증한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

global.window = {};
require(path.join(__dirname, "..", "..", "static", "js", "blend_lib.js"));
const { rescalePlan } = global.window.IRMS.blendLib;

// status.js rescaleBlock 의 drivers 가드와 동일 로직 — drivers 가 없으면 빈 문자열(변화 없음).
// 과거 저장 기록(드라이버 도입 전)이 섞여도 터지지 않음을 여기서 증명한다.
function driverLinesHtml(event) {
  const drivers = Array.isArray(event && event.drivers) ? event.drivers : [];
  return drivers.length ? drivers.map((d) => `driver:${d.material_name}`).join("|") : "";
}

test("단일 자재 초과 계량 — 그 행 하나가 드라이버, over 는 (실제-이론)", () => {
  // 비율 50% 자재, 이론 1200g 인데 1255g 을 담아 55g 초과. 총량 2400 → 2510.
  const items = [
    { material_name: "컬러 안료 블루", ratio: 50, actual_amount: 1255, theory_amount: 1200 },
    { material_name: "베이스 수지", ratio: 50, actual_amount: 1180, theory_amount: 1200 },  // 미달
  ];
  const plan = rescalePlan(items, 2400, 1);
  assert.strictEqual(plan.newTotal, 2510);
  assert.strictEqual(plan.changed, true);
  assert.ok(Array.isArray(plan.drivers), "drivers 필드 존재");
  assert.strictEqual(plan.drivers.length, 1, "초과한 자재 하나만 드라이버");
  const d = plan.drivers[0];
  assert.strictEqual(d.idx, 0);
  assert.strictEqual(d.material_name, "컬러 안료 블루");
  assert.strictEqual(d.theory_before, 1200);
  assert.strictEqual(d.actual, 1255);
  assert.strictEqual(d.over, 55);
});

test("동률 — 두 자재가 같은 newTotal 을 몰면 드라이버 둘", () => {
  // 두 자재 모두 비율 50%·이론 1200. 한쪽 1260, 다른 쪽 1260 → 같은 required(2520)로 동률.
  const items = [
    { material_name: "안료 A", ratio: 50, actual_amount: 1260, theory_amount: 1200 },
    { material_name: "안료 B", ratio: 50, actual_amount: 1260, theory_amount: 1200 },
  ];
  const plan = rescalePlan(items, 2400, 1);
  assert.strictEqual(plan.newTotal, 2520);
  assert.strictEqual(plan.drivers.length, 2, "동률이면 둘 다 드라이버");
  assert.deepStrictEqual(
    plan.drivers.map((d) => d.material_name).sort(),
    ["안료 A", "안료 B"],
  );
  plan.drivers.forEach((d) => {
    assert.strictEqual(d.over, 60);
    assert.strictEqual(d.theory_before, 1200);
    assert.strictEqual(d.actual, 1260);
  });
});

test("허용 편차 이내 — 아무것도 초과하지 않으면 drivers 는 빈 배열, changed 는 false", () => {
  // 이론 1200 ± 1g 허용. 1200.5g 은 편차 흡수 → 증량 없음.
  const items = [
    { material_name: "안료", ratio: 50, actual_amount: 1200.5, theory_amount: 1200 },
    { material_name: "수지", ratio: 50, actual_amount: 1199, theory_amount: 1200 },
  ];
  const plan = rescalePlan(items, 2400, 1);
  assert.strictEqual(plan.changed, false);
  assert.deepStrictEqual(plan.drivers, []);
});

test("newTotal/changed/rows 기존 계약 보존 — drivers 추가로 값이 변하지 않는다", () => {
  const items = [
    { material_name: "A", ratio: 50, actual_amount: 1255, theory_amount: 1200 },
    { material_name: "B", ratio: 50, actual_amount: 1180, theory_amount: 1200 },
  ];
  const plan = rescalePlan(items, 2400, 1);
  assert.strictEqual(plan.newTotal, 2510);
  assert.strictEqual(plan.changed, true);
  assert.strictEqual(plan.rows.length, 2);
  // 증량 후 새 이론량 = 50% × 2510 = 1255
  assert.strictEqual(plan.rows[0].newTheory, 1255);
  // B 는 1180g 담았으니 75g 더 필요
  assert.strictEqual(plan.rows[1].addNeeded, 75);
});

test("과거 이벤트(drivers 없음)는 오늘과 동일하게 렌더 — driver 줄 없이 빈 문자열", () => {
  // 드라이버 도입 전에 저장된 레코드 그대로의 이벤트 모양.
  const oldEvent = { before_total: 2400, after_total: 2510, approver: "김책임" };
  assert.strictEqual(driverLinesHtml(oldEvent), "", "drivers 키가 없어도 에러 없이 빈 줄");
  // drivers: [] 인 빈 배열 이벤트도 동일.
  assert.strictEqual(driverLinesHtml({ before_total: 1, after_total: 2, drivers: [] }), "");
  // drivers 가 있으면 요약 줄 생성.
  const withDrivers = {
    before_total: 2400, after_total: 2510,
    drivers: [{ material_name: "컬러 안료 블루", theory_before: 1200, actual: 1255, over: 55 }],
  };
  assert.strictEqual(driverLinesHtml(withDrivers), "driver:컬러 안료 블루");
});

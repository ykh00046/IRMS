const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// viscosity_lib.js 를 격리 컨텍스트에 올려 순수 헬퍼만 꺼낸다(DOM 불필요).
// (tests/js/viscosity_pb_link.test.js 와 동일한 로더 패턴)
function loadViscLib() {
  const context = { console, window: {}, document: {} };
  context.window.window = context.window;
  const code = fs.readFileSync("static/js/viscosity_lib.js", "utf8");
  vm.runInNewContext(code, context, { filename: "viscosity_lib.js" });
  return context.window.IRMS.viscLib;
}

const { pbBandCuts, pbBandRows, pbLinkReasonText, PB_BAND_MIN_READINGS } = loadViscLib();

// PB 점도(source_pb_viscosity) ↔ 이 반제품 점도(viscosity) 연계 측정 6건.
const LINKED = [
  { source_pb_viscosity: 44, viscosity: 70, status: "normal" },
  { source_pb_viscosity: 47, viscosity: 76, status: "normal" },
  { source_pb_viscosity: 48, viscosity: 80, status: "anomaly" },
  { source_pb_viscosity: 50, viscosity: 84, status: "normal" },
  { source_pb_viscosity: 53, viscosity: 90, status: "warn" },
  { source_pb_viscosity: 56, viscosity: 96, status: "anomaly" },
];

test("기준선이 있으면 그 값이 구간 경계다(최대 3개 · 구간 4개)", () => {
  const limits = { lower_limit: 45, warn_low: 48, warn_high: 53, upper_limit: 58 };
  const { cuts, source } = pbBandCuts(LINKED.map((r) => r.source_pb_viscosity), limits);
  assert.equal(source, "limits");
  assert.deepEqual(Array.from(cuts), [45, 48, 53]);
});

test("기준선이 없으면 연계된 PB 점도 범위를 3등분한다", () => {
  const { cuts, source } = pbBandCuts([44, 56], null);
  assert.equal(source, "range");
  assert.deepEqual(Array.from(cuts), [48, 52]);
});

test("설정하지 않은 기준선(null)은 0 으로 굳지 않는다", () => {
  // PB 는 경고 하한 하나만 두는 일이 흔하다 — 나머지는 null 로 온다.
  const { cuts, source } = pbBandCuts([44, 56], {
    lower_limit: null, warn_low: 48, warn_high: null, upper_limit: null,
  });
  assert.equal(source, "limits");
  assert.deepEqual(Array.from(cuts), [48]);          // '0.0 이하' 빈 구간이 생기면 안 된다
  const { rows } = pbBandRows(LINKED, {
    lower_limit: null, warn_low: 48, warn_high: null, upper_limit: null,
  });
  assert.deepEqual(Array.from(rows, (r) => r.label), ["48.0 이하", "48.0 초과"]);
  assert.deepEqual(Array.from(rows, (r) => r.count), [3, 3]);
});

test("구간표는 건수·이 반제품 평균·이상 건수를 구간마다 센다", () => {
  const { rows, source } = pbBandRows(LINKED, { warn_low: 48, warn_high: 53 });
  assert.equal(source, "limits");
  assert.deepEqual(Array.from(rows, (r) => r.label), ["48.0 이하", "48.0~53.0", "53.0 초과"]);
  assert.deepEqual(Array.from(rows, (r) => r.count), [3, 2, 1]);
  // 경계는 포함(48 은 첫 구간, 53 은 가운데 구간).
  assert.equal(rows[0].mean, (70 + 76 + 80) / 3);
  assert.equal(rows[1].mean, (84 + 90) / 2);
  assert.equal(rows[2].mean, 96);
  assert.deepEqual(Array.from(rows, (r) => r.anomaly), [1, 0, 1]);
});

test("기준선이 없으면 3등분 구간으로 만든다", () => {
  const { rows, source } = pbBandRows(LINKED, null);
  assert.equal(source, "range");
  assert.equal(rows.length, 3);
  assert.equal(Array.from(rows).reduce((sum, r) => sum + r.count, 0), LINKED.length);
});

test("표본이 적으면(5건 미만) 구간표를 만들지 않는다", () => {
  assert.equal(PB_BAND_MIN_READINGS, 5);
  const { rows } = pbBandRows(LINKED.slice(0, 4), { warn_low: 48 });
  assert.deepEqual(Array.from(rows), []);
});

test("PB 점도가 없는 측정은 구간표에 들어가지 않는다", () => {
  const withNulls = LINKED.concat([
    { source_pb_viscosity: null, viscosity: 88, status: "normal" },
    { viscosity: 89, status: "normal" },
  ]);
  const { rows } = pbBandRows(withNulls, { warn_low: 48 });
  assert.equal(Array.from(rows).reduce((sum, r) => sum + r.count, 0), LINKED.length);
});

test("빈 구간도 0건으로 남는다(구간 모양이 흔들리지 않게)", () => {
  const onlyLow = [44, 45, 46, 47, 44].map((pb) => ({
    source_pb_viscosity: pb, viscosity: 70, status: "normal",
  }));
  const { rows } = pbBandRows(onlyLow, { warn_low: 48, warn_high: 53 });
  assert.deepEqual(Array.from(rows, (r) => r.count), [5, 0, 0]);
  assert.equal(rows[1].mean, null);
});

test("연계 사유 문장은 붙은 건수와 가장 큰 사유 하나만 말한다", () => {
  const text = pbLinkReasonText({
    total: 363, matched: 99, pb_missing: 263, no_lot: 0, lot_unreadable: 0, pb_excluded: 1,
  });
  assert.match(text, /측정 363건 중 99건에 PB 점도가 붙었습니다\./);
  assert.match(text, /263건은 그 PB LOT의 점도 기록이 없습니다\./);
  assert.ok(!text.includes("0건은"), "0 인 사유는 말하지 않는다");
  // 전부 붙었으면 사유를 말할 것이 없다.
  assert.equal(
    pbLinkReasonText({ total: 4, matched: 4 }),
    "측정 4건 중 4건에 PB 점도가 붙었습니다.",
  );
  // 가장 큰 사유가 '통계 제외'일 수도 있다.
  assert.match(
    pbLinkReasonText({ total: 5, matched: 1, pb_excluded: 3, pb_missing: 1 }),
    /3건은 그 PB 점도가 통계에서 빠졌습니다\./,
  );
  // 측정이 없거나 응답이 옛 서버여도 터지지 않는다.
  assert.equal(pbLinkReasonText({ total: 0, matched: 0 }), "");
  assert.equal(pbLinkReasonText(null), "");
});

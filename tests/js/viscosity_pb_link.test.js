const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// viscosity_lib.js 를 격리 컨텍스트에 올려 순수 헬퍼만 꺼낸다(DOM 불필요).
// (tests/js/reading_overlay.test.js 와 동일한 로더 패턴)
function loadViscLib() {
  const context = { console, window: {}, document: {} };
  context.window.window = context.window;
  const code = fs.readFileSync("static/js/viscosity_lib.js", "utf8");
  vm.runInNewContext(code, context, { filename: "viscosity_lib.js" });
  return context.window.IRMS.viscLib;
}

const { sourcePbLinkedReadings, sourcePbScatterDatasets, pbLinkNotice } = loadViscLib();
const resolveCss = (name) => name;

function datasetByLabel(datasets) {
  const map = {};
  Array.from(datasets).forEach((d) => { map[d.label] = d; });
  return map;
}

const READINGS = [
  { measured_date: "2026-08-01", viscosity: 80, material_lot: "26073101", source_pb_viscosity: 48, status: "normal" },
  { measured_date: "2026-08-05", viscosity: 92, material_lot: "26080401", source_pb_viscosity: 52, status: "anomaly" },
  // 사용한 PB 는 적혔지만 그 PB 의 점도를 못 찾은 측정 — 그림에 올릴 좌표가 없다.
  { measured_date: "2026-08-06", viscosity: 81, material_lot: "26080501", source_pb_viscosity: null, status: "normal" },
  // LOT 자체가 없는 측정(직접 등록 등).
  { measured_date: "2026-08-07", viscosity: 79, material_lot: "  ", source_pb_viscosity: 49, status: "normal" },
];

test("연계 목록은 PB 점도가 있는 측정만, 최신순", () => {
  const linked = sourcePbLinkedReadings(READINGS);
  assert.deepEqual(linked.map((r) => r.measured_date), ["2026-08-05", "2026-08-01"]);
});

test("산점도는 x=PB 점도, y=이 반제품 점도이고 이상은 따로 뽑힌다", () => {
  const byLabel = datasetByLabel(sourcePbScatterDatasets(READINGS, resolveCss));
  assert.ok(byLabel["측정"], "정상 측정 데이터셋");
  assert.ok(byLabel["이상"], "이상 측정 데이터셋");
  assert.deepEqual(
    Array.from(byLabel["측정"].data, (p) => [p.x, p.y]),
    [[48, 80]],
  );
  assert.deepEqual(
    Array.from(byLabel["이상"].data, (p) => [p.x, p.y]),
    [[52, 92]],
  );
  // 붉은 점 + 더 크게 — 관계에서 벗어난 점이 눈에 남아야 한다.
  assert.equal(byLabel["이상"].backgroundColor, "--status-error");
  assert.ok(byLabel["이상"].radius > byLabel["측정"].radius);
  // 툴팁이 말할 것(측정일·LOT)이 점에 실려 있다.
  assert.equal(byLabel["이상"].data[0].lot, "26080401");
  assert.equal(byLabel["이상"].data[0].date, "2026-08-05");
});

test("연계가 하나도 없으면 데이터셋도 없다", () => {
  assert.equal(Array.from(sourcePbScatterDatasets([], resolveCss)).length, 0);
  assert.equal(Array.from(sourcePbScatterDatasets(null, resolveCss)).length, 0);
});

// 매칭 0 일 때 패널을 조용히 숨기면 '연계가 안 된 것'과 '원래 없는 것'을 구별할 수
// 없다(2026-08-13 검토 6번). 세 상태를 각각 다른 문장으로 말한다.
test("pbLinkNotice 는 세 상태를 구별해 말한다", () => {
  const none = pbLinkNotice({ readings_with_lot: 0, matched: 0 }, 0);
  assert.match(none, /PB 연계 기록이 없습니다/);

  const unmatched = pbLinkNotice({ readings_with_lot: 5, matched: 0 }, 0);
  assert.match(unmatched, /점도를 찾지 못했습니다/);
  assert.match(unmatched, /5건/);

  const ok = pbLinkNotice({ readings_with_lot: 5, matched: 4 }, 4);
  assert.match(ok, /4건/);

  // pb_link 자체가 없는 응답(구버전 서버)에서도 터지지 않는다.
  assert.match(pbLinkNotice(null, 0), /PB 연계 기록이 없습니다/);
});

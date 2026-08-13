const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// viscosity_lib.js 를 격리 컨텍스트에 올려 순수 헬퍼만 꺼낸다(DOM 불필요).
// (tests/js/viscosity_chart_bounds.test.js 와 동일한 로더 패턴)
function loadViscLib() {
  const context = { console, window: {}, document: {} };
  context.window.window = context.window;
  const code = fs.readFileSync("static/js/viscosity_lib.js", "utf8");
  vm.runInNewContext(code, context, { filename: "viscosity_lib.js" });
  return context.window.IRMS.viscLib;
}

const { readingOverlayDatasets, periodKeyForDate } = loadViscLib();

const resolveCss = (name) => `css(${name})`;

// datasets/data 는 vm 격리 realm 에서 생성되어 Array.prototype 이 달라
// deepStrictEqual(참조-동일 프로토타입 요구)이 실패한다. 값만 비교하는 헬퍼.
function points(dataset) {
  return Array.from(dataset.data, (p) => [p.x, p.y]);
}
function datasetByLabel(datasets) {
  const map = {};
  Array.from(datasets).forEach((d) => { map[d.label] = d; });
  return map;
}

test("periodKeyForDate buckets by granularity and drops bad dates", () => {
  assert.equal(periodKeyForDate("2026-01-05", "month"), "2026-01");
  assert.equal(periodKeyForDate("2026-01-05", "quarter"), "2026-Q1");
  assert.equal(periodKeyForDate("2026-04-05", "quarter"), "2026-Q2");
  assert.equal(periodKeyForDate("2026-01-05", "year"), "2026");
  assert.equal(periodKeyForDate("2026-01-05", "day"), "2026-01-05");
  assert.equal(periodKeyForDate(null, "month"), null);
  assert.equal(periodKeyForDate("", "month"), null);
});

test("excluded readings go to 제외 dataset, anomalies to 이상 dataset", () => {
  const readings = [
    { measured_date: "2026-01-05", viscosity: 90, status: "anomaly" },
    { measured_date: "2026-01-06", viscosity: 12, status: "excluded" },
    { measured_date: "2026-01-07", viscosity: 50, status: "normal" }, // 데이터셋에 안 들어감
  ];
  const byLabel = datasetByLabel(
    readingOverlayDatasets(readings, "month", ["2026-01"], resolveCss)
  );
  assert.ok(byLabel["이상 측정"], "이상 데이터셋 존재");
  assert.ok(byLabel["제외"], "제외 데이터셋 존재");

  assert.deepEqual(points(byLabel["이상 측정"]), [["2026-01", 90]]);
  assert.deepEqual(points(byLabel["제외"]), [["2026-01", 12]]);
  // normal 측정은 어느 오버레이에도 없다.
  assert.equal(byLabel["이상 측정"].data.length, 1);
  assert.equal(byLabel["제외"].data.length, 1);
});

// 2026-08-13 재설계: 기간 평균이 선이 되면서, 개별 측정도 옅은 점으로 함께 깔린다.
// 평균 하나가 지워버린 '그 구간 안의 흩어짐'을 남기기 위해서다. 이상·제외는 그대로
// 자기 데이터셋(크고 붉은 삼각형 / 회색 X)으로 간다.
test("normal and warn readings become the muted 개별 측정 overlay", () => {
  const readings = [
    { measured_date: "2026-01-05", viscosity: 399, status: "normal", lot_no: "A1" },
    { measured_date: "2026-01-06", viscosity: 404, status: "warn", lot_no: "A2" },
    { measured_date: "2026-01-07", viscosity: 128, status: "anomaly", lot_no: "A3" },
  ];
  const byLabel = datasetByLabel(
    readingOverlayDatasets(readings, "month", ["2026-01"], resolveCss)
  );
  assert.ok(byLabel["개별 측정"], "정상·경고 측정 데이터셋이 있어야 한다");
  assert.deepEqual(points(byLabel["개별 측정"]), [["2026-01", 399], ["2026-01", 404]]);
  // 이상 측정은 개별 측정에 섞이지 않고, 더 크게 그려진다.
  assert.deepEqual(points(byLabel["이상 측정"]), [["2026-01", 128]]);
  assert.ok(byLabel["이상 측정"].radius > byLabel["개별 측정"].radius);
  // 점 하나하나가 어느 LOT 인지 툴팁으로 말할 수 있어야 한다.
  assert.equal(byLabel["이상 측정"].data[0].lot, "A3");
});

test("reading whose period is outside provided labels is dropped", () => {
  const readings = [
    { measured_date: "2026-01-05", viscosity: 90, status: "anomaly" }, // 2026-01 라벨 있음
    { measured_date: "2026-05-05", viscosity: 88, status: "anomaly" }, // 2026-05 라벨 없음 → drop
  ];
  const byLabel = datasetByLabel(
    readingOverlayDatasets(readings, "month", ["2026-01"], resolveCss)
  );
  assert.ok(byLabel["이상 측정"]);
  assert.deepEqual(points(byLabel["이상 측정"]), [["2026-01", 90]]);
});

test("excluded flag (r.excluded) also routes to 제외 even without status", () => {
  const readings = [
    { measured_date: "2026-01-06", viscosity: 30, excluded: true },
  ];
  const datasets = Array.from(
    readingOverlayDatasets(readings, "month", ["2026-01"], resolveCss)
  );
  assert.equal(datasets.length, 1);
  assert.equal(datasets[0].label, "제외");
  assert.deepEqual(points(datasets[0]), [["2026-01", 30]]);
});

test("empty / no-match input yields no datasets", () => {
  assert.equal(Array.from(readingOverlayDatasets([], "month", ["2026-01"], resolveCss)).length, 0);
  assert.equal(Array.from(readingOverlayDatasets(null, "month", ["2026-01"], resolveCss)).length, 0);
  // 라벨이 하나도 안 맞으면 빈 배열.
  const readings = [{ measured_date: "2026-01-05", viscosity: 90, status: "anomaly" }];
  assert.equal(
    Array.from(readingOverlayDatasets(readings, "month", ["2026-02"], resolveCss)).length,
    0
  );
});

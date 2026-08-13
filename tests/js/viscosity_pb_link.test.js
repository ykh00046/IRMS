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

const {
  sourcePbLinkedReadings, sourcePbScatterDatasets, pbLinkNotice,
  pbLinearFit, pbScatterSummary, withAlpha,
} = loadViscLib();
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
  assert.ok(byLabel["최근 1건"], "정상 측정 데이터셋(최근)");
  assert.ok(byLabel["이상"], "이상 측정 데이터셋");
  assert.deepEqual(
    Array.from(byLabel["최근 1건"].data, (p) => [p.x, p.y]),
    [[48, 80]],
  );
  assert.deepEqual(
    Array.from(byLabel["이상"].data, (p) => [p.x, p.y]),
    [[52, 92]],
  );
  // 붉은 점 + 더 크게 — 관계에서 벗어난 점이 눈에 남아야 한다.
  assert.equal(byLabel["이상"].backgroundColor, "--status-error");
  assert.ok(byLabel["이상"].radius > byLabel["최근 1건"].radius);
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

// ── 추세선·요약 문장 — 점만으로는 한눈에 안 들어온다(2026-08-13 현장 지적) ──

test("pbLinearFit 은 기울기·상관을 정확히 계산한다", () => {
  // 완전한 직선 y = 2x + 1 → slope 2, r = 1.
  const fit = pbLinearFit([
    { x: 1, y: 3 }, { x: 2, y: 5 }, { x: 3, y: 7 }, { x: 4, y: 9 },
  ]);
  assert.ok(Math.abs(fit.slope - 2) < 1e-9);
  assert.ok(Math.abs(fit.r - 1) < 1e-9);
  assert.equal(fit.n, 4);
  assert.equal(fit.minX, 1);
  assert.equal(fit.maxX, 4);
  // 표본 부족(2건 이하)·x 무변동이면 적합 없음.
  assert.equal(pbLinearFit([{ x: 1, y: 2 }, { x: 2, y: 3 }]), null);
  assert.equal(pbLinearFit([{ x: 5, y: 1 }, { x: 5, y: 2 }, { x: 5, y: 3 }]), null);
});

test("pbScatterSummary 는 상관 세기를 문장으로 말한다", () => {
  assert.match(pbScatterSummary(null), /표본이 적어/);
  assert.match(
    pbScatterSummary({ slope: 2, r: 0.95, n: 10 }),
    /상관 뚜렷.*\+2\.0/,
  );
  assert.match(
    pbScatterSummary({ slope: -3.5, r: -0.45, n: 20 }),
    /상관 중간.*-3\.5/,
  );
  assert.match(pbScatterSummary({ slope: 0.1, r: 0.05, n: 30 }), /뚜렷한 상관 없음/);
});

test("옵션으로 추세선·규격 기준선이 붙고 최근/이전이 나뉜다", () => {
  // 최근 2건만 진하게 — 나머지는 '이전 측정'으로 흐리게.
  const readings = [
    { measured_date: "2026-08-05", viscosity: 90, material_lot: "L5", source_pb_viscosity: 50, status: "normal" },
    { measured_date: "2026-08-04", viscosity: 88, material_lot: "L4", source_pb_viscosity: 49, status: "normal" },
    { measured_date: "2026-08-03", viscosity: 86, material_lot: "L3", source_pb_viscosity: 48, status: "normal" },
  ];
  const fit = pbLinearFit(readings.map((r) => ({ x: r.source_pb_viscosity, y: r.viscosity })));
  const byLabel = datasetByLabel(sourcePbScatterDatasets(readings, resolveCss, {
    fit,
    recent: 2,
    limits: { lower: 80, upper: 95, center: 87 },
  }));
  assert.ok(byLabel["최근 2건"]);
  assert.equal(byLabel["이전 측정"].data.length, 1);
  assert.ok(byLabel["추세선"], "추세선 데이터셋");
  assert.deepEqual(
    Array.from(byLabel["추세선"].data, (p) => p.x),
    [48, 50],
  );
  assert.ok(byLabel["중심"]);
  assert.ok(byLabel["규격 하한"]);
  assert.ok(byLabel["규격 상한"]);
  // 기준선은 x 전체 범위를 가로지른다.
  assert.deepEqual(
    Array.from(byLabel["중심"].data, (p) => [p.x, p.y]),
    [[48, 87], [50, 87]],
  );
});

test("규격이 없으면(null) 기준선을 긋지 않는다 — 0 위치의 가짜 선 금지", () => {
  const readings = [
    { measured_date: "2026-08-05", viscosity: 90, material_lot: "L5", source_pb_viscosity: 50, status: "normal" },
    { measured_date: "2026-08-04", viscosity: 88, material_lot: "L4", source_pb_viscosity: 49, status: "normal" },
  ];
  const byLabel = datasetByLabel(sourcePbScatterDatasets(readings, resolveCss, {
    limits: { lower: null, upper: null, center: null },
  }));
  assert.equal(byLabel["규격 하한"], undefined);
  assert.equal(byLabel["규격 상한"], undefined);
  assert.equal(byLabel["중심"], undefined);
});

test("withAlpha 는 hex 만 변환하고 그 외는 원본 유지", () => {
  assert.equal(withAlpha("#3355aa", 0.3), "rgba(51, 85, 170, 0.3)");
  assert.equal(withAlpha("--brand-mid", 0.3), "--brand-mid");
});

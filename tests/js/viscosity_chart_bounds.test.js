const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// viscosity_lib.js 를 격리 컨텍스트에 올려 순수 헬퍼만 꺼낸다(DOM 불필요).
function loadViscLib() {
  const context = { console, window: {}, document: {} };
  context.window.window = context.window;
  const code = fs.readFileSync("static/js/viscosity_lib.js", "utf8");
  vm.runInNewContext(code, context, { filename: "viscosity_lib.js" });
  return context.window.IRMS.viscLib;
}

const { periodChartYBounds, controlBandHtml, periodChartDatasets } = loadViscLib();

// 기간 평균 막대 그래프의 y축이 데이터에 맞춰 확대되면, 4,850 과 4,900 처럼 거의
// 같은 값이 두 배 차이나는 막대로 보인다. 축을 관리한계·규격에 걸어 고정한다.
{
  const bounds = periodChartYBounds(
    { center: 5000, lcl: 4800, ucl: 5200 },
    { lower_limit: null, upper_limit: null },
  );
  assert.ok(bounds.suggestedMin < 4800, "하한은 관리하한보다 아래여야 한다");
  assert.ok(bounds.suggestedMax > 5200, "상한은 관리상한보다 위여야 한다");
}

// 규격만 있고 통계 관리한계가 아직 없는 반제품(표본 부족)도 축이 잡혀야 한다.
{
  const bounds = periodChartYBounds(
    { center: null, lcl: null, ucl: null },
    { lower_limit: 100, upper_limit: 140 },
  );
  assert.ok(bounds.suggestedMin < 100 && bounds.suggestedMax > 140);
}

// 기준이 하나도 없으면 Chart.js 자동 스케일에 맡긴다(빈 객체).
{
  // vm 컨텍스트가 달라 객체 프로토타입이 다르므로 deepEqual 대신 키 개수로 본다.
  const isAuto = (v) => Object.keys(v).length === 0;
  assert.ok(isAuto(periodChartYBounds({ center: null, lcl: null, ucl: null }, { lower_limit: null, upper_limit: null })));
  assert.ok(isAuto(periodChartYBounds(null, null)));
}

// 기준값이 하나뿐이면 범위를 만들 수 없으므로 역시 자동.
{
  assert.equal(Object.keys(periodChartYBounds({ center: 5000 }, {})).length, 0);
}

// 상·하한이 같은 값이어도 0 폭 축을 만들지 않는다(Chart.js 가 눈금을 못 그린다).
{
  const bounds = periodChartYBounds({ center: 5000, lcl: 5000, ucl: 5000 }, {});
  assert.ok(bounds.suggestedMax > bounds.suggestedMin, "폭이 0 이면 안 된다");
}

// suggested* 는 범위를 넓히기만 하므로, 한계를 벗어난 실측은 잘리지 않는다.
// (Chart.js 의 계약 — 여기서는 우리가 min/max 를 쓰지 않았음을 확인한다)
{
  const bounds = periodChartYBounds({ center: 5000, lcl: 4800, ucl: 5200 }, {});
  assert.equal(bounds.min, undefined);
  assert.equal(bounds.max, undefined);
}

// ── controlBandHtml — 관리 밴드 그림(순수 HTML 빌더) ──────────────────
// 규격 안에 관리 밴드, 그 안에 중심, 최근 측정값 마커가 들어가는지 본다.

// 정상 밴드: 규격 4800~5200, 관리 4850~5150, 경고 4900~5100, 중심 5000, 최근값 5050.
// 트랙은 규격 기준이므로 관리 밴드는 안쪽 구간, 경고 한계가 있으면 주황(target/warn)
// 구간이 모두 그려지고, 중심 파선·마커가 트랙 폭 안에 들어와야 한다.
{
  const html = controlBandHtml(
    {
      stats: { center: 5000, lcl: 4850, ucl: 5150, lwl: 4900, uwl: 5100 },
      product: { lower_limit: 4800, upper_limit: 5200 },
    },
    5050,
  );
  assert.ok(html.includes("visc-band"), "밴드 래퍼가 있어야 한다");
  assert.ok(html.includes("visc-band-zone target"), "관리 내(target=초록) 구간이 있어야 한다");
  assert.ok(html.includes("visc-band-zone warn"), "경고 구간(warn=주황)이 있어야 한다");
  assert.ok(html.includes("visc-band-zone spec"), "규격 내 관리 밖(spec=빨강) 구간이 있어야 한다");
  assert.ok(html.includes("visc-band-center"), "중심 파선이 있어야 한다");
  assert.ok(html.includes("visc-band-marker"), "최근 측정값 마커가 있어야 한다");
  assert.ok(html.includes("5050"), "최근 측정값 라벨(5050)이 있어야 한다");
  // 트랙은 규격(4800~5200) 기준 — 좌우 끝 라벨이 규격값(fmt 1자리)이어야 한다.
  assert.ok(html.includes(">4800.0<"), "트랙 왼쪽 끝은 규격 하한(4800.0)");
  assert.ok(html.includes(">5200.0<"), "트랙 오른쪽 끝은 규격 상한(5200.0)");
  // 인라인 hex 색이 없어야 한다(CSS 클래스만 쓴다).
  assert.ok(!/#([0-9a-fA-F]{3,6})/.test(html), "인라인 hex 색이 있으면 안 된다 — CSS 클래스로");
}

// 경고 한계가 없으면 관리 밴드(lcl~ucl) 통째로 target(초록), 바깥은 spec(빨강).
{
  const html = controlBandHtml(
    {
      stats: { center: 5000, lcl: 4850, ucl: 5150, lwl: null, uwl: null },
      product: { lower_limit: 4800, upper_limit: 5200 },
    },
    null,
  );
  assert.ok(html.includes("visc-band-zone target"), "관리 밴드는 target 구간이어야 한다");
  assert.ok(!html.includes("visc-band-zone warn"), "경고 한계가 없으면 warn 구간이 없어야 한다");
  assert.ok(!html.includes("visc-band-marker"), "lastValue 가 null 이면 마커가 없어야 한다");
  assert.ok(html.includes("visc-band-center"), "중심은 값이 있으면 파선으로");
}

// 규격 없음 폴백: 관리 한계만 있으면 트랙을 관리 한계 기준으로 그린다(끝 라벨이 관리한계).
{
  const html = controlBandHtml(
    {
      stats: { center: 100, lcl: 90, ucl: 110, lwl: null, uwl: null },
      product: { lower_limit: null, upper_limit: null },
    },
    105,
  );
  assert.ok(html.includes("visc-band"), "관리 한계만 있어도 밴드가 그려져야 한다");
  assert.ok(html.includes(">90.0<"), "규격이 없으면 트랙 왼쪽 끝은 관리 하한(90.0)");
  assert.ok(html.includes(">110.0<"), "규격이 없으면 트랙 오른쪽 끝은 관리 상한(110.0)");
  assert.ok(html.includes("105.0"), "최근 측정값(105.0) 라벨이 있어야 한다");
}

// 데이터 없음: analysis 가 없거나 규격·관리한계 모두 없으면 빈 문자열.
{
  assert.equal(controlBandHtml(null, 100), "", "analysis 가 null 이면 빈 문자열");
  assert.equal(controlBandHtml({}, 100), "", "stats/product 가 없으면 빈 문자열");
  assert.equal(
    controlBandHtml({ stats: { center: 100 }, product: { lower_limit: null, upper_limit: null } }, 100),
    "",
    "규격·관리한계 모두 없으면 빈 문자열",
  );
  assert.equal(
    controlBandHtml({ stats: { center: 100, lcl: null, ucl: null }, product: { lower_limit: null, upper_limit: null } }, 100),
    "",
    "관리한계가 null 이고 규격도 없으면 빈 문자열",
  );
}

// 마커 위치 클램프: 최근값이 규격을 벗어나도 마커는 트랙 끝(0~100%)에 붙는다.
{
  const html = controlBandHtml(
    {
      stats: { center: 5000, lcl: 4850, ucl: 5150, lwl: null, uwl: null },
      product: { lower_limit: 4800, upper_limit: 5200 },
    },
    99999,  // 규격 상한을 크게 벗어남
  );
  assert.ok(html.includes("left:100.00%"), "트랙을 벗어난 값은 100% 에 클램프");
}


// ── 한계 밖 값에서도 막대가 보이는가 (2026-08-08) ─────────────────────────
// suggestedMin/Max 는 범위를 넓히기만 한다. 관리한계만 후보로 두면, 데이터 최솟값이
// suggestedMin 보다 낮을 때 Chart.js 가 축 하한을 그 값에 정확히 맞춰 그 막대의 윗변이
// 축 바닥과 겹치고 높이가 0 이 된다 — 하필 그 값이 이상 측정이라 가장 봐야 할 막대만
// 사라졌다(PB 목표 50, 이상 35.0 관측).
{
  const bounds = periodChartYBounds(
    { center: 50, lcl: 41.2, ucl: 58.8, min: 35, max: 54.5 },
    { lower_limit: 45, upper_limit: 55 },
    [{ mean: 48 }, { mean: 35 }, { mean: 51 }],
  );
  assert.ok(bounds.suggestedMin < 35,
    `축 하한(${bounds.suggestedMin})은 데이터 최솟값 35 보다 낮아야 막대가 보인다`);
  assert.ok(bounds.suggestedMax > 58.8, "상한은 관리상한보다 위여야 한다");
}

// 한계 안쪽 데이터만 있으면 눈금이 기간마다 흔들리지 않게 하려던 원래 의도는 유지된다.
{
  const bounds = periodChartYBounds(
    { center: 50, lcl: 41.2, ucl: 58.8, min: 48, max: 52 },
    { lower_limit: 45, upper_limit: 55 },
    [{ mean: 49 }, { mean: 51 }],
  );
  assert.ok(bounds.suggestedMin < 41.2, "하한은 여전히 관리하한보다 아래");
  assert.ok(bounds.suggestedMax > 58.8, "상한은 여전히 관리상한보다 위");
}

// periods 를 안 넘기는 옛 호출부도 그대로 동작한다.
{
  const bounds = periodChartYBounds({ center: 50, lcl: 45, ucl: 55 }, {});
  assert.ok(Number.isFinite(bounds.suggestedMin) && Number.isFinite(bounds.suggestedMax));
}

// 규격 상·하한 선 — 이상 판정 근거가 그림에 있어야 한다.
{
  // vm 컨텍스트가 다르면 배열 프로토타입도 달라 deepEqual 이 참조 비교로 실패한다 —
  // 위에서 한 번 로드한 것을 그대로 쓰고, 비교는 원시값으로만 한다.
  const css = (name) => name;
  const periods = [{ period: "2026-08-01", mean: 50, anomaly_count: 0, warn_count: 0 }];
  const withLimits = periodChartDatasets(periods, 50, css, { lower: 45, upper: 55 });
  const labels = withLimits.datasets.map((d) => d.label);
  assert.ok(labels.includes("규격 하한"), "규격 하한 선이 있어야 한다");
  assert.ok(labels.includes("규격 상한"), "규격 상한 선이 있어야 한다");
  const lower = withLimits.datasets.find((d) => d.label === "규격 하한");
  assert.equal(lower.data.length, 1);
  assert.equal(lower.data[0], 45);

  // 규격이 없는 반제품은 선을 그리지 않는다(없는 기준을 그리면 거짓말이 된다).
  const noLimits = periodChartDatasets(periods, 50, css, { lower: null, upper: null });
  assert.equal(
    noLimits.datasets.filter((d) => d.label.startsWith("규격")).length, 0,
    "규격을 정하지 않은 반제품에는 규격선을 그리지 않는다(Number(null)=0 함정)");
  // limits 인자 자체를 안 주는 옛 호출부도 안전하다.
  assert.equal(periodChartDatasets(periods, 50, css).datasets.length, 2);
}

console.log("viscosity_chart_bounds.test.js OK");

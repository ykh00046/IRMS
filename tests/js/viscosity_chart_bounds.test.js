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

const { periodChartYBounds } = loadViscLib();

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

console.log("viscosity_chart_bounds.test.js OK");

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// blend_lot_audit.js 의 순수 헬퍼(window.IRMS.lotAudit)만 검증한다. 파일은 DOM 이
// 없으면 컨트롤러 등록을 건너뛰도록 짜여 있어(`typeof document === "undefined"`)
// window 스텁 하나로 로드된다.
function loadLotAudit() {
  const code = fs.readFileSync("static/js/blend_lot_audit.js", "utf8");
  const win = {};
  const sandbox = { window: win };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return win.IRMS.lotAudit;
}

test("ageBucket — 경과 일수 구간", () => {
  const { ageBucket } = loadLotAudit();
  // 오래됐는데 1차 기록이 없다 = 오타 가능성이 높다 → old 로 강조
  assert.equal(ageBucket(30), "old");
  assert.equal(ageBucket(14), "old");
  assert.equal(ageBucket(13), "warn");
  assert.equal(ageBucket(3), "warn");
  assert.equal(ageBucket(2), "");
  assert.equal(ageBucket(0), "");
  // created_at 파싱 실패(null)는 조용히 0일로 뭉개지 않는다
  assert.equal(ageBucket(null), "unknown");
  assert.equal(ageBucket(undefined), "unknown");
  assert.equal(ageBucket("이상한값"), "unknown");
});

test("fmtG — 총량 표기", () => {
  const { fmtG } = loadLotAudit();
  assert.equal(fmtG(30000), "30,000");
  assert.equal(fmtG(4632.19), "4,632.19");
  assert.equal(fmtG(null), "-");
  assert.equal(fmtG(""), "-");
  assert.equal(fmtG("abc"), "-");
});

test("excessLabels — 두 플래그가 함께 켜질 수 있다", () => {
  const { excessLabels } = loadLotAudit();
  // vm 컨텍스트의 Array 는 호스트 Array 와 프로토타입이 달라 deepStrictEqual 이
  // 구조가 같아도 실패한다 → Array.from 으로 호스트 배열로 옮겨 비교한다.
  const labels = (item) => Array.from(excessLabels(item));
  assert.deepEqual(
    labels({ oversize_total: true, over_limit_g: 5000 }),
    ["+5,000 g"],
  );
  assert.deepEqual(
    labels({ total_bypass_suspect: true, excess_pct: 15.8 }),
    ["+15.8%"],
  );
  assert.deepEqual(
    labels({
      oversize_total: true, over_limit_g: 5000,
      total_bypass_suspect: true, excess_pct: 20,
    }),
    ["+5,000 g", "+20.0%"],
  );
  assert.deepEqual(labels({}), []);
  assert.deepEqual(labels(null), []);
});

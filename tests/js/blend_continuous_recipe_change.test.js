/**
 * 다중 계량 · 레시피 변경 시 계량 셀 초기화(2026-10-21 현장 신고).
 *
 * 레시피를 바꾸면 총 배합량은 비우면서 셀(state.cells)은 비우지 않아, rebuildCells 가 같은
 * 자리의 옛 실측을 새 레시피에 이어 붙였다. 그 실측 때문에 총량 잠금이 걸린 채 총량은
 * 비어 있어 기본량 버튼도 눌리지 않는 막힌 화면이 됐다. 컨트롤러는 IIFE 라 소스 계약으로
 * 고정한다: applyRecipeData 본문에서 셀 초기화가 keepSettings 분기 바깥(항상)에 있어야 하고,
 * 총량 잠금은 총량이 정해졌을 때만 건다.
 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "static", "js", "blend_continuous.js"),
  "utf8",
);

function body(name) {
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} 를 찾지 못했다`);
  const end = src.indexOf("\n  }\n", start);
  return src.slice(start, end);
}

test("applyRecipeData: 레시피가 바뀌면 keepSettings 와 무관하게 셀을 비운다", () => {
  const fn = body("applyRecipeData");
  // 함수 본문 최상위(4칸 들여쓰기)에서 셀을 비운다 — 분기 안(6칸)에만 있으면 실패.
  assert.match(fn, /\n {4}state\.cells = \[\];/, "state.cells = [] 가 분기 바깥에 있어야 한다");
  assert.doesNotMatch(fn, /\n {6}state\.cells = \[\];/, "셀 초기화가 분기 안에만 있으면 옛 실측이 새 레시피로 넘어간다");
  const resetAt = fn.indexOf("state.cells = [];");
  const rebuildAt = fn.indexOf("rebuildCells();");
  assert.ok(resetAt >= 0 && rebuildAt > resetAt, "rebuildCells 전에 셀을 비워야 한다(rebuildCells 는 옛 값을 보존한다)");
});

test("updateContTotalLock: 총량이 비어 있으면 실측이 있어도 잠그지 않는다", () => {
  const fn = body("updateContTotalLock");
  assert.match(fn, /Number\(totalInput\.value\) \|\| 0\) > 0 &&/, "총량 > 0 조건이 잠금에 포함되어야 한다");
});

test("총 배합량 입력 즉시 잠금을 다시 판정한다", () => {
  const handler = src.slice(src.indexOf('$("cont-total").addEventListener("input"'));
  const block = handler.slice(0, handler.indexOf("});"));
  assert.match(block, /updateContTotalLock\(\);/, "총량 입력 핸들러가 updateContTotalLock 을 불러야 한다");
});

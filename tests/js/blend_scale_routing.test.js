/**
 * 저울 PRINT 라우팅 회귀 테스트 — 계량값이 다른 품목으로 새지 않게 잠근다.
 *
 * 같은 부류의 사고가 두 번 났다:
 *   2026-07-22 인라인 추가 입력칸이 포커스 감지에 안 걸려 부족 보충 PRINT 가 샘
 *   2026-08-03 부족 보충 2회차부터 PRINT 가 다음 품목으로 감(현장 신고)
 * 두 번 다 "그 행은 이미 채워져 있으니 폴백이 다음 빈 행을 골랐다"가 원인이라,
 * 우선순위 규칙을 순수 함수로 떼어 여기서 잠근다.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

global.window = {};
require(path.join(__dirname, "..", "..", "static", "js", "blend_lib.js"));
const { pickScaleRow, isAddModeRow } = global.window.IRMS.blendLib;

test("합산 모드가 켜진 행이 최우선", () => {
  assert.strictEqual(
    pickScaleRow({ addModeIdx: 1, addWeighIdx: 2, shortageIdx: 3, focusedIdx: 4 }), 1);
});

test("추가 계량 모달이 열려 있으면 그 행 — 2회차 PRINT 가 새지 않는다", () => {
  // applyAddAmount 가 addModeIdx 를 null 로 되돌린 직후 상태(2026-08-03 사고 지점).
  assert.strictEqual(pickScaleRow({ addModeIdx: null, addWeighIdx: 2 }), 2);
});

test("나눠 담기 3회차도 같은 행에 머문다", () => {
  const ctx = { addModeIdx: null, addWeighIdx: 0, focusedIdx: null };
  assert.strictEqual(pickScaleRow(ctx), 0);
  assert.strictEqual(pickScaleRow(ctx), 0);
  assert.strictEqual(pickScaleRow(ctx), 0);
});

test("부족 모달이 떠 있으면 그 행 — '추가로 채우기' 선택 전에도", () => {
  assert.strictEqual(pickScaleRow({ addModeIdx: null, addWeighIdx: null, shortageIdx: 3 }), 3);
});

test("모달이 sticky 지정·포커스보다 우선", () => {
  assert.strictEqual(
    pickScaleRow({ addWeighIdx: 2, stickyIdx: 5, stickyValid: true, focusedIdx: 6 }), 2);
  assert.strictEqual(
    pickScaleRow({ shortageIdx: 3, stickyIdx: 5, stickyValid: true, focusedIdx: 6 }), 3);
});

test("유효하지 않은 sticky 지정은 무시하고 포커스로", () => {
  assert.strictEqual(
    pickScaleRow({ stickyIdx: 9, stickyValid: false, focusedIdx: 4 }), 4);
});

test("0번 행도 정상 선택된다(falsy 인덱스 함정)", () => {
  assert.strictEqual(pickScaleRow({ addModeIdx: 0 }), 0);
  assert.strictEqual(pickScaleRow({ addWeighIdx: 0 }), 0);
  assert.strictEqual(pickScaleRow({ shortageIdx: 0 }), 0);
  assert.strictEqual(pickScaleRow({ stickyIdx: 0, stickyValid: true }), 0);
  assert.strictEqual(pickScaleRow({ focusedIdx: 0 }), 0);
});

test("해당 없으면 null — 호출부의 '첫 미입력 행' 폴백으로", () => {
  assert.strictEqual(pickScaleRow({}), null);
  assert.strictEqual(pickScaleRow(null), null);
});

test("모달이 열린 행의 PRINT 는 합산(덮어쓰기 금지)", () => {
  assert.strictEqual(isAddModeRow(2, null, 2), true);   // 2회차 이후
  assert.strictEqual(isAddModeRow(2, 2, null), true);   // 합산 모드 켜짐
  assert.strictEqual(isAddModeRow(3, null, 2), false);  // 다른 행은 덮어쓰기
  assert.strictEqual(isAddModeRow(0, null, 0), true);   // 0번 행
  assert.strictEqual(isAddModeRow(0, null, null), false);
});

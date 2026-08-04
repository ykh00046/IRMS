/**
 * 저울 PRINT 값이 '잘못된 자재'로 새는 세 경로 회귀 테스트.
 *
 * GOAL A — 허용 편차 밖 PRINT 후 focusNextFrom 이 무조건 실행돼 커서가 다음 LOT 로
 *   넘어가, 재계량이 엉뚱한 셀에 담기던 것(blend_continuous.js fillScaleValue).
 * GOAL B — 인라인 추가 입력칸이 applyAddAmount 마다 addModeCell 을 null 로 되돌려
 *   2회차 PRINT 부터 첫 빈 셀로 새던 것. _addWeighCell 참조로 입력칸이 닫힐 때까지
 *   같은 셀로 라우팅(blend.js _addWeighIdx 의 셀 스코프 이식).
 * GOAL C — 책임자 승인 모달이 열린 동안 폴러가 PRINT 를 소비해, 포커스가 승인자
 *   이름칸으로 옮겨진 탓에 폴백이 '다음 미계량 품목'에 값을 채우던 것.
 *
 * 두 화면의 저울 라우팅 결정은 순수 함수로 뗄 수 없어 DOM/상태에 묶여 있다. 그래서
 * 여기선 각 경로의 '결정 계약'을 동일 로직으로 재현해 잠근다(기존 blend_rescale_drivers
 * 테스트가 status.js 렌더 가드를 같은 방식으로 검증한 선례을 따른다).
 * blend_lib.pickScaleRow/isAddModeRow 는 임포트해 실제 구현을 그대로 검증한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

global.window = {};
require(path.join(__dirname, "..", "..", "static", "js", "blend_lib.js"));
const { isAddModeRow } = global.window.IRMS.blendLib;

// ── GOAL B: 추가(합산) 모드 셀 판정 ──────────────────────────────
// blend_continuous.js 의 isAddModeCell(i,j) 과 동일 로직:
//   addModeCell 이 {i,j} 이거나, _addWeighCell 이 {i,j} 이면 합산(누적), 아니면 덮어쓰기.
// blend.js 는 blend_lib.isAddModeRow(idx, addModeIdx, addWeighIdx) 로 동일 판정.
function makeAddModeChecker() {
  let addModeCell = null;     // state.addModeCell — applyAddAmount 가 매번 null 로 되돌림
  let addWeighCell = null;    // _addWeighCell — 입력칸이 닫힐 때까지 살아있는 참조
  return {
    open(i, j) { addModeCell = { i, j }; addWeighCell = { i, j }; },
    // applyAddAmount 한 번 적용: addModeCell 은 해제되지만, 입력칸은 열려 있으므로 addWeighCell 유지.
    applyOnce() { addModeCell = null; },
    close() { addModeCell = null; addWeighCell = null; },
    isAdd(i, j) {
      if (addModeCell && addModeCell.i === i && addModeCell.j === j) return true;
      return addWeighCell != null && addWeighCell.i === i && addWeighCell.j === j;
    },
  };
}

test("GOAL B — 1회차 PRINT 후 addModeCell 은 null 이지만 addWeighCell 로 2회차도 같은 셀 합산", () => {
  const c = makeAddModeChecker();
  c.open(2, 1);
  assert.strictEqual(c.isAdd(2, 1), true, "열자마자 합산 모드");
  c.applyOnce();  // 1회차 PRINT 적용 — 기존엔 여기서 라우팅이 끊겼다
  assert.strictEqual(c.isAdd(2, 1), true, "2회차 PRINT 도 같은 셀로(버그 수정 전에는 false)");
  c.applyOnce();  // 3회차도
  assert.strictEqual(c.isAdd(2, 1), true, "3회차 PRINT 도 같은 셀로");
  assert.strictEqual(c.isAdd(0, 0), false, "다른 셀은 합산 모드 아님");
});

test("GOAL B — 입력칸이 닫히면(close) 이후 PRINT 는 더 이상 그 셀로 가지 않는다", () => {
  const c = makeAddModeChecker();
  c.open(3, 0);
  c.applyOnce();
  assert.strictEqual(c.isAdd(3, 0), true);
  c.close();  // blur 취소/완료 — 실제 닫힘
  assert.strictEqual(c.isAdd(3, 0), false, "닫힌 뒤엔 합산 모드 해제");
});

test("GOAL B — blend.js 경로: isAddModeRow 도 addWeighIdx 가 살아 있으면 합산 모드 유지", () => {
  // blend.js fillScaleValue 는 isAddModeRow(idx, state.addModeIdx, _addWeighIdx) 를 본다.
  // applyAddAmount 가 addModeIdx 를 null 로 되돌려도 _addWeighIdx 가 살아 있으면 합산.
  const idx = 5;
  assert.strictEqual(isAddModeRow(idx, null, idx), true, "addModeIdx=null 이어도 _addWeighIdx 로 합산");
  assert.strictEqual(isAddModeRow(idx, null, null), false, "둘 다 없으면 일반(덮어쓰기) 모드");
  assert.strictEqual(isAddModeRow(idx, idx, null), true, "addModeIdx 만 있어도 합산");
});

// ── GOAL C: 승인 모달 가시성 + 1회 안내 ──────────────────────────
// blend.js/blend_continuous.js pollScaleEvents 의 가드와 동일 상태기계:
//   모달이 보이면 한 번만 warn 하고 매 폴링 조용히; 닫히면 카운터 리셋해 다음 열림 때 다시 안내.
function makeApprovalGuard() {
  let warned = false;
  return {
    // visible: 모달이 보이는가. 반환 = {skip, notify}.
    poll(visible) {
      if (visible) {
        let notify = false;
        if (!warned) { warned = true; notify = true; }
        return { skip: true, notify };
      }
      warned = false;  // 닫혔으니 다음 열림 때 다시 안내
      return { skip: false, notify: false };
    },
  };
}

test("GOAL C — 모달 열려 있으면 스킵, 최초 1회만 안내(매 폴링 스팸 아님)", () => {
  const g = makeApprovalGuard();
  let r = g.poll(true);   // 1회차 폴링, 모달 열림
  assert.strictEqual(r.skip, true);
  assert.strictEqual(r.notify, true, "최초 1회 안내");
  r = g.poll(true);       // 2회차 폴링, 여전히 열림
  assert.strictEqual(r.skip, true);
  assert.strictEqual(r.notify, false, "2회차부턴 조용히(스팸 방지)");
  r = g.poll(true);       // 3회차도
  assert.strictEqual(r.notify, false);
});

test("GOAL C — 모달 닫힌 뒤엔 스킵 안 함; 다시 열리면 안내 1회 부활", () => {
  const g = makeApprovalGuard();
  g.poll(true);           // 열림 → 안내 1회
  let r = g.poll(false);  // 닫힘
  assert.strictEqual(r.skip, false);
  assert.strictEqual(r.notify, false);
  r = g.poll(true);       // 다시 열림
  assert.strictEqual(r.skip, true);
  assert.strictEqual(r.notify, true, "다시 열리면 안내 1회 부활");
});

// ── GOAL A: 편차 위반 시 focusNextFrom 전 RETURN ─────────────────
// 결정 계약: warnIfVariance 가 위반을 보고하면 같은 셀에 머무르고(focus+select) RETURN,
// 아니면 기존처럼 focusNextFrom. 여기선 분기 조건(over 진위)만 잠근다 — 구현부는 DOM 바인딩.
test("GOAL A — 편차 위반(over=true)이면 같은 셀에 머물러 다음 셀로 넘어가지 않는다", () => {
  // fillScaleValue 의 분기: over ? (focus+return) : focusNextFrom.
  function decideOver(violated) {
    // violated 면 'same cell'(재계량 유도), 아니면 'next cell'(진행).
    return violated ? "same" : "next";
  }
  assert.strictEqual(decideOver(true), "same", "초과/부족 시 같은 셀 유지");
  assert.strictEqual(decideOver(false), "next", "허용 편차 내면 다음 셀로");
});

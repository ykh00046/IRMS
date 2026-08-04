/**
 * 저울 PRINT 값이 '잘못된 자재'로 새는 경로 회귀 테스트 — **실제 소스 계약**으로 검증.
 *
 * ⚠️ 이 파일의 이전 버전은 라우팅 로직을 테스트 안에서 다시 구현해 검증했다. 그 재구현에
 * 담긴 전제("인라인 입력칸은 적용 후에도 열려 있다")가 사실과 달랐고, 바로 그 잘못된
 * 전제 때문에 2026-08-04 회귀(applyAddAmount 가 _addWeighCell 을 놓지 않아 이후 모든
 * PRINT 가 그 셀에 누적)를 **구조적으로 잡을 수 없었다**. 통과하지만 아무것도 증명하지
 * 못하는 테스트는 없느니만 못하다 — 거짓 안심을 준다.
 *
 * 두 화면의 라우팅 결정은 DOM·상태에 묶여 순수 함수로 뗄 수 없다. 그래서 여기서는 실제
 * 파일의 **해당 함수 본문**을 읽어 계약을 확인한다(파일 전체 grep 은 주석 한 줄에도
 * 통과해 의미가 없으므로 함수 단위로 자른다). 순수 함수로 뗄 수 있는 부분
 * (pickScaleRow/isAddModeRow/varianceVerdict)은 blend_scale_routing.test.js 가
 * 실제 구현을 임포트해 검증한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "..", "static", "js");
const cont = fs.readFileSync(path.join(SRC, "blend_continuous.js"), "utf8");
const blend = fs.readFileSync(path.join(SRC, "blend.js"), "utf8");

/** 이름으로 함수 본문만 잘라낸다. */
function bodyOf(src, name) {
  const start = src.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} 함수를 찾지 못했다`);
  const open = src.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  throw new Error(`${name} 본문의 끝을 찾지 못했다`);
}

test("applyAddAmount 는 잔여가 없으면 _addWeighCell 을 놓는다 (영구 고착 방지)", () => {
  const body = bodyOf(cont, "applyAddAmount");
  assert.match(
    body, /_addWeighCell\s*=\s*null/,
    "적용 후 _addWeighCell 을 놓지 않으면 이후 모든 PRINT 가 이 셀에 계속 합산된다");
  assert.match(
    body, /addPendingCells/,
    "놓는 기준은 '아직 더 담아야 하는가'(addPendingCells) 여야 한다 — 무조건 놓으면 2회차 누수가 되살아난다");
});

test("⚖ 로 다른 셀을 고르면 _addWeighCell 을 놓는다 (우선순위 역전 방지)", () => {
  const body = bodyOf(cont, "setScaleTargetCell");
  assert.match(
    body, /_addWeighCell\s*=\s*null/,
    "_addWeighCell 이 sticky 보다 우선이라, 안 놓으면 방금 고른 셀이 무시된다");
});

test("clearOverContActuals 는 로트 하나만 비운다", () => {
  assert.match(
    cont, /function clearOverContActuals\(\s*lotIndex\s*\)/,
    "로트 인자를 받아야 한다 — 증량 제안은 offerContRescale(j) 로 그 로트 한정이다");
  const body = bodyOf(cont, "clearOverContActuals");
  assert.match(
    body, /lotIndex\s*!=\s*null\s*&&\s*j\s*!==\s*lotIndex/,
    "지정 로트 밖 셀은 건너뛰어야 한다 — 안 그러면 취소 한 번에 다른 로트 계량값까지 지워진다");
});

test("clearOverContActuals 호출부는 모두 로트를 넘긴다", () => {
  const calls = cont.match(/clearOverContActuals\([^)]*\)/g) || [];
  const invocations = calls.filter((c) => !/^clearOverContActuals\(\s*lotIndex/.test(c));
  assert.ok(invocations.length >= 2, `호출부를 찾지 못했다: ${JSON.stringify(calls)}`);
  invocations.forEach((c) => {
    assert.notStrictEqual(
      c.replace(/\s/g, ""), "clearOverContActuals()",
      "인자 없이 부르면 전 로트가 지워진다 — pendingContRescale.j 를 넘겨야 한다");
  });
});

test("편차를 벗어난 PRINT 는 그 셀에 머문다 (다음 로트로 안 넘어감)", () => {
  const body = bodyOf(cont, "fillScaleValue");
  const overIdx = body.search(/warnIfVariance\(/);
  const nextIdx = body.search(/focusNextFrom\(/);
  assert.ok(overIdx !== -1 && nextIdx !== -1, "두 호출이 모두 있어야 한다");
  assert.match(
    body.slice(overIdx, nextIdx), /return\s*;/,
    "편차 초과면 focusNextFrom 전에 return 해야 한다 — 안 그러면 재계량이 다음 로트에 담긴다");
});

test("승인 모달이 열려 있으면 두 화면 모두 PRINT 를 소비하지 않는다", () => {
  [["blend.js", blend], ["blend_continuous.js", cont]].forEach(([name, src]) => {
    const body = bodyOf(src, "pollScaleEvents");
    assert.match(
      body, /approvalModalVisible\(\)/,
      `${name}: 승인 모달은 포커스를 승인자 이름칸으로 옮겨 라우팅 플래그를 전부 비운다 — 폴러가 막아야 한다`);
    const guardIdx = body.indexOf("approvalModalVisible()");
    const fetchIdx = body.indexOf("fetch(");
    assert.ok(
      guardIdx !== -1 && fetchIdx !== -1 && guardIdx < fetchIdx,
      `${name}: 가드는 이벤트를 받아오기 전에 있어야 한다`);
  });
});

test("기준 자재 모드의 배지는 화면 이론량과 같은 목표를 쓴다", () => {
  const body = bodyOf(blend, "renderAddBadges");
  assert.match(
    body, /anchorIndex\s*>=\s*0/,
    "anchor 모드를 구분해야 한다 — rescalePlan 의 ratio 기반 newTheory 는 셀 이론량과 갈린다");
  assert.match(
    body, /theory_amount/,
    "anchor 모드 잔여는 화면이 실제로 쓰는 목표(it.theory_amount)로 계산해야 한다");
});

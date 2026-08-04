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

test("차단 모달이 열려 있으면 두 화면 모두 PRINT 를 소비하지 않는다", () => {
  [["blend.js", blend], ["blend_continuous.js", cont]].forEach(([name, src]) => {
    const body = bodyOf(src, "pollScaleEvents");
    assert.match(
      body, /printBlockingModalVisible\(\)/,
      `${name}: 차단 모달이 떠 있는 동안의 PRINT 는 폴백을 타고 엉뚱한 품목에 꽂힌다 — 폴러가 막아야 한다`);
    const guardIdx = body.indexOf("printBlockingModalVisible()");
    const fetchIdx = body.indexOf("fetch(");
    assert.ok(
      guardIdx !== -1 && fetchIdx !== -1 && guardIdx < fetchIdx,
      `${name}: 가드는 이벤트를 받아오기 전에 있어야 한다`);
  });
});

test("차단 목록은 승인 모달만이 아니라 제안·폐기·3회 차단·LOT 확인 모달까지 덮는다", () => {
  // 2026-08-04 현장 실측: 증량 '제안' 모달이 떠 있는 동안 PRINT 가 다음 품목에 꽂혔다.
  // ("포커스를 안 뺏으니 안전"이라던 86905ab 의 판단이 틀렸다 — 목록 축소 회귀를 여기서 고정.)
  const required = {
    "blend.js": [blend, ["rescale-approve-modal", "manual-approve-modal", "rescale-modal",
      "discard-modal", "rescale-block-modal", "carry-over-modal", "lot-invalid-modal",
      "scale-state-modal"]],
    "blend_continuous.js": [cont, ["cont-rescale-approve-modal", "cont-manual-approve-modal",
      "cont-rescale-modal", "cont-discard-modal", "cont-rescale-block-modal",
      "cont-lot-invalid-modal", "cont-scale-state-modal"]],
  };
  Object.entries(required).forEach(([name, [src, ids]]) => {
    const body = bodyOf(src, "printBlockingModalVisible");
    ids.forEach((id) => {
      assert.ok(
        body.includes(`"${id}"`),
        `${name}: printBlockingModalVisible 목록에 ${id} 가 빠졌다`);
    });
  });
  // 부족/추가 계량 모달은 PRINT 를 자기 행 합산으로 '소비해야 하는' 창 — 목록에 넣으면 안 된다.
  const blendBody = bodyOf(blend, "printBlockingModalVisible");
  ["shortage-modal", "add-weigh-modal"].forEach((id) => {
    assert.ok(
      !blendBody.includes(`"${id}"`),
      `blend.js: ${id} 는 PRINT 소비가 그 창의 역할이다 — 차단 목록에 넣으면 나눠 담기가 죽는다`);
  });
});

/** id 로 addEventListener("click", …) 핸들러 본문을 잘라낸다(등록 변수명 기준). */
function clickHandlerOf(src, varName) {
  const reg = src.indexOf(`${varName}.addEventListener("click"`);
  assert.notStrictEqual(reg, -1, `${varName} click 핸들러 등록을 찾지 못했다`);
  const open = src.indexOf("{", reg);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  throw new Error(`${varName} click 핸들러의 끝을 찾지 못했다`);
}

test("부족·추가 계량 모달은 바깥 클릭으로 닫히지 않는다", () => {
  // 2026-08-04 현장 신고: 나눠 담기/부족 채우기 창이 바깥 클릭 한 번에 사라져
  // 회차 진행 내역을 잃었다. 바깥 클릭은 안내만 하고, 나가는 길은 버튼뿐이어야 한다.
  const shortageHandler = clickHandlerOf(blend, "shortageOverlay");
  assert.doesNotMatch(
    shortageHandler, /shortageChooseReweigh|closeShortageModal/,
    "부족 모달 바깥 클릭이 창을 닫으면 선택 없이 빠져나간다 — 안내만 해야 한다");
  const awHandler = clickHandlerOf(blend, "awModal");
  assert.doesNotMatch(
    awHandler, /closeAddWeighModal|finishAddWeighModal/,
    "추가 계량 모달 바깥 클릭이 창을 닫으면 나눠 담기 진행 내역이 사라진다 — 안내만 해야 한다");
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

// ── 저울 상태 선택(2026-08-04 시안) — 진입·해석 계약 ─────────────────
test("추가 계량 진입은 전부 저울 상태 선택을 거친다", () => {
  // 진입문을 우회하면 상태 선택 없이 합산 모드가 열려, 영점 안 잡힌 저울의
  // PRINT(누계)가 이중 계산된다. 버튼/배지/부족 모달의 직접 open* 호출 금지.
  const direct = (blend.match(/openAddWeighModal\(/g) || []).length;
  // 허용: 함수 정의 1 + chooseScaleState 1 + openScaleStateModal 폴백 1 + 주석 언급
  const defs = (blend.match(/function openAddWeighModal\(/g) || []).length;
  assert.strictEqual(defs, 1, "정의는 하나여야 한다");
  assert.match(bodyOf(blend, "shortageChooseAdd"), /requestAddWeigh\(/,
    "부족 모달의 '추가로 채우기'는 상태 선택을 거쳐야 한다");
  assert.match(bodyOf(blend, "buildSplitButton"), /requestAddWeigh\(/,
    "⊞ 나눠 담기 버튼은 상태 선택을 거쳐야 한다");
  assert.match(bodyOf(cont, "requestAddInline"), /openContScaleStateModal\(/,
    "다중 계량 추가 입력도 상태 선택을 거쳐야 한다");
  assert.ok(direct >= defs, "sanity");
});

test("PRINT 는 두 화면 모두 상태 선택(resolveAddPortion)으로 환산된다", () => {
  [["blend.js", blend], ["blend_continuous.js", cont]].forEach(([name, src]) => {
    assert.match(bodyOf(src, "fillScaleValue"), /resolveAddPortion\(/,
      `${name}: 추가 모드 PRINT 를 환산 없이 합산하면 누계 상태에서 이중 계산된다`);
  });
});

test("부족 모달 중 PRINT 는 상태 선택 전이므로 소비하지 않는다", () => {
  const body = bodyOf(blend, "pollScaleEvents");
  const at = body.indexOf("_shortageIdx != null");
  assert.notStrictEqual(at, -1, "부족 모달 전환 분기가 있어야 한다");
  const after = body.slice(at, at + 400);
  assert.match(after, /break\s*;/,
    "전환 후 그 PRINT 를 이어서 소비하면 해석 방법이 정해지기 전의 값이 합산된다");
});

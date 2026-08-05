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
const statusSrc = fs.readFileSync(path.join(SRC, "status.js"), "utf8");

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
      "scale-state-modal", "discard-ask-modal"]],
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

test("추가 계량 모달은 바깥 클릭으로 닫히지 않는다", () => {
  // 2026-08-04 현장 신고: 나눠 담기/부족 채우기 창이 바깥 클릭 한 번에 사라져
  // 회차 진행 내역을 잃었다. 바깥 클릭은 안내만 하고, 나가는 길은 버튼뿐이어야 한다.
  // (부족 모달은 상태 선택 모달로 통합돼 소스에서 사라졌다 — 여기서는 추가 계량 창만 검사.)
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
  // PRINT(누계)가 이중 계산된다. 부족 감지는 이제 상태 선택 모달을 직접 연다(부족 창 통합).
  const direct = (blend.match(/openAddWeighModal\(/g) || []).length;
  // 허용: 함수 정의 1 + chooseScaleState 1 + openScaleStateModal 폴백 1 + 주석 언급
  const defs = (blend.match(/function openAddWeighModal\(/g) || []).length;
  assert.strictEqual(defs, 1, "정의는 하나여야 한다");
  // 부족 감지가 상태 선택 모달을 직접 연다 — showShortageModal 흐름은 소스에서 제거됐다.
  assert.match(bodyOf(blend, "warnIfVariance"), /openScaleStateModal\(/,
    "부족 감지가 상태 선택 모달을 직접 열어야 한다");
  assert.ok(!blend.includes("showShortageModal"),
    "blend 소스에 showShortageModal 이 남아 있으면 부족 창 통합이 덜 끝난 것이다");
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

test("부족 흐름 통합 후 폴러에 부족 분기가 남아 있지 않다", () => {
  // 부족 모달 자체가 사라졌으므로 폴러의 _shortageIdx 분기도 함께 제거돼야 한다 —
  // 남아 있으면 미해석 PRINT 를 소비하거나 제거된 함수(shortageChooseAdd)를 부른다.
  const body = bodyOf(blend, "pollScaleEvents");
  assert.doesNotMatch(body, /_shortageIdx/,
    "pollScaleEvents 에 _shortageIdx 분기가 남아 있으면 부족 창 통합이 덜 끝난 것이다");
  // 부족 흐름은 scale-state-modal 로 통합됐다 — 그 창이 떠 있을 때 PRINT 는 게이트가 버린다.
  assert.ok(
    bodyOf(blend, "printBlockingModalVisible").includes('"scale-state-modal"'),
    'printBlockingModalVisible 목록에 "scale-state-modal" 이 있어야 한다 — 부족 흐름 통합 후 이 창이 PRINT 를 막는다');
});

/** keydown 핸들러(문서 등록) 본문을 anchor 부분문자열로 식별해 잘라낸다.
 *  여러 개의 document.addEventListener("keydown", …) 등록 중 anchor 가 들어 있는
 *  콜백 본문을 반환한다(기존 clickHandlerOf 와 같은 방식 — 변수명 대신 anchor 식별). */
function keydownHandlerOf(src, anchor) {
  let from = 0;
  while (true) {
    const reg = src.indexOf('document.addEventListener("keydown"', from);
    if (reg === -1) break;
    const open = src.indexOf("{", reg);
    let depth = 0;
    let end = -1;
    for (let i = open; i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}") {
        depth--;
        if (depth === 0) { end = i; break; }
      }
    }
    if (end === -1) break;
    const body = src.slice(open, end + 1);
    if (body.includes(anchor)) return body;
    from = end + 1;
  }
  throw new Error(`anchor "${anchor}" 가 들어간 keydown 핸들러를 찾지 못했다`);
}

test("부족 컨텍스트에서 scale-state 모달 Esc 는 닫지 않고 안내만 한다", () => {
  // 부족 창 통합 후 저울 상태 선택 모달이 부족 컨텍스트(_scaleStateShortageIdx != null)를
  // 띄운다. 이때 Esc 가 창을 닫으면 부족 자재가 미해결로 남으므로, 닫지 않고 notify 만 해야
  // 한다(나가는 길은 그림 둘 또는 [처음부터 다시 계량] 뿐). 등록 지점을 잘라 검사한다.
  const handler = keydownHandlerOf(blend, 'e.key === "Escape" && ssModal');
  // 부족 컨텍스트 분기가 있어야 한다.
  assert.match(handler, /_scaleStateShortageIdx\s*!=\s*null/,
    "부족 컨텍스트(_scaleStateShortageIdx != null) 분기가 있어야 한다");
  // 그 분기 본문은 notify 만 해야 한다 — closeScaleStateModal 이 껴 있으면 봉인이 깨진다.
  const branchAt = handler.indexOf("_scaleStateShortageIdx");
  // 분기 조건문 다음 블록(close 전까지)만 잘라 closeScaleStateModal 이 없는지 확인.
  const after = handler.slice(branchAt);
  // 같은 핸들러 안 else 쪽 closeScaleStateModal 은 정상이므로, 부족 분기 블록 내부만 검사:
  // '_scaleStateShortageIdx != null) {' 직후부터 대응 '}' 닫기 전까지.
  const condOpen = after.indexOf("{");
  let depth = 0;
  let branchEnd = -1;
  for (let i = condOpen; i < after.length; i++) {
    if (after[i] === "{") depth++;
    else if (after[i] === "}") {
      depth--;
      if (depth === 0) { branchEnd = i; break; }
    }
  }
  assert.notStrictEqual(branchEnd, -1, "부족 분기 블록의 끝을 찾지 못했다");
  const branchBody = after.slice(condOpen, branchEnd + 1);
  assert.ok(
    !branchBody.includes("closeScaleStateModal"),
    "부족 컨텍스트에서 Esc 가 closeScaleStateModal 을 부르면 봉인이 깨진다 — notify 만 해야 한다");
  assert.match(branchBody, /notify\(/,
    "부족 컨텍스트 Esc 분기는 안내(notify)를 내보내야 한다");
});

test("담기 창의 큰 숫자는 모드별로 다르다 — 누계 모드는 목표 전체(2026-08-05 현장 지적)", () => {
  // 두 모드가 같은 '더 담아야 할 양'을 띄우면, 누계 모드에서 저울 표시가 그 값이
  // 될 때까지만 담는 실수를 부른다(표시창은 전체 무게인데). 회귀 고정.
  const body = bodyOf(blend, "refreshAddWeighModal");
  assert.match(body, /_awMode === "loaded"/, "모드 분기가 있어야 한다");
  assert.match(body, /저울 표시가 이 값이 될 때까지/, "누계 모드 라벨 = 표시창 기준 목표");
  assert.match(body, /is-cumulative/, "누계 모드는 색으로도 구분한다");
});

// F8 — 상세 증량 이력(rescaleBlock)은 마스킹된 rescaleMap 이 아닌 rec.rescale_events_json
// 에서 그려야 정식 승인 건이 '(책임자 부재)'로 허위 표시되지 않는다.
test("rescaleBlock(status.js) 은 rescaleMap 이 아닌 rec.rescale_events_json 에서 이벤트를 그린다", () => {
  const body = bodyOf(statusSrc, "rescaleBlock");
  assert.match(body, /rescale_events_json/,
    "rescaleBlock 은 rec.rescale_events_json(JSON.parse) 에서 이벤트를 읽어야 한다 — rescaleMap 은 마스킹판");
  // 마스킹판 rescaleMap 의 rescale_events 에서 직접 읽으면 안 된다.
  assert.ok(
    !/info\.rescale_events\b|rescaleMap\[.+\]\.rescale_events/.test(body),
    "rescaleMap.rescale_events(마스킹)에서 읽으면 정식 승인 건도 부재로 허위 표시된다",
  );
  // 미확인 플래그·[확인 처리] 버튼 판정은 rescaleMap 을 계속 쓴다(별개 목적).
  assert.match(body, /rescale_unacked/,
    "미확인 플래그·[확인 처리] 버튼 판정은 rescaleMap.rescale_unacked 를 계속 쓴다");
});

// F2 — 총 배합량 미입력(이론량 없음) 상태에서 나눠 담기/추가 계량 진입을 막는 가드.
test("requestAddWeigh(blend.js) 는 theory_amount 가 0/없으면 진입을 막는다", () => {
  const body = bodyOf(blend, "requestAddWeigh");
  assert.match(body, /theory_amount/,
    "requestAddWeigh 는 state.items[idx].theory_amount 가드를 가져야 한다 — 목표 0인 무의미 흐름 방지");
  assert.match(body, /총 배합량을 먼저 입력하세요/,
    "이론량이 없으면 '총 배합량을 먼저 입력하세요' 안내 후 돌아가야 한다");
  assert.match(body, /blend-total/,
    "가드에 걸리면 총 배합량 입력(#blend-total)으로 포커스를 보내야 한다");
});

// 게이트 해제 직후 첫 PRINT 무음 소실 방지 — 차단 모달이 열려 있는 동안에도 이벤트
// 커서(scaleEventLast)는 전진시켜 stale 을 그 자리에서 버려야 한다. 종전처럼 synced=false
// 로 두면 닫힌 뒤 첫 폴을 통째로 재동기화로 삼켜, 그 폴 주기(≤0.8s) 안에 들어온
// '그림 선택 직후의 유효한 PRINT'까지 조용히 사라졌다(주행 재현 2026-08-05).
for (const [label, src] of [["blend.js", blend], ["blend_continuous.js", cont]]) {
  test(`pollScaleEvents(${label}) 게이트 분기는 커서를 전진시키고 synced 를 유지한다`, () => {
    const body = bodyOf(src, "pollScaleEvents");
    const gateStart = body.indexOf("printBlockingModalVisible()");
    assert.notStrictEqual(gateStart, -1, "게이트 분기(printBlockingModalVisible)가 있어야 한다");
    const gateEnd = body.indexOf("_modalPrintWarned = false", gateStart);
    assert.notStrictEqual(gateEnd, -1, "게이트 분기의 끝 표식(_modalPrintWarned = false)을 찾지 못했다");
    const gate = body.slice(gateStart, gateEnd);
    assert.match(gate, /scaleEventLast\s*=/,
      "게이트 분기 안에서 이벤트 커서(scaleEventLast)를 전진시켜 stale 을 즉시 버려야 한다");
    assert.ok(!gate.includes("scaleEventSynced = false"),
      "게이트 분기가 synced=false 로 되돌리면 닫힌 뒤 첫 폴이 유효 PRINT 까지 삼킨다");
  });
}

// 투입 로스 보정 분해 표시(2라운드 2026-08-05) — 상세 이론량 옆 배지가 loss_comp_g 를 참조.
test("status.js 상세 이론 셀은 detail.loss_comp_g>0 일 때 보정 배지를 그린다", () => {
  assert.match(statusSrc, /loss_comp_g/,
    "status.js detail 행이 detail.loss_comp_g 를 참조해야 한다 — 없으면 분해 배지가 안 그려진다");
  assert.match(statusSrc, /blend-losscomp-badge/,
    "status.js 가 blend-losscomp-badge 클래스를 써야 한다 — 보정 분해 표시");
});

// 배합·다중 계량 이론 셀도 보정 배지를 그린다.
test("배합(blend_lib materialRowHtml)·다중 계량 이론 셀은 보정 자재에 배지를 그린다", () => {
  const blendLib = fs.readFileSync(path.join(SRC, "blend_lib.js"), "utf8");
  assert.match(blendLib, /blend-losscomp-badge/, "blend_lib.js materialRowHtml 이론 셀에 보정 배지 클래스");
  assert.match(cont, /blend-losscomp-badge/, "blend_continuous.js 이론 셀에 보정 배지 클래스");
});

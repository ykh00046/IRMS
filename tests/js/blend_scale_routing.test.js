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

// ── 셀 형태({i,j} 객체) — 다중 계량 화면. pickScaleRow/isAddModeRow 는 위치를
// 불투명하게 다루므로 행 인덱스와 같은 규칙·같은 우선순위가 그대로 성립해야 한다.
// 동등 판정은 호출부가 넘기는 eq 로(배합====, 다중 계량=cellEq). 0번 셀(0,0) 함정 포함.
const cellEq = (a, b) => !!a && !!b && a.i === b.i && a.j === b.j;

test("[셀] 합산 모드가 켜진 셀이 최우선", () => {
  const picked = pickScaleRow({
    addModeIdx: { i: 1, j: 1 },
    addWeighIdx: { i: 2, j: 0 },
    stickyIdx: { i: 3, j: 0 },
    stickyValid: true,
    focusedIdx: { i: 4, j: 0 },
  });
  assert.deepStrictEqual(picked, { i: 1, j: 1 });
});

test("[셀] 추가 입력칸이 열려 있으면 그 셀 — 2회차 PRINT 가 새지 않는다", () => {
  // applyAddAmount 가 addModeCell 을 null 로 되돌린 직후 상태.
  const picked = pickScaleRow({ addModeIdx: null, addWeighIdx: { i: 2, j: 1 } });
  assert.deepStrictEqual(picked, { i: 2, j: 1 });
});

test("[셀] 유효하지 않은 sticky 는 무시하고 포커스로", () => {
  assert.deepStrictEqual(
    pickScaleRow({ stickyIdx: { i: 9, j: 9 }, stickyValid: false, focusedIdx: { i: 4, j: 2 } }),
    { i: 4, j: 2 });
});

test("[셀] 0번 셀(0,0)도 정상 선택된다(falsy 인덱스 함정)", () => {
  const z = { i: 0, j: 0 };
  assert.deepStrictEqual(pickScaleRow({ addModeIdx: z }), z);
  assert.deepStrictEqual(pickScaleRow({ addWeighIdx: z }), z);
  assert.deepStrictEqual(pickScaleRow({ stickyIdx: z, stickyValid: true }), z);
  assert.deepStrictEqual(pickScaleRow({ focusedIdx: z }), z);
});

test("[셀] 해당 없으면 null — 호출부 폴백으로", () => {
  assert.strictEqual(pickScaleRow({}), null);
});

test("[셀] 열려 있는 셀의 PRINT 는 합산(덮어쓰기 금지) — eq 로 동등 판정", () => {
  const pos = { i: 2, j: 1 };
  assert.strictEqual(isAddModeRow(pos, null, { i: 2, j: 1 }, cellEq), true);   // 2회차 이후
  assert.strictEqual(isAddModeRow(pos, { i: 2, j: 1 }, null, cellEq), true);   // 합산 모드 켜짐
  assert.strictEqual(isAddModeRow({ i: 3, j: 1 }, null, { i: 2, j: 1 }, cellEq), false);  // 다른 셀
  assert.strictEqual(isAddModeRow({ i: 0, j: 0 }, null, { i: 0, j: 0 }, cellEq), true);   // 0번 셀
  assert.strictEqual(isAddModeRow(pos, null, null, cellEq), false);
});

// ── 허용 편차 판정 단일 헬퍼(varianceVerdict) — 비교 전 반올림 없음, +1e-9 엡실론.
const { varianceVerdict } = global.window.IRMS.blendLib;

test("[편차] 허용 편차 이내면 within, 초과/부족 플래그 모두 거짓", () => {
  const v = varianceVerdict(100.03, 100.0, 0.05);
  assert.strictEqual(v.within, true);
  assert.strictEqual(v.over, false);
  assert.strictEqual(v.short, false);
});

test("[편차] +초과면 over, 부족이면 short — 부호와 무관하게 within 은 거짓", () => {
  assert.strictEqual(varianceVerdict(100.06, 100.0, 0.05).over, true);
  assert.strictEqual(varianceVerdict(100.06, 100.0, 0.05).within, false);
  assert.strictEqual(varianceVerdict(99.90, 100.0, 0.05).short, true);
  assert.strictEqual(varianceVerdict(99.90, 100.0, 0.05).within, false);
});

test("[편차] 비교 전 반올림 없음 — 3자리/2자리 반올림이 판정을 바꾸지 않는다", () => {
  // 예전 blend.js rowVariance(3자리 반올림)와 raw 비교가 0.0005 경계에서 어긋났다.
  // 헬퍼는 raw 로 판정 — 정확히 tol+엡실론 경계의 값이 반올림에 뒤집히지 않는다.
  const v = varianceVerdict(100.0503, 100.0, 0.05);
  assert.strictEqual(v.variance, 100.0503 - 100.0);
  assert.strictEqual(v.over, true);   // 0.0503 > 0.05 + 1e-9
});

test("[편차] toleranceG 미지정/0 이하 → 기본 TOLERANCE_G(0.05)", () => {
  assert.strictEqual(varianceVerdict(100.03, 100.0).within, true);
  assert.strictEqual(varianceVerdict(100.06, 100.0, 0).over, true);
});

// ── 저울 상태 선택(2026-08-04 시안) — PRINT/입력값 해석 ─────────────
const { resolveAddPortion } = global.window.IRMS.blendLib;

test("영점 잡힘(tared): 값 = 추가분 그대로", () => {
  assert.deepStrictEqual(resolveAddPortion("tared", 40, 400), { ok: true, portion: 40 });
});

test("무게 남음(loaded): 추가분 = 표시값 - 현재 누계", () => {
  // 95 담긴 상태에서 5 더 붓고 PRINT → 100. 합산하면 195(이중 계산), 환산하면 +5.
  assert.deepStrictEqual(resolveAddPortion("loaded", 100, 95), { ok: true, portion: 5 });
});

test("loaded: 표시값이 현재 이하면 적용 불가 — 비커 교체/상태 오선택 신호", () => {
  assert.strictEqual(resolveAddPortion("loaded", 90, 95).ok, false);
  assert.strictEqual(resolveAddPortion("loaded", 90, 95).reason, "not-above-current");
  assert.strictEqual(resolveAddPortion("loaded", 95, 95).ok, false);  // 0 추가도 기록 금지
});

test("0 이하·비수치 값은 모드와 무관하게 거부", () => {
  assert.strictEqual(resolveAddPortion("tared", 0, 10).ok, false);
  assert.strictEqual(resolveAddPortion("tared", -5, 10).ok, false);
  assert.strictEqual(resolveAddPortion("loaded", NaN, 10).ok, false);
});

test("저울 해상도(2자리) 반올림 — 3자리가 실제량에 스며들지 않게", () => {
  assert.strictEqual(resolveAddPortion("loaded", 100.126, 95).portion, 5.13);
  assert.strictEqual(resolveAddPortion("tared", 40.126, 0).portion, 40.13);
});

// ── 증량 제안 비율 막대(P-4, 2026-08-05) ───────────────────────────
const { rescaleBarsHtml, rescalePlan } = global.window.IRMS.blendLib;

test("증량 막대: 담은 자재는 채움/더 담을 양, 미계량 자재는 예정으로 그린다", () => {
  const items = [
    { material_name: "A", ratio: 50, actual_amount: "600", theory_amount: 500 },
    { material_name: "B", ratio: 30, actual_amount: "300", theory_amount: 300 },
    { material_name: "C", ratio: 20, actual_amount: "", theory_amount: 200 },
  ];
  const plan = rescalePlan(items, 1000, 0.05);  // A 초과 → newTotal 1200
  const html = rescaleBarsHtml(items, plan, 2);
  assert.match(html, /rescale-bars/);
  assert.ok(html.includes("채움 ✓"), "새 이론량에 정확히 도달한 자재(A 600/600)는 채움 표시");
  assert.match(html, /\+60\.00 g 더/, "담았지만 모자란 자재(B 300/360)는 더 담을 양");
  assert.ok(!html.includes("+0.00 g 더"), "0 추가는 '더'가 아니라 채움으로");
  assert.match(html, /예정 240\.00 g/, "미계량 자재는 예정 막대");
  assert.match(html, /width:100%/, "최대 자재(A) 막대는 100%");
});

test("증량 막대: 계획이 비면 빈 문자열(모달 본문 오염 금지)", () => {
  assert.strictEqual(rescaleBarsHtml([], { rows: [] }, 2), "");
});

// ── 투입 로스 보정(2라운드 2026-08-05) — 순수 이론·증량 산술에 보정 반영 ──
const {
  computeTheoryAmount,
  theoryFromWeights,
  computeAnchorTheory,
} = global.window.IRMS.blendLib;

test("rescalePlan: 보정 자재의 newTheory = 비율×신총량+보정, addNeeded 도 그 기준", () => {
  // A(50%) 보정 +1g, B(30%) 보정 없음, C(20%) 미계량. A 를 600 으로 초과 → newTotal 1200.
  const items = [
    { material_name: "A", ratio: 50, actual_amount: "600", theory_amount: 501, loss_comp_g: 1 },
    { material_name: "B", ratio: 30, actual_amount: "300", theory_amount: 300, loss_comp_g: 0 },
    { material_name: "C", ratio: 20, actual_amount: "", theory_amount: 200, loss_comp_g: 0 },
  ];
  const plan = rescalePlan(items, 1000, 0.05);
  const a = plan.rows[0];
  const b = plan.rows[1];
  // A: 비율×1200 = 600 + 보정 1 = 601
  assert.strictEqual(a.newTheory, 601, "보정 자재 newTheory = 비율×신총량+보정(601)");
  assert.strictEqual(a.addNeeded, 1, "A 를 600 담았고 목표 601 → addNeeded = 1");
  // B: 비율×1200 = 360 (보정 없음)
  assert.strictEqual(b.newTheory, 360, "보정 없는 자재는 비율×신총량 그대로(360)");
  assert.strictEqual(b.addNeeded, 60, "B 300 담았고 목표 360 → +60");
});

test("computeTheoryAmount: lossComp 가 있으면 비율×총량+보정", () => {
  assert.strictEqual(computeTheoryAmount(50, 1000), 500, "보정 없으면 비율×총량");
  assert.strictEqual(computeTheoryAmount(50, 1000, 1), 501, "보정 1g → 501");
  assert.strictEqual(computeTheoryAmount(30, 1000, 2.5), 302.5, "보정 2.5g → 302.5");
});

test("theoryFromWeights: value_weight 비례 결과에 loss_comp_g 를 더한다", () => {
  // value_weight 60/40, 총량 100 → 60/40. A 보정 +1 → 61.
  const items = [
    { value_weight: 60, loss_comp_g: 1 },
    { value_weight: 40, loss_comp_g: 0 },
  ];
  const out = theoryFromWeights(items, 100);
  assert.strictEqual(out[0], 61, "A = 60 + 보정 1");
  assert.strictEqual(out[1], 40, "B = 40 (보정 없음)");
  // 보정 없는 기존 계약 유지
  const plain = theoryFromWeights([{ value_weight: 60 }, { value_weight: 40 }], 100);
  assert.deepStrictEqual(plain, [60, 40], "보정 필드 없으면 기존과 동일");
});

test("computeAnchorTheory: 비기준 자재 파생 이론량에 보정 더한다(기준 자재는 무시)", () => {
  // 기준 A(value_weight 100) 실측 1000, 비기준 B(value_weight 50) → 파생 500 + 보정 1 = 501.
  const items = [
    { value_weight: 100, is_anchor: true, loss_comp_g: 5 },  // 기준 자재 — 보정 무시(실측=이론)
    { value_weight: 50, loss_comp_g: 1 },                     // 비기준 — 파생 500 + 1 = 501
  ];
  const { theoryAmounts } = computeAnchorTheory(items, 0, 1000);
  assert.strictEqual(theoryAmounts[0], 1000, "기준 자재는 실측 그대로(보정 무시)");
  assert.strictEqual(theoryAmounts[1], 501, "비기준 자재 파생량 + 보정(501)");
});

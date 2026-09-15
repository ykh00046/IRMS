/**
 * 계량 완료 저장 확인 창(save-confirm-modal) 회귀 테스트 — 실제 소스 계약으로 검증.
 *
 * 2026-09-12 사고: 자재 전부를 계량하고도 저장을 누르지 않아 배합 기록이 하나도
 * 남지 않았다. 그 대책으로 ①계량 완료 순간 저장 확인 창을 자동으로 띄우고
 * ②저장 버튼의 window.confirm 을 같은 창으로 통일했다. 배합(blend.js)과
 * 다중 계량(blend_continuous.js) 두 화면이 같은 규칙을 유지하는지 여기서 잠근다
 * (blend_scale_leaks.test.js 와 같은 방식 — DOM·네트워크에 묶인 로직은 순수 함수로
 * 뗄 수 없어 소스 단위로 자른다).
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "..", "static", "js");
const blend = fs.readFileSync(path.join(SRC, "blend.js"), "utf8");
const cont = fs.readFileSync(path.join(SRC, "blend_continuous.js"), "utf8");

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

test("저울 PRINT 차단 목록에 저장 확인 창이 포함된다", () => {
  assert.ok(
    bodyOf(blend, "printBlockingModalVisible").includes('"save-confirm-modal"'),
    'printBlockingModalVisible 목록에 "save-confirm-modal" 이 빠지면 창이 열려 있는 동안 PRINT 가 엉뚱한 품목에 꽂힌다',
  );
});

test("saveBlendInner 은 window.confirm 대신 confirmSaveModal 로 확인한다", () => {
  const body = bodyOf(blend, "saveBlendInner");
  assert.ok(
    body.includes("confirmSaveModal("),
    "저장 직전 확인은 confirmSaveModal 이어야 한다 — 브라우저 확인 창은 계량 완료 자동 제안과 다른 모양으로 뜬다",
  );
  assert.ok(
    !body.includes("window.confirm(`작업자"),
    "예전 window.confirm 이 saveBlendInner 에 남아 있으면 두 저장 경로가 갈라진다",
  );
});

test("confirmSaveModal — 키보드 클릭은 저장하지 않고, 닫는 길은 '다시 보기'다", () => {
  const body = bodyOf(blend, "confirmSaveModal");
  assert.match(
    body, /detail\s*===\s*0/,
    "event.detail === 0(Enter/Space 로 만든 클릭)을 저장으로 받으면 포커스 이동만으로 저장이 확정된다",
  );
  assert.ok(
    body.includes("save-confirm-review"),
    "'다시 보기'(#save-confirm-review) 버튼이 취소 경로여야 한다",
  );
});

test("saveOfferReady — 자동 제안이 떠도 되는 조건을 모두 검사한다", () => {
  const body = bodyOf(blend, "saveOfferReady");
  [
    "printBlockingModalVisible()",
    "saveOfferDismissed",
    "pendingRescale",
    "missingLotNames",
    "blendWindowBlocked",
    "use_reactor",
  ].forEach((needle) => {
    assert.ok(
      body.includes(needle),
      `saveOfferReady 에 ${needle} 검사가 없다 — 조건 하나를 빼면 안 떠도 될 때 뜨거나, 떠야 할 때 안 뜬다`,
    );
  });
});

test("updateUnsavedNote 는 계량 완료 전환 순간 제안을 예약한다", () => {
  assert.ok(
    bodyOf(blend, "updateUnsavedNote").includes("scheduleSaveOffer()"),
    "완료 전환 시 scheduleSaveOffer() 를 불러야 자동 제안이 뜬다",
  );
});

test("saveOfferDismissed 는 세 곳에서 초기화된다(레시피 변경·저장 성공·초안 복구)", () => {
  const count = (blend.match(/saveOfferDismissed = false/g) || []).length;
  assert.ok(
    count >= 3,
    `saveOfferDismissed = false 가 ${count}곳뿐이다 — 레시피 변경·저장 성공·초안 복구 세 곳이어야 한다`,
  );
});

// ── 다중 계량(blend_continuous.js) — 같은 규칙의 cont- 판 ─────────────────

test("다중 계량: 저울 PRINT 차단 목록에 저장 확인 창이 포함된다", () => {
  assert.ok(
    bodyOf(cont, "printBlockingModalVisible").includes('"cont-save-confirm-modal"'),
    'printBlockingModalVisible 목록에 "cont-save-confirm-modal" 이 빠지면 창이 열려 있는 동안 PRINT 가 엉뚱한 셀에 꽂힌다',
  );
});

test("다중 계량: saveInner 은 window.confirm 대신 confirmSaveModal 로 확인한다", () => {
  const body = bodyOf(cont, "saveInner");
  assert.ok(
    body.includes("confirmSaveModal("),
    "저장 직전 확인은 confirmSaveModal 이어야 한다 — 배합 화면과 같은 창으로 통일되어야 한다",
  );
  assert.ok(
    !body.includes("window.confirm(`작업자"),
    "예전 window.confirm 이 saveInner 에 남아 있으면 두 저장 경로가 갈라진다",
  );
});

test("다중 계량: confirmSaveModal — 키보드 클릭은 저장하지 않고, 닫는 길은 '다시 보기'다", () => {
  const body = bodyOf(cont, "confirmSaveModal");
  assert.match(
    body, /detail\s*===\s*0/,
    "event.detail === 0(Enter/Space 로 만든 클릭)을 저장으로 받으면 포커스 이동만으로 저장이 확정된다",
  );
  assert.ok(
    body.includes("cont-save-confirm-review"),
    "'다시 보기'(#cont-save-confirm-review) 버튼이 취소 경로여야 한다",
  );
});

test("다중 계량: saveOfferReady — 자동 제안이 떠도 되는 조건을 모두 검사한다", () => {
  const body = bodyOf(cont, "saveOfferReady");
  [
    "printBlockingModalVisible()",
    "saveOfferDismissed",
    "_addWeighCell",
    "blendWindowBlocked",
    "use_reactor",
    "anchorBlocked",
  ].forEach((needle) => {
    assert.ok(
      body.includes(needle),
      `saveOfferReady 에 ${needle} 검사가 없다 — 조건 하나를 빼면 안 떠도 될 때 뜨거나, 떠야 할 때 안 뜬다`,
    );
  });
});

test("다중 계량: updateProgress 는 계량 완료 전환 순간 제안을 예약한다", () => {
  assert.ok(
    bodyOf(cont, "updateProgress").includes("scheduleSaveOffer()"),
    "완료 전환 시 scheduleSaveOffer() 를 불러야 자동 제안이 뜬다",
  );
});

// ── 제품 LOT(예정) 표시 — 개수("로트 2개")만으로는 무엇이 기록될지 알 수 없다(2026-09-15) ──

test("저장 창은 창을 여는 시점에 제품 LOT(예정)을 새로 받아 보여 준다", () => {
  assert.ok(bodyOf(blend, "saveBlendInner").includes("fetchNextLot()"),
    "배합 저장 창 직전에 fetchNextLot() 으로 번호를 새로 받아야 한다 — 상단 미리보기는 낡을 수 있다");
  assert.ok(bodyOf(blend, "fetchNextLot").includes("/blend/next-lot"),
    "fetchNextLot 은 서버의 /blend/next-lot 을 물어야 한다");
  assert.ok(bodyOf(blend, "confirmSaveModal").includes("save-confirm-lot"),
    "배합 저장 창에 제품 LOT 칸(#save-confirm-lot)을 채워야 한다");

  const contInner = bodyOf(cont, "saveInner");
  assert.ok(contInner.includes("fetchLotPreviews()"),
    "다중 계량 저장 창 직전에 fetchLotPreviews() 로 로트별 번호를 새로 받아야 한다");
  assert.ok(bodyOf(cont, "fetchLotPreviews").includes("/blend/next-lot"),
    "fetchLotPreviews 는 서버의 /blend/next-lot 을 물어야 한다");
  assert.ok(contInner.includes("lotTotal(j)"),
    "로트 줄의 총량은 lotTotal(j) 여야 한다 — 증량된 로트가 기준 총량으로 잘못 보이지 않게");
  const contModal = bodyOf(cont, "confirmSaveModal");
  assert.ok(contModal.includes("cont-save-confirm-lots"),
    "다중 계량 저장 창에 로트 목록(#cont-save-confirm-lots)을 채워야 한다");
  assert.ok(!/innerHTML/.test(contModal),
    "로트 목록은 textContent 로만 채운다 — 제품명·LOT 에 태그 문자가 섞여도 안전해야 한다");
});

test("다중 계량: saveOfferDismissed 는 두 곳에서 초기화된다(레시피 변경·초안 복구)", () => {
  const count = (cont.match(/saveOfferDismissed = false/g) || []).length;
  assert.ok(
    count >= 2,
    `saveOfferDismissed = false 가 ${count}곳뿐이다 — 레시피 변경·초안 복구 두 곳이어야 한다`,
  );
});

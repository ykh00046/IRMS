/**
 * 저장 중복 가드(_saving) 회귀 테스트 — 두 화면 공통.
 *
 * 결함: 저장 함수가 `if (_saving) return;` 로 검사만 앞에 두고 `_saving = true` 는
 *   여러 await(작업자 교대·미등록 LOT 조회) **뒤에** 세웠다. 그 사이에 저장 버튼을
 *   다시 누르면 두 번째 호출도 가드를 통과해 같은 계량값이 두 LOT(다중 계량은 N로트가
 *   두 벌)으로 저장됐다.
 *
 * 여기서는 두 저장 함수의 소스를 파싱해 "가드 설정(_saving = true)과 버튼 비활성화가
 * 첫 await 보다 앞에 있는가"를 잠근다. 두 함수는 DOM/네트워크에 강하게 묶여 있어
 * 순수 함수로 임포트할 수 없으므로(기존 blend_scale_leaks/blend_rescale_drivers 테스트가
 * 같은 방식으로 계약을 잠근 선례를 따른다) 소스 계약으로 검증한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

function readJs(name) {
  return fs.readFileSync(
    path.join(__dirname, "..", "..", "static", "js", name),
    "utf8",
  );
}

/** 이름으로 async 함수 본문을 잘라낸다(중괄호 균형). */
function functionBody(src, header) {
  const start = src.indexOf(header);
  assert.ok(start >= 0, `${header} 를 찾지 못했다`);
  const open = src.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  throw new Error(`${header} 본문의 끝을 찾지 못했다`);
}

function assertGuardBeforeFirstAwait(body, label) {
  const guard = body.indexOf("_saving = true");
  const firstAwait = body.indexOf("await ");
  assert.ok(guard >= 0, `${label}: _saving = true 가 없다`);
  assert.ok(firstAwait >= 0, `${label}: await 가 없다(테스트 전제 확인 필요)`);
  assert.ok(
    guard < firstAwait,
    `${label}: _saving = true 가 첫 await 뒤에 있다 — 그 사이 두 번째 저장이 통과한다`,
  );
}

function assertButtonDisabledBeforeFirstAwait(body, label) {
  const disabled = body.indexOf("disabled = true");
  const firstAwait = body.indexOf("await ");
  assert.ok(disabled >= 0, `${label}: 저장 버튼 비활성화 코드가 없다`);
  assert.ok(
    disabled < firstAwait,
    `${label}: 버튼 비활성화가 첫 await 뒤에 있다 — 그 사이 재클릭이 가능하다`,
  );
}

test("blend.js saveBlend — 가드와 버튼 비활성화가 첫 await 보다 앞", () => {
  const body = functionBody(readJs("blend.js"), "async function saveBlend(");
  assertGuardBeforeFirstAwait(body, "blend.js saveBlend");
  assertButtonDisabledBeforeFirstAwait(body, "blend.js saveBlend");
});

test("blend_continuous.js save — 가드와 버튼 비활성화가 첫 await 보다 앞", () => {
  const body = functionBody(readJs("blend_continuous.js"), "async function save(");
  assertGuardBeforeFirstAwait(body, "blend_continuous.js save");
  assertButtonDisabledBeforeFirstAwait(body, "blend_continuous.js save");
});

test("두 화면 모두 저장 요청에 request_id 를 실어 보낸다(서버 멱등 키)", () => {
  for (const name of ["blend.js", "blend_continuous.js"]) {
    const src = readJs(name);
    assert.ok(
      /request_id\s*:/.test(src),
      `${name}: 저장 본문에 request_id 가 없다 — 재시도가 중복 기록을 만든다`,
    );
  }
});

test("blend.js 단건 저장도 전 자재 계량 완료를 확인한다(다중 계량과 대칭)", () => {
  // 실제 저장 본문(가드 래퍼가 감싸는 안쪽)에 미계량 차단이 있어야 한다.
  const body = functionBody(readJs("blend.js"), "async function saveBlendInner(");
  assert.ok(
    /실제량 미입력/.test(body),
    "blend.js 저장 본문: 미계량 자재 차단 메시지가 없다",
  );
});

/**
 * 배합·다중 계량 화면 — 작업자 파트로 분류 초기값을 넣는 규칙(2026-09-15 현장 요청).
 *
 * 현장은 90% 이상 자기 파트 배합을 한다. 화면을 열면 로그인한 작업자의 파트(workers.category,
 * 레시피 분류와 같은 값)로 분류를 미리 고르되, 다음 경우에는 건드리지 않는다.
 *   - 레시피를 이미 골랐다(계량 중에 목록이 바뀌면 혼란, 초안 복구·저장 중 교대 포함)
 *   - 작업자가 분류를 직접 바꿨다(그 화면에 있는 동안 유지)
 *   - 이어서 하기로 들어왔다(복구가 분류를 '전체'로 풀어 레시피가 반드시 보이게 한다)
 * 두 화면의 DOM 초기화에 묶인 로직이라 blend_save_confirm.test.js 처럼 소스 계약으로 잠근다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "..", "static", "js");
const screens = [
  ["blend.js", fs.readFileSync(path.join(SRC, "blend.js"), "utf8"), "blend-recipe-cat"],
  ["blend_continuous.js", fs.readFileSync(path.join(SRC, "blend_continuous.js"), "utf8"), "cont-recipe-cat"],
];

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

for (const [name, src, catId] of screens) {
  test(`${name}: 작업자 목록을 읽을 때 파트도 보관한다`, () => {
    assert.ok(bodyOf(src, "loadWorkerNames").includes("workerParts"),
      "loadWorkerNames 가 파트(category)를 보관하지 않으면 분류 초기값을 정할 근거가 없다");
  });

  test(`${name}: applyWorkerPart 는 레시피 선택·직접 변경 중에는 분류를 건드리지 않는다`, () => {
    const body = bodyOf(src, "applyWorkerPart");
    assert.ok(body.includes(`"${catId}"`), `분류 선택(#${catId})을 대상으로 해야 한다`);
    assert.match(body, /state\.current/,
      "레시피를 이미 골랐으면 두어야 한다 — 계량 중에 레시피 목록이 바뀌면 선택이 풀린다");
    assert.match(body, /state\.catTouched/,
      "작업자가 분류를 직접 바꿨으면 두어야 한다");
    assert.match(body, /options/,
      "분류 선택지에 없는 파트 값이면 적용하지 않아야 한다(빈 목록 방지)");
    assert.match(body, /populateRecipeSelect\(\)/,
      "분류를 바꾼 뒤 레시피 목록을 다시 채워야 한다");
  });

  test(`${name}: 교대하면 새 작업자 파트를 다시 적용할 수 있다`, () => {
    const body = bodyOf(src, "switchWorker");
    assert.match(body, /state\.catTouched\s*=\s*false/,
      "사람이 바뀌면 앞사람이 바꾼 분류 표시를 풀어야 한다");
    assert.match(body, /applyWorkerPart\(clean\)/,
      "교대 성공 후 새 작업자 파트를 적용해야 한다(레시피를 골랐으면 applyWorkerPart 가 알아서 둔다)");
  });

  test(`${name}: 분류를 직접 바꾸면 그 화면에서는 유지한다`, () => {
    assert.match(src, /addEventListener\("change", \(\) => \{ state\.catTouched = true; populateRecipeSelect\(\); \}\)/,
      "분류 change 리스너가 catTouched 를 세워야 한다 — 안 그러면 교대·재로드 경로가 선택을 덮는다");
  });

  test(`${name}: 이어서 하기로 들어온 경우는 분류 초기값을 넣지 않는다`, () => {
    assert.match(src, /loadWorkerNames\(\)\.then\(\(\) => \{ if \(!resumeId\) applyWorkerPart\(state\.sessionWorker\); \}\)/,
      "복구가 분류를 '전체'로 푼 뒤 파트를 덮으면 복구한 레시피가 목록에서 빠질 수 있다");
  });
}

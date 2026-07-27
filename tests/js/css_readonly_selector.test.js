const assert = require("node:assert/strict");
const fs = require("node:fs");

// `:read-only` 는 "readonly 가 걸린 칸"이 아니라 "사용자가 편집할 수 없는 모든 요소"를
// 뜻한다. 그래서 타이핑 대상이 아닌 <select> 가 전부 걸려버린다 — 실제로 배합·기록·
// 점도·분석 등 8개 화면의 드롭다운이 멀쩡히 동작하면서도 회색 + 금지 커서로 보였다
// (2026-07-27 현장 지적). 잠금 표시는 실제 readonly 속성으로만 판단해야 한다.
const css = fs.readFileSync("static/css/common.css", "utf8");

// 주석은 걷어내고 실제 선택자만 본다.
const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");

assert.ok(
  !withoutComments.includes(":read-only"),
  "common.css 에 :read-only 가 있습니다 — <select> 까지 잠긴 것처럼 보입니다. " +
    "[readonly] 속성 선택자를 쓰세요.",
);

// 잠금 표시 자체는 남아 있어야 한다(그게 이 규칙의 목적).
assert.ok(
  /\.input\[readonly\]/.test(withoutComments),
  "잠긴 입력칸 표시 규칙(.input[readonly])이 사라졌습니다",
);

// 다른 CSS 파일에도 같은 함정이 없는지.
for (const file of fs.readdirSync("static/css")) {
  if (!file.endsWith(".css")) continue;
  const body = fs.readFileSync(`static/css/${file}`, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(!body.includes(":read-only"), `${file} 에 :read-only 가 있습니다`);
}

console.log("css_readonly_selector.test.js OK");

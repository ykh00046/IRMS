/**
 * 날짜 묶음 라벨(dayGroupLabel) 계약 — /status 목록의 날짜 묶음 행이 쓰는 순수 함수.
 *
 * status.js 는 DOM 리스너 등록으로만 움직이는 화면 스크립트라 통째로 실행할 수 없다.
 * blend_save_confirm.test.js 와 같은 방식(bodyOf)으로 소스에서 함수만 잘라내 평가한다.
 * 핵심은 "YYYY-MM-DD" 를 현지 시각으로 해석하는 것(UTC 아님) — 잘못 해석하면 시간대에
 * 따라 요일이 하루 밀려 묶음 라벨이 거짓말을 한다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "static", "js", "status.js"),
  "utf8",
);

/** 이름으로 함수 선언 전체(시그니처+본문)를 잘라낸다. */
function fnOf(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} 함수를 찾지 못했다`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`${name} 함수의 끝을 찾지 못했다`);
}

const dayGroupLabel = new Function(`return (${fnOf(src, "dayGroupLabel")})`)();

test("dayGroupLabel — MM-DD 요일(현지 시각 해석)", () => {
  assert.strictEqual(dayGroupLabel("2026-09-15"), "09-15 화요일");
  assert.strictEqual(dayGroupLabel("2026-09-14"), "09-14 월요일");
  assert.strictEqual(dayGroupLabel("2026-09-13"), "09-13 일요일");
});

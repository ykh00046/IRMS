/**
 * 운영 대시보드 '지금 조치' 재배치 계약(2026-09-15 시안 흡수).
 *
 * 네 항목이 값이 0이어도 같은 크기로 나란히 있어 볼 것이 드러나지 않았다. 조치가 필요한
 * 항목(needs-action)만 왼쪽 큰 카드 칸으로 올리고, 정상 항목은 오른쪽 '이상 없음' 목록에
 * 한 줄로 모은다. 판정 자체(markAct)는 그대로 두고 요소만 옮기므로, 여기서는 옮기는 규칙과
 * 템플릿 훅(id·data-order·is-info·is-error)이 서로 맞는지 소스 계약으로 잠근다.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
const js = fs.readFileSync(path.join(ROOT, "static", "js", "dashboard.js"), "utf8");
const html = fs.readFileSync(path.join(ROOT, "templates", "dashboard.html"), "utf8");

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

test("arrangeAttention 은 needs-action 항목만 왼쪽으로, 나머지는 '이상 없음' 목록으로 옮긴다", () => {
  const body = bodyOf(js, "arrangeAttention");
  for (const needle of ["dash-act-list", "dash-ok-list", "needs-action", "data-order", "dash-act-empty"]) {
    assert.ok(body.includes(needle), `arrangeAttention 에 ${needle} 이 없다`);
  }
  assert.match(body, /is-info/,
    "정보 줄(오늘 배합)은 판정과 무관하게 늘 '이상 없음' 목록에 있어야 한다");
  assert.match(body, /empty\.hidden\s*=\s*actionCount\s*>\s*0/,
    "조치 항목이 하나도 없을 때만 빈 안내를 보여야 한다");
});

test("renderAttention 은 판정을 마친 뒤 arrangeAttention 을 부른다", () => {
  const body = bodyOf(js, "renderAttention");
  const lastMark = body.lastIndexOf("markAct(");
  const arrange = body.lastIndexOf("arrangeAttention()");
  assert.ok(arrange !== -1, "renderAttention 이 arrangeAttention() 을 불러야 한다");
  assert.ok(arrange > lastMark, "모든 markAct 판정 뒤에 옮겨야 한다 — 먼저 옮기면 한 박자 늦은 배치가 된다");
});

test("템플릿: 두 칸과 각 항목의 순서·종류 훅이 있다", () => {
  for (const id of ['id="dash-act-list"', 'id="dash-ok-list"', 'id="dash-act-empty"']) {
    assert.ok(html.includes(id), `dashboard.html 에 ${id} 가 없다`);
  }
  for (const id of ["act-visc-due", "act-visc-anomaly", "act-lot-file", "act-today"]) {
    const re = new RegExp(`<a class="[^"]*dash-act[^"]*" id="${id}"[^>]*data-order="\\d+"`);
    assert.match(html, re, `${id} 에 data-order 가 없다 — 칸을 오가며 순서가 뒤섞인다`);
  }
  assert.match(html, /class="panel dash-act is-info" id="act-today"/,
    "오늘 배합은 정보 줄(is-info)이어야 한다");
  assert.match(html, /class="panel dash-act is-error" id="act-visc-anomaly"/,
    "점도 이상은 이상 색(is-error)이어야 한다");
});

// 2026-10-05 · '오늘 점도 미입력' 카드의 반제품 이름을 누르면 그 반제품이 골라진 점도
// 화면(/viscosity?product=<id>)으로 간다. 카드가 <a> 라 중첩 링크 대신 role=link 칩을 쓰고,
// 칩 클릭은 카드의 기본 이동을 막아야 한다.
test("오늘 점도 미입력 카드: 반제품 칩은 그 반제품 딥링크로 가고 카드 이동을 막는다", () => {
  const href = bodyOf(js, "viscosityProductHref");
  assert.match(href, /\/viscosity\?product=\$\{id\}/, "반제품 id 로 딥링크를 만들어야 한다");
  assert.match(href, /return .*"\/viscosity"/, "id 가 없으면 점도 화면 기본 주소로 간다");
  const chip = bodyOf(js, "dueChipHtml");
  assert.match(chip, /role="link"/, "칩은 role=link 여야 한다(중첩 <a> 금지)");
  assert.match(chip, /tabindex="0"/, "칩은 키보드로도 닿아야 한다");
  assert.match(chip, /textContent\s*=/, "코드는 서버 값이라 글자로 넣어야 한다");
  const bind = bodyOf(js, "bindDueChips");
  assert.match(bind, /preventDefault\(\)/, "카드(<a>)의 기본 이동을 막아야 한다");
  assert.match(bind, /stopPropagation\(\)/, "카드 클릭으로 번지지 않아야 한다");
  assert.match(bind, /location\.assign\(/, "칩의 data-href 로 이동해야 한다");
  const render = bodyOf(js, "renderAttention");
  assert.match(render, /viscosity_due_items/, "id 가 실린 목록(viscosity_due_items)을 읽어야 한다");
  assert.match(render, /act-visc-due.*setAttribute\("href"/s, "카드 자체도 첫 반제품으로 가야 한다");
  const main = js.indexOf("DOMContentLoaded");
  assert.ok(js.indexOf("bindDueChips();", main) > main, "초기화 때 칩 클릭을 한 번 걸어야 한다");
});

test("점도 화면: ?product=<id> 딥링크를 첫 로드에서 한 번만 읽어 그 반제품을 고른다", () => {
  const visc = fs.readFileSync(path.join(ROOT, "static", "js", "viscosity.js"), "utf8");
  const consume = bodyOf(visc, "consumeDeepLinkProduct");
  assert.match(consume, /get\("product"\)/, "product 파라미터를 읽어야 한다");
  assert.match(consume, /deepLinkProductRead/, "한 번만 읽어야 한다(새로고침 때 선택을 덮지 않게)");
  const overview = bodyOf(visc, "loadOverview");
  assert.match(overview, /consumeDeepLinkProduct\(\)/, "loadOverview 가 목록을 그린 뒤 딥링크를 적용해야 한다");
  assert.match(overview, /visc-cat-select/, "분류 필터에 가려 있으면 분류를 전체로 되돌려야 한다");
});

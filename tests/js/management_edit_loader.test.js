const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function loadRecipeEditLoader() {
  const events = [];
  const context = {
    console,
    setTimeout(fn) {
      fn();
    },
    // 수정 등록 프리필(기준 배합량·기준 자재·허용 편차)이 DOM 을 조회하므로 최소 스텁 제공.
    // 실제 값 검증은 브라우저 스모크가 담당하고, 여기선 모듈이 죽지 않는 것만 본다.
    document: {
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement() { return { value: "", textContent: "", appendChild() {} }; },
    },
    window: {
      IRMS: {
        escapeHtml(value) {
          return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
        },
        notify(message, type) {
          events.push({ message, type });
        },
        async getRecipeDetail(recipeId) {
          return {
            id: recipeId,
            product_name: "반제품<수정>",
            tsv: "반제품명\t원료A\n반제품<수정>\t12",
          };
        },
      },
    },
  };
  context.window.window = context.window;
  context.IRMS = context.window.IRMS;

  const code = fs.readFileSync("static/js/management/recipe-edit-loader.js", "utf8");
  vm.runInNewContext(code, context, { filename: "recipe-edit-loader.js" });

  const state = {
    currentPreview: { rows: [{ productName: "old" }], errors: [] },
    confirmedRawText: "old",
    pendingRevisionOf: null,
    previewIsStale: true,
    suppressDirtyTracking: false,
  };
  const dom = {
    revisionBanner: { hidden: true, innerHTML: "" },
    spreadsheetContainer: {},
    rawInput: { value: "" },
    errorList: {},
    warningList: {},
  };
  const calls = [];
  let loadedRows = null;
  const ctx = {
    dom,
    state,
    switchToImportTab() {
      calls.push("switch");
    },
    onDirty() {
      calls.push("dirty");
    },
    // 화면에 실제로 로드되는 편집기는 bom-editor.js 이고 loadFromTsvRows 를 가진다.
    // 예전 스텁은 이 함수를 빼놓아서, 은퇴한 jspreadsheet 시절의 raw 폴백(안전망)
    // 분기만 검사하고 운영이 타는 경로는 한 번도 거치지 않았다.
    spreadsheet: {
      destroySpreadsheet() {
        calls.push("destroy");
      },
      getSpreadsheetFactory() {
        return null;
      },
      setRawInputMode(enabled) {
        calls.push(`raw:${enabled}`);
      },
      loadFromTsvRows(tsvRows) {
        calls.push(`load:${tsvRows.length}`);
        loadedRows = tsvRows;
        return true;          // 편집기가 담아냈다 → raw 폴백으로 가지 않는다
      },
    },
    importValidate: {
      renderValidationMeta(value) {
        calls.push(`meta:${value.rows.length}`);
      },
      renderIssues() {
        calls.push("issues");
      },
      syncRegisterState() {
        calls.push("sync");
      },
    },
  };
  const loader = context.window.IRMS.management.createRecipeEditLoader(ctx);
  return { loader, state, dom, calls, events, getLoadedRows: () => loadedRows };
}

async function testLoadRecipeForEditMarksRevision() {
  const { loader, state, dom, calls, events, getLoadedRows } = loadRecipeEditLoader();

  await loader.loadRecipeForEdit(42, "레시피 현황");

  assert.equal(state.pendingRevisionOf, 42);
  assert.equal(state.previewIsStale, false);
  assert.equal(state.confirmedRawText, "");
  assert.equal(dom.revisionBanner.hidden, false);
  assert.match(dom.revisionBanner.innerHTML, /수정 등록 중/);
  assert.match(dom.revisionBanner.innerHTML, /반제품&lt;수정&gt;/);
  // 편집기가 TSV 를 받아냈으므로 raw 텍스트 폴백으로 새지 않아야 한다.
  assert.deepEqual(calls, ["switch", "load:2", "meta:0", "issues", "issues", "sync"]);
  // vm 컨텍스트에서 만들어진 배열이라 프로토타입이 달라 deepEqual 이 안 된다 → JSON 비교.
  assert.equal(
    JSON.stringify(getLoadedRows()),
    JSON.stringify([["반제품명", "원료A"], ["반제품<수정>", "12"]]),
  );
  assert.equal(events[0].type, "info");
}

function testClearRevisionBanner() {
  const { loader, dom } = loadRecipeEditLoader();
  dom.revisionBanner.hidden = false;
  dom.revisionBanner.innerHTML = "dirty";

  loader.clearRevisionBanner();

  assert.equal(dom.revisionBanner.hidden, true);
  assert.equal(dom.revisionBanner.innerHTML, "");
}

(async () => {
  await testLoadRecipeForEditMarksRevision();
  testClearRevisionBanner();
  console.log("management_edit_loader.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

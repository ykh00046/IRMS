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
  const ctx = {
    dom,
    state,
    switchToImportTab() {
      calls.push("switch");
    },
    onDirty() {
      calls.push("dirty");
    },
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
  return { loader, state, dom, calls, events };
}

async function testLoadRecipeForEditMarksRevision() {
  const { loader, state, dom, calls, events } = loadRecipeEditLoader();

  await loader.loadRecipeForEdit(42, "레시피 현황");

  assert.equal(state.pendingRevisionOf, 42);
  assert.equal(state.previewIsStale, false);
  assert.equal(state.confirmedRawText, "");
  assert.equal(dom.rawInput.value, "반제품명\t원료A\n반제품<수정>\t12");
  assert.equal(dom.revisionBanner.hidden, false);
  assert.match(dom.revisionBanner.innerHTML, /수정 등록 중/);
  assert.match(dom.revisionBanner.innerHTML, /반제품&lt;수정&gt;/);
  assert.deepEqual(calls, ["switch", "destroy", "raw:true", "meta:0", "issues", "issues", "sync"]);
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

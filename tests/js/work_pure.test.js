const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Loads a static/js/work/*.js factory module into an isolated context.
function loadWorkModule(filename, extraGlobals = {}) {
  const context = {
    console,
    window: { IRMS: { speakText() {}, notify() {}, logout() {} } },
    document: {
      createElement() { return { style: {}, value: "", select() {} }; },
      addEventListener() {},
      removeEventListener() {},
      body: { appendChild() {}, removeChild() {} },
    },
    setTimeout, clearTimeout, setInterval, clearInterval,
    ...extraGlobals,
  };
  context.window.window = context.window;
  context.IRMS = context.window.IRMS;

  const code = fs.readFileSync(`static/js/work/${filename}`, "utf8");
  vm.runInNewContext(code, context, { filename });
  return context.window.IRMS;
}

function testRecipeTableFactory() {
  const IRMS = loadWorkModule("recipe-table.js");
  const table = IRMS.work.createRecipeTable({
    dom: { tableHead: {}, tableBody: {}, statsCount: {}, statsStatus: {} },
    state: { loadingToken: 0 },
  });
  for (const name of ["render", "bindRowActions", "countRecipeMaterials"]) {
    assert.equal(typeof table[name], "function", `missing handle: ${name}`);
  }
}

function testCountRecipeMaterials() {
  const IRMS = loadWorkModule("recipe-table.js");
  const table = IRMS.work.createRecipeTable({
    dom: { tableHead: {}, tableBody: {}, statsCount: {}, statsStatus: {} },
    state: { loadingToken: 0 },
  });
  assert.equal(table.countRecipeMaterials({ items: [] }), 0, "empty items → 0");
  assert.equal(table.countRecipeMaterials({}), 0, "missing items → 0");
  assert.equal(table.countRecipeMaterials({ items: [1, 2, 3] }), 3, "3 items → 3");
}

function testWeighingRenderFactory() {
  const IRMS = loadWorkModule("weighing-render.js");
  const render = IRMS.work.createWeighingRender({
    dom: {},
    state: { weighing: { queue: [] }, lowStockSet: new Set() },
    colorLabel: (g) => g,
  });
  for (const name of ["render", "syncControls", "resetProgress", "getQueueColorCounts"]) {
    assert.equal(typeof render[name], "function", `missing handle: ${name}`);
  }
}

function testGetQueueColorCounts() {
  const IRMS = loadWorkModule("weighing-render.js");
  const render = IRMS.work.createWeighingRender({
    dom: {},
    state: { weighing: { queue: [] }, lowStockSet: new Set() },
    colorLabel: (g) => g,
  });
  const counts = render.getQueueColorCounts([
    { colorGroup: "black" },
    { colorGroup: "black" },
    { colorGroup: "red" },
    { colorGroup: "unknown_color" },
    {},
  ]);
  // Note: getQueueColorCounts uses hasOwnProperty check. "unknown_color" is not in acc → bucketed to "none".
  // {} → colorGroup is undefined → `undefined || "none"` → "none".
  // Result: black=2, red=1, none=2, blue=0, yellow=0
  assert.equal(counts.black, 2, "black count");
  assert.equal(counts.red, 1, "red count");
  assert.equal(counts.blue, 0, "blue count");
  assert.equal(counts.yellow, 0, "yellow count");
  assert.equal(counts.none, 2, "none count (unknown + empty)");
}

function testResetProgress() {
  const IRMS = loadWorkModule("weighing-render.js");
  const weighing = {
    queue: [], doneCount: 5, initialTotal: 10,
    pendingRecipeCompletion: { foo: 1 }, lastCompleted: { bar: 2 },
  };
  const render = IRMS.work.createWeighingRender({
    dom: {},
    state: { weighing, lowStockSet: new Set() },
    colorLabel: (g) => g,
  });
  render.resetProgress(7);
  assert.equal(weighing.doneCount, 0);
  assert.equal(weighing.initialTotal, 7);
  assert.equal(weighing.pendingRecipeCompletion, null);
  assert.equal(weighing.lastCompleted, null);
}

function testWeighingActionsFactory() {
  const IRMS = loadWorkModule("weighing-actions.js");
  const actions = IRMS.work.createWeighingActions({
    dom: {},
    state: { weighing: { open: false, queue: [] } },
    weighingRender: { render() {}, syncControls() {}, resetProgress() {} },
    onRefreshTable: () => Promise.resolve(),
  });
  for (const name of ["open", "close", "loadQueue", "advance", "undo", "isOpen"]) {
    assert.equal(typeof actions[name], "function", `missing handle: ${name}`);
  }
  assert.equal(actions.isOpen(), false, "isOpen reflects state.weighing.open");
}

function testIdleLogoutFactory() {
  const IRMS = loadWorkModule("idle-logout.js");
  const idle = IRMS.work.createIdleLogout({});
  for (const name of ["start", "stop"]) {
    assert.equal(typeof idle[name], "function", `missing handle: ${name}`);
  }
}

function testStockBannerFactory() {
  const IRMS = loadWorkModule("stock-banner.js", {
    fetch: () => Promise.resolve({ ok: false }),
  });
  const banner = IRMS.work.createStockBanner({
    dom: { workStockBanner: null },
    state: { lowStockSet: new Set() },
  });
  for (const name of ["refresh", "start"]) {
    assert.equal(typeof banner[name], "function", `missing handle: ${name}`);
  }
}

function testImportNotificationsFactory() {
  const IRMS = loadWorkModule("import-notifications.js", {
    window: { IRMS: { notify() {}, getRecipeImportNotifications: () => Promise.resolve({ items: [] }) }, localStorage: { setItem() {} } },
  });
  // Re-load with correct window stub
  const code = fs.readFileSync("static/js/work/import-notifications.js", "utf8");
  const context = {
    console,
    window: {
      IRMS: { notify() {}, getRecipeImportNotifications: () => Promise.resolve({ items: [] }) },
      localStorage: { setItem() {} },
      setInterval, clearInterval,
      clearInterval: () => {},
    },
    document: { visibilityState: "visible" },
    setInterval, clearInterval,
  };
  context.window.window = context.window;
  context.IRMS = context.window.IRMS;
  vm.runInNewContext(code, context, { filename: "import-notifications.js" });
  const notif = context.window.IRMS.work.createImportNotifications({
    state: { recipeImportNotice: { initialized: false, checking: false, lastSeenId: 0, timerId: null } },
    onRefreshTable: () => Promise.resolve(),
    onRefreshWeighingQueue: () => Promise.resolve(),
  });
  for (const name of ["check", "start"]) {
    assert.equal(typeof notif[name], "function", `missing handle: ${name}`);
  }
}

(async () => {
  testRecipeTableFactory();
  testCountRecipeMaterials();
  testWeighingRenderFactory();
  testGetQueueColorCounts();
  testResetProgress();
  testWeighingActionsFactory();
  testIdleLogoutFactory();
  testStockBannerFactory();
  testImportNotificationsFactory();
  console.log("work_pure.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

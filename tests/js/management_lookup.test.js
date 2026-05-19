const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Loads recipe-lookup.js (a split-management-js factory module) into an
// isolated context and returns its factory output plus a spy on execCommand.
function loadRecipeLookup(navigatorStub) {
  const execCommands = [];
  const context = {
    console,
    navigator: navigatorStub,
    window: { IRMS: {} },
    document: {
      createElement() {
        return { style: {}, value: "", select() {} };
      },
      body: { appendChild() {}, removeChild() {} },
      execCommand(cmd) {
        execCommands.push(cmd);
        return true;
      },
    },
  };
  context.window.window = context.window;
  context.IRMS = context.window.IRMS;

  const code = fs.readFileSync("static/js/management/recipe-lookup.js", "utf8");
  vm.runInNewContext(code, context, { filename: "recipe-lookup.js" });

  const lookup = context.window.IRMS.management.createRecipeLookup({ dom: {}, state: {} });
  return { lookup, execCommands };
}

function testFactoryReturnsAllHandles() {
  const { lookup } = loadRecipeLookup({});
  for (const name of [
    "loadProducts",
    "setLookupSelection",
    "handleLookup",
    "copyToClipboard",
    "handleLookupCopy",
    "handleLookupClone",
  ]) {
    assert.equal(typeof lookup[name], "function", `missing factory handle: ${name}`);
  }
}

async function testCopyToClipboardUsesClipboardApiWhenAvailable() {
  const writes = [];
  const { lookup, execCommands } = loadRecipeLookup({
    clipboard: {
      writeText(text) {
        writes.push(text);
        return Promise.resolve();
      },
    },
  });

  await lookup.copyToClipboard("hello");

  assert.deepEqual(writes, ["hello"]);
  assert.equal(execCommands.length, 0, "should not use execCommand fallback");
}

async function testCopyToClipboardFallsBackToExecCommand() {
  // navigator without clipboard → legacy textarea + execCommand path
  const { lookup, execCommands } = loadRecipeLookup({});

  await lookup.copyToClipboard("fallback text");

  assert.deepEqual(execCommands, ["copy"]);
}

(async () => {
  testFactoryReturnsAllHandles();
  await testCopyToClipboardUsesClipboardApiWhenAvailable();
  await testCopyToClipboardFallsBackToExecCommand();
  console.log("management_lookup.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

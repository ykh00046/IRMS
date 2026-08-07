const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Loads recipe-lookup.js (클립보드 복사 헬퍼, 3단계 정리 후 잔여 모듈) into an
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

function testFactoryReturnsCopyHandle() {
  const { lookup } = loadRecipeLookup({});
  assert.equal(typeof lookup.copyToClipboard, "function", "missing copyToClipboard handle");
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
  testFactoryReturnsCopyHandle();
  await testCopyToClipboardUsesClipboardApiWhenAvailable();
  await testCopyToClipboardFallsBackToExecCommand();
  console.log("management_lookup.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

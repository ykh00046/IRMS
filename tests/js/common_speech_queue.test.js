const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const COMMON_MODULES = [
  "static/js/common/core.js",
  "static/js/common/mappers.js",
  "static/js/common/format.js",
  "static/js/common/api-users.js",
  "static/js/common/api-recipes.js",
  "static/js/common/api-materials.js",
  "static/js/common/ui.js",
  "static/js/common/audio.js",
  "static/js/common.js",
];

function loadCommonJs(overrides = {}) {
  const context = {
    console,
    clearInterval() {},
    decodeURIComponent,
    encodeURIComponent,
    fetch: overrides.fetch || fetch,
    setInterval() {},
    URL,
    window: {
      IRMS: {},
      location: { origin: "http://localhost" },
      localStorage: { getItem() { return null; }, setItem() {} },
      speechSynthesis: {
        cancelCount: 0,
        utterances: [],
        cancel() { this.cancelCount += 1; },
        speak(utterance) { this.utterances.push(utterance); },
      },
    },
    document: {
      cookie: "",
      addEventListener() {},
      removeEventListener() {},
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    SpeechSynthesisUtterance: class {
      constructor(text) {
        this.text = text;
      }
    },
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.IRMS = context.window.IRMS;
  for (const modulePath of COMMON_MODULES) {
    const code = fs.readFileSync(modulePath, "utf8");
    vm.runInNewContext(code, context, { filename: modulePath });
  }
  return context;
}

function testSpeakTextQueuesMessagesWithoutCancellingCurrentUtterance() {
  const context = loadCommonJs();
  const speech = context.window.speechSynthesis;

  context.window.IRMS.speakText("first");
  context.window.IRMS.speakText("second");

  assert.equal(speech.cancelCount, 0);
  assert.equal(speech.utterances.length, 1);
  assert.equal(speech.utterances[0].text, "first");

  speech.utterances[0].onend();

  assert.equal(speech.utterances.length, 2);
  assert.equal(speech.utterances[1].text, "second");
}

async function testRequestTurnsValidationArrayIntoReadableError() {
  const context = loadCommonJs({
    fetch: async () => ({
      ok: false,
      status: 422,
      headers: { get: () => "application/json" },
      json: async () => ({
        detail: [
          {
            loc: ["body", "message_text"],
            msg: "notice messages must be 300 characters or fewer",
            type: "value_error",
          },
        ],
      }),
    }),
  });

  await assert.rejects(
    () =>
      context.window.IRMS._core.request("/_validation_error_probe", {
        method: "POST",
        body: { message_text: "x".repeat(301) },
      }),
    /notice messages must be 300 characters or fewer/,
  );
}

(async () => {
  testSpeakTextQueuesMessagesWithoutCancellingCurrentUtterance();
  await testRequestTurnsValidationArrayIntoReadableError();
  console.log("common_speech_queue.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

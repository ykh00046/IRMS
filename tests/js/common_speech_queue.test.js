const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function loadCommonJs(overrides = {}) {
  const code = fs.readFileSync("static/js/common.js", "utf8");
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
  vm.runInNewContext(code, context, { filename: "static/js/common.js" });
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
      context.window.IRMS.postChatMessage({
        roomKey: "notice",
        messageText: "x".repeat(301),
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

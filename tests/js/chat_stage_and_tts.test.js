const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function loadChatJs(overrides = {}) {
  const code = fs.readFileSync("static/js/chat.js", "utf8");
  const context = {
    console,
    HTMLElement: class {},
    window: {
      clearInterval() {},
      setInterval() { return 1; },
      localStorage: { getItem() { return null; }, setItem() {} },
      IRMS: {
        btnLoading() {},
        escapeHtml(value) { return String(value ?? ""); },
        formatDateTime(value) { return String(value ?? ""); },
        formatValue(value) { return String(value ?? ""); },
        getChatMessages: async () => ({ items: [], latestId: 0 }),
        listChatRooms: async () => ({ items: [] }),
        notify() {},
        playChatSound() {},
        postChatMessage: async () => ({}),
        speakText() {},
        ...overrides.IRMS,
      },
    },
    document: { visibilityState: "visible" },
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.IRMS = context.window.IRMS;
  vm.runInNewContext(code, context, { filename: "static/js/chat.js" });
  return context;
}

function createMessagesElement() {
  return {
    innerHTML: "",
    scrollHeight: 0,
    scrollTop: 0,
    insertAdjacentHTML(_position, markup) {
      this.innerHTML += markup;
    },
    querySelector() {
      return null;
    },
  };
}

async function testBindFormSendsTheSelectedWorkflowStage() {
  const posted = [];
  const context = loadChatJs({
    IRMS: {
      postChatMessage: async (payload) => {
        posted.push(payload);
        return {
          message: {
            id: 1,
            messageText: payload.messageText,
            stage: payload.stage,
            createdAt: "now",
            createdByUsername: "me",
          },
        };
      },
    },
  });
  const state = {
    currentUsername: "me",
    latestByRoom: {},
    rooms: [{ key: "mass_response", name: "Workflow", stageRequired: true }],
    selectedRoomKey: "mass_response",
  };
  const chat = context.window.IRMS.createChat({
    prefix: "chat",
    elements: { chatMessages: createMessagesElement() },
    state,
  });

  let submitHandler = null;
  const form = {
    addEventListener(_event, handler) {
      submitHandler = handler;
    },
  };
  const input = { value: "hello", focus() {} };
  const stage = { value: "in_progress" };

  chat.bindForm({ form, input, stage, send: {} });
  await submitHandler({ preventDefault() {} });

  assert.equal(posted[0].stage, "in_progress");
}

async function testBindFormRejectsOverlongNoticeMessageBeforePost() {
  const posted = [];
  const notifications = [];
  const context = loadChatJs({
    IRMS: {
      postChatMessage: async (payload) => {
        posted.push(payload);
        return {};
      },
      notify: (message, tone) => notifications.push({ message, tone }),
    },
  });
  const state = {
    currentUsername: "me",
    latestByRoom: {},
    rooms: [{ key: "notice", name: "Notice", stageRequired: false }],
    selectedRoomKey: "notice",
  };
  const chat = context.window.IRMS.createChat({
    prefix: "chat",
    elements: { chatMessages: createMessagesElement() },
    state,
  });

  let submitHandler = null;
  const form = {
    addEventListener(_event, handler) {
      submitHandler = handler;
    },
  };
  const input = { value: "x".repeat(301), focus() {} };

  chat.bindForm({ form, input, stage: {}, send: {} });
  await submitHandler({ preventDefault() {} });

  assert.equal(posted.length, 0);
  assert.equal(notifications.length, 1);
  assert.match(notifications[0].message, /300/);
  assert.equal(notifications[0].tone, "error");
}

async function testLoadMessagesSpeaksEveryIncomingMessageInOrder() {
  const spoken = [];
  const context = loadChatJs({
    IRMS: {
      getChatMessages: async () => ({
        items: [
          {
            id: 11,
            messageText: "first",
            createdAt: "now",
            createdByDisplayName: "Alice",
            createdByUsername: "alice",
          },
          {
            id: 12,
            messageText: "second",
            createdAt: "now",
            createdByDisplayName: "Bob",
            createdByUsername: "bob",
          },
        ],
        latestId: 12,
      }),
      speakText: (text) => spoken.push(text),
    },
  });
  const state = {
    currentUsername: "me",
    latestByRoom: { notice: 10 },
    rooms: [{ key: "notice", name: "Notice", stageRequired: false }],
    selectedRoomKey: "notice",
  };
  const chat = context.window.IRMS.createChat({
    prefix: "chat",
    elements: { chatMessages: createMessagesElement() },
    state,
  });

  await chat.loadMessages({ replace: false });

  assert.deepEqual(spoken, ["Alice: first", "Bob: second"]);
}

(async () => {
  await testBindFormSendsTheSelectedWorkflowStage();
  await testBindFormRejectsOverlongNoticeMessageBeforePost();
  await testLoadMessagesSpeaksEveryIncomingMessageInOrder();
  console.log("chat_stage_and_tts.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

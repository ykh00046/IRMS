/**
 * assistant.js — BRM 도우미(떠 있는 질문 창).
 *
 * 백엔드 계약(src/routers/assistant_routes.py):
 *   GET  /api/assistant/status  -> {enabled, provider, model}
 *   POST /api/assistant/stream  -> text/event-stream
 *        프레임: "event: <name>\n data: <json>\n\n", 주석줄 ": heartbeat\n\n"
 *        순서:   meta -> tool_call* -> token* -> done | error
 *   POST /api/assistant/reset   -> {session_id}
 *
 * 대화는 탭 단위로 sessionStorage 에 남긴다 — 메뉴를 옮겨도 이어지게 하려는 것.
 *   brm-assistant-session   : 서버 대화 세션 id
 *   brm-assistant-messages  : 말풍선 배열(JSON, 최근 40개·약 200KB 상한)
 *   brm-assistant-open      : 창이 열려 있었는지("1")
 *   brm-assistant-unread    : 닫힌 사이에 답변이 끝났는지("1")
 * 저장이 막힌 브라우저(사설 모드)에서도 동작하도록 읽기·쓰기는 모두 try/catch 로 감싼다.
 * 전역 노출은 window.IRMS.assistant = {open, close} 하나뿐.
 */
(function () {
  "use strict";

  const IRMS = (window.IRMS = window.IRMS || {});

  const SESSION_KEY = "brm-assistant-session";
  const MESSAGES_KEY = "brm-assistant-messages";
  const OPEN_KEY = "brm-assistant-open";
  const UNREAD_KEY = "brm-assistant-unread";
  const MAX_MESSAGES = 40;
  const MAX_BYTES = 200 * 1024;
  const SUGGESTIONS = [
    "오늘 배합 몇 건이야?",
    "PB 점도 최근 상태 알려줘",
    "이번 달 자재 사용량 알려줘",
    "기록이 저장 안 된 것 같아요",
  ];

  // 서버가 message 를 못 보냈을 때만 쓰는 대체 문구. 한 문장·40자 안(docs/ui-standard.md §6).
  const ERROR_TEXT = {
    DISABLED: "AI 도우미가 꺼져 있습니다.",
    RATE_LIMITED: "잠시 후 다시 물어보세요.",
    TIMEOUT: "응답이 늦어 중단했습니다.",
    LLM_ERROR: "답변을 만들지 못했습니다.",
    BAD_REQUEST: "질문을 확인하세요.",
  };
  const FALLBACK_ERROR = "답변을 받지 못했습니다.";
  const RATE_LIMIT_TEXT = "잠시 후 다시 물어보세요.";
  const DISABLED_NOTICE = "AI 도우미는 책임자가 시스템 설정에서 켭니다.";
  const STOPPED_TEXT = "중단됨";

  function escapeHtml(value) {
    if (typeof IRMS.escapeHtml === "function") return IRMS.escapeHtml(value);
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── 마크다운 경량 렌더러 ────────────────────────────────────────
  // 라이브러리 없이 굵게·인라인 코드·글머리·번호·표·같은 사이트 링크만 지원한다.
  // 항상 escape 를 먼저 하므로 모델이 뱉은 HTML 은 글자로만 보인다.

  // 같은 사이트 경로만 링크로 만든다. '/' 하나로 시작하고 글자·숫자·-._~/ 만 쓴다.
  // 그래서 '//other.com', 'javascript:', 따옴표·꺾쇠가 섞인 값은 통과하지 못한다
  // (escape 뒤라 따옴표는 &quot; 같은 꼴이 되는데, 이 목록에 &·; 가 없어 함께 걸린다).
  const PATH_CHARS = "[A-Za-z0-9\\-._~/]*";
  const SAFE_PATH_RE = new RegExp("^/" + PATH_CHARS + "$");
  const MD_LINK_RE = new RegExp("\\[([^\\]\\n]+)\\]\\((/" + PATH_CHARS + ")\\)", "g");
  const BARE_PATH_RE = new RegExp("\\((/" + PATH_CHARS + ")\\)", "g");

  function isSafePath(path) {
    return typeof path === "string"
      && path.length > 1
      && SAFE_PATH_RE.test(path)
      && path.indexOf("//") === -1;
  }

  function linkTag(path, label) {
    return `<a href="${path}">${label}</a>`;
  }

  function inlineMarkdown(text) {
    return text
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      // [글자](/경로) — 같은 창에서 이동한다.
      .replace(MD_LINK_RE, (whole, label, path) => (
        isSafePath(path) ? linkTag(path, label) : whole
      ))
      // 모델이 **메뉴**(/경로) 꼴로 적은 맨 경로도 링크로 바꾼다.
      .replace(BARE_PATH_RE, (whole, path) => (
        isSafePath(path) ? `(${linkTag(path, path)})` : whole
      ))
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  }

  function isTableRow(line) {
    return /^\s*\|/.test(line);
  }

  function splitTableRow(line) {
    let body = line.trim();
    if (body.startsWith("|")) body = body.slice(1);
    if (body.endsWith("|")) body = body.slice(0, -1);
    return body.split("|").map((cell) => cell.trim());
  }

  function isTableSeparator(line) {
    if (!isTableRow(line)) return false;
    const cells = splitTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell));
  }

  function renderMarkdown(raw) {
    const source = String(raw === undefined || raw === null ? "" : raw).replace(/\r\n/g, "\n");
    const lines = escapeHtml(source).split("\n");
    const out = [];
    let index = 0;

    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      // 표 — 머리글 줄 + 구분 줄이 짝을 이룰 때만 표로 본다.
      if (isTableRow(line) && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
        const header = splitTableRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length && isTableRow(lines[index]) && !isTableSeparator(lines[index])) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        const head = header.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("");
        const bodyRows = rows
          .map((cells) => `<tr>${cells.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`)
          .join("");
        out.push(
          `<div class="asst-table-wrap"><table><thead><tr>${head}</tr></thead>`
          + `<tbody>${bodyRows}</tbody></table></div>`,
        );
        continue;
      }

      // 글머리 목록
      if (/^\s*[-*]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
          items.push(`<li>${inlineMarkdown(lines[index].replace(/^\s*[-*]\s+/, ""))}</li>`);
          index += 1;
        }
        out.push(`<ul>${items.join("")}</ul>`);
        continue;
      }

      // 번호 목록
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
          items.push(`<li>${inlineMarkdown(lines[index].replace(/^\s*\d+\.\s+/, ""))}</li>`);
          index += 1;
        }
        out.push(`<ol>${items.join("")}</ol>`);
        continue;
      }

      // 문단 — 빈 줄이나 다른 블록이 나올 때까지 모으고 줄바꿈은 <br> 로 둔다.
      // 첫 줄은 무조건 삼킨다. 스트리밍 중에는 표 머리글이 구분 줄보다 먼저
      // 도착해 '표도 문단도 아닌 줄'이 생기는데, 그때 index 가 멈추면 무한 루프가 된다.
      const paragraph = [inlineMarkdown(line)];
      index += 1;
      while (index < lines.length) {
        const current = lines[index];
        if (!current.trim()) break;
        if (isTableRow(current) || /^\s*[-*]\s+/.test(current) || /^\s*\d+\.\s+/.test(current)) break;
        paragraph.push(inlineMarkdown(current));
        index += 1;
      }
      out.push(`<p>${paragraph.join("<br />")}</p>`);
    }

    return out.join("");
  }

  // ── SSE 파서 ────────────────────────────────────────────────────
  // 프레임 경계는 빈 줄(\n\n). 청크가 프레임 중간에서 끊기면 남은 조각을 버퍼에
  // 두었다가 다음 청크와 이어 붙인다. ':' 로 시작하는 주석줄(하트비트)은 data 가
  // 없으므로 parseFrame 이 조용히 버린다.
  function parseFrame(frame, handlers) {
    let eventName = "message";
    const dataLines = [];
    frame.split("\n").forEach((line) => {
      if (!line || line.startsWith(":")) return;
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    });
    if (!dataLines.length) return;
    let payload = null;
    try {
      payload = JSON.parse(dataLines.join("\n"));
    } catch (_error) {
      return;
    }
    if (typeof handlers[eventName] === "function") handlers[eventName](payload || {});
  }

  // ── 탭 저장소 ───────────────────────────────────────────────────
  // 사설 모드·용량 초과에서 예외를 던지므로 모든 접근을 감싼다.
  function readStore(key) {
    try {
      return window.sessionStorage.getItem(key) || "";
    } catch (_error) {
      return "";
    }
  }

  function writeStore(key, value) {
    try {
      if (value) window.sessionStorage.setItem(key, value);
      else window.sessionStorage.removeItem(key);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function byteLength(text) {
    try {
      return new TextEncoder().encode(text).length;
    } catch (_error) {
      return text.length * 3;
    }
  }

  // 저장된 값은 같은 탭이 쓴 것이지만, 모양이 깨진 값이 화면을 망가뜨리지 않게 걸러 낸다.
  function sanitizeRecord(raw) {
    if (!raw || typeof raw !== "object") return null;
    const role = raw.role === "user" || raw.role === "assistant" ? raw.role : "";
    if (!role) return null;

    const record = {
      role,
      text: typeof raw.text === "string" ? raw.text : "",
      ts: typeof raw.ts === "number" && isFinite(raw.ts) ? raw.ts : Date.now(),
    };

    if (Array.isArray(raw.tools)) {
      const tools = raw.tools
        .filter((tool) => tool && typeof tool === "object")
        .map((tool) => ({
          name: String(tool.name || ""),
          label: String(tool.label || tool.name || ""),
          status: String(tool.status || "done"),
        }));
      if (tools.length) record.tools = tools;
    }

    if (raw.meta && typeof raw.meta === "object") {
      const meta = {};
      if (raw.meta.provider) meta.provider = String(raw.meta.provider);
      if (raw.meta.model) meta.model = String(raw.meta.model);
      if (typeof raw.meta.duration_ms === "number") meta.duration_ms = raw.meta.duration_ms;
      if (raw.meta.stopped) meta.stopped = true;
      record.meta = meta;
    }

    if (Array.isArray(raw.suggestions)) {
      const items = raw.suggestions
        .filter((text) => typeof text === "string" && text.trim())
        .slice(0, 4);
      if (items.length) record.suggestions = items;
    }

    if (raw.error && typeof raw.error === "object") {
      record.error = {
        code: String(raw.error.code || ""),
        message: String(raw.error.message || ""),
      };
    }
    return record;
  }

  function loadMessages() {
    const raw = readStore(MESSAGES_KEY);
    if (!raw) return [];
    let parsed = null;
    try {
      parsed = JSON.parse(raw);
    } catch (_error) {
      return [];
    }
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(sanitizeRecord)
      .filter((record) => record !== null)
      .slice(-MAX_MESSAGES);
  }

  // 최근 MAX_MESSAGES 개까지, 그리고 약 200KB 안으로 앞에서부터 덜어 낸다.
  function saveMessages(list) {
    let items = Array.isArray(list) ? list.slice(-MAX_MESSAGES) : [];
    let json = "[]";
    for (;;) {
      try {
        json = JSON.stringify(items);
      } catch (_error) {
        items = [];
        json = "[]";
        break;
      }
      if (items.length <= 1 || byteLength(json) <= MAX_BYTES) break;
      items = items.slice(1);
    }
    writeStore(MESSAGES_KEY, items.length ? json : "");
    return items;
  }

  function clockLabel(ts) {
    const when = new Date(typeof ts === "number" && isFinite(ts) ? ts : Date.now());
    const hh = String(when.getHours()).padStart(2, "0");
    const mm = String(when.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  function init() {
    const launcher = document.getElementById("asst-launcher");
    const panel = document.getElementById("asst-panel");
    if (!launcher || !panel) return;

    const unreadDot = document.getElementById("asst-unread");
    const badge = document.getElementById("asst-badge");
    const resetBtn = document.getElementById("asst-reset");
    const closeBtn = document.getElementById("asst-close");
    const body = document.getElementById("asst-body");
    const emptyBox = document.getElementById("asst-empty");
    const suggestionBox = document.getElementById("asst-suggestions");
    const notice = document.getElementById("asst-notice");
    const thread = document.getElementById("asst-thread");
    const composer = document.getElementById("asst-composer");
    const input = document.getElementById("asst-input");
    const sendBtn = document.getElementById("asst-send");

    let enabled = null;      // null = 아직 확인 전
    let streaming = false;
    let controller = null;
    let stickToBottom = true;
    let statusLoaded = false;
    let messages = loadMessages();
    let draft = null;        // 스트리밍 중인 답변 {record, node, answer}

    // ── 세션 ──
    function newSessionId() {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
      return `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function readSessionId() {
      return readStore(SESSION_KEY);
    }

    function writeSessionId(value) {
      writeStore(SESSION_KEY, value);
    }

    function persist() {
      messages = saveMessages(messages);
    }

    function remember(record) {
      messages.push(record);
      persist();
      return record;
    }

    // ── 미확인 표시 ──
    function setUnread(on) {
      unreadDot.hidden = !on;
      writeStore(UNREAD_KEY, on ? "1" : "");
    }

    function ensureSessionId() {
      const current = readSessionId();
      if (current) return current;
      const next = newSessionId();
      writeSessionId(next);
      return next;
    }

    // ── 스크롤 ──
    body.addEventListener("scroll", () => {
      stickToBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    });

    function scrollToEnd(force) {
      if (force) stickToBottom = true;
      if (stickToBottom) body.scrollTop = body.scrollHeight;
    }

    // ── 화면 상태 ──
    function refreshEmptyState() {
      // 꺼져 있으면 추천 질문 자체를 감춘다 — 눌러도 아무 일이 없기 때문이다.
      emptyBox.hidden = enabled === false || thread.childElementCount > 0;
    }

    function setNotice(text) {
      if (!text) {
        notice.hidden = true;
        notice.textContent = "";
        return;
      }
      notice.hidden = false;
      notice.textContent = text;
    }

    function setBadge(text, tone) {
      if (!text) {
        badge.hidden = true;
        return;
      }
      badge.hidden = false;
      badge.className = `status-chip ${tone} asst-badge`;
      badge.textContent = text;
    }

    function setComposerEnabled(on) {
      input.disabled = !on;
      sendBtn.disabled = !on;
    }

    function setStreaming(on) {
      streaming = on;
      sendBtn.textContent = on ? "중지" : "보내기";
      sendBtn.classList.toggle("is-stop", on);
      sendBtn.disabled = false;
      if (resetBtn) resetBtn.disabled = on;
      if (!on && enabled === false) setComposerEnabled(false);
    }

    // ── 말풍선 ──
    // 말풍선 아래 한 줄: 시각(HH:MM)은 늘, 응답 시간·모델은 도우미 말풍선에만.
    function buildFoot(ts) {
      const foot = document.createElement("div");
      foot.className = "asst-foot";
      const time = document.createElement("span");
      time.className = "asst-time";
      time.textContent = clockLabel(ts);
      const meta = document.createElement("span");
      meta.className = "asst-meta";
      meta.hidden = true;
      foot.appendChild(time);
      foot.appendChild(meta);
      return { foot, time, meta };
    }

    function appendUser(text, ts) {
      const wrap = document.createElement("div");
      wrap.className = "asst-msg asst-msg-user";
      const bubble = document.createElement("div");
      bubble.className = "asst-bubble asst-bubble-user";
      bubble.textContent = text;
      const parts = buildFoot(ts);
      wrap.appendChild(bubble);
      wrap.appendChild(parts.foot);
      thread.appendChild(wrap);
      refreshEmptyState();
      scrollToEnd(true);
    }

    function appendAssistant(ts) {
      const wrap = document.createElement("div");
      wrap.className = "asst-msg asst-msg-bot";

      const tools = document.createElement("div");
      tools.className = "asst-tools";
      tools.hidden = true;

      const bubble = document.createElement("div");
      bubble.className = "asst-bubble asst-bubble-bot asst-md";

      const parts = buildFoot(ts);

      const followups = document.createElement("div");
      followups.className = "asst-chip-row";
      followups.hidden = true;

      wrap.appendChild(tools);
      wrap.appendChild(bubble);
      wrap.appendChild(parts.foot);
      wrap.appendChild(followups);
      thread.appendChild(wrap);
      refreshEmptyState();
      scrollToEnd(true);
      return { wrap, tools, bubble, meta: parts.meta, time: parts.time, followups };
    }

    function metaLabel(meta) {
      if (!meta || typeof meta !== "object") return "";
      if (meta.stopped) return STOPPED_TEXT;
      const bits = [];
      if (typeof meta.duration_ms === "number" && isFinite(meta.duration_ms)) {
        bits.push(`${(meta.duration_ms / 1000).toFixed(1)}초`);
      }
      if (meta.model) bits.push(String(meta.model));
      return bits.join(" · ");
    }

    function paintMeta(node, meta) {
      const text = metaLabel(meta);
      node.meta.hidden = !text;
      node.meta.textContent = text;
    }

    function renderTools(node, list) {
      const items = Array.isArray(list) ? list : [];
      if (!items.length) {
        node.hidden = true;
        node.innerHTML = "";
        return;
      }
      node.hidden = false;
      node.innerHTML = items
        .map((tool) => {
          const label = escapeHtml(tool.label || tool.name || "");
          const tone = tool.status === "error"
            ? "status-canceled"
            : tool.status === "running"
              ? "status-in_progress"
              : "status-completed";
          return `<span class="status-chip ${tone}">${label}</span>`;
        })
        .join("");
    }

    function renderFollowups(node, list) {
      const items = (Array.isArray(list) ? list : []).filter((text) => typeof text === "string" && text.trim());
      if (!items.length) {
        node.hidden = true;
        node.innerHTML = "";
        return;
      }
      node.hidden = false;
      node.innerHTML = "";
      items.slice(0, 4).forEach((text) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-sm";
        btn.textContent = text;
        btn.addEventListener("click", () => ask(text));
        node.appendChild(btn);
      });
    }

    // ── 저장된 대화 복원 ──
    // 처음 그릴 때와 같은 렌더러를 통과시킨다 — 저장된 것은 원본 마크다운뿐이다.
    function restoreMessage(record) {
      if (record.role === "user") {
        appendUser(record.text, record.ts);
        return;
      }
      const node = appendAssistant(record.ts);
      renderTools(node.tools, record.tools);
      if (record.error) {
        node.bubble.classList.add("is-error");
        node.bubble.textContent = record.error.message || FALLBACK_ERROR;
      } else if (record.text) {
        node.bubble.innerHTML = renderMarkdown(record.text);
      } else {
        node.bubble.innerHTML = `<p>${escapeHtml(STOPPED_TEXT)}</p>`;
      }
      const meta = record.meta || (record.text || record.error ? null : { stopped: true });
      paintMeta(node, meta);
      if (!record.error) renderFollowups(node.followups, record.suggestions);
    }

    function restoreThread() {
      messages.forEach(restoreMessage);
      refreshEmptyState();
      scrollToEnd(true);
    }

    // ── 질문 보내기 ──
    function buildContext() {
      const context = { path: window.location.pathname };
      const recipeEl = document.getElementById("blend-recipe");
      if (recipeEl && recipeEl.value) {
        const option = recipeEl.options ? recipeEl.options[recipeEl.selectedIndex] : null;
        const label = option ? String(option.textContent || "").trim() : "";
        if (label) context.recipe = label;
      }
      const productEl = document.getElementById("visc-product-select");
      if (productEl && productEl.value) context.product = String(productEl.value).trim();
      return context;
    }

    function stopStream() {
      if (controller) controller.abort();
    }

    async function ask(rawQuery) {
      const query = String(rawQuery || "").trim();
      if (!query || streaming || enabled === false) return;

      const askedAt = Date.now();
      appendUser(query, askedAt);
      remember({ role: "user", text: query, ts: askedAt });

      const node = appendAssistant(Date.now());
      input.value = "";
      autoGrow();

      const sessionId = ensureSessionId();
      const startedAt = Date.now();
      let answer = "";
      let toolList = [];
      let settled = false;
      let model = "";
      let provider = "";

      // 스트리밍이 끝나기 전에 화면을 떠나도 받은 만큼은 남기려고 먼저 자리를 만든다.
      const record = remember({ role: "assistant", text: "", ts: startedAt });
      draft = { record, answer: "" };

      controller = new AbortController();
      setStreaming(true);

      const paint = () => {
        node.bubble.innerHTML = renderMarkdown(answer) + '<span class="asst-cursor"></span>';
        scrollToEnd(false);
      };
      paint();

      const showError = (text) => {
        settled = true;
        node.bubble.classList.add("is-error");
        node.bubble.textContent = text;
        node.meta.hidden = true;
        record.text = "";
        record.error = { code: "", message: text };
        delete record.meta;
        persist();
        if (panel.hidden) setUnread(true);
        scrollToEnd(false);
      };

      const handlers = {
        meta: (data) => {
          if (data.session_id) writeSessionId(String(data.session_id));
          if (data.model) model = String(data.model);
          if (data.provider) provider = String(data.provider);
        },
        tool_call: (data) => {
          const name = String(data.name || "");
          const existing = toolList.find((tool) => tool.name === name);
          if (existing) existing.status = data.status || existing.status;
          else toolList.push({ name, label: data.label || name, status: data.status || "running" });
          renderTools(node.tools, toolList);
          scrollToEnd(false);
        },
        token: (data) => {
          if (typeof data.text !== "string") return;
          answer += data.text;
          if (draft) draft.answer = answer;
          paint();
        },
        done: (data) => {
          settled = true;
          if (typeof data.answer === "string" && data.answer.trim()) answer = data.answer;
          if (Array.isArray(data.tools_used) && data.tools_used.length) toolList = data.tools_used;
          if (data.model) model = String(data.model);
          if (data.provider) provider = String(data.provider);
          renderTools(node.tools, toolList);
          node.bubble.innerHTML = renderMarkdown(answer);
          const elapsed = typeof data.duration_ms === "number"
            ? data.duration_ms
            : Date.now() - startedAt;
          const suggestions = (Array.isArray(data.suggestions) ? data.suggestions : [])
            .filter((text) => typeof text === "string" && text.trim())
            .slice(0, 4);
          paintMeta(node, { duration_ms: elapsed, model, provider });
          renderFollowups(node.followups, suggestions);

          record.text = answer;
          record.ts = startedAt;
          record.tools = toolList.map((tool) => ({
            name: String(tool.name || ""),
            label: String(tool.label || tool.name || ""),
            status: String(tool.status || "done"),
          }));
          record.meta = { provider, model, duration_ms: elapsed };
          if (suggestions.length) record.suggestions = suggestions;
          delete record.error;
          persist();

          // 창이 닫혀 있는 사이에 답이 끝났으면 단추에 미확인 점을 켠다.
          if (panel.hidden) setUnread(true);
          scrollToEnd(false);
        },
        error: (data) => {
          const code = String(data.code || "");
          showError(String(data.message || ERROR_TEXT[code] || FALLBACK_ERROR));
        },
      };

      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;

        const response = await fetch("/api/assistant/stream", {
          method: "POST",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ query, session_id: sessionId, context: buildContext() }),
          signal: controller.signal,
        });

        if (response.status === 429) {
          showError(RATE_LIMIT_TEXT);
        } else if (!response.ok || !response.body) {
          let message = FALLBACK_ERROR;
          try {
            const payload = await response.json();
            if (payload && payload.message) message = String(payload.message);
            else if (payload && payload.code && ERROR_TEXT[payload.code]) message = ERROR_TEXT[payload.code];
          } catch (_error) {
            /* 본문이 JSON 이 아니면 기본 문구 */
          }
          showError(message);
        } else {
          const reader = response.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let buffer = "";
          for (;;) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            buffer = buffer.replace(/\r\n/g, "\n");
            let cut = buffer.indexOf("\n\n");
            while (cut !== -1) {
              const frame = buffer.slice(0, cut);
              buffer = buffer.slice(cut + 2);
              parseFrame(frame, handlers);
              cut = buffer.indexOf("\n\n");
            }
          }
          if (buffer.trim()) parseFrame(buffer, handlers);
        }
      } catch (error) {
        if (!error || error.name !== "AbortError") {
          showError(FALLBACK_ERROR);
        }
      } finally {
        controller = null;
        draft = null;
        setStreaming(false);
        if (!settled) {
          // done/error 없이 끊겼다(사용자 중지·화면 이동·서버 조기 종료). 받은 만큼은 남긴다.
          node.bubble.innerHTML = answer
            ? renderMarkdown(answer)
            : `<p>${escapeHtml(STOPPED_TEXT)}</p>`;
          node.meta.hidden = false;
          node.meta.textContent = STOPPED_TEXT;
          record.text = answer;
          record.meta = { stopped: true };
          persist();
        }
        scrollToEnd(false);
      }
    }

    // ── 초기화 ──
    async function resetConversation() {
      if (streaming) return;
      const sessionId = readSessionId();
      thread.innerHTML = "";
      messages = [];
      writeStore(MESSAGES_KEY, "");
      setUnread(false);
      refreshEmptyState();
      writeSessionId("");
      if (!sessionId) return;
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        await fetch("/api/assistant/reset", {
          method: "POST",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ session_id: sessionId }),
        });
      } catch (_error) {
        /* 서버가 못 지워도 화면 대화는 이미 비웠다 */
      }
    }

    // ── 상태 확인 ──
    async function loadStatus() {
      if (statusLoaded) return;
      statusLoaded = true;
      try {
        const response = await fetch("/api/assistant/status", { credentials: "same-origin" });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        enabled = !!data.enabled;
        if (enabled) {
          // 배지는 공급자만. 모델명은 답변 아래 줄에 나오고, 둘을 붙이면 헤더에서 잘린다.
          setBadge(data.provider ? String(data.provider) : "준비됨", "status-completed");
          setNotice("");
          setComposerEnabled(true);
        } else {
          setBadge("꺼짐", "status-canceled");
          setNotice(DISABLED_NOTICE);
          setComposerEnabled(false);
        }
      } catch (_error) {
        enabled = false;
        setBadge("꺼짐", "status-canceled");
        setNotice(DISABLED_NOTICE);
        setComposerEnabled(false);
      }
      refreshEmptyState();
    }

    // ── 열고 닫기 ──
    // 열림 여부를 탭에 남겨, 메뉴를 옮겨도 대화가 그대로 보이게 한다.
    function open(options) {
      const quiet = !!(options && options.quiet);
      panel.hidden = false;
      launcher.hidden = true;
      launcher.setAttribute("aria-expanded", "true");
      writeStore(OPEN_KEY, "1");
      setUnread(false);
      loadStatus();
      scrollToEnd(true);
      if (!quiet && !input.disabled) input.focus();
    }

    // 닫아도 답변은 계속 받는다. 끝나면 단추에 미확인 점이 켜진다.
    function close(options) {
      const quiet = !!(options && options.quiet);
      panel.hidden = true;
      launcher.hidden = false;
      launcher.setAttribute("aria-expanded", "false");
      writeStore(OPEN_KEY, "");
      if (!quiet) launcher.focus();
    }

    function autoGrow() {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    }

    // ── 이벤트 ──
    SUGGESTIONS.forEach((text) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm";
      btn.textContent = text;
      btn.addEventListener("click", () => ask(text));
      suggestionBox.appendChild(btn);
    });

    launcher.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    resetBtn.addEventListener("click", resetConversation);

    composer.addEventListener("submit", (event) => {
      event.preventDefault();
      if (streaming) stopStream();
      else ask(input.value);
    });

    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!streaming) ask(input.value);
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) close();
    });

    // 답변을 받는 중에 메뉴를 옮기면 요청을 끊고, 받은 만큼을 '중단됨'으로 남긴다.
    let leaving = false;
    function handleLeave() {
      if (leaving) return;
      leaving = true;
      if (streaming && draft) {
        draft.record.text = draft.answer || "";
        draft.record.meta = { stopped: true };
        persist();
      }
      stopStream();
    }

    window.addEventListener("pagehide", handleLeave);
    window.addEventListener("beforeunload", handleLeave);

    // 저장된 대화·열림 상태 복원. 화면을 옮겨도 대화창이 그대로 이어져야 한다.
    restoreThread();
    if (readStore(UNREAD_KEY) === "1") setUnread(true);
    if (readStore(OPEN_KEY) === "1") open({ quiet: true });

    refreshEmptyState();
    IRMS.assistant = { open, close };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

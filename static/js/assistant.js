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
 * 대화는 메모리에만 둔다(새로고침하면 사라짐). session_id 만 sessionStorage 에 남긴다.
 * 전역 노출은 window.IRMS.assistant = {open, close} 하나뿐.
 */
(function () {
  "use strict";

  const IRMS = (window.IRMS = window.IRMS || {});

  const SESSION_KEY = "brm-assistant-session";
  const SUGGESTIONS = [
    "오늘 배합 몇 건이야?",
    "PB 점도 최근 상태 알려줘",
    "이번 달 자재 사용량 알려줘",
    "APB 레시피 알려줘",
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
  // 라이브러리 없이 굵게·인라인 코드·글머리·번호·표만 지원한다. 항상 escape 를
  // 먼저 하므로 모델이 뱉은 HTML 은 글자로만 보인다.
  function inlineMarkdown(text) {
    return text
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
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

  function init() {
    const launcher = document.getElementById("asst-launcher");
    const panel = document.getElementById("asst-panel");
    if (!launcher || !panel) return;

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

    // ── 세션 ──
    function newSessionId() {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
      return `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function readSessionId() {
      try {
        return window.sessionStorage.getItem(SESSION_KEY) || "";
      } catch (_error) {
        return "";
      }
    }

    function writeSessionId(value) {
      try {
        if (value) window.sessionStorage.setItem(SESSION_KEY, value);
        else window.sessionStorage.removeItem(SESSION_KEY);
      } catch (_error) {
        /* 사설 모드 등에서 저장이 막히면 세션 없이 동작한다 */
      }
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
    function appendUser(text) {
      const wrap = document.createElement("div");
      wrap.className = "asst-msg asst-msg-user";
      const bubble = document.createElement("div");
      bubble.className = "asst-bubble asst-bubble-user";
      bubble.textContent = text;
      wrap.appendChild(bubble);
      thread.appendChild(wrap);
      refreshEmptyState();
      scrollToEnd(true);
    }

    function appendAssistant() {
      const wrap = document.createElement("div");
      wrap.className = "asst-msg asst-msg-bot";

      const tools = document.createElement("div");
      tools.className = "asst-tools";
      tools.hidden = true;

      const bubble = document.createElement("div");
      bubble.className = "asst-bubble asst-bubble-bot asst-md";

      const meta = document.createElement("p");
      meta.className = "asst-meta";
      meta.hidden = true;

      const followups = document.createElement("div");
      followups.className = "asst-chip-row";
      followups.hidden = true;

      wrap.appendChild(tools);
      wrap.appendChild(bubble);
      wrap.appendChild(meta);
      wrap.appendChild(followups);
      thread.appendChild(wrap);
      refreshEmptyState();
      scrollToEnd(true);
      return { wrap, tools, bubble, meta, followups };
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

      appendUser(query);
      const node = appendAssistant();
      input.value = "";
      autoGrow();

      const sessionId = ensureSessionId();
      const startedAt = Date.now();
      let answer = "";
      let toolList = [];
      let settled = false;
      let model = "";

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
        scrollToEnd(false);
      };

      const handlers = {
        meta: (data) => {
          if (data.session_id) writeSessionId(String(data.session_id));
          if (data.model) model = String(data.model);
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
          paint();
        },
        done: (data) => {
          settled = true;
          if (typeof data.answer === "string" && data.answer.trim()) answer = data.answer;
          if (Array.isArray(data.tools_used) && data.tools_used.length) toolList = data.tools_used;
          renderTools(node.tools, toolList);
          node.bubble.innerHTML = renderMarkdown(answer);
          const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
          node.meta.hidden = false;
          node.meta.textContent = model ? `${seconds}초 · ${model}` : `${seconds}초`;
          renderFollowups(node.followups, data.suggestions);
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
        setStreaming(false);
        if (!settled) {
          // done/error 없이 끊겼다(사용자 중지·서버 조기 종료). 받은 만큼은 남긴다.
          node.bubble.innerHTML = answer
            ? renderMarkdown(answer)
            : `<p>${escapeHtml(STOPPED_TEXT)}</p>`;
          node.meta.hidden = false;
          node.meta.textContent = STOPPED_TEXT;
        }
        scrollToEnd(false);
      }
    }

    // ── 초기화 ──
    async function resetConversation() {
      if (streaming) return;
      const sessionId = readSessionId();
      thread.innerHTML = "";
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
    function open() {
      panel.hidden = false;
      launcher.hidden = true;
      launcher.setAttribute("aria-expanded", "true");
      loadStatus();
      scrollToEnd(true);
      if (!input.disabled) input.focus();
    }

    function close() {
      stopStream();
      panel.hidden = true;
      launcher.hidden = false;
      launcher.setAttribute("aria-expanded", "false");
      launcher.focus();
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

    refreshEmptyState();
    IRMS.assistant = { open, close };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

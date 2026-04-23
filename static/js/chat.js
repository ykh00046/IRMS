/**
 * Shared chat module for work and management pages.
 * Usage: const chat = IRMS.createChat({ prefix, stageLabels, elements, state });
 * Default prefix is "chat" — matches unified .chat-* CSS classes.
 */
(function () {
  "use strict";

  const STAGE_LABELS = {
    registered: "등록",
    in_progress: "진행중",
    completed: "완료",
  };

  /**
   * @param {object} config
   * @param {string} config.prefix - CSS class prefix ("work-chat" or "management-chat")
   * @param {object} config.elements - { roomTabs, chatMessages, chatStageGroup, roomMeta }
   * @param {object} config.state - Must have: rooms, selectedRoomKey, latestByRoom, timerId, currentUsername
   * @param {string} [config.storageKey="irms_chat_room"]
   */
  function createChat(config) {
    const prefix = config.prefix;
    const els = config.elements;
    const chatState = config.state;
    const storageKey = config.storageKey || "irms_chat_room";
    const stageLabels = config.stageLabels || STAGE_LABELS;
    const unreadByRoom = {};

    function getSelectedRoom() {
      return chatState.rooms.find((r) => r.key === chatState.selectedRoomKey) || null;
    }

    function persistSelectedRoom() {
      window.localStorage.setItem(storageKey, chatState.selectedRoomKey);
    }

    function renderRoomTabs() {
      if (!els.roomTabs) return;

      if (!chatState.rooms.length) {
        els.roomTabs.innerHTML = '<div class="empty-state">대화방이 없습니다.</div>';
        return;
      }

      els.roomTabs.innerHTML = chatState.rooms
        .map((room) => {
          const isActive = room.key === chatState.selectedRoomKey;
          const unread = unreadByRoom[room.key] || 0;
          const unreadBadge = !isActive && unread > 0
            ? `<span class="chat-unread-badge">${unread > 99 ? "99+" : unread}</span>`
            : "";
          const countLabel =
            room.messageCount > 0
              ? `<span class="${prefix}-tab-count">${IRMS.escapeHtml(IRMS.formatValue(room.messageCount))}</span>`
              : "";
          return `
            <button type="button" class="${prefix}-tab${isActive ? " active" : ""}${unread > 0 && !isActive ? " has-unread" : ""}" data-room-key="${IRMS.escapeHtml(room.key)}">
              <span>${IRMS.escapeHtml(room.name)}</span>
              ${unreadBadge}
              ${countLabel}
            </button>
          `;
        })
        .join("");
    }

    function syncStageVisibility() {
      const room = getSelectedRoom();
      const stageRequired = Boolean(room?.stageRequired);

      if (els.chatStageGroup) {
        els.chatStageGroup.classList.toggle("hidden", !stageRequired);
      }
      if (els.roomMeta) {
        els.roomMeta.textContent = room ? room.name : "대화방";
      }
    }

    function renderMessages(items, options) {
      options = options || {};
      if (!els.chatMessages) return;

      const replace = Boolean(options.replace);
      if (!items.length && replace) {
        els.chatMessages.innerHTML = `
          <div class="${prefix}-empty">
            <strong>메시지가 없습니다.</strong>
            <p class="muted">첫 메시지를 입력하세요.</p>
          </div>
        `;
        return;
      }

      const markup = items
        .map((message) => {
          const isOwn = chatState.currentUsername && message.createdByUsername === chatState.currentUsername;
          const stageBadge = message.stage
            ? `<span class="${prefix}-stage-badge stage-${IRMS.escapeHtml(message.stage)}">${IRMS.escapeHtml(stageLabels[message.stage] || message.stage)}</span>`
            : "";
          const authorLine = isOwn
            ? ""
            : `<strong class="${prefix}-author">${IRMS.escapeHtml(message.createdByDisplayName || message.createdByUsername)}</strong>`;

          return `
            <div class="${prefix}-message-row${isOwn ? " own" : ""}" data-message-id="${message.id}">
              <article class="${prefix}-message${isOwn ? " own" : ""}">
                ${authorLine}
                <p class="${prefix}-text">${IRMS.escapeHtml(message.messageText)}</p>
                <div class="${prefix}-meta">
                  ${stageBadge}
                  <time>${IRMS.escapeHtml(IRMS.formatDateTime(message.createdAt))}</time>
                </div>
              </article>
            </div>
          `;
        })
        .join("");

      if (replace) {
        els.chatMessages.innerHTML = markup;
      } else {
        const emptyState = els.chatMessages.querySelector(`.${prefix}-empty`);
        if (emptyState) emptyState.remove();
        els.chatMessages.insertAdjacentHTML("beforeend", markup);
      }

      els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    }

    const prevMessageCounts = {};

    async function loadRooms() {
      const payload = await IRMS.listChatRooms();
      const newRooms = payload.items || [];

      // Track unread for non-active rooms by messageCount delta
      var hasOtherRoomNew = false;
      newRooms.forEach(function (room) {
        var prev = prevMessageCounts[room.key];
        if (prev !== undefined && room.messageCount > prev && room.key !== chatState.selectedRoomKey) {
          unreadByRoom[room.key] = (unreadByRoom[room.key] || 0) + (room.messageCount - prev);
          hasOtherRoomNew = true;
        }
        prevMessageCounts[room.key] = room.messageCount;
      });

      // Play sound for new messages in non-selected rooms
      if (hasOtherRoomNew) {
        IRMS.playChatSound();
      }

      chatState.rooms = newRooms;

      if (!chatState.rooms.length) {
        renderRoomTabs();
        syncStageVisibility();
        return;
      }

      if (!chatState.rooms.some((r) => r.key === chatState.selectedRoomKey)) {
        chatState.selectedRoomKey = chatState.rooms[0].key;
        persistSelectedRoom();
      }

      renderRoomTabs();
      syncStageVisibility();
    }

    async function loadMessages(options) {
      options = options || {};
      const room = getSelectedRoom();
      if (!room) {
        if (els.chatMessages && Boolean(options.replace)) {
          els.chatMessages.innerHTML = `
            <div class="${prefix}-empty">
              <strong>대화방이 선택되지 않았습니다.</strong>
              <p class="muted">페이지를 새로고침하세요.</p>
            </div>
          `;
        }
        return;
      }

      const replace = Boolean(options.replace);
      const afterId = replace ? 0 : Number(chatState.latestByRoom[room.key] || 0);
      const payload = await IRMS.getChatMessages({ roomKey: room.key, limit: 80, afterId });

      if (replace) {
        renderMessages(payload.items || [], { replace: true });
      } else if ((payload.items || []).length > 0) {
        renderMessages(payload.items || [], { replace: false });

        // Notify for new messages from others
        var incoming = (payload.items || []).filter(function (m) {
          return !chatState.currentUsername || m.createdByUsername !== chatState.currentUsername;
        });
        if (incoming.length > 0 && afterId > 0) {
          IRMS.playChatSound();
          var last = incoming[incoming.length - 1];
          var speaker = last.createdByDisplayName || last.createdByUsername || "";
          var text = speaker
            ? speaker + "님: " + last.messageText
            : last.messageText;
          IRMS.speakText(text);
        }
      }

      chatState.latestByRoom[room.key] = Number(
        payload.latestId || chatState.latestByRoom[room.key] || 0
      );
      syncStageVisibility();
    }

    async function refresh(options) {
      options = options || {};
      try {
        await loadRooms();
        await loadMessages({ replace: Boolean(options.replace) });
      } catch (error) {
        if (!options.silent) {
          IRMS.notify(`메시지 동기화 실패: ${error.message}`, "error");
        }
      }
    }

    function startPolling(intervalMs) {
      if (chatState.timerId) {
        window.clearInterval(chatState.timerId);
      }
      chatState.timerId = window.setInterval(() => {
        if (document.visibilityState === "hidden") return;
        refresh({ replace: false, silent: true });
      }, intervalMs || 10000);
    }

    function stopPolling() {
      if (chatState.timerId) {
        window.clearInterval(chatState.timerId);
        chatState.timerId = null;
      }
    }

    /**
     * Bind a chat form for submission.
     * @param {object} opts - { form, input, stage, send }
     */
    function bindForm(opts) {
      const { form, input, stage, send } = opts;
      if (!form) return;

      let sending = false;

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (sending) return;

        const room = getSelectedRoom();
        if (!room) return;

        const messageText = input?.value.trim() || "";
        const stageVal = null;

        if (!messageText) {
          IRMS.notify("메시지를 입력하세요.", "error");
          return;
        }

        sending = true;
        IRMS.btnLoading(send, true);

        try {
          const payload = await IRMS.postChatMessage({
            roomKey: room.key,
            messageText,
            stage: stageVal,
          });

          if (payload.message) {
            renderMessages([payload.message], { replace: false });
            chatState.latestByRoom[room.key] = Number(
              payload.message.id || chatState.latestByRoom[room.key] || 0
            );
            const idx = chatState.rooms.findIndex((r) => r.key === room.key);
            if (idx >= 0) {
              chatState.rooms[idx].messageCount = Number(chatState.rooms[idx].messageCount || 0) + 1;
              chatState.rooms[idx].latestMessageAt = payload.message.createdAt;
              renderRoomTabs();
            }
          }
          if (input) { input.value = ""; input.focus(); }
          IRMS.notify("메시지를 등록했습니다.", "success");
        } catch (error) {
          IRMS.notify(`메시지 등록 실패: ${error.message}`, "error");
        } finally {
          sending = false;
          IRMS.btnLoading(send, false);
        }
      });
    }

    /**
     * Bind room-tab click events (delegated).
     * @param {HTMLElement} container - The room-tabs element
     */
    function bindRoomTabs(container) {
      if (!container) return;
      container.addEventListener("click", async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const button = target.closest("[data-room-key]");
        if (!(button instanceof HTMLElement)) return;
        const nextRoomKey = button.dataset.roomKey;
        if (!nextRoomKey || nextRoomKey === chatState.selectedRoomKey) return;

        chatState.selectedRoomKey = nextRoomKey;
        unreadByRoom[nextRoomKey] = 0;
        persistSelectedRoom();
        renderRoomTabs();
        syncStageVisibility();
        try {
          await loadMessages({ replace: true });
        } catch (error) {
          IRMS.notify(`메시지 불러오기 실패: ${error.message}`, "error");
        }
      });
    }

    return {
      getSelectedRoom,
      persistSelectedRoom,
      renderRoomTabs,
      syncStageVisibility,
      renderMessages,
      loadRooms,
      loadMessages,
      refresh,
      startPolling,
      stopPolling,
      bindForm,
      bindRoomTabs,
    };
  }

  window.IRMS = window.IRMS || {};
  window.IRMS.createChat = createChat;
})();

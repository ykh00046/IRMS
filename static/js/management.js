document.addEventListener("DOMContentLoaded", () => {
  const shell = document.querySelector(".site-shell");
  const spreadsheetContainer = document.getElementById("spreadsheet");
  const rawInput = document.getElementById("raw-input");
  const previewBtn = document.getElementById("preview-btn");
  const registerBtn = document.getElementById("register-btn");
  const clearBtn = document.getElementById("clear-btn");

  const previewMeta = document.getElementById("preview-meta");
  const previewHead = document.getElementById("preview-head");
  const previewBody = document.getElementById("preview-body");
  const errorList = document.getElementById("error-list");
  const warningList = document.getElementById("warning-list");

  const historyBody = document.getElementById("history-body");
  const historyStatus = document.getElementById("history-status");
  const historySearch = document.getElementById("history-search");
  const historyFrom = document.getElementById("history-from");
  const historyTo = document.getElementById("history-to");
  const historySummary = document.getElementById("management-history-summary");
  const historyResetBtn = document.getElementById("management-history-reset");
  const roomMeta = document.getElementById("management-chat-room-meta");
  const roomTabs = document.getElementById("management-chat-room-tabs");
  const chatMessages = document.getElementById("management-chat-messages");
  const chatForm = document.getElementById("management-chat-form");
  const chatStageGroup = document.getElementById("management-chat-stage-group");
  const chatStage = document.getElementById("management-chat-stage");
  const chatInput = document.getElementById("management-chat-input");
  const chatSend = document.getElementById("management-chat-send");

  let currentPreview = null;
  let materials = [];
  let sheet = null;
  let confirmedRawText = "";
  let previewIsStale = false;
  let suppressDirtyTracking = false;
  let spreadsheetFallbackNotified = false;
  const chatState = {
    currentUsername: shell?.dataset.currentUsername || "",
    selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
    rooms: [],
    latestByRoom: {},
    sending: false,
    timerId: null,
  };

  const stageLabels = {
    registered: "Registered",
    in_progress: "In Progress",
    completed: "Completed",
  };

  const preferenceKeys = {
    status: "irms_management_history_status",
    search: "irms_management_history_search",
    from: "irms_management_history_from",
    to: "irms_management_history_to",
  };

  function getSpreadsheetFactory() {
    if (typeof window.jspreadsheet === "function") {
      return window.jspreadsheet;
    }
    if (typeof window.jexcel === "function") {
      return window.jexcel;
    }
    return null;
  }

  function setRawInputMode(enabled) {
    if (spreadsheetContainer) {
      spreadsheetContainer.style.display = enabled ? "none" : "";
    }
    if (rawInput) {
      rawInput.hidden = !enabled;
      rawInput.disabled = !enabled;
    }
  }

  function syncRegisterState() {
    const canRegister =
      Boolean(currentPreview) &&
      !previewIsStale &&
      currentPreview.errors.length === 0 &&
      currentPreview.rows.length > 0 &&
      confirmedRawText.trim().length > 0;
    registerBtn.disabled = !canRegister;
  }

  function persistHistoryFilters() {
    IRMS.savePreference(preferenceKeys.status, historyStatus.value);
    IRMS.savePreference(preferenceKeys.search, historySearch.value.trim());
    IRMS.savePreference(preferenceKeys.from, historyFrom.value);
    IRMS.savePreference(preferenceKeys.to, historyTo.value);
  }

  function updateHistorySummary() {
    if (!historySummary) {
      return;
    }

    const parts = [`상태 ${historyStatus.value || "전체"}`];
    const search = historySearch.value.trim();
    const from = historyFrom.value;
    const to = historyTo.value;

    if (search) {
      parts.push(`검색어 "${search}"`);
    }
    if (from || to) {
      parts.push(`기간 ${from || "시작 미지정"} ~ ${to || "종료 미지정"}`);
    }

    historySummary.textContent = `${parts.join(" · ")} 기준으로 등록 이력을 표시 중입니다.`;
  }

  function restoreHistoryFilters() {
    historyStatus.value = IRMS.loadPreference(preferenceKeys.status, "");
    historySearch.value = IRMS.loadPreference(preferenceKeys.search, "");
    historyFrom.value = IRMS.loadPreference(preferenceKeys.from, "");
    historyTo.value = IRMS.loadPreference(preferenceKeys.to, "");
  }

  function resetHistoryFilters() {
    historyStatus.value = "";
    historySearch.value = "";
    historyFrom.value = "";
    historyTo.value = "";
    IRMS.clearPreference(preferenceKeys.status);
    IRMS.clearPreference(preferenceKeys.search);
    IRMS.clearPreference(preferenceKeys.from);
    IRMS.clearPreference(preferenceKeys.to);
    updateHistorySummary();
    renderHistory();
  }

  function markPreviewStale() {
    if (suppressDirtyTracking || !currentPreview || previewIsStale) {
      return;
    }
    previewIsStale = true;
    syncRegisterState();
    renderPreview(currentPreview);
    IRMS.notify("?쒗듃媛 ?섏젙?섏뼱 ?뺤젙蹂몄씠 臾댄슚?붾릺?덉뒿?덈떎. ?ㅼ떆 Validate ?섏꽭??", "warn");
  }

  function destroySpreadsheet() {
    if (sheet && typeof sheet.destroy === "function") {
      sheet.destroy();
    } else if (
      spreadsheetContainer &&
      window.jspreadsheet &&
      typeof window.jspreadsheet.destroy === "function" &&
      spreadsheetContainer.spreadsheet
    ) {
      window.jspreadsheet.destroy(spreadsheetContainer, true);
    }

    if (spreadsheetContainer) {
      spreadsheetContainer.innerHTML = "";
    }
    sheet = null;
  }

  function getActiveWorksheet() {
    if (sheet && typeof sheet.getData === "function") {
      return sheet;
    }

    const workbook = spreadsheetContainer?.spreadsheet;
    const worksheet = workbook?.worksheets?.[0] || null;
    if (worksheet && typeof worksheet.getData === "function") {
      sheet = worksheet;
      return worksheet;
    }

    return null;
  }

  // Initialize JSpreadsheet
  function initSpreadsheet() {
    suppressDirtyTracking = true;
    destroySpreadsheet();

    const spreadsheetFactory = getSpreadsheetFactory();
    if (!spreadsheetFactory) {
      sheet = null;
      setRawInputMode(true);
      suppressDirtyTracking = false;
      if (!spreadsheetFallbackNotified) {
        IRMS.notify(
          "?ㅽ봽?덈뱶?쒗듃 UI 濡쒕뱶???ㅽ뙣?섏뿬 ?띿뒪???낅젰 紐⑤뱶濡??꾪솚?덉뒿?덈떎.",
          "warn",
        );
        spreadsheetFallbackNotified = true;
      }
      return;
    }

    setRawInputMode(false);

    // Create an empty 15x20 grid by default
    const data = Array.from({ length: 15 }, () => Array(20).fill(""));

    spreadsheetFactory(spreadsheetContainer, {
      worksheets: [
        {
          data,
          minDimensions: [20, 15],
          defaultColWidth: 100,
          tableOverflow: true,
          tableWidth: "100%",
          tableHeight: "300px",
          rowResize: true,
          columnDrag: true,
          contextMenu: true,
          textOverflow: true,
          onchange: () => {
            markPreviewStale();
          },
          onafterchanges: () => {
            markPreviewStale();
          },
        },
      ],
    });

    // Prevent false dirty events during first paint.
    setTimeout(() => {
      getActiveWorksheet();
      suppressDirtyTracking = false;
    }, 0);
  }

  // Extract data from spreadsheet and convert to tab-separated text
  function getSpreadsheetDataAsText() {
    const worksheet = getActiveWorksheet();
    if (!worksheet) {
      return String(rawInput?.value || "").trim();
    }

    const rawData = worksheet.getData();

    // Find the last row and column that actually has data to avoid sending huge empty grids
    let maxRow = -1;
    let maxCol = -1;

    for (let r = 0; r < rawData.length; r++) {
      for (let c = 0; c < rawData[r].length; c++) {
        if (rawData[r][c] !== null && String(rawData[r][c]).trim() !== "") {
          maxRow = Math.max(maxRow, r);
          maxCol = Math.max(maxCol, c);
        }
      }
    }

    if (maxRow === -1 || maxCol === -1) {
      return ""; // completely empty
    }

    // Trim the data to the bounding box of actual content
    const trimmedData = [];
    for (let r = 0; r <= maxRow; r++) {
      const row = [];
      for (let c = 0; c <= maxCol; c++) {
        row.push(String(rawData[r][c] || "").trim());
      }
      trimmedData.push(row.join("\t"));
    }

    return trimmedData.join("\n");
  }

  async function loadMaterials() {
    materials = await IRMS.getMaterials();
  }

  function getSelectedChatRoom() {
    return chatState.rooms.find((room) => room.key === chatState.selectedRoomKey) || null;
  }

  function persistSelectedChatRoom() {
    window.localStorage.setItem("irms_chat_room", chatState.selectedRoomKey);
  }

  function renderChatRoomTabs() {
    if (!roomTabs) {
      return;
    }

    if (!chatState.rooms.length) {
      roomTabs.innerHTML = '<div class="empty-state">No chat rooms available.</div>';
      return;
    }

    roomTabs.innerHTML = chatState.rooms
      .map((room) => {
        const isActive = room.key === chatState.selectedRoomKey;
        const countLabel =
          room.messageCount > 0
            ? `<span class="management-chat-tab-count">${IRMS.escapeHtml(IRMS.formatValue(room.messageCount))}</span>`
            : "";
        return `
          <button
            type="button"
            class="management-chat-tab${isActive ? " active" : ""}"
            data-room-key="${IRMS.escapeHtml(room.key)}"
          >
            <span>${IRMS.escapeHtml(room.name)}</span>
            ${countLabel}
          </button>
        `;
      })
      .join("");
  }

  function syncChatStageVisibility() {
    const room = getSelectedChatRoom();
    const stageRequired = Boolean(room?.stageRequired);

    if (chatStageGroup) {
      chatStageGroup.classList.toggle("hidden", !stageRequired);
    }

    if (roomMeta) {
      roomMeta.textContent = room ? room.name : "Room";
    }
  }

  function renderChatMessages(items, options = {}) {
    if (!chatMessages) {
      return;
    }

    const replace = Boolean(options.replace);
    if (!items.length && replace) {
      chatMessages.innerHTML = `
        <div class="management-chat-empty">
          <strong>No messages yet.</strong>
          <p class="muted">Post the first message in this room.</p>
        </div>
      `;
      return;
    }

    const markup = items
      .map((message) => {
        const isOwn =
          chatState.currentUsername && message.createdByUsername === chatState.currentUsername;
        const stageBadge = message.stage
          ? `<span class="management-chat-stage-badge stage-${IRMS.escapeHtml(message.stage)}">${IRMS.escapeHtml(stageLabels[message.stage] || message.stage)}</span>`
          : "";

        return `
          <article class="management-chat-message${isOwn ? " own" : ""}" data-message-id="${message.id}">
            <div class="management-chat-message-head">
              <strong class="management-chat-author">${IRMS.escapeHtml(message.createdByDisplayName || message.createdByUsername)}</strong>
              <div class="management-chat-meta">
                ${stageBadge}
                <time>${IRMS.escapeHtml(IRMS.formatDateTime(message.createdAt))}</time>
              </div>
            </div>
            <p class="management-chat-text">${IRMS.escapeHtml(message.messageText)}</p>
          </article>
        `;
      })
      .join("");

    if (replace) {
      chatMessages.innerHTML = markup;
    } else {
      const emptyState = chatMessages.querySelector(".management-chat-empty");
      if (emptyState) {
        emptyState.remove();
      }
      chatMessages.insertAdjacentHTML("beforeend", markup);
    }

    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  async function loadChatRooms() {
    const payload = await IRMS.listChatRooms();
    chatState.rooms = payload.items || [];

    if (!chatState.rooms.length) {
      renderChatRoomTabs();
      syncChatStageVisibility();
      return;
    }

    if (!chatState.rooms.some((room) => room.key === chatState.selectedRoomKey)) {
      chatState.selectedRoomKey = chatState.rooms[0].key;
      persistSelectedChatRoom();
    }

    renderChatRoomTabs();
    syncChatStageVisibility();
  }

  async function loadChatMessages(options = {}) {
    const room = getSelectedChatRoom();
    if (!room) {
      if (chatMessages && Boolean(options.replace)) {
        chatMessages.innerHTML = `
          <div class="management-chat-empty">
            <strong>No room selected.</strong>
            <p class="muted">Refresh the page to retry room loading.</p>
          </div>
        `;
      }
      return;
    }

    const replace = Boolean(options.replace);
    const afterId = replace ? 0 : Number(chatState.latestByRoom[room.key] || 0);
    const payload = await IRMS.getChatMessages({
      roomKey: room.key,
      limit: 80,
      afterId,
    });

    if (replace) {
      renderChatMessages(payload.items || [], { replace: true });
    } else if ((payload.items || []).length > 0) {
      renderChatMessages(payload.items || [], { replace: false });
    }

    chatState.latestByRoom[room.key] = Number(
      payload.latestId || chatState.latestByRoom[room.key] || 0
    );
    syncChatStageVisibility();
  }

  async function refreshChatPanel(options = {}) {
    const replace = Boolean(options.replace);

    try {
      await loadChatRooms();
      await loadChatMessages({ replace });
    } catch (error) {
      if (!options.silent) {
        IRMS.notify(`Chat sync failed: ${error.message}`, "error");
      }
    }
  }

  function startChatPolling() {
    if (chatState.timerId) {
      window.clearInterval(chatState.timerId);
    }

    chatState.timerId = window.setInterval(() => {
      if (document.visibilityState === "hidden") {
        return;
      }
      refreshChatPanel({ replace: false, silent: true });
    }, 10000);
  }

  function renderIssues(list, target, emptyText) {
    if (!list || !list.length) {
      target.innerHTML = `<li class="muted">${emptyText}</li>`;
      return;
    }
    target.innerHTML = list
      .slice(0, 12)
      .map(
        (item) =>
          `<li>L${item.level} 쨌 ${IRMS.escapeHtml(item.message)}${item.row ? ` (${item.row}??` : ""}</li>`,
      )
      .join("");
  }

  function renderPreview(result) {
    const rows = result?.rows || [];
    const materialIds = new Set();
    rows.forEach((row) =>
      (row.items || []).forEach((item) => {
        if (item.materialId !== undefined && item.materialId !== null) {
          materialIds.add(item.materialId);
        }
      }),
    );

    const columnMaterials = materials.filter((material) =>
      materialIds.has(material.id),
    );
    const badges = [
      `<span class="meta-badge meta-ok">Rows ${rows.length}</span>`,
      `<span class="meta-badge meta-warn">Warn ${(result?.warnings || []).length}</span>`,
      `<span class="meta-badge meta-error">Error ${(result?.errors || []).length}</span>`,
    ];

    if (previewIsStale) {
      badges.push('<span class="meta-badge meta-warn">Re-Preview Required</span>');
    }

    previewMeta.innerHTML = badges.join("");

    const heads = [
      "Product Name",
      "Position",
      "Ink Name",
      ...columnMaterials.map((material) => IRMS.escapeHtml(material.name)),
    ];
    previewHead.innerHTML = heads.map((head) => `<th>${head}</th>`).join("");

    if (!rows.length) {
      previewBody.innerHTML =
        '<tr><td colspan="20"><div class="empty-state">誘몃━蹂닿린 ?곗씠?곌? ?놁뒿?덈떎.</div></td></tr>';
      return;
    }

    previewBody.innerHTML = rows
      .map((row) => {
        const values = columnMaterials.map((material) => {
          const item = (row.items || []).find(
            (entry) => entry.materialId === material.id,
          );
          return `<td class="material-value">${item ? IRMS.formatValue(item.value) : "-"}</td>`;
        });
        return `
          <tr>
            <td>${IRMS.escapeHtml(row.productName)}</td>
            <td>${IRMS.escapeHtml(row.position)}</td>
            <td>${IRMS.escapeHtml(row.inkName)}</td>
            ${values.join("")}
          </tr>
        `;
      })
      .join("");
  }

  async function renderHistory() {
    persistHistoryFilters();
    updateHistorySummary();
    try {
      const rows = await IRMS.getRecipes({
        status: historyStatus.value || undefined,
        search: historySearch.value.trim() || undefined,
        dateFrom: historyFrom.value || undefined,
        dateTo: historyTo.value || undefined,
      });

      if (!rows.length) {
        historyBody.innerHTML =
          '<tr><td colspan="7"><div class="empty-state">議곌굔??留욌뒗 ?덉떆?쇨? ?놁뒿?덈떎.</div></td></tr>';
        return;
      }

      historyBody.innerHTML = rows
        .map(
          (recipe) => `
            <tr>
              <td>${recipe.id}</td>
              <td class="product-cell">${IRMS.escapeHtml(recipe.productName)}</td>
              <td>${IRMS.escapeHtml(recipe.inkName)}</td>
              <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
              <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
              <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
              <td>${(recipe.items || []).length}</td>
            </tr>
          `,
        )
        .join("");
    } catch (error) {
      IRMS.notify(`?대젰 議고쉶 ?ㅽ뙣: ${error.message}`, "error");
    }
  }

  async function handlePreview() {
    const raw = getSpreadsheetDataAsText();

    if (!raw) {
      IRMS.notify(
        "誘몃━蹂닿린 ?꾩뿉 ?쒗듃???곗씠?곕? ?낅젰?섍굅??遺숈뿬?ｌ쑝?몄슂.",
        "warn",
      );
      return;
    }

    try {
      const result = await IRMS.previewImport(raw);
      currentPreview = result;
      confirmedRawText = raw;
      previewIsStale = false;
      renderPreview(result);
      renderIssues(result.errors, errorList, "ERROR ?놁쓬");
      renderIssues(result.warnings, warningList, "WARN ?놁쓬");
      syncRegisterState();
    } catch (error) {
      IRMS.notify(`誘몃━蹂닿린 ?ㅽ뙣: ${error.message}`, "error");
    }
  }

  async function handleRegister() {
    if (
      !currentPreview ||
      previewIsStale ||
      currentPreview.errors.length > 0 ||
      currentPreview.rows.length === 0 ||
      !confirmedRawText.trim()
    ) {
      if (previewIsStale) {
        IRMS.notify("?뺤젙蹂몄씠 臾댄슚?붾릺?덉뒿?덈떎. ?ㅼ떆 Validate ???깅줉?섏꽭??", "warn");
      }
      return;
    }

    try {
      const result = await IRMS.importRecipes(confirmedRawText, "System Gate");
      IRMS.notify(
        `${result.created_count}嫄??덉떆?쇰? ?깅줉?덉뒿?덈떎.`,
        "success",
      );

      // Reset everything
      handleClear();
    } catch (error) {
      IRMS.notify(`?깅줉 ?ㅽ뙣: ${error.message}`, "error");
    }
  }

  function handleClear() {
    confirmedRawText = "";
    previewIsStale = false;
    if (sheet) {
      initSpreadsheet(); // Reinitialize to clear
    } else if (rawInput) {
      rawInput.value = "";
    }
    currentPreview = null;
    renderPreview({ rows: [], errors: [], warnings: [] });
    renderIssues([], errorList, "ERROR ?놁쓬");
    renderIssues([], warningList, "WARN ?놁쓬");
    syncRegisterState();
  }

  previewBtn.addEventListener("click", handlePreview);

  if (roomTabs) {
    roomTabs.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }

      const button = target.closest("[data-room-key]");
      if (!(button instanceof HTMLElement)) {
        return;
      }

      const nextRoomKey = button.dataset.roomKey;
      if (!nextRoomKey || nextRoomKey === chatState.selectedRoomKey) {
        return;
      }

      chatState.selectedRoomKey = nextRoomKey;
      persistSelectedChatRoom();
      renderChatRoomTabs();
      syncChatStageVisibility();

      try {
        await loadChatMessages({ replace: true });
      } catch (error) {
        IRMS.notify(`Chat load failed: ${error.message}`, "error");
      }
    });
  }

  if (chatForm) {
    chatForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (chatState.sending) {
        return;
      }

      const room = getSelectedChatRoom();
      if (!room) {
        return;
      }

      const messageText = chatInput?.value.trim() || "";
      const stage = room.stageRequired ? chatStage?.value || "registered" : null;

      if (!messageText) {
        IRMS.notify("Enter a message before sending.", "error");
        return;
      }

      chatState.sending = true;
      if (chatSend) {
        chatSend.disabled = true;
      }

      try {
        const payload = await IRMS.postChatMessage({
          roomKey: room.key,
          messageText,
          stage,
        });

        if (payload.message) {
          renderChatMessages([payload.message], { replace: false });
          chatState.latestByRoom[room.key] = Number(
            payload.message.id || chatState.latestByRoom[room.key] || 0
          );

          const roomIndex = chatState.rooms.findIndex((entry) => entry.key === room.key);
          if (roomIndex >= 0) {
            chatState.rooms[roomIndex].messageCount =
              Number(chatState.rooms[roomIndex].messageCount || 0) + 1;
            chatState.rooms[roomIndex].latestMessageAt = payload.message.createdAt;
            renderChatRoomTabs();
          }
        }

        if (chatInput) {
          chatInput.value = "";
          chatInput.focus();
        }

        IRMS.notify("Message posted.", "success");
      } catch (error) {
        IRMS.notify(`Message post failed: ${error.message}`, "error");
      } finally {
        chatState.sending = false;
        if (chatSend) {
          chatSend.disabled = false;
        }
      }
    });
  }
  registerBtn.addEventListener("click", handleRegister);
  clearBtn.addEventListener("click", handleClear);
  historyStatus.addEventListener("change", () => {
    persistHistoryFilters();
    updateHistorySummary();
    renderHistory();
  });
  historySearch.addEventListener(
    "input",
    IRMS.debounce(() => {
      persistHistoryFilters();
      updateHistorySummary();
      renderHistory();
    }, 300),
  );
  historyFrom.addEventListener("change", () => {
    persistHistoryFilters();
    updateHistorySummary();
    renderHistory();
  });
  historyTo.addEventListener("change", () => {
    persistHistoryFilters();
    updateHistorySummary();
    renderHistory();
  });
  if (historyResetBtn) {
    historyResetBtn.addEventListener("click", resetHistoryFilters);
  }
  if (rawInput) {
    rawInput.addEventListener("input", markPreviewStale);
  }


  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshChatPanel({ replace: false, silent: true });
    }
  });
  (async () => {
    try {
      restoreHistoryFilters();
      updateHistorySummary();
      initSpreadsheet();
      await loadMaterials();
      renderPreview({ rows: [], errors: [], warnings: [] });
      renderIssues([], errorList, "ERROR ?놁쓬");
      renderIssues([], warningList, "WARN ?놁쓬");
      syncRegisterState();
      await Promise.all([
        renderHistory(),
        refreshChatPanel({ replace: true, silent: true }),
      ]);
      startChatPolling();
    } catch (error) {
      IRMS.notify(`珥덇린???ㅽ뙣: ${error.message}`, "error");
    }
  })();
});

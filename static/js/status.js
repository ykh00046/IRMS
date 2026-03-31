document.addEventListener("DOMContentLoaded", async () => {
  const shell = document.querySelector(".site-shell");
  const filterSelect = document.getElementById("status-filter");
  const refreshButton = document.getElementById("status-refresh");
  const resetButton = document.getElementById("status-reset");
  const filterSummary = document.getElementById("status-filter-summary");
  const lastUpdatedNode = document.getElementById("status-last-updated");
  const board = document.getElementById("status-board");
  const importFeed = document.getElementById("status-import-feed");
  const roomTabs = document.getElementById("status-chat-room-tabs");
  const roomMeta = document.getElementById("status-chat-room-meta");
  const chatMessages = document.getElementById("status-chat-messages");
  const chatForm = document.getElementById("status-chat-form");
  const chatStageGroup = document.getElementById("status-chat-stage-group");
  const chatStage = document.getElementById("status-chat-stage");
  const chatInput = document.getElementById("status-chat-input");
  const chatSend = document.getElementById("status-chat-send");

  const summaryNodes = {
    activeRecipes: document.getElementById("status-active-recipes"),
    inProgress: document.getElementById("status-in-progress"),
    pending: document.getElementById("status-pending"),
    remainingSteps: document.getElementById("status-remaining-steps"),
    openPositions: document.getElementById("status-open-positions"),
  };

  const stageLabels = {
    registered: "등록",
    in_progress: "진행중",
    completed: "완료",
  };

  const state = {
    currentUsername: shell?.dataset.currentUsername || "",
    loading: false,
    sending: false,
    timerId: null,
    importBaselineId: Number(window.localStorage.getItem("irms_last_recipe_import_id") || 0),
    selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
    rooms: [],
    chatLatestIdByRoom: {},
  };

  const statusFilterKey = "irms_status_filter";

  function setLastUpdated() {
    if (!lastUpdatedNode) {
      return;
    }
    lastUpdatedNode.textContent = new Date().toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function updateSummary(summary) {
    summaryNodes.activeRecipes.textContent = String(summary.active_recipes || 0);
    summaryNodes.inProgress.textContent = String(summary.in_progress_recipes || 0);
    summaryNodes.pending.textContent = String(summary.pending_recipes || 0);
    summaryNodes.remainingSteps.textContent = String(summary.remaining_steps || 0);
    summaryNodes.openPositions.textContent = String(summary.open_positions || 0);
  }

  function updateFilterSummary() {
    if (!filterSummary || !filterSelect) {
      return;
    }

    const labels = {
      active: "활성 전체",
      in_progress: "진행중만",
      pending: "대기만",
      completed: "완료만",
      canceled: "취소만",
      all: "전체",
    };

    filterSummary.textContent = `${labels[filterSelect.value] || filterSelect.value} 기준으로 현장 보드를 표시 중입니다.`;
  }

  function normalizePosition(position) {
    return (position || "UNASSIGNED").trim() || "UNASSIGNED";
  }

  function comparePosition(left, right) {
    return left.localeCompare(right, "ko-KR", { numeric: true, sensitivity: "base" });
  }

  function groupByPosition(items) {
    const laneMap = new Map();

    items.forEach((recipe) => {
      const position = normalizePosition(recipe.position);
      if (!laneMap.has(position)) {
        laneMap.set(position, []);
      }
      laneMap.get(position).push(recipe);
    });

    return Array.from(laneMap.entries())
      .sort((a, b) => comparePosition(a[0], b[0]))
      .map(([position, recipes]) => ({
        position,
        recipes: recipes.sort((a, b) => {
          const leftPriority =
            a.status === "in_progress" ? 0 : a.status === "pending" ? 1 : a.status === "completed" ? 2 : 3;
          const rightPriority =
            b.status === "in_progress" ? 0 : b.status === "pending" ? 1 : b.status === "completed" ? 2 : 3;
          if (leftPriority !== rightPriority) {
            return leftPriority - rightPriority;
          }
          return String(b.createdAt || "").localeCompare(String(a.createdAt || ""));
        }),
      }));
  }

  function formatActorNames(items) {
    const actors = Array.from(
      new Set(
        items
          .map((item) => item.actorDisplayName || item.actorUsername || "")
          .filter(Boolean)
      )
    );
    return actors.slice(0, 2).join(", ");
  }

  function getSelectedRoom() {
    return state.rooms.find((room) => room.key === state.selectedRoomKey) || null;
  }

  function renderImportFeed(items) {
    if (!importFeed) {
      return;
    }

    if (!items.length) {
      importFeed.innerHTML = `
        <div class="status-empty compact">
          <div>
            <strong>최근 업로드 이력이 없습니다.</strong>
            <p class="muted">새 레시피가 등록되면 여기에 표시됩니다.</p>
          </div>
        </div>
      `;
      return;
    }

    importFeed.innerHTML = items
      .map((item) => {
        const createdCount = Number(item.details?.created_count || 0);
        const createdIds = Array.isArray(item.details?.created_ids) ? item.details.created_ids : [];
        const actor = item.actorDisplayName || item.actorUsername || "system";
        return `
          <article class="status-import-item">
            <div class="status-import-copy">
              <span class="status-import-title">+${IRMS.escapeHtml(IRMS.formatValue(createdCount || createdIds.length || 1))} recipe</span>
              <span class="status-import-meta">${IRMS.escapeHtml(actor)} · ${IRMS.escapeHtml(IRMS.formatDateTime(item.createdAt))}</span>
            </div>
            <div class="status-import-badges">
              ${createdIds
                .slice(0, 4)
                .map((recipeId) => `<span class="status-import-chip">#${IRMS.escapeHtml(recipeId)}</span>`)
                .join("")}
            </div>
          </article>
        `;
      })
      .join("");
  }

  function renderRemainingMaterials(materials) {
    if (!materials.length) {
      return '<span class="status-last-empty">No remaining materials.</span>';
    }

    return `
      <div class="status-list">
        ${materials
          .map(
            (material) =>
              `<span class="status-material-chip">${IRMS.escapeHtml(material)}</span>`
          )
          .join("")}
      </div>
    `;
  }

  function renderNextItem(recipe) {
    if (!recipe.nextItem) {
      return `
        <div class="status-next-target">
          <strong class="status-next-material">All steps completed</strong>
          <span class="status-next-meta">Recipe can be completed now.</span>
        </div>
      `;
    }

    const unitText = recipe.nextItem.unit ? ` ${IRMS.escapeHtml(recipe.nextItem.unit)}` : "";
    return `
      <div class="status-next-target">
        <strong class="status-next-material">${IRMS.escapeHtml(recipe.nextItem.materialName)}</strong>
        <span class="status-next-meta">Target ${IRMS.escapeHtml(IRMS.formatValue(recipe.nextItem.targetValue))}${unitText}</span>
        <span class="status-next-meta">Color ${IRMS.escapeHtml(recipe.nextItem.colorGroup || "none")}</span>
      </div>
    `;
  }

  function renderLastCompleted(recipe) {
    if (!recipe.lastCompletedItem) {
      return '<span class="status-last-empty">No weighing step completed yet.</span>';
    }

    return `
      <div class="status-next-target">
        <strong class="status-next-material">${IRMS.escapeHtml(recipe.lastCompletedItem.materialName)}</strong>
        <span class="status-last-meta">${IRMS.escapeHtml(IRMS.formatDateTime(recipe.lastCompletedItem.measuredAt))}</span>
        <span class="status-operator-line">${IRMS.escapeHtml(recipe.lastCompletedItem.measuredBy || "-")}</span>
      </div>
    `;
  }

  function renderRecipeCard(recipe) {
    const contextChips = [];
    if (recipe.startedBy) {
      contextChips.push(`Started by ${recipe.startedBy}`);
    }
    if (recipe.startedAt) {
      contextChips.push(`Started ${IRMS.formatDateTime(recipe.startedAt)}`);
    }
    if (recipe.completedAt) {
      contextChips.push(`Completed ${IRMS.formatDateTime(recipe.completedAt)}`);
    }

    return `
      <article class="status-card">
        <div class="status-card-head">
          <div class="status-card-copy">
            <h3 class="status-card-title">${IRMS.escapeHtml(recipe.productName)}</h3>
            <p class="status-card-subtitle">${IRMS.escapeHtml(recipe.inkName)}</p>
          </div>
          <span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.escapeHtml(IRMS.statusLabel(recipe.status))}</span>
        </div>

        <div class="status-progress-block">
          <div class="status-progress-meta">
            <span>${IRMS.escapeHtml(IRMS.formatValue(recipe.completedSteps))} / ${IRMS.escapeHtml(IRMS.formatValue(recipe.totalSteps))} steps</span>
            <strong>${IRMS.escapeHtml(IRMS.formatValue(recipe.progressPct))}%</strong>
          </div>
          <div class="status-progress-track" aria-hidden="true">
            <div class="status-progress-fill" style="width: ${Math.max(0, Math.min(100, recipe.progressPct))}%"></div>
          </div>
        </div>

        <div class="status-metrics">
          <div class="status-metric">
            <span class="status-metric-label">Remaining</span>
            <strong class="status-metric-value">${IRMS.escapeHtml(IRMS.formatValue(recipe.remainingSteps))}</strong>
          </div>
          <div class="status-metric">
            <span class="status-metric-label">Created By</span>
            <strong class="status-metric-value">${IRMS.escapeHtml(recipe.createdBy || "-")}</strong>
          </div>
          <div class="status-metric">
            <span class="status-metric-label">Created At</span>
            <strong class="status-metric-value">${IRMS.escapeHtml(IRMS.formatDateTime(recipe.createdAt))}</strong>
          </div>
        </div>

        <div class="status-detail-grid">
          <section class="status-detail-card">
            <span class="status-detail-label">Next Weighing Step</span>
            ${renderNextItem(recipe)}
          </section>
          <section class="status-detail-card">
            <span class="status-detail-label">Last Completed Step</span>
            ${renderLastCompleted(recipe)}
          </section>
        </div>

        <section class="status-detail-card">
          <span class="status-detail-label">Remaining Materials</span>
          ${renderRemainingMaterials(recipe.remainingMaterials || [])}
        </section>

        ${
          contextChips.length
            ? `
              <div class="status-chip-stack">
                ${contextChips
                  .map(
                    (label) => `<span class="status-context-chip">${IRMS.escapeHtml(label)}</span>`
                  )
                  .join("")}
              </div>
            `
            : ""
        }
      </article>
    `;
  }

  function renderBoard(items) {
    if (!board) {
      return;
    }

    if (!items.length) {
      board.innerHTML = `
        <div class="status-empty">
          <div>
            <strong>No recipes match the current filter.</strong>
            <p class="muted">Change the filter or wait for new recipe imports.</p>
          </div>
        </div>
      `;
      return;
    }

    const lanes = groupByPosition(items);
    board.innerHTML = lanes
      .map((lane) => {
        const activeCount = lane.recipes.filter(
          (recipe) => recipe.status === "pending" || recipe.status === "in_progress"
        ).length;
        const remainingSteps = lane.recipes.reduce(
          (sum, recipe) => sum + Number(recipe.remainingSteps || 0),
          0
        );

        return `
          <section class="status-lane">
            <header class="status-lane-head">
              <div>
                <span class="status-location">${IRMS.escapeHtml(lane.position)}</span>
                <h3 class="status-lane-title">${IRMS.escapeHtml(lane.position)} line</h3>
              </div>
              <div class="status-lane-metrics">
                <span>${IRMS.escapeHtml(IRMS.formatValue(lane.recipes.length))} recipes</span>
                <span>${IRMS.escapeHtml(IRMS.formatValue(activeCount))} active</span>
                <span>${IRMS.escapeHtml(IRMS.formatValue(remainingSteps))} steps left</span>
              </div>
            </header>
            <div class="status-lane-body">
              ${lane.recipes.map(renderRecipeCard).join("")}
            </div>
          </section>
        `;
      })
      .join("");
  }

  function renderRoomTabs() {
    if (!roomTabs) {
      return;
    }

    roomTabs.innerHTML = state.rooms
      .map((room) => {
        const isActive = room.key === state.selectedRoomKey;
        const countLabel =
          room.messageCount > 0 ? `<span class="status-chat-tab-count">${IRMS.escapeHtml(IRMS.formatValue(room.messageCount))}</span>` : "";
        return `
          <button
            type="button"
            class="status-chat-tab${isActive ? " active" : ""}"
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
    const room = getSelectedRoom();
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
        <div class="status-chat-empty">
          <strong>메시지가 없습니다.</strong>
          <p class="muted">현재 room의 첫 메시지를 남겨보세요.</p>
        </div>
      `;
      return;
    }

    const markup = items
      .map((message) => {
        const isOwn = state.currentUsername && message.createdByUsername === state.currentUsername;
        const stageBadge = message.stage
          ? `<span class="status-stage-badge stage-${IRMS.escapeHtml(message.stage)}">${IRMS.escapeHtml(stageLabels[message.stage] || message.stage)}</span>`
          : "";
        return `
          <article class="status-chat-message${isOwn ? " own" : ""}" data-message-id="${message.id}">
            <div class="status-chat-message-head">
              <strong class="status-chat-author">${IRMS.escapeHtml(message.createdByDisplayName || message.createdByUsername)}</strong>
              <div class="status-chat-meta">
                ${stageBadge}
                <time>${IRMS.escapeHtml(IRMS.formatDateTime(message.createdAt))}</time>
              </div>
            </div>
            <p class="status-chat-text">${IRMS.escapeHtml(message.messageText)}</p>
          </article>
        `;
      })
      .join("");

    if (replace) {
      chatMessages.innerHTML = markup;
    } else {
      const emptyState = chatMessages.querySelector(".status-chat-empty");
      if (emptyState) {
        emptyState.remove();
      }
      chatMessages.insertAdjacentHTML("beforeend", markup);
    }

    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  async function loadChatRooms() {
    const payload = await IRMS.listChatRooms();
    state.rooms = payload.items || [];

    if (!state.rooms.some((room) => room.key === state.selectedRoomKey) && state.rooms.length > 0) {
      state.selectedRoomKey = state.rooms[0].key;
    }

    renderRoomTabs();
    syncChatStageVisibility();
  }

  async function loadChatMessages(options = {}) {
    const room = getSelectedRoom();
    if (!room) {
      return;
    }

    const replace = Boolean(options.replace);
    const afterId = replace ? 0 : Number(state.chatLatestIdByRoom[room.key] || 0);
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

    state.chatLatestIdByRoom[room.key] = Number(payload.latestId || state.chatLatestIdByRoom[room.key] || 0);
    syncChatStageVisibility();
  }

  async function loadStatusBoard() {
    if (state.loading) {
      return;
    }

    state.loading = true;
    if (filterSelect) {
      IRMS.savePreference(statusFilterKey, filterSelect.value);
      updateFilterSummary();
    }
    if (refreshButton) {
      refreshButton.disabled = true;
    }

    try {
      const [progressPayload, importPayload] = await Promise.all([
        IRMS.getRecipeProgress({
          statusFilter: filterSelect?.value || "active",
        }),
        IRMS.getRecipeImportNotifications({
          latest: true,
          limit: 6,
        }),
      ]);

      updateSummary(progressPayload.summary || {});
      renderBoard(progressPayload.items || []);
      renderImportFeed(importPayload.items || []);

      const latestImportId = Number(importPayload.latestId || 0);
      if (!state.importBaselineId && latestImportId > 0) {
        state.importBaselineId = latestImportId;
        window.localStorage.setItem("irms_last_recipe_import_id", String(state.importBaselineId));
      }

      setLastUpdated();
    } catch (error) {
      IRMS.notify(`Status load failed: ${error.message}`, "error");
    } finally {
      state.loading = false;
      if (refreshButton) {
        refreshButton.disabled = false;
      }
    }
  }

  async function checkImportAlerts() {
    try {
      const payload = await IRMS.getRecipeImportNotifications({
        afterId: state.importBaselineId,
        limit: 20,
      });
      const items = payload.items || [];
      if (!items.length) {
        return;
      }

      state.importBaselineId = Number(payload.latestId || state.importBaselineId);
      window.localStorage.setItem("irms_last_recipe_import_id", String(state.importBaselineId));

      const importedRecipeCount = items.reduce((sum, item) => {
        const createdCount = Number(item.details?.created_count || 0);
        return sum + (Number.isFinite(createdCount) ? createdCount : 0);
      }, 0);
      const visibleCount = importedRecipeCount > 0 ? importedRecipeCount : items.length;
      const actorNames = formatActorNames(items);
      const suffix = actorNames ? ` (${actorNames})` : "";

      IRMS.notify(`New recipes imported: ${visibleCount}${suffix}`, "info");
    } catch (_error) {
      // Keep the dashboard stable even if background polling fails once.
    }
  }

  function persistSelectedRoom() {
    window.localStorage.setItem("irms_chat_room", state.selectedRoomKey);
  }

  function bindChatTabEvents() {
    if (!roomTabs) {
      return;
    }

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
      if (!nextRoomKey || nextRoomKey === state.selectedRoomKey) {
        return;
      }

      state.selectedRoomKey = nextRoomKey;
      persistSelectedRoom();
      renderRoomTabs();
      syncChatStageVisibility();
      await loadChatMessages({ replace: true });
    });
  }

  async function handleChatSubmit(event) {
    event.preventDefault();
    if (state.sending) {
      return;
    }

    const room = getSelectedRoom();
    if (!room) {
      return;
    }

    const messageText = chatInput?.value.trim() || "";
    const stage = room.stageRequired ? chatStage?.value || "registered" : null;

    if (!messageText) {
      IRMS.notify("메시지를 입력하세요.", "error");
      return;
    }

    state.sending = true;
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
        state.chatLatestIdByRoom[room.key] = Number(payload.message.id || state.chatLatestIdByRoom[room.key] || 0);
        const roomIndex = state.rooms.findIndex((entry) => entry.key === room.key);
        if (roomIndex >= 0) {
          state.rooms[roomIndex].messageCount = Number(state.rooms[roomIndex].messageCount || 0) + 1;
          state.rooms[roomIndex].latestMessageAt = payload.message.createdAt;
          renderRoomTabs();
        }
      }
      if (chatInput) {
        chatInput.value = "";
        chatInput.focus();
      }
      IRMS.notify("메시지를 등록했습니다.", "success");
    } catch (error) {
      IRMS.notify(`메시지 등록 실패: ${error.message}`, "error");
    } finally {
      state.sending = false;
      if (chatSend) {
        chatSend.disabled = false;
      }
    }
  }

  async function refreshWorkspace() {
    await checkImportAlerts();
    await Promise.all([
      loadStatusBoard(),
      loadChatRooms(),
      loadChatMessages({ replace: false }),
    ]);
  }

  function startAutoRefresh() {
    if (state.timerId) {
      window.clearInterval(state.timerId);
    }
    state.timerId = window.setInterval(async () => {
      if (document.visibilityState === "hidden") {
        return;
      }
      await refreshWorkspace();
    }, 10000);
  }

  if (filterSelect) {
    filterSelect.value = IRMS.loadPreference(statusFilterKey, "active") || "active";
    updateFilterSummary();
    filterSelect.addEventListener("change", loadStatusBoard);
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      await Promise.all([
        loadStatusBoard(),
        loadChatRooms(),
        loadChatMessages({ replace: true }),
      ]);
    });
  }

  if (resetButton) {
    resetButton.addEventListener("click", async () => {
      if (filterSelect) {
        filterSelect.value = "active";
      }
      IRMS.clearPreference(statusFilterKey);
      updateFilterSummary();
      await loadStatusBoard();
    });
  }

  if (chatForm) {
    chatForm.addEventListener("submit", handleChatSubmit);
  }

  bindChatTabEvents();

  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible") {
      await refreshWorkspace();
    }
  });

  await loadChatRooms();
  updateFilterSummary();
  await Promise.all([
    loadStatusBoard(),
    loadChatMessages({ replace: true }),
  ]);
  startAutoRefresh();
});

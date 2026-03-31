document.addEventListener("DOMContentLoaded", () => {
  const shell = document.querySelector(".site-shell");
  const tableHead = document.getElementById("work-head");
  const tableBody = document.getElementById("work-body");
  const searchInput = document.getElementById("search-input");
  const fromInput = document.getElementById("from-input");
  const toInput = document.getElementById("to-input");
  const filterSummary = document.getElementById("work-filter-summary");
  const filterResetBtn = document.getElementById("work-filter-reset");
  const statsCount = document.getElementById("work-count");
  const statsStatus = document.getElementById("work-status");
  const focusButtons = Array.from(document.querySelectorAll("[data-color]"));
  const logWrap = document.getElementById("work-log");
  const roomMeta = document.getElementById("work-chat-room-meta");
  const roomTabs = document.getElementById("work-chat-room-tabs");
  const chatMessages = document.getElementById("work-chat-messages");
  const chatForm = document.getElementById("work-chat-form");
  const chatStageGroup = document.getElementById("work-chat-stage-group");
  const chatStage = document.getElementById("work-chat-stage");
  const chatInput = document.getElementById("work-chat-input");
  const chatSend = document.getElementById("work-chat-send");

  const weighingOpenBtn = document.getElementById("weighing-open-btn");
  const weighingRefreshMainBtn = document.getElementById("weighing-refresh-main-btn");
  const weighingMode = document.getElementById("weighing-mode");
  const weighingCloseBtn = document.getElementById("weighing-close-btn");
  const weighingRefreshBtn = document.getElementById("weighing-refresh-btn");
  const weighingAdvanceBtn = document.getElementById("weighing-advance-btn");
  const weighingProgressFill = document.getElementById("weighing-progress-fill");
  const weighingProgressText = document.getElementById("weighing-progress-text");
  const weighingSummary = document.getElementById("weighing-summary");
  const weighingStateBadge = document.getElementById("weighing-state-badge");
  const weighingMaterialName = document.getElementById("weighing-material-name");
  const weighingTargetValue = document.getElementById("weighing-target-value");
  const weighingRecipeMeta = document.getElementById("weighing-recipe-meta");
  const weighingActionHint = document.getElementById("weighing-action-hint");
  const weighingNextValue = document.getElementById("weighing-next-value");
  const weighingColorGroup = document.getElementById("weighing-color-group");
  const weighingOperator = document.getElementById("weighing-operator");

  const state = {
    color: "all",
    loadingToken: 0,
    currentUsername: shell?.dataset.currentUsername || "",
    selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
    rooms: [],
    chatLatestIdByRoom: {},
    chatSending: false,
    chatTimerId: null,
  };

  const recipeImportNotice = {
    initialized: false,
    checking: false,
    lastSeenId: Number(window.localStorage.getItem("irms_last_recipe_import_id") || 0),
    timerId: null,
  };

  const preferenceKeys = {
    color: "irms_work_filter_color",
    search: "irms_work_filter_search",
    from: "irms_work_filter_from",
    to: "irms_work_filter_to",
    weighingColor: "irms_work_weighing_color",
  };

  const weighing = {
    open: false,
    loading: false,
    advancing: false,
    queue: [],
    doneCount: 0,
    initialTotal: 0,
    colorGroup: "all",
    pendingRecipeCompletion: null,
  };

  const stageLabels = {
    registered: "Registered",
    in_progress: "In Progress",
    completed: "Completed",
  };

  function colorLabel(color) {
    const map = {
      black: "BLACK",
      red: "RED",
      blue: "BLUE",
      yellow: "YELLOW",
      none: "기타",
      all: "전체",
    };
    return map[color] || "기타";
  }

  function persistFilters() {
    IRMS.savePreference(preferenceKeys.color, state.color);
    IRMS.savePreference(preferenceKeys.search, searchInput.value.trim());
    IRMS.savePreference(preferenceKeys.from, fromInput.value);
    IRMS.savePreference(preferenceKeys.to, toInput.value);
  }

  function updateFilterSummary() {
    if (!filterSummary) {
      return;
    }

    const parts = [`색상 ${colorLabel(state.color)}`];
    const search = searchInput.value.trim();
    const from = fromInput.value;
    const to = toInput.value;

    if (search) {
      parts.push(`검색어 "${search}"`);
    }
    if (from || to) {
      parts.push(`기간 ${from || "시작 미지정"} ~ ${to || "종료 미지정"}`);
    }

    filterSummary.textContent = `${parts.join(" · ")} 기준으로 작업 큐를 표시 중입니다.`;
  }

  function restoreFilters() {
    state.color = IRMS.loadPreference(preferenceKeys.color, "all") || "all";
    searchInput.value = IRMS.loadPreference(preferenceKeys.search, "");
    fromInput.value = IRMS.loadPreference(preferenceKeys.from, "");
    toInput.value = IRMS.loadPreference(preferenceKeys.to, "");
  }

  function resetFilters() {
    state.color = "all";
    searchInput.value = "";
    fromInput.value = "";
    toInput.value = "";
    IRMS.clearPreference(preferenceKeys.color);
    IRMS.clearPreference(preferenceKeys.search);
    IRMS.clearPreference(preferenceKeys.from);
    IRMS.clearPreference(preferenceKeys.to);
    activateColorButton(state.color);
    updateFilterSummary();
    render();
  }

  function getVisibleMaterials(recipes, materialMap) {
    const uniqueIds = new Set();
    recipes.forEach((recipe) => {
      (recipe.items || []).forEach((item) => {
        const material = materialMap.get(item.materialId);
        if (!material) {
          return;
        }
        if (state.color !== "all" && material.colorGroup !== state.color) {
          return;
        }
        uniqueIds.add(material.id);
      });
    });
    return Array.from(uniqueIds)
      .map((id) => materialMap.get(id))
      .filter(Boolean);
  }

  function buildHeader(materials) {
    const baseColumns = [
      '<th class="sticky-left">제품명</th>',
      "<th>위치</th>",
      "<th>잉크명</th>",
      "<th>상태</th>",
      "<th>등록시각</th>",
    ];
    const materialColumns = materials.map(
      (material) => `<th>${IRMS.escapeHtml(material.name)}<br /><span class="muted">${IRMS.escapeHtml(material.unit)}</span></th>`
    );
    const actionColumn = ['<th class="sticky-right">완료</th>'];
    tableHead.innerHTML = [...baseColumns, ...materialColumns, ...actionColumn].join("");
  }

  function getMaterialValue(recipe, materialId) {
    const item = (recipe.items || []).find((entry) => entry.materialId === materialId);
    if (!item) {
      return "-";
    }
    return IRMS.formatValue(item.value);
  }

  function buildRows(recipes, materials) {
    if (!recipes.length) {
      tableBody.innerHTML =
        '<tr><td colspan="24"><div class="empty-state">현재 조건에서 처리할 레시피가 없습니다.</div></td></tr>';
      return;
    }

    tableBody.innerHTML = recipes
      .map((recipe) => {
        const status = `<span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span>`;
        const values = materials
          .map(
            (material) =>
              `<td class="material-value">${getMaterialValue(recipe, material.id)}</td>`
          )
          .join("");

        return `
          <tr class="recipe-row" data-id="${recipe.id}">
            <td class="sticky-left product-cell">${IRMS.escapeHtml(recipe.productName)}</td>
            <td>${IRMS.escapeHtml(recipe.position || "-")}</td>
            <td>${IRMS.escapeHtml(recipe.inkName)}</td>
            <td>${status}</td>
            <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
            ${values}
            <td class="sticky-right">
              <button type="button" class="btn success complete-btn" data-id="${recipe.id}">
                완료
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function getSelectedChatRoom() {
    return state.rooms.find((room) => room.key === state.selectedRoomKey) || null;
  }

  function persistSelectedChatRoom() {
    window.localStorage.setItem("irms_chat_room", state.selectedRoomKey);
  }

  function renderChatRoomTabs() {
    if (!roomTabs) {
      return;
    }

    if (!state.rooms.length) {
      roomTabs.innerHTML = '<div class="empty-state">No chat rooms available.</div>';
      return;
    }

    roomTabs.innerHTML = state.rooms
      .map((room) => {
        const isActive = room.key === state.selectedRoomKey;
        const countLabel =
          room.messageCount > 0
            ? `<span class="work-chat-tab-count">${IRMS.escapeHtml(IRMS.formatValue(room.messageCount))}</span>`
            : "";
        return `
          <button
            type="button"
            class="work-chat-tab${isActive ? " active" : ""}"
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
        <div class="work-chat-empty">
          <strong>No messages yet.</strong>
          <p class="muted">Post the first message in this room.</p>
        </div>
      `;
      return;
    }

    const markup = items
      .map((message) => {
        const isOwn = state.currentUsername && message.createdByUsername === state.currentUsername;
        const stageBadge = message.stage
          ? `<span class="work-chat-stage-badge stage-${IRMS.escapeHtml(message.stage)}">${IRMS.escapeHtml(stageLabels[message.stage] || message.stage)}</span>`
          : "";

        return `
          <article class="work-chat-message${isOwn ? " own" : ""}" data-message-id="${message.id}">
            <div class="work-chat-message-head">
              <strong class="work-chat-author">${IRMS.escapeHtml(message.createdByDisplayName || message.createdByUsername)}</strong>
              <div class="work-chat-meta">
                ${stageBadge}
                <time>${IRMS.escapeHtml(IRMS.formatDateTime(message.createdAt))}</time>
              </div>
            </div>
            <p class="work-chat-text">${IRMS.escapeHtml(message.messageText)}</p>
          </article>
        `;
      })
      .join("");

    if (replace) {
      chatMessages.innerHTML = markup;
    } else {
      const emptyState = chatMessages.querySelector(".work-chat-empty");
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

    if (!state.rooms.length) {
      renderChatRoomTabs();
      syncChatStageVisibility();
      return;
    }

    if (!state.rooms.some((room) => room.key === state.selectedRoomKey)) {
      state.selectedRoomKey = state.rooms[0].key;
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
          <div class="work-chat-empty">
            <strong>No room selected.</strong>
            <p class="muted">Refresh the page to retry room loading.</p>
          </div>
        `;
      }
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

    state.chatLatestIdByRoom[room.key] = Number(
      payload.latestId || state.chatLatestIdByRoom[room.key] || 0
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
    if (state.chatTimerId) {
      window.clearInterval(state.chatTimerId);
    }

    state.chatTimerId = window.setInterval(() => {
      if (document.visibilityState === "hidden") {
        return;
      }
      refreshChatPanel({ replace: false, silent: true });
    }, 10000);
  }

  function renderStats(recipes) {
    const pendingCount = recipes.filter((recipe) => recipe.status === "pending").length;
    const inProgressCount = recipes.filter((recipe) => recipe.status === "in_progress").length;
    statsCount.textContent = String(recipes.length);
    statsStatus.textContent = `대기 ${pendingCount} / 진행 ${inProgressCount}`;
  }

  function renderLog(completedRecipes) {
    const logs = completedRecipes
      .filter((recipe) => recipe.completedAt)
      .sort((a, b) => String(b.completedAt).localeCompare(String(a.completedAt)))
      .slice(0, 8);

    if (!logs.length) {
      logWrap.innerHTML = '<div class="empty-state">완료 로그가 아직 없습니다.</div>';
      return;
    }

    logWrap.innerHTML = logs
      .map(
        (recipe) => `
          <article class="work-log-item">
            <strong>${IRMS.escapeHtml(recipe.productName)} · ${IRMS.escapeHtml(recipe.inkName)}</strong>
            <span class="muted">작업자 ${IRMS.escapeHtml(recipe.createdBy)}</span>
            <time>${IRMS.formatDateTime(recipe.completedAt)}</time>
          </article>
        `
      )
      .join("");
  }

  function storeLastSeenRecipeImportId(nextId) {
    const numericId = Number(nextId || 0);
    if (!Number.isFinite(numericId) || numericId <= 0) {
      return;
    }
    recipeImportNotice.lastSeenId = numericId;
    window.localStorage.setItem("irms_last_recipe_import_id", String(numericId));
  }

  async function render() {
    const token = ++state.loadingToken;
    const search = searchInput.value.trim();
    const from = fromInput.value;
    const to = toInput.value;
    persistFilters();
    updateFilterSummary();

    try {
      const [materials, activeRecipes, completedRecipes] = await Promise.all([
        IRMS.getMaterials(),
        IRMS.getRecipes({ search, dateFrom: from, dateTo: to }),
        IRMS.getRecipes({ status: "completed" }),
      ]);

      if (token !== state.loadingToken) {
        return;
      }

      const working = activeRecipes.filter(
        (recipe) => recipe.status === "pending" || recipe.status === "in_progress"
      );
      const materialMap = new Map(materials.map((material) => [material.id, material]));
      const visibleMaterials = getVisibleMaterials(working, materialMap);

      buildHeader(visibleMaterials);
      buildRows(working, visibleMaterials);
      renderStats(working);
      renderLog(completedRecipes);
    } catch (error) {
      IRMS.notify(`데이터 로드 실패: ${error.message}`, "error");
      tableBody.innerHTML =
        '<tr><td colspan="24"><div class="empty-state">데이터를 불러오지 못했습니다.</div></td></tr>';
    }
  }

  async function checkRecipeImportNotifications(options = {}) {
    if (recipeImportNotice.checking) {
      return;
    }

    const silent = Boolean(options.silent);
    recipeImportNotice.checking = true;

    try {
      const payload = await IRMS.getRecipeImportNotifications({
        afterId: recipeImportNotice.lastSeenId,
        limit: 20,
      });
      const items = payload.items || [];

      if (!items.length) {
        if (!recipeImportNotice.initialized && payload.latestId > recipeImportNotice.lastSeenId) {
          storeLastSeenRecipeImportId(payload.latestId);
        }
        recipeImportNotice.initialized = true;
        return;
      }

      const latestId = Number(
        items[items.length - 1]?.id || payload.latestId || recipeImportNotice.lastSeenId
      );

      if (!recipeImportNotice.initialized && recipeImportNotice.lastSeenId === 0) {
        storeLastSeenRecipeImportId(latestId);
        recipeImportNotice.initialized = true;
        return;
      }

      storeLastSeenRecipeImportId(latestId);
      recipeImportNotice.initialized = true;

      const importedRecipeCount = items.reduce((sum, item) => {
        const createdCount = Number(item.details?.created_count || 0);
        return sum + (Number.isFinite(createdCount) ? createdCount : 0);
      }, 0);

      const actorNames = Array.from(
        new Set(
          items
            .map((item) => item.actorDisplayName || item.actorUsername || "")
            .filter(Boolean)
        )
      );

      if (!silent) {
        const visibleCount = importedRecipeCount > 0 ? importedRecipeCount : items.length;
        const suffix = actorNames.length ? ` (${actorNames.slice(0, 2).join(", ")})` : "";
        const recipeLabel = visibleCount === 1 ? "recipe" : "recipes";
        IRMS.notify(`New ${recipeLabel} imported: ${visibleCount}${suffix}`, "info");
      }

      await render();
      if (weighing.open) {
        await loadWeighingQueue();
        renderWeighingPanel();
      }
    } catch (error) {
      if (!silent) {
        IRMS.notify(`Recipe alert sync failed: ${error.message}`, "error");
      }
    } finally {
      recipeImportNotice.checking = false;
    }
  }

  function startRecipeImportPolling() {
    if (recipeImportNotice.timerId) {
      window.clearInterval(recipeImportNotice.timerId);
    }

    recipeImportNotice.timerId = window.setInterval(() => {
      if (document.visibilityState === "hidden") {
        return;
      }
      checkRecipeImportNotifications();
    }, 8000);
  }

  function activateColorButton(color) {
    focusButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.color === color);
    });
  }

  function resetWeighingProgress(totalSteps) {
    weighing.doneCount = 0;
    weighing.initialTotal = Number(totalSteps || 0);
    weighing.pendingRecipeCompletion = null;
  }

  function getQueueColorCounts(queue) {
    return queue.reduce(
      (acc, item) => {
        const group = item.colorGroup || "none";
        if (!Object.prototype.hasOwnProperty.call(acc, group)) {
          acc.none += 1;
          return acc;
        }
        acc[group] += 1;
        return acc;
      },
      { black: 0, red: 0, blue: 0, yellow: 0, none: 0 }
    );
  }

  function syncWeighingControls() {
    const hasAction = Boolean(weighing.pendingRecipeCompletion) || weighing.queue.length > 0;
    const disableAdvance = weighing.loading || weighing.advancing || !hasAction;
    if (weighingAdvanceBtn) {
      weighingAdvanceBtn.disabled = disableAdvance;
    }
    if (weighingRefreshBtn) {
      weighingRefreshBtn.disabled = weighing.loading || weighing.advancing;
    }
  }

  function renderWeighingPanel() {
    if (!weighingStateBadge) {
      return;
    }

    const total = Math.max(weighing.initialTotal, weighing.doneCount + weighing.queue.length);
    const progressRatio = total > 0 ? weighing.doneCount / total : 0;
    if (weighingProgressFill) {
      weighingProgressFill.style.width = `${Math.max(0, Math.min(100, progressRatio * 100))}%`;
    }
    if (weighingProgressText) {
      weighingProgressText.textContent = `${weighing.doneCount} / ${total}`;
    }

    const queueCounts = getQueueColorCounts(weighing.queue);
    const queueRecipeCount = new Set(weighing.queue.map((item) => item.recipeId)).size;
    const chips = [
      `남은 스텝 ${weighing.queue.length}`,
      `남은 레시피 ${queueRecipeCount}`,
      `Black ${queueCounts.black}`,
      `Red ${queueCounts.red}`,
      `Blue ${queueCounts.blue}`,
      `Yellow ${queueCounts.yellow}`,
      `기타 ${queueCounts.none}`,
    ];
    if (weighing.pendingRecipeCompletion) {
      chips.unshift("레시피 완료 대기 1");
    }
    if (weighingSummary) {
      weighingSummary.innerHTML = chips
        .map((value) => `<span class="weighing-summary-chip">${value}</span>`)
        .join("");
    }

    if (weighing.pendingRecipeCompletion) {
      const pending = weighing.pendingRecipeCompletion;
      weighingStateBadge.className = "weighing-state-badge state-recipe";
      weighingStateBadge.textContent = "RECIPE COMPLETE";
      weighingMaterialName.textContent = `${pending.productName} · ${pending.inkName}`;
      weighingTargetValue.textContent = "완료 처리";
      weighingRecipeMeta.textContent = `${pending.position || "-"} | 모든 계량 완료`;
      weighingActionHint.textContent =
        "Enter 또는 Space를 눌러 레시피 완료를 확정하고 다음 계량으로 이동하세요.";
      const nextStep = weighing.queue[0];
      weighingNextValue.textContent = nextStep
        ? `${nextStep.materialName} · ${IRMS.formatValue(nextStep.targetValue)} ${nextStep.unit || ""} (${nextStep.productName})`
        : "다음 계량 없음";
      syncWeighingControls();
      return;
    }

    const current = weighing.queue[0];
    if (!current) {
      weighingStateBadge.className = "weighing-state-badge state-idle";
      weighingStateBadge.textContent = "IDLE";
      weighingMaterialName.textContent = "대기중";
      weighingTargetValue.textContent = "-";
      weighingRecipeMeta.textContent = "남은 계량 항목이 없습니다.";
      weighingActionHint.textContent = "큐를 새로고침하거나 Esc로 계량 모드를 종료하세요.";
      weighingNextValue.textContent = "-";
      syncWeighingControls();
      return;
    }

    weighingStateBadge.className = "weighing-state-badge";
    weighingStateBadge.textContent = `${colorLabel(current.colorGroup)} STEP`;
    weighingMaterialName.textContent = current.materialName;
    weighingTargetValue.textContent = `${IRMS.formatValue(current.targetValue)} ${current.unit || ""}`.trim();
    weighingRecipeMeta.textContent = `${current.productName} · ${current.position || "-"} · ${current.inkName}`;
    weighingActionHint.textContent = "Enter 또는 Space를 눌러 현재 계량을 완료 처리하세요.";

    const nextStep = weighing.queue[1];
    if (nextStep) {
      weighingNextValue.textContent = `${nextStep.materialName} · ${IRMS.formatValue(nextStep.targetValue)} ${nextStep.unit || ""} (${nextStep.productName})`;
    } else {
      weighingNextValue.textContent = "현재 큐 기준 마지막 스텝입니다.";
    }
    syncWeighingControls();
  }

  async function loadWeighingQueue(options = {}) {
    const resetProgress = Boolean(options.resetProgress);
    const notifySummary = Boolean(options.notifySummary);
    const selectedGroup = (weighingColorGroup?.value || weighing.colorGroup || "all").trim();

    weighing.loading = true;
    syncWeighingControls();

    try {
      const payload = await IRMS.getWeighingQueue(selectedGroup);
      weighing.colorGroup = payload.colorGroup;
      weighing.queue = payload.items;

      if (resetProgress) {
        resetWeighingProgress(payload.summary.totalSteps);
      } else if (weighing.initialTotal === 0) {
        weighing.initialTotal = payload.summary.totalSteps;
      } else {
        const dynamicTotal = payload.summary.totalSteps + weighing.doneCount;
        if (dynamicTotal > weighing.initialTotal) {
          weighing.initialTotal = dynamicTotal;
        }
      }

      if (notifySummary) {
        IRMS.notify(
          `계량 큐 ${payload.summary.totalSteps}건 / 레시피 ${payload.summary.recipeCount}건`,
          "info"
        );
      }
    } catch (error) {
      IRMS.notify(`계량 큐 조회 실패: ${error.message}`, "error");
    } finally {
      weighing.loading = false;
      if (weighing.open) {
        renderWeighingPanel();
      }
      syncWeighingControls();
    }
  }

  function openWeighingMode() {
    if (!weighingMode) {
      return;
    }
    weighing.open = true;
    weighingMode.classList.add("active");
    weighingMode.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    loadWeighingQueue({ resetProgress: true });
  }

  function closeWeighingMode() {
    if (!weighingMode) {
      return;
    }
    weighing.open = false;
    weighingMode.classList.remove("active");
    weighingMode.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  async function handleWeighingAdvance() {
    if (!weighing.open || weighing.loading || weighing.advancing) {
      return;
    }

    weighing.advancing = true;
    syncWeighingControls();

    try {
      if (weighing.pendingRecipeCompletion) {
        const pendingRecipe = weighing.pendingRecipeCompletion;
        await IRMS.completeWeighingRecipe(pendingRecipe.recipeId);
        weighing.pendingRecipeCompletion = null;
        IRMS.notify(
          `${pendingRecipe.productName} (${pendingRecipe.inkName}) 완료 처리되었습니다.`,
          "success"
        );
        await render();
        await loadWeighingQueue();
        renderWeighingPanel();
        return;
      }

      const current = weighing.queue[0];
      if (!current) {
        IRMS.notify("남은 계량 항목이 없습니다.", "info");
        return;
      }

      const measuredBy = weighingOperator?.value?.trim() || "작업자";
      const stepResult = await IRMS.completeWeighingStep(
        current.recipeId,
        current.materialId,
        measuredBy
      );

      weighing.queue.shift();
      weighing.doneCount += 1;

      if (stepResult.ready_for_recipe_completion) {
        weighing.pendingRecipeCompletion = current;
        IRMS.notify(
          `${current.productName} 계량 스텝 완료. Enter/Space로 레시피 완료를 확정하세요.`,
          "info"
        );
      } else {
        IRMS.notify(`${current.materialName} 계량 완료`, "success");
      }

      await render();
      renderWeighingPanel();
    } catch (error) {
      IRMS.notify(`계량 처리 실패: ${error.message}`, "error");
    } finally {
      weighing.advancing = false;
      syncWeighingControls();
    }
  }

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
      if (!nextRoomKey || nextRoomKey === state.selectedRoomKey) {
        return;
      }

      state.selectedRoomKey = nextRoomKey;
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
      if (state.chatSending) {
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

      state.chatSending = true;
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
          state.chatLatestIdByRoom[room.key] = Number(
            payload.message.id || state.chatLatestIdByRoom[room.key] || 0
          );

          const roomIndex = state.rooms.findIndex((entry) => entry.key === room.key);
          if (roomIndex >= 0) {
            state.rooms[roomIndex].messageCount =
              Number(state.rooms[roomIndex].messageCount || 0) + 1;
            state.rooms[roomIndex].latestMessageAt = payload.message.createdAt;
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
        state.chatSending = false;
        if (chatSend) {
          chatSend.disabled = false;
        }
      }
    });
  }

  focusButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.color = button.dataset.color || "all";
      activateColorButton(state.color);
      persistFilters();
      updateFilterSummary();
      render();
    });
  });

  searchInput.addEventListener(
    "input",
    IRMS.debounce(() => {
      persistFilters();
      updateFilterSummary();
      render();
    }, 300),
  );
  fromInput.addEventListener("change", () => {
    persistFilters();
    updateFilterSummary();
    render();
  });
  toInput.addEventListener("change", () => {
    persistFilters();
    updateFilterSummary();
    render();
  });
  if (filterResetBtn) {
    filterResetBtn.addEventListener("click", resetFilters);
  }

  tableBody.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains("complete-btn")) {
      return;
    }
    const recipeId = Number(target.dataset.id);
    if (!Number.isFinite(recipeId)) {
      return;
    }

    if (!window.confirm("선택한 레시피를 완료 처리하시겠습니까?")) {
      return;
    }

    const row = tableBody.querySelector(`tr[data-id="${recipeId}"]`);
    try {
      if (row) {
        row.classList.add("removing");
      }
      await IRMS.updateRecipeStatus(recipeId, "complete");
      IRMS.notify("레시피를 완료 처리했습니다.", "success");
      window.setTimeout(render, row ? 280 : 0);
    } catch (error) {
      if (row) {
        row.classList.remove("removing");
      }
      IRMS.notify(`완료 처리 실패: ${error.message}`, "error");
    }
  });

  if (weighingOpenBtn) {
    weighingOpenBtn.addEventListener("click", openWeighingMode);
  }
  if (weighingRefreshMainBtn) {
    weighingRefreshMainBtn.addEventListener("click", () => {
      loadWeighingQueue({ notifySummary: true });
    });
  }
  if (weighingCloseBtn) {
    weighingCloseBtn.addEventListener("click", closeWeighingMode);
  }
  if (weighingRefreshBtn) {
    weighingRefreshBtn.addEventListener("click", () => {
      loadWeighingQueue({ notifySummary: true });
    });
  }
  if (weighingAdvanceBtn) {
    weighingAdvanceBtn.addEventListener("click", handleWeighingAdvance);
  }
  if (weighingColorGroup) {
    weighing.colorGroup = IRMS.loadPreference(preferenceKeys.weighingColor, "all") || "all";
    weighingColorGroup.value = weighing.colorGroup;
    weighingColorGroup.addEventListener("change", () => {
      weighing.colorGroup = weighingColorGroup.value || "all";
      IRMS.savePreference(preferenceKeys.weighingColor, weighing.colorGroup);
      loadWeighingQueue({ resetProgress: true, notifySummary: true });
    });
  }
  if (weighingMode) {
    weighingMode.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (target.hasAttribute("data-close-weighing")) {
        closeWeighingMode();
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (!weighing.open) {
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      closeWeighingMode();
      return;
    }

    const target = event.target;
    const isTypingTarget =
      target instanceof HTMLElement &&
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
    if (isTypingTarget) {
      return;
    }

    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      handleWeighingAdvance();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      checkRecipeImportNotifications({ silent: true });
      refreshChatPanel({ replace: false, silent: true });
    }
  });

  restoreFilters();
  activateColorButton(state.color);
  updateFilterSummary();
  render();
  refreshChatPanel({ replace: true, silent: true });
  checkRecipeImportNotifications({ silent: true });
  startRecipeImportPolling();
  startChatPolling();
});

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
  const weighingUndoBtn = document.getElementById("weighing-undo-btn");
  const weighingProgressFill = document.getElementById("weighing-progress-fill");
  const weighingProgressText = document.getElementById("weighing-progress-text");
  const weighingSummary = document.getElementById("weighing-summary");
  const weighingStateBadge = document.getElementById("weighing-state-badge");
  const weighingProductName = document.getElementById("weighing-product-name");
  const weighingInkLabel = document.getElementById("weighing-ink-label");
  const weighingPositionLabel = document.getElementById("weighing-position-label");
  const weighingMaterialName = document.getElementById("weighing-material-name");
  const weighingTargetValue = document.getElementById("weighing-target-value");
  const weighingActionHint = document.getElementById("weighing-action-hint");
  const weighingNextValue = document.getElementById("weighing-next-value");
  const weighingColorSelect = document.getElementById("weighing-color-select");
  const weighingModeLabel = document.getElementById("weighing-mode-label");
  const workStockBanner = document.getElementById("work-stock-banner");
  const weighingCurrentCard = document.querySelector(".weighing-current");

  const lowStockSet = new Set();
  async function refreshLowStock() {
    try {
      const res = await fetch("/api/materials/stock");
      if (!res.ok) return;
      const data = await res.json();
      lowStockSet.clear();
      let neg = 0, low = 0;
      (data.items || []).forEach((m) => {
        if (m.status === "negative") { lowStockSet.add(m.id); neg++; }
        else if (m.status === "low") { lowStockSet.add(m.id); low++; }
      });
      if (workStockBanner) {
        if (neg || low) {
          const parts = [];
          if (neg) parts.push(`음수 재고 ${neg}개`);
          if (low) parts.push(`임계치 미달 ${low}개`);
          workStockBanner.textContent = `⚠ 재고 주의: ${parts.join(", ")} - 책임자에게 알려주세요`;
          workStockBanner.hidden = false;
        } else {
          workStockBanner.hidden = true;
        }
      }
    } catch (_) {}
  }
  refreshLowStock();
  setInterval(refreshLowStock, 30000);

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
  };

  const weighing = {
    open: false,
    loading: false,
    advancing: false,
    undoing: false,
    queue: [],
    doneCount: 0,
    initialTotal: 0,
    colorGroup: "all",
    pendingRecipeCompletion: null,
    lastCompleted: null,
  };

  const stageLabels = {
    registered: "Registered",
    in_progress: "In Progress",
    completed: "Completed",
  };

  var colorLabel = IRMS.colorLabel;

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

  function buildHeader() {
    tableHead.innerHTML = [
      '<th class="sticky-left">제품명</th>',
      "<th>위치</th>",
      "<th>잉크명</th>",
      "<th>원재료</th>",
      "<th>상태</th>",
      "<th>등록시각</th>",
      '<th class="sticky-right">처리</th>',
    ].join("");
  }

  function countRecipeMaterials(recipe) {
    return (recipe.items || []).length;
  }

  function buildRows(recipes) {
    if (!recipes.length) {
      tableBody.innerHTML =
        '<tr><td colspan="7"><div class="empty-state">현재 조건에서 처리할 레시피가 없습니다.</div></td></tr>';
      return;
    }

    tableBody.innerHTML = recipes
      .map((recipe) => {
        const status = `<span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span>`;
        const materialCount = countRecipeMaterials(recipe);
        const materialCell = materialCount > 0
          ? `<span class="material-count-badge">${materialCount}종</span>`
          : "-";

        return `
          <tr class="recipe-row" data-id="${recipe.id}">
            <td class="sticky-left product-cell">${IRMS.escapeHtml(recipe.productName)}</td>
            <td>${IRMS.escapeHtml(recipe.position || "-")}</td>
            <td>${IRMS.escapeHtml(recipe.inkName)}</td>
            <td>${materialCell}</td>
            <td>${status}</td>
            <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
            <td class="sticky-right">
              ${recipe.status === "in_progress"
                ? `<button type="button" class="btn success complete-btn" data-id="${recipe.id}">완료 처리</button>`
                : `<span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span>`
              }
            </td>
          </tr>
        `;
      })
      .join("");
  }

  const chatModule = IRMS.createChat({
    prefix: "chat",
    stageLabels,
    elements: { roomTabs, chatMessages, chatStageGroup, roomMeta },
    state: {
      get rooms() { return state.rooms; },
      set rooms(v) { state.rooms = v; },
      get selectedRoomKey() { return state.selectedRoomKey; },
      set selectedRoomKey(v) { state.selectedRoomKey = v; },
      get latestByRoom() { return state.chatLatestIdByRoom; },
      set latestByRoom(v) { state.chatLatestIdByRoom = v; },
      get timerId() { return state.chatTimerId; },
      set timerId(v) { state.chatTimerId = v; },
      get currentUsername() { return state.currentUsername; },
    },
  });

  function refreshChatPanel(options) { return chatModule.refresh(options); }
  function startChatPolling() { chatModule.startPolling(10000); }

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
            <span class="muted">담당자 ${IRMS.escapeHtml(recipe.createdBy)}</span>
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
      const [activeRecipes, completedRecipes] = await Promise.all([
        IRMS.getRecipes({ search, dateFrom: from, dateTo: to }),
        IRMS.getRecipes({ status: "completed" }),
      ]);

      if (token !== state.loadingToken) {
        return;
      }

      const working = activeRecipes.filter(
        (recipe) => recipe.status === "pending" || recipe.status === "in_progress"
      );

      buildHeader();
      buildRows(working);
      renderStats(working);
      renderLog(completedRecipes);
    } catch (error) {
      IRMS.notify(`데이터 로드 실패: ${error.message}`, "error");
      tableBody.innerHTML =
        '<tr><td colspan="7"><div class="empty-state">데이터를 불러오지 못했습니다.</div></td></tr>';
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
        IRMS.notify(`레시피 알림 동기화 실패: ${error.message}`, "error");
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
    weighing.lastCompleted = null;
  }

  function getQueueColorCounts(queue) {
    return queue.reduce(
      (acc, item) => {
        const group = item.colorGroup || "none";
        const key = Object.prototype.hasOwnProperty.call(acc, group) ? group : "none";
        acc[key] += 1;
        return acc;
      },
      { black: 0, red: 0, blue: 0, yellow: 0, none: 0 }
    );
  }

  function syncWeighingControls() {
    const busy = weighing.loading || weighing.advancing || weighing.undoing;
    const hasAction = Boolean(weighing.pendingRecipeCompletion) || weighing.queue.length > 0;
    if (weighingAdvanceBtn) {
      weighingAdvanceBtn.disabled = busy || !hasAction;
    }
    if (weighingUndoBtn) {
      weighingUndoBtn.disabled = busy || !weighing.lastCompleted;
    }
    if (weighingRefreshBtn) {
      weighingRefreshBtn.disabled = busy;
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
      weighing.lastSpokenStepKey = null;
      const pending = weighing.pendingRecipeCompletion;
      weighingStateBadge.className = "weighing-state-badge state-recipe";
      weighingStateBadge.textContent = "RECIPE COMPLETE";
      weighingProductName.textContent = pending.productName;
      weighingInkLabel.textContent = pending.inkName;
      weighingPositionLabel.textContent = pending.position || "-";
      weighingMaterialName.textContent = "모든 계량 완료";
      weighingTargetValue.textContent = "완료 처리";
      weighingActionHint.textContent =
        "Enter 또는 Space를 눌러 레시피 완료를 확정하고 다음 계량으로 이동하세요.";
      const nextStep = weighing.queue[0];
      weighingNextValue.textContent = nextStep
        ? `${nextStep.materialName} · ${nextStep.targetValue} (${nextStep.productName})`
        : "다음 계량 없음";
      syncWeighingControls();
      return;
    }

    const current = weighing.queue[0];
    if (!current) {
      weighing.lastSpokenStepKey = null;
      weighingStateBadge.className = "weighing-state-badge state-idle";
      weighingStateBadge.textContent = "IDLE";
      weighingProductName.textContent = "-";
      weighingInkLabel.textContent = "-";
      weighingPositionLabel.textContent = "-";
      weighingMaterialName.textContent = "대기중";
      weighingTargetValue.textContent = "-";
      weighingActionHint.textContent = "큐를 새로고침하거나 Esc로 계량 모드를 종료하세요.";
      weighingNextValue.textContent = "-";
      syncWeighingControls();
      return;
    }

    const colorBadgeClass = current.colorGroup && current.colorGroup !== "none"
      ? `color-${current.colorGroup}` : "";
    weighingStateBadge.className = `weighing-state-badge ${colorBadgeClass}`.trim();
    weighingStateBadge.textContent = `${colorLabel(current.colorGroup)} STEP`;
    weighingProductName.textContent = current.productName;
    weighingInkLabel.textContent = current.inkName;
    weighingPositionLabel.textContent = `위치: ${current.position || "-"}`;
    weighingMaterialName.textContent = current.materialName;
    weighingTargetValue.textContent = current.targetValue;
    weighingActionHint.textContent = "Enter 또는 Space를 눌러 현재 계량을 완료 처리하세요.";

    if (weighingCurrentCard) {
      weighingCurrentCard.classList.toggle(
        "stock-warning-stripe",
        lowStockSet.has(current.materialId)
      );
    }

    const stepKey = `${current.recipeId}:${current.materialId}`;
    if (weighing.lastSpokenStepKey !== stepKey) {
      weighing.lastSpokenStepKey = stepKey;
      const spokenValue = String(current.targetValue || "").trim();
      if (spokenValue && spokenValue !== "-") {
        IRMS.speakText(`${current.materialName}, ${spokenValue}`);
      }
    }

    const nextStep = weighing.queue[1];
    if (nextStep) {
      weighingNextValue.textContent = `${nextStep.materialName} · ${nextStep.targetValue} (${nextStep.productName})`;
    } else {
      weighingNextValue.textContent = "현재 큐 기준 마지막 스텝입니다.";
    }
    syncWeighingControls();
  }

  async function loadWeighingQueue(options = {}) {
    const resetProgress = Boolean(options.resetProgress);
    const notifySummary = Boolean(options.notifySummary);
    const selectedGroup = (weighing.colorGroup || "all").trim();

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
    if (!weighingMode) return;
    const selected = weighingColorSelect?.value;
    if (!selected) {
      IRMS.notify("계량 모드를 선택하세요.", "warn");
      return;
    }
    weighing.colorGroup = selected;

    // Show mode label in header
    if (weighingModeLabel) {
      const opt = weighingColorSelect.options[weighingColorSelect.selectedIndex];
      weighingModeLabel.textContent = opt ? opt.textContent : "";
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
    if (weighingColorSelect) weighingColorSelect.value = "";
    if (weighingOpenBtn) weighingOpenBtn.disabled = true;
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

      const stepResult = await IRMS.completeWeighingStep(
        current.recipeId,
        current.materialId
      );

      weighing.queue.shift();
      weighing.doneCount += 1;
      weighing.lastCompleted = {
        recipeId: current.recipeId,
        materialId: current.materialId,
        materialName: current.materialName,
        productName: current.productName,
        inkName: current.inkName,
      };

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

  async function handleWeighingUndo() {
    if (!weighing.open || weighing.loading || weighing.advancing || weighing.undoing) {
      return;
    }
    if (!weighing.lastCompleted) {
      IRMS.notify("되돌릴 수 있는 스텝이 없습니다.", "info");
      return;
    }

    weighing.undoing = true;
    syncWeighingControls();

    try {
      const target = weighing.lastCompleted;
      await IRMS.undoWeighingStep(target.recipeId, target.materialId);
      weighing.lastCompleted = null;
      if (weighing.doneCount > 0) {
        weighing.doneCount -= 1;
      }
      weighing.pendingRecipeCompletion = null;
      IRMS.notify(`${target.materialName} 되돌림 완료`, "success");
      await loadWeighingQueue();
      await render();
      renderWeighingPanel();
    } catch (error) {
      IRMS.notify(`되돌리기 실패: ${error.message}`, "error");
    } finally {
      weighing.undoing = false;
      syncWeighingControls();
    }
  }

  chatModule.bindRoomTabs(roomTabs);

  chatModule.bindForm({ form: chatForm, input: chatInput, stage: chatStage, send: chatSend });

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
      const msg = error.message || "";
      if (msg.includes("WEIGHING_INCOMPLETE")) {
        const remaining = msg.split(":")[1] || "?";
        IRMS.notify(`계량이 완료되지 않았습니다. 미계량 ${remaining}건이 남아있습니다. 계량 모드에서 진행해 주세요.`, "error");
      } else {
        IRMS.notify(`완료 처리 실패: ${msg}`, "error");
      }
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
  if (weighingUndoBtn) {
    weighingUndoBtn.addEventListener("click", handleWeighingUndo);
  }
  if (weighingColorSelect) {
    weighingColorSelect.value = "";
    if (weighingOpenBtn) weighingOpenBtn.disabled = true;
    weighingColorSelect.addEventListener("change", () => {
      if (weighingOpenBtn) weighingOpenBtn.disabled = !weighingColorSelect.value;
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

  // ── 담당자 30분 비활동 자동 로그아웃 ──
  const IDLE_TIMEOUT = 30 * 60 * 1000; // 30분
  let idleTimer = null;

  function resetIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
      try {
        await IRMS.logout();
      } catch (_e) { /* ignore */ }
      window.location.assign("/weighing/select");
    }, IDLE_TIMEOUT);
  }

  ["mousemove", "mousedown", "keydown", "touchstart", "scroll"].forEach((evt) => {
    document.addEventListener(evt, resetIdleTimer, { passive: true });
  });
  resetIdleTimer();
});

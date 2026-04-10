document.addEventListener("DOMContentLoaded", () => {
  const shell = document.querySelector(".site-shell");
  const spreadsheetContainer = document.getElementById("spreadsheet");
  const rawInput = document.getElementById("raw-input");
  const previewBtn = document.getElementById("preview-btn");
  const registerBtn = document.getElementById("register-btn");
  const clearBtn = document.getElementById("clear-btn");

  const previewMeta = document.getElementById("preview-meta");
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

  // Tab navigation
  const tabBtns = document.querySelectorAll(".mgmt-tab");
  const tabPanels = document.querySelectorAll(".tab-panel");
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });

  let currentPreview = null;
  let materials = [];
  let sheet = null;
  let confirmedRawText = "";
  let previewIsStale = false;
  let suppressDirtyTracking = false;
  let spreadsheetFallbackNotified = false;
  // Lookup tab elements
  const lookupProduct = document.getElementById("lookup-product");
  const productList = document.getElementById("product-list");
  const lookupBtn = document.getElementById("lookup-btn");
  const lookupResult = document.getElementById("lookup-result");
  const lookupActions = document.getElementById("lookup-actions");
  const lookupSelectedLabel = document.getElementById("lookup-selected-label");
  const lookupCopyBtn = document.getElementById("lookup-copy-btn");
  const lookupCloneBtn = document.getElementById("lookup-clone-btn");

  let selectedRecipeId = null;
  let pendingRevisionOf = null;

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
    renderValidationMeta(currentPreview);
    IRMS.notify("시트가 수정되어 검증이 무효화되었습니다. 다시 Validate 하세요.", "warn");
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
          "스프레드시트 UI 로드에 실패하여 텍스트 입력 모드로 전환했습니다.",
          "warn",
        );
        spreadsheetFallbackNotified = true;
      }
      return;
    }

    setRawInputMode(false);

    // Create an empty 15x10 grid by default
    const data = Array.from({ length: 15 }, () => Array(10).fill(""));

    spreadsheetFactory(spreadsheetContainer, {
      worksheets: [
        {
          data,
          minDimensions: [10, 15],
          defaultColWidth: 80,
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
          onpaste: () => {
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

  const chatModule = IRMS.createChat({
    prefix: "chat",
    stageLabels,
    elements: { roomTabs, chatMessages, chatStageGroup, roomMeta },
    state: chatState,
  });

  function refreshChatPanel(options) { return chatModule.refresh(options); }
  function startChatPolling() { chatModule.startPolling(10000); }

  function renderIssues(list, target, emptyText) {
    if (!list || !list.length) {
      target.innerHTML = `<li class="muted">${emptyText}</li>`;
      return;
    }
    target.innerHTML = list
      .slice(0, 12)
      .map(
        (item) =>
          `<li>L${item.level} · ${IRMS.escapeHtml(item.message)}${item.row ? ` (행 ${item.row})` : ""}</li>`,
      )
      .join("");
  }

  function renderValidationMeta(result) {
    const rows = result?.rows || [];
    const badges = [
      `<span class="meta-badge meta-ok">Rows ${rows.length}</span>`,
      `<span class="meta-badge meta-warn">Warn ${(result?.warnings || []).length}</span>`,
      `<span class="meta-badge meta-error">Error ${(result?.errors || []).length}</span>`,
    ];
    if (previewIsStale) {
      badges.push('<span class="meta-badge meta-warn">재검증 필요</span>');
    }
    previewMeta.innerHTML = badges.join("");
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
          '<tr><td colspan="7"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
        return;
      }

      historyBody.innerHTML = rows
        .map(
          (recipe) => `
            <tr class="history-row" data-recipe-id="${recipe.id}">
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

      // Accordion: row click to expand detail
      historyBody.querySelectorAll(".history-row").forEach((row) => {
        row.style.cursor = "pointer";
        row.addEventListener("click", async () => {
          const recipeId = Number(row.dataset.recipeId);
          const existing = row.nextElementSibling;
          if (existing && existing.classList.contains("history-detail-row")) {
            existing.remove();
            row.classList.remove("selected");
            return;
          }
          // Close any other open detail
          historyBody.querySelectorAll(".history-detail-row").forEach((r) => r.remove());
          historyBody.querySelectorAll(".history-row.selected").forEach((r) => r.classList.remove("selected"));

          row.classList.add("selected");
          try {
            const detail = await IRMS.getRecipeDetail(recipeId);
            const items = detail.items || [];
            const itemsHtml = items.length
              ? items.map((it) =>
                  `<span class="detail-chip">${IRMS.escapeHtml(it.material_name)}: ${IRMS.escapeHtml(String(it.value))}</span>`
                ).join("")
              : '<span class="muted">재료 없음</span>';

            const detailRow = document.createElement("tr");
            detailRow.classList.add("history-detail-row");
            detailRow.innerHTML = `<td colspan="7">
              <div class="history-detail-content">
                <div class="detail-items">${itemsHtml}</div>
                <div class="detail-actions">
                  <button class="btn btn-sm history-copy-btn" data-recipe-id="${recipeId}">엑셀로 복사</button>
                  <button class="btn btn-sm accent history-clone-btn" data-recipe-id="${recipeId}">복제하여 등록</button>
                </div>
              </div>
            </td>`;
            row.after(detailRow);

            detailRow.querySelector(".history-copy-btn").addEventListener("click", async (e) => {
              e.stopPropagation();
              try {
                await copyToClipboard(detail.tsv);
                IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
              } catch (err) {
                IRMS.notify(`복사 실패: ${err.message}`, "error");
              }
            });

            detailRow.querySelector(".history-clone-btn").addEventListener("click", (e) => {
              e.stopPropagation();
              selectedRecipeId = recipeId;
              handleLookupClone();
            });
          } catch (error) {
            IRMS.notify(`상세 조회 실패: ${error.message}`, "error");
          }
        });
      });
    } catch (error) {
      IRMS.notify(`이력 조회 실패: ${error.message}`, "error");
    }
  }

  async function handlePreview() {
    const raw = getSpreadsheetDataAsText();

    if (!raw) {
      IRMS.notify("데이터를 입력하거나 붙여넣은 후 Validate 하세요.", "warn");
      return;
    }

    IRMS.btnLoading(previewBtn, true);
    try {
      const result = await IRMS.previewImport(raw);
      currentPreview = result;
      confirmedRawText = raw;
      previewIsStale = false;
      renderValidationMeta(result);
      renderIssues(result.errors, errorList, "ERROR 없음");
      renderIssues(result.warnings, warningList, "WARN 없음");
      syncRegisterState();

      if (!result.errors.length && result.rows.length > 0) {
        IRMS.notify(`검증 완료: ${result.rows.length}건 등록 가능`, "success");
      }
    } catch (error) {
      IRMS.notify(`검증 실패: ${error.message}`, "error");
    } finally {
      IRMS.btnLoading(previewBtn, false);
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
        IRMS.notify("검정본이 무효화되었습니다. 다시 Validate 후 등록하세요.", "warn");
      }
      return;
    }

    IRMS.btnLoading(registerBtn, true);
    try {
      const result = await IRMS.importRecipes(confirmedRawText, "System Gate", pendingRevisionOf);
      IRMS.notify(
        `${result.created_count}건 레시피를 등록했습니다.`,
        "success",
      );

      // Reset everything
      handleClear();
    } catch (error) {
      IRMS.notify(`등록 실패: ${error.message}`, "error");
    } finally {
      IRMS.btnLoading(registerBtn, false);
    }
  }

  function handleClear() {
    confirmedRawText = "";
    previewIsStale = false;
    pendingRevisionOf = null;
    if (sheet) {
      initSpreadsheet(); // Reinitialize to clear
    } else if (rawInput) {
      rawInput.value = "";
    }
    currentPreview = null;
    previewIsStale = false;
    renderValidationMeta({ rows: [], warnings: [], errors: [] });
    renderIssues([], errorList, "ERROR 없음");
    renderIssues([], warningList, "WARN 없음");
    syncRegisterState();
  }

  previewBtn.addEventListener("click", handlePreview);

  chatModule.bindRoomTabs(roomTabs);

  chatModule.bindForm({ form: chatForm, input: chatInput, stage: chatStage, send: chatSend });
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


  // ── Lookup Tab Logic ──

  async function loadProducts() {
    try {
      const items = await IRMS.getProducts();
      if (productList) {
        productList.innerHTML = items
          .map((name) => `<option value="${IRMS.escapeHtml(name)}">`)
          .join("");
      }
    } catch (error) {
      IRMS.notify(`제품 목록 로드 실패: ${error.message}`, "error");
    }
  }

  function setLookupSelection(recipeId) {
    selectedRecipeId = recipeId;
    const rows = lookupResult.querySelectorAll("tbody tr");
    rows.forEach((row) => {
      row.classList.toggle("selected", Number(row.dataset.recipeId) === recipeId);
    });
    if (lookupSelectedLabel) {
      lookupSelectedLabel.textContent = recipeId ? `선택: #${recipeId}` : "선택: 없음";
    }
    if (lookupCopyBtn) lookupCopyBtn.disabled = !recipeId;
    if (lookupCloneBtn) lookupCloneBtn.disabled = !recipeId;
    if (lookupActions) lookupActions.hidden = !recipeId;
  }

  async function handleLookup() {
    const productName = lookupProduct ? lookupProduct.value.trim() : "";
    if (!productName) {
      IRMS.notify("제품명을 입력해주세요.", "warn");
      return;
    }

    IRMS.btnLoading(lookupBtn, true);
    try {
      const data = await IRMS.getRecipesByProduct(productName);
      const recipes = data.items || [];

      if (!recipes.length) {
        lookupResult.innerHTML = '<p class="empty-state">해당 제품의 레시피가 없습니다.</p>';
        setLookupSelection(null);
        return;
      }

      // Collect all unique material names across recipes for pivot columns
      const allMaterials = [];
      const materialSet = new Set();
      for (const recipe of recipes) {
        for (const item of recipe.items || []) {
          if (!materialSet.has(item.material_name)) {
            materialSet.add(item.material_name);
            allMaterials.push(item.material_name);
          }
        }
      }

      // Build pivot table
      const headerCells = [
        "<th>ID</th>",
        "<th>위치</th>",
        "<th>잉크명</th>",
        ...allMaterials.map((m) => `<th>${IRMS.escapeHtml(m)}</th>`),
        "<th>상태</th>",
        "<th>등록일</th>",
        "<th>등록자</th>",
      ].join("");

      const bodyRows = recipes
        .map((recipe) => {
          const valueMap = {};
          for (const item of recipe.items || []) {
            valueMap[item.material_name] = item.value;
          }
          const materialCells = allMaterials
            .map((m) => {
              const val = valueMap[m];
              return val != null && val !== ""
                ? `<td class="value-cell">${IRMS.escapeHtml(String(val))}</td>`
                : '<td class="value-cell muted">-</td>';
            })
            .join("");

          return `<tr data-recipe-id="${recipe.id}">
            <td>${recipe.id}</td>
            <td>${IRMS.escapeHtml(recipe.position || "-")}</td>
            <td>${IRMS.escapeHtml(recipe.ink_name || "")}</td>
            ${materialCells}
            <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
            <td>${IRMS.formatDateTime(recipe.created_at)}</td>
            <td>${IRMS.escapeHtml(recipe.created_by || "-")}</td>
          </tr>`;
        })
        .join("");

      lookupResult.innerHTML = `<table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>`;

      // Row click to select
      lookupResult.querySelectorAll("tbody tr").forEach((row) => {
        row.addEventListener("click", () => {
          setLookupSelection(Number(row.dataset.recipeId));
        });
      });

      setLookupSelection(null);
      if (lookupActions) lookupActions.hidden = false;
    } catch (error) {
      IRMS.notify(`조회 실패: ${error.message}`, "error");
    } finally {
      IRMS.btnLoading(lookupBtn, false);
    }
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for non-HTTPS or older browsers
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
    return Promise.resolve();
  }

  async function handleLookupCopy() {
    if (!selectedRecipeId) return;
    try {
      const detail = await IRMS.getRecipeDetail(selectedRecipeId);
      await copyToClipboard(detail.tsv);
      IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
    } catch (error) {
      IRMS.notify(`복사 실패: ${error.message}`, "error");
    }
  }

  async function handleLookupClone() {
    if (!selectedRecipeId) return;
    try {
      const detail = await IRMS.getRecipeDetail(selectedRecipeId);
      const tsvRows = detail.tsv.split("\n").map((r) => r.split("\t"));

      // Switch to import tab
      tabBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      const importTab = document.querySelector('[data-tab="import"]');
      if (importTab) importTab.classList.add("active");
      document.getElementById("tab-import").classList.add("active");

      // Load data into spreadsheet
      suppressDirtyTracking = true;
      destroySpreadsheet();

      const spreadsheetFactory = getSpreadsheetFactory();
      if (spreadsheetFactory && spreadsheetContainer) {
        // Pad rows to at least 15 rows
        while (tsvRows.length < 15) {
          tsvRows.push(Array(tsvRows[0]?.length || 10).fill(""));
        }
        // Pad columns to at least 10
        for (const row of tsvRows) {
          while (row.length < 10) row.push("");
        }

        spreadsheetFactory(spreadsheetContainer, {
          worksheets: [
            {
              data: tsvRows,
              minDimensions: [Math.max(10, tsvRows[0].length), 15],
              defaultColWidth: 80,
              tableOverflow: true,
              tableWidth: "100%",
              tableHeight: "300px",
              rowResize: true,
              columnDrag: true,
              contextMenu: true,
              textOverflow: true,
              onchange: () => markPreviewStale(),
              onafterchanges: () => markPreviewStale(),
              onpaste: () => markPreviewStale(),
            },
          ],
        });

        setRawInputMode(false);
        setTimeout(() => {
          getActiveWorksheet();
          suppressDirtyTracking = false;
        }, 0);
      } else if (rawInput) {
        rawInput.value = detail.tsv;
        setRawInputMode(true);
        suppressDirtyTracking = false;
      }

      pendingRevisionOf = selectedRecipeId;
      currentPreview = null;
      previewIsStale = false;
      confirmedRawText = "";
      renderValidationMeta({ rows: [], warnings: [], errors: [] });
      renderIssues([], errorList, "ERROR 없음");
      renderIssues([], warningList, "WARN 없음");
      syncRegisterState();

      IRMS.notify(`레시피 #${selectedRecipeId}을 불러왔습니다. 수정 후 Validate → Register 하세요.`, "info");
    } catch (error) {
      IRMS.notify(`복제 실패: ${error.message}`, "error");
    }
  }

  // Lookup event listeners
  if (lookupBtn) {
    lookupBtn.addEventListener("click", handleLookup);
  }
  if (lookupProduct) {
    lookupProduct.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleLookup();
      }
    });
  }
  if (lookupCopyBtn) {
    lookupCopyBtn.addEventListener("click", handleLookupCopy);
  }
  if (lookupCloneBtn) {
    lookupCloneBtn.addEventListener("click", handleLookupClone);
  }

  // Handle transfer from spreadsheet editor tab
  window.addEventListener("ss-transfer-to-import", (e) => {
    const tsv = e.detail?.tsv;
    if (!tsv) return;

    const tsvRows = tsv.split("\n").map((r) => r.split("\t"));

    // Switch to import tab
    tabBtns.forEach((b) => b.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    const importTab = document.querySelector('[data-tab="import"]');
    if (importTab) importTab.classList.add("active");
    document.getElementById("tab-import").classList.add("active");

    // Load data into spreadsheet
    suppressDirtyTracking = true;
    destroySpreadsheet();

    const spreadsheetFactory = getSpreadsheetFactory();
    if (spreadsheetFactory && spreadsheetContainer) {
      while (tsvRows.length < 15) {
        tsvRows.push(Array(tsvRows[0]?.length || 10).fill(""));
      }
      for (const row of tsvRows) {
        while (row.length < 10) row.push("");
      }

      spreadsheetFactory(spreadsheetContainer, {
        worksheets: [
          {
            data: tsvRows,
            minDimensions: [Math.max(10, tsvRows[0].length), 15],
            defaultColWidth: 80,
            tableOverflow: true,
            tableWidth: "100%",
            tableHeight: "300px",
            rowResize: true,
            columnDrag: true,
            contextMenu: true,
            textOverflow: true,
            onchange: () => markPreviewStale(),
            onafterchanges: () => markPreviewStale(),
            onpaste: () => markPreviewStale(),
          },
        ],
      });

      setRawInputMode(false);
      setTimeout(() => {
        getActiveWorksheet();
        suppressDirtyTracking = false;
      }, 0);
    } else if (rawInput) {
      rawInput.value = tsv;
      setRawInputMode(true);
      suppressDirtyTracking = false;
    }

    pendingRevisionOf = null;
    currentPreview = null;
    previewIsStale = false;
    confirmedRawText = "";
    renderValidationMeta({ rows: [], warnings: [], errors: [] });
    renderIssues([], errorList, "ERROR 없음");
    renderIssues([], warningList, "WARN 없음");
    syncRegisterState();
  });

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
      renderIssues([], errorList, "ERROR 없음");
      renderIssues([], warningList, "WARN 없음");
      syncRegisterState();
      await Promise.all([
        renderHistory(),
        refreshChatPanel({ replace: true, silent: true }),
        loadProducts(),
      ]);
      startChatPolling();
    } catch (error) {
      IRMS.notify(`초기화 실패: ${error.message}`, "error");
    }
  })();
});

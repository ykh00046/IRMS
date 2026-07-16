/**
 * management.js — /management page controller.
 *
 * Thinned during the split-management-js PDCA cycle (2026-05): the 1,006-LOC
 * monolith was split into 5 factory modules under static/js/management/.
 * This file now only builds the shared ctx, assembles the modules, wires
 * events, and runs the init sequence.
 * See docs/01-plan/features/split-management-js.plan.md.
 */
document.addEventListener("DOMContentLoaded", () => {
  // ── DOM references → ctx.dom ──
  const dom = {
    shell: document.querySelector(".app-shell"),
    topbarEyebrow: document.querySelector(".topbar-eyebrow"),
    topbarHeading: document.querySelector(".topbar-heading"),
    spreadsheetContainer: document.getElementById("spreadsheet"),
    rawInput: document.getElementById("raw-input"),
    revisionBanner: document.getElementById("revision-banner"),
    previewBtn: document.getElementById("preview-btn"),
    addRowBtn: document.getElementById("add-row-btn"),
    addColBtn: document.getElementById("add-col-btn"),
    registerBtn: document.getElementById("register-btn"),
    clearBtn: document.getElementById("clear-btn"),
    previewMeta: document.getElementById("preview-meta"),
    errorList: document.getElementById("error-list"),
    warningList: document.getElementById("warning-list"),
    historyBody: document.getElementById("history-body"),
    historyStatus: document.getElementById("history-status"),
    historySearch: document.getElementById("history-search"),
    historyFrom: document.getElementById("history-from"),
    historyTo: document.getElementById("history-to"),
    historySummary: document.getElementById("management-history-summary"),
    historyResetBtn: document.getElementById("management-history-reset"),
    tabBtns: document.querySelectorAll(".mgmt-tab"),
    tabPanels: document.querySelectorAll(".tab-panel"),
    lookupProduct: document.getElementById("lookup-product"),
    productList: document.getElementById("product-list"),
    lookupBtn: document.getElementById("lookup-btn"),
    lookupResult: document.getElementById("lookup-result"),
    lookupAnchor: document.getElementById("lookup-anchor"),
    lookupActions: document.getElementById("lookup-actions"),
    lookupSelectedLabel: document.getElementById("lookup-selected-label"),
    lookupCopyBtn: document.getElementById("lookup-copy-btn"),
    lookupCloneBtn: document.getElementById("lookup-clone-btn"),
    lookupHistoryBtn: document.getElementById("lookup-history-btn"),
    lookupDhr: document.getElementById("lookup-dhr"),
    lookupDhrBtn: document.getElementById("lookup-dhr-btn"),
    historyModal: document.getElementById("history-modal"),
    historyModalClose: document.getElementById("history-modal-close"),
    historyModalTitle: document.getElementById("history-modal-title"),
    historyModalSubtitle: document.getElementById("history-modal-subtitle"),
    versionHistoryBody: document.getElementById("version-history-body"),
    historyCompareBtn: document.getElementById("history-compare-btn"),
    compareModal: document.getElementById("compare-modal"),
    compareModalClose: document.getElementById("compare-modal-close"),
    compareModalTitle: document.getElementById("compare-modal-title"),
    compareThead: document.getElementById("compare-thead"),
    compareTbody: document.getElementById("compare-tbody"),
  };

  // ── Tab navigation ──
  const tabTitles = {
    history: { eyebrow: "운영 관리", heading: "레시피 현황" },
    import: { eyebrow: "레시피 관리", heading: "레시피 등록·수정" },
    lookup: { eyebrow: "레시피 관리", heading: "버전 비교" },
  };
  const canManage = dom.shell && dom.shell.dataset.canManage === "1";

  function syncTopbarTitle(tabName) {
    const title = tabTitles[tabName] || tabTitles.history;
    if (dom.topbarEyebrow) dom.topbarEyebrow.textContent = title.eyebrow;
    if (dom.topbarHeading) dom.topbarHeading.textContent = title.heading;
    document.title = `BRM · ${title.heading}`;
  }

  dom.tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      dom.tabBtns.forEach((b) => b.classList.remove("active"));
      dom.tabPanels.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
      syncTopbarTitle(btn.dataset.tab);
    });
  });

  function switchToImportTab() {
    dom.tabBtns.forEach((b) => b.classList.remove("active"));
    dom.tabPanels.forEach((p) => p.classList.remove("active"));
    const importTab = document.querySelector('[data-tab="import"]');
    if (importTab) importTab.classList.add("active");
    document.getElementById("tab-import").classList.add("active");
    syncTopbarTitle("import");
  }

  // ── Shared constants ──
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

  // ── Shared ctx ──
  const ctx = {
    dom,
    canManage,
    state: {
      currentPreview: null,
      materials: [],
      sheet: null,
      confirmedRawText: "",
      previewIsStale: false,
      suppressDirtyTracking: false,
      spreadsheetFallbackNotified: false,
      currentHistoryChain: null,
      selectedRecipeId: null,
      pendingRevisionOf: null,
    },
    const: { stageLabels, preferenceKeys },
  };
  ctx.switchToImportTab = switchToImportTab;

  // ── Module assembly (2-stage wiring; order is convention, see design §5) ──
  // 세로 BOM 편집기(item-code P5) — 인터페이스는 구 spreadsheet-editor 와 호환
  const spreadsheet = IRMS.management.createBomEditor(ctx);
  ctx.spreadsheet = spreadsheet;

  const importValidate = IRMS.management.createImportValidate(ctx);
  ctx.importValidate = importValidate;
  ctx.onDirty = importValidate.markPreviewStale;

  const recipeEditLoader = IRMS.management.createRecipeEditLoader(ctx);
  ctx.recipeEditLoader = recipeEditLoader;

  const recipeLookup = IRMS.management.createRecipeLookup(ctx);
  ctx.recipeLookup = recipeLookup;
  ctx.onEditFromVersion = recipeLookup.handleLookupClone;
  ctx.copyToClipboard = recipeLookup.copyToClipboard;

  const versionCompare = IRMS.management.createVersionCompare(ctx);
  ctx.versionCompare = versionCompare;
  const recipeHistory = IRMS.management.createRecipeHistory(ctx);

  async function loadMaterials() {
    if (!canManage) return;
    ctx.state.materials = await IRMS.getMaterials();
    spreadsheet.initSpreadsheet(ctx.state.materials);
  }

  // ── Event bindings ──
  if (canManage) {
    dom.previewBtn.addEventListener("click", importValidate.handlePreview);
    // 세로 편집기: ＋자재 = 자재 행 추가, ＋설명 = 공정 설명 줄 추가
    dom.addRowBtn?.addEventListener("click", () => spreadsheet.addMaterialRow());
    dom.addColBtn?.addEventListener("click", () => spreadsheet.addStepRow());

    dom.registerBtn.addEventListener("click", importValidate.handleRegister);
    dom.clearBtn.addEventListener("click", importValidate.handleClear);
  }

  dom.historyStatus.addEventListener("change", () => {
    recipeHistory.persistHistoryFilters();
    recipeHistory.updateHistorySummary();
    recipeHistory.renderHistory();
  });
  dom.historySearch.addEventListener(
    "input",
    IRMS.debounce(() => {
      recipeHistory.persistHistoryFilters();
      recipeHistory.updateHistorySummary();
      recipeHistory.renderHistory();
    }, 300),
  );
  dom.historyFrom.addEventListener("change", () => {
    recipeHistory.persistHistoryFilters();
    recipeHistory.updateHistorySummary();
    recipeHistory.renderHistory();
  });
  dom.historyTo.addEventListener("change", () => {
    recipeHistory.persistHistoryFilters();
    recipeHistory.updateHistorySummary();
    recipeHistory.renderHistory();
  });
  if (dom.historyResetBtn) {
    dom.historyResetBtn.addEventListener("click", recipeHistory.resetHistoryFilters);
  }
  if (dom.rawInput) {
    dom.rawInput.addEventListener("input", () => ctx.onDirty());
  }

  if (dom.lookupBtn) {
    dom.lookupBtn.addEventListener("click", recipeLookup.handleLookup);
  }
  if (dom.lookupProduct) {
    dom.lookupProduct.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        recipeLookup.handleLookup();
      }
    });
  }
  if (dom.lookupCopyBtn) {
    dom.lookupCopyBtn.addEventListener("click", recipeLookup.handleLookupCopy);
  }
  if (dom.lookupCloneBtn) {
    dom.lookupCloneBtn.addEventListener("click", recipeLookup.handleLookupClone);
  }
  if (dom.lookupHistoryBtn) {
    dom.lookupHistoryBtn.addEventListener("click", versionCompare.handleLookupHistory);
  }
  if (dom.lookupDhrBtn) {
    dom.lookupDhrBtn.addEventListener("click", recipeLookup.handleSetDhr);
  }
  if (dom.lookupDhr) {
    dom.lookupDhr.addEventListener("change", recipeLookup.handleDhrModeChange);
  }
  if (dom.historyModalClose) {
    dom.historyModalClose.addEventListener("click", () => { dom.historyModal.hidden = true; });
  }
  if (dom.historyCompareBtn) {
    dom.historyCompareBtn.addEventListener("click", versionCompare.handleCompareVersions);
  }
  if (dom.compareModalClose) {
    dom.compareModalClose.addEventListener("click", () => { dom.compareModal.hidden = true; });
  }

  // ── Init sequence ──
  (async () => {
    try {
      recipeHistory.restoreHistoryFilters();
      recipeHistory.updateHistorySummary();
      await loadMaterials();
      if (canManage) {
        importValidate.renderIssues([], dom.errorList, "오류 없음");
        importValidate.renderIssues([], dom.warningList, "확인 사항 없음");
        importValidate.syncRegisterState();
      }
      await Promise.all([
        recipeHistory.renderHistory(),
        recipeLookup.loadProducts(),
      ]);
    } catch (error) {
      IRMS.notify(`초기화 실패: ${error.message}`, "error");
    }
  })();
});

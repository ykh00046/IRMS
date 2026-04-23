/**
 * Spreadsheet Editor — "레시피 편집" tab in Management page.
 * Manages product tabs, JSpreadsheet instance, save/load, column management,
 * and Excel-style formula cells (cells starting with '=').
 */
(function () {
  "use strict";

  let products = [];
  let activeProductId = null;
  let sheetColumns = [];
  let sheetInstance = null;
  let isDirty = false;

  // Formula storage: key = "colIdx_rowIdx", value = "=B1+C1"
  let formulaMap = {};

  const $ = (id) => document.getElementById(id);

  function getFactory() {
    if (typeof window.jspreadsheet === "function") return window.jspreadsheet;
    if (typeof window.jexcel === "function") return window.jexcel;
    return null;
  }

  function getWorksheet() {
    if (sheetInstance && typeof sheetInstance.getData === "function") return sheetInstance;
    const el = $("ss-spreadsheet");
    const wb = el?.spreadsheet;
    const ws = wb?.worksheets?.[0] || null;
    if (ws && typeof ws.getData === "function") { sheetInstance = ws; return ws; }
    return sheetInstance;
  }

  // ── Product tabs ─────────────────────────────────

  async function loadProducts() {
    try {
      const data = await IRMS.ssListProducts();
      products = data.items || [];
      renderProductTabs();
      if (products.length === 0) {
        $("ss-empty-state").hidden = false;
        $("ss-sheet-area").hidden = true;
        activeProductId = null;
      } else {
        $("ss-empty-state").hidden = true;
        if (!activeProductId || !products.find((p) => p.id === activeProductId)) {
          activeProductId = products[0].id;
        }
        await loadSheet(activeProductId);
      }
    } catch (err) {
      IRMS.notify("제품 목록 조회 실패: " + err.message, "error");
    }
  }

  function renderProductTabs() {
    const container = $("ss-product-tabs");
    container.innerHTML = products
      .map((p) => {
        const typeLabel = p.recipeType === "powder" ? "파우더" : "액상";
        return `
      <button class="ss-product-tab${p.id === activeProductId ? " active" : ""}"
              data-product-id="${p.id}" type="button">
        <span class="ss-tab-type ss-tab-type-${p.recipeType || "solution"}">${typeLabel}</span>
        ${IRMS.escapeHtml(p.name)}
        <span class="ss-tab-close" data-delete-product="${p.id}" title="삭제">&times;</span>
      </button>`;
      })
      .join("");
  }

  async function switchProduct(productId) {
    if (productId === activeProductId) return;
    if (isDirty && !confirm("저장하지 않은 변경 사항이 있습니다. 이동하시겠습니까?")) return;
    activeProductId = productId;
    renderProductTabs();
    await loadSheet(productId);
  }

  let productModalMode = "create";
  let productModalTargetId = null;

  function openProductModal(mode, product) {
    productModalMode = mode;
    productModalTargetId = product?.id || null;
    $("ss-product-modal-title").textContent = mode === "edit" ? "제품 수정" : "새 제품";
    $("ss-product-name").value = product?.name || "";
    $("ss-product-name").disabled = mode === "edit";
    const recipeType = product?.recipeType || "solution";
    document.querySelectorAll('input[name="ss-recipe-type"]').forEach((r) => {
      r.checked = r.value === recipeType;
    });
    $("ss-product-modal-warn").hidden = mode !== "edit";
    $("ss-product-modal").hidden = false;
    if (mode !== "edit") setTimeout(() => $("ss-product-name").focus(), 50);
  }

  function closeProductModal() {
    $("ss-product-modal").hidden = true;
    productModalTargetId = null;
  }

  async function submitProductModal() {
    const name = $("ss-product-name").value.trim();
    const recipeType = document.querySelector('input[name="ss-recipe-type"]:checked')?.value || "solution";
    const typeLabel = recipeType === "powder" ? "파우더" : "액상";

    if (productModalMode === "create") {
      if (!name) { IRMS.notify("제품 이름을 입력하세요.", "error"); return; }
      try {
        const result = await IRMS.ssCreateProduct({ name, recipeType });
        activeProductId = result.id;
        IRMS.notify(`${typeLabel} 제품을 생성했습니다.`, "success");
        closeProductModal();
        await loadProducts();
      } catch (err) {
        const msg = err.message === "PRODUCT_NAME_EXISTS" ? "이미 존재하는 제품명입니다." : err.message;
        IRMS.notify("제품 생성 실패: " + msg, "error");
      }
      return;
    }

    // edit
    try {
      await IRMS.ssUpdateProduct(productModalTargetId, { recipeType });
      IRMS.notify(`${typeLabel}(으)로 변경했습니다.`, "success");
      closeProductModal();
      await loadProducts();
    } catch (err) {
      IRMS.notify("제품 수정 실패: " + err.message, "error");
    }
  }

  function createProduct() {
    openProductModal("create", null);
  }

  function editActiveProduct() {
    const p = products.find((x) => x.id === activeProductId);
    if (!p) return;
    openProductModal("edit", p);
  }

  async function deleteProduct(productId) {
    const product = products.find((p) => p.id === productId);
    if (!product) return;
    if (!confirm(`"${product.name}" 제품과 모든 데이터를 삭제하시겠습니까?`)) return;
    try {
      await IRMS.ssDeleteProduct(productId);
      if (activeProductId === productId) activeProductId = null;
      IRMS.notify("제품을 삭제했습니다.", "success");
      await loadProducts();
    } catch (err) {
      IRMS.notify("제품 삭제 실패: " + err.message, "error");
    }
  }

  // ── Sheet load/save ──────────────────────────────

  async function loadSheet(productId) {
    destroySheet();
    formulaMap = {};
    try {
      const data = await IRMS.ssLoadSheet(productId);
      sheetColumns = data.columns || [];
      $("ss-sheet-area").hidden = false;
      renderSheet(data);
      isDirty = false;
    } catch (err) {
      IRMS.notify("시트 로드 실패: " + err.message, "error");
    }
  }

  function renderSheet(data) {
    const factory = getFactory();
    if (!factory) {
      IRMS.notify("스프레드시트 UI 로드 실패", "error");
      return;
    }

    const columns = data.columns || [];
    const rows = data.rows || [];

    const jssColumns = columns.map((col) => ({
      title: col.name,
      width: col.colType === "text" ? 100 : 80,
    }));

    // Build data array, extracting formulas into formulaMap
    const jssData = [];
    const maxRow = rows.length > 0 ? Math.max(...rows.map((r) => r.rowIndex)) + 1 : 0;
    const totalRows = Math.max(maxRow, 5);

    for (let ri = 0; ri < totalRows; ri++) {
      const rowData = rows.find((r) => r.rowIndex === ri);
      const cells = rowData?.cells || {};
      const rowArr = columns.map((col, ci) => {
        const cellVal = cells[String(col.colIndex)];
        if (cellVal && typeof cellVal === "object" && cellVal.formula) {
          // Formula cell: store formula, display calculated value
          formulaMap[`${ci}_${ri}`] = cellVal.formula;
          return cellVal.display || "#ERR";
        }
        return cellVal || "";
      });
      jssData.push(rowArr);
    }

    const container = $("ss-spreadsheet");
    factory(container, {
      worksheets: [
        {
          data: jssData,
          columns: jssColumns,
          minDimensions: [columns.length || 3, 5],
          defaultColWidth: 80,
          tableOverflow: true,
          tableWidth: "100%",
          tableHeight: "400px",
          rowResize: true,
          contextMenu: true,
          onchange: handleCellChange,
          onafterchanges: () => { isDirty = true; },
          onpaste: () => { isDirty = true; },
          oneditionstart: handleEditStart,
          oneditionend: handleEditEnd,
        },
      ],
    });

    setTimeout(() => {
      getWorksheet();
      applyFormulaStyles();
    }, 50);
  }

  // ── Formula cell handling ───────────────────────

  function handleCellChange(_instance, cell, colIdx, rowIdx, newValue) {
    const ci = typeof colIdx === "string" ? parseInt(colIdx, 10) : colIdx;
    const ri = typeof rowIdx === "string" ? parseInt(rowIdx, 10) : rowIdx;
    const key = `${ci}_${ri}`;

    if (typeof newValue === "string" && newValue.startsWith("=")) {
      // User entered a formula
      formulaMap[key] = newValue;
      isDirty = true;
    } else {
      // Regular value — remove any old formula
      delete formulaMap[key];
      isDirty = true;
    }
  }

  function handleEditStart(_el, cell, colIdx, rowIdx) {
    const ci = typeof colIdx === "string" ? parseInt(colIdx, 10) : colIdx;
    const ri = typeof rowIdx === "string" ? parseInt(rowIdx, 10) : rowIdx;
    const key = `${ci}_${ri}`;
    const formula = formulaMap[key];
    if (formula) {
      // Show formula text while editing
      const ws = getWorksheet();
      if (ws) {
        const cellName = jssCell(ci, ri);
        ws.setValue(cellName, formula, true);
      }
    }
  }

  function handleEditEnd(_el, cell, colIdx, rowIdx, newValue) {
    const ci = typeof colIdx === "string" ? parseInt(colIdx, 10) : colIdx;
    const ri = typeof rowIdx === "string" ? parseInt(rowIdx, 10) : rowIdx;
    const key = `${ci}_${ri}`;

    if (typeof newValue === "string" && newValue.startsWith("=")) {
      // Keep formula, show placeholder until save
      formulaMap[key] = newValue;
      const ws = getWorksheet();
      if (ws) {
        const cellName = jssCell(ci, ri);
        ws.setValue(cellName, "...", true);
        applyFormulaStyleToCell(ws, ci, ri);
      }
    } else {
      // Cleared formula
      if (formulaMap[key]) {
        delete formulaMap[key];
        removeFormulaStyleFromCell(ci, ri);
      }
    }
  }

  function applyFormulaStyles() {
    const ws = getWorksheet();
    if (!ws) return;
    for (const key of Object.keys(formulaMap)) {
      const [ci, ri] = key.split("_").map(Number);
      applyFormulaStyleToCell(ws, ci, ri);
    }
  }

  function applyFormulaStyleToCell(ws, ci, ri) {
    const cellName = jssCell(ci, ri);
    try { ws.setStyle(cellName, "background-color", "#e8f4fd"); } catch (_e) { /* ignore */ }
    try { ws.setStyle(cellName, "color", "#1a5276"); } catch (_e) { /* ignore */ }
  }

  function removeFormulaStyleFromCell(ci, ri) {
    const ws = getWorksheet();
    if (!ws) return;
    const cellName = jssCell(ci, ri);
    try { ws.setStyle(cellName, "background-color", ""); } catch (_e) { /* ignore */ }
    try { ws.setStyle(cellName, "color", ""); } catch (_e) { /* ignore */ }
  }

  function jssCell(col, row) {
    const letter = String.fromCharCode(65 + (col % 26));
    const prefix = col >= 26 ? String.fromCharCode(65 + Math.floor(col / 26) - 1) : "";
    return `${prefix}${letter}${row + 1}`;
  }

  function destroySheet() {
    const container = $("ss-spreadsheet");
    if (sheetInstance && typeof sheetInstance.destroy === "function") {
      sheetInstance.destroy();
    } else if (container?.spreadsheet && typeof window.jspreadsheet?.destroy === "function") {
      window.jspreadsheet.destroy(container, true);
    }
    if (container) container.innerHTML = "";
    sheetInstance = null;
    formulaMap = {};
  }

  function collectSheetData() {
    const ws = getWorksheet();
    if (!ws) return [];

    const rawData = ws.getData();
    const rows = [];
    for (let ri = 0; ri < rawData.length; ri++) {
      const cells = {};
      let hasValue = false;
      rawData[ri].forEach((val, ci) => {
        // Check if this cell has a formula stored
        const key = `${ci}_${ri}`;
        const formula = formulaMap[key];
        const cellValue = formula || String(val ?? "").trim();

        if (cellValue) {
          cells[String(sheetColumns[ci]?.colIndex ?? ci)] = cellValue;
          hasValue = true;
        }
      });
      if (hasValue) {
        rows.push({ rowIndex: ri, cells });
      }
    }
    return rows;
  }

  async function saveSheet() {
    if (!activeProductId) return;
    const rows = collectSheetData();
    const saveBtn = $("ss-save");
    IRMS.btnLoading(saveBtn, true);
    try {
      const result = await IRMS.ssSaveSheet(activeProductId, rows);
      IRMS.notify(`저장 완료 (${result.rowCount}행)`, "success");
      isDirty = false;
      // Reload to get calculated formula values
      await loadSheet(activeProductId);
    } catch (err) {
      IRMS.notify("저장 실패: " + err.message, "error");
    } finally {
      IRMS.btnLoading(saveBtn, false);
    }
  }

  // ── Row management ───────────────────────────────

  function addRowLocal() {
    const ws = getWorksheet();
    if (!ws) return;
    ws.insertRow();
    isDirty = true;
  }

  function deleteLastRowLocal() {
    const ws = getWorksheet();
    if (!ws) return;
    const data = ws.getData();
    if (data.length <= 1) {
      IRMS.notify("최소 1행은 유지해야 합니다.", "warn");
      return;
    }
    const lastRow = data.length - 1;
    // Clean up formula entries for deleted row
    sheetColumns.forEach((_col, ci) => {
      delete formulaMap[`${ci}_${lastRow}`];
    });
    ws.deleteRow(lastRow);
    isDirty = true;
  }

  // ── Column management modal ──────────────────────

  function openColumnModal() {
    if (!activeProductId) {
      IRMS.notify("제품을 먼저 선택하세요.", "warn");
      return;
    }
    renderColumnList();
    $("ss-col-modal").hidden = false;
  }

  function closeColumnModal() {
    $("ss-col-modal").hidden = true;
  }

  function renderColumnList() {
    const list = $("ss-col-list");
    list.innerHTML = sheetColumns
      .map((col) => {
        const isFixed = col.colIndex <= 2;
        const typeLabel = col.colType === "numeric" ? "숫자" : "텍스트";
        return `
          <div class="ss-col-item">
            <span class="ss-col-name">${IRMS.escapeHtml(col.name)}</span>
            <span class="ss-col-type">${typeLabel}</span>
            ${isFixed
              ? '<span class="ss-col-fixed">고정</span>'
              : `<span class="ss-col-del" data-del-col="${col.id}" title="삭제">&times;</span>`
            }
          </div>`;
      })
      .join("");
  }

  async function addColumn() {
    if (!activeProductId) return;
    const nameInput = $("ss-new-col-name");
    const typeSelect = $("ss-new-col-type");
    const name = nameInput.value.trim();
    const colType = typeSelect.value;

    if (!name) {
      IRMS.notify("컬럼 이름을 입력하세요.", "error");
      return;
    }

    try {
      await IRMS.ssAddColumn(activeProductId, { name, colType });
      nameInput.value = "";
      IRMS.notify("컬럼을 추가했습니다.", "success");
      closeColumnModal();
      await loadSheet(activeProductId);
    } catch (err) {
      const msg = err.message === "COLUMN_LIMIT_EXCEEDED" ? "컬럼 수 제한(30)을 초과했습니다." : err.message;
      IRMS.notify("컬럼 추가 실패: " + msg, "error");
    }
  }

  async function deleteColumn(colId) {
    const col = sheetColumns.find((c) => c.id === colId);
    if (!col) return;
    if (!confirm(`"${col.name}" 컬럼을 삭제하시겠습니까? 해당 컬럼의 데이터가 사라집니다.`)) return;
    try {
      await IRMS.ssDeleteColumn(colId);
      IRMS.notify("컬럼을 삭제했습니다.", "success");
      closeColumnModal();
      await loadSheet(activeProductId);
    } catch (err) {
      IRMS.notify("컬럼 삭제 실패: " + err.message, "error");
    }
  }

  // ── Register recipes directly ─────────────────────

  function buildTsv() {
    const ws = getWorksheet();
    if (!ws) return null;

    const rawData = ws.getData();
    const headerCols = sheetColumns;
    const header = headerCols.map((c) => c.name).join("\t");
    const dataLines = [];

    for (let ri = 0; ri < rawData.length; ri++) {
      let hasValue = false;
      const vals = headerCols.map((col, ci) => {
        const key = `${ci}_${ri}`;
        const formula = formulaMap[key];
        // For formulas, use displayed value (calculated result), not formula text
        const displayed = String(rawData[ri][ci] ?? "").trim();
        const val = formula ? displayed : displayed;
        if (val) hasValue = true;
        return val;
      });
      if (hasValue) dataLines.push(vals.join("\t"));
    }

    if (!dataLines.length) return null;
    return [header, ...dataLines].join("\n");
  }

  function nextVersionName(currentName) {
    // "test-3" → "test-4", "ABC-12" → "ABC-13", "YM6-B" → "YM6-B" (no number)
    const match = currentName.match(/^(.+-)(\d+)$/);
    if (match) {
      return `${match[1]}${Number(match[2]) + 1}`;
    }
    return currentName;
  }

  async function registerRecipes() {
    if (!activeProductId) {
      IRMS.notify("제품을 먼저 선택하세요.", "warn");
      return;
    }

    // Save first if dirty
    if (isDirty) {
      await saveSheet();
    }

    const tsv = buildTsv();
    if (!tsv) {
      IRMS.notify("등록할 행이 없습니다.", "warn");
      return;
    }

    // Get current product name and compute next version
    const product = products.find((p) => p.id === activeProductId);
    const currentName = product?.name || "";
    const nextName = nextVersionName(currentName);

    // Replace product name column in TSV with next version name
    const lines = tsv.split("\n");
    const headers = lines[0].split("\t");
    const productColIdx = headers.indexOf("제품명");

    let registrationTsv = tsv;
    if (productColIdx >= 0 && nextName !== currentName) {
      const newLines = [lines[0]];
      for (let i = 1; i < lines.length; i++) {
        const cols = lines[i].split("\t");
        cols[productColIdx] = nextName;
        newLines.push(cols.join("\t"));
      }
      registrationTsv = newLines.join("\n");
    }

    // Preview first to check for duplicates/errors
    const registerBtn = $("ss-register");
    IRMS.btnLoading(registerBtn, true);
    try {
      const preview = await IRMS.previewImport(registrationTsv);

      if (preview.errors && preview.errors.length > 0) {
        IRMS.notify("등록 오류: " + preview.errors[0].message, "error");
        return;
      }

      // Check for duplicate product name
      const rowCount = preview.rows?.length || (lines.length - 1);
      const versionLabel = nextName !== currentName
        ? `${currentName} → ${nextName}`
        : currentName;

      // Check existing recipes with same product name
      let duplicateWarning = "";
      try {
        const existing = await IRMS.getRecipes({ search: nextName });
        const activeRecipes = (existing || []).filter(
          (r) => r.status === "pending" || r.status === "in_progress"
        );
        if (activeRecipes.length > 0) {
          duplicateWarning = `\n\n⚠ "${nextName}" 이름의 활성 레시피가 ${activeRecipes.length}건 있습니다.`;
        }
      } catch (_e) { /* ignore */ }

      const confirmed = confirm(
        `${versionLabel} (${rowCount}행)을 레시피로 등록하시겠습니까?${duplicateWarning}`
      );
      if (!confirmed) return;

      const result = await IRMS.importRecipes(registrationTsv, "레시피 편집");
      IRMS.notify(`${result.created_count}건 레시피를 등록했습니다. (${versionLabel})`, "success");

      // Auto-create next version product in spreadsheet if name changed
      if (nextName !== currentName) {
        try {
          const newProduct = await IRMS.ssCreateProduct({
            name: nextName,
            recipeType: product?.recipeType || "solution",
          });
          activeProductId = newProduct.id;
          await loadProducts();
        } catch (_e) {
          // Product might already exist — just reload
          await loadProducts();
        }
      }
    } catch (err) {
      IRMS.notify("레시피 등록 실패: " + err.message, "error");
    } finally {
      IRMS.btnLoading(registerBtn, false);
    }
  }

  // ── Event binding ────────────────────────────────

  function init() {
    if (!$("ss-product-tabs")) return;

    $("ss-product-tabs").addEventListener("click", (e) => {
      const delBtn = e.target.closest("[data-delete-product]");
      if (delBtn) {
        e.stopPropagation();
        deleteProduct(Number(delBtn.dataset.deleteProduct));
        return;
      }
      const tab = e.target.closest("[data-product-id]");
      if (tab) switchProduct(Number(tab.dataset.productId));
    });

    $("ss-add-product").addEventListener("click", createProduct);
    $("ss-product-modal-close")?.addEventListener("click", closeProductModal);
    $("ss-product-modal")?.addEventListener("click", (e) => {
      if (e.target === $("ss-product-modal")) closeProductModal();
    });
    $("ss-product-modal-save")?.addEventListener("click", submitProductModal);
    // Double-click active tab to edit type
    $("ss-product-tabs")?.addEventListener("dblclick", (e) => {
      const tab = e.target.closest("[data-product-id]");
      if (tab) {
        const id = Number(tab.dataset.productId);
        const p = products.find((x) => x.id === id);
        if (p) openProductModal("edit", p);
      }
    });
    $("ss-add-row")?.addEventListener("click", addRowLocal);
    $("ss-del-row")?.addEventListener("click", deleteLastRowLocal);
    $("ss-manage-cols")?.addEventListener("click", openColumnModal);
    $("ss-save")?.addEventListener("click", saveSheet);
    $("ss-register")?.addEventListener("click", registerRecipes);

    // Column modal
    $("ss-col-modal-close")?.addEventListener("click", closeColumnModal);
    $("ss-col-modal")?.addEventListener("click", (e) => {
      if (e.target === $("ss-col-modal")) closeColumnModal();
    });
    $("ss-add-col-btn")?.addEventListener("click", addColumn);
    $("ss-col-list")?.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del-col]");
      if (del) deleteColumn(Number(del.dataset.delCol));
    });

    // Listen for tab activation to load data
    document.addEventListener("click", (e) => {
      const tab = e.target.closest('[data-tab="editor"]');
      if (tab) {
        setTimeout(() => loadProducts(), 50);
      }
    });

    // Auto-load if editor tab is active on page load
    const editorPanel = document.getElementById("tab-editor");
    if (editorPanel?.classList.contains("active")) {
      loadProducts();
    }
  }

  window.IRMS_SpreadsheetEditor = { init, loadProducts };

  document.addEventListener("DOMContentLoaded", init);
})();

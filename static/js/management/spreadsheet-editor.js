/**
 * spreadsheet-editor module — jspreadsheet/jexcel instance lifecycle.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createSpreadsheetEditor(ctx)
 * Returns: { getSpreadsheetFactory, setRawInputMode, destroySpreadsheet,
 *            getActiveWorksheet, initSpreadsheet, getSpreadsheetDataAsText }
 *
 * ctx dependencies:
 *   dom:   spreadsheetContainer, rawInput
 *   state: sheet, suppressDirtyTracking, spreadsheetFallbackNotified
 *   other: ctx.onDirty (jspreadsheet onchange/onafterchanges/onpaste)
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createSpreadsheetEditor = function (ctx) {
    const { dom, state } = ctx;

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
      if (dom.spreadsheetContainer) {
        dom.spreadsheetContainer.style.display = enabled ? "none" : "";
      }
      if (dom.rawInput) {
        dom.rawInput.hidden = !enabled;
        dom.rawInput.disabled = !enabled;
      }
    }

    function destroySpreadsheet() {
      if (state.sheet && typeof state.sheet.destroy === "function") {
        state.sheet.destroy();
      } else if (
        dom.spreadsheetContainer &&
        window.jspreadsheet &&
        typeof window.jspreadsheet.destroy === "function" &&
        dom.spreadsheetContainer.spreadsheet
      ) {
        window.jspreadsheet.destroy(dom.spreadsheetContainer, true);
      }

      if (dom.spreadsheetContainer) {
        dom.spreadsheetContainer.innerHTML = "";
      }
      state.sheet = null;
    }

    function getActiveWorksheet() {
      if (state.sheet && typeof state.sheet.getData === "function") {
        return state.sheet;
      }

      const workbook = dom.spreadsheetContainer?.spreadsheet;
      const worksheet = workbook?.worksheets?.[0] || null;
      if (worksheet && typeof worksheet.getData === "function") {
        state.sheet = worksheet;
        return worksheet;
      }

      return null;
    }

    // Initialize JSpreadsheet
    function initSpreadsheet() {
      state.suppressDirtyTracking = true;
      destroySpreadsheet();

      const spreadsheetFactory = getSpreadsheetFactory();
      if (!spreadsheetFactory) {
        state.sheet = null;
        setRawInputMode(true);
        state.suppressDirtyTracking = false;
        if (!state.spreadsheetFallbackNotified) {
          IRMS.notify(
            "스프레드시트 UI 로드에 실패하여 텍스트 입력 모드로 전환했습니다.",
            "warn",
          );
          state.spreadsheetFallbackNotified = true;
        }
        return;
      }

      setRawInputMode(false);

      // Create an empty 15x10 grid by default
      const data = Array.from({ length: 15 }, () => Array(10).fill(""));

      spreadsheetFactory(dom.spreadsheetContainer, {
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
              ctx.onDirty();
            },
            onafterchanges: () => {
              ctx.onDirty();
            },
            onpaste: () => {
              ctx.onDirty();
            },
          },
        ],
      });

      // Prevent false dirty events during first paint.
      setTimeout(() => {
        getActiveWorksheet();
        state.suppressDirtyTracking = false;
      }, 0);
    }

    // Extract data from spreadsheet and convert to tab-separated text
    function getSpreadsheetDataAsText() {
      const worksheet = getActiveWorksheet();
      if (!worksheet) {
        return String(dom.rawInput?.value || "").trim();
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

    return {
      getSpreadsheetFactory,
      setRawInputMode,
      destroySpreadsheet,
      getActiveWorksheet,
      initSpreadsheet,
      getSpreadsheetDataAsText,
    };
  };
})();

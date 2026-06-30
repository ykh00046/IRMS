(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeEditLoader = function (ctx) {
    const { dom, state } = ctx;

    function setRevisionBanner(detail, sourceLabel) {
      if (!dom.revisionBanner) return;
      const product = IRMS.escapeHtml(detail.product_name || `#${detail.id}`);
      const source = sourceLabel ? ` · ${IRMS.escapeHtml(sourceLabel)}` : "";
      dom.revisionBanner.innerHTML =
        `<b>수정 등록 중</b><span>${product} #${detail.id}${source}</span>` +
        '<span class="muted">검증 후 등록하면 기존 레시피의 새 버전으로 연결됩니다.</span>';
      dom.revisionBanner.hidden = false;
    }

    function loadRowsIntoSpreadsheet(tsvRows, tsvText) {
      state.suppressDirtyTracking = true;
      ctx.spreadsheet.destroySpreadsheet();

      const spreadsheetFactory = ctx.spreadsheet.getSpreadsheetFactory();
      if (spreadsheetFactory && dom.spreadsheetContainer) {
        while (tsvRows.length < 15) {
          tsvRows.push(Array(tsvRows[0]?.length || 10).fill(""));
        }
        for (const row of tsvRows) {
          while (row.length < 10) row.push("");
        }

        spreadsheetFactory(dom.spreadsheetContainer, {
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
              onchange: () => ctx.onDirty(),
              onafterchanges: () => ctx.onDirty(),
              onpaste: () => ctx.onDirty(),
            },
          ],
        });

        ctx.spreadsheet.setRawInputMode(false);
        setTimeout(() => {
          ctx.spreadsheet.getActiveWorksheet();
          state.suppressDirtyTracking = false;
        }, 0);
        return;
      }

      if (dom.rawInput) {
        dom.rawInput.value = tsvText;
        ctx.spreadsheet.setRawInputMode(true);
      }
      state.suppressDirtyTracking = false;
    }

    async function loadRecipeForEdit(recipeId, sourceLabel) {
      const detail = await IRMS.getRecipeDetail(recipeId);
      const tsvRows = detail.tsv.split("\n").map((row) => row.split("\t"));

      ctx.switchToImportTab();
      loadRowsIntoSpreadsheet(tsvRows, detail.tsv);

      state.pendingRevisionOf = recipeId;
      state.currentPreview = null;
      state.previewIsStale = false;
      state.confirmedRawText = "";
      ctx.importValidate.renderValidationMeta({ rows: [], warnings: [], errors: [] });
      ctx.importValidate.renderIssues([], dom.errorList, "오류 없음");
      ctx.importValidate.renderIssues([], dom.warningList, "확인 사항 없음");
      ctx.importValidate.syncRegisterState();
      setRevisionBanner(detail, sourceLabel);

      IRMS.notify(`레시피 #${recipeId}을 수정 등록 탭으로 불러왔습니다.`, "info");
    }

    function clearRevisionBanner() {
      if (dom.revisionBanner) {
        dom.revisionBanner.hidden = true;
        dom.revisionBanner.innerHTML = "";
      }
    }

    return {
      loadRecipeForEdit,
      clearRevisionBanner,
    };
  };
})();

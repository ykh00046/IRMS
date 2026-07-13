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
      // 기준 배합량 프리필 — 수정 등록 시 저장된 값(최대 3개)이 그대로 승계되도록.
      const baseEl = document.getElementById("register-base-total");
      if (baseEl) {
        baseEl.value = detail.base_totals
          ? String(detail.base_totals).split(",").map((t) => t.trim()).join(", ")
          : (detail.base_total != null ? String(detail.base_total) : "");
      }
      // 기준 자재 후보를 불러온 레시피의 자재로 채우고, 기존 기준 자재를 미리 선택.
      // 수정 등록은 검증(미리보기)을 거치지 않고 시트를 곧바로 채우므로 여기서 후보를 구성한다.
      const anchorSel = document.getElementById("imp-anchor");
      if (anchorSel) {
        const itemNames = (detail.items || [])
          .map((it) => it.material_name)
          .filter((n) => !!n);
        const seen = new Set();
        const uniq = [];
        for (const n of itemNames) {
          if (!seen.has(n)) { seen.add(n); uniq.push(n); }
        }
        anchorSel.innerHTML =
          '<option value="">없음</option>' +
          uniq.map((n) => `<option value="${IRMS.escapeHtml(n)}">${IRMS.escapeHtml(n)}</option>`).join("");
        anchorSel.value = detail.anchor_material_name || "";
      }
      // 허용 편차 프리필 — 수정 등록 시 부모의 tolerance_g 를 미리 채운다.
      // (서버는 tolerance_g 미지정 시 부모 값을 자동 승계하므로, 빈 칸으로 두면 기본값
      // 또는 부모 승계로 처리된다. 사용자가 명시한 값이 있으면 그것을 우선.)
      const toleranceEl = document.getElementById("imp-tolerance");
      if (toleranceEl) {
        toleranceEl.value = detail.tolerance_g != null ? String(detail.tolerance_g) : "";
      }
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

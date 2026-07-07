/**
 * import-validate module — Import tab: validate / register / clear flow.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createImportValidate(ctx)
 * Returns: { syncRegisterState, markPreviewStale, renderIssues,
 *            renderValidationMeta, handlePreview, handleRegister, handleClear }
 *
 * ctx dependencies:
 *   dom:   previewBtn, registerBtn, previewMeta, errorList, warningList
 *   state: currentPreview, confirmedRawText, previewIsStale,
 *          pendingRevisionOf, suppressDirtyTracking, sheet
 *   other: ctx.spreadsheet.getSpreadsheetDataAsText / .initSpreadsheet
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createImportValidate = function (ctx) {
    const { dom, state } = ctx;

    function syncRegisterState() {
      const canRegister =
        Boolean(state.currentPreview) &&
        !state.previewIsStale &&
        (state.currentPreview.errors || []).length === 0 &&
        state.currentPreview.rows.length > 0 &&
        state.confirmedRawText.trim().length > 0;
      dom.registerBtn.disabled = !canRegister;
    }

    function markPreviewStale() {
      if (state.suppressDirtyTracking || !state.currentPreview || state.previewIsStale) {
        return;
      }
      state.previewIsStale = true;
      syncRegisterState();
      renderValidationMeta(state.currentPreview);
      IRMS.notify("시트가 수정되어 검증이 무효화되었습니다. 다시 검증하세요.", "warn");
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
            `<li>L${item.level} · ${IRMS.escapeHtml(item.message)}${item.row ? ` (행 ${item.row})` : ""}</li>`,
        )
        .join("");
    }

    function renderValidationMeta(result) {
      const rows = result?.rows || [];
      const badges = [
        `<span class="meta-badge meta-ok">등록 ${rows.length}건</span>`,
        `<span class="meta-badge meta-warn">확인 ${(result?.warnings || []).length}건</span>`,
        `<span class="meta-badge meta-error">오류 ${(result?.errors || []).length}건</span>`,
      ];
      if (state.previewIsStale) {
        badges.push('<span class="meta-badge meta-warn">재검증 필요</span>');
      }
      dom.previewMeta.innerHTML = badges.join("");
    }

    async function handlePreview() {
      const raw = ctx.spreadsheet.getSpreadsheetDataAsText();

      if (!raw) {
        IRMS.notify("데이터를 입력하거나 붙여넣은 후 검증하세요.", "warn");
        return;
      }

      IRMS.btnLoading(dom.previewBtn, true);
      try {
        const result = await IRMS.previewImport(raw);
        state.currentPreview = result;
        state.confirmedRawText = raw;
        state.previewIsStale = false;
        renderValidationMeta(result);
        renderIssues(result.errors, dom.errorList, "오류 없음");
        renderIssues(result.warnings, dom.warningList, "확인 사항 없음");
        syncRegisterState();

        if (!result.errors.length && result.rows.length > 0) {
          IRMS.notify(`검증 완료: ${result.rows.length}건 등록 가능`, "success");
        }
      } catch (error) {
        IRMS.notify(`검증 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.previewBtn, false);
      }
    }

    async function handleRegister() {
      if (
        !state.currentPreview ||
        state.previewIsStale ||
        state.currentPreview.errors.length > 0 ||
        state.currentPreview.rows.length === 0 ||
        !state.confirmedRawText.trim()
      ) {
        if (state.previewIsStale) {
          IRMS.notify("검증본이 무효화되었습니다. 다시 검증 후 등록하세요.", "warn");
        }
        return;
      }

      IRMS.btnLoading(dom.registerBtn, true);
      try {
        const baseEl = document.getElementById("register-base-total");
        const baseTotal = baseEl && baseEl.value ? Number(baseEl.value) : null;
        const result = await IRMS.importRecipes(state.confirmedRawText, "레시피 관리", state.pendingRevisionOf, baseTotal);
        IRMS.notify(
          `${result.created_count}건 레시피를 등록했습니다.`,
          "success",
        );

        handleClear();
      } catch (error) {
        IRMS.notify(`등록 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.registerBtn, false);
      }
    }

    function handleClear() {
      state.confirmedRawText = "";
      state.previewIsStale = false;
      state.pendingRevisionOf = null;
      // 수정 등록에서 프리필된 기준 배합량이 다음 신규 등록에 새어들지 않게 비움.
      const baseTotalEl = document.getElementById("register-base-total");
      if (baseTotalEl) baseTotalEl.value = "";
      if (ctx.recipeEditLoader) {
        ctx.recipeEditLoader.clearRevisionBanner();
      }
      if (state.sheet) {
        ctx.spreadsheet.initSpreadsheet(state.materials);
      } else if (dom.rawInput) {
        dom.rawInput.value = "";
      }
      state.currentPreview = null;
      state.previewIsStale = false;
      renderValidationMeta({ rows: [], warnings: [], errors: [] });
      renderIssues([], dom.errorList, "오류 없음");
      renderIssues([], dom.warningList, "확인 사항 없음");
      syncRegisterState();
    }

    return {
      syncRegisterState,
      markPreviewStale,
      renderIssues,
      renderValidationMeta,
      handlePreview,
      handleRegister,
      handleClear,
    };
  };
})();

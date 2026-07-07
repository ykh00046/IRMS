/**
 * recipe-lookup module — Lookup tab: product recipe pivot, selection,
 * copy, and clone-to-import.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createRecipeLookup(ctx)
 * Returns: { loadProducts, setLookupSelection, handleLookup,
 *            copyToClipboard, handleLookupCopy, handleLookupClone }
 *
 * ctx dependencies (see design §4.4):
 *   dom:   lookup* refs, spreadsheetContainer, rawInput, errorList, warningList
 *   state: selectedRecipeId, pendingRevisionOf, currentPreview,
 *          previewIsStale, confirmedRawText, suppressDirtyTracking
 *   other: ctx.spreadsheet.*, ctx.importValidate.*, ctx.onDirty,
 *          ctx.switchToImportTab
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeLookup = function (ctx) {
    const { dom, state } = ctx;

    function dhrMode() {
      return !!(dom.lookupDhr && dom.lookupDhr.checked);
    }

    async function loadProducts() {
      try {
        const items = await IRMS.getProducts(dhrMode());
        if (dom.productList) {
          dom.productList.innerHTML = items
            .map((name) => `<option value="${IRMS.escapeHtml(name)}">`)
            .join("");
        }
      } catch (error) {
        IRMS.notify(`제품 목록 로드 실패: ${error.message}`, "error");
      }
    }

    function setLookupSelection(recipeId) {
      const canManage = !!ctx.canManage;
      state.selectedRecipeId = recipeId;
      const rows = dom.lookupResult.querySelectorAll("tbody tr");
      rows.forEach((row) => {
        row.classList.toggle("selected", Number(row.dataset.recipeId) === recipeId);
      });
      if (dom.lookupSelectedLabel) {
        dom.lookupSelectedLabel.textContent = recipeId ? `선택: #${recipeId}` : "선택: 없음";
      }
      if (dom.lookupCopyBtn) dom.lookupCopyBtn.disabled = !recipeId;
      if (dom.lookupCloneBtn) dom.lookupCloneBtn.disabled = !canManage || !recipeId;
      if (dom.lookupHistoryBtn) dom.lookupHistoryBtn.disabled = !recipeId;
      if (dom.lookupDhrBtn) {
        dom.lookupDhrBtn.disabled = !canManage || !recipeId;
        dom.lookupDhrBtn.textContent = dhrMode() ? "DHR 전용 해제" : "DHR 전용 지정";
      }
      if (dom.lookupActions) dom.lookupActions.hidden = !recipeId;
    }

    async function handleSetDhr() {
      if (!state.selectedRecipeId) return;
      const target = !dhrMode(); // 일반 보기→지정(true), DHR 보기→해제(false)
      try {
        await IRMS.setRecipeDhr(state.selectedRecipeId, target);
        IRMS.notify(target ? "DHR 전용으로 지정했습니다." : "DHR 전용을 해제했습니다.", "success");
        await loadProducts();
        await handleLookup(); // 현재 목록 갱신(이동된 레시피는 빠짐)
      } catch (error) {
        IRMS.notify(`DHR 지정 실패: ${error.message}`, "error");
      }
    }

    async function handleDhrModeChange() {
      await loadProducts();
      if (dom.lookupProduct) dom.lookupProduct.value = "";
      if (dom.lookupResult) {
        dom.lookupResult.innerHTML =
          '<p class="empty-state">반제품명을 선택하면 버전별 자재 구성이 표시됩니다.</p>';
      }
      setLookupSelection(null);
    }

    async function handleLookup() {
      const productName = dom.lookupProduct ? dom.lookupProduct.value.trim() : "";
      if (!productName) {
        IRMS.notify("반제품명을 입력해주세요.", "warn");
        return;
      }

      IRMS.btnLoading(dom.lookupBtn, true);
      try {
        const data = await IRMS.getRecipesByProduct(productName, undefined, dhrMode());
        const recipes = data.items || [];

        if (!recipes.length) {
          dom.lookupResult.innerHTML = '<p class="empty-state">해당 반제품의 레시피가 없습니다.</p>';
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
          ...allMaterials.map((m) => `<th>${IRMS.escapeHtml(m)}</th>`),
          "<th>항목수</th>",
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
              ${materialCells}
              <td class="value-cell">${(recipe.items || []).length}</td>
              <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
              <td>${IRMS.formatDateTime(recipe.created_at)}</td>
              <td>${IRMS.escapeHtml(recipe.created_by || "-")}</td>
            </tr>`;
          })
          .join("");

        dom.lookupResult.innerHTML = `<table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>`;

        // Row click to select
        dom.lookupResult.querySelectorAll("tbody tr").forEach((row) => {
          row.addEventListener("click", () => {
            setLookupSelection(Number(row.dataset.recipeId));
          });
        });

        setLookupSelection(null);
        if (dom.lookupActions) dom.lookupActions.hidden = false;
      } catch (error) {
        IRMS.notify(`조회 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.lookupBtn, false);
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
      if (!state.selectedRecipeId) return;
      try {
        const detail = await IRMS.getRecipeDetail(state.selectedRecipeId);
        await copyToClipboard(detail.tsv);
        IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
      } catch (error) {
        IRMS.notify(`복사 실패: ${error.message}`, "error");
      }
    }

    async function handleLookupClone() {
      if (!state.selectedRecipeId) return;
      try {
        await ctx.recipeEditLoader.loadRecipeForEdit(state.selectedRecipeId, "버전 비교");
      } catch (error) {
        IRMS.notify(`수정 등록 준비 실패: ${error.message}`, "error");
      }
    }

    return {
      loadProducts,
      setLookupSelection,
      handleLookup,
      handleSetDhr,
      handleDhrModeChange,
      copyToClipboard,
      handleLookupCopy,
      handleLookupClone,
    };
  };
})();

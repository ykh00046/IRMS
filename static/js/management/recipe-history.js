/**
 * recipe-history module — 이력 tab: filters persistence + history table
 * with accordion detail rows.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createRecipeHistory(ctx)
 * Returns: { persistHistoryFilters, updateHistorySummary,
 *            restoreHistoryFilters, resetHistoryFilters, renderHistory }
 *
 * ctx dependencies:
 *   dom:   historyBody, historyStatus, historySearch, historyFrom,
 *          historyTo, historySummary
 *   const: preferenceKeys
 *   state: selectedRecipeId (accordion clone button)
 *   other: ctx.onClone (.history-clone-btn), ctx.copyToClipboard (.history-copy-btn)
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeHistory = function (ctx) {
    const { dom, state } = ctx;
    const { preferenceKeys } = ctx.const;

    function persistHistoryFilters() {
      IRMS.savePreference(preferenceKeys.status, dom.historyStatus.value);
      IRMS.savePreference(preferenceKeys.search, dom.historySearch.value.trim());
      IRMS.savePreference(preferenceKeys.from, dom.historyFrom.value);
      IRMS.savePreference(preferenceKeys.to, dom.historyTo.value);
    }

    function updateHistorySummary() {
      if (!dom.historySummary) {
        return;
      }

      const parts = [`상태 ${dom.historyStatus.value || "전체"}`];
      const search = dom.historySearch.value.trim();
      const from = dom.historyFrom.value;
      const to = dom.historyTo.value;

      if (search) {
        parts.push(`검색어 "${search}"`);
      }
      if (from || to) {
        parts.push(`기간 ${from || "시작 미지정"} ~ ${to || "종료 미지정"}`);
      }

      dom.historySummary.textContent = `${parts.join(" · ")} 기준으로 등록 이력을 표시 중입니다.`;
    }

    function restoreHistoryFilters() {
      dom.historyStatus.value = IRMS.loadPreference(preferenceKeys.status, "");
      dom.historySearch.value = IRMS.loadPreference(preferenceKeys.search, "");
      dom.historyFrom.value = IRMS.loadPreference(preferenceKeys.from, "");
      dom.historyTo.value = IRMS.loadPreference(preferenceKeys.to, "");
    }

    function resetHistoryFilters() {
      dom.historyStatus.value = "";
      dom.historySearch.value = "";
      dom.historyFrom.value = "";
      dom.historyTo.value = "";
      IRMS.clearPreference(preferenceKeys.status);
      IRMS.clearPreference(preferenceKeys.search);
      IRMS.clearPreference(preferenceKeys.from);
      IRMS.clearPreference(preferenceKeys.to);
      updateHistorySummary();
      renderHistory();
    }

    async function renderHistory() {
      persistHistoryFilters();
      updateHistorySummary();
      try {
        const rows = await IRMS.getRecipes({
          status: dom.historyStatus.value || undefined,
          search: dom.historySearch.value.trim() || undefined,
          dateFrom: dom.historyFrom.value || undefined,
          dateTo: dom.historyTo.value || undefined,
        });

        if (!rows.length) {
          dom.historyBody.innerHTML =
            '<tr><td colspan="7"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
          return;
        }

        dom.historyBody.innerHTML = rows
          .map(
            (recipe) => `
              <tr class="history-row" data-recipe-id="${recipe.id}">
                <td>${recipe.id}</td>
                <td class="product-cell">${IRMS.escapeHtml(recipe.productName)}</td>
                <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
                <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
                <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
                <td>${(recipe.items || []).length}</td>
              </tr>
            `,
          )
          .join("");

        // Accordion: row click to expand detail
        dom.historyBody.querySelectorAll(".history-row").forEach((row) => {
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
            dom.historyBody.querySelectorAll(".history-detail-row").forEach((r) => r.remove());
            dom.historyBody.querySelectorAll(".history-row.selected").forEach((r) => r.classList.remove("selected"));

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
                    ${detail.status === "pending" || detail.status === "in_progress"
                      ? `<button class="btn btn-sm history-cancel-btn" data-recipe-id="${recipeId}">등록 취소</button>`
                      : ""}
                    ${detail.status === "pending" || detail.status === "canceled"
                      ? `<button class="btn btn-sm danger history-delete-btn" data-recipe-id="${recipeId}">삭제</button>`
                      : ""}
                  </div>
                </div>
              </td>`;
              row.after(detailRow);

              detailRow.querySelector(".history-copy-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await ctx.copyToClipboard(detail.tsv);
                  IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
                } catch (err) {
                  IRMS.notify(`복사 실패: ${err.message}`, "error");
                }
              });

              detailRow.querySelector(".history-clone-btn").addEventListener("click", (e) => {
                e.stopPropagation();
                state.selectedRecipeId = recipeId;
                ctx.onClone();
              });

              const cancelBtn = detailRow.querySelector(".history-cancel-btn");
              if (cancelBtn) {
                cancelBtn.addEventListener("click", async (e) => {
                  e.stopPropagation();
                  if (!window.confirm("이 레시피를 등록 취소하시겠습니까?")) return;
                  try {
                    await IRMS.updateRecipeStatus(recipeId, "cancel");
                    IRMS.notify("레시피를 취소했습니다.", "success");
                    renderHistory();
                  } catch (err) {
                    IRMS.notify(`취소 실패: ${err.message}`, "error");
                  }
                });
              }

              const deleteBtn = detailRow.querySelector(".history-delete-btn");
              if (deleteBtn) {
                deleteBtn.addEventListener("click", async (e) => {
                  e.stopPropagation();
                  if (!window.confirm("이 레시피를 완전히 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) return;
                  try {
                    await IRMS.deleteRecipe(recipeId);
                    IRMS.notify("레시피를 삭제했습니다.", "success");
                    renderHistory();
                  } catch (err) {
                    IRMS.notify(`삭제 실패: ${err.message}`, "error");
                  }
                });
              }
            } catch (error) {
              IRMS.notify(`상세 조회 실패: ${error.message}`, "error");
            }
          });
        });
      } catch (error) {
        IRMS.notify(`이력 조회 실패: ${error.message}`, "error");
      }
    }

    return {
      persistHistoryFilters,
      updateHistorySummary,
      restoreHistoryFilters,
      resetHistoryFilters,
      renderHistory,
    };
  };
})();
